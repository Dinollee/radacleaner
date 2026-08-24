"""Синхронізація бази законопроектів ВРУ з data.rada.gov.ua → D1 (через Worker API)."""
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

from .config import log
from .d1_client import d1_query, d1_exec, refresh_stats_cache

FILES = {
    "billinfo_list": {
        "url": "https://data.rada.gov.ua/ogd/zpr/skl9/billinfo_list-skl9.json",
        "local": "/tmp/billinfo_list-skl9.json",
    },
    "billinfo_full": {
        "url": "https://data.rada.gov.ua/ogd/zpr/skl9/billinfo-skl9.json",
        "local": "/tmp/billinfo-skl9.json",
    },
}

STAGE_CASES = [
    ("Відхилено та знято з розгляду", 5),
    ("Знято з розгляду", 5),
    ("Проект відкликано", 5),
    ("Заслухано та знято з розгляду", 5),
    ("Проект не прийнято", 5),
    ("В порядок денний не включено", 5),
    ("Розгляд відкладено", 5),
    ("Закон підписано", 4),
    ("Постанову підписано", 4),
    ("Повернуто з підписом від Президента України", 4),
    ("Закон прийнято", 4),
    ("Передано в тираж", 4),
    ("Передано на підпис Президенту", 3),
    ("Готується на підпис", 3),
    ("Очікує на розгляд з вето Президента", 3),
    ("Готується на розгляд з вето Президента", 3),
    ("Повернуто з підписом Голови Верховної Ради України", 3),
    ("Передано на підпис Президенту (10)", 3),
    ("Готується на підпис (після вето)", 3),
    ("Одержано проєкт", 1),
    ("Опрацьовується в комітеті", 2),
    ("Очікує розгляду", 2),
    ("Готується на друге читання", 3),
    ("Готується на перше читання", 2),
    ("Передано на підпис Президенту (20)", 3),
    ("Готується на друге читання", 2),
    ("Очікує на друге читання", 2),
    ("Готується на повторне перше читання", 2),
    ("Прийнято в першому читанні", 2),
    ("new", 1),
]


def ensure_sync_table() -> None:
    """Створює sync_state таблицю, якщо її немає (D1 через raw_sql)."""
    d1_exec("raw_sql", {
        "sql": """CREATE TABLE IF NOT EXISTS sync_state (
            filename TEXT PRIMARY KEY,
            etag TEXT,
            last_checked TEXT,
            last_downloaded TEXT
        )""",
        "params": [],
    })


def get_etag(filename: str) -> str | None:
    """Отримує збережений ETag з D1."""
    rows = d1_query(
        "SELECT etag FROM sync_state WHERE filename = ?", [filename]
    )
    return rows[0]["etag"] if rows else None


def save_etag(filename: str, etag: str) -> None:
    """Зберігає ETag в D1."""
    d1_exec("raw_sql", {
        "sql": """INSERT INTO sync_state (filename, etag, last_checked, last_downloaded)
                  VALUES (?, ?, (now() AT TIME ZONE 'utc'), (now() AT TIME ZONE 'utc'))
                  ON CONFLICT(filename) DO UPDATE SET
                    etag=?, last_checked=(now() AT TIME ZONE 'utc'), last_downloaded=(now() AT TIME ZONE 'utc')""",
        "params": [filename, etag, etag],
    })


def update_last_checked(filename: str) -> None:
    """Оновлює мітку часу перевірки."""
    d1_exec("raw_sql", {
        "sql": "UPDATE sync_state SET last_checked=(now() AT TIME ZONE 'utc') WHERE filename=?",
        "params": [filename],
    })


