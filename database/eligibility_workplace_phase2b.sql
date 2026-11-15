-- Phase 2B: PREPARED ONLY. Run after the Phase 2A upgrade, following target
-- verification and normal backup/review. Never applied by the public endpoint.
-- Keep the preview flag OFF in live deployments; DEBUG=False still forces OFF.
-- No existing application is updated or reclassified. New columns default NULL.
-- The unique nullable fingerprint protects new preview applications only;
-- historical records remain outside deduplication. Response receipts contain
-- no applicant answers and allow identical retries to reuse the same row.
-- Screening statuses/constraints remain unchanged.

BEGIN;
ALTER TABLE enrolment.eligibility_form
    DROP CONSTRAINT IF EXISTS eligibility_assessment_status_reserved,
    DROP CONSTRAINT IF EXISTS eligibility_assessment_reason_reserved,
    ADD COLUMN IF NOT EXISTS submission_fingerprint text
        CONSTRAINT eligibility_submission_fingerprint_check CHECK (submission_fingerprint ~ '^[0-9a-f]{64}$')
        CONSTRAINT eligibility_submission_fingerprint_unique UNIQUE,
    ADD COLUMN IF NOT EXISTS submission_response jsonb
        CONSTRAINT eligibility_submission_response_check CHECK (jsonb_typeof(submission_response) = 'object');

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'enrolment.eligibility_form'::regclass AND conname = 'eligibility_assessment_status_check') THEN
        ALTER TABLE enrolment.eligibility_form ADD CONSTRAINT eligibility_assessment_status_check
            CHECK (assessment_status IN ('provisional_eligible', 'pending_assessment', 'not_eligible'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'enrolment.eligibility_form'::regclass AND conname = 'eligibility_assessment_reason_length') THEN
        ALTER TABLE enrolment.eligibility_form ADD CONSTRAINT eligibility_assessment_reason_length
            CHECK (char_length(assessment_reason) BETWEEN 1 AND 1000);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'enrolment.eligibility_form'::regclass AND conname = 'eligibility_assessment_consistency') THEN
        ALTER TABLE enrolment.eligibility_form ADD CONSTRAINT eligibility_assessment_consistency CHECK (
            assessment_status IS NULL OR
            (assessment_status = 'pending_assessment' AND eligible IS NULL AND assessment_reason IS NOT NULL) OR
            (assessment_status = 'provisional_eligible' AND eligible IS TRUE AND assessment_reason IS NULL) OR
            (assessment_status = 'not_eligible' AND eligible IS FALSE AND assessment_reason IS NOT NULL)
        );
    END IF;
END $$;
COMMIT;
