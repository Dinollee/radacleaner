#!/usr/bin/env python3
"""
scripts/parse_votes.py — Парсер голосувань депутатів з RADA
"""
import re, json, urllib.request, psycopg2, bs4, os, sys, time
from datetime import datetime

DB = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5432")),
    dbname=os.environ.get("DB_NAME", "my_bills"),
    user=os.environ.get("DB_USER", "hermes"),
    password=os.environ.get("DB_PASSWORD", "hermes"),
)

STATUS_MAP = {
    "за": "yes",
    "проти": "no",
    "утримався": "abstain",
    "утрималися": "abstain",
    "не голосував": "not_present",
    "не голосували": "not_present",
    "відсутній": "absent",
    "відсутня": "absent",
    "відсутні": "absent",
}

STATUS_NUMBERS = {
    "yes": 1,
    "no": 2,
    "abstain": 3,
    "not_present": 4,
    "absent": 5,
}


def db_conn():
    return psycopg2.connect(**DB)


def get_status_id(code):
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM vote_statuses WHERE code=%s", (code,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def get_results(text):
    results = {}
    m = re.search(r"За\s*:\s*(\d+)", text)
    if m: results["yes"] = int(m.group(1))
    m = re.search(r"Проти\s*:\s*(\d+)", text)
    if m: results["no"] = int(m.group(1))
    m = re.search(r"Утрималися\s*:\s*(\d+)", text)
    if m: results["abstain"] = int(m.group(1))
    m = re.search(r"Не голосували\s*:\s*(\d+)", text)
    if m: results["not_present"] = int(m.group(1))
    m = re.search(r"Відсутні\s*:\s*(\d+)", text)
    if m: results["absent"] = int(m.group(1))
    return results


def parse_vote_page(g_id):
    url = f"http://w1.c1.rada.gov.ua/pls/radan_gs09/ns_golos?g_id={g_id}"
    print(f"Fetching: {url}")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        raw = resp.read()
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    try:
        html = raw.decode("windows-1251")
    except:
        html = raw.decode("utf-8", errors="replace")

    soup = bs4.BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    if not body:
        return None

    text = body.get_text(separator="\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # Results
    results = {}
    for line in lines:
        if line.startswith("За:"):
            results = get_results(line)
            break

    # Date
    vote_date = None
    for line in lines:
        dm = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})", line)
        if dm:
            try:
                vote_date = datetime(
                    int(dm.group(3)), int(dm.group(2)), int(dm.group(1)),
                    int(dm.group(4)), int(dm.group(5))
                )
            except ValueError:
                pass
            break

    # Title
    title = ""
    bill_reg_num = None
    for line in lines:
        if "голосування" in line.lower() and len(line) > 50:
            title = line[:300]
            m = re.search(r"[№#]?(\d{3,5}(?:/\S+)?)", line)
            if m:
                bill_reg_num = m.group(1)
            break

    # MPs — after "Версія для друку"
    mps = []
    in_mp_list = False
    
    for i, line in enumerate(lines):
        if "Версія для друку" in line:
            in_mp_list = True
            continue
        if not in_mp_list:
            continue
        
        # Check if status
        status_code = None
        for sn, code in STATUS_MAP.items():
            if line.lower() == sn:
                status_code = code
                break
        
        if status_code and i > 0:
            mp_name = lines[i - 1]
            # Validate name format
            if re.match(r"^[А-Яа-яІіЇїЄєҐґ\s'\.\-]+$", mp_name) and len(mp_name) > 5:
                mps.append({
                    "name": mp_name,
                    "faction": "",  # Will be filled later
                    "status": status_code,
                    "status_num": STATUS_NUMBERS.get(status_code, 0),
                })

    return {
        "g_id": g_id,
        "bill_reg_num": bill_reg_num,
        "title": title,
        "vote_date": vote_date,
        "results": results,
        "mps": mps,
        "mp_count": len(mps),
    }


def get_faction_for_mp(mp_name):
    """Отримує фракцію депутата з БД."""
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT faction FROM mps WHERE name=%s", (mp_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else ""


