-- Additive structured Eligibility addresses. Apply only to a verified target.
-- Existing full_address/employer_address values are neither split nor updated.
-- Nullable columns preserve historical applications; new requiredness is enforced
-- by the current form/backend. Postcodes and countries reuse existing columns.
BEGIN;
ALTER TABLE enrolment.eligibility_form
    ADD COLUMN IF NOT EXISTS employer_address_line1 text,
    ADD COLUMN IF NOT EXISTS employer_address_line2 text,
    ADD COLUMN IF NOT EXISTS employer_address_line3 text,
    ADD COLUMN IF NOT EXISTS employer_town_city text,
    ADD COLUMN IF NOT EXISTS employer_county text,
    ADD COLUMN IF NOT EXISTS residential_address_line1 text,
    ADD COLUMN IF NOT EXISTS residential_address_line2 text,
    ADD COLUMN IF NOT EXISTS residential_address_line3 text,
    ADD COLUMN IF NOT EXISTS residential_town_city text,
    ADD COLUMN IF NOT EXISTS residential_county text;
COMMIT;
