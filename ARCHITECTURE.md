# Architecture — Rada Cleaner

## Stack
- **Python 3** — sync scripts, LLM analysis, KPI calculation
- **PostgreSQL 18.4** — primary DB at 192.168.1.244/radacleaner
- **Express (Node.js)** — API server (worker/api-server.js, port 8788)
- **Cloudflare Pages** — dashboard (dashboard/)
- **Cloudflare Tunnel** — exposes API as api.dino.pp.ua
- **LLM** — `nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter (primary), NVIDIA API (secondary), Gemini gemma-4 (backup). See `scripts/test_llm_providers.py`.
- **Telegram Bot** — `telegram_bot.py` (python-telegram-bot v22), systemd `telegram-bot.service`

## Directory layout
```
src/              — shared Python modules (db, llm_client, helpers, telegram_notifier, prompts)
worker/           — Express API server (api-server.js)
dashboard/        — Cloudflare Pages frontend (index.html)
data/             — конфіги (disinfo_channels.json — канали моніторингу детектора атак)
migrations/       — SQL migration files
scripts/          — utility scripts (test_llm_providers.py, backfill_summaries.py; test_kpi_formula.py — в корні)
systemd/          — .service unit files
tests/            — tests
telegram_bot.py   — Telegram bot (interactive commands)
calc_kpi_v12.py   — ІЕД calculation (active, 6 equal-weight categories)
```

## Data flow: Bill Assessment → Deputy ІЕД

```
RADA API (billinfo_full JSON)
  ↓
bills (significance, impact, risk_score, toxicity, is_urgent, is_euro)
  ↓
LLM Analysis (rag_engine.py → risk_assessments)
  ↓
bill_sponsors (rada_uid → links bill to deputy)
  ↓
calc_bill_quality.py → mps (bill_quality_score, avg_risk_score, authorship_ratio)
  ↓
