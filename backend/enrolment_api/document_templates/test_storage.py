"""Storage diagnostics and post-deployment retries, with no live connections."""
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from . import revisions, revision_views, service, storage


@override_settings(DOCUMENT_TEMPLATES_ENABLED=True, DOCUMENT_REVISIONS_ENABLED=True)
class StorageTests(SimpleTestCase):
    def connection(self, found):
        db = MagicMock(vendor='postgresql')
        db.cursor.return_value.__enter__.return_value.fetchone.return_value = found
        return db

    def test_diagnostics_identify_all_missing_tables_with_one_read_only_query(self):
        db = self.connection((None,) * 5)
        with patch.object(storage, 'connections', {'enrolment': db}):
            details = storage.setup_details('enrolment')
        self.assertEqual(details['missingTables'], [*storage.TEMPLATE_TABLES, storage.REVISION_TABLE])
        self.assertEqual(details['code'], 'missing_tables')
        self.assertIn('--with-revisions --check', details['checkCommand'])
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.execute.assert_called_once()
        self.assertTrue(cursor.execute.call_args.args[0].startswith('SELECT to_regclass'))

    def test_diagnostics_distinguish_disabled_configuration_from_missing_tables(self):
        db = self.connection(('present',) * 5)
        with override_settings(DOCUMENT_REVISIONS_ENABLED=False), patch.object(storage, 'connections', {'enrolment': db}):
            details = storage.setup_details('enrolment')
        self.assertEqual(details['code'], 'configuration_disabled')
        self.assertEqual(details['disabledSettings'], ['DOCUMENT_REVISIONS_ENABLED'])
        self.assertEqual(details['missingTables'], [])

    def test_explicit_retry_bypasses_both_negative_caches_immediately_after_setup(self):
        db = self.connection(('revision_table',))
        db.cursor.return_value.__enter__.return_value.fetchone.side_effect = [('v', 'd', 'i', 'h'), ('r',)]
        with patch.object(service, 'database', return_value='enrolment'), \
                patch.object(service, 'connections', {'enrolment': db}), \
                patch.object(revisions, 'connections', {'enrolment': db}), \
                patch.object(service, '_READY', False), patch.object(service, '_CHECKED_AT', time.monotonic()), \
                patch.object(revisions, '_AVAILABLE', False), patch.object(revisions, '_CHECKED', time.monotonic()):
            self.assertFalse(revisions.ready())
            db.cursor.assert_not_called()
            self.assertTrue(revisions.ready(force=True))
        self.assertEqual(db.cursor.return_value.__enter__.return_value.execute.call_count, 2)

    def test_force_does_not_override_explicit_configuration_flags(self):
        db = self.connection(('present',) * 5)
        with patch.object(service, 'database', return_value='enrolment'), \
                patch.object(service, 'connections', {'enrolment': db}), \
                patch.object(revisions, 'connections', {'enrolment': db}):
            for flag in ('DOCUMENT_TEMPLATES_ENABLED', 'DOCUMENT_REVISIONS_ENABLED'):
                with self.subTest(flag=flag), override_settings(**{flag: False}):
                    self.assertFalse(revisions.ready(force=True))
        db.cursor.assert_not_called()

    def test_edit_gate_forces_recheck_but_history_does_not_and_only_admin_gets_diagnostics(self):
        request = SimpleNamespace(method='POST', login_account=None)
        details = {'code': 'missing_tables', 'message': 'Install approved storage.', 'missingTables': ['Learner_Document_Revisions'], 'disabledSettings': []}
        with patch.object(revision_views.auth, 'is_authenticated', return_value=True), \
                patch.object(revision_views.auth, '_may_access', return_value=True), \
                patch.object(revision_views, 'is_staff', return_value=True), \
                patch.object(revisions, 'ready', return_value=False) as ready, \
                patch.object(revision_views, 'setup_details', return_value=details):
            # Bypass only CSRF's wrapper here; CSRF is covered by real API tests.
            result = revision_views.start_revision.__wrapped__(request, pk=1, key='ilr')
            self.assertEqual(result.status_code, 503)
            self.assertEqual(json.loads(result.content)['setup'], details)
            ready.assert_called_once_with(force=True)
            request.method = 'GET'
            ready.reset_mock()
            revision_views.document_history.__wrapped__(request, pk=1, key='ilr')
            ready.assert_called_once_with(force=False)
            request.login_account = SimpleNamespace(role='learner')
            with patch.object(revision_views, 'is_staff', return_value=False), \
                    patch.object(revision_views, 'setup_details') as diagnostic:
                result = revision_views.document_history.__wrapped__(request, pk=1, key='ilr')
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('setup', json.loads(result.content))
            diagnostic.assert_not_called()


class DeploymentPlanTests(SimpleTestCase):
    def test_plan_lists_five_tables_without_connecting_or_printing_secrets(self):
        from enrolment_api.management.commands import apply_document_templates as command
        output = io.StringIO()
        with patch.object(command, 'connections') as connections:
            call_command('apply_document_templates', '--with-revisions', '--plan', stdout=output)
        connections.__getitem__.assert_not_called()
        text = output.getvalue()
        for name in (*storage.TEMPLATE_TABLES, storage.REVISION_TABLE): self.assertIn(name, text)
        self.assertIn('SQL SHA256:', text)
        self.assertIn('explicit approval', text)

    def test_check_fails_for_missing_storage_and_never_executes_ddl(self):
        from enrolment_api.management.commands import apply_document_templates as command
        db = MagicMock(vendor='postgresql', settings_dict={'HOST': 'synthetic-pooler.example.test'})
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (None,)
        with patch.object(command.os.environ, 'get', return_value=None), \
                patch.object(command, 'connections', {'enrolment': db}), self.assertRaises(CommandError):
            call_command('apply_document_templates', '--with-revisions', '--check', stdout=io.StringIO())
        self.assertEqual(cursor.execute.call_count, 5)
        self.assertTrue(all(call.args[0].startswith('SELECT to_regclass') for call in cursor.execute.call_args_list))


class PostgreSQLRevisionLockTests(SimpleTestCase):
    def test_schema_qualified_revision_lock_compiles_without_qualified_of_or_nullable_join(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from django.db.models.query import QuerySet
        db = DatabaseWrapper({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'offline-sql-compilation'}, 'offline')
        captured = []

        def compile_first(queryset):
            captured.append(queryset.query.get_compiler(connection=db).as_sql())
            return None

        with patch.object(db, 'get_autocommit', return_value=False), patch.object(QuerySet, 'first', compile_first):
            revisions.get_revision('12345678-1234-1234-1234-123456789012', 42, lock=True)
        sql, params = captured[0]
        self.assertIn('FOR UPDATE', sql)
        self.assertNotIn('FOR UPDATE OF', sql)
        self.assertNotIn('LEFT OUTER JOIN', sql)
        self.assertIn('"enrolment"."Learner_Document_Revisions"', sql)
        self.assertIn(42, params)
        self.assertIn('"learner_id"', sql)
