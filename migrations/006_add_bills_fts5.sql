-- Full-text search index for bills
CREATE VIRTUAL TABLE IF NOT EXISTS bills_fts USING fts5(
    bill_number UNINDEXED,
    title,
    act_number UNINDEXED,
    content='bills',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Populate FTS from existing bills
INSERT INTO bills_fts (rowid, bill_number, title, act_number)
SELECT id, bill_number, title, act_number FROM bills;

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS bills_ai AFTER INSERT ON bills BEGIN
    INSERT INTO bills_fts (rowid, bill_number, title, act_number)
    VALUES (new.id, new.bill_number, new.title, new.act_number);
END;

CREATE TRIGGER IF NOT EXISTS bills_ad AFTER DELETE ON bills BEGIN
    INSERT INTO bills_fts (bills_fts, rowid, bill_number, title, act_number)
    VALUES ('delete', old.id, old.bill_number, old.title, old.act_number);
END;

CREATE TRIGGER IF NOT EXISTS bills_au AFTER UPDATE ON bills BEGIN
    INSERT INTO bills_fts (bills_fts, rowid, bill_number, title, act_number)
    VALUES ('delete', old.id, old.bill_number, old.title, old.act_number);
    INSERT INTO bills_fts (rowid, bill_number, title, act_number)
    VALUES (new.id, new.bill_number, new.title, new.act_number);
END;
