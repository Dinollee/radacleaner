#!/usr/bin/env python3
"""sync_schedule_legacy.py — Синхронізація графіку ВРУ з legacy API.

Джерело: w1.c1.rada.gov.ua/pls/radan_gs09/ns_el_h
Парсить HTML календаря для отримання дат пленарних засідань.
"""
import sys
import os
import re
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log

CALENDAR_URL = "https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_el_h"


def fetch_calendar():
    """Fetch and parse the VRU calendar page."""
    try:
        r = requests.get(CALENDAR_URL, timeout=30)
        r.raise_for_status()
        # Try windows-1251 encoding (legacy system)
        try:
            return r.content.decode("windows-1251")
        except:
            return r.content.decode("utf-8", errors="replace")
    except Exception as e:
        log.error("Failed to fetch calendar: %s", e)
        return None


def extract_plenary_dates(html):
    """Extract plenary session dates from calendar HTML."""
    # Pattern: href="https://w2.rada.gov.ua/pls/radan_gs09/ns_el_h2?data=DDMMYYYY&nom_s=15"
    pattern = re.compile(
        r'href="https://w2\.rada\.gov\.ua/pls/radan_gs09/ns_el_h2\?data=(\d{8})&nom_s=15"'
    )

    dates = set()
    for match in pattern.finditer(html):
        date_str = match.group(1)
        # Parse DDMMYYYY format
        day = int(date_str[:2])
        month = int(date_str[2:4])
        year = int(date_str[4:8])
        try:
            dt = datetime(year, month, day)
            dates.add(dt.strftime("%Y-%m-%d"))
        except ValueError:
            continue

    return sorted(dates)


def sync_plenary_from_legacy():
    """Синхронізація пленарних засідань з legacy календаря."""
    log.info("Syncing plenary sessions from legacy calendar...")

    html = fetch_calendar()
    if not html:
        return 0

    dates = extract_plenary_dates(html)
    log.info("Found %d plenary session dates", len(dates))

    synced = 0
    for date_str in dates:
        d1_exec_sql("""
            INSERT INTO rada_schedule (date, event_type, title, description, session)
            VALUES (%s, 'plenary', 'Пленарне засідання', %s, %s)
            ON CONFLICT (date, event_type) DO NOTHING
        """, [
            date_str,
            "Дані з календаря ВРУ",
            "П'ятнадцята сесія IX скликання"
        ])
        synced += 1

    log.info("Plenary sessions: %d synced", synced)
    return synced


def main():
    log.info("=== Legacy schedule sync ===")
    p = sync_plenary_from_legacy()
    log.info("Done: %d plenary sessions", p)


if __name__ == "__main__":
    main()
