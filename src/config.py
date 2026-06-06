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

# === Database ===
DB_HOST = os.environ.get("DB_HOST", "192.168.1.229")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "my_bills")
DB_USER = os.environ.get("DB_USER", "hermes")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "hermes")

DB_PARAMS = {
    "host": DB_HOST,
    "port": DB_PORT,
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
}

# === Groq (LLM) ===
GROQ_API_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def get_groq_key() -> str:
    """Отримує Groq API ключ: спочатку з оточення, потім з .env, потім з Hermes config."""
    key = os.environ.get("GROQ_API_KEY", "") or os.environ.get("GROQ_TOKEN", "")
    if key:
        return key.strip()

    # Спробувати .env ще раз напряму (якщо змінна є в файлі, але не в os.environ)
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY") or line.startswith("GROQ_TOKEN"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["GROQ_API_KEY"] = val
                        return val
    except Exception:
        pass

    # /root/.hermes/config.yaml (Hermes бот)
    try:
        import subprocess
        result = subprocess.run(
            ["bash", "-c", "grep GROQ_API_KEY /root/.hermes/config.yaml | sed 's/.*: //'"],
            capture_output=True, text=True, timeout=5,
        )
        key = result.stdout.strip()
        if key and (key.startswith("gsk_") or len(key) > 20):
            os.environ["GROQ_API_KEY"] = key
            return key
    except Exception:
        pass

    return ""


GROQ_API_KEY = get_groq_key()

# === Telegram ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "") or os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "") or os.environ.get("TG_CHAT_ID", "")

# === RADA API ===
RADA_BASE_URL = "https://data.rada.gov.ua/ogd/zpr/skl9"
RADA_TOKEN_URL = "https://data.rada.gov.ua/api/token"

# === LLM Prompt (новий формат — risks[]) ===
RISK_ANALYSIS_PROMPT = """Ти — незалежний аналітик законодавства. Проаналізуй текст законопроєкту та вияви ризики для демократії, прав громадян, бюджету та верховенства права.
Відповідай українською, ТІЛЬКИ JSON, без додаткового тексту.
Будь прагматичним і фактологічним. Не давай політичних оцінок, не використовуй емоційний мову.
Кожен ризик підкріплюй точною цитатою з тексту.
Якщо текст не містить ризиків — поверни пустий масив risks.
Формат відповіді:
{{
  "summary": "3-4 речення: що конкретно змінюється на практиці, які механізми вводяться або скасовуються. Без декларативних преамбул.",
  "risks": [
    {{
      "category": "Corruption | Budgetary | Legal Collision | Ambiguity | Civil Rights | Power Concentration | Other",
      "severity": "Low | Medium | High",
      "quote": "Точна цитата з тексту закону (1-2 речення)",
      "explanation": "Об'єктивне пояснення: чому ця норма є ризиком, які наслідки можуть бути на практиці"
    }}
  ]
}}
Правила:
- Не вигадуй наслідки — аналізуй лише наведений текст.
- Не використовуй політичні лозунги або емоційні оцінки.
- Якщо текст обмежений або неповний — зазнач це в summary.
- Кожен ризик має бути підкріплений конкретною цитатою з тексту.
- Категорія має бути однією з: Corruption, Budgetary, Legal Collision, Ambiguity, Civil Rights, Power Concentration, Other.
- Якщо ризиків не виявлено — поверни [].

Текст законопроєкту:
{text}"""

RISK_ANALYSIS_SYSTEM_PROMPT = "Ти — незалежний експерт з українського законодавства. Відповідай українською, лише JSON без додаткових коментарів."