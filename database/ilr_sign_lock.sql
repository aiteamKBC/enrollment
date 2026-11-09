-- Apply only to a verified isolated enrolment database before deploying the
-- Sign + Next code. This does not infer finalisation from historical signatures.
BEGIN;
ALTER TABLE enrolment."Wizard_Ilr_Learner_Details"
    ADD COLUMN IF NOT EXISTS "Ilr_finalised_at" timestamptz,
    ADD COLUMN IF NOT EXISTS "Ilr_finalised_by" bigint,
    ADD COLUMN IF NOT EXISTS "Ilr_signed_snapshot" jsonb;
COMMIT;
