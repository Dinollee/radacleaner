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

**Current state (verified 2026-08-19):**
- Authors: 15,181/15,188 (99.95%) ✅ DONE
- Analyzed: 5,931/15,188 (39%) — 9,257 bills need analysis (night batch auto-fills)
- Deputies: 460 (6 duplicates removed)
- KPI: 460/460 ✅ (v12 active)
- Quality: 456/460 ✅
- Deputy requests: 303 deputies with responses ✅
- Committee: 385 members, scores synced (22 chairs, 54 vice, 164 sub+sec, 143 members)

| # | Task | Status | Blocks |
|---|------|--------|--------|
| 1.1 | ~~Extract authors from JSON~~ | ✅ DONE | — |
| 1.2 | Night batch: auto-fills 9,257 bills overnight (no action needed) | AUTO | Quality/RiskPenalty accuracy |
| 1.3 | ~~Sync deputy requests~~ | ✅ DONE | — |
| 1.4 | ~~Virtual KPI formula testing~~ | ✅ DONE | — |
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
| 3.1 | Design unified dashboard layout (figma/sketch or HTML mockup) | ✅ DONE (pentagon + profile + signals in deputy detail) | — |
| 3.2 | API: `/api/dashboard/unified` — returns all 4 blocks in one call | OPEN | Frontend |
| 3.3 | Frontend: replace tabs with unified layout | PARTIAL — deputies tab uses KPI v11, but tabs still separate | — |

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

### GROUP 5: Telegram Bot + Notifications
**Goal:** Interactive bot with menu + automated alerts.

**Current state (verified 2026-08-19):**
- Token: в .env (Telegram Bot API), chat_id=349941927
- `send_message()` works ✅
- `format_risk_message()` / `format_status_message()` — auto-triggered ✅
- Bot commands registered: `/start`, `/bill`, `/dep`, `/top`, `/eu` ✅

**Implementation order (step by step):**

| # | Task | Status | What it does |
|---|------|--------|-------------|
| 5.1 | ~~Bill info by number~~ | ✅ DONE | `/bill 14332` → title, status, risk, authors |
| 5.2 | ~~Bot menu + /start~~ | ✅ DONE | Inline keyboard with 4 buttons |
| 5.3 | High-risk alert | OPEN | toxicity > 0.7 → instant message (auto, no user action) |
| 5.4 | ~~Daily digest~~ | ✅ DONE (deterministic, no LLM) | Top 5 risky + changes + plenary status |
| 5.5 | ~~Deputy profile~~ | ✅ DONE | `/dep <name>` → KPI v11 pentagon + profile + signals |
| 5.6 | Weekly digest | OPEN | Trends, EU progress (cron at Monday 08:00) |

**Deputy profile format (Telegram):**
```
🏛️ Юрчишин П.В. — KPI 72.4

📊 Дисципліна       ████████░░ 82
🏛️ Авторство        ██████░░░░ 63
⚡ Результативність  █████░░░░░ 48
🤝 Взаємодія        ████████░░ 79
🎯 Профільність     █████████░ 91

⚠️ Ризики: 🟡 середні (toxicity=0.22)
```

**Task 5.1 — Bill Info (first to implement):**
- User sends `/bill 14332` or presses "Пошук закону" button
- Bot asks "Введіть номер закону"
- User sends number
- Bot queries: `bills` (title, status, toxicity), `risk_assessments` (LLM analysis, risks), `bill_sponsors` (author)
- Response format:
```
📜 #14332 — Податковий кодекс (звільнення ДП від боргу)
📊 Статус: Закон підписано | Стадія: 5/5
⚠️ Ризик: medium (toxicity=0.22)
📝 Аналіз: Закон звільняє 3 держпідприємства від стягнення податкового боргу...
👤 Автор: Волинець М.Я. та ін.
🔗 https://itd.rada.gov.ua/billinfo/Bills/Card/...
```

**Dependencies:** Group 2 (EU score) — ✅ done.

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

## EXECUTOR PLAN — T4: Deputy Requests + Committee Roles — ✅ DONE (2026-08-19)

### T4.1: Sync Deputy Requests — DONE
Ran `sync_deputy_requests.py`, populated `deputy_requests` data. 303 deputies with responses.

### T4.2: Verify Committee Roles — DONE
Fixed 6 duplicate rada_uid in mps, re-synced committee_members → mps.committee_score.
Score mapping confirmed: chair=10 (22), vice_chair=7 (54), subcommittee_head=5 (146), secretary=5 (19), member=3 (144).

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

---

## KPI Redesign: Pentagon + RiskPenalty (v11)

### Problem with v10.1
Зважена сума з 8 метрик = одне число. Користувач бачить "KPI = 67" але не розуміє ЧОМУ. Формула з мультиплікаторами (att_mult, committee halving) — незрозуміла для звичайного виборця.

### Critique of hexagon approach (from agent analysis)

**1. Дублювання осей "Законотворчість" + "Вплив":**
LEI та Conv — виробні від total_bills і adopted. Депутат з багатьма законами отримує 100/100 по ОБОМ осям → симетричне роздуття форми. ❌ Неприйнятно.

**2. Ось "Євроінтеграція" = дискримінація:**
is_euro є тільки у 274/460 депутатів. ~200 депутатів (оборона, аграрка) отримують 0 по цій осі просто тому їх комітет не перетинається з ЄС. ❌ Неприйнятно як окрема ось.

**3. Ось "Ризики" ломає візуалізацію:**
На radar chart "чим більша площа — тим краще". Високий ризик = пік = виглядає як "ефективність". ❌ Неприйнятно.

### Solution: Pentagon (5 clean axes) + RiskPenalty as multiplier

```
                    ▲
                    │
    5. ПРОФІЛЬНІСТЬ ◄──────────► 2. АВТОРСТВО
    (Спеціалізація) │            (LEI + Quality + Docs)
                    │
                    ▼
      4. ВЗАЄМОДІЯ ◄───┴───► 3. РЕЗУЛЬТАТИВНІСТЬ
  (Requests+Committee)       (Conv + adoption_rate)

  1. ДИСЦІПЛІНА = top axis (py + pda + vkp)

  ⚠️ ШТРАФ: Final = Base × (1 - RiskPenalty)
```

**Axis mapping:**

| Axis | Metrics | Data coverage | What it shows |
|------|---------|---------------|---------------|
| 1. Дисципліна | py, pda, vkp | 460/460 (100%) | Ходить? Голосує? Дисципліна фракції? |
| 2. Авторство | lei, bill_quality_score, documents_count | 357/460 (78%) | Пише закони? Які вони? Наскільки ретельно? |
| 3. Результативність | conv, adoption_rate | 357/460 (78%) | Дожимає закони до підпису? Швидко чи повільно? |
| 4. Взаємодія | requests, committee, co_authors, authorship_ratio | 305/460 (66%) | Працює з колами? Допомагає виборцям? Лобіює? |
| 5. Профільність | agenda_diversity, eu_as_ratio | 357/460 (78%) | Спеціалізується чи розпорошує зусилля? |

**RiskPenalty (виведена з гексагона):**
- `RiskPenalty = 1 - (avg_risk_score / 5)` → від 0.0 (максимальний ризик) до 1.0 (без ризиків)
- Візуально: 🔴🟡🟢 значок під гексагоном
- Формула: `Final KPI = Base × (1 - RiskPenalty)`

**Formula v11:**
```
Base = 0.20×Дисципліна + 0.25×Авторство + 0.20×Результативність + 0.20×Взаємодія + 0.15×Профільність

Final KPI = Base × (1 - RiskPenalty)
```

**Втрати (що прибираємо з v10.1):**
- att_mult (progressive attendance multiplier) — прибираємо, бо Дисципліна вже є окремою осью
- Committee halving for zero-legislators — прибираємо, бо Авторство = 0 і так видно
- RiskPenalty як компонент суми — винесена в множник

**Набуття (що додаємо):**
- `documents_count` — якість підготовки документів (з bill_documents)
- `adoption_rate` — швидкість проходження (з bill_passings)
- `co_authors_count` — мережа впливу (з bill_sponsors)
- `agenda_diversity` — різноманітність сфер діяльності (з agenda_category)

### Telegram text representation

```
🏛️ Юрчишин П.В. — KPI 72.4

📊 Дисципліна       ████████░░ 82
🏛️ Авторство        ██████░░░░ 63
⚡ Результативність  █████░░░░░ 48
🤝 Взаємодія        ████████░░ 79
🎯 Профільність     █████████░ 91

⚠️ Ризики: 🟡 середні (toxicity=0.22)
```

### Dashboard representation
SVG radar chart (5 осей) + progress bars під ним + значок ризику.

### DECISIONS (CONFIRMED 2026-07-01)

1. **Гексагон → Пентагон** — 5 чистих осей + RiskPenalty множник
2. **RiskPenalty винесена з осі** — візуально: 🔴🟡🟢 значок під гексагоном
3. **att_mult прибирається** — замінюється осью "Дисципліна"
4. **Committee halving прибирається** — Авторство = 0 якщо немає законів
5. **ЄС не окрема ось** — входить в "Профільність" як доля
6. **Formula v11**: `Base = 0.20×Дисципліна + 0.25×Авторство + 0.20×Результативність + 0.20×Взаємодія + 0.15×Профільність`, `Final = Base × (1 - RiskPenalty)`

### Implementation plan

**Step 0: ~~Створити конфіг ваг~~** ✅ DONE
**Step 1: ~~Додати нові колонки в mps~~** ✅ DONE
**Step 2: ~~Агрегувати дані~~** ✅ DONE
**Step 3: ~~Перерахувати KPI v11~~** ✅ DONE
**Step 4: ~~Оновити API~~** ✅ DONE
**Step 5: ~~Оновити Telegram бот~~** ✅ DONE

**Step 6: Оновити дашборд** — ✅ DONE (pentagon radar, 5-component sort, profile + signals in deputy detail)

### Plan A: Axes filling (NOT FINAL — discussion ongoing)

**Взаємодія (Interaction):**

| Метрика | Джерело | Нормалізація | Coverage |
|---------|---------|-------------|----------|
| requests_with_response | deputy_requests | 0-100 (від max=28) | 305/391 (78%) |
| committee_score | committee_members | 0-100 (з 0-10) | 383/391 (98%) |
| authorship_ratio | bill_sponsors | 0-100 (з 0-0.77) | 352/391 (90%) |

~~co_authors_count~~ — відхилено: медіана 388 з 391 депутата, всі співпрацюють з усіма. Не диференціює.

Score = (requests_norm × 0.4 + committee_norm × 0.3 + authorship_norm × 0.3)

**Профільність (Specialization):**

| Метрика | Джерело | Формула | Coverage |
|---------|---------|---------|----------|
| agenda_diversity | agenda_category | Shannon entropy: H = -Σ(p_i × log2(p_i)) | 357/391 (91%) |
| eu_ratio | is_euro (bills) | eu_bills / total_bills × 100 | 344/391 (88%) |

**Shannon entropy interpretation:**
- Низька H (1-3) = спеціаліст (одна-дві категорії) — "оборонець", "податківець"
- Середня H (3-5) = збалансований (7-10 категорій)
- Висока H (5-7) = універсал (все по трохи)

**Проблема Plan A:** "краще" на осі "Профільність" — середня спеціалізація (10-11 категорій). Але як нормалізувати? Якщо max H = "краще", то універсал = краще за спеціаліста. Якщо min H = "краще", то спеціаліст = краще за універсала. Потрібен баланс.

**Open question:** Як нормалізувати Профільність? Варіанти:
- A) H = 3-5 = optimal (100), відхилення = -10 за кожен крок
- B) H = min (спеціалізація) = краще (reward focus)
- C) H = max (універсальність) = краще (reward breadth)
- D) Не нормалізувати, показувати як інфографіку (спеціаліст/універсал)

### Plan B: Three-level system (DISCUSSED — 2026-07-01)

**Замість одного KPI — три рівні.**

#### Рівень 1: KPI (як працює)

Відповідає: «Наскільки добре депутат виконує свою роботу?»

| Компонента | Метрики |
|-----------|---------|
| Законодавча ефективність | LEI (primary only) |
| Дисципліна | ПЯ + ПДА + ВКП |
| Результативність | Conv (primary) + adoption_rate (stage=4) |
| Контрольна діяльність | requests_with_response + committee_score |
| Якість авторства | bill_quality_score + documents_count |

Formula: `KPI = Σ(component × weight)`

**Ваги НЕ фіксуються** — зберігаються в конфігу (`kpi_weights.json`) для калібрування.

