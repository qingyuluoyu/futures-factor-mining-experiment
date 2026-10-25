CREATE TABLE stock_archive_jobs (
    id UUID PRIMARY KEY,
    source_batch_id UUID NOT NULL,
    source_data_version CHAR(64) NOT NULL,
    method_version VARCHAR(64) NOT NULL,
    trade_date DATE NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    step VARCHAR(32) NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    deadline_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    archive_batch_id UUID,
    error_code VARCHAR(64),
    error_message VARCHAR(300),
    CONSTRAINT uq_stock_archive_job_source_method UNIQUE (source_batch_id, method_version),
    CONSTRAINT ck_stock_archive_job_status CHECK (status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'),
    CONSTRAINT ck_stock_archive_job_step CHECK ((status = 'running' AND step = 'archiving') OR (status = 'completed' AND step = 'completed') OR (status = 'failed' AND step = 'failed') OR (status = 'cancelled' AND step = 'cancelled')),
    CONSTRAINT ck_stock_archive_job_attempts CHECK (attempts = 1),
    CONSTRAINT ck_stock_archive_job_times CHECK (deadline_at > started_at AND updated_at >= started_at),
    CONSTRAINT ck_stock_archive_job_finished CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT ck_stock_archive_job_result CHECK ((status = 'running' AND finished_at IS NULL AND archive_batch_id IS NULL AND error_code IS NULL AND error_message IS NULL) OR (status = 'completed' AND finished_at IS NOT NULL AND archive_batch_id IS NOT NULL AND error_code IS NULL AND error_message IS NULL) OR ((status = 'failed' OR status = 'cancelled') AND finished_at IS NOT NULL AND archive_batch_id IS NULL AND error_code IS NOT NULL))
);
CREATE INDEX ix_stock_archive_job_status_deadline ON stock_archive_jobs (status, deadline_at);
CREATE INDEX ix_stock_archive_job_started ON stock_archive_jobs (started_at);
