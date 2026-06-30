#!/usr/bin/env python3
"""calc_bill_quality.py — Розрахунок Quality, RiskPenalty та Authorship для депутатів.

Оновлює mps:
  - bill_quality_score — weighted AVG((significance + impact) / 2) by sponsor_order
  - avg_bill_significance, avg_bill_impact — unweighted averages (for reference)
  - avg_risk_score — AVG(risk_score) from analyzed bills
  - bills_analyzed_count — count of bills with risk_assessments
  - authorship_ratio — primary_bills / total_bills (order=0 only)

Quality weights by sponsor_order:
  0 → 1.0 (author)
  1 → 0.7 (first co-author)
  2 → 0.5
  ≥3 → 0.3 (formal support)

Deputies with 0 analyzed bills get neutral defaults (50/50).
"""
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def calc_quality_risk_authorship():
    conn = get_db()
    cur = conn.cursor()

    # Step 1: Calculate weighted quality, risk, and authorship per deputy
    cur.execute("""
        WITH dep_analyzed AS (
            SELECT
                bs.rada_uid,
                -- Weighted quality
                SUM(CASE
                    WHEN bs.sponsor_order = 0 THEN (ra.significance + ra.impact) / 2.0 * 1.0
                    WHEN bs.sponsor_order = 1 THEN (ra.significance + ra.impact) / 2.0 * 0.7
                    WHEN bs.sponsor_order = 2 THEN (ra.significance + ra.impact) / 2.0 * 0.5
                    ELSE (ra.significance + ra.impact) / 2.0 * 0.3
                END) / SUM(CASE
                    WHEN bs.sponsor_order = 0 THEN 1.0
                    WHEN bs.sponsor_order = 1 THEN 0.7
                    WHEN bs.sponsor_order = 2 THEN 0.5
                    ELSE 0.3
                END) as weighted_quality,
                -- Unweighted averages (for reference)
                AVG((ra.significance + ra.impact) / 2.0) as avg_quality,
                AVG(ra.significance) as avg_significance,
                AVG(ra.impact) as avg_impact,
                -- Risk
                AVG(ra.risk_score) as avg_risk,
                COUNT(*) as analyzed_count
            FROM bill_sponsors bs
            JOIN risk_assessments ra ON ra.bill_id = bs.bill_id
            GROUP BY bs.rada_uid
        ),
        dep_authorship AS (
            SELECT
                rada_uid,
                COUNT(*) as total_bills,
                COUNT(*) FILTER (WHERE sponsor_order = 0) as primary_bills
            FROM bill_sponsors
            GROUP BY rada_uid
        )
        SELECT
            m.id,
            m.rada_uid,
            COALESCE(da.weighted_quality, 50) as quality,
            COALESCE(da.avg_significance, 0) as avg_sig,
            COALESCE(da.avg_impact, 0) as avg_imp,
            COALESCE(da.avg_risk, 0) as avg_risk,
            COALESCE(da.analyzed_count, 0) as analyzed,
            COALESCE(dau.total_bills, 0) as total_bills,
            COALESCE(dau.primary_bills, 0) as primary_bills,
            CASE WHEN COALESCE(dau.total_bills, 0) > 0
                 THEN dau.primary_bills::float / dau.total_bills
                 ELSE 0 END as authorship_ratio
        FROM mps m
        LEFT JOIN dep_analyzed da ON da.rada_uid = m.rada_uid
        LEFT JOIN dep_authorship dau ON dau.rada_uid = m.rada_uid
        WHERE m.end_date IS NULL OR m.end_date = ''
    """)

    rows = cur.fetchall()
    updated = 0
    for row in rows:
        mp_id, rada_uid, quality, avg_sig, avg_imp, avg_risk, analyzed, total_bills, primary_bills, authorship_ratio = row

        cur.execute("""
            UPDATE mps SET
                bill_quality_score = %s,
                avg_bill_significance = %s,
                avg_bill_impact = %s,
                avg_risk_score = %s,
                bills_analyzed_count = %s,
                authorship_ratio = %s
            WHERE id = %s
        """, (round(quality, 2), round(avg_sig, 2), round(avg_imp, 2),
              round(avg_risk, 2), analyzed, round(authorship_ratio, 4), mp_id))
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    return updated


if __name__ == "__main__":
    print("Розрахунок Quality, Risk та Authorship...")
    count = calc_quality_risk_authorship()
    print(f"Оновлено {count} депутатів")
