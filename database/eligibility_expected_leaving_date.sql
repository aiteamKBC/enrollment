-- PREPARED ONLY: review and apply before accepting an expected leaving date.
-- No historical values are inferred or updated. Do not run on the shared DB
-- without explicit deployment authorisation. Existing blank/No submissions
-- continue to use the original INSERT columns until this upgrade is applied.
BEGIN;
ALTER TABLE enrolment.eligibility_form
    ADD COLUMN IF NOT EXISTS expected_leaving_date date;
COMMIT;
