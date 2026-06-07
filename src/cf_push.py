"""cf_push.py — Push даних у Cloudflare Worker через POST /api/sync.

Використання:
    from src.cf_push import push_bill, push_risk, push_change_log, push_law_version

    push_bill({"bill_number": "1234", "title": "..."})
    push_risk({"bill_id": 1, "overall_score": 85, ...})

Залежності: requests (встановлено в requirements.txt)
Змінні .env: WORKER_URL, CF_SYNC_TOKEN
"""

import json
import logging
import os
import time
from typing import Any

import requests

from .config import log

# Конфігурація з .env
WORKER_URL = os.environ.get("WORKER_URL", "https://rada-monitor-api.distih.workers.dev")
SYNC_TOKEN = os.environ.get("CF_SYNC_TOKEN", "")
SYNC_URL = f"{WORKER_URL}/api/sync"

log = logging.getLogger(__name__)


def push_to_worker(type_name: str, data: dict, retries: int = 2) -> bool:
    """Відправляє один запис у Worker API.

    Args:
        type_name: Тип даних ('bill', 'risk', 'change_log', 'law_version').
        data: Словник з даними для відправки.
        retries: Кількість повторів при помилці.

    Returns:
        True якщо успішно, False якщо помилка.
    """
    if not SYNC_TOKEN:
        log.warning("CF_SYNC_TOKEN не встановлено — push пропущено")
        return False

    payload = {"type": type_name, "data": data}

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                SYNC_URL,
                json=payload,
                headers={"Authorization": f"Bearer {SYNC_TOKEN}"},
                timeout=30,
            )
            if resp.status_code == 200:
                return True
            else:
                log.warning(
                    "Push %s error (attempt %d/%d): HTTP %d %s",
                    type_name, attempt + 1, retries + 1,
                    resp.status_code, resp.text[:200],
                )
        except requests.exceptions.Timeout:
            log.warning(
                "Push %s timeout (attempt %d/%d)",
                type_name, attempt + 1, retries + 1,
            )
        except requests.exceptions.ConnectionError as e:
            log.warning(
                "Push %s connection error (attempt %d/%d): %s",
                type_name, attempt + 1, retries + 1, str(e)[:100],
            )
        except Exception as e:
            log.warning(
                "Push %s unexpected error (attempt %d/%d): %s: %s",
                type_name, attempt + 1, retries + 1,
                type(e).__name__, str(e)[:200],
            )

        if attempt < retries:
            time.sleep(1.5)

    log.error("Push %s failed after %d attempts", type_name, retries + 1)
    return False


def push_bill(
    bill_number: str,
    title: str = "",
    current_status: str = "new",
    registration_date: str | None = None,
    committee: str = "",
    agenda_category: str = "other",
    url: str = "",
    stage: int = 1,
    act_number: str | None = None,
    act_date: str | None = None,
) -> bool:
    """Відправляє законопроект у Worker.

    Args:
        bill_number: Номер законопроекту (реєстраційний).
        title: Назва.
        current_status: Поточний статус.
        registration_date: Дата реєстрації (YYYY-MM-DD).
        committee: Комітет.
        agenda_category: Категорія.
        url: URL на card картку.
        stage: Етап (1-5).
        act_number: Номер акту (наприклад 4121-IX), тільки для прийнятих.
        act_date: Дата прийняття (YYYY-MM-DD).

    Returns:
        True якщо успішно.
    """
    data = {
        "bill_number": str(bill_number),
        "title": title,
        "current_status": current_status,
        "registration_date": registration_date,
        "committee": committee,
        "agenda_category": agenda_category,
        "url": url,
        "stage": stage,
        "act_number": act_number,
        "act_date": act_date,
    }
    return push_to_worker("bill", data)


