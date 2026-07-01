#!/usr/bin/env python3
"""Telegram bot — інтерактивний інтерфейс для моніторингу законопроектів."""
import logging
import re

import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger("tg_bot")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"


def db_query(sql, params=None):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(sql, params or [])
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# --- Command handlers ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📜 Закон за номером", callback_data="bill_search")],
        [InlineKeyboardButton("🏆 Топ депутатів", callback_data="top_deputies")],
        [InlineKeyboardButton("🇪🇺 Євроінтеграція", callback_data="eu_top")],
        [InlineKeyboardButton("ℹ️ Допомога", callback_data="help")],
    ]
    reply = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🛡 <b>Страж Демократії</b>\n\n"
        "Моніторинг законопроектів Верховної Ради IX скликання.\n\n"
        "Оберіть дію:",
        reply_markup=reply,
        parse_mode="HTML",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Команди бота:</b>\n\n"
        "/bill <i>номер</i> — інформація про закон\n"
        "/top — топ депутатів за KPI\n"
        "/eu — топ за євроінтеграцією\n"
        "/help — це повідомлення\n\n"
        "Або скористайтесь кнопками нижче.",
        parse_mode="HTML",
    )


async def cmd_bill(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обробка /bill <номер>."""
    text = update.message.text or ""
    match = re.search(r"/bill\s+(\S+)", text)
    if not match:
        await update.message.reply_text("Введіть номер закону. Приклад: /bill 14332")
        return
    await send_bill_info(update, match.group(1))


async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = db_query(
        "SELECT name, faction, kpi_score, lei, eu_integration_score "
        "FROM mps WHERE (end_date IS NULL OR end_date = '') AND kpi_score > 0 "
        "ORDER BY kpi_score DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("Дані відсутні.")
        return
    lines = ["🏆 <b>Топ-10 депутатів за KPI:</b>\n"]
    for i, r in enumerate(rows, 1):
        eu = f"🇪🇺 {r['eu_integration_score']:.0f}" if r.get("eu_integration_score", 0) > 0 else ""
        lines.append(f"{i}. <b>{r['name']}</b> ({r['faction']}) — KPI {r['kpi_score']:.1f} {eu}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_eu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = db_query(
        "SELECT name, faction, eu_integration_score, eu_euro_bills, eu_state_aid_bills "
        "FROM mps WHERE (end_date IS NULL OR end_date = '') AND eu_integration_score > 0 "
        "ORDER BY eu_integration_score DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("Дані відсутні.")
        return
    lines = ["🇪🇺 <b>Топ-10 за євроінтеграцією:</b>\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i}. <b>{r['name']}</b> ({r['faction']}) — {r['eu_integration_score']:.1f} "
            f"(ЄС: {r['eu_euro_bills']}, допомога: {r['eu_state_aid_bills']})"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# --- Bill info ---

async def send_bill_info(update, bill_number):
    """Шукає закон за номером та відправляє інформацію."""
    # Search by bill_number or id
    rows = db_query(
        "SELECT b.id, b.bill_number, b.title, b.current_status, b.stage, "
        "b.toxicity, b.is_urgent, b.is_euro, b.url, b.agenda_category "
        "FROM bills b WHERE b.bill_number = %s OR b.id = %s::integer",
        [bill_number, bill_number],
    )
    if not rows:
        # Try partial match
        rows = db_query(
            "SELECT b.id, b.bill_number, b.title, b.current_status, b.stage, "
            "b.toxicity, b.is_urgent, b.is_euro, b.url, b.agenda_category "
            "FROM bills b WHERE b.bill_number ILIKE %s LIMIT 1",
            [f"%{bill_number}%"],
        )
    if not rows:
        await update.message.reply_text(f"❌ Закон #{bill_number} не знайдено.")
        return

    b = rows[0]
    bill_id = b["id"]

    # Risk assessment
    risks = db_query(
        "SELECT significance, impact, risk_score, toxicity, risk_level, "
        "raw_response FROM risk_assessments WHERE bill_id = %s LIMIT 1",
        [bill_id],
    )

    # Authors
    authors = db_query(
        "SELECT mp_name FROM bill_sponsors WHERE bill_id = %s ORDER BY sponsor_order LIMIT 5",
        [bill_id],
    )

    # Format message
    lines = []
    url = b.get("url", "")
    title = (b.get("title") or "")[:120]
    if url:
        lines.append(f"📜 <b>#{b['bill_number']}</b> — <a href='{url}'>{title}</a>")
    else:
        lines.append(f"📜 <b>#{b['bill_number']}</b> — {title}")

    # Status + stage
    stage = b.get("stage", 0) or 0
    bar = "█" * stage + "░" * (5 - stage)
    status = b.get("current_status", "Невідомо")
    lines.append(f"📊 Статус: <b>{status}</b> | Стадія: {bar} {stage}/5")

    # Tags
    tags = []
    if b.get("is_urgent"):
        tags.append("⚡ Терміновий")
    if b.get("is_euro"):
        tags.append("🇪🇺 ЄС-інтеграція")
    if tags:
        lines.append(f"🏷 {' | '.join(tags)}")

    # Category
    if b.get("agenda_category"):
        lines.append(f"📂 {b['agenda_category']}")

    # Risk assessment
    if risks:
        r = risks[0]
        risk_level = r.get("risk_level") or "—"
        tox = r.get("toxicity")
        tox_str = f"toxicity={tox:.2f}" if tox else ""
        emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}.get(risk_level, "⚪")
        lines.append(f"\n⚠️ <b>Ризик:</b> {emoji} {risk_level} {tox_str}")

        # Summary from raw_response
        if r.get("raw_response"):
            try:
                import json
                raw = json.loads(r["raw_response"]) if isinstance(r["raw_response"], str) else r["raw_response"]
                summary = raw.get("summary", "")
                if summary:
                    lines.append(f"📝 {summary[:300]}")
                detailed = raw.get("detailed_risks", [])
                if detailed:
                    lines.append("\n<b>Ризики:</b>")
                    for dr in detailed[:3]:
                        lines.append(f"  • {dr[:150]}")
            except Exception:
                pass
    else:
        lines.append("\n⚠️ Аналіз: не проведено")

    # Authors
    if authors:
        names = [a["mp_name"] for a in authors if a.get("mp_name")]
        if names:
            lines.append(f"\n👤 <b>Автори:</b> {', '.join(names[:3])}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# --- Callback handlers (inline buttons) ---

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "bill_search":
        await query.edit_message_text(
            "📜 Введіть номер закону:\n\nПриклад: <code>14332</code>",
            parse_mode="HTML",
        )
        ctx.user_state = "awaiting_bill_number"
        return

    if query.data == "top_deputies":
        rows = db_query(
            "SELECT name, faction, kpi_score FROM mps "
            "WHERE (end_date IS NULL OR end_date = '') AND kpi_score > 0 "
            "ORDER BY kpi_score DESC LIMIT 10"
        )
        lines = ["🏆 <b>Топ-10 за KPI:</b>\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. <b>{r['name']}</b> ({r['faction']}) — {r['kpi_score']:.1f}")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        return

    if query.data == "eu_top":
        rows = db_query(
            "SELECT name, faction, eu_integration_score FROM mps "
            "WHERE (end_date IS NULL OR end_date = '') AND eu_integration_score > 0 "
            "ORDER BY eu_integration_score DESC LIMIT 10"
        )
        lines = ["🇪🇺 <b>Топ-10 за євроінтеграцією:</b>\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. <b>{r['name']}</b> ({r['faction']}) — {r['eu_integration_score']:.1f}")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        return

    if query.data == "help":
        await cmd_help(update, ctx)
        return


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обробка текстових повідомлень (номер закону)."""
    state = getattr(ctx, "user_state", None)
    if state == "awaiting_bill_number":
        ctx.user_state = None
        text = update.message.text.strip()
        if re.match(r"^\d+(-д)?$", text):
            await send_bill_info(update, text)
        else:
            await update.message.reply_text("Введіть коректний номер (наприклад: 14332)")


def main():
    token = os.getenv("TG_BOT_TOKEN", "")
    if not token:
        print("TG_BOT_TOKEN not set!")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("bill", cmd_bill))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("eu", cmd_eu))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Register bot commands menu
    import asyncio
    bot = Bot(token=token)
    asyncio.run(bot.set_my_commands([
        BotCommand("start", "Запустити бота"),
        BotCommand("bill", "Інформація про закон (напр. /bill 14332)"),
        BotCommand("top", "Топ депутатів за KPI"),
        BotCommand("eu", "Топ за євроінтеграцією"),
        BotCommand("help", "Довідка"),
    ]))

    print("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
