-- Expand only, prerequisites: users and 015 stock_research_datasets.
-- Explicit deployment transaction; never run automatically at application startup.
CREATE TABLE IF NOT EXISTS stock_research_columnar_datasets (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trading_mode VARCHAR(16) NOT NULL,
    dataset_id UUID NOT NULL REFERENCES stock_research_datasets(id) ON DELETE RESTRICT,
    idempotency_key UUID NOT NULL,
    input_hash VARCHAR(64) NOT NULL,
    manifest_sha256 VARCHAR(64) NOT NULL,
    manifest_json JSONB NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    state VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_columnar_scope_key UNIQUE(owner_id,trading_mode,idempotency_key),
    CONSTRAINT ck_stock_columnar_mode CHECK (trading_mode IN ('virtual','live')),
    CONSTRAINT ck_stock_columnar_state CHECK (state IN ('ready','quarantined')),
    CONSTRAINT ck_stock_columnar_hash CHECK (length(input_hash) = 64 AND length(manifest_sha256) = 64)
);
CREATE INDEX IF NOT EXISTS ix_stock_columnar_dataset_scope ON stock_research_columnar_datasets(owner_id,trading_mode,dataset_id);
CREATE TABLE IF NOT EXISTS stock_research_columnar_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- The explicit migration runner must validate the complete reflected schema and
-- register this file's SHA before commit. This file alone does not certify ready.
