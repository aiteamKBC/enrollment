-- Forward-only extension. Existing source documents/templates/signatures untouched.
-- Existing immutable trigger also locks discarded rows and prevents hard DELETE.
DO $$
DECLARE constraint_row record;
BEGIN
 FOR constraint_row IN
  SELECT conname FROM pg_constraint
  WHERE conrelid = 'enrolment."Learner_Document_Revisions"'::regclass
   AND contype = 'c' AND position('state' IN pg_get_expr(conbin, conrelid)) > 0
 LOOP
  EXECUTE format('ALTER TABLE enrolment."Learner_Document_Revisions" DROP CONSTRAINT %I', constraint_row.conname);
 END LOOP;
END;
$$;
ALTER TABLE enrolment."Learner_Document_Revisions"
 ADD CONSTRAINT learner_revision_state_with_discard CHECK (state IN ('draft','finalized','original','discarded')),
 ADD CONSTRAINT learner_revision_terminal_time CHECK (
  (state IN ('draft','discarded') AND finalized_at IS NULL)
  OR (state IN ('original','finalized') AND finalized_at IS NOT NULL)),
 ADD CONSTRAINT learner_revision_discard_safety CHECK (state <> 'discarded' OR (
  number > 1 AND base_id IS NOT NULL AND signing_source_id = ''
  AND signature_method <> 'original' AND changes->'discardedDraft'->>'by' IS NOT NULL
  AND changes->'discardedDraft'->>'at' IS NOT NULL));
