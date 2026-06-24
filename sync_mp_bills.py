#!/usr/bin/env python3
"""sync_mp_bills.py — Парсинг законопроектів депутатів з RADA → D1.

Usage:
    python sync_mp_bills.py              — всі депутати
    python sync_mp_bills.py --user 21211 — один депутат
"""
import re
import sys
import time
import urllib.request
import gzip

from src.config import log
from src.d1_client import d1_exec, d1_query


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def parse_deputy_bills(html):
    """Парсинг таблиці законопроектів депутата."""
    bills = []
    
    # Знаходимо рядки таблиці
    # Структура: <tr><td>№</td><td><a>номер</a></td><td>дата</td><td>назва</td><td><a>акт</a></td></tr>
    pattern = re.compile(
        r'<tr>\s*<td>\d+</td>\s*'
        r'<td><a[^>]*>([^<]+)</a></td>\s*'  # номер реєстр.
        r'<td>([^<]+)</td>\s*'  # дата
        r'<td>([^<]+)</td>\s*'  # назва
        r'<td>(?:<a[^>]*>([^<]*)</a>|([^<]*))</td>\s*'  # став чинним актом
        r'</tr>',
        re.DOTALL
    )
    
    for match in pattern.finditer(html):
        reg_number = match.group(1).strip()
        reg_date = match.group(2).strip()
        title = match.group(3).strip()
        law_number = (match.group(4) or match.group(5) or "").strip()
        
        bills.append({
            "reg_number": reg_number,
            "reg_date": reg_date,
            "title": title,
            "law_number": law_number,
            "is_law": bool(law_number),
        })
    
    return bills


def get_deputy_user_id(deputy_name):
    """Отримання userId депутата з RADA."""
    # Шукаємо депутата на сторінці депутатів
    url = "https://people.rada.gov.ua/go/vr-mps"
    html = fetch_url(url)
    
    # Знаходимо data-name та URL картки
    pattern = re.compile(
        r'data-name="([^"]*)"[^>]*>.*?<a[^>]*href="([^"]*)"',
        re.DOTALL
    )
    
    for match in pattern.finditer(html):
        name = match.group(1).strip()
        card_url = match.group(2).strip()
        
        # Перевіряємо чи це наш депутат
        if name == deputy_name or deputy_name in name:
            # Витягуємо userId з URL
            # URL: /body/view/mp-but11_skl9
            user_match = re.search(r'mp-(\w+)_skl9', card_url)
            if user_match:
                return user_match.group(1)
    
    return None


def sync_deputy_bills(user_id, deputy_name):
    """Синхронізація законопроектів депутата."""
    url = f"https://itd.rada.gov.ua/billInfo/LawmakingActivity/deputies/{user_id}/10"
    html = fetch_url(url)
    
    bills = parse_deputy_bills(html)
    
    if not bills:
        log.warning("No bills found for %s (user_id=%s)", deputy_name, user_id)
        return 0, 0
    
    total_bills = len(bills)
    total_laws = sum(1 for b in bills if b["is_law"])
    
    # Зберігаємо в БД
    for bill in bills:
        try:
            d1_exec("raw_sql", {
                "sql": """INSERT INTO mp_bills (mp_name, reg_number, reg_date, title, law_number, is_law)
                          VALUES (?, ?, ?, ?, ?, ?)
                          ON CONFLICT(mp_name, reg_number) DO UPDATE SET
                            law_number=excluded.law_number, is_law=excluded.is_law""",
                "params": [
                    deputy_name, bill["reg_number"], bill["reg_date"],
                    bill["title"][:500], bill["law_number"], 1 if bill["is_law"] else 0
                ]
            })
        except Exception as e:
            log.warning("Failed to save bill %s for %s: %s", bill["reg_number"], deputy_name, str(e)[:100])
    
    log.info("%s: %d bills, %d laws (%.0f%%)", deputy_name, total_bills, total_laws,
             (total_laws / total_bills * 100) if total_bills > 0 else 0)
    
    return total_bills, total_laws


def get_all_deputies():
    """Отримання списку депутатів з БД."""
    result = d1_query("SELECT id, name FROM mps ORDER BY name")
    return result


def get_deputy_user_id(card_url):
    """Отримання userId депутата з його сторінки."""
    # card_url: https://people.rada.gov.ua/body/view/mp-but11_skl9
    html = fetch_url(card_url)
    
    # Знаходимо userId
    match = re.search(r'userId=(\d+)', html)
    if match:
        return match.group(1)
    
    return None


def sync_all():
    """Синхронізація законопроектів всіх депутатів."""
    deputies = get_all_deputies()
    log.info("Found %d deputies to sync", len(deputies))
    
    # Спочатку отримуємо маппінг card_url → userId
    url = "https://people.rada.gov.ua/go/vr-mps"
    html = fetch_url(url)
    
    # Знаходимо всі картки депутатів з data-name та URL
    card_pattern = re.compile(
        r'<li[^>]*class="mp-card"[^>]*data-name="([^"]*)"[^>]*>.*?<a[^>]*href="(https://people\.rada\.gov\.ua/body/view/mp-[^"]*)"',
        re.DOTALL
    )
    
    card_url_map = {}
    for match in card_pattern.finditer(html):
        name = match.group(1).strip()
        card_url = match.group(2).strip()
        card_url_map[name] = card_url
    
    log.info("Found %d deputies with card URLs", len(card_url_map))
    
    # Створюємо маппінг за прізвищем
    last_name_map = {}
    for name, url in card_url_map.items():
        parts = name.split()
        if parts:
            last_name = parts[0]
            last_name_map[last_name] = (name, url)
    
    # Синхронізуємо кожного депутата
    total_synced = 0
    total_errors = 0
    for deputy in deputies:
        name = deputy["name"]
        
        # Шукаємо за прізвищем
        parts = name.split()
        if not parts:
            continue
        
        last_name = parts[0]
        match = last_name_map.get(last_name)
        
        if not match:
            log.debug("No match for %s, skipping", name)
            continue
        
        full_name, card_url = match
        
        try:
            # Отримуємо userId з картки депутата
            user_id = get_deputy_user_id(card_url)
            if not user_id:
                log.warning("No user ID found for %s", name)
                continue
            
            bills, laws = sync_deputy_bills(user_id, name)
            total_synced += 1
            time.sleep(0.5)  # Пауза між запитами
        except Exception as e:
            total_errors += 1
            log.error("Failed to sync %s: %s", name, str(e)[:200])
            if total_errors > 20:
                log.error("Too many errors, stopping")
                break
            time.sleep(2)  # Longer pause on error
    
    log.info("Synced %d deputies, %d errors", total_synced, total_errors)


if __name__ == "__main__":
    args = sys.argv[1:]
    
    if not args:
        sync_all()
    elif args[0] == "--user" and len(args) > 1:
        user_id = args[1]
        # Шукаємо депутата за user_id
        deputies = get_all_deputies()
        log.info("Syncing bills for user_id=%s", user_id)
        # Тут потрібно знайти ім'я депутата за user_id
        # Поки що просто синхронізуємо
        url = f"https://itd.rada.gov.ua/billInfo/LawmakingActivity/deputies/{user_id}/10"
        html = fetch_url(url)
        bills = parse_deputy_bills(html)
        log.info("Found %d bills", len(bills))
    else:
        print(__doc__)
