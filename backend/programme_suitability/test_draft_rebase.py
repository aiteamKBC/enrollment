import copy
import json
import tempfile
from pathlib import Path

from django.db import connections
from django.test import TestCase, SimpleTestCase

from .draft_rebase import merge, rebase_shared_draft, RebaseConflict, snapshot
from .models import ModuleConfigurationDraft


def published_fixtures(base, current):
    connection = connections['default']
    connection.ensure_connection()
    connection.connection.create_function('to_regclass', 1, lambda _: 'Wizard_Form_Layouts')
    with connection.cursor() as cursor:
        cursor.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout") VALUES (%s,%s)', [16, json.dumps(base)])
        cursor.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout") VALUES (%s,%s)', [20, json.dumps(current)])


class MergeTests(SimpleTestCase):
    def test_independent_fields_entities_and_ids_are_preserved(self):
        base = {'modules': [{'id': 'a', 'title': 'Original', 'sessions': 12}]}
        draft = copy.deepcopy(base); draft['modules'][0]['sessions'] = 16
        current = copy.deepcopy(base); current['modules'][0]['title'] = 'Published name'
        current['modules'].append({'id': 'b', 'title': 'New published module'})
        result = merge(base, draft, current)
        self.assertEqual(result['modules'], [{'id': 'a', 'title': 'Published name', 'sessions': 16}, {'id': 'b', 'title': 'New published module'}])
        self.assertEqual(base['modules'][0]['sessions'], 12)

    def test_conflicting_value_and_deleted_entity_fail_closed(self):
        for draft in ({'modules': [{'id': 'a', 'sessions': 16}]}, {'modules': []}):
            with self.assertRaisesRegex(RebaseConflict, 'modules'):
                merge({'modules': [{'id': 'a', 'sessions': 12}]}, draft, {'modules': [{'id': 'a', 'sessions': 18}]})

    def test_conflicting_reordering_fails_closed(self):
        items = [{'id': x} for x in 'abc']
        with self.assertRaisesRegex(RebaseConflict, 'ordering'):
            merge(items, list(reversed(items)), [items[1], items[0], items[2]])

    def test_legacy_conversion_does_not_fabricate_managed_cohorts(self):
        result = snapshot({'programmeModules': {'programmes': [{'id': 'ME_L4', 'modules': []}]}})
        self.assertNotIn('cohorts', result['programmes'][0])


class DraftRebaseTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.base = {'programmeStructure': {'schemaVersion': 2, 'programmes': [{
            'id': 'ME_L4', 'title': 'Marketing', 'modules': [], 'ksbs': [], 'mappings': [],
            'cohorts': [{'id': 'real-feb', 'programmeId': 'ME_L4', 'name': 'Feb 2027', 'modules': [], 'mappings': []}]}]}}
        self.current = copy.deepcopy(self.base)
        self.current['programmeStructure']['programmes'][0]['epao'] = 'Updated EPAO'
        self.current['texts'] = {'intro': 'Unrelated team change'}
        published_fixtures(self.base, self.current)
        self.draft = copy.deepcopy(self.base['programmeStructure'])
        self.draft['programmes'][0]['cohorts'].append({'id': 'real-june', 'programmeId': 'ME_L4', 'name': 'June 2027', 'modules': [], 'mappings': []})
        self.draft['_draftRevision'] = 2
        ModuleConfigurationDraft.objects.create(actor_key='programme-structure:shared', base_version=16, configuration=self.draft)

    def run_rebase(self, directory, **kwargs):
        return rebase_shared_draft(expected_base=16, expected_revision=2, expected_published=20, backup_dir=directory, **kwargs)

    def test_preflight_backup_then_atomic_save_reload_preserves_all_published_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_rebase(directory)
            self.assertFalse(result['applied'])
            self.assertEqual(ModuleConfigurationDraft.objects.get().base_version, 16)
            backup = json.loads((Path(directory) / 'before-rebase.json').read_text(encoding='utf-8'))
            self.assertEqual(backup['draft']['configuration'], self.draft)
            self.run_rebase(directory, apply=True)
        row = ModuleConfigurationDraft.objects.get()
        self.assertEqual((row.base_version, row.configuration['_draftRevision']), (20, 3))
        self.assertEqual([x['id'] for x in row.configuration['programmes'][0]['cohorts']], ['real-feb', 'real-june'])
        self.assertEqual(row.configuration['programmes'][0]['epao'], 'Updated EPAO')
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT "Layout" FROM enrolment."Wizard_Form_Layouts" ORDER BY id')
            self.assertEqual([json.loads(x[0]) for x in cursor.fetchall()], [self.base, self.current])

    def test_conflicts_leave_shared_draft_untouched_with_recovery_backup(self):
        self.draft['programmes'][0]['epao'] = 'Different draft EPAO'
        ModuleConfigurationDraft.objects.update(configuration=self.draft)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RebaseConflict, 'epao'):
                self.run_rebase(directory, apply=True)
            self.assertTrue((Path(directory) / 'before-rebase.json').exists())
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, self.draft)

    def test_concurrent_revision_change_cannot_be_overwritten(self):
        self.draft['_draftRevision'] = 3
        ModuleConfigurationDraft.objects.update(configuration=self.draft)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RebaseConflict, 'changed'):
                self.run_rebase(directory, apply=True)
        self.assertEqual(ModuleConfigurationDraft.objects.get().base_version, 16)
