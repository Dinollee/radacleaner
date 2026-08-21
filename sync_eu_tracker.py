#!/usr/bin/env python3
"""sync_eu_tracker.py — Моніторинг EU кластерів з кількох джерел.

Джерела:
  1. EC RSS — enlargement.ec.europa.eu (news з фільтром по Україні)
  2. Європравда — eurointegration.com.ua (скрапінг статей)
  3. pulse.kmu.gov.ua — урядовий портал (прогрес)

Запуск: раз на день або вручну.
"""
import re
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

CLUSTER_KEYWORDS = ['кластер', 'cluster', 'відкриття переговорів', 'accession', 'acquis', 'screening']

# EU integration index v1: назви кластерів для консервативної детекції відкриттів
CLUSTER_NAME_PATTERNS = {
    1: r'fundamentals',
    2: r'internal market',
    3: r'competitiveness|inclusive growth',
    4: r'green agenda|sustainable connectivity',
    5: r'resources,\s*agriculture|resources and agriculture',
    6: r'external relations',
}


def detect_cluster_opening(title, summary=''):
    """Чиста функція: новина ЄК → cluster_id (1-6) або None.

    Консервативно: потрібні одночасно (а) контекст «accession negotiations»
    або «cluster», (б) дієслово відкриття, (в) номер або офіційна назва кластера.
    """
    text = f"{title} {summary}".lower()
    if not re.search(r'accession negotiation|cluster', text):
        return None
    if not re.search(r'\bopen(ed|s|ing)?\b|\blaunched?\b|\bstart(ed|s)?\b', text):
        return None
    m = re.search(r'cluster\s*(?:no\.?\s*)?([1-6])\b', text)
    if m:
        return int(m.group(1))
    for cid, pat in CLUSTER_NAME_PATTERNS.items():
        if re.search(pat, text):
            return cid
    return None


