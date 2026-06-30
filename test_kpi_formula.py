#!/usr/bin/env python3
"""
test_kpi_formula.py — Virtual testing of KPI v10 formula.

Generates 10-15 virtual deputies with extreme/edge-case metrics
and calculates KPI scores to test formula stability and logic.
"""
import math

# KPI v10 weights
WEIGHTS = {
    "lei": 0.20,
    "py": 0.15,
    "pda": 0.10,
    "quality": 0.15,
    "committee": 0.10,
    "conv": 0.10,
    "risk_penalty": 0.10,
    "requests": 0.10,
}

# Committee scores
COMMITTEE_SCORES = {
    "chair": 10,
    "vice_chair": 7,
    "secretary": 5,
    "subcommittee_head": 5,
    "member": 3,
    "none": 0,
}


def normalize(values):
    """Normalize list to 0-100."""
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mx == mn:
        return [50.0] * len(values)
    return [(v - mn) / (mx - mn) * 100 for v in values]


def get_att_mult(py):
    """Progressive attendance multiplier."""
    if py < 30:
        return 0.3
    elif py < 50:
        return 0.6
    elif py < 70:
        return 0.85
    else:
        return 1.0


def get_requests_eff(requests, py):
    """Requests threshold: can't submit requests without attending."""
    return requests if py >= 30 else 0


def calc_kpi(deputy):
    """Calculate KPI score for a virtual deputy."""
    py = deputy["py"]
    pda = deputy["pda"]
    lei = deputy["lei"]
    quality = deputy["quality"]
    committee = COMMITTEE_SCORES.get(deputy["committee_role"], 0)
    conv = deputy["conv"]
    risk_penalty = deputy["risk_penalty"]
    requests = deputy["requests"]
    total_primary = deputy.get("total_primary", 10)

    # Zero attendance floor
    if py < 10:
        return 0.0, 0.0, committee

    # Committee halving for no bills
    if total_primary == 0:
        committee *= 0.5

    # Attendance multiplier
    att_mult = get_att_mult(py)

    # Requests threshold
    requests_eff = get_requests_eff(requests, py)

    # Score (raw, before normalization)
    score = (
        WEIGHTS["lei"] * lei * 100 +  # LEI is 0-1.5, scale to 0-150
        WEIGHTS["py"] * py +
        WEIGHTS["pda"] * pda +
        WEIGHTS["quality"] * quality * 20 * att_mult +  # quality is 1-5, scale to 20-100
        WEIGHTS["committee"] * committee * 10 * att_mult +  # committee is 0-10, scale to 0-100
        WEIGHTS["conv"] * conv * att_mult +  # conv is 0-100
        WEIGHTS["risk_penalty"] * risk_penalty * att_mult +  # risk_penalty is 0-100
        WEIGHTS["requests"] * requests_eff * att_mult  # requests is 0-100 (normalized)
    )

    return min(max(score, 0), 100), att_mult, committee


# Virtual deputies
VIRTUAL_DEPUTIES = [
    # Scenario A: Ideal legislator
    {
        "name": "Ideal_Legislator",
        "scenario": "A: Ideal legislator",
        "py": 95, "pda": 100, "lei": 1.2,
        "quality": 4.5, "committee_role": "chair",
        "conv": 45, "risk_penalty": 20, "requests": 25,
        "total_primary": 50,
    },
    # Scenario B: Absentee with good bills
    {
        "name": "Absentee_Good",
        "scenario": "B: Absentee with good bills",
        "py": 25, "pda": 90, "lei": 0.8,
        "quality": 4.2, "committee_role": "vice_chair",
        "conv": 35, "risk_penalty": 25, "requests": 10,
        "total_primary": 30,
    },
    # Scenario C: Rubber stamp
    {
        "name": "Rubber_Stamp",
        "scenario": "C: Rubber stamp (high attendance, low quality)",
        "py": 95, "pda": 100, "lei": 0.3,
        "quality": 1.5, "committee_role": "member",
        "conv": 15, "risk_penalty": 80, "requests": 5,
        "total_primary": 20,
    },
    # Scenario D: Institutional leader
    {
        "name": "Institutional_Leader",
        "scenario": "D: Institutional leader (chair, no bills)",
        "py": 90, "pda": 85, "lei": 0.0,
        "quality": 3.0, "committee_role": "chair",
        "conv": 0, "risk_penalty": 50, "requests": 15,
        "total_primary": 0,
    },
    # Scenario E: Co-author specialist
    {
        "name": "CoAuthor_Specialist",
        "scenario": "E: Co-author specialist (high weighted adopted)",
        "py": 80, "pda": 75, "lei": 0.6,
        "quality": 3.5, "committee_role": "member",
        "conv": 25, "risk_penalty": 35, "requests": 8,
        "total_primary": 5,
    },
    # Scenario F: Protest voter
    {
        "name": "Protest_Voter",
        "scenario": "F: Protest voter (high attendance, 50% abstain)",
        "py": 85, "pda": 50, "lei": 0.4,
        "quality": 3.0, "committee_role": "member",
        "conv": 20, "risk_penalty": 40, "requests": 3,
        "total_primary": 15,
    },
    # Scenario G: Low everything
    {
        "name": "Low_Everything",
        "scenario": "G: Low everything (bad deputy)",
        "py": 30, "pda": 40, "lei": 0.0,
        "quality": 1.0, "committee_role": "none",
        "conv": 0, "risk_penalty": 90, "requests": 0,
        "total_primary": 0,
    },
    # Scenario H: High risk, high quality
    {
        "name": "HighRisk_HighQuality",
        "scenario": "H: High risk but high quality bills",
        "py": 75, "pda": 80, "lei": 0.9,
        "quality": 4.8, "committee_role": "vice_chair",
        "conv": 40, "risk_penalty": 10, "requests": 12,
        "total_primary": 25,
    },
    # Scenario I: Subcommittee head
    {
        "name": "SubCommittee_Head",
        "scenario": "I: Subcommittee head (score=5)",
        "py": 88, "pda": 92, "lei": 0.5,
        "quality": 3.8, "committee_role": "subcommittee_head",
        "conv": 30, "risk_penalty": 30, "requests": 18,
        "total_primary": 20,
    },
    # Scenario J: Zero attendance
    {
        "name": "Zero_Attendance",
        "scenario": "J: Zero attendance (should be near 0)",
        "py": 0, "pda": 0, "lei": 1.0,
        "quality": 5.0, "committee_role": "chair",
        "conv": 50, "risk_penalty": 0, "requests": 30,
        "total_primary": 100,
    },
]


