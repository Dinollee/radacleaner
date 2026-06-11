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
| 🧠 **LLM-аналіз** | Аналіз кожного закону на ризики (корупція, бюджет, права громадян) |
| 📱 **Telegram** | Сповіщення про нові закони та зміни статусів |
| 📊 **Дашборд** | Веб-інтерфейс з фільтрами, пошуком, диффом версій |
| 👥 **Депутати** | КПД, паттерни голосувань, статистика участі |
| 📅 **Графік** | Пленарні засідання, закони на голосування |

---

## Архітектура

```
┌─────────────────────────────────────────────────────────────┐
│                     ВАЖКИЙ СЕРВЕР (Python)                  │
│                                                             │
│  sync_bills.py ────→ RADA API ────→ законопроєкти           │
│  rag_monitor.py ───→ PDF → LLM → ризики → Telegram         │
│  sync_votes.py ────→ RADA HTML → депутати + голосування    │
│                          │                                  │
│                    POST /api/sync                           │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  CLOUDFLARE FREE TIER                       │
│                                                             │
│  🌐 Pages    — фронтенд-дашборд                            │
│  ⚡ Worker   — REST API (~2ms CPU)                         │
│  🗄 D1       — SQLite база (15K+ законів, 160K+ записів)   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Стек технологій

| Шар | Технологія |
|-----|-----------|
| **Бекенд** | Python 3 + PyMuPDF + Groq API |
| **API** | Cloudflare Worker (JavaScript) |
| **База** | Cloudflare D1 (SQLite) |
| **Фронтенд** | Vanilla HTML/CSS/JS (Cloudflare Pages) |
| **Сповіщення** | Telegram Bot API |
| **LLM** | Groq (`openai/gpt-oss-120b`) |

---

## Структура проекту

```
radacleaner/
├── src/                        # Python пакет
│   ├── config.py               # Конфігурація (.env)
│   ├── bill_sync.py            # Синхронізація з RADA API → D1
│   ├── rag_engine.py           # LLM-аналіз ризиків + Telegram
│   ├── risk_storage.py         # Збереження оцінок ризиків
│   ├── pdf_utils.py            # PDF → текст (PyMuPDF)
│   ├── groq_client.py          # Groq API клієнт
│   ├── d1_client.py            # HTTP-клієнт до Worker (D1)
│   └── telegram_notifier.py    # Telegram бот
├── worker/
│   └── src/index.js            # Cloudflare Worker (REST API)
├── dashboard/
│   └── index.html              # Веб-дашборд (5 секцій)
├── scripts/
│   └── migrate_to_d1_fast.py   # Міграція PG → D1
├── sync_bills.py               # Точка входу: синхронізація законів
├── sync_votes.py               # Парсер голосувань (один закон)
├── sync_votes_bulk.py          # Масова синхронізація голосувань
├── rag_monitor.py              # Точка входу: LLM-моніторинг
└── wrangler.jsonc              # Конфігурація Cloudflare
```

---

## API Endpoints

| Метод | Ендпоінт | Опис |
|-------|----------|------|
| GET | `/api/stats` | Загальна статистика |
| GET | `/api/bills` | Список законів (фільтри, пошук, пагінація) |
| GET | `/api/bills/:id` | Деталі: ризики, версії, зміни, голосування |
| GET | `/api/bills/:id/versions` | Версії закону (для диффу) |
| GET | `/api/bills/:id/risks` | Оцінка ризиків LLM |
| GET | `/api/bills/:id/votes` | Голосування по закону |
| GET | `/api/deputies` | Список депутатів (КПД, фракція) |
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
systemctl status radacleaner-votesync

# Логи
journalctl -u radacleaner-monitor -f       # LLM + Telegram
journalctl -u radacleaner-sync -f           # Синхронізація
journalctl -u radacleaner-votesync -f       # Голосування

# Ручний запуск
sudo systemctl start radacleaner-sync
sudo systemctl start radacleaner-monitor
sudo systemctl start radacleaner-votesync
```

| Сервіс | Інтервал | Що робить |
|--------|----------|-----------|
| `radacleaner-sync.timer` | кожні 4 год | Синхронізація законів з RADA API |
| `radacleaner-monitor.timer` | кожні 30 хв | LLM-аналіз + Telegram сповіщення |
| `radacleaner-votesync.service` | continuous | Масова синхронізація голосувань |

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

### Cloudflare

```bash
npm install -g wrangler
wrangler login
wrangler deploy                           # Worker
wrangler pages deploy dashboard --project-name radacleaner-dashboard  # Pages
```

---

## Деплой після змін

```bash
# Зміни в Worker (index.js)
cd worker && npx wrangler deploy

# Зміни в дашборді
npx wrangler pages deploy dashboard --project-name radacleaner-dashboard

# Зміни в Python — просто git push (авто-синхронізація через systemd)
```

---

## Ліцензія

MIT
