#!/usr/bin/env python3
"""sync_holidays.py — Синхронізація святкових та неробочих днів ВРУ.

Джерело: Кодекс законів про працю України + постанови ВРУ.
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log

# Official Ukrainian holidays and non-working days
HOLIDAYS_2026 = [
    ("2026-01-01", "Новий рік"),
    ("2026-01-07", "Різдво Христове"),
    ("2026-03-08", "Міжнародний жіночий день"),
    ("2026-04-20", "Великдень (католицький)"),
    ("2026-04-20", "Великдень (православний)"),
    ("2026-05-01", "День праці"),
    ("2026-05-09", "День Перемоги"),
    ("2026-06-08", "День Трійці"),
    ("2026-06-28", "День Конституції України"),
    ("2026-07-15", "День Української Державності"),
    ("2026-08-24", "День незалежності України"),
    ("2026-10-14", "День захисників і захисниць України"),
    ("2026-12-25", "Різдво Христове"),
]

# Transferred working days (weekend → working day for holidays)
# These are set by government resolutions each year
TRANSFERRED_2026 = []  # Will be filled when resolution is published


def sync_holidays(year=2026):
    """Синхронізація святкових днів."""
    log.info("Syncing holidays for %d...", year)

    synced = 0
    for date_str, title in HOLIDAYS_2026:
        if not date_str.startswith(str(year)):
            continue

        d1_exec_sql("""
            INSERT INTO rada_schedule (date, event_type, title, description, session)
            VALUES (%s, 'holiday', %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, [
            date_str,
            title,
            title,
            f"П'ятнадцята сесія IX скликання"
        ])
        synced += 1

    log.info("Holidays: %d synced", synced)
    return synced


def main():
    log.info("=== Holiday sync ===")
    h = sync_holidays()
    log.info("Done: %d holidays", h)


if __name__ == "__main__":
    main()
