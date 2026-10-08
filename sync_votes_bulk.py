#!/usr/bin/env python3
"""sync_votes_bulk.py — Масова синхронізація голосувань з RADA.

Перевіряє дві групи законів stage >= 2 (без progress-файлу: раніше --resume
у щохвилинному таймері перетворював його на перманентний чорний список —
закони, перевірені без голосів, більше ніколи не перечитувалися):
  (1) свіжі updated_at (4 дні) — нові голосування з'являються разом зі
      зміною статусу, яку bill_sync підхоплює щогодини;
  (2) voteless зі статусом, що передбачає пленарне голосування —
      дозавантаження до першого успіху.

Стеля (ponytail): голосування без зміни статусу понад 4 дні та статуси
поза списком гілки (2) не потраплять у прохід; «Опрацьовується в
комітеті»/«Очікує розгляду»/зняті не перевіряються — спот-перевірка
0 з N мала g_ids на RADA.

Usage:
    python sync_votes_bulk.py           — повний прохід
    python sync_votes_bulk.py --limit 100 — максимум законів для перевірки
    python sync_votes_bulk.py --since 2026-09-01 — catch-up: розширити
        вікно (1) до заданої дати (ретельне наздогання після простою)
"""
import sys
import time
from datetime import datetime

from src.config import log
from src.d1_client import d1_query
from sync_votes import parse_vote_page, save_vote, find_g_ids, resolve_bill_id

# Статуси, що гарантовано мають пленарне голосування на RADA.
# ponytail: «Постанову підписано» НАВМИСНО немає — catch-up 2026-10-08 знайшов
# лише 16 з 2121 (решта 2105 не має g_ids взагалі, вартість повтору ~19хв/6h);
# нові постанови все одно ловляться вікном (1) через updated_at при підписанні.
VOTE_IMPLYING_STATUSES = (
    "Закон підписано",
    "Акт підписано",
    "Передано на підпис Президенту",
    "Готується на підпис",
    "Готується на підпис (після вето)",
    "Готується на друге читання",
    "Очікує на друге читання",
    "Готується на повторне перше читання",
    "Очікує на повторне друге читання",
    "Готується на розгляд з вето Президента",
    "Очікує на розгляд з вето Президента",
    "Пропозиції враховано",
)


def get_bills_to_check(limit=None, since=None):
    """Закони stage >= 2, для яких варто (пер)перевірити голосування."""
    if since:
        window = f"'{datetime.strptime(since, '%Y-%m-%d').strftime('%Y-%m-%d')}'"
    else:
        window = "(now() AT TIME ZONE 'utc') - interval '4 days'"
    statuses = ",".join(f"'{s}'" for s in VOTE_IMPLYING_STATUSES)
    sql = f"""
        SELECT b.bill_number
        FROM bills b
        WHERE b.stage >= 2
          AND (
            b.updated_at::timestamp >= {window}
            OR (
              NOT EXISTS (SELECT 1 FROM votes v WHERE v.bill_id = b.id)
              AND b.current_status IN ({statuses})
            )
          )
        ORDER BY b.updated_at DESC
    """
    if limit:
        sql += f" LIMIT {limit}"
    return d1_query(sql)


def main():
    limit = None
    since = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]

    bills = get_bills_to_check(limit, since)
    log.info("Bills to check: %d (since=%s)", len(bills), since or "4d window")

    found = 0
    total_votes = 0

    for i, b in enumerate(bills):
        bn = b["bill_number"]
        try:
            g_ids = find_g_ids(bn)
            if g_ids:
                bill_id = resolve_bill_id(bn)
                existing = set()
                if bill_id:
                    existing = {r["vote_id"] for r in d1_query(
                        "SELECT vote_id FROM votes WHERE bill_id = ?", [bill_id])}
                new_g_ids = [g for g in g_ids if g not in existing]
                if new_g_ids:
                    found += 1
                    log.info("[%d/%d] #%s: %d new of %d votes",
                             i + 1, len(bills), bn, len(new_g_ids), len(g_ids))
                    for g_id in new_g_ids:
                        try:
                            data = parse_vote_page(g_id)
                            if data and data["mps"]:
                                save_vote(data, bn)
                                total_votes += 1
                            time.sleep(0.5)
                        except Exception as e:
                            log.error("  Vote %d failed: %s", g_id, str(e)[:100])
        except Exception as e:
            log.error("[%d/%d] #%s: %s", i + 1, len(bills), bn, str(e)[:100])

        if (i + 1) % 50 == 0:
            log.info("Progress: %d/%d checked, %d bills with new votes, %d total votes",
                     i + 1, len(bills), found, total_votes)

        time.sleep(0.2)

    log.info("=== Done: %d/%d bills got new votes, %d total vote records ===",
             found, len(bills), total_votes)


if __name__ == "__main__":
    main()