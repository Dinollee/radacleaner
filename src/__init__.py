"""radacleaner — Моніторинг законопроектів ВРУ з LLM-аналізом ризиків.

База даних: PostgreSQL 192.168.1.244/radacleaner (див. .env).

Запуск:
    python sync_bills.py all           # Синхронізація законопроєктів
    python night_batch.py              # Нічний LLM-аналіз (3 воркери)
    python analyze_api.py              # Воркер черги pending_analysis
    python calc_kpi_v12.py             # Розрахунок ІЕД депутатів

Або через модулі:
    python -m src.bill_sync list
    python -m src.rag_engine
"""
__version__ = "0.4.0"
