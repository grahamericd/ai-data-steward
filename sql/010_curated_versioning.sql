-- Immutable curated snapshots with field-level change and raw-row lineage.

BEGIN;

CREATE TABLE IF NOT EXISTS curated.remediation_run (
    remediation_run_id BIGSERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES metadata.dataset(dataset_id),
    dataset_name TEXT NOT NULL,
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    initiated_by TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    remediation_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    CONSTRAINT curated_remediation_run_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS curated.dataset_version (
    curated_version_id BIGSERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES metadata.dataset(dataset_id),
    dataset_name TEXT NOT NULL,
    remediation_run_id BIGINT NOT NULL REFERENCES curated.remediation_run(remediation_run_id),
    stewardship_run_id BIGINT REFERENCES metadata.stewardship_run(stewardship_run_id),
    previous_version_id BIGINT REFERENCES curated.dataset_version(curated_version_id),
    physical_table_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'building',
    row_count BIGINT,
    CONSTRAINT curated_version_status_check
        CHECK (status IN ('building', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS curated.row_lineage (
    curated_version_id BIGINT NOT NULL REFERENCES curated.dataset_version(curated_version_id) ON DELETE CASCADE,
    source_row_identifier TEXT NOT NULL,
    raw_load_run_id BIGINT,
    source_file TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (curated_version_id, source_row_identifier)
);

CREATE TABLE IF NOT EXISTS curated.change_history (
    change_id BIGSERIAL PRIMARY KEY,
    curated_version_id BIGINT NOT NULL REFERENCES curated.dataset_version(curated_version_id) ON DELETE CASCADE,
    previous_version_id BIGINT REFERENCES curated.dataset_version(curated_version_id),
    remediation_id INTEGER NOT NULL REFERENCES dq.remediation_suggestion(id),
    rule_id INTEGER NOT NULL REFERENCES dq.rule(id),
    result_id INTEGER NOT NULL REFERENCES dq.result(id),
    failed_record_id BIGINT NOT NULL REFERENCES dq.failed_record(id),
    source_row_identifier TEXT NOT NULL,
    column_name TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS applied_in_remediation_run_id BIGINT
    REFERENCES curated.remediation_run(remediation_run_id);

CREATE INDEX IF NOT EXISTS idx_curated_version_dataset
    ON curated.dataset_version(dataset_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_curated_change_version
    ON curated.change_history(curated_version_id, change_id);
CREATE INDEX IF NOT EXISTS idx_curated_lineage_source
    ON curated.row_lineage(source_row_identifier);

COMMIT;
