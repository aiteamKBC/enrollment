-- Eligibility Form submission schema repair (independent of Workplace Phase 2).
-- Additive and rerunnable: no defaults, inferred answers, updates or dropped columns.
-- Review/deploy separately in shared environments. Never run from the public API.
BEGIN;
ALTER TABLE enrolment.eligibility_form
    ADD COLUMN IF NOT EXISTS start_date_with_employer date,
    ADD COLUMN IF NOT EXISTS job_title text,
    ADD COLUMN IF NOT EXISTS country_of_residence text,
    ADD COLUMN IF NOT EXISTS nationality text,
    ADD COLUMN IF NOT EXISTS resident_prev_3_years boolean,
    ADD COLUMN IF NOT EXISTS years_in_uk text,
    ADD COLUMN IF NOT EXISTS full_time_education_prior boolean,
    ADD COLUMN IF NOT EXISTS expected_leaving_date date;
COMMIT;
