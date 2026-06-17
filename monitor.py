#!/usr/bin/env python3
import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from src.config import log
from src.d1_client import d1_exec, d1_query
from src.telegram_notifier import send_message

QUIET_HOURS_START = 23
QUIET_HOURS_END = 8
CRITICAL_SCORE_THRESHOLD = 70

STATUS_MAP = {
    "new": (1, "\u0417\u0430\u0440\u0435\u0454\u0441\u0442\u0440\u043e\u0432\u0430\u043d\u043e"),
    "\u0417\u0430\u043a\u043e\u043d \u043f\u0440\u0438\u0439\u043d\u044f\u0442\u043e": (4, "\u041f\u0440\u0438\u0439\u043d\u044f\u0442\u043e"),
    "\u0417\u0430\u043a\u043e\u043d \u043f\u0456\u0434\u043f\u0438\u0441\u0430\u043d\u043e": (4, "\u041f\u0456\u0434\u043f\u0438\u0441\u0430\u043d\u043e"),
    "\u0412\u0456\u0434\u0445\u0438\u043b\u0435\u043d\u043e \u0442\u0430 \u0437\u043d\u044f\u0442\u043e \u0437 \u0440\u043e\u0437\u0433\u043b\u044f\u0434\u0443": (5, "\u0412\u0456\u0434\u0445\u0438\u043b\u0435\u043d\u043e"),
}

def stage_bar(status):
    step, name = STATUS_MAP.get(status, (1, status or "\u041d\u0435\u0432\u0456\u0434\u043e\u043c\u043e"))
    filled = min(step, 5)
    return chr(9608) * filled + chr(9617) * (5 - filled), name

def format_new_bill_message(info):
    bn = info["bill_number"]
    title = info["title"]
    status = info["status"]
    url = info.get("url", "")
    reg_date = info.get("reg_date", "")
    committee = info.get("committee", "")
    score = info.get("overall_score", 0)
    lines = [f"\U0001f195 \u041d\u043e\u0432\u0438\u0439 \u0437\u0430\u043a\u043e\u043d\u043e\u043f\u0440\u043e\u0454\u043a\u0442 #{bn}"]
    if url:
        lines.append(f'<a href="{url}">{title[:100]}</a>')
    else:
        lines.append(title[:100])
    bar, stage_name = stage_bar(status)
    lines.append(f"\U0001f4ca {bar} {stage_name}")
    if reg_date:
        lines.append(f"\U0001f4c5 \u0417\u0430\u0440\u0435\u0454\u0441\u0442\u0440\u043e\u0432\u0430\u043d\u043e: {reg_date}")
    if committee:
        lines.append(f"\U0001f3db {committee}")
    if score >= CRITICAL_SCORE_THRESHOLD:
        lines.append(f"\n\u26a0\ufe0f \u0412\u0418\u0421\u041e\u041a\u0418\u0419 \u0420\u0418\u0417\u0418\u041a: {score}/100")
    elif score > 0:
        lines.append(f"\n\U0001f4ca \u0420\u0438\u0437\u0438\u043a: {score}/100")
    return "\n".join(lines)

def format_status_update_group(changes):
    lines = [f"\U0001f504 \u0417\u043c\u0456\u043d\u0438 \u0441\u0442\u0430\u0442\u0443\u0441\u0456\u0432 ({len(changes)})"]
    for ch in changes[:15]:
        bn = ch["bill_number"]
        title = ch.get("title", "")[:60]
        old = ch.get("old_value", "?")
        new = ch.get("new_value", "?")
        url = ch.get("url", "")
        if url:
            lines.append(f"\u2022 <a href='{url}'>#{bn}</a> {title}")
        else:
            lines.append(f"\u2022 #{bn} {title}")
        lines.append(f"  {old} \u2192 {new}")
    if len(changes) > 15:
        lines.append(f"\n... \u0456 \u0449\u0435 {len(changes) - 15} \u0437\u043c\u0456\u043d")
    return "\n".join(lines)

def format_daily_digest(changes, date_str):
    new_bills = [c for c in changes if c.get("change_type") == "new"]
    status_changes = [c for c in changes if c.get("change_type") == "status_change"]
    lines = [f"\U0001f4ca \u0414\u0410\u0419\u0414\u0416\u0415\u0421\u0422 \u0417\u0410 {date_str}"]
    if new_bills:
        lines.append(f"\U0001f195 \u041d\u043e\u0432\u0438\u0445: {len(new_bills)}")
        for b in new_bills[:10]:
            score = b.get("overall_score", 0)
            score_str = f" (\u26a0\ufe0f{score})" if score >= CRITICAL_SCORE_THRESHOLD else ""
            lines.append(f"  \u2022 #{b['bill_number']} {b.get('title', '')[:50]}{score_str}")
        if len(new_bills) > 10:
            lines.append(f"  ... \u0456 \u0449\u0435 {len(new_bills) - 10}")
    if status_changes:
        if new_bills:
            lines.append("")
        lines.append(f"\U0001f504 \u0417\u043c\u0456\u043d \u0441\u0442\u0430\u0442\u0443\u0441\u0443: {len(status_changes)}")
        for ch in status_changes[:10]:
            lines.append(f"  \u2022 #{ch['bill_number']}: {ch.get('old_value', '?')} \u2192 {ch.get('new_value', '?')}")
        if len(status_changes) > 10:
            lines.append(f"  ... \u0456 \u0449\u0435 {len(status_changes) - 10}")
    if not new_bills and not status_changes:
        lines.append("\u041d\u0435\u043c\u0430\u0454 \u0437\u043c\u0456\u043d")
    return "\n".join(lines)

