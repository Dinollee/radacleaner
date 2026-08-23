#!/usr/bin/env python3
"""sync_lobbying_registry.py — синхронізація Реєстру прозорості НАЗК (закон №3606-20).

Джерело (відкрите API без auth): transparency.nazk.gov.ua
  GET /api/v1/public/allsubjects          — усі суб'єкти лобіювання (205 на 08.2026)
  GET /api/v1/public/report?id={guid}     — звіт суб'єкта: objects[] з предметом
                                            лобіювання, відомством, представником,
                                            датами контактів
Номери законопроєктів витягуються з subjectOfLobbying і валідуються
EXISTS bills.bill_number (постанови КМУ тощо не матчує).
Запускається щодня (таймер lobbying-registry).
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
BASE = "https://transparency.nazk.gov.ua/api/v1/public"
DELAY = 0.4  # сек між запитами — ввічливість до держAPI

BILL_RE = re.compile(r"№\s*(\d{3,6})(?:-[ІIXV]+)?(?![\d])")


def api(path: str) -> dict | list:
    req = urllib.request.Request(f"{BASE}{path}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def unwrap(data):
    """API частково повертає подвійне JSON-кодування ('{\"report\":...}' як стрінг)."""
    while isinstance(data, str) and data.strip():
        data = json.loads(data)
    return data


def extract_bill_number(text: str, valid_numbers: set[str]) -> str | None:
    """№NNNN з тексту предмета лобіювання → валідований bill_number (4-11 цифр у bills)."""
    for m in BILL_RE.finditer(text):
        num = m.group(1)
        if num in valid_numbers:
            return num
    return None


def sync() -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT bill_number FROM bills")
    valid_numbers = {r[0] for r in cur.fetchall()}

    subjects = api("/allsubjects")
    print(f"Суб'єктів лобіювання в реєстрі: {len(subjects)}")

    cur.execute("DELETE FROM lobbying_objects")
    synced = linked = 0
    for i, s in enumerate(subjects, 1):
        guid, report_guid = s["guid"], s.get("reportGuid")
        cur.execute("""
            INSERT INTO lobbying_subjects (guid, name, edrpou, subject_type, is_active,
                                           status, period_year, period_half_year, report_guid, synced_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (guid) DO UPDATE SET
                name = EXCLUDED.name, edrpou = EXCLUDED.edrpou,
                subject_type = EXCLUDED.subject_type, is_active = EXCLUDED.is_active,
                status = EXCLUDED.status, period_year = EXCLUDED.period_year,
                period_half_year = EXCLUDED.period_half_year, report_guid = EXCLUDED.report_guid,
                synced_at = now()
        """, (guid, s.get("name"), s.get("edrpou"), s.get("subjectType"), s.get("isActive", False),
              s.get("status"), s.get("periodYear"), s.get("periodHalfYear"), report_guid))

        if not report_guid:
            continue
        try:
            report = unwrap(api(f"/report?id={report_guid}"))
        except Exception as e:
            print(f"  report FAIL {s.get('name', '')[:40]}: {str(e)[:80]}")
            report = None
        time.sleep(DELAY)

        if not isinstance(report, dict) or not report.get("report"):
            continue
        rep = unwrap(report["report"]) if isinstance(report["report"], str) else report["report"]
        if not isinstance(rep, dict):
            continue

        spheres = sorted({ls["lobbySphere"]["name"]
                          for o in rep.get("objects", [])
                          for ls in o.get("lobbySphereSubjects", [])
                          if ls.get("lobbySphere", {}).get("name")})
        funding = (rep.get("fundingSource") or "").strip()
        if funding:
            cur.execute("UPDATE lobbying_subjects SET funding_source = %s, spheres = %s WHERE guid = %s",
                        (funding, json.dumps(spheres, ensure_ascii=False), guid))
        else:
            cur.execute("UPDATE lobbying_subjects SET spheres = %s WHERE guid = %s",
                        (json.dumps(spheres, ensure_ascii=False), guid))

        for o in rep.get("objects", []):
            agency = (o.get("governmentAgency") or "").strip()
            rep_name = (o.get("governmentAgencyRepresentativeFullName") or {}).get("fullName", "")
            interactions = o.get("interactions", []) or []
            dates = sorted(str(ix.get("interactionDate", ""))[:10] for ix in interactions if ix.get("interactionDate"))
            for ls in o.get("lobbySphereSubjects", []):
                text = (ls.get("subjectOfLobbying") or "").strip()
                if not text:
                    continue
                bill = extract_bill_number(text, valid_numbers)
                cur.execute("""
                    INSERT INTO lobbying_objects (subject_guid, sphere, subject_of_lobbying,
                        government_agency, agency_representative, interactions_count,
                        last_interaction, bill_number, report_guid)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (subject_guid, subject_of_lobbying, government_agency) DO UPDATE SET
                        sphere = EXCLUDED.sphere, interactions_count = EXCLUDED.interactions_count,
                        last_interaction = EXCLUDED.last_interaction, bill_number = EXCLUDED.bill_number
                """, (guid, (ls.get("lobbySphere") or {}).get("name"), text, agency,
                      rep_name, len(interactions), dates[-1] if dates else None, bill, report_guid))
                if bill:
                    linked += 1
        synced += 1
        if i % 25 == 0:
            conn.commit()
            print(f"  прогрес {i}/{len(subjects)} (звітів: {synced}, зв'язок із bills: {linked})")

    conn.commit()

    cur.execute("SELECT count(*) FROM lobbying_subjects WHERE is_active")
    active = cur.fetchone()[0]
    cur.execute("SELECT count(*), count(bill_number) FROM lobbying_objects")
    total_objs, total_linked = cur.fetchone()
    cur.execute("""
        INSERT INTO stats_cache (key, value, updated_at)
        VALUES ('lobbying_registry_meta', %s, now() AT TIME ZONE 'utc')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
    """, (json.dumps({
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "subjects": len(subjects), "active": active,
        "objects": total_objs, "bill_linked": total_linked,
    }, ensure_ascii=False),))
    conn.commit()

    cur.close()
    conn.close()
    print(f"Готово: суб'єктів {len(subjects)} (активних {active}), об'єктів лобіювання {total_objs}, "
          f"з посиланням на законопроєкт ВРУ: {total_linked}")


if __name__ == "__main__":
    sync()
