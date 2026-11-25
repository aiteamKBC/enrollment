-- PROPOSED ONE-TIME CUTOVER. Run only while ALL PLR writers are paused,
-- old writer processes/jobs have drained, and every instance is ULN-capable.
-- Operator must SET enrolment.uln_legacy_writers_drained = 'yes' in this session.
-- NEVER run this file again after canonical reads/writes have been activated.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
DO $$
BEGIN
    IF current_setting('enrolment.uln_legacy_writers_drained', true) IS DISTINCT FROM 'yes' THEN
        RAISE EXCEPTION 'ULN cutover requires a verified legacy-writer drain';
    END IF;
END $$;
LOCK TABLE enrolment."Created_users", enrolment."Wizard_Plr" IN SHARE ROW EXCLUSIVE MODE;
DO $$
BEGIN
    -- A successful first cutover starts with an unused new column. Failure
    -- requires investigation rather than overwriting an existing canonical ULN.
    IF EXISTS (SELECT 1 FROM enrolment."Created_users" WHERE "ULN" IS NOT NULL) THEN
        RAISE EXCEPTION 'Canonical ULN column is already populated; do not rerun backfill';
    END IF;
    IF EXISTS (
        SELECT 1 FROM enrolment."Wizard_Plr" p
        JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
        WHERE p."Learner_kind" IS DISTINCT FROM u."Learner_type"
           OR (NULLIF(p."ULN", '') IS NOT NULL AND
               (char_length(p."ULN") <> 10 OR p."ULN" COLLATE "C" !~ '^[0-9]{10}$'))
    ) THEN
        RAISE EXCEPTION 'Legacy ULN format or learner scope needs manual review';
    END IF;
    IF EXISTS (
        SELECT p."Learner_id" FROM enrolment."Wizard_Plr" p
        JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
        WHERE NULLIF(p."ULN", '') IS NOT NULL
        GROUP BY p."Learner_id" HAVING count(DISTINCT p."ULN") > 1
    ) THEN
        RAISE EXCEPTION 'Conflicting legacy ULNs; no mappings will be invented';
    END IF;
    IF EXISTS (
        SELECT p."ULN" FROM enrolment."Wizard_Plr" p
        JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
        WHERE NULLIF(p."ULN", '') IS NOT NULL
        GROUP BY p."ULN" HAVING count(DISTINCT p."Learner_id") > 1
    ) THEN
        RAISE EXCEPTION 'Duplicate legacy ULN ownership needs identity review';
    END IF;
END $$;
UPDATE enrolment."Created_users" u SET "ULN" = source.uln
FROM (
    SELECT p."Learner_id", min(p."ULN") AS uln
    FROM enrolment."Wizard_Plr" p
    JOIN enrolment."Created_users" owner ON owner.id = p."Learner_id"
    WHERE NULLIF(p."ULN", '') IS NOT NULL
    GROUP BY p."Learner_id"
) source
WHERE u.id = source."Learner_id";
-- Wizard_Plr (including the orphan rows), qualifications and progress untouched.
COMMIT;
