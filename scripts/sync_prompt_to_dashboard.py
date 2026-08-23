#!/usr/bin/env python3
"""Синхронізація тексту промптів із src/prompts.py у методологію дашборда.

Запускати після КОЖНОЇ зміни промпту (правило MEMORY: промпт змінено → дашборд оновлено):
    venv/bin/python scripts/sync_prompt_to_dashboard.py
"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.prompts import RISK_ANALYSIS_PROMPT, RISK_ANALYSIS_SYSTEM_PROMPT

ROOT = Path(__file__).parent.parent
INDEX = ROOT / "dashboard" / "index.html"

PRE = ('<pre style="white-space:pre-wrap;background:var(--bg);border:1px solid var(--border);'
       'border-radius:6px;padding:10px;font-size:11.5px;max-height={maxh}px;overflow:auto;'
       'margin:0 0 10px">{text}</pre>')


def replace_block(text: str, marker: str, new_block: str) -> str:
    start_tag = f"<!-- {marker}:START -->"
    end_tag = f"<!-- {marker}:END -->"
    try:
        start = text.index(start_tag) + len(start_tag)
        end = text.index(end_tag)
    except ValueError:
        sys.exit(f"МАРКЕР ВІДСУТНІЙ в index.html: {marker}")
    return text[:start] + "\n" + new_block + "\n  " + text[end:]


def main():
    doc = INDEX.read_text(encoding="utf-8")

    system_pre = PRE.format(maxh=200, text=html.escape(RISK_ANALYSIS_SYSTEM_PROMPT))
    # {text} у промпті — плейсхолдер шаблону; на дашборді показуємо як літерал
    main_text = html.escape(RISK_ANALYSIS_PROMPT).replace("{text}", "&#123;text&#125;")
    main_pre = PRE.format(maxh=420, text=main_text)

    doc = replace_block(doc, "PROMPT-SYSTEM", system_pre)
    doc = replace_block(doc, "PROMPT-MAIN", main_pre)
    INDEX.write_text(doc, encoding="utf-8")
    print("OK: промпти синхронізовано в dashboard/index.html")


if __name__ == "__main__":
    main()
