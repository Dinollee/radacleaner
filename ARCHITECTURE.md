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
data/             — SQLite backup (superseded by PG)
migrations/       — SQL migration files
scripts/          — utility scripts (test_llm_providers.py, test_kpi_formula.py)
systemd/          — .service unit files
tests/            — tests
kpi_weights.json  — KPI v11 component weights (legacy, configurable)
telegram_bot.py   — Telegram bot (interactive commands)
calc_kpi_v11.py   — KPI v11 calculation (legacy, three-level system)
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
| C4 | Комітет | `score/10` | committee_members |
| C5 | Звернення | `min(req_resp/20) × (0.7 + 0.3 × response_rate)` | deputy_requests |
| C6 | Вплив | `(1-risk/5)×0.6 + eu/35×0.4` | risk_assessments + eu_alignment |

Defaults: C2=0.5 (no LLM data), C3=0.5 (primary<3), C4=0.5 (no committee), C6=0.5 (no data). C1=0 if py<10%.

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

## Key scripts
| Script | Purpose |
|---|---|
| sync_all.py | Master pipeline: factions → stats → committees → MSI/K_pb → Quality/Risk → KPI → requests |
| bill_sync.py | Bill sync from RADA bulk JSON (status, documents, **authors**, isUrgent, isEuro) |
| sync_votes.py / sync_votes_bulk.py | Fetch voting records |
| sync_mp_factions.py | Deputy faction membership |
| sync_mp_bills.py | Bills per deputy (FULL NAME matching!) |
| sync_mp_stats.py | Voting stats per deputy (ПЯ/ПДА/ВКП) + adoption_rate |
| sync_committee_members.py | Committee assignments |
| sync_deputy_requests.py | Deputy requests (matches by first+patronymic initials) |
| calc_bill_quality.py | Quality/Risk/Authorship recalculation (weighted by sponsor_order) |
| calc_kpi_v12.py | **ІЕД**: 6 equal-weight categories (C1-C6) |
| eu_alignment.py | EU alignment scoring |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger (with PDF retry) |
| monitor.py | Telegram monitor: NEW bills + status change posts |
| daily_digest_llm.py | Daily digest: LLM-powered summary + fallback |
| telegram_bot.py | **Telegram bot**: /bill, /dep, /top, /eu, /start |
| telegram_notifier.py | Telegram alerts (send_message, format_risk/status) |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |
| worker/api-server.js | Express API (port 8788) — bills, deputies, EU alignment, schedule |
| dashboard/index.html | Cloudflare Pages frontend — single-page app (ІЕД, hexagon radar, clickable sort) |

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
- **eu_alignment_overall / eu_alignment_chapters** — EU alignment scores
- **deputy_aliases** — name change history (6 entries)

## API
- Express: `https://api.dino.pp.ua` (tunnel → localhost:8788)
- Dashboard: Cloudflare Pages static site
- Key endpoints: `/api/bills`, `/api/deputies`, `/api/deputies/:name`, `/api/eu-alignment`, `/api/eu-alignment/trend`, `/api/schedule`, `/api/plenary-sessions`, `/api/activity-calendar`, `/api/activity-day`
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
| `radacleaner-api` | — | Express API (port 8788) |
| `radacleaner-tunnel` | — | Cloudflare Tunnel |
| `telegram-bot` | — | Telegram bot polling |
| `radacleaner-mpstats` | every 6h | factions + stats (with adoption_rate) + **ІЕД recalc** |
| `night-batch` | 21:00-08:00 | LLM analysis (nemotron-super) |
| `sync_bills` | periodic | Bill sync from RADA |
| `radacleaner-votesync` | every 6h | Voting records sync |

## Roadmap
See `RESEARCH.md` — "ROADMAP — Project Plan" section. 7 groups, dependency graph.
Current status: ІЕД (v12) implemented, Dashboard with hexagon + activity calendar + clickable sort + modals, Telegram bot with ІЕД + daily digest + rate limiting, EU Score done.

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
- One session = one logical step = one commit
- Before finishing: self-reflection — did I add dependencies/tables/scripts/APIs not in ARCHITECTURE.md?
