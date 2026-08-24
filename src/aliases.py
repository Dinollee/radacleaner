"""deputy_aliases — історичні прізвища депутатів (шлюб/розлучення).

rada_uid — стабільний ключ особи; mps.name — лише поточне ім'я.
Напрямок пари: old_name (до зміни) → new_name (поточна).
"""
import re

_WS = re.compile(r"\s+")


def resolve_name_candidates(cur, name):
    """[name] + усі відомі форми імен того ж депутата (old↔new з deputy_aliases)."""
    candidates = [_WS.sub(" ", name or "").strip()]
    cur.execute(
        "SELECT old_name, new_name FROM deputy_aliases "
        "WHERE new_name = %s OR old_name = %s",
        (name, name),
    )
    for old_name, new_name in cur.fetchall():
        for form in (old_name, new_name):
            if form not in candidates:
                candidates.append(form)
    return candidates


def alias_surnames(candidates):
    """Унікальні прізвища (перші слова) із кандидатів імен, відсортовані."""
    return sorted({_WS.sub(" ", c or "").strip().split()[0] for c in candidates if c and c.strip()})
