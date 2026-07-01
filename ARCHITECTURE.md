# Architecture — Rada Cleaner

## Stack
- **Python 3** — sync scripts, LLM analysis, KPI calculation
- **PostgreSQL 18.4** — primary DB at 192.168.1.244/radacleaner
- **Express (Node.js)** — API server (worker/api-server.js, port 8788)
- **Cloudflare Pages** — dashboard (dashboard/)
- **Cloudflare Tunnel** — exposes API as api.dino.pp.ua
- **LLM** — `nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter (primary), NVIDIA API (secondary), Gemini gemma-4 (backup). See `scripts/test_llm_providers.py`.

## Directory layout
```
src/              — shared Python modules (db, llm_client, helpers)
worker/           — Express API server (api-server.js)
dashboard/        — Cloudflare Pages frontend (index.html, dashboard.js)
data/             — SQLite backup (superseded by PG)
migrations/       — SQL migration files
scripts/          — utility scripts
systemd/          — .service unit files
tests/            — tests
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
calc_deputy_kpi_v10.py → mps (kpi_score, kpi_rank, lei)
```

## KPI v10 Formula (UPDATED 2026-06-30)

```
Score = 0.20×LEI + 0.15×ПЯ + 0.10×ПДА
      + (0.15×Quality + 0.10×Committee + 0.10×Conv + 0.10×RiskPenalty + 0.10×Requests) × att_mult
