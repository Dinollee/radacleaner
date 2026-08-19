#!/usr/bin/env python3
"""sync_pulse.py — Синхронізація прогресу виконання Угоди з pulse.kmu.gov.ua.

Джерело: https://pulse.kmu.gov.ua/ — моніторинг 24 напрямків асоціації.
Оновлює: mps.eu_integration_score (загальний прогрес) + stats_cache.
"""
import re
import os
import urllib.request
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"


def fetch_pulse_data():
    """Отримання даних з pulse.kmu.gov.ua."""
    req = urllib.request.Request('https://pulse.kmu.gov.ua/', headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8', errors='ignore')

    # Parse area names and percentages
    text_blocks = re.findall(r'>([^<]+)<', html)
    areas = []
    for i, block in enumerate(text_blocks):
        block = block.strip()
        if '%' in block and len(block) < 10:
            for j in range(max(0, i - 5), i):
                prev = text_blocks[j].strip()
                if len(prev) > 10 and '%' not in prev and not prev.startswith('<'):
                    name = re.sub(r'&#\d+;', '', prev).strip()
                    pct = int(re.search(r'(\d+)', block).group(1))
                    areas.append({'name': name, 'progress': pct})
                    break

    return areas


def sync_pulse():
    """Синхронізація даних pulse.kmu.gov.ua."""
    print("Fetching pulse.kmu.gov.ua...")
    areas = fetch_pulse_data()
    print(f"Found {len(areas)} areas")

    if not areas:
        print("No data found!")
        return

    # Calculate overall progress
    avg_progress = sum(a['progress'] for a in areas) / len(areas)
    print(f"Overall progress: {avg_progress:.1f}%")

    # Store in stats_cache
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # Save individual areas
    for area in areas:
        cur.execute("""
            INSERT INTO stats_cache (key, value, updated_at)
            VALUES (%s, %s, now() AT TIME ZONE 'utc')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (f"pulse_{area['name'][:50]}", str(area['progress'])))

    # Save overall
    cur.execute("""
        INSERT INTO stats_cache (key, value, updated_at)
        VALUES (%s, %s, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, ("pulse_overall", f"{avg_progress:.1f}"))

    # Save all areas as JSON
    import json
    cur.execute("""
        INSERT INTO stats_cache (key, value, updated_at)
        VALUES (%s, %s, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, ("pulse_areas", json.dumps(areas, ensure_ascii=False)))

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nTop 5 areas:")
    for a in sorted(areas, key=lambda x: x['progress'], reverse=True)[:5]:
        print(f"  {a['progress']:>3}% {a['name'][:50]}")

    print(f"\nBottom 5 areas:")
    for a in sorted(areas, key=lambda x: x['progress'])[:5]:
        print(f"  {a['progress']:>3}% {a['name'][:50]}")


if __name__ == "__main__":
    sync_pulse()
