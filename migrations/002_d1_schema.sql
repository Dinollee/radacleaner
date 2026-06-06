-- radacleaner — D1 (SQLite) схема для Cloudflare
-- JSON-колонки зберігаються як TEXT, дати як TEXT (ISO 8601)

-- Законопроекти
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_number TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    current_status TEXT DEFAULT 'new',
    registration_date TEXT,
    committee TEXT,
    agenda_category TEXT,
    url TEXT,
    text_hash TEXT,
    plain_text TEXT,
    stage INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bills_number ON bills(bill_number);
CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(current_status);
CREATE INDEX IF NOT EXISTS idx_bills_stage ON bills(stage);

-- Історія версій законів
CREATE TABLE IF NOT EXISTS law_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    law_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    version_date TEXT DEFAULT (datetime('now')),
    status_at_moment TEXT,
    text_hash TEXT NOT NULL,
    plain_text TEXT,
    analysis_summary TEXT,
    risks_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(law_id, text_hash)
);

CREATE INDEX IF NOT EXISTS idx_lv_law_id ON law_versions(law_id);
CREATE INDEX IF NOT EXISTS idx_lv_hash ON law_versions(text_hash);

-- Лог змін (для моніторингу)
CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    notified INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cl_notified ON change_log(notified);
CREATE INDEX IF NOT EXISTS idx_cl_bill_id ON change_log(bill_id);

-- Оцінки ризиків від LLM
CREATE TABLE IF NOT EXISTS risk_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    bill_id INTEGER NOT NULL UNIQUE REFERENCES bills(id) ON DELETE CASCADE,
    assessed_at TEXT DEFAULT (datetime('now')),
    model_used TEXT,
    budget_risk TEXT,
    legal_risk TEXT,
    economic_risk TEXT,
    social_risk TEXT,
    corruption_risk TEXT,
    overall_score REAL,
    raw_response TEXT,
    raw_analysis TEXT,
    json_data TEXT,
    legislative_risk TEXT,
    official_power_risk TEXT,
    vague_norms_risk TEXT,
    confidence_level INTEGER DEFAULT 5,
    insufficient_text INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ra_bill_id ON risk_assessments(bill_id);

-- Документи законів (PDF файли)
CREATE TABLE IF NOT EXISTS rag_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    doc_type TEXT,
    file_id TEXT,
    title TEXT,
    char_count INTEGER,
    content TEXT,
    risk_level TEXT,
    chunk_count INTEGER,
    annotations TEXT,
    content_hash TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rd_bill_id ON rag_documents(bill_id);

-- Чанки тексту документів
CREATE TABLE IF NOT EXISTS rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER REFERENCES rag_documents(id) ON DELETE CASCADE,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT,
    section TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rc_bill_id ON rag_chunks(bill_id);
CREATE INDEX IF NOT EXISTS idx_rc_doc_id ON rag_chunks(document_id);

-- Документи з RADA API
CREATE TABLE IF NOT EXISTS bill_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    file_id TEXT,
    doc_type TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bd_bill_id ON bill_documents(bill_id);

-- Стан синхронізації (ETag)
CREATE TABLE IF NOT EXISTS sync_state (
    filename TEXT PRIMARY KEY,
    etag TEXT,
    last_checked TEXT,
    last_downloaded TEXT
);

-- Депутати
CREATE TABLE IF NOT EXISTS mps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    faction TEXT DEFAULT ''
);

-- Статуси голосувань
CREATE TABLE IF NOT EXISTS vote_statuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    label TEXT
);

INSERT OR IGNORE INTO vote_statuses (id, code, label) VALUES
    (1, 'yes', 'За'),
    (2, 'no', 'Проти'),
    (3, 'abstain', 'Утримався'),
    (4, 'not_present', 'Не голосував'),
    (5, 'absent', 'Відсутній');

-- Голосування
CREATE TABLE IF NOT EXISTS votes (
    vote_id INTEGER PRIMARY KEY,
    bill_id INTEGER,
    title TEXT,
    vote_date TEXT,
    yes_count INTEGER DEFAULT 0,
    no_count INTEGER DEFAULT 0,
    abstain_count INTEGER DEFAULT 0,
    not_present_count INTEGER DEFAULT 0,
    absent_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_votes_bill_id ON votes(bill_id);
CREATE INDEX IF NOT EXISTS idx_votes_date ON votes(vote_date);

-- Голосування депутатів
CREATE TABLE IF NOT EXISTS mp_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id INTEGER NOT NULL REFERENCES votes(vote_id) ON DELETE CASCADE,
    mp_name TEXT NOT NULL REFERENCES mps(name) ON DELETE CASCADE,
    mp_faction TEXT DEFAULT '',
    status_id INTEGER NOT NULL REFERENCES vote_statuses(id),
    UNIQUE(vote_id, mp_name)
);

CREATE INDEX IF NOT EXISTS idx_mv_vote_id ON mp_votes(vote_id);
CREATE INDEX IF NOT EXISTS idx_mv_mp_name ON mp_votes(mp_name);