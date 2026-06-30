# RESEARCH.md — Ideas & Proposals (Architect's Working Document)

> This file is read by the Architect only. The Executor never reads it.

## Session 2026-06-30: Bill Assessment → KPI connection

### Problem found
`mps.bill_quality_score` is populated (396/466 deputies) but NO current script recalculates it. When new bills are analyzed via LLM, `risk_assessments` gets updated but `mps` columns stay frozen.

### Root cause
`sync_all.py` pipeline runs: factions → stats → committees → LEI → KPI. There is NO step that aggregates `risk_assessments` → `mps.bill_quality_score`. The `calc_deputy_kpi_v8.py` script READS `bill_quality_score` but never WRITES it.

### Evidence
```sql
-- bill_quality_score was set historically, not recalculated
SELECT bill_quality_score, avg_bill_significance, avg_bill_impact, avg_bill_toxicity, bills_analyzed_count
FROM mps WHERE bill_quality_score > 0 LIMIT 5;
-- All values present but frozen
```

### Proposal A: Add quality recalculation to sync_all.py
Add a SQL step AFTER bill_sponsors sync:
```sql
UPDATE mps SET
    bill_quality_score = sub.q,
    avg_bill_significance = sub.avg_s,
    avg_bill_impact = sub.avg_i,
    avg_bill_toxicity = sub.avg_t,
    bills_analyzed_count = sub.cnt
FROM (
    SELECT bs.rada_uid,
        AVG((ra.significance + ra.impact) / 2.0) as q,
        AVG(ra.significance) as avg_s,
        AVG(ra.impact) as avg_i,
        AVG(ra.toxicity) as avg_t,
        COUNT(*) as cnt
    FROM bill_sponsors bs
    JOIN risk_assessments ra ON ra.bill_id = bs.bill_id
    GROUP BY bs.rada_uid
) sub
WHERE mps.rada_uid = sub.rada_uid;
```
- Pros: Simple, one step in existing pipeline
- Cons: Runs on ALL deputies even if only 1 bill changed

### Proposal B: Trigger-based (per-bill update)
After `save_risk()` in `risk_storage.py`, immediately update the sponsoring deputy's quality score.
- Pros: Always up-to-date, no full rescan
- Cons: More complex, need to handle multiple sponsors per bill

### Proposal C: Hybrid — full recalc in sync_all + incremental in analyze_api
- Full recalc on sync_all (nightly)
- Incremental update after each analysis in analyze_api.py
- Pros: Best of both worlds
- Cons: Two code paths to maintain

### Recommendation
**Proposal A** is the lazy choice (one SQL block in sync_all.py). Since sync_all already runs after every 10 analyses (via analyze_api.py), the quality score will be at most ~10 bills stale. Good enough.

### KPI Formula History
| Version | LEI Formula | Quality Weight | Notes |
|---------|------------|----------------|-------|
| v1 | `Σ(stage_weight × Q)` | N/A | Original, quality-based LEI |
| v6 | `adopted² / total_bills` | 20% | Efficiency-based, penalizes spam |
| v6.1 | `adopted² / total_bills` | 20% | Same as v6 |
| v8 (current) | `log(1+adopted) / log(1+total×kpb)` | 15% | Logarithmic, with political barrier |

### DECISION (2026-06-30): Toxicity / Risk / Quality separation

**Problem:** `toxicity = significance × impact × risk_score / 125` bundles three things into one number. Then KPI uses `Impact = avg_tox × 100` as a REWARD metric. Result: deputies who write dangerous laws get HIGHER KPI.

**Decision:**
1. **Quality** = `AVG((significance + impact) / 2)` — reward metric. Higher = better.
2. **Risk** = `AVG(risk_score)` — penalty metric. Higher = worse.
3. **Remove Impact from KPI v8** — it was a duplicate of Quality distorted by risk_score.

**New KPI v9 formula:**
```
Score = 0.20×LEI + 0.15×ПЯ + 0.10×ПДА + 0.20×Quality + 0.15×Committee + 0.10×Conv + 0.10×RiskPenalty
```

