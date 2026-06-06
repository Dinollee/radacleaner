#!/usr/bin/env python3
"""
migrate_to_d1.py — Міграція даних з PostgreSQL → D1 через Worker API.

Використання:
    source venv/bin/activate
    python scripts/migrate_to_d1.py              # всі таблиці
    python scripts/migrate_to_d1.py --dry-run    # тільки підрахунок
    python scripts/migrate_to_d1.py --table bills # тільки одну таблицю

Потрібні змінні .env:
    WORKER_URL=https://rada-monitor-api.distih.workers.dev
    CF_SYNC_TOKEN=radacleaner-sync-secret-2026
"""
import json
import os
import sys
import time

import psycopg2
import requests

# Конфіг
WORKER_URL = os.environ.get("WORKER_URL", "https://rada-monitor-api.distih.workers.dev")
SYNC_TOKEN = os.environ.get("CF_SYNC_TOKEN", "radacleaner-sync-secret-2026")
SYNC_URL = f"{WORKER_URL}/api/sync"

DB_PARAMS = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "my_bills"),
    "user": os.environ.get("DB_USER", "hermes"),
    "password": os.environ.get("DB_PASSWORD", "hermes"),
}

DRY_RUN = "--dry-run" in sys.argv
ONLY_TABLE = None
for i, a in enumerate(sys.argv):
    if a == "--table" and i + 1 < len(sys.argv):
        ONLY_TABLE = sys.argv[i + 1]


def pg_conn():
    return psycopg2.connect(**DB_PARAMS)


