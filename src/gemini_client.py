"""Gemini API клієнт — Google AI Studio (Gemma 4, Gemini)."""
import json
import re
import time
import logging

import requests

log = logging.getLogger(__name__)

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"


def _extract_text(candidate: dict) -> str:
    """Извлечь текст из ответа, отфильтровав thinking-части."""
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))


def gemini_completion(
    prompt: str,
    system_prompt: str = "",
    api_key: str = "",
    model: str = "gemma-4-31b-it",
    temperature: float = 0.1,
    max_tokens: int = 1600,
    max_retries: int = 3,
) -> dict:
    """Gemini API call, returns parsed JSON."""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    url = f"{GEMINI_API_URL}/models/{model}:generateContent?key={api_key}"

    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": system_prompt}]})
        contents.append({"role": "model", "parts": [{"text": "OK, understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=120)

            if resp.status_code == 429:
                wait = min(2 ** attempt * 5, 60)
                log.warning("Gemini rate limited (429), retry %d/%d wait=%ds", attempt, max_retries, wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            text = _extract_text(data["candidates"][0])

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{[\s\S]+\}", text)
                if m:
                    return json.loads(m.group(0))
                log.error("Gemini response is not valid JSON: %s", text[:500])
                return {}

        except Exception as e:
            last_exc = e
            log.warning("Gemini error (attempt %d/%d): %s: %s",
                        attempt, max_retries, type(e).__name__, str(e)[:200])
            time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("Gemini failed after all retries")


def gemini_completion_raw(
    prompt: str,
    system_prompt: str = "",
    api_key: str = "",
    model: str = "gemma-4-31b-it",
    temperature: float = 0.1,
    max_tokens: int = 1600,
    max_retries: int = 3,
) -> str:
    """Gemini API call, returns raw text."""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    url = f"{GEMINI_API_URL}/models/{model}:generateContent?key={api_key}"

    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": system_prompt}]})
        contents.append({"role": "model", "parts": [{"text": "OK, understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=120)

            if resp.status_code == 429:
                wait = min(2 ** attempt * 5, 60)
                log.warning("Gemini rate limited, retry %d/%d", attempt, max_retries)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            return _extract_text(data["candidates"][0])

        except Exception as e:
            last_exc = e
            log.warning("Gemini error (attempt %d/%d): %s", attempt, max_retries, str(e)[:200])
            time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("Gemini failed after all retries")
