#!/usr/bin/env python3
"""sync_schedule_api.py — Синхронізація графіку ВРУ з data.rada.gov.ua API.

Джерела:
  1. data.rada.gov.ua — набір "meetings" (повістки пленарних засідань)
  2. data.rada.gov.ua — набір "zal" (хронологія розгляду питань)
"""
import sys
import os
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log

BASE_URL = "https://data.rada.gov.ua"


def fetch_json(url):
    """Fetch JSON from data.rada.gov.ua."""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def sync_plenary_from_api():
    """Синхронізація пленарних засідань з API data.rada.gov.ua."""
    log.info("Syncing plenary sessions from data.rada.gov.ua API...")

    # Get list of meeting datasets for IX convocation
    meetings_list = fetch_json(f"{BASE_URL}/ogd/meetings/list.json")
    if not meetings_list:
        log.error("Could not fetch meetings list")
        return 0

    synced = 0
    # Look for current session (15th) datasets
    for dataset in meetings_list:
        ds_id = dataset.get("id", "")
        ds_title = dataset.get("title", "")

        # We want agenda datasets for current session
        if "порядок_денний" not in ds_id.lower() and "agenda" not in ds_id.lower():
            continue

        # Fetch dataset metadata
        meta_url = f"{BASE_URL}/ogd/meetings/{ds_id}/catalog.json"
        meta = fetch_json(meta_url)
        if not meta:
            continue

        # Get the JSON resource
        resources = meta.get("resources", [])
        for res in resources:
            if res.get("format", "").upper() == "JSON":
                data_url = res.get("url", "")
                if not data_url:
                    continue

                data = fetch_json(data_url)
                if not data:
                    continue

                # Parse agenda items
                items = data if isinstance(data, list) else data.get("data", [])
                for item in items:
                    date_str = item.get("date_plenary", "") or item.get("date", "")
                    if not date_str or len(date_str) < 10:
                        continue

                    date_str = date_str[:10]  # YYYY-MM-DD

                    # Insert plenary session
                    d1_exec_sql("""
                        INSERT INTO rada_schedule (date, event_type, title, description, session)
                        VALUES (%s, 'plenary', 'Пленарне засідання', %s, %s)
                        ON CONFLICT DO NOTHING
                    """, [
                        date_str,
                        f"Засідання ВРУ (дані API)",
                        "П'ятнадцята сесія IX скликання"
                    ])
                    synced += 1

    log.info("Plenary sessions from API: %d synced", synced)
    return synced


def sync_question_days():
    """Синхронізація днів запитань до Уряду."""
    log.info("Syncing question days...")

    # Question days are typically Thursdays during session
    # We'll check the API for "година запитань" entries
    meetings_list = fetch_json(f"{BASE_URL}/ogd/meetings/list.json")
    if not meetings_list:
        return 0

    synced = 0
    for dataset in meetings_list:
        ds_id = dataset.get("id", "")
        if "запитань" not in ds_id.lower() and "question" not in ds_id.lower():
            continue

        meta_url = f"{BASE_URL}/ogd/meetings/{ds_id}/catalog.json"
        meta = fetch_json(meta_url)
        if not meta:
            continue

        resources = meta.get("resources", [])
        for res in resources:
            if res.get("format", "").upper() == "JSON":
                data = fetch_json(res.get("url", ""))
                if not data:
                    continue

                items = data if isinstance(data, list) else data.get("data", [])
                for item in items:
                    date_str = item.get("date", "")[:10]
                    if not date_str or len(date_str) < 10:
                        continue

                    d1_exec_sql("""
                        INSERT INTO rada_schedule (date, event_type, title, description, session)
                        VALUES (%s, 'question_day', 'Запитання до Уряду', %s, %s)
                        ON CONFLICT DO NOTHING
                    """, [
                        date_str,
                        "Година запитань до Уряду",
                        "П'ятнадцята сесія IX скликання"
                    ])
                    synced += 1

    log.info("Question days: %d synced", synced)
    return synced


def main():
    log.info("=== Schedule API sync ===")
    p = sync_plenary_from_api()
    q = sync_question_days()
    log.info("Done: %d plenary + %d question_day", p, q)


if __name__ == "__main__":
    main()
