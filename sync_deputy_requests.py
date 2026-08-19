#!/usr/bin/env python3
"""sync_deputy_requests.py — Синхронізація депутатських запитів з RADA ITD API.

Враховує ТІЛЬКИ запити з відповіддю (фільтр спаму).
"""
import urllib.request, urllib.parse, json, http.cookiejar, re, time, html as html_mod
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_session():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('User-Agent', 'Mozilla/5.0'), ('Accept', 'application/json')]
    opener.open('https://itd.rada.gov.ua/struct/uk/Structure/MPs').read()
    return opener


def get_mprequests_id(opener, full_name):
    """Find deputy's mprequests ID by full name matching.

    full_name format: "Прізвище І.І." (from mps table)
    API returns: surname, firstname, patronymic (full forms)
    """
    name_parts = full_name.split()
    if not name_parts:
        return None
    last_name = name_parts[0]

    # Extract initials from mps name: "Ткаченко М.М." -> ["М", "М"]
    # Handle both "М.М." and "О.М" formats
    initials = []
    for p in name_parts[1:]:
        p = p.rstrip('.')
        if '.' in p:
            # "О.М" -> split into ["О", "М"]
            initials.extend([x for x in p.split('.') if x])
        elif p:
            initials.append(p)

    url = f'https://itd.rada.gov.ua/mprequests/api/DeputyRequest/mpautocomplite?word={urllib.parse.quote(last_name)}&convId=10'
    try:
        resp = opener.open(url)
        data = json.loads(resp.read().decode('utf-8'))
        authors = data.get('authors', [])
        if not authors:
            return None
        if len(authors) == 1:
            return authors[0].get('id')

        # Multiple matches — find by first name + patronymic initials
        for a in authors:
            api_first = (a.get('firstname') or "")[:1]
            api_patronymic = (a.get('patronymic') or "")[:1]
            if len(initials) >= 2 and api_first == initials[0] and api_patronymic == initials[1]:
                return a.get('id')
            elif len(initials) == 1 and api_first == initials[0]:
                return a.get('id')

        # ponytail: Edge case — same first+patronymic initials for 2+ deputies
        # (e.g., Стефанчук М.О. vs Микола О., Павленко Р.М. vs Ростислав М.)
        # ~6 deputies affected out of 460. Acceptable for request count accuracy.
        return authors[0].get('id')
    except Exception:
        pass
    return None


def search_requests_with_responses(opener, mp_req_id):
    """Пошук запитів з відповіддю."""
    url = 'https://itd.rada.gov.ua/mprequests/api/DeputyRequest/SearchResults'
    params = {"ConvocationId": 10, "AuthorId": mp_req_id, "Take": 500}
    try:
        resp = opener.open(urllib.request.Request(url,
            data=json.dumps(params).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}))
        data = json.loads(resp.read().decode('utf-8'))
        html_content = data.get('view', '')

        # Count total requests
        total_match = re.search(r'Знайдено[^:]*:\s*(\d+)', html_content)
        total = int(total_match.group(1)) if total_match else 0

        # Parse rows to find responses
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL | re.IGNORECASE)
        data_rows = [r for r in rows if '<td' in r]

        with_response = 0
        for row in data_rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL | re.IGNORECASE)
            if len(cells) >= 5:
                status_raw = html_mod.unescape(re.sub(r'<[^>]+>', '', cells[4]).strip())
                if 'Відповідь' in status_raw or 'відповідь' in status_raw.lower():
                    with_response += 1

        return total, with_response
    except Exception:
        return 0, 0


def sync_requests():
    print("Синхронізація депутатських запитів (з фільтром відповідей)...")
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT id, name FROM mps WHERE end_date IS NULL OR end_date = '' ORDER BY name")
    deputies = cur.fetchall()
    print(f"Депутатів: {len(deputies)}")
    
    opener = get_session()
    results = []
    errors = 0
    
    for i, (mps_id, name) in enumerate(deputies):
        name_parts = name.split()
        if not name_parts:
            continue

        try:
            mp_req_id = get_mprequests_id(opener, name)
            if not mp_req_id:
                continue
            
            total, with_response = search_requests_with_responses(opener, mp_req_id)
            results.append((mps_id, name, total, with_response))
            
            if with_response > 0:
                print(f"  [{i+1}/{len(deputies)}] {name}: {with_response}/{total} з відповіддю")
            
        except Exception as e:
            errors += 1
        
        time.sleep(0.2)
    
    # Update database
    for mps_id, name, total, with_response in results:
        cur.execute("UPDATE mps SET request_count = %s, requests_with_response = %s WHERE id = %s",
                    (total, with_response, mps_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\nГотово: {len(results)} депутатів, {errors} помилок")
    with_resp = sum(1 for _, _, _, wr in results if wr > 0)
    print(f"З відповіддю: {with_resp} депутатів")


if __name__ == "__main__":
    sync_requests()
