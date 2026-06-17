"""d1_client.py — Клієнт для роботи з D1 через Worker API.

Замінює psycopg2/db.py. Всі запити йдуть через Cloudflare Worker:
  - SELECT → GET/POST /api/query
  - INSERT/UPDATE/DELETE → POST /api/sync

Використання:
    from src.d1_client import d1_query, d1_exec

    # SELECT
    rows = d1_query("SELECT * FROM bills WHERE stage = ?", [1])

    # INSERT/UPDATE через sync API
    d1_exec("bill", {"bill_number": "1234", "title": "..."})
"""
import json
import logging
import time
from typing import Any

import requests

from .config import D1_API_URL, D1_QUERY_URL, SYNC_TOKEN, log


def d1_query(sql: str, params: list | None = None) -> list[dict]:
    """Виконує SELECT запит до D1 через Worker API.

    Args:
        sql: SQL запит (тільки SELECT).
        params: Список параметрів для prepared statement.

    Returns:
        Список рядків (dicts).
    """
    if not SYNC_TOKEN:
        log.error("CF_SYNC_TOKEN не встановлено")
        return []

    for attempt in range(3):
        try:
            if params and len(params) <= 5:
                # GET для простих запитів (мало параметрів)
                qp = {"sql": sql}
                for i, p in enumerate(params):
                    qp[f"p{i}"] = str(p) if p is not None else ""
                resp = requests.get(
                    D1_QUERY_URL,
                    params=qp,
                    headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
                    timeout=30,
                )
            else:
                # POST для складних запитів
                resp = requests.post(
                    D1_QUERY_URL,
                    json={"sql": sql, "params": params or []},
                    headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
                    timeout=30,
                )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            else:
                log.warning(
                    "d1_query HTTP %d (attempt %d/3): %s",
                    resp.status_code, attempt + 1, resp.text[:200],
                )
        except requests.exceptions.Timeout:
            log.warning("d1_query timeout (attempt %d/3)", attempt + 1)
        except Exception as e:
            log.warning("d1_query error (attempt %d/3): %s", attempt + 1, str(e)[:100])

        if attempt < 2:
            time.sleep(1.5)

    log.error("d1_query failed after 3 attempts: %s", sql[:100])
    return []


def d1_exec(type_name: str, data: dict) -> bool:
    """Виконує INSERT/UPDATE через POST /api/sync.

    Args:
        type_name: Тип даних ('bill', 'risk', 'change_log', 'law_version').
        data: Словник з даними.

    Returns:
        True якщо успішно.
    """
    if not SYNC_TOKEN:
        log.error("CF_SYNC_TOKEN не встановлено")
        return False

    payload = {"type": type_name, "data": data}

    for attempt in range(3):
        try:
            resp = requests.post(
                D1_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
                timeout=30,
            )
            if resp.status_code == 200:
                return True
            else:
                log.warning(
                    "d1_exec %s HTTP %d (attempt %d/3): %s",
                    type_name, resp.status_code, attempt + 1, resp.text[:200],
                )
        except requests.exceptions.Timeout:
            log.warning("d1_exec %s timeout (attempt %d/3)", type_name, attempt + 1)
        except Exception as e:
            log.warning("d1_exec %s error (attempt %d/3): %s", type_name, attempt + 1, str(e)[:100])

        if attempt < 2:
            time.sleep(1.5)

    log.error("d1_exec %s failed after 3 attempts", type_name)
    return False


def d1_exec_sql(sql: str, params: list | None = None) -> bool:
    """Виконує INSERT/UPDATE/DELETE через сирий SQL (через sync API з типом 'raw').

    Потрібно щоб Worker підтримував type='raw'. Поки що — заглушка.
    Для всіх операцій використовуйте d1_exec() з конкретним type_name.
    """
    log.warning("d1_exec_sql не підтримується — використовуйте d1_exec()")
    return False


def refresh_stats_cache() -> bool:
    """Оновлює кеш статистики дашборду в D1."""
    return d1_exec("refresh_stats", {})
