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

### DECISION (2026-06-30): Progressive attendance penalty

**Problem:** ПЯ and ПДА are additive (0.15×ПЯ + 0.10×ПДА). Deputy with ПЯ=33% gets only 7.95 fewer points than one with ПЯ=86%. Too small a gap for 53% attendance difference.

**HR logic:** Discipline is mandatory, not optional. Low attendance should penalize more heavily.

**Decision — progressive multiplier:**
```
ПЯ < 30%  → multiplier 0.3 (70% penalty)
ПЯ 30-50% → multiplier 0.6 (40% penalty)
ПЯ 50-70% → multiplier 0.85 (15% penalty)
ПЯ > 70%  → multiplier 1.0 (no penalty)
```

Applied to: Quality, Committee, Conv, RiskPenalty (not to ПЯ/ПДА themselves — they're already attendance metrics).

**Effect:** Бондар (ПЯ=33%) gets multiplier 0.6 on all other metrics → Score drops ~20 points.

### DECISION (2026-06-30): Committee weight for legislators without bills

**Problem:** Committee_score=10 for institutional leaders (Speaker, deputies) without any bills. Стефанчук: committee=10, but primary_bills=22, adopted=7. Бондар: committee=10, primary_bills=2, adopted=1.

**Decision:** If total_primary = 0, committee_weight = 0.5 (halved). These are institutional roles, not legislative.

**Note:** Стефанчук actually has primary_bills=22, so he IS a legislator. The rule only affects deputies with ZERO primary bills.

### BUG (2026-06-30): LEI in DB (v8) ≠ LEI in KPI (v9)

**Problem:** `mps.lei` column stores v8 values (calculated from total_bills), but KPI v9 calculates LEI in Python using primary_bills only. The database value is stale and misleading.

**Evidence:**
- Устінова: db_lei=42 (v8, total_bills=304), v9_lei=0.42 (primary_bills=5)
- Скороход: db_lei=8.2 (v8, total_bills=214), v9_lei=0.24 (primary_bills=18)

**Fix options:**
1. Update `mps.lei` after v9 calculation (sync value)
2. Remove `mps.lei` column entirely (KPI calculates in Python)
3. Add separate `mps.lei_v9` column

**DECIDED:** Option 1 — update `mps.lei` with v9 values. Old LEI was wrong, no point keeping it.

### DECISION (2026-06-30): LEI formula — volume vs conversion

**Current formula:** `LEI = log(1+adopted) / log(1+total×kpb)`

**Problem:** Logarithmic scale favors absolute numbers over conversion rate:
- 100 bills, 35 adopted (35%) → LEI = 0.79
- 10 bills, 3.5 adopted (35%) → LEI = 0.65
- Same conversion, different LEI

**Proposal A (current):** Keep logarithmic — rewards volume + conversion. Rationale: deputies who submit MORE bills and get them passed are more effective.

**Proposal B (linear conversion):** `LEI = (adopted/total) × kpb × 100`. Pure conversion rate. Rationale: quality over quantity.

**Proposal C (hybrid):** `LEI = log(1+adopted) × (adopted/total)`. Combines both. Rationale: rewards conversion, but absolute numbers still matter.

**Recommendation:** Proposal A (keep current) is acceptable. The logarithmic formula already penalizes spam (high total with low adoption = low LEI). The issue was v8 using ALL bills vs v9 using only primary — that's the real fix.

### DECISION (2026-06-30): LEI should use weighted adopted count

**Problem:** LEI uses only primary authorship (order=0). Deputy who co-authored 1 adopted bill gets LEI=0. Example: Безугла: 0 adopted as primary → LEI=0, but she co-authored 1 adopted bill.

**Evidence:**
- Безугла: adopted_primary=0, adopted_weighted=0.3 → LEI 0.00 → 0.25
- Підласа: adopted_primary=1, adopted_weighted=1.6 → LEI 0.37 → 0.51

**Decision:** Use weighted adopted count for LEI, same weights as Quality:
```
adopted_weighted = Σ(weight) для принятых законов
  order=0 → 1.0
  order=1 → 0.7
  order=2 → 0.5
  order≥3 → 0.3
```

**Formula:** `LEI = log(1+adopted_weighted) / log(1+total_primary×kpb)`

**Effect:** Co-authored adopted bills now contribute to LEI, but with reduced weight. Pure co-authors get some credit, but less than primary authors.

### TASK (2026-06-30): Fix committee roles parsing + deputy appeals

**Problem 1: 168 "chairs"**
- `sync_committee_members.py` parses "Голова" → chair
- But "Голова підкомітету" is NOT a committee chair
- Real: 23 committee chairs, ~145 subcommittee chairs
-committee_score incorrectly gives 10 to subcommittee chairs

**Fix:**
1. Update `sync_committee_members.py` to distinguish:
   - "Голова Комітету" (without "підкомітету") → chair (score=10)
   - "Голова підкомітету" → subcommittee_head (score=5)
2. Re-run sync to fix roles
3. Update `mps.committee_score` from committee_members

**Problem 2: Deputy appeals not in KPI**
- `mps.request_count` and `mps.requests_with_response` exist
- Currently NOT used in KPI v10
- User decided: use `requests_with_response` (not total requests)

**Fix:**
1. Add `requests_with_response` to KPI formula
2. Weight: 0.10 (same as Conv)
3. Metric: `requests_with_response / total_deputies` normalized 0-100

**Files to change:**
- `sync_committee_members.py` — fix role parsing
- `calc_deputy_kpi_v9.py` — add requests metric
- `calc_bill_quality.py` — update committee_score from committee_members

### TASK (2026-06-30): Virtual KPI formula testing

**Goal:** Test KPI v10 formula stability and logical consistency using virtual (mock) deputy data.

**What to do:**
1. Create a Python script `test_kpi_formula.py` that generates 10-15 virtual deputies with extreme/edge-case metrics
2. Calculate KPI score for each using the v10 formula
3. Output a table showing: name, each metric (raw), normalized values, final score
4. Test these scenarios:

**Scenario A: "Ideal legislator"**
- High LEI (many adopted bills), high Quality, high Committee, high Requests
- ПЯ=95%, ПДА=100%
- Expected: Score > 80

**Scenario B: "Absentee with good bills"**
- High Quality (good laws), but ПЯ=25%
- Expected: Score < 40 (progressive penalty kicks in)

**Scenario C: "Rubber stamp"**
- High attendance (ПЯ=95%), high ПДА (100%), but low Quality (bad laws)
- Expected: Score moderate (attendance helps, but bad laws hurt)

**Scenario D: "Institutional leader"**
- Committee chair (10), but total_primary=0, no bills
- Expected: Score moderate (committee halved)

**Scenario E: "Co-author specialist"**
- Low primary bills, but high weighted adopted (many co-authored)
- Expected: LEI > 0 (weighted adopted works)

**Scenario F: "Protest voter"**
- High attendance, but abstains 50% of time (low ПДА)
- Expected: Score penalized

**Output format:**
```
Virtual Deputy Test Results
==========================

Name              | LEI   | ПЯ    | ПДА   | Qual  | Comm  | Conv  | Risk  | Req   | Score
------------------+-------+-------+-------+-------+-------+-------+-------+-------+------
Ideal_Legislator  | 1.200 | 95.0  | 100.0 | 4.50  | 10    | 45.0  | 20.0  | 25    | XX.X
Absentee_Good     | 0.800 | 25.0  | 90.0  | 4.20  | 7     | 35.0  | 25.0  | 10    | XX.X
...

Analysis:
1. Stability: Are scores within 0-100? No extreme outliers?
2. Logic: Does progressive penalty work correctly?
3. Ideology: Does the formula reward good behavior and penalize bad?
```

**Files to create:**
- `test_kpi_formula.py` — virtual testing script

**Report to Architect:** Table + analysis of stability, logic, and ideology alignment.

### DECISION (2026-06-30): Zero attendance floor + Requests threshold

**Problem 1:** Zero_Attendance score = 29.9 (too high for ПЯ=0%)
**Problem 2:** Deputy with ПЯ=0% cannot submit requests, but Requests metric still counts

**Decision:**
- Score = 0 if ПЯ < 10% (hard floor — no work = no score)
- Requests_effective = 0 if ПЯ < 30% (can't submit requests without attending)
- att_mult + ПЯ/ПДА double penalty: FEATURE, keep as-is

**Final formula:**
```
Score = 0.20×LEI + 0.15×ПЯ + 0.10×ПДА
      + (0.15×Quality + 0.10×Committee + 0.10×Conv
         + 0.10×RiskPenalty + 0.10×Requests_effective) × att_mult

where:
  att_mult = 0.3  if ПЯ < 30%
  att_mult = 0.6  if 30% ≤ ПЯ < 50%
  att_mult = 0.85 if 50% ≤ ПЯ < 70%
  att_mult = 1.0  if ПЯ ≥ 70%

  Requests_effective = 0 if ПЯ < 30%
  Requests_effective = Requests if ПЯ ≥ 30%

  Score = 0 if ПЯ < 10%
```

### DECISION (2026-06-30): LEI = hardcore primary authorship

**Problem:** Top 3 deputies (Батенко, Корнієнко, Герасимов) get 100% of LEI from co-authorship. They authored 0 laws, but co-authored many. LEI should measure personal legislative effectiveness, not team contributions.

**Evidence:**
- Батенко: LEI=1.550, but primary_adopted=0 (100% from co-authorship)
- Корнієнко: LEI=1.675, but primary_adopted=0 (100% from co-authorship)
- Марусяк: LEI=1.577, primary_adopted=1 (100% from primary authorship)

**Decision:**
- LEI = `log(1+adopted_primary) / log(1+total_primary×kpb)` — only primary authorship
- Co-authorship removed from LEI/Conv
- Co-authorship data preserved in `authorship_ratio` for dashboard display

**Future option:** Teamwork metric (co-authorship) may be added to KPI later with weight 0.05-0.10. For now, not included.

### Open questions
1. Should Quality weight stay at 20% or adjust?
2. Should we add a "recency" factor (recent bills weighted higher)?
3. RiskPenalty formula: linear `100 - risk×20` or threshold-based (risk>3 = -20 penalty)?
4. LEI formula: change to favor conversion over volume? Current: log(1+adopted)/log(1+total×kpb). Alternative: (adopted/total)×kpb (linear conversion).
