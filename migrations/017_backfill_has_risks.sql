-- Бекфіл has_risks для непроцедурних аналізів, де модель (nemotron) пропустила ключ.
-- Без цього ключа фронтенд не рендерив блок ризиків (246 рядків станом на 2026-08-21).
UPDATE risk_assessments
SET json_data = jsonb_set(
        json_data::jsonb, '{has_risks}',
        to_jsonb(
            jsonb_array_length(COALESCE(json_data::jsonb->'detailed_risks','[]'::jsonb)) > 0
            OR (json_data::jsonb->>'risk_level') IS NOT NULL
        ))::text
WHERE json_data IS NOT NULL
  AND NOT json_data::jsonb ? 'has_risks'
  AND COALESCE(json_data::jsonb->>'is_procedural','false') = 'false';
