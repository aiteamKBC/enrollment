import copy
import json
from unittest.mock import patch

from django.db import connection, OperationalError
from django.test.utils import CaptureQueriesContext
from django.test import TestCase

from . import test_pcp_selection_fix as fixtures
from . import modules_views


class IsolatedModuleSaveTests(TestCase):
    setUp = fixtures.PublishedPcpSelectionTests.setUp
    request = fixtures.PublishedPcpSelectionTests.request
    choice = fixtures.PublishedPcpSelectionTests.choice

    def test_save_never_updates_or_locks_ilr_and_preserves_every_existing_field(self):
        before = {field.attname: copy.deepcopy(getattr(self.draft, field.attname)) for field in self.draft._meta.concrete_fields}
        with CaptureQueriesContext(connection) as captured:
            response = self.request(self.choice())
        self.assertEqual(response.status_code, 200)
        for query in captured.captured_queries:
            sql = query['sql'].upper()
            if 'EXTENDED_ILR' in sql:
                self.assertNotIn('FOR UPDATE', sql)
                self.assertFalse(sql.startswith(('UPDATE', 'INSERT', 'DELETE')))
        self.draft.refresh_from_db(using='default')
        self.assertEqual({field.attname: getattr(self.draft, field.attname) for field in self.draft._meta.concrete_fields}, before)
        self.row.refresh_from_db()
        self.assertEqual(self.row.modules_revision, 1)
        self.assertEqual(json.loads(self.request().content)['savedSelection'], self.row.modules_selection)

    def test_partial_autosave_is_persisted_but_never_complete(self):
        response = self.request(self.choice([]))
        self.assertEqual(response.status_code, 200)
        saved = json.loads(response.content)
        self.assertFalse(saved['selectionComplete'])
        self.assertEqual(len(saved['selection']['modulesByPathway']['operational']), 3)
        complete = self.request({**self.choice(), 'expectedRevision': saved['selectionRevision']})
        self.assertTrue(json.loads(complete.content)['selectionComplete'])

    def test_two_sessions_cannot_overwrite_newer_choices_and_retry_is_idempotent(self):
        first = json.loads(self.request(self.choice()).content)
        other = self.choice(['planning-control', 'earned-value'])
        stale = self.request(other)
        self.assertEqual(stale.status_code, 409)
        self.assertIn('another session', json.loads(stale.content)['error'])
        self.row.refresh_from_db()
        self.assertEqual(self.row.modules_selection, first['selection'])
        with CaptureQueriesContext(connection) as captured:
            same = json.loads(self.request(self.choice()).content)
        self.assertEqual(same['selectionRevision'], first['selectionRevision'])
        self.assertFalse(any(q['sql'].lstrip().upper().startswith('UPDATE') for q in captured.captured_queries))
        fresh = self.request({**other, 'expectedRevision': first['selectionRevision']})
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(json.loads(fresh.content)['selectionRevision'], 2)

    def test_missing_or_invalid_revision_is_rejected_without_writes(self):
        for expected in [None, -1, True, '0']:
            self.assertEqual(self.request({**self.choice(), 'expectedRevision': expected}).status_code, 400)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.modules_selection)

    def test_legacy_choices_remain_readable_until_first_isolated_save(self):
        old = {key: value for key, value in self.choice().items() if key != 'expectedRevision'}
        self.draft.wizard_draft['programmeModules'] = old
        self.draft.save(using='default')
        self.assertEqual(json.loads(self.request().content)['savedSelection'], old)
        self.assertEqual(self.request(self.choice()).status_code, 200)
        self.draft.refresh_from_db(using='default')
        self.assertEqual(self.draft.wizard_draft['programmeModules'], old)
        self.row.refresh_from_db()
        self.assertNotEqual(self.row.modules_selection, old)

    def test_database_failure_logs_context_but_never_returns_database_details(self):
        with patch.object(modules_views, 'save_selection', side_effect=OperationalError('canceling statement while locking tuple (23,1)')), \
             self.assertLogs(modules_views.logger, level='WARNING') as logged:
            response = self.request(self.choice())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('tuple', response.content.decode())
        self.assertNotIn('canceling', response.content.decode())
        self.assertIn('learner_id=41', logged.output[0])
