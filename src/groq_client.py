"""Уніфікований Groq LLM клієнт з ретраями."""
import json
import re
import time
import logging

import requests

from .config import GROQ_API_KEY, GROQ_API_URL, GROQ_MODEL, RISK_ANALYSIS_SYSTEM_PROMPT

log = logging.getLogger(__name__)


def groq_completion(
    prompt: str,
    system_prompt: str = RISK_ANALYSIS_SYSTEM_PROMPT,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1600,
    max_retries: int = 5,
) -> dict:
    """Надіслати запит до Groq API, повернути розпаршений JSON.

    Args:
        prompt: Текст запиту (user content).
        system_prompt: System message.
        model: Модель (за замовчуванням GROQ_MODEL з конфіга).
        temperature: Температура генерації.
        max_tokens: Максимум токенів у відповіді.
        max_retries: Кількість ретраїв при 429 або помилках.

    Returns:
        Розпаршений JSON dict.

    Raises:
        RuntimeError: Якщо всі ретраї вичерпано або ключ відсутній.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set — перевірте .env або змінні оточення")

    model = model or GROQ_MODEL
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(
                f"{GROQ_API_URL}/chat/completions",
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
                # Спроба витягти JSON з тексту
                m = re.search(r"\{[\s\S]+\}", text)
                if m:
                    return json.loads(m.group(0))
                log.error("LLM response is not valid JSON: %s", text[:500])
                return {}  # повертаємо пустий dict, щоб не впасти

        except Exception as e:
            last_exc = e
            log.warning("LLM error (attempt %d/%d): %s: %s",
                        attempt, max_retries, type(e).__name__, str(e)[:200])
            time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("LLM failed after all retries")