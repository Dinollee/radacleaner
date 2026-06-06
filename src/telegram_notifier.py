"""Telegram сповіщення — відправка повідомлень про нові закони та ризики."""
import asyncio
import logging

from telegram import Bot

from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)


async def _send_async(text: str, chat_id: str, token: str) -> bool:
    """Асинхронна відправка одного повідомлення."""
    bot = Bot(token=token)
    await bot.send_message(chat_id=chat_id, text=text[:4000], parse_mode="HTML")
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