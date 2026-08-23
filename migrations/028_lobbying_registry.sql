-- Реєстр прозорості НАЗК: суб'єкти лобіювання та їх об'єкти (закон №3606-20)
CREATE TABLE IF NOT EXISTS lobbying_subjects (
  guid text PRIMARY KEY,
  name text NOT NULL,
  edrpou text,
  subject_type integer,
  is_active boolean NOT NULL DEFAULT true,
  status integer,
  funding_source text,
  period_year integer,
  period_half_year integer,
  report_guid text,
  spheres jsonb NOT NULL DEFAULT '[]'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lobbying_objects (
  id serial PRIMARY KEY,
  subject_guid text NOT NULL REFERENCES lobbying_subjects(guid),
  sphere text,
  subject_of_lobbying text NOT NULL,
  government_agency text,
  agency_representative text,
  interactions_count integer NOT NULL DEFAULT 0,
  last_interaction date,
  bill_number text,
  report_guid text,
  UNIQUE (subject_guid, subject_of_lobbying, government_agency)
);
CREATE INDEX IF NOT EXISTS idx_lobbying_objects_bill ON lobbying_objects (bill_number) WHERE bill_number IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_lobbying_objects_recent ON lobbying_objects (last_interaction DESC NULLS LAST);
