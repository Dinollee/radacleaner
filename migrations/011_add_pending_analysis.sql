-- Міграція 011: Черга LLM-аналізу
CREATE TABLE IF NOT EXISTS pending_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    bill_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / running / done / error
    output TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pa_status ON pending_analysis(status);
CREATE INDEX IF NOT EXISTS idx_pa_bill_id ON pending_analysis(bill_id);
