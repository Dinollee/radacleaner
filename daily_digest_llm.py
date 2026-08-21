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


def format_digest(data, news=None):
    """Форматує дайджест у точному форматі без LLM."""
    if news is None:
        news = {'new_bills': [], 'committee_news': [], 'other_news': []}

    date_str = _fmt_date(data['date'])
    lines = []

    # --- Заголовок ---
    lines.append(f'📋 {date_str} — Моніторинг законів ВРУ')
    lines.append('')

    # --- СЬОГОДНІ ---
    lines.append('📊 СЬОГОДНІ:')

    # Пленарне засідання
    plenary = data.get('plenary_today')
    if plenary:
        title = plenary.get('title', 'Пленарне засідання')
        lines.append(f'• {title}')
    else:
        lines.append('• Пленарне засідання: не заплановано')

    # Комітети
    meetings = data.get('committee_meetings', [])
    if meetings:
        lines.append(f'• Комітети: {len(meetings)} засідань заплановано')
    else:
        tracked = data.get('tracked_bills', [])
        in_committee = sum(1 for b in tracked if b.get('stage') == 2)
        if in_committee:
            lines.append(f'• Комітети: працюють над {in_committee} законопроектами у стадії 2/4')
        else:
            lines.append('• Комітети: засідань не заплановано')

    # Нові законопроекти
    new_bills = data.get('new_bills', [])
    rada_new = news.get('new_bills', [])
    if rada_new:
        nums = ', '.join(b['number'] for b in rada_new[:5])
        lines.append(f'• Нові законопроекти: {nums}')
    elif new_bills:
        nums = ', '.join('#' + str(b['bill_number']) for b in new_bills[:5])
        lines.append(f'• Нові законопроекти: {nums}')
    else:
        lines.append('• Нові законопроекти: немає')

    lines.append('')

    # --- УВАГА ---
    lines.append('📢 УВАГА (топ-5 ризикових за 30 днів, від нового до старого):')
    tracked = data.get('tracked_bills', [])
    if tracked:
        for b in tracked[:5]:
            bill_num = b.get('bill_number', '')
            title = (b.get('title') or 'Без назви')[:60]
            stage = b.get('stage') or 1
            status = b.get('current_status') or 'Невідомо'
            reg = _fmt_date(b.get('registration_date', ''))
            stage_name = STAGE_NAMES.get(stage, status)
            lines.append(f'📌 {bill_num} — {title}')
            lines.append(f'   Стадія {stage}/4 · {stage_name} · {reg}')
    else:
        high_risk = data.get('high_risk_bills', [])
        if high_risk:
            for b in high_risk[:5]:
                bill_num = b.get('bill_number', '')
                title = (b.get('title') or 'Без назви')[:60]
                stage = b.get('stage') or 1
                status = b.get('current_status') or 'Невідомо'
                lines.append(f'📌 {bill_num} — {title}')
                lines.append(f'   Стадія {stage}/4 · {status}')
        else:
            lines.append('📌 Ризикових законів не виявлено')

    lines.append('')

    # --- Перевірено ---
    analyzed = data.get('analyzed_bills', 0)
    total = data.get('total_bills', 0)
    lines.append(f'✅ Перевірено: {analyzed}/{total}')
    lines.append('')

    # --- Підсумок ---
    changes = data.get('recent_changes_count', 0)
    new_cnt = len(new_bills) + len(rada_new)
    high_risk_cnt = len(data.get('high_risk_bills', []))
    summary_parts = []
    if changes > 0:
        summary_parts.append(f'Зафіксовано {changes} змін статусів.')
    if new_cnt > 0:
        summary_parts.append(f'Нових законопроектів: {new_cnt}.')
    if high_risk_cnt > 0:
        summary_parts.append(f'Відстежується {high_risk_cnt} високоризикових законів.')
    if not summary_parts:
        summary_parts.append('Змін статусів не зафіксовано.')
    summary_parts.append(f'Усього в базі {total} законопроектів.')
    lines.append('Підсумок: ' + ' '.join(summary_parts))
    lines.append('')

    # --- Джерело ---
    lines.append('Дані: rada.gov.ua')

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
