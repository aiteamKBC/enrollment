"""Forward extension for auditable draft discard; no historical SQL edits."""
from pathlib import Path
from django.core.management.base import CommandError
from .apply_document_templates import Command as StorageCommand


class Command(StorageCommand):
    help = 'Enable audited soft discard of learner revision drafts; source documents remain unchanged.'
    plan_summary = 'Extends only revision state/time/audit CHECK constraints. Existing rows, templates, immutable triggers, source documents, signatures and PDFs are not rewritten.'
    success_message = 'Draft discard storage is ready. Existing documents were not modified.'

    def schema_plan(self, options):
        return ('Learner_Document_Revisions',), (Path(__file__).parents[2] / 'document_templates' / 'discard_draft_schema.sql').read_text(encoding='utf-8')

    def check_storage(self, connection):
        with connection.cursor() as cursor:
            cursor.execute('''SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass
                AND conname IN ('learner_revision_state_with_discard','learner_revision_terminal_time','learner_revision_discard_safety')''', ['enrolment."Learner_Document_Revisions"'])
            if len(cursor.fetchall()) != 3:
                raise CommandError('Draft discard storage requires the forward apply_document_revision_discard setup.')