def get_unprocessed_changes(limit=100):
    rows = d1_query(
        "SELECT cl.id, cl.bill_id, cl.change_type, cl.old_value, cl.new_value, "
        "cl.created_at, b.bill_number, b.title, b.current_status, b.registration_date, "
        "b.committee, b.url, b.agenda_category, ra.overall_score "
        "FROM change_log cl "
        "JOIN bills b ON cl.bill_id = b.id "
        "LEFT JOIN risk_assessments ra ON ra.bill_id = b.id "
        "WHERE cl.notified = 0 "
        "ORDER BY cl.created_at ASC LIMIT ?",
        [limit],
    )
    return [
        {
            "change_id": r["id"], "bill_id": r["bill_id"],
            "bill_number": r["bill_number"], "title": r["title"],
            "status": r["current_status"], "reg_date": r["registration_date"],
            "committee": r["committee"], "url": r["url"],
            "category": r["agenda_category"], "change_type": r["change_type"],
            "old_value": r["old_value"], "new_value": r["new_value"],
            "created_at": r["created_at"], "overall_score": r["overall_score"] or 0,
        }
        for r in rows
    ]

def mark_processed(change_ids):
    for cid in change_ids:
        d1_exec("raw_sql", {"sql": "UPDATE change_log SET notified=1 WHERE id=?", "params": [cid]})

def is_quiet_hours():
    h = datetime.now().hour
    return h >= QUIET_HOURS_START or h < QUIET_HOURS_END

def run_monitor(test_mode=False, force=False):
    quiet = is_quiet_hours() and not force
    if quiet:
        log.info("Quiet hours - only critical")
    changes = get_unprocessed_changes(limit=100)
    log.info("Found %d unprocessed", len(changes))
    if not changes:
        return
    new_bills = [c for c in changes if c["change_type"] in ("new", "status_fix")]
    status_changes = [c for c in changes if c["change_type"] == "status_change"]
    processed_ids = []
    for bill in new_bills:
        if quiet and bill["overall_score"] < CRITICAL_SCORE_THRESHOLD:
            continue
        msg = format_new_bill_message(bill)
        if not test_mode:
            send_message(msg)
            time.sleep(0.5)
        else:
            log.info("[TEST] %s", msg[:200])
        processed_ids.append(bill["change_id"])
    if status_changes and not quiet:
        for i in range(0, len(status_changes), 15):
            chunk = status_changes[i:i + 15]
            msg = format_status_update_group(chunk)
            if not test_mode:
                send_message(msg)
                time.sleep(0.3)
            else:
                log.info("[TEST] %s", msg[:200])
        processed_ids.extend([c["change_id"] for c in status_changes])
    elif quiet:
        log.info("Skipping %d status changes", len(status_changes))
    if processed_ids:
        mark_processed(processed_ids)
    log.info("Done: %d processed", len(processed_ids))

def run_daily_digest(test_mode=False):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = d1_query(
        "SELECT cl.change_type, cl.old_value, cl.new_value, "
        "b.bill_number, b.title, b.url, ra.overall_score "
        "FROM change_log cl JOIN bills b ON cl.bill_id = b.id "
        "LEFT JOIN risk_assessments ra ON ra.bill_id = b.id "
        "WHERE date(cl.created_at) = ? ORDER BY cl.created_at ASC",
        [yesterday],
    )
    changes = [{"change_type": r["change_type"], "bill_number": r["bill_number"],
                "title": r["title"], "url": r["url"], "old_value": r["old_value"],
                "new_value": r["new_value"], "overall_score": r["overall_score"] or 0} for r in rows]
    if not changes:
        return
    msg = format_daily_digest(changes, yesterday)
    if not test_mode:
        send_message(msg)
    else:
        log.info("[TEST] %s", msg[:300])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--daily", action="store_true")
    args = parser.parse_args()
    if args.daily:
        run_daily_digest(test_mode=args.test)
    else:
        run_monitor(test_mode=args.test, force=args.force)

if __name__ == "__main__":
    main()