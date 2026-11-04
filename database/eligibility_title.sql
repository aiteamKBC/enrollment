-- Additive only. Historical applications retain NULL; never infer a Title.
-- Apply before serving code that writes the new Eligibility Title field.
BEGIN;
ALTER TABLE enrolment.eligibility_form ADD COLUMN IF NOT EXISTS title text;
COMMIT;
