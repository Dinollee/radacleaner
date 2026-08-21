#!/usr/bin/env python3
"""Аналізатор законів — опитує PostgreSQL на наявність pending_analysis запитів.

Запуск: ./venv/bin/python analyze_api.py
Працює в фоні, опитує кожні 30 секунд.
Після кожної партії аналізів запускає повну синхронізацію KPI.
"""
import subprocess
import time
from pathlib import Path
from src.config import log
from src.d1_client import d1_query, d1_exec

ANALYZE_SCRIPT = "/home/radamon/radacleaner/analyze_bill.py"
SYNC_SCRIPT = "/home/radamon/radacleaner/sync_all.py"
VENV_PYTHON = "/home/radamon/radacleaner/venv/bin/python"
POLL_INTERVAL = 30
SYNC_AFTER_ANALYZES = 10  # Run sync_all after N analyses
ANALYZE_TIMEOUT = 3600  # Великі закони (30+ чанків) аналізуються 20-30 хв
SYNC_TIMEOUT = 1800


def run_sync():
    """Запустити повну синхронізацію KPI."""
    log.info("Running sync_all...")
    try:
        result = subprocess.run(
            [VENV_PYTHON, SYNC_SCRIPT],
            capture_output=True, text=True, timeout=SYNC_TIMEOUT,
            cwd="/home/radamon/radacleaner",
        )
        if result.returncode == 0:
            log.info("sync_all completed successfully")
        else:
            log.warning("sync_all exited with code %d", result.returncode)
    except Exception as e:
        log.error("sync_all failed: %s", e)


def poll_and_analyze():
    analyses_since_sync = 0
    
    while True:
        try:
            pending = d1_query(
                "SELECT id, bill_id, bill_number FROM pending_analysis WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            )

            if not pending:
                # No pending - run sync if we've done analyses
                if analyses_since_sync > 0:
                    run_sync()
                    analyses_since_sync = 0
                time.sleep(POLL_INTERVAL)
                continue

            for req in pending:
                bill_number = req["bill_number"]
                req_id = req["id"]

                log.info("Processing pending analysis: #%s (req_id=%d)", bill_number, req_id)

                d1_exec("raw_sql", {
                    "sql": "UPDATE pending_analysis SET status='running', started_at=(now() AT TIME ZONE 'utc') WHERE id=%s",
                    "params": [req_id],
                })

                try:
                    result = subprocess.run(
                        [VENV_PYTHON, ANALYZE_SCRIPT, bill_number],
                        capture_output=True, text=True, timeout=ANALYZE_TIMEOUT,
                        cwd="/home/radamon/radacleaner",
                    )
                    output = result.stdout + result.stderr

                    # 'done' лише якщо скрипт завершився успішно; інакше 'error'
                    status = 'done' if result.returncode == 0 else 'error'
                    d1_exec("raw_sql", {
                        "sql": "UPDATE pending_analysis SET status=%s, finished_at=(now() AT TIME ZONE 'utc'), output=%s WHERE id=%s",
                        "params": [status, output[:5000], req_id],
                    })
                    log.info("Analysis %s for #%s (rc=%d)", status, bill_number, result.returncode)
                    analyses_since_sync += 1

                    # Run sync after batch of analyses
                    if analyses_since_sync >= SYNC_AFTER_ANALYZES:
                        run_sync()
                        analyses_since_sync = 0

                except subprocess.TimeoutExpired as e:
                    tail = ((e.stdout or b"") + (e.stderr or b""))
                    if isinstance(tail, bytes):
                        tail = tail.decode("utf-8", errors="replace")
                    d1_exec("raw_sql", {
                        "sql": "UPDATE pending_analysis SET status='error', finished_at=(now() AT TIME ZONE 'utc'), output=%s WHERE id=%s",
                        "params": [f"TIMEOUT after {ANALYZE_TIMEOUT}s. Tail: {tail[-2000:]}", req_id],
                    })
                    log.error("Analysis TIMEOUT for #%s after %ds", bill_number, ANALYZE_TIMEOUT)
                except Exception as e:
                    d1_exec("raw_sql", {
                        "sql": "UPDATE pending_analysis SET status='error', finished_at=(now() AT TIME ZONE 'utc'), output=%s WHERE id=%s",
                        "params": [str(e)[:5000], req_id],
                    })
                    log.error("Analysis failed for #%s: %s", bill_number, e)

                time.sleep(2)

        except Exception as e:
            log.error("Poll error: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    log.info("Analyze API starting (poll every %ds)", POLL_INTERVAL)
    poll_and_analyze()
