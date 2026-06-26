#!/usr/bin/env python3
"""
Розрахунок KPI для депутатів — покращена версія (v2).

Комбінований Score (0-100):
  Score = 0.35 × norm(LEI) + 0.25 × norm(ПЯ) + 0.20 × norm(ПДА) + 0.10 × norm(ВКП) + 0.10 × norm(Конверсія)

Нормалізація:
  - По фракції (порівняння в межах однієї фракції)
  - Мін-макс: norm(x) = (x - min) / (max - min) × 100
  - Якщо max = min (всі однакові) → norm = 50

Збереження:
  - mps.kpi_score — комбінований Score (0-100)
  - mps.kpi_rank — рейтинг у фракції
  - mps.kpi_lei_norm, kpi_py_norm, kpi_pda_norm, kpi_vkp_norm, kpi_conv_norm — нормалізовані компоненти
"""

import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

WEIGHTS = {
    "lei": 0.35,
    "py": 0.25,
    "pda": 0.20,
    "vkp": 0.10,
    "conv": 0.10,
}


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def normalize(values: list[float]) -> list[float]:
    """Min-max normalization to 0-100 scale."""
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mx == mn:
        return [50.0] * len(values)
    return [(v - mn) / (mx - mn) * 100 for v in values]


def calc_kpi_v2():
    conn = get_db()
    cur = conn.cursor()

    # Fetch all active deputies with their metrics
    cur.execute("""
        SELECT
            m.id, m.name, m.faction,
            COALESCE(m.lei, 0) as lei,
            COALESCE(m.py, 0) as py,
            COALESCE(m.pda, 0) as pda,
            COALESCE(m.vkp, 0) as vkp,
            COALESCE(m.total_bills, 0) as total_bills,
            COALESCE(m.total_laws, 0) as total_laws
        FROM mps m
        WHERE m.end_date IS NULL OR m.end_date = ''
        ORDER BY m.name
    """)
    deputies = []
    for row in cur.fetchall():
        dep_id, name, faction, lei, py, pda, vkp, total_bills, total_laws = row
        conv = (total_laws / total_bills * 100) if total_bills > 0 else 0
        deputies.append({
            "id": dep_id, "name": name, "faction": faction or "",
            "lei": lei, "py": py, "pda": pda, "vkp": vkp, "conv": conv,
        })

    # Group by faction for normalization
    factions: dict[str, list[int]] = {}
    for i, d in enumerate(deputies):
        factions.setdefault(d["faction"], []).append(i)

    # Normalize within each faction
    for faction, indices in factions.items():
        lei_vals = [deputies[i]["lei"] for i in indices]
        py_vals = [deputies[i]["py"] for i in indices]
        pda_vals = [deputies[i]["pda"] for i in indices]
        vkp_vals = [deputies[i]["vkp"] for i in indices]
        conv_vals = [deputies[i]["conv"] for i in indices]

        lei_norm = normalize(lei_vals)
        py_norm = normalize(py_vals)
        pda_norm = normalize(pda_vals)
        vkp_norm = normalize(vkp_vals)
        conv_norm = normalize(conv_vals)

        for j, idx in enumerate(indices):
            deputies[idx]["lei_norm"] = lei_norm[j]
            deputies[idx]["py_norm"] = py_norm[j]
            deputies[idx]["pda_norm"] = pda_norm[j]
            deputies[idx]["vkp_norm"] = vkp_norm[j]
            deputies[idx]["conv_norm"] = conv_norm[j]

            # Combined score
            score = (
                WEIGHTS["lei"] * lei_norm[j] +
                WEIGHTS["py"] * py_norm[j] +
                WEIGHTS["pda"] * pda_norm[j] +
                WEIGHTS["vkp"] * vkp_norm[j] +
                WEIGHTS["conv"] * conv_norm[j]
            )
            deputies[idx]["score"] = round(score, 2)

    # Calculate rank within faction
    for faction, indices in factions.items():
        ranked = sorted(indices, key=lambda i: deputies[i]["score"], reverse=True)
        for rank, idx in enumerate(ranked, 1):
            deputies[idx]["rank"] = rank

    return deputies


def save_kpi_v2(deputies):
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
    print("Розрахунок KPI v2 (комбінований Score)...")
    deputies = calc_kpi_v2()
    print(f"Оброблено {len(deputies)} депутатів")

    save_kpi_v2(deputies)
    print("KPI v2 збережено в БД")

    # Top 15 by score
    top = sorted(deputies, key=lambda x: x["score"], reverse=True)[:15]
    print("\nТоп-15 за KPI Score:")
    print(f"  {'Name':<25} {'Faction':<20} {'Score':<8} {'LEI':<8} {'ПЯ':<8} {'ПДА':<8} {'ВКП':<8} {'Conv':<8}")
    print("-" * 100)
    for d in top:
        print(f"  {d['name']:<25} {d['faction']:<20} {d['score']:<8.1f} {d['lei']:<8.1f} {d['py']:<8.1f} {d['pda']:<8.1f} {d['vkp']:<8.1f} {d['conv']:<8.1f}")

    # Statistics
    scores = [d["score"] for d in deputies]
    print(f"\nСтатистика:")
    print(f"  Середній Score: {sum(scores)/len(scores):.1f}")
    print(f"  Мін: {min(scores):.1f}, Макс: {max(scores):.1f}")
    print(f"  Медіана: {sorted(scores)[len(scores)//2]:.1f}")