def check_and_download(url: str, local_path: str, filename: str):
    """Перевіряє ETag і завантажує файл, якщо змінився.

    Returns:
        (True, data) якщо завантажено, (False, None) якщо не змінився.
    """
    old_etag = get_etag(filename)
    req = urllib.request.Request(url, method="HEAD")
    if old_etag:
        req.add_header("If-None-Match", old_etag)

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        new_etag = resp.headers.get("ETag", "")
        content_length = resp.headers.get("ContentLength", "?")
        log.info(
            "[200] File changed. Size: %s bytes. ETag: %s", content_length, new_etag
        )

        resp2 = urllib.request.urlopen(url, timeout=120)
        data = resp2.read()
        with open(local_path, "wb") as f:
            f.write(data)
        save_etag(filename, new_etag)
        return True, data

    except urllib.error.HTTPError as e:
        if e.code == 304:
            log.info("[304] Not modified. ETag: %s", old_etag)
            update_last_checked(filename)
            # Читаємо з кешу ( для оновлення author_sponsors)
            try:
                with open(local_path, "rb") as f:
                    return False, f.read()
            except FileNotFoundError:
                return False, None
        else:
            log.error("[ERROR] HTTP %d: %s", e.code, e.read().decode()[:200])
            return False, None


def log_change(bill_id: int, change_type: str, old_value=None, new_value=None) -> None:
    """Записує зміну в change_log (D1), якщо такої зміни ще немає."""
    existing = d1_query(
        "SELECT 1 FROM change_log WHERE bill_id=? AND change_type=? AND old_value IS NOT DISTINCT FROM ? AND new_value IS NOT DISTINCT FROM ? LIMIT 1",
        [bill_id, change_type, old_value, new_value],
    )
    if existing:
        return
    d1_exec("change_log", {
        "bill_id": bill_id,
        "change_type": change_type,
        "old_value": old_value,
        "new_value": new_value,
    })


def sync_billinfo_list(data: bytes) -> int:
    """Синхронізує список законопроектів (billinfo_list) → D1."""
    bills = json.loads(data)

    # Отримуємо існуючі bill_number з D1
    existing_rows = d1_query("SELECT bill_number FROM bills")
    existing = {row["bill_number"] for row in existing_rows}
    added = 0

    for b in bills:
        bn = str(b.get("registrationNumber", "")).strip()
        if not bn or bn in existing:
            continue

        title = b.get("name", "")
        reg_date = b.get("registrationDate", "")[:10] if b.get("registrationDate") else None
        subject = b.get("subject", "")
        api_id = str(b.get("id", ""))
        url = (
            f"https://itd.rada.gov.ua/billinfo/Bills/Card/{api_id}"
            if api_id
            else ""
        )

        d1_exec("bill", {
            "bill_number": bn,
            "title": title,
            "current_status": "new",
            "registration_date": reg_date,
            "committee": subject,
            "agenda_category": "other",
            "url": url,
            "stage": 1,
        })

        # Знаходимо D1 id для change_log
        rows = d1_query("SELECT id FROM bills WHERE bill_number = ?", [bn])
        if rows:
            log_change(rows[0]["id"], "new", None, "new")

            # Для нових законів ставимо дату реєстрації як status_changed_at
            if reg_date:
                d1_exec("raw_sql", {
                    "sql": "UPDATE bills SET status_changed_at=? WHERE bill_number=?",
                    "params": [reg_date, bn],
                })

        added += 1

    return added


def queue_for_analysis(bill_id: int, bill_number: str, reason: str) -> None:
    """Додає закон в чергу LLM-аналізу, якщо він ще не в черзі і не проаналізований."""
    existing = d1_query(
        "SELECT 1 FROM pending_analysis WHERE bill_id=? AND status IN ('pending','running','done')",
        [bill_id],
    )
    if existing:
        return
    analyzed = d1_query("SELECT 1 FROM risk_assessments WHERE bill_id=?", [bill_id])
    if analyzed:
        return
    d1_exec("raw_sql", {
        "sql": "INSERT INTO pending_analysis (bill_id, bill_number, status) VALUES (?, ?, ?)",
        "params": [bill_id, bill_number, "pending"],
    })
    log.info("Queued for LLM: %s (reason: %s)", bill_number, reason)


