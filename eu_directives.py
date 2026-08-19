#!/usr/bin/env python3
"""eu_directives.py — База ключових EU директив для кожної глави acquis.

Джерело: EUR-Lex (public data), EU Acquis Communautaire.
Використовується для reverse engineering harmonization.
"""

# Ключові EU директиви та регламенти для України (35 глав acquis)
EU_DIRECTIVES = {
    1: {  # Judiciary & Fundamental Rights
        "name": "Судова влада та фундаментальні права",
        "directives": [
            {"celex": "32016L0679", "name": "GDPR (General Data Protection Regulation)", "year": 2016, "status": "key"},
            {"celex": "32016L0680", "name": "Law Enforcement Directive (Data Protection)", "year": 2016, "status": "key"},
            {"celex": "32014L0041", "name": "Preventing and Combating Violence Against Women", "year": 2024, "status": "key"},
            {"celex": "32012L0029", "name": "Victims' Rights in Criminal Proceedings", "year": 2012, "status": "key"},
            {"celex": "32016L0800", "name": "Minimum Standards on Rights of Suspects", "year": 2016, "status": "key"},
            {"celex": "32012L0013", "name": "Right to Access to Lawyer", "year": 2013, "status": "key"},
            {"celex": "32010L0064", "name": "Presumption of Innocence", "year": 2016, "status": "key"},
        ],
        "keywords_ua": ["GDPR", "захист персональних даних", "антикорупція", "НАБУ", "САП", "судова реформа", "незалежність суду", "права людини"],
    },
    2: {  # Justice, Freedom & Security
        "name": "Юстиція, свобода та безпека",
        "directives": [
            {"celex": "32013L0040", "name": "European Protection Order", "year": 2013, "status": "key"},
            {"celex": "32014L0042", "name": "European Investigation Order", "year": 2014, "status": "key"},
            {"celex": "32013L037", "name": "Mutual Recognition of Protection Measures", "year": 2013, "status": "key"},
            {"celex": "32011L0093", "name": "Right to Interpretation and Translation", "year": 2012, "status": "key"},
            {"celex": "32012L0036", "name": "Freezing and Confiscation of Crime Proceeds", "year": 2014, "status": "key"},
        ],
        "keywords_ua": ["міграція", "притулок", "Шенген", "боротьба з тероризмом", "Європол", "Євроюст"],
    },
    3: {  # Public Procurement
        "name": "Публічні закупівлі",
        "directives": [
            {"celex": "32014L0024", "name": "Public Procurement Directive", "year": 2014, "status": "key"},
            {"celex": "32014L0025", "name": "Concession Contracts Directive", "year": 2014, "status": "key"},
            {"celex": "32014L0023", "name": "Utilities Procurement Directive", "year": 2014, "status": "key"},
        ],
        "keywords_ua": ["ProZorro", "публічні закупівлі", "державні закупівлі", "тендерні закупівлі"],
    },
    6: {  # Free Movement of Goods
        "name": "Вільний рух товарів",
        "directives": [
            {"celex": "32015L0153", "name": "New Legislative Framework (Market Surveillance)", "year": 2015, "status": "key"},
            {"celex": "32014L0035", "name": "Accreditation and Market Surveillance", "year": 2014, "status": "key"},
            {"celex": "32011L0065", "name": "Construction Products Regulation", "year": 2011, "status": "key"},
        ],
        "keywords_ua": ["технічний регламент", "маркування", "сертифікація", "оцінка відповідності"],
    },
    9: {  # Free Movement of Capital
        "name": "Вільний рух капіталів",
        "directives": [
            {"celex": "32013L036", "name": "Bank Recovery and Resolution Directive", "year": 2014, "status": "key"},
            {"celex": "32014L0065", "name": "Capital Requirements Directive IV", "year": 2013, "status": "key"},
            {"celex": "32013L036", "name": "Single Resolution Mechanism", "year": 2014, "status": "key"},
        ],
        "keywords_ua": ["банківська діяльність", "фінансові послуги", "цінні папери", "валютне регулювання"],
    },
    13: {  # Financial Services
        "name": "Фінансові послуги",
        "directives": [
            {"celex": "32013L002", "name": "Bank Account Directive", "year": 2014, "status": "key"},
            {"celex": "32014L0091", "name": "Payment Services Directive 2", "year": 2015, "status": "key"},
            {"celex": "32016L0878", "name": "Insurance Distribution Directive", "year": 2016, "status": "key"},
        ],
        "keywords_ua": ["банківська діяльність", "фінансові послуги", "страхування", "пенсійне забезпечення"],
    },
    15: {  # Agriculture
        "name": "Сільське господарство",
        "directives": [
            {"celex": "32018L0848", "name": "Common Agricultural Policy (CAP) 2023-2027", "year": 2021, "status": "key"},
            {"celex": "32018L0849", "name": "CAP Horizontal Regulation", "year": 2021, "status": "key"},
        ],
        "keywords_ua": ["сільське господарство", "земельна реформа", "органічне виробництво"],
    },
    16: {  # Food Safety
        "name": "Безпечність харчових продуктів",
        "directives": [
            {"celex": "32017R0625", "name": "Official Controls Regulation", "year": 2017, "status": "key"},
            {"celex": "32002L0002", "name": "General Food Law", "year": 2002, "status": "key"},
        ],
        "keywords_ua": ["безпечність харчових продуктів", "ветеринарна медицина", "фітосанітарний контроль"],
    },
    19: {  # Energy
        "name": "Енергетика",
        "directives": [
            {"celex": "32019L0944", "name": "Clean Energy Package", "year": 2019, "status": "key"},
            {"celex": "32018L0202", "name": "Governance of the Energy Union", "year": 2018, "status": "key"},
            {"celex": "32012L0027", "name": "Energy Efficiency Directive", "year": 2012, "status": "key"},
        ],
        "keywords_ua": ["енергетична політика", "газова промисловість", "відновлювана енергетика", "енергоефективність"],
    },
    21: {  # Environment
        "name": "Охорона навколишнього середовища",
        "directives": [
            {"celex": "32018L0842", "name": "Environmental Impact Assessment", "year": 2014, "status": "key"},
            {"celex": "32008L0050", "name": "Waste Framework Directive", "year": 2008, "status": "key"},
            {"celex": "32010L0075", "name": "Environmental Liability Directive", "year": 2004, "status": "key"},
        ],
        "keywords_ua": ["охорона навколишнього середовища", "екологічна політика", "управління відходами"],
    },
    23: {  # Social Policy
        "name": "Соціальна політика",
        "directives": [
            {"celex": "32003L0088", "name": "Working Time Directive", "year": 2003, "status": "key"},
            {"celex": "32019L1152", "name": "Work-Life Balance Directive", "year": 2019, "status": "key"},
            {"celex": "32019L1158", "name": "Transparent and Predictable Working Conditions", "year": 2019, "status": "key"},
            {"celex": "32008L0104", "name": "Equal Treatment Directive", "year": 2006, "status": "key"},
        ],
        "keywords_ua": ["трудове законодавство", "пенсійна реформа", "соціальне страхування", "зайнятість"],
    },
    29: {  # Security & Defence
        "name": "Зовнішня, оборонна, безпека",
        "directives": [
            {"celex": "32021R0783", "name": "European Peace Facility", "year": 2021, "status": "key"},
            {"celex": "32003R02580", "name": "European Defence Agency", "year": 2004, "status": "key"},
        ],
        "keywords_ua": ["оборонна політика", "безпекова стратегія", "військова допомога"],
    },
}


def get_directives_summary():
    """Повертає підсумок по директивах для кожної глави."""
    summary = []
    for ch_id, ch in sorted(EU_DIRECTIVES.items()):
        total_directives = len(ch["directives"])
        key_directives = sum(1 for d in ch["directives"] if d["status"] == "key")
        summary.append({
            "chapter": ch_id,
            "name": ch["name"],
            "total_directives": total_directives,
            "key_directives": key_directives,
            "keywords": ch["keywords_ua"],
        })
    return summary


if __name__ == "__main__":
    summary = get_directives_summary()
    print(f"{'#':>3} {'Chapter':<40} {'Directives':>10} {'Keywords'}")
    print("-" * 80)
    for s in summary:
        print(f"{s['chapter']:>3} {s['name']:<40} {s['total_directives']:>5} ({s['key_directives']} key)  {', '.join(s['keywords'][:3])}")
