"""RAG engine — моніторинг законопроектів з LLM-аналізом ризиків.

Основні команди:
  python -m src.rag_engine              — стандартний запуск (нові/змінені закони)
  python -m src.rag_engine --force      — переаналізувати всі неоповіщені
  python -m src.rag_engine --test       — без відправки Telegram
  python -m src.rag_engine --batch      — batch-обробка існуючих rag_documents
  python -m src.rag_engine --batch --limit 10
"""
import hashlib
import logging
import os
import sys
import time

from .config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    RISK_ANALYSIS_PROMPT,
    log,
)
from .groq_client import groq_completion
from .pdf_utils import (
    download_rada_pdf,
    extract_pdf_text,
    chunk_text,
    determine_doc_type,
    classify_chunk_section,
    md5_hash,
)
from .risk_storage import (
    db_conn,
    get_stored_hash,
    save_risk,
    mark_notified,
    find_bills_needing_rag,
    get_bill_documents,
    delete_existing_chunks,
    insert_new_document,
    insert_chunks,
)
from .telegram_notifier import send_message

log = logging.getLogger(__name__)


# === Форматування повідомлень ===

SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}
CATEGORY_EMOJI = {
    "Corruption": "💰",
    "Budgetary": "💵",
    "Legal Collision": "⚖️",
    "Ambiguity": "⚠️",
    "Civil Rights": "👤",
    "Power Concentration": "🏛",
    "Other": "📌",
}
SEVERITY_EMOJI = {"High": "🔴", "Medium": "🟠", "Low": "🟡"}

STATUS_MAP = {
    "new": (1, "Зареєстровано"),
    "У процесі": (2, "У процесі"),
    "Перше читання": (3, "Перше читання"),
    "Друге читання": (4, "Друге читання"),
    "Підписано": (5, "Підписано"),
    "Відхилено": (5, "Відхилено"),
}


def format_risk_message(info: dict, data: dict) -> str:
    """Формує HTML-повідомлення для Telegram з аналізом ризиків."""
    bill_number = info["bill_number"]
    title = info["title"]
    status = info["status"]
    bill_url = info.get("url", "")

    lines = []
    if bill_url:
        lines.append(
            f"📜 <b>#{bill_number}</b> — "
            f"<a href='{bill_url}'>{title[:80]}</a>"
        )
    else:
        lines.append(f"📜 <b>#{bill_number}</b> — {title[:80]}")

    # Legislative progress bar
    step, step_name = STATUS_MAP.get(status, (1, status or "Невідомо"))
    bar = "█" * step + "░" * (5 - step)
    reg_date = info.get("reg_date", "")
    date_str = f" — зареєстровано {reg_date}" if reg_date else ""
    lines.append(f"📊 Прогрес: {bar} {step_name}{date_str}")

    summary = data.get("summary", "—")
    lines.append(f"💡 Суть: {summary[:150]}")

    # Ризики з нового формату risks[]
    risks = data.get("risks", [])
    if risks:
        risks_sorted = sorted(
            risks, key=lambda r: SEVERITY_ORDER.get(r.get("severity", ""), 9)
        )
        for risk in risks_sorted[:5]:  # максимум 5 ризиків
            cat = risk.get("category", "Other")
            sev = risk.get("severity", "Low")
            emoji = CATEGORY_EMOJI.get(cat, "📌")
            sev_icon = SEVERITY_EMOJI.get(sev, "🟡")
            quote = risk.get("quote", "")[:100]
            explanation = risk.get("explanation", "")[:120]
            lines.append(f"{emoji} <b>{cat}</b> {sev_icon} {sev}")
            if quote:
                lines.append(f"   📝 «{quote}»")
            if explanation:
                lines.append(f"   💬 {explanation}")
            lines.append("")
    else:
        lines.append("✅ Ризиків не виявлено")

    if data.get("insufficient_text"):
        lines.append("⚠️ <i>Текст обмежений — аналіз може бути неповним</i>")

    return "\n".join(lines)


def format_status_message(info: dict) -> str:
    """Формує повідомлення про зміну статусу закону (без LLM-аналізу)."""
    bill_number = info["bill_number"]
    title = info["title"]
    status = info["status"]
    bill_url = info.get("url", "")
    old_value = info.get("old_value", "")
    new_value = info.get("new_value", "")
    reg_date = info.get("reg_date", "")
    committee = info.get("committee", "")

    lines = []
    if bill_url:
        lines.append(
            f"📜 <b>#{bill_number}</b> — "
            f"<a href='{bill_url}'>{title[:80]}</a>"
        )
    else:
        lines.append(f"📜 <b>#{bill_number}</b> — {title[:80]}")

    if old_value:
        lines.append(f"🔄 Статус: {old_value} → <b>{new_value}</b>")
    else:
        lines.append(f"📊 Статус: <b>{status}</b>")

    date_str = f" (зареєстровано {reg_date})" if reg_date else ""
    if committee:
        lines.append(f"🏛 Комітет: {committee}{date_str}")
    elif date_str:
        lines.append(f"📅{date_str}")

    return "\n".join(lines)


# === Основний пайплайн ===

