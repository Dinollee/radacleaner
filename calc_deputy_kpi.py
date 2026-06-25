#!/usr/bin/env python3
"""
Розрахунок KPI для депутатів.
Формули:
  Q = (S + I) / 10
  LEI = Sum(Stage Weight * Q)
  Avg Tox = average of toxicity

Ваги стадій:
  Реєстрація (0) = 1
  1-ше читання (1) = 5
  2-ге читання (2) = 15
  Прийняття (3) = 50
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.d1_client import d1_query, d1_exec_sql


STAGE_WEIGHTS = {
    0: 1,    # Реєстрація
    1: 5,    # 1-ше читання
    2: 15,   # 2-ге читання
    3: 50,   # Прийняття
}


def calc_deputy_kpi():
    """
    Розраховує KPI для кожного депутата.
    Повертає список з метриками: lei, avg_s, avg_i, avg_tox.
    """
    deputies = d1_query("SELECT id, name, faction FROM mps")
    
    results = []
    
    for deputy in deputies:
        mp_name = deputy["name"]
        
        bills_with_scores = d1_query("""
            SELECT DISTINCT b.id, b.stage, b.significance, b.impact, b.toxicity
            FROM bills b
            JOIN votes v ON v.bill_id = b.id
            JOIN mp_votes mv ON mv.vote_id = v.vote_id
            WHERE mv.mp_name = %s
              AND b.significance IS NOT NULL
        """, [mp_name])
        
        if not bills_with_scores:
            results.append({
                "id": deputy["id"],
                "name": mp_name,
                "faction": deputy.get("faction", ""),
                "lei": 0,
                "avg_s": 0,
                "avg_i": 0,
                "avg_tox": 0,
                "bills_analyzed": 0
            })
            continue
        
        total_q = 0
        total_s = 0
        total_i = 0
        total_tox = 0
        bill_count = len(bills_with_scores)
        
        for bill in bills_with_scores:
            s = bill.get("significance") or 0
            i = bill.get("impact") or 0
            tox = bill.get("toxicity") or 0
            stage = bill.get("stage") or 0
            
            q = (s + i) / 10
            weight = STAGE_WEIGHTS.get(stage, 1)
            
            total_q += weight * q
            total_s += s
            total_i += i
            total_tox += tox
        
        lei = total_q
        avg_s = total_s / bill_count if bill_count > 0 else 0
        avg_i = total_i / bill_count if bill_count > 0 else 0
        avg_tox = total_tox / bill_count if bill_count > 0 else 0
        
        results.append({
            "id": deputy["id"],
            "name": mp_name,
            "faction": deputy.get("faction", ""),
            "lei": round(lei, 2),
            "avg_s": round(avg_s, 2),
            "avg_i": round(avg_i, 2),
            "avg_tox": round(avg_tox, 2),
            "bills_analyzed": bill_count
        })
    
    return results


def save_kpi_to_db(results):
    """
    Зберігає KPI в таблицю mps.
    """
    for r in results:
        try:
            d1_exec_sql(
                "UPDATE mps SET lei=%s, avg_s=%s, avg_i=%s, avg_tox=%s WHERE id=%s",
                [r["lei"], r["avg_s"], r["avg_i"], r["avg_tox"], r["id"]]
            )
        except Exception as e:
            print(f"Помилка оновлення KPI для {r['name']}: {e}")


if __name__ == "__main__":
    print("Розрахунок KPI для депутатів...")
    results = calc_deputy_kpi()
    
    print(f"Оброблено {len(results)} депутатів")
    
    save_kpi_to_db(results)
    
    print("KPI збережено в БД")
    
    for r in results[:5]:
        print(f"  {r['name']}: LEI={r['lei']}, AvgS={r['avg_s']}, AvgI={r['avg_i']}, AvgTox={r['avg_tox']}, Bills={r['bills_analyzed']}")
