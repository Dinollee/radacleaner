"""radacleaner — Моніторинг законопроектів ВРУ з LLM-аналізом ризиків.

Всі дані зберігаються в Cloudflare D1 (через Worker API).
Локальна БД не потрібна.

Запуск:
    python sync_bills.py list          # Швидка синхронізація (ETag)
    python sync_bills.py full          # Повна синхронізація
    python rag_monitor.py              # Моніторинг + аналіз ризиків
    python rag_monitor.py --test       # Без Telegram
    python batch_rag_50.py --limit 10  # Batch-обробка

Або через модулі:
    python -m src.bill_sync list
    python -m src.rag_engine
"""
__version__ = "0.3.0"
