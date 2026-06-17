#!/usr/bin/env python3
"""sync_mp_stats.py — Розрахунок статистики депутатів (ПЯ, ПДА, ВКП) в D1."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec, refresh_stats_cache
from src.config import log

# Ваги голосувань для ВКП
VOTE_WEIGHTS = {
    'procedure': 1,   # Процедурні
    'second': 2,      # Друге читання
    'final': 3,       # Фінальне голосування
}

# Дії та їх вага
ACTION_WEIGHTS = {
    'yes': 1,
    'no': 1,
    'abstain': 0.5,
    'not_present': 0,
    'absent': 0,
}

MIN_VOTES = 5  # Мінімум голосувань для розрахунку


def get_vote_weight(title: str) -> int:
    """Визначає вагу голосування за назвою."""
    title_lower = (title or '').lower()
    if any(w in title_lower for w in ['друге читання', 'прийняття', 'останнє', 'в цілому']):
        return VOTE_WEIGHTS['final']
    elif any(w in title_lower for w in ['перше читання', 'за основу']):
        return VOTE_WEIGHTS['second']
    return VOTE_WEIGHTS['procedure']


def calculate_stats():
    """Розраховує статистику для всіх депутатів та оновлює mps."""
    log.info("=== Синхронізація статистики депутатів ===")

    # Отримуємо всіх депутатів
    deputies = d1_query("SELECT id, name FROM mps")
    log.info("Deputies: %d", len(deputies))

    # Отримуємо статистику законопроектів
    bills_data = d1_query("""
        SELECT mp_name, COUNT(*) as total, SUM(CASE WHEN is_law = 1 THEN 1 ELSE 0 END) as laws
        FROM mp_bills
        GROUP BY mp_name
    """)
    mp_bills = {r['mp_name']: (r['total'], r['laws']) for r in bills_data}

    # Розраховуємо статистику для кожного депутата (окремо)
    updated = 0
    for dep in deputies:
        name = dep['name']
        dep_id = dep['id']

        # Отримуємо голоси одного депутата
        votes = d1_query("""
            SELECT v.title, vs.code
            FROM mp_votes mv
            JOIN vote_statuses vs ON mv.status_id = vs.id
            JOIN votes v ON mv.vote_id = v.vote_id
            WHERE mv.mp_name = ?
        """, [name])

        total = len(votes)
        attended = sum(1 for v in votes if v['code'] in ('yes', 'no', 'abstain'))
        voted = sum(1 for v in votes if v['code'] in ('yes', 'no'))

        # ПЯ (Індекс явки) = attended / total
        py = round((attended / total) * 100, 1) if total > 0 else 0

        # ПДА (Діяльне участь) = voted / attended
        pda = round((voted / attended) * 100, 1) if attended > 0 else 0

        # ВКП (Зважений КПД)
        weight_sum = 0
        weight_total = 0
        for v in votes:
            w = get_vote_weight(v['title'])
            weight_total += w
            if v['code'] in ('yes', 'no'):
                weight_sum += w * 1.0
            elif v['code'] == 'abstain':
                weight_sum += w * 0.5

        vkp = round((weight_sum / weight_total) * 100, 1) if weight_total > 0 else 0

        data_sufficient = 1 if total >= MIN_VOTES else 0

        total_bills, total_laws = mp_bills.get(name, (0, 0))

        # Оновлюємо mps
        d1_exec("raw_sql", {
            "sql": """UPDATE mps SET
                py = ?, pda = ?, vkp = ?, data_sufficient = ?,
                total_votes = ?, attended_votes = ?, voted_votes = ?,
                total_bills = ?, total_laws = ?,
                stats_updated_at = datetime('now')
            WHERE id = ?""",
            "params": [py, pda, vkp, data_sufficient,
                       total, attended, voted,
                       total_bills, total_laws,
                       dep_id]
        })
        updated += 1

        if updated % 50 == 0:
            log.info("  Progress: %d/%d deputies", updated, len(deputies))

    log.info("=== Updated %d deputies ===", updated)
    refresh_stats_cache()


if __name__ == "__main__":
    calculate_stats()
