"""Тести форматування пушів про підписані закони з citizen_impact (без мережі/БД)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor import _format_law_impact_message, DASHBOARD_URL


class TestFormatLawImpactMessage:
    """Формування окремого пуша для stage 4 законів з citizen_impact."""

    def _info(self, bn="12345", title="Про захист тварин", url="https://itd.rada.gov.ua/billinfo/Bills/Card/?id=12345"):
        return {"bill_number": bn, "title": title, "url": url}

    def _impact(self, headline="Закон встановлює нові штрафи", changes=None):
        if changes is None:
            changes = [
                {"who": "власники тварин", "before": "штраф 850 грн", "after": "штраф від 5000 до 17000 грн"},
                {"who": "платники податків", "before": "нічого", "after": "кошти йдуть на притулки"},
            ]
        return {"affects_citizens": True, "headline": headline, "changes": changes, "no_impact_reason": None}

    def test_contains_header_and_bill_info(self):
        msg = _format_law_impact_message(self._info(), self._impact())
        assert "📜 <b>ЗАКОН ПІДПИСАНО!</b>" in msg
        assert "#12345" in msg
        assert "Про захист тварин" in msg

    def test_headline_shown(self):
        msg = _format_law_impact_message(self._info(), self._impact())
        assert "👥 <b>Закон встановлює нові штрафи</b>" in msg

    def test_changes_rendered(self):
        msg = _format_law_impact_message(self._info(), self._impact())
        assert "<i>Хто: власники тварин</i>" in msg
        assert "До: штраф 850 грн" in msg
        assert "Після: штраф від 5000 до 17000 грн" in msg

    def test_dashboard_link(self):
        msg = _format_law_impact_message(self._info(), self._impact())
        assert f"{DASHBOARD_URL}/overview" in msg

    def test_no_headline_still_works(self):
        impact = {"affects_citizens": True, "headline": None, "changes": [], "no_impact_reason": None}
        msg = _format_law_impact_message(self._info(), impact)
        assert "📜 <b>ЗАКОН ПІДПИСАНО!</b>" in msg
        assert "👥" not in msg

    def test_empty_changes_no_crash(self):
        impact = {"affects_citizens": True, "headline": "Щось важливе", "changes": [], "no_impact_reason": None}
        msg = _format_law_impact_message(self._info(), impact)
        assert "Щось важливе" in msg
        assert "💡" in msg

    def test_truncates_to_4000_chars(self):
        long_title = "Дуже довга назва закону " * 20
        long_changes = [{"who": "усі громадяни України", "before": "старий стан речей який дуже довгий ",
                         "after": "новий стан речей який також дуже довгий "} for _ in range(20)]
        impact = {"affects_citizens": True, "headline": "Заголовок", "changes": long_changes, "no_impact_reason": None}
        info = {"bill_number": "99999", "title": long_title, "url": ""}
        msg = _format_law_impact_message(info, impact)
        assert len(msg) <= 4000

    def test_limits_to_8_changes(self):
        many_changes = [{"who": f"хто {i}", "before": f"до {i}", "after": f"після {i}"} for i in range(10)]
        impact = {"affects_citizens": True, "headline": "Багато змін", "changes": many_changes, "no_impact_reason": None}
        msg = _format_law_impact_message(self._info(), impact)
        # Перші 8 мають бути видимими
        assert "<i>Хто: хто 0</i>" in msg
        assert "<i>Хто: хто 7</i>" in msg
        # 9-й прихований
        assert "<i>Хто: хто 8</i>" not in msg
        assert "і ще 2 змін" in msg

    def test_url_optional(self):
        info = {"bill_number": "12345", "title": "Тест", "url": ""}
        msg = _format_law_impact_message(info, self._impact())
        assert "#12345" in msg

    def test_no_bill_anchor_when_no_url(self):
        info = {"bill_number": "12345", "title": "Тест", "url": ""}
        msg = _format_law_impact_message(info, self._impact())
        # Закон без URL — немає якірного посилання на закон (до дашборд-футера)
        assert "#12345" in msg and "<a href=" not in msg.split("💡")[0]