def process_full_data(data: bytes) -> int:
    """Обробляє повні дані про законопроекти (billinfo_full) → D1.

    В чергу LLM-аналізу потрапляють ТІЛЬКИ:
    - закони зі зміною статусу
    - закони з новими документами
    """
    BATCH_DOCS = 5000

    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    data = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", data)
    bills = json.loads(data, strict=False)

    db_rows = d1_query("SELECT id, bill_number, current_status, agenda_category, committee, stage, is_urgent, is_euro FROM bills")
    db_bills = {row["bill_number"]: row for row in db_rows}

    existing_file_ids = d1_query("SELECT bill_id, file_id FROM bill_documents")
    bills_doc_ids: dict[int, set[str]] = {}
    for r in existing_file_ids:
        bills_doc_ids.setdefault(r["bill_id"], set()).add(r["file_id"])

    status_updates = []
    doc_updates = []
    bills_with_status_change = set()

    for b in bills:
        bn = str(b.get("registrationNumber", "")).strip()
        if bn not in db_bills:
            continue
        row = db_bills[bn]
        db_id = row["id"]

        phase = b.get("currentPhase") or {}
        new_status = (phase.get("status") or "").strip()

        if new_status and new_status != row["current_status"]:
            new_act = (b.get("actNumber") or "").strip() or None
            new_act_date = b.get("actDate", "")[:10] if b.get("actDate") else None
            status_updates.append((bn, new_status, new_act, new_act_date, db_id, row["current_status"]))
            bills_with_status_change.add(db_id)

        new_rubric = b.get("rubric", "").strip()
        if new_rubric and new_rubric != row.get("agenda_category"):
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET agenda_category=? WHERE bill_number=?",
                "params": [new_rubric, bn],
            })

        # Extract isUrgent and isEuro flags
        new_urgent = bool(b.get("isUrgent", False))
        new_euro = bool(b.get("isEuro", False))
        if new_urgent != row.get("is_urgent", False):
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET is_urgent=? WHERE bill_number=?",
                "params": [new_urgent, bn],
            })
        if new_euro != row.get("is_euro", False):
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET is_euro=? WHERE bill_number=?",
                "params": [new_euro, bn],
            })

        new_subject = b.get("subject", "").strip()
        if new_subject and new_subject != row.get("committee"):
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET committee=? WHERE bill_number=?",
                "params": [new_subject, bn],
            })

        # Оновлюємо URL з API ID (якщо він є і URL хибний)
        api_id = str(b.get("id", "")).strip()
        if api_id and api_id.isdigit():
            correct_url = f"https://itd.rada.gov.ua/billinfo/Bills/Card/{api_id}"
            # Оновлюємо якщо URL пустий, хибний (не починається з http), або використовує bill_number замість API ID
            current_url = row.get("url", "") or ""
            if (not current_url.startswith("http") or
                current_url == "rada.gov.ua" or
                (current_url.endswith(f"/{bn}") and bn != api_id)):
                d1_exec("raw_sql", {
                    "sql": "UPDATE bills SET url=? WHERE bill_number=?",
                    "params": [correct_url, bn],
                })

        # Витягуємо авторів з initiators (якщо ще немає в bill_sponsors)
        existing_sponsors = d1_query(
            "SELECT 1 FROM bill_sponsors WHERE bill_id=? LIMIT 1", [db_id]
        )
        if not existing_sponsors:
            initiators = b.get("initiators", [])
            if initiators:
                for i, init in enumerate(initiators):
                    mp_data = init.get("mp") or {}
                    person = mp_data.get("person") or {}
                    rada_uid = person.get("id")
                    surname = person.get("surname", "")
                    firstname = person.get("firstname", "")
                    patronymic = person.get("patronymic", "")
                    full_name = f"{surname} {firstname[0]}.{patronymic[0]}." if firstname and patronymic else surname

                    # Знаходимо mp_id через rada_uid
                    mp_id = None
                    if rada_uid:
                        mp_rows = d1_query("SELECT id, name FROM mps WHERE rada_uid=?", [rada_uid])
                        if mp_rows:
                            mp_id = mp_rows[0]["id"]
                            # RADA віддає свіже ім'я: якщо воно відрізняється від
                            # поточного mps.name — фіксуємо пару (стара → нова) в deputy_aliases
                            if full_name != mp_rows[0]["name"]:
                                d1_exec("raw_sql", {
                                    "sql": ("INSERT INTO deputy_aliases (rada_uid, old_name, new_name) "
                                            "SELECT ?, ?, ? WHERE NOT EXISTS ("
                                            "  SELECT 1 FROM deputy_aliases WHERE rada_uid=? AND old_name=?) "
                                            "AND NOT EXISTS ("
                                            "  SELECT 1 FROM deputy_aliases WHERE rada_uid=? AND new_name=?)"),
                                    "params": [rada_uid, full_name, mp_rows[0]["name"],
                                               rada_uid, full_name, rada_uid, full_name],
                                })

                    d1_exec("raw_sql", {
                        "sql": "INSERT INTO bill_sponsors (bill_id, mp_id, mp_name, rada_uid, sponsor_order) VALUES (?, ?, ?, ?, ?)",
                        "params": [db_id, mp_id, full_name, rada_uid, i],
                    })

        stage = row.get("stage", 0) or 0
        if stage >= 4:
            continue

        docs = b.get("documents", {})
        if not docs:
            continue

        known_ids = bills_doc_ids.get(db_id, set())
        for kind in ["source", "workflow"]:
            for d in docs.get(kind, []) or []:
                for f in d.get("docFiles", []) or []:
                    fid = str(f.get("id", ""))
                    if not fid:
                        continue
                    if fid not in known_ids:
                        doc_updates.append((db_id, fid, f.get("type") or d.get("kind", "?"), bn))
                        known_ids.add(fid)

    for bn, new_status, new_act, new_act_date, db_id, old_status in status_updates:
        d1_exec("bill", {
            "bill_number": bn, "current_status": new_status,
            "act_number": new_act, "act_date": new_act_date,
        })
        log_change(db_id, "status_change", old_status, new_status)
        queue_for_analysis(db_id, bn, "status_change")

    doc_count = 0
    new_doc_bills: dict[int, str] = {}
    for db_id, file_id, dtype, bn in doc_updates[:BATCH_DOCS]:
        d1_exec("raw_sql", {
            "sql": "INSERT INTO bill_documents (bill_id, file_id, doc_type) VALUES (?, ?, ?) ON CONFLICT (bill_id, file_id) DO NOTHING",
            "params": [db_id, file_id, dtype],
        })
        doc_count += 1
        new_doc_bills[db_id] = bn

    for db_id, bn in new_doc_bills.items():
        queue_for_analysis(db_id, bn, "new_documents")

    log.info("Status changes: %d, Documents indexed: %d/%d",
             len(status_updates), doc_count, len(doc_updates))
    return len(status_updates)



