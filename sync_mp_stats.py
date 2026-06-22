#!/usr/bin/env python3
"""sync_mp_stats.py — Розрахунок статистики депутатів (ПЯ, ПДА, ВКП).

Оновлено: N+1 усунуто. Замість 465×2=930 запитів тепер 3:
  1. Агрегатний SELECT голосувань (JOIN votes.weight + vote_statuses)
  2. Агрегатний SELECT законопроектів
  3. Масовий UPDATE через CTE VALUES (один запит)

ВКП вага береться з votes.weight (встановлюється окремим скриптом).
При додаванні нових голосувань sync.py повинен оновлювати weight.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec, d1_exec_sql, refresh_stats_cache
from src.config import log

MIN_VOTES = 5  # Мінімум голосувань для розрахунку


def _sql_str(s) -> str:
    """Екранування для SQL-літералу text."""
    return "'" + str(s).replace("'", "''") + "'"


def calculate_stats():
    """Розраховує статистику для всіх депутатів та оновлює mps."""
    log.info("=== Синхронізація статистики депутатів ===")

    # ── 1. Агрегат голосувань по всіх депутатах ──────────────────────
    # Вага береється з votes.weight (не ILIKE на льоту!)
    vote_sql = """
        SELECT
            mv.mp_name,
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
        GROUP BY mv.mp_name
    """

    vote_agg = d1_query(vote_sql)
    log.info("Vote aggregates: %d deputies", len(vote_agg))

    # ── 2. Агрегат законопроектів ─────────────────────────────────────
    bills_data = d1_query("""
        SELECT mp_name, COUNT(*) as total_bills,
               SUM(CASE WHEN is_law = 1 THEN 1 ELSE 0 END) as total_laws
        FROM mp_bills
        GROUP BY mp_name
    """)
    mp_bills = {r['mp_name']: (r['total_bills'], r['total_laws']) for r in bills_data}

    # ── 3. Рахуємо метрики на Python (з агрегованих даних) ─────────────
    stats_rows = []
    for r in vote_agg:
        name = r['mp_name']
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

    # ── 4. Масовий UPDATE через CTE VALUES (один запит) ────────────────
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
        # Fallback: по одному (як масовий не пройшов)
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

    # Оновлюємо кеш статистики дашборду
    refresh_stats_cache()
    return len(stats_rows)


if __name__ == "__main__":
    calculate_stats()
