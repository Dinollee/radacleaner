"""Конфігурація та налаштування."""
import os
import re
import subprocess

# === Database ===
DB_HOST = os.environ.get("DB_HOST", "192.168.1.229")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "my_bills")
DB_USER = os.environ.get("DB_USER", "hermes")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "hermes")

# === Groq ===
GROQ_API_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-120b"


def get_groq_key():
    """Отримує Groq API ключ з конфігурації."""
    # Спочатку з environment
    key = os.environ.get("GROQ_API_KEY", "")
    if key:
        return key
    # Потім з config.yaml Hermes
    try:
        result = subprocess.run(
            ["bash", "-c", "grep GROQ_API_KEY /root/.hermes/config.yaml | sed 's/.*: //'"],
            capture_output=True, text=True, timeout=5
        )
        key = result.stdout.strip()
        if key and key.startswith("gsk_"):
            return key
    except Exception:
        pass
    return ""


GROQ_API_KEY = get_groq_key()

# === Telegram ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# === RADA API ===
RADA_BASE_URL = "https://data.rada.gov.ua/ogd/zpr/skl9"
RADA_TOKEN_URL = "https://data.rada.gov.ua/api/token"

# === LLM Prompt ===
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