```json
{
  "kpi_v11": {
    "effectiveness": 0.25,
    "discipline": 0.25,
    "efficiency": 0.20,
    "control": 0.15,
    "quality": 0.15
  }
}
```

#### Рівень 2: Профіль (хто він)

Не впливає на рейтинг. Опис депутата.

| Показник | Джерело | Формат |
|----------|---------|--------|
| Комітет | committee_members | "Оборона", "Бюджет" |
| Спеціалізація | Shannon H | "Вузька" / "Середня" / "Широка" |
| Shannon Diversity | entropy(formula) | числове 0-7 |
| EU ratio | eu_euro_bills / total_bills | "6%" |
| Стиль авторства | authorship_ratio | "Індивідуальний" / "Колективний" |
| Основна тема | top agenda_category | "Безпека" |
| Законів | total_bills | "47" |
| Прийнято | total_laws (stage=4) | "12" |

#### Рівень 3: Аналітичні сигнали

Автоматичні висновки. Не KPI, не профіль — **інсайти**.

**Спочатку rule-based, потім LLM** (сигнали = вхідні дані для LLM-висновків).

**Розділення на 3 категорії:**

**⚠ Попередження (негативні сигнали):**

| Сигнал | Умова |
|--------|-------|
| Законодавчий спам | total_bills > 200 AND total_laws < 10 |
| Не працює в комітеті | committee_score = 0 AND total_bills > 0 |
| Дуже вузька спеціалізація | Shannon H < 2 |
| Аномально багато соавторств | authorship_ratio < 0.05 |
| Висока доля термінових | urgent_ratio > 20% |
| Висока доля технічних | is_procedural ratio > 50% |
| Підписав 95% законів фракції | same-faction ratio > 95% |

**✓ Сильні сторони (позитивні сигнали):**

| Сигнал | Умова |
|--------|-------|
| Висока якість законів | bill_quality_score > 70 |
| Стабільна спеціалізація | Shannon H < 3 AND total_bills > 10 |
| Висока результативність | adoption_rate > 30% |
| Висока дисципліна | ПЯ > 80% AND ПДА > 80% |

**ℹ Цікаві особливості (нейтральні):**

| Сигнал | Умова |
|--------|-------|
| Колективний стиль авторства | authorship_ratio < 0.15 |
| Вузький експерт | Shannon H < 3 |
| Євроінтеграційний профіль | eu_ratio > 15% |
| Багато термінових законів | urgent_ratio > 15% |

### Карточка депутата (фінальний формат)

```
═══════════════════════════════
        Іван Петренко
═══════════════════════════════

KPI
──────────────
Законодавство      ████████░░ 82
Дисципліна         █████████░ 95
Контроль           ███████░░░ 71
Результативність   █████████░ 88
Загальний KPI      █████████░ 84

Профіль
──────────────
Комітет:           Оборона
Спеціалізація:     Вузька (H=2.3)
EU:                6%
Стиль:             Індивідуальний
Основна тема:      Безпека
Законів:           47 (прийнято 12)

⚠ Попередження
──────────────
⚠ Дуже низька активність у запитах
⚠ Майже не працює з іншими авторами

✓ Сильні сторони
──────────────
✓ Висока якість законів
✓ Стабільна спеціалізація

ℹ Особливості
──────────────
ℹ Вузький експерт
ℹ Євроінтеграційний профіль
```

---

## Dashboard Implementation (DONE 2026-08-19)

### What was implemented

**Deputies list** (`/deputies` tab):
- Table columns: Name, Faction, **ІЕД** (large number), Дисципліна, Законотворчість, Результативність, Комітет, Звернення, Вплив
- Each component shows as a colored progress bar (green ≥70, orange ≥40, red <40)
- **ІЕД v12 sort**: by ІЕД score, or by individual component (6 components)
- Former deputies dimmed (opacity 0.55) with "Вибулий" badge
- Pagination, search, faction filter, status filter (active/former)

**Deputy detail** (click row → detail page):
- Header: name, faction, **ІЕД score** (large, color-coded), rank
- **Left**: SVG hexagon radar chart (6 axes: Дисципліна, Законотворчість, Результативність, Комітет, Звернення, Вплив)
  - Grid rings at 20/40/60/80/100%
  - Data path with blue fill + value labels at each vertex
- **Right top**: Profile grid — specialization (Shannon H), authorship style, bills/laws, EU score, ПЯ/ПДА/ВКП, LEI
- **Right bottom**: Signal badges — warnings (red), strengths (green), features (blue)
- Voting history with pagination + vote type filters

**API** (`/api/deputies`):
- Returns `ked12`, `kedDisc12`, `kedLegis12`, `kedEff12`, `kedComm12`, `kedReq12`, `kedImpact12`, `kedRank12`
- Returns `signal_warnings`, `signal_strengths`, `signal_features` (JSONB arrays)
- Sortable by any KPI component

### Files touched
- `dashboard/index.html` — deputies table, detail page, hexagon radar chart
- `worker/api-server.js` — `/api/deputies` returns v12 fields (ked12, kedDisc12, etc.)

### Open items (next session)
1. **Group 3**: Unified dashboard (4 blocks: Top deputies, Top bills, EU score, Fakes) — tabs still separate
2. **Group 3.2**: `/api/dashboard/unified` endpoint — single call for all 4 blocks
3. **Group 5.3**: Telegram high-risk alert (toxicity > 0.7 → instant message)
4. **Group 5.4**: Daily digest (cron at 08:00)
5. **Group 4**: News monitoring (RSS + fake detection)

---

## KPI v12 — Альтернативна точка зору (Plan C)

> **Статус:** Розробка. KPI v11 залишається основою.
> **Мета:** Валідація підходу v11 через незалежну формулу. Якщо v12 дає схожі рейтинги — підхід robust. Якщо сильно відрізняється — шукаємо чому.

### Філософія v12

**Проблема v11:** 5 компонентів з різними вагами (0.25, 0.25, 0.20, 0.15, 0.15). Кожна калібрування ваг = endless debate. Shannon entropy в профілі, але тягне за собою нормалізацію. Сигнали = rule-based, але суб'єктивні.

**Рішення v12:** 6 категорій діяльності з рівною вагою (1/6 кожна). Кожна категорія = одне питання про роботу депутата. Формула проста: KPI = (C1 + C2 + C3 + C4 + C5 + C6) / 6.

**Правила:**
- Категорії ортогональні (не перетинаються)
- Кожна Ci ∈ [0, 1]
- Якщо немає даних для категорії → 0.5 (нейтрально, не 0)
- KPI v12 НЕ замінює v11, а перевіряє його

### 6 категорій — детальний опис

---

#### C1: ДИСЦІПЛІНА (чи ходить на роботу?)

**Питання:** Чи присутній депутат на пленарних засіданнях і чи голосує?

**Метрики (наявні в базі):**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| py | mp_votes | % (0-100) | 460/460 | Послух якості — % голосувань де був присутній |
| pda | mp_votes | % (0-100) | 460/460 | Послух діяльності — % голосувань де проголосував (не утримався) |
| vkp | mp_votes | % (0-100) | 460/460 | Відповідність корпусу — % голосувань де голосував як фракція |
| total_votes | mp_votes | int | 460/460 | Загальна кількість голосувань за каденцію |
| attended_votes | mp_votes | int | 460/460 | Скільки разів фактично голосував |

**Агрегація:**
```
C1 = (py_norm × 0.5 + pda_norm × 0.3 + vkp_norm × 0.2)
```

Де py_norm, pda_norm, vkp_norm ∈ [0, 1] — це просто значення / 100.

**Чому такі ваги:**
- py (0.5) — головна: депутат має бути присутній
- pda (0.3) — важливо: не просто сидіти, а голосувати
- vkp (0.2) — додатково: голосувати разом з фракцією (дисципліна фракції, не особиста)

**Edge cases:**
- ПЯ < 10% → C1 = 0 (не працює взагалі)
- Депутат покинув фракцію → vkp може бути низьким через іншу фракцію → це OK

**Що НЕ включаємо:**
- data_sufficient — це прапорець, не метрика
- attended_votes — вже в py

---

#### C2: ЗАКОНОТВОРЧІСТЬ (чи пише закони?)

**Питання:** Чи ініціює депутат законопроєкти і наскільки вони якісні?

**Метрики:**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| total_bills | mps | int | 460/460 | Всього підписів (автор + співавтор) |
| total_laws | mps | int | 460/460 | З них stage=4 (прийнято) |
| bill_quality_score | mps | float | 357/460 | AVG(significance + impact) / 2 з risk_assessments |
| avg_risk_score | mps | float | 357/460 | Середній risk_score аналізованих законів |
| documents_count | mps | int | 357/460 | Скільки документів до законопроєктів (підготовка) |
| bills_analyzed_count | mps | int | 357/460 | Скільки законів проаналізовано LLM |
| authorship_ratio | mps | float | 352/460 | Частка власних ініціатив (order=0 / total) |

**Агрегація:**
```
C2 = quality_norm × 0.4 + risk_penalty × 0.3 + docs_norm × 0.3
```

Де:
- quality_norm = bill_quality_score / 5 (нормалізація до 0-1, бо significance і impact ∈ [0, 5])
- risk_penalty = 1 - (avg_risk_score / 5) (чим вищий ризик — тим нижча оцінка)
- docs_norm = min(documents_count / 200, 1) (нормалізація: 200+ документів = максимум)

**Якщо немає даних:** quality_norm = 0.5, risk_penalty = 0.5, docs_norm = 0.5 → C2 = 0.5

**Що НЕ включаємо:**
- total_bills — це обсяг, а не якість. Високий total_bills без якості = спам
- authorship_ratio — це профіль (чи пише сам чи з кимось), не якість
- adoption_rate — це C3 (результативність)
- LEI — це похідна від adoption, теж C3

**Критичне зауваження:** bill_quality_score = AVG((significance + impact) / 2) залежить від LLM аналізу. Наразі проаналізовано лише ~39% законів. Для 61% депутатів quality = 0.5 (нейтрально). Це acceptable для v12 як тесту.

---

#### C3: РЕЗУЛЬТАТИВНІСТЬ (чи доходять закони до кінця?)

**Питання:** Чи здатен депутат провести закон через усі стадії до підпису?

**Метрики:**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| adoption_rate | mps | % | 357/460 | % законопроєктів stage=4 від загальної кількості |
| total_bills | mps | int | 460/460 | Знаменник adoption_rate |
| total_laws | mps | int | 460/460 | Чисельник adoption_rate |

**Агрегація:**
```
C3 = adoption_rate / 100
```

Просто: яка частка законопроєктів стала законами.

**Додатковий множник (обсяг):**
```
volume_factor = min(total_primary / 10, 1)  (де total_primary = sponsor_order=0 bills)
C3_final = C3 × volume_factor
```

Це гарантує що депутат з 1 законом і 100% конверсією не отримує C3=1. Мінімум 10 ініційованих законів для повного балу.

**Якщо total_primary < 3:** C3 = 0.5 (замало даних для оцінки)

**Що НЕ включаємо:**
- LEI — це log-функція, занадто складна для v12
- total_laws alone — це обсяг, не конверсія

---

#### C4: КОМІТЕТСЬКА РОБОТА (чи працює в комітеті?)

**Питання:** Чи бере депутат участь у роботі комітету і яку роль відіграє?

**Метрики:**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| committee_score | mps | int (0-10) | 383/460 | Роль: chair=10, vice=7, subcommittee=5, member=3, none=0 |
| committee_role | committee_members | text | 383/460 | Текстова роль |

**Агрегація:**
```
C4 = committee_score / 10
```

Проста нормалізація: 10 = максимум (голова комітету), 0 = немає комітету.

**Якщо committee_score = 0:** C4 = 0.5 (депутат може не мати комітету з інших причин)

**Проблема:** committee_score = static. Він не змінюється протягом каденції. Голова комітету завжди 10, навіть якщо комітет не працює.

**Для v12 приймаємо:** committee_score як є. Це "роль", не "активність". Активність комітету вимірюється інакше (але цих даних у нас немає).

**Що НЕ включаємо:**
- Кількість засідань комітету — немає даних
- Авторські закони комітету — це C2

---

#### C5: ЗВЕРНЕННЯ (чи допомагає виборцям?)

**Питання:** Чи подає депутат запити і чи отримує відповіді?

**Метрики:**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| requests_with_response | mps | int | 303/460 | Кількість запитів з відповіддю |
| request_count | mps | int | 303/460 | Загальна кількість запитів |

**Агрегація:**
```
C5 = min(requests_with_response / 20, 1)
```

