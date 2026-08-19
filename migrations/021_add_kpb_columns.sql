-- Міграція 021: Додавання MSI та K_pb до таблиці mps
-- msi — Majority Support Index (як часто депутат голосує з коаліцією)
-- kpb — Коефіцієнт політичного бар'єра (normalizований MSI)

ALTER TABLE mps ADD COLUMN msi REAL DEFAULT 0;
ALTER TABLE mps ADD COLUMN kpb REAL DEFAULT 1.0;
