-- ============================================================
-- AI Data Steward
-- Multi-column and dataset rule support
-- ============================================================

-- Existing rules are COLUMN scoped by default.
ALTER TABLE dq.rule
ADD COLUMN IF NOT EXISTS rule_scope TEXT NOT NULL DEFAULT 'COLUMN';

-- Stores every column involved in a rule.
-- Examples:
-- ["filing_date"]
-- ["filing_date", "expiration_date"]
-- ["status", "cancellation_date"]
ALTER TABLE dq.rule
ADD COLUMN IF NOT EXISTS target_columns JSONB;


-- Populate target_columns for existing column rules.
UPDATE dq.rule
SET target_columns = jsonb_build_array(column_name)
WHERE target_columns IS NULL
  AND column_name IS NOT NULL;


-- Limit rule_scope to supported values.
ALTER TABLE dq.rule
DROP CONSTRAINT IF EXISTS chk_rule_scope;

ALTER TABLE dq.rule
ADD CONSTRAINT chk_rule_scope
CHECK (
    rule_scope IN (
        'COLUMN',
        'ROW',
        'DATASET'
    )
);


-- Helpful indexes.
CREATE INDEX IF NOT EXISTS idx_rule_scope
ON dq.rule(rule_scope);

CREATE INDEX IF NOT EXISTS idx_rule_dataset_scope
ON dq.rule(dataset_name, rule_scope);
