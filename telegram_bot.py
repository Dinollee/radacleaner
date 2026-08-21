#!/usr/bin/env python3
"""Telegram bot — інтерактивний інтерфейс для моніторингу законопроектів."""
import logging
import re
import json

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


def db_exec(sql, params=None):
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute(sql, params or [])
    conn.commit()
    cur.close()
    conn.close()


# --- Info attacks / fakes formatters (чистые, покрыты тестами) ---

def format_attacks(alerts):
    """attack_alerts rows -> український текст. Пусто -> спокійний стейт."""
    if not alerts:
        return "✅ <b>Синхронних хвиль не зафіксовано.</b>\n\nЦе добре — координованих кампаній зараз не видно."
    lines = ["🚨 <b>Зафіксовані синхронні хвилі:</b>\n"]
    for a in alerts:
        d = a.get("detected_at")
        when = d.strftime("%d.%m %H:%M") if d else ""
        lines.append(
            f"🚨 <b>{(a.get('label') or 'нарратив')[:80]}</b>\n"
            f"   {a.get('posts_count', 0)} постів × {a.get('channels_count', 0)} каналів · {when}")
        if a.get("debunk_url"):
            lines.append(f"   🔎 <a href='{a['debunk_url']}'>спростування ↗</a>")
    lines.append("\n<i>Ознаки скоординованої хвилі; вердикт — за фактчекерами.</i>")
    return "\n".join(lines)


def format_fakes(digest):
    """info_digest dict -> ТОП перевірок фактчекерів. None/пусто -> заглушка."""
    fakes = (digest or {}).get("fakes") or []
    if not fakes:
        return "🧪 <b>ТОП перевірок фактчекерів</b>\n\nЗа останню добу розборів немає — зазирни пізніше."
    lines = ["🧪 <b>ТОП перевірок фактчекерів (24г):</b>\n"]
    for i, f in enumerate(fakes[:10], 1):
        src = f.get("source") or ""
        line = f"{i}. {(f.get('one_line') or f.get('title') or '')[:180]}"
        if src:
            line = f"{i}. [{src}] {(f.get('one_line') or f.get('title') or '')[:170]}"
        if f.get("url"):
            line += f"\n   <a href='{f['url']}'>повний розбір ↗</a>"
        lines.append(line)
    return "\n".join(lines)


def build_attacks_text():
    rows = db_query(
        "SELECT label, channels_count, posts_count, debunk_url, detected_at "
        "FROM attack_alerts ORDER BY detected_at DESC LIMIT 5")
    return format_attacks(rows)


def build_fakes_text():
    rows = db_query("SELECT value FROM stats_cache WHERE key = 'info_digest' LIMIT 1")
    try:
        digest = json.loads(rows[0]["value"]) if rows else None
    except Exception:
        digest = None
    return format_fakes(digest)


def sub_keyboard(chat_id):
    row = db_query("SELECT attacks, digest FROM bot_subscribers WHERE chat_id = %s", [chat_id])
    atk = bool(row and row[0]["attacks"])
    dig = bool(row and row[0]["digest"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🚨 Атаки: {'✅ увімк' if atk else '❌ вимк'}",
                              callback_data="sub_toggle_attacks")],
        [InlineKeyboardButton(f"📰 Дайджест: {'✅ увімк' if dig else '❌ вимк'}",
                              callback_data="sub_toggle_digest")],
        [InlineKeyboardButton("🗑 Видалити мене повністю", callback_data="sub_off")],
    ])


# --- Command handlers ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📜 Закон за номером", callback_data="bill_search")],
        [InlineKeyboardButton("👤 Депутат", callback_data="dep_search")],
        [InlineKeyboardButton("🏆 Топ депутатів", callback_data="top_deputies")],
        [InlineKeyboardButton("🇪🇺 Євроінтеграція", callback_data="eu_top")],
        [InlineKeyboardButton("🚨 Інфоатаки", callback_data="ia_attacks"),
         InlineKeyboardButton("🧪 Фейкі дня", callback_data="ia_fakes")],
        [InlineKeyboardButton("🔔 Підписки", callback_data="sub_menu")],
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
        "/dep <i>ім'я</i> — профіль депутата (ІЕД)\n"
        "/top — топ депутатів за ІЕД\n"
        "/eu — топ за євроінтеграцією\n"
        "/attacks — синхронні інфохвилі\n"
        "/fakes — ТОП перевірок фактчекерів дня\n"
        "/sub — керування підписками на сповіщення\n"
        "/off — видалити себе та всі підписки\n"
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
        "SELECT name, faction, kpi_v12_score, eu_integration_score "
        "FROM mps WHERE (end_date IS NULL OR end_date = '') AND kpi_v12_score > 0 "
        "ORDER BY kpi_v12_score DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("Дані відсутні.")
        return
    lines = ["🏆 <b>Топ-10 депутатів за ІЕД:</b>\n"]
    for i, r in enumerate(rows, 1):
        eu = f"🇪🇺 {r['eu_integration_score']:.0f}" if r.get("eu_integration_score", 0) > 0 else ""
        lines.append(f"{i}. <b>{r['name']}</b> ({r['faction']}) — ІЕД {r['kpi_v12_score']:.1f} {eu}")
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


