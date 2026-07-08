#!/usr/bin/env python3
"""calc_eu_llm.py — EU Score на основі LLM аналізу (raw_analysis).

Запуск: після night_batch (раз на день) або вручну.
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


def classify(text):
    t = text.lower()
    pro = sum(1 for p in PRO_PATTERNS if re.search(p, t))
    anti = sum(1 for p in ANTI_PATTERNS if re.search(p, t))
    return 'pro' if pro > anti else ('anti' if anti > pro else 'neutral')


def main():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # Get EU bills with raw_analysis
    cur.execute("""
        SELECT bs.mp_id, bs.bill_id, ra.raw_analysis
        FROM bill_sponsors bs
        JOIN risk_assessments ra ON ra.bill_id = bs.bill_id
        WHERE bs.mp_id IS NOT NULL AND ra.raw_analysis ILIKE '%ЄС%'
    """)
    rows = cur.fetchall()
    print(f"EU bill-deputy links: {len(rows)}")

    # Aggregate per deputy
    dep_data = {}
    for mp_id, bill_id, analysis in rows:
        if mp_id not in dep_data:
            dep_data[mp_id] = {'eu': set(), 'pro': set(), 'anti': set()}
        dep_data[mp_id]['eu'].add(bill_id)
        cls = classify(analysis or '')
        if cls == 'pro':
            dep_data[mp_id]['pro'].add(bill_id)
        elif cls == 'anti':
            dep_data[mp_id]['anti'].add(bill_id)

    print(f"Deputies with EU data: {len(dep_data)}")

    # Update mps
    updated = 0
    for mp_id, data in dep_data.items():
        eu_count = len(data['eu'])
        pro_count = len(data['pro'])
        anti_count = len(data['anti'])

        cur.execute("SELECT total_bills FROM mps WHERE id = %s", (mp_id,))
        row = cur.fetchone()
        total_bills = row[0] if row else 0

        eu_score = round((eu_count / total_bills) * 100, 1) if total_bills > 0 else 0
        eu_ratio = pro_count - anti_count

        cur.execute("""
            UPDATE mps SET eu_integration_score = %s, eu_euro_bills = %s, eu_risk_bills = %s
            WHERE id = %s
        """, (eu_score, eu_count, eu_ratio, mp_id))
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

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
