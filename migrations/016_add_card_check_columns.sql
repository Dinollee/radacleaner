-- Add columns to track Card page check state for sync_period optimization
ALTER TABLE bills ADD COLUMN last_card_check TEXT;
ALTER TABLE bills ADD COLUMN card_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_bills_last_card_check ON bills(last_card_check);
