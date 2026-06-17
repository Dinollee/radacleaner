ALTER TABLE bills ADD COLUMN is_procedural INTEGER DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_bills_is_procedural ON bills(is_procedural);

UPDATE bills SET is_procedural = 1
WHERE agenda_category IN ('Організаційні питання', 'Інші (заяви, звернення ВРУ)')
  AND is_procedural IS NULL;

UPDATE bills SET is_procedural = 0
WHERE agenda_category NOT IN ('Організаційні питання', 'Інші (заяви, звернення ВРУ)')
  AND agenda_category IS NOT NULL
  AND agenda_category != ''
  AND is_procedural IS NULL;

UPDATE bills SET is_procedural = 1
WHERE id IN (
  SELECT ra.bill_id FROM risk_assessments ra
  WHERE ra.json_data LIKE '%"is_procedural": true%'
     OR ra.json_data LIKE '%"is_procedural":true%'
)
AND is_procedural IS NULL;

UPDATE bills SET is_procedural = 0
WHERE id IN (
  SELECT ra.bill_id FROM risk_assessments ra
  WHERE ra.json_data LIKE '%"is_procedural": false%'
     OR ra.json_data LIKE '%"is_procedural":false%'
)
AND is_procedural IS NULL;
