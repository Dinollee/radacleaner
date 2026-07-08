#!/usr/bin/env python3
"""sync_schedule.py — Синхронізація графіку засідань ВРУ.

Джерела:
  1. Пленарні засідання — з votes (дні з голосуваннями)
  2. Дні питань — з votes (дні з >20 голосуваннями)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log


def sync_plenary_sessions():
    """Синхронізація пленарних засідань з votes."""
    log.info("Syncing plenary sessions from votes...")

    rows = d1_query("""
        SELECT vote_date::date as session_date, COUNT(*) as vote_count
        FROM votes
        WHERE vote_date IS NOT NULL
        GROUP BY vote_date::date
        ORDER BY session_date DESC
    """)

    synced = 0
    for row in rows:
        date_str = str(row['session_date'])[:10]
        vote_count = row['vote_count']

        event_type = 'plenary' if vote_count >= 5 else 'question_day'

        d1_exec_sql("""
            INSERT INTO rada_schedule (date, event_type, title, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT DO NOTHING
        """, [
            date_str,
            event_type,
            f"Пленарне засідання ({vote_count} голосувань)" if event_type == 'plenary' else f"День питань ({vote_count} голосувань)",
            f"День з {vote_count} голосуваннями"
        ])
        synced += 1

    log.info("Plenary sessions: %d synced", synced)
    return synced


def main():
    log.info("=== Schedule sync ===")
    p = sync_plenary_sessions()
    log.info("Done: %d plenary/question_day sessions", p)


if __name__ == "__main__":
    main()
