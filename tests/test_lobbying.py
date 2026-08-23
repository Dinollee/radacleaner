"""Тест витягу номера законопроєкту з предмета лобіювання (sync_lobbying_registry)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sync_lobbying_registry import extract_bill_number

VALID = {"14245", "14387", "10249"}


def test_standard_number():
    assert extract_bill_number("Проект Закону №14245 «Про внесення змін»", VALID) == "14245"


def test_number_with_space_and_suffix():
    assert extract_bill_number("проєкт закону № 14387-IX від 12.05.2026", VALID) == "14387"


def test_unknown_number_ignored():
    assert extract_bill_number("Постанова КМУ №1802 від 26 грудня 2025", VALID) is None


def test_no_number():
    assert extract_bill_number("Регламент Верховної Ради", VALID) is None
