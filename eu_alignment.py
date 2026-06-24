#!/usr/bin/env python3
"""eu_alignment.py — Розрахунок EU Alignment Score для українських законів.

Визначає відповідність українського законодавства нормам ЄС (acquis communautaire)
та зберігає результати в D1 для відображення на дашборді.

Використання:
    python eu_alignment.py              # Повний розрахунок
    python eu_alignment.py --chapter 1  # Тільки конкретна глава
    python eu_alignment.py --dry-run    # Без збереження
"""
import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import log
from src.d1_client import d1_query, d1_exec

logger = logging.getLogger("eu_alignment")

# === EU Acquis Chapters ===
# Структура: глава → ключові слова/теми для порівняння
EU_ACQUIS_CHAPTERS = {
    1: {
        "name": "Засади",
        "name_en": "Fundamentals",
        "weight": 1.5,
        "keywords": [
            "верховенство права", "незалежність суду", "конституція",
            "демократія", "права людини", "свобода слова", "антикорупція",
            "НАЗК", "НАБУ", "САП", "судова реформа", "прозорість",
            "захист персональних даних", "доступ до інформації"
        ],
        "description": "Верховенство права, демократія, антикорупція"
    },
    2: {
        "name": "Вільний рух товарів",
        "name_en": "Free movement of goods",
        "weight": 1.0,
        "keywords": [
            "митний союз", "тариф", "квота", "технічний регламент",
            "стандарти", "сертифікація", "маркування", "безпечність",
            "конкурентоспроможність", "вільна торгівля", " Dumping"
        ],
        "description": "Митне регулювання, технічні бар'єри"
    },
    3: {
        "name": "Вільний рух осіб",
        "name_en": "Free movement of persons",
        "weight": 1.0,
        "keywords": [
            "ḡромадянство", "перебування", "працевлаштування",
            "соціальне забезпечення", "освіта", "визнання дипломів",
            "шлюб", "реєстрація", "посвідка"
        ],
        "description": "Міграція, працевлаштування, освіта"
    },
    4: {
        "name": "Вільний рух послуг",
        "name_en": "Free movement of services",
        "weight": 1.0,
        "keywords": [
            "послуги", "ліцензування", "дозвільна система",
            "професійна діяльність", "банківські послуги",
            "страхування", "електронна комерція", "торгівля"
        ],
        "description": "Ліцензування, професійні послуги"
    },
    5: {
        "name": "Вільний рух капіталів",
        "name_en": "Free movement of capital",
        "weight": 1.0,
        "keywords": [
            "інвестиції", "капітал", "банківська діяльність",
            "фінансові послуги", "валютне регулювання",
            "цінні папери", "боргові зобов'язання"
        ],
        "description": "Інвестиції, фінансові ринки"
    },
    6: {
        "name": "Корпоративне право",
        "name_en": "Company law",
        "weight": 0.8,
        "keywords": [
            "товариство", "статутний капітал", "акціонерне товариство",
            "об'єднання підприємств", "злиття", "поглинання",
            "банкрутство", "ліквідація", "аудит"
        ],
        "description": "Корпоративне управління, банкрутство"
    },
    7: {
        "name": "Інтелектуальна власність",
        "name_en": "Intellectual property",
        "weight": 0.8,
        "keywords": [
            "патент", "торговельна марка", "авторське право",
            "промисловий зразок", "винахід", "програмне забезпечення",
            "захист даних", "БДР"
        ],
        "description": "Патенти, торговельні марки, авторське право"
    },
    8: {
        "name": "Конкурентна політика",
        "name_en": "Competition policy",
        "weight": 1.2,
        "keywords": [
            "конкуренція", "монополія", "зловживання домінуючим становищем",
            "злиття підприємств", "державна допомога",
            "антиконкурентна угода", "АМКУ", "антимонопольний"
        ],
        "description": "Антимонопольне регулювання, держдопомога"
    },
    9: {
        "name": "Фінансові послуги",
        "name_en": "Financial services",
        "weight": 1.0,
        "keywords": [
            "банківська діяльність", "кредит", "депозит",
            "страхування", "пенсійне забезпечення", "платіжна система",
            "фінансовий нагляд", "Нацбанк", "цінні папери"
        ],
        "description": "Банківська діяльність, страхування"
    },
    10: {
        "name": "Інформаційне суспільство",
        "name_en": "Information society",
        "weight": 0.8,
        "keywords": [
            "електронна комерція", "телекомунікації", "інтернет",
            "персональні дані", "захист даних", "автоматизоване оброблення",
            "цифрова інфраструктура"
        ],
        "description": "Телекомунікації, електронна комерція"
    },
    11: {
        "name": "Сільське господарство",
        "name_en": "Agriculture",
        "weight": 1.0,
        "keywords": [
            "сільське господарство", "харчова безпечність", "ветеринарія",
            "рослинництво", "тваринництво", "продовольство",
            "аграрна політика", "дотації"
        ],
        "description": "Харчова безпечність, аграрна політика"
    },
    12: {
        "name": "Безпечність харчових продуктів",
        "name_en": "Food safety",
        "weight": 1.0,
        "keywords": [
            "харчова безпечність", "ветеринарний контроль",
            "фітосанітарний контроль", "добавки", "ГМО",
            "етикетування", "треті країни"
        ],
        "description": "Ветеринарний та фітосанітарний контроль"
    },
    13: {
        "name": "Рибне господарство",
        "name_en": "Fisheries",
        "weight": 0.6,
        "keywords": [
            "рибне господарство", "аквакультура", "рибальство",
            "контроль рибальства", "морські ресурси"
        ],
        "description": "Рибальство, аквакультура"
    },
    14: {
        "name": "Транспорт",
        "name_en": "Transport",
        "weight": 0.8,
        "keywords": [
            "транспорт", "автомобільний", "залізничний",
            "морський", "авіаційний", "безпечність",
            "ліцензування перевезень", "техогляд"
        ],
        "description": "Транспортна інфраструктура"
    },
    15: {
        "name": "Енергетика",
        "name_en": "Energy",
        "weight": 1.0,
        "keywords": [
            "енергетика", "електроенергія", "газопостачання",
            "нафта", "ядерна енергетика", "відновлювані джерела",
            "енергоефективність", "тарифи"
        ],
        "description": "Енергетичний ринок, відновлювані джерела"
    },
    16: {
        "name": "Телекомунікації та ЗМІ",
        "name_en": "Telecoms & media",
        "weight": 0.8,
        "keywords": [
            "телекомунікації", "засоби масової інформації",
            "радіомовлення", "телебачення", "аудіовізуальні послуги",
            "регулювання ЗМІ"
        ],
        "description": "Телекомунікації, медіа"
    },
    17: {
        "name": "Культура та оподаткування",
        "name_en": "Culture & taxation",
        "weight": 0.8,
        "keywords": [
            "культура", "оподаткування", "ПДВ", "акциз",
            "податок на прибуток", "мита", "податкові пільги"
        ],
        "description": "Оподаткування, культурна політика"
    },
    18: {
        "name": "Статистика",
        "name_en": "Statistics",
        "weight": 0.6,
        "keywords": [
            "статистика", "перепис", "облік", "звітність",
            "статистичні дані", "методологія"
        ],
        "description": "Статистичний облік"
    },
    19: {
        "name": "Соціальна політика та зайнятість",
        "name_en": "Social policy & employment",
        "weight": 1.0,
        "keywords": [
            "праця", "трудове право", "заробітна плата",
            "робочий час", "відпустка", "охорона праці",
            "профспілки", "безробіття", "пенсія"
        ],
        "description": "Трудове право, соціальний захист"
    },
    20: {
        "name": "Підприємництво та промислова політика",
        "name_en": "Enterprise & industrial policy",
        "weight": 0.8,
        "keywords": [
            "підприємництво", "мале та середнє підприємництво",
            "промислова політика", "конкурентоспроможність",
            "інновації", "спрощення регулювання"
        ],
        "description": "Підприємницьке середовище"
    },
    21: {
        "name": "Технічне регулювання",
        "name_en": "Technical barriers",
        "weight": 0.8,
        "keywords": [
            "технічний регламент", "стандарти", "сертифікація",
            "акредитація", "оцінка відповідності", "MET"
        ],
        "description": "Технічні бар'єри у торгівлі"
    },
    22: {
        "name": "Охорона навколишнього середовища",
        "name_en": "Environment",
        "weight": 1.2,
        "keywords": [
            "навколишнє середовище", "екологія", "забруднення",
            "відходи", "вода", "повітря", "оцінка впливу",
            "ОВД", "відновлювані джерела", "клімат"
        ],
        "description": "Екологічне регулювання, клімат"
    },
    23: {
        "name": "Споживачі та охорона здоров'я",
        "name_en": "Consumer & health protection",
        "weight": 1.0,
        "keywords": [
            "споживач", "захист прав споживачів", "безпечність",
            "ліки", "медичні вироби", "огляд здоров'я",
            "епідеміологія", "карантин"
        ],
        "description": "Захист споживачів, охорона здоров'я"
    },
    24: {
        "name": "Зовнішні відносини",
        "name_en": "External relations",
        "weight": 1.0,
        "keywords": [
            "зовнішня політика", "санкції", "допомога",
            "торговельна угода", "асоціація", "партнерство"
        ],
        "description": "Зовнішня політика, міжнародні угоди"
    },
    25: {
        "name": "Митна справа",
        "name_en": "Customs",
        "weight": 0.8,
        "keywords": [
            "митна справа", "митний контроль", "митна вартість",
            "тариф", "процедури", "митний союз"
        ],
        "description": "Митне регулювання"
    },
    26: {
        "name": "Зовнішня торгівля",
        "name_en": "External trade",
        "weight": 0.8,
        "keywords": [
            "зовнішня торгівля", "експорт", "імпорт",
            "антидемпінг", "компенсаційні мита", "_trade agreements"
        ],
        "description": "Торговельна політика"
    },
    27: {
        "name": "Судова співпраця",
        "name_en": "Judicial cooperation",
        "weight": 1.0,
        "keywords": [
            "судова співпраця", "визнання рішень", "виконання",
            "екстрадиція", "взаємна правова допомога"
        ],
        "description": "Судова співпраця з ЄС"
    },
    28: {
        "name": "Свобода, безпека, правосуддя",
        "name_en": "Justice, freedom & security",
        "weight": 1.2,
        "keywords": [
            "безпека", "боротьба з тероризмом", "корупція",
            "організована злочинність", "відмивання грошей",
            "поліція", "слідство", "прокуратура"
        ],
        "description": "Безпека, боротьба зі злочинністю"
    },
    29: {
        "name": "Фінансовий контроль",
        "name_en": "Financial control",
        "weight": 0.8,
        "keywords": [
            "фінансовий контроль", "аудит", "бюджет",
            "фінансова дисципліна", "прозорість"
        ],
        "description": "Фінансовий нагляд"
    },
    30: {
        "name": "Інформація та комунікація",
        "name_en": "Information & communication",
        "weight": 0.6,
        "keywords": [
            "інформація", "комунікація", "дані", "звітність"
        ],
        "description": "Інформаційна інфраструктура"
    },
    31: {
        "name": "Зовнішня, оборонна, безпека",
        "name_en": "Foreign, defence & security",
        "weight": 1.3,
        "keywords": [
            "оборона", "безпека", "військова", "стратегічна",
            "оборонне замовлення", "ВПК", "НАТО"
        ],
        "description": "Оборона, безпека"
    },
    32: {
        "name": "Фінансові та бюджетні положення",
        "name_en": "Financial & budgetary provisions",
        "weight": 0.8,
        "keywords": [
            "бюджет", "фінанси", "внески", "звітність"
        ],
        "description": "Бюджетний процес"
    },
    33: {
        "name": "Інституційні питання",
        "name_en": "Institutional issues",
        "weight": 0.6,
        "keywords": [
            "інституції", "управління", "адміністрація"
        ],
        "description": "Інституційна структура"
    },
    34: {
        "name": "Загальні положення",
        "name_en": "General provisions",
        "weight": 0.6,
        "keywords": [
            "загальні положення", "визначення", "принципи"
        ],
        "description": "Загальні норми"
    },
    35: {
        "name": "Інше",
        "name_en": "Other",
        "weight": 0.4,
        "keywords": [],
        "description": "Різне"
    }
}


