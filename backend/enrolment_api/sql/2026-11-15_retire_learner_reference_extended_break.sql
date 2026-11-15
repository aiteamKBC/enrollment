-- Pending explicit approval. Do not run against Neon until existing values
-- have been checked and any needed historical data has been exported.
-- Learner columns only; Staff_users and Organisations have separate
-- Reference_number fields and are deliberately untouched.
ALTER TABLE enrolment."Created_users"
  DROP COLUMN IF EXISTS "Reference_number",
  DROP COLUMN IF EXISTS "Extended_break";
