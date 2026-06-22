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

# Стадії: (крок, коротка назва)
STATUS_MAP = {
    "new": (1, "Ініціатива"),
    "Одержано проєкт": (1, "Одержано"),
    "Подано": (1, "Подано"),
    "На розгляді в комітеті": (2, "В комітеті"),
    "Перше читання": (2, "І читання"),
    "Друге читання": (3, "ІІ читання"),
    "Закон прийнято": (4, "Прийнято"),
    "Закон підписано": (4, "Підписано"),
    "Відхилено та знято з розгляду": (5, "Відхилено"),
}


def _stage_indicator(stage, max_stages=4):
    """●●○○ = пройшли 2 з 4 стадій, ✕ = відхилено"""
    if stage == 5:
        return "✕"
    filled = min(stage, max_stages)
    return "●" * filled + "○" * (max_stages - filled)


def format_new_bill_message(info):
    bn = info["bill_number"]
    title = info["title"][:80]
    status = info["status"]
    url = info.get("url", "")
    reg_date = info.get("reg_date", "")
    committee = info.get("committee", "")
    score = info.get("overall_score", 0)

    step, stage_name = STATUS_MAP.get(status, (1, status or "Невідомо"))
    indicator = _stage_indicator(step)

    # Номер як посилання (інлайн, без прев'ю)
    if url:
        header = f'<a href="{url}">#{bn}</a>'
    else:
        header = f"#{bn}"

    lines = [f"<b>{header}</b>  {indicator}  {stage_name}"]
    lines.append(title)

    meta = []
    if reg_date:
        meta.append(reg_date)
    if committee:
        meta.append(committee[:40])
    if meta:
        lines.append(" · ".join(meta))

    if score >= CRITICAL_SCORE_THRESHOLD:
        lines.append(f"⚠️ Ризик {score}/100")
    elif score >= 40:
        lines.append(f"Ризик {score}/100")

    return "\n".join(lines)


def format_status_update_group(changes):
    lines = [f"🔄 Зміни статусів <b>{len(changes)}</b>"]
    for ch in changes[:15]:
        bn = ch["bill_number"]
        old = ch.get("old_value", "?")[:25]
        new = ch.get("new_value", "?")[:25]
        url = ch.get("url", "")
        old_step, old_name = STATUS_MAP.get(old, (0, old))
        new_step, new_name = STATUS_MAP.get(new, (0, new))
        if url:
            lines.append(f'<a href="{url}">#{bn}</a>  {old_name} → {new_name}')
        else:
            lines.append(f"#{bn}  {old_name} → {new_name}")
    if len(changes) > 15:
        lines.append(f"... і ще {len(changes) - 15}")
    return "\n".join(lines)


def format_daily_digest(changes, date_str):
    new_bills = [c for c in changes if c.get("change_type") == "new"]
    status_changes = [c for c in changes if c.get("change_type") == "status_change"]
    lines = [f"📋 Дайджест <b>{date_str}</b>"]

    if new_bills:
        lines.append(f"\n<b>Нові</b> ({len(new_bills)})")
        for b in new_bills[:10]:
            score = b.get("overall_score", 0)
            tag = f" ⚠️{score}" if score >= CRITICAL_SCORE_THRESHOLD else ""
            lines.append(f'#{b["bill_number"]} {b.get("title", "")[:50]}{tag}')
        if len(new_bills) > 10:
            lines.append(f"... і ще {len(new_bills) - 10}")

    if status_changes:
        if new_bills:
            lines.append("")
        lines.append(f"<b>Зміни статусу</b> ({len(status_changes)})")
        for ch in status_changes[:10]:
            lines.append(f'#{ch["bill_number"]}: {ch.get("old_value", "?")} → {ch.get("new_value", "?")}')
        if len(status_changes) > 10:
            lines.append(f"... і ще {len(status_changes) - 10}")

    if not new_bills and not status_changes:
        lines.append("Без змін")

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
