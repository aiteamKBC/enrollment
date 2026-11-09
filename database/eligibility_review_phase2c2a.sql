-- Phase 2C.2A: PREPARED ONLY. Apply after Phase 2A and Phase 2B SQL.
-- Review/backup the target first. Never executed by the public or staff APIs.
-- Keep ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=false in live environments;
-- DEBUG=False also prevents decision writes. No existing row is reclassified.
-- Original screening fields, submission fingerprints and receipts stay intact.
-- The review queue can still read older tables before this upgrade is applied.
BEGIN;
ALTER TABLE enrolment.eligibility_form
    ADD COLUMN IF NOT EXISTS review_status text CONSTRAINT eligibility_review_status_check
        CHECK (review_status IN ('approved', 'rejected', 'more_evidence_required')),
    ADD COLUMN IF NOT EXISTS review_version integer CONSTRAINT eligibility_review_version_check
        CHECK (review_version >= 1);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'enrolment.eligibility_form'::regclass AND conname = 'eligibility_review_consistency') THEN
        ALTER TABLE enrolment.eligibility_form ADD CONSTRAINT eligibility_review_consistency CHECK (
            (review_status IS NULL AND review_version IS NULL) OR
            (review_status IS NOT NULL AND review_version IS NOT NULL AND
             assessment_status IS NOT DISTINCT FROM 'pending_assessment' AND eligible IS NULL)
        );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS enrolment.eligibility_application_reviews (
    id bigserial PRIMARY KEY,
    application_id bigint NOT NULL REFERENCES enrolment.eligibility_form(id) ON DELETE RESTRICT,
    version integer NOT NULL CHECK (version >= 1),
    request_id uuid NOT NULL,
    reviewer_id bigint NOT NULL CHECK (reviewer_id >= 1),
    reviewer_name text NOT NULL CHECK (char_length(btrim(reviewer_name)) BETWEEN 1 AND 200),
    previous_status text NOT NULL,
    new_status text NOT NULL CHECK (new_status IN ('approved', 'rejected', 'more_evidence_required')),
    reason text NOT NULL CHECK (char_length(btrim(reason)) BETWEEN 1 AND 2000),
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT eligibility_review_application_version UNIQUE (application_id, version),
    CONSTRAINT eligibility_review_application_request UNIQUE (application_id, request_id),
    CONSTRAINT eligibility_review_transition CHECK (
        (previous_status = 'pending_assessment' AND new_status IN ('approved', 'rejected', 'more_evidence_required')) OR
        (previous_status = 'more_evidence_required' AND new_status IN ('approved', 'rejected'))
    )
);

-- Reviewer id is a stable authenticated account id; name is a historical
-- snapshot. No FK to a mutable/deletable login identity can erase the audit.
-- Normal application credentials cannot edit/delete/truncate history.
CREATE OR REPLACE FUNCTION enrolment.reject_eligibility_review_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
    RAISE EXCEPTION 'Eligibility review history is append-only';
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'enrolment.eligibility_application_reviews'::regclass AND tgname = 'eligibility_reviews_no_edit') THEN
        CREATE TRIGGER eligibility_reviews_no_edit BEFORE UPDATE OR DELETE ON enrolment.eligibility_application_reviews
            FOR EACH ROW EXECUTE FUNCTION enrolment.reject_eligibility_review_mutation();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'enrolment.eligibility_application_reviews'::regclass AND tgname = 'eligibility_reviews_no_truncate') THEN
        CREATE TRIGGER eligibility_reviews_no_truncate BEFORE TRUNCATE ON enrolment.eligibility_application_reviews
            FOR EACH STATEMENT EXECUTE FUNCTION enrolment.reject_eligibility_review_mutation();
    END IF;
END $$;
COMMIT;
