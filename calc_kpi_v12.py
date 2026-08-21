#!/usr/bin/env python3
"""KPI v12 — 6 equal-weight categories, no arbitrary weights.

Categories:
  C1: Discipline      — py, pda, vkp
  C2: Legislation     — quality, risk, docs, authorship
  C3: Efficiency      — adoption_rate × volume_factor
  C4: Committee       — committee_score
  C5: Requests        — requests_with_response × response_rate
  C6: Impact          — risk + eu_integration

KPI_v12 = (C1 + C2 + C3 + C4 + C5 + C6) / 6
"""
import json
import math
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"


def get_conn():
    return psycopg2.connect(DB_DSN)


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def calc_c1(py, pda, vkp):
    """Discipline: attendance + voting participation + faction loyalty."""
    if py < 10:
        return 0.0
    return py / 100 * 0.5 + pda / 100 * 0.3 + vkp / 100 * 0.2


def calc_c2(quality, risk, docs, authorship, has_data):
    """Legislation: bill quality + low risk + document richness + authorship."""
    if not has_data:
        return 0.5
    q = quality / 5 if quality else 0.5
    r = 1 - (risk / 5) if risk is not None else 0.5
    d = clamp(docs / 2000) if docs else 0.5
    a = clamp(authorship / 0.5) if authorship else 0.5
    return q * 0.3 + r * 0.3 + d * 0.2 + a * 0.2


def calc_c3(adoption, total_primary):
    """Efficiency: adoption rate (main driver) + volume bonus."""
    if total_primary < 3:
        return 0.5
    adoption_norm = adoption / 100
    volume_norm = clamp(total_primary / 10)
    return adoption_norm * 0.7 + volume_norm * 0.3


# Публічна шкала C4 «Комітет» (див. розділ «Методологія» на дашборді).
# Строго монотонна: будь-яка роль >= відсутність ролі. Крок 15 — м'який.
C4_LADDER = {
    0: 0.40,   # немає ролі в комітеті
    3: 0.55,   # член комітету
    5: 0.70,   # секретар / голова підкомітету
    7: 0.85,   # заступник голови
    10: 1.00,  # голова комітету / спікер
}


def calc_c4(committee_score):
    """Committee: published monotonic ladder (see C4_LADDER)."""
    return C4_LADDER.get(committee_score, C4_LADDER[0])


def calc_c5(requests_with_response, request_count):
    """Requests: how many citizen requests received responses."""
    if requests_with_response == 0:
        return 0.0
    base = clamp(requests_with_response / 20)
    rate = requests_with_response / request_count if request_count > 0 else 0
    return base * (0.7 + 0.3 * rate)


def calc_c6(risk, eu_score):
    """Impact: low-risk bills + EU alignment."""
    if risk is None and eu_score is None:
        return 0.5
    r = 1 - (risk / 5) if risk is not None else 0.5
    e = clamp(eu_score / 35) if eu_score else 0.5
    return r * 0.6 + e * 0.4


