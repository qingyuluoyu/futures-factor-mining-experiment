CREATE TABLE stock_eod_batches (
    id UUID PRIMARY KEY,
    trade_date DATE NOT NULL UNIQUE,
    status VARCHAR(24) NOT NULL,
    step VARCHAR(40) NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    attempts INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    deadline_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    method_version VARCHAR(64) NOT NULL,
    data_version CHAR(64),
    snapshot_json JSONB NOT NULL,
    error_code VARCHAR(64),
    error_message VARCHAR(500),
    CONSTRAINT ck_stock_eod_status CHECK (status = 'running' OR status = 'completed' OR status = 'partially_completed' OR status = 'failed' OR status = 'no_data'),
    CONSTRAINT ck_stock_eod_attempts CHECK (attempts = 1),
    CONSTRAINT ck_stock_eod_finished CHECK ((status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL))
);
CREATE INDEX ix_stock_eod_latest ON stock_eod_batches (status, trade_date);
