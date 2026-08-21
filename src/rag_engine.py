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
from .llm_client import llm_completion, llm_completion_raw
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


def _is_українською(text: str) -> bool:
    """Перевіряє чи текст українською мовою (а не англійською)."""
    if not text:
        return True  # порожній текст — пропускаємо
    # Ukrainian characters: а-я, ї, є, ґ
    ukr_chars = sum(1 for c in text if c in 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ')
    eng_chars = sum(1 for c in text if c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
    total = ukr_chars + eng_chars
    if total == 0:
        return True  # немає літер — пропускаємо
    return ukr_chars / total > 0.3  # хоча б 30% українських літер
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
    "Аналізуєш законопроекти по частинах (чанках). Зберігай контекст попередніх частин. "
    "ВІДПОВІДАЙ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ. Жодних англійських слів чи речень у відповідях."
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

ЕТАП 3: ПОЧАТКОВІ КІЛЬКІСНІ ОЦІНКИ
Навіть на першому чанку почни формувати три оцінки від 1 до 5:

3А. ЗНАЧИМНІСТЬ (significance) — масштаб охоплення:
  1 — Вузькогруповий (окремі установи, кадрові зміни, перейменування)
  2 — Галузевий локальний (1-3 підприємства, окрема професійна група)
  3 — Галузевий широкий (ціла галузь: IT, сільгосп, енергетика; велика соцгрупа)
  4 — Міжгалузевий (кілька галузей, значна частина населення)
  5 — Загальнонаціональний (всі громадяни: податки, мобілізація, воєнний стан)

3Б. ВПЛИВ (impact) — наскільки ламає поточні процеси:
  1 — Декларативний (дні пам'яті, стратегії без санкцій)
  2 — Помірний м'який (нові процедури звітності, зміна термінів, дрібні штрафи)
  3 — Помірний жорсткий (нові регуляції, штрафи, зміна бізнес-процесів, ліцензії)
  4 — Сильний (значні штрафи, ліквідація органів, обмеження діяльності, нові контролюючі органи)
  5 — Екстремальний (кримінальна відповідальність, блокування платформ, конфіскація, заборона галузей)

3В. РИЗИКИ (risk) — побічні ефекти:
  1 — Мінімальні
  2 — Низькі (технічні колізії, невизначеність термінів без наслідків)
  3 — Середні (конфлікт з підзаконними актами, ризик корупції, нечіткі критерії)
  4 — Високі (конфлікт з МВФ/ЄС/OECD, дірка в бюджеті, неможливість контролю, новий орган без нагляду)
  5 — Критичні (суперечність Конституції/КПЛ, колапс галузі, втрата міжнародної допомоги)

Правила:
- Не вигадуй наслідки — аналізуй лише наведений текст.
- Кожен ризик ПОВИНЕН містити посилання на конкретну статтю/пункт.
- Абстрактні/надумані ризики без конкретики — НЕ включай.
- Оборона/безпека/воєнний стан/окуповані території — контекст знижує оцінки (1-2), але НЕ ігноруй ризики. Перевіряй зловживання.
- Звичайні процедурні закони → significance=1, impact=1, risk=1. Критичні процедурні (КСУ, бюджет, ратифікація, НБУ) — оцінюй окремо.

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "is_procedural": true/false,  // true = ТІЛЬКИ ординарні процедурні (технічні правки, зміна назви комітету). Критичні процедурні = false
  "classification_reason": "Стисле пояснення",
  "has_risks": true/false,
  "significance": 1-5,
  "impact": 1-5,
  "risk": 1-5,
  "chunk_risks": [
    "Конкретний ризик: посилання на статтю + опис наслідків"
  ],
  "chunk_summary": "Що містить ця частина тексту (1-2 речення)"
}}

Текст (чанк {chunk_num}/{total_chunks}):
{text}"""

CHUNK_N_PROMPT = """Тобі надано НАСТУПНУ частину тексту законопроєкту Верховної Ради України (чанк {chunk_num}/{total_chunks}).

Попередній контекст (чанки 1-{prev_num}):
 - Попередні оцінки: significance={prev_significance}, impact={prev_impact}, risk={prev_risk}
{prev_context}

АНАЛІЗ ЦІЄЇ ЧАСТИНИ:
Проаналізуй цей чанк на наявність ризиків. Порівняй з попереднім контекстом — чи додає ця частина нові ризики або змінює оцінку?

Оновлюй кількісні оцінки якщо новий чанк дає підстави:
- significance — може зрости якщо виявляться ширші наслідки
- impact — може зрости якщо виявляться жорсткіші механізми
- risk — може зрости якщо виявляться додаткові побічні ефекти

Правила:
- Не дублюй ризики з попередніх чанків
- Додавай ТІЛЬКИ нові знахідки з посиланнями на конкретні статті
- Абстрактні ризики без конкретики — НЕ включай
- Якщо нових ризиків немає — chunk_risks: [], оцінки не змінюй

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "has_risks": true/false,
  "significance": 1-5,
  "impact": 1-5,
  "risk": 1-5,
  "chunk_risks": [
    "Новий ризик: посилання на норму (статтю/пункт + назва базового закону) + опис"
  ],
  "chunk_summary": "Що містить ця частина (1-2 речення)"
}}

