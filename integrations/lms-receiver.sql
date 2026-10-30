-- Apply only to the verified existing LMS database during reviewed deployment.
-- Additive receipt ledger; no learner/study table is changed by this migration.
CREATE SCHEMA IF NOT EXISTS enrollment_integration;
CREATE TABLE IF NOT EXISTS enrollment_integration.handoff_receipts (
    learner_uuid uuid PRIMARY KEY,
    enrolment_id bigint NOT NULL,
    payload_hash text NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT now()
);
