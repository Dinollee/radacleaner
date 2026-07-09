#!/usr/bin/env python3
"""calc_harmonization.py — Повний harmonization gap analysis для 35 глав EU acquis.

Метод: reverse engineering — маппимо EU директиви на наші закони.
Джерело: bills.title + agenda_category + risk_assessments.raw_analysis.
"""
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = f"host={os.getenv('DB_HOST', '192.168.1.244')} dbname={os.getenv('DB_NAME', 'radacleaner')} user={os.getenv('DB_USER', 'postgres')} password={os.getenv('DB_PASSWORD', '164352')}"

# 35 EU acquis chapters with Ukrainian keywords for matching
CHAPTERS = {
    1: {"name": "Judiciary & Fundamental Rights", "name_ua": "Судова влада та фундаментальні права",
        "keywords": ["судов", "права людини", "захист прав", "незалежність суд", "судд", "антикорупці", "НАБУ", "САП", "запобігання корупці", "Вища рада правосуддя", "Конституційний Суд", "захист персональних даних", "GDPR", "дискримінація", "гендер"]},
    2: {"name": "Justice, Freedom & Security", "name_ua": "Юстиція, свобода та безпека",
        "keywords": ["безпека", "СБУ", "міграція", "притулок", "віз", "Шенген", "боротьба з тероризмом", "зброю", "наркотик"]},
    3: {"name": "Public Procurement", "name_ua": "Публічні закупівлі",
        "keywords": ["закупівлі", "прозорість закупівель", "ProZorro", "тендер", "допорогові закупівлі"]},
    4: {"name": "Statistics", "name_ua": "Статистика",
        "keywords": ["статистик", "обмін інформацією", "перепис", "статистичний"]},
    5: {"name": "Financial Control", "name_ua": "Фінансовий контроль",
        "keywords": ["фінансов%контроль", "рахункова палата", "аудит державних коштів", "звітність"]},
    6: {"name": "Free Movement of Goods", "name_ua": "Вільний рух товарів",
        "keywords": ["митний", "тариф", "квота", "технічний регламент", "стандарт", "сертифікація", "маркування", "безпечність", "конкурентоспроможність"]},
    7: {"name": "Free Movement of Workers", "name_ua": "Вільний рух працівників",
        "keywords": ["працевлаштування", "мобільність працівників", "визнання кваліфікацій"]},
    8: {"name": "Right of Establishment & Services", "name_ua": "Право на заснування та послуги",
        "keywords": ["послуги", "ліцензування", "дозвільна система", "професійн діяльність", "торгівля"]},
    9: {"name": "Free Movement of Capital", "name_ua": "Вільний рух капіталів",
        "keywords": ["інвестиції", "капітал", "банківська діяльність", "фінансові послуги", "валютне регулювання", "цінні папери", "боргові зобов"]},
    10: {"name": "Company Law", "name_ua": "Корпоративне право",
         "keywords": ["товариство", "статутний капітал", "акціонерне товариство", "злиття", "поглинання", "банкрутство", "ліквідація", "аудит"]},
    11: {"name": "Intellectual Property", "name_ua": "Інтелектуальна власність",
         "keywords": ["патент", "торговельна марка", "авторське право", "промисловий зразок", "винахід", "програмне забезпечення"]},
    12: {"name": "Competition Policy", "name_ua": "Конкурентна політика",
         "keywords": ["конкурентн", "антимонопол", "захист конкуренції", "державна допомога", "монополія"]},
    13: {"name": "Financial Services", "name_ua": "Фінансові послуги",
         "keywords": ["банківськ", "фінансов%послуг", "страхування", "пенсійне забезпечення", "цінні папери"]},
    14: {"name": "Information Society & Media", "name_ua": "Інформаційне суспільство та ЗМІ",
         "keywords": ["електронна комерція", "телекомунікації", "Інтернет", "захист даних", "цифров", "ЗМІ", "медіа"]},
    15: {"name": "Agriculture & Rural Development", "name_ua": "Сільське господарство та розвиток сільських територій",
         "keywords": ["сільськ%господарств", "аграрн", "земл", "виробництво", "рослинництво", "тваринництво"]},
    16: {"name": "Food Safety, Veterinary & Phytosanitary", "name_ua": "Безпечність харчових продуктів",
         "keywords": ["харчов%продукт", "ветеринарн", "фітосанітарн", "безпечність харчових", "Санітарні заходи"]},
    17: {"name": "Fisheries", "name_ua": "Рибне господарство",
         "keywords": ["рибн", "риболовств", "аквакультур"]},
    18: {"name": "Transport Policy", "name_ua": "Транспортна політика",
         "keywords": ["транспорт", "залізниц", "авіація", "морський транспорт", "автомобільний транспорт", "поштов"]},
    19: {"name": "Energy", "name_ua": "Енергетика",
         "keywords": ["енергетик", "енерг", "газ", "нафта", "ядерна", "відновлювана енергетик", "енергоефективн"]},
    20: {"name": "Trans-European Networks", "name_ua": "Трансєвропейські мережі",
         "keywords": ["трансєвропейськ", "інфраструктур", "мереж"]},
    21: {"name": "Environment & Climate Change", "name_ua": "Охорона навколишнього середовища",
         "keywords": ["навколишн%середовищ", "екологічн", "відходи", "клімат", "забруднення", "охорона природи"]},
    22: {"name": "Consumer & Health Protection", "name_ua": "Захист прав споживачів та здоров",
         "keywords": ["споживач", "захист прав споживачів", "медичн", "лікарські засоби", "здоров"]},
    23: {"name": "Social Policy & Employment", "name_ua": "Соціальна політика та зайнятість",
         "keywords": ["соціальн%політик", "працевлаштування", "пенсія", "соціальне забезпечення", "безробіття", "заробітна плат"]},
    24: {"name": "Enterprise & Industrial Policy", "name_ua": "Підприємництво та промислова політика",
         "keywords": ["підприємництв", "промислов", "МСП", "мале підприємництво", "інноваці"]},
    25: {"name": "Science & Research", "name_ua": "Наука та дослідження",
         "keywords": ["науков", "дослідженн", "інноваці", "технологічн", "HORIZON"]},
    26: {"name": "Education & Culture", "name_ua": "Освіта та культура",
         "keywords": ["освіт", "навчання", "культура", "молодь", "наука"]},
    27: {"name": "Customs Union", "name_ua": "Митний союз",
         "keywords": ["митн", "митний союз", "митна справа", "митне оформлення"]},
    28: {"name": "External Relations", "name_ua": "Зовнішні відносини",
         "keywords": ["зовнішн%політик", "дипломатія", "санкції", "міжнародні угоди", " двосторонн"]},
    29: {"name": "Foreign, Security & Defence Policy", "name_ua": "Зовнішня, оборонна, безпека",
         "keywords": ["безпек", "оборон", "ВПС", "СБУ", "військов", "оборонний", "НАТО"]},
    30: {"name": "Financial & Budgetary Provisions", "name_ua": "Фінансові та бюджетні положення",
         "keywords": ["бюджет", "фінансов%контроль", "рахункова палата", "фінансування", "бюджетний"]},
    31: {"name": "Institutional Issues", "name_ua": "Інституційні питання",
         "keywords": ["державн%будівництво", "реорганізація", "інституційн", "управління державою"]},
    32: {"name": "Other", "name_ua": "Інше",
         "keywords": ["загальні положення", "інші питання"]},
}


