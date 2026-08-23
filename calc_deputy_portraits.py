#!/usr/bin/env python3
"""calc_deputy_portraits.py — «Портрет депутата»: LLM-узагальнення даних моніторингу.

Замінює шаблонні порогові сигнали (в усіх однакові) на персоналізований текст:
портрет 3-5 речень + 3-5 сигналів з конкретними числами. Джерело — ЛИШЕ наші дані:
ІЕД за компонентами, ранги серед 389 активних, однодумці (voting_allies),
профіль інтересів (deputy_interests), результативність, звернення.
Правила для LLM: українська, тільки факти з наданого листа, без здогадів,
без оцінних ярликів і обвинувачень. Оновлення щотижня (пн 04:30).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from src.llm_client import llm_completion_raw

load_dotenv(Path(__file__).parent / ".env")

DB_DSN = "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
CALL_DELAY = 6  # сек між викликами (~10/хв — ліміт OpenRouter free)

PORTRAIT_SYSTEM_PROMPT = (
    "Ти — аналітик моніторингового центру «Страж Демократії», що веде об'єктивний "
    "облік діяльності народних депутатів України. ВІДПОВІДАЙ ВИКЛЮЧНО УКРАЇНСЬКОЮ МОВОЮ. "
    "ТІЛЬКИ JSON без Markdown-оберток."
)

PORTRAIT_PROMPT = """Нижче — фактологічний лист про народного депутата з бази моніторингу.

{facts}

Завдання:
1. "portrait" — характеристика депутата, 3-5 речень українською: чим займається в парламенті,
   сильні та слабкі місця ЗА ДАНИМИ, стиль роботи. Тільки факти з листа, жодних припущень,
   домислов чи інформації поза листом. Без ярликів «провалля»/«герой» — лише нейтральна фіксація даних.
2. "signals" — масив 3-5 персональних спостережень, КОЖНЕ має містити конкретне число або
   порівняння з іншими депутатами (з листа). Не повторюй загальні фрази — кожен пункт
   має відрізнятися від типових для більшості.