calc_kpi_v12.py → mps (kpi_v12_score, kpi_v12_* — ІЕД)
```

## ІЕД — Індекс ефективної діяльності (v12, ACTIVE)

**6 рівних категорій, без ваг.** Кожна ∈ [0, 1]. `ІЕД = (C1+C2+C3+C4+C5+C6) / 6 × 100`

| # | Категорія | Метрики | Джерело |
|---|-----------|---------|---------|
| C1 | Дисципліна | `py×0.5 + pda×0.3 + vkp×0.2` | mp_votes |
| C2 | Законотворчість | `quality/5×0.3 + (1-risk/5)×0.3 + docs/2000×0.2 + authorship/0.5×0.2` | risk_assessments |
| C3 | Результативність | `adoption/100×0.7 + min(primary/10)×0.3` | bill_sponsors + bills |
| C4 | Комітет | публічна монотонна шкала `C4_LADDER`: немає ролі **40** · член **55** · секретар/голова підком. **70** · заступник **85** · голова/спікер **100** | committee_members |
| C5 | Звернення | `min(req_resp/20) × (0.7 + 0.3 × response_rate)` | itd.rada.gov.ua mprequests API (з пагінацією) |
| C6 | Вплив | `(1-risk/5)×0.6 + eu/35×0.4` | risk_assessments + eu_alignment |

Defaults: C2=0.5 (no LLM data), C3=0.5 (primary<3), C6=0.5 (no data). C1=0 if py<10%.
C4 — єдина опублікована шкала для всіх (v2.1): будь-яка роль ≥ відсутність ролі, крок 15.

### Методологія — публічна (v2.1, 2026-08-21)

Дашборд, футер на КОЖНІЙ сторінці: карточка «📖 Методологія ІЕД» (всі компоненти,
ваги, шкали, правила нейтралей) + «🤖 Промпт ШІ-аналізу законопроєктів» (дослівний
текст system+main промпта з `src/prompts.py`, нотатки про чанкинг і пост-перевірки).
⚠️ При зміні формул ІЕД або промптів — оновити розділ на дашборді і bump версії.

### Signals (Level 3 — auto-generated insights)

Three categories:
- ⚠ Warnings: spam, no committee work, narrow specialization, high urgent ratio
- ✓ Strengths: high quality, stable specialization, high efficiency, high discipline
- ℹ Features: collective authorship, narrow expert, EU profile

### Dashboard (UPDATED 2026-07-08)

- **"KPI" renamed to "ІЕД"** (Індекс ефективної діяльності) — legal safety
- **Deputies table**: sorted by ІЕД, shows 6 component columns with progress bars + color coding
- **Clickable column headers**: click to sort by that column
- **Deputy detail**: SVG hexagon radar chart (6 axes) + Profile grid + Signal badges
- **Schedule calendar**: activity badges (+N new, ~N changes) per day, clickable → modal with bill list
- **All dates**: dd.mm.yyyy format, UTC→Kyiv timezone conversion
- **API**: `/api/deputies` returns `ked12`, `kedDisc12`, `kedLegis12`, `kedEff12`, `kedComm12`, `kedReq12`, `kedImpact12`, `kedRank12`

### Auto-recalculation
- `radacleaner-mpstats.timer` (every 6h): sync factions → sync stats (with adoption_rate) → calc_kpi_v12
- `sync_eu_tracker.timer` (daily 09:00): EU cluster news monitoring + Telegram alerts

## Гармонізація законодавства (вхід EU Integration Index)

**Метод:** частка прийнятих законів (stage 4) серед EU-релевантних по главах.

### Дані
- `stats_cache`: `harmonization_ch1..32` (значення `"процент:всього:прийнято"`, пише calc_harmonization.py щоночі) + overall **31.1%**
- ⚠️ Старі ключі `harmonization_cluster1-6` МЕРТВІ з 2026-07-09 (пись/читання розійшлися — виправлено 2026-08-21 агрегацією ch→cluster на стороні API)
- `eu_alignment_chapters` / `eu_alignment_overall` — keyword alignment (legacy, історія тренду)
- Агрегація по кластерах для дашборда: `EU_CLUSTER_CHAPTERS` в api-server.js (harm = середнє harmonization_ch глав кластера)

### Джерела для трекінгу кластерів
1. EC RSS: `enlargement.ec.europa.eu/node/2/rss_en`
2. Європравда: `eurointegration.com.ua` (скрапінг)
3. pulse.kmu.gov.ua: моніторинг 24 напрямків асоціації

## EU Integration Index v1 (гіпотетичний рівень євроінтеграції)

Композитний індекс: переговорний трек (статуси кластерів) + законодавчий трек (гармонізація).

### Формула
```
INDEX       = round(0.5 × NEGOTIATION + 0.5 × LEGISLATION, 1)
NEGOTIATION = середнє по 6 кластерах: not_opened=0, opened=50, provisionally_closed=100
LEGISLATION = overall гармонізація (calc_harmonization.py: total_signed/total_bills×100)
```
Поточне значення: **23.9** (NEGOTIATION=16.7 — відкриті C1+C6; LEGISLATION=31.1).

### Дані
- Таблиця `eu_cluster_status` (migration 023): cluster_id PK, status CHECK, event_date, source_url
- Ключ `stats_cache` → `eu_integration_v1`: JSON `{v, index, negotiation, legislation, clusters[], computed_at}` — пише calc_harmonization.py при нічному перерахунку
- API `/api/eu-alignment`: повертає `index{value,negotiation,legislation,computedAt}`, `clusters[{id,status,eventDate,sourceUrl,harm}]` (harm = середнє harmonization_ch по главах кластера), `timeline[]`, `news[]` (8 свіжих з eu_news_*), `trend[]` + legacy поля (overall, chapters, harmonizationScore...)

### Авто-детекція відкриттів
`sync_eu_tracker.py` (щоденно 09:00): консервативна регекс-детекція `detect_cluster_opening(title, summary)` — потрібні одночасно контекст «accession negotiations/cluster», дієслово відкриття та номер/назва кластера. При збігу — UPSERT статусу 'opened' в eu_cluster_status (тільки якщо статус ще не змінювався).

## Key scripts
| Script | Purpose |
|---|---|
| sync_all.py | Master pipeline: factions → stats → committees → MSI/K_pb → Quality/Risk → EU scores → requests (ІЕД v12 рахує окремо mpstats) |
| bill_sync.py | Bill sync from RADA bulk JSON (status, documents, **authors**, isUrgent, isEuro) |
| sync_bill_passings.py | Bill passings from bulk JSON (1x/day) |
| sync_bill_passings_html.py | **Bill passings from HTML (every 4h): АКТИВНІ закони (остання подія ≤7 днів) першими, потім ніколи не синхронізовані, потім ротация застарілих (500/запуск)** |
| sync_votes.py / sync_votes_bulk.py | Fetch voting records |
| sync_mp_factions.py | Deputy faction membership |
| sync_mp_bills.py | Bills per deputy (FULL NAME matching!) |
| sync_mp_stats.py | Voting stats per deputy (ПЯ/ПДА/ВКП) + adoption_rate |
| sync_committee_members.py | Committee assignments |
| sync_deputy_requests.py | Deputy requests (matches by first+patronymic initials) |
| calc_bill_quality.py | Quality/Risk/Authorship recalculation (weighted by sponsor_order) |
| calc_kpi_v12.py | **ІЕД**: 6 equal-weight categories (C1-C6) |
| calc_eu_llm.py | EU Score from LLM aggregation (raw_analysis) |
| sync_eu_tracker.py | EU cluster monitoring (EC RSS + Європравда) + авто-детекція відкриттів кластерів → eu_cluster_status |
| sync_schedule_legacy.py | Plenary calendar from w1.c1.rada.gov.ua (daily 07:30) |
| sync_committee_schedule.py | Committee meetings from committees.rada.gov.ua (daily 07:40) |
| sync_holidays.py | Holidays → rada_schedule |
| sync_info_monitor.py | **Info attack collector** (every 30 min): factcheck RSS + t.me/s disinfo channels → info_items (simhash dedup) |
| detect_attacks.py | **Burst detector**: union-find кластеризація 48ч вікна, правило ≥4 TG-каналів/≥8 постів, debunk lookup, bill linking, TG-алерт з cooldown+ескалацією |
| label_narratives.py | **Nightly LLM labeling** (07:15): 2 nemotron виклики → stats_cache `info_digest` (нарративи дня + ТОП фактчеків) |
| eu_alignment.py | EU keyword alignment scoring (legacy, replaced by harmonization) |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger (3 workers, sliding window, language check) |
| monitor.py | Telegram monitor: NEW bills + status change posts |
| daily_digest_llm.py | **Daily digest: deterministic format (no LLM)** — fixed template, data from DB + rada.gov.ua scraping |
| telegram_bot.py | **Telegram bot**: /bill, /dep, /top, /eu, /start |
| telegram_notifier.py | Telegram alerts (send_message, format_risk/status) |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |
| worker/api-server.js | Express API (port 8788) — bills, deputies, EU integration index, schedule, info-digest |
| dashboard/index.html | Cloudflare Pages frontend — single-page app: Дашборд, Закони, Депутати (ІЕД + radar), Графік (календар активностей), EU Alignment (індекс євроінтеграції), Інфоатаки |

## Night Batch (UPDATED 2026-07-15)

**night_batch.py** — пакетний LLM аналіз законів (21:00-08:00).

### Конфігурація
| Параметр | Значення |
|----------|----------|
| Workers | **3** (parallel threads) |
| Context window | Sliding window: max 9 messages (~153K tokens) |
| Rate limiting | OpenRouter: 10/min, NVIDIA: 15/min |
| Language check | Ukrainian only (retry if English) |

### Фікси (2026-07-15)
1. **Sliding window** — фікс 400 помилок OpenRouter (контекст >262K tokens)
2. **Rate limiting** — запобігає 429 помилкам при 3 воркерах
3. **Language check** — retry при англійському аналізі (>30% українських літер)

### Швидкість
| Метрика | 1 worker | 3 workers |
|---------|----------|-----------|
| Чанків/хв | 0.10 | 1.40 |
| Законів/день | ~50 | ~350 |
| Прискорення | — | **13.3x** |

## Data sources
- **billinfo_list-skl9.json** — bill list (15K records, no authors)
- **billinfo-skl9.json** — full bill data (130MB, has `initiators` field with authors)
- Both from `data.rada.gov.ua/ogd/zpr/skl9/`
- Updated daily (usually overnight)

## DB schema (key tables)
- **bills** — 15K+ bills from RADA API. Has: significance, impact, risk_score, toxicity (set by LLM), is_urgent, is_euro (from JSON)
- **bill_sponsors** — 15K+ author records (extracted from JSON initiators). Columns: bill_id, mp_id, mp_name, rada_uid, sponsor_order
- **mps** — 460 deputies. rada_uid = stable identity key (from RADA API person.id). 6 name changes in deputy_aliases.
  - kpi_v12_score, kpi_v12_rank — ІЕД results (6 equal-weight categories)
  - kpi_v12_discipline/legislation/efficiency/committee/requests/impact — 6 components
  - kpi_v11_score, kpi_v11_rank — KPI v11 results (legacy, 5 weighted components)
  - kpi_v11_effectiveness/discipline/efficiency/control/quality — 5 components (legacy)
  - signal_warnings, signal_strengths, signal_features — JSONB signal arrays
  - lei, bill_quality_score, avg_risk_score — quality metrics
  - authorship_ratio, adoption_rate, shannon_diversity, unique_coauthors — profile
  - eu_integration_score, eu_euro_bills, eu_risk_bills, eu_state_aid_bills — EU
  - requests_with_response, committee_score — interaction
  - documents_count — bill document richness
- **mp_votes** — 7.5M voting records
- **bill_sponsors** — deputy↔bill links (rada_uid, mp_id, sponsor_order)
- **risk_assessments** — LLM analysis results
- **committee_members** — 385 members, 24 committees
- **eu_alignment_overall / eu_alignment_chapters** — EU alignment scores (trend history)
- **eu_cluster_status** — переговорні кластери: status CHECK(not_opened|opened|provisionally_closed), event_date, source_url (migration 023)
- **rada_schedule** — пленарні/день запитань/свята (uniq date+event_type); **rada_committee_schedule** — засідання комітетів (uniq_rcs_meeting)
- **info_items** — сырий інфопотік детектора атак: factcheck RSS + t.me/s канали, url UNIQUE, simhash BIGINT, cluster_id (migration 024)
- **attack_alerts** — зафіксовані синхронні хвилі: label, channels/posts count, debunk_url, related_bill_number, alert_sent
- **deputy_aliases** — name change history (6 entries)

## API
- Express: `https://api.dino.pp.ua` (tunnel → localhost:8788)
- Dashboard: Cloudflare Pages static site
- Key endpoints: `/api/bills`, `/api/deputies`, `/api/deputies/:name`, `/api/eu-alignment` (EU integration index v1 + clusters/timeline/news + legacy compat), `/api/eu-alignment/trend`, `/api/schedule`, `/api/plenary-sessions`, `/api/activity-calendar` (bills activity + votes/committee/eu), `/api/activity-day`, `/api/info-digest` (нарративи дня + атаки), `/api/dashboard` (unified)
- Timezone: all date queries convert UTC→Europe/Kyiv
- Dashboard deploy: `npx wrangler pages deploy dashboard --project-name radacleaner-dashboard`

