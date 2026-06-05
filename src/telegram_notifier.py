"""Telegram бот для відправки сповіщень."""
import os
import asyncio
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_telegram_async(text: str, chat_id: str = None):
    """Асинхронна відправка повідомлення в Telegram."""
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN not set")
        return False
    bot = Bot(token=TELEGRAM_TOKEN)
    cid = chat_id or TELEGRAM_CHAT_ID
    if not cid:
        print("TELEGRAM_CHAT_ID not set")
        return False
    await bot.send_message(chat_id=cid, text=text[:4000], parse_mode='HTML')
    return True


def send_telegram(text: str, chat_id: str = None):
    """Синхронна обгортка для відправки в Telegram."""
    try:
        asyncio.run(send_telegram_async(text, chat_id))
        print("Sent to TG")
        return True
    except Exception as e:
        print(f"TG error: {e}")
        return False
