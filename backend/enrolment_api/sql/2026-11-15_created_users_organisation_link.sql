-- Apply before deploying the Organisation-first Django model. This statement
-- retains all existing learners and uses NULL for unresolved historical links.
ALTER TABLE enrolment."Created_users"
  ADD COLUMN IF NOT EXISTS "Organisation_id" bigint;

-- Backfill only where one manager belongs to exactly one company and the
-- learner's recorded company name does not disagree with that company.
UPDATE enrolment."Created_users" AS learner
SET "Organisation_id" = (manager."Employer_group_ids"->>0)::bigint
FROM enrolment."Employers" AS manager
JOIN enrolment."Organisations" AS organisation
  ON organisation.id = CASE
    WHEN jsonb_typeof(manager."Employer_group_ids") = 'array' THEN
      CASE WHEN jsonb_array_length(manager."Employer_group_ids") = 1
        AND (manager."Employer_group_ids"->>0) ~ '^[0-9]+$'
      THEN (manager."Employer_group_ids"->>0)::bigint ELSE NULL END
    ELSE NULL END
WHERE learner."Employer_id" = manager.id
  AND learner."Organisation_id" IS NULL
  AND (NULLIF(btrim(learner."Orgnization"), '') IS NULL
    OR lower(btrim(learner."Orgnization")) = lower(btrim(organisation."Name")));

CREATE INDEX IF NOT EXISTS created_users_organisation_id_idx
  ON enrolment."Created_users" ("Organisation_id");

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'enrolment."Created_users"'::regclass
      AND conname = 'created_users_organisation_id_fk'
  ) THEN
    ALTER TABLE enrolment."Created_users"
      ADD CONSTRAINT created_users_organisation_id_fk
      FOREIGN KEY ("Organisation_id") REFERENCES enrolment."Organisations"(id)
      ON DELETE RESTRICT NOT VALID;
  END IF;
END $$;
ALTER TABLE enrolment."Created_users"
  VALIDATE CONSTRAINT created_users_organisation_id_fk;
