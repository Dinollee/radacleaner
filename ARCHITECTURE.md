# Architecture — Rada Cleaner

## Stack
- **Python 3** — sync scripts, LLM analysis, KPI calculation
- **PostgreSQL 18.4** — primary DB at 192.168.1.244/radacleaner
- **Express (Node.js)** — API server (worker/api-server.js, port 8788)
- **Cloudflare Pages** — dashboard (dashboard/)
- **Cloudflare Tunnel** — exposes API as api.dino.pp.ua
- **LLM** — OpenRouter owl-alpha (primary), Gemini gemma-4 (fallback)

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
RADA API
  ↓
bills (significance, impact, risk_score, toxicity)
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
| sync_deputy_requests.py | Deputy parliamentary requests |
| calc_msi_kpb.py | MSI + K_pb (political barrier) calculation |
| calc_bill_quality.py | Quality/Risk/Authorship recalculation (weighted by sponsor_order) |
| calc_deputy_kpi_v9.py | KPI v10 score calculation (current) |
| eu_alignment.py | EU alignment scoring |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |

## Data sources
- **billinfo_list-skl9.json** — bill list (15K records, no authors)
- **billinfo-skl9.json** — full bill data (130MB, has `initiators` field with authors)
- Both from `data.rada.gov.ua/ogd/zpr/skl9/`
- Updated daily (usually overnight)

## DB schema (key tables)
- **bills** — 15K+ bills from RADA API. Has: significance, impact, risk_score, toxicity (set by LLM)
- **bill_sponsors** — 15K+ author records (extracted from JSON initiators). Columns: bill_id, mp_id, mp_name, rada_uid, sponsor_order
- **mps** — 389 active + 53 former deputies. rada_uid = stable identity key
  - kpi_score, kpi_rank — KPI v10 results
  - lei — Legislative Effectiveness Index (v9 values, synced)
  - bill_quality_score — weighted quality (NULL = no analyzed bills)
  - avg_risk_score — average risk (NULL = no analyzed bills)
  - authorship_ratio — primary_bills / total_bills
  - bills_analyzed_count — count of bills with risk_assessments
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

## Rules
- NEVER match deputies by last name alone — always full name
- mps.rada_uid is the stable identity key; text names are mutable
- FK relationships use mps.id, never text mp_name
- Dashboard deploys: `npx wrangler pages deploy dashboard --project-name radacleaner-dashboard`
- PostgreSQL uses %s placeholders, not ?
- One session = one logical step = one commit
- Before finishing: self-reflection — did I add dependencies/tables/scripts/APIs not in ARCHITECTURE.md?
