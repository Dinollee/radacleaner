#!/usr/bin/env python3
"""
KPI v4: hybrid normalization (faction + global for small groups).

Rules:
- Factions with 10+ members: normalize within faction
- Позафракційні and small factions (<10): normalize globally
"""
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

WEIGHTS = {
    "lei": 0.30,
    "py": 0.20,
    "pda": 0.15,
    "vkp": 0.10,
    "conv": 0.10,
    "committee": 0.15,
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


def calc_kpi_v4():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            m.id, m.name, m.faction,
            COALESCE(m.lei, 0) as lei,
            COALESCE(m.py, 0) as py,
            COALESCE(m.pda, 0) as pda,
            COALESCE(m.vkp, 0) as vkp,
            COALESCE(m.total_bills, 0) as total_bills,
            COALESCE(m.total_laws, 0) as total_laws,
            COALESCE(m.committee_score, 0) as committee_score
        FROM mps m
        WHERE m.end_date IS NULL OR m.end_date = ''
        ORDER BY m.name
    """)
    
    deputies = []
    for row in cur.fetchall():
        dep_id, name, faction, lei, py, pda, vkp, total_bills, total_laws, committee_score = row
        conv = (total_laws / total_bills * 100) if total_bills > 0 else 0
        deputies.append({
            "id": dep_id, "name": name, "faction": faction or "",
            "lei": lei, "py": py, "pda": pda, "vkp": vkp, 
            "conv": conv, "committee_score": committee_score,
        })

    # Count faction sizes
    faction_sizes = {}
    for d in deputies:
        faction_sizes[d["faction"]] = faction_sizes.get(d["faction"], 0) + 1

    # Global normalization for all
    lei_global = normalize([d["lei"] for d in deputies])
    py_global = normalize([d["py"] for d in deputies])
    pda_global = normalize([d["pda"] for d in deputies])
    vkp_global = normalize([d["vkp"] for d in deputies])
    conv_global = normalize([d["conv"] for d in deputies])
    comm_global = normalize([d["committee_score"] for d in deputies])

    # Faction normalization
    factions = {}
    for i, d in enumerate(deputies):
        factions.setdefault(d["faction"], []).append(i)

    lei_faction = {}
    py_faction = {}
    pda_faction = {}
    vkp_faction = {}
    conv_faction = {}
    comm_faction = {}

    for faction, indices in factions.items():
        lei_faction[faction] = normalize([deputies[i]["lei"] for i in indices])
        py_faction[faction] = normalize([deputies[i]["py"] for i in indices])
        pda_faction[faction] = normalize([deputies[i]["pda"] for i in indices])
        vkp_faction[faction] = normalize([deputies[i]["vkp"] for i in indices])
        conv_faction[faction] = normalize([deputies[i]["conv"] for i in indices])
        comm_faction[faction] = normalize([deputies[i]["committee_score"] for i in indices])

    # Apply hybrid normalization
    for i, d in enumerate(deputies):
        use_faction = faction_sizes.get(d["faction"], 0) >= MIN_FACTION_SIZE
        
        if use_faction and d["faction"] in lei_faction:
            indices = factions[d["faction"]]
            pos = indices.index(i)
            lei_n = lei_faction[d["faction"]][pos]
            py_n = py_faction[d["faction"]][pos]
            pda_n = pda_faction[d["faction"]][pos]
            vkp_n = vkp_faction[d["faction"]][pos]
            conv_n = conv_faction[d["faction"]][pos]
            comm_n = comm_faction[d["faction"]][pos]
        else:
            lei_n = lei_global[i]
            py_n = py_global[i]
            pda_n = pda_global[i]
            vkp_n = vkp_global[i]
            conv_n = conv_global[i]
            comm_n = comm_global[i]

        d["lei_norm"] = lei_n
        d["py_norm"] = py_n
        d["pda_norm"] = pda_n
        d["vkp_norm"] = vkp_n
        d["conv_norm"] = conv_n
        d["comm_norm"] = comm_n

        score = (
            WEIGHTS["lei"] * lei_n +
            WEIGHTS["py"] * py_n +
            WEIGHTS["pda"] * pda_n +
            WEIGHTS["vkp"] * vkp_n +
            WEIGHTS["conv"] * conv_n +
            WEIGHTS["committee"] * comm_n
        )
        d["score"] = round(score, 2)

    # Rank within faction
    for faction, indices in factions.items():
        ranked = sorted(indices, key=lambda i: deputies[i]["score"], reverse=True)
        for rank, idx in enumerate(ranked, 1):
            deputies[idx]["rank"] = rank

    return deputies


def save_kpi_v4(deputies):
    conn = get_db()
    cur = conn.cursor()

    for d in deputies:
        cur.execute("""
            UPDATE mps SET
                kpi_score = %s,
                kpi_rank = %s,
                kpi_lei_norm = %s,
                kpi_py_norm = %s,
                kpi_pda_norm = %s,
                kpi_vkp_norm = %s,
                kpi_conv_norm = %s
            WHERE id = %s
        """, (
            d["score"], d.get("rank", 0),
            round(d.get("lei_norm", 0), 2),
            round(d.get("py_norm", 0), 2),
            round(d.get("pda_norm", 0), 2),
            round(d.get("vkp_norm", 0), 2),
            round(d.get("conv_norm", 0), 2),
            d["id"],
        ))

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    print("Розрахунок KPI v4 (гибридна нормалізація)...")
    deputies = calc_kpi_v4()
    print(f"Оброблено {len(deputies)} депутатів")

    save_kpi_v4(deputies)
    print("KPI v4 збережено в БД")

    # Top 15
    top = sorted(deputies, key=lambda x: x["score"], reverse=True)[:15]
    print("\nТоп-15 за KPI Score v4:")
    print(f"  {'Name':<25} {'Faction':<20} {'Score':<8} {'LEI':<8} {'ПЯ':<8} {'ПДА':<8} {'ВКП':<8} {'Comm':<8}")
    print("-" * 100)
    for d in top:
        print(f"  {d['name']:<25} {d['faction']:<20} {d['score']:<8.1f} {d['lei']:<8.1f} {d['py']:<8.1f} {d['pda']:<8.1f} {d['vkp']:<8.1f} {d['committee_score']:<8.1f}")
