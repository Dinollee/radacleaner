#!/usr/bin/env python3
"""
migrate_to_d1_fast.py — Швидка міграція PG → D1 через прямий SQL batch.

Створює SQL файли та виконує їх через wrangler d1 execute.

Використання:
    source venv/bin/activate
    python scripts/migrate_to_d1_fast.py            # повна міграція
    python scripts/migrate_to_d1_fast.py --dry-run  # тільки підрахунок
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg2
import requests

DB_PARAMS = {
    "host": os.environ.get("DB_HOST", "192.168.1.229"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "my_bills"),
    "user": os.environ.get("DB_USER", "hermes"),
    "password": os.environ.get("DB_PASSWORD", "hermes"),
}

WORKER_URL = os.environ.get("WORKER_URL", "https://rada-monitor-api.distih.workers.dev")
SYNC_TOKEN = os.environ.get("CF_SYNC_TOKEN", "radacleaner-sync-secret-2026")
SYNC_URL = f"{WORKER_URL}/api/sync"

DRY_RUN = "--dry-run" in sys.argv
BATCH_SIZE = 200

TMP_DIR = Path("/tmp/d1_migrate")
TMP_DIR.mkdir(parents=True, exist_ok=True)


def pg_conn():
    return psycopg2.connect(**DB_PARAMS)


def run_sql_file(filepath: str, desc: str = "") -> bool:
    """Виконує SQL файл на D1 через wrangler."""
    if DRY_RUN:
        print(f"  [DRY-RUN] {desc}: {filepath}")
        return True

    if not os.path.getsize(filepath):
        print(f"  SKIP {desc}: empty file")
        return True

    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "radacleaner-db", "--remote", "--file", filepath],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"  ERROR {desc}: {result.stderr[:500]}")
        print(f"  STDOUT: {result.stdout[:500]}")
        return False

    # Рахуємо rows_written з output
    import re
    total_rows = 0
    for m in re.finditer(r'"rows_written":\s*(\d+)', result.stdout):
        total_rows += int(m.group(1))
    print(f"  OK {desc}: {total_rows} rows affected")
    return True


def escape_sql(val) -> str:
    """Екранує значення для SQL (NULL-safe)."""
    if val is None:
        return "NULL"
    s = str(val).replace("'", "''")
    return f"'{s}'"


def migrate_bills():
    """Мігрує bills через окремі INSERT OR REPLACE (multi-statement SQL файл)."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT bill_number, COALESCE(title,''), COALESCE(current_status,'new'), "
        "COALESCE(registration_date::text,''), COALESCE(committee,''), "
        "COALESCE(agenda_category,'other'), COALESCE(url,'') "
        "FROM bills ORDER BY id"
    )
    rows = cur.fetchall()
    total = len(rows)
    cur.close()
    conn.close()
    print(f"\n📜 Bills: {total} rows")

    if total == 0:
        return 0

    COLUMNS = "(bill_number,title,current_status,registration_date,committee,agenda_category,url,stage)"
    MAX_SQL_SIZE = 900_000  # залишаємо запас під 1MB ліміт SQLite

    done = 0
    batch_idx = 0

    while done < total:
        sql_lines = []
        batch_size = 0
        start = done

        for i in range(done, min(done + BATCH_SIZE * 2, total)):
            row = rows[i]
            reg_date = escape_sql(str(row[3])[:10] if row[3] else None)
            # Окремий INSERT на рядок
            stmt = f"INSERT OR REPLACE INTO bills {COLUMNS} VALUES ({escape_sql(str(row[0]))},{escape_sql(row[1])},{escape_sql(row[2] or 'new')},{reg_date},{escape_sql(row[4])},{escape_sql(row[5] or 'other')},{escape_sql(row[6])},1);\n"
            if batch_size + len(stmt) > MAX_SQL_SIZE:
                break
            sql_lines.append(stmt)
            batch_size += len(stmt)
            done += 1

        sql = "".join(sql_lines)
        sql_path = TMP_DIR / f"bills_batch_{batch_idx:03d}.sql"
        sql_path.write_text(sql)

        if run_sql_file(str(sql_path), f"bills batch {batch_idx+1} ({done-start} rows)"):
            pass
        else:
            # Якщо помилка — зменшити BATCH і спробувати менше
            print(f"  ERROR at row {start}, trying smaller batch")
            done = start

        batch_idx += 1
        print(f"  Progress: {done}/{total}")

    return done


def migrate_risk_assessments():
    """Мігрує risk_assessments через Worker API (мало даних)."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT r.id, r.bill_id, r.overall_score, r.social_risk, r.economic_risk,
               r.legal_risk, r.budget_risk, r.corruption_risk,
               r.json_data, r.raw_response,
               b.bill_number
        FROM risk_assessments r
        JOIN bills b ON r.bill_id = b.id
    """)
    rows = cur.fetchall()
    total = len(rows)
    cur.close()
    conn.close()
    print(f"\n⚠️ Risk assessments: {total} entries")

    if total == 0:
        return 0

    def js(v):
        if v is None: return "{}"
        if isinstance(v, (dict, list)): return json.dumps(v, ensure_ascii=False)
        return str(v)

    done = 0
    for row in rows:
        bill_number = str(row[10])
        payload = {
            "type": "risk",
            "data": {
                "bill_number": bill_number,
                "overall_score": float(row[2]) if row[2] else 0,
                "model_used": "openai/gpt-oss-120b",
                "social_risk": js(row[3] or "{}"),
                "economic_risk": js(row[4] or "{}"),
                "legal_risk": js(row[5] or "{}"),
                "budget_risk": js(row[6] or "{}"),
                "corruption_risk": js(row[7] or "{}"),
                "json_data": js(row[8] or "{}"),
                "raw_response": js(row[9] or "{}"),
            }
        }
        if DRY_RUN:
            print(f"  [DRY-RUN] risk #{bill_number}")
            done += 1
            continue

        try:
            resp = requests.post(SYNC_URL, json=payload,
                headers={"Authorization": f"Bearer {SYNC_TOKEN}"}, timeout=30)
            if resp.status_code == 200:
                done += 1
            else:
                print(f"  ERROR risk #{bill_number}: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ERROR risk #{bill_number}: {e}")

        if done % 10 == 0 and done > 0:
            print(f"  Risks progress: {done}/{total}")
            time.sleep(0.3)

    print(f"  Risks: {done}/{total} migrated")
    return done


def main():
    print(f"=== Fast Migration PG → D1 === WORKER_URL: {WORKER_URL}")
    if DRY_RUN:
        print("  DRY RUN mode — no data will be sent")

    t0 = time.time()
    bills = migrate_bills()
    risks = migrate_risk_assessments()
    elapsed = time.time() - t0

    print(f"\n✅ Migration complete!")
    print(f"   Bills: {bills}")
    print(f"   Risks: {risks}")
    print(f"   Time: {elapsed:.1f}s")

    # Cleanup tmp files
    if not DRY_RUN:
        for f in TMP_DIR.glob("*.sql"):
            f.unlink()

    print(f"\n📌 Для голосувань/депутатів — після парсингу через wrangler d1 execute")


if __name__ == "__main__":
    main()