-- Таблиця законопроектів депутатів
CREATE TABLE IF NOT EXISTS mp_bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mp_name TEXT NOT NULL,
    reg_number TEXT NOT NULL,
    reg_date TEXT,
    title TEXT,
    law_number TEXT,
    is_law INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(mp_name, reg_number)
);

CREATE INDEX IF NOT EXISTS idx_mp_bills_name ON mp_bills(mp_name);
CREATE INDEX IF NOT EXISTS idx_mp_bills_law ON mp_bills(is_law);
