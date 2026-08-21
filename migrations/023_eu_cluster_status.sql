-- EU accession negotiation cluster status (index v1)
CREATE TABLE IF NOT EXISTS eu_cluster_status (
    cluster_id INT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'not_opened'
        CHECK (status IN ('not_opened','opened','provisionally_closed')),
    event_date DATE,
    source_url TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO eu_cluster_status (cluster_id, status, event_date, source_url) VALUES
    (1, 'opened', '2026-06-15', 'https://enlargement.ec.europa.eu/news/eu-and-ukraine-open-first-accession-negotiations-cluster-2026-06-15_en'),
    (2, 'not_opened', NULL, NULL),
    (3, 'not_opened', NULL, NULL),
    (4, 'not_opened', NULL, NULL),
    (5, 'not_opened', NULL, NULL),
    (6, 'opened', '2026-07-14', 'https://enlargement.ec.europa.eu/news/enlargement-eu-opens-accession-negotiations-ukraine-external-relations-policies-2026-07-14_en')
ON CONFLICT (cluster_id) DO NOTHING;
