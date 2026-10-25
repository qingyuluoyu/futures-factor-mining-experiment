CREATE TABLE gp_job_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE gp_checkpoint_jobs (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    market_type VARCHAR(8) NOT NULL,
    trading_mode VARCHAR(8) NOT NULL,
    input_id UUID NOT NULL REFERENCES gp_checkpoint_sessions(id) ON DELETE RESTRICT,
    idempotency_key UUID NOT NULL,
    request_hash CHAR(64) NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    title VARCHAR(80) NOT NULL,
    status VARCHAR(16) NOT NULL,
    step VARCHAR(40) NOT NULL,
    generation INTEGER NOT NULL,
    total_generations INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    runtime_seconds DOUBLE PRECISION NOT NULL,
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    generation_deadline TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    method_version VARCHAR(64) NOT NULL,
    workflow_version VARCHAR(64) NOT NULL,
    result_json JSONB,
    result_hash CHAR(64),
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    queued_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_gp_job_scope_key UNIQUE(user_id, trading_mode, idempotency_key),
    CONSTRAINT uq_gp_job_input UNIQUE(input_id),
    CONSTRAINT ck_gp_job_market CHECK (market_type = 'futures'),
    CONSTRAINT ck_gp_job_mode CHECK (trading_mode = 'live' OR trading_mode = 'virtual'),
    CONSTRAINT ck_gp_job_status CHECK (status = 'pending' OR status = 'running' OR status = 'paused' OR status = 'completed' OR status = 'failed' OR status = 'cancelled' OR status = 'expired'),
    CONSTRAINT ck_gp_job_progress CHECK (generation >= 0 AND generation <= total_generations AND total_generations >= 1 AND total_generations <= 200 AND attempts >= 0 AND runtime_seconds >= 0 AND runtime_seconds <= 1800),
    CONSTRAINT ck_gp_job_lease CHECK ((lease_token IS NULL AND lease_until IS NULL AND generation_deadline IS NULL AND claimed_at IS NULL) OR (lease_token IS NOT NULL AND lease_until IS NOT NULL AND generation_deadline IS NOT NULL AND claimed_at IS NOT NULL)),
    CONSTRAINT ck_gp_job_running CHECK (status <> 'running' OR lease_token IS NOT NULL),
    CONSTRAINT ck_gp_job_result CHECK ((status = 'completed' AND result_json IS NOT NULL AND result_hash IS NOT NULL) OR (status <> 'completed' AND result_json IS NULL AND result_hash IS NULL))
);
CREATE INDEX ix_gp_jobs_queue ON gp_checkpoint_jobs(status, queued_at, id);
CREATE INDEX ix_gp_jobs_owner_created ON gp_checkpoint_jobs(user_id, trading_mode, created_at, id);
CREATE INDEX ix_gp_jobs_lease ON gp_checkpoint_jobs(lease_token);
