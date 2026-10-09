-- Migration 031: laws_signed subscription toggle for bot_subscribers
-- Adds opt-out for signed-law citizen_impact push notifications.
-- Default TRUE — feature ships enabled; users can opt out via /sub.

ALTER TABLE bot_subscribers ADD COLUMN IF NOT EXISTS laws_signed BOOLEAN NOT NULL DEFAULT TRUE;
