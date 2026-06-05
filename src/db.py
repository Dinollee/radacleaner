"""Загальні утиліти для роботи з БД."""
import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DB_PARAMS = {
    "host": os.environ.get("DB_HOST", "192.168.1.229"),
    "port": int(os.environ.get("DB_PORT", "5432")),
    "dbname": os.environ.get("DB_NAME", "my_bills"),
    "user": os.environ.get("DB_USER", "hermes"),
    "password": os.environ.get("DB_PASSWORD", "hermes"),
}


@contextmanager
def db():
    """Context manager для з'єднання з БД."""
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        yield conn
    finally:
        conn.close()


def db_conn():
    """Повертає нове з'єднання з БД."""
    return psycopg2.connect(**DB_PARAMS)
