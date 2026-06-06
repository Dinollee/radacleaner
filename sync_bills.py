#!/usr/bin/env python3
"""
sync_bills.py — Синхронізація бази законопроектів ВРУ з data.rada.gov.ua.

Тонка обгортка над src.bill_sync. Використовується для cron:
  python sync_bills.py list   # швидка синхронізація (ETag)
  python sync_bills.py full   # повна синхронізація
  python sync_bills.py all    # обидві
"""
import sys
import os

# Додаємо корінь проекту в sys.path для сумісності з cron
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.bill_sync import main

if __name__ == "__main__":
    main()