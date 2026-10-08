"""Тести формули ІЕД v12 (calc_kpi_v12.py) — чисті функції, без БД."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calc_kpi_v12 import clamp, calc_c1, calc_c2, calc_c3, calc_c4, calc_c5, calc_c6, compute_kpi


def make_mp(**over):
    d = dict(
        py=50, pda=50, vkp=50, quality=3, risk=2, docs=500, authorship=0.3,
        analyzed=10, adoption=50, committee=3, req_resp=10, req_count=20, eu_score=20,
    )
    d.update(over)
    return d


class TestClamp:
    def test_within_bounds(self):
        assert clamp(0.5) == 0.5

    def test_below(self):
        assert clamp(-1) == 0.0

    def test_above(self):
        assert clamp(2) == 1.0


class TestC1Discipline:
    def test_insufficient_data(self):
        assert calc_c1(5, 50, 50) == 0.0

    def test_perfect(self):
        assert calc_c1(100, 100, 100) == 1.0

    def test_weights(self):
        # 0.5*py + 0.3*pda + 0.2*vkp; py>=10 інакше ранній вихід 0
        assert abs(calc_c1(20, 0, 0) - 0.1) < 1e-9          # лише py
        assert abs(calc_c1(10, 100, 0) - 0.35) < 1e-9       # py(0.05) + pda(0.3)
        assert abs(calc_c1(10, 0, 100) - 0.25) < 1e-9       # py(0.05) + vkp(0.2)


class TestC2Legislation:
    def test_no_data_neutral(self):
        assert calc_c2(None, None, None, None, has_data=False) == 0.5

    def test_best_values(self):
        assert calc_c2(5, 0, 2000, 0.5, has_data=True) == 1.0

    def test_zero_means_no_data_neutral(self):
        # quality=0/docs=0/authorship=0 трактується як «немає даних» → нейтраль 0.5
        # risk=5 → r=0. Разом: 0.5*0.3 + 0*0.3 + 0.5*0.2 + 0.5*0.2 = 0.35
        assert abs(calc_c2(0, 5, 0, 0, has_data=True) - 0.35) < 1e-9


class TestC3Efficiency:
    def test_low_volume_neutral(self):
        assert calc_c3(100, 2) == 0.5

    def test_perfect(self):
        assert calc_c3(100, 10) == 1.0

    def test_volume_clamped(self):
        # adoption=0, volume>10 clamps to 1 -> 0.3
        assert abs(calc_c3(0, 50) - 0.3) < 1e-9


class TestC4Committee:
    """Публічна монотонна шкала C4_LADDER: будь-яка роль >= немає ролі."""

    def test_no_role_is_40(self):
        assert calc_c4(0) == 0.40

    def test_member(self):
        assert calc_c4(3) == 0.55

    def test_secretary_subhead(self):
        assert calc_c4(5) == 0.70

    def test_vice_chair(self):
        assert calc_c4(7) == 0.85

    def test_chair(self):
        assert calc_c4(10) == 1.0

    def test_monotonic_ladder(self):
        scores = [calc_c4(s) for s in [0, 3, 5, 7, 10]]
        assert scores == sorted(scores)
        assert len(set(scores)) == 5

    def test_unknown_score_falls_to_no_role(self):
        assert calc_c4(99) == 0.40


class TestC5Requests:
    def test_no_responses_zero(self):
        assert calc_c5(0, 10) == 0.0

    def test_all_responded_high_volume(self):
        assert calc_c5(20, 20) == 1.0

    def test_rate_factor(self):
        # base = 10/20 = 0.5, rate = 0.5 -> 0.5 * (0.7 + 0.15) = 0.425
        assert abs(calc_c5(10, 20) - 0.425) < 1e-9

    def test_zero_request_count_no_crash(self):
        # request_count=0 → rate=0 (guard), base=5/20=0.25 → 0.25*0.7
        assert abs(calc_c5(5, 0) - 0.175) < 1e-9


class TestC6Impact:
    def test_no_data_neutral(self):
        assert calc_c6(None, None) == 0.5

    def test_best(self):
        assert abs(calc_c6(0, 35) - 1.0) < 1e-9

    def test_worst(self):
        # risk=5 → r=0; eu=0 трактується як «немає даних» → e=0.5. Разом 0*0.6 + 0.5*0.4
        assert abs(calc_c6(5, 0) - 0.2) < 1e-9


class TestComputeKpi:
    """Сборка: adoption-перерахунок + середнє 6 компонентів (compute_kpi)."""

    def test_score_is_mean_of_six_components(self):
        r = compute_kpi(make_mp(), 5, 2)
        expected = (r['c1'] + r['c2'] + r['c3'] + r['c4'] + r['c5'] + r['c6']) / 6
        assert abs(r['kpi_v12'] - expected) < 1e-12
        assert 0.0 <= r['kpi_v12'] <= 1.0

    def test_adoption_recalculated_from_primary_bills(self):
        # total=10, adopted=10 → adoption=100% (поле d['adoption']=50 ігнорується)
        r = compute_kpi(make_mp(), 10, 10)
        assert abs(r['c3'] - 1.0) < 1e-9
        assert r['adopted_primary'] == 10

    def test_no_primary_falls_back_then_c3_neutral(self):
        # total_primary=0 → adoption з поля, але c3 нейтральний (<3 первинних)
        r = compute_kpi(make_mp(adoption=100), 0, 0)
        assert r['c3'] == 0.5
        assert r['total_primary'] == 0

    def test_no_analysis_neutral_c2(self):
        r = compute_kpi(make_mp(analyzed=0), 5, 1)
        assert r['c2'] == 0.5

    def test_extremes_stay_in_range(self):
        best = compute_kpi(make_mp(py=100, pda=100, vkp=100, quality=5, risk=0,
                                   docs=2000, authorship=0.5, analyzed=10,
                                   committee=10, req_resp=20, req_count=20, eu_score=35),
                           10, 10)
        worst = compute_kpi(make_mp(py=10, pda=0, vkp=0, quality=0, risk=5,
                                    docs=0, authorship=0, analyzed=10,
                                    committee=0, req_resp=0, req_count=0, eu_score=0),
                            3, 0)
        assert 0.0 <= worst['kpi_v12'] < best['kpi_v12'] <= 1.0