def main():
    conn = get_conn()
    cur = conn.cursor()

    # Get primary bills count per deputy
    cur.execute("""
        SELECT mp_id, COUNT(*) as primary_count
        FROM bill_sponsors
        WHERE sponsor_order = 0
        GROUP BY mp_id
    """)
    primary_map = {row[0]: row[1] for row in cur.fetchall()}

    # Get adopted primary bills count per deputy
    cur.execute("""
        SELECT bs.mp_id, COUNT(*) as adopted_primary
        FROM bill_sponsors bs
        JOIN bills b ON b.id = bs.bill_id
        WHERE bs.sponsor_order = 0 AND b.stage = 4
        GROUP BY bs.mp_id
    """)
    adopted_primary_map = {row[0]: row[1] for row in cur.fetchall()}

    # Main query
    cur.execute("""
        SELECT
            m.id, m.name, m.faction,
            COALESCE(m.py, 0) as py,
            COALESCE(m.pda, 0) as pda,
            COALESCE(m.vkp, 0) as vkp,
            COALESCE(m.bill_quality_score, 0) as quality,
            m.avg_risk_score as risk,
            COALESCE(m.documents_count, 0) as docs,
            COALESCE(m.authorship_ratio, 0) as authorship,
            COALESCE(m.bills_analyzed_count, 0) as analyzed,
            COALESCE(m.adoption_rate, 0) as adoption,
            COALESCE(m.total_bills, 0) as total_bills,
            COALESCE(m.total_laws, 0) as total_laws,
            COALESCE(m.committee_score, 0) as committee,
            COALESCE(m.requests_with_response, 0) as req_resp,
            COALESCE(m.request_count, 0) as req_count,
            m.eu_integration_score as eu_score,
            COALESCE(m.kpi_v11_score, 0) as kpi_v11
        FROM mps m
        WHERE (m.end_date IS NULL OR m.end_date = '')
    """)
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]

    results = []
    for row in rows:
        d = dict(zip(cols, row))

        total_primary = primary_map.get(d['id'], 0)
        adopted_primary = adopted_primary_map.get(d['id'], 0)

        # Recalculate adoption_rate from primary bills
        if total_primary > 0:
            adoption_primary = adopted_primary / total_primary * 100
        else:
            adoption_primary = d['adoption']

        has_legislation_data = d['analyzed'] > 0

        c1 = calc_c1(d['py'], d['pda'], d['vkp'])
        c2 = calc_c2(d['quality'], d['risk'], d['docs'], d['authorship'], has_legislation_data)
        c3 = calc_c3(adoption_primary, total_primary)
        c4 = calc_c4(d['committee'])
        c5 = calc_c5(d['req_resp'], d['req_count'])
        c6 = calc_c6(d['risk'], d['eu_score'])

        kpi_v12 = (c1 + c2 + c3 + c4 + c5 + c6) / 6

        results.append({
            'id': d['id'], 'name': d['name'], 'faction': d['faction'],
            'c1': c1, 'c2': c2, 'c3': c3, 'c4': c4, 'c5': c5, 'c6': c6,
            'kpi_v12': kpi_v12, 'kpi_v11': d['kpi_v11'],
            'total_primary': total_primary, 'adopted_primary': adopted_primary,
        })

    # Sort by KPI v12
    results.sort(key=lambda r: r['kpi_v12'], reverse=True)
    for i, r in enumerate(results):
        r['rank'] = i + 1

    # Update DB
    for r in results:
        cur.execute("""
            UPDATE mps SET
                kpi_v12_score = %s,
                kpi_v12_rank = %s,
                kpi_v12_discipline = %s,
                kpi_v12_legislation = %s,
                kpi_v12_efficiency = %s,
                kpi_v12_committee = %s,
                kpi_v12_requests = %s,
                kpi_v12_impact = %s
            WHERE id = %s
        """, (
            round(r['kpi_v12'] * 100, 1),
            r['rank'],
            round(r['c1'] * 100, 1),
            round(r['c2'] * 100, 1),
            round(r['c3'] * 100, 1),
            round(r['c4'] * 100, 1),
            round(r['c5'] * 100, 1),
            round(r['c6'] * 100, 1),
            r['id'],
        ))

    conn.commit()
    cur.close()
    conn.close()

    # Print results
    print(f"\nKPI v12 — {len(results)} deputies calculated")
    print(f"\n{'#':<3} {'Name':<25} {'v12':>5} {'v11':>5} {'Δ':>5} | {'C1':>4} {'C2':>4} {'C3':>4} {'C4':>4} {'C5':>4} {'C6':>4}")
    print("-" * 85)
    for r in results[:20]:
        delta = r['kpi_v12'] * 100 - r['kpi_v11']
        print(f"{r['rank']:<3} {r['name']:<25} {r['kpi_v12']*100:>5.1f} {r['kpi_v11']:>5.1f} {delta:>+5.1f} | "
              f"{r['c1']*100:>4.0f} {r['c2']*100:>4.0f} {r['c3']*100:>4.0f} {r['c4']*100:>4.0f} {r['c5']*100:>4.0f} {r['c6']*100:>4.0f}")

    print(f"\n--- Bottom 10 ---")
    for r in results[-10:]:
        delta = r['kpi_v12'] * 100 - r['kpi_v11']
        print(f"{r['rank']:<3} {r['name']:<25} {r['kpi_v12']*100:>5.1f} {r['kpi_v11']:>5.1f} {delta:>+5.1f} | "
              f"{r['c1']*100:>4.0f} {r['c2']*100:>4.0f} {r['c3']*100:>4.0f} {r['c4']*100:>4.0f} {r['c5']*100:>4.0f} {r['c6']*100:>4.0f}")

    # Correlation
    n = len(results)
    vals_v12 = [r['kpi_v12'] * 100 for r in results]
    vals_v11 = [r['kpi_v11'] for r in results]
    mean_x = sum(vals_v12) / n
    mean_y = sum(vals_v11) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(vals_v12, vals_v11)) / n
    std_x = math.sqrt(sum((x - mean_x)**2 for x in vals_v12) / n)
    std_y = math.sqrt(sum((y - mean_y)**2 for y in vals_v11) / n)
    corr = cov / (std_x * std_y) if std_x and std_y else 0
    print(f"\nCorrelation v11 vs v12: {corr:.3f}")

    # Distribution
    print(f"\nDistribution of KPI v12:")
    brackets = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    for lo, hi in brackets:
        count = sum(1 for v in vals_v12 if lo <= v < hi)
        print(f"  {lo:>2}-{hi:<2}: {count:>3} deputies {'█' * count}")
    count_100 = sum(1 for v in vals_v12 if v >= 100)
    if count_100:
        print(f"  100+: {count_100:>3} deputies")

    return results


if __name__ == "__main__":
    main()
