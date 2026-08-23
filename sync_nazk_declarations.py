#!/usr/bin/env python3
"""sync_nazk_declarations.py — декларації НАЗК депутатів: компанії (корпоративні права).

Джерела (без auth):
  GET public.nazk.gov.ua/documents/list?q={прізвище}   — серверний HTML-список
     (у кожному рядку: ПІБ, посада, дата подання, uuid)
  GET public-api.nazk.gov.ua/v2/documents/{uuid}       — повна декларація JSON
     (data.step_8 = корпоративні права: name, ЄДРПОУ, legalForm, частка)

Матчинг депутата: прізвище + перший літера імені + посада «народний депутат України».
Беремо найсвіжішу за «Дата та час подання». КВЕД у декларації немає — галузь
потім визначаємо окремо (відкритий реєстр ЄДР або класифікація назви).
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
LIST_URL = "https://public.nazk.gov.ua/documents/list?q="
API_URL = "https://public-api.nazk.gov.ua/v2/documents/"
DELAY = 0.5

ARTICLE_RE = re.compile(
    r'<a href="/documents/([0-9a-f-]{36})">([^<]+)</a>.*?'
    r'(?:Дата та час подання:</span>([^<]*)<)?.*?(?:<div class="type-info">([^<]+)</div>)?.*?'
    r'Посада:</span>([^<]*)</div>',
    re.S,
)


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_list(html_text: str) -> list[dict]:
    """Рядки списку: uuid, ПІБ, дата подання, посада. Поблочно між <article>."""
    out = []
    for block in html_text.split("<article")[1:]:
        muuid = re.search(r'href="/documents/([0-9a-f-]{36})"', block)
        if not muuid:
            continue
        mfio = re.search(r'<div class="fio"><a href="/documents/[0-9a-f-]{36}">([^<]+)</a>', block)
        mdate = re.search(r"Дата та час подання:</span>([^<]*)", block)
        mtype = re.search(r'type-info">([^<]*)<', block)
        mpost = re.search(r"Посада:</span>([^<]*)", block)
        out.append({
            "uuid": muuid.group(1),
            "fio": unescape(mfio.group(1)).strip() if mfio else "",
            "submitted": unescape(mdate.group(1)).strip() if mdate else "",
            "doc_type": unescape(mtype.group(1)).strip() if mtype else "",
            "post": unescape(mpost.group(1)).strip() if mpost else "",
        })
    return out


def match_deputy(fio: str, surname: str, first_initial: str | None) -> bool:
    parts = fio.upper().split()
    if not parts or parts[0] != surname.upper():
        return False
    if first_initial and parts[1:2] and not parts[1].startswith(first_initial.upper()):
        return False
    return True


def pick_newest(rows: list[dict]) -> dict | None:
    def key(r):
        try:
            return datetime.strptime(r["submitted"], "%d.%m.%Y %H:%M")
        except Exception:
            return datetime.min
    return max((r for r in rows), key=key, default=None)


def fetch_companies(uuid: str) -> tuple[list[dict], int | None]:
    raw = json.loads(http_get(API_URL + uuid).decode("utf-8"))
    data = raw.get("data") or {}
    step1 = ((data.get("step_1") or {}).get("data")) or {}
    year = raw.get("declaration_year")
    companies = []
    for e in (data.get("step_8") or {}).get("data") or []:
        name = (e.get("name") or "").strip()
        code = str(e.get("corporate_rights_company_code") or "").strip()
        if not name and not code:
            continue
        companies.append({
            "name": name,
            "edrpou": code,
            "legalForm": (e.get("legalForm") or "").strip(),
            "share_pct": e.get("cost_percent"),
            "cost": e.get("cost"),
            "owningDate": e.get("owningDate"),
        })
    return companies, year, step1.get("workPost", "")


def run(limit: int | None = None, delay: float = DELAY) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM mps WHERE end_date IS NULL ORDER BY id")
    deputies = cur.fetchall()

    found = not_found = errors = 0
    total_companies = 0
    targets = deputies if not limit else deputies[:limit]
    for i, (mp_id, name) in enumerate(targets, 1):
        parts = name.split()
        surname = parts[0]
        first_initial = parts[1][0] if len(parts) > 1 else None
        try:
            html_text = http_get(LIST_URL + urllib.parse.quote(surname)).decode("utf-8", errors="replace")
            rows = [r for r in parse_list(html_text)
                    if "народний депутат" in r["post"].lower()
                    and match_deputy(r["fio"], surname, first_initial)]
            best = pick_newest(rows)
            if not best:
                not_found += 1
                continue
            companies, year, work_post = fetch_companies(best["uuid"])
            cur.execute("""
                INSERT INTO deputy_declarations (mp_id, uuid, submitted_at, declaration_year, companies, synced_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (mp_id) DO UPDATE SET
                    uuid = EXCLUDED.uuid, submitted_at = EXCLUDED.submitted_at,
                    declaration_year = EXCLUDED.declaration_year, companies = EXCLUDED.companies,
                    synced_at = now()
            """, (mp_id, best["uuid"], best["submitted"], year,
                  json.dumps(companies, ensure_ascii=False)))
            found += 1
            total_companies += len(companies)
        except Exception as e:
            errors += 1
            print(f"  FAIL {name}: {str(e)[:100]}")
        if i % 20 == 0:
            conn.commit()
            print(f"  прогрес {i}/{len(targets)} (знайдено {found}, нема {not_found}, помилок {errors})")
        time.sleep(delay)

    conn.commit()
    meta = {"synced_at": datetime.now(timezone.utc).isoformat(), "found": found,
            "not_found": not_found, "errors": errors, "companies": total_companies}
    cur.execute("""
        INSERT INTO stats_cache (key, value, updated_at)
        VALUES ('nazk_declarations_meta', %s, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, (json.dumps(meta, ensure_ascii=False),))
    conn.commit()
    cur.close()
    conn.close()
    print(f"Готово: декларацій {found}, без декларації {not_found}, помилок {errors}, компаній {total_companies}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=DELAY)
    args = parser.parse_args()
    run(args.limit, args.delay)