def process_bill(info: dict, test_mode: bool = False):
    """Повна обробка одного законопроекту: PDF → текст → LLM → збереження.

    Args:
        info: Словник з даними законопроекту.
        test_mode: Якщо True — не надсилати Telegram.

    Returns:
        (info, data) якщо успішно, (None, None) якщо пропущено.
    """
    bill_id = info["id"]
    bill_number = info["bill_number"]
    title = info["title"]
    status = info["status"]

    log.info("  Processing: #%s | %s", bill_number, title[:60])
    log.info("  Status: %s", status)

    docs = get_bill_documents(bill_id, bill_number)
    if not docs:
        log.info("  No documents found")
        return None, None

    log.info("  Documents: %d", len(docs))

    from .pdf_utils import get_rada_token
    rada_token = get_rada_token()
    all_chunks = []
    pdf_hashes = []

    for doc in docs:
        try:
            data = download_rada_pdf(str(doc["file_id"]), rada_token)
            if len(data) < 1000:
                continue

            pdf_hash = md5_hash(data)
            pdf_hashes.append(pdf_hash)

            # Рання перевірка кешу
            if len(pdf_hashes) == 1:
                stored_hash = get_stored_hash(bill_id)
                current_hash = md5_hash("".join(pdf_hashes).encode())
                if stored_hash and stored_hash == current_hash:
                    log.info("  Early cache hit (hash=%s) — skipping all", current_hash[:8])
                    return None, None

            safe_bn = "".join(
                c if (c.isalnum() or c in "._-") else "_" for c in str(bill_number)
            )
            path = f"/tmp/rag_{safe_bn}_{doc['file_id']}.pdf"
            with open(path, "wb") as f:
                f.write(data)

            text = extract_pdf_text(path)
            os.unlink(path)

            doc_type = determine_doc_type(doc.get("type", ""))
            chunks = chunk_text(text)

            for i, chunk in enumerate(chunks):
                sec = classify_chunk_section(chunk)
                all_chunks.append(
                    {
                        "bill_id": bill_id,
                        "reg_number": bill_number,
                        "doc_type": doc_type,
                        "chunk_index": i,
                        "text": chunk,
                        "section": sec,
                    }
                )
        except Exception as e:
            log.warning("  Doc error for file_id=%s: %s: %s", doc.get("file_id"), type(e).__name__, str(e)[:200])

    if not all_chunks:
        log.info("  No text extracted")
        return None, None

    # Dedup
    uniq, seen = [], set()
    for chunk in all_chunks:
        short = chunk["text"][:120]
        if short not in seen:
            seen.add(short)
            uniq.append(chunk)
    all_chunks = uniq

    all_pdf_hash = md5_hash("".join(pdf_hashes).encode()) if pdf_hashes else None

    # Перевірка кешу після всіх PDF
    stored_hash = get_stored_hash(bill_id)
    if stored_hash and stored_hash == all_pdf_hash:
        log.info("  Cache hit (hash=%s) — skipping LLM", all_pdf_hash[:8])
        return None, None

    # Зберігаємо чанки
    delete_existing_chunks(bill_id)
    doc_db_id = insert_new_document(bill_id, bill_number, all_pdf_hash)
    insert_chunks(doc_db_id, bill_id, all_chunks)
    log.info("  Stored: doc_id=%d chunks=%d pdf_hash=%s", doc_db_id, len(all_chunks), all_pdf_hash[:8])

    # Зберігаємо версію для історії (law_versions)
    plain_text = "\n\n".join(c["text"] for c in all_chunks)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO law_versions (law_id, status_at_moment, text_hash, plain_text)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (law_id, text_hash) DO NOTHING
            RETURNING id
            """,
            (bill_id, status, all_pdf_hash, plain_text[:50000]),
        )
        conn.commit()

    # Оновлюємо bills.text_hash та bills.plain_text
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE bills SET text_hash=%s, plain_text=%s WHERE id=%s",
            (all_pdf_hash, plain_text[:50000], bill_id),
        )
        conn.commit()

    # Готуємо контекст для LLM
    substantive = [
        c["text"]
        for c in all_chunks
        if any(
            w in c["text"]
            for w in [
                "стаття", "Угода", "Позик", "Меморандум",
                "фінансов", "Кредитор", "Позичальник", "макрофінансова",
            ]
        )
    ]
    ctx = (
        "\n\n".join(substantive[:5])
        if substantive
        else "\n\n".join(c["text"] for c in all_chunks[:3])
    )
    insufficient = len(ctx.strip()) < 1200

    prompt = RISK_ANALYSIS_PROMPT.format(text=ctx)

    try:
        data = groq_completion(prompt)
    except Exception as e:
        log.error("  LLM_FAIL: %s: %s", type(e).__name__, str(e)[:200])
        return None, None

    # Додаємо insufficient_text, якщо текст обмежений
    if insufficient:
        data["insufficient_text"] = True

    try:
        save_risk(doc_db_id, data, GROQ_MODEL)
    except Exception as e:
        log.error("  SAVE_FAIL: %s: %s", type(e).__name__, str(e)[:200])
        return None, None

    # Оновлюємо law_versions з результатами
    import json
    analysis_summary = data.get("summary", "")[:2000]
    risks_json_str = json.dumps(data, ensure_ascii=False)
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE law_versions SET analysis_summary=%s, risks_json=%s
            WHERE law_id=%s AND text_hash=%s
            """,
            (analysis_summary, risks_json_str, bill_id, all_pdf_hash),
        )
        conn.commit()

    return info, data