def classify_bill_to_chapter(bill_text: str, bill_title: str = "") -> dict:
    """Класифікує законопроєкт до глави EU acquis за ключовими словами."""
    text_lower = (bill_text + " " + bill_title).lower()
    scores = {}

    for chapter_id, chapter in EU_ACQUIS_CHAPTERS.items():
        score = 0
        matched_keywords = []

        for keyword in chapter["keywords"]:
            if keyword.lower() in text_lower:
                score += 1
                matched_keywords.append(keyword)

        scores[chapter_id] = {
            "score": score,
            "matched": matched_keywords,
            "keywords_count": len(chapter["keywords"])
        }

    if not scores:
        return {"chapter": 1, "confidence": 0, "matched": []}

    best = max(scores.items(), key=lambda x: x[1]["score"])
    chapter_id = best[0]
    data = best[1]

    total_keywords = len(EU_ACQUIS_CHAPTERS[chapter_id]["keywords"])
    confidence = data["score"] / total_keywords if total_keywords > 0 else 0

    return {
        "chapter": chapter_id,
        "confidence": min(confidence, 1.0),
        "matched": data["matched"],
        "score": data["score"]
    }


def calculate_alignment_score(ua_bills: list, chapter_id: int) -> dict:
    """Розраховує Alignment Score для конкретної глави EU acquis."""
    chapter = EU_ACQUIS_CHAPTERS[chapter_id]
    total_keywords = len(chapter["keywords"])
    chapter_bills = [b for b in ua_bills if b.get("eu_chapter") == chapter_id]

    if not chapter_bills:
        return {
            "chapter": chapter_id,
            "name": chapter["name"],
            "name_en": chapter["name_en"],
            "alignment": 0,
            "total_bills": 0,
            "keywords_matched": 0,
            "total_keywords": total_keywords,
            "trend": "stable"
        }

    all_matched = set()

    for bill in chapter_bills:
        text = (bill.get("title", "") + " " + bill.get("agenda_category", "")).lower()
        for keyword in chapter["keywords"]:
            if keyword.lower() in text:
                all_matched.add(keyword)

    alignment = (len(all_matched) / total_keywords * 100) if total_keywords > 0 else 0

    return {
        "chapter": chapter_id,
        "name": chapter["name"],
        "name_en": chapter["name_en"],
        "weight": chapter["weight"],
        "description": chapter["description"],
        "alignment": round(min(alignment, 100), 1),
        "total_bills": len(chapter_bills),
        "keywords_matched": len(all_matched),
        "total_keywords": total_keywords,
        "matched_keywords": list(all_matched)
    }