def push_risk(
    document_id: int | None,
    bill_id: int | None = None,
    bill_number: str | None = None,
    overall_score: float = 0,
    model_used: str = "",
    budget_risk: str = "{}",
    legal_risk: str = "{}",
    economic_risk: str = "{}",
    social_risk: str = "{}",
    corruption_risk: str = "{}",
    raw_response: str = "{}",
    raw_analysis: str = "",
    json_data: str = "{}",
    legislative_risk: str = "{}",
    official_power_risk: str = "{}",
    vague_norms_risk: str = "{}",
    confidence_level: int = 5,
    insufficient_text: bool = False,
) -> bool:
    """Відправляє оцінку ризиків у Worker.

    Всі JSON-поля приймають рядки (серіалізований JSON),
    або dict/list (будуть серіалізовані автоматично).

    Returns:
        True якщо успішно.
    """
    def js(v: Any) -> str:
        if v is None:
            return "{}"
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    data = {
        "document_id": document_id,
        "bill_id": bill_id,
        "bill_number": bill_number,
        "model_used": model_used,
        "overall_score": float(overall_score),
        "budget_risk": js(budget_risk),
        "legal_risk": js(legal_risk),
        "economic_risk": js(economic_risk),
        "social_risk": js(social_risk),
        "corruption_risk": js(corruption_risk),
        "raw_response": js(raw_response),
        "raw_analysis": raw_analysis,
        "json_data": js(json_data),
        "legislative_risk": js(legislative_risk),
        "official_power_risk": js(official_power_risk),
        "vague_norms_risk": js(vague_norms_risk),
        "confidence_level": confidence_level,
        "insufficient_text": insufficient_text,
    }
    return push_to_worker("risk", data)


def push_change_log(
    bill_id: int | None = None,
    bill_number: str | None = None,
    change_type: str = "new",
    old_value: str | None = None,
    new_value: str | None = None,
) -> bool:
    """Відправляє запис change_log у Worker.

    Args:
        bill_id: ID законопроекту в D1 (або None, якщо використовується bill_number).
        bill_number: Номер законопроекту (альтернатива bill_id).
        change_type: Тип зміни ('new', 'status_change').
        old_value: Попереднє значення (для status_change).
        new_value: Нове значення (для status_change).

    Returns:
        True якщо успішно.
    """
    data = {
        "bill_id": bill_id,
        "bill_number": bill_number,
        "change_type": change_type,
        "old_value": old_value,
        "new_value": new_value,
    }
    return push_to_worker("change_log", data)


def push_law_version(
    law_id: int | None = None,
    bill_number: str | None = None,
    status_at_moment: str = "",
    text_hash: str = "",
    plain_text: str = "",
    analysis_summary: str = "",
    risks_json: str = "{}",
) -> bool:
    """Відправляє версію закону у Worker.

    Args:
        law_id: ID закону в D1 (або None, якщо використовується bill_number).
        bill_number: Номер законопроекту (альтернатива law_id).
        status_at_moment: Статус на момент версії.
        text_hash: Хеш тексту.
        plain_text: Повний текст (до 50000 символів).
        analysis_summary: Аналіз LLM.
        risks_json: JSON з ризиками.

    Returns:
        True якщо успішно.
    """
    def js(v: Any) -> str:
        if v is None:
            return "{}"
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)

    data = {
        "law_id": law_id,
        "bill_number": bill_number,
        "status_at_moment": status_at_moment,
        "text_hash": text_hash,
        "plain_text": plain_text[:50000],
        "analysis_summary": analysis_summary,
        "risks_json": js(risks_json),
    }
    return push_to_worker("law_version", data)


def push_bills_batch(bills: list[dict]) -> tuple[int, int]:
    """Відправляє пачку законопроектів.

    Args:
        bills: Список словників з ключами як у push_bill().

    Returns:
        (done, errors) — кількість успішних та помилкових.
    """
    done = errors = 0
    for b in bills:
        if push_bill(**b):
            done += 1
        else:
            errors += 1
    return done, errors