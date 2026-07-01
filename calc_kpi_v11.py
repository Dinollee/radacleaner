#!/usr/bin/env python3
"""KPI v11 — Three-level system: KPI + Profile + Signals."""
import json
import math
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

WEIGHTS_PATH = Path(__file__).parent / "kpi_weights.json"


def load_weights():
    with open(WEIGHTS_PATH) as f:
        return json.load(f)["kpi_v11"]


def get_conn():
    return psycopg2.connect(DB_DSN)


def normalize(values, floor=0, ceiling=100):
    """Normalize list of values to 0-100 scale."""
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [50.0] * len(values)
    return [floor + (v - min_v) / (max_v - min_v) * (ceiling - floor) for v in values]


def calc_kpi_components(conn, weights):
    """Level 1: Calculate KPI components for all active deputies."""
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, faction,
            COALESCE(lei, 0) as lei,
            COALESCE(py, 0) as py,
            COALESCE(pda, 0) as pda,
            COALESCE(vkp, 0) as vkp,
            COALESCE(adoption_rate, 0) as adoption_rate,
            COALESCE(requests_with_response, 0) as requests,
            COALESCE(committee_score, 0) as committee,
            COALESCE(bill_quality_score, 0) as quality,
            COALESCE(documents_count, 0) as docs,
            COALESCE(total_bills, 0) as total_bills,
            COALESCE(total_laws, 0) as total_laws
        FROM mps
        WHERE (end_date IS NULL OR end_date = '') AND total_bills > 0
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    deputies = [dict(zip(cols, row)) for row in rows]

    if not deputies:
        return []

    # Normalize each component
    lei_vals = normalize([d["lei"] for d in deputies])
    discipline_vals = normalize([(d["py"] + d["pda"] + d["vkp"]) / 3 for d in deputies])
    efficiency_vals = normalize([d["adoption_rate"] for d in deputies])
    control_vals = normalize([(d["requests"] * 3.57 + d["committee"] * 10) for d in deputies])  # scale requests to 0-100
    quality_vals = normalize([d["quality"] * 20 for d in deputies])  # scale 0-5 to 0-100

    for i, d in enumerate(deputies):
        d["lei_norm"] = lei_vals[i]
        d["discipline_norm"] = discipline_vals[i]
        d["efficiency_norm"] = efficiency_vals[i]
        d["control_norm"] = control_vals[i]
        d["quality_norm"] = quality_vals[i]

        d["kpi_score"] = (
            weights["effectiveness"] * lei_vals[i] +
            weights["discipline"] * discipline_vals[i] +
            weights["efficiency"] * efficiency_vals[i] +
            weights["control"] * control_vals[i] +
            weights["quality"] * quality_vals[i]
        )

    # Rank
    deputies.sort(key=lambda d: d["kpi_score"], reverse=True)
    for i, d in enumerate(deputies):
        d["kpi_rank"] = i + 1

    return deputies


def calc_profile(deputy):
    """Level 2: Calculate profile metrics for a deputy."""
    shannon = deputy.get("shannon_diversity", 0)
    if shannon < 2:
        spec = "Дуже вузька"
    elif shannon < 3:
        spec = "Вузька"
    elif shannon < 4.5:
        spec = "Середня"
    else:
        spec = "Широка"

    ar = deputy.get("authorship_ratio", 0) if "authorship_ratio" in deputy else 0
    if ar > 0.5:
        style = "Індивідуальний"
    elif ar > 0.2:
        style = "Змішаний"
    else:
        style = "Колективний"

    eu = deputy.get("eu_integration_score", 0)
    eu_ratio = deputy.get("eu_euro_bills", 0) / max(deputy.get("total_bills", 1), 1) * 100

    return {
        "specialization": spec,
        "shannon": round(shannon, 2),
        "authorship_style": style,
        "eu_ratio": round(eu_ratio, 1),
    }


def calc_signals(deputy):
    """Level 3: Calculate analytical signals for a deputy."""
    warnings = []
    strengths = []
    features = []

    total = deputy.get("total_bills", 0)
    laws = deputy.get("total_laws", 0)
    py = deputy.get("py", 0)
    pda = deputy.get("pda", 0)
    quality = deputy.get("bill_quality_score", 0)
    committee = deputy.get("committee_score", 0)
    shannon = deputy.get("shannon_diversity", 0)
    ar = deputy.get("authorship_ratio", 0)
    eu_ratio = deputy.get("eu_euro_bills", 0) / max(total, 1) * 100
    urgent_ratio = 0  # TODO: calculate from bills
    adoption = deputy.get("adoption_rate", 0)
    coauthors = deputy.get("unique_coauthors", 0)

    # Warnings
    if total > 200 and laws < 10:
        warnings.append("Законодавчий спам")
    if committee == 0 and total > 0:
        warnings.append("Не працює в комітеті")
    if shannon < 2 and total > 5:
        warnings.append("Дуже вузька спеціалізація")
    if ar < 0.05 and total > 5:
        warnings.append("Аномально багато соавторств")
    if py < 30:
        warnings.append("Низька відвідуваність")

    # Strengths
    if quality > 70:
        strengths.append("Висока якість законів")
    if shannon < 3 and total > 10:
        strengths.append("Стабільна спеціалізація")
    if adoption > 30:
        strengths.append("Висока результативність")
    if py > 80 and pda > 80:
        strengths.append("Висока дисципліна")

    # Features
    if ar < 0.15:
        features.append("Колективний стиль авторства")
    if shannon < 3:
        features.append("Вузький експерт")
    if eu_ratio > 15:
        features.append("Євроінтеграційний профіль")

    return warnings, strengths, features


def main():
    weights = load_weights()
    conn = get_conn()

    deputies = calc_kpi_components(conn, weights)
    print(f"Calculated KPI for {len(deputies)} deputies")

    cur = conn.cursor()
    for d in deputies:
        profile = calc_profile(d)
        warnings, strengths, features = calc_signals(d)

        cur.execute("""
            UPDATE mps SET
                kpi_v11_score = %s,
                kpi_v11_rank = %s,
                kpi_v11_effectiveness = %s,
                kpi_v11_discipline = %s,
                kpi_v11_efficiency = %s,
                kpi_v11_control = %s,
                kpi_v11_quality = %s,
                signal_warnings = %s,
                signal_strengths = %s,
                signal_features = %s
            WHERE id = %s
        """, (
            round(d["kpi_score"], 1),
            d["kpi_rank"],
            round(d["lei_norm"], 1),
            round(d["discipline_norm"], 1),
            round(d["efficiency_norm"], 1),
            round(d["control_norm"], 1),
            round(d["quality_norm"], 1),
            json.dumps(warnings, ensure_ascii=False),
            json.dumps(strengths, ensure_ascii=False),
            json.dumps(features, ensure_ascii=False),
            d["id"],
        ))

    conn.commit()
    cur.close()
    conn.close()

    # Print top 10
    print("\nTop 10 KPI v11:")
    print(f"{'#':<3} {'Name':<25} {'KPI':>6} {'Eff':>5} {'Disc':>5} {'Res':>5} {'Ctrl':>5} {'Qual':>5}")
    for d in deputies[:10]:
        print(f"{d['kpi_rank']:<3} {d['name']:<25} {d['kpi_score']:>6.1f} {d['lei_norm']:>5.1f} {d['discipline_norm']:>5.1f} {d['efficiency_norm']:>5.1f} {d['control_norm']:>5.1f} {d['quality_norm']:>5.1f}")


if __name__ == "__main__":
    main()
