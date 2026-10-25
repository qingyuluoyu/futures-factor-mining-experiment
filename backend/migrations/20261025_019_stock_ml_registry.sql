-- Additive private ML calculation history only; no holdout certification.

CREATE TABLE IF NOT EXISTS stock_ml_experiments (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	trading_mode VARCHAR(16) NOT NULL, 
	idempotency_key UUID NOT NULL, 
	request_id VARCHAR(32) NOT NULL, 
	dataset_id UUID NOT NULL, 
	data_version VARCHAR(64) NOT NULL, 
	report_sha256 VARCHAR(64) NOT NULL, 
	report_byte_size INTEGER NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	validation_status VARCHAR(24) NOT NULL, 
	fold_count INTEGER NOT NULL, 
	detail_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_stock_ml_experiment_scope_key UNIQUE (owner_id, trading_mode, idempotency_key), 
	CONSTRAINT ck_stock_ml_experiment_mode CHECK (trading_mode IN ('virtual','live')), 
	CONSTRAINT ck_stock_ml_experiment_state CHECK (state IN ('ready','quarantined')), 
	CONSTRAINT ck_stock_ml_experiment_validation CHECK (validation_status IN ('qa_only','research_only')), 
	CONSTRAINT ck_stock_ml_experiment_folds CHECK (fold_count BETWEEN 1 AND 5), 
	CONSTRAINT ck_stock_ml_experiment_hashes CHECK (length(report_sha256) = 64 AND length(data_version) = 64), 
	CONSTRAINT ck_stock_ml_experiment_report_size CHECK (report_byte_size BETWEEN 1 AND 67108864), 
	CONSTRAINT ck_stock_ml_experiment_detail_size CHECK (length(detail_json) BETWEEN 1 AND 524288), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE RESTRICT, 
	FOREIGN KEY(dataset_id) REFERENCES stock_research_datasets (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_stock_ml_experiment_scope ON stock_ml_experiments (owner_id, trading_mode, created_at, id);

CREATE TABLE IF NOT EXISTS stock_ml_registry_schema_versions (
	version VARCHAR(64) NOT NULL, 
	checksum VARCHAR(64) NOT NULL, 
	applied_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (version)
);
