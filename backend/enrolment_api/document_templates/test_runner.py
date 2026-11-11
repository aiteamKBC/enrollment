from django.db import connection, connections
from programme_suitability.test_runner import SuitabilityRunner


class DocumentTemplateRunner(SuitabilityRunner):
    def setup_databases(self, **kwargs):
        result = super().setup_databases(**kwargs)
        from learner_api.models import ApprenticeshipAgreement, IlrDocument, TrainingPlanDocument, WrittenAgreement, EnrolmentReview
        from .models import TemplateVersion, TemplateDraft, IssuedTemplate, RenderHistory, LearnerDocumentRevision
        # Source issuance explicitly uses the enrolment transaction; route both
        # aliases to the SAME isolated in-memory connection, never to .env.
        connections['enrolment'] = connections['default']
        with connection.schema_editor() as editor:
            for model in (ApprenticeshipAgreement, IlrDocument, TrainingPlanDocument, WrittenAgreement, EnrolmentReview,
                          TemplateVersion, TemplateDraft, IssuedTemplate, RenderHistory, LearnerDocumentRevision):
                # PostgreSQL FK/trigger enforcement is defined in schema.sql;
                # verify that DDL on an isolated PostgreSQL branch before deploy.
                constraints = []
                for field in model._meta.fields:
                    if getattr(field, 'db_constraint', False):
                        constraints.append(field); field.db_constraint = False
                try:
                    editor.create_model(model)
                    # Django's PostgreSQL-style schema-qualified index SQL is
                    # not SQLite syntax. Recreate the relevant unique indexes
                    # below with SQLite's schema.index ON table syntax.
                    editor.deferred_sql.clear()
                finally:
                    for field in constraints: field.db_constraint = True
        with connection.cursor() as cursor:
            for name, table, columns in (
                ('template_version_unique', 'Document_Template_Versions', 'template_id, number'),
                ('template_issue_unique', 'Document_Template_Issues', 'document_key, source_id'),
                ('template_render_unique', 'Document_Template_Render_History', 'issue_id, fingerprint'),
                ('learner_revision_unique', 'Learner_Document_Revisions', 'issue_id, number'),
            ):
                cursor.execute(f'CREATE UNIQUE INDEX enrolment."{name}" ON "{table}" ({columns})')
        return result
