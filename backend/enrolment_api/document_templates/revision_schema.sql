-- Additive revision storage. Original document, signature and blob tables untouched.
CREATE TABLE IF NOT EXISTS enrolment."Learner_Document_Revisions" (
 id uuid PRIMARY KEY,
 issue_id uuid NOT NULL REFERENCES enrolment."Document_Template_Issues"(id) ON DELETE RESTRICT,
 number integer NOT NULL CHECK(number >= 1),
 base_id uuid REFERENCES enrolment."Learner_Document_Revisions"(id) ON DELETE RESTRICT,
 state varchar(16) NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','finalized','original')),
 edit_version integer NOT NULL DEFAULT 1 CHECK(edit_version > 0),
 content jsonb NOT NULL, data jsonb NOT NULL,
 signature_method varchar(16) NOT NULL DEFAULT '' CHECK(signature_method IN ('','original','keep','sign-again')),
 provenance jsonb NOT NULL DEFAULT '{}', signing_source_id varchar(160) NOT NULL DEFAULT '',
 reason text NOT NULL DEFAULT '', changes jsonb NOT NULL DEFAULT '{}', checksum varchar(64) NOT NULL DEFAULT '',
 created_by text NOT NULL, updated_by text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), finalized_at timestamptz,
 UNIQUE(issue_id,number),
 CHECK((state = 'draft' AND finalized_at IS NULL) OR (state <> 'draft' AND finalized_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS learner_document_one_draft ON enrolment."Learner_Document_Revisions"(issue_id) WHERE state='draft';
CREATE INDEX IF NOT EXISTS learner_document_revision_order ON enrolment."Learner_Document_Revisions"(issue_id,number);
CREATE OR REPLACE FUNCTION enrolment.learner_document_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state <> 'draft' THEN RAISE EXCEPTION 'Finalized learner document revisions are immutable'; END IF;
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Document revision audit records cannot be deleted'; END IF;
 IF (NEW.id,NEW.issue_id,NEW.number,NEW.base_id,NEW.created_at,NEW.created_by)
 IS DISTINCT FROM (OLD.id,OLD.issue_id,OLD.number,OLD.base_id,OLD.created_at,OLD.created_by)
 THEN RAISE EXCEPTION 'Revision identity and ancestry are immutable'; END IF;
 RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS learner_document_revision_immutable ON enrolment."Learner_Document_Revisions";
CREATE TRIGGER learner_document_revision_immutable BEFORE UPDATE OR DELETE ON enrolment."Learner_Document_Revisions"
 FOR EACH ROW EXECUTE FUNCTION enrolment.learner_document_revision_immutable();