Нормалізація: 20 запитів з відповіддю = максимум (дані: max=28).

**Додатковий множник (якість відповідей):**
```
response_rate = request_count > 0 ? requests_with_response / request_count : 0
C5_final = C5 × (0.7 + 0.3 × response_rate)
```

Це заохочує не просто подавати запити, а отримувати відповіді.

**Якщо requests_with_response = 0:** C5 = 0 (депутат не подає запитів)

**Проблема:** API повертає дані лише для 303/460 депутатів. 157 депутатів мають C5 = 0 через відсутність даних, а не через відсутність діяльності.

**Для v12:** приймаємо як є. Немає даних = 0. Але позначаємо це як known limitation.

**Що НЕ включаємо:**
- total_bills — це C2
- committee_score — це C4

---

#### C6: СУСПІЛЬНИЙ ВПЛИВ (чи не шкодить системі?)

**Питання:** Чи закони депутата несуть ризики для держави, і чи відповідають вони стандартам?

**Метрики:**

| Метрика | Джерело | Одиниця | Coverage | Опис |
|---------|---------|---------|----------|------|
| avg_risk_score | mps | float (0-5) | 357/460 | Середній risk_score проаналізованих законів |
| eu_integration_score | mps | float | 344/460 | EU alignment коефіцієнт |
| bill_quality_score | mps | float | 357/460 | Якість (significance + impact) / 2 |

**Агрегація:**
```
risk_component = 1 - (avg_risk_score / 5)    (низький ризик = високий бал)
eu_component = min(eu_integration_score / 10, 1)  (високий EU = високий бал)
quality_component = bill_quality_score / 5    (висока якість = високий бал)

C6 = risk_component × 0.5 + eu_component × 0.25 + quality_component × 0.25
```

**Чому саме так:**
- risk (0.5) — головна: закони не повинні шкодити
- eu (0.25) — відповідність європейським стандартам = позитивний вплив
- quality (0.25) — якісні закони = позитивний внесок

**Якщо немає даних:** risk_component = 0.5, eu_component = 0.5, quality_component = 0.5 → C6 = 0.5

**Проблема:** quality з'являється і в C2, і в C6. Це дублювання.

**Рішення для v12:** прибираємо quality з C6. Залишаємо:
```
C6 = risk_component × 0.6 + eu_component × 0.4
```

Тепер C2 = якість законів, C6 = ризики + EU. Ортогональні.

**Що НЕ включаємо:**
- toxicity — це похідна від risk_score × significance × impact, занадто складна
- signal_warnings — rule-based, суб'єктивні

---

### Формула v12 (фінальна)

```
KPI_v12 = (C1 + C2 + C3 + C4 + C5 + C6) / 6

Де:
C1 = Дисципліна:       py×0.5 + pda×0.3 + vkp×0.2
C2 = Законотворчість:  quality_norm×0.4 + risk_penalty×0.3 + docs_norm×0.3
C3 = Результативність: adoption_rate × volume_factor
C4 = Комітет:          committee_score / 10
C5 = Звернення:        min(requests_with_response/20, 1) × response_multiplier
C6 = Вплив:            risk_component×0.6 + eu_component×0.4

Правила:
- Кожна Ci ∈ [0, 1]
- Ci = 0.5 якщо немає даних
- C1 = 0 якщо ПЯ < 10%
- C3 = 0.5 якщо total_primary < 3
- C5 = 0 якщо requests_with_response = 0 (але request_count > 0)
```

### Порівняння v11 vs v12 (очікування)

| Аспект | v11 | v12 |
|--------|-----|-----|
| Компонентів | 5 | 6 |
| Ваги | різні (0.15-0.25) | рівні (1/6) |
| Складність | log-функція LEI, att_mult | лінійна нормалізація |
| Shannon | в профілі | прибрано з KPI |
| RiskPenalty | множник | окрема категорія C6 |
| data coverage | 357/460 (78%) | 303/460 (66%) через C5 |

### Задачі для виконавця

| # | Задача | Залежить від | Опис |
|---|--------|-------------|------|
| T1 | Назви категорій | — | Проаналізувати покриття даних, придумати назви, винести на затвердження |
| T2 | Детальний розрахунок кожної категорії | T1 | Для кожної з 6: формула, edge cases, приклади на реальних депутатів |
| T3 | calc_kpi_v12.py | T2 | Новий скрипт, 6 компонентів, порівняння з v11 |
| T4 | Пост-перевірка | T3 | Топ-20, низ-20, кореляція v11 vs v12, логічність |
| T5 | Оновлення RESEARCH.md | T4 | Фінальні висновки: чи підтверджує v12 підхід v11 |

### Known limitations v12

1. **C5 (Звернення)**: дані тільки для 305/391 депутатів — 86 отримують 0
2. **C2 (Законотворчість)**: bill_quality_score залежить від LLM аналізу (~99% покриття для active deputies)
3. **C4 (Комітет)**: статична метрика, не відображає активність
4. **C6 (Вплив)**: eu_integration_score для 367/391 — 24 депутати без EU даних

---

## ІЕД v12 — IMPLEMENTED (2026-07-07, dashboard 2026-07-07)

### Status: PRODUCTION (replaces v11 on dashboard)

**Script:** `calc_kpi_v12.py` — 391 deputies calculated.
**DB columns:** `mps.kpi_v12_score`, `kpi_v12_rank`, `kpi_v12_discipline`, `kpi_v12_legislation`, `kpi_v12_efficiency`, `kpi_v12_committee`, `kpi_v12_requests`, `kpi_v12_impact`.
**Dashboard:** "KPI" renamed to "ІЕД" (Індекс ефективної діяльності) for legal safety. Hexagon radar (6 axes), clickable column headers, dropdown filter by C1-C6.

### Final Formulas

```
KPI_v12 = (C1 + C2 + C3 + C4 + C5 + C6) / 6

C1 (Дисципліна)  = py×0.5 + pda×0.3 + vkp×0.2,  C1=0 if py<10
C2 (Законотв.)   = quality/5×0.3 + (1-risk/5)×0.3 + docs/2000×0.2 + authorship/0.5×0.2
C3 (Результат.)  = adoption/100×0.7 + min(primary/10)×0.3
C4 (Комітет)     = score/10
C5 (Звернення)   = min(req_resp/20) × (0.7 + 0.3 × response_rate)
C6 (Вплив)       = (1-risk/5)×0.6 + eu/35×0.4

Defaults: C2=0.5 if no LLM data, C3=0.5 if primary<3, C4=0.5 if no committee, C6=0.5 if no data
```

### Results

**391 active deputies.** Distribution: 126 in 20-40, 258 in 40-60, 7 in 60-80.

**Correlation v11 vs v12: 0.634** — moderate. Formulas are related but measure different things.

### Component Correlation (v12 vs v11)

| v12 Category | v11 Component | Correlation | Interpretation |
|---|---|---|---|
| C1 Discipline | Discipline | 0.858 | Same metrics (py/pda/vkp) |
| C5 Requests | Control | 0.723 | Both weight requests heavily |
| C2 Legislation | Quality | 0.055 | **Near-zero** — v12 adds risk+docs+authorship |
| C3 Efficiency | Efficiency | 0.370 | Moderate — v12 adds volume factor |
| C2 Legislation | LEI | 0.446 | Moderate — no log formula in v12 |

### Key Findings

1. **C2 (Legislation) is genuinely independent** from v11's quality metric (0.055 correlation). This validates that v12 measures something different: not just "how good are the bills" but also "how risky, how documented, how authorial."

2. **v12 rewards request activity more equally.** In v11, requests were bundled into "Control" with committee score. In v12, C5 is a standalone category — deputies with high requests (Разумков: 96, Гончаренко: 93) jump significantly.

3. **v12 penalizes low-attendance deputies harder.** Дубінський, Столар, Івахів, Палиця (py < 10%) get C1=0, pulling their total down to 22-33. In v11, attendance was one component among many.

4. **v12 is more egalitarian for deputies without legislation data.** 4 deputies with 0 analyzed bills get neutral defaults (C2=C3=C6=50), preventing both punishment and reward.

### Biggest Movers

**Positive (+15-21):** Скрипка Т.В. (+21.8), Разумков Д.О. (+15.8), Гузь І.В. (+14.6), Кривошеєв І.С. (+14.2) — all had high requests that v11 underweighted.

**Negative (-14-17):** Гетманцев Д.О. (-17.0), Вагнєр В.О. (-14.9), Лічман Г.В. (-14.8) — high discipline but low requests/impact penalized by equal weighting.

### Known Issues (resolved)

1. ~~Скрипка Т.В.: adoption_rate=0~~ — FIXED: sync_mp_stats.py now writes adoption_rate, bulk updated.
2. ~~rada_uid mismatch~~ — FIXED: 47 deputies had -mps.id instead of real RADA UID. Corrected from bill_sponsors. +3,328 bill_sponsors links recovered.
3. **4 deputies** with 0 analyzed bills get neutral C2/C3/C6=50. Acceptable until LLM coverage improves.
4. **C4 (Committee)** is static — doesn't reflect actual committee activity, only role.
5. **6 ministers** (Криклій, Коваленко, Малюська, Новосад, Оржель, Федоров) have NULL rada_uid — no bill_sponsors data.

### Data Integrity Fixes (2026-07-07)

- `sync_mp_stats.py`: added `adoption_rate` write (was missing, caused adoption_rate=0 for Скрипка)
- `bill_sponsors.mp_id`: linked 363 records by name, 3,328 records via corrected rada_uid
- `mps.rada_uid`: fixed 47 negative values (were -mps.id, now correct RADA API person.id)
- `mps.total_bills/total_laws`: recalculated from bill_sponsors for 389 deputies
- `mps.adoption_rate`: bulk recalculated for all 391 deputies

### Verdict

**ІЕД v12 is the production formula.** v11 kept in DB for historical comparison. v12 provides independent validation of legislative effectiveness with 6 orthogonal categories. Dashboard fully migrated to ІЕД.

---

## Session 2026-07-08: Dashboard + Telegram + Infrastructure

### Dashboard: Activity Calendar

**New feature**: calendar cells show daily bill activity (+N new, ~N status changes).

- **API**: `/api/activity-calendar?month=YYYY-MM` — daily counts from change_log
- **API**: `/api/activity-day?date=YYYY-MM-DD` — detailed bill list for a day
- **Timezone**: UTC→Europe/Kyiv conversion (created_at is text in UTC)
- **UX**: clickable badges → modal with bill list, bill numbers linked to RADA

### Telegram Bot: Format Updates

**NEW posts** (monitor.py):
- 🆕 icon first (before bill number)
- No progress bar (removed ●○○○)
- Date normalized to dd.mm.yyyy
- Author name shown when available (from bill_sponsors)

**Daily digest** (daily_digest_llm.py):
- **LLM removed** — was returning prompt text ("We need to produce a daily summary...") instead of filled digest
- **Deterministic format** — fixed template, all data from DB + rada.gov.ua scraping
- Format: 📋 date → 📊 СЬОГОДНІ (plenary, committees, new bills) → 📢 УВАГА (top-5 risky, 30 days) → ✅ Перевірено → Підсумок → Дані
- Data sources: `bills`, `risk_assessments`, `change_log`, `rada_schedule`, `rada_committee_schedule` + rada.gov.ua/news scraping
- No Markdown, no LLM call, no ambiguity

### Infrastructure Fixes

**LLM rate limiting** (`src/llm_client.py`):
- `_RateLimiter` class: sliding window, thread-safe
- Gemini: 12 req/min (limit is 15), 1400 req/day (limit is 1500)
- Prevents 429 errors when night_batch uses multiple workers

**PDF retry** (`src/pdf_utils.py`):
- `download_rada_pdf`: retry 3 times with exponential backoff (5s→10s→20s) on 503/429/500
- `get_rada_token`: retry 3 times with backoff
- Fixes night_batch failures during RADA API outages

**Git history**:
- Rewrote all 183 commits with correct email (distih@gmail.com)
- GitHub activity squares now work

### Files Changed This Session

| File | Changes |
|------|---------|
| `calc_kpi_v12.py` | NEW: ІЕД calculation (6 categories) |
| `sync_mp_stats.py` | Added adoption_rate write |
| `src/llm_client.py` | Gemini rate limiter (12/min, 1400/day) |
| `src/pdf_utils.py` | PDF download retry with backoff |
| `monitor.py` | NEW post format: icon first, no progress bar, dd.mm.yyyy, author name |
| `daily_digest_llm.py` | **LLM removed**, deterministic format, plenary+committee from DB, new bills from rada.gov.ua |
| `dashboard/index.html` | ІЕД, hexagon radar, clickable sort, activity calendar, modal |
| `worker/api-server.js` | v12 fields, activity-calendar, activity-day endpoints, UTC→Kyiv |
| `ARCHITECTURE.md` | Updated with all session changes |
| `RESEARCH.md` | Updated with v12 production status + session notes |

