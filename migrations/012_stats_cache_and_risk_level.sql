-- Міграція 012: Кеш статистики + risk_level колонка

-- Таблиця кешу статистики дашборду
CREATE TABLE IF NOT EXISTS stats_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Заповнюємо початковими даними
INSERT OR IGNORE INTO stats_cache (key, value) VALUES
    ('total_bills', '0'),
    ('high_risk', '0'),
    ('medium_risk', '0'),
    ('analyzed_bills', '0'),
    ('procedural_bills', '0'),
    ('total_votes', '0'),
    ('total_mps', '0'),
    ('active_mps_30d', '0'),
    ('new_bills_24h', '0'),
    ('status_changes_24h', '0'),
    ('recent_changes', '0'),
    ('by_stage', '[]'),
    ('last_updated', '');

-- risk_level колонка в risk_assessments (замість LIKE по json_data)
ALTER TABLE risk_assessments ADD COLUMN risk_level TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_ra_risk_level ON risk_assessments(risk_level);
