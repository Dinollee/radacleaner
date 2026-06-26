-- Міграція 018: Додавання KPI колонок до таблиці mps
-- lei — Legislative Effectiveness Index
-- avg_s — середній significance
-- avg_i — середній impact
-- avg_tox — середній toxicity

ALTER TABLE mps ADD COLUMN lei REAL DEFAULT 0;
ALTER TABLE mps ADD COLUMN avg_s REAL DEFAULT 0;
ALTER TABLE mps ADD COLUMN avg_i REAL DEFAULT 0;
ALTER TABLE mps ADD COLUMN avg_tox REAL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_mps_lei ON mps(lei);
