-- REVIEWED PLAN ONLY: do not run against Production as part of deployment.
-- The Reference_number column already exists. This index closes the race left
-- by the staff API's friendly uniqueness check while permitting legacy blanks.
-- Preflight before approval:
--   select btrim("Reference_number"), count(*)
--   from enrolment."Created_users"
--   where nullif(btrim("Reference_number"), '') is not null
--   group by 1 having count(*) > 1;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS created_users_ilr_reference_unique
ON enrolment."Created_users" (upper(btrim("Reference_number")))
WHERE nullif(btrim("Reference_number"), '') IS NOT NULL;