## Free LLM Providers (tested 2026-07-01)

All providers offer free tiers. Provider testing: `./venv/bin/python scripts/test_llm_providers.py`

### OpenRouter (openrouter.ai)
- Auth: `OPENROUTER_API_KEY` in .env
- Primary model: `nvidia/nemotron-3-super-120b-a12b:free` (50s, excellent Ukrainian, 1M context)
- Others: `nvidia/nemotron-3-ultra-550b-a55b:free`, `google/gemma-4-31b-it:free`, `openrouter/free`
- Note: `openrouter/owl-alpha` is DEAD (404 since 2026-06-30)

### NVIDIA API (integrate.api.nvidia.com)
- Auth: `NVIDIA_API_KEY` in .env
- Rate limit: 40 req/min max, **use 30 for safety**
- Fallback model: `nvidia/nemotron-3-super-120b-a12b` (17s)
- Also works: `mistralai/mistral-large-3-675b-instruct-2512` (6s), `mistral-medium-3.5-128b`, `llama-4-maverick`

### Google Gemini (direct API)
- Auth: `GEMINI_API_KEY` in .env
- Model: `gemma-4-31b-it`
- Free tier: 1500 req/day, 15 req/min
- **Rate limiter**: 12 req/min + 1400 req/day (safety margins in `src/llm_client.py`)
- Prefer OpenRouter route for same model: `google/gemma-4-31b-it:free`

