<p align="center">
  <h1 align="center">🏛️ Страж Демократії</h1>
  <p align="center"><em>Автоматизований моніторинг законопроектів Верховної Ради України IX скликання</em></p>
</p>

<p align="center">
  <a href="https://radacleaner-dashboard.pages.dev">🌐 Дашборд</a> •
  <a href="https://github.com/Dinollee/radacleaner">GitHub</a>
</p>

---

## Що це робить

**Страж Демократії** — це система, яка автоматично збирає всі законопроєкти ВРУ, аналізує їх на ризики за допомогою ШІ та сповіщає громадян через Telegram.

### Можливості

| Компонент | Опис |
|-----------|------|
| 🔄 **Синхронізація** | Автоматичний збір 15,000+ законопроектів з RADA API |
| 🧠 **LLM-аналіз** | Chain of Thought аналіз на ризики з розділенням критичних та абстрактних |
| 📱 **Telegram** | Сповіщення про нові закони, зміни статусів, ризики та щоденний дайджест |
| 📊 **Дашборд** | Веб-інтерфейс з FTS5 пошуком, фільтрами, диффом версій |
| 👥 **Депутати** | ПЯ, ПДА, ВКП — три метрики ефективності, паттерни голосувань |
| 📅 **Голосування** | 8,000+ голосувань, 440+ депутатів, детальна статистика |

---

## Архітектура

```
┌─────────────────────────────────────────────────────────────┐
│                     VPS (Python)                            │
│                                                             │
│  sync_bills.py ────→ RADA JSON API ────→ закони → D1       │
│  sync_active_bills.py → VRU HTML → live статуси → D1       │
│  sync_bill_passings.py → хронологія → D1                   │
│  rag_engine.py ────→ PDF → LLM → ризики → D1              │
│  analyze_api.py ───→ pending_analysis → LLM → risks        │
│  sync_mp_factions.py → фракції + дати (RADA HTML) → D1     │
│  sync_mp_stats.py ──→ ПЯ/ПДА/ВКП депутатів → D1           │
│  sync_votes_bulk.py → голосування → D1                     │
│  monitor.py ────→ change_log → Telegram                    │
│  daily_digest_llm.py → щоденний дайджест → Telegram        │
│                          │                                  │
│                    POST /api/sync                           │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  CLOUDFLARE                                  │
│                                                             │
│  🌐 Pages    — дашборд (Vanilla JS, SPA)                   │
│  ⚡ Worker   — REST API + FTS5 search                       │
│  🗄 D1       — SQLite (15K+ bills, 7.5M mp_votes)          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Стек технологій

| Шар | Технологія |
|-----|-----------|
| **Бекенд** | Python 3 + PyMuPDF + OpenRouter LLM |
| **API** | Cloudflare Worker (JavaScript) |
| **База** | Cloudflare D1 (SQLite) + FTS5 |
| **Фронтенд** | Vanilla HTML/CSS/JS (Cloudflare Pages) |
| **Сповіщення** | Telegram Bot API |
| **LLM** | OpenRouter `owl-alpha` (1M context, безкоштовно) |

---

## Структура проекту

```
radacleaner/
├── src/                        # Python package
│   ├── config.py               # Конфігурація + імпорти промтів
│   ├── prompts.py              # LLM промти (аналіз ризиків)
│   ├── bill_sync.py            # Синхронізація законів: RADA JSON → D1
│   ├── rag_engine.py           # LLM аналіз ризиків (Chain of Thought)
│   ├── risk_storage.py         # Збереження оцінок ризиків
│   ├── pdf_utils.py            # PDF → текст (PyMuPDF)
│   ├── groq_client.py          # OpenRouter API клієнт
│   ├── d1_client.py            # HTTP клієнт до Worker (D1)
│   └── telegram_notifier.py    # Telegram бот
├── worker/
│   └── src/index.js            # Cloudflare Worker (REST API + FTS5)
├── dashboard/
│   └── index.html              # Дашборд (SPA, 4 секції)
├── migrations/                 # D1 SQL міграції (001-013)
├── sync_bills.py               # Entry: синхронізація законів
├── sync_active_bills.py        # Live VRU HTML перевірка (30-денні bills)
├── sync_bill_passings.py       # Хронологія проходження законів
├── sync_votes_bulk.py          # Пакетна синхронізація голосувань
├── sync_votes.py               # Синхронізація голосувань (окремі)
├── sync_mp_stats.py            # Перерахунок статистики депутатів
├── sync_mp_factions.py         # Синхронізація фракцій + дат
├── sync_mp_bills.py            # Активність депутатів у законотворчості
├── analyze_api.py              # Сервіс LLM аналізу (черга pending_analysis)
├── analyze_bill.py             # CLI: аналіз окремого закону
├── daily_digest_llm.py         # Щоденний Telegram дайджест
├── monitor.py                  # Telegram сповіщення з change_log
├── rag_monitor.py              # Моніторинг законопроектів з LLM
└── wrangler.jsonc              # Конфігурація Cloudflare
```

---

## Міграції D1

| Міграція | Опис |
|----------|------|
| 001 | Базова схема bills |
| 002 | D1 схема (bills, risk_assessments, change_log) |
| 003 | Додавання act_number |
| 004 | Додавання start_date для депутатів |
| 005 | Таблиця mp_bills (закони депутатів) |
| 006 | FTS5 повнотекстовий пошук (bills_fts) |
| 007 | Таблиця mp_stats (метрики депутатів) |
| 008 | Виправлення bill_documents |
| 009 | Виправлення FTS5 indexed |
| 010 | Додавання is_procedural |
| 011 | Таблиця pending_analysis (черга LLM) |
| 012 | Таблиця stats_cache + стовпчик risk_level |
| 013 | Додавання vote_date до mp_votes + індекс |

---

## API Endpoints

| Метод | Ендпоінт | Опис |
|-------|----------|------|
| GET | `/api/stats` | Загальна статистика (з cache) |
| GET | `/api/bills` | Список законів (FTS5 пошук, фільтри, пагінація) |
| GET | `/api/bills/:id` | Деталі: ризики, хронологія, документи, голосування |
| GET | `/api/bills/:id/versions` | Версії закону (для диффу) |
| GET | `/api/bills/:id/risks` | Оцінка ризиків LLM |
| GET | `/api/bills/:id/votes` | Голосування по закону |
| GET | `/api/deputies` | Список депутатів (ПЯ, ПДА, ВКП з D1) |
| GET | `/api/deputies/:name` | Профіль депутата + історія голосувань (пагінація) |
| GET | `/api/votes` | Список голосувань |
| GET | `/api/factions` | Список фракцій |
| GET | `/api/plenary-sessions` | Календар засідань |
| POST | `/api/sync` | Прийом даних від Python-сервера |

---

## Автоматизація (systemd)

```bash
# Перевірка статусу
systemctl list-timers | grep -E 'sync|monitor|radacleaner|digest'

