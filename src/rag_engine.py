"""RAG engine — моніторинг законопроектів з LLM-аналізом ризиків.

Основні команди:
  python -m src.rag_engine              — стандартний запуск (нові/змінені закони)
  python -m src.rag_engine --force      — переаналізувати всі неоповіщені
  python -m src.rag_engine --test       — без відправки Telegram
  python -m src.rag_engine --batch      — batch-обробка існуючих rag_documents
  python -m src.rag_engine --batch --limit 10
"""
import hashlib
import json
import logging
import os
import sys
import time

from .config import (
    LLM_API_KEY,
    LLM_MODEL,
    RISK_ANALYSIS_PROMPT,
    log,
)
from .groq_client import groq_completion
from .pdf_utils import (
    download_rada_pdf,
    extract_pdf_text,
    md5_hash,
)
from .risk_storage import (
    get_stored_hash,
    save_risk,
    update_bill_procedural,
    mark_notified,
    find_bills_needing_rag,
    get_bill_documents,
    insert_new_document,
)
from .d1_client import d1_exec, d1_query
from .telegram_notifier import send_message

log = logging.getLogger(__name__)


# === Форматування повідомлень ===

RISK_LEVEL_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🟡"}
RISK_LEVEL_UA = {"high": "ВИСОКИЙ", "medium": "СЕРЕДНІЙ", "low": "НИЗЬКИЙ"}

STATUS_MAP = {
    "new": (1, "Зареєстровано"),
    "У процесі": (2, "У процесі"),
    "Перше читання": (3, "Перше читання"),
    "Друге читання": (4, "Друге читання"),
    "Підписано": (5, "Підписано"),
    "Відхилено": (5, "Відхилено"),
}


