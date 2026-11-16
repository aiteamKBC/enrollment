-- Apply only after deploying code that no longer maps these columns.
-- The five columns were verified to contain no nonempty values before cleanup.
-- Re-running is safe; do not use CASCADE.
BEGIN;
ALTER TABLE enrolment."Created_users"
    DROP COLUMN IF EXISTS "Referrer",
    DROP COLUMN IF EXISTS "Referrer_address",
    DROP COLUMN IF EXISTS "Referrer_contact",
    DROP COLUMN IF EXISTS "Learning_provider";
ALTER TABLE enrolment."Staff_users"
    DROP COLUMN IF EXISTS "Learning_provider";
COMMIT;
