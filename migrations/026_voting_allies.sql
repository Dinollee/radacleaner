-- voting_allies — парная согласованность голосований депутатов (клубы голосования)
-- Канонический порядок: mp_a < mp_b. pct = agree/common по позициям за/против/воздержался.
CREATE TABLE IF NOT EXISTS voting_allies (
  mp_a integer NOT NULL REFERENCES mps(id),
  mp_b integer NOT NULL REFERENCES mps(id),
  common integer NOT NULL,
  agree integer NOT NULL,
  pct real NOT NULL,
  cross_faction boolean NOT NULL DEFAULT false,
  calculated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mp_a, mp_b)
);
CREATE INDEX IF NOT EXISTS idx_voting_allies_cross ON voting_allies (cross_faction, pct DESC);
CREATE INDEX IF NOT EXISTS idx_voting_allies_a ON voting_allies (mp_a, pct DESC);
CREATE INDEX IF NOT EXISTS idx_voting_allies_b ON voting_allies (mp_b, pct DESC);
