#!/usr/bin/env python3
"""detect_attacks.py — Phase 2 burst detector синхронных инфоатак.

Читает info_items за последние 48ч, кластеризует union-find попарно
(hamming(simhash) <= 10 ИЛИ token-Jaccard >= 0.45), фильтрует бьорстом
(>= 4 телеграм-каналов, >= 8 постов, разброс <= ATTACK_WINDOW_HOURS/2),
ищет спростування у фактчекеров за 14 дней, линкует законопроекты,
охлаждает повторы той же кампании (реалерт только при эскалации >= 2x
постов) и шлёт украинский алерт в Telegram.

CLI: --dry-run (кластеры/кандидаты в stdout, БД и TG не трогаем),
     --no-send (всё кроме отправки TG, алерт печатается).
Запуск: systemd sync_info_monitor.service вторым ExecStart после collector'а.
"""
import argparse
import html
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from sync_info_monitor import norm_words

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '')}"
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

ATTACK_WINDOW_HOURS = 48       # окно загрузки info_items
SIMHASH_HAMMING_MAX = 10       # расстояние Хэмминга для копипаста
JACCARD_MIN = 0.45             # token-Jaccard для парафраза
BURST_MIN_CHANNELS = 4         # distinct telegram-каналов в кластере
BURST_MIN_POSTS = 8            # всего постов в кластере
BURST_SPREAD_HOURS = ATTACK_WINDOW_HOURS / 2  # временной разброс кластера
DEBUNK_DAYS = 14               # глубина поиска спростуваний
DEBUNK_OVERLAP_MIN = 0.25      # доля топ-токенов кластера в тексте фактчекера
COOLDOWN_HOURS = 24            # окно дедупликации кампаний
COOLDOWN_JACCARD = 0.6         # jaccard label-наборов => та же кампания
ESCALATION_X = 2               # реалерт только если постов >= 2x прошлых
TOP_TOKENS = 5                 # токенов в нарративе алерта

# номер рядом со словом «законопроєкт/закон/білль» либо после «№»
BILL_RE = re.compile(
    r"(?:законопроєкт|законопроект|закон|білль)[а-яїієґ']*\s*(?:№\s*)?(\d{4,5})\b"
    r"|№\s*(\d{4,5})\b", re.IGNORECASE)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------- pure functions ---
def hamming(a, b):
    """Расстояние Хэмминга 64-битных simhash (BIGINT может быть знаковым)."""
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / (len(a) + len(b) - len(a & b))


def find_clusters(items):
    """Union-find попарно: hamming(simhash) <= MAX ИЛИ jaccard(tokens) >= MIN.

    ponytail: O(n²) скан — при сотнях/парах тысяч записей это < сек;
    апгрейд при росте — бандинг по simhash-битам (LSH).
    """
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if (hamming(items[i]["simhash"], items[j]["simhash"]) <= SIMHASH_HAMMING_MAX
                    or jaccard(items[i]["tokens"], items[j]["tokens"]) >= JACCARD_MIN):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return sorted(groups.values(), key=lambda g: min(it["id"] for it in g))


def cluster_stats(cluster):
    tg = [it for it in cluster if it["source_type"] == "telegram"]
    times = sorted(it["posted_at"] for it in cluster if it["posted_at"])
    spread = (times[-1] - times[0]).total_seconds() / 3600 if len(times) > 1 else 0.0
    return {
        "posts": len(cluster),
        "tg_posts": len(tg),
        "channels": len({it["source_name"] for it in tg}),
        "spread_hours": spread,
    }


def is_burst(cluster):
    """Бьорст-правило. Factcheck-only никогда: TG-каналов у него 0."""
    st = cluster_stats(cluster)
    return (st["channels"] >= BURST_MIN_CHANNELS
            and st["posts"] >= BURST_MIN_POSTS
            and st["spread_hours"] <= BURST_SPREAD_HOURS)


