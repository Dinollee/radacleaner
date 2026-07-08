#!/usr/bin/env python3
"""calc_eu_llm.py — EU Score на основі LLM аналізу (raw_analysis).

Ваги за стадіями:
  - Stage 4 (прийнято): +1.0 (позитивний EU-сигнал)
  - Stage 5 (відхилено): -1.0 (негативний — хороший EU-закон відхилили)
  - Stage 1-3 (в процесі): 0.5 (очікуємо)

Запуск: після night_batch (раз на день).
"""
import re
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

PRO_PATTERNS = [r'гармонізація', r'acquis', r'імплементація', r'євроінтеграці', r'відповідність.*єс']
ANTI_PATTERNS = [r'обмеження.*єс', r'суперечність', r'невідповідність']

# Stage weights for EU Score
STAGE_WEIGHTS = {1: 0.5, 2: 0.5, 3: 0.7, 4: 1.0, 5: -1.0}


def classify(text):
    t = text.lower()
    pro = sum(1 for p in PRO_PATTERNS if re.search(p, t))
    anti = sum(1 for p in ANTI_PATTERNS if re.search(p, t))
    return 'pro' if pro > anti else ('anti' if anti > pro else 'neutral')


def main():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # Get EU bills with stage info
    cur.execute("""
        SELECT bs.mp_id, bs.bill_id, ra.raw_analysis, b.stage
        FROM bill_sponsors bs
        JOIN risk_assessments ra ON ra.bill_id = bs.bill_id
        JOIN bills b ON b.id = bs.bill_id
        WHERE bs.mp_id IS NOT NULL AND ra.raw_analysis ILIKE '%ЄС%'
    """)
    rows = cur.fetchall()
    print(f"EU bill-deputy links: {len(rows)}")

    # Aggregate per deputy with stage weights
    dep_data = {}
    for mp_id, bill_id, analysis, stage in rows:
        if mp_id not in dep_data:
            dep_data[mp_id] = {'weighted_score': 0, 'eu_bills': 0, 'pro': 0, 'anti': 0, 'rejected': 0}

        cls = classify(analysis or '')
        weight = STAGE_WEIGHTS.get(stage, 0.5)

        dep_data[mp_id]['eu_bills'] += 1

        if cls == 'pro':
            dep_data[mp_id]['weighted_score'] += weight
            dep_data[mp_id]['pro'] += 1
        elif cls == 'anti':
            dep_data[mp_id]['weighted_score'] -= weight
            dep_data[mp_id]['anti'] += 1
        else:
            dep_data[mp_id]['weighted_score'] += weight * 0.3  # neutral = small positive

        if stage == 5:
            dep_data[mp_id]['rejected'] += 1
            dep_data[mp_id]['weighted_score'] -= 0.5  # rejected EU bill = negative

    print(f"Deputies with EU data: {len(dep_data)}")

    # Update mps
    updated = 0
    for mp_id, data in dep_data.items():
        cur.execute("SELECT total_bills FROM mps WHERE id = %s", (mp_id,))
        row = cur.fetchone()
        total_bills = row[0] if row else 0

        if total_bills > 0:
            # EU Score = weighted_score / total_bills × 100, capped at 0-100
            eu_score = max(0, min(100, round((data['weighted_score'] / total_bills) * 100, 1)))
        else:
            eu_score = 0

        cur.execute("""
            UPDATE mps SET eu_integration_score = %s, eu_euro_bills = %s, eu_risk_bills = %s
            WHERE id = %s
        """, (eu_score, data['eu_bills'], data['pro'] - data['anti'], mp_id))
        updated += 1

    conn.commit()
    print(f"Updated {updated} deputies")

    # Stats
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE eu_integration_score > 0),
               ROUND(AVG(eu_integration_score) FILTER (WHERE eu_integration_score > 0)::numeric, 1),
               ROUND(MAX(eu_integration_score)::numeric, 1)
        FROM mps WHERE end_date IS NULL OR end_date = ''
    """)
    s = cur.fetchone()
    print(f"EU Stats: {s[0]} with EU score, avg: {s[1]}%, max: {s[2]}%")

    # Top 10
    cur.execute("""
        SELECT name, faction, eu_integration_score, eu_euro_bills, eu_risk_bills
        FROM mps WHERE (end_date IS NULL OR end_date = '') AND eu_integration_score > 0
        ORDER BY eu_integration_score DESC LIMIT 10
    """)
    print("\nTop 10 EU deputies:")
    for i, (name, faction, score, euro, ratio) in enumerate(cur.fetchall(), 1):
        print(f"  {i}. {name} ({faction}) — {score}% (EU: {euro}, net: {ratio})")

    # Bottom 5 (anti-EU)
    cur.execute("""
        SELECT name, faction, eu_integration_score, eu_euro_bills, eu_risk_bills
        FROM mps WHERE (end_date IS NULL OR end_date = '') AND eu_risk_bills < 0
        ORDER BY eu_risk_bills ASC LIMIT 5
    """)
    bottom = cur.fetchall()
    if bottom:
        print("\nMost anti-EU deputies:")
        for name, faction, score, euro, ratio in bottom:
            print(f"  {name} ({faction}) — {score}% (net: {ratio})")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
