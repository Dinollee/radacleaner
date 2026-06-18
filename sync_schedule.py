#!/usr/bin/env python3
"""sync_schedule.py — Parse RADA calendar plan and committee schedules into D1."""

import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta

from src.config import log
from src.d1_client import d1_query

logger = logging.getLogger('sync_schedule')

# Event type mapping from CSS classes
EVENT_TYPE_MAP = {
    'b_yellow': 'plenary',
    'b_red_yellow': 'question_day',
    'b_green': 'committee',
    'b_purple': 'coordination',
    'b_blue': 'voter_work',
    'b_orange': 'extraordinary',
}

EVENT_LABELS = {
    'plenary': 'Пленарне засідання',
    'question_day': 'Запитання до Уряду',
    'committee': 'Робота в комітетах',
    'coordination': 'Погоджувальна рада',
    'voter_work': 'Робота з виборцями',
    'extraordinary': 'Позачергове засідання',
    'holiday': 'Свято / вихідний',
}

SESSION_INFO = {
    'number': 15,
    'name': "П'ятнадцята сесія",
    'convocation': 'IX скликання',
    'start': '2026-02-01',
    'end': '2026-07-31',
}


def fetch_html(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='ignore')


def parse_calendar_plan(html):
    """Parse the calendar plan page to extract scheduled events."""
    events = []

    # Extract month blocks: each has a calendar table
    # Pattern: data-cal="YYYYMMDD" with parent td having class b_yellow etc.
    # Find all cells with event classes
    month_pattern = re.findall(
        r'<h3[^>]*>.*?<a[^>]*>([^<]+)</a>.*?<small>(\d{4})</small>',
        html
    )

    # Parse each month's calendar
    for month_name, year in month_pattern:
        logger.info('Parsing month: %s %s', month_name, year)

    # Extract individual day cells with event types
    # Pattern: <td class="b_yellow period2 ses9_15"><span data-cal="20260203"
    cell_pattern = re.findall(
        r'<td\s+class="([^"]*)"[^>]*>\s*<span\s+data-cal="(\d{8})"',
        html
    )

    for classes, date_str in cell_pattern:
        # Parse date
        try:
            date = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
        except (IndexError, ValueError):
            continue

        # Determine event type from classes
        event_type = None
        for css_class, etype in EVENT_TYPE_MAP.items():
            if css_class in classes:
                event_type = etype
                break

        if not event_type:
            continue

        title = EVENT_LABELS.get(event_type, event_type)
        events.append({
            'date': date,
            'event_type': event_type,
            'title': title,
            'description': None,
            'url': None,
        })

    # Extract holidays
    holiday_pattern = re.findall(
        r'<td\s+class="holiday"[^>]*title="([^"]*)"[^>]*>\s*<span\s+data-cal="(\d{8})"',
        html
    )
    for title_html, date_str in holiday_pattern:
        try:
            date = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
        except (IndexError, ValueError):
            continue
        # Clean HTML from title
        title = re.sub(r'<[^>]+>', '', title_html).strip()
        events.append({
            'date': date,
            'event_type': 'holiday',
            'title': title or 'Свято',
            'description': title,
            'url': None,
        })

    return events


def parse_agenda_links(html):
    """Extract links to daily agendas from the calendar page."""
    links = {}
    pattern = re.findall(
        r'<a\s+href="(/meeting/awt/show/\d+\.html)"[^>]*>\s*(\d+)\s*</a>',
        html
    )
    for path, day in pattern:
        # Extract date from the data-cal attribute of parent td
        # We need to match day numbers to dates
        links[day] = 'https://www.rada.gov.ua' + path
    return links


def sync_calendar_plan():
    """Sync the main calendar plan from meeting.rada.gov.ua."""
    logger.info('Syncing calendar plan...')

    url = 'https://meeting.rada.gov.ua/work/main/dayp'
    try:
        html = fetch_html(url)
    except Exception as e:
        logger.error('Failed to fetch calendar plan: %s', e)
        return 0

    events = parse_calendar_plan(html)
    agenda_links = parse_agenda_links(html)

    # Add agenda links to plenary events
    for event in events:
        if event['event_type'] == 'plenary':
            day = event['date'].split('-')[2].lstrip('0')
            if day in agenda_links:
                event['url'] = agenda_links[day]

    # Insert into D1
    count = 0
    for event in events:
        try:
            # Upsert: delete existing event for this date+type, then insert
            d1_query(
                'DELETE FROM rada_schedule WHERE date = ? AND event_type = ?',
                [event['date'], event['event_type']]
            )
            d1_query(
                'INSERT INTO rada_schedule (date, event_type, title, description, url, session) VALUES (?, ?, ?, ?, ?, ?)',
                [
                    event['date'],
                    event['event_type'],
                    event['title'],
                    event['description'],
                    event['url'],
                    f"{SESSION_INFO['name']} {SESSION_INFO['convocation']}",
                ]
            )
            count += 1
        except Exception as e:
            logger.error('Failed to insert schedule event: %s', e)

    logger.info('Synced %d calendar events', count)
    return count