## Systemd Services
| Service | Timer | Purpose |
|---------|-------|---------|
| `radacleaner-api` | — (daemon) | Express API (port 8788) |
| `radacleaner-tunnel` | — (daemon) | Cloudflare Tunnel → api.dino.pp.ua |
| `telegram-bot` | — (daemon) | Telegram bot polling (@RadaCleaner_bot) |
| `radacleaner-analyze` | — (daemon) | pending_analysis worker (poll 30s, timeout 3600s/bill) |
| `monitor` | :05 и :35 | Telegram monitor: new bills, status changes, high-risk alerts |
| `digest` | daily 09:00 | Daily digest #1 (`monitor.py --daily`) |
| `digest-llm` | daily 20:00 | Daily digest #2 (`daily_digest_llm.py`, deterministic, no LLM) |
| `sync_schedule` | daily 07:30 | VRU plenary calendar sync (legacy HTML calendar) |
| `sync_committee_schedule` | daily 07:40 | Committee meetings sync (committees.rada.gov.ua weekly pages) |
| `sync_info_monitor` | every 30 min | Info attack collector: factcheck RSS (ЦПД/VoxCheck/StopFake/Детектор/SPRAVDI) + t.me/s disinfo channels → info_items; другим ExecStart — `detect_attacks.py` (Phase 2 burst detector: union-find кластеризация simham/Jaccard, бьорст ≥4 TG-каналов и ≥8 постов за ≤24ч, спростування фактчекеров, cooldown кампаний, TG-алерт) |
| `sync_bills` | hourly :55 | Bill sync from RADA (bulk JSON + passings) |
| `sync_bill_passings_html` | every 4h :15 | Bill passings sync (HTML parsing, real-time) |
| `sync_eu_tracker` | daily 09:00 | EU cluster news monitoring + Telegram alerts |
| `radacleaner-votesync` | every 6h :00 | Voting records sync (`sync_votes_bulk.py --resume`) |
| `radacleaner-mpstats` | every 6h | factions + stats + **ІЕД recalc** (calc_kpi_v12.py) |
| `night-batch` | 21:00 (+stop 08:00) | LLM analysis, 3 workers, alert on err>10 |

