"""Scope recovery uses disposable SQLite publications, never live records."""
import copy
import io
import json
import tempfile

from django.core.management import call_command
from django.db import connections
from django.test import TestCase

from enrolment_api.wizard_layout import normalize_layout, read_current
from .draft_rebase import RebaseConflict, rebase_shared_draft
from .models import ModuleConfigurationDraft
from .modules_configuration import defaults
from .programme_structure import from_legacy
from .publication import build_published_layout, preflight
from .publication_scope import prepare_scope_rebase


class PublicationScopeTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        from enrolment_api.skills_radar.catalogue import with_catalogues
        layout = {'steps': [{'slug': 'welcome', 'label': 'Welcome', 'items': []}],
                  'programmeModules': defaults(), 'texts': {'welcome': 'Keep wording'}}
        layout = with_catalogues(layout)
        self.target_layout, _ = normalize_layout(layout, None)
        self.source_layout = copy.deepcopy(self.target_layout)
        self.source_layout['programmeStructure'] = from_legacy(self.target_layout['programmeModules'],
                                                               self.target_layout['skillsRadarCatalogues'])
        self.draft = copy.deepcopy(self.source_layout['programmeStructure'])
        for p in self.draft['programmes']:
            for c in p.get('cohorts', []):
                c['assignmentPeriod'] = {'year': 2027, 'startMonth': 10, 'startDay': 16, 'endMonth': 2, 'endDay': 15}
                c['startDate'], c['endDate'] = '2027-02-01', '2029-01-31'
        self.draft['_draftRevision'] = 12
        self.original = (23, self.source_layout, None, None, 'default', None, None, None, False)
        self.target = (16, self.target_layout, None, None, 'default', None, None, 'apprenticeship', False)
        ModuleConfigurationDraft.objects.create(actor_key='programme-structure:shared', base_version=23,
                                                configuration=self.draft)
        db = connections['default']
        db.ensure_connection()
        db.connection.create_function('to_regclass', 1, lambda _: 'Wizard_Form_Layouts')
        with db.cursor() as q:
            for number, layout, kind in [(16, self.target_layout, 'apprenticeship'), (23, self.source_layout, None)]:
                q.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout","Learner_kind") VALUES (%s,%s,%s)',
                          [number, json.dumps(layout), kind])
            q.execute('INSERT INTO enrolment."Wizard_Layout_Assignments" '
                      '("Learner_kind","Learner_id","Override_version","Submitted_version","Pinned_version") '
                      'VALUES (%s,%s,%s,%s,%s)', ['apprenticeship', 9001, 12, 16, 11])

    def state(self):
        with connections['default'].cursor() as q:
            q.execute('SELECT id,"Layout","Learner_kind" FROM enrolment."Wizard_Form_Layouts" ORDER BY id')
            publications = q.fetchall()
            q.execute('SELECT * FROM enrolment."Wizard_Layout_Assignments"')
            pins = q.fetchall()
        return publications, pins

    def run_recovery(self, directory, **kwargs):
        return rebase_shared_draft(expected_base=23, expected_revision=12, expected_published=16,
                                   backup_dir=directory, preserve_content=True, **kwargs)

    def test_effective_apprenticeship_resolver_still_prefers_v16_over_generic_v23(self):
        with connections['default'].cursor() as q:
            self.assertEqual(read_current(q, 'apprenticeship')[0], 16)
            self.assertEqual(read_current(q, 'commercial')[0], 23)

    def test_existing_three_way_merge_blocks_the_scope_deletion_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RebaseConflict):
                rebase_shared_draft(expected_base=23, expected_revision=12, expected_published=16,
                                    backup_dir=directory)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, self.draft)

    def test_explicit_preflight_then_apply_changes_only_base_and_revision_not_academic_content(self):
        before = self.state()
        with tempfile.TemporaryDirectory() as directory:
            prepared = self.run_recovery(directory)
            self.assertFalse(prepared['applied'])
            self.assertEqual(ModuleConfigurationDraft.objects.get().base_version, 23)
            self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, self.draft)
            saved = self.run_recovery(directory, apply=True)
        expected = copy.deepcopy(self.draft)
        expected['_draftRevision'] = 13
        self.assertEqual(saved['structure'], expected)
        row = ModuleConfigurationDraft.objects.get()
        self.assertEqual((row.base_version, row.configuration), (16, expected))
        self.assertEqual(self.state(), before)
        result = preflight({'programmeStructure': row.configuration}, current_version=16, base_version=16)
        built = build_published_layout(self.target_layout, result)
        normalized, _ = normalize_layout(built, self.target_layout)
        self.assertEqual(normalized['programmeStructure'], expected)
        self.assertEqual({k: v for k, v in normalized.items() if k != 'programmeStructure'}, self.target_layout)

    def test_changed_draft_revision_base_or_target_publication_is_rejected(self):
        for field, value in [('revision', 13), ('base', 22), ('published', 24)]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                args = dict(expected_base=23, expected_revision=12, expected_published=16,
                            backup_dir=directory, preserve_content=True, apply=True)
                args[{'revision': 'expected_revision', 'base': 'expected_base', 'published': 'expected_published'}[field]] = value
                with self.assertRaises(RebaseConflict):
                    rebase_shared_draft(**args)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, self.draft)

    def test_a_new_apprenticeship_publication_invalidates_the_reviewed_target(self):
        with connections['default'].cursor() as q:
            q.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout","Learner_kind") VALUES (%s,%s,%s)',
                      [24, json.dumps(self.target_layout), 'apprenticeship'])
        before = self.state()
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RebaseConflict):
            self.run_recovery(directory, apply=True)
        self.assertEqual(self.state(), before)
        self.assertEqual(ModuleConfigurationDraft.objects.get().base_version, 23)

    def test_later_shared_draft_edit_invalidates_the_reviewed_revision(self):
        changed = copy.deepcopy(self.draft)
        changed['_draftRevision'] = 13
        ModuleConfigurationDraft.objects.update(configuration=changed)
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(RebaseConflict):
            self.run_recovery(directory, apply=True)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, changed)

    def test_later_publication_changes_unpinned_default_but_preserves_historical_pin_values(self):
        before_pins = self.state()[1]
        prepared = prepare_scope_rebase(self.original, self.target, self.draft)
        built = build_published_layout(self.target_layout,
            preflight({'programmeStructure': prepared}, current_version=16, base_version=16))
        with connections['default'].cursor() as q:
            q.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id,"Layout","Learner_kind") VALUES (%s,%s,%s)',
                      [24, json.dumps(built), 'apprenticeship'])
            self.assertEqual(read_current(q, 'apprenticeship')[0], 24)
            self.assertEqual(read_current(q, 'commercial')[0], 23)
        self.assertEqual(self.state()[1], before_pins)

    def test_conflicting_legacy_module_changes_fail_closed(self):
        proposed = copy.deepcopy(self.draft)
        proposed['programmes'][0]['modules'][0]['sessions'] = 999
        with self.assertRaisesRegex(ValueError, 'legacy data changed'):
            prepare_scope_rebase(self.original, self.target, proposed)

    def test_existing_target_canonical_content_cannot_be_overwritten(self):
        target = list(self.target)
        target[1] = copy.deepcopy(self.source_layout)
        with self.assertRaisesRegex(RebaseConflict, 'already has canonical'):
            prepare_scope_rebase(self.original, target, self.draft)

    def test_other_scopes_kinds_archived_or_missing_canonical_sources_are_rejected(self):
        for location, index, value in [('source', 4, 'targeted'), ('source', 7, 'commercial'),
                                       ('source', 8, True), ('target', 7, None), ('target', 8, True)]:
            original, target = list(self.original), list(self.target)
            (original if location == 'source' else target)[index] = value
            with self.subTest(location=location, index=index), self.assertRaises(RebaseConflict):
                prepare_scope_rebase(original, target, self.draft)
        original = list(self.original)
        original[1] = self.target_layout
        with self.assertRaisesRegex(RebaseConflict, 'no canonical'):
            prepare_scope_rebase(original, self.target, self.draft)

    def test_calendar_or_unrelated_normalization_changes_cannot_be_smuggled_in(self):
        from .intake_calendar import DEFAULT_INTAKES
        draft = copy.deepcopy(self.draft)
        draft['intakeCalendar'] = copy.deepcopy(DEFAULT_INTAKES)
        with self.assertRaisesRegex(RebaseConflict, 'unrelated'):
            prepare_scope_rebase(self.original, self.target, draft)

    def test_management_command_preserve_content_preflight_does_not_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            call_command('rebase_programme_structure_draft', base_version=23, revision=12,
                         published_version=16, backup_dir=directory, preserve_content=True, stdout=output)
            self.assertIn('Preflight passed', output.getvalue())
        self.assertEqual(ModuleConfigurationDraft.objects.get().base_version, 23)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, self.draft)
