"""Тести детермінованого тайбрейкера PROCEDURAL_TITLE_RE (без мережі/БД)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag_engine import PROCEDURAL_TITLE_RE


def match(title):
    return bool(PROCEDURAL_TITLE_RE.search(title))


class TestProceduralTitles:
    def test_agenda_variants(self):
        assert match("Проєкт Постанови про внесення змін до порядку денного п'ятнадцятої сесії ВРУ")
        assert match("Проєкт Постанови про порядок денний шістнадцятої сесії Верховної Ради")
        assert match("Проєкт Постанови про включення до порядку денного питання")

    def test_deputy_request(self):
        assert match("Проєкт Постанови про направлення депутатського запиту групи народних депутатів")
        assert match("Проєкт Постанови про направлення депутатського запиту Президенту")

    def test_reglament(self):
        assert match("Проєкт Закону про внесення змін до Регламенту Верховної Ради України")

    def test_substantive_laws_do_not_match(self):
        # Регресія А-випадків: предметні закони НЕ мають форситись у процедурні
        assert not match('Проєкт Закону про внесення зміни до статті 16-3 Закону України "Про соціальний захист"')
        assert not match("Проєкт Закону про внесення змін до Закону України \"Про державне регулювання ринків капіталу\"")
        assert not match("Проєкт Закону про вихід з Угоди про принципи справляння непрямих податків")
        assert not match('Проєкт Закону про внесення змін до Кримінального процесуального кодексу України')

    def test_case_insensitive(self):
        assert match("про ВНЕННЯ ЗМІН ДО ПОРЯДКУ ДЕННОГО сесії")