def calculate_overall_alignment(chapter_results: list, total_bills: int = 0) -> dict:
    """Розраховує загальний Alignment Score з урахуванням ваг."""
    if not chapter_results:
        return {"overall": 0, "weighted": 0, "total_bills": 0}

    total_weight = sum(
        EU_ACQUIS_CHAPTERS[r["chapter"]]["weight"]
        for r in chapter_results
    )

    weighted_sum = sum(
        r["alignment"] * EU_ACQUIS_CHAPTERS[r["chapter"]]["weight"]
        for r in chapter_results
    )

    overall = sum(r["alignment"] for r in chapter_results) / len(chapter_results)
    weighted = (weighted_sum / total_weight) if total_weight > 0 else 0

    return {
        "overall": round(overall, 1),
        "weighted": round(weighted, 1),
        "chapters_analyzed": len([r for r in chapter_results if r["total_bills"] > 0]),
        "total_chapters": len(chapter_results),
        "total_bills": total_bills
    }


def fetch_ua_bills_for_alignment() -> tuple:
    """Завантажує українські закони: прийняті та в процесі."""
    try:
        # Прийняті закони (підписані)
        signed = d1_query("""
            SELECT id, bill_number, title, agenda_category, committee,
                   stage, current_status, 'signed' as category
            FROM bills
            WHERE title IS NOT NULL AND title != ''
              AND stage = 4
            LIMIT 15000
        """)
        
        # Закони в процесі (зареєстровані, перше/друге читання)
        in_process = d1_query("""
            SELECT id, bill_number, title, agenda_category, committee,
                   stage, current_status, 'in_process' as category
            FROM bills
            WHERE title IS NOT NULL AND title != ''
              AND stage IN (1, 2, 3)
            LIMIT 15000
        """)
        
        logger.info("Завантажено %d прийнятих + %d в процесі", len(signed), len(in_process))
        return signed, in_process
    except Exception as e:
        logger.error("Помилка завантаження законів: %s", e)
        return [], []