def save_vote_to_db(vote_data, bill_id=None):
    if not vote_data:
        return

    conn = db_conn()
    cur = conn.cursor()

    # Save vote
    cur.execute(
        """
        INSERT INTO votes (vote_id, bill_id, title, vote_date, yes_count, no_count, abstain_count, not_present_count, absent_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (vote_id) DO UPDATE SET
            title=EXCLUDED.title, vote_date=EXCLUDED.vote_date,
            yes_count=EXCLUDED.yes_count, no_count=EXCLUDED.no_count,
            abstain_count=EXCLUDED.abstain_count, not_present_count=EXCLUDED.not_present_count,
            absent_count=EXCLUDED.absent_count
    """,
        (
            vote_data["g_id"], bill_id, vote_data.get("title", "")[:500],
            vote_data.get("vote_date"),
            vote_data.get("results", {}).get("yes", 0),
            vote_data.get("results", {}).get("no", 0),
            vote_data.get("results", {}).get("abstain", 0),
            vote_data.get("results", {}).get("not_present", 0),
            vote_data.get("results", {}).get("absent", 0),
        ),
    )

    # Save MP votes
    for mp in vote_data.get("mps", []):
        status_id = get_status_id(mp["status"])
        if not status_id:
            continue

        faction = mp.get("faction", "") or get_faction_for_mp(mp["name"])

        cur.execute(
            "INSERT INTO mps (name, faction) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET faction=EXCLUDED.faction",
            (mp["name"], faction),
        )

        cur.execute(
            """
            INSERT INTO mp_votes (vote_id, mp_name, mp_faction, status_id)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (vote_id, mp_name) DO UPDATE SET mp_faction=EXCLUDED.mp_faction, status_id=EXCLUDED.status_id
        """,
            (vote_data["g_id"], mp["name"], faction, status_id),
        )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Saved vote {vote_data['g_id']}: {vote_data.get('mp_count', 0)} MPs")


def get_factions_summary(vote_data):
    factions = {}
    for mp in vote_data.get("mps", []):
        f = mp.get("faction", "Невідомо") or "Невідомо"
        if f not in factions:
            factions[f] = {"total": 0, "yes": 0, "no": 0, "abstain": 0, "not_present": 0, "absent": 0}
        factions[f]["total"] += 1
        s = mp.get("status", "")
        if s in factions[f]:
            factions[f][s] += 1
    return factions


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: parse_votes.py <bill_reg_num|g_id> [--save]")
        sys.exit(1)

    target = sys.argv[1]
    save = "--save" in sys.argv

    if target.isdigit() and len(target) <= 5:
        g_ids = [int(target)]
    else:
        zn = target.replace("/", "%2F")
        url = f"https://w2.rada.gov.ua/pls/radan_gs09/ns_zakon_gol_dep_wohf?zn={zn}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            html = resp.read().decode("utf-8", errors="replace")
            g_ids = [int(x) for x in re.findall(r"ns_golos\?g_id=(\d+)", html)]
            print(f"Found g_ids: {g_ids}")
        except Exception as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    for g_id in g_ids:
        vote_data = parse_vote_page(g_id)
        if vote_data:
            print(f"\n=== Vote {g_id} ===")
            print(f"Bill: {vote_data.get('bill_reg_num', '?')}")
            print(f"Date: {vote_data.get('vote_date')}")
            print(f"Results: {vote_data.get('results')}")
            print(f"MPs: {vote_data.get('mp_count')}")

            factions = get_factions_summary(vote_data)
            print(f"\nFactions:")
            for f, s in sorted(factions.items(), key=lambda x: -x[1]["total"]):
                print(f"  {f[:35]:35} | Total: {s['total']:3} | За: {s['yes']:3} | Проти: {s['no']:3} | Утрим: {s['abstain']:2} | Не голос: {s['not_present']:2} | Відсут: {s['absent']:2}")

            if save:
                save_vote_to_db(vote_data)

        time.sleep(1)
