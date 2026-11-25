-- PROPOSED ONLY. Explicit production approval and verified backup required.
-- No ORM field is added. Old binaries continue using Wizard_Plr unchanged.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE enrolment."Created_users"
    ADD COLUMN "ULN" VARCHAR(10) NULL,
    ADD CONSTRAINT created_users_uln_format CHECK (
        "ULN" IS NULL OR
        (char_length("ULN") = 10 AND "ULN" COLLATE "C" ~ '^[0-9]{10}$')
    );
COMMIT;
