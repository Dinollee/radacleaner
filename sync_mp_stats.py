#!/usr/bin/env python3
"""sync_mp_stats.py — Розрахунок статистики депутатів (ПЯ, ПДА, ВКП).

Єдина точка правди — mps.id. Всі зв'язки через mp_id FK.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql, refresh_stats_cache
from src.config import log

MIN_VOTES = 5


def calculate_stats():
    log.info("=== Синхронізація статистики депутатів ===")

    vote_sql = """
        SELECT
            mv.mp_id,
            COUNT(*) AS total_votes,
            SUM(CASE WHEN vs.code IN ('yes','no','abstain') THEN 1 ELSE 0 END)
                AS attended_votes,
            SUM(CASE WHEN vs.code IN ('yes','no') THEN 1 ELSE 0 END)
                AS voted_votes,
            SUM(CASE
                WHEN vs.code IN ('yes','no') THEN v.weight
                WHEN vs.code = 'abstain' THEN v.weight * 0.5
                ELSE 0
            END) AS vkp_weighted_sum,
            SUM(CASE
                WHEN vs.code IN ('yes','no','abstain') THEN v.weight
                ELSE 0
            END) AS vkp_weight_total
        FROM mp_votes mv
        JOIN vote_statuses vs ON mv.status_id = vs.id
        JOIN votes v ON mv.vote_id = v.vote_id
        WHERE mv.mp_id IS NOT NULL
        GROUP BY mv.mp_id
    """

    vote_agg = d1_query(vote_sql)
    log.info("Vote aggregates: %d deputies", len(vote_agg))

    bills_sql = """
        SELECT
            mb.mp_id,
            COUNT(*) as total_bills,
            SUM(CASE WHEN mb.is_law = 1 THEN 1 ELSE 0 END) as total_laws
        FROM mp_bills mb
        WHERE mb.mp_id IS NOT NULL
        GROUP BY mb.mp_id
    """
    bills_data = d1_query(bills_sql)
    mp_bills = {r['mp_id']: (r['total_bills'], r['total_laws']) for r in bills_data}

    for r in vote_agg:
        mp_id = r['mp_id']
        total = r['total_votes'] or 0
        attended = r['attended_votes'] or 0
        voted = r['voted_votes'] or 0
        w_sum = float(r['vkp_weighted_sum'] or 0)
        w_total = float(r['vkp_weight_total'] or 0)

        py = round((attended / total) * 100, 1) if total > 0 else 0
        pda = round((voted / attended) * 100, 1) if attended > 0 else 0
        vkp = round((w_sum / w_total) * 100, 1) if w_total > 0 else 0
        data_sufficient = 1 if total >= MIN_VOTES else 0
        total_bills, total_laws = mp_bills.get(mp_id, (0, 0))

        lei = total_laws * (total_laws / total_bills) if total_bills > 0 else 0

        d1_exec_sql("""
            UPDATE mps SET
                py = %s, pda = %s, vkp = %s, data_sufficient = %s,
                total_votes = %s, attended_votes = %s, voted_votes = %s,
                total_bills = %s, total_laws = %s, lei = %s,
                stats_updated_at = (now() AT TIME ZONE 'utc')
            WHERE id = %s
        """, [py, pda, vkp, data_sufficient,
              total, attended, voted,
              total_bills, total_laws, lei, mp_id])

    log.info("=== Updated %d deputies ===", len(vote_agg))

    refresh_stats_cache()
    return len(vote_agg)


if __name__ == "__main__":
    calculate_stats()
