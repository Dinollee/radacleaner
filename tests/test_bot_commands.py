"""Тести форматтерів команд бота v2 (/attacks, /fakes)."""
from telegram_bot import format_attacks, format_fakes, normalize_bill_number


def test_normalize_bill_number():
    """Нормалізація номера закону: видаляє суфікси /П, -1, тощо."""
    assert normalize_bill_number("14191") == "14191"
    assert normalize_bill_number("14191/П") == "14191"
    assert normalize_bill_number("14191/П1") == "14191"
    assert normalize_bill_number("14191-2") == "14191"
    assert normalize_bill_number("14191-10") == "14191"
    assert normalize_bill_number("15579") == "15579"
    assert normalize_bill_number("15579/П") == "15579"


def test_bill_search_returns_correct_law():
    """Search for bill 14191 should return 14191, NOT 14110-2."""
    from telegram_bot import db_query

    # Exact match
    rows = db_query(
        'SELECT b.bill_number FROM bills b WHERE b.bill_number = %s LIMIT 1',
        ['14191']
    )
    assert len(rows) == 1
    assert rows[0]['bill_number'] == '14191'
    assert rows[0]['bill_number'] != '14110-2'

    # Normalized match (14191/П -> 14191)
    normalized = normalize_bill_number("14191/П")
    rows = db_query(
        'SELECT b.bill_number FROM bills b WHERE b.bill_number = %s OR b.bill_number ILIKE %s ORDER BY b.bill_number LIMIT 1',
        [normalized, f'{normalized}%']
    )
    assert len(rows) == 1
    assert rows[0]['bill_number'] == '14191'


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
