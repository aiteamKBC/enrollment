-- Phase 1 additive migration. Applied to production neondb on 2026-11-23
-- after explicit approval, in a single transaction.
-- Keep legacy eligibility columns until the JSONB backfill and all readers are
-- verified; the separate retirement migration must be reviewed first.

BEGIN;

ALTER TABLE enrolment."Eligibility_Form_Layouts"
  ADD COLUMN IF NOT EXISTS status text,
  ADD COLUMN IF NOT EXISTS published_at timestamptz,
  ADD COLUMN IF NOT EXISTS archived_at timestamptz,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz,
  ADD COLUMN IF NOT EXISTS updated_by text;

UPDATE enrolment."Eligibility_Form_Layouts" l
SET status = CASE WHEN l.id = (SELECT max(id) FROM enrolment."Eligibility_Form_Layouts")
                  THEN 'published' ELSE 'archived' END,
    published_at = COALESCE(l.published_at, l."Created_at"),
    archived_at = CASE WHEN l.id = (SELECT max(id) FROM enrolment."Eligibility_Form_Layouts")
                       THEN NULL ELSE COALESCE(l.archived_at, l."Created_at") END
WHERE l.status IS NULL;

ALTER TABLE enrolment."Eligibility_Form_Layouts"
  ALTER COLUMN status SET DEFAULT 'draft',
  ALTER COLUMN status SET NOT NULL,
  ADD CONSTRAINT eligibility_layout_status_check CHECK (status IN ('draft','published','archived'));
CREATE UNIQUE INDEX eligibility_one_published_layout
  ON enrolment."Eligibility_Form_Layouts" (status) WHERE status='published';
CREATE UNIQUE INDEX eligibility_one_draft_layout
  ON enrolment."Eligibility_Form_Layouts" (status) WHERE status='draft';

ALTER TABLE enrolment.eligibility_form
  ADD COLUMN IF NOT EXISTS answers jsonb,
  ADD COLUMN IF NOT EXISTS programme_id varchar(255);

-- JSONB keys are the existing snake_case column names. This preserves both
-- built-in and custom answers, including historical programme wording.
UPDATE enrolment.eligibility_form a
SET answers = COALESCE(a.answers, '{}'::jsonb) || jsonb_strip_nulls(
  to_jsonb(a) - ARRAY['id','first_name','surname','work_email','layout_version',
    'eligible','ineligible_reason','submitted_at','assessment_status',
    'assessment_reason','submission_fingerprint','submission_response',
    'answers','programme_id']::text[])
WHERE a.answers IS NULL;

UPDATE enrolment.eligibility_form a
SET programme_id = p.programme_id
FROM curriculum.programmes p
WHERE a.programme_id IS NULL AND nullif(btrim(a.programme),'') IS NOT NULL
  AND lower(btrim(a.programme)) = lower(btrim(p.name))
  AND (SELECT count(*) FROM curriculum.programmes candidate
       WHERE lower(btrim(candidate.name)) = lower(btrim(a.programme))) = 1;

ALTER TABLE enrolment.eligibility_form
  ALTER COLUMN answers SET DEFAULT '{}'::jsonb,
  ALTER COLUMN answers SET NOT NULL,
  ADD CONSTRAINT eligibility_answers_object CHECK (jsonb_typeof(answers)='object'),
  ADD CONSTRAINT eligibility_programme_id_fk FOREIGN KEY (programme_id)
    REFERENCES curriculum.programmes(programme_id) NOT VALID;
ALTER TABLE enrolment.eligibility_form VALIDATE CONSTRAINT eligibility_programme_id_fk;

ALTER TABLE enrolment.eligibility_learner_links
  ALTER COLUMN learner_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS invited_at timestamptz,
  ADD COLUMN IF NOT EXISTS accepted_at timestamptz,
  ADD COLUMN IF NOT EXISTS invitation_id bigint;
