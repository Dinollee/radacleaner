#!/usr/bin/env python3
"""Щоденний дайджест для Telegram.

Детерміноване форматування без LLM — LLM повертав текст промпта
замість заповненого дайджесту. Формат фіксований, дані з БД.
"""
import argparse, logging, re, sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import log
from src.d1_client import d1_query
from src.telegram_notifier import send_message

MAX_CHANGES = 200
NL = chr(10)
logger = logging.getLogger('daily_digest_llm')


def broadcast_digest(text):
    """Рассылка дайджеста подписчикам bot_subscribers (digest=true)."""
    try:
        rows = d1_query("SELECT chat_id FROM bot_subscribers WHERE digest = true")
    except Exception as e:
        logger.warning('Subscribers query failed: %s', e)
        return 0
    sent = 0
    for r in rows or []:
        cid = r.get('chat_id')
        if cid and send_message(text, str(cid)):
            sent += 1
    if rows:
        logger.info('Digest broadcast: %s/%s subscribers', sent, len(rows))
    return sent


def get_today_date():
    return datetime.now().strftime('%Y-%m-%d')


def collect_our_data():
    """Збирає всі дані для дайджесту з БД одним проходом."""
    data = {
        'date': get_today_date(),
        'total_bills': 0,
        'analyzed_bills': 0,
        'by_stage': [],
        'new_bills': [],
        'status_changes': [],
        'high_risk_bills': [],
        'recent_changes_count': 0,
        'tracked_bills': [],
        'plenary_today': None,
        'committee_meetings': [],
        'new_bill_numbers': [],
        'fresh_risky': [],
    }
    try:
        # Загальна статистика
        stats = d1_query('SELECT COUNT(*) as total FROM bills')
        data['total_bills'] = stats[0]['total'] if stats else 0

        analyzed = d1_query('SELECT COUNT(DISTINCT bill_id) as cnt FROM risk_assessments')
        data['analyzed_bills'] = analyzed[0]['cnt'] if analyzed else 0

        by_stage = d1_query(
            'SELECT stage, COUNT(*) as count FROM bills '
            'WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage')
        data['by_stage'] = by_stage

        # Високоризикові закони (топ-10 для статистики)
        high_risk = d1_query(
            'SELECT b.bill_number, b.title, b.stage, b.current_status, ra.overall_score '
            'FROM bills b JOIN risk_assessments ra ON ra.bill_id = b.id '
            'WHERE ra.overall_score > 0 ORDER BY ra.overall_score DESC LIMIT 10')
        data['high_risk_bills'] = high_risk

        # Зміни за сьогодні
        today = get_today_date()
        changes = d1_query(
            'SELECT cl.change_type, cl.old_value, cl.new_value, cl.created_at, '
            'b.bill_number, b.title, b.url, b.stage, b.current_status, ra.overall_score '
            'FROM change_log cl '
            'JOIN bills b ON cl.bill_id = b.id '
            'LEFT JOIN risk_assessments ra ON ra.bill_id = b.id '
            'WHERE date(cl.created_at) = ? ORDER BY cl.created_at DESC LIMIT ?',
            [today, MAX_CHANGES])
        for c in changes:
            entry = {
                'change_type': c['change_type'],
                'bill_number': c['bill_number'],
                'title': c['title'][:80] if c['title'] else '',
                'url': c['url'] or '',
                'old_value': c['old_value'] or '',
                'new_value': c['new_value'] or '',
                'stage': c['stage'],
                'status': c['current_status'] or '',
                'score': c['overall_score'] or 0,
                'created_at': c['created_at'],
            }
            if c['change_type'] == 'new':
                data['new_bills'].append(entry)
            elif c['change_type'] == 'status_change':
                data['status_changes'].append(entry)
        data['recent_changes_count'] = len(changes)

        # Топ-5 ризикових за 30 днів (від нового до старого)
        tracked = d1_query(
            "SELECT b.bill_number, b.title, b.stage, b.current_status, "
            "ra.overall_score, b.registration_date "
            "FROM bills b JOIN risk_assessments ra ON ra.bill_id = b.id "
            "WHERE ra.overall_score > 0 "
            "AND b.registration_date >= to_char(CURRENT_DATE - INTERVAL '30 days', 'YYYY-MM-DD') "
            "ORDER BY b.registration_date DESC, ra.overall_score DESC LIMIT 5")
        data['tracked_bills'] = tracked

        # Пленарне засідання сьогодні
        plenary = d1_query(
            "SELECT title, description, event_type FROM rada_schedule "
            "WHERE date = ? AND event_type IN ('plenary', 'extraordinary') "
            "ORDER BY event_type LIMIT 1",
            [today])
        data['plenary_today'] = plenary[0] if plenary else None

        # Засідання комітетів сьогодні
        committees = d1_query(
            "SELECT committee_name, meeting_time, topic FROM rada_committee_schedule "
            "WHERE meeting_date = ? ORDER BY meeting_time",
            [today])
        data['committee_meetings'] = committees

        # Свіжі високоризиковані за день (risk_score>=4, від нових до старих)
        try:
            fresh = d1_query(
                "SELECT b.bill_number, b.title, ra.risk_score, ra.json_data "
                "FROM risk_assessments ra JOIN bills b ON ra.bill_id=b.id "
                "WHERE date(ra.assessed_at) = ? AND ra.risk_score >= 4 "
                "ORDER BY ra.assessed_at DESC LIMIT 3",
                [today])
            import json as _json
            for b in fresh or []:
                cats_short = ''
                if b.get('json_data'):
                    try:
                        j = _json.loads(b['json_data']) if isinstance(b['json_data'], str) else b['json_data']
                        names = [c.get('category', '') for c in j.get('risk_categories', [])][:2]
                        cats_short = ' · '.join(n for n in names if n)
                    except Exception:
                        pass
                data['fresh_risky'].append({
                    'bill_number': b['bill_number'],
                    'title': b['title'],
                    'risk_score': b['risk_score'],
                    'risk_categories_short': cats_short[:160],
                })
        except Exception as e:
            logger.warning('fresh_risky query failed: %s', e)

    except Exception as e:
        logger.error('Error collecting data: %s', e, exc_info=True)
    return data

