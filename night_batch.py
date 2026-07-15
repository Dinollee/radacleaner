#!/usr/bin/env python3
"""Нічний пакетний аналіз законопроектів.

Запуск:
    ./venv/bin/python night_batch.py                  — 1 потік
    ./venv/bin/python night_batch.py --workers 3      — 3 потоки
    ./venv/bin/python night_batch.py --workers 3 --limit 100  — обмежити 100 законами
    ./venv/bin/python night_batch.py --workers 3 --force      — переаналізувати все
"""
import argparse
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from src.config import log
from src.d1_client import d1_query, d1_exec
from src.rag_engine import process_bill

_stop = False
_lock = Lock()
_stats = {"done": 0, "ok": 0, "skip": 0, "err": 0, "start": 0}
_force = False


def _signal_handler(sig, frame):
    global _stop
    log.info("收到 сигнал %d — зупиняю після поточних задач...", sig)
    _stop = True


def fetch_bills(stages: list[int], limit: int) -> list[dict]:
    """Отримує закони для аналізу з пріоритетом stage."""

    stage_list = ",".join(str(s) for s in stages)
    sabotage_sub = """
        UNION
        SELECT DISTINCT v.bill_id FROM votes v
        WHERE v.no_count > v.yes_count
    """

    sql = f"""
        SELECT b.id, b.bill_number, b.title, b.current_status, b.url, b.stage
        FROM bills b
        WHERE b.stage IN ({stage_list})
          AND NOT EXISTS (SELECT 1 FROM risk_assessments ra WHERE ra.bill_id = b.id)
        ORDER BY
            CASE b.stage
                WHEN 2 THEN 1
                WHEN 1 THEN 2
                WHEN 3 THEN 3
                WHEN 4 THEN 4
                ELSE 5
            END,
            b.registration_date DESC
        LIMIT {limit}
    """

    return d1_query(sql)


def analyze_one(bill: dict) -> dict:
    """Аналіз одного закону. Повертає результат."""
    if _stop:
        return {"status": "stopped"}

    info = {
        "id": bill["id"],
        "bill_number": bill["bill_number"],
        "title": bill["title"],
        "status": bill["current_status"],
        "url": bill.get("url", ""),
    }

    if _force:
        try:
            d1_exec("raw_sql", {
                "sql": "DELETE FROM risk_assessments WHERE bill_id=%s",
                "params": [bill["id"]],
            })
            d1_exec("raw_sql", {
                "sql": "DELETE FROM rag_documents WHERE bill_id=%s",
                "params": [bill["id"]],
            })
            d1_exec("raw_sql", {
                "sql": "UPDATE bills SET text_hash=NULL, plain_text=NULL WHERE id=%s",
                "params": [bill["id"]],
            })
        except Exception as e:
            log.error("Cache clear failed for #%s: %s", bill["bill_number"], str(e)[:100])

    try:
        info_result, data = process_bill(info, test_mode=True)
    except Exception as e:
        log.error("ERROR #%s: %s", bill["bill_number"], str(e)[:200])
        with _lock:
            _stats["err"] += 1
        return {"status": "error", "bill": bill["bill_number"], "error": str(e)[:200]}

    with _lock:
        _stats["done"] += 1
        if data:
            _stats["ok"] += 1
        else:
            _stats["skip"] += 1

    return {"status": "ok" if data else "skip", "bill": bill["bill_number"]}


def print_progress(idx: int, total: int, elapsed: float):
    """Друк прогресу."""
    pct = (idx / total * 100) if total else 0
    rate = idx / elapsed if elapsed > 0 else 0
    eta = (total - idx) / rate if rate > 0 else 0
    eta_h = int(eta // 3600)
    eta_m = int((eta % 3600) // 60)

    s = _stats
    log.info(
        "PROGRESS %d/%d (%.0f%%) | ok=%d skip=%d err=%d | %.1f bill/s | ETA %dh%02dm",
        idx, total, pct, s["ok"], s["skip"], s["err"], rate, eta_h, eta_m,
    )


def main():
    parser = argparse.ArgumentParser(description="Нічний пакетний аналіз законопроектів")
    parser.add_argument("--workers", type=int, default=3, help="Кількість потоків (1-5)")
    parser.add_argument("--limit", type=int, default=10000, help="Макс. кількість законів")
    parser.add_argument("--stages", type=str, default="1,2,3,4", help="Стейджі через кому")
    parser.add_argument("--force", action="store_true", help="Очистити кеш та переаналізувати")
    args = parser.parse_args()

    workers = max(1, min(5, args.workers))
    stages = [int(s) for s in args.stages.split(",")]
    global _force
    _force = args.force

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    log.info("=" * 60)
    log.info("NIGHT BATCH START | workers=%d limit=%d stages=%s force=%s", workers, args.limit, stages, _force)
    log.info("=" * 60)

    bills = fetch_bills(stages, args.limit)
    total = len(bills)
    log.info("Bills to analyze: %d", total)

    if total == 0:
        log.info("Nothing to analyze. Done.")
        return

    _stats["start"] = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze_one, bill): bill for bill in bills}

        for idx, future in enumerate(as_completed(futures), 1):
            if _stop:
                log.info("Stopping — cancelling remaining futures")
                for f in futures:
                    f.cancel()
                break

            try:
                result = future.result(timeout=600)
            except Exception as e:
                log.error("Future error: %s", str(e)[:200])

            if idx % 10 == 0 or idx == total:
                print_progress(idx, total, time.time() - _stats["start"])

    elapsed = time.time() - _stats["start"]
    s = _stats
    log.info("=" * 60)
    log.info("NIGHT BATCH DONE | total=%d ok=%d skip=%d err=%d | %.0fs (%.1fm)",
             total, s["ok"], s["skip"], s["err"], elapsed, elapsed / 60)
    log.info("=" * 60)
    
    # Run full sync after analysis
    log.info("Running sync_all...")
    import subprocess
    from pathlib import Path
    subprocess.run([sys.executable, str(Path(__file__).parent / "sync_all.py")])


if __name__ == "__main__":
    main()
