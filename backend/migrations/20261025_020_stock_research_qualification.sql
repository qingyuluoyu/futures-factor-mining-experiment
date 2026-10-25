-- Expand only, prerequisites: users and 20261023_015_stock_research_datasets.
-- Relaxes the stock research dataset qualification grade so an evidence-certified
-- dataset can be registered above observation_only. No historical backfill: rows
-- already registered as observation_only keep that grade until an authorized
-- certification re-registers them (master plan §2.6).
-- Explicit deployment transaction; never run automatically at application startup.
ALTER TABLE stock_research_datasets DROP CONSTRAINT IF EXISTS ck_stock_dataset_qualification;
ALTER TABLE stock_research_datasets ADD CONSTRAINT ck_stock_dataset_qualification CHECK (qualification IN ('observation_only','research_only','backtest_ready'));
CREATE TABLE IF NOT EXISTS stock_research_qualification_schema_versions (
    version VARCHAR(64) PRIMARY KEY,
    checksum VARCHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- The explicit migration runner must validate the reflected constraint against
-- app/models/stock_research_dataset.py and register this file's SHA before commit.
-- This file alone does not certify ready: a missing version row must refuse service.
