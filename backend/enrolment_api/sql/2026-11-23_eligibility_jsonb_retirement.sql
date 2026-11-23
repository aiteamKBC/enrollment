-- Retire per-question columns only after the JSONB readers and writers are deployed.
-- Historical non-null values must already exist identically in answers.
-- Keep identity, screening, receipt, version and programme metadata columns.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
DO $migration$
DECLARE
    old_column text;
    missing_count bigint;
    retired_columns text[] := ARRAY[
        'phone_number',
        'full_address',
        'postcode',
        'postcode_country',
        'postcode_region',
        'postcode_district',
        'date_of_birth',
        'employment_status',
        'majority_hours_in_england',
        'employer_name',
        'employer_address',
        'employer_postcode',
        'employer_postcode_country',
        'employer_postcode_region',
        'employer_postcode_district',
        'representative_name',
        'representative_email',
        'lives_in_uk',
        'residence_not_for_education',
        'residency_status',
        'programme',
        'attended_funded_training',
        'funded_training_completed_on',
        'not_previously_completed_programme',
        'start_date_with_employer',
        'job_title',
        'country_of_residence',
        'nationality',
        'resident_prev_3_years',
        'years_in_uk',
        'full_time_education_prior',
        'expected_leaving_date',
        'employer_address_line1',
        'employer_address_line2',
        'employer_address_line3',
        'employer_town_city',
        'employer_county',
        'residential_address_line1',
        'residential_address_line2',
        'residential_address_line3',
        'residential_town_city',
        'residential_county',
        'title',
        'workplace_arrangement',
        'main_workplace_postcode',
        'main_workplace_country',
        'main_workplace_region',
        'main_workplace_district'
    ];
BEGIN
    IF EXISTS (
        SELECT 1 FROM enrolment.eligibility_form
        WHERE answers IS NULL OR jsonb_typeof(answers) <> 'object'
    ) THEN
        RAISE EXCEPTION 'Eligibility answers JSONB is missing or invalid';
    END IF;

    FOREACH old_column IN ARRAY retired_columns LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns c
            WHERE c.table_schema = 'enrolment' AND c.table_name = 'eligibility_form'
              AND c.column_name = old_column
        ) THEN
            EXECUTE format(
                'SELECT count(*) FROM enrolment.eligibility_form a
                 WHERE to_jsonb(a)->%L IS NOT NULL
                   AND to_jsonb(a)->%L <> ''null''::jsonb
                   AND to_jsonb(a)->%L IS DISTINCT FROM a.answers->%L',
                old_column, old_column, old_column, old_column
            ) INTO missing_count;
            IF missing_count <> 0 THEN
                RAISE EXCEPTION 'Eligibility column % has % values absent or different in JSONB',
                    old_column, missing_count;
            END IF;
            EXECUTE format('ALTER TABLE enrolment.eligibility_form DROP COLUMN %I', old_column);
        END IF;
    END LOOP;
END
$migration$;
COMMIT;
