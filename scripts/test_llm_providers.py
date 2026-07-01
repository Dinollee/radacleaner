#!/usr/bin/env python3
"""Unified LLM provider tester — compares free models for Ukrainian law analysis.

Usage:
    ./venv/bin/python scripts/test_llm_providers.py                  # test all providers
    ./venv/bin/python scripts/test_llm_providers.py --provider nvidia # test only NVIDIA
    ./venv/bin/python scripts/test_llm_providers.py --provider openrouter --model nvidia/nemotron-3-super-120b-a12b:free
"""
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

# --- Prompts (match real night_batch analysis) ---

SYSTEM_PROMPT = (
    "Ти — аналітик законодавства України. "
    "Проаналізуй текст законопроекту та поверни JSON з полями: "
    "significance (1-5), impact (1-5), risk_score (1-5), toxicity (0-1), "
    "risk_level (low/medium/high/critical), detailed_risks (список загроз українською), "
    "overall_score (0-100). Відповідай ТІЛЬКИ JSON без пояснень."
)

TEST_BILL = """Проєкт Закону про внесення змін до Закону України "Про свободу пересування та вільний вибір місця проживання в Україні" щодо обмеження реєстрації місця проживання для громадян деяких категорій.

Стаття 1. Внести зміни до статті 6 Закону України "Про свободу пересування та вільний вибір місця проживання в Україні":
1. Доповнити частину другу абзацом такого змісту:
"Реєстрація місця проживання може бути обмежена для осіб, які перебувають під адміністративним наглядом, а також для осіб, засуджених за злочини проти основ національної безпеки України."
"""

# --- Provider registry ---

@dataclass
class Provider:
    name: str
    url: str
    api_key: str
    models: list[str]
    header_auth: str = "Authorization"
    auth_prefix: str = "Bearer "
    timeout: int = 120
    rate_limit_delay: float = 2.0  # seconds between requests
    extra_headers: dict = field(default_factory=dict)

    @property
    def headers(self):
        h = {"Content-Type": "application/json"}
        h[self.header_auth] = f"{self.auth_prefix}{self.api_key}"
        h.update(self.extra_headers)
        return h


def load_providers() -> list[Provider]:
    """Build provider list from .env and hardcoded configs."""
    providers = []

    # --- OpenRouter ---
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not or_key:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    or_key = line.split("=", 1)[1].strip()
    if or_key:
        providers.append(Provider(
            name="openrouter",
            url="https://openrouter.ai/api/v1/chat/completions",
            api_key=or_key,
            models=[
                "nvidia/nemotron-3-super-120b-a12b:free",
                "nvidia/nemotron-3-ultra-550b-a55b:free",
                "nvidia/nemotron-3-nano-30b-a3b:free",
                "google/gemma-4-31b-it:free",
                "openrouter/free",
            ],
            extra_headers={
                "HTTP-Referer": "https://github.com/radacleaner",
                "X-Title": "Radacleaner",
            },
        ))

    # --- NVIDIA API ---
    nv_key = os.environ.get("NVIDIA_API_KEY", "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")
    providers.append(Provider(
        name="nvidia",
        url="https://integrate.api.nvidia.com/v1/chat/completions",
        api_key=nv_key,
        models=[
            "nvidia/nemotron-3-super-120b-a12b",
            "mistralai/mistral-large-3-675b-instruct-2512",
            "mistralai/mistral-medium-3.5-128b",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5",
            "nvidia/nemotron-3-nano-30b-a3b",
            "meta/llama-4-maverick-17b-128e-instruct",
            "meta/llama-3.3-70b-instruct",
            "deepseek-ai/deepseek-v4-flash",
        ],
        rate_limit_delay=2.5,  # NVIDIA: 40 req/min max, use 30 safe margin
    ))

    # --- Google Gemini (direct API) ---
    gkey = os.environ.get("GEMINI_API_KEY", "")
    if not gkey:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    gkey = line.split("=", 1)[1].strip()
    if gkey:
        providers.append(Provider(
            name="gemini",
            url="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            api_key=gkey,
            models=["gemma-4-31b-it"],
            timeout=60,
        ))

    return providers


# --- Test logic ---

def _parse_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _call_openai_compatible(provider: Provider, model: str) -> tuple[str, float]:
    """OpenAI-compatible API (OpenRouter, NVIDIA)."""
    start = time.time()
    resp = requests.post(
        provider.url,
        headers=provider.headers,
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": TEST_BILL},
            ],
            "temperature": 0.1,
            "max_tokens": 2000,
        },
        timeout=provider.timeout,
    )
    elapsed = time.time() - start
    data = resp.json()

    if "error" in data:
        err = data["error"]
        msg = err.get("message", str(err))[:200] if isinstance(err, dict) else str(err)[:200]
        raise RuntimeError(f"API error: {msg}")

    return data["choices"][0]["message"]["content"], elapsed