def main():
    print("Virtual KPI v10 Formula Test Results")
    print("=" * 120)

    # Calculate scores
    results = []
    for dep in VIRTUAL_DEPUTIES:
        score, att_mult, committee = calc_kpi(dep)
        results.append({
            **dep,
            "score": score,
            "att_mult": att_mult,
            "committee_score": committee,
        })

    # Print table
    header = f"{'Name':<22} | {'Scenario':<45} | {'LEI':>6} | {'ПЯ':>5} | {'ПДА':>5} | {'Qual':>5} | {'Comm':>5} | {'Conv':>5} | {'Risk':>5} | {'Req':>5} | {'Mult':>5} | {'Score':>6}"
    print(header)
    print("-" * len(header))

    for r in results:
        print(f"{r['name']:<22} | {r['scenario']:<45} | {r['lei']:>6.2f} | {r['py']:>5.1f} | {r['pda']:>5.1f} | {r['quality']:>5.2f} | {r['committee_score']:>5.1f} | {r['conv']:>5.1f} | {r['risk_penalty']:>5.1f} | {r['requests']:>5.1f} | {r['att_mult']:>5.2f} | {r['score']:>6.1f}")

    # Analysis
    print("\n" + "=" * 120)
    print("ANALYSIS")
    print("=" * 120)

    scores = [r["score"] for r in results]

    print(f"\n1. STABILITY:")
    print(f"   Score range: {min(scores):.1f} - {max(scores):.1f}")
    print(f"   All scores within 0-100: {'YES' if all(0 <= s <= 100 for s in scores) else 'NO'}")
    print(f"   Average: {sum(scores)/len(scores):.1f}")

    print(f"\n2. LOGIC (progressive penalty):")
    ideal = next(r for r in results if r["name"] == "Ideal_Legislator")
    absentee = next(r for r in results if r["name"] == "Absentee_Good")
    zero = next(r for r in results if r["name"] == "Zero_Attendance")
    print(f"   Ideal (ПЯ=95%) vs Absentee (ПЯ=25%): {ideal['score']:.1f} vs {absentee['score']:.1f} (diff={ideal['score']-absentee['score']:.1f})")
    print(f"   Zero attendance: {zero['score']:.1f} (should be near 0)")

    print(f"\n3. IDEOLOGY (reward good, penalize bad):")
    rubber = next(r for r in results if r["name"] == "Rubber_Stamp")
    high_risk = next(r for r in results if r["name"] == "HighRisk_HighQuality")
    print(f"   Rubber stamp (low quality): {rubber['score']:.1f}")
    print(f"   High risk + high quality: {high_risk['score']:.1f}")
    print(f"   High quality should beat low quality: {'YES' if high_risk['score'] > rubber['score'] else 'NO'}")

    print(f"\n4. SPECIAL CASES:")
    institutional = next(r for r in results if r["name"] == "Institutional_Leader")
    coauthor = next(r for r in results if r["name"] == "CoAuthor_Specialist")
    print(f"   Institutional leader (no bills, committee=10→5): {institutional['score']:.1f}")
    print(f"   Co-author specialist (weighted adopted): {coauthor['score']:.1f}")
    print(f"   Co-author gets LEI credit: {'YES' if coauthor['lei'] > 0 else 'NO'}")

    print(f"\n5. WEIGHT DISTRIBUTION:")
    print(f"   ПЯ weight: {WEIGHTS['py']*100:.0f}%")
    print(f"   ПДА weight: {WEIGHTS['pda']*100:.0f}%")
    print(f"   Quality weight: {WEIGHTS['quality']*100:.0f}%")
    print(f"   Committee weight: {WEIGHTS['committee']*100:.0f}%")
    print(f"   Requests weight: {WEIGHTS['requests']*100:.0f}%")


if __name__ == "__main__":
    main()
