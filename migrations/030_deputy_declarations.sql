-- Декларації НАЗК депутатів: корпоративні права (компанії) з останньої декларації
CREATE TABLE IF NOT EXISTS deputy_declarations (
  mp_id integer PRIMARY KEY REFERENCES mps(id),
  uuid text NOT NULL,
  submitted_at text,
  declaration_year integer,
  companies jsonb NOT NULL DEFAULT '[]'::jsonb,
  synced_at timestamptz NOT NULL DEFAULT now()
);
