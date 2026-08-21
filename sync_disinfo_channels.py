#!/usr/bin/env python3
"""sync_disinfo_channels.py — daily refresh of the disinfo channel watchlist.

Sources:
  1. SBU named list via 5.ua article (quoted channel names after «увійшли»)
  2. Handle resolution for NEW names: our SearXNG (serch.h.dino.pp.ua),
     query '"<name>" site:t.me', verify og:title fuzzy-matches the name
  3. Liveness pass over every configured channel (t.me/s fetch);
     dead_streak >= DEAD_STREAK_MAX -> prune (transient failures don't prune)

Writes data/disinfo_channels.json only if changed. Guards:
  - if parsed SBU names == 0 -> no adds (source layout may have changed)
  - if resulting list < MIN_CHANNELS -> abort write entirely
TG notification to admin: added / pruned / unresolved-new-names.
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import psycopg2  # noqa: F401 (kept for env parity with other scripts)
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

ROOT = Path(__file__).parent
CONFIG = ROOT / "data" / "disinfo_channels.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
SBU_ARTICLE = "https://www.5.ua/dv/life/272194"
SEARX = "https://serch.h.dino.pp.ua/search"
DEAD_STREAK_MAX = 3
MIN_CHANNELS = 10
TG_PAUSE_SEC = 2


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log(f"WARN fetch {url}: {e}")
        return None


def norm_name(s):
    """UA/RU spelling fold: и/ы/і->i, є/ё/е->e, drop ъ/ь/apostrophes, non-letters."""
    s = (s or "").lower()
    table = str.maketrans({"и": "i", "ы": "i", "і": "i", "ї": "i",
                           "є": "e", "ё": "e", "ъ": "", "ь": "",
                           "ʼ": "", "'": "", "’": ""})
    return re.sub(r"[^a-zа-яёіїєґ ]", "", s.translate(table)).strip()


def tg_alive(handle):
    """t.me/s/<handle> отвечает и содержит посты."""
    h = fetch(f"https://t.me/s/{handle}")
    return bool(h) and "tgme_widget_message_wrap" in h


def tg_title(handle):
    h = fetch(f"https://t.me/s/{handle}")
    if not h:
        return ""
    m = re.search(r'<meta property="og:title" content="([^"]*)"', h)
    return m.group(1) if m else ""


def sbu_names_from_article():
    """Именованный список СБУ из статьи 5.ua: фрагмент после «увійшли»."""
    h = fetch(SBU_ARTICLE)
    if not h:
        return []
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.DOTALL)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    i = t.lower().find("увійшли")
    if i < 0:
        return []
    seg = t[i:i + 1200]
    end = seg.find(". ")          # конец предложения = конец списка имён
    if end > 0:
        seg = seg[:end]
    names = re.findall(r'[«"]([^«»"]{3,50})[»"]', seg)
    return [n.strip() for n in names if not n.strip().isdigit()][:30]


def resolve_handle(name):
    """SearXNG '\"name\" site:t.me' -> первый живой канал с совпадающим og:title."""
    q = urllib.request.quote(f'"{name}" site:t.me')
    raw = fetch(f"{SEARX}?q={q}&format=json")
    if not raw:
        return None
    try:
        results = json.loads(raw).get("results", [])
    except Exception:
        return None
    target = norm_name(name)
    seen = set()
    for r in results[:10]:
        m = re.match(r"https?://t\.me/(?:s/)?([A-Za-z0-9_]{4,32})/?$", r.get("url", ""))
        if not m:
            continue
        handle = m.group(1)
        if handle in seen:
            continue
        seen.add(handle)
        title = tg_title(handle)
        nt = norm_name(title)
        if nt and (nt == target or target in nt or nt in target):
            return handle
        time.sleep(TG_PAUSE_SEC)
    return None


def send_telegram(text):
    token = os.getenv("TG_BOT_TOKEN", "")
    chat = os.getenv("TG_CHAT_ID", "")
    if not token:
        log("TG_BOT_TOKEN not set, skip notify")
        return
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text[:3500]}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            json.load(r)
    except Exception as e:
        log(f"WARN telegram: {e}")


def main(dry_run=False):
    channels = json.loads(CONFIG.read_text(encoding="utf-8"))
    by_name = {c["name"]: c for c in channels}
    added, pruned, unresolved = [], [], []

    # 1. Новые имена из списка СБУ
    names = sbu_names_from_article()
    log(f"SBU article: parsed {len(names)} names")
    known = set(by_name) | {norm_name(n) for n in by_name}
    for n in names:
        if n in by_name or norm_name(n) in known:
            continue
        handle = resolve_handle(n)
        if handle and not dry_run:
            entry = {"name": n, "handle": handle,
                     "origin": "sbu_auto", "added": date.today().isoformat()}
            channels.append(entry)
            by_name[n] = entry
            added.append(f"{n} (@{handle})")
        else:
            unresolved.append(n)
        time.sleep(TG_PAUSE_SEC)

    # 2. Проверка живости всех каналов
    alive_channels = []
    for c in channels:
        ok = tg_alive(c["handle"])
        streak = 0 if ok else c.get("dead_streak", 0) + 1
        if ok:
            c.pop("dead_streak", None)
            alive_channels.append(c)
        elif streak < DEAD_STREAK_MAX:
            c["dead_streak"] = streak
            alive_channels.append(c)
        else:
            pruned.append(f"@{c['handle']} ({c['name']})")
        log(f"@{c['handle']}: {'ok' if ok else f'dead_streak={streak}'}")
        time.sleep(TG_PAUSE_SEC)

    # 3. Запись с защитой
    changed = len(added) > 0 or len(pruned) > 0 or \
        any(c.get("dead_streak") for c in alive_channels)
    if len(alive_channels) < MIN_CHANNELS:
        log(f"ABORT: would leave {len(alive_channels)} channels (<{MIN_CHANNELS})")
        return
    if not dry_run and changed:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(alive_channels, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(CONFIG)

    # 4. Отчёт админу (TG — только при изменении набора нерешённых/списка)
    parts = ["📋 Оновлення списку каналів моніторингу (СБУ):"]
    if added:
        parts.append("➕ Додано: " + "; ".join(added))
    if pruned:
        parts.append("➖ Видалено (мертві ≥3 днів): " + "; ".join(pruned))
    if unresolved:
        parts.append("⚠️ Нові в списку СБУ, хендл не знайдено автоматично: "
                     + ", ".join(unresolved))
    log(" | ".join(parts))
    state = ROOT / "data" / ".disinfo_unresolved.json"
    prev = json.loads(state.read_text()) if state.exists() else []
    if not dry_run and sorted(unresolved) != sorted(prev):
        send_telegram("\n".join(parts))
        state.write_text(json.dumps(unresolved, ensure_ascii=False))


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
