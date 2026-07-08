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

📋 ДД.ММ.РРРР — Моніторинг законів ВРУ

📊 СЬОГОДНІ:
• статус пленарного засідання
• статус комітетів
• нові законопроекти або немає

📢 ВІДСЛІДЖУВАНІ:
📢 номер — назва (статус)

✅ Перевірено: число

Підсумок: 2-3 речення

Дані: rada.gov.ua

Правила: не вигадуй дані, не давай політичних оцінок, конкретні номери законів, без Markdown, максимум 1500 символів, дати в форматі dd.mm.yyyy'''

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
    lines.append('Date: ' + _fmt_date(data['date']))
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
            status = b.get('current_status') or 'Unknown'
            lines.append('  - #' + b['bill_number'] + ' [' + status + '] ' + b['title'][:50])
        lines.append('')
    return NL.join(lines)

def search_news():
    news_items = []
    import urllib.request

    new_bills = []
    committee_news = []

    # --- RADA news (main source for bill-related news) ---
    try:
        req = urllib.request.Request('https://www.rada.gov.ua/news', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')

        # Extract new bill registrations: <a href="https://itd.rada.gov.ua/billInfo/Bills/Card/NNNNN">...</a>
        bill_pattern = re.findall(
            r'<a\s+href="(https?://itd\.rada\.gov\.ua/billInfo/Bills/Card/\d+)"[^>]*>\s*'
            r'Новий законопроєкт\s*\((№\s*[\d\-]+)\)[^<]*</a>\s*'
            r'(?:</div>\s*(?:<div[^>]*>)?\s*<p>([^<]*)</p>)?',
            html, re.DOTALL
        )
        for url, bill_num, desc in bill_pattern:
            bill_num = bill_num.strip()
            desc = (desc or '').strip()[:120]
            new_bills.append((bill_num, desc, url))

        # Extract committee/fraction news: links with /news/news_kom/ or /news/news_fr/
        kom_pattern = re.findall(
            r'<a\s+href="(/news/(?:news_kom|news_fr|Top-novyna)/\d+\.html)"[^>]*>\s*'
            r'([^<]+)</a>',
            html
        )
        for path, title in kom_pattern:
            title = title.strip()
            if len(title) > 20:
                committee_news.append((title, 'https://www.rada.gov.ua' + path))

        # Extract bill numbers from committee news titles
        def extract_bill_num(text):
            m = re.search(r'[№#]\s*(\d[\d\-]*)', text)
            return m.group(1) if m else None

        if new_bills:
            news_items.append('Нові законопроєкти на сайті ВРУ:')
            for num, desc, url in new_bills[:5]:
                line = '  - ' + num
                if desc:
                    line += ': ' + desc[:80]
                news_items.append(line)
            news_items.append('')

        if committee_news:
            news_items.append('Новини комітетів/фракцій:')
            seen = set()
            for title, url in committee_news[:8]:
                if title in seen:
                    continue
                seen.add(title)
                bill = extract_bill_num(title)
                prefix = '[#' + bill + '] ' if bill else ''
                news_items.append('  - ' + prefix + title[:90])
            news_items.append('')

    except Exception as e:
        logger.warning('Failed to fetch RADA news: %s', e)

    # --- RADA RSS feed (structured committee/fraction news) ---
    if not committee_news:
        try:
            req = urllib.request.Request('https://www.rada.gov.ua/rss', headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                rss = resp.read().decode('utf-8', errors='ignore')
            items = re.findall(r'<item>\s*<title>([^<]+)</title>\s*<link>([^<]+)</link>', rss)
            for title, link in items[:10]:
                title = title.strip()
                if len(title) > 20:
                    committee_news.append((title, link))
        except Exception as e:
            logger.warning('Failed to fetch RADA RSS: %s', e)

    # --- Other Ukrainian news sources ---
    other_sources = [
        ('Українська правда', 'https://www.pravda.com.ua/news/'),
        ('Європейська правда', 'https://www.eurointegration.com.ua/news/'),
    ]
    for name, url in other_sources:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
            titles = re.findall(r'<h[23][^>]*>([^<]+)</h[23]>', html)
            titles = [t.strip() for t in titles if len(t.strip()) > 20][:3]
            if titles:
                news_items.append('Новини ' + name + ':')
                for t in titles:
                    news_items.append('  - ' + t[:100])
                news_items.append('')
        except Exception as e:
            logger.warning('Failed to fetch news from %s: %s', name, e)

    return NL.join(news_items) if news_items else 'Новини недоступні'

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

def _fmt_date(date_str):
    """Конвертує dd.mm.yyyy або ISO."""
    if not date_str:
        return ""
    s = str(date_str).strip()
    if len(s) >= 10 and s[4] == '-':
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
    return s[:10]


def format_fallback(data):
    lines = []
    lines.append(chr(128203) + ' ' + _fmt_date(data['date']) + ' — Моніторинг законів ВРУ')
    lines.append('')
    lines.append(chr(128202) + ' СЬОГОДНІ:')
    lines.append(chr(10060) + ' Пленарне засідання — не заплановано')
    if data['new_bills']:
        lines.append(chr(9989) + ' Нових законопроєктів: ' + str(len(data['new_bills'])))
    else:
        lines.append(chr(10060) + ' Нових законопроєктів — немає')
    lines.append('')
    lines.append(chr(128226) + ' ВІДСЛІДЖУВАНІ:')
    for b in data['tracked_bills'][:5]:
        title = b['title'][:50] if b['title'] else 'Без назви'
        status = b.get('current_status') or 'Невідомо'
        lines.append(chr(128226) + ' #' + b['bill_number'] + ' — ' + title + ' (' + status + ')')
    lines.append('')
    violations = len([b for b in data['high_risk_bills'] if b.get('overall_score', 0) >= 70])
    lines.append(chr(9989) + ' Перевірено: ' + str(data['total_bills']))
    lines.append('')
    if data['recent_changes_count'] > 0:
        lines.append('Підсумок: ' + str(data['recent_changes_count']) + ' змін статусів.')
    else:
        lines.append('Підсумок: Змін статусів не зафіксовано.')
    lines.append('')
    lines.append('Дані: rada.gov.ua')
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
