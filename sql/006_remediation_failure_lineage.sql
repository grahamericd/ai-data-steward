-- Anchor remediation suggestions to the exact failed rule evaluation.

BEGIN;

ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS result_id INTEGER
    REFERENCES dq.result(id) ON DELETE CASCADE;

ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS failed_record_id BIGINT
    REFERENCES dq.failed_record(id) ON DELETE CASCADE;

ALTER TABLE dq.remediation_suggestion
ADD COLUMN IF NOT EXISTS generation_method TEXT
    NOT NULL DEFAULT 'deterministic';

ALTER TABLE dq.remediation_suggestion
DROP CONSTRAINT IF EXISTS remediation_generation_method_check;

ALTER TABLE dq.remediation_suggestion
ADD CONSTRAINT remediation_generation_method_check
CHECK (generation_method IN ('deterministic', 'llm_assisted'));

-- NOT VALID preserves legacy rows, while PostgreSQL still enforces the
-- lineage requirement for every new or updated remediation.
ALTER TABLE dq.remediation_suggestion
DROP CONSTRAINT IF EXISTS remediation_lineage_required;

ALTER TABLE dq.remediation_suggestion
ADD CONSTRAINT remediation_lineage_required
CHECK (
    rule_id IS NOT NULL
    AND result_id IS NOT NULL
    AND failed_record_id IS NOT NULL
) NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_remediation_failed_record
    ON dq.remediation_suggestion(failed_record_id)
    WHERE failed_record_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_remediation_result
    ON dq.remediation_suggestion(result_id);

COMMIT;
