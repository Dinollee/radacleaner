#!/usr/bin/env python3
"""pilot_citizen_impact.py — пілот: «що зміниться для громадянина» (до/після).

Один виклик LLM на закон, повний plain_text (до 50K символів — контекст nemotron
1M дозволяє). Результат у data/citizen_impact_pilot.json для перегляду формулювань.
Пілот для оцінки якості промпта; продакшн-версія (поле в FINAL_PROMPT + бекфілл)
піде після схвалення формалянок.
"""
import json
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.llm_client import llm_completion_raw

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"

# id з bills: нікотин/кальян, соцдопомога, нацсвятині, Податковий кодекс, освіта
PILOT_BILLS = [14191, 14262, 19033, 19010, 11186]

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
    texts = [impact.get("headline", ""), impact.get("no_impact_reason") or ""]
    for ch in impact.get("changes", []):
        texts += [ch.get("before", ""), ch.get("after", ""), ch.get("who", "")]
    return all(_is_ukrainian(t) for t in texts)


def _selftest() -> None:
    ok = '{"changes": [{"before": "a", "after": "b", "who": "c"}], "affects_citizens": true}'
    wrapped = f"Ось відповідь:\n```json\n{ok}\n```"
    assert parse_impact(wrapped) == {"changes": [{"before": "a", "after": "b", "who": "c"}, ]} | {"affects_citizens": True}
    assert parse_impact("no json here") is None
    assert parse_impact('{"nope": []}') is None
    assert _is_ukrainian("Закон вводить новий статус та штрафи за знищення.")
    assert not _is_ukrainian("Закон wprowadжує новий статус.")  # змішане слово
    assert _is_ukrainian("Штраф від 50 000 до 100 000 грн (до 30% площі).")  # цифри/латиница ОК
    assert impact_is_ukrainian({"headline": "Закон про допомогу", "changes": [{"before": "був", "after": "стане", "who": "усі"}]})
    assert not impact_is_ukrainian({"headline": "totally english headline here", "changes": []})
    print("selftest ok")


MAX_ATTEMPTS = 3


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    results = []
    for bill_id in PILOT_BILLS:
        cur.execute(
            "SELECT bill_number, title, plain_text FROM bills WHERE id = %s",
            (bill_id,),
        )
        row = cur.fetchone()
        if not row or not row[2]:
            print(f"[SKIP] id={bill_id} — немає тексту")
            continue
        number, title, text = row
        print(f"→ №{number} ({len(text)} символів): {title[:60]}...")
        impact = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw = llm_completion_raw(
                USER_PROMPT.format(number=number, title=title, text=text),
                system_prompt=SYSTEM_PROMPT,
                max_tokens=2500,
            )
            candidate = parse_impact(raw)
            if candidate is None:
                print(f"  [retry {attempt}] невалідний JSON")
                continue
            if not impact_is_ukrainian(candidate):
                print(f"  [retry {attempt}] нечиста українська мова")
                continue
            impact = candidate
            break
        if impact is None:
            print(f"  [FAIL] не вдалося за {MAX_ATTEMPTS} спроб")
            continue
        impact["bill_id"] = bill_id
        impact["bill_number"] = number
        results.append(impact)

    out = ROOT / "data" / "citizen_impact_pilot.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for r in results:
        print(f"\n{'=' * 70}\n№{r['bill_number']} — {r.get('headline', '')}")
        if not r.get("affects_citizens"):
            print(f"  Прямого ефекту немає: {r.get('no_impact_reason')}")
            continue
        for i, ch in enumerate(r["changes"], 1):
            print(f"\n  {i}. Хто: {ch.get('who', '—')}")
            print(f"     БУЛО:  {ch.get('before', '')}")
            print(f"     СТАНЕ: {ch.get('after', '')}")
    print(f"\nЗбережено: {out} ({len(results)}/{len(PILOT_BILLS)})")


if __name__ == "__main__":
    main()