Відповідь — ТІЛЬКИ JSON:
{{"portrait": "...", "signals": ["...", "..."]}}"""


def pct_label(rank: int | None, total: int) -> str:
    if not rank or total <= 0:
        return ""
    pos = rank / total
    if rank == 1:
        return f"№1 серед {total}"
    if pos <= 0.05:
        return f"топ-5% (№{rank} із {total})"
    if pos <= 0.10:
        return f"топ-10% (№{rank} із {total})"
    if pos <= 0.25:
        return f"топ-25% (№{rank} із {total})"
    if pos <= 0.5:
        return f"вище середнього (№{rank} із {total})"
    return f"№{rank} із {total}"


def compute_ranks(deputies: list[dict]) -> None:
    """Додає d['_rank_<field>'] — місце серед активних (1 = найкращий)."""
    for field in ("kpi_v12_score", "adoption_rate", "total_laws", "eu_integration_score", "py"):
        vals = sorted((d[field] for d in deputies if isinstance(d.get(field), (int, float))), reverse=True)
        for d in deputies:
            v = d.get(field)
            d[f"_rank_{field}"] = vals.index(v) + 1 if isinstance(v, (int, float)) and v in vals else None
            d["_rank_total"] = len(vals)


def build_fact_sheet(d: dict, allies: list[dict], interests: list[dict]) -> str:
    lines = [
        f"Депутат: {d['name']} (фракція «{d.get('faction') or '—'}»).",
        f"Комітет: {d.get('committee_name') or 'не працює в комітеті'}"
        + (f", роль: {d['committee_role']}" if d.get('committee_role') else "") + ".",
    ]
    ied = d.get('kpi_v12_score')
    if ied is not None:
        comp = ", ".join(f"{n}={d.get(k) or 0:.0f}" for n, k in [
            ("дисципліна", "kpi_v12_discipline"), ("законотворчість", "kpi_v12_legislation"),
            ("результативність", "kpi_v12_efficiency"), ("комітет", "kpi_v12_committee"),
            ("звернення", "kpi_v12_requests"), ("вплив", "kpi_v12_impact")])
        lines.append(f"ІЕД: {ied:.1f}/100 ({comp}). Загальний рейтинг: "
                     f"{pct_label(d.get('_rank_kpi_v12_score'), d['_rank_total'] or 0)}.".replace("Загальний рейтинг: .", ""))
    laws = f"Законотворчість: {d.get('total_bills') or 0} авторських/співавторських законопроєктів, прийнято законів {d.get('total_laws') or 0}"
    if d.get('_rank_total_laws'):
        laws += f" ({pct_label(d['_rank_total_laws'], d['_rank_total'])} за кількістю прийнятих)"
    if d.get('adoption_rate') is not None:
        laws += f", частка прийнятих {d['adoption_rate']:.0f}%"
    if d.get('authorship_ratio') is not None:
        ar = d['authorship_ratio']
        style = "переважно індивідуальний автор" if ar > 0.5 else "змішаний стиль" if ar > 0.2 else "переважно колективний автор (співавторство)"
        laws += f". Стиль: {style}"
    lines.append(laws + ".")
    lines.append(f"Голосування: працював {d.get('py') or 0:.0f}% засідань"
                 + (f" ({pct_label(d['_rank_py'], d['_rank_total'])})" if d.get('_rank_py') else "") + ".")
    if allies:
        a_txt = "; ".join(
            f"{a['name']} («{a['faction']}», збіг {a['pct']:.0f}%{' — ІНША фракція' if a['cross_faction'] else ''})"
            for a in allies[:3])
        lines.append(f"Однодумці за голосуваннями (найчастіше однакова позиція): {a_txt}.")
    if interests:
        i_txt = ", ".join(f"{i['sector']} (автор {i['authored']}, голосів «за» {i['voted_for']})" for i in interests[:3])
        lines.append(f"Профіль інтересів (галузі, які виграють від його законів): {i_txt}.")
    req = f"Звернення: {d.get('request_count') or 0}, з відповідями {d.get('requests_with_response') or 0}."
    if d.get('eu_integration_score'):
        req += f" Євроінтеграційний профіль: {d['eu_integration_score']:.0f}/35 (євро-законів {d.get('eu_euro_bills') or 0})."
    lines.append(req)
    return "\n".join(lines)


def _is_ukrainian(text: str) -> bool:
    """Портрет має бути українською — відсікаємо англійські витоки моделі."""
    alpha = [c.lower() for c in text if c.isalpha()]
    if not alpha:
        return False
    latin = sum(1 for c in alpha if "a" <= c <= "z")
    return latin / len(alpha) < 0.05


def parse_portrait(raw: str) -> dict | None:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    portrait = data.get("portrait")
    signals = data.get("signals")
    if not isinstance(portrait, str) or len(portrait) < 40:
        return None
    if not isinstance(signals, list):
        signals = []
    signals = [s.strip() for s in signals if isinstance(s, str) and s.strip()][:5]
    if not _is_ukrainian(portrait) or any(not _is_ukrainian(s) for s in signals):
        return None
    return {"portrait": portrait.strip(), "signals": signals}


def run(limit: int | None = None) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, faction, committee_role,
               (SELECT cm.committee_name FROM committee_members cm WHERE cm.member_uid = m.rada_uid LIMIT 1) AS committee_name,
               kpi_v12_score, kpi_v12_discipline, kpi_v12_legislation, kpi_v12_efficiency,
               kpi_v12_committee, kpi_v12_requests, kpi_v12_impact,
               total_bills, total_laws, adoption_rate, authorship_ratio, py,
               request_count, requests_with_response, eu_integration_score, eu_euro_bills,
               portrait_at
        FROM mps m WHERE end_date IS NULL ORDER BY id
    """)
    cols = [c[0] for c in cur.description]
    deputies = [dict(zip(cols, r)) for r in cur.fetchall()]
    compute_ranks(deputies)
    print(f"Активних депутатів: {len(deputies)}")

    allies_map: dict[int, list[dict]] = {}
    cur.execute("""
        SELECT va.mp_a, va.mp_b, ma.name AS a_name, ma.faction AS a_faction,
               mb.name AS b_name, mb.faction AS b_faction, va.pct, va.cross_faction
        FROM voting_allies va JOIN mps ma ON ma.id = va.mp_a JOIN mps mb ON mb.id = va.mp_b
    """)
    for mp_a, mp_b, an, af, bn, bf, pct, cross in cur.fetchall():
        allies_map.setdefault(mp_a, []).append({"name": bn, "faction": bf, "pct": pct, "cross_faction": cross})
        allies_map.setdefault(mp_b, []).append({"name": an, "faction": af, "pct": pct, "cross_faction": cross})
    for lst in allies_map.values():
        lst.sort(key=lambda a: -a["pct"])

    interests_map: dict[int, list[dict]] = {}
    cur.execute("""
        SELECT mp_id, sector, authored, voted_for FROM deputy_interests
        WHERE authored > 0 OR voted_for > 0
        ORDER BY (authored * 3 + voted_for) DESC
    """)
    for mp_id, sector, authored, voted_for in cur.fetchall():
        interests_map.setdefault(mp_id, []).append(
            {"sector": sector, "authored": authored, "voted_for": voted_for})

    targets = deputies if not limit else deputies[:limit]
    ok = fail = skipped = 0
    for i, d in enumerate(targets, 1):
        if limit is None and d.get("portrait_at") and (time.time() - d["portrait_at"].timestamp()) < 6 * 86400:
            skipped += 1
            continue
        facts = build_fact_sheet(d, allies_map.get(d["id"], []), interests_map.get(d["id"], []))
        try:
            raw = llm_completion_raw(PORTRAIT_PROMPT.format(facts=facts),
                                     system_prompt=PORTRAIT_SYSTEM_PROMPT, max_tokens=1200)
            parsed = parse_portrait(raw)
        except Exception as e:
            print(f"  FAIL #{d['id']} {d['name']}: {str(e)[:100]}")
            parsed = None
        if not parsed:
            fail += 1
            continue
        cur.execute("""
            UPDATE mps SET portrait = %s, portrait_signals = %s::jsonb, portrait_at = now()
            WHERE id = %s
        """, (parsed["portrait"], json.dumps(parsed["signals"], ensure_ascii=False), d["id"]))
        conn.commit()
        ok += 1
        if ok % 20 == 0:
            print(f"  прогрес {i}/{len(targets)} (ok={ok}, fail={fail}, skip={skipped})")
        time.sleep(CALL_DELAY)

    cur.close()
    conn.close()
    print(f"Готово: ok={ok}, fail={fail}, свіжих пропущено={skipped}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="обробити перших N (для тесту)")
    args = parser.parse_args()
    run(args.limit)
