-- Дедуп активної черги аналізу: один bill не може бути pending/running двічі.
-- Помилка-дубль 2026-08-20: закон 10399 стояв у черзі двічі одночасно.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_active
ON pending_analysis (bill_id)
WHERE status IN ('pending', 'running');