def classify_all_bills(bills: list) -> list:
    """Класифікує всі закони до глав EU acquis."""
    for bill in bills:
        text = bill.get("title", "") + " " + (bill.get("agenda_category", "") or "")
        classification = classify_bill_to_chapter(text, bill.get("title", ""))
        bill["eu_chapter"] = classification["chapter"]
        bill["eu_confidence"] = classification["confidence"]
        bill["eu_matched"] = classification["matched"]

    return bills


def store_alignment_results(results: dict, chapter_results: list, 
                           signed_results: dict = None, in_process_results: dict = None) -> bool:
    """Зберігає результати в D1 з двома метриками."""
    try:
        now = datetime.now().isoformat()

        d1_exec("eu_alignment", {
            "type": "overall",
            "overall_score": results["overall"],
            "weighted_score": results["weighted"],
            "chapters_analyzed": results["chapters_analyzed"],
            "total_chapters": results["total_chapters"],
            "calculated_at": now,
            "signed_score": signed_results["weighted"] if signed_results else 0,
            "in_process_score": in_process_results["weighted"] if in_process_results else 0,
            "signed_bills": signed_results.get("total_bills", 0) if signed_results else 0,
            "in_process_bills": in_process_results.get("total_bills", 0) if in_process_results else 0,
        })

        for chapter in chapter_results:
            d1_exec("eu_alignment", {
                "type": "chapter",
                "chapter_id": chapter["chapter"],
                "chapter_name": chapter["name"],
                "chapter_name_en": chapter["name_en"],
                "alignment": chapter["alignment"],
                "total_bills": chapter["total_bills"],
                "keywords_matched": chapter["keywords_matched"],
                "total_keywords": chapter["total_keywords"],
                "weight": EU_ACQUIS_CHAPTERS[chapter["chapter"]]["weight"],
                "calculated_at": now
            })

        logger.info("Результати збережено в D1")
        return True

    except Exception as e:
        logger.error("Помилка збереження: %s", e)
        return False


