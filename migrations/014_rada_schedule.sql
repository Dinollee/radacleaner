-- 014: RADA schedule (plenary sessions, committees, coordination councils, holidays)
CREATE TABLE IF NOT EXISTS rada_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,              -- YYYY-MM-DD
    event_type TEXT NOT NULL,        -- plenary, committee, coordination, holidays, question_day, extraordinary, voter_work
    title TEXT NOT NULL,             -- short title (e.g. "Пленарне засідання")
    description TEXT,                -- detailed description (committee name, topic)
    url TEXT,                        -- link to agenda/details
    session TEXT,                    -- e.g. "15-та сесія IX скликання"
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_schedule_date ON rada_schedule(date);
CREATE INDEX IF NOT EXISTS idx_schedule_type ON rada_schedule(event_type);
CREATE INDEX IF NOT EXISTS idx_schedule_date_type ON rada_schedule(date, event_type);

-- Weekly committee schedules
CREATE TABLE IF NOT EXISTS rada_committee_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,        -- YYYY-MM-DD (Monday of the week)
    committee_name TEXT NOT NULL,
    meeting_date TEXT NOT NULL,      -- YYYY-MM-DD
    meeting_time TEXT,               -- HH:MM
    topic TEXT,                      -- what's being discussed
    room TEXT,                       -- meeting room
    url TEXT,                        -- source URL
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cs_week ON rada_committee_schedule(week_start);
CREATE INDEX IF NOT EXISTS idx_cs_date ON rada_committee_schedule(meeting_date);
