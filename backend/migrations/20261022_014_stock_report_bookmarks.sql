CREATE TABLE stock_report_bookmarks (
    job_id UUID PRIMARY KEY REFERENCES stock_research_jobs(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_stock_report_bookmark_created ON stock_report_bookmarks (created_at, job_id);
