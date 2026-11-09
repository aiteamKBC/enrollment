-- Additive only. Apply to a verified isolated environment before testing.
-- No historical backfill, application decisions, or account changes.
BEGIN;
CREATE TABLE IF NOT EXISTS enrolment.eligibility_learner_links (
    application_id bigint PRIMARY KEY REFERENCES enrolment.eligibility_form(id) ON DELETE RESTRICT,
    learner_id bigint NOT NULL UNIQUE REFERENCES enrolment."Created_users"(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    activation_verified_at timestamptz,
    invitation_status text NOT NULL DEFAULT 'pending' CHECK (invitation_status IN ('pending', 'sending', 'sent', 'failed')),
    invitation_claim uuid,
    invitation_claimed_at timestamptz,
    invitation_error text
);
COMMIT;
