-- Run only after approving the permanent removal of historical eligibility
-- Title answers. The learner profile Title is a separate column and remains.
BEGIN;
ALTER TABLE enrolment.eligibility_form DROP COLUMN IF EXISTS title;
COMMIT;
