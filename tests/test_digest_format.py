"""Тести детермінованого форматування дайджестів (без мережі/БД)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weekly_digest import DASHBOARD_URL, fmt_date, format_weekly


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
        'status_breakdown': [('Знято з розгляду', 12), ('Опрацьовується в комітеті', 7)],
        'signed': [
            {'bill_number': '15579', 'title': 'Про внесення змін до Податкового кодексу',
             'act_number': '1557-ІХ', 'act_date': '2026-10-05'},
        ],
        'rejected': [
            {'bill_number': '15525', 'title': 'Про відкликання', 'stage': 5,
             'current_status': 'Відхилено', 'risk_score': 4},
        ],
        'risky': [
            {'bill_number': '15529', 'title': 'Про інтеграцію', 'stage': 2,
             'risk_score': 4, 'risk_level': 'high', 'toxicity': 0.8,
             'json_data': json.dumps({'risk_categories': [{'category': 'Корупція'}],
                                      'summary': 'Підвищує митні збори для ФОП.'})},
        ],
        'total_bills': 15416,
        'analyzed_total': 9544,
    }


class TestFormatWeekly:
    def test_contains_header_and_footer(self):
        text = format_weekly(_sample_data(), [])
        assert 'Щотижневий огляд ВРУ' in text
        assert '📈 Загалом:' in text
        assert '5 нових · 9544/15416 проаналізовано' in text
        assert f"{DASHBOARD_URL}/overview" in text

    def test_summary_bullets_section(self):
        text = format_weekly(_sample_data(), ['Прийнято бюджет на 2027 рік'])
        assert '📝 ГОЛОВНЕ:' in text
        assert 'Прийнято бюджет на 2027 рік' in text

    def test_empty_summary_skips_main_but_renders_groups(self):
        """LLM fallback: без буллетів ГОЛОВНЕ пропускається, пуш усе одно йде."""
        text = format_weekly(_sample_data(), [])
        assert 'ГОЛОВНЕ' not in text
        assert '📜 Підписані закони:' in text

    def test_signed_full_title_and_link(self):
        text = format_weekly(_sample_data(), [])
        assert '№15579 (акт 1557-ІХ) — Про внесення змін до Податкового кодексу' in text
        assert '05.10.2026' in text
        assert 'картка закону' in text

    def test_risky_categories_and_summary(self):
        text = format_weekly(_sample_data(), [])
        assert '⚠️ Ризиковані' in text
        assert '№15529 (risk 4/5)' in text
        assert 'Корупція' in text
        assert 'Підвищує митні збори для ФОП.' in text
        assert 'itd.rada.gov.ua' in text

    def test_status_breakdown_grouped(self):
        text = format_weekly(_sample_data(), [])
        assert '📊 Зміни статусів:' in text
        assert 'Знято з розгляду: 12' in text

    def test_empty_data_no_crash(self):
        empty = {'new_bills': 0, 'status_breakdown': [], 'signed': [],
                 'rejected': [], 'risky': [], 'total_bills': 0, 'analyzed_total': 0}
        text = format_weekly(empty, [])
        assert 'Щотижневий огляд' in text
        assert '0 нових' in text

    def test_no_stage_5_of_4(self):
        """Регресія: відхилений закон не має показувати «Стадія 5/4»."""
        text = format_weekly(_sample_data(), [])
        assert 'Стадія 5/4' not in text

    def test_length_limit(self):
        d = _sample_data()
        d['risky'] = [dict(d['risky'][0], title='Дуже довга назва ' * 30) for _ in range(5)]
        d['signed'] = [dict(d['signed'][0], title='Дуже довга назва ' * 30) for _ in range(5)]
        assert len(format_weekly(d, [])) <= 3800

    def test_long_risky_title_truncated(self):
        d = _sample_data()
        d['risky'][0]['title'] = 'Дуже довга назва ' * 30
        text = format_weekly(d, [])
        assert '...' in text
        # обрізання risky: >150 -> 147 символів + '...'
        line = [l for l in text.split('\n') if '№15529' in l][0]
        assert len(line.split('— ', 1)[1]) <= 150