-- Run this in Supabase SQL Editor before deploying these features.

CREATE TABLE IF NOT EXISTS staff_violations (
    id          bigserial PRIMARY KEY,
    staff_discord_id text NOT NULL,
    staff_username   text NOT NULL,
    order_id         int  REFERENCES orders(id) ON DELETE SET NULL,
    content          text NOT NULL,
    reason           text NOT NULL,
    created_at       timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_staff_violations_discord_id
    ON staff_violations (staff_discord_id);
CREATE INDEX IF NOT EXISTS idx_staff_violations_created_at
    ON staff_violations (created_at DESC);
