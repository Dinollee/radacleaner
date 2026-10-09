"""Тести форматтерів команд бота v2 (/attacks, /fakes) + citizen_impact."""
from telegram_bot import format_attacks, format_fakes, format_citizen_impact


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


# --- citizen_impact formatters ---

def _impact(headline="Закон встановлює нові штрафи", changes=None, affects=True):
    if changes is None:
        changes = [
            {"who": "власники тварин", "before": "штраф 850 грн", "after": "штраф від 5000"},
            {"who": "платники", "before": "нічого", "after": "кошти на притулки"},
        ]
    return {"affects_citizens": affects, "headline": headline, "changes": changes, "no_impact_reason": None}


def test_affects_citizens_shows_headline_and_changes():
    lines = format_citizen_impact(_impact())
    assert any("👥" in l for l in lines)
    assert any("Штрафи" in l or "нові" in l for l in lines)
    assert any("До:" in l for l in lines)
    assert any("Після:" in l for l in lines)


def test_no_affect_shows_reason():
    imp = {"affects_citizens": False, "headline": None, "changes": [],
           "no_impact_reason": "Процедурний законопроєкт — не містить змін для громадян"}
    lines = format_citizen_impact(imp)
    assert any("Не впливає" in l for l in lines)
    assert any("Процедурний" in l for l in lines)


def test_none_impact_returns_empty():
    assert format_citizen_impact(None) == []
    assert format_citizen_impact({}) == []
    assert format_citizen_impact("not a dict") == []


def test_limits_to_8_changes():
    many = [{"who": f"хто {i}", "before": f"до {i}", "after": f"після {i}"} for i in range(10)]
    imp = _impact(changes=many)
    lines = format_citizen_impact(imp)
    text = "\n".join(lines)
    assert "<i>Хто: хто 0</i>" in text
    assert "<i>Хто: хто 7</i>" in text
    assert "<i>Хто: хто 8</i>" not in text
    assert "і ще 2 змін" in text


def test_long_before_after_truncated():
    imp = _impact(changes=[
        {"who": "усі", "before": "Дуже довгий текст до змін який перевищує 80 символів і має бути обрізаний",
         "after": "Дуже довгий текст після змін який також перевищує 80 символів і має бути обрізаний"},
    ])
    lines = format_citizen_impact(imp)
    text = "\n".join(lines)
    # Обрізані тексти мають "..." в кінці
    assert "..." in text
    # Але не зникають повністю
    assert "до змін" in text
    assert "після змін" in text


def test_empty_changes_no_crash():
    imp = _impact(changes=[])
    lines = format_citizen_impact(imp)
    assert len(lines) >= 1  # headline still shown


def test_no_headline_only_changes():
    imp = _impact(headline="", affects=True)
    lines = format_citizen_impact(imp)
    assert not any("👥" in l for l in lines)
    assert any("До:" in l for l in lines)
