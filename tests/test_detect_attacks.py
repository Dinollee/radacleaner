"""Тесты detect_attacks.py (Phase 2 burst detector): только чистые функции."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from detect_attacks import (
    build_alert_text,
    campaign_alert,
    extract_bill_numbers,
    find_clusters,
    hamming,
    is_burst,
    jaccard,
    top_tokens,
)
from sync_info_monitor import norm_words, simhash64

T0 = datetime(2026, 8, 21, 12, 0)


def mk(i, source_type="telegram", source_name="Канал А", title="", body="",
       posted_min=0, simhash=None, tokens=None):
    return {
        "id": i,
        "source_type": source_type,
        "source_name": source_name,
        "url": f"https://t.me/x/{i}",
        "title": title,
        "body": body,
        "posted_at": T0 + timedelta(minutes=posted_min),
        "simhash": simhash64(title + " " + body) if simhash is None else simhash,
        "tokens": set(norm_words(f"{title} {body}")) if tokens is None else tokens,
    }


# --- hamming / jaccard ---

def test_hamming_identical_zero_and_masked_signed():
    assert hamming(0, 0) == 0
    assert hamming(-1, -1) == 0          # знаковые BIGINT не ломают xor


def test_hamming_distance_bounded_by_64():
    assert hamming(0, -1) == 64
    assert hamming(1 << 10, 0) == 1


def test_jaccard_basic():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b", "c"}, {"a", "b", "d"}) == 0.5
    assert jaccard(set(), {"a"}) == 0.0


# --- find_clusters: копипаст ловит simhash, парафраз — jaccard ---

COPY_PASTE = "Термінова мобілізація в Україні: нові правила для чоловіків віком від 25 років"


def test_simhash_catches_copy_paste():
    a = mk(1, title=COPY_PASTE, body=COPY_PASTE * 2)
    b = mk(2, title=COPY_PASTE + "!", body=COPY_PASTE * 2)   # почти копия
    c = mk(3, title="Курс євро сьогодні трохи зріс на валютному ринку", body="фінанси")
    clusters = find_clusters([a, b, c])
    ids = sorted(sorted(it["id"] for it in cl) for cl in clusters)
    assert [1, 2] in ids and [3] in ids


def test_jaccard_catches_paraphrase():
    # попарно далёкие simhash (hamming > 10), кластер только по словарю
    far_a, far_b, far_c = -1, 0, ((1 << 21) - 1) << 43
    toks = {f"w{i}" for i in range(20)}
    para = {f"w{i}" for i in range(14)} | {f"x{i}" for i in range(6)}  # jac = 14/26 ≈ 0.54
    other = {f"y{i}" for i in range(20)}                               # jac с любым < 0.1
    clusters = find_clusters([
        mk(1, simhash=far_a, tokens=toks),
        mk(2, simhash=far_b, tokens=para),
        mk(3, simhash=far_c, tokens=other),
    ])
    ids = sorted(sorted(it["id"] for it in cl) for cl in clusters)
    assert [1, 2] in ids and [3] in ids


def test_unrelated_items_stay_singletons():
    # биты в непересекающихся 22-битных блоках => hamming 44 > 10 у любой пары
    items = [mk(i, title=f"зовсім інша тема номер {i} про космос",
                simhash=((1 << 22) - 1) << (22 * i),
                tokens={f"z{i}", f"q{i}", f"r{i}"}) for i in range(3)]
    assert len(find_clusters(items)) == 3


# --- is_burst: бьорст-правило ---

def burst_cluster(n_channels, n_posts=8, spread_min=60):
    items = []
    for p in range(n_posts):
        ch = f"Канал-{p % n_channels}"
        items.append(mk(p, source_name=ch, title=f"теза {p}",
                        posted_min=p * spread_min))
    return items


def test_burst_fires_on_4_channels_8_posts():
    assert is_burst(burst_cluster(4)) is True


def test_burst_not_on_3_channels():
    assert is_burst(burst_cluster(3, n_posts=12)) is False


def test_burst_not_on_wide_time_spread():
    # 7 * 250 мин ≈ 29 ч > ATTACK_WINDOW_HOURS/2 (24 ч)
    assert is_burst(burst_cluster(4, spread_min=250)) is False


def test_factcheck_only_never_bursts():
    items = [mk(p, source_type="factcheck", source_name=f"FC-{p % 5}",
                title=f"розслідування {p}") for p in range(12)]
    assert is_burst(items) is False


# --- cooldown / эскалация ---

def test_campaign_new_always_alerts():
    assert campaign_alert([], {"a", "b", "c"}, 8) is True


def test_campaign_same_without_escalation_suppressed():
    prev = [( {"a", "b", "d"}, 10)]
    # jaccard({a,b,c},{a,b,d}) = 2/4 = 0.5 < 0.6 -> другая кампания
    assert campaign_alert(prev, {"a", "b", "c"}, 30) is True
    same = [( {"a", "b", "c", "d"}, 10)]     # jac = 3/4 = 0.75 >= 0.6
    assert campaign_alert(same, {"a", "b", "c"}, 15) is False   # 15 < 2*10
    assert campaign_alert(same, {"a", "b", "c"}, 20) is True    # эскалация 2x


# --- топ-токены и текст алерта ---

def _cluster():
    return [mk(i, source_name=f"Канал-{i}", title="мобілізація термінова Україна",
               posted_min=i * 30) for i in range(8)]


def test_top_tokens_most_common():
    # счётчики равны -> порядок не гарантирован, сравниваем множество
    assert set(top_tokens(_cluster())) == {"мобілізація", "термінова", "україна"}


def test_alert_text_with_debunk_and_bill():
    debunk = {"title": "ЦПД: фейк про мобілізацію", "url": "https://cpd.gov.ua/x"}
    text = build_alert_text(_cluster(), debunk=debunk, bill_number="10490")
    narrative = next(l for l in text.splitlines() if l.startswith("Нарратив: "))
    assert "🚨 Синхронна хвиля публікацій" in text
    assert set(narrative.removeprefix("Нарратив: ").split(", ")) == \
        {"мобілізація", "термінова", "україна"}
    assert "8 постів у 8 каналах за 4 год" in text
    assert "🔎 Спростування: ЦПД: фейк про мобілізацію — https://cpd.gov.ua/x" in text
    assert "📜 Законопроєкт №10490" in text
    assert "_Ознаки скоординованої хвилі; вердикт — за фактчекерами._" in text


def test_alert_text_without_debunk_has_followup_line():
    assert "🔎 Спростування поки немає — стежимо" in build_alert_text(_cluster())


# --- bill regex ---

def test_extract_bill_numbers_keyword_and_hash():
    texts = ["У Раді зареєстрували законопроєкт № 10490 про мобілізацію",
             "№12345 ухвалять до кінця сесії", "просто число 2026 без контексту"]
    assert extract_bill_numbers(texts) == ["10490", "12345"]
