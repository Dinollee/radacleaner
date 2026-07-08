"""Unified LLM client — OpenRouter + NVIDIA API + Google AI (Gemini/Gemma).

Usage:
    from .llm_client import llm_completion, llm_completion_raw

    result = llm_completion(prompt, system_prompt=...)  # dict
    text = llm_completion_raw(prompt, system_prompt=...)  # str
"""
import json
import re
import time
import logging
import threading

import requests

from .config import LLM_API_KEY, LLM_MODEL, GEMINI_API_KEY, GEMINI_MODEL, NVIDIA_API_KEY

log = logging.getLogger(__name__)


class _RateLimiter:
    """Simple sliding window rate limiter (thread-safe)."""
    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.window = 60
        self.timestamps: list[float] = []
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            self.timestamps = [t for t in self.timestamps if now - t < self.window]
            if len(self.timestamps) >= self.max_per_minute:
                oldest = self.timestamps[0]
                sleep_for = self.window - (now - oldest) + 0.5
                if sleep_for > 0:
                    log.info("Rate limit: sleeping %.1fs (gemini %d/%d per min)",
                             sleep_for, len(self.timestamps), self.max_per_minute)
                    time.sleep(sleep_for)
            self.timestamps.append(time.time())


# Gemini: 15 req/min (free tier), 1500 req/day. Use 12/min for safety margin.
_gemini_limiter = _RateLimiter(max_per_minute=12)

# Daily counter for Gemini (1500 req/day limit)
_gemini_daily_count = 0
_gemini_daily_date = ""
_gemini_daily_lock = threading.Lock()
GEMINI_DAILY_LIMIT = 1400  # safety margin below 1500


def _check_gemini_daily():
    """Check and increment daily Gemini counter. Returns True if OK to proceed."""
    global _gemini_daily_count, _gemini_daily_date
    import datetime
    today = datetime.date.today().isoformat()
    with _gemini_daily_lock:
        if _gemini_daily_date != today:
            _gemini_daily_date = today
            _gemini_daily_count = 0
        if _gemini_daily_count >= GEMINI_DAILY_LIMIT:
            log.warning("Gemini daily limit reached (%d/%d)", _gemini_daily_count, GEMINI_DAILY_LIMIT)
            return False
        _gemini_daily_count += 1
        return True

# --- Provider configs ---
PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1",
        "key": LLM_API_KEY,
        "model": LLM_MODEL,
    },
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1",
        "key": NVIDIA_API_KEY,
        "model": "nvidia/nemotron-3-super-120b-a12b",
    },
    "gemini": {
        "url": "https://generativelanguage.googleapis.com/v1beta",
        "key": GEMINI_API_KEY,
        "model": GEMINI_MODEL,
    },
}


def _openrouter_call(messages, model, api_key, max_tokens, temperature, timeout=120):
    """OpenRouter chat completions."""
    resp = requests.post(
        f"https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/radacleaner",
            "X-Title": "Radacleaner",
        },
        json={"model": model, "messages": messages, "temperature": temperature, "max_output_tokens": max_tokens},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if "choices" not in data or not data["choices"]:
        raise RuntimeError(f"OpenRouter response missing 'choices': {str(data)[:300]}")
    return data["choices"][0]["message"]["content"]


def _nvidia_call(messages, model, api_key, max_tokens, temperature, timeout=120):
    """NVIDIA Build API — OpenAI-compatible endpoint."""
    resp = requests.post(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    if "choices" not in data or not data["choices"]:
        raise RuntimeError(f"NVIDIA response missing 'choices': {str(data)[:300]}")
    return data["choices"][0]["message"]["content"]


def _gemini_extract_text(candidate):
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))


