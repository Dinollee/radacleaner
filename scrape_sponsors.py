#!/usr/bin/env python3
"""
Скрапінг ініціаторів законопроєктів з карток на itd.rada.gov.ua
і збереження їх у таблицю bill_sponsors.
"""
import re
import time
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv
import urllib.request

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://itd.rada.gov.ua/billinfo/Bills/Card/"

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def extract_rada_card_id(url):
    if not url:
        return None
    m = re.search(r"/Card/(\d+)", url)
    return int(m.group(1)) if m else None


def fetch_sponsors_from_card(card_id):
    url = BASE_URL + str(card_id)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return [], str(e)

    idx = html.find("\u0406\u043d\u0456\u0446\u0456\u0430\u0442\u043e\u0440")
    if idx < 0:
        return [], "no_initiators_section"

    chunk = html[idx:idx+3000]

    names_raw = re.findall(
        r'<a\s+target="_blank"\s+href="https://itd\.rada\.gov\.ua/struct/uk/Structure/MPs\?userId=(\d+)">\s*(.*?)\s*</a>',
        chunk,
        re.DOTALL,
    )

    sponsors = []
    for i, (uid_str, name_raw) in enumerate(names_raw):
        name = re.sub(r"\s*\(.*?\u0441\u043a\u043b\u0438\u043a\u0430\u043d\u043d\u044f.*?\)\s*,?\s*", "", name_raw).strip()
        if not name:
            continue
        sponsors.append({
            "rada_uid": int(uid_str),
            "mp_name": name,
            "sponsor_order": i,
        })

    if not sponsors:
        span_match = re.search(
            r'class="routes-container">\s*(.*?)\s*<span class="extra-routes"',
            chunk,
            re.DOTALL,
        )
        if span_match:
            text = span_match.group(1).strip()
            for i, part in enumerate(re.split(r",\s*", text)):
                name = part.strip()
                if name:
                    sponsors.append({
                        "rada_uid": None,
                        "mp_name": name,
                        "sponsor_order": i,
                    })

    return sponsors, None


def main():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT b.id, b.bill_number, b.url
        FROM bills b
        WHERE b.significance IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM bill_sponsors bs WHERE bs.bill_id = b.id)
        ORDER BY b.id
    """)
    bills = cur.fetchall()
    print(f"Bills without sponsors: {len(bills)}")

    success = 0
    errors = 0
    no_sponsors = 0

    for bill_id, bill_number, bill_url in bills:
        card_id = extract_rada_card_id(bill_url)
        if not card_id:
            errors += 1
            continue

        sponsors, err = fetch_sponsors_from_card(card_id)

        if err:
            errors += 1
            continue

        if not sponsors:
            no_sponsors += 1
            continue

        for s in sponsors:
            cur.execute(
                "INSERT INTO bill_sponsors (bill_id, mp_name, rada_uid, sponsor_order) VALUES (%s, %s, %s, %s)",
                (bill_id, s["mp_name"], s.get("rada_uid"), s["sponsor_order"]),
            )

        success += 1
        if success % 50 == 0:
            conn.commit()
            print(f"  Progress: {success}/{len(bills)} ({bill_number})")

        time.sleep(0.3)

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nDone! Success: {success}, No sponsors: {no_sponsors}, Errors: {errors}")


if __name__ == "__main__":
    main()
