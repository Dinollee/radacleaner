-- Міграція 017: Додавання метрик ризику в таблицю bills
-- significance, impact, risk_score — оцінки від LLM (1-5)
-- toxicity — обчислюється як significance × impact × risk_score / 25 (0.0-1.0)

ALTER TABLE bills ADD COLUMN significance INTEGER DEFAULT NULL;
ALTER TABLE bills ADD COLUMN impact INTEGER DEFAULT NULL;
ALTER TABLE bills ADD COLUMN risk_score INTEGER DEFAULT NULL;
ALTER TABLE bills ADD COLUMN toxicity REAL DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_bills_toxicity ON bills(toxicity);
CREATE INDEX IF NOT EXISTS idx_bills_significance ON bills(significance);
