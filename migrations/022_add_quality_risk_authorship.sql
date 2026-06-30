-- Migration 022: Add quality/risk/authorship columns to mps
-- avg_risk_score — average risk_score from analyzed bills
-- authorship_ratio — share of bills where deputy is primary author (order=0)

ALTER TABLE mps ADD COLUMN avg_risk_score REAL DEFAULT 0;
ALTER TABLE mps ADD COLUMN authorship_ratio REAL DEFAULT 0;
