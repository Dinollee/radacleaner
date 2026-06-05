"""radacleaner — Моніторинг законопроектів ВРУ з LLM-аналізом ризиків.

Встановлення:
    pip install -r requirements.txt

Запуск:
    python -m src.sync_bills list          # Швидка синхронізація (ETag)
    python -m src.sync_bills full          # Повна синхронізація
    python -m src.rag_monitor              # Моніторинг + аналіз ризиків
    python -m src.batch_rag_50 --limit 10  # Batch-обробка

Конфігурація:
    Скопіюйте .env.example → .env та заповніть змінні.
"""
__version__ = "0.1.0"
