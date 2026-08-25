#!/usr/bin/env python3
"""backfill_citizen_impact.py — «що зміниться для громадянина» (до/після) простою мовою.

Процедурним і законам без тексту ставить фіксований об'єкт без LLM.
Непроцедурним — один виклик LLM на повний текст (до 50K символів, контекст
nemotron 1M дозволяє), пріоритет risk_score DESC. Ліміт за запуск: 300
(~3 год). Щодня о 05:30 (citizen-impact.timer). Перерваний запуск резюмиться
сам — WHERE відбирає лише записи без citizen_impact.

Джерело: bills.plain_text → risk_assessments.json_data.citizen_impact.
Споживачі: /api/bills/:id (json_data) → блок «👥 Що зміниться для громадянина».
"""
import argparse
import json
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src.llm_client import llm_completion_raw

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"

MAX_ATTEMPTS = 3

PROCEDURAL_IMPACT = {
    "affects_citizens": False,
    "headline": None,
    "changes": [],
    "no_impact_reason": "Процедурний законопроєкт — не містить норм, що змінюють життя громадян",
}

NO_TEXT_IMPACT = {
    "affects_citizens": False,
    "headline": None,
    "changes": [],
    "no_impact_reason": "Текст законопроєкту недоступний або замалий для аналізу",
}

SYSTEM_PROMPT = """Ти — помічник, що пояснює закони України звичайним громадянам.
Пишеш ЛИШЕ українською мовою, жодного слова іншими мовами: короткі речення,
без канцеляриту та юристермінів («суб'єкт господарювання» → «бізнес»,
«набрання чинності» → «закон запрацює»). Не цитуй статті закону дослівно —
переказуй своїми словами.
Сувора заборона на вигадки: кожне твердження має прямо випливати з тексту закону.
Якщо прямого ефекту для життя громадян немає (бюрократичні/технічні зміни) — чесно
скажи це, не вигадуй наслідків."""

USER_PROMPT = """Законопроєкт №{number}: {title}

Текст законопроєкту:
---
{text}
---

Поясни звичайній людині, що зміниться після прийняття цього закону.

Правила:
- Тільки те, що прямо випливає з тексту вище. Невідоме — не вигадуй.
- 3-5 найважливіших змін у форматі «до / після». Конкретні суми, строки,
  відсотки з тексту переноси як є.
- Кожне поле «before» та «after» — МАКСИМУМ 1-2 короткі речення (до ~200
  символів). Це пояснення для людини, а не витяг із закону.
- «before» — як влаштовано зараз (з тексту або загальновідомо), «after» — як стане.
- «who» — одне коротке речення: кого саме стосується (наприклад «власники житла»,
  «батьки школярів», «усі платники податків»).
- Якщо закон безпосередньо не торкається життя громадян — постав
  affects_citizens=false і коротко поясни у no_impact_reason.

Відповідь — ТІЛЬКИ валідний JSON без markdown:
{{
  "affects_citizens": true,
  "headline": "одне речення простими словами, про що цей закон",
  "changes": [
    {{"before": "...", "after": "...", "who": "..."}}
  ],
  "no_impact_reason": null
}}"""


def parse_impact(raw: str) -> dict | None:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data.get("changes"), list):
        return None
    return data


def _is_ukrainian(text: str) -> bool:
    ukr = sum(1 for c in text if c in "абвгґдеєжзиіїйклмнопрстуфхцчшщьюяіАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯІ")
    lat = sum(1 for c in text if c.isascii() and c.isalpha())
    if lat / max(ukr, 1) > 0.3:
        return False
    # змішані слова на кшталт «wprowadжує» — латиниця і кирилиця в одному слові
    for w in text.split():
        w = w.strip(".,;:!?()«»\"'—-")
        has_lat = any(c.isascii() and c.isalpha() for c in w)
        has_cyr = any("\u0400" <= c <= "\u04FF" for c in w)
        if has_lat and has_cyr:
            return False
    return True