Cron ліквідовано (2026-08-21): все планування — systemd timers (17 активних). sync_period */10 пн-пт; eu_alignment daily 04:00.
Всі unit-файли зберігаються в `systemd/`. Після редагування: `sudo cp systemd/<unit> /etc/systemd/system/ && sudo systemctl daemon-reload` (в /etc — КОПІЇ, не symlink).

## Roadmap
See `RESEARCH.md` — "ROADMAP — Project Plan" section. 7 groups, dependency graph.
Current status: **ІЕД (v12) ACTIVE**, Dashboard: ІЕД radar + Графік (календар активностей) + EU Integration Index 23.9% + Інфоатаки (детектор синхронних хвиль), Telegram bot + digest + attack alerts, EU tracker + cluster auto-detection.

## Rules
- NEVER match deputies by last name alone — always full name
- mps.rada_uid is the stable identity key (from RADA API person.id); text names are mutable
- FK relationships use mps.id, never text mp_name
- Dashboard uses ІЕД (not "KPI") — legal safety
- Dashboard deploys: `npx wrangler pages deploy dashboard --project-name radacleaner-dashboard`
- PostgreSQL uses %s placeholders, not ?
- PostgreSQL numeric returns as strings — use Number() in JS before .toFixed()
- created_at is text in UTC — convert to Kyiv timezone in API queries
- Gemini rate limit: 12 req/min, 1400 req/day (enforced in llm_client.py)
- PDF downloads: retry 3 times with backoff on 503/429/500
- `bills.act_number` — official law number in IX-convocation register (e.g. «4931-ІХ»), 100% заповнений для stage 4. Дашборд показує бейдж із посиланням на zakon.rada.gov.ua/laws/show/{номер} (кирилиця ІХ → латиниця IX для URL)
- `json_data.has_risks` — обов'язковий ключ: фронтенд фільтрує рендеринг ризиків за ним. rag_engine гарантує його для непроцедурних аналізів (модель nemotron іноді пропускає; міграція 017 бекфіллила 246 старих рядків)
- One session = one logical step = one commit
- Before finishing: self-reflection — did I add dependencies/tables/scripts/APIs not in ARCHITECTURE.md?