def calc_harmonization():
    """Розрахунок harmonization для всіх 35 глав."""
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    results = []
    for ch_id, ch in sorted(CHAPTERS.items()):
        # Build SQL WHERE clause from keywords
        conditions = []
        for kw in ch["keywords"]:
            conditions.append(f"(b.title ILIKE '%{kw}%' OR ra.raw_analysis ILIKE '%{kw}%')")

        where_clause = " OR ".join(conditions)

        cur.execute(f"""
            SELECT
                COUNT(DISTINCT b.id) as total_bills,
                COUNT(DISTINCT b.id) FILTER (WHERE b.stage = 4) as signed_bills,
                COUNT(DISTINCT b.id) FILTER (WHERE b.stage = 5) as rejected_bills,
                ROUND(AVG(CASE WHEN ra.overall_score > 0 THEN ra.overall_score END)::numeric, 1) as avg_risk
            FROM bills b
            LEFT JOIN risk_assessments ra ON ra.bill_id = b.id
            WHERE ({where_clause})
        """)
        row = cur.fetchone()
        total = row[0] or 0
        signed = row[1] or 0
        rejected = row[2] or 0
        avg_risk = row[3] or 0

        harmonization = round((signed / total * 100), 1) if total > 0 else 0
        gap = round(100 - harmonization, 1) if total > 0 else 0

        results.append({
            "id": ch_id,
            "name": ch["name"],
            "name_ua": ch["name_ua"],
            "total_bills": total,
            "signed_bills": signed,
            "rejected_bills": rejected,
            "avg_risk": avg_risk,
            "harmonization": harmonization,
            "gap": gap,
        })

    # Print results
    print(f"\n{'#':>3} {'Chapter':<40} {'Bills':>6} {'Signed':>6} {'Rej':>4} {'Harm%':>6} {'Gap%':>6}")
    print("-" * 85)
    for r in results:
        flag = "🟢" if r["harmonization"] > 30 else "🟠" if r["harmonization"] > 20 else "🔴"
        print(f"{r['id']:>3} {r['name_ua']:<40} {r['total_bills']:>6} {r['signed_bills']:>6} {r['rejected_bills']:>4} {r['harmonization']:>5.1f}% {r['gap']:>5.1f}% {flag}")

    # Summary
    total_bills = sum(r["total_bills"] for r in results)
    total_signed = sum(r["signed_bills"] for r in results)
    overall = round((total_signed / total_bills * 100), 1) if total_bills > 0 else 0
    print(f"\n{'':>3} {'ЗАГАЛОМ':<40} {total_bills:>6} {total_signed:>6} {'':>4} {overall:>5.1f}%")

    # Top gaps (most work needed)
    print(f"\nТоп-5 глав з найбільшим gap:")
    for r in sorted(results, key=lambda x: x["gap"], reverse=True)[:5]:
        print(f"  Ch{r['id']:>2} {r['name_ua']:<40} gap={r['gap']:.1f}% ({r['total_bills']} bills, {r['signed_bills']} signed)")

    # Save to stats_cache
    for r in results:
        cur.execute("""
            INSERT INTO stats_cache (key, value, updated_at)
            VALUES (%s, %s, now() AT TIME ZONE 'utc')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
        """, (f"harmonization_ch{r['id']}", f"{r['harmonization']}:{r['total_bills']}:{r['signed_bills']}"))

    conn.commit()
    cur.close()
    conn.close()

    return results


if __name__ == "__main__":
    calc_harmonization()
