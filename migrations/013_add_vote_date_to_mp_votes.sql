-- 013: Add vote_date to mp_votes for faster sorting (avoid JOIN with votes)
-- This denormalization eliminates the expensive JOIN for deputy detail queries.

ALTER TABLE mp_votes ADD COLUMN vote_date TEXT;

CREATE INDEX IF NOT EXISTS idx_mv_mp_name_date ON mp_votes(mp_name, vote_date);
