"""Тести src/aliases.resolve_name_candidates / alias_surnames (чиста логіка, без БД)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.aliases import alias_surnames, resolve_name_candidates


class FakeCur:
    """Курсор-заглушка: execute запам'ятовує запит, fetchall віддає задані рядки."""

    def __init__(self, rows):
        self.rows = rows
        self.executed = None

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchall(self):
        return self.rows


def test_current_name_returns_old_form():
    cur = FakeCur([("Рябуха Т.В.", "Скрипка Т.В.")])
    assert resolve_name_candidates(cur, "Скрипка Т.В.") == ["Скрипка Т.В.", "Рябуха Т.В."]


def test_bidirectional_lookup_from_old_name():
    cur = FakeCur([("Рябуха Т.В.", "Скрипка Т.В.")])
    assert resolve_name_candidates(cur, "Рябуха Т.В.") == ["Рябуха Т.В.", "Скрипка Т.В."]


def test_merges_multiple_pairs_without_duplicates():
    # подвійна зміна прізвища: два рядки на одного депутата
    cur = FakeCur([("А B.", "Б В."), ("Б В.", "Г Д.")])
    out = resolve_name_candidates(cur, "Б В.")
    assert out == ["Б В.", "А B.", "Г Д."]


def test_unknown_name_returns_single_candidate():
    assert resolve_name_candidates(FakeCur([]), "Хтось І.П.") == ["Хтось І.П."]


def test_query_filters_by_both_directions():
    cur = FakeCur([])
    resolve_name_candidates(cur, "Скрипка Т.В.")
    sql, params = cur.executed
    assert "new_name = %s" in sql and "old_name = %s" in sql
    assert params == ("Скрипка Т.В.", "Скрипка Т.В.")


def test_alias_surnames_dedupes_and_sorts():
    assert alias_surnames([
        "Мезенцева-Федоренко М.С.", "Мезенцева М.С.", "Мезенцева-Федоренко М.С.",
    ]) == ["Мезенцева", "Мезенцева-Федоренко"]


def test_alias_surnames_keeps_primary_first_in_caller_order():
    # у sync_nazk основне прізвище йде першим, решта — з alias_surnames
    surnames = ["Мезенцева"] + [s for s in alias_surnames(
        resolve_name_candidates(FakeCur([("Мезенцева-Федоренко М.С.", "Мезенцева М.С.")]),
                                "Мезенцева М.С."))
        if s != "Мезенцева"]
    assert surnames == ["Мезенцева", "Мезенцева-Федоренко"]


def test_alias_surnames_skips_empty_entries():
    assert alias_surnames(["Мезенцева М.С.", "", None]) == ["Мезенцева"]