---

## Next Steps — Prioritized Backlog

### High Priority (next session)

| # | Task | Value | Effort |
|---|------|-------|--------|
| 1 | **Group 5.3: High-risk alert** — toxicity > 0.7 → instant Telegram message | Автоматичні алерти, найвищий ROI | 1-2h |
| 2 | **Group 2: EU Score page audit** — перевірити покриття, оновити дашборд | Аудит наявного | 30min |
| 3 | **Group 3.2: Unified API** — один `/api/dashboard` замість 5+ запитів | Швидший дашборд | 1h |

### Medium Priority

| # | Task | Value | Effort |
|---|------|-------|--------|
| 4 | **Group 5.6: Weekly digest** — тренди за тиждень (пн 08:00) | Регулярний огляд | 1-2h |
| 5 | **Group 4: News monitoring** — RSS + LLM класифікація | Моніторинг новин | 3-4h |
| 6 | **Committee meetings sync** — ручне введення або нове джерело даних | Повний календар | 2h |

### Low Priority

| # | Task | Value | Effort |
|---|------|-------|--------|
| 7 | **Group 6: Social media** — Twitter/Facebook API | Дайджест в соцмережах | 4-6h |
| 8 | **Group 2.2: EU pro/anti classification** | Глибший EU аналіз | 2h |
| 9 | **Committee meetings scraper** — RADA ITD API | Автоматичні дані | 3-4h |

---

## EU Score — Current State (2026-07-08)

### Загальний стан: 95% DONE

**Alignment Score**: 26.5% (нормально для країни-кандидата)
**Покриття**: 30/35 глав EU acquis аналізовано

### Що є

| Компонент | Джерело | Покриття | Стан |
|-----------|---------|----------|------|
| Overall Alignment | eu_alignment_overall | 1 запис | ✅ |
| Per-chapter alignment | eu_alignment_chapters | 35 глав, 931 запис | ✅ |
| EU bills (is_euro) | bills.is_euro | 274 bills | ✅ |
| Per-deputy EU score | mps.eu_integration_score | 367/391 (94%) | ✅ |
| Per-deputy euro bills | mps.eu_euro_bills | 330/391 (84%) | ✅ |
| Per-deputy risk bills | mps.eu_risk_bills | 323/391 (82%) | ✅ |
| EU risk assessments | risk_assessments | 59 bills | ✅ |
| Dashboard: overall score | /api/eu-alignment | ✅ |
| Dashboard: chapter clusters | /api/eu-alignment | ✅ |
| Dashboard: classified bills | /api/eu-alignment/bills | ✅ |
| Dashboard: trend chart | /api/eu-alignment/trend | ✅ |
| Per-deputy EU in table | /api/deputies (euScore) | ✅ |

### Що НЕ зроблено

| # | Task | Опис | Пріоритет |
|---|------|------|-----------|
| 2.2 | **EU pro/anti classification** | Класифікувати EU bills як pro-reform (гармонізація) або anti-reform (обмеження) | Низький |
| — | EU Score в карточці депутата | Показувати EU ratio в профілі | Середній |
| — | EU filter на сторінці депутатів | Фільтр по EU score | Низький |

### Джерела даних

1. **bills.is_euro** — булевий прапорець з RADA JSON (тег "Євроінтеграційний")
2. **risk_assessments.json_data** — LLM знаходить "державна допомога ЄС", "гармонізація" в категоріях ризиків
3. **eu_alignment_chapters** — keyword matching по 35 главах acquis
4. **mps.eu_integration_score** — агрегація по депутатам з bill_sponsors + risk_assessments

### Формула EU Score депутата

```
eu_integration_score = COUNT(bills where risk_categories mention EU) / total_bills_by_deputy
```

Джерело: `calc_bill_quality.py` → `mps.eu_integration_score`

---

## EU Negotiation Clusters — Data Sources Research (2026-07-08)

### Статус кластерів (з Wikipedia, verified)

| # | Кластер | Статус | Дата |
|---|---------|--------|------|
| 1 | Fundamentals (Основи) | 🟢 Відкрито | 15.06.2026 |
| 2 | Internal Market | ⚪ Не відкрито | — |
| 3 | Competitiveness | ⚪ Не відкрито | — |
| 4 | Green Agenda | ⚪ Не відкрито | — |
| 5 | Security & Defence | ⚪ Не відкрито | — |
| 6 | General Provisions | ⚪ Не відкрито | — |

### Джерела даних для автоматизації

**Єдиного API для статусу кластерів НЕМАЄ.** Потрібна комбінація джерел.

| Джерело | URL | Тип | Автоматизація | Частота |
|---------|-----|-----|---------------|---------|
| **EC RSS** | enlargement.ec.europa.eu/node/2/rss_en?f[0]=country_country:UKR | RSS | ✅ | Щодня |
| **Європравда** | eurointegration.com.ua/news/ | Парсинг | ✅ | Щодня |
| **eu-ua.kmu.gov.ua** | eu-ua.kmu.gov.ua/news/ | CMS | ✅ | Щотижня |
| **pulse.kmu.gov.ua** | pulse.kmu.gov.ua | API? | ⚠️ Дослідити | Щотижня |
| **State of Play PDF** | enlargement.ec.europa.eu/document/download/ | PDF | ⚠️ Парсинг | Щомісяця |
| **Ukrainska Pravda RSS** | pravda.com.ua/rss/view_news/ | RSS | ✅ | Щогодини |
| MFA Ukraine | mfa.gov.ua | — | ❌ 403 | — |
| EU Council RSS | consilium.europa.eu | RSS | ❌ 403 | — |

### Рекомендована архітектура трекера

```
EC RSS (щодня) → keyword "cluster" → change_log
     +
Європравда (щодня) → скрапінг → change_log
     +
eu-ua.kmu.gov.ua (щотижня) → скрапінг → change_log
     =
Автоматичне оновлення статусу кластерів на дашборді
```

### Third-party trackers

**Відкритих трекерів НЕМАЄ.** CEPS, EBRD, Bertelsmann — тільки PDF-звіти. GitHub — 0 репозиторіїв.

### Ключове джерело: pulse.kmu.gov.ua

Моніторинг 24 напрямків асоціації. Потрібен додатковий досліджувальний запит для визначення наявності API.

### Повний звіт

Див. `research/eu-clusters-sources/REPORT.md` та `findings/F2-F5.md`

---

## EU Harmonization — Reverse Engineering (2026-07-08)

### Метод

**Reverse engineering:** беремо публічні EU директиви, порівнюємо з нашими законами.

1. Визначаємо ключові директиви для кожної глави (EUR-Lex)
2. Маппимо теми директив на наші закони (keyword matching по title)
3. Рахуємо harmonization = signed_bills / total_bills × 100

### Результати по кластерах

| # | Кластер | Гармонізація | Закони | Прийняті |
|---|---------|-------------|--------|----------|
| 1 | Fundamentals | **26.0%** | 689 | 179 |
| 2 | Internal Market | **28.4%** | 571 | 162 |
| 3 | Competitiveness | **22.1%** | 1,216 | 269 |
| 4 | Green Agenda | **29.0%** | 731 | 212 |
| 5 | Security | **31.5%** | 482 | 152 |
| 6 | General | **21.6%** | 648 | 140 |

**Загальний harmonization: 26.5%** (4,792 прийнятих з 15,217 EU-релевантних)

### Деталі по Chapter 23 (Proof of Concept)

| Вимога ЄС | Наші закони | Прийняті | Покриття |
|-----------|-------------|----------|----------|
| Антикорупція | 350 | 62 | 🟢 ВИСОКЕ |
| Незалежність суду | 319 | 65 | 🟢 ВИСОКЕ |
| Захист від насильства | 36 | 8 | 🟠 СЕРЕДНЄ |
| GDPR/персональні дані | 8 | 2 | 🔴 НИЗЬКЕ |
| Право на адвоката | 41 | 4 | 🟠 СЕРЕДНЄ |

### Порівняння: Alignment vs Harmonization

| Метрика | Що вимірює | Значення | Сенс |
|---------|-----------|----------|------|
| Alignment (legacy) | Keyword match | 26.5% | "Ми ГОВОРИМО про EU" |
| **Harmonization** | Signed bills | **26.5%** | "Ми ПРИЙНЯЛИ EU-закони" |

**Висновок:** Harmonization — більш реалістичний показник. Показує реальний законодавчий вихід, а не просто keyword matching.

### Наступні кроки

1. Розширити Chapter 23 analysis на всі 35 глав (повний harmonization gap)
2. Зв'язати наші закони з конкретними EU директивами (reverse engineering)
3. Показувати harmonization gap на дашборді (що залишилось зробити)

---

## EU Integration Index v1 (2026-08-21)

Єдиний композитний індекс євроінтеграції: об'єднує переговорний прогрес (кластери) та законодавчий вихід (гармонізація).

### Формула
```
INDEX = round(0.5 × NEGOTIATION + 0.5 × LEGISLATION, 1)
NEGOTIATION = avg(статусів 6 кластерів): not_opened=0, opened=50, provisionally_closed=100
LEGISLATION = overall гармонізація (signed/total×100 по всіх главах)
```

**Стартове значення: 23.9%** — NEGOTIATION=16.7 (2 з 6 кластерів відкриті → 100/6), LEGISLATION=31.1.

### Джерела статусів кластерів
1. **Ручний seed** (migration 023): C1 Fundamentals opened 2026-06-15, C6 External Relations opened 2026-07-14 — посилання на офіційні пресрелізи ЄК
2. **Авто-детекція** (`sync_eu_tracker.py` → `detect_cluster_opening`): консервативні регекси по новинах ЄК RSS — вимагає одночасно контекст «accession negotiations/cluster», дієслово відкриття («open/launch/start») і номер або офіційну назву кластера. Закриття/irrelevant новини не тригерять. UPSERT тільки якщо статус ще 'not_opened'.

### Чому кластеровий рівень, а не поглавний
Офіційно Україна веде переговори по кластерах (групах глав), а не по окремих 35 главах — публічні джерела ЄК фіксують саме відкриття кластерів. Поглавна гранулярність (33 глави скринінгу vs наші 32 розділи keyword-mapping) поки недосяжна з відкритих даних — питання відкладено до появи офіційного поглавного трекінгу.

### Обмеження v1
- Ваги 0.5/0.5 та шкала 0/50/100 — експертне припущення, не офіційна методологія ЄС
- LEGISLATION успадковує обмеження гармонізації (keyword matching, stage=4 = підписано)
- provisionally_closed поки не зустрічається (зарезервовано для майбутнього)

---

## Session 2026-07-15: Real-time Bill Sync (HTML Parsing)

### Проблема

Bulk JSON з `data.rada.gov.ua` оновлюється **1 раз на добу** (зазвичай вночі). Голосування, яке відбулося сьогодні (14.07, 14:29) з'явиться в JSON лише **завтра вночі**.

**Приклад:** Bill 15294
- Сайт Ради: 14.07.2026 "Прийнято за основу"
- Bulk JSON: `currentPhase.status = "Опрацьовується в комітеті"` (застарілі дані)
- Наша БД: stage=2, status="Готується на друге читання" (correct, але без хронології голосування)

### Наявні джерела даних

| Джерело | Оновлення | Дані | Метод |
|---------|-----------|------|-------|
| `data.rada.gov.ua/billinfo-skl9.json` | 1x/добу | passings, stage, status | JSON bulk |
| `w1.c1.rada.gov.ua/pls/radan_gs09/ns_golos` | Реальний час | голосування (result, votes) | HTML parsing |
| `itd.rada.gov.ua/billinfo/Bills/Card/{id}` | Реальний час | хронологія, статус, документи | HTML parsing |

**Існуючий скрипт:** `sync_votes.py` вже парсить HTML з `w1.c1.rada.gov.ua` для отримання результатів голосування.

### Аналіз можливості парсингу Bill Card HTML

**URL:** `https://itd.rada.gov.ua/billinfo/Bills/Card/{api_id}`

**Що містить сторінка:**
1. **Проходження** (вкладка) — хронологія з датами та статусами
2. **Результати голосування** (вкладка) — AJAX, завантажується після кліку
3. **Зв'язані** — пов'язані законопроекти
4. **Альтернативні** — альтернативні проекти

