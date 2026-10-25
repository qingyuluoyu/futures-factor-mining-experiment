CREATE TABLE stock_replay_datasets (
    id UUID PRIMARY KEY,
    version VARCHAR(64) NOT NULL UNIQUE,
    code VARCHAR(6) NOT NULL,
    instrument_id VARCHAR(16) NOT NULL,
    name VARCHAR(32) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    bars_json JSONB NOT NULL,
    meta JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_stock_replay_dataset_dates CHECK (start_date <= end_date)
);

CREATE INDEX ix_stock_replay_dataset_code_created ON stock_replay_datasets (code, created_at);

CREATE TABLE stock_replay_accounts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trading_mode VARCHAR(16) NOT NULL,
    name VARCHAR(64) NOT NULL,
    initial_cash NUMERIC(18, 2) NOT NULL,
    cash NUMERIC(18, 2) NOT NULL,
    realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
    fees_paid NUMERIC(18, 2) NOT NULL DEFAULT 0,
    start_date DATE NOT NULL,
    "current_date" DATE NOT NULL,
    end_date DATE NOT NULL,
    datasets_json JSONB NOT NULL,
    positions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    settings_json JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'active',
    revision INTEGER NOT NULL DEFAULT 0,
    create_key UUID NOT NULL,
    params_hash VARCHAR(64) NOT NULL,
    rules_version VARCHAR(40) NOT NULL DEFAULT 'stock-replay-v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_replay_account_create UNIQUE (user_id, trading_mode, create_key),
    CONSTRAINT ck_stock_replay_account_status CHECK (status IN ('active', 'completed')),
    CONSTRAINT ck_stock_replay_account_mode CHECK (trading_mode IN ('live', 'virtual')),
    CONSTRAINT ck_stock_replay_account_revision CHECK (revision >= 0),
    CONSTRAINT ck_stock_replay_account_cash CHECK (initial_cash > 0 AND cash >= 0 AND fees_paid >= 0),
    CONSTRAINT ck_stock_replay_account_dates CHECK (start_date <= "current_date" AND "current_date" <= end_date)
);

CREATE INDEX ix_stock_replay_account_user_mode_created ON stock_replay_accounts (user_id, trading_mode, created_at);

CREATE TABLE stock_replay_orders (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES stock_replay_accounts(id) ON DELETE RESTRICT,
    idempotency_key UUID NOT NULL,
    params_hash VARCHAR(64) NOT NULL,
    code VARCHAR(6) NOT NULL,
    direction VARCHAR(8) NOT NULL,
    quantity INTEGER NOT NULL,
    order_type VARCHAR(8) NOT NULL,
    limit_price NUMERIC(18, 4),
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    reason VARCHAR(160),
    submitted_on DATE NOT NULL,
    filled_on DATE,
    filled_price NUMERIC(18, 4),
    fees NUMERIC(18, 2) NOT NULL DEFAULT 0,
    cash_delta NUMERIC(18, 2),
    realized_pnl NUMERIC(18, 2) NOT NULL DEFAULT 0,
    reserved_cash NUMERIC(18, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_replay_order_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT ck_stock_replay_order_direction CHECK (direction IN ('buy', 'sell')),
    CONSTRAINT ck_stock_replay_order_type CHECK (order_type IN ('market', 'limit')),
    CONSTRAINT ck_stock_replay_order_status CHECK (status IN ('pending', 'filled', 'rejected', 'cancelled')),
    CONSTRAINT ck_stock_replay_order_quantity CHECK (quantity > 0 AND quantity % 100 = 0),
    CONSTRAINT ck_stock_replay_order_amounts CHECK (fees >= 0 AND reserved_cash >= 0),
    CONSTRAINT ck_stock_replay_order_limit CHECK (
        (order_type = 'market' AND limit_price IS NULL) OR (order_type = 'limit' AND limit_price IS NOT NULL AND limit_price > 0)
    )
);

CREATE INDEX ix_stock_replay_order_account_status_created ON stock_replay_orders (account_id, status, created_at);

CREATE TABLE stock_replay_steps (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES stock_replay_accounts(id) ON DELETE RESTRICT,
    idempotency_key UUID NOT NULL,
    kind VARCHAR(16) NOT NULL,
    from_revision INTEGER NOT NULL,
    params_hash VARCHAR(64) NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_replay_step_key UNIQUE (account_id, idempotency_key),
    CONSTRAINT uq_stock_replay_step_revision UNIQUE (account_id, from_revision),
    CONSTRAINT ck_stock_replay_step_kind CHECK (kind IN ('advance', 'cancel')),
    CONSTRAINT ck_stock_replay_step_revision CHECK (from_revision >= 0)
);
