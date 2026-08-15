-- Allow isolated rule-evaluation failures to be persisted explicitly.

BEGIN;

ALTER TABLE dq.result
DROP CONSTRAINT IF EXISTS result_status_check;

ALTER TABLE dq.result
ADD CONSTRAINT result_status_check
CHECK (result_status IN ('PASS', 'FAIL', 'SKIPPED', 'ERROR'));

COMMIT;