def push_to_worker(type_name: str, data: dict) -> bool:
    """Відправляє один запис у Worker API."""
    if DRY_RUN:
        print(f"  [DRY-RUN] would push {type_name}: {json.dumps(data)[:200]}")
        return True

    try:
        resp = requests.post(
            SYNC_URL,
            json={"type": type_name, "data": data},
            headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            return True
        else:
            print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  REQUEST ERROR: {e}")
        return False


def migrate_bills():
    """Мігрує законопроекти (PG: bills → D1: bills)."""
    conn = pg_conn()
    cur = conn.cursor()
    # PG має іншу схему — bill_id замість id, status замість current_status
    cur.execute("SELECT bill_id, COALESCE(title,''), COALESCE(status,'new'), date_registered, COALESCE(url,'') FROM bills")
    rows = cur.fetchall()
    total = len(rows)
    print(f"\n📜 Bills: {total} to migrate")

    done = 0
    errors = 0
    for row in rows:
        data = {
            "bill_number": str(row[0]),
            "title": row[1],
            "current_status": row[2],
            "registration_date": str(row[3]) if row[3] else None,
            "committee": "",
            "agenda_category": "other",
            "url": row[4],
            "stage": 1,
        }
        if push_to_worker("bill", data):
            done += 1
        else:
            errors += 1

        if done % 50 == 0:
            print(f"  Progress: {done}/{total}")
            time.sleep(1)

    cur.close()
    conn.close()
    print(f"  Done: {done} migrated, {errors} errors")
    return done


def migrate_change_log():
    """Мігрує лог змін (PG: old_status/new_status → D1: old_value/new_value)."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT bill_id, change_type, old_status, new_status FROM change_log")
    rows = cur.fetchall()
    print(f"\n📝 Change log: {len(rows)} entries")

    done = 0
    for row in rows:
        data = {
            "bill_id": row[0],
            "change_type": row[1],
            "old_value": row[2],
            "new_value": row[3],
        }
        if push_to_worker("change_log", data):
            done += 1
    print(f"  Done: {done} migrated")


def migrate_law_versions():
    """Мігрує версії законів (PG: law_versions → D1: law_versions)."""
    conn = pg_conn()
    cur = conn.cursor()
    cur.execute("SELECT bill_id, status_at_moment, text_hash, plain_text, analysis_summary, risks_json FROM law_versions")
    rows = cur.fetchall()
    print(f"\n📚 Law versions: {len(rows)} entries")

    done = 0
    for row in rows:
        risks = row[5]
        if isinstance(risks, (dict, list)):
            risks = json.dumps(risks, ensure_ascii=False)

        data = {
            "law_id": row[0],       # PG: bill_id = D1: law_id
            "status_at_moment": row[1],
            "text_hash": row[2],
            "plain_text": (row[3] or "")[:50000],
            "analysis_summary": row[4] or "",
            "risks_json": risks or "{}",
        }
        if push_to_worker("law_version", data):
            done += 1
    print(f"  Done: {done} migrated")


def migrate_risk_assessments():
    """Мігрує оцінки ризиків (PG: ризики в окремих колонках → D1: JSON колонки)."""
    conn = pg_conn()
    cur = conn.cursor()
    # PG має social, economic, legal, environmental, institutional замість budget_risk/legal_risk/etc
    cur.execute("""
        SELECT id, bill_id, document_id, overall_score, social, economic,
               legal, environmental, institutional, risks_json, raw_response
        FROM risk_assessments
    """)
    rows = cur.fetchall()
    print(f"\n⚠️ Risk assessments: {len(rows)} entries")

    def js(v):
        if v is None:
            return "{}"
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, str):
            return v
        return str(v)

    done = 0
    for row in rows:
        data = {
            "document_id": row[2],          # document_id
            "bill_id": row[1],              # bill_id
            "model_used": "openai/gpt-oss-120b",
            "overall_score": float(row[3]) if row[3] else 0,
            "budget_risk": js({"finding": str(row[5] or "Не виявлено")}),     # economic → budget
            "legal_risk": js({"finding": str(row[6] or "Не виявлено")}),       # legal
            "economic_risk": js({"finding": str(row[7] or "Не виявлено")}),    # environmental → economic
            "social_risk": js({"finding": str(row[4] or "Не виявлено")}),      # social
            "corruption_risk": js({"finding": str(row[8] or "Не виявлено")}),  # institutional → corruption
            "raw_response": js(row[10] or "{}"),
            "raw_analysis": "",
            "json_data": js(row[9] or "{}"),
            "legislative_risk": "{}",
            "official_power_risk": "{}",
            "vague_norms_risk": "{}",
            "confidence_level": 5,
            "insufficient_text": False,
        }
        if push_to_worker("risk", data):
            done += 1
    print(f"  Done: {done} migrated")


def migrate_votes():
    """Мігрує голосування — через прямі SQL вставки в D1 (не через /api/sync)."""
    conn = pg_conn()
    cur = conn.cursor()

    # Рахуємо
    cur.execute("SELECT COUNT(*) FROM votes")
    votes_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mp_votes")
    mp_votes_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mps")
    mps_count = cur.fetchone()[0]

    print(f"\n🗳️  Votes: {votes_count}, MP votes: {mp_votes_count}, MPs: {mps_count}")

    # Голосування та MP голоси потребують прямого SQL в D1 (немає endpoint)
    # Поки що виводимо інфо, міграція через D1 SQL
    print(f"  For votes, MPs, mp_votes: run via D1 SQL console or wrangler d1 execute")
    print(f"  Use: npx wrangler d1 execute radacleaner-db --remote --command='...'")

    cur.close()
    conn.close()


def main():
    print(f"=== Migrate to D1: {WORKER_URL} ===")
    if DRY_RUN:
        print("  DRY RUN mode — no data will be sent")

    tables = {
        "bills": migrate_bills,
        "change_log": migrate_change_log,
        "law_versions": migrate_law_versions,
        "risk_assessments": migrate_risk_assessments,
        "votes": migrate_votes,
    }

    if ONLY_TABLE:
        if ONLY_TABLE in tables:
            tables[ONLY_TABLE]()
        else:
            print(f"Unknown table: {ONLY_TABLE}. Available: {', '.join(tables.keys())}")
    else:
        for name, func in tables.items():
            if name != "votes":  # votes окремо
                func()
        migrate_votes()

    print("\n✅ Migration complete!")


if __name__ == "__main__":
    main()