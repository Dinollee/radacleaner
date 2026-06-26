#!/usr/bin/env python3
"""sync_deputy_requests.py — Синхронізація кількості депутатських запитів з RADA ITD API.

Отримує кількість запитів для кожного депутата з mprequests системи.
Зберігає в таблицю deputy_requests та оновлює mps.request_count.
"""
import urllib.request, urllib.parse, json, http.cookiejar, re, time
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
    """Створити HTTP сесію з cookies."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [('User-Agent', 'Mozilla/5.0'), ('Accept', 'application/json')]
    opener.open('https://itd.rada.gov.ua/struct/uk/Structure/MPs').read()
    return opener


def get_mprequests_id(opener, last_name):
    """Отримати ID депутата в системі mprequests через autocomplete."""
    url = f'https://itd.rada.gov.ua/mprequests/api/DeputyRequest/mpautocomplite?word={urllib.parse.quote(last_name)}&convId=10'
    try:
        resp = opener.open(url)
        data = json.loads(resp.read().decode('utf-8'))
        authors = data.get('authors', [])
        if authors:
            return authors[0].get('id')
    except Exception:
        pass
    return None


def search_requests(opener, mp_req_id):
    """Пошук кількості запитів для депутата."""
    url = 'https://itd.rada.gov.ua/mprequests/api/DeputyRequest/SearchResults'
    params = {"ConvocationId": 10, "AuthorId": mp_req_id}
    try:
        resp = opener.open(urllib.request.Request(url,
            data=json.dumps(params).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}))
        data = json.loads(resp.read().decode('utf-8'))
        html = data.get('view', '')
        count_match = re.search(r'Знайдено[^:]*:\s*(\d+)', html)
        return int(count_match.group(1)) if count_match else 0
    except Exception:
        return 0


def sync_requests():
    """Основна функція синхронізації."""
    print("Синхронізація депутатських запитів...")
    
    conn = get_db()
    cur = conn.cursor()
    
    # Get all active deputies
    cur.execute("SELECT id, name FROM mps WHERE end_date IS NULL OR end_date = '' ORDER BY name")
    deputies = cur.fetchall()
    print(f"Депутатів для обробки: {len(deputies)}")
    
    opener = get_session()
    results = []
    errors = 0
    
    for i, (mps_id, name) in enumerate(deputies):
        name_parts = name.split()
        if not name_parts:
            continue
        
        last_name = name_parts[0]
        
        try:
            mp_req_id = get_mprequests_id(opener, last_name)
            if not mp_req_id:
                continue
            
            request_count = search_requests(opener, mp_req_id)
            results.append((mps_id, name, request_count))
            
            if request_count > 0:
                print(f"  [{i+1}/{len(deputies)}] {name}: {request_count} запитів")
            
        except Exception as e:
            errors += 1
        
        time.sleep(0.2)
    
    # Update database
    for mps_id, name, count in results:
        cur.execute("UPDATE mps SET request_count = %s WHERE id = %s", (count, mps_id))
    
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"\nГотово: {len(results)} депутатів оновлено, {errors} помилок")
    with_requests = sum(1 for _, _, c in results if c > 0)
    print(f"Запити є у {with_requests} депутатів")


if __name__ == "__main__":
    sync_requests()
