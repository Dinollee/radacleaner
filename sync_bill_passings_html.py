#!/usr/bin/env python3
"""sync_bill_passings_html.py — Синхронізація хронології проходження законів через HTML.

На відміну від sync_bill_passings.py ( bulk JSON 1x/добу),
цей скрипт парсить HTML карточки законів для отримання актуальних даних.

Використання:
    ./venv/bin/python sync_bill_passings_html.py           — активні закони (події за 7 днів) + хвіст застарілих
    ./venv/bin/python sync_bill_passings_html.py --all     — перевірити всі stage 2,3,4
    ./venv/bin/python sync_bill_passings_html.py --bill 15294 — один закон
    ./venv/bin/python sync_bill_passings_html.py --days 3  — вікно активності 3 дні
"""
import argparse
import re
import sys
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec
from src.config import log

# Rate limiting: 1 запит на 1.5 секунди
REQUEST_DELAY = 1.5
# Максимум законів за один запуск
MAX_BILLS = 500


def fetch_html(url: str, timeout: int = 30) -> str | None:
    """Завантажує HTML з URL."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; Radacleaner/1.0)',
            'Accept-Language': 'uk-UA,uk;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        log.warning("Fetch error for %s: %s", url, str(e)[:100])
        return None


def parse_passings_from_html(html: str) -> list[dict]:
    """Парсить таблицю 'Проходження' з HTML карточки закону."""
    passings = []

    # Шукаємо таблицю з хронологією
    # Паттерн: <td>DD.MM.YYYY</td><td>Status text</td>
    # Або: <td>Date</td><td>Status</td> в контексті "Проходження"

    # Знаходимо секцію "Проходження"
    section_match = re.search(
        r'Проходження.*?<tbody>(.*?)</tbody>',
        html, re.DOTALL | re.IGNORECASE
    )
    if not section_match:
        return passings

    tbody = section_match.group(1)

    # Парсимо рядки таблиці
    rows = re.findall(r'<tr>(.*?)</tr>', tbody, re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) >= 2:
            date_raw = re.sub(r'<[^>]+>', '', cells[0]).strip()
            title = re.sub(r'<[^>]+>', '', cells[1]).strip()

            if date_raw and title:
                # Конвертуємо DD.MM.YYYY → YYYY-MM-DD
                date_match = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', date_raw)
                if date_match:
                    day, month, year = date_match.groups()
                    pass_date = f"{year}-{month}-{day}"
                    passings.append({
                        'pass_date': pass_date,
                        'title': title,
                        'status': title,  # status = title для простоти
                    })

    return passings


def get_hot_bills(days: int = 7) -> list[dict]:
    """Отримує 'гарячі' закони для перевірки хронології.

    Пріоритет:
    1. АКТИВНІ — останнє проходження в межах N днів (саме вони отримують
       нові події щодня; JSON-булк оновлюється 1x/добу, тому внутрішньоденні
       події активних законів інакше провалюються у щілину)
    2. Закони без passings (ніколи не синхронізовані)
    3. Закони де passings старіші за N днів (ротация хвоста)
    """
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    rows = d1_query(f"""
        SELECT b.id, b.bill_number, b.url, b.stage,
               MAX(bp.pass_date::date) as last_passing
        FROM bills b
        LEFT JOIN bill_passings bp ON bp.bill_id = b.id
        WHERE b.stage IN (2, 3, 4)
          AND b.url IS NOT NULL
        GROUP BY b.id, b.bill_number, b.url, b.stage
        ORDER BY
            CASE
                WHEN MAX(bp.pass_date::date) >= '{cutoff}' THEN 0
                WHEN MAX(bp.pass_date::date) IS NULL THEN 1
                ELSE 2
            END,
            MAX(bp.pass_date::date) DESC NULLS LAST,
            b.stage,
            b.registration_date DESC
        LIMIT {MAX_BILLS}
    """)

    active = sum(1 for r in rows if r['last_passing'] and str(r['last_passing']) >= cutoff)
    log.info("Hot bills: %d (active<=%dd: %d, stale/never: %d)", len(rows), days, active, len(rows) - active)
    return rows


def get_bill_by_number(bill_number: str) -> dict | None:
    """Отримує один закон за номером."""
    rows = d1_query("""
        SELECT id, bill_number, url, stage
        FROM bills
        WHERE bill_number = %s
    """, [bill_number])
    return rows[0] if rows else None


def extract_api_id(url: str) -> str | None:
    """Витягує API ID з URL (https://itd.rada.gov.ua/billinfo/Bills/Card/70129 → 70129)."""
    if not url:
        return None
    match = re.search(r'Card/(\d+)', url)
    return match.group(1) if match else None


def sync_bill_passings(bill: dict, existing_set: set) -> tuple[int, int]:
    """Синхронізує passings для одного закону. Повертає (inserted, skipped)."""
    bill_id = bill['id']
    url = bill.get('url', '')
    api_id = extract_api_id(url)

    if not api_id:
        log.debug("Bill %s: no API ID in URL", bill.get('bill_number'))
        return 0, 0

    card_url = f"https://itd.rada.gov.ua/billinfo/Bills/Card/{api_id}"
    html = fetch_html(card_url)
    if not html:
        return 0, 0

    passings = parse_passings_from_html(html)
    if not passings:
        log.debug("Bill %s: no passings found in HTML", bill.get('bill_number'))
        return 0, 0

    inserted = 0
    skipped = 0

    for p in passings:
        key = (bill_id, p['pass_date'], p['title'])
        if key in existing_set:
            skipped += 1
            continue

        d1_exec("raw_sql", {
            "sql": "INSERT INTO bill_passings (bill_id, pass_date, title, status) VALUES (?, ?, ?, ?) ON CONFLICT (bill_id, pass_date, title) DO NOTHING",
            "params": [bill_id, p['pass_date'], p['title'], p['status']]
        })
        inserted += 1
        existing_set.add(key)

    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(description="HTML-синхронізація хронології законів")
    parser.add_argument("--all", action="store_true", help="Перевірити всі stage 2,3,4")
    parser.add_argument("--bill", type=str, help="Один закон за номером (напр. 15294)")
    parser.add_argument("--days", type=int, default=7, help="Гарячі закони: passings старіші за N днів (default: 7)")
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Затримка між запитами (default: 1.5s)")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("HTML PASSINGS SYNC START")
    log.info("=" * 60)

    # Завантажуємо існуючі passings для дедупації
    existing_rows = d1_query("SELECT bill_id, pass_date::date as day, title FROM bill_passings")
    existing_set = {(r['bill_id'], str(r['day']), r['title']) for r in existing_rows}
    log.info("Existing passings: %d", len(existing_set))

    # Визначаємо список законів
    if args.bill:
        bill = get_bill_by_number(args.bill)
        if not bill:
            log.error("Bill %s not found", args.bill)
            return
        bills = [bill]
    elif args.all:
        bills = d1_query("""
            SELECT id, bill_number, url, stage
            FROM bills
            WHERE stage IN (2, 3, 4) AND url IS NOT NULL
            ORDER BY stage, registration_date DESC
            LIMIT %s
        """, [MAX_BILLS])
        log.info("All active bills: %d", len(bills))
    else:
        bills = get_hot_bills(args.days)

    if not bills:
        log.info("Nothing to sync. Done.")
        return

    total_inserted = 0
    total_skipped = 0
    errors = 0

    for i, bill in enumerate(bills, 1):
        try:
            inserted, skipped = sync_bill_passings(bill, existing_set)
            total_inserted += inserted
            total_skipped += skipped

            if inserted > 0:
                log.info("[%d/%d] Bill %s: +%d passings",
                         i, len(bills), bill.get('bill_number'), inserted)

            if i % 50 == 0:
                log.info("Progress: %d/%d | inserted=%d skipped=%d errors=%d",
                         i, len(bills), total_inserted, total_skipped, errors)

            time.sleep(args.delay)

        except Exception as e:
            log.error("Error on bill %s: %s", bill.get('bill_number'), str(e)[:200])
            errors += 1
            time.sleep(args.delay * 2)  # подвоюємо затримку після помилки

    log.info("=" * 60)
    log.info("HTML PASSINGS SYNC DONE | bills=%d inserted=%d skipped=%d errors=%d",
             len(bills), total_inserted, total_skipped, errors)
    log.info("=" * 60)

    # Оновлюємо status_changed_at
    if total_inserted > 0:
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
        log.info("Updated status_changed_at for affected bills")


if __name__ == "__main__":
    main()
