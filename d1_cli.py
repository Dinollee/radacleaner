#!/usr/bin/env python3
"""D1 CLI — швидкий доступ до бази даних (локальна SQLite).

Usage:
    python d1_cli.py "SELECT COUNT(*) FROM bills"
    python d1_cli.py "SELECT bill_number, title FROM bills WHERE stage=4 LIMIT 5"
    python d1_cli.py --exec "UPDATE bills SET stage=1 WHERE id=999"
    python d1_cli.py --exec "DELETE FROM risk_assessments WHERE bill_id=123"
    python d1_cli.py --tables              — показати всі таблиці та кількість рядків
    python d1_cli.py --table bills         — схема таблиці
"""
import json
import os
import sqlite3
import sys

DB_PATH = os.environ.get("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "radacleaner.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=None):
    conn = get_conn()
    rows = conn.execute(sql, params or []).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def exec_sql(sql, params=None):
    conn = get_conn()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        affected = cur.rowcount
        conn.close()
        return {"success": True, "changes": affected}
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}


def show_tables():
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
    rows = query(f"PRAGMA table_info({table})")
    for row in rows:
        print(f"  {row['name']:25s} {row['type']:15s} {'NOT NULL' if row['notnull'] else ''}")


def format_results(rows):
    if not rows:
        print("(empty)")
        return
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
