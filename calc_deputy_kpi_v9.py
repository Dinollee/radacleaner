#!/usr/bin/env python3
"""
KPI v9: Quality/Risk separation + Authorship split.

Formula (0-100):
  Score = 0.20×norm(LEI) + 0.15×norm(ПЯ) + 0.10×norm(ПДА)
        + 0.20×norm(Quality) + 0.15×norm(Committee)
        + 0.10×norm(Conv) + 0.10×norm(RiskPenalty)

Key changes from v8:
  - LEI & Conv: only order=0 bills (primary authorship)
  - Quality: pre-calculated by calc_bill_quality.py (weighted by sponsor_order)
  - RiskPenalty: 100 - avg_risk_score × 20 (replaces Impact)
  - Impact metric REMOVED (was rewarding dangerous laws)
"""
import math
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

WEIGHTS = {
    "lei": 0.20,
    "py": 0.15,
    "pda": 0.10,
    "quality": 0.20,
    "committee": 0.15,
    "conv": 0.10,
    "risk_penalty": 0.10,
}

MIN_FACTION_SIZE = 10


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def normalize(values):
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mx == mn:
        return [50.0] * len(values)
    return [(v - mn) / (mx - mn) * 100 for v in values]


def calc_kpi_v9():
    conn = get_db()
    cur = conn.cursor()

    # LEI and Conv use only primary authorship (order=0)
    cur.execute("""
        SELECT
            m.id, m.name, m.faction,
            COALESCE(m.py, 0) as py,
            COALESCE(m.pda, 0) as pda,
            COALESCE(m.committee_score, 0) as committee_score,
            COALESCE(m.bill_quality_score, 50) as quality,
            COALESCE(m.avg_risk_score, 0) as avg_risk,
            COALESCE(m.bills_analyzed_count, 0) as analyzed,
            COALESCE(m.kpb, 1.0) as kpb,
            COALESCE(
                (SELECT COUNT(*)
                 FROM bill_sponsors bs
                 JOIN bills b ON b.id = bs.bill_id AND b.stage = 4
                 WHERE bs.rada_uid = m.rada_uid AND bs.sponsor_order = 0), 0
            ) as adopted_primary,
            COALESCE(
                (SELECT COUNT(*)
                 FROM bill_sponsors bs
                 WHERE bs.rada_uid = m.rada_uid AND bs.sponsor_order = 0), 0
            ) as total_primary
        FROM mps m
        WHERE m.end_date IS NULL OR m.end_date = ''
        ORDER BY m.name
    """)

    deputies = []
    for row in cur.fetchall():
        dep_id, name, faction, py, pda, committee_score, quality, \
            avg_risk, analyzed, kpb, adopted_primary, total_primary = row

        total_primary = int(total_primary or 0)
        adopted_primary = int(adopted_primary or 0)
        kpb = float(kpb or 1.0)
        avg_risk = float(avg_risk or 0)
        analyzed = int(analyzed or 0)

        kpb_eff = max(kpb, 0.1)

        # LEI: logarithmic with political barrier (order=0 only)
        if total_primary > 0 and adopted_primary > 0:
            lei = math.log(1 + adopted_primary) / math.log(1 + total_primary * kpb_eff)
        else:
            lei = 0.0

        # Conversion: linear with political barrier (order=0 only)
        if total_primary > 0:
            conv = (adopted_primary / total_primary) * kpb_eff * 100
        else:
            conv = 0.0

        # RiskPenalty: high risk → low score (only if analyzed)
        if analyzed > 0:
            risk_penalty = max(100 - avg_risk * 20, 0)
        else:
            risk_penalty = 50  # neutral for deputies with no analyzed bills

        deputies.append({
            "id": dep_id, "name": name, "faction": faction or "",
            "lei": lei, "py": py, "pda": pda,
            "conv": conv, "committee_score": committee_score,
            "quality": quality, "risk_penalty": risk_penalty,
            "kpb": kpb,
        })

    # Count faction sizes
    faction_sizes = {}
    for d in deputies:
        faction_sizes[d["faction"]] = faction_sizes.get(d["faction"], 0) + 1

    # Global normalization
    lei_global = normalize([d["lei"] for d in deputies])
    py_global = normalize([d["py"] for d in deputies])
    pda_global = normalize([d["pda"] for d in deputies])
    conv_global = normalize([d["conv"] for d in deputies])
    comm_global = normalize([d["committee_score"] for d in deputies])
    qual_global = normalize([d["quality"] for d in deputies])
    rp_global = normalize([d["risk_penalty"] for d in deputies])

    # Faction normalization
    factions = {}
    for i, d in enumerate(deputies):
        factions.setdefault(d["faction"], []).append(i)

    lei_faction = {f: normalize([deputies[i]["lei"] for i in idx]) for f, idx in factions.items()}
    py_faction = {f: normalize([deputies[i]["py"] for i in idx]) for f, idx in factions.items()}
    pda_faction = {f: normalize([deputies[i]["pda"] for i in idx]) for f, idx in factions.items()}
    conv_faction = {f: normalize([deputies[i]["conv"] for i in idx]) for f, idx in factions.items()}
    comm_faction = {f: normalize([deputies[i]["committee_score"] for i in idx]) for f, idx in factions.items()}
    qual_faction = {f: normalize([deputies[i]["quality"] for i in idx]) for f, idx in factions.items()}
    rp_faction = {f: normalize([deputies[i]["risk_penalty"] for i in idx]) for f, idx in factions.items()}

    # Apply hybrid normalization
    for i, d in enumerate(deputies):
        use_faction = faction_sizes.get(d["faction"], 0) >= MIN_FACTION_SIZE

        if use_faction and d["faction"] in lei_faction:
            indices = factions[d["faction"]]
            pos = indices.index(i)
            lei_n = lei_faction[d["faction"]][pos]
            py_n = py_faction[d["faction"]][pos]
            pda_n = pda_faction[d["faction"]][pos]
            conv_n = conv_faction[d["faction"]][pos]
            comm_n = comm_faction[d["faction"]][pos]
            qual_n = qual_faction[d["faction"]][pos]
            rp_n = rp_faction[d["faction"]][pos]
        else:
            lei_n = lei_global[i]
            py_n = py_global[i]
            pda_n = pda_global[i]
            conv_n = conv_global[i]
            comm_n = comm_global[i]
            qual_n = qual_global[i]
            rp_n = rp_global[i]

        score = (
            WEIGHTS["lei"] * lei_n +
            WEIGHTS["py"] * py_n +
            WEIGHTS["pda"] * pda_n +
            WEIGHTS["quality"] * qual_n +
            WEIGHTS["committee"] * comm_n +
            WEIGHTS["conv"] * conv_n +
            WEIGHTS["risk_penalty"] * rp_n
        )
        d["score"] = round(score, 2)

    # Rank within faction
    for faction, indices in factions.items():
        ranked = sorted(indices, key=lambda i: deputies[i]["score"], reverse=True)
        for rank, idx in enumerate(ranked, 1):
            deputies[idx]["rank"] = rank

    return deputies


def save_kpi_v9(deputies):
    conn = get_db()
    cur = conn.cursor()

    for d in deputies:
        cur.execute("""
            UPDATE mps SET
                kpi_score = %s,
                kpi_rank = %s
            WHERE id = %s
        """, (d["score"], d.get("rank", 0), d["id"]))

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    print("Розрахунок KPI v9 (Quality/Risk + Authorship)...")
    deputies = calc_kpi_v9()
    print(f"Оброблено {len(deputies)} депутатів")

    save_kpi_v9(deputies)
    print("KPI v9 збережено в БД")

    top = sorted(deputies, key=lambda x: x["score"], reverse=True)[:15]
    print("\nТоп-15 за KPI Score v9:")
    print(f"  {'Name':<25} {'Faction':<20} {'Score':<8} {'LEI':<8} {'Quality':<8} {'Risk':<8} {'Comm':<8}")
    print("-" * 110)
    for d in top:
        print(f"  {d['name']:<25} {d['faction']:<20} {d['score']:<8.1f} {d['lei']:<8.3f} {d['quality']:<8.2f} {d['risk_penalty']:<8.1f} {d['committee_score']:<8.1f}")
