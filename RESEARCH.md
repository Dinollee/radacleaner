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

### ~~TASK (2026-06-30): Extract authors from billinfo_full JSON~~ — COMPLETED

**Status:** 15,181 of 15,188 bills have authors (99.95%). Extraction was done in a previous session.

### DECISION (2026-07-01): Free LLM providers — migration from dead owl-alpha

**Problem:** `openrouter/owl-alpha` returned 404 since 2026-06-30. Gemini direct API hit 1500 req/day free quota. Night batch effectively dead.

**Research:** Tested 20+ free models across 3 providers (OpenRouter, NVIDIA API, Gemini direct). Test script: `scripts/test_llm_providers.py`

**Results — working models (OpenRouter free tier):**

| Model | Time | Quality | Context |
|-------|------|---------|---------|
| nvidia/nemotron-3-super-120b-a12b:free | 50s | Excellent | 1M |
| nvidia/nemotron-3-ultra-550b-a55b:free | 111s | Excellent | 1M |
| google/gemma-4-31b-it:free | 7s | Good | 262K |
| nvidia/nemotron-3-nano-30b-a3b:free | 3s | Fair | 256K |
| openrouter/free | 62s | Good | 200K |

**Results — working models (NVIDIA API, free tier, 40 req/min):**

| Model | Time | Quality |
|-------|------|---------|
| nvidia/nemotron-3-super-120b-a12b | 17s | Excellent |
| mistralai/mistral-large-3-675b-instruct-2512 | 6s | Excellent |
| mistralai/mistral-medium-3.5-128b | 4s | Good |
| nvidia/llama-3.3-nemotron-super-49b-v1.5 | 12s | Good |
| meta/llama-4-maverick-17b-128e-instruct | 3s | Good |

**Results — Gemini direct API:**
- `gemma-4-31b-it` works (58s via NVIDIA, 7s via OpenRouter) but free quota = 1500 req/day
- Night batch with 4000+ bills exhausts quota in first hour

