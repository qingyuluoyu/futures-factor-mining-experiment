CREATE TABLE stock_custom_factors (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trading_mode VARCHAR(16) NOT NULL,
    latest_revision INTEGER NOT NULL,
    disabled BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_stock_custom_factor_mode CHECK (trading_mode IN ('virtual','live')),
    CONSTRAINT ck_stock_custom_factor_revision CHECK (latest_revision >= 1)
);
CREATE INDEX ix_stock_custom_factor_scope ON stock_custom_factors(owner_id,trading_mode,created_at,id);
CREATE TABLE stock_custom_factor_versions (
    factor_id UUID NOT NULL REFERENCES stock_custom_factors(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL,
    name VARCHAR(80) NOT NULL,
    expression TEXT NOT NULL,
    expression_version VARCHAR(64) NOT NULL,
    formula_version VARCHAR(64) NOT NULL,
    fields_json JSON NOT NULL,
    lookback INTEGER NOT NULL,
    direction VARCHAR(8) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(factor_id,revision),
    CONSTRAINT ck_stock_custom_version_revision CHECK (revision >= 1),
    CONSTRAINT ck_stock_custom_version_direction CHECK (direction IN ('high','low')),
    CONSTRAINT ck_stock_custom_version_lookback CHECK (lookback BETWEEN 0 AND 244)
);
CREATE TABLE stock_custom_factor_mutations (
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trading_mode VARCHAR(16) NOT NULL,
    idempotency_key UUID NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    result_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(owner_id,trading_mode,idempotency_key),
    CONSTRAINT ck_stock_custom_mutation_mode CHECK (trading_mode IN ('virtual','live'))
);
