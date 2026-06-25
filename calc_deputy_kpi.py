#!/usr/bin/env python3
"""
Розрахунок KPI для депутатів на основі АВТОРСТВА законопроєктів.
Використовує bill_sponsors з JOIN через rada_uid.

Формули:
  Q = (S + I) / 10   (якість законопроєкту, 0-1)
  LEI = Sum(Stage Weight * Q)   (законодавча ефективність)
  Avg Tox = average of toxicity

Ваги стадій (значення stage з БД):
  1 (Реєстрація) = 1
  2 (1-ше читання) = 5
  4 (2-ге читання) = 15
  5 (Прийняття/Закон) = 50
"""

import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

STAGE_WEIGHTS = {
    1: 1,    # Реєстрація
    2: 5,    # 1-ше читання
    4: 15,   # 2-ге читання
    5: 50,   # Прийняття/Закон
}


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
    )


def calc_deputy_kpi():
    conn = get_db()
    cur = conn.cursor()

    # Single SQL query: calculate per-deputy KPI from bill_sponsors
    # JOIN mps -> bill_sponsors via rada_uid
    cur.execute("""
        SELECT
            m.id,
            m.name,
            m.faction,
            COALESCE(SUM(
                COALESCE(STAGE_WEIGHTS.w, 1) * ((b.significance + b.impact) / 10.0)
            ), 0) AS lei,
            COALESCE(AVG(b.significance), 0) AS avg_s,
            COALESCE(AVG(b.impact), 0) AS avg_i,
            COALESCE(AVG(b.toxicity), 0) AS avg_tox,
            COUNT(DISTINCT b.id) AS bills_count
        FROM mps m
        LEFT JOIN bill_sponsors bs ON bs.rada_uid = m.rada_uid
        LEFT JOIN bills b ON b.id = bs.bill_id AND b.significance IS NOT NULL
        LEFT JOIN (VALUES (1,1),(2,5),(4,15),(5,50)) AS STAGE_WEIGHTS(stage, w)
            ON b.stage = STAGE_WEIGHTS.stage
        GROUP BY m.id, m.name, m.faction
        ORDER BY lei DESC
    """)

    results = []
    for row in cur.fetchall():
        results.append({
            "id": row[0], "name": row[1], "faction": row[2] or "",
            "lei": round(float(row[3]), 2),
            "avg_s": round(float(row[4]), 2),
            "avg_i": round(float(row[5]), 2),
            "avg_tox": round(float(row[6]), 2),
            "bills_analyzed": int(row[7]),
        })

    cur.close()
    conn.close()
    return results


def save_kpi_to_db(results):
    conn = get_db()
    cur = conn.cursor()
    for r in results:
        cur.execute(
            "UPDATE mps SET lei=%s, avg_s=%s, avg_i=%s, avg_tox=%s WHERE id=%s",
            (r["lei"], r["avg_s"], r["avg_i"], r["avg_tox"], r["id"]),
        )
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    print("Розрахунок KPI (авторство законопроєктів)...")
    results = calc_deputy_kpi()
    print(f"Оброблено {len(results)} депутатів")
    save_kpi_to_db(results)
    print("KPI збережено в БД")

    top = sorted(results, key=lambda x: x["lei"], reverse=True)[:15]
    print("\nТоп-15 за LEI:")
    for r in top:
        print(f"  {r['name']} ({r['faction']}): LEI={r['lei']}, AvgS={r['avg_s']}, AvgI={r['avg_i']}, Tox={r['avg_tox']}, Bills={r['bills_analyzed']}")

    zero_count = sum(1 for r in results if r["lei"] == 0)
    nonzero_count = sum(1 for r in results if r["lei"] > 0)
    print(f"\nСтатистика: {nonzero_count} з LEI>0, {zero_count} з LEI=0")
