#!/usr/bin/env python3
"""sync_mp_factions.py — Парсинг фракцій депутатів з RADA → D1.

Usage:
    python sync_mp_factions.py
"""
import re
import sys
import urllib.request

from src.config import log
from src.d1_client import d1_exec, d1_query


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # Handle gzip encoding
        if resp.headers.get('Content-Encoding') == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def parse_faction_name(raw_faction):
    """Нормалізація назви фракції."""
    if not raw_faction:
        return ""
    
    raw = raw_faction.lower()
    
    if "слуга народу" in raw or "слуга народа" in raw:
        return "СЛУГА НАРОДУ"
    elif "європейська солідарність" in raw:
        return "Європейська Солідарність"
    elif "батьківщина" in raw:
        return "Батьківщина"
    elif "платформа за життя" in raw or "за життя та мир" in raw:
        return "Платформа за життя та мир"
    elif "довіра" in raw:
        return "ДОВІРА"
    elif "за майбутнє" in raw:
        return 'Партія "За майбутнє"'
    elif "голос" in raw:
        return "ГОЛОС"
    elif "відновлення україни" in raw:
        return "Відновлення України"
    else:
        return "Позафракційні"


def sync_factions():
    """Парсинг сторінки депутатів та оновлення фракцій."""
    log.info("Fetching deputy list from RADA...")
    url = "https://people.rada.gov.ua/go/vr-mps"
    html = fetch_url(url)
    
    # Знаходимо всі карточки депутатів з data-faction і data-name
    # Структура: <li class="mp-card" data-name="..." data-faction="...">
    pattern = re.compile(
        r'data-name="([^"]*)"[^>]*data-faction="([^"]*)"',
        re.IGNORECASE | re.DOTALL
    )
    
    matches = pattern.findall(html)
    log.info("Found %d deputies with faction data", len(matches))
    
    if not matches:
        log.error("Could not parse deputy list")
        return
    
    # Групуємо по фракціях
    factions_count = {}
    updates = []
    
    for full_name, raw_faction in matches:
        faction = parse_faction_name(raw_faction)
        factions_count[faction] = factions_count.get(faction, 0) + 1
        
        # Шукаємо депутата в БД за ім'ям
        # RADA дає повне ім'я, в БД може бути скорочене
        # Спробуємо знайти за прізвищем
        parts = full_name.strip().split()
        if len(parts) < 1:
            continue
        
        last_name = parts[0]
        updates.append((faction, last_name))
    
    # Батч-оновлення через raw_sql
    batch_size = 20
    total_updated = 0
    
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i+batch_size]
        
        # Оновлюємо кожного депутата окремо
        for faction, last_name in batch:
            try:
                d1_exec("raw_sql", {
                    "sql": "UPDATE mps SET faction = ? WHERE name LIKE ?",
                    "params": [faction, f"%{last_name}%"]
                })
                total_updated += 1
            except Exception as e:
                log.warning("Update failed for %s: %s", last_name, str(e)[:100])
    
    log.info("Faction distribution:")
    for f, c in sorted(factions_count.items(), key=lambda x: -x[1]):
        log.info("  %s: %d", f, c)
    
    log.info("Updated %d deputies in database", total_updated)


if __name__ == "__main__":
    sync_factions()
