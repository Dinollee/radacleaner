# 🏛️ Страж Демократії — Моніторинг законопроектів ВРУ

Автоматизований моніторинг законопроектів Верховної Ради України з LLM-аналізом ризиків.
Збирає, аналізує та візуалізує всі законопроєкти ВРУ IX скликання.

## Live Demo

**🌐 Дашборд:** https://radacleaner-dashboard.pages.dev

## Архітектура

```
┌─────────────────────────────────────────────────────────────┐
│                     ВАЖКИЙ СЕРВЕР (Python)                  │
│                                                             │
│  sync_bills.py ──→ RADA API ──→ законопроєкти              │
│  rag_monitor.py ──→ PDF → PyMuPDF → Groq LLM → ризики     │
│                          │                                  │
│                    POST /api/sync                           │
│                          │                                  │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  CLOUDFLARE FREE TIER                       │
│                                                             │
│  🌐 Pages (radacleaner-dashboard.pages.dev) — фронтенд     │
│  ⚡ Worker (rada-monitor-api.distih.workers.dev) — REST API │
│  🗄 D1 (radacleaner-db) — SQLite база                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Стек

- **Бекенд:** Python 3 (RADA API, PyMuPDF, Groq LLM)
- **Фронтенд:** Vanilla HTML/CSS/JS дашборд на Cloudflare Pages
- **API:** Cloudflare Worker (REST, JSON, ~2ms CPU)
- **База:** Cloudflare D1 (SQLite, 15086+ законопроєктів)
- **LLM:** Groq API (`openai/gpt-oss-120b`)

## Дані

- **15,086+** законопроєктів ВРУ IX скликання мігровано до D1
- **40** LLM-оцінок ризиків (Groq)
- Джерело: data.rada.gov.official API

## Структура проекту

```
radacleaner/
├── src/                        # Python пакет
│   ├── config.py               # Конфігурація (.env)
│   ├── db.py                   # Підключення до БД
│   ├── bill_sync.py            # Синхронізація з RADA API + push
│   ├── cf_push.py              # Push даних у Cloudflare Worker
│   ├── groq_client.py          # LLM клієнт (Groq)
│   ├── pdf_utils.py            # PDF → текст
│   ├── rag_engine.py           # LLM-аналіз ризиків + push
│   ├── risk_storage.py         # Збереження оцінок
│   └── telegram_notifier.py    # Telegram сповіщення
├── worker/
│   └── src/index.js            # Cloudflare Worker (REST API + D1)
├── dashboard/
│   └── index.html              # Веб-дашборд (Pages)
├── scripts/
│   ├── migrate_to_d1_fast.py   # Швидка міграція PG → D1
│   └── parse_votes.py          # Парсер голосувань
├── sync_bills.py               # Точка входу синхронізації
├── rag_monitor.py              # Точка входу моніторингу
├── batch_rag_50.py             # Batch-обробка
├── wrangler.jsonc              # Конфігурація Worker
└── wrangler.pages.jsonc        # Конфігурація Pages
```

## Worker API Endpoints

| Method | Endpoint | Опис |
|--------|----------|------|
| GET | `/api/stats` | Статистика (total, byStage, highRisk) |
| GET | `/api/statuses` | Унікальні статуси законів (для фільтру) |
| GET | `/api/bills` | Список законів (limit, offset, stage, status, search, sort) |
| GET | `/api/bills/:id` | Деталі закону + risks + versions + changes |
| GET | `/api/bills/:id/risks` | Оцінка ризиків |
| GET | `/api/bills/:id/votes` | Голосування по закону |
| GET | `/api/votes` | Список голосувань |
| GET | `/api/deputies/:id` | Профіль депутата |
| POST | `/api/sync` | Прийом даних від важкого сервера |

## Dashboard Features

- 📊 **Дашборд** — статика, останні закони, календар пленарних засідань
- 📜 **Законопроєкти** — список з фільтрами (статус, етап 1-5), пошук, пагінація
- 👥 **Депутати** — по фракціях з пошуком
- 🔗 Картка закону → деталі: ризики LLM, версії, історія змін

## Встановлення

```bash
git clone https://github.com/Dinollee/radacleaner.git
cd radacleaner
pip install -r requirements.txt
```

## Cloudflare Deps

```bash
npm install -g wrangler
wrangler login               # авторизація Cloudflare
wrangler deploy              # деплой Worker
wrangler pages deploy dashboard --project-name radacleaner-dashboard  # деплой Pages
```

## Запуск (важкий сервер)

```bash
# Синхронізація з RADA API + push у Worker
python sync_bills.py

# LLM-аналіз нових законів + push
python rag_monitor.py
```

## Ліцензія

MIT
