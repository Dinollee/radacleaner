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
kpi_weights.json  — KPI v11 component weights (configurable)
telegram_bot.py   — Telegram bot (interactive commands)
calc_kpi_v11.py   — KPI v11 calculation (three-level system)
```

## Data flow: Bill Assessment → Deputy KPI

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
calc_kpi_v11.py → mps (kpi_v11_score, kpi_v11_*, signal_*, shannon_diversity, adoption_rate)
```

## KPI v11 — Three-Level System (UPDATED 2026-07-01, dashboard 2026-07-02)

### Level 1: KPI (performance)

`KPI = 0.25×Ефективність + 0.25×Дисципліна + 0.20×Результативність + 0.15×Контроль + 0.15×Якість`

Weights stored in `kpi_weights.json` (configurable for calibration).

| Component | Metrics | Source |
|-----------|---------|--------|
| Ефективність | LEI (primary only) | bill_sponsors + bills.stage=4 |
| Дисципліна | ПЯ + ПДА + ВКП | mp_votes |
| Результативність | Conv (primary) + adoption_rate | bills.stage=4 / total |
| Контроль | requests_with_response + committee_score | deputy_requests + committee_members |
| Якість | bill_quality_score × 20 + documents_count | risk_assessments + bill_documents |

### Level 2: Profile (description, doesn't affect KPI)

| Metric | Source | Format |
|--------|--------|--------|
| Committee | committee_members | "Оборона" |
| Specialization | Shannon H | "Вузька"/"Середня"/"Широка" |
| Shannon Diversity | entropy formula | 0-7 |
| EU ratio | eu_euro_bills / total_bills | "6%" |
| Authorship style | authorship_ratio | "Індивідуальний"/"Колективний" |
| Top topic | top agenda_category | "Безпека" |
| Bills/Laws | total_bills, total_laws (stage=4) | "47/12" |

### Level 3: Signals (auto-generated insights)

Three categories:
- ⚠ Warnings: spam, no committee work, narrow specialization, high urgent ratio
- ✓ Strengths: high quality, stable specialization, high efficiency, high discipline
- ℹ Features: collective authorship, narrow expert, EU profile

### Dashboard (DONE 2026-07-02)

- **Deputies table**: sorted by KPI v11, shows 5 component columns (Законодав., Дисципліна, Результат., Контроль, Якість) with progress bars + color coding
- **Deputy detail**: SVG pentagon radar chart (5 axes) + Profile grid (specialization, authorship style, bills/laws, EU, ПЯ/ПДА/ВКП, LEI) + Signal badges (warnings/strengths/features)
- **Sort options**: by KPI, by each component individually
- **API**: `/api/deputies` returns `kpiV11`, `kpiEff`, `kpiDisc`, `kpiRes`, `kpiCtrl`, `kpiQual`, `shannon`, `adoptionRate`, `signal_warnings`, `signal_strengths`, `signal_features`

### Auto-recalculation
- `radacleaner-mpstats.timer` (every 6h): sync factions → sync stats → calc_kpi_v11

## Key scripts
| Script | Purpose |
|---|---|
| sync_all.py | Master pipeline: factions → stats → committees → MSI/K_pb → Quality/Risk → KPI → requests |
| bill_sync.py | Bill sync from RADA bulk JSON (status, documents, **authors**, isUrgent, isEuro) |
| sync_votes.py / sync_votes_bulk.py | Fetch voting records |
| sync_mp_factions.py | Deputy faction membership |
| sync_mp_bills.py | Bills per deputy (FULL NAME matching!) |
| sync_mp_stats.py | Voting stats per deputy (ПЯ/ПДА/ВКП) |
| sync_committee_members.py | Committee assignments |
| sync_deputy_requests.py | Deputy requests (matches by first+patronymic initials) |
| calc_bill_quality.py | Quality/Risk/Authorship recalculation (weighted by sponsor_order) |
| calc_kpi_v11.py | **KPI v11**: three-level system (KPI + Profile + Signals) |
| eu_alignment.py | EU alignment scoring |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger |
| telegram_bot.py | **Telegram bot**: /bill, /dep, /top, /eu, /start |
| telegram_notifier.py | Telegram alerts (send_message, format_risk/status) |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |
| worker/api-server.js | Express API (port 8788) — bills, deputies, EU alignment, schedule |
| dashboard/index.html | Cloudflare Pages frontend — single-page app (5 tabs) |

## Data sources
- **billinfo_list-skl9.json** — bill list (15K records, no authors)
- **billinfo-skl9.json** — full bill data (130MB, has `initiators` field with authors)
- Both from `data.rada.gov.ua/ogd/zpr/skl9/`
- Updated daily (usually overnight)

## DB schema (key tables)
- **bills** — 15K+ bills from RADA API. Has: significance, impact, risk_score, toxicity (set by LLM), is_urgent, is_euro (from JSON)
- **bill_sponsors** — 15K+ author records (extracted from JSON initiators). Columns: bill_id, mp_id, mp_name, rada_uid, sponsor_order
- **mps** — 460 deputies. rada_uid = stable identity key. 6 name changes in deputy_aliases.
  - kpi_v11_score, kpi_v11_rank — KPI v11 results (5 weighted components)
  - kpi_v11_effectiveness/discipline/efficiency/control/quality — 5 components
  - kpi_v12_score, kpi_v12_rank — KPI v12 results (6 equal-weight categories, research/validation)
  - kpi_v12_discipline/legislation/efficiency/committee/requests/impact — 6 components
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
- Key endpoints: `/api/bills`, `/api/deputies`, `/api/deputies/:name`, `/api/eu-alignment`, `/api/eu-alignment/trend`, `/api/schedule`, `/api/plenary-sessions`
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
- Free tier: 1500 req/day (quota may be exhausted by nightly batch)
- Prefer OpenRouter route for same model: `google/gemma-4-31b-it:free`

## Systemd Services
| Service | Timer | Purpose |
|---------|-------|---------|
| `radacleaner-api` | — | Express API (port 8788) |
| `radacleaner-tunnel` | — | Cloudflare Tunnel |
| `telegram-bot` | — | Telegram bot polling |
| `radacleaner-mpstats` | every 6h | factions + stats + **KPI v11 recalc** |
| `night-batch` | 21:00-08:00 | LLM analysis (nemotron-super) |
| `sync_bills` | periodic | Bill sync from RADA |
| `radacleaner-votesync` | every 6h | Voting records sync |

## Roadmap
See `RESEARCH.md` — "ROADMAP — Project Plan" section. 7 groups, dependency graph.
Current status: KPI v11 implemented, Dashboard updated with pentagon + profile + signals, Telegram bot running, EU Score done.

## Rules
- NEVER match deputies by last name alone — always full name
- mps.rada_uid is the stable identity key; text names are mutable
- FK relationships use mps.id, never text mp_name
- Dashboard deploys: `npx wrangler pages deploy dashboard --project-name radacleaner-dashboard`
- PostgreSQL uses %s placeholders, not ?
- One session = one logical step = one commit
- Before finishing: self-reflection — did I add dependencies/tables/scripts/APIs not in ARCHITECTURE.md?
