CREATE TABLE stock_eod_recoveries (
    id UUID PRIMARY KEY,
    source_batch_id UUID NOT NULL,
    idempotency_key UUID NOT NULL,
    request_hash CHAR(64) NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    operator_ref VARCHAR(64) NOT NULL,
    reason VARCHAR(160) NOT NULL,
    method_version VARCHAR(64) NOT NULL,
    source_data_version CHAR(64) NOT NULL,
    before_row JSONB NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL,
    source_calls INTEGER NOT NULL,
    CONSTRAINT uq_eod_recovery_key UNIQUE (idempotency_key),
    CONSTRAINT uq_eod_recovery_batch UNIQUE (source_batch_id),
    CONSTRAINT ck_eod_recovery_no_source CHECK (source_calls = 0)
);
