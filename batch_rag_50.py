#!/usr/bin/env python3
"""
batch_rag_50.py — Batch-обробка існуючих rag_documents LLM-аналізом.

Тонка обгортка над src.rag_engine (_run_batch).
  python batch_rag_50.py --limit 10
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Додаємо --batch до argv, щоб rag_engine зрозумів режим
if "--batch" not in sys.argv:
    sys.argv.insert(1, "--batch")

from src.rag_engine import main

if __name__ == "__main__":
    main()