def main() -> None:
    """Головна функція: моніторинг законів з LLM-аналізом.

    Підтримує режими:
      - звичайний: нові/змінені закони з change_log
      - --force: переаналізувати всі неоповіщені
      - --test: без відправки Telegram
      - --batch: batch-обробка існуючих rag_documents (без завантаження PDF)
    """
    force = "--force" in sys.argv
    test_mode = "--test" in sys.argv
    batch_mode = "--batch" in sys.argv

    if not GROQ_API_KEY:
        log.error("GROQ_API_KEY не встановлено")
        return

    log.info("=== RAG Monitor %s ===", __import__("datetime").datetime.now())

    if batch_mode:
        _run_batch(test_mode)
        return

    # Знаходимо закони для аналізу
    bills_raw = find_bills_needing_rag(limit=20)
    log.info("Bills to analyze: %d", len(bills_raw))

    if not bills_raw:
        log.info("Nothing to do.")
        return

    processed = []      # (info, data) — повний аналіз ризиків
    status_updates = []  # info — тільки зміна статусу

    for bill_info in bills_raw:
        try:
            change_type = bill_info.get("change_type", "new")
            if change_type == "status_change":
                log.info(
                    "  Status update: #%s | %s",
                    bill_info["bill_number"],
                    bill_info["title"][:60],
                )
                log.info(
                    "  %s → %s",
                    bill_info.get("old_value", "?"),
                    bill_info.get("new_value", "?"),
                )
                status_updates.append(bill_info)
                mark_notified([bill_info["id"]])
            else:
                info, data = process_bill(bill_info, test_mode)
                if data:
                    processed.append((info, data))
                    mark_notified([info["id"]])
        except Exception as e:
            log.exception("Error processing bill #%s", bill_info.get("bill_number"))

    # Відправка повідомлень
    if not test_mode:
        for info in status_updates:
            msg = format_status_message(info)
            send_message(msg)
            log.info("  Sent status update: #%s", info["bill_number"])
            time.sleep(0.5)

        for info, data in processed:
            msg = format_risk_message(info, data)
            send_message(msg)
            log.info("  Sent to TG: #%s", info["bill_number"])
            time.sleep(0.5)

    log.info(
        "=== Done: %d analyzed, %d status updates ===",
        len(processed),
        len(status_updates),
    )


def _run_batch(test_mode: bool = False) -> None:
    """Batch-обробка існуючих rag_documents (без завантаження PDF)."""
    limit = 50
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    log.info("Batch mode: limit=%d groq_key=%s", limit, bool(GROQ_API_KEY))

    import json
    from .risk_storage import db_conn

    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.bill_id, d.file_id, d.title
            FROM rag_documents d
            WHERE NOT EXISTS (
                SELECT 1 FROM risk_assessments r
                WHERE r.document_id = d.id
            )
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    log.info("Batch size: %d", len(rows))
    done = 0
    skip = 0

    for idx, (id_, bill_id, file_id, title) in enumerate(rows, start=1):
        log.info("[%d/%d] doc_id=%d bill_id=%d", idx, len(rows), id_, bill_id)

        # Отримуємо текст з rag_chunks
        with db_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_text FROM rag_chunks WHERE document_id = %s ORDER BY chunk_index",
                (id_,),
            )
            texts = [r[0] for r in cur.fetchall() if r[0]]

        if not texts:
            log.info("  SKIP: empty text")
            skip += 1
            if idx < len(rows):
                time.sleep(1)
            continue

        log.info("  TEXT_LEN: %d chars", sum(len(t) for t in texts))
        text = "\n".join(texts)

        substantive = [
            t
            for t in text.split("\n\n")
            if any(
                w in t
                for w in [
                    "стаття", "Угода", "Позик", "Меморандум",
                    "фінансов", "Кредитор", "Позичальник", "макрофінансова",
                ]
            )
        ]
        ctx = "\n\n".join(substantive[:5]) if substantive else text[:4000]
        insufficient = len(ctx.strip()) < 1200

        prompt = RISK_ANALYSIS_PROMPT.format(text=ctx)

        try:
            data = groq_completion(prompt)
        except Exception as e:
            log.error("  LLM_FAIL: %s: %s", type(e).__name__, str(e)[:200])
            if idx < len(rows):
                time.sleep(2.5)
            continue

        if insufficient:
            data["insufficient_text"] = True

        try:
            save_risk(id_, data, GROQ_MODEL)
            done += 1
        except Exception as e:
            log.error("  SAVE_FAIL: %s: %s", type(e).__name__, str(e)[:200])

        if idx < len(rows):
            time.sleep(2.5)

    log.info("Batch done: done=%d skip=%d", done, skip)


if __name__ == "__main__":
    main()