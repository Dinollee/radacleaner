<p align="center">
  <h1 align="center">🏛️ Страж Демократії</h1>
  <p align="center"><em>Автоматизований моніторинг законопроектів Верховної Ради України IX скликання</em></p>
</p>

<p align="center">
  <a href="https://radacleaner-dashboard.pages.dev">🌐 Дашборд</a> •
  <a href="https://github.com/Dinollee/radacleaner">GitHub</a> •
  <a href="https://t.me/+example">📱 Telegram</a>
</p>

---

## Що це робить

**Страж Демократії** — це система, яка автоматично збирає всі законопроєкти ВРУ, аналізує їх на ризики за допомогою ШІ та сповіщає громадян через Telegram.

### Можливості

| Компонент | Опис |
|-----------|------|
| 🔄 **Синхронізація** | Автоматичний збір 15,000+ законопроектів з RADA API кожні 4 години |
| 🧠 **LLM-аналіз** | Chain of Thought аналіз кожного закону на ризики (корупція, бюджет, права громадян, євроінтеграція) |
| 📱 **Telegram** | Сповіщення про нові закони, зміни статусів та виявлені ризики |
| 📊 **Дашборд** | Веб-інтерфейс з FTS5 пошуком, фільтрами, диффом версій, хронологією |
| 👥 **Депутати** | ПЯ, ПДА, ВКП — три метрики ефективності, паттерни голосувань |
| 📅 **Голосування** | 8,000+ голосувань, 440+ депутатів, детальна статистика |

---

## Архітектура

```
┌─────────────────────────────────────────────────────────────┐
│                     ВАЖКИЙ СЕРВЕР (Python)                  │
│                                                             │
│  sync_bills.py ────→ RADA API ────→ законопроєкти → D1     │
│  rag_engine.py ────→ PDF → LLM → ризики → D1              │
│  sync_votes_bulk.py → RADA HTML → депутати + голосування   │
│  sync_mp_stats.py ──→ перерахунок ПЯ/ПДА/ВКП → D1         │
│  sync_bill_passings.py → хронологія проходження → D1       │
│                          │                                  │
│                    POST /api/sync                           │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  CLOUDFLARE FREE TIER                       │
│                                                             │
│  🌐 Pages    — фронтенд-дашборд (Vanilla JS)               │
│  ⚡ Worker   — REST API (~2ms CPU)                         │
│  🗄 D1       — SQLite база (15K+ законів, 160K+ записів)   │
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
├── src/                        # Python пакет
│   ├── config.py               # Конфігурація + промпт LLM
│   ├── bill_sync.py            # Синхронізація з RADA API → D1
│   ├── rag_engine.py           # LLM-аналіз ризиків (Chain of Thought)
│   ├── risk_storage.py         # Збереження оцінок ризиків
│   ├── pdf_utils.py            # PDF → текст (PyMuPDF) + рекурсивний чанкинг
│   ├── groq_client.py          # OpenRouter API клієнт
│   ├── d1_client.py            # HTTP-клієнт до Worker (D1)
│   └── telegram_notifier.py    # Telegram бот
├── worker/
│   └── src/index.js            # Cloudflare Worker (REST API + FTS5)
├── dashboard/
│   └── index.html              # Веб-дашборд (SPA, 5 секцій)
├── migrations/                 # SQL міграції D1
│   ├── 001_initial.sql
│   ├── 002_d1_schema.sql
│   ├── 003_add_act_number.sql
│   ├── 004_add_mp_start_date.sql
│   ├── 005_add_mp_bills.sql
│   ├── 006_add_bills_fts5.sql      # FTS5 повнотекстовий пошук
│   ├── 007_add_mp_stats.sql        # Статистика депутатів
│   └── 008_fix_bill_documents.sql  # Унікальність документів
├── sync_bills.py               # Точка входу: синхронізація законів
├── sync_votes_bulk.py          # Масова синхронізація голосувань
├── sync_mp_stats.py            # Перерахунок ПЯ/ПДА/ВКП депутатів
├── sync_bill_passings.py       # Синхронізація хронології проходження
├── sync_mp_factions.py         # Синхронізація фракцій
├── sync_mp_bills.py            # Законотворча діяльність депутатів
└── wrangler.jsonc              # Конфігурація Cloudflare
```

