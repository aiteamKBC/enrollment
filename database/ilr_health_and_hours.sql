-- Apply only to the verified isolated local enrolment database for testing.
-- Existing values and signed snapshots are untouched.
BEGIN;
ALTER TABLE enrolment."Wizard_Ilr_Learner_Details"
    ADD COLUMN IF NOT EXISTS "Health_condition_codes" jsonb,
    ADD COLUMN IF NOT EXISTS "Primary_health_condition_code" text,
    ADD COLUMN IF NOT EXISTS "Additional_support_details" text,
    ADD COLUMN IF NOT EXISTS "Working_hours_per_week" numeric(5,2);
COMMIT;
