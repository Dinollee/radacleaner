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

# === LLM Prompt (Chain of Thought — класифікація + аналіз ризиків) ===
RISK_ANALYSIS_SYSTEM_PROMPT = (
    "Ти — головний юридичний аналітик моніторингового центру, "
    "глибоко спеціалізований на законодавстві України, нормативно-правовій базі Верховної Ради, "
    "регламентах та вимогах до гармонізації українського права з директивами ЄС (acquis communautaire). "
    "Відповідай українською, ТІЛЬКИ JSON без додаткових коментарів або Markdown-оберток."
)

RISK_ANALYSIS_PROMPT = """Тобі надано повний текст законопроєкту Верховної Ради України.

ЕТАП 1: КЛАСИФІКАЦІЯ
Визнач, чи є цей закон ПРОЦЕДУРНИМ чи НЕПРОЦЕДУРНИМ.

ПРОЦЕДУРНІ закони (не змінюють правових норм, не впливають на життя громадян):
- Заяви та звернення ВРУ (до органів влади, міжнародних організацій)
- Зміни до регламенту ВРУ
- Організаційні питання (створення комітетів, призначення на посади)
- Кадрові призначення (судді, члени комісій, посадовці)
- Державні свята, пам'ятні дати, вшанування
- Протокольні рішення

НЕПРОЦЕДУРНІ закони (впливають на життя громадян, демократію, права):
- Зміни до кодексів (КК, КПК, Податковий, Бюджетний, Цивільний тощо)
- Нові санкції, штрафи, обмеження прав
- Бюджетні зміни, рух фінансових ресурсів
- Зміни виборчого законодавства
- Обмеження свободи слова, ЗМІ, зібрань
- Корупційні ризики, зміни правил закупівель
- Зміни в оборонній та безпековій сфері
- Євроінтеграційні зобов'язання та гармонізація з правом ЄС

ЕТАП 2: АНАЛІЗ РИЗИКІВ (тільки якщо закон НЕПРОЦЕДУРНИЙ)
Проведи глибокий аналіз тексту закону. Вияви:
- Зміни статей кодексів, нові санкції, обмеження прав
- Бюджетні наслідки, рух фінансових ресурсів
- Корупційні ризики, порушення балансу влади
- Загрози демократичним інститутам, свободі ЗМІ, правам громадян
- Невідповідність регламентам ЄС, зрив євроінтеграції

Якщо закон ПРОЦЕДУРНИЙ — пропусти Етап 2, встанови has_risks: false, risk_level: null.

ФОРМАТ ВІДПОВІДІ (строго JSON, без Markdown-оберток):
{{
  "is_procedural": true/false,
  "classification_reason": "Стисле пояснення чому закон процедурний або непроцедурний",
  "has_risks": true/false,
  "risk_level": "low/medium/high/null",
  "summary": "Стисле опис суті змін (1-2 речення)",
  "law_summary": "Повний опис суті закону: хто ініціює, що змінюється в чинному законодавстві, які нові механізми або обов'язки вводяться, на кого поширюється дія. 3-5 речень, без обривів.",
  "detailed_risks": [
    "Конкретний ризик з посиланням на статтю/норму закону. Опис наслідків для фінансів, законодавства, демократії або ЄС-інтеграції."
  ],
  "insufficient_text": false
}}

Правила:
- Не вигадуй наслідки — аналізуй лише наведений текст.
- Якщо текст обмежений або неповний — insufficient_text: true.
- Якщо ризиків не виявлено — has_risks: false, detailed_risks: [].

Текст законопроєкту:
{text}"""