---

## API Endpoints

| Метод | Ендпоінт | Опис |
|-------|----------|------|
| GET | `/api/stats` | Загальна статистика |
| GET | `/api/bills` | Список законів (FTS5 пошук, фільтри, пагінація) |
| GET | `/api/bills/:id` | Деталі: ризики, хронологія, документи, голосування |
| GET | `/api/bills/:id/versions` | Версії закону (для диффу) |
| GET | `/api/bills/:id/risks` | Оцінка ризиків LLM (Chain of Thought) |
| GET | `/api/bills/:id/votes` | Голосування по закону |
| GET | `/api/deputies` | Список депутатів (ПЯ, ПДА, ВКП з D1) |
| GET | `/api/deputies/:id` | Профіль депутата + історія голосувань |
| GET | `/api/votes` | Список голосувань |
| GET | `/api/factions` | Список фракцій |
| GET | `/api/plenary-sessions` | Календар засідань |
| POST | `/api/sync` | Прийом даних від Python-сервера |

---

## Автоматизація (systemd)

```bash
# Перевірка статусу
systemctl list-timers | grep radacleaner

# Логи
journalctl -u radacleaner-sync.service -f       # Синхронізація законів
journalctl -u radacleaner-monitor.timer -f       # LLM-аналіз + Telegram
journalctl -u radacleaner-votesync.service -f    # Голосування
journalctl -u radacleaner-mpstats.service -f     # Статистика депутатів
journalctl -u radacleaner-passings.service -f    # Хронологія проходження
```

| Сервіс | Інтервал | Що робить |
|--------|----------|-----------|
| `radacleaner-sync.timer` | кожні 4 години | Синхронізація законів з RADA API |
| `radacleaner-monitor.timer` | кожні 30 хвилин | LLM-аналіз нових/змінених законів + Telegram |
| `radacleaner-votesync.service` | безперервно | Масова синхронізація голосувань (~8,000) |
| `radacleaner-mpstats.timer` | кожні 6 годин | Перерахунок ПЯ/ПДА/ВКП депутатів |
| `radacleaner-passings.timer` | кожні 4 години | Синхронізація хронології проходження |

---

## LLM-аналіз (Chain of Thought)

Система використовує двоетапний аналіз:

1. **Етап 1 — Фільтрація**: виділення критичних чанків (статті кодексів, санкції, фінанси, корупція, ЄС)
2. **Етап 2 — Аналіз**: глибокий аналіз тільки критичних чанків

**Формат відповіді LLM:**
```json
{
  "analyzed_chunks": [1, 3, 5],
  "has_risks": true,
  "risk_level": "high",
  "summary": "Стислий опис суті змін",
  "law_summary": "Повний опис суті закону (3-5 речень)",
  "detailed_risks": ["Ризик 1: опис..."],
  "insufficient_text": false
}
```

**Метрики депутатів:**
- **ПЯ** (Індекс явки) = (yes + no + abstain) / total
- **ПДА** (Діяльне участь) = (yes + no) / (yes + no + abstain)
- **ВКП** (Зважений КПД) = Σ(weight × action) / Σ(weight)

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

### Перемикання на D1 (замість PostgreSQL)

```bash
./venv/bin/python scripts/migrate_to_d1_fast.py
```

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
  npx wrangler d1 execute radacleaner-db --remote --file=migrations/00X_name.sql
```

---

## Технічні деталі

### FTS5 повнотекстовий пошук

Віртуальна таблиця `bills_fts` з токенізатором `unicode61 remove_diacritics 2`. Тригери автоматично синхронізують дані при INSERT/UPDATE/DELETE. Префіксний пошук для часткових збігів.

### Рекурсивний чанкинг

Ієрархія роздільників: `\n\n` → `\n` → ` ` → `""`. Параметри: max_size=600, overlap=100. Ідеально для юридичних текстів зі структурованими статтями.

### Кешування LLM

MD5-хеш перевіряється після кожного скачування PDF (до PyMuPDF + чанкингу). Дедуплікація по 120 символах до чанкингу.

---

## Ліцензія

MIT
