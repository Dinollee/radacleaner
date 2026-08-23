"""Тест нормалізації interest_sectors (rag_engine) та парсера бекфілу."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag_engine import _normalize_interest_sectors
from backfill_interest_sectors import parse_sectors
from src.prompts import INTEREST_SECTORS


def test_normalize_guarantees_key():
    data = {}
    _normalize_interest_sectors(data)
    assert data["interest_sectors"] == []


def test_normalize_filters_unknown_and_caps():
    data = {"interest_sectors": [INTEREST_SECTORS[0], "Вигадана галузь", 123,
                                 INTEREST_SECTORS[1], INTEREST_SECTORS[2], INTEREST_SECTORS[3]]}
    _normalize_interest_sectors(data)
    assert data["interest_sectors"] == [INTEREST_SECTORS[0], INTEREST_SECTORS[1], INTEREST_SECTORS[2]]


def test_normalize_non_list():
    data = {"interest_sectors": "Енергетика"}
    _normalize_interest_sectors(data)
    assert data["interest_sectors"] == []


def test_parse_sectors_from_raw():
    assert parse_sectors('текст {"interest_sectors": ["Агропром"]} кінець') == ["Агропром"]
    assert parse_sectors('без json') == []
    assert parse_sectors('{"interest_sectors": null}') == []