def format_risk_message(info: dict, data: dict) -> str:
    """Формує HTML-повідомлення для Telegram з аналізом ризиків (Chain of Thought)."""
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

    step, step_name = STATUS_MAP.get(status, (1, status or "Невідомо"))
    bar = "█" * step + "░" * (5 - step)
    reg_date = info.get("reg_date", "")
    date_str = f" — зареєстровано {reg_date}" if reg_date else ""
    lines.append(f"📊 Прогрес: {bar} {step_name}{date_str}")

    summary = data.get("summary", "—")
    lines.append(f"💡 Суть: {summary[:200]}")

    # Класифікація
    is_procedural = data.get("is_procedural", False)
    if is_procedural:
        reason = data.get("classification_reason", "")
        lines.append(f"\n📋 <b>Процедурний</b> — {reason[:150]}")
        lines.append("✅ Ризиків немає (процедурний закон)")
        return "\n".join(lines)

    has_risks = data.get("has_risks", False)
    risk_level = data.get("risk_level", "low")
    detailed_risks = data.get("detailed_risks", [])

    if has_risks and detailed_risks:
        level_icon = RISK_LEVEL_EMOJI.get(risk_level, "🟡")
        level_name = RISK_LEVEL_UA.get(risk_level, "НЕВІДОМИЙ")
        lines.append(f"\n{level_icon} <b>Рівень ризику: {level_name}</b>")

        for i, risk in enumerate(detailed_risks[:5], 1):
            lines.append(f"\n{i}. {risk[:200]}")
    else:
        lines.append("✅ Ризиків не виявлено")

    if data.get("insufficient_text"):
        lines.append("\n⚠️ <i>Текст обмежений — аналіз може бути неповним</i>")

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
    """Повна обробка одного законопроекту: PDF → текст → LLM → збереження в D1.

    Пайплайн:
      1. Скачуємо PDF文档а
      2. Витягуємо весь текст
      3. LLM: класифікація (процедурний/непроцедурний) + аналіз ризиків
      4. Зберігаємо результати

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

    # Кеш: якщо жоден PDF не змінився — skip all
    stored_hash = get_stored_hash(bill_id)
    pdf_hashes = []
    all_texts = []

    for doc in docs:
        try:
            data = download_rada_pdf(str(doc["file_id"]), rada_token)
            if len(data) < 1000:
                continue

            pdf_hash = md5_hash(data)
            pdf_hashes.append(pdf_hash)

            # Рання перевірка кешу ПІСЛЯ кожного скачування
            current_hash = md5_hash("".join(pdf_hashes).encode())
            if stored_hash and stored_hash == current_hash:
                log.info("  Cache hit after doc %s (hash=%s) — skipping", doc["file_id"], current_hash[:8])
                return None, None

            safe_bn = "".join(
                c if (c.isalnum() or c in "._-") else "_" for c in str(bill_number)
            )
            path = f"/tmp/rag_{safe_bn}_{doc['file_id']}.pdf"
            with open(path, "wb") as f:
                f.write(data)

            text = extract_pdf_text(path)
            os.unlink(path)

            # Dedup по 120 символах — відсікаємо шапки/титули
            short = text[:120].strip()
            if short and short in {t[:120].strip() for t in all_texts}:
                log.info("  Duplicate text from doc %s — skipping", doc["file_id"])
                continue

            all_texts.append(text)
        except Exception as e:
            log.warning("  Doc error for file_id=%s: %s: %s", doc.get("file_id"), type(e).__name__, str(e)[:200])

    if not all_texts:
        log.info("  No text extracted")
        return None, None

    # Об'єднуємо весь текст
    full_text = "\n\n".join(all_texts)
    all_pdf_hash = md5_hash("".join(pdf_hashes).encode()) if pdf_hashes else None

    # Фінальна перевірка кешу
    if stored_hash and stored_hash == all_pdf_hash:
        log.info("  Final cache hit (hash=%s) — skipping LLM", all_pdf_hash[:8])
        return None, None

    # Зберігаємо документ
    doc_db_id = insert_new_document(bill_id, bill_number, all_pdf_hash)
    log.info("  Stored: doc_id=%d pdf_hash=%s text_len=%d", doc_db_id, all_pdf_hash[:8], len(full_text))

    # Зберігаємо версію в law_versions (D1)
    d1_exec("raw_sql", {
        "sql": """INSERT OR IGNORE INTO law_versions (law_id, status_at_moment, text_hash, plain_text)
                  VALUES (?, ?, ?, ?)""",
        "params": [bill_id, status, all_pdf_hash, full_text[:50000]],
    })

    # Оновлюємо bills.text_hash та bills.plain_text (D1)
    d1_exec("raw_sql", {
        "sql": "UPDATE bills SET text_hash=?, plain_text=? WHERE id=?",
        "params": [all_pdf_hash, full_text[:50000], bill_id],
    })

    # Відправляємо повний текст в LLM
    insufficient = len(full_text.strip()) < 1200
    prompt = RISK_ANALYSIS_PROMPT.format(text=full_text)

    try:
        llm_data = groq_completion(prompt)
    except Exception as e:
        log.error("  LLM_FAIL: %s: %s", type(e).__name__, str(e)[:200])
        return None, None

    if insufficient:
        llm_data["insufficient_text"] = True

    # Додаємо назву моделі в результат
    llm_data["model_used"] = LLM_MODEL

    # Визначаємо чи процедурний
    is_procedural = llm_data.get("is_procedural", False)
    risk_level = llm_data.get("risk_level")
    has_risks = llm_data.get("has_risks", False)

    if is_procedural:
        log.info("  Classification: ПРОЦЕДУРНИЙ — %s", llm_data.get("classification_reason", "")[:80])
        # Для процедурних — зберігаємо без ризиків
        llm_data["has_risks"] = False
        llm_data["risk_level"] = None
        llm_data["detailed_risks"] = []
    else:
        log.info("  Classification: НЕПРОЦЕДУРНИЙ — risk_level=%s risks=%d", risk_level, len(llm_data.get("detailed_risks", [])))

    try:
        save_risk(doc_db_id, llm_data, LLM_MODEL)
        update_bill_procedural(bill_id, is_procedural)
    except Exception as e:
        log.error("  SAVE_FAIL: %s: %s", type(e).__name__, str(e)[:200])
        return None, None

    # Оновлюємо law_versions з результатами (D1)
    analysis_summary = llm_data.get("summary", "")[:2000]
    law_summary = llm_data.get("law_summary", "")[:3000]
    risks_json_str = json.dumps(llm_data, ensure_ascii=False)
    d1_exec("raw_sql", {
        "sql": """UPDATE law_versions SET analysis_summary=?, risks_json=?
                  WHERE law_id=? AND text_hash=?""",
        "params": [analysis_summary, risks_json_str, bill_id, all_pdf_hash],
    })

    # Push risk + law_version через стандартний sync API
    d1_exec("risk", {
        "bill_number": bill_number,
        "overall_score": 100 if risk_level == "high" else 70 if risk_level == "medium" else 30 if risk_level == "low" else 0,
        "model_used": LLM_MODEL,
        "json_data": json.dumps(llm_data, ensure_ascii=False),
        "raw_analysis": law_summary or analysis_summary,
        "insufficient_text": bool(llm_data.get("insufficient_text", False)),
    })
    d1_exec("law_version", {
        "bill_number": bill_number,
        "status_at_moment": status,
        "text_hash": all_pdf_hash,
        "plain_text": full_text[:50000],
        "analysis_summary": analysis_summary,
        "risks_json": risks_json_str,
    })

    return info, llm_data


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

    if not LLM_API_KEY:
        log.error("LLM_API_KEY не встановлено")
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

    processed = []       # (info, data) — повний аналіз ризиків
    status_updates = []  # info — тільки зміна статусу (без зміни тексту)

    for bill_info in bills_raw:
        try:
            change_type = bill_info.get("change_type", "new")
            if change_type == "status_change":
                log.info(
                    "  Status change: #%s | %s → %s",
                    bill_info["bill_number"],
                    bill_info.get("old_value", "?"),
                    bill_info.get("new_value", "?"),
                )
                # Пробуємо LLM-аналіз — якщо текст змінився, отримаємо результат
                info, data = process_bill(bill_info, test_mode)
                if data:
                    # Текст змінився — повний аналіз + Telegram
                    processed.append((info, data))
                    mark_notified([info["id"]])
                    log.info("  Text changed — full analysis sent")
                else:
                    # Текст не змінився — тільки статус-оновлення
                    status_updates.append(bill_info)
                    mark_notified([bill_info["id"]])
                    log.info("  Text unchanged — status notification only")
            else:
                # Новий закон — повний аналіз
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

    log.info("Batch mode: limit=%d llm_key=%s", limit, bool(LLM_API_KEY))

    rows = d1_query(
        """
        SELECT d.id, d.bill_id, d.file_id, d.title
        FROM rag_documents d
        WHERE NOT EXISTS (
            SELECT 1 FROM risk_assessments r
            WHERE r.document_id = d.id
        )
        LIMIT ?
        """,
        [limit],
    )

    log.info("Batch size: %d", len(rows))
    done = 0
    skip = 0

    for idx, row in enumerate(rows, start=1):
        id_ = row["id"]
        bill_id = row["bill_id"]
        file_id = row["file_id"]
        title = row["title"]

        log.info("[%d/%d] doc_id=%d bill_id=%d", idx, len(rows), id_, bill_id)

        # Отримуємо текст з law_versions
        text_rows = d1_query(
            "SELECT plain_text FROM law_versions WHERE law_id = ? ORDER BY version_date DESC LIMIT 1",
            [bill_id],
        )
        texts = [r["plain_text"] for r in text_rows if r.get("plain_text")]

        if not texts:
            log.info("  SKIP: empty text")
            skip += 1
            if idx < len(rows):
                time.sleep(1)
            continue

        text = texts[0]
        log.info("  TEXT_LEN: %d chars", len(text))
        insufficient = len(text.strip()) < 1200

        prompt = RISK_ANALYSIS_PROMPT.format(text=text)

        try:
            llm_data = groq_completion(prompt)
        except Exception as e:
            log.error("  LLM_FAIL: %s: %s", type(e).__name__, str(e)[:200])
            if idx < len(rows):
                time.sleep(2.5)
            continue

        if insufficient:
            llm_data["insufficient_text"] = True

        try:
            save_risk(id_, llm_data, LLM_MODEL)
            update_bill_procedural(bill_id, llm_data.get("is_procedural", False))
            done += 1
        except Exception as e:
            log.error("  SAVE_FAIL: %s: %s", type(e).__name__, str(e)[:200])

        if idx < len(rows):
            time.sleep(2.5)

    log.info("Batch done: done=%d skip=%d", done, skip)


if __name__ == "__main__":
    main()