**Проблема:** "Результати голосування" завантажуються через JavaScript/AJAX, не в HTML напряму.

### Варіанти рішення

#### Варіант A: Парсинг хронології з Bill Card (простий)

Парсити HTML вкладку "Проходження" для отримання актуальних passings.

**Як працюватиме:**
1. Визначити "гарячі" закони (stage 2,3,4 + останні 24-48 годин за registration_date або останньою зміною)
2. Завантажити HTML карточку кожного
3. Парсити таблицю "Проходження" → оновити `bill_passings`

**Маппінг HTML → DB:**
```html
<!-- HTML -->
<tr><td>14.07.2026</td><td>Прийнято за основу</td></tr>

<!-- → DB -->
INSERT INTO bill_passings (bill_id, pass_date, title, status)
VALUES (15072, '2026-07-14', 'Прийнято за основу', 'Прийнято за основу')
```

**Переваги:**
- Простий парсинг (таблиця з 2 колонок)
- Реальний час для хронології
- Існуючий pattern в `sync_bill_passings.py`

**Недоліки:**
- Не дає результатів голосування (тільки passings)
- Потрібно знати API ID (не bill_number)

#### Варіант B: Парсинг результатів голосування (складний)

Парсити AJAX endpoint для "Результати голосування".

**Дослідження:**
- Tab "Результати голосування" завантажує дані через JavaScript
- Потрібно знайти API endpoint (перевірити Network tab в DevTools)
- Або: використати `w1.c1.rada.gov.ua` endpoint напряму

**Існуючий код в `sync_votes.py`:**
```python
# Вже працює!
url = f"http://w1.c1.rada.gov.ua/pls/radan_gs09/ns_golos?g_id={g_id}"
html = fetch_url(url)
# Парсить: За, Проти, Утрималися, Не голосували, Відсутні
```

**Проблема:** Потрібен `g_id` (vote ID). Він НЕ міститься в bulk JSON.

#### Варіант C: Гібридний (рекомендований)

1. **Основний sync:** Bulk JSON (1x/добу) — повна синхронізація
2. **Додатковий sync (кожні 2-4 години):** Парсинг HTML для "гарячих" законів
3. **Визначення "гарячих":** Ті, де `stage IN (2,3,4)` й остання зміна > 24 годин тому

**Логіка визначення змін:**
```python
# Отримати закони де stage змінився або passings застарілі
SELECT b.id, b.bill_number, b.stage, b.current_status, 
       MAX(bp.pass_date) as last_passing
FROM bills b
LEFT JOIN bill_passings bp ON bp.bill_id = b.id
WHERE b.stage IN (2,3,4)
GROUP BY b.id
HAVING MAX(bp.pass_date) < NOW() - INTERVAL '24 hours'
   OR MAX(bp.pass_date) IS NULL
```

### Практична реалізація

**Крок 1: Додати парсинг хронології з HTML (Варіант A)**

Новий скрипт `sync_bill_passings_html.py`:
1. Знайти закони де passings застарілі (>24 год)
2. Для кожного: завантажити `itd.rada.gov.ua/billinfo/Bills/Card/{api_id}`
3. Парсити таблицю "Проходження"
4. INSERT нові passings (з dedup по (bill_id, pass_date, title))

**API ID → Card URL:**
```python
# В bulk JSON є поле "url": "https://itd.rada.gov.ua/billinfo/Bills/Card/70129"
# api_id = 70129
# Або: bills.api_id з нашої БД (якщо є)
```

**Крок 2: Додати синхронізацію голосувань (Варіант B)**

Після отримання passings з HTML, перевірити чи є "Результати голосування":
1. Парсити вкладку (якщо AJAX endpoint знайдено)
2. Або: дочекатися bulk JSON (простіше)

### Обмеження

1. **Rate limiting:** RADA API може блокувати часті запити (потребує `time.sleep(1-2)` між запитами)
2. **API ID:** Потрібен маппінг `bill_number` → `api_id` (є в bulk JSON)
3. **AJAX:** Результати голосування завантажуються через JS — потрібен окремий endpoint

### Рекомендація

**Почати з Варіанту A** (парсинг хронології). Це дасть:
- Актуальні passings для "гарячих" законів
- Без залежності від bulk JSON
- Просту реалізацію (HTML parsing вже є в кодовій базі)

**Варіант B** (голосування) — додати пізніше, якщо буде знайдено AJAX endpoint.

### Конфлікти з bulk JSON: НЕМАЄ

Обидва скрипти (bulk JSON + HTML) використовують однакову логіку дедупації:
- Ключ: `(bill_id, pass_date, title)`
- Unique constraint в БД: `ON CONFLICT DO NOTHING`
- `existing_set` в пам'яті для швидкої перевірки

**Сценарій:**
1. HTML sync (14:00): вставляє "Прийнято за основу" для bill 15294
2. Bulk JSON (03:00): бачить що вже є → пропускає

**Результат:** HTML заповнює прогалини до наступного оновлення JSON. Конфліктів немає.

### TODO

- [x] Знайти AJAX endpoint для "Результати голосування" (DevTools Network tab)
- [x] Додати `sync_bill_passings_html.py`
- [x] Додати в `sync_bill_passings_html.timer` (кожні 4 години)
- [x] Оновити `sync_bill_passings.py` для dedup
- [x] Оновити ARCHITECTURE.md

### Статус: РЕАЛІЗОВАНО (2026-07-15)

Скрипт `sync_bill_passings_html.py` працює:
- Таймер: `sync_bill_passings_html.timer` (кожні 4 години)
- Тест: успішно синхронізував passings для 5 законів
- Конфліктів з bulk JSON немає (dedup)

---

## Session 2026-07-15: Night Batch Fixes (Sliding Window + Rate Limiting + Language Check)

### Проблема 1: OpenRouter 400 Errors (Context Limit)

**Причина:** `messages` список ріс безмежно з кожним чанком. Після 19+ чанків загальний розмір перевищував 262K токенів (ліміт моделі).

**Помилка:**
```
"This endpoint's maximum context length is 262144 tokens. 
However, you requested about 1025395 tokens..."
```

**Рішення:** Sliding window — максимум 9 повідомлень (system + 4 пари user+assistant).

**Код (`rag_engine.py`):**
```python
MAX_HISTORY_MESSAGES = 9
if len(messages) > MAX_HISTORY_MESSAGES:
    messages = [messages[0]] + messages[-(MAX_HISTORY_MESSAGES - 1):]
```

**Результат:** ~153K токенів (замість 325K) → 400 помилок немає.

### Проблема 2: Rate Limiting для 3 Workers

**Проблема:** 3 воркера одночасно вичерпували ліміт OpenRouter (10 req/min).

**Рішення:** Додано `_RateLimiter` для кожного провайдера:
- OpenRouter: 10 req/min (розподіляється між 3 воркерами)
- NVIDIA: 15 req/min (30 ліміт / 2 для запасу)
- Gemini: 12 req/min (вже було)

**Код (`llm_client.py`):**
```python
_openrouter_limiter = _RateLimiter(max_per_minute=10)
_nvidia_limiter = _RateLimiter(max_per_minute=15)
```

### Проблема 3: Аналіз англійською мовою

**Проблема:** Модель іноді ігнорувала інструкцію "ВІДПОВІДАЙ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ".

**Статистика:** 1 англійський аналіз з 97 (1%).

**Рішення:** Post-verification + retry:
1. Функція `_is_українською()` перевіряє % українських літер (>30%)
2. Якщо англійський → повторний аналіз
3. Якщо повторний теж англійський → залишаємо оригінал (без зациклення)

**Код (`rag_engine.py`):**
```python
def _is_українською(text: str) -> bool:
    ukr_chars = sum(1 for c in text if c in 'абвгґдеєжзиіїй...')
    eng_chars = sum(1 for c in text if c in 'abcdefghijklmnopqrstuvwxyz...')
    total = ukr_chars + eng_chars
    return ukr_chars / total > 0.3 if total > 0 else True
```

### Швидкість: 1 vs 3 Workers

| Метрика | 1 воркер | 3 воркера |
|---------|----------|-----------|
| Чанків/хв | 0.10 | 1.40 |
| Законів/день | ~50 | ~350 |
| Прискорення | — | **13.3x** |

**Причина 13x замість 3x:**
1. Немає 400 помилок → не витрачаємо час на fallback до NVIDIA
2. NVIDIA таймаути (120с × retry) більше не блокують обробку
3. Паралельне завантаження API

### TODO

- [x] Sliding window для контексту (fix 400 errors)
- [x] Rate limiting для OpenRouter/NVIDIA
- [x] Перевірка мови (retry при англійському)
- [x] 3 воркери за замовчуванням
- [x] Оновити ARCHITECTURE.md

### Статус: РЕАЛІЗОВАНО (2026-07-15)

Night batch працює стабільно:
- 400 помилок: 0 (після фіксу)
- Мова: 100% українська (з retry)
- Швидкість: ~350 законів/день

---

## Session 2026-08-19: Merge dev → main — Production Release

### What was merged
- **KPI v12 (ІЕД)** now active in production — 6 equal-weight categories replacing v11
- **All systemd services/timers** committed to repo (mpstats, votesync, night-batch, sync_eu_tracker)
- **Telegram bot** fully functional with `/bill`, `/dep`, `/top`, `/eu`, `/start` commands
- **Dashboard** migrated to ІЕД: hexagon radar (6 axes), clickable sort, activity calendar, modals
- **EU tracker** deployed: daily EC RSS + EuroPravda monitoring with Telegram alerts
- **Committee roles fix** + **deputy requests sync** completed

### Files added/updated in this merge
| Category | Files |
|----------|-------|
| KPI | `calc_kpi_v12.py`, `calc_kpi_v11.py`, `calc_kpi_v9.py`, `calc_msi_kpb.py`, `calc_bill_quality.py` |
| EU | `calc_eu_llm.py`, `calc_harmonization.py`, `eu_directives.py`, `sync_eu_tracker.py`, `sync_pulse.py` |
| Dashboard | `dashboard/index.html` (ІЕД, hexagon, calendar, sort) |
| Telegram | `telegram_bot.py`, `daily_digest_llm.py` |
| Sync | `sync_all.py`, `sync_mp_stats.py`, `sync_committee_members.py`, `sync_deputy_requests.py`, `sync_bill_passings_html.py` |
| Systemd | 7 service/timer files |
| Migrations | 3 SQL files (mp_id FK, K_pb, quality/risk/authorship) |

---

## NEXT STEPS — Prioritized Backlog (Updated 2026-08-19)

### 🔥 High Priority (next 1-2 sessions)

| # | Task | Value | Effort | Status |
|---|------|-------|--------|--------|
| 1 | **Group 5.3: High-risk alert** — toxicity > 0.7 → instant Telegram message | Автоматичні алерти, найвищий ROI | 1-2h | **DONE** (monitor.py:221, monitor.timer :05/:35) |
| 2 | **Group 3.2: Unified API** — один `/api/dashboard` замість 5+ запитів | Швидший дашборд, менше навантаження | 1h | **DONE** (e6ed5d8, d7b5c8d) |
| 3 | **Deploy sync_eu_tracker** — enable systemd timer on server | EU cluster monitoring в продакшн | 30min | OPEN |

### 🟡 Medium Priority

| # | Task | Value | Effort | Status |
|---|------|-------|--------|--------|
| 4 | **Group 5.4: Daily digest — cron setup** — systemd timers | Щоденний огляд | 30min | **DONE** (digest 09:00 + digest-llm 20:00) |
| 5 | **Group 5.6: Weekly digest** — тренди за тиждень (пн 08:00) | Регулярний огляд | 1-2h | OPEN |
| 6 | **Group 4: News monitoring** — RSS + LLM класифікація | Моніторинг новин про закони | 3-4h | OPEN |

### 🟢 Low Priority

| # | Task | Value | Effort | Status |
|---|------|-------|--------|--------|
| 7 | **Group 6: Social media** — Twitter/Facebook API | Дайджест в соцмережах | 4-6h | OPEN |
| 8 | **Group 2.2: EU pro/anti classification** | Глибший EU аналіз | 2h | OPEN |
| 9 | **Committee meetings scraper** — RADA ITD API | Автоматичні дані календаря | 3-4h | OPEN |

### 📋 Maintenance / Tech Debt

