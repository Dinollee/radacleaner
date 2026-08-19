#!/usr/bin/env python3
"""
calc_msi_kpb.py — Розрахунок MSI (Majority Support Index) та K_pb (політичний бар'єр).

Алгоритм:
  1. Bootstrap: коаліція = фракція з найбільшою кількістю депутатів
  2. Для кожного прийнятого закону (stage=4): визначити позицію коаліції
  3. Для кожного депутата: MSI = голоси_з_коаліцією_на_прийнятих / всі_голоси_на_прийнятих
  4. Перерахувати коаліцію = фракції з MSI > 0.6
  5. Повторювати до стабілізації (<5% зміни)
  6. K_pb = MSI / MSI_max, мінімум 0.1

Vote statuses: 1=yes, 2=no, 3=abstain, 4=not_present, 5=absent
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.d1_client import d1_query, d1_exec_sql
from src.config import log

COALITION_THRESHOLD = 0.6
MIN_KPB = 0.1
MAX_ITERATIONS = 5
CONVERGENCE_THRESHOLD = 0.05


def get_adopted_bill_votes():
    """Отримати всі голосування на прийнятих законах з фракціями з mps."""
    sql = """
        SELECT
            v.vote_id,
            v.bill_id,
            mv.mp_id,
            m.faction as mp_faction,
            vs.code as vote_code
        FROM mp_votes mv
        JOIN votes v ON v.vote_id = mv.vote_id
        JOIN bills b ON b.id = v.bill_id
        JOIN vote_statuses vs ON vs.id = mv.status_id
        JOIN mps m ON m.id = mv.mp_id
        WHERE b.stage = 4
          AND mv.mp_id IS NOT NULL
          AND m.faction IS NOT NULL
          AND m.faction != ''
          AND vs.code IN ('yes', 'no', 'abstain')
    """
    return d1_query(sql)


def get_largest_faction():
    """Знайти фракцію з найбільшою кількістю активних депутатів."""
    sql = """
        SELECT faction, COUNT(*) as cnt
        FROM mps
        WHERE (end_date IS NULL OR end_date = '')
          AND faction IS NOT NULL
          AND faction != ''
        GROUP BY faction
        ORDER BY cnt DESC
        LIMIT 1
    """
    rows = d1_query(sql)
    return rows[0]['faction'] if rows else ''


def calculate_msi_kpb():
    log.info("=== Розрахунок MSI та K_pb ===")

    votes_data = get_adopted_bill_votes()
    log.info("Loaded %d vote records on adopted bills", len(votes_data))

    if not votes_data:
        log.warning("No votes on adopted bills found")
        return

    # Group votes by bill
    bills_votes = {}
    for v in votes_data:
        bid = v['bill_id']
        if bid not in bills_votes:
            bills_votes[bid] = []
        bills_votes[bid].append(v)

    log.info("Found %d adopted bills with votes", len(bills_votes))

    # Build MP -> faction mapping from mps table (clean, no trailing spaces)
    mp_factions = {}
    all_mp_ids = set()
    for v in votes_data:
        mp_id = v['mp_id']
        all_mp_ids.add(mp_id)
        mp_factions[mp_id] = v['mp_faction'].strip()

    # Bootstrap: largest faction
    bootstrap_faction = get_largest_faction()
    coalition_factions = {bootstrap_faction}
    log.info("Bootstrap coalition: %s", coalition_factions)

    for iteration in range(MAX_ITERATIONS):
        # For each bill, determine coalition position
        bill_coalition_pos = {}
        for bid, bvotes in bills_votes.items():
            coalition_votes = [v for v in bvotes if v['mp_faction'].strip() in coalition_factions]
            if not coalition_votes:
                continue
            yes_count = sum(1 for v in coalition_votes if v['vote_code'] == 'yes')
            no_count = sum(1 for v in coalition_votes if v['vote_code'] == 'no')
            if yes_count > no_count:
                bill_coalition_pos[bid] = 'yes'
            elif no_count > yes_count:
                bill_coalition_pos[bid] = 'no'
            else:
                bill_coalition_pos[bid] = 'yes' if yes_count > 0 else 'no'

        # Calculate per-MP MSI
        mp_with_coalition = {}
        mp_total_votes = {}

        for bid, bvotes in bills_votes.items():
            if bid not in bill_coalition_pos:
                continue
            cpos = bill_coalition_pos[bid]
            for v in bvotes:
                mp_id = v['mp_id']
                mp_total_votes[mp_id] = mp_total_votes.get(mp_id, 0) + 1
                if v['vote_code'] == cpos:
                    mp_with_coalition[mp_id] = mp_with_coalition.get(mp_id, 0) + 1

        # Calculate MSI per MP
        mp_msi = {}
        for mp_id in all_mp_ids:
            total = mp_total_votes.get(mp_id, 0)
            with_c = mp_with_coalition.get(mp_id, 0)
            if total > 0:
                mp_msi[mp_id] = with_c / total
            else:
                mp_msi[mp_id] = 0.5

        # Determine new coalition: factions with avg MSI > threshold
        faction_msi_sum = {}
        faction_msi_count = {}
        for mp_id, msi in mp_msi.items():
            f = mp_factions.get(mp_id, '')
            if f:
                faction_msi_sum[f] = faction_msi_sum.get(f, 0) + msi
                faction_msi_count[f] = faction_msi_count.get(f, 0) + 1

        new_coalition = set()
        for f in faction_msi_sum:
            avg = faction_msi_sum[f] / faction_msi_count[f]
            if avg > COALITION_THRESHOLD:
                new_coalition.add(f)

        # Check convergence
        changed_factions = new_coalition.symmetric_difference(coalition_factions)
        changed_mps = sum(1 for mp_id in all_mp_ids if mp_factions.get(mp_id, '') in changed_factions)
        total_active = len([mp_id for mp_id in all_mp_ids if mp_total_votes.get(mp_id, 0) > 0])
        churn = changed_mps / total_active if total_active > 0 else 0

        log.info("Iteration %d: coalition=%s, changed_mps=%d, churn=%.1f%%",
                 iteration + 1, new_coalition, changed_mps, churn * 100)

        # Print per-faction avg MSI
        for f in sorted(faction_msi_sum.keys()):
            avg = faction_msi_sum[f] / faction_msi_count[f]
            in_c = "✓" if f in new_coalition else " "
            log.info("  %s [%s] avg_MSI=%.3f (n=%d)", in_c, f, avg, faction_msi_count[f])

        coalition_factions = new_coalition

        if churn < CONVERGENCE_THRESHOLD and iteration > 0:
            log.info("Converged after %d iterations", iteration + 1)
            break

    # Final MSI and K_pb calculation with stable coalition
    # Recalculate one last time with final coalition
    bill_coalition_pos = {}
    for bid, bvotes in bills_votes.items():
        coalition_votes = [v for v in bvotes if v['mp_faction'].strip() in coalition_factions]
        if not coalition_votes:
            continue
        yes_count = sum(1 for v in coalition_votes if v['vote_code'] == 'yes')
        no_count = sum(1 for v in coalition_votes if v['vote_code'] == 'no')
        bill_coalition_pos[bid] = 'yes' if yes_count > no_count else 'no'

    mp_with_coalition = {}
    mp_total_votes = {}
    for bid, bvotes in bills_votes.items():
        if bid not in bill_coalition_pos:
            continue
        cpos = bill_coalition_pos[bid]
        for v in bvotes:
            mp_id = v['mp_id']
            mp_total_votes[mp_id] = mp_total_votes.get(mp_id, 0) + 1
            if v['vote_code'] == cpos:
                mp_with_coalition[mp_id] = mp_with_coalition.get(mp_id, 0) + 1

    mp_msi = {}
    for mp_id in all_mp_ids:
        total = mp_total_votes.get(mp_id, 0)
        with_c = mp_with_coalition.get(mp_id, 0)
        mp_msi[mp_id] = with_c / total if total > 0 else 0.5

    msi_max = max(mp_msi.values()) if mp_msi else 1.0
    if msi_max == 0:
        msi_max = 1.0

    # Write to DB
    updated = 0
    for mp_id in all_mp_ids:
        msi = mp_msi.get(mp_id, 0.5)
        kpb = max(msi / msi_max, MIN_KPB)
        d1_exec_sql(
            "UPDATE mps SET msi = %s, kpb = %s WHERE id = %s",
            [round(msi, 4), round(kpb, 4), mp_id]
        )
        updated += 1

    log.info("=== Updated %d deputies ===", updated)
    log.info("MSI_max = %.4f, Coalition = %s", msi_max, coalition_factions)

    msi_values = list(mp_msi.values())
    kpb_values = [max(m / msi_max, MIN_KPB) for m in msi_values]
    active = [v for v in msi_values if v != 0.5 or mp_total_votes.get(list(mp_msi.keys())[msi_values.index(v)], 0) > 0]
    log.info("MSI  range: %.3f — %.3f (avg %.3f)", min(msi_values), max(msi_values), sum(msi_values) / len(msi_values))
    log.info("K_pb range: %.3f — %.3f (avg %.3f)", min(kpb_values), max(kpb_values), sum(kpb_values) / len(kpb_values))

    return mp_msi, coalition_factions


if __name__ == "__main__":
    calculate_msi_kpb()