# Логи
journalctl -u sync_bills.service -f
journalctl -u radacleaner-mpstats.service -f
journalctl -u radacleaner-votesync.service -f
journalctl -u digest.service -f
journalctl -u radacleaner-analyze.service -f
```

| Сервіс | Інтервал | Що робить |
|--------|----------|-----------|
| `sync_bills.timer` | щогодини | Bill sync: list + full JSON + passings |
| `sync_active_bills.timer` | кожні 30 хв | Live VRU HTML check (30-денні bills) |
| `monitor.timer` | кожні 30 хв | Telegram notifications з change_log |
| `radacleaner-mpstats.timer` | щодня ~01:00 | Фракції + дати + ПЯ/ПДА/ВКП депутатів |
| `radacleaner-votesync.timer` | кожні 6 год | Синхронізація голосувань |
| `digest.timer` | щодня 09:00 | Щоденний Telegram дайджест (fallback) |
| `digest-llm.timer` | щодня 20:00 | Щоденний дайджест з LLM |

---

## LLM-аналіз (Chain of Thought)

Система використовує двоетапний аналіз:

1. **Етап 1 — Класифікація**: визначення чи є закон процедурним чи непроцедурним
2. **Етап 2 — Аналіз ризиків**: глибокий аналіз тільки для непроцедурних законів

**Формат відповіді LLM:**
```json
{
  "is_procedural": false,
  "has_risks": true,
  "risk_level": "high",
  "summary": "Стислий опис суті змін",
  "law_summary": "Повний опис суті закону (3-5 речень)",
  "detailed_risks": ["Ризик 1: опис..."],
  "insufficient_text": false
}
```

**Автоматична черга**: `process_full_data()` додає bills в `pending_analysis` після індексації документів (тільки для законів, які вже мали документи в БД). `analyze_api.py` (безперервний сервіс) опитує чергу і запускає аналіз.

### Правила аналізу ризиків

1. **Розділення ризиків на критичні та абстрактні:**
   - **Критичні** — конкретні, вимірювані наслідки: порушення міжнародних зобов'язань (МВФ, ЄС, OECD), діра в бюджеті, корупція з конкретними сумами або механізмом
   - **Абстрактні/надумані** — загальні твердження без конкретики: «порушення прав громадян», «дискримінація платників» — НЕ включаються до detailed_risks

2. **Фактор форс-мажору / критичної інфраструктури:**
   - Закони про енергетику, оборону, безпеку, воєнний стан — знижуємо «токсичність» оцінки
   - Тимчасові пільги для критичної інфраструктури = обґрунтована необхідність, а не «дискримінація»

### Метрики депутатів

- **ПЯ** (Індекс явки) = (yes + no + abstain) / total
- **ПДА** (Діяльне участь) = (yes + no) / (yes + no + abstain)
- **ВКП** (Зважений КПД) = Σ(weight × action) / Σ(weight)

Метрики кешуються в таблиці `mps` і оновлюються щодня разом з фракціями.

---

## Оптимізація D1 Free Tier

| Ресурс | Використання | Ліміт | Стратегія |
|--------|-------------|-------|-----------|
| Reads | ~417K/день | 5M | Кеш `stats_cache`, мінімізація JOIN |
| Writes | ~51K/день | 100K | Пакетні оновлення, batch INSERT |
| Storage | ~1.4GB | 5GB | Індекси, стиснення тексту |

**Ключові оптимізації:**
- `/api/stats` читає 1 рядок з `stats_cache` замість 10+ запитів
- Депутати використовують кешовані py/pda/vkp з `mps` замість перерахунку з mp_votes
- `vote_date` денормалізовано в `mp_votes` — не потрібен JOIN з `votes` для сортування
- Пагінація депутатів: limit/offset з мінімальним часом відповіді

---

## Щоденний дайджест

`daily_digest_llm.py` генерує Telegram-дайджест з:

1. **Даних з нашої БД:** кількість законів, зміни за день, високоризикові закони
2. **Новин з RADA:** нові законопроєкти (з номерами), новини комітетів/фракцій
3. **Новин з ЗМІ:** Українська правда, Європейська правда

**Джерела новин ВРУ:**
- `/news` — основна сторінка (нові законопроєкти з посиланнями на itd.rada.gov.ua)
- `/news/news_kom/` — новини комітетів
- `/news/news_fr/` — новини фракцій
- `/rss` — RSS-фід (запасне джерело)

---

## Деплой

```bash
# Зміни в Worker (index.js)
cd worker && CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN npx wrangler deploy

