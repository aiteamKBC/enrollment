-- Phase 2A: additive workplace storage only. PREPARED, NOT APPLIED.
-- Review the target database and normal deployment/backup process before use.
-- No existing rows are updated or inferred: all seven columns default to NULL.
-- Idempotent for this release. Existing columns must have the reviewed types
-- and constraints; IF NOT EXISTS deliberately does not replace definitions.
--
-- Rollout: leave ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=false in live-facing
-- deployments. The preview also requires Django DEBUG=True. Only enable in an
-- isolated development environment AFTER this SQL (or the existing management
-- upgrade command) has been reviewed/applied there. Never use it to activate
-- Phase 2 eligibility decisions: those remain unchanged until Phase 2B.
-- No new endpoint, assessment response, or staff action is introduced here.
-- This is the original Phase 2A upgrade. For the Phase 2B code, ALSO apply
-- eligibility_workplace_phase2b.sql before enabling the isolated preview.
-- Phase 2B replaces the two reserved-NULL constraints; this script does not.

BEGIN;
ALTER TABLE enrolment.eligibility_form
    ADD COLUMN IF NOT EXISTS workplace_arrangement text
        CONSTRAINT eligibility_workplace_arrangement_check CHECK (workplace_arrangement IN ('same', 'different', 'remote_hybrid_multiple')),
    ADD COLUMN IF NOT EXISTS main_workplace_postcode text
        CONSTRAINT eligibility_workplace_postcode_length CHECK (char_length(main_workplace_postcode) <= 12),
    ADD COLUMN IF NOT EXISTS main_workplace_country text
        CONSTRAINT eligibility_workplace_country_length CHECK (char_length(main_workplace_country) <= 100),
    ADD COLUMN IF NOT EXISTS main_workplace_region text
        CONSTRAINT eligibility_workplace_region_length CHECK (char_length(main_workplace_region) <= 100),
    ADD COLUMN IF NOT EXISTS main_workplace_district text
        CONSTRAINT eligibility_workplace_district_length CHECK (char_length(main_workplace_district) <= 100),
    ADD COLUMN IF NOT EXISTS assessment_status text
        CONSTRAINT eligibility_assessment_status_reserved CHECK (assessment_status IS NULL),
    ADD COLUMN IF NOT EXISTS assessment_reason text
        CONSTRAINT eligibility_assessment_reason_reserved CHECK (assessment_reason IS NULL);
COMMIT;
