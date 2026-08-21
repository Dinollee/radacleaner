"""Тесты label_narratives.py: чистые функции merge_labels/rank_fakes (LLM-мусор → fallback)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from label_narratives import merge_labels, rank_fakes

ROWS = [{"id": 1, "size": 12, "channels": 5, "sample_title": "s1",
         "tokens": ["мобілізація", "термінова"]},
        {"id": 2, "size": 9, "channels": 4, "sample_title": "s2", "tokens": []}]


def test_merge_labels_ok():
    data = [{"id": 1, "label": "Хвилі мобілізації", "category": "мобілізація"},
            {"id": 2, "label": "Про виборчі реформи", "category": "вибори"}]
    out = merge_labels(ROWS, data)
    assert out[0]["label"] == "Хвилі мобілізації" and out[0]["category"] == "мобілізація"
    assert out[1]["category"] == "вибори"


def test_merge_labels_garbage_falls_back_to_tokens_and_inshe():
    for garbage in (None, "не json", {"id": 1}, [{"id": 99, "label": "x"}], [{"broken":
                   "]}"}]):
        out = merge_labels(ROWS, garbage)
        assert out[0]["label"].lower().startswith("мобілізація")  # топ-токены
        assert out[1]["label"] == "Нарратив без назви"            # токенов нет
        assert out[0]["category"] == "інше" and out[1]["category"] == "інше"


def test_merge_labels_bad_category_normalized():
    out = merge_labels(ROWS, [{"id": 1, "label": "ok", "category": "погода"}])
    assert out[0]["category"] == "інше"


FAKES = [{"title": "t1", "url": "u1", "source": "ЦПД"},
         {"title": "t2", "url": "u2", "source": "StopFake"}]


def test_rank_fakes_sorted_desc_and_clamped():
    data = [{"i": 0, "significance": 3, "one_line": "суть перша"},
            {"i": 1, "significance": 99, "one_line": "суть друга"}]
    out = rank_fakes(FAKES, data)
    assert [f["significance"] for f in out] == [10, 3]  # 99 зажат до 10
    assert out[0]["one_line"] == "суть друга"


def test_rank_fakes_garbage_keeps_order_zero_sig():
    out = rank_fakes(FAKES, None)
    assert [f["significance"] for f in out] == [0, 0]
    assert out[0]["title"] == "t1" and out[0]["one_line"] == ""
