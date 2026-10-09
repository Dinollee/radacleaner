"""Telegram сповіщення — відправка повідомлень про нові закони та ризики."""
import asyncio
import html
import logging

from telegram import Bot

from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

TG_LIMIT = 4000  # ліміт Telegram на одне повідомлення


def esc(text):
    """Екранує лише &, <, > — цього досить для тексту всередині HTML-розмітки Telegram."""
    return html.escape(str(text or ""), quote=False)


def format_citizen_impact(impact):
    """citizen_impact (risk_assessments.json_data) -> HTML-блоки для Telegram.

    Структура як на дашборді: заголовок, пояснення, нумеровані зміни «Було / Стане».
    Текст не обрізаємо: довжину тримає LLM-промпт, а довгий перелік ділиться
    на кілька повідомлень через chunk_blocks().

    Кольори: «Було» —  (сірий кружок, пройдеш), «Стане» — 🟢 (зелений, майбутнє).
    Telegram не підтримує <font>, тому використовуємо емодзі.
    """
    if not isinstance(impact, dict):
        return []
    if not impact.get("affects_citizens"):
        reason = impact.get("no_impact_reason")
        return [f"ℹ️ <b>Не впливає на громадян:</b> {esc(reason)}"] if reason else []

    head = ["👥 <b>Що зміниться для громадянина</b>"]
    if impact.get("headline"):
        head.append(esc(impact["headline"]))
    blocks = ["\n".join(head)]

    for i, ch in enumerate(impact.get("changes") or [], 1):
        lines = [f"<b>{i}. {esc(ch.get('who') or '—')}</b>"]
        if ch.get("before"):
            lines.append(f"<b>⚪ Було:</b> {esc(ch['before'])}")
        if ch.get("after"):
            lines.append(f"<b>🟢 Стане:</b> {esc(ch['after'])}")
        blocks.append("\n".join(lines))

    blocks.append("<i>пояснення згенеровано ШІ за текстом закону</i>")
    return blocks


def chunk_blocks(blocks, limit=TG_LIMIT):
    """Склеює HTML-блоки у повідомлення ≤ limit. Блок ніколи не розрізається навпіл."""
    messages, cur = [], ""
    for block in blocks:
        cand = f"{cur}\n\n{block}" if cur else block
        if cur and len(cand) > limit:
            messages.append(cur)
            cur = block
        else:
            cur = cand
    if cur:
        messages.append(cur)
    return messages


async def _send_async(text: str, chat_id: str, token: str) -> bool:
    """Асинхронна відправка одного повідомлення."""
    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text=text[:4000], parse_mode="HTML", disable_web_page_preview=True)
    return True


def send_message(text: str, chat_id: str | None = None) -> bool:
    """Синхронна відправка повідомлення в Telegram.

    Args:
        text: Текст повідомлення (до 4000 символів, з HTML-розміткою).
        chat_id: ID чату (за замовчуванням з конфіга).

    Returns:
        True якщо успішно, False при помилці.
    """
    token = TELEGRAM_TOKEN
    cid = chat_id or TELEGRAM_CHAT_ID

    if not token:
        log.warning("TELEGRAM_TOKEN не встановлено — повідомлення не відправлено")
        return False
    if not cid:
        log.warning("TELEGRAM_CHAT_ID не встановлено — повідомлення не відправлено")
        return False

    try:
        asyncio.run(_send_async(text, cid, token))
        log.info("Telegram повідомлення відправлено (chat=%s)", cid)
        return True
    except Exception as e:
        log.error("Помилка відправки Telegram: %s: %s", type(e).__name__, str(e)[:200])
        return False