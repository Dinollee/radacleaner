#!/usr/bin/env python3
"""sync_active_bills.py — Live check активних законів через VRU HTML.

Парсить HTML сторінки bill card з itd.rada.gov.ua для отримання:
- Поточного статусу
- Документів (file_id, type, name)
- Хронології проходження

Rate limit: 1 запит/сек (не DDoS).

Usage:
    python sync_active_bills.py           — check active bills (30 днів)
    python sync_active_bills.py --days 7  — check only 7 днів
    python sync_active_bills.py --dry-run — show what would change
"""
import argparse
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

sys.path.insert(0, __import__("os").path.dirname(__file__))

from src.d1_client import d1_query, d1_exec
from src.config import log
from src.bill_sync import queue_for_analysis


def fetch_bill_card(api_id: str) -> dict | None:
    """Завантажує сторінку bill card та парсить статус, документи, хронологію."""
    url = f"https://itd.rada.gov.ua/billInfo/Bills/Card/{api_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) radacleaner/1.0",
        "Accept": "text/html",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None

    if len(html) < 1000:
        log.warning("Short response from %s (%d bytes)", url, len(html))
        return None

    result = {"url": url, "documents": [], "passings": [], "status": ""}

    # Parse status from "Дати та стан проходження" section
    # Pattern: <th>Дати та стан проходження:</th>\n <th>STATUS</th>
    status_match = re.search(
        r'Дати та стан проходження.*?</th>\s*<th[^>]*>([^<]+)</th>',
        html, re.DOTALL | re.IGNORECASE
    )
    if status_match:
        result["status"] = status_match.group(1).strip()

    # Parse documents
    doc_pattern = re.compile(
        r'<a[^>]+class="downloadFile"[^>]+data-id="(\d+)"[^>]*href="[^"]*pubFile/(\d+)"[^>]*data-file-name="([^"]*)"',
        re.IGNORECASE
    )
    for m in doc_pattern.finditer(html):
        result["documents"].append({
            "file_id": m.group(2),
            "name": m.group(3),
        })

    # Parse passings from "Дати та стан проходження" table
    passings_section = re.search(
        r'Дати та стан проходження.*?<tbody>(.*?)</tbody>',
        html, re.DOTALL | re.IGNORECASE
    )
    if passings_section:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', passings_section.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 2:
                date_raw = re.sub(r'<[^>]+>', '', cells[0]).strip()
                title = re.sub(r'<[^>]+>', '', cells[1]).strip()
                if date_raw and title:
                    # Parse date: "25.04.2025" or "25.04.2025 14:30"
                    date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?', date_raw)
                    if date_match:
                        d, mo, y = date_match.group(1), date_match.group(2), date_match.group(3)
                        h, mi = date_match.group(4) or '00', date_match.group(5) or '00'
                        pass_date = f"{y}-{mo}-{d} {h}:{mi}:00"
                        result["passings"].append({
                            "pass_date": pass_date,
                            "title": title,
                        })

    return result


def sync_active_bills(days: int = 30, dry_run: bool = False):
    """Перевіряє статуси активних законів через VRU HTML."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    log.info("=== Live check: bills with status_changed_at >= %s ===", cutoff)

    bills = d1_query(
        "SELECT id, bill_number, current_status, url FROM bills "
        "WHERE status_changed_at >= ? AND url IS NOT NULL AND url != '' "
        "ORDER BY status_changed_at DESC",
        [cutoff],
    )
    log.info("Active bills to check: %d", len(bills))

    updated = 0
    docs_added = 0
    passings_added = 0
    checked = 0
    errors = 0

    for bill in bills:
        url = bill.get("url", "")
        m = re.search(r"/Card/(\d+)", url)
        if not m:
            continue

        api_id = m.group(1)
        checked += 1

        data = fetch_bill_card(api_id)
        if not data:
            errors += 1
            time.sleep(1)
            continue

        # Status update
        new_status = data.get("status", "")
        if new_status and new_status != bill["current_status"]:
            log.info("Status: %s %s → %s", bill["bill_number"], bill["current_status"], new_status)
            if not dry_run:
                d1_exec("bill", {
                    "bill_number": bill["bill_number"],
                    "current_status": new_status,
                })
                d1_exec("change_log", {
                    "bill_id": bill["id"],
                    "change_type": "status_change",
                    "old_value": bill["current_status"],
                    "new_value": new_status,
                })
                queue_for_analysis(bill["id"], bill["bill_number"], "status_change_live")
            updated += 1

        # Documents (only if bill has none)
        if data.get("documents"):
            existing = d1_query(
                "SELECT 1 FROM bill_documents WHERE bill_id=? LIMIT 1",
                [bill["id"]],
            )
            if not existing:
                for doc in data["documents"][:50]:
                    if not dry_run:
                        d1_exec("raw_sql", {
                            "sql": """INSERT INTO bill_documents (bill_id, file_id, doc_type)
                                      VALUES (?, ?, ?) ON CONFLICT (bill_id, file_id) DO NOTHING""",
                            "params": [bill["id"], doc["file_id"], doc["name"]],
                        })
                docs_added += 1
                if not dry_run:
                    queue_for_analysis(bill["id"], bill["bill_number"], "new_documents_live")
                log.info("Docs: %s — %d documents", bill["bill_number"], len(data["documents"]))

        # Passings (only if bill has none)
        if data.get("passings"):
            existing_p = d1_query(
                "SELECT 1 FROM bill_passings WHERE bill_id=? LIMIT 1",
                [bill["id"]],
            )
            if not existing_p:
                for p in data["passings"][:30]:
                    if not dry_run:
                        d1_exec("raw_sql", {
                            "sql": "INSERT INTO bill_passings (bill_id, pass_date, title, status) VALUES (?, ?, ?, '') ON CONFLICT (bill_id, pass_date, title) DO NOTHING",
                            "params": [bill["id"], p["pass_date"], p["title"]],
                        })
                passings_added += 1
                log.info("Passings: %s — %d entries", bill["bill_number"], len(data["passings"]))

        if checked % 50 == 0:
            log.info("Progress: %d/%d checked, %d updated, %d docs, %d passings",
                     checked, len(bills), updated, docs_added, passings_added)

        d1_exec("raw_sql", {
            "sql": "UPDATE bills SET last_card_check = now() AT TIME ZONE 'utc' WHERE id = ?",
            "params": [bill["id"]],
        })
        time.sleep(1)  # Rate limit: 1 req/sec

    log.info("=== Done: checked=%d, status_updated=%d, docs_added=%d, passings_added=%d, errors=%d ===",
             checked, updated, docs_added, passings_added, errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sync_active_bills(days=args.days, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
