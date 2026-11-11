-- Additive, idempotent deploy step. No existing document/blob/answer table changes.
CREATE SCHEMA IF NOT EXISTS enrolment;
CREATE TABLE IF NOT EXISTS enrolment."Document_Template_Versions" (
 id uuid PRIMARY KEY, template_id varchar(120) NOT NULL, document_key varchar(64) NOT NULL,
 number integer NOT NULL CHECK(number > 0), content jsonb NOT NULL, content_hash varchar(64) NOT NULL,
 published_at timestamptz NOT NULL DEFAULT now(), published_by text NOT NULL, approval_note text NOT NULL DEFAULT '',
 UNIQUE(template_id, number)
);
CREATE TABLE IF NOT EXISTS enrolment."Document_Template_Drafts" (
 template_id varchar(120) PRIMARY KEY, document_key varchar(64) NOT NULL, base_version varchar(100) NOT NULL,
 revision integer NOT NULL CHECK(revision > 0), content jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), updated_by text NOT NULL
);
CREATE TABLE IF NOT EXISTS enrolment."Document_Template_Issues" (
 id uuid PRIMARY KEY, document_key varchar(64) NOT NULL, source_id varchar(160) NOT NULL,
 learner_kind varchar(32) NOT NULL, learner_id bigint NOT NULL,
 version_id uuid REFERENCES enrolment."Document_Template_Versions"(id) ON DELETE RESTRICT,
 template_snapshot jsonb NOT NULL, field_values jsonb NOT NULL, review_snapshot jsonb,
 issued_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_key, source_id)
);
CREATE TABLE IF NOT EXISTS enrolment."Document_Template_Render_History" (
 id uuid PRIMARY KEY, issue_id uuid NOT NULL REFERENCES enrolment."Document_Template_Issues"(id) ON DELETE RESTRICT,
 payload jsonb NOT NULL, fingerprint varchar(64) NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(), recorded_by text NOT NULL DEFAULT '',
 UNIQUE(issue_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS document_template_latest ON enrolment."Document_Template_Versions"(template_id, number DESC);
CREATE INDEX IF NOT EXISTS document_template_learner ON enrolment."Document_Template_Issues"(learner_id, issued_at DESC);
CREATE OR REPLACE FUNCTION enrolment.document_template_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Published document templates and render history are immutable'; END;
$$;
DROP TRIGGER IF EXISTS document_template_version_immutable ON enrolment."Document_Template_Versions";
CREATE TRIGGER document_template_version_immutable BEFORE UPDATE OR DELETE ON enrolment."Document_Template_Versions"
 FOR EACH ROW EXECUTE FUNCTION enrolment.document_template_immutable();
DROP TRIGGER IF EXISTS document_template_render_immutable ON enrolment."Document_Template_Render_History";
CREATE TRIGGER document_template_render_immutable BEFORE UPDATE OR DELETE ON enrolment."Document_Template_Render_History"
 FOR EACH ROW EXECUTE FUNCTION enrolment.document_template_immutable();
CREATE OR REPLACE FUNCTION enrolment.document_template_issue_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Issued template bindings are immutable'; END IF;
 IF (NEW.id, NEW.document_key, NEW.source_id, NEW.learner_kind, NEW.learner_id, NEW.version_id, NEW.template_snapshot, NEW.field_values, NEW.issued_at)
 IS DISTINCT FROM (OLD.id, OLD.document_key, OLD.source_id, OLD.learner_kind, OLD.learner_id, OLD.version_id, OLD.template_snapshot, OLD.field_values, OLD.issued_at)
 THEN RAISE EXCEPTION 'Issued template identity and content are immutable'; END IF;
 RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS document_template_issue_immutable ON enrolment."Document_Template_Issues";
CREATE TRIGGER document_template_issue_immutable BEFORE UPDATE OR DELETE ON enrolment."Document_Template_Issues"
 FOR EACH ROW EXECUTE FUNCTION enrolment.document_template_issue_immutable();
