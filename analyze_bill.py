#!/usr/bin/env python3
"""Аналіз одного законопроекту через LLM.

Usage:
    python analyze_bill.py 15314          — аналіз за номером
    python analyze_bill.py 15314 --force  — переаналіз (ігнорувати кеш)
"""
import sys
import json
from src.rag_engine import process_bill
from src.d1_client import d1_query
from src.config import log

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_bill.py <bill_number> [--force]")
        sys.exit(1)

    bill_number = sys.argv[1]
    force = "--force" in sys.argv

    # Знаходимо закон в D1
    rows = d1_query(
        "SELECT id, bill_number, title, current_status, url FROM bills WHERE bill_number = ?",
        [bill_number],
    )
    if not rows:
        print(f"Bill #{bill_number} not found in D1")
        sys.exit(1)

    bill = rows[0]
    info = {
        "id": bill["id"],
        "bill_number": bill["bill_number"],
        "title": bill["title"],
        "status": bill["current_status"],
        "url": bill.get("url", ""),
    }

    if force:
        # Видаляємо старий аналіз + скидаємо кеш хешів
        from src.d1_client import d1_exec
        d1_exec("raw_sql", {
            "sql": f"DELETE FROM risk_assessments WHERE bill_id={bill['id']}",
        })
        d1_exec("raw_sql", {
            "sql": f"DELETE FROM rag_documents WHERE bill_id={bill['id']}",
        })
        d1_exec("raw_sql", {
            "sql": f"UPDATE bills SET text_hash=NULL, plain_text=NULL WHERE id={bill['id']}",
        })
        print(f"Cleared old analysis + cache for #{bill_number}")

    print(f"Analyzing #{bill_number} — {info['title'][:60]}...")
    info_result, data = process_bill(info, test_mode=True)

    if data:
        proc = "✅ ПРОЦЕДУРНИЙ" if data.get("is_procedural") else "❌ НЕПРОЦЕДУРНИЙ"
        risk = data.get("risk_level", "null")
        risks_count = len(data.get("detailed_risks", []))
        model = data.get("model_used", "?")
        print(f"\nResult: {proc}")
        print(f"  Model: {model}")
        print(f"  Risk level: {risk}")
        print(f"  Risks: {risks_count}")
        print(f"  Summary: {data.get('summary', '')[:120]}")
        if data.get("detailed_risks"):
            print("\nRisks:")
            for i, r in enumerate(data["detailed_risks"], 1):
                print(f"  {i}. {r[:120]}")
    else:
        print("Skipped (cache hit or error)")

if __name__ == "__main__":
    main()
