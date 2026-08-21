"""Тести sync_votes: ваги голосувань та маппінг статусів (без мережі/БД)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync_votes import STATUS_IDS, STATUS_MAP, FACTION_MAP, get_vote_weight


class TestVoteWeight:
    def test_second_reading(self):
        assert get_vote_weight("Проєкт закону (друге читання)") == 3.0

    def test_adoption_in_whole(self):
        assert get_vote_weight("Закон прийняття в цілому") == 3.0

    def test_first_reading(self):
        assert get_vote_weight("за основу проєкту закону") == 2.0

    def test_procedural_default(self):
        assert get_vote_weight("Про порядок денний") == 1.0

    def test_empty_title(self):
        assert get_vote_weight("") == 1.0
        assert get_vote_weight(None) == 1.0


class TestStatusIds:
    """Регресія 16.07: STATUS_IDS[] має покривати всі ключі STATUS_MAP.values()."""

    def test_all_statuses_have_ids(self):
        for status in STATUS_MAP.values():
            assert status in STATUS_IDS, f"{status} відсутній у STATUS_IDS"

    def test_ids_are_ints_1_to_5(self):
        assert set(STATUS_IDS.values()) == {1, 2, 3, 4, 5}

    def test_faction_map_complete(self):
        assert len(FACTION_MAP) == 9
