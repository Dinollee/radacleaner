#!/usr/bin/env python3
"""calc_harmonization.py — Harmonization gap analysis (reverse engineering EU directives).

Формат виводу: "X з Y прийнято (Z%)" — зрозуміло для людей.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

# 35 EU acquis chapters — ВУЗЬКІ ключові слова (не широкі)
CHAPTERS = {
    1: {"name": "Judiciary & Fundamental Rights", "name_ua": "Судова влада та фундаментальні права",
        "keywords": ["незалежність суд", "судова реформа", "Вища рада правосуддя", "Конституційний Суд",
                      "захист персональних даних", "антикорупційн", "НАБУ", "САП", "запобігання корупці",
                      "антикорупційна політика", "декларування доходів", "електронне декларування"]},
    2: {"name": "Justice, Freedom & Security", "name_ua": "Юстиція, свобода та безпека",
        "keywords": ["міграційн", "притулок", "Шенген", "боротьба з тероризмом", "контроль кордонів",
                      "европол", "Європол", "Євроюст"]},
    3: {"name": "Public Procurement", "name_ua": "Публічні закупівлі",
        "keywords": ["ProZorro", "публічні закупівлі", "державні закупівлі", "тендерні закупівлі",
                      "закупівлі товарів", "закупівлі послуг", "закупівлі робіт"]},
    4: {"name": "Statistics", "name_ua": "Статистика",
        "keywords": ["статистичний звіт", "перепис населення", "статистична звітність",
                      "обмін статистичними даними"]},
    5: {"name": "Financial Control", "name_ua": "Фінансовий контроль",
        "keywords": ["рахункова палата", "аудит державних коштів", "фінансовий аудит",
                      "контроль виконання бюджету"]},
    6: {"name": "Free Movement of Goods", "name_ua": "Вільний рух товарів",
        "keywords": ["технічний регламент", "маркування продукції", "сертифікація відповідності",
                      "акредитація органів", "оцінка відповідності"]},
    7: {"name": "Free Movement of Workers", "name_ua": "Вільний рух працівників",
        "keywords": ["визнання професійних кваліфікацій", "мобільність трудових ресурсів",
                      "працевлаштування за кордоном", "соціальне забезпечення працівників"]},
    8: {"name": "Right of Establishment & Services", "name_ua": "Право на заснування та послуги",
        "keywords": ["послуги електронної комерції", "ліцензування господарської діяльності",
                      "професійна діяльність", "послуги для населення"]},
    9: {"name": "Free Movement of Capital", "name_ua": "Вільний рух капіталів",
        "keywords": ["інвестиційний клімат", "банківська діяльність", "фінансові послуги",
                      "валютне регулювання", "цінні папери", "боргові зобов"]},
    10: {"name": "Company Law", "name_ua": "Корпоративне право",
         "keywords": ["корпоративне управління", "статутний капітал", "злиття підприємств",
                       "банкрутство підприємств", "ліквідація юридичних осіб"]},
    11: {"name": "Intellectual Property", "name_ua": "Інтелектуальна власність",
         "keywords": ["патентне право", "торговельна марка", "авторське право",
                       "промисловий зразок", "захист інтелектуальної власності"]},
    12: {"name": "Competition Policy", "name_ua": "Конкурентна політика",
         "keywords": ["антимонопольне регулювання", "захист конкуренції", "державна допомога",
                       "зловживання монопольним становищем"]},
    13: {"name": "Financial Services", "name_ua": "Фінансові послуги",
         "keywords": ["банківська діяльність", "фінансові послуги", "страхування",
                       "пенсійне забезпечення", "ринок цінних паперів"]},
    14: {"name": "Information Society & Media", "name_ua": "Інформаційне суспільство та ЗМІ",
         "keywords": ["електронна комерція", "телекомунікаційні послуги", "захист персональних даних в мережі",
                       "цифрова ідентифікація", "авторське право в мережі"]},
    15: {"name": "Agriculture & Rural Development", "name_ua": "Сільське господарство",
         "keywords": ["державна підтримка сільськогосподарських виробників", "земельна реформа",
                       "органічне виробництво", "рослинництво", "тваринництво"]},
    16: {"name": "Food Safety, Veterinary & Phytosanitary", "name_ua": "Безпечність харчових продуктів",
         "keywords": ["безпечність харчових продуктів", "ветеринарна медицина",
                       "фітосанітарний контроль", "санітарні норми"]},
    17: {"name": "Fisheries", "name_ua": "Рибне господарство",
         "keywords": ["рибальство", "риболовство", "аквакультура", "морські ресурси"]},
    18: {"name": "Transport Policy", "name_ua": "Транспортна політика",
         "keywords": ["транспортна політика", "залізничний транспорт", "авіаційний транспорт",
                       "морський транспорт", "автомобільний транспорт"]},
    19: {"name": "Energy", "name_ua": "Енергетика",
         "keywords": ["енергетична політика", "газова промисловість", "ядерна енергетика",
                       "відновлювана енергетика", "енергоефективність"]},
    20: {"name": "Trans-European Networks", "name_ua": "Трансєвропейські мережі",
         "keywords": ["трансєвропейські мережі", "транспортна інфраструктур"]},
    21: {"name": "Environment & Climate Change", "name_ua": "Охорона навколишнього середовища",
         "keywords": ["охорона навколишнього середовища", "екологічна політика",
                       "управління відходами", "кліматичні зміни"]},
    22: {"name": "Consumer & Health Protection", "name_ua": "Захист споживачів та здоров",
         "keywords": ["захист прав споживачів", "безпечність медичних виробів",
                       "лікарські засоби", "громадське здоров"]},
    23: {"name": "Social Policy & Employment", "name_ua": "Соціальна політика та зайнятість",
         "keywords": ["трудове законодавство", "пенсійна реформа", "соціальне страхування",
                       "зайнятість населення", "охорона праці"]},
    24: {"name": "Enterprise & Industrial Policy", "name_ua": "Підприємництво та промисловість",
         "keywords": ["підтримка МСП", "промислова політика", "інноваційна діяльність",
                       "мале та середнє підприємництво"]},
    25: {"name": "Science & Research", "name_ua": "Наука та дослідження",
         "keywords": ["наукова діяльність", "дослідження та розробки", "інноваційні технології",
                       "HORIZON", "наукові гранти"]},
    26: {"name": "Education & Culture", "name_ua": "Освіта та культура",
         "keywords": ["освітня політика", "визнання дипломів", "культурна політика",
                       "молодіжна політика"]},
    27: {"name": "Customs Union", "name_ua": "Митний союз",
         "keywords": ["митна справа", "митне оформлення", "митна вартість",
                       "тарифне регулювання"]},
    28: {"name": "External Relations", "name_ua": "Зовнішні відносини",
         "keywords": ["зовнішньополітичн", "дипломатичні відносини", "міжнародні договори",
                       " двосторонні угоди"]},
    29: {"name": "Foreign, Security & Defence Policy", "name_ua": "Зовнішня, оборонна, безпека",
         "keywords": ["оборонна політика", "безпекова стратегія", "військова допомога",
                       "оборонний бюджет"]},
    30: {"name": "Financial & Budgetary Provisions", "name_ua": "Фінансові та бюджетні положення",
         "keywords": ["бюджетний процес", "фінансовий контроль", "рахункова палата",
                       "бюджетна політика"]},
    31: {"name": "Institutional Issues", "name_ua": "Інституційні питання",
         "keywords": ["державна служба", "реформа державного управління", "інституційна реформа"]},
    32: {"name": "Other", "name_ua": "Інше",
         "keywords": ["загальні положення", "інші питання"]},
}


# EU integration index v1: negotiation cluster statuses → score
STATUS_SCORES = {"not_opened": 0, "opened": 50, "provisionally_closed": 100}


def compute_index(statuses, legislation):
    """Чистая функция: статусы 6 кластеров + overall гармонизация → индекс v1.

    NEGOTIATION = среднее по кластерам (not_opened=0, opened=50, provisionally_closed=100)
    INDEX = round(0.5 * NEGOTIATION + 0.5 * LEGISLATION, 1)
    """
    negotiation = round(sum(STATUS_SCORES[s] for s in statuses) / len(statuses), 1) if statuses else 0
    legislation = round(legislation, 1)
    return {
        "negotiation": negotiation,
        "legislation": legislation,
        "index": round(0.5 * negotiation + 0.5 * legislation, 1),
    }


def calc_harmonization():
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    results = []
    for ch_id, ch in sorted(CHAPTERS.items()):
        conditions = []
        for kw in ch["keywords"]:
            conditions.append(f"(b.title ILIKE '%{kw}%' OR ra.raw_analysis ILIKE '%{kw}%')")
        where_clause = " OR ".join(conditions)

        cur.execute(f"""
            SELECT
                COUNT(DISTINCT b.id) as total,
                COUNT(DISTINCT b.id) FILTER (WHERE b.stage = 4) as signed,
                COUNT(DISTINCT b.id) FILTER (WHERE b.stage = 5) as rejected,
                COUNT(DISTINCT b.id) FILTER (WHERE b.stage IN (1,2,3)) as pending
            FROM bills b
            LEFT JOIN risk_assessments ra ON ra.bill_id = b.id
            WHERE ({where_clause})
        """)
        row = cur.fetchone()
        total = row[0] or 0
        signed = row[1] or 0
        rejected = row[2] or 0
        pending = row[3] or 0

        harmonization = round((signed / total * 100), 1) if total > 0 else 0

        results.append({
            "id": ch_id,
            "name_ua": ch["name_ua"],
            "total": total,
            "signed": signed,
            "rejected": rejected,
            "pending": pending,
            "harmonization": harmonization,
        })

    # Print in human-readable format
    print(f"\n{'#':>3} {'Глава':<40} {'Прийнято':>10} {'Всього':>8} {'Гармон.':>8}")
    print("-" * 75)
    for r in results:
        flag = "🟢" if r["harmonization"] > 30 else "🟠" if r["harmonization"] > 20 else "🔴"
        print(f"{r['id']:>3} {r['name_ua']:<40} {r['signed']:>5} з {r['total']:<5} {r['harmonization']:>6.1f}% {flag}")

    total_bills = sum(r["total"] for r in results)
    total_signed = sum(r["signed"] for r in results)
    overall = round((total_signed / total_bills * 100), 1) if total_bills > 0 else 0
    print(f"\n{'':>3} {'ЗАГАЛОМ':<40} {total_signed:>5} з {total_bills:<5} {overall:>6.1f}%")

    # Save
    for r in results:
        cur.execute("""
            INSERT INTO stats_cache (key, value, updated_at)
            VALUES (%s, %s, now() AT TIME ZONE 'utc')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (f"harmonization_ch{r['id']}", f"{r['harmonization']}:{r['total']}:{r['signed']}"))

    # EU integration index v1 → stats_cache 'eu_integration_v1'
    try:
        cur.execute("SELECT cluster_id, status, event_date FROM eu_cluster_status ORDER BY cluster_id")
        clusters = [
            {"id": cid, "status": st, "event_date": ed.isoformat() if ed else None}
            for cid, st, ed in cur.fetchall()
        ]
        if clusters:
            idx = compute_index([c["status"] for c in clusters], overall)
            value = json.dumps({
                "v": 1,
                "index": idx["index"],
                "negotiation": idx["negotiation"],
                "legislation": idx["legislation"],
                "clusters": clusters,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False)
            cur.execute("""
                INSERT INTO stats_cache (key, value, updated_at)
                VALUES ('eu_integration_v1', %s, now() AT TIME ZONE 'utc')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """, (value,))
    except Exception as e:
        print(f"eu_integration_v1 skipped: {e}")

    conn.commit()
    cur.close()
    conn.close()
    return results


if __name__ == "__main__":
    calc_harmonization()
