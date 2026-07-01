#!/usr/bin/env python3
"""sync_mp_factions.py — Парсинг фракцій та дат депутатів з RADA → D1.

Usage:
    python sync_mp_factions.py
"""
import re
import urllib.request

from src.config import log
from src.d1_client import d1_exec


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def parse_faction_name(raw_faction):
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


def parse_mp_cards(html):
    """Парсинг mp-card li з data-name, data-faction, data-start-date, data-end-date."""
    pattern = re.compile(
        r'data-name="([^"]*)"[^>]*data-faction="([^"]*)"[^>]*'
        r'data-fr_id="([^"]*)"[^>]*'
        r'data-start-date="([^"]*)"[^>]*data-end-date="([^"]*)"',
        re.IGNORECASE | re.DOTALL
    )
    return pattern.findall(html)


def full_name_to_initials(full_name):
    """Конвертує 'Юрчишин Петро Васильович' → 'Юрчишин П.В.'"""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return full_name
    last_name = parts[0]
    initials = ''.join(f"{p[0]}." for p in parts[1:] if p)
    return f"{last_name} {initials}"


def sync_factions():
    log.info("Fetching deputy list from RADA...")
    active_html = fetch_url("https://people.rada.gov.ua/go/vr-mps")
    left_html = fetch_url("https://people.rada.gov.ua/go/vr-exmps")

    active_cards = parse_mp_cards(active_html)
    left_cards = parse_mp_cards(left_html)
    log.info("Active: %d, Left: %d", len(active_cards), len(left_cards))

    all_cards = active_cards + left_cards
    if not all_cards:
        log.error("Could not parse deputy list")
        return

    factions_count = {}
    total_updated = 0

    for full_name, raw_faction, fr_id, start_date, end_date in all_cards:
        faction = parse_faction_name(raw_faction)
        factions_count[faction] = factions_count.get(faction, 0) + 1

        db_name = full_name_to_initials(full_name)
        if not db_name:
            continue

        try:
            d1_exec("raw_sql", {
                "sql": "UPDATE mps SET faction = ?, start_date = ?, end_date = ? WHERE name = ?",
                "params": [
                    faction,
                    start_date if start_date else None,
                    end_date if end_date else None,
                    db_name,
                ],
            })
            total_updated += 1
        except Exception as e:
            log.warning("Update failed for %s: %s", db_name, str(e)[:100])

    log.info("Faction distribution:")
    for f, c in sorted(factions_count.items(), key=lambda x: -x[1]):
        log.info("  %s: %d", f, c)

    log.info("Updated %d deputies in database", total_updated)


if __name__ == "__main__":
    sync_factions()
