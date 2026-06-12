-- Видаляємо дублікати bill_documents та додаємо уникальний constraint
CREATE TABLE IF NOT EXISTS bill_documents_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    doc_type TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(bill_id, file_id)
);

INSERT OR IGNORE INTO bill_documents_new (bill_id, file_id, doc_type, created_at)
SELECT bill_id, file_id, doc_type, created_at FROM bill_documents;

DROP TABLE bill_documents;
ALTER TABLE bill_documents_new RENAME TO bill_documents;

CREATE INDEX IF NOT EXISTS idx_bd_bill_id ON bill_documents(bill_id);
CREATE INDEX IF NOT EXISTS idx_bd_file_id ON bill_documents(file_id);
