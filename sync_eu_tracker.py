#!/usr/bin/env python3
"""sync_eu_tracker.py — Моніторинг EU кластерів з кількох джерел.

Джерела:
  1. EC RSS — enlargement.ec.europa.eu (news з фільтром по Україні)
  2. Європравда — eurointegration.com.ua (скрапінг статей)
  3. pulse.kmu.gov.ua — урядовий портал (прогрес)

Запуск: раз на день або вручну.

Дедуплікація (2026-09-07): стабільний хеш замість Python hash() (який
рандомізується між запусками → дублі в Telegram). Поле `translated: true`
в stats_cache value = переклад українською вже зроблено (не перекладати
повторно, не дублювати пуш).

LLM-переклад (2026-09-07): заголовки EC RSS → українська через nemotron,
батчем. Групування за категорією: «🔓 Відкриття кластера N» окремо,
«🇪🇺 Реформи / Фінансування» — окремо.
"""
import hashlib
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

try:
    from src.llm_client import llm_completion_raw, _parse_json
    HAS_LLM = True
except Exception:
    HAS_LLM = False

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

# Ключові слова для фільтра «cluster-related». Раніше був `'ukraine', 'україн'`,
# який ловив ВСІ новини про Україну (оборона, фінансування), не пов'язані з
# переговорами — дашборд показував сміття. Звужено до кластерних тем.
CLUSTER_KEYWORDS = ['кластер', 'cluster', 'відкриття переговорів', 'accession',
                    'acquis', 'screening', 'переговори про вступ']

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


CLUSTER_NAMES = {
    1: 'Основи (Fundamentals)',
    2: 'Внутрішній ринок',
    3: 'Конкурентоспроможність',
    4: 'Зелений порядок денний',
    5: 'Ресурси, сільське господарство',
    6: 'Зовнішні відносини',
}

LLM_SYS_TRANSLATE = ("Ти перекладач заголовків новин з англійської на українську. "
                     "Відповідай ТІЛЬКИ валідним JSON без пояснень, коментарів, Markdown. "
                     "Стисло, інформативно. Зберігай власні назви та абревіатури.")


def news_key(url: str) -> str:
    """Стабільний ключ для stats_cache: md5(url), 12 hex символів.
    Python hash() рандомізується між запусками (PYTHONHASHSEED) → був дуб.
    """
    return "eu_news_" + hashlib.md5(url.encode()).hexdigest()[:12]


def _is_ukrainian(s: str) -> bool:
    """Мовний гейт (українська): ≥70% кириличних літер + латинських <30%."""
    if not s:
        return False
    cyr = sum(1 for c in s if '\u0400' <= c <= '\u04FF')
    lat = sum(1 for c in s if c.isascii() and c.isalpha())
    letters = cyr + lat
    if letters < 5:
        return False
    return (lat / letters) < 0.30


def translate_titles_to_uk(items: list) -> None:
    """Переклад заголовків EC RSS (en→uk) батчем через nemotron.

    Уже перекладені (`translated: true` в stats_cache) — не чіпаємо. Тільки ті,
    що мають `uk_title: ""` і `lang == "en"`. Якщо LLM недоступний — залишаємо
    оригінал англійською, пуш все одно збираємо (однаково зрозуміло).
    """
    if not HAS_LLM:
        return
    todo = [(i, it) for i, it in enumerate(items)
            if it.get("lang") == "en" and not it.get("uk_title")]
    if not todo:
        return

    prompt = "Переклади українською ці заголовки. Поверни JSON-масив [{i, uk}]:\n\n"
    for i, it in todo:
        title = re.sub(r"<[^>]+>", "", it["title"]).strip()
        prompt += f'{i}. {title}\n'
    prompt += "\nВідповідь: JSON-масив."

    try:
        raw = llm_completion_raw(prompt, system_prompt=LLM_SYS_TRANSLATE,
                                 temperature=0.1, max_tokens=1500)
    except Exception as e:
        print(f"LLM translate error: {e}")
        return

    parsed = _parse_json(raw)
    if not isinstance(parsed, list):
        return
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        i = entry.get("i")
        uk = entry.get("uk", "").strip()
        if i is None or not uk or not _is_ukrainian(uk):
            continue
        if 0 <= i < len(items):
            items[i]["uk_title"] = uk
            items[i]["translated"] = True