**Recommendation:**
1. Primary: `nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter (best balance: quality + speed + 1M context)
2. Fallback: `mistralai/mistral-large-3-675b-instruct-2512` via NVIDIA API (fast, 675B, references Constitution)
3. Backup: `google/gemma-4-31b-it:free` via OpenRouter
4. Remove `--force` from nightly batch (toxicity IS NULL guard works)
5. Reduce workers to 1 (rate limit protection)

**Provider testing:** `./venv/bin/python scripts/test_llm_providers.py`
- `--provider openrouter` — test OpenRouter only
- `--provider nvidia` — test NVIDIA API only
- `--provider gemini` — test Gemini direct only
- `--model <id>` — test single model

### Open questions
1. Should Quality weight stay at 20% or adjust?
2. Should we add a "recency" factor (recent bills weighted higher)?
3. RiskPenalty formula: linear `100 - risk×20` or threshold-based (risk>3 = -20 penalty)?
4. LEI formula: change to favor conversion over volume? Current: log(1+adopted)/log(1+total×kpb). Alternative: (adopted/total)×kpb (linear conversion).

---

## ROADMAP — Project Plan (2026-07-01)

> Grouped by dependency. Each group = one logical work session.

### GROUP 1: Data completeness (no external dependencies)
**Goal:** All bills analyzed, all deputies have up-to-date KPI.

**Current state (verified 2026-07-01):**
- Authors: 15,181/15,188 (99.95%) ✅ DONE
- Analyzed: 5,931/15,188 (39%) — 9,257 bills need analysis
- Deputies: 460 (6 duplicates removed)
- KPI: 460/460 ✅
- Quality: 456/460 ✅
- Deputy requests: 303 deputies with responses ✅
- Committee: 385 members, scores synced (22 chairs, 54 vice, 164 sub+sec, 143 members)

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 1.1 | ~~Extract authors from JSON~~ | ✅ DONE | — |
| 1.2 | Night batch: auto-fills 9,257 bills overnight (no action needed) | AUTO | Quality/RiskPenalty accuracy |
| 1.3 | ~~Sync deputy requests~~ | ✅ DONE | — |
| 1.4 | Virtual KPI formula testing (`test_kpi_formula.py`) | OPEN | Confidence in formula |
| 1.5 | ~~Fix 6 duplicate rada_uid + committee sync~~ | ✅ DONE | — |
| 1.6 | ~~Extract isUrgent + isEuro from billinfo_full JSON~~ | ✅ DONE | — |

### JSON Metrics Extraction (from billinfo_full, 130MB)

**Problem:** `bill_sync.py` extracts `rubric`, `subject`, `initiators`, `documents`, `passings` from JSON — but ignores `isUrgent`, `isEuro`, `type`.

**Available fields NOT in DB:**
| Field | Description | KPI/Dashboard potential |
|-------|-------------|------------------------|
| `isUrgent` | Срочный закон | ⚡ Urgent bills ratio per deputy |
| `isEuro` | Закон про ЄС-інтеграцію | 🇪🇺 EU focus per deputy (feeds Group 2) |
| `type` | Тип (звичайний, процедура) | 📋 Better procedural filter |

**Recommended metrics:**
1. `urgent_ratio = urgent_bills / total_bills × 100` — does the deputy react to current problems?
2. `eu_ratio = euro_bills / total_bills × 100` — EU integration focus (ties to Group 2)

**Implementation:** Add extraction to `process_full_data()` in `bill_sync.py` — same pass where initiators are extracted. Two boolean columns in `bills` table: `is_urgent`, `is_euro`.

**Night batch approach (CONFIRMED):** Free models are overloaded during the day, stable at night. Night batch runs automatically via systemd (21:00–08:00), fills data incrementally. No manual intervention needed. As bills get analyzed, Quality/RiskPenalty metrics become more accurate automatically.

### GROUP 2: EU Integration — Deputy Score
**Goal:** Every deputy has an EU integration score: who blocks, who advances.

**Problem:** EU Alignment = per-bill keyword matching (35 chapters). No per-deputy breakdown.

**Data sources:**
- **Option A: `isEuro` from JSON (Task 1.6)** — boolean flag per bill. Simple: `COUNT(euro bills by deputy) / total`. Fast, free.
- **Option B: risk_assessments** — LLM flags "Невідповідність ЄС" as risk category. Aggregate by bill_sponsors → deputy. More nuanced (pro/anti).
- **Option C: EU keyword matching in bill text** — 35 EU acquis chapters. Most accurate but slowest.

**Recommendation:** Option A (isEuro) first — one SQL query after Task 1.6 adds the column. Option B as enrichment for depth.

**Clarification: isEuro vs EU State Aid vs LLM EU risks — THREE different markers:**

| Marker | Source | Meaning | Use |
|--------|--------|---------|-----|
| `isEuro` (JSON) | RADA API metadata | Bill tagged as EU integration by RADA | Deputy's EU focus (% of EU bills) |
| "Євроінтеграційні ризики" | LLM risk_categories | Bill may conflict with acquis | Risk metric (58 hits in 5,180 analyses) |
| "Державна допомога ЄС" | LLM risk_categories | Bill is state aid violating EU rules | Specific compliance marker |

**For EU Score per deputy:** Use `isEuro` (primary) + LLM "державна допомога" (enrichment). These are complementary, not redundant.

**EU State Aid marker** — LLM already detects `risk_categories[].category` containing "державна допомога" or "ЄС/ОЕСР". These bills link to specific deputies via `bill_sponsors`. Formula:
```
eu_score_deputy = COUNT(bills where risk_categories mention EU) / total_bills_by_deputy
```
Negative: bills that BLOCK EU integration (anti-reform).
Positive: bills that ADVANCE EU integration (pro-reform).

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 2.1 | ~~SQL: aggregate EU risk_categories per deputy~~ | ✅ DONE | — |
| 2.2 | Classify EU bills as pro/anti reform (keyword: "гармонізація", "відповідність" = pro; "обмеження", "суперечність" = anti) | OPEN | Dashboard |
| 2.3 | ~~Add `eu_integration_score` to mps table~~ | ✅ DONE | — |
| 2.4 | ~~API endpoint: /api/deputies returns euScore fields~~ | ✅ DONE | — |

### GROUP 3: Dashboard Unification
**Goal:** Single view: Top deputies + Top problematic bills + EU score + Fakes.

**Current:** Tabs (Deputies, EU Alignment, Bills) — disconnected. EU Alignment has only 20 records.
**Target:** Unified dashboard with 4 blocks on one page:
1. **Топ ефективних депутатів** — KPI ranking, filtered by faction
2. **Топ проблемних законів** — highest risk_score bills, recent (5,931 analyzed, 9,257 pending)
3. **Євроінтеграція** — overall % + per-deputy EU score (from Group 2)
4. **Фейки та маніпуляції** — bills with high toxicity + debunked claims (from Group 4)

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 3.1 | Design unified dashboard layout (figma/sketch or HTML mockup) | OPEN | Implementation |
| 3.2 | API: `/api/dashboard/unified` — returns all 4 blocks in one call | OPEN | Frontend |
| 3.3 | Frontend: replace tabs with unified layout | OPEN | — |

### GROUP 4: News Monitoring & Fake Detection
**Goal:** Track news mentioning our laws, detect fakes/misinformation.

**Approach:**
- RSS feeds: Ukrainska Pravda, EP, Interfax-Ukraine, Unian
- Search keywords: bill numbers, "Верховна Рада", "законопроєкт"
- LLM classification: real news / opinion / fake / manipulation
- Link to bills via bill_number matching

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 4.1 | RSS scraper: collect news mentioning bill numbers | OPEN | Dashboard |
| 4.2 | LLM classification: news sentiment + fake detection | OPEN | Dashboard |
| 4.3 | Store in `news_mentions` table (bill_id, source, url, sentiment, is_fake) | OPEN | Dashboard |

### GROUP 5: Telegram Notifications
**Goal:** Automated alerts for high-risk bills, daily digest, deputy alerts.

**Subtasks:**
| # | Task | Status | Blocks |
|---|------|--------|--------|
| 5.1 | High-risk alert: toxicity > 0.7 → instant Telegram message | OPEN | — |
| 5.2 | Daily digest: top 5 bills + top 3 deputies + EU score change | OPEN | — |
| 5.3 | Weekly digest: trends, new fakes, EU progress | OPEN | — |
| 5.4 | Deputy alert: if a tracked deputy's bill gets high risk → notify | OPEN | — |

**Dependencies:** Group 2 (EU score), Group 4 (fakes).

### GROUP 6: Digest Design + Social Media
**Goal:** Digest format for Telegram, Twitter, Facebook.

**Digest structure (proposal):**
```
🛡 Страж Демократії — Дайджест [дата]

