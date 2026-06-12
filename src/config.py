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

# === LLM Prompt (Chain of Thought — етапна перевірка) ===
RISK_ANALYSIS_SYSTEM_PROMPT = (
    "Ти — головний юридичний аналітик моніторингового центру, "
    "глибоко спеціалізований на законодавстві України, нормативно-правовій базі Верховної Ради, "
    "регламентах та вимогах до гармонізації українського права з директивами ЄС (acquis communautaire). "
    "Відповідай українською, ТІЛЬКИ JSON без додаткових коментарів або Markdown-оберток."
)

RISK_ANALYSIS_PROMPT = """Твоя задача — виявити приховані ризики, фінансові загрози, зміни в кодексах/регламентах та потенційні маркери загроз демократичним інститутам, звуженню прав громадян або зриву зобов'язань з євроінтеграції України в тексті законопроєкту.

Тобі на вхід подається текст, розбитий на пронумеровані фрагменти (чанки).
Дій строго за наступними етапами (Chain of Thought):

ЕТАП 1. ФІЛЬТРАЦІЯ ТА СКОРИНГ (Внутрішній пошук)
Уважно прочитай всі чанки. Обери фрагменти, які містять реальне юридичне, інституційне або фінансове навантаження.
Ігноруй «воду» та декларативні преамбули. Особливу увагу звертай на:
- Зміни статей кодексів (КК, КПК, Податковий, Бюджетний тощо).
- Санкції, штрафи, обмеження прав, нові обов'язки або регуляторні бар'єри.
- Рух, розподіл або контроль фінансових ресурсів (кошти, бюджет, закупівлі, податки).
- Норми, які можуть створювати корупційні ризики, порушувати баланс влади, послаблювати незалежні інститути (загрози демократії).
- Невідповідність регламентам ЄС, затягування реформ або норми, що суперечать міжнародним зобов'язанням України (ризики євроінтеграції).

Виведи для себе список номерів критичних чанків.

ЕТАП 2. АНАЛІЗ РИЗИКІВ
Проведи глибокий аналіз виключно обраних на Етапі 1 чанків. Якщо критичних чанків не виявлено (наприклад, закон має виключно технічний або термінологічний характер), проаналізуй перші 3 чанки.

ФОРМАТ ВІДПОВІДІ (Виведи строго у форматі JSON, без зайвого тексту та Markdown-оберток у вигляді ```json):
{{
  "analyzed_chunks": [номери обраних фрагментів],
  "has_risks": true/false,
  "risk_level": "low/medium/high",
  "summary": "Стисле та прагматичне опис суті змін",
  "law_summary": "Повний опис суті закону: хто ініціює, що змінюється в чинному законодавстві, які нові механізими або обов'язки вводяться, на кого поширюється дія. 3-5 речень, без обривів, без Markdown.",
  "detailed_risks": [
    "Конкретний ризик (з посиланням на чанк). Опис, до чого це призведе в контексті фінансів, законодавства, демократичних інститутів або інтеграції в ЄС."
  ],
  "insufficient_text": false
}}

Правила:
- Не вигадуй наслідки — аналізуй лише наведений текст.
- Не використовуй політичні лозунги або емоційні оцінки.
- Якщо текст обмежений або неповний — зазнач це в summary та встанови insufficient_text: true.
- Кожен ризик має бути підкріплений конкретним посиланням на чанк.
- Якщо ризиків не виявлено — поверни has_risks: false, detailed_risks: [].

Текст законопроєкту:
{text}"""