def progress_bar(value, max_val=100, length=10):
    """Generate text progress bar."""
    filled = int(value / max_val * length)
    return "█" * filled + "░" * (length - filled)


async def cmd_dep(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Профіль депутата з KPI v11."""
    text = update.message.text or ""
    match = re.search(r"/dep\s+(.+)", text)
    if not match:
        await update.message.reply_text("Введіть ім'я депутата. Приклад: /dep Юрчишин")
        return
    await send_dep_profile(update, match.group(1).strip())


async def send_dep_profile(update, name_query):
    rows = db_query(
        "SELECT id, name, faction, kpi_v11_score, kpi_v11_effectiveness, kpi_v11_discipline, "
        "kpi_v11_efficiency, kpi_v11_control, kpi_v11_quality, "
        "committee_score, shannon_diversity, eu_integration_score, eu_euro_bills, "
        "authorship_ratio, total_bills, total_laws, bill_quality_score, "
        "signal_warnings, signal_strengths, signal_features, "
        "(SELECT committee_name FROM committee_members WHERE member_uid = mps.rada_uid LIMIT 1) as committee "
        "FROM mps WHERE (end_date IS NULL OR end_date = '') "
        "AND name ILIKE %s LIMIT 1",
        [f"%{name_query}%"],
    )
    if not rows:
        await update.message.reply_text(f"❌ Депутата '{name_query}' не знайдено.")
        return

    d = rows[0]
    lines = []
    lines.append(f"═══ <b>{d['name']}</b> ({d['faction']}) ═══")

    # KPI v11
    lines.append("\n📊 <b>KPI</b>")
    lines.append(f"  Законодавство  {progress_bar(d['kpi_v11_effectiveness'])} {d['kpi_v11_effectiveness']:.0f}")
    lines.append(f"  Дисципліна     {progress_bar(d['kpi_v11_discipline'])} {d['kpi_v11_discipline']:.0f}")
    lines.append(f"  Результативн.  {progress_bar(d['kpi_v11_efficiency'])} {d['kpi_v11_efficiency']:.0f}")
    lines.append(f"  Контроль       {progress_bar(d['kpi_v11_control'])} {d['kpi_v11_control']:.0f}")
    lines.append(f"  Якість         {progress_bar(d['kpi_v11_quality'])} {d['kpi_v11_quality']:.0f}")
    lines.append(f"  <b>Загальний    {progress_bar(d['kpi_v11_score'])} {d['kpi_v11_score']:.1f}</b>")

    # Profile
    lines.append("\n📋 <b>Профіль</b>")
    if d.get("committee"):
        lines.append(f"  Комітет: {d['committee']}")
    shannon = d.get("shannon_diversity", 0) or 0
    spec = "Дуже вузька" if shannon < 2 else "Вузька" if shannon < 3 else "Середня" if shannon < 4.5 else "Широка"
    lines.append(f"  Спеціалізація: {spec} (H={shannon:.1f})")
    eu = d.get("eu_integration_score", 0) or 0
    if eu > 0:
        lines.append(f"  EU: {eu:.1f}")
    ar = d.get("authorship_ratio", 0) or 0
    style = "Індивідуальний" if ar > 0.5 else "Змішаний" if ar > 0.2 else "Колективний"
    lines.append(f"  Стиль: {style}")
    lines.append(f"  Законів: {d['total_bills']} (прийнято {d['total_laws']})")

    # Signals
    warnings = d.get("signal_warnings") or []
    strengths = d.get("signal_strengths") or []
    features = d.get("signal_features") or []

    if warnings:
        lines.append("\n⚠️ <b>Попередження</b>")
        for w in warnings:
            lines.append(f"  ⚠ {w}")
    if strengths:
        lines.append("\n✓ <b>Сильні сторони</b>")
        for s in strengths:
            lines.append(f"  ✓ {s}")
    if features:
        lines.append("\nℹ <b>Особливості</b>")
        for f in features:
            lines.append(f"  ℹ {f}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# --- Info attacks / subscriptions (v2) ---

async def cmd_attacks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        build_attacks_text(), parse_mode="HTML", disable_web_page_preview=True)


async def cmd_fakes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        build_fakes_text(), parse_mode="HTML", disable_web_page_preview=True)


async def cmd_sub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🔔 <b>Підписки на сповіщення</b>\n\n"
        "🚨 <b>Атаки</b> — пуш, коли ≥4 каналів синхронно поширюють один нарратив.\n"
        "📰 <b>Дайджест</b> — щоденне зведення проєкту у твій чат.\n\n"
        "Твій статус:",
        reply_markup=sub_keyboard(chat_id),
        parse_mode="HTML",
    )


async def cmd_off(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db_exec("DELETE FROM bot_subscribers WHERE chat_id = %s", [chat_id])
    await update.message.reply_text(
        "🗑 Тебе видалено зі списку підписників. Дані про тебе не зберігаються.\n\n"
        "Повернутись: /sub")


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
            "SELECT name, faction, kpi_v12_score FROM mps "
            "WHERE (end_date IS NULL OR end_date = '') AND kpi_v12_score > 0 "
            "ORDER BY kpi_v12_score DESC LIMIT 10"
        )
        lines = ["🏆 <b>Топ-10 за ІЕД:</b>\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. <b>{r['name']}</b> ({r['faction']}) — {r['kpi_v12_score']:.1f}")
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

    if query.data == "ia_attacks":
        await query.edit_message_text(
            build_attacks_text(), parse_mode="HTML", disable_web_page_preview=True)
        return

    if query.data == "ia_fakes":
        await query.edit_message_text(
            build_fakes_text(), parse_mode="HTML", disable_web_page_preview=True)
        return

    if query.data == "dep_search":
        await query.edit_message_text(
            "👤 Введіть прізвище депутата:\n\nПриклад: <code>Юрчишин</code>",
            parse_mode="HTML",
        )
        ctx.user_state = "awaiting_dep_name"
        return

    if query.data == "sub_menu":
        await query.edit_message_text(
            "🔔 <b>Підписки на сповіщення</b>\n\n"
            "Керуй тим, що приходитиме у твій чат:",
            reply_markup=sub_keyboard(query.message.chat_id),
            parse_mode="HTML",
        )
        return

    if query.data in ("sub_toggle_attacks", "sub_toggle_digest"):
        chat_id = query.message.chat_id
        field = "attacks" if query.data == "sub_toggle_attacks" else "digest"
        db_exec(
            f"""INSERT INTO bot_subscribers (chat_id, {field}, subscribed_at)
                VALUES (%s, true, now())
                ON CONFLICT (chat_id) DO UPDATE SET {field} = NOT bot_subscribers.{field}""",
            [chat_id])
        await query.edit_message_reply_markup(reply_markup=sub_keyboard(chat_id))
        return

    if query.data == "sub_off":
        chat_id = query.message.chat_id
        db_exec("DELETE FROM bot_subscribers WHERE chat_id = %s", [chat_id])
        await query.edit_message_text(
            "🗑 Тебе видалено зі списку підписників.\n\nПовернутись: /sub")
        return


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обробка текстових повідомлень (номер закону / ім'я депутата)."""
    state = getattr(ctx, "user_state", None)
    if state == "awaiting_bill_number":
        ctx.user_state = None
        text = update.message.text.strip()
        if re.match(r"^\d+(-д)?$", text):
            await send_bill_info(update, text)
        else:
            await update.message.reply_text("Введіть коректний номер (наприклад: 14332)")
    elif state == "awaiting_dep_name":
        ctx.user_state = None
        name = update.message.text.strip()
        if 2 <= len(name) <= 60:
            await send_dep_profile(update, name)
        else:
            await update.message.reply_text("Введіть прізвище депутата (наприклад: Юрчишин)")


def main():
    token = os.getenv("TG_BOT_TOKEN", "")
    if not token:
        print("TG_BOT_TOKEN not set!")
        return

    # PID lock — prevent multiple instances
    pid_file = Path("/tmp/telegram_bot.pid")
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        import subprocess
        try:
            subprocess.run(["kill", "-0", old_pid], check=True)
            print(f"Another bot instance running (PID {old_pid}). Exiting.")
            return
        except (subprocess.CalledProcessError, ProcessLookupError):
            pid_file.unlink()

    pid_file.write_text(str(os.getpid()))

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("bill", cmd_bill))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("eu", cmd_eu))
    app.add_handler(CommandHandler("dep", cmd_dep))
    app.add_handler(CommandHandler("attacks", cmd_attacks))
    app.add_handler(CommandHandler("fakes", cmd_fakes))
    app.add_handler(CommandHandler("sub", cmd_sub))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Register bot commands menu
    import asyncio
    bot = Bot(token=token)
    asyncio.run(bot.set_my_commands([
        BotCommand("start", "Запустити бота"),
        BotCommand("bill", "Інформація про закон"),
        BotCommand("dep", "Профіль депутата"),
        BotCommand("top", "Топ депутатів за ІЕД"),
        BotCommand("eu", "Топ за євроінтеграцією"),
        BotCommand("attacks", "Синхронні інфохвилі"),
        BotCommand("fakes", "ТОП перевірок фактчекерів дня"),
        BotCommand("sub", "Підписки на сповіщення"),
        BotCommand("off", "Видалити підписки"),
        BotCommand("help", "Довідка"),
    ]))

    print("Bot started...")
    try:
        app.run_polling(drop_pending_updates=True)
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
