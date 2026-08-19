#!/usr/bin/env python3
"""
KPI v8: Додано політичний бар'єр (K_pb).

Формула (0-100):
  Score = 0.20×norm(LEI) + 0.15×norm(ПЯ) + 0.10×norm(ПДА)
        + 0.15×norm(Quality) + 0.15×norm(Committee)
        + 0.10×norm(Conversion) + 0.10×norm(Impact)

Змінені метрики vs v6:
  LEI = LN(1+adopted) / LN(1+total_bills×max(kpb,0.1))
  Conversion = (adopted/total_bills) × kpb × 100%
  Impact = Σ(toxicity×weight)/adopted (avg toxicity of authored bills)
  ПДА = voted/attended × 100% (no IVI)
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
    "quality": 0.15,
    "committee": 0.15,
    "conv": 0.10,
    "impact": 0.10,
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


def calc_kpi_v8():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            m.id, m.name, m.faction,
            COALESCE(m.py, 0) as py,
            COALESCE(m.pda, 0) as pda,
            COALESCE(m.vkp, 0) as vkp,
            COALESCE(m.total_bills, 0) as total_bills,
            COALESCE(m.total_laws, 0) as total_laws,
            COALESCE(m.committee_score, 0) as committee_score,
            COALESCE(m.bill_quality_score, 0) as quality,
            COALESCE(m.requests_with_response, 0) as requests,
            COALESCE(m.kpb, 1.0) as kpb,
            COALESCE(m.avg_tox, 0) as avg_tox,
            COALESCE(
                (SELECT COUNT(*)
                 FROM bill_sponsors bs
                 JOIN bills b ON b.id = bs.bill_id AND b.stage = 4
                 WHERE bs.rada_uid = m.rada_uid), 0
            ) as adopted
        FROM mps m
        WHERE m.end_date IS NULL OR m.end_date = ''
        ORDER BY m.name
    """)

    deputies = []
    for row in cur.fetchall():
        dep_id, name, faction, py, pda, vkp, total_bills, total_laws, \
            committee_score, quality, requests, kpb, avg_tox, adopted = row

        total_bills = int(total_bills or 0)
        total_laws = int(total_laws or 0)
        adopted = int(adopted or 0)
        kpb = float(kpb or 1.0)
        avg_tox = float(avg_tox or 0)

        kpb_eff = max(kpb, 0.1)

        # LEI: logarithmic with political barrier
        if total_bills > 0 and adopted > 0:
            lei = math.log(1 + adopted) / math.log(1 + total_bills * kpb_eff)
        else:
            lei = 0.0

        # Conversion: linear with political barrier
        if total_bills > 0:
            conv = (adopted / total_bills) * kpb_eff * 100
        else:
            conv = 0.0

        # Impact: avg toxicity of authored bills (proxy from avg_tox)
        impact = avg_tox * 100 if avg_tox > 0 else 0.0

        deputies.append({
            "id": dep_id, "name": name, "faction": faction or "",
            "lei": lei, "py": py, "pda": pda,
            "conv": conv, "committee_score": committee_score,
            "quality": quality, "impact": impact,
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
    imp_global = normalize([d["impact"] for d in deputies])

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
    imp_faction = {f: normalize([deputies[i]["impact"] for i in idx]) for f, idx in factions.items()}

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
            imp_n = imp_faction[d["faction"]][pos]
        else:
            lei_n = lei_global[i]
            py_n = py_global[i]
            pda_n = pda_global[i]
            conv_n = conv_global[i]
            comm_n = comm_global[i]
            qual_n = qual_global[i]
            imp_n = imp_global[i]

        score = (
            WEIGHTS["lei"] * lei_n +
            WEIGHTS["py"] * py_n +
            WEIGHTS["pda"] * pda_n +
            WEIGHTS["quality"] * qual_n +
            WEIGHTS["committee"] * comm_n +
            WEIGHTS["conv"] * conv_n +
            WEIGHTS["impact"] * imp_n
        )
        d["score"] = round(score, 2)

    # Rank within faction
    for faction, indices in factions.items():
        ranked = sorted(indices, key=lambda i: deputies[i]["score"], reverse=True)
        for rank, idx in enumerate(ranked, 1):
            deputies[idx]["rank"] = rank

    return deputies


def save_kpi_v8(deputies):
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
    print("Розрахунок KPI v8 (з політичним бар'єром)...")
    deputies = calc_kpi_v8()
    print(f"Оброблено {len(deputies)} депутатів")

    save_kpi_v8(deputies)
    print("KPI v8 збережено в БД")

    top = sorted(deputies, key=lambda x: x["score"], reverse=True)[:15]
    print("\nТоп-15 за KPI Score v8:")
    print(f"  {'Name':<25} {'Faction':<20} {'Score':<8} {'LEI':<8} {'K_pb':<8} {'Quality':<8} {'Comm':<8}")
    print("-" * 110)
    for d in top:
        print(f"  {d['name']:<25} {d['faction']:<20} {d['score']:<8.1f} {d['lei']:<8.3f} {d['kpb']:<8.3f} {d['quality']:<8.2f} {d['committee_score']:<8.1f}")
