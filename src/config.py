"""Центральна конфігурація — всі налаштування з .env та оточення."""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Завантажуємо .env з кореня проекту
PROJECT_ROOT = Path(__file__).resolve().parent.parent
dotenv_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path)

# === Логування ===
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("radacleaner")

# === D1 API (Cloudflare Worker) ===
WORKER_URL = os.environ.get("WORKER_URL", "https://rada-monitor-api.distih.workers.dev")
SYNC_TOKEN = os.environ.get("CF_SYNC_TOKEN", "")
D1_API_URL = f"{WORKER_URL}/api/sync"
D1_QUERY_URL = f"{WORKER_URL}/api/query"

# === LLM (OpenRouter) ===
LLM_API_URL = "https://openrouter.ai/api/v1"
LLM_MODEL = os.environ.get("LLM_MODEL", "openrouter/owl-alpha")

# === LLM (Google AI Studio / Gemini) ===
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemma-4-31b-it")


def get_llm_key() -> str:
    """Отримує OpenRouter API ключ: спочатку з оточення, потім з .env."""
    key = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("LLM_API_KEY", "")
    if key:
        return key.strip()

    # Спробувати .env ще раз напряму
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY") or line.startswith("LLM_API_KEY"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["OPENROUTER_API_KEY"] = val
                        return val
    except Exception:
        pass

    return ""


LLM_API_KEY = get_llm_key()

# === Telegram ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "") or os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "") or os.environ.get("TG_CHAT_ID", "")

# === RADA API ===
RADA_BASE_URL = "https://data.rada.gov.ua/ogd/zpr/skl9"
RADA_TOKEN_URL = "https://data.rada.gov.ua/api/token"

# === LLM Prompt — імпорт з prompts.py ===
from .prompts import RISK_ANALYSIS_SYSTEM_PROMPT, RISK_ANALYSIS_PROMPT
