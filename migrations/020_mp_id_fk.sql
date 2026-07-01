-- Міграція 020: Перехід на mp_id (FK) замість mp_name
-- Єдина точка правди — таблиця mps. Всі зв'язки через mps.id.

-- 1. Додаємо mp_id до mp_votes
ALTER TABLE mp_votes ADD COLUMN mp_id INTEGER;
UPDATE mp_votes mv SET mp_id = m.id FROM mps m WHERE mv.rada_uid = m.rada_uid;
CREATE INDEX idx_mv_mp_id ON mp_votes(mp_id);

-- 2. Додаємо mp_id до mp_bills
ALTER TABLE mp_bills ADD COLUMN mp_id INTEGER;
UPDATE mp_bills mb SET mp_id = m.id FROM mps m WHERE mb.rada_uid = m.rada_uid;
CREATE INDEX idx_mb_mp_id ON mp_bills(mp_id);

-- 3. Додаємо mp_id до bill_sponsors
ALTER TABLE bill_sponsors ADD COLUMN mp_id INTEGER;
UPDATE bill_sponsors bs SET mp_id = m.id FROM mps m WHERE bs.rada_uid = m.rada_uid;
CREATE INDEX idx_bs_mp_id ON bill_sponsors(mp_id);

-- 4. Додаємо FK обмеження
ALTER TABLE mp_votes ADD CONSTRAINT fk_mv_mp FOREIGN KEY (mp_id) REFERENCES mps(id);
ALTER TABLE mp_bills ADD CONSTRAINT fk_mb_mp FOREIGN KEY (mp_id) REFERENCES mps(id);
ALTER TABLE bill_sponsors ADD CONSTRAINT fk_bs_mp FOREIGN KEY (mp_id) REFERENCES mps(id);
