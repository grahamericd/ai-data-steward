-- Append-only stewardship decisions and rule-generation provenance.

BEGIN;

ALTER TABLE dq.rule ADD COLUMN IF NOT EXISTS llm_provider TEXT;
ALTER TABLE dq.rule ADD COLUMN IF NOT EXISTS llm_model TEXT;
ALTER TABLE dq.rule ADD COLUMN IF NOT EXISTS prompt_version TEXT;
ALTER TABLE dq.rule ADD COLUMN IF NOT EXISTS decision_by TEXT;
ALTER TABLE dq.rule ADD COLUMN IF NOT EXISTS decision_at TIMESTAMP;

ALTER TABLE dq.remediation_suggestion
DROP CONSTRAINT IF EXISTS remediation_suggestion_rule_id_fkey;

ALTER TABLE dq.remediation_suggestion
ADD CONSTRAINT remediation_suggestion_rule_id_fkey
FOREIGN KEY (rule_id) REFERENCES dq.rule(id) ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS dq.rule_audit (
    id                BIGSERIAL PRIMARY KEY,
    rule_id           INTEGER NOT NULL
                      REFERENCES dq.rule(id) ON DELETE CASCADE,
    previous_status   TEXT NOT NULL,
    new_status        TEXT NOT NULL,
    changed_by        TEXT NOT NULL,
    changed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decision_note     TEXT
);

ALTER TABLE dq.remediation_audit
ADD COLUMN IF NOT EXISTS previous_status TEXT;
ALTER TABLE dq.remediation_audit
ADD COLUMN IF NOT EXISTS new_status TEXT;
ALTER TABLE dq.remediation_audit
ADD COLUMN IF NOT EXISTS changed_by TEXT;
ALTER TABLE dq.remediation_audit
ADD COLUMN IF NOT EXISTS changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE dq.remediation_audit
ADD COLUMN IF NOT EXISTS decision_note TEXT;

-- The original prototype columns remain nullable for compatibility.
ALTER TABLE dq.remediation_audit ALTER COLUMN action_taken DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_rule_audit_rule_changed
    ON dq.rule_audit(rule_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_remediation_audit_changed
    ON dq.remediation_audit(remediation_id, changed_at DESC);

COMMIT;
