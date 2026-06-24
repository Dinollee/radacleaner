"""sync_mp_dates.py — Синхронізація дат вступу/вибуття депутатів з RADA.

Використання:
    ./venv/bin/python sync_mp_dates.py
"""

import os
import re
import time
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

from src.d1_client import d1_exec


def fetch_mp_dates():
    """Отримуємо дані про депутатів з RADA."""
    url = "https://people.rada.gov.ua/go/vr-mps"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Radacleaner/1.0)",
        "Accept": "text/html",
    }
    
    log.info("Fetching deputies from %s", url)
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    
    html = resp.text
    
    # Парсимо data-атрибути з карток депутатів
    # Патерн: data-name="..." data-start-date="..." data-end-date="..."
    pattern = r'data-name="([^"]+)"[^>]*data-faction="[^"]*"[^>]*data-fr_id="[^"]*"[^>]*data-start-date="([^"]*)"[^>]*data-end-date="([^"]*)"'
    
    matches = re.findall(pattern, html)
    
    deputies = []
    for name, start_date, end_date in matches:
        # Декодуємо HTML entities
        name = name.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        deputies.append({
            "name": name,
            "start_date": start_date if start_date else None,
            "end_date": end_date if end_date else None,
        })
    
    log.info("Found %d deputies", len(deputies))
    return deputies


def update_mp_dates(deputies):
    """Оновлюємо дати в нашій базі через Worker.
    
    Імена в базі скорочені ("Абдуллін О.Р."), а з RADA — повні.
    Шукаємо по прізвищу (перше слово) для узгодження.
    """
    updated = 0
    errors = 0
    
    for dep in deputies:
        try:
            # Отримуємо прізвище (перше слово повного імені)
            last_name = dep["name"].split()[0]
            
            # Шукаємо по прізвищу та оновлюємо
            sql = "UPDATE mps SET start_date = ?, end_date = ? WHERE name LIKE ?"
            params = [dep["start_date"], dep["end_date"], f"{last_name}%"]
            
            d1_exec("raw_sql", {"sql": sql, "params": params})
            updated += 1
            
            if updated % 50 == 0:
                log.info("Updated %d deputies...", updated)
                
        except Exception as e:
            log.warning("Failed to update %s: %s", dep["name"], str(e)[:100])
            errors += 1
        
        time.sleep(0.1)  # Не перевантажуємо API
    
    log.info("Done: %d updated, %d errors", updated, errors)
    return updated, errors


def main():
    log.info("=== Синхронізація дат депутатів ===")
    
    deputies = fetch_mp_dates()
    if not deputies:
        log.error("No deputies found")
        return
    
    # Показуємо приклад
    log.info("Sample: %s", deputies[0])
    
    updated, errors = update_mp_dates(deputies)
    
    log.info("=== Готово: %d оновлено, %d помилок ===", updated, errors)


if __name__ == "__main__":
    main()