def _convert_messages_to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Convert OpenRouter-style messages to Gemini contents format."""
    contents = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"
        contents.append({"role": gemini_role, "parts": [{"text": text}]})
    return contents


def _gemini_call(contents, model, api_key, max_tokens, temperature, timeout=120):
    """Google AI Studio generateContent. Accepts either Gemini contents or OpenRouter messages."""
    if contents and "parts" not in contents[0]:
        contents = _convert_messages_to_gemini_contents(contents)
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        json={
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return _gemini_extract_text(resp.json()["candidates"][0])


def _build_gemini_contents(system_prompt, user_prompt):
    contents = []
    if system_prompt:
        contents.append({"role": "user", "parts": [{"text": system_prompt}]})
        contents.append({"role": "model", "parts": [{"text": "OK, understood."}]})
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})
    return contents


def _parse_json(text):
    if not text:
        return None
    # Strip markdown code blocks
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            return json.loads(m.group(0))
        return None


def _try_provider(name, provider, prompt, system_prompt, max_tokens, temperature):
    """Try a single provider. Returns text or raises."""
    key = provider["key"]
    if not key:
        raise RuntimeError(f"{name}: API key not set")

    model = provider["model"]
    log.info("LLM: trying %s (%s)", name, model)

    if name in ("openrouter", "nvidia"):
        call = _openrouter_call if name == "openrouter" else _nvidia_call
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return call(messages, model, key, max_tokens, temperature)
    elif name == "gemini":
        if not _check_gemini_daily():
            raise RuntimeError("Gemini daily limit reached")
        _gemini_limiter.wait()
        contents = _build_gemini_contents(system_prompt, prompt)
        return _gemini_call(contents, model, key, max_tokens, temperature)
    else:
        raise RuntimeError(f"Unknown provider: {name}")


def _provider_order():
    """Return providers in preferred order: OpenRouter, NVIDIA, Gemini."""
    order = []
    if LLM_API_KEY:
        order.append("openrouter")
    if NVIDIA_API_KEY:
        order.append("nvidia")
    if GEMINI_API_KEY:
        order.append("gemini")
    return order


def llm_completion(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4000,
    max_retries: int = 5,
    messages: list | None = None,
    provider: str | None = None,
) -> dict:
    """Unified LLM call with provider fallback. Returns parsed JSON.

    Args:
        provider: Force a specific provider ("openrouter" or "gemini").
                  If None, tries all available providers in order.
    """
    if provider:
        providers = [(provider, PROVIDERS[provider])]
    else:
        providers = [(n, p) for n, p in PROVIDERS.items() if p["key"]]

    if not providers:
        raise RuntimeError("No LLM API keys configured — set OPENROUTER_API_KEY, NVIDIA_API_KEY, or GEMINI_API_KEY in .env")

    last_exc = None
    for pname, prov in providers:
        for attempt in range(1, max_retries + 1):
            try:
                m = model or prov["model"]
                if pname in ("openrouter", "nvidia") and messages:
                    call = _openrouter_call if pname == "openrouter" else _nvidia_call
                    text = call(messages, m, prov["key"], max_tokens, temperature)
                elif pname == "gemini" and messages:
                    if not _check_gemini_daily():
                        raise RuntimeError("Gemini daily limit reached")
                    _gemini_limiter.wait()
                    text = _gemini_call(messages, m, prov["key"], max_tokens, temperature)
                else:
                    text = _try_provider(pname, prov, prompt, system_prompt, max_tokens, temperature)

                parsed = _parse_json(text)
                if parsed is not None:
                    parsed.setdefault("model_used", m)
                    return parsed
                log.warning("LLM response is not JSON: %s", text[:300])
                return {}

            except requests.exceptions.HTTPError as e:
                last_exc = e
                status = getattr(e.response, 'status_code', 0)
                if status == 429:
                    wait = min(2 ** attempt * 3, 60)
                    try:
                        wait = max(wait, int(e.response.headers.get("retry-after", "0")))
                    except Exception:
                        pass
                    log.warning("LLM 429 from %s, retry %d/%d wait=%ds", pname, attempt, max_retries, wait)
                    time.sleep(wait)
                    continue
                elif status >= 500:
                    log.warning("LLM %d from %s, retry %d/%d", status, pname, attempt, max_retries)
                    time.sleep(2 ** attempt)
                    continue
                else:
                    log.error("LLM %d from %s, skipping provider: %s", status, pname, str(e)[:200])
                    break
            except Exception as e:
                last_exc = e
                log.warning("LLM error from %s (attempt %d/%d): %s", pname, attempt, max_retries, str(e)[:200])
                time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("All LLM providers failed")


def llm_completion_raw(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4000,
    max_retries: int = 5,
    messages: list | None = None,
    provider: str | None = None,
) -> str:
    """Unified LLM call — returns raw text. With provider fallback."""
    if provider:
        providers = [(provider, PROVIDERS[provider])]
    else:
        providers = [(n, p) for n, p in PROVIDERS.items() if p["key"]]

    if not providers:
        raise RuntimeError("No LLM API keys configured")

    last_exc = None
    for pname, prov in providers:
        for attempt in range(1, max_retries + 1):
            try:
                m = model or prov["model"]
                if pname in ("openrouter", "nvidia") and messages:
                    call = _openrouter_call if pname == "openrouter" else _nvidia_call
                    return call(messages, m, prov["key"], max_tokens, temperature)
                return _try_provider(pname, prov, prompt, system_prompt, max_tokens, temperature)

            except requests.exceptions.HTTPError as e:
                last_exc = e
                status = getattr(e.response, 'status_code', 0)
                if status == 429:
                    wait = min(2 ** attempt * 3, 60)
                    log.warning("LLM 429 from %s, retry %d/%d", pname, attempt, max_retries)
                    time.sleep(wait)
                    continue
                elif status >= 500:
                    log.warning("LLM %d from %s, retry %d/%d", status, pname, attempt, max_retries)
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break
            except Exception as e:
                last_exc = e
                log.warning("LLM error from %s (attempt %d/%d): %s", pname, attempt, max_retries, str(e)[:200])
                time.sleep(2 ** attempt)

    raise last_exc or RuntimeError("All LLM providers failed")
