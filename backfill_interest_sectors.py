#!/usr/bin/env python3
"""backfill_interest_sectors.py — бекфілл поля interest_sectors у risk_assessments.

Процедурним аналізам ставить [] без LLM. Непроцедурним — витягує 0-3 галузі
з наявного raw_analysis (короткий виклик LLM, не перечитує текст закону).
Ліміт за запуск: нові аналізи отримують поле одразу (rag_engine), цей скрипт
доганяє історію. Запускається щодня о 08:30 (після night-batch), потім
у тому ж сервісі рахує профілі calc_interest_profiles.py.
"""
import argparse
import json
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src.llm_client import llm_completion_raw
from src.prompts import INTEREST_SECTORS, RISK_ANALYSIS_SYSTEM_PROMPT

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"

EXTRACT_PROMPT = """Нижче — готовий аналіз законопроєкту Верховної Ради.
Визнач, які галузі отримують реальні вигоди або пільги за цим законом.

Дозволені галузі (ЛИШЕ ці значення): {sectors}.

Правила: 0-3 галузі; без явних галузевих вигод — []; процедурному — [];
не плутай «на кого поширюється дія» з «хто виграє».

Відповідь — ТІЛЬКИ JSON без пояснень:
{{"interest_sectors": ["..."]}}

Аналіз законопроєкту:
{analysis}"""


def parse_sectors(raw: str) -> list[str]:
    """llm_completion_raw повертає текст — парсимо самі (він ламається на масивах)."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    sectors = data.get("interest_sectors")
    if not isinstance(sectors, list):
        return []
    allowed = set(INTEREST_SECTORS)
    return [s for s in sectors if isinstance(s, str) and s in allowed][:3]


def backfill(limit: int) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # 1. Процедурні — [] без LLM
    cur.execute("""
        UPDATE risk_assessments
        SET json_data = jsonb_set(json_data::jsonb, '{interest_sectors}', '[]'::jsonb)::text
        WHERE (json_data::jsonb -> 'interest_sectors') IS NULL
          AND (json_data::jsonb ->> 'is_procedural') = 'true'
    """)
    print(f"Процедурних позначено []: {cur.rowcount}")

    # 2. Непроцедурні — LLM-екстракція з raw_analysis, спочатку найризиковіші
    cur.execute("""
        SELECT ra.id, ra.raw_analysis
        FROM risk_assessments ra
        LEFT JOIN bills b ON b.id = ra.bill_id
        WHERE (ra.json_data::jsonb -> 'interest_sectors') IS NULL
          AND (ra.json_data::jsonb ->> 'is_procedural') = 'false'
          AND ra.raw_analysis IS NOT NULL AND length(ra.raw_analysis) > 100
        ORDER BY b.risk_score DESC NULLS LAST, ra.id DESC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    print(f"До обробки: {len(rows)} (ліміт {limit})")

    ok = fail = 0
    for ra_id, analysis in rows:
        try:
            raw = llm_completion_raw(
                EXTRACT_PROMPT.format(sectors=", ".join(INTEREST_SECTORS), analysis=analysis[:12000]),
                system_prompt=RISK_ANALYSIS_SYSTEM_PROMPT,
                max_tokens=300,
            )
            sectors = parse_sectors(raw)
            cur.execute("""
                UPDATE risk_assessments
                SET json_data = jsonb_set(json_data::jsonb, '{interest_sectors}', %s::jsonb)::text
                WHERE id = %s
            """, (json.dumps(sectors, ensure_ascii=False), ra_id))
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  FAIL id={ra_id}: {str(e)[:120]}")
        if (ok + fail) % 50 == 0:
            conn.commit()
            print(f"  прогрес: {ok + fail}/{len(rows)} (ok={ok}, fail={fail})")

    conn.commit()

    cur.execute("""
        SELECT count(*) FROM risk_assessments
        WHERE (json_data::jsonb -> 'interest_sectors') IS NULL
          AND (json_data::jsonb ->> 'is_procedural') = 'false'
    """)
    left = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"Готово: ok={ok}, fail={fail}, залишилось непроцедурних без поля: {left}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    backfill(args.limit)