def format_progress_bar(score: float, width: int = 30) -> str:
    """Форматує прогрес-бар."""
    filled = int(score / 100 * width)
    bar = "█" * filled + "░" * (width - filled)

    if score > 70:
        color = "🟢"
    elif score > 40:
        color = "🟡"
    else:
        color = "🔴"

    return f"{color} {bar} {score:.1f}%"


def main():
    parser = argparse.ArgumentParser(description="EU Alignment Score Calculator")
    parser.add_argument("--chapter", type=int, help="Аналіз тільки конкретної глави")
    parser.add_argument("--dry-run", action="store_true", help="Без збереження в D1")
    parser.add_argument("--json", action="store_true", help="Вивід у форматі JSON")
    args = parser.parse_args()

    logger.info("=== EU Alignment Score Calculator ===")
    logger.info("Завантаження законів з D1...")

    signed_bills, in_process_bills = fetch_ua_bills_for_alignment()
    if not signed_bills and not in_process_bills:
        logger.error("Не вдалося завантажити закони")
        return

    logger.info("Класифікація законів до глав EU acquis...")
    signed_bills = classify_all_bills(signed_bills)
    in_process_bills = classify_all_bills(in_process_bills)

    chapters_to_analyze = [args.chapter] if args.chapter else range(1, 36)

    # Розрахунок для прийнятих законів
    signed_chapters = []
    for chapter_id in chapters_to_analyze:
        if chapter_id not in EU_ACQUIS_CHAPTERS:
            continue
        result = calculate_alignment_score(signed_bills, chapter_id)
        signed_chapters.append(result)

    # Розрахунок для законів в процесі
    process_chapters = []
    for chapter_id in chapters_to_analyze:
        if chapter_id not in EU_ACQUIS_CHAPTERS:
            continue
        result = calculate_alignment_score(in_process_bills, chapter_id)
        process_chapters.append(result)

    signed_overall = calculate_overall_alignment(signed_chapters, len(signed_bills))
    process_overall = calculate_overall_alignment(process_chapters, len(in_process_bills))
    
    # Загальний (вагове середнє)
    total_bills = len(signed_bills) + len(in_process_bills)
    if total_bills > 0:
        overall_weighted = (signed_overall["weighted"] * len(signed_bills) + 
                           process_overall["weighted"] * len(in_process_bills)) / total_bills
    else:
        overall_weighted = 0

    overall = {
        "overall": overall_weighted,
        "weighted": overall_weighted,
        "chapters_analyzed": max(signed_overall.get("chapters_analyzed", 0), 
                                process_overall.get("chapters_analyzed", 0)),
        "total_chapters": 35,
        "total_bills": total_bills,
    }

    if args.json:
        output = {
            "overall": overall,
            "signed": signed_overall,
            "in_process": process_overall,
            "chapters": signed_chapters,
            "calculated_at": datetime.now().isoformat()
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"  EU ALIGNMENT SCORE")
        print("=" * 60)
        print(f"\n  📗 Прийняті закони:  {signed_overall['weighted']:.1f}% ({len(signed_bills)} законів)")
        print(f"  📘 В процесі:       {process_overall['weighted']:.1f}% ({len(in_process_bills)} законів)")
        print(f"  📊 Загальний:       {overall_weighted:.1f}%")
        print(f"\n  Аналіз глав: {overall['chapters_analyzed']}/35")
        print("  " + "-" * 56)

        for r in sorted(signed_chapters, key=lambda x: x["alignment"], reverse=True):
            if r["total_bills"] > 0:
                bar = format_progress_bar(r["alignment"], 20)
                print(f"  {r['name_en'][:25]:25s} {bar} ({r['total_bills']} bills)")

        print("  " + "-" * 56)
        print("=" * 60)

    if not args.dry_run:
        store_alignment_results(overall, signed_chapters, signed_overall, process_overall)


if __name__ == "__main__":
    main()