def sync_committee_schedules():
    """Sync weekly committee schedules from static.rada.gov.ua."""
    logger.info('Syncing committee schedules...')

    # Get list of weekly schedule links
    index_url = 'http://static.rada.gov.ua/zakon/new/RK/index.htm'
    try:
        index_html = fetch_html(index_url)
    except Exception as e:
        logger.error('Failed to fetch committee index: %s', e)
        return 0

    # Extract links to weekly schedules (e.g., RK080626.htm)
    week_links = re.findall(r'href="(RK\d{6}\.htm)"', index_html)
    week_links = list(set(week_links))  # deduplicate

    count = 0
    for link in week_links[-4:]:  # Only last 4 weeks
        url = f'http://static.rada.gov.ua/zakon/new/RK/{link}'
        try:
            html = fetch_html(url, timeout=20)
            count += parse_weekly_committee(html, link)
        except Exception as e:
            logger.warning('Failed to fetch %s: %s', link, e)

    logger.info('Synced %d committee schedule entries', count)
    return count


def parse_weekly_committee(html, filename):
    """Parse a weekly committee schedule HTML file."""
    count = 0

    # Extract week start date from filename (e.g., RK080626.htm -> 2026-06-08)
    m = re.search(r'RK(\d{2})(\d{2})(\d{2})\.htm', filename)
    if not m:
        return 0
    day, month, year = m.group(1), m.group(2), m.group(3)
    week_start = f'20{year}-{month}-{day}'

    # Extract committee meetings from tables
    # The HTML has tables with committee names and meeting details
    # Pattern varies, but generally: committee name, date, time, topic

    # Simple extraction: look for committee names and dates
    # The format is typically in table rows
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    current_committee = None

    for row in rows:
        # Check for committee name header
        committee_match = re.search(
            r'<(?:h[23]|b|strong)[^>]*>\s*(?:Комітет|Комісія)[^<]*</(?:h[23]|b|strong)>',
            row, re.IGNORECASE
        )
        if committee_match:
            current_committee = re.sub(r'<[^>]+>', '', committee_match.group()).strip()
            continue

        # Look for meeting details in table cells
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if len(cells) >= 2 and current_committee:
            # Try to extract date and topic
            date_match = re.search(r'(\d{1,2})\s*(\S+)\s*(\d{4})', re.sub(r'<[^>]+>', '', cells[0]))
            topic = re.sub(r'<[^>]+>', '', cells[-1]).strip()[:200]

            if date_match and topic:
                # Simple date extraction (Ukrainian months)
                month_map = {
                    'січня': '01', 'лютого': '02', 'березня': '03', 'квітня': '04',
                    'травня': '05', 'червня': '06', 'липня': '07', 'серпня': '08',
                    'вересня': '09', 'жовтня': '10', 'листопада': '11', 'грудня': '12'
                }
                day_num = date_match.group(1).zfill(2)
                month_num = month_map.get(date_match.group(2), '01')
                year_num = date_match.group(3)
                meeting_date = f'{year_num}-{month_num}-{day_num}'

                try:
                    d1_query(
                        'INSERT INTO rada_committee_schedule (week_start, committee_name, meeting_date, topic, url) VALUES (?, ?, ?, ?, ?)',
                        [week_start, current_committee, meeting_date, topic, f'http://static.rada.gov.ua/zakon/new/RK/{filename}']
                    )
                    count += 1
                except Exception as e:
                    logger.debug('Failed to insert committee entry: %s', e)

    return count


def run_schedule_sync():
    """Main entry point for schedule sync."""
    logger.info('Starting RADA schedule sync')
    cal_count = sync_calendar_plan()
    committee_count = sync_committee_schedules()
    logger.info('Schedule sync complete: %d calendar events, %d committee entries', cal_count, committee_count)
    return cal_count + committee_count


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    run_schedule_sync()
