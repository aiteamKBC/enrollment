-- Review and approve before applying to the configured enrolment database.
-- Additive metadata only. Existing applications, links, and screening decisions stay intact.
BEGIN;

ALTER TABLE enrolment.eligibility_learner_links
    ADD COLUMN IF NOT EXISTS override_approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS override_approved_by_account_id bigint,
    ADD COLUMN IF NOT EXISTS override_reason text;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'enrolment.eligibility_learner_links'::regclass
          AND conname = 'eligibility_link_override_complete'
    ) THEN
        ALTER TABLE enrolment.eligibility_learner_links
            ADD CONSTRAINT eligibility_link_override_complete CHECK (
                (override_approved_at IS NULL AND override_approved_by_account_id IS NULL AND override_reason IS NULL)
                OR (override_approved_at IS NOT NULL AND override_approved_by_account_id IS NOT NULL
                    AND nullif(btrim(override_reason), '') IS NOT NULL)
            );
    END IF;
END $$;

-- Historical eligible learners who accepted an invitation before this release
-- should not be stranded by the new Fresh-user gate. Preserve every other status.
UPDATE enrolment."Created_users" u
SET "Programme_status" = 'Onboarding'
FROM enrolment.eligibility_learner_links l
JOIN enrolment.eligibility_form a ON a.id = l.application_id
WHERE u.id = l.learner_id
  AND a.eligible IS TRUE
  AND l.activation_verified_at IS NOT NULL
  AND (u."Programme_status" = 'Fresh user' OR nullif(btrim(u."Programme_status"), '') IS NULL);

COMMIT;
