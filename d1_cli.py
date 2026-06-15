#!/usr/bin/env python3
"""D1 CLI — швидкий доступ до бази даних через Worker API.

Usage:
    python d1_cli.py "SELECT COUNT(*) FROM bills"
    python d1_cli.py "SELECT bill_number, title FROM bills WHERE stage=4 LIMIT 5"
    python d1_cli.py --exec "UPDATE bills SET stage=1 WHERE id=999"
    python d1_cli.py --exec "DELETE FROM risk_assessments WHERE bill_id=123"
    python d1_cli.py --tables              — показати всі таблиці та кількість рядків
    python d1_cli.py --table bills         — схема таблиці
"""
import json
import sys
import urllib.request
import urllib.parse

WORKER_URL = "https://rada-monitor-api.distih.workers.dev"
SYNC_TOKEN = "radacleaner-sync-secret-2026"


def query(sql, params=None):
    """SELECT через Worker /api/query."""
    url = f"{WORKER_URL}/api/query"
    body = json.dumps({"sql": sql, "params": params or []}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {SYNC_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        return data.get("results", [])


def exec_sql(sql, params=None):
    """INSERT/UPDATE/DELETE через Worker /api/sync з типом raw_sql."""
    url = f"{WORKER_URL}/api/sync"
    body = json.dumps({"type": "raw_sql", "data": {"sql": sql, "params": params or []}}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {SYNC_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def show_tables():
    """Показує всі таблиці та кількість рядків."""
    rows = query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    for row in rows:
        name = row["name"]
        if name.startswith("sqlite_") or name.startswith("__"):
            continue
        try:
            cnt = query(f"SELECT COUNT(*) as c FROM {name}")
            print(f"  {name:30s} {cnt[0]['c']:>10,} rows")
        except:
            print(f"  {name:30s} (error)")


def show_schema(table):
    """Показує схему таблиці."""
    rows = query(f"PRAGMA table_info({table})")
    for row in rows:
        print(f"  {row['name']:25s} {row['type']:15s} {'NOT NULL' if row['notnull'] else ''}")


def format_results(rows):
    """Форматує результати для виведення."""
    if not rows:
        print("(empty)")
        return
    # Headers
    keys = list(rows[0].keys())
    widths = {k: max(len(k), max(len(str(r.get(k, ""))) for r in rows)) for k in keys}
    header = " | ".join(k.ljust(widths[k]) for k in keys)
    print(header)
    print("-" * len(header))
    for row in rows:
        print(" | ".join(str(row.get(k, "")).ljust(widths[k]) for k in keys))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--tables":
        show_tables()
    elif sys.argv[1] == "--table" and len(sys.argv) > 2:
        show_schema(sys.argv[2])
    elif sys.argv[1] == "--exec":
        sql = " ".join(sys.argv[2:])
        result = exec_sql(sql)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        sql = " ".join(sys.argv[1:])
        rows = query(sql)
        format_results(rows)


if __name__ == "__main__":
    main()
