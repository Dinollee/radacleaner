-- 024: Info attack monitor (Phase 1 collector)
-- info_items: сырые посты из фактчекеров (RSS) и деструктивных telegram-каналов
-- attack_alerts: детектированные синхронные инфоатаки (фаза 2)

CREATE TABLE IF NOT EXISTS info_items (
    id SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('factcheck', 'telegram')),
    source_name TEXT NOT NULL,
    url TEXT UNIQUE,
    title TEXT NOT NULL,
    body TEXT,
    posted_at TIMESTAMPTZ,
    simhash BIGINT,
    cluster_id INT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ii_posted ON info_items(posted_at);
CREATE INDEX IF NOT EXISTS idx_ii_cluster ON info_items(cluster_id);

CREATE TABLE IF NOT EXISTS attack_alerts (
    id SERIAL PRIMARY KEY,
    first_item_id INT REFERENCES info_items(id),
    label TEXT,
    channels_count INT,
    posts_count INT,
    window_hours NUMERIC,
    debunk_url TEXT,
    related_bill_number TEXT,
    alert_sent BOOLEAN DEFAULT false,
    detected_at TIMESTAMPTZ DEFAULT now()
);
