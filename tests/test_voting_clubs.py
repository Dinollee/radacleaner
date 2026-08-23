"""Тест pure-функції agreement_matrix (calc_voting_clubs)."""
import numpy as np
import pytest

from calc_voting_clubs import agreement_matrix


def test_agreement_matrix():
    # рядки — депутати, стовпці — голосування, -1 = немає позиції
    m = np.array([
        [1, 1, 2, -1],   # A
        [1, 2, 2, 1],    # B
        [1, 1, 2, 3],    # C
    ], dtype=np.int8)
    agree, common = agreement_matrix(m)
    assert int(common[0, 1]) == 3 and int(agree[0, 1]) == 2      # A-B: 2 з 3
    assert int(common[0, 2]) == 3 and int(agree[0, 2]) == 3      # A-C: повний збіг
    assert int(common[1, 2]) == 4 and int(agree[1, 2]) == 2      # B-C: 2 з 4
    assert int(agree[0, 0]) == 3                                  # діагональ


def test_absent_excluded():
    m = np.array([
        [1, -1],
        [1, -1],
        [5, 2],   # відсутній (5) не рахується позицією
    ], dtype=np.int8)
    agree, common = agreement_matrix(m)
    assert int(common[0, 2]) == 0 and int(agree[0, 2]) == 0
