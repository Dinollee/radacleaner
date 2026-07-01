#!/usr/bin/env python3
"""sync_votes.py — Парсер голосувань з RADA → D1 (через Worker API).

Usage:
    python sync_votes.py 0374           — один закон за номером
    python sync_votes.py 34579          — одне голосування за g_id
    python sync_votes.py --all          — всі відомі закони з голосуваннями
    python sync_votes.py --recent 10    — останні 10 голосувань з кожного закону
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from src.config import log
from src.d1_client import d1_query, d1_exec


def get_vote_weight(title: str) -> float:
    """Класифікує вагу голосування за заголовком."""
    t = title.lower() if title else ""
    if any(kw in t for kw in ("друге читання", "прийняття", "останнє", "в цілому")):
        return 3.0
    if any(kw in t for kw in ("перше читання", "за основу")):
        return 2.0
    return 1.0


# Кнопка фракцій — idf → назва
FACTION_MAP = {
    "idf1": "СЛУГА НАРОДУ",
    "idf4": "Європейська Солідарність",
    "idf3": "Батьківщина",
    "idf9": "Платформа за життя та мир",
    "idf7": "ДОВІРА",
    "idf8": "Партія \"За майбутнє\"",
    "idf5": "ГОЛОС",
    "idf10": "Відновлення України",
    "idf0": "Позафракційні",
}

STATUS_MAP = {
    "за": "yes", "проти": "no",
    "утримався": "abstain", "утрималися": "abstain",
    "не голосував": "not_present", "не голосували": "not_present",
    "відсутній": "absent", "відсутня": "absent", "відсутні": "absent",
}

STATUS_IDS = {"yes": 1, "no": 2, "abstain": 3, "not_present": 4, "absent": 5}


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        return raw.decode("windows-1251")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def find_g_ids(bill_number):
    zn = bill_number.replace("/", "%2F")
    # URL-encode any non-ASCII characters
    zn = urllib.parse.quote(zn, safe="%")
    url = f"https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_zakon_gol_dep_wohf?zn={zn}"
    html = fetch_url(url)
    return [int(x) for x in re.findall(r"ns_golos\?g_id=(\d+)", html)]


def parse_vote_page(g_id):
    url = f"http://w1.c1.rada.gov.ua/pls/radan_gs09/ns_golos?g_id={g_id}"
    html = fetch_url(url)
    lines = [l.strip() for l in html.split("\n") if l.strip()]

    # Results — search in raw HTML (results are in <br> separated line)
    results = {}
    m = re.search(r"За\s*:\s*(\d+)", html)
    if m: results["yes"] = int(m.group(1))
    m = re.search(r"Проти\s*:\s*(\d+)", html)
    if m: results["no"] = int(m.group(1))
    m = re.search(r"Утрималися\s*:\s*(\d+)", html)
    if m: results["abstain"] = int(m.group(1))
    m = re.search(r"Не голосували\s*:\s*(\d+)", html)
    if m: results["not_present"] = int(m.group(1))
    m = re.search(r"Відсутні\s*:\s*(\d+)", html)
    if m: results["absent"] = int(m.group(1))

    # Date
    vote_date = None
    for line in lines:
        dm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", line)
        if dm:
            try:
                vote_date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)} {dm.group(4)}:{dm.group(5)}"
            except ValueError:
                pass
            break

    # Title
    title = ""
    for line in lines:
        if "голосування" in line.lower() and len(line) > 50:
            title = line[:300]
            break

    # MPs — HTML structure: <div class="dep">Name</div><div class="golos">Status</div>
    mps = []
    # Find all <li id="0idd..."> blocks after "Версія для друку"
    print_idx = html.find("Версія для друку")
    if print_idx == -1:
        print_idx = 0
    section = html[print_idx:]

    # Extract deputy blocks
    dep_pattern = re.compile(
        r'<div class="dep">([^<]+)</div>\s*<div class="golos">(.*?)</div>',
        re.DOTALL
    )
    for match in dep_pattern.finditer(section):
        mp_name = match.group(1).strip()
        raw_status = re.sub(r'<[^>]+>', '', match.group(2)).strip().lower()

        status_code = None
        for sn, code in STATUS_MAP.items():
            if raw_status == sn:
                status_code = code
                break

        if status_code and len(mp_name) > 3:
            mps.append({
                "name": mp_name,
                "faction": "",
                "status": status_code,
            })

    return {
        "g_id": g_id,
        "title": title,
        "vote_date": vote_date,
        "results": results,
        "mps": mps,
    }


def ensure_vote_statuses():
    """Створює vote_statuses якщо їх немає."""
    existing = d1_query("SELECT id FROM vote_statuses")
    if existing:
        return
    for code, label in [("yes", "За"), ("no", "Проти"), ("abstain", "Утримався"),
                         ("not_present", "Не голосував"), ("absent", "Відсутній")]:
        d1_exec("raw_sql", {
            "sql": "INSERT INTO vote_statuses (code, label) VALUES (?, ?) ON CONFLICT (code) DO NOTHING",
            "params": [code, label],
        })


def resolve_bill_id(bill_number):
    rows = d1_query("SELECT id FROM bills WHERE bill_number = ?", [bill_number])
    return rows[0]["id"] if rows else None


def save_vote(vote_data, bill_number=None):
    ensure_vote_statuses()

    bill_id = None
    if bill_number:
        bill_id = resolve_bill_id(bill_number)

    title = vote_data.get("title", "")[:500]
    weight = get_vote_weight(title)

    d1_exec("raw_sql", {
        "sql": """INSERT INTO votes (vote_id, bill_id, title, vote_date, yes_count, no_count, abstain_count, not_present_count, absent_count, weight)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(vote_id) DO UPDATE SET
                    title=excluded.title, vote_date=excluded.vote_date,
                    yes_count=excluded.yes_count, no_count=excluded.no_count,
                    abstain_count=excluded.abstain_count, not_present_count=excluded.not_present_count,
                    absent_count=excluded.absent_count, weight=excluded.weight""",
        "params": [
            vote_data["g_id"], bill_id, title,
            vote_data.get("vote_date"),
            vote_data.get("results", {}).get("yes", 0),
            vote_data.get("results", {}).get("no", 0),
            vote_data.get("results", {}).get("abstain", 0),
            vote_data.get("results", {}).get("not_present", 0),
            vote_data.get("results", {}).get("absent", 0),
            weight,
        ],
    })

    # Resolve mp_id from name via mps table
    mps_lookup = {}
    mps_rows = d1_query("SELECT id, name, faction FROM mps")
    for m in mps_rows:
        mps_lookup[m["name"]] = {"id": m["id"], "faction": m["faction"] or ""}

    mps = vote_data.get("mps", [])
    valid_mps = [(mps_lookup.get(mp["name"], {}).get("id"), mp["status"])
                 for mp in mps if STATUS_IDS.get(mp["status"])]

    # Insert mp_votes using mp_id FK
    for i in range(0, len(valid_mps), 20):
        batch = valid_mps[i:i+20]
        values = []
        params = []
        for mp_id, status_id in batch:
            if not mp_id:
                continue
            values.append("(?, ?, ?, ?)")
            params.extend([vote_data["g_id"], mp_id, status_id, vote_data.get("vote_date")])
        if values:
            try:
                d1_exec("raw_sql", {
                    "sql": f"""INSERT INTO mp_votes (vote_id, mp_id, status_id, vote_date)
                              VALUES {','.join(values)}
                              ON CONFLICT(vote_id, mp_id) DO UPDATE SET
                                status_id=excluded.status_id, vote_date=excluded.vote_date""",
                    "params": params,
                })
            except Exception as e:
                log.warning("mp_votes batch insert failed: %s", str(e)[:100])

    log.info("Saved vote %d: %d MPs (weight=%.1f)", vote_data["g_id"], len(mps), weight)


def process_bill(bill_number, limit=None):
    log.info("=== Processing bill %s ===", bill_number)
    g_ids = find_g_ids(bill_number)
    log.info("Found %d votes", len(g_ids))
    if limit:
        g_ids = g_ids[:limit]
    for g_id in g_ids:
        try:
            data = parse_vote_page(g_id)
            if data:
                save_vote(data, bill_number)
            time.sleep(1)
        except Exception as e:
            log.error("Vote %d failed: %s", g_id, str(e)[:200])


# Відомі закони з голосуваннями (з rada-endpoints.md)
KNOWN_BILLS = ["0371", "0374", "0376"]

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    if args[0] == "--all":
        for bn in KNOWN_BILLS:
            process_bill(bn)
    elif args[0] == "--recent":
        limit = int(args[1]) if len(args) > 1 else 5
        for bn in KNOWN_BILLS:
            process_bill(bn, limit=limit)
    elif args[0].isdigit() and len(args[0]) <= 5:
        # g_id
        data = parse_vote_page(int(args[0]))
        if data:
            print(f"Vote {args[0]}: {data['results']}, {len(data['mps'])} MPs")
            if "--save" in sys.argv:
                save_vote(data)
    else:
        process_bill(args[0])
