#!/usr/bin/env python3
"""sync_mp_stats.py — Розрахунок статистики депутатів (ПЯ, ПДА, ВКП).

Використовує rada_uid для об'єднання даних при зміні фамілії.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec, d1_exec_sql, refresh_stats_cache
from src.config import log

MIN_VOTES = 5


def _sql_str(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def calculate_stats():
    """Розраховує статистику для всіх депутатів з урахуванням зміни фамілій."""
    log.info("=== Синхронізація статистики депутатів ===")

    # Голосування: групуємо по rada_uid (об'єднуємо стару/нову фамілію)
    vote_sql = """
        SELECT
            COALESCE(
                (SELECT m.name FROM mps m WHERE m.rada_uid = mv.rada_uid LIMIT 1),
                mv.mp_name
            ) as resolved_name,
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
        GROUP BY resolved_name
    """

    vote_agg = d1_query(vote_sql)
    log.info("Vote aggregates: %d deputies", len(vote_agg))

    # Законопроекти: групуємо по rada_uid
    bills_sql = """
        SELECT
            COALESCE(
                (SELECT m.name FROM mps m WHERE m.rada_uid = mb.rada_uid LIMIT 1),
                mb.mp_name
            ) as resolved_name,
            COUNT(*) as total_bills,
            SUM(CASE WHEN mb.is_law = 1 THEN 1 ELSE 0 END) as total_laws
        FROM mp_bills mb
        GROUP BY resolved_name
    """
    bills_data = d1_query(bills_sql)
    mp_bills = {r['resolved_name']: (r['total_bills'], r['total_laws']) for r in bills_data}

    # Рахуємо метрики
    stats_rows = []
    for r in vote_agg:
        name = r['resolved_name']
        total = r['total_votes'] or 0
        attended = r['attended_votes'] or 0
        voted = r['voted_votes'] or 0
        w_sum = float(r['vkp_weighted_sum'] or 0)
        w_total = float(r['vkp_weight_total'] or 0)

        py = round((attended / total) * 100, 1) if total > 0 else 0
        pda = round((voted / attended) * 100, 1) if attended > 0 else 0
        vkp = round((w_sum / w_total) * 100, 1) if w_total > 0 else 0
        data_sufficient = 1 if total >= MIN_VOTES else 0
        total_bills, total_laws = mp_bills.get(name, (0, 0))

        stats_rows.append((name, py, pda, vkp, data_sufficient,
                           total, attended, voted,
                           total_bills, total_laws))

    if not stats_rows:
        log.warning("No vote data to update")
        return 0

    # Масовий UPDATE через CTE VALUES
    values_clauses = []
    for row in stats_rows:
        nm = _sql_str(row[0]) + "::text"
        values_clauses.append(
            f"({nm}, {row[1]}::numeric, {row[2]}::numeric, "
            f"{row[3]}::numeric, {row[4]}::int, "
            f"{row[5]}::int, {row[6]}::int, {row[7]}::int, "
            f"{row[8]}::int, {row[9]}::int)"
        )
    values_sql = ",\n".join(values_clauses)

    update_sql = f"""
        WITH new_stats(name, py, pda, vkp, data_sufficient,
                       total_votes, attended_votes, voted_votes,
                       total_bills, total_laws) AS (
            VALUES {values_sql}
        )
        UPDATE mps m
        SET
            py = ns.py,
            pda = ns.pda,
            vkp = ns.vkp,
            data_sufficient = ns.data_sufficient,
            total_votes = ns.total_votes,
            attended_votes = ns.attended_votes,
            voted_votes = ns.voted_votes,
            total_bills = ns.total_bills,
            total_laws = ns.total_laws,
            stats_updated_at = (now() AT TIME ZONE 'utc')
        FROM new_stats ns
        WHERE m.name = ns.name
    """

    ok = d1_exec_sql(update_sql)
    if ok:
        log.info("=== Updated %d deputies (batch CTE) ===", len(stats_rows))
    else:
        log.warning("Batch CTE failed, falling back to per-deputy...")
        for row in stats_rows:
            d1_exec("raw_sql", {
                "sql": """UPDATE mps SET
                    py = ?, pda = ?, vkp = ?, data_sufficient = ?,
                    total_votes = ?, attended_votes = ?, voted_votes = ?,
                    total_bills = ?, total_laws = ?,
                    stats_updated_at = now() AT TIME ZONE 'utc'
                WHERE name = ?""",
                "params": [row[1], row[2], row[3], row[4],
                           row[5], row[6], row[7],
                           row[8], row[9], row[0]]
            })
        log.info("=== Updated %d deputies (fallback) ===", len(stats_rows))

    refresh_stats_cache()
    return len(stats_rows)


if __name__ == "__main__":
    calculate_stats()
