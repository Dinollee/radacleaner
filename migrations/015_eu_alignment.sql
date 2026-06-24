-- 015: Таблиці для EU Alignment Score
-- Зберігає результати порівняння українського законодавства з нормами ЄС

-- Загальні результати Alignment Score
CREATE TABLE IF NOT EXISTS eu_alignment_overall (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    overall_score REAL NOT NULL DEFAULT 0,
    weighted_score REAL NOT NULL DEFAULT 0,
    chapters_analyzed INTEGER NOT NULL DEFAULT 0,
    total_chapters INTEGER NOT NULL DEFAULT 35,
    calculated_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Результати по главах EU acquis
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

-- Класифікація законів до глав EU acquis
CREATE TABLE IF NOT EXISTS bill_eu_classification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    matched_keywords TEXT,
    classified_at TEXT NOT NULL,
    FOREIGN KEY (bill_id) REFERENCES bills(id)
);

-- Індекси
CREATE INDEX IF NOT EXISTS idx_eua_overall_date ON eu_alignment_overall(calculated_at);
CREATE INDEX IF NOT EXISTS idx_eua_chapter_id ON eu_alignment_chapters(chapter_id);
CREATE INDEX IF NOT EXISTS idx_eua_chapter_date ON eu_alignment_chapters(calculated_at);
CREATE INDEX IF NOT EXISTS idx_beuc_bill_id ON bill_eu_classification(bill_id);
CREATE INDEX IF NOT EXISTS idx_beuc_chapter ON bill_eu_classification(chapter_id);
