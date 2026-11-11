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
