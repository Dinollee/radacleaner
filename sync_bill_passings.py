#!/usr/bin/env python3
"""sync_bill_passings.py — Синхронізація хронології проходження законів з RADA."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec
from src.config import log


def sync_passings():
    log.info("=== Синхронізація bill_passings ===")

    with open('/tmp/billinfo-skl9.json', 'rb') as f:
        raw = f.read().decode('utf-8', errors='replace')
    import re
    raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', raw)
    data = json.loads(raw, strict=False)

    db_bills = d1_query("SELECT id, bill_number FROM bills")
    bill_map = {str(b['bill_number']): b['id'] for b in db_bills}
    log.info("Bills in D1: %d", len(bill_map))

    existing_rows = d1_query("SELECT bill_id, pass_date::date as day, title FROM bill_passings")
    existing_set = {(r['bill_id'], str(r['day']), r['title']) for r in existing_rows}
    log.info("Existing passings: %d", len(existing_set))

    inserted = 0
    skipped = 0

    for b in data:
        bn = str(b.get('registrationNumber', '')).strip()
        bill_id = bill_map.get(bn)
        if not bill_id:
            continue

        passings = b.get('passings', []) or []
        if not passings:
            continue

        for p in passings:
            raw_date = p.get('date', '')
            if not raw_date:
                continue

            pass_date = raw_date[:10] + ' ' + raw_date[11:19] if len(raw_date) > 10 else raw_date[:10]
            title = p.get('title', '')
            status = p.get('status', '')

            key = (bill_id, pass_date[:10], title)
            if key in existing_set:
                skipped += 1
                continue

            d1_exec("raw_sql", {
                "sql": "INSERT INTO bill_passings (bill_id, pass_date, title, status) VALUES (?, ?, ?, ?) ON CONFLICT (bill_id, pass_date, title) DO NOTHING",
                "params": [bill_id, pass_date, title, status]
            })
            inserted += 1
            existing_set.add(key)

    log.info("=== Done: inserted=%d, skipped=%d ===", inserted, skipped)

    # Оновлюємо status_changed_at з останнього проходження
    d1_exec("raw_sql", {
        "sql": """UPDATE bills SET status_changed_at = (
            SELECT MAX(pass_date) FROM bill_passings WHERE bill_id = bills.id
        ) WHERE id IN (
            SELECT DISTINCT bill_id FROM bill_passings
        ) AND (status_changed_at IS NULL OR status_changed_at < (
            SELECT MAX(pass_date) FROM bill_passings WHERE bill_id = bills.id
        ))""",
        "params": [],
    })


if __name__ == "__main__":
    sync_passings()
