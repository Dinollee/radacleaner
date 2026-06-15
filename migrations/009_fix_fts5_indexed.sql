-- Fix FTS5: bill_number and act_number were UNINDEXED (not searchable)
-- Recreate with all columns indexed for proper search

DROP TRIGGER IF EXISTS bills_ai;
DROP TRIGGER IF EXISTS bills_ad;
DROP TRIGGER IF EXISTS bills_au;
DROP TABLE IF EXISTS bills_fts;

CREATE VIRTUAL TABLE bills_fts USING fts5(
    bill_number,
    title,
    act_number,
    content='bills',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

INSERT INTO bills_fts (rowid, bill_number, title, act_number)
SELECT id, bill_number, title, act_number FROM bills;

CREATE TRIGGER bills_ai AFTER INSERT ON bills BEGIN
    INSERT INTO bills_fts (rowid, bill_number, title, act_number)
    VALUES (new.id, new.bill_number, new.title, new.act_number);
END;

CREATE TRIGGER bills_ad AFTER DELETE ON bills BEGIN
    INSERT INTO bills_fts (bills_fts, rowid, bill_number, title, act_number)
    VALUES ('delete', old.id, old.bill_number, old.title, old.act_number);
END;

CREATE TRIGGER bills_au AFTER UPDATE ON bills BEGIN
    INSERT INTO bills_fts (bills_fts, rowid, bill_number, title, act_number)
    VALUES ('delete', old.id, old.bill_number, old.title, old.act_number);
    INSERT INTO bills_fts (rowid, bill_number, title, act_number)
    VALUES (new.id, new.bill_number, new.title, new.act_number);
END;
