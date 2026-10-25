CREATE TABLE stock_research_artifacts (
    id UUID PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    trading_mode VARCHAR(16) NOT NULL,
    idempotency_key UUID NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    request_id VARCHAR(32) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    byte_size INTEGER NOT NULL,
    relative_path VARCHAR(80) NOT NULL,
    purpose VARCHAR(16) NOT NULL,
    state VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_artifact_scope_key UNIQUE(owner_id,trading_mode,idempotency_key),
    CONSTRAINT ck_stock_artifact_mode CHECK (trading_mode IN ('virtual','live')),
    CONSTRAINT ck_stock_artifact_state CHECK (state IN ('ready','quarantined')),
    CONSTRAINT ck_stock_artifact_purpose CHECK (purpose IN ('source','dataset')),
    CONSTRAINT ck_stock_artifact_size CHECK (byte_size BETWEEN 1 AND 67108864),
    CONSTRAINT ck_stock_artifact_path CHECK (length(sha256) = 64 AND relative_path = sha256 || '.json')
);
CREATE INDEX ix_stock_artifact_scope ON stock_research_artifacts(owner_id,trading_mode,created_at,id);
CREATE TABLE stock_research_sources (
    id UUID PRIMARY KEY,
    artifact_id UUID NOT NULL REFERENCES stock_research_artifacts(id) ON DELETE RESTRICT,
    kind VARCHAR(16) NOT NULL,
    stream_key VARCHAR(200) NOT NULL,
    revises_id UUID REFERENCES stock_research_sources(id) ON DELETE RESTRICT,
    row_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_source_artifact UNIQUE(artifact_id),
    CONSTRAINT ck_stock_source_kind CHECK (kind IN ('daily','valuation','financial')),
    CONSTRAINT ck_stock_source_rows CHECK (row_count BETWEEN 1 AND 10000)
);
CREATE INDEX ix_stock_source_stream ON stock_research_sources(stream_key,created_at,id);
CREATE TABLE stock_research_datasets (
    id UUID PRIMARY KEY,
    manifest_artifact_id UUID NOT NULL REFERENCES stock_research_artifacts(id) ON DELETE RESTRICT,
    data_version VARCHAR(64) NOT NULL,
    qualification VARCHAR(24) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_stock_dataset_manifest UNIQUE(manifest_artifact_id),
    CONSTRAINT ck_stock_dataset_qualification CHECK (qualification = 'observation_only'),
    CONSTRAINT ck_stock_dataset_version CHECK (length(data_version) = 64)
);
CREATE TABLE stock_research_dataset_sources (
    dataset_id UUID NOT NULL REFERENCES stock_research_datasets(id) ON DELETE RESTRICT,
    source_id UUID NOT NULL REFERENCES stock_research_sources(id) ON DELETE RESTRICT,
    PRIMARY KEY(dataset_id,source_id)
);
