-- deputy_interests — профиль интересов депутата по LLM-разметке interest_sectors
CREATE TABLE IF NOT EXISTS deputy_interests (
  mp_id integer NOT NULL REFERENCES mps(id),
  sector text NOT NULL,
  authored integer NOT NULL DEFAULT 0,
  voted_for integer NOT NULL DEFAULT 0,
  voted_against integer NOT NULL DEFAULT 0,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mp_id, sector)
);
CREATE INDEX IF NOT EXISTS idx_deputy_interests_sector ON deputy_interests (sector, authored DESC);
