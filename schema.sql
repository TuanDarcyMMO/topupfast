-- ============================================================
-- TopUpFast Bot - Database Schema (PostgreSQL / Supabase)
-- Chạy file này trong Supabase SQL Editor
-- ============================================================

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    discord_id  TEXT      UNIQUE NOT NULL,
    avatar_url  TEXT,
    balance     FLOAT     NOT NULL DEFAULT 0.0,
    language    TEXT      NOT NULL DEFAULT 'vi',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Transactions table
CREATE TABLE IF NOT EXISTS transactions (
    id          BIGSERIAL PRIMARY KEY,
    discord_id  TEXT    NOT NULL,
    user_id     BIGINT  NOT NULL REFERENCES users(id),

    type        TEXT    NOT NULL CHECK(type IN ('bank', 'crypto')),
    provider    TEXT    NOT NULL CHECK(provider IN ('sepay', 'nowpayments')),
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'completed', 'failed', 'expired', 'cancelled')),

    amount_usd  FLOAT   NOT NULL DEFAULT 0.0,
    amount_vnd  BIGINT           DEFAULT 0,
    currency    TEXT,

    coin        TEXT,
    provider_ref TEXT,
    invoice_url  TEXT,
    tfa_code    TEXT,
    qr_url      TEXT,

    discord_channel_id  TEXT,
    discord_message_id  TEXT,

    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Games table
-- Mỗi game có 1 dòng. Thêm game mới = INSERT vào đây.
CREATE TABLE IF NOT EXISTS games (
    id          TEXT    PRIMARY KEY,            -- e.g. 'roblox', 'genshin', 'mlbb'
    name        TEXT    NOT NULL,               -- tên hiển thị
    emoji       TEXT    NOT NULL DEFAULT '🎮',
    icon_url    TEXT,                           -- URL ảnh từ Supabase Storage
    platform    TEXT    NOT NULL DEFAULT 'all'
                        CHECK(platform IN ('all', 'ios')),  -- 'ios' = chỉ iOS, 'all' = cả 2
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order  INT     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Packages table
-- Mỗi gói nạp = 1 dòng. id dạng: {game_id}.{type}.{tier}
-- Ví dụ: 'roblox.robux.400', 'genshin.crystal.980', 'mlbb.diamond.86'
CREATE TABLE IF NOT EXISTS packages (
    id          TEXT    PRIMARY KEY,            -- 'roblox.robux.400'
    game_id     TEXT    NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    category    TEXT    NOT NULL,               -- 'robux', 'crystal', 'diamond'
    name        TEXT    NOT NULL,               -- '400 Robux'
    price_usd   FLOAT   NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order  INT     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_packages_game_id  ON packages(game_id);
CREATE INDEX IF NOT EXISTS idx_packages_category ON packages(game_id, category);
CREATE INDEX IF NOT EXISTS idx_packages_active   ON packages(active);

-- Games và packages được INSERT riêng sau khi tạo bảng.


CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    discord_id      TEXT    NOT NULL,
    user_id         BIGINT  NOT NULL REFERENCES users(id),

    game_id         TEXT    NOT NULL,   -- ví dụ: 'roblox', 'genshin'
    package_id      TEXT    NOT NULL,   -- ví dụ: 'rbl_400'
    package_name    TEXT    NOT NULL,   -- ví dụ: '400 Robux'
    price_usd       FLOAT   NOT NULL,

    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending', 'delivering', 'completed', 'failed', 'refunded')),

    -- Thông tin tài khoản game do khách nhập
    game_account    TEXT,               -- username / UID / server+uid
    game_account_note TEXT,             -- ghi chú thêm (optional)

    -- Ghi chú giao hàng (bot/admin điền)
    delivery_note   TEXT,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ghi lại ai nhận và hoàn thành đơn
ALTER TABLE orders ADD COLUMN IF NOT EXISTS claimed_by       TEXT;   -- staff discord_id
ALTER TABLE orders ADD COLUMN IF NOT EXISTS completed_by     TEXT;   -- staff discord_id
ALTER TABLE orders ADD COLUMN IF NOT EXISTS claimed_at       TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS completed_at     TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS ticket_channel_id TEXT;  -- Discord ticket channel ID

CREATE INDEX IF NOT EXISTS idx_orders_discord_id ON orders(discord_id);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders(status);

-- Staff table: cộng tác viên và admin dashboard
CREATE TABLE IF NOT EXISTS staff (
    id              BIGSERIAL PRIMARY KEY,
    discord_id      TEXT    UNIQUE NOT NULL,    -- Discord user ID
    username        TEXT    UNIQUE NOT NULL,    -- login username
    password_hash   TEXT    NOT NULL,           -- bcrypt hash
    role            TEXT    NOT NULL DEFAULT 'staff'
                            CHECK(role IN ('admin', 'staff')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default admin account (password: Trumblack2k7 → bcrypt hash)
-- Run: python -c "import bcrypt; print(bcrypt.hashpw(b'Trumblack2k7', bcrypt.gensalt()).decode())"
-- then replace the hash below before running schema
INSERT INTO staff (discord_id, username, password_hash, role)
VALUES ('0', 'anhtrung', '$2b$12$gdqPB2JCtYjt9wE.NcuCROFREwRFLFiRVEtamAefRmuElP6DFp/Iu', 'admin')
ON CONFLICT (username) DO NOTHING;

-- Staff commission balance (added to staff table)
ALTER TABLE staff ADD COLUMN IF NOT EXISTS commission_balance DECIMAL(10,2) NOT NULL DEFAULT 0;

-- Commission tracking on orders
ALTER TABLE orders ADD COLUMN IF NOT EXISTS commission_paid    BOOLEAN     NOT NULL DEFAULT FALSE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS commission_amount  DECIMAL(10,2);
-- status enum extension for refund_requested
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE orders ADD  CONSTRAINT orders_status_check
    CHECK(status IN ('pending','delivering','completed','failed','refunded','refund_requested'));

-- Staff bank accounts
CREATE TABLE IF NOT EXISTS staff_bank_accounts (
    id              BIGSERIAL PRIMARY KEY,
    staff_discord_id TEXT NOT NULL,
    bank_name       TEXT NOT NULL,
    account_number  TEXT NOT NULL,
    account_holder  TEXT NOT NULL,
    branch          TEXT,
    qr_image        TEXT,   -- base64 data URL, stored directly
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_bank_accounts_staff ON staff_bank_accounts(staff_discord_id);

-- Withdrawal requests
CREATE TABLE IF NOT EXISTS withdrawal_requests (
    id              BIGSERIAL PRIMARY KEY,
    staff_discord_id TEXT NOT NULL,
    staff_username  TEXT NOT NULL,
    bank_account_id BIGINT REFERENCES staff_bank_accounts(id),
    amount_usd      DECIMAL(10,2) NOT NULL,
    amount_vnd      BIGINT        NOT NULL,
    exchange_rate   DECIMAL(10,2) NOT NULL DEFAULT 26500,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','completed','rejected')),
    admin_note      TEXT,
    completed_by    TEXT,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_withdrawals_staff  ON withdrawal_requests(staff_discord_id);
CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawal_requests(status);