📊 Топ депутатів тижня:
1. Фракція — Ім'я — KPI 85.2 (+2.1)
2. ...

⚠️ Топ проблемних законів:
1. #14332 — Податковий кодекс — risk: high (3 ризики)
2. ...

🇪🇺 Євроінтеграція: 42% законів відповідають acquis (+1.2%)

🔍 Фейки тижня:
- [назва] — спростовано
```

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 6.1 | Design digest template (Telegram, Twitter 280 chars, Facebook) | OPEN | Implementation |
| 6.2 | Twitter/Facebook API integration | OPEN | Posting |
| 6.3 | Cron: daily digest at 08:00 | OPEN | — |

### GROUP 7: KPI UX for Regular Voters
**Goal:** Make KPI understandable for non-experts.

**Problem:** Current KPI = 8 metrics, weighted formula. Voter sees "KPI = 67.3" without understanding what it means.

**Proposal:** Simplified view with explanations:
- "Ефективність" (LEI) — скільки законів прийнято з поданих
- "Дисципліна" (ПЯ + ПДА) — відвідування + голосування
- "Якість" (Quality) — наскільки важливі закони
- "Ризики" (RiskPenalty) — чи не шкодить демократії
- "Комітет" — роль у комітеті
- "Звернення" — допомога виборцям

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 7.1 | Redesign deputy profile: simplified metrics with tooltips | OPEN | — |
| 7.2 | Add "What does this mean?" explanations to each metric | OPEN | — |
| 7.3 | Visual: progress bars instead of raw numbers | OPEN | — |

---

### Dependency Graph

```
Group 1 (Data) ─────────────────────┐
                                     ├→ Group 2 (EU Score) ─→ Group 3 (Dashboard)
Group 4 (News/Fakes) ───────────────┤                    ─→ Group 5 (Telegram)
                                     │
Group 6 (Digest/Social) ←───────────┘
                                     │
