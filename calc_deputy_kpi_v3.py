#!/usr/bin/env python3
"""
KPI v3: комбінований Score з урахуванням комітетської роботи.

Формула (0-100):
  Score = 0.30×norm(LEI) + 0.20×norm(ПЯ) + 0.15×norm(ПДА) + 0.10×norm(ВКП) + 0.10×norm(Конверсія) + 0.15×norm(Комітет)

Комітетський бал:
  - Голова комітету: 10
  - Заступник голови: 7
  - Секретар: 5
  - Голова підкомітету: 4
  - Член: 3
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


def calc_kpi_v3():
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

    # Group by faction for normalization
    factions = {}
    for i, d in enumerate(deputies):
        factions.setdefault(d["faction"], []).append(i)

    for faction, indices in factions.items():
        lei_norm = normalize([deputies[i]["lei"] for i in indices])
        py_norm = normalize([deputies[i]["py"] for i in indices])
        pda_norm = normalize([deputies[i]["pda"] for i in indices])
        vkp_norm = normalize([deputies[i]["vkp"] for i in indices])
        conv_norm = normalize([deputies[i]["conv"] for i in indices])
        comm_norm = normalize([deputies[i]["committee_score"] for i in indices])

        for j, idx in enumerate(indices):
            deputies[idx]["lei_norm"] = lei_norm[j]
            deputies[idx]["py_norm"] = py_norm[j]
            deputies[idx]["pda_norm"] = pda_norm[j]
            deputies[idx]["vkp_norm"] = vkp_norm[j]
            deputies[idx]["conv_norm"] = conv_norm[j]
            deputies[idx]["comm_norm"] = comm_norm[j]

            score = (
                WEIGHTS["lei"] * lei_norm[j] +
                WEIGHTS["py"] * py_norm[j] +
                WEIGHTS["pda"] * pda_norm[j] +
                WEIGHTS["vkp"] * vkp_norm[j] +
                WEIGHTS["conv"] * conv_norm[j] +
                WEIGHTS["committee"] * comm_norm[j]
            )
            deputies[idx]["score"] = round(score, 2)

    # Calculate rank within faction
    for faction, indices in factions.items():
        ranked = sorted(indices, key=lambda i: deputies[i]["score"], reverse=True)
        for rank, idx in enumerate(ranked, 1):
            deputies[idx]["rank"] = rank

    return deputies


def save_kpi_v3(deputies):
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
    print("Розрахунок KPI v3 (з комітетами)...")
    deputies = calc_kpi_v3()
    print(f"Оброблено {len(deputies)} депутатів")

    save_kpi_v3(deputies)
    print("KPI v3 збережено в БД")

    # Top 15
    top = sorted(deputies, key=lambda x: x["score"], reverse=True)[:15]
    print("\nТоп-15 за KPI Score v3:")
    print(f"  {'Name':<25} {'Faction':<20} {'Score':<8} {'LEI':<8} {'ПЯ':<8} {'ПДА':<8} {'ВКП':<8} {'Comm':<8}")
    print("-" * 100)
    for d in top:
        print(f"  {d['name']:<25} {d['faction']:<20} {d['score']:<8.1f} {d['lei']:<8.1f} {d['py']:<8.1f} {d['pda']:<8.1f} {d['vkp']:<8.1f} {d['committee_score']:<8.1f}")
