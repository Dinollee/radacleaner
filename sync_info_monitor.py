#!/usr/bin/env python3
"""sync_info_monitor.py — Phase 1 collector детектора синхронных инфоатак.

Собирает сырые посты в info_items из двух типов источников:
  1. Фактчекеры (RSS, stdlib xml.etree): ЦПД, VoxCheck, StopFake, Детектор медіа, SPRAVDI
  2. Деструктивные telegram-каналы (regex-парсинг https://t.me/s/<handle>,
     конфиг data/disinfo_channels.json)

Дедуп: url UNIQUE + лексический simhash (для будущей кластеризации кампаний).
CLI: --once (полный цикл, по умолчанию) / --dry-run (без записи в БД).
Запуск: systemd timer каждые 30 мин или вручную venv/bin/python sync_info_monitor.py
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '')}"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CHANNELS_FILE = Path(__file__).parent / "data" / "disinfo_channels.json"

FACTCHECK_SOURCES = [
    ("ЦПД", "https://cpd.gov.ua/feed/"),
    ("VoxCheck", "https://voxukraine.org/category/voxcheck-uk/feed"),
    ("StopFake", "https://stopfake.org/ru/feed/"),
    ("Детектор медіа", "https://detector.media/rss/"),
    ("SPRAVDI", "https://spravdi.gov.ua/feed/"),
]

TG_LAST_POSTS = 10   # последних постов канала за запуск
RSS_MAX_ITEMS = 20   # первых RSS-итемов источника за запуск
TG_PAUSE_SEC = 3     # пауза между каналами


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fetch(url, timeout=15):
    """GET с браузерным User-Agent → текст | None (ошибка не роняет запуск)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"WARN fetch {url}: {e}")
        return None


# ---------------------------------------------------------------- simhash ---
def norm_words(text):
    """Нормализация: нижний регистр, слова > 2 символов без пунктуации."""
    return re.findall(r"\w{3,}", text.lower(), re.UNICODE)


def simhash64(text):
    """Лексический simhash: 64 бита по 4-шинглам слов, побитовая свёртка."""
    words = norm_words(text)
    if len(words) < 4:
        words = words or [""]
        while len(words) < 4:
            words.append("")
    hashes = []
    for i in range(len(words) - 3):
        shingle = " ".join(words[i:i + 4])
        digest = hashlib.md5(shingle.encode("utf-8")).digest()
        hashes.append(int.from_bytes(digest[:8], "big"))
    bits = 0
    for b in range(64):
        ones = sum((h >> b) & 1 for h in hashes)
        if ones * 2 > len(hashes):
            bits |= 1 << b
    bits &= (1 << 64) - 1
    return bits - (1 << 64) if bits >= (1 << 63) else bits  # BIGINT signed


def strip_html(s):
    import html as htmllib
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


# ------------------------------------------------------------------- RSS ----
def parse_rss(xml_text):
    """RSS 2.0 через stdlib ElementTree → список dict(title,url,date,body)."""
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = item.findtext("title")
        link = item.findtext("link")
        if not title or not link:
            continue
        pub = item.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(pub.strip()) if pub.strip() else None
        except Exception:
            dt = None
        desc = strip_html(item.findtext("description") or "")[:2000]
        items.append({"title": strip_html(title), "url": link.strip(),
                      "posted": dt, "body": desc})
    return items