Текст (чанк {chunk_num}/{total_chunks}):
{text}"""

FINAL_PROMPT = """Аналіз законопроєкту завершено. Ось всі зібрані дані з {total_chunks} чанків:

{all_summaries}

Ризики, знайдені в різних чанках:
{all_risks}

Зібрані оцінки з чанків: significance={max_significance}, impact={max_impact}, risk={max_risk}

ФІНАЛЬНА ОЦІНКА — підсумуй всі знахідки в один JSON.

ВАЖЛИВО: Ризики ПОВИННІ бути згруповані в 3-5 ДИНАМІЧНИХ НАПРЯМКІВ за тематикою.
Наприклад:
- "Конституційні невідповідності" (якщо є порушення Конституції)
- "Бюджетний дисбаланс" (якщо є фінансові ризики)
- "Корупційні фактори та нечіткі поняття" (якщо є дискреційні повноваження)
- "Невідповідність чинному законодавству" (якщо є колізії з кодексами)
- "Обмеження прав та свобод" (якщо є звуження прав)
Назви напрямків — ДИНАМІЧНІ, залежать від конкретного закону. Не використовуй шаблонні назви.

КІЛЬКІСНІ ОЦІНКИ — фінальні значення трьох метрик (1-5):

significance (значимість для суспільства):
  1 — Вузькогруповий  →  3 — Галузевий  →  5 — Загальнонаціональний
impact (глибина впливу):
  1 — Декларативний  →  3 — Помірний жорсткий  →  5 — Екстремальний
risk (побічні ризики):
  1 — Мінімальні  →  3 — Середні  →  5 — Критичні

significance, impact, risk — фінальні цілі числа 1-5.
risk_level та toxicity будуть розраховані сервером — НЕ повертай їх у JSON.