Where:
- Quality = `AVG((significance + impact) / 2)` normalized 0-100
- RiskPenalty = `100 - AVG(risk_score) × 20` — high risk = low penalty score = lower KPI

**Impact of change:**
- Deputy writing 10 laws with risk_score=5: old Impact=40, new RiskPenalty=0 → KPI drops ~10 points
- Deputy writing 10 laws with risk_score=1: old Impact=8, new RiskPenalty=80 → KPI stays similar

**Files to change:**
- `sync_all.py` — add quality/risk recalculation step
- `calc_deputy_kpi_v8.py` → rename to `calc_deputy_kpi_v9.py` with new formula
- `risk_storage.py` — keep toxicity as-is (it's fine for Telegram alerts), but stop using it for KPI

### DECISION (2026-06-30): Authorship vs Co-authorship

**Problem:** `total_bills` and `total_laws` count ALL sponsorships equally. A deputy who wrote 5 bills and co-signed 100 = same as one who wrote 100 bills. LEI is inflated for co-authors (e.g., Мезенцева: LEI=294 but 0 adopted as primary author).

**Data:**
- `sponsor_order=0` = primary author (bill initiator)
- `sponsor_order>0` = co-author (signed someone else's bill)
- Coverage: 1,281 bills have BOTH sponsors AND risk_assessments. 752 bills have sponsors but no analysis yet.

**Decision — split metrics by authorship type:**

| Metric | Filter | Logic |
|--------|--------|-------|
| LEI | `sponsor_order = 0` only | Initiative metric — only own bills count |
| Conv | `sponsor_order = 0` only | Conversion of OWN bills to law |
| Quality | All bills, WEIGHTED by order | Contribution exists even as co-author, but weighted |
| RiskPenalty | All bills, WEIGHTED by order | Same — weighted penalty |
| ПЯ, ПДА | No change | Voting behavior, not bill-related |

**Quality weighting by sponsor_order:**
```
order = 0 → weight 1.0 (author)
order = 1 → weight 0.7 (first co-author, often co-initiator)
order = 2 → weight 0.5
order ≥ 3 → weight 0.3 (formal support)
```

**New metric: Authorship ratio**
```
authorship_ratio = primary_bills / total_bills
```
Not in KPI score itself, but displayed on dashboard as profile indicator.

### DECISION (2026-06-30): Analysis coverage handling

**Problem:** 752 bills have sponsors but no risk_assessments yet. Analysis runs in background, will eventually cover all.

**Decision:**
- **LEI, Conv**: Count ALL bills (analysis not needed)
- **Quality, RiskPenalty**: Count ONLY bills with risk_assessments. Unanalyzed bills are SKIPPED in the average.
- **Deputy with 0 analyzed bills**: Quality = 50, RiskPenalty = 50 (neutral, no penalty/reward)

**Why:** We don't know if unanalyzed bills are good or bad → don't reward or punish. As analysis progresses, Quality and RiskPenalty become more representative automatically.

### BUG FIX (2026-06-30): Unanalyzed deputies get perfect RiskPenalty

**Problem:** `avg_risk_score = 0` for deputies with 0 analyzed bills. KPI v9 calculates `risk_penalty = 100 - 0×20 = 100` (perfect score). 37 deputies without any data get ideal RiskPenalty, pushing them to top of KPI.

**Root cause:** `calc_bill_quality.py` stores `avg_risk_score = 0` for unanalyzed deputies. KPI v9 treats 0 as "zero risk" instead of "no data".

**Fix:**
1. `calc_bill_quality.py`: for deputies with 0 analyzed bills → set `avg_risk_score = NULL` (not 0)
2. `calc_deputy_kpi_v9.py`: already handles this correctly with `if analyzed > 0` check

**Verification:** After fix, Бондар В.В. (0 analyzed bills) should have risk_penalty = 50 (neutral), not 100.

### Open questions
1. Should Quality weight stay at 20% or adjust?
2. Should we add a "recency" factor (recent bills weighted higher)?
3. RiskPenalty formula: linear `100 - risk×20` or threshold-based (risk>3 = -20 penalty)?
