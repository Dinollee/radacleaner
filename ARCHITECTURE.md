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
mps (bill_quality_score, avg_bill_significance, avg_bill_impact, avg_bill_toxicity, bills_analyzed_count)
  ↓
KPI v9 Score (0-100)
```

**Quality** = `AVG((significance + impact) / 2)` — reward: higher = better
**RiskPenalty** = `100 - AVG(risk_score) × 20` — penalty: higher risk = lower score

## KPI v9 Formula (DECIDED 2026-06-30)

```
Score = 0.20×LEI + 0.15×ПЯ + 0.10×ПДА + 0.20×Quality + 0.15×Committee + 0.10×Conv + 0.10×RiskPenalty
```

| Metric | Source | Filter | Meaning | Weight |
|--------|--------|--------|---------|--------|
| LEI | `log(1+adopted) / log(1+total×kpb)` | **order=0 only** | Legislative effectiveness (own bills) | 0.20 |
| ПЯ | `attended/total × 100` | all | Attendance rate | 0.15 |
| ПДА | `voted/attended × 100` | all | Voting activity | 0.10 |
| Quality | `AVG((significance + impact) / 2)` | **weighted by order** | Bill importance (REWARD) | 0.20 |
| Committee | `committee_score` | all | Committee role weight | 0.15 |
| Conv | `adopted/total_bills × kpb × 100` | **order=0 only** | Own bill → law conversion | 0.10 |
| RiskPenalty | `100 - AVG(risk_score) × 20` | **weighted by order** | Danger to democracy (PENALTY) | 0.10 |

**Quality weights by sponsor_order:** 0→1.0, 1→0.7, 2→0.5, ≥3→0.3
**Analysis coverage:** Quality/RiskPenalty skip unanalyzed bills. Deputy with 0 analyzed bills gets neutral 50/50.
**Dashboard metric:** `authorship_ratio = primary_bills / total_bills` (profile indicator, not in score)

| Metric | Source | Meaning |
|--------|--------|---------|
| LEI | `adopted² / total_bills × kpb` | Legislative effectiveness |
| ПЯ | `attended/total × 100` | Attendance rate |
| ПДА | `voted/attended × 100` | Voting activity |
| Quality | `mps.bill_quality_score` | Bill quality (significance+impact avg) |
| Committee | `mps.committee_score` | Committee role weight |
| Conv | `adopted/total_bills × kpb × 100` | Bill → law conversion rate |
| Impact | `avg_tox × 100` | Avg toxicity of authored bills |

## Key scripts
| Script | Purpose |
|---|---|
| sync_all.py | Master pipeline: factions → stats → committees → LEI → KPI |
| sync_bills.py | Fetch bills from RADA API |
| sync_votes.py / sync_votes_bulk.py | Fetch voting records |
| sync_mp_factions.py | Deputy faction membership |
| sync_mp_bills.py | Bills per deputy (FULL NAME matching!) |
| sync_mp_stats.py | Voting stats per deputy |
| sync_committee_members.py | Committee assignments |
| sync_deputy_requests.py | Deputy parliamentary requests |
| calc_msi_kpb.py | MSI + K_pb (political barrier) calculation |
| calc_deputy_kpi_v8.py | KPI score calculation (current) |
| eu_alignment.py | EU alignment scoring |
| analyze_api.py | LLM risk analysis worker |
| night_batch.py | Nightly bill fetch + analysis trigger |
| d1_client.py | PostgreSQL client (auto-converts ? → %s) |

## DB schema (key tables)
- **bills** — 15K+ bills from RADA API. Has: significance, impact, risk_score, toxicity (set by LLM)
- **mps** — 389 active + 53 former deputies. rada_uid = stable identity key
- **mp_votes** — 7.5M voting records
- **mp_bills** — bills authored by deputies
- **bill_sponsors** — deputy↔bill links (rada_uid, mp_id)
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