ALTER TABLE enrolment.eligibility_learner_links
  DROP CONSTRAINT eligibility_learner_links_invitation_status_check,
  ADD CONSTRAINT eligibility_learner_links_invitation_status_check
    CHECK (invitation_status IN ('pending','sending','sent','failed','expired','accepted')),
  ADD CONSTRAINT eligibility_link_invitation_fk FOREIGN KEY (invitation_id)
    REFERENCES login."Invitations"(id) NOT VALID;
UPDATE enrolment.eligibility_learner_links
SET accepted_at = activation_verified_at,
    invitation_status = 'accepted'
WHERE accepted_at IS NULL AND activation_verified_at IS NOT NULL;
ALTER TABLE enrolment.eligibility_learner_links VALIDATE CONSTRAINT eligibility_link_invitation_fk;
CREATE UNIQUE INDEX eligibility_link_invitation_id_unique
  ON enrolment.eligibility_learner_links(invitation_id) WHERE invitation_id IS NOT NULL;

ALTER TABLE login."Invitations"
  ALTER COLUMN "Account_id" DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS "Eligibility_application_id" bigint,
  ADD COLUMN IF NOT EXISTS "Eligibility_accepted_at" timestamptz,
  ADD CONSTRAINT invitation_eligibility_application_fk
    FOREIGN KEY ("Eligibility_application_id")
    REFERENCES enrolment.eligibility_form(id) NOT VALID,
  ADD CONSTRAINT invitation_has_owner CHECK
    ("Account_id" IS NOT NULL OR "Eligibility_application_id" IS NOT NULL);
ALTER TABLE login."Invitations" VALIDATE CONSTRAINT invitation_eligibility_application_fk;
CREATE INDEX eligibility_invitation_application_idx
  ON login."Invitations"("Eligibility_application_id")
  WHERE "Eligibility_application_id" IS NOT NULL;

ALTER TABLE enrolment."Organisations"
  ADD COLUMN IF NOT EXISTS "Merged_into_organisation_id" integer,
  ADD COLUMN IF NOT EXISTS "Merged_at" timestamptz,
  ADD COLUMN IF NOT EXISTS "Merged_by_account_id" bigint,
  ADD COLUMN IF NOT EXISTS "Potential_match_ids" jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE enrolment."Organisations"
  ADD CONSTRAINT organisation_merge_target_fk FOREIGN KEY ("Merged_into_organisation_id")
    REFERENCES enrolment."Organisations"(id) NOT VALID,
  ADD CONSTRAINT organisation_merge_actor_fk FOREIGN KEY ("Merged_by_account_id")
    REFERENCES login."Login_accounts"(id) NOT VALID,
  ADD CONSTRAINT organisation_no_self_merge CHECK
    ("Merged_into_organisation_id" IS NULL OR "Merged_into_organisation_id" <> id),
  ADD CONSTRAINT organisation_merge_complete CHECK
    (("Merged_into_organisation_id" IS NULL AND "Merged_at" IS NULL AND "Merged_by_account_id" IS NULL)
     OR ("Merged_into_organisation_id" IS NOT NULL AND "Merged_at" IS NOT NULL AND "Merged_by_account_id" IS NOT NULL));
ALTER TABLE enrolment."Organisations"
  ADD CONSTRAINT organisation_potential_matches_array CHECK (jsonb_typeof("Potential_match_ids")='array');
ALTER TABLE enrolment."Organisations" VALIDATE CONSTRAINT organisation_merge_target_fk;
ALTER TABLE enrolment."Organisations" VALIDATE CONSTRAINT organisation_merge_actor_fk;
CREATE UNIQUE INDEX organisations_active_name_postcode_unique
  ON enrolment."Organisations" (
    lower(btrim(regexp_replace(lower("Name"),'[^0-9a-z]+',' ','g'))),
    upper(regexp_replace("Post_code",'[[:space:]]+','','g')))
  WHERE "Merged_into_organisation_id" IS NULL
    AND nullif(btrim("Name"),'') IS NOT NULL
    AND nullif(btrim("Post_code"),'') IS NOT NULL;

COMMIT;