ФОРМАТ ВІДПОВІДІ (строго JSON):
{{
  "is_procedural": true/false,  // true = ТІЛЬКИ ординарні процедурні (технічні правки, зміна назви комітету). Критичні процедурні = false
  "classification_reason": "Підсумкове пояснення класифікації",
  "has_risks": true/false,
  "summary": "Стисле опис суті змін (1-2 речення)",
  "law_summary": "Повний опис: хто ініціює, що змінює, на кого поширюється. 3-5 речень.",
  "significance": 1-5,
  "impact": 1-5,
  "risk": 1-5,
  "risk_categories": [
    {{
      "category": "Назва напрямку (динамічна, за тематикою)",
      "risks": [
    "Конкретний ризик: посилання на статтю/пункт законопроєкту або змінюваного закону (із назвою) + опис наслідків"
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
- detailed_risks — плоский список для зворотної сумісності (ті самі що в risk_categories)
- Кількість категорій залежить від закону: від 1 до 5
- significance, impact, risk — фінальні цілі числа 1-5
- Процедурні → significance=1, impact=1, risk=1 (тільки ординарні; критичні процедурні оцінюй за критеріями)"""


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

    # === ТОКСИЧНІСТЬ ІНДЕКС ===
    significance = data.get("significance", 0)
    impact = data.get("impact", 0)
    risk = data.get("risk", 0)
    toxicity = data.get("toxicity", 0)
    
    if significance and impact and risk:
        if toxicity == 0:
            toxicity = significance * impact * risk / 125
        toxicity = min(max(toxicity, 0.0), 1.0)
        # Візуальна шкала токсичності
        blocks = min(int(toxicity * 10), 10)
        tox_bar = "█" * blocks + "░" * (10 - blocks)
        tox_emoji = "🔴" if toxicity >= 0.73 else "🟠" if toxicity >= 0.49 else "🟡" if toxicity >= 0.25 else "🟢"
        lines.append(
            f"\n{tox_emoji} <b>Токсичність: {toxicity:.2f}</b> {tox_bar}"
        )
        lines.append(
            f"📐 Значимість: {significance}/5 | Вплив: {impact}/5 | Ризики: {risk}/5"
        )

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
                    for i, risk_item in enumerate(cat_risks[:3], 1):
                        lines.append(f"  {i}. {risk_item[:180]}")
                    if len(cat_risks) > 3:
                        lines.append(f"  ... та ще {len(cat_risks) - 3}")
        else:
            for i, risk_item in enumerate(detailed_risks[:5], 1):
                lines.append(f"\n{i}. {risk_item[:200]}")
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


MAX_CHUNKS = 30  # макс. чанків (60K × 30 = 1.8M символів; при >30 — рівномірний відбір)


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


def _chunked_llm_analysis(full_text: str, total_chunks: int, provider: str | None = None) -> dict | None:
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
    
    # Трекінг кількісних оцінок — беремо максимум з усіх чанків
    max_significance = 1
    max_impact = 1
    max_risk = 1

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
                result = llm_completion(prompt, system_prompt=SYSTEM_PROMPT, max_tokens=8000, provider=provider)
            except Exception as e:
                log.error("  LLM error on chunk %d: %s", chunk_num, str(e)[:200])
                return None

            all_chunk_results.append(result)
            all_risks.extend(result.get("chunk_risks", []))
            all_summaries.append(result.get("chunk_summary", ""))
            
            # Збираємо оцінки
            max_significance = max(max_significance, result.get("significance", 1))
            max_impact = max(max_impact, result.get("impact", 1))
            max_risk = max(max_risk, result.get("risk", 1))

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
            # Наступні чанки — продовження з контекстом + оцінками
            prev_context = "\n".join(
                f"[Чанк {j+1}]: {s}" for j, s in enumerate(all_summaries)
            )
            prompt = CHUNK_N_PROMPT.format(
                chunk_num=chunk_num,
                total_chunks=actual_chunks,
                prev_num=i,
                prev_context=prev_context[:3000],
                prev_significance=max_significance,
                prev_impact=max_impact,
                prev_risk=max_risk,
                text=chunk,
            )

            messages.append({"role": "user", "content": prompt})

            # ponytail: обрізаємо історію щоб не перевищити контекстне вікно (262K tokens)
            # Залишаємо: system + останні 4 пари (user+assistant) = 9 повідомлень
            MAX_HISTORY_MESSAGES = 9  # system + 4 * (user + assistant)
            if len(messages) > MAX_HISTORY_MESSAGES:
                messages = [messages[0]] + messages[-(MAX_HISTORY_MESSAGES - 1):]

            try:
                result = llm_completion(
                    prompt=None,
                    system_prompt=SYSTEM_PROMPT,
                    messages=messages,
                    max_tokens=8000,
                    provider=provider,
                )
            except Exception as e:
                log.error("  LLM error on chunk %d: %s", chunk_num, str(e)[:200])
                return None

            all_chunk_results.append(result)
            all_risks.extend(result.get("chunk_risks", []))
            all_summaries.append(result.get("chunk_summary", ""))
            
            # Оновлюємо максимальні оцінки
            max_significance = max(max_significance, result.get("significance", max_significance))
            max_impact = max(max_impact, result.get("impact", max_impact))
            max_risk = max(max_risk, result.get("risk", max_risk))

            # Додаємо відповідь асистента в історію
            messages.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})

        time.sleep(3)  # пауза між чанками

    # Фінальна агрегація
    log.info("  Final aggregation: %d risks found across %d chunks, sig=%d/imp=%d/risk=%d", 
             len(all_risks), actual_chunks, max_significance, max_impact, max_risk)

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
        max_significance=max_significance,
        max_impact=max_impact,
        max_risk=max_risk,
    )

    messages.append({"role": "user", "content": final_prompt})

    try:
        final_result = llm_completion(
            prompt=None,
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
            max_tokens=8000,
            provider=provider,
        )
    except Exception as e:
        log.error("  LLM final aggregation error: %s", str(e)[:200])
        return None

    final_result["analyzed_chunks"] = list(range(1, actual_chunks + 1))
    
    # Гарантуємо наявність кількісних оцінок
    if "significance" not in final_result:
        final_result["significance"] = max_significance
    if "impact" not in final_result:
        final_result["impact"] = max_impact
    if "risk" not in final_result:
        final_result["risk"] = max_risk
    
    # Автоматичний розрахунок toxicity якщо LLM не повернув
    sig = final_result.get("significance", 1)
    imp = final_result.get("impact", 1)
    rsk = final_result.get("risk", 1)
    if "toxicity" not in final_result:
        final_result["toxicity"] = round(sig * imp * rsk / 125, 2)
    
    # Консистентність risk_level за математичною формулою
    rsk_val = final_result.get("risk", 1)
    sig_val = final_result.get("significance", 1)
    imp_val = final_result.get("impact", 1)
    if not final_result.get("is_procedural"):
        product = sig_val * imp_val
        if rsk_val >= 4 or (rsk_val == 3 and product >= 12):
            final_result["risk_level"] = "high"
        elif rsk_val == 3 or (rsk_val == 2 and product >= 12):
            final_result["risk_level"] = "medium"
        else:
            final_result["risk_level"] = "low"
    
    return final_result


def _build_fallback_summaries(data: dict, bill_number: str = "") -> None:
    """Заповнює summary/law_summary з detailed_risks, ЛИШЕ якщо модель їх пропустила.

    Ставить data["summary_source"]: "llm" | "fallback" | "none".
    "none" — немає ні відповіді моделі, ні матеріалу для склейки (процедурні).
    """
    import re

    has_summary = bool(data.get("summary", "").strip())
    has_law_summary = bool(data.get("law_summary", "").strip())
    if has_summary and has_law_summary:
        data["summary_source"] = "llm"
        return

    risks = [r.strip() for r in data.get("detailed_risks", []) or [] if isinstance(r, str) and r.strip()]
    if not risks:
        data["summary_source"] = "llm" if (has_summary or has_law_summary) else "none"
        return

    risks_text = " ".join(risks[:3])
    if not has_law_summary:
        intro = f"Проєкт закону №{bill_number}. " if bill_number else ""
        data["law_summary"] = f"{intro}{risks_text}"[:3000]
    if not has_summary:
        sentences = re.split(r"(?<=[.!?])\s+", risks_text)
        data["summary"] = " ".join(sentences[:2])[:2000]
    data["summary_source"] = "fallback"


def _fix_discretion_hallucination(data: dict):
    """Post-verification: catch 'discretion' hallucination when law actually grants selective preferences.

    If the law imperatively exempts specific entities from enforcement,
    the model may incorrectly write 'discretionary authority of controlling body'.
    This function detects and corrects that pattern.
    """
    import re

    law = (data.get("law_summary", "") + " " + data.get("summary", "")).lower()
    risks = data.get("detailed_risks", [])
    categories = data.get("risk_categories", [])

    # Pattern: imperative exemption for specific entities
    imperative_patterns = [
        r"звільняє.*від.*стягнення",
        r"не здійснюють заходи",
        r"зупиняє.*стягнення",
        r"призупиняє.*борг",
        r"звільнити.*від.*зобов'язань",
    ]
    has_imperative = any(re.search(p, law) for p in imperative_patterns)

    # Pattern: model incorrectly claims discretion
    discretion_pattern = re.compile(r"дискреційн\w*\s+(право|повноваження|простір)", re.IGNORECASE)

    if not has_imperative:
        return

    fixed = False
    template = (
        "Створення вибіркових законодавчих преференцій для окремих суб'єктів господарювання, "
        "що порушує принцип рівності платників податків (ст. 4 ПК України) "
        "та створює дискримінаційні умови."
    )

    for i, risk in enumerate(risks):
        if discretion_pattern.search(risk):
            log.info("  POST_VERIFY: fixing discretion hallucination in detailed_risks[%d]", i)
            # Keep the original norm reference if present
            norm_ref = re.search(r"((?:ст|п|пп)\.\s*[\d\.\s,–]+)", risk)
            prefix = f"({norm_ref.group(0)}) " if norm_ref else ""
            risks[i] = prefix + template
            fixed = True

    for cat in categories:
        cat_risks = cat.get("risks", [])
        for i, risk in enumerate(cat_risks):
            if discretion_pattern.search(risk):
                log.info("  POST_VERIFY: fixing discretion hallucination in risk_categories")
                cat_risks[i] = template
                fixed = True

    if fixed:
        # Also check for English terms and flag them
        eng_pattern = re.compile(r"\b(preferential treatment|arrears|enforcement|moratorium)\b", re.IGNORECASE)
        ukr_replacements = {
            "preferential treatment": "вибіркове ставлення",
            "arrears": "заборгованість",
            "enforcement": "стягнення",
            "moratorium": "мораторій",
        }
        for i, risk in enumerate(risks):
            for eng, ukr in ukr_replacements.items():
                if eng_pattern.search(risk):
                    risks[i] = re.sub(eng, ukr, risks[i], flags=re.IGNORECASE)
                    log.info("  POST_VERIFY: replaced English term '%s' with '%s'", eng, ukr)


def process_bill(info: dict, test_mode: bool = False, provider: str | None = None):
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
    llm_data = _chunked_llm_analysis(full_text, len(chunk_text(full_text)), provider=provider)

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
        sig = llm_data.get("significance", 1)
        imp = llm_data.get("impact", 1)
        rsk = llm_data.get("risk", 1)
        log.info("  Classification: ПРОЦЕДУРНИЙ — %s (sig=%d imp=%d risk=%d)",
                 llm_data.get("classification_reason", "")[:60], sig, imp, rsk)
        llm_data["has_risks"] = False
        llm_data["risk_level"] = None
        llm_data["detailed_risks"] = []
    else:
        sig = llm_data.get("significance", 0)
        imp = llm_data.get("impact", 0)
        rsk = llm_data.get("risk", 0)
        tox = llm_data.get("toxicity", 0)
        log.info("  Classification: НЕПРОЦЕДУРНИЙ — risk_level=%s risks=%d sig=%d imp=%d risk=%d tox=%.2f",
                 risk_level, len(llm_data.get("detailed_risks", [])), sig, imp, rsk, tox)

    # Post-verification: fix discretion hallucination
    _fix_discretion_hallucination(llm_data)

    # Post-verification: check language (українська vs англійська)
    summary_text = llm_data.get("summary", "") + llm_data.get("law_summary", "")
    if not _is_українською(summary_text) and not is_procedural:
        log.warning("  Language check FAILED — analysis in English, retrying with посиленим промптом")
        try:
            # Повторний аналіз з посиленим промптом про мову
            llm_data2 = _chunked_llm_analysis(full_text, len(chunk_text(full_text)), provider=provider)
            if llm_data2 and _is_українською(llm_data2.get("summary", "") + llm_data2.get("law_summary", "")):
                log.info("  Language retry SUCCESS — now in Ukrainian")
                llm_data = llm_data2
                llm_data["model_used"] = LLM_MODEL
                is_procedural = llm_data.get("is_procedural", False)
                risk_level = llm_data.get("risk_level")
                has_risks = llm_data.get("has_risks", False)
                _fix_discretion_hallucination(llm_data)
            else:
                log.warning("  Language retry FAILED again — keeping original")
        except Exception as e:
            log.error("  Language retry error: %s", str(e)[:200])

    # Fallback: заповнити summary/law_summary, якщо модель їх пропустила (з маркером summary_source)
    _build_fallback_summaries(llm_data, bill_number)

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
            time.sleep(3)

        for info, data in processed:
            msg = format_risk_message(info, data)
            send_message(msg)
            log.info("  Sent to TG: #%s", info["bill_number"])
            time.sleep(3)

    log.info(
        "=== Done: %d analyzed, %d status updates ===",
        len(processed),
        len(status_updates),
    )


if __name__ == "__main__":
    main()
