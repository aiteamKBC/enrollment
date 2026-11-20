-- Run ONLY in the LMS database, as its table owner/administrator.
-- Prepared prerequisite; not executed by the Enrolment application.
-- Read access to placement identifiers/deletion flags only, no write privileges.
GRANT SELECT (group_id, group_name, programme_id, cohort_id,
              is_programme_deleted, deleted_at, deleted_by, deleted_via_parent)
ON TABLE curriculum.groups TO enrolment_modules_reader;
