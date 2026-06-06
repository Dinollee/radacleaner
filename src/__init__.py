"""radacleaner — Моніторинг законопроектів ВРУ з LLM-аналізом ризиків.

Встановлення:
    pip install -r requirements.txt

Запуск:
    python sync_bills.py list          # Швидка синхронізація (ETag)
    python sync_bills.py full          # Повна синхронізація
    python rag_monitor.py              # Моніторинг + аналіз ризиків
    python rag_monitor.py --test       # Без Telegram
    python batch_rag_50.py --limit 10  # Batch-обробка

Або через модулі:
    python -m src.bill_sync list
    python -m src.rag_engine
    python -m src.bill_sync full

Конфігурація:
    Скопіюйте .env.example → .env та заповніть змінні.
"""
__version__ = "0.2.0"