"""Read-only deployment diagnostics for the existing unmanaged feature tables."""
from django.conf import settings
from django.db import connections

TEMPLATE_TABLES = ('Document_Template_Versions', 'Document_Template_Drafts', 'Document_Template_Issues', 'Document_Template_Render_History')
REVISION_TABLE = 'Learner_Document_Revisions'
SETUP_COMMAND = 'python backend/manage.py apply_document_templates --with-revisions'


def setup_details(alias):
    db = connections[alias]
    disabled = [name for name in ('DOCUMENT_TEMPLATES_ENABLED', 'DOCUMENT_REVISIONS_ENABLED') if not getattr(settings, name, db.vendor == 'postgresql')]
    names = (*TEMPLATE_TABLES, REVISION_TABLE)
    with db.cursor() as cursor:
        if db.vendor == 'postgresql':
            cursor.execute('SELECT ' + ', '.join(['to_regclass(%s)'] * len(names)), [f'enrolment."{name}"' for name in names])
            missing = [name for name, found in zip(names, cursor.fetchone()) if found is None]
        else:
            # SQLite is used only by isolated feature tests, never for rollout.
            cursor.execute('SELECT name FROM enrolment.sqlite_master WHERE type = %s', ['table'])
            found = {row[0] for row in cursor.fetchall()}; missing = [name for name in names if name not in found]
    message = ('Document revisions are disabled in backend configuration: ' + ', '.join(disabled) + '.') if disabled else (
        'Document revision storage has not been installed. A database administrator must review and apply the additive setup to the approved database using a direct connection.' if missing else
        'Document storage was updated. Retry Edit to check the current database state.')
    return {'code': 'configuration_disabled' if disabled else 'missing_tables' if missing else 'recheck_required',
        'message': message, 'missingTables': missing, 'disabledSettings': disabled,
        'checkCommand': SETUP_COMMAND + ' --check', 'planCommand': SETUP_COMMAND + ' --plan', 'setupCommand': SETUP_COMMAND}