# -------------------------------------------------------------- telegram ----
MSG_BLOCK_RE = re.compile(r'(?=<div class="tgme_widget_message_wrap )')
POST_ID_RE = re.compile(r'data-post="([^"]+)"')
TIME_RE = re.compile(r'<time datetime="([^"]+)"')
TEXT_RE = re.compile(r'tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
FALLBACK_TEXT_RE = re.compile(r'js-single-message[^>]*>(.*?)</div>', re.DOTALL)


def parse_telegram(html):
    """Regex-парсинг t.me/s страницы → посты в хронологическом порядке."""
    posts = []
    seen = set()
    for block in MSG_BLOCK_RE.split(html):
        if not block.startswith('<div class="tgme_widget_message_wrap'):
            continue
        pid = POST_ID_RE.search(block)
        if not pid or pid.group(1) in seen:
            continue
        seen.add(pid.group(1))
        m = TEXT_RE.search(block) or FALLBACK_TEXT_RE.search(block)
        text = strip_html(m.group(1)) if m else ""
        if not text:
            continue  # медиа-пост без подписи — для кластеризации бесполезен
        t = TIME_RE.search(block)
        try:
            dt = datetime.fromisoformat(t.group(1)) if t else None
        except ValueError:
            dt = None
        posts.append({
            "url": f"https://t.me/{pid.group(1)}",
            "title": text[:120],
            "body": text[:4000],
            "posted": dt,
        })
    return posts


# ------------------------------------------------------------------- DB -----
def last_posted(cur, source_name):
    cur.execute("SELECT max(posted_at) FROM info_items WHERE source_name = %s", [source_name])
    row = cur.fetchone()
    return row[0] if row else None


def insert_items(conn, source_type, source_name, items):
    """INSERT ... ON CONFLICT (url) DO NOTHING → число реально вставленных."""
    cur = conn.cursor()
    n = 0
    for it in items:
        cur.execute(
            """INSERT INTO info_items
               (source_type, source_name, url, title, body, posted_at, simhash)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (url) DO NOTHING""",
            [source_type, source_name, it["url"], it["title"], it["body"],
             it["posted"], simhash64(it["title"] + " " + it["body"])])
        n += cur.rowcount
    conn.commit()
    cur.close()
    return n


def fresh_only(items, cutoff):
    """Оставить элементы новее последнего сохранённого posted_at источника."""
    out, seen = [], set()
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        if cutoff is not None and it["posted"] is not None and it["posted"] <= cutoff:
            continue
        out.append(it)
    return out


def run_cycle(dry_run=False):
    log("=== Info monitor: Phase 1 collector ===")
    collected = []  # (source_type, source_name, items)

    # 1. Фактчекеры (RSS)
    for name, url in FACTCHECK_SOURCES:
        xml_text = fetch(url)
        items = parse_rss(xml_text)[:RSS_MAX_ITEMS] if xml_text else []
        log(f"factcheck {name}: fetched {len(items)} items")
        collected.append(("factcheck", name, items))
        time.sleep(2)

    # 2. Telegram-каналы
    channels = json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    for ch in channels:
        html = fetch(f"https://t.me/s/{ch['handle']}")
        if html is None:
            continue  # WARN уже залогирован в fetch()
        if "tgme_widget_message_wrap" not in html:
            log(f"WARN telegram @{ch['handle']}: challenge/пустая страница, пропуск")
        else:
            posts = parse_telegram(html)[-TG_LAST_POSTS:]
            log(f"telegram @{ch['handle']} ({ch['name']}): fetched {len(posts)} posts")
            collected.append(("telegram", ch["name"], posts))
        time.sleep(TG_PAUSE_SEC)

    total_new = 0
    if dry_run:
        for stype, name, items in collected:
            log(f"DRY-RUN would insert {len(items):3d} [{stype}] {name}")
            total_new += len(items)
        log(f"DRY-RUN total: would insert ~{total_new} rows")
        return total_new

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        for stype, name, items in collected:
            cutoff = last_posted(cur, name)
            new_items = fresh_only(items, cutoff)
            inserted = insert_items(conn, stype, name, new_items)
            total_new += inserted
            log(f"{stype}/{name}: new {inserted}/{len(items)} (cutoff {cutoff})")
    finally:
        conn.close()

    log(f"DONE: inserted {total_new} new info_items")
    return total_new


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Info attack collector (Phase 1)")
    ap.add_argument("--once", action="store_true", help="один полный цикл (по умолчанию)")
    ap.add_argument("--dry-run", action="store_true", help="собрать, но не писать в БД")
    args = ap.parse_args()
    try:
        run_cycle(dry_run=args.dry_run)
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        sys.exit(1)
