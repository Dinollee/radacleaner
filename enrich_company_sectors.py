#!/usr/bin/env python3
"""enrich_company_sectors.py — авто-визначення галузі компаній з декларацій НАЗК.

КВЕДа в декларації немає, а повний ЄДР з data.gov.ua — багатогігабайтні архіви.
Для 282 компаній галузь класифікує LLM ЗА НАЗВОЮ у словник INTEREST_SECTORS.
Чесність: поле marker "sector_source": "auto_name" — на дашборді це позначено.
Запускається після sync_nazk_declarations.py (ланцюг у nazk-declarations.service).
"""
import json
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src.llm_client import llm_completion_raw
from src.prompts import INTEREST_SECTORS

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
BATCH = 12

CLASSIFY_PROMPT = """Нижче — назви юридичних осіб із декларацій депутатів.
Для КОЖНОЇ визнач ОДНУ галузь економіки, до якої вона найімовірніше належить,
ЛИШЕ зі списку: {sectors}.

Якщо галузь неочевидна або назва нічого не каже (наприклад, «ФОНД», назва без
галузевого слова) — поверни "".

Відповідь — ТІЛЬКИ JSON:
{{"items": [{{"name": "...", "sector": "..."}}, ...]}}
Усі {n} назв мають бути у відповіді.

Назви:
{names}"""


def parse_items(raw: str) -> dict[str, str]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}
    allowed = set(INTEREST_SECTORS)
    out = {}
    for item in data.get("items", []):
        if isinstance(item, dict) and item.get("sector") in allowed and item.get("name"):
            out[item["name"].strip().upper()] = item["sector"]
    return out


def run() -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT c->>'name'
        FROM deputy_declarations d, jsonb_array_elements(d.companies) c
        WHERE coalesce(c->>'sector', '') = '' AND coalesce(c->>'name','') <> ''
    """)
    names = [r[0] for r in cur.fetchall()]
    print(f"Компаній без галузі: {len(names)}")

    classified: dict[str, str] = {}
    for i in range(0, len(names), BATCH):
        batch = names[i:i + BATCH]
        try:
            raw = llm_completion_raw(
                CLASSIFY_PROMPT.format(sectors=", ".join(INTEREST_SECTORS), n=len(batch),
                                       names="\n".join(f"{j+1}. {n}" for j, n in enumerate(batch))),
                max_tokens=800,
            )
            classified.update(parse_items(raw))
        except Exception as e:
            print(f"  batch FAIL ({len(batch)}): {str(e)[:100]}")
        print(f"  прогрес {min(i + BATCH, len(names))}/{len(names)}")

    updated = 0
    cur.execute("""
        SELECT mp_id, companies FROM deputy_declarations
        WHERE companies::text LIKE '%"name"%'
    """)
    rows = cur.fetchall()
    for mp_id, companies in rows:
        changed = False
        for c in companies:
            if not c.get("sector") and c.get("name"):
                sector = classified.get(c["name"].strip().upper())
                if sector:
                    c["sector"] = sector
                    c["sector_source"] = "auto_name"
                    changed = True
        if changed:
            cur.execute("UPDATE deputy_declarations SET companies = %s::jsonb WHERE mp_id = %s",
                        (json.dumps(companies, ensure_ascii=False), mp_id))
            updated += 1
    conn.commit()

    cur.execute("""
        SELECT count(*) FROM deputy_declarations d, jsonb_array_elements(d.companies) c
        WHERE coalesce(c->>'sector','') <> ''
    """)
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"Готово: класифіковано {len(classified)} назв, оновлено {updated} депутатів, "
          f"компаній з галуззю в базі: {total}")


if __name__ == "__main__":
    run()
