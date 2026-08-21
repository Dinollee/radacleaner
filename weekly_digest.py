#!/usr/bin/env python3
"""Щотижневий дайджест для Telegram (щопонеділка 08:00).

Детерміноване форматування без LLM — той самий принцип, що й у
daily_digest_llm.py. Дані з БД, нічого не вигадується.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.d1_client import d1_query
from src.telegram_notifier import send_message

NL = chr(10)


def fmt_date(s):
    """YYYY-MM-DD -> DD.MM.YYYY (якщо парситься)."""
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d').strftime('%d.%m.%Y')
    except ValueError:
        return str(s)[:10]


def collect_week():
    week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    d = {}
    new = d1_query(
        "SELECT COUNT(DISTINCT b.id) AS cnt FROM change_log cl JOIN bills b ON cl.bill_id=b.id "
        "WHERE cl.change_type='new' AND date(cl.created_at) >= ?", [week_ago])
    d['new_bills'] = new[0]['cnt'] if new else 0

    st = d1_query(
        "SELECT COUNT(*) AS cnt FROM change_log "
        "WHERE change_type='status_change' AND date(created_at) >= ?", [week_ago])
    d['status_changes'] = st[0]['cnt'] if st else 0

    an = d1_query(
        "SELECT COUNT(*) AS cnt FROM risk_assessments "
        "WHERE date(assessed_at) >= ?", [week_ago])
    d['analyzed_week'] = an[0]['cnt'] if an else 0

    tot = d1_query("SELECT COUNT(*) AS bills FROM bills")
    an_total = d1_query("SELECT COUNT(DISTINCT bill_id) AS a FROM risk_assessments")
    d['total_bills'] = tot[0]['bills'] if tot else 0
    d['analyzed_total'] = an_total[0]['a'] if an_total else 0

    d['risky'] = d1_query(
        "SELECT b.bill_number, b.title, b.stage, b.current_status, b.registration_date "
        "FROM bills b JOIN risk_assessments ra ON ra.bill_id = b.id "
        "WHERE ra.overall_score > 0 "
        "AND b.registration_date >= to_char(CURRENT_DATE - INTERVAL '30 days', 'YYYY-MM-DD') "
        "ORDER BY b.registration_date DESC, ra.overall_score DESC LIMIT 5") or []

    d['top_mps'] = d1_query(
        "SELECT name, faction, kpi_v12_score FROM mps "
        "WHERE (end_date IS NULL OR end_date = '') AND kpi_v12_score > 0 "
        "ORDER BY kpi_v12_score DESC LIMIT 5") or []
    return d


def format_weekly(d):
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    lines = [
        f"📋 {today.strftime('%d.%m.%Y')} — Щотижневий огляд ВРУ ({week_ago.strftime('%d.%m')}-{today.strftime('%d.%m')})",
        "",
        "📊 ТИЖДЕНЬ:",
        f"• Нові законопроекти: {d['new_bills']}",
        f"• Зміни статусу: {d['status_changes']}",
        f"• Проаналізовано LLM: {d['analyzed_week']} (всього {d['analyzed_total']}/{d['total_bills']})",
    ]
    if d['risky']:
        lines += ["", "📢 УВАГА (топ-5 ризикових за 30 днів, від нового до старого):"]
        for b in d['risky']:
            lines.append(f"📌 {b['bill_number']} — {(b['title'] or '')[:70]}")
            stage = b['stage']
            mid = f"Стадія {stage}/4 · " if stage and stage < 5 else ""
            lines.append(f"   {mid}{b['current_status'] or '—'} · {fmt_date(b['registration_date'])}")
    if d['top_mps']:
        lines += ["", "🏆 ТОП-5 ІЕД:"]
        for i, m in enumerate(d['top_mps'], 1):
            lines.append(f"{i}. {m['name']} ({m['faction']}) — {m['kpi_v12_score']:.1f}")
    lines += ["", f"✅ Перевірено: {d['analyzed_total']}/{d['total_bills']}", "", "Дані: rada.gov.ua"]
    return NL.join(lines)[:3800]


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    text = format_weekly(collect_week())
    if '--test' in sys.argv:
        print(text)
    else:
        send_message(text)
        print(f"Weekly digest sent ({len(text)} chars)")


if __name__ == '__main__':
    main()
