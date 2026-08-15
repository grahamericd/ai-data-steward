-- End-to-end workflow lineage for stewardship pipeline executions.

BEGIN;

CREATE TABLE IF NOT EXISTS metadata.stewardship_run (
    stewardship_run_id BIGSERIAL PRIMARY KEY,
    dataset_id         INTEGER NOT NULL
                       REFERENCES metadata.dataset(dataset_id),
    dataset_name       TEXT NOT NULL,
    initiated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    initiated_by       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running',
    error_message      TEXT,
    CONSTRAINT stewardship_run_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS metadata.stewardship_run_phase (
    phase_id           BIGSERIAL PRIMARY KEY,
    stewardship_run_id BIGINT NOT NULL
                       REFERENCES metadata.stewardship_run(stewardship_run_id)
                       ON DELETE CASCADE,
    phase_name         TEXT NOT NULL,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    actor              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'running',
    load_run_id        BIGINT REFERENCES metadata.load_run(load_run_id),
    error_message      TEXT,
    CONSTRAINT stewardship_phase_name_check
        CHECK (phase_name IN ('load', 'profiling', 'rule_generation', 'evaluation', 'remediation')),
    CONSTRAINT stewardship_phase_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

ALTER TABLE metadata.load_run
ADD COLUMN IF NOT EXISTS stewardship_run_id BIGINT
    REFERENCES metadata.stewardship_run(stewardship_run_id);
ALTER TABLE dq.rule
ADD COLUMN IF NOT EXISTS stewardship_run_id BIGINT
    REFERENCES metadata.stewardship_run(stewardship_run_id);
ALTER TABLE dq.result
ADD COLUMN IF NOT EXISTS stewardship_run_id BIGINT
    REFERENCES metadata.stewardship_run(stewardship_run_id);
ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS stewardship_run_id BIGINT
    REFERENCES metadata.stewardship_run(stewardship_run_id);

CREATE INDEX IF NOT EXISTS idx_stewardship_run_dataset
    ON metadata.stewardship_run(dataset_name, initiated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stewardship_phase_run
    ON metadata.stewardship_run_phase(stewardship_run_id, phase_id);
CREATE INDEX IF NOT EXISTS idx_load_run_stewardship
    ON metadata.load_run(stewardship_run_id);
CREATE INDEX IF NOT EXISTS idx_result_stewardship
    ON dq.result(stewardship_run_id);
CREATE INDEX IF NOT EXISTS idx_remediation_stewardship
    ON dq.remediation_suggestion(stewardship_run_id);

COMMIT;