```

### Metrics

| Metric | Source | Filter | Meaning | Weight |
|--------|--------|--------|---------|--------|
| LEI | `log(1+adopted_primary) / log(1+total×kpb)` | **order=0 only** | Legislative effectiveness (own bills only) | 0.20 |
| ПЯ | `attended/total × 100` | all | Attendance rate | 0.15 |
| ПДА | `voted/attended × 100` | all | Voting activity | 0.10 |
| Quality | `AVG((significance + impact) / 2)` | **weighted by order** | Bill importance (REWARD) | 0.15 |
| Committee | `committee_score` | all | Committee role weight | 0.10 |
| Conv | `adopted_primary/total_bills × kpb × 100` | **order=0 only** | Own bill → law conversion | 0.10 |
| RiskPenalty | `100 - AVG(risk_score) × 20` | **weighted by order** | Danger to democracy (PENALTY) | 0.10 |
| Requests | `requests_with_response` | all | Constituent responses (REWARD) | 0.10 |

### Attendance multiplier (applied to Quality, Committee, Conv, RiskPenalty)

```
ПЯ < 30%  → att_mult = 0.3 (70% penalty)
ПЯ 30-50% → att_mult = 0.6 (40% penalty)
ПЯ 50-70% → att_mult = 0.85 (15% penalty)
ПЯ > 70%  → att_mult = 1.0 (no penalty)
```

### Special rules

- **Quality weights by sponsor_order:** 0→1.0, 1→0.7, 2→0.5, ≥3→0.3
- **LEI & Conv = PRIMARY authorship only:** adopted_primary (order=0). Co-authorship NOT counted.
- **Analysis coverage:** Quality/RiskPenalty skip unanalyzed bills. Deputy with 0 analyzed → neutral 50 (not normalized, stays 50).
- **Committee threshold:** If total_primary = 0 → committee_weight × 0.5
- **Committee roles:** chair=10, vice_chair=7, secretary=5, subcommittee_head=5, member=3
- **LEI sync:** `mps.lei` stores v9 values (updated after each KPI calculation)
- **Requests:** uses `requests_with_response` (not total requests). **Threshold:** ПЯ < 30% → requests = 0
- **Zero attendance floor:** ПЯ < 10% → Score = 0

### Dashboard metric
- `authorship_ratio = primary_bills / total_bills` (profile indicator, not in score)

## Key scripts
| Script | Purpose |
|---|---|
| sync_all.py | Master pipeline: factions → stats → committees → MSI/K_pb → Quality/Risk → KPI v10 → requests |
| bill_sync.py | Bill sync from RADA bulk JSON (status, documents, **authors**) |
| scrape_sponsors.py | Fallback: scrape authors from HTML bill cards |
| sync_votes.py / sync_votes_bulk.py | Fetch voting records |
| sync_mp_factions.py | Deputy faction membership |
| sync_mp_bills.py | Bills per deputy (FULL NAME matching!) |
| sync_mp_stats.py | Voting stats per deputy |
| sync_committee_members.py | Committee assignments |
| sync_deputy_requests.py | Deputy parliamentary requests (matches by first+patronymic initials) |
| calc_msi_kpb.py | MSI + K_pb (political barrier) calculation |
| calc_bill_quality.py | Quality/Risk/Authorship recalculation (weighted by sponsor_order) |
| calc_deputy_kpi_v9.py | KPI v10 score calculation (current) |
| eu_alignment.py | EU alignment scoring |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger |
| telegram_notifier.py | Telegram alerts (send_message, format_risk/status) |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |

## Data sources
- **billinfo_list-skl9.json** — bill list (15K records, no authors)
- **billinfo-skl9.json** — full bill data (130MB, has `initiators` field with authors)
- Both from `data.rada.gov.ua/ogd/zpr/skl9/`
- Updated daily (usually overnight)

## DB schema (key tables)
- **bills** — 15K+ bills from RADA API. Has: significance, impact, risk_score, toxicity (set by LLM), is_urgent, is_euro (from JSON)
- **bill_sponsors** — 15K+ author records (extracted from JSON initiators). Columns: bill_id, mp_id, mp_name, rada_uid, sponsor_order
- **mps** — 460 deputies (389 active + former). rada_uid = stable identity key. 6 name changes tracked in deputy_aliases.
  - kpi_score, kpi_rank — KPI v10 results
  - lei — Legislative Effectiveness Index (v9 values, synced)
  - bill_quality_score — weighted quality (NULL = no analyzed bills)
  - avg_risk_score — average risk (NULL = no analyzed bills)
  - authorship_ratio — primary_bills / total_bills
  - bills_analyzed_count — count of bills with risk_assessments
  - eu_integration_score — EU focus (isEuro×2 + eu_risk + state_aid×3) / total
  - eu_euro_bills, eu_risk_bills, eu_state_aid_bills — EU breakdown
  - requests_with_response — deputy requests with responses
- **mp_votes** — 7.5M voting records
- **mp_bills** — bills authored by deputies
- **bill_sponsors** — deputy↔bill links (rada_uid, mp_id, sponsor_order)
- **risk_assessments** — LLM analysis results (significance, impact, risk_score, toxicity, overall_score)
- **committee_members** — committee assignments
- **eu_alignment_overall / eu_alignment_chapters** — EU alignment scores
- **deputy_aliases** — name change history

## API
- Express: `https://api.dino.pp.ua` (tunnel → localhost:8788)
- Dashboard: Cloudflare Pages static site
- Key endpoints: `/api/bills`, `/api/deputies`, `/api/eu-alignment`, `/api/eu-alignment/trend`

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

## Roadmap
See `RESEARCH.md` — "ROADMAP — Project Plan" section. 7 groups, dependency graph, execution order.
Current status: Authors extracted (99.95%), LLM migrated to nemotron-super. Next: analyze remaining 9,257 bills.

## Rules
- NEVER match deputies by last name alone — always full name
- mps.rada_uid is the stable identity key; text names are mutable
- FK relationships use mps.id, never text mp_name
- Dashboard deploys: `npx wrangler pages deploy dashboard --project-name radacleaner-dashboard`
- PostgreSQL uses %s placeholders, not ?
- One session = one logical step = one commit
- Before finishing: self-reflection — did I add dependencies/tables/scripts/APIs not in ARCHITECTURE.md?
