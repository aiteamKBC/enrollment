"""No network or live credentials: pure CSV/version and isolated SQLite tests."""
import copy
import csv
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase

from programme_suitability.models import Assessment, ModuleConfigurationDraft
from programme_suitability.modules_configuration import defaults
from enrolment_api.models import ExtendedIlr
from learner_api.models import EnrolmentUser
from . import service, views
from .catalogue import APPROVALS, KEY, PROGRAMMES, CatalogueError, check_approval, compare, current_catalogues, initial, integrate_layout, validate
from .csv_io import MAX_BYTES, export_csv, parse_csv


def approval(before, after):
    difference = compare(before, after)
    return {'againstVersion': before['version'], 'completeSnapshot': True,
            'removedCodes': difference['removedCodes'], 'categoryCodes': difference['categoryCodes']}


class CatalogueTests(SimpleTestCase):
    def setUp(self):
        self.modules = defaults()

    def test_all_four_initial_snapshots_validate_and_export_import_identically(self):
        for pid, total in zip(PROGRAMMES, (71, 42, 33, 62)):
            with self.subTest(pid=pid):
                old = initial(pid)
                self.assertEqual(len(old['records']), total)
                self.assertEqual(validate(pid, old, self.modules, old), old)
                result = parse_csv(export_csv(old).encode('utf-8'), pid, self.modules, old)
                self.assertEqual(result, old)
                self.assertEqual(compare(old, result)['unchanged'], total)

    def test_formula_apostrophe_comma_unicode_and_multiline_roundtrip_is_lossless(self):
        for text in ['=HYPERLINK("bad")', '+cmd', '-9', '@SUM(A1)', '\t=cmd', '  =cmd', "'Original apostrophe", 'Learner’s “quote”, comma\nsecond line\r\nالعربية']:
            with self.subTest(text=text):
                old = initial('ME_L4')
                old['records'][0]['description'] = text
                value = export_csv(old)
                cells = list(csv.reader(io.StringIO(value.lstrip('\ufeff'), newline='')))
                if text[0] in "'=+-@ \t":
                    self.assertTrue(cells[2][2].startswith("'"))
                result = parse_csv(value.encode(), 'ME_L4', self.modules, old)
                self.assertEqual(result['records'][0]['description'], text)

    def test_plain_utf8_csv_never_strips_genuine_apostrophes_and_accepts_bom(self):
        for bom in ['', '\ufeff']:
            value = bom + "ksb_code,ksb_type,description,module_key\r\nK1,Knowledge,'A literal apostrophe,marketing-impact\r\n"
            result = parse_csv(value.encode(), 'ME_L4', self.modules, initial('ME_L4'))
            self.assertEqual(result['records'][0]['description'], "'A literal apostrophe")

    def test_comparison_detects_each_change_and_keeps_existing_programme_scoped_ids(self):
        old = initial('ME_L4'); new = copy.deepcopy(old)
        new['records'][0]['description'] += ' revised'
        new['records'][1]['kind'] = 'Skill'
        new['records'][2]['moduleKey'] = 'marketing-impact' if old['records'][2]['moduleKey'] != 'marketing-impact' else 'martech'
        new['records'].pop()
        new['records'].append({'code': 'K40', 'kind': 'Knowledge', 'description': 'Exact new question', 'moduleKey': 'martech'})
        new = validate('ME_L4', new, self.modules, old)
        result = compare(old, new)
        self.assertEqual({k: result[k] for k in ('added', 'removed', 'descriptions', 'categories', 'mappings')}, dict.fromkeys(('added', 'removed', 'descriptions', 'categories', 'mappings'), 1))
        self.assertEqual(new['records'][0]['id'], old['records'][0]['id'])
        self.assertEqual(next(r for r in new['records'] if r['code'] == 'K40')['id'], 'radar:ME_L4:K40')
        self.assertNotEqual(initial('APM_L4')['records'][0]['id'], new['records'][0]['id'])
        self.assertEqual(initial('ME_L4'), old)

    def test_removal_and_category_changes_need_explicit_complete_snapshot_review(self):
        old = initial('ME_L4'); new = copy.deepcopy(old)
        new['records'] = new['records'][:10]; new['records'][0]['kind'] = 'Skill'
        new = validate('ME_L4', new, self.modules, old)
        self.assertTrue(compare(old, new)['largeRemoval'])
        for invalid in [None, {}, {'completeSnapshot': True}, {**approval(old, new), 'removedCodes': []}, {**approval(old, new), 'categoryCodes': []}, {**approval(old, new), 'removedCodes': None}]:
            with self.subTest(invalid=invalid), self.assertRaises(CatalogueError):
                check_approval(old, new, invalid)
        check_approval(old, new, approval(old, new))
        check_approval(old, old, None)

    def test_invalid_rows_are_reported_together_without_partial_import(self):
        old = initial('ME_L4')
        value = 'ksb_code,ksb_type,description,module_key\nK1,Wrong,,unknown\nK1,Knowledge,Duplicate,marketing-impact\nwrong,Skill,Text,martech\n'
        with self.assertRaises(CatalogueError) as caught:
            parse_csv(value.encode(), 'ME_L4', self.modules, old)
        self.assertGreaterEqual(len(caught.exception.errors), 5)
        self.assertEqual({e['row'] for e in caught.exception.errors}, {2, 3, 4})
        self.assertEqual(initial('ME_L4'), old)

    def test_csv_syntax_encoding_headers_limits_and_programme_scope(self):
        old = initial('ME_L4')
        invalid = [b'\xff', b'', b'wrong,columns\n', b'ksb_code,ksb_type,description,module_key\nK1,Knowledge,"unclosed,martech',
                   export_csv(initial('MM_L6')).encode(), export_csv(old, template=True).encode(), b'x' * (MAX_BYTES + 1),
                   b'ksb_code,ksb_type,description,module_key\nK1,Knowledge,Text,pmp\n',
                   b'ksb_code,ksb_type,description,module_key\nK1,Knowledge,unquoted,comma,martech\n']
        for value in invalid:
            with self.subTest(value=value[:80]), self.assertRaises(CatalogueError):
                parse_csv(value, 'ME_L4', self.modules, old)

    def test_pcp_each_pathway_checked_and_parent_rejected_but_shared_finance_supported(self):
        old = initial('PCP_L6')
        for path in ('operational', 'strategic', 'chartered'):
            for invalid in ('certified-pmo', '', None, ['AI']):
                value = copy.deepcopy(old); value['records'][0]['owners'][path] = invalid
                with self.subTest(path=path, invalid=invalid), self.assertRaises(CatalogueError) as caught:
                    validate('PCP_L6', value, self.modules, old)
                self.assertIn(path, caught.exception.errors[0]['message'])
        value = copy.deepcopy(old); value['records'][0]['owners']['chartered'] = 'FINANCE'
        result = validate('PCP_L6', value, self.modules, old)
        self.assertEqual(result['records'][0]['owners']['chartered'], 'FINANCE')

    def test_inactive_fixed_modules_and_parent_groups_are_not_assignable(self):
        old = initial('ME_L4'); modules = copy.deepcopy(self.modules)
        definition = next(p for p in modules['programmes'] if p['id'] == 'ME_L4')
        definition['modules'][0]['active'] = False
        with self.assertRaises(CatalogueError):
            validate('ME_L4', old, modules, old)
        definition['modules'][0].update(active=True, itemType='GROUP', items=[])
        with self.assertRaises(CatalogueError):
            validate('ME_L4', old, modules, old)

    def test_invalid_json_mapping_shapes_raise_validation_errors(self):
        for pid in PROGRAMMES:
            value = initial(pid)
            value['records'][0].update(moduleKey=['bad'], owners={'operational': [], 'strategic': {}, 'chartered': False})
            with self.subTest(pid=pid), self.assertRaises(CatalogueError):
                validate(pid, value, self.modules, initial(pid))

    def test_publish_retains_unchanged_catalogues_modules_and_old_immutable_versions(self):
        previous = {'programmeModules': copy.deepcopy(self.modules), KEY: current_catalogues({})}
        old = copy.deepcopy(previous); raw = copy.deepcopy(previous)
        candidate = copy.deepcopy(raw[KEY]['ME_L4']); candidate['records'][0]['description'] = 'Approved new exact text'
        raw[KEY]['ME_L4'] = candidate; raw[APPROVALS] = {'ME_L4': approval(old[KEY]['ME_L4'], candidate)}
        layout = {'programmeModules': copy.deepcopy(self.modules)}
        integrate_layout(raw, previous, layout)
        self.assertEqual(previous, old)
        self.assertEqual(layout['programmeModules'], self.modules)
        self.assertEqual(layout[KEY]['APM_L4'], old[KEY]['APM_L4'])
        self.assertIsNotNone(layout[KEY]['ME_L4']['publishedAt'])
        self.assertNotEqual(layout[KEY]['ME_L4']['version'], old[KEY]['ME_L4']['version'])
        self.assertNotIn(APPROVALS, layout)

    def test_changing_layout_directly_cannot_bypass_import_confirmation(self):
        raw = {KEY: current_catalogues({})}; raw[KEY]['ME_L4']['records'].pop()
        with self.assertRaises(CatalogueError):
            integrate_layout(raw, {}, {'programmeModules': self.modules})


class VersionAndApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.layout = {'programmeModules': defaults(), KEY: current_catalogues({})}
        self.source = EnrolmentUser(id=4101, username='CSV synthetic learner', email='csv@example.test', learner_type='apprenticeship',
            status='FullUser', programme_status='Onboarding', onboarding_status='Not started', programme='Marketing Executive Level 4',
            cohort='', group='', learning_plan=[], start_date='2027-01-01', employer='')
        EnrolmentUser.all_learners.bulk_create([self.source])
        self.publish = patch.object(service, 'published', side_effect=lambda: (11, copy.deepcopy(self.layout)))
        self.publish.start(); self.addCleanup(self.publish.stop)

    def request(self, method='GET', data=None, staff=True, csrf=True, path='/'):
        request = self.factory.get(path) if method == 'GET' else self.factory.post(path, data) if method == 'POST' else self.factory.put(path, json.dumps(data), content_type='application/json')
        request.user = SimpleNamespace(pk=91, is_authenticated=True, is_staff=staff, is_superuser=False, get_username=lambda: 'synthetic-admin')
        request._dont_enforce_csrf_checks = csrf
        return request

    def changed(self, pid='ME_L4'):
        old = self.layout[KEY][pid]; value = copy.deepcopy(old)
        value['records'].append({'code': 'K99', 'kind': 'Knowledge', 'description': 'New question', 'moduleKey': old['records'][0].get('moduleKey'), 'owners': old['records'][0].get('owners')})
        return validate(pid, value, self.layout['programmeModules'], old)

    def test_all_programme_admin_cards_and_csv_downloads_do_not_write(self):
        response = views.catalogue_admin(self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(json.loads(response.content)['catalogues']), set(PROGRAMMES))
        for pid in PROGRAMMES:
            response = views.catalogue_admin(self.request(), pid)
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response['Content-Disposition'])
            self.assertEqual(parse_csv(response.content, pid, self.layout['programmeModules'], initial(pid)), initial(pid))
        self.assertEqual(ModuleConfigurationDraft.objects.count(), 0)
        self.assertEqual(ExtendedIlr.objects.count(), 0)
        self.assertEqual(Assessment.objects.count(), 0)

    def test_preview_detects_new_question_but_never_publishes_or_changes_learners(self):
        candidate = self.changed(); before = copy.deepcopy(self.layout)
        file = SimpleUploadedFile('mapping.csv', export_csv(candidate).encode(), 'text/csv')
        response = views.catalogue_admin(self.request('POST', {'file': file}), 'ME_L4')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['comparison']['added'], 1)
        self.assertEqual(self.layout, before)
        self.assertFalse(ModuleConfigurationDraft.objects.exists())
        self.assertFalse(ExtendedIlr.objects.exists())

    def test_bad_file_type_size_and_invalid_rows_leave_database_unchanged(self):
        for file in [SimpleUploadedFile('bad.txt', b'bad', 'text/plain'), SimpleUploadedFile('bad.csv', b'bad', 'image/png'),
                     SimpleUploadedFile('large.csv', b'x' * (MAX_BYTES + 1), 'text/csv'), SimpleUploadedFile('bad.csv', b'wrong', 'text/csv')]:
            response = views.catalogue_admin(self.request('POST', {'file': file}), 'ME_L4')
            self.assertIn(response.status_code, (400, 413))
        self.assertFalse(ModuleConfigurationDraft.objects.exists())
        self.assertFalse(ExtendedIlr.objects.exists())

    def test_non_staff_and_missing_csrf_cannot_import_save_or_publish_catalogues(self):
        from enrolment_api.wizard_layout import publish_wizard_layout
        self.assertEqual(views.catalogue_admin(self.request(staff=False)).status_code, 403)
        self.assertEqual(views.catalogue_draft(self.request('PUT', {}, staff=False)).status_code, 403)
        self.assertEqual(views.catalogue_admin(self.request('POST', {}, csrf=False), 'ME_L4').status_code, 403)
        self.assertEqual(views.catalogue_draft(self.request('PUT', {}, csrf=False)).status_code, 403)
        self.assertEqual(publish_wizard_layout(self.request('PUT', {'layout': {KEY: {}}, 'baseVersion': 11}, staff=False)).status_code, 403)
        self.assertEqual(publish_wizard_layout(self.request('PUT', {'layout': {KEY: {}}, 'baseVersion': 11}, csrf=False)).status_code, 403)

    def test_valid_csrf_is_accepted_bad_origin_and_anonymous_access_are_rejected(self):
        from django.conf import settings
        from django.middleware.csrf import get_token
        for origin, expected in [('http://localhost:3000', 400), ('https://untrusted.example.test', 403)]:
            request = self.request('POST', {}, csrf=False)
            token = get_token(request)
            request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META['CSRF_COOKIE']
            request.META.update(HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=origin)
            response = views.catalogue_admin(request, 'ME_L4')
            # Valid security reaches CSV validation (no file); hostile origins do not.
            self.assertEqual(response.status_code, expected, response.content)
        request = self.request(); request.user.is_authenticated = False
        self.assertEqual(views.catalogue_admin(request).status_code, 401)
        request = self.request(); request.login_account = SimpleNamespace(is_active=True, role='learner', subject_id=999)
        self.assertEqual(views.learner_catalogue(request, 'apprenticeship', self.source.pk).status_code, 404)

    def test_all_four_started_programmes_keep_their_own_original_ids_and_catalogue(self):
        from programme_suitability.programme_assignment import CATALOGUE_NAMES
        for pid in PROGRAMMES:
            with self.subTest(programme=pid):
                self.source.programme = CATALOGUE_NAMES[pid]
                old = initial(pid); record = old['records'][0]
                radar = {'assessments': {record['id']: {'level': 'rarely', 'note': 'Saved', 'evidenceFiles': ['saved.pdf']}}}
                self.layout[KEY][pid] = self.changed(pid)
                result = service.learner_payload('apprenticeship', self.source, radar, self.layout)
                self.assertTrue(result['pinned'])
                self.assertEqual(result['catalogue'], old)
                new = service.learner_payload('apprenticeship', self.source, {}, self.layout)
                self.assertEqual(len(new['catalogue']['records']), len(old['records']) + 1)

    def test_assessment_metadata_and_answers_reject_invalid_shapes_without_a_write(self):
        for radar in [[], None, 'bad', {'assessments': []}]:
            with self.subTest(radar=radar), self.assertRaises(CatalogueError):
                service.prepare_answers('apprenticeship', self.source, {'skillsRadar': radar}, {})
        self.assertFalse(ExtendedIlr.objects.exists())

    def test_actor_scoped_draft_saved_only_after_validation_and_does_not_affect_active_catalogue(self):
        from enrolment_api import wizard_layout
        candidate = self.changed(); config = {KEY: {**self.layout[KEY], 'ME_L4': candidate}, APPROVALS: {'ME_L4': approval(self.layout[KEY]['ME_L4'], candidate)}}
        current = (11, copy.deepcopy(self.layout), None, 'staff')
        with patch.object(wizard_layout, 'CONN', 'default'), patch.object(wizard_layout, '_table_exists', return_value=False), patch.object(wizard_layout, 'read_current', return_value=current):
            same = {KEY: copy.deepcopy(self.layout[KEY]), APPROVALS: {}}
            unchanged = views.catalogue_draft(self.request('PUT', {'configuration': same, 'baseVersion': 11}))
            self.assertEqual(unchanged.status_code, 200, unchanged.content)
            self.assertFalse(ModuleConfigurationDraft.objects.exists())
            response = views.catalogue_draft(self.request('PUT', {'configuration': config, 'baseVersion': 11}))
            self.assertEqual(response.status_code, 200, response.content)
            row = ModuleConfigurationDraft.objects.get()
            self.assertTrue(row.actor_key.startswith('skills-radar:'))
            updated_at = row.updated_at
            views.catalogue_draft(self.request('PUT', {'configuration': config, 'baseVersion': 11}))
            self.assertEqual(ModuleConfigurationDraft.objects.get().updated_at, updated_at)
            stale = views.catalogue_draft(self.request('PUT', {'configuration': config, 'baseVersion': 10}))
            self.assertEqual(stale.status_code, 409)
            invalid = copy.deepcopy(config); invalid[APPROVALS] = {}
            self.assertEqual(views.catalogue_draft(self.request('PUT', {'configuration': invalid, 'baseVersion': 11})).status_code, 400)
        self.assertEqual(ModuleConfigurationDraft.objects.get().configuration, config)
        active = service.learner_payload('apprenticeship', self.source, {}, self.layout)
        self.assertEqual(len(active['catalogue']['records']), 33)
        self.assertFalse(ExtendedIlr.objects.exists())
        self.assertFalse(Assessment.objects.exists())
        other = self.request(); other.user.pk = 92
        self.assertIsNone(json.loads(views.catalogue_draft(other).content)['configuration'])

    def test_legacy_started_ratings_notes_evidence_remain_on_original_version_after_publication(self):
        old = initial('ME_L4'); record = old['records'][0]
        radar = {'assessments': {record['id']: {'ksbId': record['id'], 'level': 'frequently', 'note': 'Historical note', 'evidenceFiles': ['proof.pdf'], 'actionPlan': {'text': 'Keep plan'}}}}
        self.layout[KEY]['ME_L4'] = self.changed()
        loaded = service.learner_payload('apprenticeship', self.source, radar, self.layout)
        self.assertEqual(loaded['catalogue'], old)
        self.assertTrue(loaded['pinned'])
        incoming = {'skillsRadar': {'assessments': {}}}
        service.prepare_answers('apprenticeship', self.source, incoming, {'skillsRadar': radar})
        self.assertEqual(incoming['skillsRadar']['assessments'], radar['assessments'])
        self.assertEqual(incoming['skillsRadar']['catalogueVersions'], {'ME_L4': old['version']})
        self.assertEqual(service.applicable('ME_L4', incoming['skillsRadar'], self.layout)[0], old)

    def test_placeholders_and_other_programme_history_do_not_pin_an_unstarted_assessment(self):
        self.layout[KEY]['ME_L4'] = self.changed()
        radar = {'assessments': {initial('ME_L4')['records'][0]['id']: {'level': None}, initial('APM_L4')['records'][0]['id']: {'level': 'rarely'}}}
        result = service.learner_payload('apprenticeship', self.source, radar, self.layout)
        self.assertEqual(len(result['catalogue']['records']), 34)
        self.assertFalse(result['pinned'])

    def test_first_save_pins_signed_version_across_refresh_without_resetting_answers(self):
        candidate = self.changed(); self.layout[KEY]['ME_L4'] = candidate
        loaded = service.learner_payload('apprenticeship', self.source, {}, self.layout)
        answer = {'level': 'rarely', 'note': 'New note', 'evidenceFiles': ['new.pdf']}
        incoming = {'skillsRadar': {'catalogueToken': loaded['token'], 'catalogueVersions': {'ME_L4': 'forged'}, 'assessments': {candidate['records'][0]['id']: answer}}}
        service.prepare_answers('apprenticeship', self.source, incoming, {})
        self.assertEqual(incoming['skillsRadar']['catalogueVersions'], {'ME_L4': candidate['version']})
        self.assertNotIn('catalogueToken', incoming['skillsRadar'])
        ExtendedIlr.objects.create(learner_kind='apprenticeship', learner_id=self.source.pk, learner_name='Synthetic', wizard_draft=incoming)
        stored = ExtendedIlr.objects.get().wizard_draft
        refreshed = views.learner_catalogue(self.request(staff=False), 'apprenticeship', self.source.pk)
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        self.assertEqual(json.loads(refreshed.content)['catalogue'], candidate)
        self.assertEqual(stored['skillsRadar']['assessments'][candidate['records'][0]['id']], answer)

    def test_signed_old_open_tab_version_survives_new_publication_and_cross_learner_tokens_are_rejected(self):
        loaded = service.learner_payload('apprenticeship', self.source, {}, self.layout)
        self.layout[KEY]['ME_L4'] = self.changed()
        record = initial('ME_L4')['records'][0]
        value = {'skillsRadar': {'catalogueToken': loaded['token'], 'assessments': {record['id']: {'level': 'rarely'}}}}
        service.prepare_answers('apprenticeship', self.source, value, {})
        self.assertEqual(value['skillsRadar']['catalogueVersions']['ME_L4'], initial('ME_L4')['version'])
        wrong = copy.deepcopy(value); wrong['skillsRadar']['catalogueToken'] = loaded['token'] + 'tampered'
        with self.assertRaises(CatalogueError):
            service.prepare_answers('apprenticeship', self.source, wrong, {})
        other = SimpleNamespace(pk=999, programme='Marketing Executive Level 4')
        wrong['skillsRadar']['catalogueToken'] = loaded['token']
        with self.assertRaises(CatalogueError):
            service.prepare_answers('apprenticeship', other, wrong, {})

    def test_saved_pin_cannot_be_replaced_by_client_and_removed_answers_survive_other_step_saves(self):
        old = initial('ME_L4'); self.layout[KEY]['ME_L4'] = self.changed()
        previous = {'skillsRadar': {'catalogueVersions': {'ME_L4': old['version']}, 'assessments': {'historical': {'level': 'rarely', 'evidenceFiles': ['old.pdf']}}}}
        incoming = {'skillsRadar': {'catalogueVersions': {'ME_L4': 'forged'}, 'assessments': {}}}
        service.prepare_answers('apprenticeship', self.source, incoming, previous)
        self.assertEqual(incoming['skillsRadar']['catalogueVersions'], previous['skillsRadar']['catalogueVersions'])
        self.assertEqual(incoming['skillsRadar']['assessments'], previous['skillsRadar']['assessments'])
        unrelated = {'cvJob': {'experienceText': 'Preserved'}}
        service.prepare_answers('apprenticeship', self.source, unrelated, previous)
        self.assertEqual(unrelated['skillsRadar'], previous['skillsRadar'])
        self.assertEqual(unrelated['cvJob']['experienceText'], 'Preserved')

    def test_first_save_without_current_token_is_rejected_and_blank_seeding_does_not_pin(self):
        self.layout[KEY]['ME_L4'] = self.changed()
        record = initial('ME_L4')['records'][0]
        with self.assertRaises(CatalogueError):
            service.prepare_answers('apprenticeship', self.source, {'skillsRadar': {'assessments': {record['id']: {'level': 'rarely'}}}}, {})
        blank = {'skillsRadar': {'catalogueToken': 'unneeded', 'assessments': {record['id']: {'level': None}}}}
        service.prepare_answers('apprenticeship', self.source, blank, {})
        self.assertNotIn('catalogueVersions', blank['skillsRadar'])
        self.assertNotIn('catalogueToken', blank['skillsRadar'])

    def test_existing_builder_publish_activates_catalogue_atomically_and_checks_base_version(self):
        from enrolment_api import wizard_layout
        candidate = self.changed()
        old = copy.deepcopy(self.layout)
        raw = {**copy.deepcopy(self.layout), 'steps': [{'slug': 'skills-radar', 'label': 'Skills Radar', 'builtin': True, 'items': [{'key': 'block.skillsRadar', 'builtin': True}]}],
               KEY: {**self.layout[KEY], 'ME_L4': candidate}, APPROVALS: {'ME_L4': approval(self.layout[KEY]['ME_L4'], candidate)}}
        cursor = MagicMock(); cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = (12, datetime(2026, 11, 3, tzinfo=timezone.utc))
        created = []
        def execute(sql, params=None):
            if sql.startswith('INSERT'):
                created.append(json.loads(params[0]))
        cursor.execute.side_effect = execute
        database = SimpleNamespace(cursor=lambda: cursor)
        with patch.object(wizard_layout, 'CONN', 'default'), patch.object(wizard_layout, 'connections', {'default': database}), \
             patch.object(wizard_layout, 'read_current', return_value=(11, old, None, 'previous')), \
             patch.object(wizard_layout, 'ensure_layout_table'), patch.object(wizard_layout, 'apply_plan') as ddl:
            result = wizard_layout.publish_wizard_layout(self.request('PUT', {'layout': raw, 'baseVersion': 11}))
            self.assertEqual(result.status_code, 200, result.content)
            ddl.assert_called_once_with(cursor, {})
            self.assertEqual(len(created), 1)
            stale = wizard_layout.publish_wizard_layout(self.request('PUT', {'layout': raw, 'baseVersion': 10}))
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(len(created), 1)
            invalid = copy.deepcopy(raw); invalid[APPROVALS] = {}
            rejected = wizard_layout.publish_wizard_layout(self.request('PUT', {'layout': invalid, 'baseVersion': 11}))
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(len(created), 1)
        self.assertEqual(self.layout, old)
        self.layout = created[0]
        self.assertNotIn(APPROVALS, self.layout)
        from programme_suitability.modules_configuration import validate as validate_modules
        self.assertEqual(self.layout['programmeModules'], validate_modules(old['programmeModules'], publishing=True))
        self.assertEqual(len(service.learner_payload('apprenticeship', self.source, {}, self.layout)['catalogue']['records']), 34)
        self.assertFalse(ExtendedIlr.objects.exists())
        self.assertFalse(Assessment.objects.exists())

    def test_historical_published_snapshot_is_resolved_after_a_later_removal(self):
        from enrolment_api import wizard_layout
        older = self.changed(); previous_layout = {**self.layout, KEY: {**self.layout[KEY], 'ME_L4': older}}
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id, "Layout") VALUES (%s, %s)', [12, json.dumps(previous_layout)])
        self.layout[KEY]['ME_L4'] = initial('ME_L4')
        added = next(r for r in older['records'] if r['code'] == 'K99')
        radar = {'catalogueVersions': {'ME_L4': older['version']}, 'assessments': {added['id']: {'level': 'rarely', 'evidenceFiles': ['historic.pdf']}}}
        with patch.object(wizard_layout, 'CONN', 'default'):
            result = service.learner_payload('apprenticeship', self.source, radar, self.layout)
        self.assertEqual(result['catalogue'], older)
        self.assertEqual(len(result['catalogue']['records']), 34)
        self.assertEqual(len(self.layout[KEY]['ME_L4']['records']), 33)

    def test_real_answer_endpoint_pins_and_projects_ratings_notes_evidence_and_retains_them_on_other_saves(self):
        from django.db import transaction
        from enrolment_api import extended_ilr
        from enrolment_api.models import WizardKsbAssessment
        candidate = self.changed(); self.layout[KEY]['ME_L4'] = candidate
        loaded = service.learner_payload('apprenticeship', self.source, {}, self.layout)
        record = next(r for r in candidate['records'] if r['code'] == 'K99')
        answer = {'ksbId': record['id'], 'level': 'frequently', 'note': 'Existing written reason', 'evidenceFiles': ['saved-evidence.pdf'], 'actionPlan': {'text': 'Existing action'}}
        data = {'draft': {'skillsRadar': {'standardId': '', 'catalogueToken': loaded['token'], 'assessments': {record['id']: answer}}}}
        real_atomic = transaction.atomic
        with patch.object(extended_ilr.transaction, 'atomic', side_effect=lambda using=None, **kw: real_atomic(using='default', **kw)), \
             patch.object(extended_ilr, 'read_projection', return_value={}), patch.object(extended_ilr, 'project_custom_fields'), \
             patch.object(EnrolmentUser.objects, 'select_for_update', wraps=EnrolmentUser.objects.select_for_update) as lock:
            response = extended_ilr.extended_ilr(self.request('PUT', data, staff=False), 'apprenticeship', self.source.pk)
            self.assertEqual(response.status_code, 200, response.content)
            lock.assert_called_once()
            second = extended_ilr.extended_ilr(self.request('PUT', {'draft': {}}, staff=False), 'apprenticeship', self.source.pk)
            self.assertEqual(second.status_code, 200, second.content)
        row = ExtendedIlr.objects.get()
        self.assertEqual(row.wizard_draft['skillsRadar']['catalogueVersions'], {'ME_L4': candidate['version']})
        self.assertEqual(row.wizard_draft['skillsRadar']['assessments'][record['id']], answer)
        projected = WizardKsbAssessment.objects.get()
        self.assertEqual(projected.note, answer['note'])
        self.assertEqual(projected.evidence_files, answer['evidenceFiles'])
        self.assertEqual(projected.level, answer['level'])
        self.assertEqual(projected.action_text, answer['actionPlan']['text'])

    def test_legacy_per_step_answers_are_recovered_when_a_partial_json_draft_has_other_sections_only(self):
        from enrolment_api import extended_ilr
        record = initial('ME_L4')['records'][0]
        radar = {'standardId': '', 'assessments': {record['id']: {'ksbId': record['id'], 'level': 'rarely', 'note': 'Legacy note', 'evidenceFiles': ['legacy.pdf'], 'actionPlan': None}}}
        ExtendedIlr.objects.create(learner_kind='apprenticeship', learner_id=self.source.pk, learner_name='Synthetic', wizard_draft={'cvJob': {'experienceText': 'Keep CV'}})
        self.layout[KEY]['ME_L4'] = self.changed()
        from enrolment_api.models import WizardKsbAssessment
        WizardKsbAssessment.objects.create(learner_kind='apprenticeship', learner_id=self.source.pk,
            ksb_id=record['id'], level='rarely', note='Legacy note', evidence_files=['legacy.pdf'])
        response = views.learner_catalogue(self.request(staff=False), 'apprenticeship', self.source.pk)
        self.assertEqual(json.loads(response.content)['catalogue'], initial('ME_L4'))
        payload = extended_ilr._read_extended_ilr_row('apprenticeship', self.source.pk, 'Synthetic')
        self.assertEqual(payload['draft']['skillsRadar'], radar)
        self.assertEqual(payload['draft']['cvJob']['experienceText'], 'Keep CV')
        self.assertNotIn('skillsRadar', ExtendedIlr.objects.get().wizard_draft)  # Read-only recovery.
