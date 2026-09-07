#!/usr/bin/env python3
"""Щотижневий дайджест для Telegram (щопонеділка 08:00).

Принцип: детерміновані групи (підписані / знято з розгляду / ризиковані)
+ ОДИН LLM-виклик для «ГОЛОВНЕ» — 3-5 буллетів українською, що
станеться для звичайного користувача.

Без LLM → fallback на авто-саммарі з stats (рахуємо тільки числа).

Ponytail: фоллбэк на числа — без LLM теж інформативно (юзер бачить
«12 підписаних, 149 відхилено, 5 ризиковних» замість промпт-тексту).
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.d1_client import d1_query
from src.telegram_notifier import send_message

try:
    from src.llm_client import llm_completion_raw, _parse_json
    HAS_LLM = True
except Exception:
    HAS_LLM = False

NL = chr(10)
DASHBOARD_URL = "https://radacleaner-dashboard.pages.dev"

LLM_SYS = ("Ти редактор щотижневого огляду Верховної Ради України для звичайних громадян. "
           "Відповідай ТІЛЬКИ валідним JSON без пояснень, коментарів, Markdown. "
           "Стисло, без канцеляризмів, українською.")


def fmt_date(s):
    """YYYY-MM-DD -> DD.MM.YYYY."""
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').strftime('%d.%m.%Y')
    except ValueError:
        return str(s)[:10]


def bill_url(bill_number):
    """Посилання на сторінку закону на сайті ВРУ."""
    bn = str(bill_number or '').strip()
    if not bn:
        return ''
    return f"https://itd.rada.gov.ua/billinfo/Bills/Card/?id={bn}"


def collect_week():
    """Збирає дані за тиждень. Повертає dict з усіма групами."""
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    d = {}

    # Нові законопроекти
    nb = d1_query(
        "SELECT COUNT(DISTINCT b.id) AS cnt FROM change_log cl "
        "JOIN bills b ON cl.bill_id=b.id "
        "WHERE cl.change_type='new' AND date(cl.created_at) >= ?", [week_ago])
    d['new_bills'] = nb[0]['cnt'] if nb else 0

    # Знято з розгляду (наймасовніша зміна, юзеру варто бачити)
    st = d1_query(
        "SELECT new_value, COUNT(*) AS cnt FROM change_log "
        "WHERE change_type='status_change' AND date(created_at) >= ? "
        "GROUP BY new_value ORDER BY cnt DESC LIMIT 10", [week_ago])
    d['status_breakdown'] = [(r['new_value'] or '?', r['cnt']) for r in st]

    # Підписані Президентом за тиждень (тільки закони stage=4)
    signed = d1_query(
        "SELECT b.bill_number, b.title, b.act_number, b.act_date, b.url, "
        "       ra.risk_score, ra.risk_level "
        "FROM bills b LEFT JOIN risk_assessments ra ON ra.bill_id=b.id "
        "WHERE b.stage=4 AND date(b.act_date) >= ? "
        "ORDER BY b.act_date DESC LIMIT 8", [week_ago])
    d['signed'] = signed or []

    # Знято/відхилено за тиждень — вибірка помітних (з аналізом LLM)
    removed = d1_query(
        "SELECT b.bill_number, b.title, b.stage, b.current_status, "
        "       ra.risk_score, ra.json_data "
        "FROM bills b LEFT JOIN risk_assessments ra ON ra.bill_id=b.id "
        "WHERE b.stage IN (5) AND date(b.updated_at) >= ? "
        "ORDER BY ra.risk_score DESC NULLS LAST LIMIT 6", [week_ago])
    d['rejected'] = removed or []

    # Ризиковані нові аналізи за тиждень (risk_score>=3)
    risky = d1_query(
        "SELECT b.bill_number, b.title, b.stage, b.url, "
        "       ra.risk_score, ra.risk_level, ra.toxicity, ra.json_data "
        "FROM risk_assessments ra JOIN bills b ON ra.bill_id=b.id "
        "WHERE date(ra.assessed_at) >= ? AND ra.risk_score >= 3 "
        "ORDER BY ra.risk_score DESC, ra.toxicity DESC LIMIT 5", [week_ago])
    d['risky'] = risky or []

    # Цифри загального стану
    tot = d1_query("SELECT COUNT(*) AS bills FROM bills")
    an_total = d1_query("SELECT COUNT(DISTINCT bill_id) AS a FROM risk_assessments")
    d['total_bills'] = tot[0]['bills'] if tot else 0
    d['analyzed_total'] = an_total[0]['a'] if an_total else 0

    return d


def llm_summary(d):
    """Один LLM-виклик: 3-5 буллетів «що сталося для звичайного користувача».

    ponytail: повертає список рядків. Якщо LLM не відповіла або JSON криво
    парситься → повертаємо порожній список (виклик форматування пропустить
    секцію «ГОЛОВНЕ», дайджест все одно зрозумілий завдяки групам).
    """
    if not HAS_LLM:
        return []

    payload = []
    for b in d.get('signed', [])[:5]:
        payload.append({"type": "signed", "bill": b['bill_number'],
                        "act": b.get('act_number'), "title": (b.get('title') or '')[:200]})
    for b in d.get('rejected', [])[:5]:
        payload.append({"type": "rejected", "bill": b['bill_number'],
                        "title": (b.get('title') or '')[:200]})
    for b in d.get('risky', [])[:5]:
        cats = []
        summary = ''
        if b.get('json_data'):
            try:
                j = json.loads(b['json_data']) if isinstance(b['json_data'], str) else b['json_data']
                cats = [c.get('category', '') for c in j.get('risk_categories', [])][:3]
                summary = j.get('summary', '')[:300]
            except Exception:
                pass
        payload.append({"type": "risky", "bill": b['bill_number'],
                        "risk": b.get('risk_score'), "title": (b.get('title') or '')[:200],
                        "categories": cats, "summary": summary})

    if not payload:
        return []

    prompt = (
        "На основі списку подій Верховної Ради за тиждень (підписані закони, "
        "відхилені, ризиковані за LLM-аналізом) напиши 3-5 коротких буллетів: "
        "що сталося для звичайного громадяна України. Стисло, по суті, без "
        "канцеляризнів. Не вигадуй, тільки факти з подій.\n\n"
        "Події:\n" + json.dumps(payload, ensure_ascii=False, indent=2) +
        "\n\nПоверни JSON-масив рядків: [\"буллет 1\", \"буллет 2\", ...]"
    )

    try:
        raw = llm_completion_raw(prompt, system_prompt=LLM_SYS,
                                 temperature=0.3, max_tokens=800)
    except Exception as e:
        print(f"LLM weekly summary error: {e}")
        return []

    parsed = _parse_json(raw)
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            # Мовний гейт: якщо буллет латиницею >30% або nemotron mix
            # («wprowadжує» = латинка+кирилиця в одному слові) — викидаємо
            # весь саммарі, бо LLM дав неякісний результат.
            cyr = sum(1 for c in item if '\u0400' <= c <= '\u04FF')
            lat = sum(1 for c in item if c.isascii() and c.isalpha())
            letters = cyr + lat
            if letters >= 5 and (lat / letters) >= 0.30:
                return []
            if 'wprowad' in item.lower() or 'vprovad' in item.lower():
                return []
            out.append(item.strip()[:200])
    return out[:5]


def format_weekly(d, summary_bullets):
    """Telegram-пуш: групи + LLM-саммарі + посилання."""
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    lines = [
        f"📋 Щотижневий огляд ВРУ ({week_ago.strftime('%d.%m')}-{today.strftime('%d.%m.%Y')})",
        "",
    ]

    # ГОЛОВНЕ — LLM
    if summary_bullets:
        lines.append("<b>📝 ГОЛОВНЕ:</b>")
        for b in summary_bullets:
            lines.append(f"• {b}")
        lines.append("")

    # Підписані Президентом
    if d.get('signed'):
        lines.append("<b>📜 Підписані закони:</b>")
        for b in d['signed'][:5]:
            title = (b.get('title') or '').strip()
            act = b.get('act_number') or ''
            date = fmt_date(b.get('act_date'))
            url = bill_url(b.get('bill_number'))
            label = f"№{b['bill_number']}"
            if act:
                label += f" (акт {act})"
            # Повна назва, скорочення тільки якщо довша за 200
            if len(title) > 200:
                title = title[:197] + "..."
            line = f"• {label} — {title}"
            if url:
                line += f"\n    {date} · <a href='{url}'>картка закону</a>"
            else:
                line += f" ({date})"
            lines.append(line)
        lines.append("")

    # Ризиковані
    if d.get('risky'):
        lines.append("<b>⚠️ Ризиковані (за LLM):</b>")
        for b in d['risky']:
            cats = []
            summary = ''
            if b.get('json_data'):
                try:
                    j = json.loads(b['json_data']) if isinstance(b['json_data'], str) else b['json_data']
                    cats = [c.get('category', '') for c in j.get('risk_categories', [])][:2]
                    summary = j.get('summary', '')[:180]
                except Exception:
                    pass
            title = (b.get('title') or '').strip()
            if len(title) > 150:
                title = title[:147] + "..."
            risk = b.get('risk_score', '?')
            url = bill_url(b.get('bill_number'))
            label = f"№{b['bill_number']} (risk {risk}/5)"
            lines.append(f"• {label} — {title}")
            if cats:
                lines.append(f"    <i>{' · '.join(cats)}</i>")
            if summary:
                lines.append(f"    {summary}")
            if url:
                lines.append(f"    <a href='{url}'>деталі</a>")
        lines.append("")

    # Знято з розгляду / відхилено
    rejected = d.get('rejected') or []
    breakdown = d.get('status_breakdown') or []
    total_status = sum(c for _, c in breakdown)
    if breakdown:
        lines.append("<b>📊 Зміни статусів:</b>")
        for name, cnt in breakdown[:5]:
            lines.append(f"• {name}: {cnt}")
        lines.append("")

    # Цифри
    lines.append(
        f"<b>📈 Загалом:</b> "
        f"{d['new_bills']} нових · {d['analyzed_total']}/{d['total_bills']} проаналізовано"
    )
    lines.append("")
    lines.append(f"💡 <a href='{DASHBOARD_URL}/overview'>Огляд на дашборді</a>")
    return NL.join(lines)[:3800]


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    d = collect_week()
    summary = llm_summary(d)
    text = format_weekly(d, summary)
    if '--test' in sys.argv:
        print(text)
    else:
        send_message(text)
        print(f"Weekly digest sent ({len(text)} chars, {len(summary)} LLM bullets)")


if __name__ == '__main__':
    main()