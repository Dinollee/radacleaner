"""З'єднання з PostgreSQL базою даних."""
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from .config import DB_PARAMS


@contextmanager
def db():
    """Context manager для з'єднання з БД.

    Use:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
    """
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        yield conn
    finally:
        conn.close()


def db_conn():
    """Повертає пряме з'єднання з БД (не забудьте закрити)."""
    return psycopg2.connect(**DB_PARAMS)