def _call_gemini(provider: Provider, model: str) -> tuple[str, float]:
    """Google Gemini generateContent API."""
    start = time.time()
    url = provider.url.format(model=model, key=provider.api_key)
    resp = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [
                {"role": "user", "parts": [{"text": SYSTEM_PROMPT}]},
                {"role": "model", "parts": [{"text": "OK, understood."}]},
                {"role": "user", "parts": [{"text": TEST_BILL}]},
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 2000,
            },
        },
        timeout=provider.timeout,
    )
    elapsed = time.time() - start
    data = resp.json()

    if "error" in data:
        msg = data["error"].get("message", str(data["error"]))[:200]
        raise RuntimeError(f"API error: {msg}")

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("No candidates in response")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p["text"] for p in parts if "text" in p and not p.get("thought"))
    return text, elapsed


def test_model(provider: Provider, model: str) -> dict:
    """Test a single model. Returns result dict."""
    print(f"\n  {model} ... ", end="", flush=True)
    try:
        if provider.name == "gemini":
            text, elapsed = _call_gemini(provider, model)
        else:
            text, elapsed = _call_openai_compatible(provider, model)

        parsed = _parse_json(text)
        if parsed is None:
            print(f"PARSE FAIL ({elapsed:.1f}s)")
            return {"model": model, "status": "json_error", "time": elapsed, "raw": text[:200]}

        required = ["significance", "impact", "risk_score", "toxicity", "risk_level", "overall_score"]
        missing = [f for f in required if f not in parsed]
        if missing:
            print(f"MISSING {missing} ({elapsed:.1f}s)")
            return {"model": model, "status": "missing_fields", "time": elapsed, "missing": missing}

        risks = parsed.get("detailed_risks", [])
        print(f"OK ({elapsed:.1f}s, score={parsed.get('overall_score')}, risks={len(risks)})")
        return {
            "model": model,
            "status": "ok",
            "time": elapsed,
            "score": parsed.get("overall_score"),
            "risks_count": len(risks),
            "parsed": parsed,
        }

    except requests.exceptions.Timeout:
        print(f"TIMEOUT ({provider.timeout}s)")
        return {"model": model, "status": "timeout", "time": provider.timeout}
    except Exception as e:
        elapsed = time.time() - start if "start" in dir() else 0
        print(f"ERROR: {str(e)[:100]}")
        return {"model": model, "status": "error", "time": elapsed, "error": str(e)[:150]}


def run_tests(filter_provider: str | None = None, filter_model: str | None = None):
    providers = load_providers()
    all_results = []

    for prov in providers:
        if filter_provider and prov.name != filter_provider:
            continue

        models = [filter_model] if filter_model else prov.models
        print(f"\n{'='*60}")
        print(f"Provider: {prov.name} ({prov.url.split('/')[2]})")
        print(f"Models: {len(models)}")
        print(f"{'='*60}")

        for i, model in enumerate(models):
            r = test_model(prov, model)
            r["provider"] = prov.name
            all_results.append(r)
            if i < len(models) - 1:
                time.sleep(prov.rate_limit_delay)

    # Summary table
    print(f"\n\n{'='*80}")
    print("COMBINED RESULTS — sorted by status then time")
    print(f"{'='*80}")
    print(f"{'Provider':<12} {'Model':<48} {'Status':<10} {'Time':>6} {'Score':>6}")
    print("-" * 85)

    ok = [r for r in all_results if r["status"] == "ok"]
    fail = [r for r in all_results if r["status"] != "ok"]

    for r in sorted(ok, key=lambda x: x.get("time", 999)):
        model = r["model"].replace(":free", "")
        print(f"{r['provider']:<12} {model:<48} {'ok':<10} {r['time']:>5.1f}s {r.get('score', ''):>6}")

    if fail:
        print()
        for r in fail:
            model = r["model"].replace(":free", "")
            print(f"{r['provider']:<12} {model:<48} {r['status']:<10} {r['time']:>5.1f}s")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test LLM providers for Ukrainian law analysis")
    parser.add_argument("--provider", type=str, help="Test only this provider (openrouter/nvidia/gemini)")
    parser.add_argument("--model", type=str, help="Test only this model ID")
    args = parser.parse_args()
    run_tests(args.provider, args.model)
