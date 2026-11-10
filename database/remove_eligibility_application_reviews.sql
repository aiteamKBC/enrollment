-- Prepared cleanup for the retired application staff-review feature.
-- Run only after checking the target database and approving this operation.
-- The guards refuse to discard any recorded staff decisions.
BEGIN;

DO $$
DECLARE
    review_rows bigint;
    reviewed_applications bigint;
BEGIN
    IF to_regclass('enrolment.eligibility_application_reviews') IS NOT NULL THEN
        SELECT count(*) INTO review_rows FROM enrolment.eligibility_application_reviews;
        IF review_rows <> 0 THEN
            RAISE EXCEPTION 'Refusing to remove % recorded application reviews', review_rows;
        END IF;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'enrolment' AND table_name = 'eligibility_form'
          AND column_name IN ('review_status', 'review_version')
    ) THEN
        SELECT count(*) INTO reviewed_applications
        FROM enrolment.eligibility_form
        WHERE to_jsonb(eligibility_form)->>'review_status' IS NOT NULL
           OR to_jsonb(eligibility_form)->>'review_version' IS NOT NULL;
        IF reviewed_applications <> 0 THEN
            RAISE EXCEPTION 'Refusing to remove review state from % applications', reviewed_applications;
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS enrolment.eligibility_application_reviews;
ALTER TABLE enrolment.eligibility_form
    DROP CONSTRAINT IF EXISTS eligibility_review_consistency,
    DROP COLUMN IF EXISTS review_status,
    DROP COLUMN IF EXISTS review_version;
DROP FUNCTION IF EXISTS enrolment.reject_eligibility_review_mutation();

COMMIT;
