#!/usr/bin/env python3
"""fix_clones.py — Виправлення клонованих депутатів у mp_bills.

Очищує дані для депутатів з однаковими total_bills/total_laws
і пересинхронізує їх через RADA API.
"""
import sys
import time
import re
import urllib.request
import gzip

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))

from src.config import log
from src.d1_client import d1_query, d1_exec


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate"
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
    try:
        return raw.decode("utf-8")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def parse_deputy_bills(html):
    bills = []
    pattern = re.compile(
        r'<tr>\s*<td>\d+</td>\s*'
        r'<td><a[^>]*>([^<]+)</a></td>\s*'
        r'<td>([^<]+)</td>\s*'
        r'<td>([^<]+)</td>\s*'
        r'<td>(?:<a[^>]*>([^<]*)</a>|([^<]*))</td>\s*'
        r'</tr>',
        re.DOTALL
    )
    for match in pattern.finditer(html):
        bills.append({
            "reg_number": match.group(1).strip(),
            "reg_date": match.group(2).strip(),
            "title": match.group(3).strip(),
            "law_number": (match.group(4) or match.group(5) or "").strip(),
            "is_law": bool(match.group(4) or match.group(5)),
        })
    return bills


def get_deputy_user_id(card_url):
    html = fetch_url(card_url)
    match = re.search(r'userId=(\d+)', html)
    return match.group(1) if match else None


def sync_deputy_bills(user_id, deputy_name):
    url = f"https://itd.rada.gov.ua/billInfo/LawmakingActivity/deputies/{user_id}/10"
    html = fetch_url(url)
    bills = parse_deputy_bills(html)
    if not bills:
        return 0, 0

    for bill in bills:
        try:
            d1_exec("raw_sql", {
                "sql": """INSERT INTO mp_bills (mp_name, reg_number, reg_date, title, law_number, is_law)
                          VALUES (?, ?, ?, ?, ?, ?)
                          ON CONFLICT(mp_name, reg_number) DO UPDATE SET
                            law_number=excluded.law_number, is_law=excluded.is_law""",
                "params": [
                    deputy_name, bill["reg_number"], bill["reg_date"],
                    bill["title"][:500], bill["law_number"], 1 if bill["is_law"] else 0
                ]
            })
        except Exception as e:
            log.warning("Failed: %s %s: %s", deputy_name, bill["reg_number"], str(e)[:100])

    total_bills = len(bills)
    total_laws = sum(1 for b in bills if b["is_law"])
    return total_bills, total_laws


def find_clones():
    """Знайти депутатів з однаковими total_bills/total_laws."""
    r = d1_query("""
        SELECT total_bills, total_laws, ARRAY_AGG(name ORDER BY name) as names
        FROM mps
        WHERE end_date IS NULL OR end_date = '' AND total_bills > 0
        GROUP BY total_bills, total_laws
        HAVING COUNT(*) > 1
    """)
    clones = []
    for x in r:
        for name in x['names']:
            clones.append(name)
    return clones


def full_name_to_initials(full_name):
    """Конвертує 'Юрчишин Петро Васильович' → 'Юрчишин П.В.'"""
    parts = full_name.strip().split()
    if len(parts) < 2:
        return full_name
    last_name = parts[0]
    initials = ''.join(f"{p[0]}." for p in parts[1:] if p)
    return f"{last_name} {initials}"


def main():
    clones = find_clones()
    log.info("Found %d clone deputies to fix", len(clones))

    # Get card URL map from RADA
    html = fetch_url("https://people.rada.gov.ua/go/vr-mps")
    card_pattern = re.compile(
        r'<li[^>]*class="mp-card"[^>]*data-name="([^"]*)"[^>]*>.*?<a[^>]*href="(https://people\.rada\.gov\.ua/body/view/mp-[^"]*)"',
        re.DOTALL
    )
    # Map: initials_name → card_url
    card_url_map = {}
    for match in card_pattern.finditer(html):
        full_name = match.group(1).strip()
        card_url = match.group(2).strip()
        initials = full_name_to_initials(full_name)
        card_url_map[initials] = card_url

    log.info("Found %d card URLs on RADA", len(card_url_map))

    fixed = 0
    errors = 0
    for name in clones:
        card_url = card_url_map.get(name)
        if not card_url:
            log.warning("No card URL for %s, skipping", name)
            continue

        # Clear old data
        d1_exec("raw_sql", {
            "sql": "DELETE FROM mp_bills WHERE mp_name = ?",
            "params": [name]
        })

        try:
            user_id = get_deputy_user_id(card_url)
            if not user_id:
                log.warning("No user_id for %s", name)
                continue

            bills, laws = sync_deputy_bills(user_id, name)

            # Update mps totals
            d1_exec("raw_sql", {
                "sql": "UPDATE mps SET total_bills = ?, total_laws = ? WHERE name = ?",
                "params": [bills, laws, name]
            })

            fixed += 1
            log.info("Fixed %s: %d bills, %d laws", name, bills, laws)
            time.sleep(0.5)
        except Exception as e:
            errors += 1
            log.error("Failed to fix %s: %s", name, str(e)[:200])
            time.sleep(2)

    log.info("Done: %d fixed, %d errors", fixed, errors)


if __name__ == "__main__":
    main()
