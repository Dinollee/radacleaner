#!/usr/bin/env python3
"""calc_voting_clubs.py — Клуби голосування: попарна узгодженість депутатів.

Матриця голосувань (депутат × vote_id, позиції 1=за/2=проти/3=утримався),
попарне узгодження через numpy, збереження пар у voting_allies (migration 026).
Не голосував (4) та відсутній (5) у знаменник не входять — це не позиція.
Кореляція голосувань ≠ доказ лобіювання: на дашборді лише факт спільних позицій.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

POSITIONS = (1, 2, 3)          # за / проти / утримався
MIN_COMMON = 400               # мінімум спільних позицій у СПІРНИХ голосуваннях
                               # (400+ відсікає шум рідко голосуючих: 100%-збіг на 100 голосах)
STORE_PCT = 70.0               # зберігати пари з узгодженістю ≥ 70%


def agreement_matrix(matrix):
    """Чиста функція: (N×V) int8-матриця → (agree NxN int64, common NxN int64).
    Позиція = лише 1/2/3 (за/проти/утримався); будь-що інше (-1, 4, 5) ігнорується."""
    present = np.isin(matrix, POSITIONS)
    agree = np.zeros((matrix.shape[0], matrix.shape[0]), dtype=np.int64)
    for i in range(matrix.shape[0]):
        agree[i] = ((matrix == matrix[i]) & present[i] & present).sum(axis=1)
    common = present.astype(np.int64) @ present.astype(np.int64).T
    return agree, common


def contested_columns(matrix):
    """Стовпці-події, де були і «за» (1), і «проти» (2) — єдиногласні голосування
    не несуть сигналу (усі згодні за замовчуванням)."""
    has_yes = (matrix == 1).any(axis=0)
    has_no = (matrix == 2).any(axis=0)
    return has_yes & has_no


def load_votes(cur):
    cur.execute("""
        SELECT m.id, v.vote_id, v.status_id
        FROM mps m JOIN mp_votes v ON v.mp_id = m.id
        WHERE m.end_date IS NULL AND v.status_id IN %s
        ORDER BY m.id, v.vote_id
    """, (POSITIONS,))
    return cur.fetchall()


def calc(min_common=MIN_COMMON, min_pct=STORE_PCT):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute("SELECT id, name, faction FROM mps WHERE end_date IS NULL ORDER BY id")
    mps = cur.fetchall()
    mp_ids = [r[0] for r in mps]
    idx_of = {mid: i for i, mid in enumerate(mp_ids)}

    rows = load_votes(cur)
    vote_ids = sorted({r[1] for r in rows})
    vidx_of = {vid: i for i, vid in enumerate(vote_ids)}

    matrix = np.full((len(mp_ids), len(vote_ids)), -1, dtype=np.int8)
    for mid, vid, st in rows:
        matrix[idx_of[mid], vidx_of[vid]] = st

    print(f"Депутатів: {len(mp_ids)}, подій голосування: {len(vote_ids)}, позиційних записів: {len(rows)}")

    mask = contested_columns(matrix)
    matrix = matrix[:, mask]
    print(f"Спірних голосувань (були і «за», і «проти»): {matrix.shape[1]}")
    agree, common = agreement_matrix(matrix)

    factions = {r[0]: (r[2] or '') for r in mps}
    pairs = []
    for a in range(len(mp_ids)):
        for b in range(a + 1, len(mp_ids)):
            c = int(common[a, b])
            if c < min_common:
                continue
            pct = round(float(agree[a, b]) / c * 100, 1)
            if pct < min_pct:
                continue
            cross = factions[mp_ids[a]] != factions[mp_ids[b]]
            pairs.append((mp_ids[a], mp_ids[b], c, int(agree[a, b]), pct, cross))

    print(f"Пар зі збігом ≥{min_pct}% і спільних ≥{min_common}: {len(pairs)} "
          f"(крос-фракційних: {sum(1 for p in pairs if p[5])})")

    cur.execute("TRUNCATE voting_allies")
    cur.executemany("""
        INSERT INTO voting_allies (mp_a, mp_b, common, agree, pct, cross_faction)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, pairs)

    meta = {
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "deputies": len(mp_ids),
        "events_total": len(vote_ids),
        "events_contested": int(matrix.shape[1]),
        "positioned_rows": len(rows),
        "pairs_stored": len(pairs),
        "cross_faction_pairs": sum(1 for p in pairs if p[5]),
        "min_common": min_common,
        "min_pct": min_pct,
    }
    cur.execute("""
        INSERT INTO stats_cache (key, value, updated_at)
        VALUES ('voting_clubs_meta', %s, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, (json.dumps(meta, ensure_ascii=False),))

    conn.commit()
    cur.close()
    conn.close()
    return meta


if __name__ == "__main__":
    meta = calc()
    print(json.dumps(meta, ensure_ascii=False, indent=2))
