"""Тести парсера списку та матчинга декларацій НАЗК (sync_nazk_declarations)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sync_nazk_declarations import (
    is_deputy_post,
    list_url,
    match_deputy,
    max_page,
    parse_list,
    pick_newest,
)
from enrich_company_sectors import parse_items

SAMPLE = """<article class="doc-icon-1-1">
<div class="fio"><a href="/documents/0650c718-7273-47fb-b2f4-aa522e3da4b8">ГЕТМАНЦЕВ ДАНИЛО ОЛЕКСАНДРОВИЧ</a></div>
<div class="row-1"><div class="info-2"><span class="info-1">Дата та час подання:</span>05.06.2020 18:09</div></div>
<div class="row-1"><div class="type-info">Виправлена декларація (Щорічна)</div></div>
<div class="row-1"><div class="info-2"><span class="info-1">Посада:</span>народний депутат України</div></div>
</article>
<article class="doc-icon-3-1">
<div class="fio"><a href="/documents/b564815f-a3f9-43e3-b445-f65495a1e6e7">ГЕТМАНЦЕВА ОЛЕНА ДМИТРІВНА</a></div>
<div class="row-1"><div class="info-2"><span class="info-1">Дата та час подання:</span>01.02.2021 10:00</div></div>
<div class="row-1"><div class="info-2"><span class="info-1">Посада:</span>майстер</div></div>
</article>"""


def test_parse_list():
    rows = parse_list(SAMPLE)
    assert len(rows) == 2
    assert rows[0]["uuid"] == "0650c718-7273-47fb-b2f4-aa522e3da4b8"
    assert rows[0]["submitted"] == "05.06.2020 18:09"
    assert rows[0]["post"] == "народний депутат України"
    assert rows[1]["post"] == "майстер"


def test_match_deputy():
    assert match_deputy("ГЕТМАНЦЕВ ДАНИЛО ОЛЕКСАНДРОВИЧ", "Гетманцев", "Д")
    assert not match_deputy("ГЕТМАНЦЕВА ОЛЕНА ДМИТРІВНА", "Гетманцев", "Д")
    assert not match_deputy("ГЕТМАНЦЕВ ДАНИЛО", "Гетманцев", "О")


def test_match_deputy_patronymic_filters_namesake():
    # Грищенко Т.М. (нардеп) vs Грищенко Тетяна Василівна (райрада)
    assert match_deputy("ГРИЩЕНКО ТЕТЯНА МИКОЛАЇВНА", "Грищенко", "Т", "М")
    assert not match_deputy("ГРИЩЕНКО ТЕТЯНА ВАСИЛІВНА", "Грищенко", "Т", "М")


def test_is_deputy_post():
    # формат посад 2019+: комітети ВРУ, жіночі форми, «Депутат Верховної Ради»
    assert is_deputy_post("Народний депутат України")
    assert is_deputy_post("Народна депутатка України")
    assert is_deputy_post("Член Комітету Верховної Ради України з питань прав")
    assert is_deputy_post("Голова підкомітету")
    assert is_deputy_post("Заступниця Голови Верховної Ради України")
    assert not is_deputy_post("учитель української мови та літератури")
    assert not is_deputy_post("Головний бухгалтер")
    assert not is_deputy_post("")


def test_list_url_filters_and_pagination():
    url = list_url("Ткаченко")
    assert url.startswith("https://public.nazk.gov.ua/documents/list?q=%D0%A2%D0%BA")
    assert "responsible_position%5B%5D=52" in url
    assert "document_type%5B%5D=1" in url and "document_type%5B%5D=3" in url
    assert "&page=" not in url
    assert list_url("Шевченко", 2).endswith("&page=2")


def test_max_page():
    html = '<li><a href="?page=1" data-page="1">1</a></li>' \
           '<a href="?page=304" class="page-link" data-page="304">»</a>'
    assert max_page(html) == 304
    assert max_page("<article>без пагінції</article>") == 1


def test_pick_newest():
    rows = [
        {"uuid": "old", "submitted": "05.06.2020 18:09"},
        {"uuid": "new", "submitted": "01.02.2024 10:00"},
        {"uuid": "bad-date", "submitted": ""},
    ]
    assert pick_newest(rows)["uuid"] == "new"


def test_parse_classify_items():
    raw = '{"items": [{"name": "Інтер Солар Енерджі", "sector": "Енергетика"}, ' \
          '{"name": "Хтось", "sector": "Вигадана галузь"}, {"name": ""}]}'
    out = parse_items(raw)
    assert out == {"ІНТЕР СОЛАР ЕНЕРДЖІ": "Енергетика"}
    assert parse_items("без json") == {}
