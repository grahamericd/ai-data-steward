-- Relational row-level failures for remediation and lineage workflows.

BEGIN;

CREATE TABLE IF NOT EXISTS dq.failed_record (
    id                      BIGSERIAL PRIMARY KEY,
    result_id               INTEGER NOT NULL
                            REFERENCES dq.result(id)
                            ON DELETE CASCADE,
    rule_id                 INTEGER
                            REFERENCES dq.rule(id)
                            ON DELETE SET NULL,
    dataset_name            TEXT NOT NULL,
    source_row_identifier   TEXT,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_failed_record_result
    ON dq.failed_record(result_id);

CREATE INDEX IF NOT EXISTS idx_failed_record_rule
    ON dq.failed_record(rule_id);

CREATE INDEX IF NOT EXISTS idx_failed_record_dataset_row
    ON dq.failed_record(dataset_name, source_row_identifier);

COMMIT;