# Зміни в дашборді
CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN npx wrangler pages deploy dashboard --project-name radacleaner-dashboard

# Зміни в Python — просто git push (авто-синхронізація через systemd)
```

### Деплой міграцій D1

```bash
CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID=$CLOUDFLARE_ACCOUNT_ID \
  npx wrangler d1 execute radacleaner-db --remote --file=migrations/0XX_name.sql
```

---

## Встановлення

```bash
git clone https://github.com/Dinollee/radacleaner.git
cd radacleaner
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заповніть свої дані
```

---

## Технічні деталі

### FTS5 повнотекстовий пошук

Віртуальна таблиця `bills_fts` з токенізатором `unicode61 remove_diacritics 2`. Тригери автоматично синхронізують дані при INSERT/UPDATE/DELETE. Префіксний пошук для часткових збігів.

### Рекурсивний чанкинг

Ієрархія роздільників: `\n\n` → `\n` → ` ` → `""`. Параметри: max_size=600, overlap=100. Ідеально для юридичних текстів зі структурованими статтями.

### Денормалізація vote_date

Стовпчик `vote_date` в `mp_votes` дозволяє сортування без JOIN з таблицею `votes`. Індекс `idx_mv_mp_name_date` прискорює запити за депутатом + датою.

### Кешування статистики

Таблиця `stats_cache` зберігає попередньо обчислену статистику. Оновлюється після кожної синхронізації голосувань або депутатів. `/api/stats` читає 1 рядок замість виконання 10+ запитів.

---

## Ліцензія

MIT
