"""Уніфікований LLM клієнт (OpenRouter) з ретраями + multi-turn conversations."""
import json
import re
import time
import logging

import requests

from .config import LLM_API_KEY, LLM_API_URL, LLM_MODEL, RISK_ANALYSIS_SYSTEM_PROMPT

log = logging.getLogger(__name__)


def groq_completion(
    prompt: str,
    system_prompt: str = RISK_ANALYSIS_SYSTEM_PROMPT,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1600,
    max_retries: int = 5,
    messages: list | None = None,
) -> dict:
    """Надіслати запит до LLM API (OpenRouter), повернути розпаршений JSON.

    Args:
        prompt: Текст запиту (user content).
        system_prompt: System message.
        model: Модель (за замовчуванням LLM_MODEL з конфіга).
        temperature: Температура генерації.
        max_tokens: Максимум токенів у відповіді.
        max_retries: Кількість ретраїв при 429 або помилках.
        messages: Готовий список messages для multi-turn (перевизначає system+user).

    Returns:
        Розпаршений JSON dict.

    Raises:
        RuntimeError: Якщо всі ретраї вичерпано або ключ відсутній.
    """
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set — перевірте .env або змінні оточення")

    model = model or LLM_MODEL
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/radacleaner",
        "X-Title": "Radacleaner",
    }

    if messages:
        msg_list = messages
    else:
        msg_list = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    payload = {
        "model": model,
        "messages": msg_list,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                f"{LLM_API_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )

            if resp.status_code == 429:
                wait = min(2 ** attempt, 60)
                try:
                    wait = max(wait, int(resp.headers.get("retry-after", "0")))
                except Exception:
                    pass
                log.warning("LLM rate limited (429), retry %d/%d wait=%ds", attempt, max_retries, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            log.debug("LLM raw response: %s", text[:500])

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{[\s\S]+\}", text)
                if m:
                    return json.loads(m.group(0))
                log.error("LLM response is not valid JSON: %s", text[:500])
                return {}

        except Exception as e:
            last_exc = e
            log.warning("LLM error (attempt %d/%d): %s: %s",
                        attempt, max_retries, type(e).__name__, str(e)[:200])
            time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("LLM failed after all retries")


def groq_completion_raw(
    prompt: str,
    system_prompt: str = RISK_ANALYSIS_SYSTEM_PROMPT,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1600,
    max_retries: int = 5,
    messages: list | None = None,
) -> str:
    """Надіслати запит до LLM, повернути raw text (не JSON).

    Використовується для чанкованого аналізу де LLM повертає текстовий аналіз.
    """
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not set")

    model = model or LLM_MODEL
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/radacleaner",
        "X-Title": "Radacleaner",
    }

    if messages:
        msg_list = messages
    else:
        msg_list = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

    payload = {
        "model": model,
        "messages": msg_list,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                f"{LLM_API_URL}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            )

            if resp.status_code == 429:
                wait = min(2 ** attempt, 60)
                try:
                    wait = max(wait, int(resp.headers.get("retry-after", "0")))
                except Exception:
                    pass
                log.warning("LLM rate limited (429), retry %d/%d wait=%ds", attempt, max_retries, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        except Exception as e:
            last_exc = e
            log.warning("LLM error (attempt %d/%d): %s: %s",
                        attempt, max_retries, type(e).__name__, str(e)[:200])
            time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("LLM failed after all retries")