Group 7 (KPI UX) ←──独立 (no deps) ──┘
```

### Recommended execution order
1. **Group 1** — foundation, no external deps
2. **Group 2** — EU score, depends on Group 1 data
3. **Group 3** — Dashboard, depends on Groups 1+2
4. **Group 4** — News monitoring, independent but feeds Group 5
5. **Group 5** — Telegram, depends on Groups 2+4
6. **Group 6** — Social media, depends on Group 5 digest format
7. **Group 7** — KPI UX, can run in parallel with anything

---

## EXECUTOR PLAN — T4: Deputy Requests + Committee Roles

### T4.1: Sync Deputy Requests

**What:** Run `sync_deputy_requests.py` to populate `deputy_requests` data.
**Current state:** 0 records in deputy_requests. `mps.request_count` and `mps.requests_with_response` are 0 for all deputies.
**Script:** `sync_deputy_requests.py` — scrapes RADA ITD API, filters only requests with responses (anti-spam).
**Output:** Updates `mps.request_count` and `mps.requests_with_response`.
**Time estimate:** ~2-3 minutes (466 deputies × 0.2s delay).
**Dependencies:** None. Network access to `itd.rada.gov.ua`.

**Steps:**
1. Run: `./venv/bin/python sync_deputy_requests.py`
2. Verify: `SELECT request_count, requests_with_response FROM mps WHERE requests_with_response > 0 LIMIT 5;`
3. Verify KPI change: `SELECT name, kpi_score FROM mps ORDER BY kpi_score DESC LIMIT 10;`

### T4.2: Verify Committee Roles

**What:** Fix data inconsistencies in committee_members → mps.committee_score sync.

**Problems found (verified 2026-07-01):**
1. **6 duplicate rada_uid in mps** — name changes not merged:
   - 19778: Красносільська А.О. / Радіна А.О. (chair score=10 on duplicate)
   - 19848: Мезенцева-Федоренко М.С. / Мезенцева М.С.
   - 19752: Сірко Ю.Л. / Клименко Ю.Л.
   - 21214: Рябуха Т.В. / Скрипка Т.В.
   - 19585: Аллахвердієва І.В. / Кормишкіна І.В.
   - 21819: Короленко В.Ю. / Короленко-Усова В.Ю.
2. **Score discrepancy:** vice_chair=54 in committee_members, but score=7 has 55 entries in mps (duplicate adds +1)
3. **subcommittee_head+secretary=165, score=5=164** — one less (likely same duplicate issue)

**Score mapping (confirmed):**
| Role | Score | Count |
|------|-------|-------|
| chair | 10 | 22 |
| vice_chair | 7 | 54 |
| subcommittee_head | 5 | 146 |
| secretary | 5 | 19 |
| member | 3 | 144 |

**Steps:**
1. Check `deputy_aliases` table for the 6 duplicates
2. For each duplicate: keep the CURRENT name (with end_date if former), delete the old entry
3. Re-run `sync_committee_members.py` to refresh roles
4. Re-run `calc_bill_quality.py` to recalculate committee_score from committee_members
5. Verify: no duplicates, counts match

**Files to change:**
- None (data fix, not code fix)
- If sync_committee_members.py needs fix — update role parsing

---

## Open Issues for Future Analysis

### Suspiciously low deputy requests count

**Observation:** Only 303 of 460 deputies have requests_with_response > 0. Total: 1,740 responses across 6 years of Rada work (IX convocation started Aug 2019).

**Why suspicious:**
- 303/460 = 66% — means 34% of deputies have ZERO responses to their requests
- 1,740 / 303 / 6 years = ~0.96 responses per deputy per year — extremely low
- Average active MP should submit 20-50 requests/year, with 50-80% response rate

**Possible explanations:**
1. API only returns recent requests (not full 6-year history)
2. Some deputies don't submit requests (institutional roles, faction leaders)
3. RADA ITD API has pagination limits (script uses Take=500)
4. Name matching issues (some deputies not found by last name)

**Fixed (2026-07-01):**
- Name matching: `get_mprequests_id()` now matches by first+patronymic initials when multiple deputies share last name
- Ткаченко О.М. corrected: 1 → 113 requests (was matched to wrong Ткаченко)
- ~6 edge cases with identical first+patronymic initials — accepted limitation

**Remaining concerns:**
- Pagination: `Take=500` — deputies with 500+ requests may have truncated data
- Compare top requesters (Яценко: 218, Геращенко: 130, Фріз: 118) with RADA ITD website manually
