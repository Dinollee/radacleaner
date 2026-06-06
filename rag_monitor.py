#!/usr/bin/env python3
"""
rag_monitor.py — Моніторинг законопроектів з LLM-аналізом ризиків.

Тонка обгортка над src.rag_engine. Використовується для cron:
  python rag_monitor.py            # стандартний запуск
  python rag_monitor.py --force    # переаналізувати всі
  python rag_monitor.py --test     # без Telegram
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.rag_engine import main

if __name__ == "__main__":
    main()