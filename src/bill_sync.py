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
                  VALUES (?, ?, datetime('now'), datetime('now'))
                  ON CONFLICT(filename) DO UPDATE SET
                    etag=?, last_checked=datetime('now'), last_downloaded=datetime('now')""",
        "params": [filename, etag, etag],
    })


def update_last_checked(filename: str) -> None:
    """Оновлює мітку часу перевірки."""
    d1_exec("raw_sql", {
        "sql": "UPDATE sync_state SET last_checked=datetime('now') WHERE filename=?",
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
            return False, None
        else:
            log.error("[ERROR] HTTP %d: %s", e.code, e.read().decode()[:200])
            return False, None


def log_change(bill_id: int, change_type: str, old_value=None, new_value=None) -> None:
    """Записує зміну в change_log (D1), якщо такої зміни ще немає."""
    existing = d1_query(
        "SELECT 1 FROM change_log WHERE bill_id=? AND change_type=? AND old_value IS ? AND new_value IS ? LIMIT 1",
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
            f"https://itd.rada.gov.ua/billInfo/Bills/Card/{api_id}"
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

    db_rows = d1_query("SELECT id, bill_number, current_status, agenda_category, committee FROM bills")
    db_bills = {row["bill_number"]: row for row in db_rows}

    docs_rows = d1_query("SELECT DISTINCT bill_id FROM bill_documents")
    bills_with_docs = {r["bill_id"] for r in docs_rows}

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

        new_subject = b.get("subject", "").strip()
        if new_subject and new_subject != row.get("committee"):
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET committee=? WHERE bill_number=?",
                "params": [new_subject, bn],
            })

        if db_id in bills_with_docs:
            continue

        docs = b.get("documents", {})
        if docs:
            for kind in ["source", "workflow"]:
                for d in docs.get(kind, []) or []:
                    for f in d.get("docFiles", []) or []:
                        if not f.get("id"):
                            continue
                        doc_updates.append((db_id, str(f["id"]), f.get("type") or d.get("kind", "?"), bn))

    for bn, new_status, new_act, new_act_date, db_id, old_status in status_updates:
        d1_exec("bill", {
            "bill_number": bn, "current_status": new_status,
            "act_number": new_act, "act_date": new_act_date,
        })
        log_change(db_id, "status_change", old_status, new_status)
        queue_for_analysis(db_id, bn, "status_change")

    doc_count = 0
    new_doc_bills = {}  # {bill_id: bill_number} — закони які вже мали документи
    for db_id, file_id, dtype, bn in doc_updates[:BATCH_DOCS]:
        d1_exec("raw_sql", {
            "sql": "INSERT OR IGNORE INTO bill_documents (bill_id, file_id, doc_type) VALUES (?, ?, ?)",
            "params": [db_id, file_id, dtype],
        })
        doc_count += 1
        if db_id in bills_with_docs:
            # Закон вже мав документи — це НОВІ документи (зміна)
            new_doc_bills[db_id] = bn
        bills_with_docs.add(db_id)

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
        if downloaded and data:
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