| # | Task | Note |
|---|------|------|
| A | Migrate `worker/api-server.js` to D1/Cloudflare Workers | Currently Express on server, could be edge |
| B | Add tests for `calc_kpi_v12.py` | Only `test_kpi_formula.py` for virtual testing |
| C | Document `rag_engine.py` / `risk_storage.py` | LLM pipeline not in ARCHITECTURE.md |
| D | Cleanup legacy KPI scripts (v2-v8) | Keep only v9, v11, v12 |

---

## EXECUTOR PLAN — T5: High-Risk Alert + Unified API

### T5.1: High-Risk Telegram Alert
**What:** Monitor `risk_assessments` for new high-risk bills (toxicity > 0.7 or risk_score >= 4) and send instant Telegram alert.

**Steps:**
1. Add `send_high_risk_alert(bill)` to `telegram_notifier.py`
2. Create `monitor_high_risk.py` — runs every 10 min, checks `risk_assessments` where `created_at > last_check`
3. Add systemd timer: `radacleaner-highrisk.timer` (every 10 min)
4. Filter: only NEW analyses (not re-analyzed), exclude bills already alerted

**Time estimate:** 1-2h

### T5.2: Unified Dashboard API
**What:** Create `/api/dashboard` endpoint returning all 4 blocks in one call:
- Top 20 deputies by ІЕД
- Top 10 risky bills (last 30 days)
- EU harmonization summary (6 clusters)
- High-risk alerts (recent)

**Steps:**
1. Add route in `worker/api-server.js`
2. Optimize queries (single transaction, indexed columns)
3. Update `dashboard/index.html` to use unified endpoint
4. Remove redundant API calls on page load

**Time estimate:** 1h

---

## Session 2026-08-20: Daily Digest — LLM Removed, Deterministic Format

### Problem
`daily_digest_llm.py` used an LLM (OpenRouter) to format the daily Telegram digest. The LLM was returning the **prompt text itself** ("We need to produce a daily summary in exact format...") instead of filling the template with data. Users received literal prompt instructions in Telegram instead of the digest.

### Root Cause
The LLM prompt (`DIGEST_PROMPT`) contained the format specification with placeholders like `{our_data}` and `{news_data}`. The model (nemotron-3-super) treated the format instructions as content to echo, not as a template to fill. The `format_fallback()` existed but only triggered when the LLM call failed entirely — when the LLM returned *something* (even wrong), the fallback was skipped.

### Fix
**Removed LLM from daily digest entirely.** All formatting is now deterministic:

1. **`collect_our_data()`** — queries DB for: total bills, analyzed bills, by-stage counts, today's changes (new + status changes), top-5 risky bills (30 days), plenary session status (from `rada_schedule`), committee meetings (from `rada_committee_schedule`)

2. **`search_news()`** — scrapes rada.gov.ua/news for new bill registrations + committee news; falls back to RSS; also scrapes Ukrainska Pravda + Eurointegration. Returns structured dict (was returning plain text).

3. **`format_digest(data, news)`** — new function, replaces both `call_llm()` and `format_fallback()`. Outputs the exact format:
   ```
   📋 ДД.ММ.РРРР — Моніторинг законів ВРУ
   📊 СЬОГОДНІ: plenary / committees / new bills
   📢 УВАГА: top-5 risky (30 days, newest first)
   ✅ Перевірено: analyzed/total
   Підсумок: 2-3 sentences from data
   Дані: rada.gov.ua
   ```

4. **`run_daily_digest()`** — calls `collect_our_data()` → `search_news()` → `format_digest()`. No LLM, no fallback logic.

### Files Changed
| File | Changes |
|------|---------|
| `daily_digest_llm.py` | Removed LLM (DIGEST_SYSTEM, DIGEST_PROMPT, call_llm, format_fallback, format_digest_from_text). Added format_digest(), expanded collect_our_data() with plenary/committee/analyzed. Rewrote search_news() to return dict. |
| `ARCHITECTURE.md` | Updated daily_digest_llm.py description in Key scripts table |
| `RESEARCH.md` | Updated Group 5.4 status, added this session log |

### Verification
- `venv/bin/python daily_digest_llm.py --test --force` → outputs correct format with real data (15409 bills, 9536 analyzed, 5 tracked risky, 6 new bills today)
- No LLM call, no API key needed, no rate limiting concerns
- Deterministic: same data → same output every time

### What's Left
- **Systemd timer** — need `digest-llm.timer` at 08:00 (code ready, timer setup needed)
- **Weekly digest** (Group 5.6) — not yet started

---

# Session 2026-08-21: Full Project Audit + Action Plan

Полный аудит проекта (4 направления: инфраструктура, БД, пайплайн+фронтенд, репозиторий+доки).
Все факты ниже проверены запросами к живой системе, не из памяти.

## Результаты аудита (сводка)

**Здорово:** все 10 таймеров + 4 демона работают, 0 failed units. API <300ms, дашборд задеплоен
(байт-идентичен локальному). Синк bills/risk_assessments/change_log/mps/EU-alignment свежий.
FK-целостность чистая, секреты не утекли, бот жив. mp_votes: 7.5M строк, constraint mismatch
из памяти от 16.07 давно решён (оба unique-индекса существуют). Голосования «заморожены» с
16.07 — каникулы парламента, НЕ баг (4 новых голосования после 16.07 обработаны корректно).

**Проблемы (проверено):**
| # | Проблема | Факт |
|---|----------|------|
| 1 | `rada_schedule` протух с 2026-07-08 | Дайджест всегда пишет «Пленарне засідання: не заплановано» |
| 2 | Тихие отказы | night_batch выходит 0 даже при err=275 (авария RADA 503 18.07 не замечена); sync_all timeout 600s — 6 раз за неделю в radacleaner-analyze |
| 3 | Пустые summary/law_summary | 2 992 строки без summary, НО 2 897 (97%) — процедурные (by design). Реальных дефектов ~7 непроцедурных строк |
| 4 | Закон 10399 потерял анализ | Повторная очередь 20.08 (дубль записи в pending_analysis — отдельный баг), анализ 30 чанков упал, старая запись удалена. Сейчас 0 строк в risk_assessments |
| 5 | Кодовый мусор | KPI v1–v9+v11 осиротели (активен v12); sync_all.py считает v9; мёртвые таблицы deputy_requests, bill_eu_classification (0 строк); legacy SQLite 1.67 GB нетронут с 19.06 |
| 6 | Доки отстали | ARCHITECTURE.md: digest «08:00 NEEDED» (реально 20:00 работает), в таблице юнитов ~7 из ~20; RESEARCH.md: 3.2/5.3/6.3 фактически DONE, статус OPEN |
| 7 | Git | dev/main разошлись с дублями коммитов (8≠1); ветки deploy/night-batch-fixes, feat/kpi-backend, feat/rada-schedule-research мертвы; stash на main; мусорные файлы «Очікує», «новий],» в корне |
| 8 | Планирование раздвоено | cron: sync_period */10 пн-пт + eu_alignment 04:00; systemd: остальные ~20 юнитов. В репо только 7 юнитов из установленных |
| 9 | Мелочи | psycopg2 нет в requirements.txt; stats_cache: 399/437 ключей старых (harmonization_* мертвы с 09.07, ~325 eu_news_* не чистятся); bill_sponsors 6.9% без mp_id; daemon-reload просрочен у всех юнитов |

---

## ПЛАН ДЕЙСТВИЙ (утверждён пользователем 2026-08-21)

### Фаза A — видимые пользователям исправления

#### A2. Fallback summary/law_summary + бэкфилл + перезапуск ошибок (ПЕРВЫМ — боль 10399)

**Дизайн fallback (требования пользователя):**
1. Fallback срабатывает ТОЛЬКО если модель реально не заполнила поля
   (`not llm_data.get("summary")` / `not llm_data.get("law_summary")`)
2. Обязательная маркировка источника: `json_data.summary_source = "llm" | "fallback"`
   — чтобы всегда можно было отличить реальный ответ LLM от склейки
3. Дашборд: при `summary_source == "fallback"` показывать метку «(авто)» возле «📝 Суть закону»
4. law_summary собирается из detailed_risks (кто инициирует — из title/sponsors; что меняет —
   из текстов рисков); summary = первые 1–2 предложения law_summary
5. Процедурные законы НЕ трогаем — отсутствие резюме for them by design (подтверждено)

**Шаги:**
1. `_build_fallback_summaries(llm_data)` в rag_engine.py + выставление `summary_source`
2. При успешном LLM-ответе тоже писать `summary_source="llm"` (единообразие)
3. `scripts/backfill_summaries.py` — идёт по непроцедурным строкам с пустыми полями (~7),
   заполняет из detailed_risks, ставит source=fallback. Идемпотентный, dry-run режим
4. Разобраться с падением 10399 (30 чанков, text_len=1.7M): лог обрывается на чанке 9/30 —
   вероятен таймаут/лимиты. Починить, перезапустить все 10 error из pending_analysis
5. Убрать дубль записи в pending_analysis (22415/22416 — один bill дважды)
6. Frontend: метка «(авто)» в блоке «📝 Суть закону»

**Критерий готовности:** у всех непроцедурных анализов заполнены summary/law_summary;
в json_data у каждой строки есть summary_source; 10399 имеет анализ на дашборде.

#### A1. Синк расписания Рады (rada_schedule протух с 08.07)

1. Найти/создать скрипт синка rada_schedule (источник: rada.gov.ua календарь/API)
2. Повесить на systemd timer (ежедневно утром до дайджеста 09:00/20:00)
3. Критерий: MAX(updated_at) свежий; дайджест показывает реальный статус пленарки

#### A3. Алертинг тихих отказов

1. night_batch.py: при err > 10 — Telegram alert + ненулевой exit code
2. radacleaner-analyze: расследовать sync_all timeout 600s (хроника 14–20.08),
   увеличить таймаут или разбить шаги
3. digest-llm.service Description: «AI-powered» → «Deterministic daily digest»

### Фаза B — гигиена

#### B1. Git
1. Merge dev→main одним мерджем (устранить дубли коммитов)
2. Удалить ветки: deploy/night-batch-fixes, feat/kpi-backend, feat/rada-schedule-research
3. Drop stash@{0}; удалить мусор: «Очикує», «новий],\n tracked: номер»
4. Untrack dashboard/best.html, worker/package-lock.json (уже в .gitignore)

#### B2. KPI cleanup
1. Удалить: calc_deputy_kpi.py, v2–v8, v9, v11 (активен только v12)
2. sync_all.py: сначала выяснить роль (его зовёт radacleaner-analyze!), затем вывести
   из эксплуатации или переписать v9→v12
3. telegram_bot.py: отвязать от v11, если ссылается

#### B3. Документация + юниты
1. ARCHITECTURE.md: полная таблица systemd (~20 юнитов), digest 20:00, путь test_kpi_formula.py
2. RESEARCH.md: статусы 3.2, 5.3, 6.3 → DONE
3. Закоммитить недостающие юниты в systemd/: monitor, digest×2, analyze,
   votesync.service, sync_active_bills
4. `systemctl daemon-reload`; решить судьбу disabled sync_active_bills

#### B4. Планирование и хранение
1. cron → systemd timers: eu_alignment.py (04:00), sync_period.py (*/10 пн-пт)
2. stats_cache: DELETE старых ключей (~325 eu_news_*, harmonization_* c 09.07,
   directive_*, pulse_*, active_mps_30d)
3. DROP deputy_requests, bill_eu_classification — ⚠️ только после подтверждения
4. Архив/удаление legacy SQLite 1.67 GB — ⚠️ только после подтверждения
5. requirements.txt += psycopg2-binary

### Фаза C — развитие

#### C1. Еженедельный дайджест (пн 08:00)
Детерминированный (как ежедневный): новые законы за неделю, изменения стадий,
топ-5 рисков, динамика ІЕД топ-10. Timer weekly-digest.timer.

#### C2. Минимальные тесты
pytest: формула calc_kpi_v12, STATUS_IDS в sync_votes, format_digest.
Без CI — локальный запуск.

### Отложено (вне этой серии)
Group 4 (новости/фейки), Group 7 (UX избирателей), соцсети, jsonb-миграция json_data,
edge-deploy API.

### Правила выполнения
- Порядок: A2 → A1 → A3 → B1 → B2 → B3 → B4 → C1 → C2
- Коммиты в dev; merge в main — в конце фаз
- Деструктивные операции (DROP таблиц, удаление SQLite/веток) — только после явного «да»

---

## Session 2026-08-21: Action Plan Execution — A/B/C Complete

Виконано повний план (див. вище). Комміти в dev: 7546352..d46e74a.

