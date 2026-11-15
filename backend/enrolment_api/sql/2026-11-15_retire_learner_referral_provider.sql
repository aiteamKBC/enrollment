-- Applied to Neon neondb on 2026-11-15 after explicit approval.
-- No CASCADE: dependent objects must be reviewed before any column is dropped.
-- Read-only preflight on 2026-11-15 found no populated learner referral values,
-- 121 populated learner provider values (all Kent Business College), and no
-- populated staff provider values.
ALTER TABLE enrolment."Created_users"
  DROP COLUMN IF EXISTS "Referrer",
  DROP COLUMN IF EXISTS "Referrer_address",
  DROP COLUMN IF EXISTS "Referrer_contact",
  DROP COLUMN IF EXISTS "Learning_provider";

ALTER TABLE enrolment."Staff_users"
  DROP COLUMN IF EXISTS "Learning_provider";
