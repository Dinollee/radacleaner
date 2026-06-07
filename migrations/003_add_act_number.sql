-- Додаємо номер акту та дату прийняття для прийнятих законів
-- actNumber з RADA API (наприклад: 4121-IX, 3650-IX)
ALTER TABLE bills ADD COLUMN act_number TEXT;
ALTER TABLE bills ADD COLUMN act_date TEXT;

CREATE INDEX IF NOT EXISTS idx_bills_act_number ON bills(act_number);