def recalc_stages() -> None:
    """Перераховує stage для всіх законів на основі current_status."""
    for status, stage in STAGE_CASES:
        d1_exec("raw_sql", {
            "sql": "UPDATE bills SET stage=? WHERE current_status=?",
            "params": [stage, status],
        })


def main() -> None:
    """Головна функція: синхронізація законопроектів з RADA API → D1."""
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    ensure_sync_table()

    if mode in ("list", "all"):
        log.info("=== Sync billinfo_list (%s) ===", datetime.now())
        fi = FILES["billinfo_list"]
        downloaded, data = check_and_download(fi["url"], fi["local"], "billinfo_list")
        if downloaded and data:
            added = sync_billinfo_list(data)
            log.info("Added %d new bills", added)
            recalc_stages()

    if mode in ("full", "all"):
        log.info("=== Sync billinfo_full (%s) ===", datetime.now())
        fi = FILES["billinfo_full"]
        downloaded, data = check_and_download(fi["url"], fi["local"], "billinfo_full")
        if data:  # Process even from cache (downloaded=False means 304, data from cache)
            updated = process_full_data(data)
            log.info("Updated %d bill statuses", updated)
            recalc_stages()

    # Підсумок
    total_rows = d1_query("SELECT COUNT(*) as cnt FROM bills")
    total = total_rows[0]["cnt"] if total_rows else 0
    stage_rows = d1_query(
        "SELECT stage, COUNT(*) as cnt FROM bills WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage"
    )

    log.info("=== Summary: %d bills total ===", total)
    stage_names = {
        0: "У процесі",
        1: "Зареєстровано",
        2: "Перше читання",
        3: "Друге читання",
        4: "Підписано",
        5: "Відхилено",
    }
    for row in stage_rows:
        log.info("  Stage %d - %s: %d", row["stage"], stage_names.get(row["stage"], "?"), row["cnt"])

    # Оновлюємо кеш статистики
    refresh_stats_cache()
    log.info("Stats cache refreshed")


if __name__ == "__main__":
    main()