### Фаза A
| Задача | Результат |
|--------|-----------|
| A1 rada_schedule | sync_schedule_legacy.py працює (48 дат), timer 07:30; дайджест показує реальний статус пленарки |
| A2 fallback+бекфіл | `_build_fallback_summaries()` з маркером `summary_source` (llm/fallback/none); метка «(авто)» на дашборді; timeout 600s→3600s; статус 'done' лише при rc=0; бекфіл 6 рядків; дедуп-індекс черги; 4 закони переаналізовано (10399 — 53 хв, 30 чанків, source=llm); черга 360 done / 0 error |
| A3 алертинг | night_batch: Telegram alert + exit(1) при err>10; sync timeout 1800s |

### Фаза B
| Задача | Результат |
|--------|-----------|
| B1 git | гілки/stash/сміття видалено, main злитий з dev без конфліктів |
| B2 KPI | 9 легасі-скриптів видалено (-1783 рядки), бот → kpi_v12_score, sync_all без v9 |
| B3 доки+юнити | 26 unit-файлів у репо, ARCHITECTURE.md таблиця повна, daemon-reload |
| B4 планування | cron→systemd (eu_alignment 04:00, sync_period */10 пн-пт, перший запуск перевірено); stats_cache 437→110; DROP deputy_requests + bill_eu_classification (+мертвий роут і UI); SQLite 1.6GB заархівовано в ~/backups (md5-звірено) і видалено |

### Фаза C
| Задача | Результат |
|--------|-----------|
| C1 тижневий дайджест | weekly_digest.py (детермінований), timer пн 08:00; фікс «Стадія 5/4» у обох дайджестах |
| C2 тести | pytest: 37 тестів (формула ІЕД v12 C1-C6, STATUS_IDS регресія 16.07, формат дайджестів) |

### Відкладено
Group 4 (новини/фейки), Group 7 (UX виборців), соцмережі, jsonb-міграція, edge-deploy API.

---

## Session 2026-08-21 (додаток): Дрібніші фікси після основного плану

### 1. Ризики не рендерились у 246 аналізів (4696eb9)
**Симптом:** закон 10399 на дашборді без блоку ризиків (36 ризиків у БД!).
**Корінь:** фронтенд рендерить ризики лише за `Object.hasOwn(parsed, 'has_risks')`.
Nemotron іноді пропускає цей ключ у JSON → блок зникає.
**Фікс:** (1) rag_engine гарантує ключ для непроцедурних; (2) фронтенд приймає
також `Array.isArray(parsed.detailed_risks)`; (3) міграція 017 бекфіллила 246 рядків.

### 2. Хронологія відставала для активних законів (586c3eb)
**Симптом:** 10399 — 14 подій у БД проти 19 на сайті Ради (підписання 20–21.08 відсутні).
**Корінь:** відбір у sync_bill_passings_html.py був вивернутий — перевірялись закони
з останнім проходженням СТАРШИМ за 7 днів, тобто найактивніші виключались.
JSON-булк оновлюється 1x/добу → внутрішньоденні події провалювались у щілину.
**Фікс:** пріоритет — АКТИВНІ (остання подія ≤7 днів, ~60 шт.) першими, потім
несинхронізовані, потім ротація застарілих (440/запуск). Активні перевіряються кожні 4 год.

### 3. Офіційний номер закону на дашборді (d02dd0f)
Після підписання (stage 4) законопроєкт отримує номер у реєстрі законів IX скликання
(`bills.act_number`, напр. «4931-ІХ» для 15225). Покриття 4784/4784 (100%).
Додано бейдж «Закон: №4931-IX →» з посиланням на zakon.rada.gov.ua
(кирилиця ІХ → латиниця IX для URL). Раніше поле ніде не рендерилось.

---

## Аудит ІЕД v12 (2026-08-21): сомнительные метрики и варианты исправления

### Находки аудита
| # | Проблема | Масштаб |
|---|----------|---------|
| 1 | C4 «комітет»: бесплатная нейтраль 50 за отсутствие роли; топ определяется должностью (все top-5 имеют C4=100) | 171/389 (44%) на ровно 50 |
| 2 | C3 парадокс малых авторов: <3 своих законов → нейтраль 50; автор 2/2 принятых получает 50, автор 3 с 1 принятым → 32 | 77 чел. на нейтрали |
| 3 | Непоследовательная философия «нет данных»: C4 нет роли → 50, C5 нет ответов → жёсткий 0; 69 без обращений неотличимы от игнорирующих | 85 нулей C5 |
| 4 | C6/C2: eu_score=0 и quality=0 трактуются как «нет данных» (`if x else 0.5`) → реальный ноль даёт нейтраль | 41 депутат eu=0 |
| 5 | Двойные нейтралы C3+C4 → ⅙ балла бесплатно | 34 чел. |

Здорово: C1 нули = реальные прогульщики (явка 2.5–5%); C5 данные живые (320/389);
распределение 22.1–67.0 без выбросов; входы актуальны (mpstats каждые 6ч).

### Варианты исправления (выбор за пользователем)
**A. Точечный (~30 мин):**
- C6/C2: различать NULL и 0 (`is None` вместо falsy)
- C5: «нет обращений» → н/д, «есть обращения, нет ответов» → 0
- C3: считать adoption честно от 1 закона (порог <3 убрать)

**B. Взвешенное среднее с покрытием (~2ч):**
- Вес компоненты = 1 если есть данные, 0 если нет; ІЕД = Σ(cᵢ·wᵢ)/Σwᵢ
- Без комитетской роли ІЕД считается по остальным 5 компонентам
- Дашборд показывает «покрытие данных N/6». Минус: заметный сдвиг ранкинга

**C. Гибрид (рекомендация архитектора):**
- B для C4 (главный источник инфляции) + точечные фиксы A для C3/C5/C6

⚠️ Перед выбором варианта — аудит источников данных (см. следующий раздел):
возможно часть «аномалий» лечится сбором данных, а не формулой.

---

## Аудит источников данных ІЕД (2026-08-21, продолжение): C5 был сломан сбором, не формулой

### Гипотеза пользователя подтвердилась
«Может у нас просто данных нет, а расчёты правильные» — ДА, для C5 (звернення) это ровно так.

### Находка: пагинация mprequests API
- `itd.rada.gov.ua/mprequests/api/DeputyRequest/SearchResults` отдаёт **30 строк/страницу**,
  параметр `{"Page": N}` листает страницы (Take/Skip/PageNumber — игнорируются)
- Старый синк парсил ТОЛЬКО страницу 1: `requests_with_response` был обрезан сверху ~30
- **Пример: Яценко А.В. — реально 221/222 обращений с ответом (99%), в БД лежало 22 (10%)**
- Затронуты 19 депутатов с >30 обращениями (самые активные — ядро метрики)
- Второй баг: сбой API возвращал (0,0) → обнулял депутата. Теперь сбой = пропуск (старые данные сохраняются)

### Исправление (50f3ac0)
`sync_deputy_requests.py`: обход всех страниц + защита от рассинхрона пагинации
(если обошли < total → None, не писать). Прогон: 389 депутатов, 0 ошибок.

### Верификация остальных источников (живые проверки 21.08)
| Компонента | Источник | Вердикт |
|------------|----------|---------|
| C1 py/pda/vkp | w1.c1.rada.gov.ua (голосования) | ✅ корректно; нули = реальные прогульщики (Дубінський 5.1% и др.) |
| C4 committee_score | itd.rada.gov.ua/struct/Data/UserNames | ✅ роли совпадают с живым API (Голова=100, Заступник=70, Спікер=100) |
| C5 request_count/with_response | itd.rada.gov.ua/mprequests/api | ❌→✅ ИСПРАВЛЕНО (пагинация) |
| C3 adoption | bill_sponsors (sponsor_order=0) + bills.stage | ✅ покрытие спонсоров 99.95% (аудит 07.01) |
| C2/C6 quality/risk/eu | внутренние (LLM-пайплайн) | ✅ пайплайн живой, но см. вопросы формулы (нейтраль 0.5) выше |

### Эффект исправления C5 на ранкинг (перерасчёт 21.08)
- Климпуш-Цинцадзе: #2 → **#1** (C5 78→99)
- В топ-10 вошли депутаты с реальной работой по обращениям: Качура (C5=100),
  Разумков (C5=100), Бондар (C5=100), Крейденко (C5=100)
- Выпали из топ-10: Пушкаренко, Підласа, Радуцький
- Средний ІЕД: 44.1 → 43.9 (оценки только выросли, сдвиг за счёт лидеров C5)

### Вывод для выбора варианта исправления (A/B/C выше)
C4-нейтраль (44% депутатов на халявных 50) остаётся ГЛАВНОЙ проблемой формулы —
данными её не лечить (роли собраны верно). Рекомендация C (гибрид) остаётся в силе.

---

## Методологія 2.1 — ОПУБЛІКОВАНА (2026-08-21, ухвалено користувачем)

Рішення по аудиту ІЕД: прозорість замість прихованих ваг.

### Що зроблено
1. **C4 «Комітет» — публічна монотонна шкала** (`C4_LADDER` в calc_kpi_v12.py):
   немає ролі 40 · член 55 · секретар/голова підкомітету 70 · заступник 85 · голова/спікер 100.
   Усунено інверсію (член комітету 30 < безролий 50). Крок 15 — м'який, будь-яка роль ≥ немає ролі.
2. **Відкритий промпт ШІ** на дашборді: дослівний system+main промпт із src/prompts.py
   + нотатки про чанкинг (~30К символів), повторний аналіз при англійській мові,
   фікс галюцинації дискреції, маркування «(авто)».
3. **Футер на всіх сторінках**: карточки методології винесені з секції депутатів
   у глобальний контейнер — видно на Дашборді, Законах, Депутатах, Графіку, EU Alignment.

### Принцип (слова користувача)
«Нас не повинні обвинуватити в упередженості. Ніяких прихованих ваг — тільки буквально
м'які поправочні коефіцієнти». Єдина опублікована лінійка для всіх 389 депутатів
замість виключень компонент (виключення виглядало б як «для одних інші правила»).