def top_tokens(cluster, k=TOP_TOKENS):
    """Топ-k частотных токенов текстов кластера = label кампании."""
    freq = Counter()
    for it in cluster:
        freq.update(it["tokens"])
    return [w for w, _ in freq.most_common(k)]


def campaign_alert(prev_alerts, tokens, posts_count):
    """False если это та же кампания (jaccard label >= 0.6) без эскалации.

    prev_alerts: [(token_set, posts_count)] недавних алертов.
    Новая кампания или рост постов >= ESCALATION_X раз -> True.
    """
    for prev_tokens, prev_posts in prev_alerts:
        if jaccard(set(tokens), prev_tokens) >= COOLDOWN_JACCARD:
            return posts_count >= ESCALATION_X * prev_posts
    return True


def find_debunk(cluster, factchecks):
    """Самый свежий фактчек с token-overlap >= порога -> dict | None."""
    top = set(top_tokens(cluster))
    best = None
    for fc in factchecks:
        if not top or len(top & fc["tokens"]) / len(top) < DEBUNK_OVERLAP_MIN:
            continue
        if best is None or fc["posted_at"] > best["posted_at"]:
            best = fc
    return best


def extract_bill_numbers(texts):
    """Номера законопроектов рядом с ключевыми словами / после №, без дублей."""
    nums = []
    for t in texts:
        for m in BILL_RE.finditer(t):
            num = m.group(1) or m.group(2)
            if num not in nums:
                nums.append(num)
    return nums


