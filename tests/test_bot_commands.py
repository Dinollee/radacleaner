"""Тести форматтерів команд бота v2 (/attacks, /fakes)."""
from telegram_bot import format_attacks, format_fakes


def test_format_attacks_empty():
    t = format_attacks([])
    assert "не зафіксовано" in t and "добре" in t


def test_format_attacks_with_data():
    from datetime import datetime
    rows = [{
        "label": "вибори, спостерігачі, рф",
        "posts_count": 12, "channels_count": 4,
        "debunk_url": "https://cpd.gov.ua/x",
        "detected_at": datetime(2026, 8, 21, 18, 51),
    }]
    t = format_attacks(rows)
    assert "🚨" in t and "12 постів × 4 каналів" in t
    assert "21.08 18:51" in t
    assert "https://cpd.gov.ua/x" in t
    assert "вердикт — за фактчекерами" in t


def test_format_fakes_none():
    assert "розборів немає" in format_fakes(None)
    assert "розборів немає" in format_fakes({})


def test_format_fakes_top10_and_source():
    fakes = [{"one_line": f"перевірка {i}", "source": "ЦПД", "url": f"https://x/{i}"}
             for i in range(15)]
    t = format_fakes({"fakes": fakes})
    assert t.count("повний розбір") == 10          # тільки ТОП-10
    assert "[ЦПД]" in t and "перевірка 0" in t
    assert "перевірка 14" not in t                  # зайві обрізані


def test_format_fakes_missing_fields():
    t = format_fakes({"fakes": [{"title": "тільки заголовок"}]})
    assert "тільки заголовок" in t and "повний розбір" not in t
