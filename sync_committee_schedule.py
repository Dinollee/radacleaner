#!/usr/bin/env python3
"""sync_committee_schedule.py — Синхронізація графіку засідань комітетів ВРУ.

Джерело: static.rada.gov.ua/zakon/new/RK/RK{DDMMYY}.htm
Формат URL: RK + день + місяць + 2-значний рік + .htm

Парсить Word-exported HTML через regex (складна структура таблиць).
"""
import sys
import os
import re
import html
import requests
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log

BASE_URL = "http://static.rada.gov.ua/zakon/new/RK"

# Ukrainian month names
MONTH_MAP = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12
}


def fetch_page(url):
    """Fetch and decode a page."""
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        for encoding in ["windows-1251", "utf-8", "iso-8859-5"]:
            try:
                return r.content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return r.content.decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def extract_committees_from_html(html, week_start):
    """Extract committee meetings from Word-exported HTML using regex."""
    meetings = []

    # Find committee blocks: "КОМІТЕТ З ПИТАНЬ ..."
    committee_pattern = re.compile(
        r"КОМІТЕТ\s+З\s+ПИТАНЬ\s+(.+?)(?:</[a-z]|$)",
        re.IGNORECASE | re.DOTALL
    )

    # Find time patterns: "о 13.30", "о 09:00", "час не оприлюднюється"
    time_pattern = re.compile(r"о\s+(\d{1,2}[.:]\d{2})")

    # Find date patterns in context: "на період з ... по ..."
    date_range_pattern = re.compile(
        r"на\s+період\s+з\s+(\d{1,2})\s+по\s+(\d{1,2})\s+(\S+)\s+(\d{4})",
        re.IGNORECASE
    )

    # Single date pattern: "08 липня 2026"
    single_date_pattern = re.compile(
        r"(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\s+(\d{4})"
    )

    # Split HTML by committee headers
    committee_blocks = re.split(r"(?=КОМІТЕТ\s+З\s+ПИТАНЬ)", html)

    for block in committee_blocks[1:]:  # Skip first (before any committee)
        # Extract committee name
        name_match = committee_pattern.search(block)
        if not name_match:
            continue

        raw_name = name_match.group(1)
        # Clean up HTML tags, entities and extra whitespace
        name = html.unescape(re.sub(r"<[^>]+>", "", raw_name))
        name = re.sub(r"\s+", " ", name).strip()
        name = name.rstrip(".")  # Remove trailing period

        if not name or len(name) < 5:
            continue

        committee_name = f"Комітет з питань {name}"

        # Extract time
        time_match = time_pattern.search(block)
        meeting_time = time_match.group(1).replace(".", ":") if time_match else None

        # Extract dates from this block
        dates_found = []

        # Try date range
        for dr in date_range_pattern.finditer(block):
            day_start = int(dr.group(1))
            day_end = int(dr.group(2))
            month_name = dr.group(3).lower()
            year = int(dr.group(4))
            month = MONTH_MAP.get(month_name)
            if month:
                for day in range(day_start, day_end + 1):
                    dates_found.append(f"{year}-{month:02d}-{day:02d}")

        # Try single dates
        if not dates_found:
            for sd in single_date_pattern.finditer(block):
                day = int(sd.group(1))
                month_name = sd.group(2).lower()
                year = int(sd.group(3))
                month = MONTH_MAP.get(month_name)
                if month:
                    dates_found.append(f"{year}-{month:02d}-{day:02d}")

        # If no specific dates found, use week_start
        if not dates_found:
            dates_found = [week_start]

        # Extract topics (lines with bill numbers or agenda items)
        topic_lines = []
        # Look for "Законопроєкт" or bill references
        topic_pattern = re.compile(r"(?:Законопроєкт|законопроєкт|Проект|проєкт)[^<]{10,200}", re.IGNORECASE)
        for tm in topic_pattern.finditer(block):
            topic = re.sub(r"<[^>]+>", "", tm.group(0))
            topic = re.sub(r"\s+", " ", topic).strip()
            if len(topic) > 10:
                topic_lines.append(topic[:150])

        topic = "; ".join(topic_lines[:3]) if topic_lines else ""
        topic = html.unescape(topic)

        for date_str in dates_found:
            meetings.append({
                "name": committee_name,
                "date": date_str,
                "time": meeting_time,
                "topic": topic,
            })

    return meetings


def sync_committee_schedules():
    """Синхронізація графіку засідань комітетів."""
    log.info("Syncing committee schedules from static.rada.gov.ua...")

    # Fetch index page
    html = fetch_page(f"{BASE_URL}/index.htm")
    if not html:
        log.error("Could not fetch index page")
        return 0

    # Extract week links
    week_links = re.findall(r'href="(RK\d{6}\.htm)"', html)
    if not week_links:
        log.warning("No week links found in index")
        return 0

    log.info("Found %d week pages", len(week_links))

    synced = 0
    for link in week_links:
        url = f"{BASE_URL}/{link}"

        # Extract week start from filename
        match = re.match(r"RK(\d{2})(\d{2})(\d{2})\.htm", link)
        if not match:
            continue

        day, month, year = int(match.group(1)), int(match.group(2)), 2000 + int(match.group(3))
        week_start = f"{year}-{month:02d}-{day:02d}"

        page_html = fetch_page(url)
        if not page_html:
            continue

        meetings = extract_committees_from_html(page_html, week_start)

        for m in meetings:
            d1_exec_sql("""
                INSERT INTO rada_committee_schedule (week_start, committee_name, meeting_date, meeting_time, topic, url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (week_start, committee_name, meeting_date, COALESCE(meeting_time,''), COALESCE(topic,'')) DO NOTHING
            """, [
                week_start,
                m["name"],
                m["date"],
                m.get("time"),
                m.get("topic", ""),
                url
            ])
            synced += 1

    log.info("Committee schedules: %d synced", synced)
    return synced


def main():
    log.info("=== Committee schedule sync ===")
    c = sync_committee_schedules()
    log.info("Done: %d committee meetings", c)


if __name__ == "__main__":
    main()
