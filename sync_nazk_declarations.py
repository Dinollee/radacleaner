#!/usr/bin/env python3
"""sync_nazk_declarations.py — декларації НАЗК депутатів: компанії (корпоративні права).

Джерела (без auth):
  GET public.nazk.gov.ua/documents/list?q={прізвище}   — серверний HTML-список
     (у кожному рядку: ПІБ, посада, дата подання, uuid)
  GET public-api.nazk.gov.ua/v2/documents/{uuid}       — повна декларація JSON
     (data.step_8 = корпоративні права: name, ЄДРПОУ, legalForm, частка)

Матчинг депутата: прізвище + ініціали імені та по батькові + посада депутата/комітету ВРУ.
Пошук звужуємо серверними фільтрами НАЗК (responsible_position=52 «народний депутат»,
лише декларації) — це згортає пагінцію для поширених прізвищ (Ткаченко = 304 сторінки)
і прибирає тезок не-депутатів; за потреби догуляємо ?page=N до MAX_PAGES.
Беремо найсвіжішу за «Дата та час подання». КВЕД у декларації немає — галузь
потім визначаємо окремо (відкритий реєстр ЄДР або класифікація назви).
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

from src.aliases import alias_surnames, resolve_name_candidates  # noqa: E402

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
LIST_URL = "https://public.nazk.gov.ua/documents/list?q="
# document_type: 1 = Декларація, 3 = Виправлена (2 = Повідомлення про суттєві зміни — без посади)
LIST_FILTERS = "&document_type%5B%5D=1&document_type%5B%5D=3&responsible_position%5B%5D=52"
MAX_PAGES = 5  # межа догулювання пагінції на випадок зміни сортування
API_URL = "https://public-api.nazk.gov.ua/v2/documents/"
DELAY = 0.5


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def list_url(surname: str, page: int = 1) -> str:
    url = LIST_URL + urllib.parse.quote(surname) + LIST_FILTERS
    return f"{url}&page={page}" if page > 1 else url


def max_page(html_text: str) -> int:
    pages = [int(p) for p in re.findall(r'data-page="(\d+)"', html_text)]
    return max(pages) if pages else 1


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


def match_deputy(fio: str, surname: str, first_initial: str | None,
                 patronymic_initial: str | None = None) -> bool:
    parts = fio.upper().split()
    if not parts or parts[0] != surname.upper():
        return False
    if first_initial and parts[1:2] and not parts[1].startswith(first_initial.upper()):
        return False
    # по батькові відсіває тезок із збігом прізвище+ініціал (Грищенко Т.М. vs Т.В.)
    if patronymic_initial and parts[2:3] and not parts[2].startswith(patronymic_initial.upper()):
        return False
    return True


def is_deputy_post(post: str) -> bool:
    """Посада формату 2019+: «Член/Голова Комітету Верховної Ради…», «депутатка» тощо."""
    p = post.lower()
    return "депутат" in p or "комітет" in p or "верховної рад" in p


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


def run(limit: int | None = None, delay: float = DELAY,
        only_unmatched: bool = False) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    if only_unmatched:
        cur.execute("""
            SELECT id, name FROM mps
            WHERE end_date IS NULL AND NOT EXISTS (
                SELECT 1 FROM deputy_declarations dd WHERE dd.mp_id = mps.id)
            ORDER BY id""")
    else:
        cur.execute("SELECT id, name FROM mps WHERE end_date IS NULL ORDER BY id")
    deputies = cur.fetchall()

    found = not_found = errors = 0
    total_companies = 0
    targets = deputies if not limit else deputies[:limit]
    for i, (mp_id, name) in enumerate(targets, 1):
        parts = name.split()
        surname = parts[0]
        first_initial = parts[1][0] if len(parts) > 1 else None
        patronymic_initial = parts[1][2] if len(parts) > 1 and len(parts[1]) > 3 else None
        # декларація могла бути подана до зміни прізвища — пробуємо й старі форми
        surnames = [surname] + [s for s in alias_surnames(resolve_name_candidates(cur, name))
                                if s != surname]
        try:
            best = None
            for sn in surnames:
                html_text = http_get(list_url(sn)).decode("utf-8", errors="replace")
                rows = parse_list(html_text)
                for page in range(2, min(max_page(html_text), MAX_PAGES) + 1):
                    html_text = http_get(list_url(sn, page)).decode("utf-8", errors="replace")
                    rows += parse_list(html_text)
                    time.sleep(delay)
                rows = [r for r in rows
                        if is_deputy_post(r["post"])
                        and match_deputy(r["fio"], sn, first_initial, patronymic_initial)]
                best = pick_newest(rows)
                time.sleep(delay)
                if best:
                    break
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
    parser.add_argument("--only-unmatched", action="store_true",
                        help="лише депутати без запису в deputy_declarations")
    args = parser.parse_args()
    run(args.limit, args.delay, args.only_unmatched)
