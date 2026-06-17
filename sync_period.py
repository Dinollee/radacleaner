#!/usr/bin/env python3
"""sync_period.py — Парсинг сторінки Надходження законопроєктів + Card-сторінки кожного.

JSON-дампи на data.rada.gov.ua відстають на 1-2 дні.
Цей скрипт:
  1. Парсить HTML Period-сторінку для нових законів
  2. Заходить на Card-сторінку кожного нового закону
  3. Оновлює статус, хронологію (passings), документи

Usage:
    python sync_period.py              — check & sync new bills
    python sync_period.py --dry-run    — show what would change
    python sync_period.py --all        — re-check ALL bills on Period (not just new)
"""
import argparse
import re
import sys
import time
import urllib.request
import urllib.error

from src.config import log
from src.d1_client import d1_query, d1_exec
from src.bill_sync import queue_for_analysis


def fetch_html(url: str) -> str | None:
    """Завантажує HTML сторінку."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) radacleaner/1.0",
        "Accept": "text/html",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning("Failed to fetch %s: %s", url, e)
        return None


def parse_period_bills(html: str) -> list[dict]:
    """Парсить номери та назви законів з HTML таблиці Period."""
    bills = []
    pattern = re.compile(
        r'<a[^>]+href="[^"]*Card/(\d+)"[^>]*>\s*(\d{4,5}(?:/[А-Яа-яіїєґІЇЄҐ]+)?)\s*</a>\s*</td>\s*'
        r'<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*'
        r'<td>\s*(.*?)\s*</td>',
        re.DOTALL
    )
    for m in pattern.finditer(html):
        api_id = m.group(1).strip()
        bn = m.group(2).strip()
        reg_date_raw = m.group(3).strip()
        title_html = m.group(4)

        date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', reg_date_raw)
        if date_match:
            d, mo, y = date_match.group(1), date_match.group(2), date_match.group(3)
            reg_date = f"{y}-{mo}-{d}"
        else:
            reg_date = reg_date_raw

        title = re.sub(r'<[^>]+>', '', title_html).strip()
        title = title.replace('&quot;', '"').replace('&amp;', '&')
        url = f"https://itd.rada.gov.ua/billInfo/Bills/Card/{api_id}"

        bills.append({
            "bill_number": bn,
            "api_id": api_id,
            "title": title,
            "registration_date": reg_date,
            "url": url,
        })
    return bills


def parse_card_page(html: str) -> dict:
    """Парсить Card-сторінку: статус, passings, documents."""
    result = {"status": "", "passings": [], "documents": []}

    # Status: "Дати та стан проходження:</th> <th>STATUS</th>"
    status_match = re.search(
        r'Дати та стан проходження.*?</th>\s*<th[^>]*>([^<]+)</th>',
        html, re.DOTALL | re.IGNORECASE
    )
    if status_match:
        result["status"] = status_match.group(1).strip()

    # Passings from "Дати та стан проходження" table
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
                    date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?', date_raw)
                    if date_match:
                        d, mo, y = date_match.group(1), date_match.group(2), date_match.group(3)
                        h, mi = date_match.group(4) or '00', date_match.group(5) or '00'
                        pass_date = f"{y}-{mo}-{d} {h}:{mi}:00"
                        result["passings"].append({
                            "pass_date": pass_date,
                            "title": title,
                        })

    # Documents: <a class="downloadFile" ... data-id="..." href="...pubFile/..." data-file-name="...">
    doc_pattern = re.compile(
        r'<a[^>]+class="downloadFile"[^>]+data-id="(\d+)"[^>]*href="[^"]*pubFile/(\d+)"[^>]*data-file-name="([^"]*)"',
        re.IGNORECASE
    )
    for m in doc_pattern.finditer(html):
        result["documents"].append({
            "file_id": m.group(2),
            "name": m.group(3),
        })

    return result


def save_bill_to_d1(bill: dict, card: dict, dry_run: bool = False) -> bool:
    """Зберігає закон та його дані в D1."""
    bn = bill["bill_number"]

    # Insert bill if not exists
    existing = d1_query("SELECT id FROM bills WHERE bill_number = ?", [bn])
    if not existing:
        if dry_run:
            log.info("[DRY] Would add bill %s", bn)
            return True
        d1_exec("bill", {
            "bill_number": bn,
            "title": bill["title"],
            "current_status": card.get("status") or "new",
            "registration_date": bill["registration_date"],
            "committee": "Народний депутат України",
            "agenda_category": "other",
            "url": bill["url"],
            "stage": 1,
        })
        d1_exec("raw_sql", {
            "sql": "UPDATE bills SET status_changed_at=? WHERE bill_number=?",
            "params": [bill["registration_date"], bn],
        })
        rows = d1_query("SELECT id FROM bills WHERE bill_number = ?", [bn])
        if rows:
            d1_exec("change_log", {
                "bill_id": rows[0]["id"],
                "change_type": "new",
                "old_value": None,
                "new_value": card.get("status") or "new",
            })
            queue_for_analysis(rows[0]["id"], bn, "new_bill")
        log.info("Added bill %s", bn)

    # Get bill_id
    rows = d1_query("SELECT id, current_status FROM bills WHERE bill_number = ?", [bn])
    if not rows:
        return False
    bill_id = rows[0]["id"]
    old_status = rows[0]["current_status"]

    # Update status if changed
    new_status = card.get("status", "")
    if new_status and new_status != old_status:
        if dry_run:
            log.info("[DRY] Status %s: %s → %s", bn, old_status, new_status)
        else:
            d1_exec("bill", {"bill_number": bn, "current_status": new_status})
            d1_exec("change_log", {
                "bill_id": bill_id,
                "change_type": "status_change",
                "old_value": old_status,
                "new_value": new_status,
            })
            queue_for_analysis(bill_id, bn, "status_change_period")
            log.info("Status %s: %s → %s", bn, old_status, new_status)

    # Passings
    if card.get("passings"):
        existing_p = d1_query(
            "SELECT pass_date, title FROM bill_passings WHERE bill_id=?", [bill_id]
        )
        existing_set = {(r["pass_date"][:19], r["title"]) for r in existing_p}

        new_passings = 0
        for p in card["passings"]:
            key = (p["pass_date"][:19], p["title"])
            if key in existing_set:
                continue
            if dry_run:
                log.info("[DRY] Passing: %s — %s %s", bn, p["pass_date"], p["title"])
            else:
                d1_exec("raw_sql", {
                    "sql": "INSERT OR IGNORE INTO bill_passings (bill_id, pass_date, title, status) VALUES (?, ?, ?, '')",
                    "params": [bill_id, p["pass_date"], p["title"]],
                })
            new_passings += 1

        if new_passings:
            log.info("Passings %s: %d new", bn, new_passings)

        # Update status_changed_at from latest passing
        if not dry_run and card["passings"]:
            latest = max(p["pass_date"] for p in card["passings"])
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET status_changed_at=? WHERE id=? AND (status_changed_at IS NULL OR status_changed_at<?)",
                "params": [latest, bill_id, latest],
            })

    # Documents
    if card.get("documents"):
        existing_docs = d1_query(
            "SELECT file_id FROM bill_documents WHERE bill_id=?", [bill_id]
        )
        existing_doc_ids = {r["file_id"] for r in existing_docs}

        new_docs = 0
        for doc in card["documents"]:
            if doc["file_id"] in existing_doc_ids:
                continue
            if dry_run:
                log.info("[DRY] Doc: %s — %s", bn, doc["name"])
            else:
                d1_exec("raw_sql", {
                    "sql": "INSERT OR IGNORE INTO bill_documents (bill_id, file_id, doc_type) VALUES (?, ?, ?)",
                    "params": [bill_id, doc["file_id"], doc["name"]],
                })
            new_docs += 1

        if new_docs:
            log.info("Documents %s: %d new", bn, new_docs)
            if not dry_run:
                queue_for_analysis(bill_id, bn, "new_documents_period")

    return True


def sync_period(dry_run: bool = False, check_all: bool = False) -> int:
    """Перевіряє сторінку Period та синхронізує закони."""
    html = fetch_html("https://itd.rada.gov.ua/billinfo/Bills/Period")
    if not html:
        return 0

    bills = parse_period_bills(html)
    log.info("Found %d bills on Period page", len(bills))

    if not bills:
        log.warning("No bills parsed from HTML — page structure may have changed")
        return 0

    existing_rows = d1_query("SELECT bill_number FROM bills")
    existing = {row["bill_number"] for row in existing_rows}

    processed = 0
    for b in bills:
        bn = b["bill_number"]

        if not check_all and bn in existing:
            continue

        log.info("Processing %s — %s", bn, b["title"][:70])

        card_html = fetch_html(b["url"])
        if not card_html:
            log.warning("Could not fetch Card for %s", bn)
            time.sleep(1)
            continue

        card = parse_card_page(card_html)
        log.info("  Status: %s, Passings: %d, Docs: %d",
                 card["status"] or "?", len(card["passings"]), len(card["documents"]))

        save_bill_to_d1(b, card, dry_run=dry_run)
        processed += 1
        time.sleep(1)  # Rate limit

    log.info("=== Done: %d bills processed ===", processed)
    return processed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--all", action="store_true", help="Re-check ALL bills on Period")
    args = parser.parse_args()
    sync_period(dry_run=args.dry_run, check_all=args.all)


if __name__ == "__main__":
    main()
