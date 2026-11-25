-- Apply through the approved deployment process BEFORE deploying this feature.
-- No runtime DDL. Existing duplicate non-empty ULNs cause this migration to fail
-- and roll back; they must be reviewed, never silently reassigned or deleted.
BEGIN;
ALTER TABLE enrolment."Wizard_Plr" ADD COLUMN IF NOT EXISTS "Revision" integer NOT NULL DEFAULT 0;
ALTER TABLE enrolment."Wizard_Plr_Records" ADD COLUMN IF NOT EXISTS "Source_fingerprint" text;
ALTER TABLE enrolment."Wizard_Plr_Records" ADD COLUMN IF NOT EXISTS "Source_metadata" jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE enrolment."Wizard_Plr_Records" ADD COLUMN IF NOT EXISTS "Deleted" boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS wizard_plr_unique_uln ON enrolment."Wizard_Plr" ("ULN") WHERE "ULN" IS NOT NULL AND "ULN" <> '';
CREATE UNIQUE INDEX IF NOT EXISTS wizard_plr_source_identity ON enrolment."Wizard_Plr_Records" ("Learner_id", "Source_fingerprint") WHERE "Source_fingerprint" IS NOT NULL;
COMMIT;
