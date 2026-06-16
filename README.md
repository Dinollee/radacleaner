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
│                     VPS (Python)                            │
│                                                             │
│  sync_bills.py ────→ RADA JSON API ────→ закони → D1       │
│  sync_active_bills.py → VRU HTML → live статуси → D1       │
│  sync_bill_passings.py → хронологія → D1                   │
│  rag_engine.py ────→ PDF → LLM → ризики → D1              │
│  analyze_api.py ───→ pending_analysis → LLM → risks        │
│  sync_mp_stats.py ──→ ПЯ/ПДА/ВКП депутатів → D1           │
│  sync_votes_bulk.py → голосування → D1                     │
│  monitor.py ────→ change_log → Telegram                    │
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
│  🗄 D1       — SQLite (15K+ bills, 6.8M mp_votes)          │
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
│   ├── config.py               # Config + LLM prompt
│   ├── bill_sync.py            # Bill sync: RADA JSON → D1 (3-phase)
│   ├── rag_engine.py           # LLM risk analysis (Chain of Thought)
│   ├── risk_storage.py         # Risk assessment storage
│   ├── pdf_utils.py            # PDF → text (PyMuPDF)
│   ├── groq_client.py          # OpenRouter API client
│   ├── d1_client.py            # HTTP client to Worker (D1)
│   └── telegram_notifier.py    # Telegram bot
├── worker/
│   └── src/index.js            # Cloudflare Worker (REST API + FTS5)
├── dashboard/
│   └── index.html              # Dashboard (SPA, 4 sections)
├── migrations/                 # D1 SQL migrations (001-009)
├── sync_bills.py               # Entry: bill sync (list/full/all modes)
├── sync_active_bills.py        # Live VRU HTML check (30-day bills, 1 req/sec)
├── sync_bill_passings.py       # Bill chronology sync (optimized batch)
├── sync_votes_bulk.py          # Bulk vote sync from RADA
├── sync_mp_stats.py            # Deputy stats recalculation (ПЯ/ПДА/ВКП)
├── sync_mp_factions.py         # Faction sync from RADA
├── sync_mp_bills.py            # Deputy bill activity from itd.rada.gov.ua
├── analyze_api.py              # LLM analysis service (polls pending_analysis)
├── analyze_bill.py             # CLI: analyze single bill via LLM
├── monitor.py                  # Telegram notifications from change_log
└── wrangler.jsonc              # Cloudflare config
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
systemctl list-timers | grep -E 'sync|monitor|radacleaner'

# Логи
journalctl -u sync_bills.service -f           # Синхронізація законів
journalctl -u sync_active_bills.service -f    # Live VRU check (30 хв)
journalctl -u monitor.service -f              # Telegram сповіщення
journalctl -u radacleaner-analyze.service -f  # LLM аналіз
```

| Сервіс | Інтервал | Що робить |
|--------|----------|-----------|
| `sync_bills.timer` | щогодини :55 | Bill sync: list + full JSON + passings |
| `sync_active_bills.timer` | кожні 30 хв | Live VRU HTML check (30-денні bills) |
| `monitor.timer` | кожні 30 хв | Telegram notifications з change_log |
| `radacleaner-mpstats.timer` | кожні 6 год | Перерахунок ПЯ/ПДА/ВКП депутатів |
| `radacleaner-votesync.timer` | кожні 6 год | Синхронізація голосувань |
| `digest.timer` | щодня 09:00 | Щоденний Telegram дайджест |
| `radacleaner-analyze.service` | безперервно | LLM аналіз (pending_analysis → risks) |

---

## LLM-аналіз (Chain of Thought)

Система використовує двоетапний аналіз:

1. **Етап 1 — Класифікація**: визначення чи є закон процедурним (напр. постанова про відхилення)
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

**Автоматична черга**: `process_full_data()` додає bills в `pending_analysis` після індексації документів. `analyze_api.py` (безперервний сервіс) опитує чергу кожні 30 сек і запускає аналіз.

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
