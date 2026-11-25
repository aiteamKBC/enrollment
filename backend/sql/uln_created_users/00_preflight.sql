-- Metadata/aggregate results only. No learner names, ULNs or document contents.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '10s';
SELECT current_database() AS target_database, current_schema() AS search_schema;
SELECT column_name, data_type, character_maximum_length, is_nullable
FROM information_schema.columns
WHERE table_schema = 'enrolment' AND table_name = 'Created_users' AND column_name = 'ULN';
SELECT (SELECT count(*) FROM enrolment."Created_users") AS users,
       (SELECT count(*) FROM enrolment."Wizard_Plr") AS legacy_plr,
       (SELECT count(*) FROM enrolment."Wizard_Plr_Records") AS qualifications;
SELECT count(*) FILTER (WHERE NULLIF("ULN", '') IS NOT NULL) AS populated,
       count(*) FILTER (WHERE NULLIF("ULN", '') IS NOT NULL AND
           (char_length("ULN") <> 10 OR "ULN" COLLATE "C" !~ '^[0-9]{10}$')) AS invalid
FROM enrolment."Wizard_Plr";
SELECT p."Learner_kind", count(*) AS orphan_count
FROM enrolment."Wizard_Plr" p LEFT JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
WHERE u.id IS NULL GROUP BY p."Learner_kind";
SELECT count(*) AS scope_mismatches
FROM enrolment."Wizard_Plr" p JOIN enrolment."Created_users" u ON u.id = p."Learner_id"
WHERE p."Learner_kind" IS DISTINCT FROM u."Learner_type";
SELECT count(*) AS conflicting_owners FROM (
    SELECT "Learner_id" FROM enrolment."Wizard_Plr" WHERE NULLIF("ULN", '') IS NOT NULL
    GROUP BY "Learner_id" HAVING count(DISTINCT "ULN") > 1
) x;
SELECT count(*) AS duplicate_ulns FROM (
    SELECT "ULN" FROM enrolment."Wizard_Plr" WHERE NULLIF("ULN", '') IS NOT NULL
    GROUP BY "ULN" HAVING count(DISTINCT "Learner_id") > 1
) x;
-- A legacy custom ULN binding would be another writer and must be reviewed,
-- without changing any layout definition or assignment during this phase.
SELECT count(*) AS custom_legacy_uln_bindings
FROM enrolment."Wizard_Form_Layouts" l,
LATERAL jsonb_array_elements(COALESCE(l."Layout"->'steps', '[]'::jsonb)) s,
LATERAL jsonb_array_elements(COALESCE(s->'items', '[]'::jsonb)) i
WHERE i->>'table' = 'Wizard_Plr' AND i->>'column' = 'ULN'
  AND COALESCE(i->>'builtin', 'false') <> 'true';
SELECT indexname, indexdef FROM pg_indexes
WHERE (schemaname = 'enrolment' AND tablename = 'Created_users'
       OR schemaname = 'Learner' AND tablename = 'learners')
  AND indexdef LIKE '%UNIQUE%';
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid IN ('enrolment."Created_users"'::regclass, '"Learner".learners'::regclass)
  AND contype = 'f';
COMMIT;