def build_alert_text(cluster, debunk=None, bill_number=None):
    st = cluster_stats(cluster)
    hours = max(1, round(st["spread_hours"]))
    med = sorted(cluster, key=lambda it: (it["posted_at"], it["id"]))[len(cluster) // 2]
    sample = html.escape(f"{med['title']} {med['body'] or ''}".strip()[:140])
    lines = [
        "🚨 Синхронна хвиля публікацій",
        "",
        f"Нарратив: {', '.join(top_tokens(cluster))}",
        f"{st['posts']} постів у {st['channels']} каналах за {hours} год",
        f"Приклад: «{sample}…»",
        "",
    ]
    if debunk:
        lines.append(f"🔎 Спростування: {html.escape(debunk['title'])} — {debunk['url']}")
    else:
        lines.append("🔎 Спростування поки немає — стежимо")
    if bill_number:
        lines.append(f"📜 Законопроєкт №{bill_number}")
    lines += ["", "_Ознаки скоординованої хвилі; вердикт — за фактчекерами._"]
    return "\n".join(lines)


def send_telegram(text):
    """Паттерн sync_eu_tracker.send_telegram: env TG_BOT_TOKEN/TG_CHAT_ID."""
    if not TG_BOT_TOKEN:
        log("TG_BOT_TOKEN not set, skipping Telegram alert")
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log(f"WARN telegram: {e}")
        return False


# ------------------------------------------------------------------- DB -----
def load_window(cur, hours):
    """info_items за окно с посчитанными токенами текстов."""
    cur.execute(
        """SELECT id, source_type, source_name, url, title, body, posted_at, simhash
           FROM info_items
           WHERE posted_at >= now() - interval '%s hours' AND simhash IS NOT NULL""",
        [hours])
    items = []
    for r in cur.fetchall():
        it = dict(zip(["id", "source_type", "source_name", "url", "title",
                       "body", "posted_at", "simhash"], r))
        it["tokens"] = set(norm_words(f"{it['title']} {it['body'] or ''}"))
        items.append(it)
    return items


def load_factchecks(cur, days):
    cur.execute(
        """SELECT id, url, title, body, posted_at FROM info_items
           WHERE source_type = 'factcheck' AND posted_at >= now() - interval '%s days'""",
        [days])
    return [{"id": r[0], "url": r[1], "title": r[2], "body": r[3], "posted_at": r[4],
             "tokens": set(norm_words(f"{r[2]} {r[3] or ''}"))} for r in cur.fetchall()]


def load_recent_alerts(cur, hours):
    """[(token_set, posts_count)] алертов за окно cooldown."""
    cur.execute(
        """SELECT label, posts_count FROM attack_alerts
           WHERE detected_at >= now() - interval '%s hours' AND label IS NOT NULL""",
        [hours])
    return [(set(norm_words(label)), posts or 0) for label, posts in cur.fetchall()]


# ------------------------------------------------------------------ main ----
def run(dry_run=False, no_send=False):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        items = load_window(cur, ATTACK_WINDOW_HOURS)
        factchecks = load_factchecks(cur, DEBUNK_DAYS)
        recent = load_recent_alerts(cur, COOLDOWN_HOURS)
        clusters = find_clusters(items)
        log(f"{ATTACK_WINDOW_HOURS}г: {len(items)} items -> {len(clusters)} кластеров")

        candidates = []
        for cid, cluster in enumerate(clusters, 1):
            st = cluster_stats(cluster)
            if len(cluster) > 1:
                log(f"  кластер #{cid}: {st['posts']} постов, "
                    f"{st['channels']} TG-каналов, разброс {st['spread_hours']:.1f}ч")
            if is_burst(cluster):
                candidates.append(cluster)
        log(f"кандидатов (бьорст >= {BURST_MIN_CHANNELS} каналов / "
            f">{BURST_MIN_POSTS} постов / <= {BURST_SPREAD_HOURS:.0f}ч): {len(candidates)}")
        if dry_run:
            log("DRY-RUN: БД не трогаем, завершаемся")
            return

        cur.execute("SELECT coalesce(max(cluster_id), 0) FROM info_items")
        next_cid = (cur.fetchone()[0] or 0) + 1
        for cluster in candidates:
            st = cluster_stats(cluster)
            tokens = top_tokens(cluster)
            texts = [f"{it['title']} {it['body'] or ''}" for it in cluster]

            debunk = find_debunk(cluster, factchecks)
            bill_number = None
            for num in extract_bill_numbers(texts):
                cur.execute("SELECT 1 FROM bills WHERE bill_number = %s LIMIT 1", [num])
                if cur.fetchone():
                    bill_number = num
                    break

            if not campaign_alert(recent, tokens, st["posts"]):
                log(f"та же кампания ({', '.join(tokens)}) без эскалации "
                    f"({st['posts']} постов) — пропуск")
                continue

            text = build_alert_text(cluster, debunk, bill_number)
            if no_send:
                log(f"NO-SEND алерт:\n{text}")
                sent = False
            else:
                sent = send_telegram(text)

            first = min(cluster, key=lambda it: (it["posted_at"], it["id"]))
            cur.execute(
                """INSERT INTO attack_alerts
                   (first_item_id, label, channels_count, posts_count,
                    window_hours, debunk_url, related_bill_number, alert_sent)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [first["id"], ", ".join(tokens), st["channels"], st["posts"],
                 round(st["spread_hours"], 1),
                 debunk["url"] if debunk else None, bill_number, sent])
            cur.executemany("UPDATE info_items SET cluster_id = %s WHERE id = %s",
                            [[next_cid, it["id"]] for it in cluster])
            conn.commit()
            recent.append((set(tokens), st["posts"]))
            log(f"ALERT cluster_id={next_cid}: {st['posts']} постов, "
                f"{st['channels']} каналов, sent={sent}, "
                f"debunk={'да' if debunk else 'нет'}, bill={bill_number or '—'}")
            next_cid += 1
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Info attack burst detector (Phase 2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="кластеры/кандидаты в stdout, БД и TG не трогаем")
    ap.add_argument("--no-send", action="store_true",
                    help="всё кроме отправки TG (алерт печатается)")
    args = ap.parse_args()
    try:
        run(dry_run=args.dry_run, no_send=args.no_send)
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        sys.exit(1)
