"""Тести детермінованого форматування дайджестів (без мережі/БД)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weekly_digest import fmt_date, format_weekly


class TestFmtDate:
    def test_iso(self):
        assert fmt_date('2026-08-21') == '21.08.2026'

    def test_with_time(self):
        assert fmt_date('2026-08-21 14:30:00') == '21.08.2026'

    def test_garbage_passthrough(self):
        assert fmt_date('не дата') == 'не дата'


def _sample_data():
    return {
        'new_bills': 5,
        'status_changes': 3,
        'analyzed_week': 12,
        'total_bills': 15416,
        'analyzed_total': 9544,
        'risky': [
            {'bill_number': '15525', 'title': 'Про відкликання', 'stage': 5,
             'current_status': 'Відхилено', 'registration_date': '2026-08-19'},
            {'bill_number': '15529', 'title': 'Про інтеграцію', 'stage': 2,
             'current_status': 'Опрацьовується', 'registration_date': '2026-08-19'},
        ],
        'top_mps': [
            {'name': 'Тестова Т.Т.', 'faction': 'ФРАКЦІЯ', 'kpi_v12_score': 67.0},
        ],
    }


class TestFormatWeekly:
    def test_contains_header_and_sections(self):
        text = format_weekly(_sample_data())
        assert 'Щотижневий огляд ВРУ' in text
        assert '📊 ТИЖДЕНЬ:' in text
        assert '📢 УВАГА' in text
        assert '🏆 ТОП-5 ІЕД:' in text
        assert 'Дані: rada.gov.ua' in text

    def test_rejected_bill_no_stage_5_of_4(self):
        """Регресія: відхилений закон не має показувати «Стадія 5/4»."""
        text = format_weekly(_sample_data())
        assert 'Стадія 5/4' not in text
        assert 'Відхилено · 19.08.2026' in text

    def test_active_bill_shows_stage(self):
        text = format_weekly(_sample_data())
        assert 'Стадія 2/4 · Опрацьовується · 19.08.2026' in text

    def test_empty_data_no_crash(self):
        empty = {'new_bills': 0, 'status_changes': 0, 'analyzed_week': 0,
                 'total_bills': 0, 'analyzed_total': 0, 'risky': [], 'top_mps': []}
        text = format_weekly(empty)
        assert 'Щотижневий огляд' in text

    def test_length_limit(self):
        d = _sample_data()
        d['risky'] = [dict(d['risky'][0], title='Дуже довга назва ' * 30) for _ in range(5)]
        assert len(format_weekly(d)) <= 3800
