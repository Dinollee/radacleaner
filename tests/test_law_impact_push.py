"""Тести «Що зміниться для громадянина» у Telegram: повний текст, без обрізання, розбиття по лімітах."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor import DASHBOARD_URL, _format_law_impact_messages
from src.telegram_notifier import TG_LIMIT, chunk_blocks, format_citizen_impact


def _impact(changes=None, headline="Закон встановлює нові штрафи", affects=True):
    if changes is None:
        changes = [
            {"who": "власники тварин", "before": "штраф 850 грн", "after": "штраф від 5000 до 17000 грн"},
            {"who": "платники податків", "before": "нічого", "after": "кошти йдуть на притулки"},
        ]
    return {"affects_citizens": affects, "headline": headline, "changes": changes, "no_impact_reason": None}


_INFO = {"bill_number": "12345", "title": "Про захист тварин",
         "url": "https://itd.rada.gov.ua/billinfo/Bills/Card/?id=12345"}


class TestFormatCitizenImpact:
    def test_headline_and_numbered_changes_with_было_стане(self):
        blocks = format_citizen_impact(_impact())
        text = "\n".join(blocks)
        assert "👥 <b>Що зміниться для громадянина</b>" in text
        assert "Закон встановлює нові штрафи" in text
        assert '<font color="#888888"><b>Було:</b> штраф 850 грн</font>' in text
        assert '<font color="#4CAF50"><b>Стане:</b> штраф від 5000 до 17000 грн</font>' in text
        assert "<b>1. власники тварин</b>\n" in text

    def test_long_text_is_never_truncated(self):
        long_before = "Дуже довгий текст до змін " * 15  # ~375 символів
        long_after = "Дуже довгий текст після змін " * 15
        text = "\n".join(format_citizen_impact(_impact(changes=[
            {"who": "усі", "before": long_before, "after": long_after}])))
        assert long_before.strip() in text
        assert long_after.strip() in text
        assert "..." not in text

    def test_all_changes_present_no_cap(self):
        many = [{"who": f"хто {i}", "before": f"до {i}", "after": f"після {i}"} for i in range(12)]
        text = "\n".join(format_citizen_impact(_impact(changes=many)))
        for i in range(12):
            assert f"<b>{i + 1}. хто {i}</b>" in text

    def test_html_is_escaped(self):
        text = "\n".join(format_citizen_impact(_impact(changes=[
            {"who": "<script>", "before": "a & b", "after": "x < y"}])))
        assert "&lt;script&gt;" in text and "a &amp; b" in text and "x &lt; y" in text
        assert "<script>" not in text

    def test_no_impact_shows_reason(self):
        imp = {"affects_citizens": False, "headline": None, "changes": [],
               "no_impact_reason": "Процедурний законопроєкт"}
        blocks = format_citizen_impact(imp)
        assert len(blocks) == 1 and "Не впливає на громадян" in blocks[0]

    def test_none_or_garbage_returns_empty(self):
        assert format_citizen_impact(None) == []
        assert format_citizen_impact({}) == []
        assert format_citizen_impact("not a dict") == []


class TestChunkBlocks:
    def test_small_fits_one_message(self):
        assert chunk_blocks(["a", "b", "c"]) == ["a\n\nb\n\nc"]

    def test_splits_on_block_boundaries_and_keeps_all_text(self):
        blocks = [f"блок {i} " + "x" * 900 for i in range(10)]
        msgs = chunk_blocks(blocks)
        assert len(msgs) > 1
        assert all(len(m) <= TG_LIMIT for m in msgs)
        joined = "\n\n".join(msgs)
        for b in blocks:
            assert b in joined  # жоден блок не розрізаний і не втрачений

    def test_empty(self):
        assert chunk_blocks([]) == []


class TestLawImpactMessages:
    def test_header_has_bill_link_and_full_title(self):
        long_title = "Проєкт Закону про внесення змін до Закону України " * 3
        msgs = _format_law_impact_messages({**_INFO, "title": long_title}, _impact())
        assert "📜 <b>ЗАКОН ПІДПИСАНО!</b>" in msgs[0]
        assert "#12345" in msgs[0]
        assert long_title.strip() in msgs[0]  # заголовок не обрізаний

    def test_dashboard_footer_present(self):
        msgs = _format_law_impact_messages(_INFO, _impact())
        assert f"{DASHBOARD_URL}/overview" in msgs[-1]

    def test_every_message_within_limit_for_big_law(self):
        many = [{"who": f"група {i}", "before": "б" * 300, "after": "а" * 300} for i in range(30)]
        msgs = _format_law_impact_messages(_INFO, _impact(changes=many))
        assert len(msgs) > 1
        assert all(len(m) <= TG_LIMIT for m in msgs)
        joined = "\n".join(msgs)
        assert "<b>30. група 29</b>" in joined  # останній пункт не втрачено

    def test_no_bill_url_no_anchor_in_header(self):
        msgs = _format_law_impact_messages({**_INFO, "url": ""}, _impact())
        assert "#12345" in msgs[0] and "<a href=\"" not in msgs[0].split("💡")[0]