def mark_cluster_opened(conn, cluster_id, event_date=None, source_url=None):
    """UPSERT статусу 'opened' — тільки якщо статус ще 'not_opened' (не відкочуємо назад)."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT status FROM eu_cluster_status WHERE cluster_id = %s", [cluster_id])
        row = cur.fetchone()
        if row and row[0] != 'not_opened':
            return False
        cur.execute("""
            INSERT INTO eu_cluster_status (cluster_id, status, event_date, source_url, updated_at)
            VALUES (%s, 'opened', %s, %s, now())
            ON CONFLICT (cluster_id) DO UPDATE
            SET status = 'opened', event_date = EXCLUDED.event_date,
                source_url = EXCLUDED.source_url, updated_at = now()
        """, [cluster_id, event_date, source_url])
        return True
    finally:
        cur.close()


def parse_rss_date(s):
    """'Tue, 14 Jul 2026 15:01:46 +0200' → datetime.date | None."""
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        return None

# Telegram config
TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.getenv('TG_CHAT_ID', '349941927')


def send_telegram(text: str):
    """Надіслати повідомлення в Telegram."""
    if not TG_BOT_TOKEN:
        print("TG_BOT_TOKEN not set, skipping Telegram alert")
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            'chat_id': TG_CHAT_ID,
            'text': text[:4000],
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        }).encode()
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def fetch_ec_rss():
    """Отримання новин з EC RSS."""
    url = 'https://enlargement.ec.europa.eu/node/2/rss_en'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        xml = resp.read().decode('utf-8', errors='ignore')

        items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)
        results = []
        for item in items[:30]:
            title = re.search(r'<title>(.*?)</title>', item)
            link = re.search(r'<link>(.*?)</link>', item)
            pubdate = re.search(r'<pubDate>(.*?)</pubDate>', item)
            desc = re.search(r'<description>(.*?)</description>', item, re.DOTALL)
            if title and link:
                title_text = title.group(1)
                if any(kw.lower() in title_text.lower() for kw in CLUSTER_KEYWORDS + ['ukraine', 'україн']):
                    results.append({
                        'source': 'EC RSS',
                        'title': title_text,
                        'url': link.group(1),
                        'date': pubdate.group(1) if pubdate else '',
                        'summary': re.sub(r'<[^>]+>', '', desc.group(1)).strip() if desc else '',
                    })
        return results
    except Exception as e:
        print(f"EC RSS error: {e}")
        return []


def fetch_eurointegration():
    """Скрапінг новин з Європравди (EU accession section)."""
    url = 'https://www.eurointegration.com.ua/news/'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='ignore')

        # Find article links with cluster keywords
        articles = re.findall(r'<a[^>]*href="(/news/\d{4}/\d{2}/\d{2}/\d+/)"[^>]*>(.*?)</a>', html, re.DOTALL)
        results = []
        seen = set()
        for path, title in articles:
            title = re.sub(r'<[^>]+>', '', title).strip()
            if any(kw.lower() in title.lower() for kw in CLUSTER_KEYWORDS) and path not in seen:
                seen.add(path)
                results.append({
                    'source': 'Європравда',
                    'title': title,
                    'url': f'https://www.eurointegration.com.ua{path}',
                    'date': '',
                })
        return results[:10]
    except Exception as e:
        print(f"Європравда error: {e}")
        return []


def check_cluster_updates():
    """Перевірка оновлень кластерів."""
    print("=== EU Cluster Tracker ===")

    # Source 1: EC RSS
    print("\n1. EC RSS (Ukraine)...")
    ec_news = fetch_ec_rss()
    print(f"   Found {len(ec_news)} cluster-related articles")
    for item in ec_news[:3]:
        print(f"   - {item['title'][:60]}")

    # Source 2: Європравда
    print("\n2. Європравда...")
    ep_news = fetch_eurointegration()
    print(f"   Found {len(ep_news)} cluster-related articles")
    for item in ep_news[:3]:
        print(f"   - {item['title'][:60]}")

    # Combine and store
    all_news = ec_news + ep_news
    new_items = []

    if all_news:
        conn = psycopg2.connect(DB_DSN)
        cur = conn.cursor()

        for item in all_news:
            # Check if already tracked (by URL)
            cur.execute(
                "SELECT 1 FROM stats_cache WHERE key = %s",
                [f"eu_news_{hash(item['url']) % 1000000}"]
            )
            if cur.fetchone():
                continue

            # Store in stats_cache
            cur.execute(
                "INSERT INTO stats_cache (key, value, updated_at) VALUES (%s, %s, now() AT TIME ZONE 'utc')",
                [f"eu_news_{hash(item['url']) % 1000000}",
                 json.dumps(item, ensure_ascii=False)]
            )
            print(f"   NEW: [{item['source']}] {item['title'][:60]}")
            new_items.append(item)

        # EU index v1: авто-детекція відкриттів кластерів у нових новинах
        for item in new_items:
            cid = detect_cluster_opening(item['title'], item.get('summary', ''))
            if cid:
                if mark_cluster_opened(conn, cid, parse_rss_date(item.get('date', '')), item.get('url')):
                    print(f"   🎯 CLUSTER {cid} OPENED (auto-detected): {item['title'][:60]}")

        conn.commit()
        cur.close()
        conn.close()

    # Send Telegram alert for new cluster-related news
    if new_items:
        alert_lines = ["🇪🇺 <b>EU Cluster Tracker — нові новини:</b>\n"]
        for item in new_items[:5]:
            source = item['source']
            title = item['title'][:80]
            url = item.get('url', '')
            alert_lines.append(f"📌 <b>{source}</b>: {title}")
            if url:
                alert_lines.append(f"   <a href='{url}'>Деталі</a>")
            alert_lines.append("")

        alert_text = "\n".join(alert_lines)
        if send_telegram(alert_text):
            print(f"\n✅ Telegram alert sent ({len(new_items)} items)")

    print(f"\nTotal: {len(all_news)} cluster-related news items ({len(new_items)} new)")
    return all_news


if __name__ == "__main__":
    check_cluster_updates()
