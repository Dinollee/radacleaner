"""RAG engine — моніторинг законопроектів з LLM-аналізом ризиків.

Чанкований аналіз:
  1. Текст розбивається на чанки ~30K символів на межах речень/абзаців
  2. Кожен чанк відправляється в LLM послідовно в межах однієї сесії
  3. Контекст попередніх чанків зберігається для наступних
  4. Фінальний результат агрегується з усіх чанків

Основні команди:
  python -m src.rag_engine              — стандартний запуск (нові/змінені закони)
  python -m src.rag_engine --force      — переаналізувати всі неоповіщені
  python -m src.rag_engine --test       — без відправки Telegram
"""
import json
import logging
import os
import sys
import time

from .config import (
    LLM_API_KEY,
    LLM_MODEL,
    log,
)
from .groq_client import groq_completion, groq_completion_raw
from .chunking import chunk_text, CHUNK_SIZE
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

log = logging.getLogger(__name__)

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

SYSTEM_PROMPT = (
    "Ти — головний юридичний аналітик моніторингового центру, "
    "глибоко спеціалізований на законодавстві України, нормативно-правовій базі Верховної Ради, "
    "регламентах та вимогах до гармонізації українського права з директивами ЄС (acquis communautaire). "
    "Аналізуєш законопроекти по частинах (чанках). Зберігай контекст попередніх частин."
)

CHUNK1_PROMPT = """Тобі надано ПЕРШУ частину тексту законопроєкту Верховної Ради України (чанк {chunk_num}/{total_chunks}).

ЕТАП 1: КЛАСИФІКАЦІЯ
Визнач, чи є цей закон ПРОЦЕДУРНИМ чи НЕПРОЦЕДУРНИМ.

ПРОЦЕДУРНІ закони (не змінюють правових норм, не впливають на життя громадян):
- Заяви та звернення ВРУ
- Зміни до регламенту ВРУ
- Організаційні питання
- Кадрові призначення
- Державні свята, пам'ятні дати

НЕПРОЦЕДУРНІ закони (впливають на життя громадян, демократію, права):
- Зміни до кодексів
- Нові санкції, штрафи, обмеження прав
- Бюджетні зміни
- Зміни виборчого законодавства
- Обмеження свободи слова, ЗМІ, зібрань
- Корупційні ризики
- Зміни в оборонній сфері
- Євроінтеграційні зобов'язання

ЕТАП 2: ПОЧАТКОВИЙ АНАЛІЗ РИЗИКІВ (тільки якщо НЕПРОЦЕДУРНИЙ)
Проаналізуй цю частину тексту. Шукай конкретні норми, які:
1. Корупція та зловживання (невизначені повноваження, винятки з тендерів)
2. Обмеження прав громадян (штрафи, санкції, звуження прав)
3. Загрози демократії (контроль над ЗМІ, зміни балансу влади)
4. Фінансові ризики (зміни до Податкового/Бюджетного кодексів)
5. Євроінтеграція (невідповідність директивам ЄС)

Правила:
- Не вигадуй наслідки — аналізуй лише наведений текст.
- Кожен ризик ПОВИНЕН містити посилання на конкретну статтю/пункт.
- Абстрактні/надумані ризики без конкретики — НЕ включай.
- Якщо закон стосується оборони/безпеки/воєнного стану — знижуй "градус токсичності".

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "is_procedural": true/false,
  "classification_reason": "Стисле пояснення",
  "has_risks": true/false,
  "risk_level": "low/medium/high/null",
  "chunk_risks": [
    "Конкретний ризик: посилання на статтю + опис наслідків"
  ],
  "chunk_summary": "Що містить ця частина тексту (1-2 речення)"
}}

Текст (чанк {chunk_num}/{total_chunks}):
{text}"""

CHUNK_N_PROMPT = """Тобі надано НАСТУПНУ частину тексту законопроєкту Верховної Ради України (чанк {chunk_num}/{total_chunks}).

Попередній контекст (чанки 1-{prev_num}):
{prev_context}

АНАЛІЗ ЦІЄЇ ЧАСТИНИ:
Проаналізуй цей чанк на наявність ризиків. Порівняй з попереднім контекстом — чи додає ця частина нові ризики або змінює оцінку?

Правила:
- Не дублюй ризики з попередніх чанків
- Додавай ТІЛЬКИ нові знахідки з посиланнями на конкретні статті
- Абстрактні ризики без конкретики — НЕ включай
- Якщо нових ризиків немає — chunk_risks: []

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "has_risks": true/false,
  "risk_level": "low/medium/high/null (або залиш попередній)",
  "chunk_risks": [
    "Новий ризик: посилання на статтю + опис"
  ],
  "chunk_summary": "Що містить ця частина (1-2 речення)"
}}

Текст (чанк {chunk_num}/{total_chunks}):
{text}"""

