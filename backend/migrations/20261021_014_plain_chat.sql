CREATE TABLE plain_chat_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum CHAR(64) NOT NULL
);

CREATE TABLE plain_chat_requests (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    trading_mode VARCHAR(16) NOT NULL CHECK (trading_mode IN ('live', 'virtual')),
    market_type VARCHAR(16) NOT NULL CHECK (market_type = 'futures'),
    model_record_id UUID NOT NULL,
    params_hash CHAR(64) NOT NULL,
    prompt_version VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'unknown')),
    error_code VARCHAR(64),
    response_text TEXT NOT NULL DEFAULT '',
    usage_json JSONB,
    cost_state VARCHAR(16) NOT NULL DEFAULT 'unknown' CHECK (cost_state = 'unknown'),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deadline_at TIMESTAMPTZ NOT NULL DEFAULT (CURRENT_TIMESTAMP + INTERVAL '90 seconds'),
    finished_at TIMESTAMPTZ,
    CHECK ((status = 'running' AND finished_at IS NULL) OR (status <> 'running' AND finished_at IS NOT NULL))
);

CREATE UNIQUE INDEX uq_plain_chat_user_running ON plain_chat_requests (user_id) WHERE status = 'running';
CREATE INDEX ix_plain_chat_user_created ON plain_chat_requests (user_id, started_at DESC);
