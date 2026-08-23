-- Портрет депутата: LLM-узагальнення даних моніторингу (заміна шаблонних сигналів)
ALTER TABLE mps ADD COLUMN IF NOT EXISTS portrait text;
ALTER TABLE mps ADD COLUMN IF NOT EXISTS portrait_signals jsonb;
ALTER TABLE mps ADD COLUMN IF NOT EXISTS portrait_at timestamptz;