def impact_is_ukrainian(impact: dict) -> bool:
    texts = [impact.get("headline") or "", impact.get("no_impact_reason") or ""]
    for ch in impact.get("changes", []):
        texts += [ch.get("before", ""), ch.get("after", ""), ch.get("who", "")]
    return all(_is_ukrainian(t) for t in texts)


def _selftest() -> None:
    ok = '{"changes": [{"before": "був", "after": "стане", "who": "усі"}], "affects_citizens": true}'
    wrapped = f"Ось відповідь:\n```json\n{ok}\n```"
    assert parse_impact(wrapped)["changes"][0]["who"] == "усі"
    assert parse_impact("no json here") is None
    assert parse_impact('{"nope": []}') is None
    assert _is_ukrainian("Закон вводить новий статус та штрафи за знищення.")
    assert not _is_ukrainian("Закон wprowadжує новий статус.")  # змішане слово
    assert not _is_ukrainian("totally english headline here")  # суцільна латиниця
    assert _is_ukrainian("Штраф від 50 000 до 100 000 грн (до 30% площі).")  # цифри ОК
    print("selftest ok")


def save_impact(cur: psycopg2.extensions.cursor, ra_id: int, impact: dict) -> None:
    cur.execute(
        """
        UPDATE risk_assessments
        SET json_data = jsonb_set(json_data::jsonb, '{citizen_impact}', %s::jsonb)::text
        WHERE id = %s
        """,
        (json.dumps(impact, ensure_ascii=False), ra_id),
    )


def backfill(limit: int) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    # 1. Процедурні та закони без тексту — фіксована відповідь без LLM
    cur.execute(
        """
        UPDATE risk_assessments ra
        SET json_data = jsonb_set(json_data::jsonb, '{citizen_impact}', %s::jsonb)::text
        FROM bills b
        WHERE b.id = ra.bill_id
          AND (ra.json_data::jsonb -> 'citizen_impact') IS NULL
          AND (
            (ra.json_data::jsonb ->> 'is_procedural') = 'true'
            OR COALESCE(length(b.plain_text), 0) < 1200
          )
        """,
        (json.dumps(NO_TEXT_IMPACT, ensure_ascii=False),),
    )
    print(f"Процедурних/без тексту позначено: {cur.rowcount}")
    conn.commit()

    # 2. Непроцедурні з текстом — LLM, спочатку найризиковіші
    cur.execute(
        """
        SELECT ra.id, b.bill_number, b.title, b.plain_text
        FROM risk_assessments ra
        JOIN bills b ON b.id = ra.bill_id
        WHERE (ra.json_data::jsonb -> 'citizen_impact') IS NULL
          AND (ra.json_data::jsonb ->> 'is_procedural') = 'false'
          AND length(COALESCE(b.plain_text, '')) >= 1200
        ORDER BY b.risk_score DESC NULLS LAST, ra.id DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall()
    print(f"До генерації: {len(rows)}")

    done = failed = 0
    for i, (ra_id, number, title, text) in enumerate(rows, 1):
        text = text[:50000]
        impact = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = llm_completion_raw(
                    USER_PROMPT.format(number=number, title=title, text=text),
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=2500,
                )
            except Exception as e:
                print(f"  [{i}/{len(rows)}] №{number} LLM error: {str(e)[:100]}")
                continue
            candidate = parse_impact(raw)
            if candidate is None:
                continue
            if not impact_is_ukrainian(candidate):
                continue
            impact = candidate
            break
        if impact is None:
            failed += 1
            print(f"  [{i}/{len(rows)}] №{number} FAIL ({MAX_ATTEMPTS} спроб)")
            continue
        save_impact(cur, ra_id, impact)
        conn.commit()
        done += 1
        if done % 10 == 0:
            print(f"  [{i}/{len(rows)}] готово {done} (failed {failed})")

    print(f"Готово: {done}, невдало: {failed}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        _selftest()
        return
    backfill(args.limit)


if __name__ == "__main__":
    main()
