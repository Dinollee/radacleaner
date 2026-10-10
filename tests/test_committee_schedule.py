"""Тест парсера графіку комітетів (sync_committee_schedule).

Регресія: параметр функції extract_committees_from_html звався `html` і затеняв
`import html` → `html.unescape` падав з AttributeError, юніт мовчки падав щодня.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sync_committee_schedule import extract_committees_from_html

HTML = """<html><body>
<p>КОМІТЕТ З ПИТАНЬ ФІНАНСІВ, ПОДАТКОВОЇ ТА МИТНОЇ ПОЛІТИКИ&nbsp;&amp; БЮДЖЕТУ</p>
<p>Засідання о 13.30 08 жовтня 2026</p>
</body></html>"""


def test_unescapes_entities_and_extracts_time_date():
    meetings = extract_committees_from_html(HTML, "2026-10-08")
    assert len(meetings) == 1
    assert "&amp;" not in meetings[0]["name"]
    assert "&" in meetings[0]["name"]  # &amp; розкодовано, модуль html не затенено
    assert meetings[0]["time"] == "13:30"
    assert meetings[0]["date"] == "2026-10-08"
