-- radacleaner — Міграція 001: Початкова схема БД
-- Виконується один раз при ініціалізації

-- Таблиця законопроектів
CREATE TABLE IF NOT EXISTS bills (
    id SERIAL PRIMARY KEY,
    bill_number VARCHAR(50) UNIQUE NOT NULL,
    title TEXT NOT NULL,
    current_status VARCHAR(50) DEFAULT 'new',
    registration_date DATE,
    committee VARCHAR(200),
    agenda_category VARCHAR(100),
    url TEXT,
    text_hash VARCHAR(64),
    plain_text TEXT,
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- Історія версій законів (для журналістів)
CREATE TABLE IF NOT EXISTS law_versions (
    id SERIAL PRIMARY KEY,
    law_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    version_date TIMESTAMP DEFAULT now(),
    status_at_moment VARCHAR(50),
    text_hash VARCHAR(64) NOT NULL,
    plain_text TEXT,
    analysis_summary TEXT,
    risks_json JSONB,
    created_at TIMESTAMP DEFAULT now(),
    UNIQUE(law_id, text_hash)
);

CREATE INDEX IF NOT EXISTS idx_law_versions_law_id ON law_versions(law_id);
CREATE INDEX IF NOT EXISTS idx_law_versions_hash ON law_versions(text_hash);

-- Лог змін (для моніторингу)
CREATE TABLE IF NOT EXISTS change_log (
    id BIGSERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    change_type VARCHAR(20) NOT NULL,  -- 'new', 'status_change', 'text_change'
    old_value TEXT,
    new_value TEXT,
    created_at TIMESTAMP DEFAULT now(),
    notified BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_change_log_notified ON change_log(notified);
CREATE INDEX IF NOT EXISTS idx_change_log_bill_id ON change_log(bill_id);

-- Оцінки ризиків від LLM
CREATE TABLE IF NOT EXISTS risk_assessments (
    id SERIAL PRIMARY KEY,
    document_id INTEGER,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    assessed_at TIMESTAMP DEFAULT now(),
    model_used VARCHAR(100),
    budget_risk JSONB,
    legal_risk JSONB,
    economic_risk JSONB,
    social_risk JSONB,
    corruption_risk JSONB,
    overall_score NUMERIC(5,2),
    raw_response TEXT,
    raw_analysis TEXT,
    json_data JSONB,
    legislative_risk JSONB,
    official_power_risk JSONB,
    vague_norms_risk JSONB,
    confidence_level INTEGER DEFAULT 5,
    insufficient_text BOOLEAN DEFAULT false,
    UNIQUE(bill_id)
);

CREATE INDEX IF NOT EXISTS idx_risk_assessments_bill_id ON risk_assessments(bill_id);

-- Документи законів (PDF файли)
CREATE TABLE IF NOT EXISTS rag_documents (
    id SERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    doc_type VARCHAR(50),
    file_id VARCHAR(100),
    title TEXT,
    char_count INTEGER,
    content TEXT,
    risk_level VARCHAR(20),
    chunk_count INTEGER,
    annotations JSONB,
    content_hash VARCHAR(64),
    created_at TIMESTAMP DEFAULT now(),
    updated_at TIMESTAMP DEFAULT now()
);

-- Чанки тексту документів
CREATE TABLE IF NOT EXISTS rag_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES rag_documents(id) ON DELETE CASCADE,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT,
    section VARCHAR(50),
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_bill_id ON rag_chunks(bill_id);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_doc_id ON rag_chunks(document_id);

-- Документи з RADA API
CREATE TABLE IF NOT EXISTS bill_documents (
    id SERIAL PRIMARY KEY,
    bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    file_id VARCHAR(100),
    doc_type VARCHAR(50),
    created_at TIMESTAMP DEFAULT now()
);

-- Стан синхронізації (ETag)
CREATE TABLE IF NOT EXISTS sync_state (
    filename VARCHAR(100) PRIMARY KEY,
    etag VARCHAR(200),
    last_checked TIMESTAMP,
    last_downloaded TIMESTAMP
);
