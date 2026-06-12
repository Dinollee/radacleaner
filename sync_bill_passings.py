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

    with open('/tmp/billinfo-skl9.json', 'r') as f:
        data = json.load(f, strict=False)

    # Отримуємо всі bills з D1
    db_bills = d1_query("SELECT id, bill_number FROM bills")
    bill_map = {str(b['bill_number']): b['id'] for b in db_bills}
    log.info("Bills in D1: %d", len(bill_map))

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

        # Отримуємо існуючі passings для цього bill
        existing = d1_query(
            "SELECT pass_date, title FROM bill_passings WHERE bill_id = ?",
            [bill_id]
        )
        existing_set = {(p['pass_date'][:19], p['title']) for p in existing}

        for p in passings:
            raw_date = p.get('date', '')
            if not raw_date:
                continue

            # Форматуємо дату
            pass_date = raw_date[:10] + ' ' + raw_date[11:19] if len(raw_date) > 10 else raw_date[:10]
            title = p.get('title', '')
            status = p.get('status', '')

            key = (pass_date, title)
            if key in existing_set:
                skipped += 1
                continue

            d1_exec("raw_sql", {
                "sql": "INSERT OR IGNORE INTO bill_passings (bill_id, pass_date, title, status) VALUES (?, ?, ?, ?)",
                "params": [bill_id, pass_date, title, status]
            })
            inserted += 1
            existing_set.add(key)

    log.info("=== Done: inserted=%d, skipped=%d ===", inserted, skipped)


if __name__ == "__main__":
    sync_passings()
