#!/usr/bin/env python3
"""migrate_to_pg.py — Export data from local SQLite → PostgreSQL."""
import os
import sqlite3
import sys
import time

import psycopg2
import psycopg2.extras

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "data", "radacleaner.db")
PG_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"

TABLES = [
    "vote_statuses", "bills", "risk_assessments", "change_log",
    "bill_passings", "bill_documents", "mps", "votes", "mp_votes",
    "pending_analysis", "stats_cache", "law_versions",
    "eu_alignment_overall", "eu_alignment_chapters",
    "bill_eu_classification", "rada_schedule", "rada_committee_schedule", "sync_state",
]

BATCH = 50000


def migrate_table(table, sqlite_conn, pg_conn):
    sqlite_cur = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
    total = sqlite_cur.fetchone()[0]
    print(f"[{table}] {total:,} rows", end="", flush=True)
    if total == 0:
        print(" — empty, skip")
        return

    # Get column names
    cols = [desc[0] for desc in sqlite_conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    col_names = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))

    # Reset sequences for SERIAL columns
    with pg_conn.cursor() as cur:
        for t, c in [("bills", "id"), ("mps", "id")]:
            try:
                cur.execute(f"SELECT setval('{t}_{c}_seq', COALESCE((SELECT MAX({c}) FROM {t}), 1))")
            except Exception:
                pass

    offset = 0
    inserted = 0
    start = time.time()

    while offset < total:
        rows = sqlite_conn.execute(f"SELECT * FROM {table} LIMIT ? OFFSET ?", (BATCH, offset)).fetchall()
        if not rows:
            break

        # Convert None to None, strings stay strings
        data = [tuple(row) for row in rows]

        with pg_conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                data,
                page_size=10000
            )
        pg_conn.commit()

        inserted += len(rows)
        offset += BATCH

        if inserted % 200000 == 0 or offset >= total:
            elapsed = time.time() - start
            rate = inserted / elapsed if elapsed > 0 else 0
            print(f" → {inserted:,}/{total:,} ({inserted*100//total}%) {rate:.0f}/s", end="", flush=True)

    elapsed = time.time() - start
    print(f" ✓ {elapsed:.1f}s")


def main():
    print(f"SQLite: {SQLITE_PATH}")
    print(f"PostgreSQL: {PG_DSN.split('dbname=')[1].split()[0]}@{PG_DSN.split('host=')[1].split()[0]}")
    print()

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    pg_conn = psycopg2.connect(PG_DSN)
    pg_conn.autocommit = False

    start = time.time()
    for table in TABLES:
        try:
            migrate_table(table, sqlite_conn, pg_conn)
        except Exception as e:
            print(f"  ERROR: {e}")
            pg_conn.rollback()

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.1f}s ===")

    # Reset sequences after all data is loaded
    print("Resetting sequences...")
    with pg_conn.cursor() as cur:
        for table, col in [("bills", "id"), ("mps", "id"), ("change_log", "id"),
                             ("bill_passings", "id"), ("bill_documents", "id"),
                             ("risk_assessments", "id"), ("mp_votes", "id"),
                             ("pending_analysis", "id"), ("law_versions", "id"),
                             ("eu_alignment_overall", "id"), ("eu_alignment_chapters", "id")]:
            try:
                cur.execute(f"SELECT setval('{table}_{col}_seq', COALESCE((SELECT MAX({col}) FROM {table}), 1))")
            except Exception:
                pass
    pg_conn.commit()
    print("Done!")

    sqlite_conn.close()
    pg_conn.close()


if __name__ == "__main__":
    main()