def search_news():
    """Парсить новини з rada.gov.ua — нові законопроекти та новини комітетів."""
    import urllib.request
    news_items = []
    new_bills = []
    committee_news = []

    try:
        req = urllib.request.Request('https://www.rada.gov.ua/news',
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        bill_pattern = re.findall(
            r'<a\s+href="(https?://itd\.rada\.gov\.ua/billInfo/Bills/Card/\d+)"[^>]*>\s*'
            r'Новий законопроєкт\s*\((№\s*[\d\-]+)\)[^<]*</a>\s*'
            r'(?:</div>\s*(?:<div[^>]*>)?\s*<p>([^<]*)</p>)?',
            html, re.DOTALL)
        for url, bill_num, desc in bill_pattern:
            bill_num = bill_num.strip()
            desc = (desc or '').strip()[:120]
            new_bills.append({'number': bill_num, 'desc': desc, 'url': url})

        kom_pattern = re.findall(
            r'<a\s+href="(/news/(?:news_kom|news_fr|Top-novyna)/\d+\.html)"[^>]*>\s*'
            r'([^<]+)</a>', html)
        for path, title in kom_pattern:
            title = title.strip()
            if len(title) > 20:
                committee_news.append({'title': title, 'url': 'https://www.rada.gov.ua' + path})
    except Exception as e:
        logger.warning('Failed to fetch RADA news: %s', e)

    # RSS — запасне джерело для комітетів
    if not committee_news:
        try:
            req = urllib.request.Request('https://www.rada.gov.ua/rss',
                                         headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                rss = resp.read().decode('utf-8', errors='ignore')
            items = re.findall(r'<item>\s*<title>([^<]+)</title>\s*<link>([^<]+)</link>', rss)
            for title, link in items[:10]:
                title = title.strip()
                if len(title) > 20:
                    committee_news.append({'title': title, 'url': link})
        except Exception as e:
            logger.warning('Failed to fetch RADA RSS: %s', e)

    # Інші джерела
    other_sources = [
        ('Українська правда', 'https://www.pravda.com.ua/news/'),
        ('Європейська правда', 'https://www.eurointegration.com.ua/news/'),
    ]
    other_news = []
    for name, url in other_sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
            titles = re.findall(r'<h[23][^>]*>([^<]+)</h[23]>', html)
            for t in titles[:3]:
                t = t.strip()
                if len(t) > 20:
                    other_news.append({'source': name, 'title': t})
        except Exception as e:
            logger.warning('Failed to fetch news from %s: %s', name, e)

    return {
        'new_bills': new_bills[:5],
        'committee_news': committee_news[:8],
        'other_news': other_news,
    }


def _fmt_date(date_str):
    """Конвертує дату з ISO (YYYY-MM-DD) у dd.mm.yyyy."""
    if not date_str:
        return ''
    s = str(date_str).strip()
    if len(s) >= 10 and s[4] == '-':
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d').strftime('%d.%m.%Y')
        except ValueError:
            pass
    return s[:10]


STAGE_NAMES = {
    1: 'Реєстрація',
    2: 'Опрацьовується в комітеті',
    3: 'Друге читання',
    4: 'Прийнято/Підписано',
    5: 'Відхилено',
}

DASHBOARD_URL = "https://radacleaner-dashboard.pages.dev"


def bill_url(bill_number):
    """Посилання на сторінку закону на сайті ВРУ."""
    bn = str(bill_number or '').strip()
    if not bn:
        return ''
    return f"https://itd.rada.gov.ua/billinfo/Bills/Card/?id={bn}"


def format_digest(data, news=None):
    """Форматує дайджест: групи + повні назви + посилання. Без LLM.

    Структура (2026-09-07, на основі weekly_digest.py):
      🏛 ПЛЕНАРНЕ
      🆕 НОВІ ЗАКОНОПРОЕКТИ (повні назви + посилання)
      📋 КОМІТЕТИ СЬОГОДНІ (конкретні)
      🔄 ЗМІНИ СТАТУСІВ ЗА ДЕНЬ (1-3 з посиланнями)
      ⚠️ СВІЖІ ВИСОКОРИЗИКОВАНІ (risk>=4 за день, не дублі weekly)
      💡 Дашборд

    Видалено: «Перевірено: X/Y» (дубль weekly), «Підсумок:» (дубль «СЬОГОДНІ»).
    """
    if news is None:
        news = {'new_bills': [], 'committee_news': [], 'other_news': []}

    date_str = _fmt_date(data['date'])
    lines = []

    # --- Заголовок ---
    lines.append(f'📋 {date_str} — Моніторинг законів ВРУ')
    lines.append('')

    # --- ПЛЕНАРНЕ ---
    plenary = data.get('plenary_today')
    if plenary:
        title = plenary.get('title') or 'Пленарне засідання'
        ev_type = plenary.get('event_type') or ''
        prefix = '🏛 ПЛЕНАРНЕ:'
        lines.append(f'{prefix} {title}' + (f' ({ev_type})' if ev_type else ''))
    else:
        lines.append('🏛 ПЛЕНАРНЕ: не заплановано')
    lines.append('')

    # --- НОВІ ЗАКОНОПРОЕКТИ (повні назви + посилання) ---
    new_bills = data.get('new_bills', [])
    rada_new = news.get('new_bills', [])
    # Пріоритет: зміни change_log з повним title; якщо немає — з rada.gov.ua парсера
    if new_bills:
        lines.append(f'🆕 НОВІ ЗАКОНОПРОЕКТИ ({len(new_bills)}):')
        for b in new_bills[:5]:
            num = b.get('bill_number', '?')
            title = (b.get('title') or 'Без назви').strip()
            if len(title) > 200:
                title = title[:197] + '...'
            url = bill_url(num)
            line = f'• №{num} — {title}'
            if url:
                line += f'\n    <a href="{url}">картка закону</a>'
            lines.append(line)
    elif rada_new:
        lines.append(f'🆕 НОВІ ЗАКОНОПРОЕКТИ ({len(rada_new)}):')
        for b in rada_new[:5]:
            # number може приходити як "№ 16043" з rada.gov.ua — strip префікс
            num = str(b.get('number', '?')).replace('№', '').strip()
            desc = (b.get('desc') or '').strip()
            if len(desc) > 200:
                desc = desc[:197] + '...'
            # URL будуємо з bill_number, не з id (id в URL втрачається після видалень)
            url = bill_url(num) or b.get('url', '')
            line = f'• №{num}'
            if desc:
                line += f' — {desc}'
            if url:
                line += f'\n    <a href="{url}">картка закону</a>'
            lines.append(line)
    else:
        lines.append('🆕 Нових законопроектів не зафіксовано')
    lines.append('')

    # --- КОМІТЕТИ СЬОГОДНІ ---
    meetings = data.get('committee_meetings', [])
    if meetings:
        lines.append(f'📋 КОМІТЕТИ ({len(meetings)}):')
        for m in meetings[:6]:
            name = m.get('committee_name') or 'комітет'
            tm = m.get('meeting_time') or ''
            topic = (m.get('topic') or '').strip()
            if len(topic) > 100:
                topic = topic[:97] + '...'
            line = f'• {name}'
            if tm:
                line += f' · {tm}'
            if topic:
                line += f' — {topic}'
            lines.append(line)
    else:
        lines.append('📋 Комітетів сьогодні не заплановано')
    lines.append('')

    # --- ЗМІНИ СТАТУСІВ ЗА ДЕНЬ (конкретні, з посиланнями) ---
    status_changes = data.get('status_changes', [])
    if status_changes:
        lines.append(f'🔄 ЗМІНИ СТАТУСІВ ({len(status_changes)}):')
        for c in status_changes[:5]:
            num = c.get('bill_number', '?')
            new_v = (c.get('new_value') or '').strip()
            old_v = (c.get('old_value') or '').strip()
            title = (c.get('title') or '').strip()
            if len(title) > 120:
                title = title[:117] + '...'
            url = bill_url(num)
            arrow = f'{old_v[:40]} → {new_v[:40]}' if old_v else new_v[:80]
            line = f'• №{num} — {arrow}'
            if title:
                line += f'\n    {title}'
            if url:
                line += f'\n    <a href="{url}">деталі</a>'
            lines.append(line)
    else:
        lines.append('🔄 Змін статусів не зафіксовано')
    lines.append('')

    # --- СВІЖІ ВИСОКОРИЗИКОВАНІ (за день, не дублі weekly) ---
    fresh_risky = data.get('fresh_risky', [])
    if fresh_risky:
        lines.append('⚠️ СВІЖІ ВИСОКОРИЗИКОВАНІ (за день):')
        for b in fresh_risky[:3]:
            num = b.get('bill_number', '?')
            title = (b.get('title') or 'Без назви').strip()
            if len(title) > 150:
                title = title[:147] + '...'
            risk = b.get('risk_score') or '?'
            cats = b.get('risk_categories_short') or ''
            url = bill_url(num)
            line = f'• №{num} (risk {risk}/5) — {title}'
            if cats:
                line += f'\n    <i>{cats}</i>'
            if url:
                line += f'\n    <a href="{url}">деталі</a>'
            lines.append(line)
    else:
        # Якщо за день немає нових — підкажемо де шукати повний топ
        lines.append('⚠️ Свіжих високоризикованих за день немає (повний топ — у weekly)')
    lines.append('')

    # --- Дашборд ---
    lines.append(f"💡 <a href='{DASHBOARD_URL}/overview'>Огляд на дашборді</a>")

    return NL.join(lines)


def run_daily_digest(test_mode=False, force=False):
    logger.info('Starting daily digest for %s', get_today_date())
    if not force:
        hour = datetime.now().hour
        if hour >= 23 or hour < 8:
            logger.info('Quiet hours — skip')
            return

    data = collect_our_data()
    news = search_news()
    digest_text = format_digest(data, news)

    if test_mode:
        print(digest_text)
    else:
        send_message(digest_text)
        broadcast_digest(digest_text)
        logger.info('Digest sent (%d chars)', len(digest_text))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    run_daily_digest(test_mode=args.test, force=args.force)


if __name__ == '__main__':
    main()
