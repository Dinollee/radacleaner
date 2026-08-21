-- 025: bot_subscribers — персональные подписки на пуш-уведомления бота
CREATE TABLE IF NOT EXISTS bot_subscribers (
    chat_id BIGINT PRIMARY KEY,
    attacks BOOLEAN NOT NULL DEFAULT false,
    digest BOOLEAN NOT NULL DEFAULT false,
    subscribed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
