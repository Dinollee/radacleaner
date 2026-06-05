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
git clone https://github.com/yourname/radacleaner.git
cd radacleaner

# Залежності
pip install -r requirements.txt

# Конфігурація
cp .env.example .env
# Відредагуйте .env — заповніть GROQ_API_KEY, TELEGRAM_TOKEN, DB_PASSWORD

# Ініціалізація БД
psql -h <db_host> -U <db_user> -d <db_name> -f migrations/001_initial.sql
```

## Запуск

```bash
# Швидка синхронізація (перевірка ETag)
python -m src.sync_bills list

# Повна синхронізація
python -m src.sync_bills full

# Моніторинг + аналіз ризиків
python -m src.rag_monitor

# Batch-обробка
python -m src.batch_rag_50 --limit 10
```

## Cron

```cron
# Синхронізація кожну годину (за 5 хвилин до моніторингу)
55 * * * * cd /path/to/radacleaner && python -m src.sync_bills list >> /tmp/sync.log 2>&1

# Моніторинг кожну годину
0 * * * * cd /path/to/radacleaner && python -m src.rag_monitor >> /tmp/rag_monitor.log 2>&1

# Повна синхронізація раз на день
0 3 * * * cd /path/to/radacleaner && python -m src.sync_bills full >> /tmp/sync_full.log 2>&1
```

## Структура БД

- `bills` — законопроекти
- `law_versions` — історія версій (для журналістів)
- `change_log` — лог змін (для моніторингу)
- `risk_assessments` — оцінки ризиків від LLM
- `rag_documents` / `rag_chunks` — документи та чанки тексту
- `sync_state` — стан синхронізації (ETag)

## LLM-аналіз

Використовується Groq API (`openai/gpt-oss-120b`). Кожен законопроект аналізується за критеріями:
- Corruption — корупційні ризики
- Budgetary — бюджетні ризики
- Legal Collision — колізії з іншими законами
- Ambiguity — розмиті норми
- Civil Rights — права громадян
- Power Concentration — концентрація влади

## Ліцензія

MIT
