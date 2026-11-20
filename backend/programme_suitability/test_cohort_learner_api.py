"""Authenticated learner HTTP checks for Cohorts and immutable layout pins."""
import json
import os
from unittest.mock import patch

from django.db import connections
from django.test import Client, TestCase, override_settings

from enrolment_api.models import ExtendedIlr
from enrolment_api.wizard_layout import CONN
from learner_api.models import EnrolmentUser, LearnerProfile
from login.models import LoginAccount
from login.sessions import COOKIE_NAME, issue_session
from programme_suitability.models import Assessment


@override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
class CohortLearnerApiTests(TestCase):
    databases = {'default', 'enrolment'}
    ksb_id = 'ksb:ME_L4:K1'

    @patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': 'synthetic-reader-config', 'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    @patch('enrollment_service.lms_readonly.get_assigned_cohort')
    def test_lms_placement_is_shared_by_step_six_and_profile_without_snapshot_changes(self, read):
        from .home_cohort import home_cohort_for_board
        profile = LearnerProfile.objects.get(enrolment_id=8101)
        # A staff change after publication need not exist in an old wizard layout.
        profile.cohort_id = 'new-lms-cohort'
        profile.group_id = 'saved-group'
        profile.save(update_fields=['cohort_id', 'group_id'])
        read.return_value = [dict(programme_id='PROG-ME-L4', cohort_id='new-lms-cohort',
            cohort_name='Current LMS cohort', group_id='saved-group', group_name='Current LMS group',
            module_catalogue_id='lms-module', title='Current module', sessions_number=16, start_date=None, end_date=None)]
        before = Assessment.objects.filter(learner_id=8101).values().get()
        response = self.clients[8101].get('/enrolment_api/programme-suitability/apprenticeship/8101/modules/')
        self.assertEqual(response.status_code, 200, response.content)
        home = response.json()['homeCohort']
        self.assertEqual(home['name'], 'Current LMS cohort')
        self.assertEqual(home['groupName'], 'Current LMS group')
        source = EnrolmentUser.all_learners.get(pk=8101)
        self.assertEqual(home, home_cohort_for_board(source))
        self.assertEqual(home['lmsModules'][0]['id'], 'lms-module')
        self.assertEqual(Assessment.objects.filter(learner_id=8101).values().get(), before)
        read.assert_called_with('PROG-ME-L4', 'new-lms-cohort', 'saved-group')

    def prepare_account_only_assignment(self):
        # Disposable fixtures only: model deletion would traverse unmanaged
        # lifecycle tables that this focused test runner deliberately excludes.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM "Learner".learners WHERE enrolment_id = %s', [8101])
        EnrolmentUser.all_learners.filter(pk=8101).update(cohort='Feb 2027')
        outcome = {'status': 'APPLIED', 'programmeId': 'PROG-ME-L4', 'programmeName': 'Marketing Executive',
                   'cohortAssignmentVersion': 1, 'cohortId': 'ME_L4-FEB-2027',
                   'cohortName': 'Feb 2027', 'cohortPublicationVersion': 1, 'profileId': None}
        Assessment.objects.filter(learner_id=8101).update(review={'programmeAssignment': outcome})
        return outcome

    @patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': 'synthetic-reader-config', 'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    @patch('enrollment_service.lms_readonly.get_programme_cohorts')
    @patch('enrollment_service.lms_readonly.get_assigned_cohort')
    def test_legacy_account_recovers_after_matching_lms_cohort_is_added_without_backfill(self, modules, cohorts):
        self.prepare_account_only_assignment()
        before = Assessment.objects.filter(learner_id=8101).values().get()
        cohorts.return_value = []
        modules.return_value = []
        endpoint = '/enrolment_api/programme-suitability/apprenticeship/8101/modules/'
        first = self.clients[8101].get(endpoint)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()['homeCohort']['reason'], 'assigned_cohort_not_in_lms')
        cohorts.return_value = [dict(programme_id='PROG-ME-L4', cohort_id='real-lms-id', cohort_name='Feb 2027')]
        modules.side_effect = lambda programme, cohort, group=None: ([dict(programme_id=programme,
            cohort_id=cohort, cohort_name='Feb 2027', module_catalogue_id='lms-m1', title='Live module',
            group_id='g1', group_name='G1', sessions_number=16, start_date=None, end_date=None)] if cohort == 'real-lms-id' else [])
        second = self.clients[8101].get(endpoint)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()['homeCohort']['cohortId'], 'real-lms-id')
        self.assertIsNone(second.json()['homeCohort']['groupId'])
        self.assertEqual(second.json()['homeCohort']['lmsModules'][0]['groupId'], 'g1')
        self.assertEqual(Assessment.objects.filter(learner_id=8101).values().get(), before)
        self.assertFalse(LearnerProfile.objects.filter(enrolment_id=8101).exists())

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_account_only_assignment_resolves_in_step_six_and_admin_without_creating_profile(self):
        self.prepare_account_only_assignment()
        from .home_cohort import cohort_label_for_board
        before = Assessment.objects.filter(learner_id=8101).values().get()
        response = self.clients[8101].get('/enrolment_api/programme-suitability/apprenticeship/8101/modules/')
        self.assertEqual(response.status_code, 200, response.content)
        home = response.json()['homeCohort']
        self.assertEqual(home['status'], 'resolved')
        self.assertEqual(home['cohortId'], 'ME_L4-FEB-2027')
        source = EnrolmentUser.all_learners.get(pk=8101)
        self.assertEqual(home['name'], cohort_label_for_board(source, source.cohort))
        self.assertEqual(Assessment.objects.filter(learner_id=8101).values().get(), before)
        self.assertFalse(LearnerProfile.objects.filter(enrolment_id=8101).exists())

    def test_account_only_assignment_reads_saved_publication_without_adopting_new_latest(self):
        self.prepare_account_only_assignment()
        from .home_cohort import read_home_cohort
        with connections[CONN].cursor() as cursor:
            cursor.execute('DELETE FROM enrolment."Wizard_Layout_Assignments" WHERE "Learner_id" = %s', [8101])
        Assessment.objects.filter(learner_id=8101).update(modules_configuration_version=None)
        row = Assessment.objects.get(learner_id=8101)
        home = read_home_cohort(row, 'apprenticeship', 8101, None)
        self.assertEqual(home['status'], 'resolved')
        self.assertEqual(home['layoutVersion'], 1)  # Current is 2, assignment was saved against 1.
        with connections[CONN].cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM enrolment."Wizard_Layout_Assignments" WHERE "Learner_id" = %s', [8101])
            self.assertEqual(cursor.fetchone()[0], 0)  # Display creates no pin.

    def test_recorded_cohort_cannot_override_a_conflicting_explicit_historical_pin(self):
        self.prepare_account_only_assignment()
        from .home_cohort import read_home_cohort
        with connections[CONN].cursor() as cursor:
            cursor.execute('UPDATE enrolment."Wizard_Layout_Assignments" SET "Pinned_version" = %s WHERE "Learner_id" = %s', [2, 8101])
        row = Assessment.objects.get(learner_id=8101)
        home = read_home_cohort(row, 'apprenticeship', 8101, 1)
        self.assertEqual(home['reason'], 'cohort_publication_mismatch')

    def test_later_manual_account_assignment_is_preserved(self):
        self.prepare_account_only_assignment()
        from .home_cohort import read_home_cohort, cohort_label_for_board
        EnrolmentUser.all_learners.filter(pk=8101).update(cohort='Staff assigned group')
        row = Assessment.objects.get(learner_id=8101)
        self.assertEqual(read_home_cohort(row, 'apprenticeship', 8101, 1)['reason'], 'account_assignment_changed')
        source = EnrolmentUser.all_learners.get(pk=8101)
        self.assertEqual(cohort_label_for_board(source, source.cohort), 'Staff assigned group')

    def test_old_applied_sync_and_legacy_text_do_not_become_a_new_assignment(self):
        outcome = self.prepare_account_only_assignment()
        outcome.pop('cohortAssignmentVersion')
        outcome['automaticCohort'] = ['ME_L4-FEB-2027', 'Feb 2027', False]
        Assessment.objects.filter(learner_id=8101).update(review={'programmeAssignment': outcome})
        from .home_cohort import read_home_cohort
        row = Assessment.objects.get(learner_id=8101)
        before = json.dumps(row.review, sort_keys=True)
        self.assertEqual(read_home_cohort(row, 'apprenticeship', 8101, 1)['reason'], 'assignment_missing')
        row.refresh_from_db()
        self.assertEqual(json.dumps(row.review, sort_keys=True), before)

    @override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=False)
    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_display_metadata_translates_persisted_id_while_rollout_is_disabled(self):
        LearnerProfile.objects.filter(enrolment_id=8101).update(programme_id='PROG-ME-L4')
        before = Assessment.objects.filter(learner_id=8101).values().get()
        profile_before = LearnerProfile.objects.filter(enrolment_id=8101).values().get()
        response = self.clients[8101].get('/enrolment_api/programme-suitability/apprenticeship/8101/modules/')
        self.assertEqual(response.status_code, 200)
        home = response.json()['homeCohort']
        self.assertEqual(home['status'], 'resolved')
        self.assertEqual(home['programmeId'], 'ME_L4')
        self.assertEqual(home['cohortId'], 'ME_L4-FEB-2027')
        self.assertEqual(home['name'], 'Feb 2027')
        self.assertEqual(home['layoutVersion'], 1)  # Never replace with latest v2.
        self.assertEqual(Assessment.objects.filter(learner_id=8101).values().get(), before)
        self.assertEqual(LearnerProfile.objects.filter(enrolment_id=8101).values().get(), profile_before)

    @override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=False)
    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_missing_canonical_or_pinned_publication_keeps_modules_and_pending_label(self):
        with connections[CONN].cursor() as cursor:
            cursor.execute('UPDATE enrolment."Wizard_Form_Layouts" SET "Layout" = %s WHERE id = %s',
                           [json.dumps({'programmeModules': {'programmes': []}}), 1])
        endpoint = '/enrolment_api/programme-suitability/apprenticeship/8101/modules/'
        response = self.clients[8101].get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['homeCohort']['reason'], 'canonical_publication_missing')
        with connections[CONN].cursor() as cursor:
            cursor.execute('DELETE FROM enrolment."Wizard_Form_Layouts" WHERE id = %s', [1])
        response = self.clients[8101].get(endpoint)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['homeCohort']['reason'], 'pinned_publication_unavailable')

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_existing_cohort_scoped_modules_also_accept_curriculum_ids(self):
        LearnerProfile.objects.filter(enrolment_id=8101).update(programme_id='PROG-ME-L4')
        response = self.clients[8101].get('/enrolment_api/programme-suitability/apprenticeship/8101/modules/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([m['id'] for m in response.json()['configuration']['programmes'][0]['modules']],
                         ['FEB-A', 'FEB-B'])
        self.assertEqual(response.json()['homeCohort']['layoutVersion'], 1)

    @staticmethod
    def module(identifier, title, sessions):
        return {'id': identifier, 'title': title, 'sessions': sessions,
                'active': True, 'selection': 'required'}

    def cohort(self, identifier, name, modules, covered):
        return {'id': identifier, 'programmeId': 'ME_L4', 'name': name,
                'status': 'planned', 'modules': modules, 'mappings': [
                    {'programmeId': 'ME_L4', 'moduleId': module_id,
                     'ksbId': self.ksb_id, 'status': 'active'} for module_id in covered]}

    def layout(self, feb_sessions, feb_coverage, catalogue_version, *, include_june):
        cohorts = [self.cohort('ME_L4-FEB-2027', 'Feb 2027', [
            self.module('FEB-A', 'Module A', feb_sessions),
            self.module('FEB-B', 'Module B', 14)], feb_coverage)]
        if include_june:
            cohorts.append(self.cohort('ME_L4-JUN-2027', 'June 2027', [
                self.module('JUN-C', 'Module C', 10),
                self.module('JUN-D', 'Module D', 12)], ['JUN-C', 'JUN-D']))
        return {
            'programmeStructure': {'schemaVersion': 2, 'programmes': [{
                'id': 'ME_L4', 'title': 'Marketing Executive', 'status': 'active',
                'modules': [], 'ksbs': [{'id': self.ksb_id, 'code': 'K1',
                    'kind': 'Knowledge', 'description': 'K1', 'status': 'active'}],
                'mappings': [], 'cohorts': cohorts,
                'selectionPolicy': {'type': 'required-optional'}}]},
            'programmeModules': {'schemaVersion': 2, 'programmes': [{
                'id': 'ME_L4', 'title': 'Marketing Executive', 'modules': []}]},
            'skillsRadarCatalogues': {'ME_L4': {'programmeId': 'ME_L4',
                'records': [{'id': self.ksb_id, 'code': 'K1', 'kind': 'Knowledge',
                             'description': 'K1', 'moduleKey': 'FEB-A'}],
                'version': catalogue_version}},
        }

    def setUp(self):
        old = self.layout(16, ['FEB-A'], 'old-catalogue', include_june=False)
        new = self.layout(18, ['FEB-B'], 'new-catalogue', include_june=True)
        with connections[CONN].cursor() as cursor:
            for number, layout in ((1, old), (2, new)):
                cursor.execute('INSERT INTO enrolment."Wizard_Form_Layouts" (id, "Layout") VALUES (%s, %s)',
                               [number, json.dumps(layout)])
            cursor.execute('INSERT INTO enrolment."Wizard_Layout_Assignments" '
                           '("Learner_kind", "Learner_id", "Pinned_version") VALUES (%s, %s, %s)',
                           ['apprenticeship', 8101, 1])
        self.clients = {}
        self.create_learner(8101, 'ME_L4-FEB-2027', module_version=1,
                            catalogue_version='old-catalogue')
        self.create_learner(8102, 'ME_L4-JUN-2027')
        self.create_learner(8103, 'ME_L4-FEB-2027')

    def create_learner(self, pk, cohort_id, *, module_version=None, catalogue_version=None):
        learner = EnrolmentUser(id=pk, username=f'learner-{pk}', email=f'{pk}@example.test',
            learner_type='apprenticeship', onboarding_status='In progress',
            programme='Marketing Executive', programme_status='Onboarding')
        EnrolmentUser.all_learners.bulk_create([learner])
        LearnerProfile.objects.create(enrolment_id=pk, email=learner.email,
            full_name=learner.username, lifecycle_status='active',
            programme='Marketing Executive', programme_id='ME_L4',
            cohort_id=cohort_id, cohort=cohort_id)
        Assessment.objects.create(learner_id=pk, learner_kind='apprenticeship',
            selected_programme='ME_L4', pathway='marketing', configuration_version=1,
            confirmed_at='2027-01-01T00:00:00Z', selection_confirmed_at='2027-01-01T00:00:00Z',
            configuration={'programmes': []}, modules_configuration={'schemaVersion': 2,
                'programmes': [{'id': 'ME_L4', 'modules': []}]},
            modules_configuration_version=module_version, modules_revision=0,
            responses={}, result={}, modules_selection={'programme': 'ME_L4',
                'modules': ['FEB-A']} if pk == 8101 else None)
        ExtendedIlr.objects.create(learner_kind='apprenticeship', learner_id=pk,
            learner_name=learner.username, wizard_draft={'skillsRadar': {
                'assessments': {self.ksb_id: {'level': 'often'}},
                'catalogueVersions': {'ME_L4': catalogue_version} if catalogue_version else {}}})
        account = LoginAccount.objects.create(subject_type='learner', subject_id=pk,
            role='learner', email=learner.email, display_name=learner.username, is_active=True)
        token, _, _ = issue_session(account)
        client = Client()
        client.cookies[COOKIE_NAME] = token
        self.clients[pk] = client

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_authenticated_modules_respect_cohort_and_historical_layout_pin(self):
        endpoint = lambda pk: f'/enrolment_api/programme-suitability/apprenticeship/{pk}/modules/'
        self.assertEqual(Client().get(endpoint(8101)).status_code, 401)
        old = self.clients[8101].get(endpoint(8101))
        june = self.clients[8102].get(endpoint(8102))
        latest_feb = self.clients[8103].get(endpoint(8103))
        self.assertEqual([r.status_code for r in (old, june, latest_feb)], [200, 200, 200])
        modules = lambda response: [(m['id'], m['sessions']) for m in response.json()['configuration']['programmes'][0]['modules']]
        self.assertEqual(modules(old), [('FEB-A', 16), ('FEB-B', 14)])
        self.assertEqual(modules(june), [('JUN-C', 10), ('JUN-D', 12)])
        self.assertEqual(modules(latest_feb), [('FEB-A', 18), ('FEB-B', 14)])
        self.assertEqual(old.json()['savedSelection']['modules'], ['FEB-A'])
        self.assertEqual(old.json()['configurationVersion'], 1)
        self.assertEqual(self.clients[8101].get(endpoint(8102)).status_code, 404)
        self.assertEqual(self.clients[8102].get(endpoint(8101)).status_code, 404)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_authenticated_skills_radar_uses_matching_historical_coverage(self):
        endpoint = lambda pk: f'/enrolment_api/skills-radar/apprenticeship/{pk}/'
        old = self.clients[8101].get(endpoint(8101))
        june = self.clients[8102].get(endpoint(8102))
        latest_feb = self.clients[8103].get(endpoint(8103))
        self.assertEqual([r.status_code for r in (old, june, latest_feb)], [200, 200, 200])
        record = lambda response: response.json()['catalogue']['records'][0]
        self.assertEqual(record(old)['moduleKeys'], ['FEB-A'])
        self.assertEqual(record(old)['moduleNames'], ['Module A'])
        self.assertEqual(record(june)['moduleKeys'], ['JUN-C', 'JUN-D'])
        self.assertEqual(record(june)['moduleNames'], ['Module C', 'Module D'])
        self.assertEqual(record(latest_feb)['moduleKeys'], ['FEB-B'])
        self.assertEqual(record(old)['id'], record(june)['id'])
        self.assertEqual(len(june.json()['catalogue']['records']), 1)
        self.assertEqual(self.clients[8101].get(endpoint(8102)).status_code, 404)
        self.assertEqual(ExtendedIlr.objects.get(learner_id=8101).wizard_draft['skillsRadar'], {
            'assessments': {self.ksb_id: {'level': 'often'}},
            'catalogueVersions': {'ME_L4': 'old-catalogue'}})
        self.assertEqual(Assessment.objects.get(learner_id=8101).modules_configuration_version, 1)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_admin_cohort_change_does_not_rewrite_historical_answers_or_pin(self):
        LearnerProfile.objects.filter(enrolment_id=8101).update(
            cohort_id='ME_L4-JUN-2027', cohort='June 2027')
        endpoint = '/enrolment_api/programme-suitability/apprenticeship/8101/modules/'
        response = self.clients[8101].get(endpoint)
        self.assertEqual(response.status_code, 409)
        self.assertIn('saved published layout', response.json()['error'])
        radar = self.clients[8101].get('/enrolment_api/skills-radar/apprenticeship/8101/')
        self.assertEqual(radar.status_code, 409)
        self.assertEqual(Assessment.objects.get(learner_id=8101).modules_selection['modules'], ['FEB-A'])
        self.assertEqual(Assessment.objects.get(learner_id=8101).modules_configuration_version, 1)
        self.assertEqual(ExtendedIlr.objects.get(learner_id=8101).wizard_draft['skillsRadar']['assessments'][self.ksb_id]['level'], 'often')

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_completed_cohort_keeps_existing_learner_access(self):
        with connections[CONN].cursor() as cursor:
            cursor.execute('SELECT "Layout" FROM enrolment."Wizard_Form_Layouts" WHERE id = %s', [1])
            layout = json.loads(cursor.fetchone()[0])
            layout['programmeStructure']['programmes'][0]['cohorts'][0]['status'] = 'completed'
            cursor.execute('UPDATE enrolment."Wizard_Form_Layouts" SET "Layout" = %s WHERE id = %s',
                           [json.dumps(layout), 1])
        modules = self.clients[8101].get('/enrolment_api/programme-suitability/apprenticeship/8101/modules/')
        radar = self.clients[8101].get('/enrolment_api/skills-radar/apprenticeship/8101/')
        self.assertEqual(modules.status_code, 200)
        self.assertEqual(radar.status_code, 200)
        self.assertEqual(modules.json()['configuration']['programmes'][0]['modules'][0]['sessions'], 16)
        self.assertEqual(radar.json()['catalogue']['records'][0]['moduleKeys'], ['FEB-A'])

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_legacy_zero_pin_does_not_adopt_latest_cohort_modules(self):
        Assessment.objects.filter(learner_id=8103).update(modules_configuration_version=0)
        response = self.clients[8103].get('/enrolment_api/programme-suitability/apprenticeship/8103/modules/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['configurationVersion'], 0)
        self.assertEqual(response.json()['configuration']['programmes'][0]['modules'], [])
        radar = self.clients[8103].get('/enrolment_api/skills-radar/apprenticeship/8103/')
        self.assertEqual(radar.status_code, 200)
        self.assertNotEqual(radar.json()['catalogue']['records'][0].get('moduleKeys'), ['FEB-B'])
