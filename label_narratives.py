#!/usr/bin/env python3
"""label_narratives.py — Phase 3 ночной дайджест інфоатак.

За 24ч собирает: (а) топ-15 кластеров telegram/factcheck — переиспользует
detect_attacks.load_window/find_clusters; (б) все factcheck-итемы суток
(= «фейки з спростуваннями» дня). Ровно ДВА LLM-вызова (nemotron):
  1) метки нарративов кластеров [{id,label,category}];
  2) значимость + суть factcheck-заголовков [{i,significance,one_line}].
Результат → stats_cache 'info_digest' (читают /api/info-digest и вкладка
«Інфоатаки»). Мусор/не-JSON от LLM скрипт не роняет: label ← топ-токены,
significance/one_line остаются пустыми.
Запуск: systemd label_narratives.timer daily 07:15.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from detect_attacks import cluster_stats, find_clusters, load_window, top_tokens
from src.llm_client import _parse_json, llm_completion_raw

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '')}"

TOP_CLUSTERS = 15
CATEGORIES = {"закони", "вибори", "мобілізація", "інше"}
LLM_SYS = ("Ти аналітик інформаційного простору України. Відповідай ТІЛЬКИ валідним "
           "JSON без пояснень, коментарів та Markdown.")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------- collection ---
def collect_clusters(cur):
    """Топ-N кластеров доба → [{id,size,channels,sample_title,tokens}]."""
    items = load_window(cur, 24)
    ranked = sorted(find_clusters(items), key=len, reverse=True)[:TOP_CLUSTERS]
    rows = []
    for n, c in enumerate(ranked, 1):
        st = cluster_stats(c)
        med = sorted(c, key=lambda it: (it["posted_at"], it["id"]))[len(c) // 2]
        rows.append({"id": n, "size": st["posts"], "channels": st["channels"],
                     "sample_title": med["title"][:200], "tokens": top_tokens(c)})
    return rows


def collect_fakes(cur):
    """Все factcheck-итемы за 24ч → [{title,url,source}] (порядок RSS)."""
    cur.execute(
        """SELECT id, url, title, source_name FROM info_items
           WHERE source_type = 'factcheck' AND posted_at >= now() - interval '24 hours'""")
    return [{"title": t, "url": u, "source": s}
            for _id, u, t, s in cur.fetchall()]


# ------------------------------------------------------------------- LLM ----
def ask_llm(prompt):
    """llm_completion_raw → распарсенный JSON | None (никогда не бросает).

    ponytail: llm_completion() делает setdefault() на результате и падает на
    JSON-массивах, поэтому здесь raw + собственный парсинг через _parse_json.
    """
    try:
        return _parse_json(llm_completion_raw(prompt, system_prompt=LLM_SYS,
                                              temperature=0.2, max_tokens=3000))
    except Exception as e:
        log(f"WARN llm: {type(e).__name__}: {e}")
        return None


def merge_labels(rows, data):
    """Кластеры + ответ LLM → [{id,label,category,...}]; мусор → fallback."""
    by_id = {d.get("id"): d for d in data if isinstance(d, dict)} if isinstance(data, list) else {}
    out = []
    for r in rows:
        d = by_id.get(r["id"]) or {}
        label = str(d.get("label") or "").strip()
        category = str(d.get("category") or "").strip()
        if not label:
            label = ", ".join(r["tokens"]).capitalize() or "Нарратив без назви"
        out.append({"id": r["id"], "label": label[:120],
                    "category": category if category in CATEGORIES else "інше",
                    "size": r["size"], "channels": r["channels"],
                    "sample": r["sample_title"]})
    return out


def rank_fakes(fakes, data):
    """Фейки + оценки LLM → сортировка по significance desc (пустые — вниз)."""
    by_i = {d.get("i"): d for d in data if isinstance(d, dict)} if isinstance(data, list) else {}
    out = []
    for n, f in enumerate(fakes):
        d = by_i.get(n) or {}
        try:
            sig = min(10, max(1, int(d.get("significance"))))
        except (TypeError, ValueError):
            sig = 0
        one_line = str(d.get("one_line") or "").strip()
        out.append({"title": f["title"], "url": f["url"], "source": f["source"],
                    "one_line": one_line, "significance": sig})
    return sorted(out, key=lambda f: -f["significance"])


# ------------------------------------------------------------------- main ----
def run():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        clusters = collect_clusters(cur)
        fakes = collect_fakes(cur)
        log(f"24ч: топ-{len(clusters)} кластеров, {len(fakes)} factcheck-итемов")

        labels_data = None
        if clusters:
            payload = [{"id": c["id"], "size": c["size"], "channels": c["channels"],
                        "sample_title": c["sample_title"]} for c in clusters]
            labels_data = ask_llm(
                "Нижче — найбільші кластери синхронних публікацій в україномовному "
                f"телеграмі та фактчеках за останню добу:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
                "Для кожного кластера придумай КОРОТКИЙ опис нарративу українською "
                "(до 8 слів) і визнач категорію — строго одну з: закони, вибори, "
                "мобілізація, інше.\nПоверни СТРОГО JSON-масив: "
                '[{"id":1,"label":"...","category":"інше"}]')
        digest_clusters = merge_labels(clusters, labels_data)

        fakes_data = None
        if fakes:
            titles = [{"i": n, "title": f["title"]} for n, f in enumerate(fakes)]
            fakes_data = ask_llm(
                "Нижче — заголовки перевірок українських фактчекерів (ЦПД, VoxCheck, "
                f"StopFake тощо) за добу — це фейкі, які вони спростували:\n"
                f"{json.dumps(titles, ensure_ascii=False)}\n\n"
                "Для кожного оціни значимість 1–10 (10 — національний масштаб, впливає "
                "на громадську думку чи безпеку; 1 — вузькоспеціальна перевірка) і передай "
                "суть перевірки одним реченням українською.\nПоверни СТРОГО JSON-масив: "
                '[{"i":0,"significance":5,"one_line":"..."}]')
        digest_fakes = rank_fakes(fakes, fakes_data)

        log(f"LLM labels: {'ok' if isinstance(labels_data, list) else 'FALLBACK токены'}, "
            f"fakes: {'ok' if isinstance(fakes_data, list) else 'FALLBACK без оценок'}")

        value = json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "clusters": digest_clusters,
            "fakes": digest_fakes,
        }, ensure_ascii=False)
        cur.execute(
            """INSERT INTO stats_cache (key, value, updated_at)
               VALUES ('info_digest', %s, now() AT TIME ZONE 'utc')
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
            [value])
        conn.commit()
        log(f"DONE: info_digest записан ({len(digest_clusters)} кластеров, "
            f"{len(digest_fakes)} фейків)")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        sys.exit(1)
