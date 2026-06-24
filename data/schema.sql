-- D1 schema → local SQLite migration
-- Generated from D1 on 2026-06-19

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
    updated_at TEXT DEFAULT (datetime('now')),
    act_number TEXT,
    act_date TEXT,
    status_changed_at TEXT,
    is_procedural INTEGER DEFAULT NULL,
    last_card_check TEXT,
    card_hash TEXT
);

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
    insufficient_text INTEGER DEFAULT 0,
    risk_level TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    notified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bill_passings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    pass_date TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT,
    FOREIGN KEY (bill_id) REFERENCES bills(id)
);

CREATE TABLE IF NOT EXISTS bill_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    doc_type TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(bill_id, file_id)
);

CREATE TABLE IF NOT EXISTS mps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    faction TEXT DEFAULT '',
    start_date TEXT,
    end_date TEXT,
    py REAL DEFAULT 0,
    pda REAL DEFAULT 0,
    vkp REAL DEFAULT 0,
    data_sufficient INTEGER DEFAULT 0,
    total_votes INTEGER DEFAULT 0,
    attended_votes INTEGER DEFAULT 0,
    voted_votes INTEGER DEFAULT 0,
    total_bills INTEGER DEFAULT 0,
    total_laws INTEGER DEFAULT 0,
    stats_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS mp_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vote_id INTEGER NOT NULL,
    mp_name TEXT NOT NULL,
    mp_faction TEXT DEFAULT '',
    status_id INTEGER NOT NULL,
    vote_date TEXT,
    UNIQUE(vote_id, mp_name),
    FOREIGN KEY (vote_id) REFERENCES votes(vote_id) ON DELETE CASCADE,
    FOREIGN KEY (mp_name) REFERENCES mps(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vote_statuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    label TEXT
);

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

CREATE TABLE IF NOT EXISTS pending_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    bill_number TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    output TEXT
);

CREATE TABLE IF NOT EXISTS stats_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

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

CREATE TABLE IF NOT EXISTS eu_alignment_overall (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    overall_score REAL NOT NULL DEFAULT 0,
    weighted_score REAL NOT NULL DEFAULT 0,
    chapters_analyzed INTEGER NOT NULL DEFAULT 0,
    total_chapters INTEGER NOT NULL DEFAULT 35,
    calculated_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS eu_alignment_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL,
    chapter_name TEXT NOT NULL,
    chapter_name_en TEXT NOT NULL,
    alignment REAL NOT NULL DEFAULT 0,
    total_bills INTEGER NOT NULL DEFAULT 0,
    keywords_matched INTEGER NOT NULL DEFAULT 0,
    total_keywords INTEGER NOT NULL DEFAULT 0,
    weight REAL NOT NULL DEFAULT 1.0,
    calculated_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(chapter_id, calculated_at)
);

CREATE TABLE IF NOT EXISTS bill_eu_classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    matched_keywords TEXT,
    classified_at TEXT NOT NULL,
    FOREIGN KEY (bill_id) REFERENCES bills(id)
);

CREATE TABLE IF NOT EXISTS rada_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    url TEXT,
    session TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rada_committee_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    committee_name TEXT NOT NULL,
    meeting_date TEXT NOT NULL,
    meeting_time TEXT,
    topic TEXT,
    room TEXT,
    url TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_state (
    filename TEXT PRIMARY KEY,
    etag TEXT,
    last_checked TEXT,
    last_downloaded TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_bills_bill_number ON bills(bill_number);
CREATE INDEX IF NOT EXISTS idx_bills_stage ON bills(stage);
CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(current_status);
CREATE INDEX IF NOT EXISTS idx_bills_last_card_check ON bills(last_card_check);
CREATE INDEX IF NOT EXISTS idx_risk_bill_id ON risk_assessments(bill_id);
CREATE INDEX IF NOT EXISTS idx_cl_bill_id ON change_log(bill_id);
CREATE INDEX IF NOT EXISTS idx_bp_bill_id ON bill_passings(bill_id);
CREATE INDEX IF NOT EXISTS idx_bd_bill_id ON bill_documents(bill_id);
CREATE INDEX IF NOT EXISTS idx_mv_vote_id ON mp_votes(vote_id);
CREATE INDEX IF NOT EXISTS idx_mv_mp_name ON mp_votes(mp_name);
CREATE INDEX IF NOT EXISTS idx_mv_mp_name_date ON mp_votes(mp_name, vote_date);
CREATE INDEX IF NOT EXISTS idx_pa_bill_id ON pending_analysis(bill_id);
CREATE INDEX IF NOT EXISTS idx_pa_status ON pending_analysis(status);
CREATE INDEX IF NOT EXISTS idx_lv_law_id ON law_versions(law_id);
CREATE INDEX IF NOT EXISTS idx_bec_bill_id ON bill_eu_classification(bill_id);
CREATE INDEX IF NOT EXISTS idx_rs_date ON rada_schedule(date);
CREATE INDEX IF NOT EXISTS idx_rcs_meeting_date ON rada_committee_schedule(meeting_date);
