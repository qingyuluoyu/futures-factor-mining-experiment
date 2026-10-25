-- Expand only, prerequisites: users, 20261023_015_stock_research_datasets,
-- 20261023_016_stock_research_v2_jobs and 20261025_019_stock_ml_registry.
-- Widens the 016 task contracts so the already implemented fixed-data research,
-- backtest, benchmark, native LambdaRank and report kernels can be dispatched as
-- first-class v2 jobs instead of staying an unmounted dataset-only workflow.
-- No historical backfill: every row registered before this migration keeps its
-- kind, method version and observation_only result until a new task writes one.
-- 019 tables are only referenced by the new result foreign key; their own DDL,
-- version row and schema are not rewritten here.
-- Explicit deployment transaction; never run automatically at application startup.
ALTER TABLE stock_research_v2_jobs DROP CONSTRAINT IF EXISTS ck_stock_v2_job_kind;
ALTER TABLE stock_research_v2_jobs ADD CONSTRAINT ck_stock_v2_job_kind CHECK (kind IN ('dataset','research_ic','backtest','benchmark','ml_train','report'));
ALTER TABLE stock_research_v2_jobs ADD COLUMN IF NOT EXISTS result_experiment_id UUID REFERENCES stock_ml_experiments(id) ON DELETE RESTRICT;
ALTER TABLE stock_research_v2_jobs ADD COLUMN IF NOT EXISTS result_report_sha256 VARCHAR(64);
ALTER TABLE stock_research_v2_jobs ADD COLUMN IF NOT EXISTS result_artifact_id UUID REFERENCES stock_research_artifacts(id) ON DELETE RESTRICT;
ALTER TABLE stock_research_v2_jobs DROP CONSTRAINT IF EXISTS ck_stock_v2_job_runtime;
ALTER TABLE stock_research_v2_jobs ADD CONSTRAINT ck_stock_v2_job_runtime CHECK (max_runtime_seconds BETWEEN 30 AND 900);
ALTER TABLE stock_research_v2_jobs DROP CONSTRAINT IF EXISTS ck_stock_v2_job_result;
ALTER TABLE stock_research_v2_jobs ADD CONSTRAINT ck_stock_v2_job_result CHECK ((status = 'completed' AND result_dataset_id IS NOT NULL AND result_data_version IS NOT NULL AND result_qualification IS NOT NULL AND length(result_data_version) = 64 AND result_qualification = 'observation_only' AND result_experiment_id IS NULL AND result_report_sha256 IS NULL AND result_artifact_id IS NULL) OR (status = 'completed' AND result_experiment_id IS NOT NULL AND result_dataset_id IS NULL AND result_data_version IS NULL AND result_qualification IS NULL AND result_report_sha256 IS NULL AND result_artifact_id IS NULL) OR (status = 'completed' AND result_artifact_id IS NOT NULL AND result_report_sha256 IS NOT NULL AND length(result_report_sha256) = 64 AND result_dataset_id IS NULL AND result_data_version IS NULL AND result_qualification IS NULL AND result_experiment_id IS NULL) OR (status <> 'completed' AND result_dataset_id IS NULL AND result_data_version IS NULL AND result_qualification IS NULL AND result_experiment_id IS NULL AND result_report_sha256 IS NULL AND result_artifact_id IS NULL));
ALTER TABLE stock_research_v2_workers DROP CONSTRAINT IF EXISTS ck_stock_v2_worker_name;
ALTER TABLE stock_research_v2_workers ADD CONSTRAINT ck_stock_v2_worker_name CHECK (name IN ('stock-v2-dataset-job-v1','stock-v2-research-task-v1'));
CREATE TABLE IF NOT EXISTS stock_research_v2_task_chain_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- The explicit migration runner must validate the reflected constraints against
-- app/models/stock_research_v2_job.py and register this file's SHA before commit.
-- This file alone does not certify ready: a missing version row must refuse service.
