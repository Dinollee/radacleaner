#!/usr/bin/env python3
"""migrate_to_sqlite.py — Export all data from D1 and import into local SQLite.

Usage:
    python migrate_to_sqlite.py              — full migration
    python migrate_to_sqlite.py --table mp_votes  — migrate only one table
    python migrate_to_sqlite.py --dry-run    — show what would be migrated
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from src.config import log, WORKER_URL, SYNC_TOKEN
import requests as _requests


def d1_query_remote(sql: str, params: list | None = None) -> list[dict]:
    """Query D1 via Worker API (bypasses local SQLite)."""
    for attempt in range(3):
        try:
            if params and len(params) <= 5:
                qp = {"sql": sql}
                for i, p in enumerate(params):
                    qp[f"p{i}"] = str(p) if p is not None else ""
                resp = _requests.get(
                    f"{WORKER_URL}/api/query", params=qp,
                    headers={"Authorization": f"Bearer {SYNC_TOKEN}"}, timeout=30)
            else:
                resp = _requests.post(
                    f"{WORKER_URL}/api/query",
                    json={"sql": sql, "params": params or []},
                    headers={"Authorization": f"Bearer {SYNC_TOKEN}"}, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("results", [])
        except Exception as e:
            log.warning("d1_query_remote attempt %d: %s", attempt+1, str(e)[:100])
            time.sleep(1)
    return []

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "radacleaner.db")

# Tables to migrate, in order (respecting foreign keys)
TABLES = [
    "vote_statuses",
    "bills",
    "risk_assessments",
    "change_log",
    "bill_passings",
    "bill_documents",
    "mps",
    "votes",
    "mp_votes",
    "pending_analysis",
    "stats_cache",
    "law_versions",
    "eu_alignment_overall",
    "eu_alignment_chapters",
    "bill_eu_classification",
    "rada_schedule",
    "rada_committee_schedule",
    "sync_state",
]

# Columns for each table (to ensure consistent ordering)
TABLE_COLUMNS = {
    "vote_statuses": ["id", "code", "label"],
    "bills": ["id", "bill_number", "title", "current_status", "registration_date",
              "committee", "agenda_category", "url", "text_hash", "plain_text",
              "stage", "created_at", "updated_at", "act_number", "act_date",
              "status_changed_at", "is_procedural", "last_card_check", "card_hash"],
    "risk_assessments": ["id", "document_id", "bill_id", "assessed_at", "model_used",
                         "budget_risk", "legal_risk", "economic_risk", "social_risk",
                         "corruption_risk", "overall_score", "raw_response", "raw_analysis",
                         "json_data", "legislative_risk", "official_power_risk",
                         "vague_norms_risk", "confidence_level", "insufficient_text", "risk_level"],
    "change_log": ["id", "bill_id", "change_type", "old_value", "new_value", "created_at", "notified"],
    "bill_passings": ["id", "bill_id", "pass_date", "title", "status"],
    "bill_documents": ["id", "bill_id", "file_id", "doc_type", "created_at"],
    "mps": ["id", "name", "faction", "start_date", "end_date", "py", "pda", "vkp",
            "data_sufficient", "total_votes", "attended_votes", "voted_votes",
            "total_bills", "total_laws", "stats_updated_at"],
    "votes": ["vote_id", "bill_id", "title", "vote_date", "yes_count", "no_count",
              "abstain_count", "not_present_count", "absent_count", "created_at"],
    "mp_votes": ["id", "vote_id", "mp_name", "mp_faction", "status_id", "vote_date"],
    "pending_analysis": ["id", "bill_id", "bill_number", "status", "created_at",
                         "started_at", "finished_at", "output"],
    "stats_cache": ["key", "value", "updated_at"],
    "law_versions": ["id", "law_id", "version_date", "status_at_moment", "text_hash",
                     "plain_text", "analysis_summary", "risks_json", "created_at"],
    "eu_alignment_overall": ["id", "overall_score", "weighted_score", "chapters_analyzed",
                             "total_chapters", "calculated_at", "created_at"],
    "eu_alignment_chapters": ["id", "chapter_id", "chapter_name", "chapter_name_en",
                              "alignment", "total_bills", "keywords_matched", "total_keywords",
                              "weight", "calculated_at", "created_at"],
    "bill_eu_classification": ["id", "bill_id", "chapter_id", "confidence",
                               "matched_keywords", "classified_at"],
    "rada_schedule": ["id", "date", "event_type", "title", "description", "url",
                      "session", "created_at", "updated_at"],
    "rada_committee_schedule": ["id", "week_start", "committee_name", "meeting_date",
                                "meeting_time", "topic", "room", "url", "created_at"],
    "sync_state": ["filename", "etag", "last_checked", "last_downloaded"],
}

BATCH_SIZE = 5000  # D1 limit for rows returned


def get_d1_count(table: str) -> int:
    """Get row count from D1."""
    rows = d1_query_remote(f"SELECT COUNT(*) as cnt FROM {table}")
    return rows[0]["cnt"] if rows else 0


def get_sqlite_count(table: str, conn: sqlite3.Connection) -> int:
    """Get row count from SQLite."""
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


def fetch_d1_batch(table: str, columns: list[str], offset: int, limit: int) -> list[dict]:
    """Fetch a batch of rows from D1."""
    cols = ", ".join(columns)
    rows = d1_query_remote(f"SELECT {cols} FROM {table} LIMIT ? OFFSET ?", [limit, offset])
    return rows


def insert_sqlite_batch(table: str, columns: list[str], rows: list[dict], conn: sqlite3.Connection):
    """Insert a batch of rows into SQLite."""
    if not rows:
        return 0

    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"

    values = []
    for row in rows:
        values.append(tuple(row.get(c) for c in columns))

    conn.executemany(sql, values)
    conn.commit()
    return len(values)


def migrate_table(table: str, conn: sqlite3.Connection, dry_run: bool = False):
    """Migrate a single table from D1 to SQLite."""
    columns = TABLE_COLUMNS.get(table)
    if not columns:
        log.warning("No columns defined for %s — skipping", table)
        return

    d1_count = get_d1_count(table)
    sqlite_count = get_sqlite_count(table, conn)
    log.info("[%s] D1: %d rows, SQLite: %d rows", table, d1_count, sqlite_count)

    if dry_run:
        return

    if d1_count == 0:
        log.info("[%s] Empty in D1 — skipping", table)
        return

    # For mp_votes, use special handling (7.5M rows)
    if table == "mp_votes":
        migrate_mp_votes(conn)
        return

    offset = 0
    total_inserted = 0
    while offset < d1_count:
        rows = fetch_d1_batch(table, columns, offset, BATCH_SIZE)
        if not rows:
            break

        inserted = insert_sqlite_batch(table, columns, rows, conn)
        total_inserted += inserted
        offset += BATCH_SIZE

        if total_inserted % 50000 == 0 or offset >= d1_count:
            log.info("[%s] Progress: %d/%d rows (%.1f%%)",
                     table, total_inserted, d1_count, total_inserted / d1_count * 100)

        time.sleep(0.1)  # Rate limit D1

    final_count = get_sqlite_count(table, conn)
    log.info("[%s] Done: %d rows inserted (SQLite total: %d)", table, total_inserted, final_count)


def migrate_mp_votes(conn: sqlite3.Connection):
    """Special migration for mp_votes (7.5M rows). Uses chunked approach."""
    d1_count = get_d1_count("mp_votes")
    log.info("[mp_votes] Starting migration of %d rows", d1_count)

    columns = TABLE_COLUMNS["mp_votes"]
    offset = 0
    total_inserted = 0

    while offset < d1_count:
        rows = fetch_d1_batch("mp_votes", columns, offset, BATCH_SIZE)
        if not rows:
            break

        inserted = insert_sqlite_batch("mp_votes", columns, rows, conn)
        total_inserted += inserted
        offset += BATCH_SIZE

        pct = total_inserted / d1_count * 100
        log.info("[mp_votes] Progress: %d/%d (%.1f%%)", total_inserted, d1_count, pct)

        time.sleep(0.15)  # Slightly longer delay for large table

    final_count = get_sqlite_count("mp_votes", conn)
    log.info("[mp_votes] Done: %d rows inserted (SQLite total: %d)", total_inserted, final_count)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", help="Migrate only this table")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        log.error("SQLite database not found at %s. Run schema first.", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    tables = [args.table] if args.table else TABLES

    log.info("=== Starting D1 → SQLite migration ===")
    log.info("Database: %s", DB_PATH)
    log.info("Tables: %s", ", ".join(tables))

    start = time.time()
    for table in tables:
        try:
            migrate_table(table, conn, dry_run=args.dry_run)
        except Exception as e:
            log.error("[%s] FAILED: %s", table, str(e)[:200])

    elapsed = time.time() - start
    log.info("=== Migration complete in %.1f seconds ===", elapsed)

    # Print summary
    log.info("SQLite database size: %d bytes (%.1f MB)",
             os.path.getsize(DB_PATH), os.path.getsize(DB_PATH) / 1024 / 1024)

    conn.close()


if __name__ == "__main__":
    main()