FINAL_PROMPT = """Аналіз законопроєкту завершено. Ось всі зібрані дані з {total_chunks} чанків:

{all_summaries}

Ризики, знайдені в різних чанках:
{all_risks}

ФІНАЛЬНА ОЦІНКА — підсумуй всі знахідки в один JSON.

ВАЖЛИВО: Ризики ПОВИННІ бути згруповані в 3-5 ДИНАМІЧНИХ НАПРЯМКІВ за тематикою.
Наприклад:
- "Конституційні невідповідності" (якщо є порушення Конституції)
- "Бюджетний дисбаланс" (якщо є фінансові ризики)
- "Корупційні фактори та нечіткі поняття" (якщо є дискреційні повноваження)
- "Невідповідність чинному законодавству" (якщо є колізії з кодексами)
- "Обмеження прав та свобод" (якщо є звуження прав)
Назви напрямків — ДИНАМІЧНІ, залежать від конкретного закону. Не використовуй шаблонні назви.

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "is_procedural": true/false,
  "classification_reason": "Підсумкове пояснення класифікації",
  "has_risks": true/false,
  "risk_level": "low/medium/high/null",
  "summary": "Стисле опис суті змін (1-2 речення)",
  "law_summary": "Повний опис: хто ініціює, що змінює, на кого поширюється. 3-5 речень.",
  "risk_categories": [
    {{
      "category": "Назва напрямку (динамічна, за тематикою)",
      "risks": [
        "Конкретний ризик: посилання на статтю + опис наслідків"
      ]
    }}
  ],
  "detailed_risks": ["flat list of all risks for backward compatibility"],
  "analyzed_chunks": [{chunk_indices}],
  "insufficient_text": false
}}

Правила:
- Об'єднай дублікати ризиків з різних чанків
- Підвищуй risk_level якщо ризики з кількох чанків утворюють ланцюг
- НЕ додавай нових ризиків яких не було в чанках
- detailed_risks — плоский список для обратної сумісності (ті самі що в risk_categories)
- Кількість категорій залежить від закону: від 1 до 5"""


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

    step, step_name = STATUS_MAP.get(status, (1, status or "Невідомо"))
    bar = "█" * step + "░" * (5 - step)
    reg_date = info.get("reg_date", "")
    date_str = f" — зареєстровано {reg_date}" if reg_date else ""
    lines.append(f"📊 Прогрес: {bar} {step_name}{date_str}")

    chunks_analyzed = data.get("analyzed_chunks", [])
    if chunks_analyzed:
        lines.append(f"📑 Проаналізовано чанків: {len(chunks_analyzed)}")

    summary = data.get("summary", "—")
    lines.append(f"💡 Суть: {summary[:200]}")

    is_procedural = data.get("is_procedural", False)
    if is_procedural:
        reason = data.get("classification_reason", "")
        lines.append(f"\n📋 <b>Процедурний</b> — {reason[:150]}")
        lines.append("✅ Ризиків немає (процедурний закон)")
        return "\n".join(lines)

    has_risks = data.get("has_risks", False)
    risk_level = data.get("risk_level", "low")
    detailed_risks = data.get("detailed_risks", [])
    risk_categories = data.get("risk_categories", [])

    if has_risks and detailed_risks:
        level_icon = RISK_LEVEL_EMOJI.get(risk_level, "🟡")
        level_name = RISK_LEVEL_UA.get(risk_level, "НЕВІДОМИЙ")
        lines.append(f"\n{level_icon} <b>Рівень ризику: {level_name}</b>")
        if risk_categories:
            for cat in risk_categories:
                cat_name = cat.get("category", "Інше")
                cat_risks = cat.get("risks", [])
                if cat_risks:
                    lines.append(f"\n📂 <b>{cat_name}</b>")
                    for i, risk in enumerate(cat_risks[:3], 1):
                        lines.append(f"  {i}. {risk[:180]}")
                    if len(cat_risks) > 3:
                        lines.append(f"  ... та ще {len(cat_risks) - 3}")
        else:
            for i, risk in enumerate(detailed_risks[:5], 1):
                lines.append(f"\n{i}. {risk[:200]}")
    else:
        lines.append("✅ Ризиків не виявлено")

    if data.get("insufficient_text"):
        lines.append("\n⚠️ <i>Текст обмежений — аналіз може бути неповним</i>")

    return "\n".join(lines)


def format_status_message(info: dict) -> str:
    """Формує повідомлення про зміну статусу закону."""
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
        lines.append(f"📜 <b>#{bill_number}</b> — <a href='{bill_url}'>{title[:80]}</a>")
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


