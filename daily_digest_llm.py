#!/usr/bin/env python3
import argparse, json, logging, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import log, LLM_API_KEY, LLM_API_URL, LLM_MODEL, get_llm_key
from src.d1_client import d1_query
from src.telegram_notifier import send_message

MAX_CHANGES = 200
NL = chr(10)
logger = logging.getLogger('daily_digest_llm')

DIGEST_SYSTEM = 'Ти - аналітичний центр моніторингу ВРУ. Відповідай українською, сухо. Не використовуй Markdown.'

DIGEST_PROMPT = '''Проаналізуй дані та сформуй щоденну сводку.

ДАНІ З НАШОЇ БАЗИ:
{our_data}

НОВИНИ:
{news_data}

Сформуй сводку ТОЧНО в такому форматі:

📋 ДАТА — Моніторинг законів ВРУ

🔴 ПОРУШЕННЯ ВИЯВЛЕНО: кількість

📊 СЬОГОДНІ:
• статус пленарного засідання
• статус комітетів
• нові законопроекти або немає

📌 ВІДСЛІДЖУВАНІ (з флагами):
📌 номер — назва
   Етап: [████░] відсоток%

✅ Перевірено: число
🔴 З порушеннями: число

Підсумок: 2-3 речення

Дані: rada.gov.ua 🔥

Правила: не вигадуй дані, не давай політичних оцінок, конкретні номери законів, без Markdown, максимум 1500 символів'''

def get_today_date():
    return datetime.now().strftime('%Y-%m-%d')

def collect_our_data():
    data = {'date': get_today_date(), 'total_bills': 0, 'by_stage': [], 'new_bills': [], 'status_changes': [], 'high_risk_bills': [], 'recent_changes_count': 0, 'tracked_bills': []}
    try:
        stats = d1_query('SELECT COUNT(*) as total FROM bills')
        data['total_bills'] = stats[0]['total'] if stats else 0
        by_stage = d1_query('SELECT stage, COUNT(*) as count FROM bills WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage')
        data['by_stage'] = by_stage
        high_risk = d1_query('SELECT b.bill_number, b.title, b.stage, b.current_status, ra.overall_score FROM bills b JOIN risk_assessments ra ON ra.bill_id = b.id WHERE ra.overall_score > 0 ORDER BY ra.overall_score DESC LIMIT 10')
        data['high_risk_bills'] = high_risk
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
            entry = {'change_type': c['change_type'], 'bill_number': c['bill_number'], 'title': c['title'][:80] if c['title'] else '', 'url': c['url'] or '', 'old_value': c['old_value'] or '', 'new_value': c['new_value'] or '', 'stage': c['stage'], 'status': c['current_status'] or '', 'score': c['overall_score'] or 0, 'created_at': c['created_at']}
            if c['change_type'] == 'new': data['new_bills'].append(entry)
            elif c['change_type'] == 'status_change': data['status_changes'].append(entry)
        data['recent_changes_count'] = len(changes)
        tracked = d1_query('SELECT b.bill_number, b.title, b.stage, b.current_status, ra.overall_score FROM bills b JOIN risk_assessments ra ON ra.bill_id = b.id WHERE ra.overall_score > 0 ORDER BY ra.overall_score DESC LIMIT 10')
        data['tracked_bills'] = tracked
    except Exception as e:
        logger.error('Error collecting data: %s', e, exc_info=True)
    return data

def format_our_data_for_llm(data):
    lines = []
    lines.append('Date: ' + data['date'])
    lines.append('Total bills: ' + str(data['total_bills']))
    lines.append('Changes today: ' + str(data['recent_changes_count']))
    lines.append('')
    if data['by_stage']:
        lines.append('By stage:')
        stage_names = {1: 'Registered', 2: 'First reading', 3: 'Second reading', 4: 'Signed', 5: 'Rejected'}
        for s in data['by_stage']:
            name = stage_names.get(s['stage'], 'Stage ' + str(s['stage']))
            lines.append('  - ' + name + ': ' + str(s['count']))
        lines.append('')
    if data['new_bills']:
        lines.append('New bills: ' + str(len(data['new_bills'])))
        for b in data['new_bills'][:10]:
            score_str = ' (risk:' + str(b['score']) + ')' if b['score'] >= 50 else ''
            lines.append('  - #' + b['bill_number'] + ': ' + b['title'][:60] + score_str)
        lines.append('')
    if data['status_changes']:
        lines.append('Status changes: ' + str(len(data['status_changes'])))
        for c in data['status_changes'][:15]:
            lines.append('  - #' + c['bill_number'] + ': ' + c['old_value'] + ' -> ' + c['new_value'])
        lines.append('')
    if data['high_risk_bills']:
        lines.append('High risk: ' + str(len(data['high_risk_bills'])))
        for b in data['high_risk_bills'][:5]:
            lines.append('  - #' + b['bill_number'] + ' (risk: ' + str(b['overall_score']) + '/100)')
        lines.append('')
    if data['tracked_bills']:
        lines.append('Tracked (with risks): ' + str(len(data['tracked_bills'])))
        for b in data['tracked_bills'][:5]:
            stage = b.get('stage') or 1
            bar = '#' * min(stage, 5) + '-' * (5 - min(stage, 5))
            pct = min(stage * 20, 100)
            lines.append('  - #' + b['bill_number'] + ' [' + bar + '] ' + str(pct) + '% ' + b['title'][:50])
        lines.append('')
    return NL.join(lines)

