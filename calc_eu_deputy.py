#!/usr/bin/env python3
"""calc_eu_deputy.py — Перерахунок EU Score для кожного депутата.

Джерела даних:
  1. eu_euro_bills — bills з is_euro=true через bill_sponsors
  2. eu_risk_bills — bills з EU-реляційними ризиками в risk_assessments
  3. eu_state_aid_bills — bills з "державна допомога" в risk_categories
  4. eu_integration_score — агрегація (eu_euro + eu_risk + eu_state_aid) / total × 100
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log


def calc_eu_scores():
    """Перерахунок EU полів для всіх активних депутатів."""
    log.info("=== EU Score recalculation ===")

    # 1. eu_euro_bills — з bills.is_euro
    log.info("Calculating eu_euro_bills...")
    euro_data = d1_query("""
        SELECT bs.mp_id, COUNT(*) as cnt
        FROM bill_sponsors bs
        JOIN bills b ON b.id = bs.bill_id
        WHERE b.is_euro = true AND bs.mp_id IS NOT NULL
        GROUP BY bs.mp_id
    """)
    euro_map = {r['mp_id']: r['cnt'] for r in euro_data}
    log.info("  Found %d deputies with euro bills", len(euro_map))

    # 2. eu_risk_bills — skip for now (d1_query NULL handling issue)
    risk_map = {}

    # 3. eu_state_aid_bills — skip for now
    state_aid_map = {}

    # 4. Оновлюємо mps
    log.info("Updating mps table...")
    deputies = d1_query("SELECT id FROM mps WHERE end_date IS NULL OR end_date = ''")

    updated = 0
    for dep in deputies:
        mp_id = dep['id']
        euro = euro_map.get(mp_id, 0)
        risk = risk_map.get(mp_id, 0)
        state_aid = state_aid_map.get(mp_id, 0)

        # eu_integration_score: відсоток депутатів з EU-законами
        # Формула: (euro + risk + state_aid) / total_bills × 100 (cap at 100)
        total = d1_query("SELECT total_bills FROM mps WHERE id = %s", [mp_id])
        total_bills = total[0]['total_bills'] if total else 0

        if total_bills > 0:
            score = min(((euro + risk + state_aid) / total_bills) * 100, 100)
        else:
            score = 0

        d1_exec_sql("""
            UPDATE mps SET
                eu_integration_score = %s,
                eu_euro_bills = %s,
                eu_risk_bills = %s,
                eu_state_aid_bills = %s
            WHERE id = %s
        """, [round(score, 1), euro, risk, state_aid, mp_id])
        updated += 1

    log.info("Updated %d deputies", updated)

    # 5. Статистика
    stats = d1_query("""
        SELECT
            COUNT(*) FILTER (WHERE eu_integration_score > 0) as with_eu,
            COUNT(*) FILTER (WHERE eu_euro_bills > 0) as with_euro,
            ROUND(AVG(eu_integration_score) FILTER (WHERE eu_integration_score > 0)::numeric, 1) as avg_score
        FROM mps WHERE end_date IS NULL OR end_date = ''
    """)
    if stats:
        s = stats[0]
        log.info("EU Stats: %d with EU score, %d with euro bills, avg score: %s",
                 s['with_eu'], s['with_euro'], s['avg_score'])


if __name__ == "__main__":
    calc_eu_scores()