### Ефект ранкингу (перерахунок 21.08)
Топ: Климпуш-Цинцадзе 68.5 (#1), Третьякова 68.2, Аліксійчук 64.3 (#3, C4 50→70),
Бондар #5, Юрченко #9 (+26.2 до v11). Діапазон 19.4–68.5.

### Версія методології
**2.1 (2026-08-21)** — вказана на дашборді. При зміні формул/промптів: оновити код,
розділ на дашборді і версію. Тести: 41 (включно з монотонністю C4_LADDER).

## Графік rework: sync fixes (2026-08-21)

Діагностика (explore-агент): sync_schedule.timer був enable-без-start → жодного запуску
(виправлено 2026-08-21, перший запуск 17:30); sync_committee_schedule.py не був підключений
до оркестрації (тепер systemd timer 07:40). Дублікати (date,event_type) в rada_schedule
видалені при створенні uniq_rs_date_type (477→409 plenary). uniq_rcs_meeting — виразний
унікум для ON CONFLICT.

Календар збагачено 3 новими типами подій: votes (votes.vote_date), committee
(rada_committee_schedule), eu (eu_cluster_status.event_date). /api/activity-day віддає
events{schedule,committees,votes} + changes. /api/schedule: session без фейкових дат
(лише «IX скликання»), freshness-штампи (max updated_at), committees з
meeting_date_ua (форматування на сервері через node-pg DATE→local-midnight баг).

Обмеження: пленарні дати ВР публікує без майбутнього горизонту (вересень порожній —
це канікули, не баг); комітети публікують лише поточний тиждень. UI має показувати
freshness-штамп замість «зламаного» вигляду.

## Info Attack Detector — Phase 1 collector (2026-08-21)

Мета: детектор синхронних інфоатак — коли багато каналів одночасно починають
постити одне й те саме, пуш у Telegram з посиланням на спростування.
Фішка проєкту: швидкість (хвилини замість денного розбору фактчекерів).

Фаза 1 (collector): sync_info_monitor.py кожні 30 хв (systemd timer).
Джерела: 5 RSS фактчекерів (ЦПД, VoxCheck, StopFake, Детектор медіа, SPRAVDI)
+ ~20 верифікованих t.me/s каналів зі списку СБУ (data/disinfo_channels.json,
мертві канали викинуті при перевірці). Таблиці: info_items (url UNIQUE +
simhash64 лексичний), attack_alerts. Рішення: simhash без embeddings — без
важких залежностей (torch), координовані кампанії часто копипастять тезиси;
семантичну кластеризацію додає нічна LLM-розмітка (фаза 3). MTProto НЕ
використовуємо (ToS Telegram забороняє скрапинг), тільки публічний t.me/s.

Далі: фаза 2 = кластеризація + бьорст-детектор + TG-пуш; фаза 3 = нічна
LLM-розмітка нарративів + ТОП дня; фаза 4 = вкладка «Інфоатаки» на дашборді.

## Info Attack Detector — Phase 2 burst detector (2026-08-21)

detect_attacks.py — другим ExecStart у sync_info_monitor.service (Type=oneshot
дозволяє кілька ExecStart підряд; таймер той самий, 30 хв). Пайплайн:

1. **Завантаження**: info_items за ATTACK_WINDOW_HOURS=48 год, simhash NOT NULL.
2. **Кластеризація**: union-find попарно, O(n²) (~сотні/пари тисяч записів —
   < 1 с; апгрейд при рості — LSH-бандінг). Пара в одному кластері, якщо
   hamming(simhash_a, simhash_b) <= 10 (копипаст) АБО token-Jaccard
   norm_words(title+body) >= 0.45 (парафраз; norm_words реюз з фази 1).
3. **Бьорст-правило**: distinct telegram-каналів >= 4 І постів >= 8 І
   розкид <= ATTACK_WINDOW_HOURS/2 (24 год; у ТЗ було «12 год» — арифметика
   48/2 дає 24, узято формулу). Factcheck-only кластери ніколи не алертять
   (TG-каналів = 0).
4. **Спростування**: фактчекери за 14 днів; token-overlap >= 0.25 з топ-5
   токенами кластера; найсвіжіший → debunk_url. Немає → «поки немає — стежимо».
5. **Біллі**: regex «законопроєкт/закон/білль №NNNNN» або «№NNNNN» (4-5 цифр,
   слово-ключ обов'язкове поруч — щоб не ловити роки); перевірка EXISTS у
   bills.bill_number. Прямого URL картки в дашборді немає (він ходить по
   внутрішньому id через /api/bills/:id) → в пуші просто «№NNNN».
6. **Cooldown**: алерти за 24 год; Jaccard label-наборів >= 0.6 => та сама
   кампанія → повторний пуш лише якщо постів >= 2x (ескалація). Кожне
   спрацювання → INSERT attack_alerts + UPDATE info_items.cluster_id
   (послідовні id від max(cluster_id)).
7. **Алерт**: український, send_telegram-патерн sync_eu_tracker (env
   TG_BOT_TOKEN/TG_CHAT_ID), alert_sent=true лише після реальної відправки.
   CLI: --dry-run (тільки stdout) / --no-send (все крім TG).

Чому лексичної кластеризації досить: скоординовані кампанії копипастять
тези/пости майже дослівно (simhash ловить з hamming <= 10), решта —
парафрази з спільним словником (Jaccard). Семантику («одна теза різними
словами») додає нічна LLM-розмітка у фазі 3 — вона ж дасть назви нарративів
замість топ-токенів.

Тести: tests/test_detect_attacks.py, 16 кейсів на чистих функціях (hamming
з маскою знакового BIGINT, jaccard, find_clusters копипаст/парафраз/синглтони,
бьорст 4 канали так / 3 ні / розкид / factcheck-only, cooldown-ескалація,
текст алерта, bill regex). Повний suite: 70 pass.

## Info Attack Detector — Phases 3+4 (2026-08-21, SHIPPED)

**Фаза 3** — label_narratives.py (щодня 07:15): топ-15 кластерів + factcheck-ітеми
за 24г → рівно 2 виклики nemotron (llm_completion_raw + власний JSON-парсинг,
бо llm_completion() падає на масивах) → stats_cache `info_digest`
{clusters[{label,category,size}], fakes[{one_line,significance}]}. Fallback при
смітті LLM: label = топ-токени, significance=0.

**Фаза 4** — вкладка «🛡 Інфоатаки» на дашборді: hero (постів/каналів/атак за
24г), лента Зафіксовані атаки (з attack_alerts, empty-state «це добре ✅»),
плитки Наративи доби (LLM-labels + категорії вибори/закони/інше), ТОП перевірок
фактчекерів (truth-sandwich: суть → бейдж джерела → повний розбір ↗),
методологія: вердикти — тільки за людськими фактчекерами.

API GET /api/info-digest: digest + останні attack_alerts + stats 24г.
Тести: 75 passed (5 нових). Жива верифікація: браузерний скриншот вкладки +
енд-то-енд тест алерта (синтетична хвиля 8 постів × 4 канали → TG push
sent=True → тестові дані видалені з БД).

Обмеження v1: лексична кластеризація ловить копипаст-кампанії; парафрази —
тільки через Jaccard ≥0.45 (семантика — кандидат у v2 через embeddings).
Мертві канали у списку СБУ дають старі posted_at — не шкодять (cutoff).

## Watchlist refresh — щоденне оновлення списку каналів (2026-08-21)

sync_disinfo_channels.py (щодня 06:50): джерело списку СБУ — стаття 5.ua
(іменований перелік у лапках після «увійшли»; топ-100 публікується картинкою —
непарсабельно). Нові імена → резолвинг хендла через наш SearXNG
(serch.h.dino.pp.ua, запит «"ім'я" site:t.me», верифікація og:title з
UA/RU-нормалізацією: и/ы/і→i, є/ё→e, ъ/ь→''). Liveness всіх каналів щодня;
dead_streak≥3 → prune (мертві канали зі старого списку СБУ 2022 не смітять).
TG-повідомлення адміну тільки при ЗМІНІ набору (стан у
data/.disinfo_unresolved.json). Захист: <10 каналів → abort запису.

Перший прогін: авто-додано «Херсон live» (@kherson_live); 6 імен списку СБУ —
перейменовані канали, живі наступники вже в конфізі («Харківський регіон»,
«Запорізький регіон», «Одеська область...») — залишені як unresolved для адміна.

## Меню бота v2 — проработка (2026-08-21)

**Зараз**: /start з inline-меню (закон / топ депутатів / євроінтеграція /
допомога), /bill NNNN, /dep Ім'я, /top, /eu, /help; text_handler приймає номер
закону після кнопки. Виправлено 21.08: /help і set_my_commands згадували
«KPI» (→ ІЕД) та не згадували /dep.

**Пропозиція v2** (пріоритети):

P1 — нові read-only команди (дешево, дані вже є):
- /attacks — останні зафіксовані синхронні хвилі (attack_alerts) + лінки на
  спростування;
- /fakes — ТОП-10 перевірок фактчекерів дня (stats_cache `info_digest`,
  one_line + бейдж джерела + посилання);
- кнопка «🚨 Інфоатаки» у головному меню /start.

P2 — персональні підписки на сповіщення:
- таблиця bot_subscribers (chat_id PK, attacks BOOL, digest BOOL,
  subscribed_at); опт-ін через кнопку «🔔 Підписатися» в /start і після кожного
  пуша; /off — повне видалення запису (приватність: зберігаємо лише chat_id +
  флаги);
- розсилка: цикл send_telegram по підписниках (Bot API ~30 msg/s — запас
  великий); тексти реюзають build_alert_text / digest-форматери; анти-спам
  успадковує cooldown кампаній з detect_attacks (24г + ескалація ×2);
- дисклеймери ті ж самі (вердикти — за фактчекерами).

P3 — персональний ранковий дайджест (той самий deterministic формат, що
digest-llm о 20:00, але в приватний чат підписника).

Не робити: довільні LLM-відповіді користувачу у приваті (вартість лімітів +
ризик галюцинацій від імені проєкту).

## Меню бота v2 — ВНЕДРЕНО (2026-08-22, P1+P2+P3)

**P1**: /attacks (attack_alerts, останні 5, з debunk-лінками), /fakes (ТОП-10
фактчеків з stats_cache `info_digest`), кнопки «🚨 Інфоатаки» / «🧪 Фейкі дня»
та «👤 Депутат» (введення імені через стан awaiting_dep_name → send_dep_profile)
в /start. Форматери format_attacks/format_fakes — чисті, 5 тестів.

**P2**: таблиця bot_subscribers (migration 025: chat_id PK, attacks, digest).
Кнопка «🔔 Підписки» → інлайн-тогли (INSERT..ON CONFLICT DO UPDATE SET x = NOT x,
перевірено SQL). /off — повне видалення запису. Розсилка:
detect_attacks.broadcast_subscribers() після кожного алерта (attacks=true),
daily_digest_llm.broadcast_digest() після дайджесту (digest=true); помилка
одного чата не ламає розсилку; Bot API ліміт ~30 msg/s — запас великий.

**P3**: персональний дайджест = той самий deterministic текст digest-llm,
розісланий підписникам одразу після службового каналу (20:00).

Борг закрито (2026-08-22): cmd_dep переведено на kpi_v12_* — C1–C6 у шкалі 0–100, середнє компонент = ІЕД. Перевірено на живих даних (Гетманцев 56.7).
## Клуби голосування — Level A ВНЕДРЕНО (2026-08-23)

**Що це**: попарна узгодженість голосувань активних депутатів → виявлення
неформальних груп через межі фракцій. Рівень A з плану «групи інтересів»
(обговорення 2026-08-23): лише власні дані, нуль нових джерел.

**Метод**: numpy-матриця 389 активних депутатів × 19,674 голосування
(позиції 1=за/2=проти/3=утримався; 4/5 не позиції). Рахується ЛИШЕ у спірних
голосуваннях (11,992 — де були і «за», і «проти»): одноголосні не несуть
сигналу. Пара зберігається якщо ≥400 спільних позицій (відсіює шум рідко
голосуючих — інакше топ забитий 100%-збігами на ~100 голосах) і узгодженість
≥70%. Результат: 33,063 пари, з них 7,992 крос-фракційні.

**Інфра**: voting_allies (migration 026), calc_voting_clubs.py (щотижня пн
05:00, ~65с), /api/voting-clubs (?limit= крос-фракційні пари, ?mp= однодумці),
вкладка «🤝 Клуби» + блок «Однодумці» в профілі депутата. Тесты: pure-функція
agreement_matrix (2 кейси). Чесна застереження на дашборді: кореляція ≠ доказ
лобіювання.

**Level B (наступний крок, узгоджено)**: LLM-поле interest_sectors в аналізі
законів (чиї галузі виграє) → агрегат «профіль інтересів» депутата. Вписується
в наявний пайплайн rag_engine + нічний джоб у стилі label_narratives; бекфілл
по 9.5K проаналізованих.

**Level C (на радарі)**: Реєстр прозорості НАЗК (закон №3606-20,
transparency.nazk.gov.ua, у фронтенді знайдено /api/v1) — джойн суб'єктів
лобіювання з bill_number. Реєстру молодий, моніторити наповнення. Опційно:
декларації НАЗК (КВЕДи компаній депутатів). Юридична дисципліна та сама, що
в інфоатаках: факти + атрибуція, без власних вердиктів «лобіст».

## Level B — Профіль інтересів ВНЕДРЕНО (2026-08-23)

**Механіка**: LLM у фінальному JSON аналізу тепер повертає `interest_sectors` —
0-3 галузі з контрольованого словника INTEREST_SECTORS (16 значень,
src/prompts.py). Словник фіксований навмисно: вільні назви фрагментувались би
й агрегація по депутатах була б неможлива. rag_engine._normalize_interest_sectors()
гарантує ключ (урок has_risks), фільтрує до словника, процедурним ставить [].

**Бекфілл історії** (backfill_interest_sectors.py): 2,971 процедурних → []
без LLM; 6,511 непроцедурних → LLM-екстракція 0-3 галузей з НАЯВНОГО raw_analysis
(короткий виклик ~10с через llm_completion_raw + власний парсинг — gotcha з
масивами), пріоритет risk_score DESC, 300/день о 08:30 ≈ повний обхват за ~22 дні.

**Агрегація** (calc_interest_profiles.py → deputy_interests, migration 027):
authored = авторство/співавторство законів галузі; voted_for/voted_against =
голоси депутата за такі закони (вікно 2 роки). Перший прогін: 857 пар
депутат×галузь (голосування розходяться одразу на всіх, авторство росте з бекфілом).

**Витрина**: /api/interests?mp=ID; блок «Профіль інтересів» у профілі депутата
(бари: N автор. · M голосів «за», дисклеймер «кореляція, не доказ лобіювання»).
Промпт змінено → методологія на дашборді оновлена скриптом
scripts/sync_prompt_to_dashboard.py (новий маркерний генератор — повторне
використання при кожній зміні промпту).

**Що далі (Level C)**: Реєстр прозорості НАЗК — моніторити наповнення.
