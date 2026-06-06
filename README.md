# radacleaner — Моніторинг законопроектів ВРУ

Автоматизований моніторинг законопроектів Верховної Ради України з LLM-аналізом ризиків.

## Архітектура

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  sync_bills.py  │────▶│   change_log    │────▶│  rag_monitor.py │
│  (кожну годину) │     │   (в БД)        │     │  (кожну годину) │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                              ┌──────────────────────────┤
                              ▼                          ▼
                    ┌──────────────────┐      ┌──────────────────┐
                    │ Новий закон     │      │ Зміна статусу   │
                    │ PDF→LLM→TG      │      │ Тільки TG       │
                    └──────────────────┘      └──────────────────┘
```

## Встановлення

```bash
# Клонування
git clone https://github.com/Dinollee/radacleaner.git
cd radacleaner

# Залежності
pip install -r requirements.txt

# Конфігурація
cp .env.example .env
# Відредагуйте .env — заповніть GROQ_API_KEY, TG_BOT_TOKEN, DB_PASSWORD

# Ініціалізація БД
psql -h <db_host> -U <db_user> -d <db_name> -f migrations/001_initial.sql
```

## Запуск

```bash
# Швидка синхронізація (перевірка ETag)
python sync_bills.py list

# Повна синхронізація
python sync_bills.py full

# Моніторинг + аналіз ризиків
python rag_monitor.py

# Batch-обробка існуючих документів
python batch_rag_50.py --limit 10

# Тестовий запуск (без Telegram)
python rag_monitor.py --test
```

## Cron

```cron
# Синхронізація кожну годину (за 5 хвилин до моніторингу)
55 * * * * cd /path/to/radacleaner && python sync_bills.py list >> /tmp/sync.log 2>&1

# Моніторинг кожну годину
0 * * * * cd /path/to/radacleaner && python rag_monitor.py >> /tmp/rag_monitor.log 2>&1

# Повна синхронізація раз на день
0 3 * * * cd /path/to/radacleaner && python sync_bills.py full >> /tmp/sync_full.log 2>&1
```

## Структура проекту

```
radacleaner/
├── src/                    # Пакет з логікою
│   ├── __init__.py         # Метадата пакету
│   ├── config.py           # Конфігурація з .env
│   ├── db.py               # Підключення до БД
│   ├── bill_sync.py        # Синхронізація з RADA API
│   ├── groq_client.py      # LLM клієнт (Groq)
│   ├── pdf_utils.py        # PDF завантаження/текст/чанки
│   ├── rag_engine.py       # LLM-аналіз ризиків
│   ├── risk_storage.py     # Збереження оцінок в БД
│   └── telegram_notifier.py # Telegram сповіщення
├── scripts/
│   ├── deploy.sh           # Деплой дашборду
│   └── parse_votes.py      # Парсер голосувань (WIP)
├── dashboard/
│   └── index.html          # Веб-дашборд
├── sync_bills.py           # ← тонка обгортка
├── rag_monitor.py           # ← тонка обгортка
├── batch_rag_50.py          # ← тонка обгортка
└── .env                    # Конфігурація (в gitignore)
```

## LLM-аналіз

Використовується Groq API (`openai/gpt-oss-120b`). Кожен законопроект аналізується за критеріями:
- **Corruption** — корупційні ризики
- **Budgetary** — бюджетні ризики
- **Legal Collision** — колізії з іншими законами
- **Ambiguity** — розмиті норми
- **Civil Rights** — права громадян
- **Power Concentration** — концентрація влади

## Структура БД

- `bills` — законопроекти
- `law_versions` — історія версій
- `change_log` — лог змін (для моніторингу)
- `risk_assessments` — оцінки ризиків від LLM
- `rag_documents` / `rag_chunks` — документи та чанки тексту
- `sync_state` — стан синхронізації (ETag)
- `votes` / `mp_votes` / `mps` — голосування (WIP)

## Ліцензія

MIT