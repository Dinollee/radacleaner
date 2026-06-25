-- Таблиця ініціаторів/співавторів законопроєктів
CREATE TABLE IF NOT EXISTS bill_sponsors (
    id SERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    mp_name TEXT NOT NULL,
    rada_uid INTEGER,
    sponsor_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (now() AT TIME ZONE 'utc')::text
);

CREATE INDEX IF NOT EXISTS idx_bill_sponsors_bill_id ON bill_sponsors(bill_id);
CREATE INDEX IF NOT EXISTS idx_bill_sponsors_mp_name ON bill_sponsors(mp_name);

-- Для швидкого підрахунку кількості авторів на білл
CREATE INDEX IF NOT EXISTS idx_bill_sponsors_bill_mp ON bill_sponsors(bill_id, mp_name);
