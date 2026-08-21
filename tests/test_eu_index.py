"""Тести EU Integration Index v1: compute_index (calc_harmonization) + detect_cluster_opening (sync_eu_tracker)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from calc_harmonization import compute_index
from sync_eu_tracker import detect_cluster_opening


# --- compute_index ---

def test_all_not_opened_index_is_half_legislation():
    r = compute_index(["not_opened"] * 6, 31.1)
    assert r["negotiation"] == 0
    assert r["index"] == round(31.1 / 2, 1)


def test_two_of_six_opened_negotiation_16_7():
    statuses = ["opened", "not_opened", "not_opened", "not_opened", "not_opened", "opened"]
    r = compute_index(statuses, 0)
    assert r["negotiation"] == 16.7


def test_provisionally_closed_scores_100():
    r = compute_index(["provisionally_closed"] * 6, 0)
    assert r["negotiation"] == 100
    assert r["index"] == 50


def test_rounding_to_one_decimal():
    r = compute_index(["opened"] * 6, 30.0)
    assert r == {"negotiation": 50.0, "legislation": 30.0, "index": 40.0}


def test_live_scenario_v1_seed():
    """Поточний стан БД: C1+C6 opened, legislation=31.1 → index=23.9."""
    statuses = ["opened"] + ["not_opened"] * 4 + ["opened"]
    r = compute_index(statuses, 31.1)
    assert r == {"negotiation": 16.7, "legislation": 31.1, "index": 23.9}


def test_empty_statuses_fallback():
    r = compute_index([], 20.0)
    assert r["negotiation"] == 0
    assert r["index"] == 10.0


# --- detect_cluster_opening ---

def test_detect_c1_fundamentals():
    assert detect_cluster_opening(
        "EU and Ukraine open first accession negotiations cluster on fundamentals") == 1


def test_detect_c6_external_relations():
    assert detect_cluster_opening(
        "Enlargement: EU opens accession negotiations with Ukraine on external relations policies") == 6


def test_detect_by_cluster_number():
    assert detect_cluster_opening("EU opens accession negotiations: Cluster 3 with Ukraine") == 3


def test_detect_with_summary_context():
    assert detect_cluster_opening(
        "Enlargement package published",
        "The EU opened accession negotiations with Ukraine on cluster 2 internal market") == 2


def test_close_news_not_detected():
    assert detect_cluster_opening(
        "Enlargement: EU and Montenegro close accession negotiations on competition policy and customs union") is None


def test_irrelevant_news_not_detected():
    assert detect_cluster_opening(
        "Ukraine Facility unlocks investment opportunities for dual-use industries") is None


def test_cluster_mention_without_opening_not_detected():
    assert detect_cluster_opening("Screening report for cluster 1 fundamentals published") is None
