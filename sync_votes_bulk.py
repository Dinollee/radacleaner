#!/usr/bin/env python3
"""sync_votes_bulk.py — Масова синхронізація голосувань для всіх законів stage >= 2.

Usage:
    python sync_votes_bulk.py           — синхронізувати всі
    python sync_votes_bulk.py --limit 100 — максимум законів для перевірки
    python sync_votes_bulk.py --resume  — продовжити звідки зупинились
"""
import re
import sys
import time
import urllib.request

from src.config import log
from src.d1_client import d1_query
from sync_votes import parse_vote_page, save_vote, find_g_ids

PROGRESS_FILE = "/home/radamon/.vote_sync_progress.txt"


def get_bills_to_check(limit=None):
    """Отримує закони stage >= 2, які ще не мають голосувань."""
    sql = """
        SELECT b.bill_number
        FROM bills b
        WHERE b.stage >= 2
          AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.bill_id = b.id)
        ORDER BY b.registration_date DESC
    """
    if limit:
        sql += f" LIMIT {limit}"
    return d1_query(sql)


def get_resumed_bills():
    """Читає progress file для resume."""
    try:
        with open(PROGRESS_FILE, "r") as f:
            done = set(line.strip() for line in f if line.strip())
        return done
    except FileNotFoundError:
        return set()


def mark_done(bill_number):
    """Позначає закон як оброблений."""
    with open(PROGRESS_FILE, "a") as f:
        f.write(bill_number + "\n")


def main():
    limit = None
    resume = "--resume" in sys.argv
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    bills = get_bills_to_check(limit)
    log.info("Bills to check: %d", len(bills))

    if resume:
        done = get_resumed_bills()
        bills = [b for b in bills if b["bill_number"] not in done]
        log.info("After resume skip: %d remaining", len(bills))

    found = 0
    total_votes = 0

    for i, b in enumerate(bills):
        bn = b["bill_number"]
        try:
            g_ids = find_g_ids(bn)
            if g_ids:
                found += 1
                log.info("[%d/%d] #%s: %d votes", i + 1, len(bills), bn, len(g_ids))
                for g_id in g_ids:
                    try:
                        data = parse_vote_page(g_id)
                        if data and data["mps"]:
                            save_vote(data, bn)
                            total_votes += 1
                        time.sleep(0.5)
                    except Exception as e:
                        log.error("  Vote %d failed: %s", g_id, str(e)[:100])
            mark_done(bn)
        except Exception as e:
            log.error("[%d/%d] #%s: %s", i + 1, len(bills), bn, str(e)[:100])
            mark_done(bn)

        if (i + 1) % 50 == 0:
            log.info("Progress: %d/%d checked, %d bills with votes, %d total votes",
                     i + 1, len(bills), found, total_votes)

        time.sleep(0.2)

    log.info("=== Done: %d/%d bills have votes, %d total vote records ===",
             found, len(bills), total_votes)


if __name__ == "__main__":
    main()