MAX_CHUNKS = 15  # максимальна кількість чанків для аналізу


def _select_chunks(chunks: list[str], max_chunks: int = MAX_CHUNKS) -> list[tuple[int, str]]:
    """Відбирає чанки для аналізу: перший + рівномірно розподілені + останній.

    Returns:
        Список (індекс, текст) чанків для аналізу.
    """
    if len(chunks) <= max_chunks:
        return list(enumerate(chunks))

    selected = set()
    selected.add(0)  # перший (класифікація)
    selected.add(len(chunks) - 1)  # останній

    # Рівномірно розподіляємо решту
    step = (len(chunks) - 1) / (max_chunks - 2)
    for i in range(1, max_chunks - 1):
        idx = round(i * step)
        selected.add(min(idx, len(chunks) - 1))

    return [(i, chunks[i]) for i in sorted(selected)]


def _chunked_llm_analysis(full_text: str, total_chunks: int) -> dict | None:
    """Послідовний чанкований аналіз тексту через LLM з збереженням контексту.

    Returns:
        Фінальний JSON з результатами або None при помилці.
    """
    chunks = chunk_text(full_text)
    selected = _select_chunks(chunks)
    actual_chunks = len(chunks)
    to_analyze = len(selected)
    log.info("  Chunked analysis: %d total chunks, analyzing %d (max=%d)", actual_chunks, to_analyze, MAX_CHUNKS)

    all_chunk_results = []
    all_risks = []
    all_summaries = []
    messages = None

    for seq, (i, chunk) in enumerate(selected):
        chunk_num = i + 1
        log.info("  Processing chunk %d/%d (%d chars)", seq + 1, to_analyze, len(chunk))

        if seq == 0:
            # Перший чанк — класифікація + початковий аналіз
            prompt = CHUNK1_PROMPT.format(
                chunk_num=chunk_num,
                total_chunks=actual_chunks,
                text=chunk,
            )
            try:
                result = groq_completion(prompt, system_prompt=SYSTEM_PROMPT, max_tokens=1600)
            except Exception as e:
                log.error("  LLM error on chunk %d: %s", chunk_num, str(e)[:200])
                return None

            all_chunk_results.append(result)
            all_risks.extend(result.get("chunk_risks", []))
            all_summaries.append(result.get("chunk_summary", ""))

            # Перевіряємо чи процедурний — якщо так, пропускаємо решту
            if result.get("is_procedural"):
                log.info("  Procedural detected at chunk %d — stopping", chunk_num)
                return result

            # Будуємо messages для наступних чанків
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": json.dumps(result, ensure_ascii=False)},
            ]
        else:
            # Наступні чанки — продовження з контекстом
            prev_context = "\n".join(
                f"[Чанк {j+1}]: {s}" for j, s in enumerate(all_summaries)
            )
            prompt = CHUNK_N_PROMPT.format(
                chunk_num=chunk_num,
                total_chunks=actual_chunks,
                prev_num=i,
                prev_context=prev_context[:3000],
                text=chunk,
            )

            messages.append({"role": "user", "content": prompt})

            try:
                result = groq_completion(
                    prompt=None,
                    system_prompt=SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=1600,
                )
            except Exception as e:
                log.error("  LLM error on chunk %d: %s", chunk_num, str(e)[:200])
                return None

            all_chunk_results.append(result)
            all_risks.extend(result.get("chunk_risks", []))
            all_summaries.append(result.get("chunk_summary", ""))

            # Додаємо відповідь асистента в історію
            messages.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})

        time.sleep(1)  # пауза між чанками

    # Фінальна агрегація
    log.info("  Final aggregation: %d risks found across %d chunks", len(all_risks), actual_chunks)

    summaries_text = "\n".join(
        f"[Чанк {j+1}]: {s}" for j, s in enumerate(all_summaries) if s
    )
    risks_text = "\n".join(
        f"{j+1}. {r}" for j, r in enumerate(all_risks)
    ) if all_risks else "Ризиків не знайдено."

    final_prompt = FINAL_PROMPT.format(
        total_chunks=actual_chunks,
        all_summaries=summaries_text,
        all_risks=risks_text,
        chunk_indices=json.dumps(list(range(1, actual_chunks + 1))),
    )

    messages.append({"role": "user", "content": final_prompt})

    try:
        final_result = groq_completion(
            prompt=None,
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=2000,
        )
    except Exception as e:
        log.error("  LLM final aggregation error: %s", str(e)[:200])
        return None

    final_result["analyzed_chunks"] = list(range(1, actual_chunks + 1))
    return final_result


