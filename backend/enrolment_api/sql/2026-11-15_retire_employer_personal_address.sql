-- Separate clean-up step after deploying code that no longer reads/writes
-- personal address fields on employer contacts. This is irreversible DDL.
ALTER TABLE enrolment."Employers"
  DROP COLUMN IF EXISTS "Post_code",
  DROP COLUMN IF EXISTS "Address_1",
  DROP COLUMN IF EXISTS "Address_2",
  DROP COLUMN IF EXISTS "Town_City",
  DROP COLUMN IF EXISTS "County",
  DROP COLUMN IF EXISTS "Country";
