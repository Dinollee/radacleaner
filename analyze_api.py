#!/usr/bin/env python3
"""Аналізатор законів — опитує D1 на наявність pending_analysis запитів.

Запуск: python analyze_api.py
Працює в фоні, опитує D1 кожні 30 секунд.
"""
import json
import subprocess
import sys
import time
from src.config import log
from src.d1_client import d1_query, d1_exec

ANALYZE_SCRIPT = "/home/radamon/radacleaner/analyze_bill.py"
VENV_PYTHON = "/home/radamon/radacleaner/venv/bin/python"
POLL_INTERVAL = 30  # секунд


def poll_and_analyze():
    """Опитує D1 та запускає аналіз для pending запитів."""
    while True:
        try:
            # Знаходимо pending запити
            pending = d1_query(
                "SELECT id, bill_id, bill_number FROM pending_analysis WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            )

            if not pending:
                time.sleep(POLL_INTERVAL)
                continue

            for req in pending:
                bill_number = req["bill_number"]
                req_id = req["id"]

                log.info("Processing pending analysis: #%s (req_id=%d)", bill_number, req_id)

                # Позначаємо як "в роботі"
                d1_exec("raw_sql", {
                    "sql": f"UPDATE pending_analysis SET status='running', started_at=datetime('now') WHERE id={req_id}",
                })

                try:
                    result = subprocess.run(
                        [VENV_PYTHON, ANALYZE_SCRIPT, bill_number, "--force"],
                        capture_output=True, text=True, timeout=300,
                        cwd="/home/radamon/radacleaner",
                    )
                    output = result.stdout + result.stderr

                    # Позначаємо як "виконано"
                    d1_exec("raw_sql", {
                        "sql": f"UPDATE pending_analysis SET status='done', finished_at=datetime('now'), output=? WHERE id={req_id}",
                        "params": [output[:5000]],
                    })
                    log.info("Analysis done for #%s", bill_number)
                except Exception as e:
                    d1_exec("raw_sql", {
                        "sql": f"UPDATE pending_analysis SET status='error', finished_at=datetime('now'), output=? WHERE id={req_id}",
                        "params": [str(e)[:5000]],
                    })
                    log.error("Analysis failed for #%s: %s", bill_number, e)

                time.sleep(2)

        except Exception as e:
            log.error("Poll error: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    log.info("Analyze API starting (poll every %ds)", POLL_INTERVAL)
    poll_and_analyze()