def detect_category(item: dict) -> str:
    """Класифікація новини: cluster_opening | reform | finance | other."""
    cid = detect_cluster_opening(item.get("title", ""), item.get("summary", ""))
    if cid:
        return f"cluster_opening:{cid}"
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    if any(kw in text for kw in ["fund", "billion", "million", "€", "грн", "фінанс", "investment"]):
        return "finance"
    if any(kw in text for kw in ["reform", "law", "legislat", "реформ", "закон"]):
        return "reform"
    return "other"


def format_alert(new_items: list) -> str:
    """Telegram-пуш: групування за категорією, українською, без дублів."""
    by_cat: dict[str, list] = {}
    for it in new_items:
        by_cat.setdefault(detect_category(it), []).append(it)

    lines = ["🇪🇺 <b>Євроінтеграція — оновлення</b>\n"]

    cluster_openings = sorted(c for c in by_cat if c.startswith("cluster_opening:"))
    if cluster_openings:
        lines.append("<b>🔓 Відкриття кластерів:</b>")
        for c in cluster_openings:
            cid = int(c.split(":")[1])
            name = CLUSTER_NAMES.get(cid, f"кластер {cid}")
            for it in by_cat[c]:
                title = it.get("uk_title") or it["title"]
                title = re.sub(r"<[^>]+>", "", title)[:200]
                url = it.get("url", "")
                lines.append(f"• Кластер {cid} ({name}) — {title}")
                if url:
                    lines.append(f"  <a href='{url}'>деталі</a>")
        lines.append("")

    for cat, header in [("reform", "📜 Реформи / закони"),
                        ("finance", "💰 Фінансування"),
                        ("other", "📰 Інше")]:
        if cat not in by_cat:
            continue
        lines.append(f"<b>{header}:</b>")
        for it in by_cat[cat]:
            title = it.get("uk_title") or it["title"]
            title = re.sub(r"<[^>]+>", "", title)[:200]
            src = it.get("source", "")
            url = it.get("url", "")
            lines.append(f"• {title}")
            if url:
                lines.append(f"  {src} · <a href='{url}'>деталі</a>")
        lines.append("")

    return "\n".join(lines).strip()


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
            key = news_key(item['url'])
            cur.execute("SELECT value FROM stats_cache WHERE key = %s", [key])
            row = cur.fetchone()
            if row:
                # Якщо вже є — оновлюємо uk_title з існуючого value (щоб не
                # перекладати повторно); але в new_items не додаємо.
                continue

            item['lang'] = 'en' if item['source'] == 'EC RSS' else 'uk'
            item['uk_title'] = '' if item['lang'] == 'en' else item['title']

            cur.execute(
                "INSERT INTO stats_cache (key, value, updated_at) VALUES (%s, %s, now() AT TIME ZONE 'utc')",
                [key, json.dumps(item, ensure_ascii=False)]
            )
            print(f"   NEW: [{item['source']}] {item['title'][:60]}")
            new_items.append(item)

        # EU index v1: авто-детекція відкриттів кластерів у нових новинах
        opened = []
        for item in new_items:
            cid = detect_cluster_opening(item['title'], item.get('summary', ''))
            if cid:
                if mark_cluster_opened(conn, cid, parse_rss_date(item.get('date', '')), item.get('url')):
                    opened.append(cid)
                    print(f"   🎯 CLUSTER {cid} OPENED (auto-detected): {item['title'][:60]}")

        # Переклад en→uk батчем (тільки для нових EC RSS)
        if new_items:
            translate_titles_to_uk(new_items)
            # Зберегти uk_title назад у stats_cache (translated: true)
            for item in new_items:
                if item.get('translated'):
                    key = news_key(item['url'])
                    cur.execute(
                        "UPDATE stats_cache SET value = %s WHERE key = %s",
                        [json.dumps(item, ensure_ascii=False), key]
                    )

        conn.commit()
        cur.close()
        conn.close()

    # Send Telegram alert for new cluster-related news
    if new_items:
        alert_text = format_alert(new_items)
        if send_telegram(alert_text):
            print(f"\n✅ Telegram alert sent ({len(new_items)} items)")

    print(f"\nTotal: {len(all_news)} cluster-related news items ({len(new_items)} new)")
    return all_news


if __name__ == "__main__":
    check_cluster_updates()