def process_bill(info: dict, test_mode: bool = False):
    """Повна обробка одного законопроекту: PDF → чанки → LLM → збереження.

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

    full_text = "\n\n".join(all_texts)
    all_pdf_hash = md5_hash("".join(pdf_hashes).encode()) if pdf_hashes else None

    if stored_hash and stored_hash == all_pdf_hash:
        log.info("  Final cache hit (hash=%s) — skipping LLM", all_pdf_hash[:8])
        return None, None

    # Зберігаємо документ
    doc_db_id = insert_new_document(bill_id, bill_number, all_pdf_hash)
    log.info("  Stored: doc_id=%d pdf_hash=%s text_len=%d", doc_db_id, all_pdf_hash[:8], len(full_text))

    # Зберігаємо ПОВНИЙ текст в law_versions (для pgvector)
    d1_exec("raw_sql", {
        "sql": """INSERT INTO law_versions (law_id, status_at_moment, text_hash, plain_text)
                  VALUES (?, ?, ?, ?)
                  ON CONFLICT (law_id, text_hash) DO NOTHING""",
        "params": [bill_id, status, all_pdf_hash, full_text],
    })

    # Оновлюємо bills.text_hash та bills.plain_text
    d1_exec("raw_sql", {
        "sql": "UPDATE bills SET text_hash=?, plain_text=? WHERE id=?",
        "params": [all_pdf_hash, full_text[:50000], bill_id],
    })

    # Чанкований LLM аналіз
    insufficient = len(full_text.strip()) < 1200
    llm_data = _chunked_llm_analysis(full_text, len(chunk_text(full_text)))

    if llm_data is None:
        log.error("  LLM analysis failed")
        return None, None

    if insufficient:
        llm_data["insufficient_text"] = True

    llm_data["model_used"] = LLM_MODEL

    is_procedural = llm_data.get("is_procedural", False)
    risk_level = llm_data.get("risk_level")
    has_risks = llm_data.get("has_risks", False)

    if is_procedural:
        log.info("  Classification: ПРОЦЕДУРНИЙ — %s", llm_data.get("classification_reason", "")[:80])
        llm_data["has_risks"] = False
        llm_data["risk_level"] = None
        llm_data["detailed_risks"] = []
    else:
        log.info("  Classification: НЕПРОЦЕДУРНИЙ — risk_level=%s risks=%d",
                 risk_level, len(llm_data.get("detailed_risks", [])))

    try:
        save_risk(doc_db_id, llm_data, LLM_MODEL)
        update_bill_procedural(bill_id, is_procedural)
    except Exception as e:
        log.error("  SAVE_FAIL: %s: %s", type(e).__name__, str(e)[:200])
        return None, None

    analysis_summary = llm_data.get("summary", "")[:2000]
    law_summary = llm_data.get("law_summary", "")[:3000]
    risks_json_str = json.dumps(llm_data, ensure_ascii=False)
    d1_exec("raw_sql", {
        "sql": """UPDATE law_versions SET analysis_summary=?, risks_json=?
                  WHERE law_id=? AND text_hash=?""",
        "params": [analysis_summary, risks_json_str, bill_id, all_pdf_hash],
    })

    d1_exec("risk", {
        "bill_id": bill_id,
        "bill_number": bill_number,
        "overall_score": 100 if risk_level == "high" else 70 if risk_level == "medium" else 30 if risk_level == "low" else 0,
        "model_used": LLM_MODEL,
        "json_data": json.dumps(llm_data, ensure_ascii=False),
        "raw_analysis": law_summary or analysis_summary,
        "insufficient_text": 1 if llm_data.get("insufficient_text", False) else 0,
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
    """Головна функція: моніторинг законів з LLM-аналізом."""
    force = "--force" in sys.argv
    test_mode = "--test" in sys.argv

    if not LLM_API_KEY:
        log.error("LLM_API_KEY не встановлено")
        return

    log.info("=== RAG Monitor (chunked) %s ===", __import__("datetime").datetime.now())

    bills_raw = find_bills_needing_rag(limit=20)
    log.info("Bills to analyze: %d", len(bills_raw))

    if not bills_raw:
        log.info("Nothing to do.")
        return

    processed = []
    status_updates = []

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
                info, data = process_bill(bill_info, test_mode)
                if data:
                    processed.append((info, data))
                    mark_notified([info["id"]])
                    log.info("  Text changed — full analysis sent")
                else:
                    status_updates.append(bill_info)
                    mark_notified([bill_info["id"]])
                    log.info("  Text unchanged — status notification only")
            else:
                info, data = process_bill(bill_info, test_mode)
                if data:
                    processed.append((info, data))
                    mark_notified([info["id"]])
        except Exception as e:
            log.exception("Error processing bill #%s", bill_info.get("bill_number"))

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


if __name__ == "__main__":
    main()
