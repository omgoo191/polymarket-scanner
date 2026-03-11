-- Polymarket Smart Money Radar — DB Schema
-- Run automatically on first docker-compose up

CREATE TABLE IF NOT EXISTS markets (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    slug            TEXT,
    end_time        TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE,
    insider_risk    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(is_active);
CREATE INDEX IF NOT EXISTS idx_markets_insider ON markets(insider_risk);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    tx_hash         TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    trader          TEXT NOT NULL,           -- wallet address
    outcome         TEXT,                    -- YES / NO / token name
    side            TEXT,                    -- BUY / SELL
    size_usd        NUMERIC(20, 4),          -- notional in USD
    price           NUMERIC(10, 6),          -- outcome price (0–1)
    timestamp       TIMESTAMPTZ NOT NULL,
    raw             JSONB,                   -- full raw record for debugging
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tx_hash, trader, market_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_market   ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_trader   ON trades(trader);
CREATE INDEX IF NOT EXISTS idx_trades_ts       ON trades(timestamp DESC);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS funding_events (
    id              BIGSERIAL PRIMARY KEY,
    tx_hash         TEXT NOT NULL UNIQUE,
    to_address      TEXT NOT NULL,           -- the trader wallet that got funded
    from_address    TEXT,                    -- source wallet
    amount_usd      NUMERIC(20, 4),
    timestamp       TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_funding_to    ON funding_events(to_address);
CREATE INDEX IF NOT EXISTS idx_funding_ts    ON funding_events(timestamp DESC);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS wallet_profiles (
    address         TEXT PRIMARY KEY,
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    total_trades    INTEGER DEFAULT 0,
    total_volume    NUMERIC(20, 4) DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    trader          TEXT NOT NULL,
    score           INTEGER NOT NULL,
    severity        TEXT NOT NULL,           -- STRONG / MEDIUM
    reasons         JSONB,                   -- list of reason strings
    trade_ids       JSONB,                   -- list of trade IDs that triggered
    sent_at         TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_market  ON alerts(market_id);
CREATE INDEX IF NOT EXISTS idx_alerts_trader  ON alerts(trader);
CREATE INDEX IF NOT EXISTS idx_alerts_sent    ON alerts(sent_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS price_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT NOT NULL,
    outcome         TEXT,
    price           NUMERIC(10, 6),
    timestamp       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_market ON price_snapshots(market_id, timestamp DESC);
