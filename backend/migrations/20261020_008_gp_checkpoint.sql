CREATE TABLE gp_checkpoint_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE gp_checkpoint_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    market_type VARCHAR(8) NOT NULL,
    trading_mode VARCHAR(8) NOT NULL,
    idempotency_key UUID NOT NULL,
    input_json JSONB NOT NULL,
    input_hash CHAR(64) NOT NULL,
    config_json JSONB NOT NULL,
    config_hash CHAR(64) NOT NULL,
    checkpoint_json JSONB,
    checkpoint_hash CHAR(64),
    generation INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_gp_checkpoint_scope_key UNIQUE(user_id, market_type, trading_mode, idempotency_key),
    CONSTRAINT ck_gp_checkpoint_market CHECK (market_type = 'futures'),
    CONSTRAINT ck_gp_checkpoint_mode CHECK (trading_mode = 'live' OR trading_mode = 'virtual'),
    CONSTRAINT ck_gp_checkpoint_generation CHECK (generation >= 0 AND generation <= 200 AND revision = generation),
    CONSTRAINT ck_gp_checkpoint_state CHECK ((generation = 0 AND checkpoint_json IS NULL AND checkpoint_hash IS NULL) OR (generation > 0 AND checkpoint_json IS NOT NULL AND checkpoint_hash IS NOT NULL))
);
