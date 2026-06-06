"""Синхронізація бази законопроектів ВРУ з data.rada.gov.ua."""
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

from .config import log, DB_PARAMS
from .risk_storage import db_conn

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
    """Створює sync_state таблицю, якщо її немає."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_state (
                filename TEXT PRIMARY KEY, etag TEXT,
                last_checked TIMESTAMP, last_downloaded TIMESTAMP
            )
            """
        )
        conn.commit()


def get_etag(filename: str) -> str | None:
    """Отримує збережений ETag з БД."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT etag FROM sync_state WHERE filename=%s", (filename,)
        )
        row = cur.fetchone()
        return row[0] if row else None


def save_etag(filename: str, etag: str) -> None:
    """Зберігає ETag в БД."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_state (filename, etag, last_checked, last_downloaded)
            VALUES (%s, %s, now(), now())
            ON CONFLICT (filename) DO UPDATE SET
                etag=%s, last_checked=now(), last_downloaded=now()
            """,
            (filename, etag, etag),
        )
        conn.commit()


def update_last_checked(filename: str) -> None:
    """Оновлює мітку часу перевірки."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sync_state SET last_checked=now() WHERE filename=%s",
            (filename,),
        )
        conn.commit()


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


def log_change(cur, bill_id: int, change_type: str, old_value=None, new_value=None) -> None:
    """Записує зміну в change_log."""
    cur.execute(
        "INSERT INTO change_log (bill_id, change_type, old_value, new_value) "
        "VALUES (%s, %s, %s, %s)",
        (bill_id, change_type, old_value, new_value),
    )


def sync_billinfo_list(data: bytes) -> int:
    """Синхронізує список законопроектів (billinfo_list)."""
    bills = json.loads(data)

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT bill_number FROM bills")
        existing = {row[0] for row in cur.fetchall()}
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

            cur.execute(
                """
                INSERT INTO bills (bill_number, title, registration_date, current_status, committee, url, agenda_category, stage)
                VALUES (%s, %s, %s, 'new', %s, %s, 'other', 1)
                ON CONFLICT (bill_number) DO NOTHING
                RETURNING id
                """,
                (bn, title, reg_date, subject, url),
            )
            row = cur.fetchone()
            if row:
                log_change(cur, row[0], "new", None, "new")
                added += 1

        conn.commit()
    return added


def process_full_data(data: bytes) -> int:
    """Обробляє повні дані про законопроекти (billinfo_full)."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    data = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", data)
    bills = json.loads(data, strict=False)

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, bill_number, current_status FROM bills")
        db_bills = {row[1]: (row[0], row[2]) for row in cur.fetchall()}

        updated = 0
        doc_count = 0

        for b in bills:
            bn = str(b.get("registrationNumber", "")).strip()
            if bn not in db_bills:
                continue

            new_status = b.get("currentPhase_title", "").strip()
            new_rubric = b.get("rubric", "").strip()
            new_subject = b.get("subject", "").strip()
            new_url = b.get("url", "").strip()
            db_id, old_status = db_bills[bn]

            if new_status and new_status != old_status:
                cur.execute(
                    "UPDATE bills SET current_status=%s, updated_at=now() WHERE id=%s",
                    (new_status, db_id),
                )
                log_change(cur, db_id, "status_change", old_status, new_status)
                updated += 1

            if new_rubric:
                cur.execute(
                    "UPDATE bills SET agenda_category=%s WHERE id=%s AND agenda_category='other'",
                    (new_rubric, db_id),
                )
            if new_subject:
                cur.execute(
                    "UPDATE bills SET committee=%s WHERE id=%s", (new_subject, db_id)
                )

            # Document references
            docs = b.get("documents", {})
            if docs:
                for kind in ["source", "workflow"]:
                    for d in docs.get(kind, []) or []:
                        dtype = d.get("kind", "?")
                        for f in d.get("docFiles", []) or []:
                            file_id = f["id"]
                            fname = f.get("name", "")
                            cur.execute(
                                """
                                INSERT INTO bill_documents (bill_id, file_id, doc_kind, doc_type, doc_name)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (bill_id, file_id) DO NOTHING
                                """,
                                (db_id, str(file_id), kind, dtype, fname),
                            )
                            if cur.rowcount > 0:
                                doc_count += 1

        conn.commit()
    log.info("Documents indexed: %d", doc_count)
    return updated


def recalc_stages() -> None:
    """Перераховує stage для всіх законів на основі current_status."""
    with db_conn() as conn, conn.cursor() as cur:
        for status, stage in STAGE_CASES:
            cur.execute(
                "UPDATE bills SET stage=%s WHERE current_status=%s",
                (stage, status),
            )
        conn.commit()


def main() -> None:
    """Головна функція: синхронізація законопроектів з RADA API."""
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
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM bills")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT stage, COUNT(*) FROM bills WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage"
        )
        stages = cur.fetchall()

    log.info("=== Summary: %d bills total ===", total)
    stage_names = {
        0: "У процесі",
        1: "Зареєстровано",
        2: "Перше читання",
        3: "Друге читання",
        4: "Підписано",
        5: "Відхилено",
    }
    for stage, count in stages:
        log.info("  Stage %d - %s: %d", stage, stage_names.get(stage, "?"), count)


if __name__ == "__main__":
    main()