def search_news():
    news_items = []
    import urllib.request
    sources = [('Ukrainska Pravda', 'https://www.pravda.com.ua/news/'), ('European Pravda', 'https://www.eurointegration.com.ua/news/'), ('RADA', 'https://www.rada.gov.ua/news/')]
    for name, url in sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
                titles = re.findall(r'<h[23][^>]*>([^<]+)</h[23]>', html)
                titles = [t.strip() for t in titles if len(t.strip()) > 20][:5]
                if titles:
                    news_items.append('News from ' + name + ':')
                    for t in titles:
                        news_items.append('  - ' + t[:100])
                    news_items.append('')
        except Exception as e:
            logger.warning('Failed to fetch news from %s: %s', name, e)
    return NL.join(news_items) if news_items else 'No news found'

def call_llm(our_data_str, news_str):
    # Use raw API call to get text directly (groq_completion expects JSON)
    import requests as req_lib
    prompt = DIGEST_PROMPT.format(our_data=our_data_str, news_data=news_str)
    try:
        headers = {'Authorization': 'Bearer ' + LLM_API_KEY, 'Content-Type': 'application/json'}
        payload = {'model': LLM_MODEL, 'messages': [{'role': 'system', 'content': DIGEST_SYSTEM}, {'role': 'user', 'content': prompt}], 'temperature': 0.3, 'max_tokens': 2000}
        resp = req_lib.post(LLM_API_URL + '/chat/completions', headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        text = resp.json()['choices'][0]['message']['content']
        if text and len(text) > 50:
            return text
    except Exception as e:
        logger.error('LLM call failed: %s', e)
    return None

def format_digest_from_text(text):
    # Clean up markdown and return
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    return text.strip()

def format_fallback(data):
    lines = []
    lines.append(chr(128203) + ' ' + data['date'] + ' — Моніторинг законів ВРУ')
    lines.append('')
    violations = len([b for b in data['high_risk_bills'] if b.get('overall_score', 0) >= 70])
    lines.append(chr(128308) + ' ПОРУШЕННЯ ВИЯВЛЕНО: ' + str(violations))
    lines.append('')
    lines.append(chr(128202) + ' СЬОГОДНІ:')
    lines.append(chr(10060) + ' Пленарне засідання — не заплановано')
    if data['new_bills']:
        lines.append(chr(9989) + ' Нових законопроєктів: ' + str(len(data['new_bills'])))
    else:
        lines.append(chr(10060) + ' Нових законопроєктів — немає')
    lines.append('')
    lines.append(chr(128204) + ' ВІДСЛІДЖУВАНІ (з флагами):')
    for b in data['tracked_bills'][:5]:
        stage = b.get('stage') or 1
        bar = chr(9608) * min(stage, 5) + chr(9617) * (5 - min(stage, 5))
        pct = min(stage * 20, 100)
        title = b['title'][:50] if b['title'] else 'Без назви'
        lines.append(chr(128204) + ' #' + b['bill_number'] + ' — ' + title)
        lines.append('   Етап: [' + bar + '] ' + str(pct) + '%')
    lines.append('')
    lines.append(chr(9989) + ' Перевірено: ' + str(data['total_bills']))
    lines.append(chr(128308) + ' З порушеннями: ' + str(violations))
    lines.append('')
    if data['recent_changes_count'] > 0:
        lines.append('Підсумок: ' + str(data['recent_changes_count']) + ' змін статусів.')
    else:
        lines.append('Підсумок: Змін статусів не зафіксовано.')
    lines.append('')
    lines.append('Дані: rada.gov.ua ' + chr(128293))
    return NL.join(lines)

def run_daily_digest(test_mode=False, force=False):
    logger.info('Starting daily digest for %s', get_today_date())
    if not force:
        hour = datetime.now().hour
        if hour >= 23 or hour < 8:
            logger.info('Quiet hours - skipping')
            return
    data = collect_our_data()
    our_data_str = format_our_data_for_llm(data)
    news_str = search_news()
    digest_text = call_llm(our_data_str, news_str)
    if digest_text and len(digest_text) > 50:
        digest_text = format_digest_from_text(digest_text)
    else:
        digest_text = format_fallback(data)
    if test_mode:
        print(digest_text)
    else:
        send_message(digest_text)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    run_daily_digest(test_mode=args.test, force=args.force)

if __name__ == '__main__':
    main()
