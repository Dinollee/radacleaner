#!/usr/bin/env python3
"""Точковий бекфіл summary/law_summary для непроцедурних аналізів без цих полів.

Використовує _build_fallback_summaries з rag_engine (та сама логіка, що й для
майбутніх аналізів). Ставить summary_source="fallback".

Usage:
    python scripts/backfill_summaries.py            # dry-run: показати що буде зроблено
    python scripts/backfill_summaries.py --apply    # застосувати зміни
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.d1_client import d1_query, d1_exec
from src.rag_engine import _build_fallback_summaries


def main():
    apply = "--apply" in sys.argv
    rows = d1_query(
        "SELECT ra.id, ra.bill_id, b.bill_number, ra.json_data "
        "FROM risk_assessments ra JOIN bills b ON b.id = ra.bill_id "
        "WHERE COALESCE(ra.json_data::jsonb->>'is_procedural','false')::boolean = false "
        "AND (COALESCE(ra.json_data::jsonb->>'summary','') = '' "
        "     OR COALESCE(ra.json_data::jsonb->>'law_summary','') = '')"
    )
    print(f"Знайдено {len(rows)} непроцедурних аналізів з порожніми полями")
    fixed = 0
    for r in rows:
        try:
            data = json.loads(r["json_data"]) if r["json_data"] else {}
        except (TypeError, ValueError):
            continue
        before_summary = data.get("summary", "")
        before_law = data.get("law_summary", "")
        _build_fallback_summaries(data, r["bill_number"])
        if data.get("summary_source") != "fallback":
            continue
        fixed += 1
        print(f"\n#{r['bill_number']} (ra_id={r['id']}):")
        if not before_law:
            print(f"  law_summary: {data['law_summary'][:120]}")
        if not before_summary:
            print(f"  summary: {data['summary'][:120]}")
        if apply:
            d1_exec("raw_sql", {
                "sql": "UPDATE risk_assessments SET json_data=%s, raw_analysis=%s WHERE id=%s",
                "params": [json.dumps(data, ensure_ascii=False),
                           data.get("law_summary") or data.get("summary") or "", r["id"]],
            })
    print(f"\n{'ОНОВЛЕНО' if apply else 'DRY-RUN'}: {fixed} рядків")
    if not apply and fixed:
        print("Запустіть з --apply для застосування")


if __name__ == "__main__":
    main()
