#!/usr/bin/env python3
"""sync_all.py — Повна синхронізація всіх даних для KPI.

Запускається після нічного аналізу або за потреби.
Оновлює:
  1. Фракції депутатів (sync_mp_factions.py)
  2. Статистику голосувань (sync_mp_stats.py)
  3. Комітети (sync_committee_members.py)
  4. MSI та K_pb (calc_msi_kpb.py)
  5. Quality, Risk, Authorship (calc_bill_quality.py)
  6. KPI Score v9 (calc_deputy_kpi_v9.py)
  7. Депутатські запити (sync_deputy_requests.py)
"""
import subprocess
import sys
import os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
PYTHON = SCRIPTS_DIR / "venv" / "bin" / "python"

def run_script(name, script):
    print(f"\n{'='*60}")
    print(f"Running: {name}")
    print(f"{'='*60}")
    result = subprocess.run(
        [str(PYTHON), str(SCRIPTS_DIR / script)],
        cwd=str(SCRIPTS_DIR),
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"WARNING: {name} exited with code {result.returncode}")
    return result.returncode

def main():
    print("Повна синхронізація KPI даних (v9)")
    print("=" * 60)

    steps = [
        ("Sync factions", "sync_mp_factions.py"),
        ("Sync MP stats", "sync_mp_stats.py"),
        ("Sync committee members", "sync_committee_members.py"),
        ("Calculate MSI & K_pb", "calc_msi_kpb.py"),
        ("Calculate Quality, Risk, Authorship", "calc_bill_quality.py"),
        ("Recalculate EU Deputy Scores", "calc_eu_deputy.py"),
        ("Recalculate EU Alignment", "eu_alignment.py"),
        ("Calculate KPI Score v9", "calc_deputy_kpi_v9.py"),
        ("Sync deputy requests", "sync_deputy_requests.py"),
    ]

    errors = []
    for name, script in steps:
        rc = run_script(name, script)
        if rc != 0:
            errors.append(name)

    print(f"\n{'='*60}")
    print("DONE")
    if errors:
        print(f"Errors: {errors}")
    else:
        print("All steps completed successfully")

if __name__ == "__main__":
    main()
