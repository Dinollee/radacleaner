#!/usr/bin/env python3
"""calc_interest_profiles.py — профіль інтересів депутатів з LLM-розмітки interest_sectors.

Джерела: авторство (bill_sponsors) та голосування «за»/«проти» законів,
чиї аналізи містять галузі. Результат → deputy_interests (migration 027),
API /api/interests?mp=ID, блок «Профіль інтересів» у профілі депутата.
Запускається щодня о 08:30 після backfill_interest_sectors.py (той самий сервіс).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"

AUTHORED_SQL = """
    SELECT bs.mp_id, s.sector, count(DISTINCT bs.bill_id)
    FROM bill_sponsors bs
    JOIN risk_assessments ra ON ra.bill_id = bs.bill_id
    CROSS JOIN LATERAL jsonb_array_elements_text(ra.json_data::jsonb -> 'interest_sectors') AS s(sector)
    WHERE bs.mp_id IS NOT NULL AND ra.json_data::jsonb ? 'interest_sectors'
    GROUP BY 1, 2
"""

VOTED_SQL = """
    SELECT mv.mp_id, s.sector,
           count(*) FILTER (WHERE mv.status_id = 1),
           count(*) FILTER (WHERE mv.status_id = 2)
    FROM mp_votes mv
    JOIN mps m ON m.id = mv.mp_id AND m.end_date IS NULL
    JOIN votes v ON v.vote_id = mv.vote_id
    JOIN risk_assessments ra ON ra.bill_id = v.bill_id
    CROSS JOIN LATERAL jsonb_array_elements_text(ra.json_data::jsonb -> 'interest_sectors') AS s(sector)
    WHERE mv.status_id IN (1, 2)
      AND mv.vote_date > '2024-08-01'  -- останні 2 роки: профіль має бути актуальним
    GROUP BY 1, 2
"""


def calc() -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute(AUTHORED_SQL)
    authored = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    print(f"Авторство: {len(authored)} пар депутат×галузь")

    cur.execute(VOTED_SQL)
    voted = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
    print(f"Голосування: {len(voted)} пар депутат×галузь")

    keys = set(authored) | set(voted)
    rows = [
        (mp, sector, authored.get((mp, sector), 0), voted.get((mp, sector), (0, 0))[0],
         voted.get((mp, sector), (0, 0))[1])
        for mp, sector in keys
    ]

    cur.execute("TRUNCATE deputy_interests")
    cur.executemany("""
        INSERT INTO deputy_interests (mp_id, sector, authored, voted_for, voted_against, calculated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (mp_id, sector) DO UPDATE SET
            authored = EXCLUDED.authored, voted_for = EXCLUDED.voted_for,
            voted_against = EXCLUDED.voted_against, calculated_at = EXCLUDED.calculated_at
    """, [(r[0], r[1], r[2], r[3], r[4], datetime.now(timezone.utc)) for r in rows])

    conn.commit()
    cur.close()
    conn.close()
    print(f"Збережено {len(rows)} записів у deputy_interests")


if __name__ == "__main__":
    calc()
