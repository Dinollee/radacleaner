"""Тести формули ІЕД v12 (calc_kpi_v12.py) — чисті функції, без БД."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calc_kpi_v12 import clamp, calc_c1, calc_c2, calc_c3, calc_c4, calc_c5, calc_c6


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


class TestC6Impact:
    def test_no_data_neutral(self):
        assert calc_c6(None, None) == 0.5

    def test_best(self):
        assert abs(calc_c6(0, 35) - 1.0) < 1e-9

    def test_worst(self):
        # risk=5 → r=0; eu=0 трактується як «немає даних» → e=0.5. Разом 0*0.6 + 0.5*0.4
        assert abs(calc_c6(5, 0) - 0.2) < 1e-9
