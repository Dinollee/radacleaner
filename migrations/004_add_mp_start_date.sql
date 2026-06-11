-- Додаємо дату вступу на посаду для депутатів
ALTER TABLE mps ADD COLUMN start_date TEXT;
ALTER TABLE mps ADD COLUMN end_date TEXT;

CREATE INDEX IF NOT EXISTS idx_mps_start_date ON mps(start_date);
