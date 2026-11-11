"""Real LoginSession middleware, CSRF enforcement and shared ORM persistence."""
import json
import os
from unittest.mock import patch
from django.test import Client, TestCase
from django.db import connections
from learner_api.models import StaffUser
from login.models import LoginAccount
from login.sessions import issue_session, COOKIE_NAME
from .models import ModuleConfigurationDraft
from enrolment_api.programme_structure_views import SHARED_DRAFT_KEY


class SharedDraftAuthenticationTests(TestCase):
    databases = {'default', 'enrolment'}

    def _seed_verified_curriculum_rows(self):
        programmes = (
            ('PROG-ME-L4', 'Marketing Executive Level 4'),
            ('PROG-MM-L6', 'Marketing Manager Level 6'),
            ('PROG-20260907141619067932', 'Associate Project Manager Level 4'),
            ('PROG-PCP-L6', 'Project Controls Professional Level 6'),
        )
        cohorts = (
            ('COHORT-TEST-ME-FEB', 'Feb 2027', 'PROG-ME-L4'),
            ('COHORT-TEST-ME-JUN', 'June 2027', 'PROG-ME-L4'),
            ('COHORT-TEST-MM-FEB', 'Feb 2027', 'PROG-MM-L6'),
            ('COHORT-TEST-APM-FEB', 'Feb 2027', 'PROG-20260907141619067932'),
            ('COHORT-TEST-PCP-FEB', 'Feb 2027', 'PROG-PCP-L6'),
            ('COHORT-20261111015402258877CC69FD92DAF3', 'hisham of aa7', 'PROG-ME-L4'),
        )
        with connections['enrolment'].cursor() as cursor:
            for programme_id, name in programmes:
                cursor.execute('INSERT INTO curriculum.programmes (programme_id, name) VALUES (%s, %s)', [programme_id, name])
            for cohort_id, name, programme_id in cohorts:
                cursor.execute('INSERT INTO curriculum.cohorts (cohort_id, cohort_name, programme_id, status) VALUES (%s, %s, %s, %s)', [cohort_id, name, programme_id, 'planned'])

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_reconcile_prepares_verified_curriculum_correction_without_persisting(self):
        from copy import deepcopy
        self._seed_verified_curriculum_rows()
        programmes = []
        for programme_id, curriculum_id in (
            ('ME_L4', 'PROG-ME-L4'), ('MM_L6', 'PROG-MM-L6'),
            ('APM_L4', 'PROG-20260907141619067932'), ('PCP_L6', 'PROG-PCP-L6'),
        ):
            cohort_id = {
                'ME_L4': 'COHORT-TEST-ME-FEB', 'MM_L6': 'COHORT-TEST-MM-FEB',
                'APM_L4': 'COHORT-TEST-APM-FEB', 'PCP_L6': 'COHORT-TEST-PCP-FEB',
            }[programme_id]
            programme = {'id': programme_id, 'title': programme_id, 'modules': [], 'ksbs': [], 'mappings': [], 'curriculumProgrammeId': None,
                         'cohorts': [{'id': cohort_id, 'programmeId': programme_id, 'name': 'Feb 2027', 'modules': [], 'mappings': []}]}
            if programme_id == 'ME_L4':
                programme['cohorts'].extend([
                    {'id': 'COHORT-TEST-ME-JUN', 'programmeId': programme_id, 'name': 'June 2027', 'modules': [], 'mappings': []},
                    {'id': 'COHORT-20261111015402258877CC69FD92DAF3', 'programmeId': programme_id, 'name': 'hisham of aa7', 'modules': [], 'mappings': []},
                ])
            programmes.append(programme)
        original = {'schemaVersion': 2, '_draftRevision': 1, 'programmes': programmes}
        row = ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        row.configuration = deepcopy(original)
        row.save(update_fields=['configuration'])
        client = self.clients[114]
        csrf = client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        response = client.post('/enrolment_api/programme-structure/draft/reconcile/', data=json.dumps({'baseVersion': 16, 'revision': 1}), content_type='application/json', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertFalse(payload['persisted'])
        self.assertEqual(payload['removedCohorts'], ['COHORT-20261111015402258877CC69FD92DAF3'])
        self.assertEqual({p['id']: p['curriculumProgrammeId'] for p in payload['structure']['programmes']}, {
            'ME_L4': 'PROG-ME-L4', 'MM_L6': 'PROG-MM-L6',
            'APM_L4': 'PROG-20260907141619067932', 'PCP_L6': 'PROG-PCP-L6',
        })
        self.assertEqual(len(payload['structure']['programmes'][0]['cohorts']), 2)
        persisted = ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        self.assertEqual(persisted.configuration, original)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_reconcile_handles_numeric_legacy_application_cohort_id(self):
        """A legacy bigint placement column must not break identity verification.

        Production ``enrolment.applications.cohort_id`` is numeric, while the
        canonical Curriculum identity is a ``COHORT-*`` string.  The
        correction must still verify all four authoritative programme rows,
        including the APM identity, and remove only the unreferenced test
        Cohort.
        """
        self._seed_verified_curriculum_rows()
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('CREATE TABLE enrolment.applications (id INTEGER PRIMARY KEY, cohort_id INTEGER)')
            cursor.execute('INSERT INTO enrolment.applications (id, cohort_id) VALUES (1, 12345)')
        programmes = []
        for programme_id, curriculum_id in (
            ('ME_L4', 'PROG-ME-L4'), ('MM_L6', 'PROG-MM-L6'),
            ('APM_L4', 'PROG-20260907141619067932'), ('PCP_L6', 'PROG-PCP-L6'),
        ):
            cohort_id = {
                'ME_L4': 'COHORT-TEST-ME-FEB', 'MM_L6': 'COHORT-TEST-MM-FEB',
                'APM_L4': 'COHORT-TEST-APM-FEB', 'PCP_L6': 'COHORT-TEST-PCP-FEB',
            }[programme_id]
            cohorts = [{'id': cohort_id, 'name': 'Feb 2027', 'modules': [], 'mappings': []}]
            if programme_id == 'ME_L4':
                cohorts.extend([
                    {'id': 'COHORT-TEST-ME-JUN', 'name': 'June 2027', 'modules': [], 'mappings': []},
                    {'id': 'COHORT-20261111015402258877CC69FD92DAF3', 'name': 'hisham of aa7', 'modules': [], 'mappings': []},
                ])
            programmes.append({'id': programme_id, 'title': programme_id, 'modules': [], 'ksbs': [],
                               'mappings': [], 'curriculumProgrammeId': None, 'cohorts': cohorts})
        original = {'schemaVersion': 2, '_draftRevision': 1, 'programmes': programmes}
        row = ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        row.configuration = original
        row.save(update_fields=['configuration'])
        client = self.clients[114]
        csrf = client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        response = client.post(
            '/enrolment_api/programme-structure/draft/reconcile/',
            data=json.dumps({'baseVersion': 16, 'revision': 1}),
            content_type='application/json', HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload['verifiedCurriculumProgrammeIds']['APM_L4'], 'PROG-20260907141619067932')
        self.assertEqual(payload['removedCohorts'], ['COHORT-20261111015402258877CC69FD92DAF3'])

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_generated_module_identity_and_mapping_survive_real_shared_save_and_edit(self):
        from uuid import uuid4
        from copy import deepcopy
        identity=str(uuid4())
        structure=deepcopy(self.structure)
        programme=structure['programmes'][0]
        programme['ksbs']=[{'id':'stable-K1','code':'K1','description':'Marketing','kind':'Knowledge','status':'active'}]
        cohort=programme['cohorts'][0]
        cohort['modules']=[{'id':identity,'title':'New Module','selection':'optional','sessions':12}]
        cohort['mappings']=[{'programmeId':'ME_L4','moduleId':identity,'ksbId':'stable-K1','status':'active'}]
        client=self.clients[114]
        token=client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        url='/enrolment_api/programme-structure/draft/'
        saved=client.put(url,data=json.dumps({'structure':structure,'revision':1,'baseVersion':16}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(saved.status_code,200,saved.content)
        edited=saved.json()['structure']
        edited['programmes'][0]['cohorts'][0]['modules'][0]['title']='Renamed'
        saved=client.put(url,data=json.dumps({'structure':edited,'revision':2,'baseVersion':16}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(saved.status_code,200)
        restored=self.clients[108].get(url).json()['structure']['programmes'][0]['cohorts'][0]
        self.assertEqual(restored['modules'][0]['id'],identity)
        self.assertEqual(restored['mappings'][0]['moduleId'],identity)
        duplicate=deepcopy(saved.json()['structure'])
        duplicate['programmes'][0]['cohorts'][0]['modules'].append(deepcopy(restored['modules'][0]))
        refused=client.put(url,data=json.dumps({'structure':duplicate,'revision':3,'baseVersion':16}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(refused.status_code,400)

    def setUp(self):
        self.structure = {'schemaVersion': 2, '_draftRevision': 1, 'programmes': [{
            'id': 'ME_L4', 'title': 'Marketing Executive', 'modules': [], 'ksbs': [], 'mappings': [],
            'cohorts': [{'id': 'REAL-FEB', 'programmeId': 'ME_L4', 'name': 'Feb 2027', 'modules': [], 'mappings': []}]}]}
        ModuleConfigurationDraft.objects.create(actor_key=SHARED_DRAFT_KEY, base_version=16, configuration=self.structure)
        self.clients = {}
        for pk in (108, 114):
            StaffUser.objects.create(id=pk, username=f'Synthetic admin {pk}', access='enrolment', position='Admin')
            account = LoginAccount.objects.create(id=pk, subject_type='staff', subject_id=pk,
                role='admin', email=f'admin-{pk}@example.test', display_name='Synthetic Admin', is_active=True)
            token, _, _ = issue_session(account)
            client = Client(enforce_csrf_checks=True)
            client.cookies[COOKIE_NAME] = token
            self.clients[pk] = client

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_real_admin_sessions_reproduce_html_csrf_403_then_save_and_share(self):
        client = self.clients[114]
        url = '/enrolment_api/programme-structure/draft/'
        self.assertEqual(client.get(url).status_code, 200)
        body = {'structure': self.structure, 'baseVersion': 16, 'revision': 1}
        rejected = client.put(url, data=json.dumps(body), content_type='application/json')
        self.assertEqual(rejected.status_code, 403)
        self.assertIn(b'CSRF', rejected.content)
        self.assertTrue(rejected['Content-Type'].startswith('text/html'))
        security = client.get('/learner_api/enrollment-csrf/')
        self.assertEqual(security.status_code, 200)
        token = security.json()['csrfToken']
        body['structure']['programmes'][0]['cohorts'].append({'id': 'REAL-JUNE', 'name': 'June 2027',
            'programmeId': 'ME_L4', 'modules': [], 'mappings': []})
        saved = client.put(url, data=json.dumps(body), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(saved.json()['revision'], 2)
        other = self.clients[108].get(url)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json(), client.get(url).json())
        self.assertEqual(other.json()['structure']['programmes'][0]['cohorts'][1]['name'], 'June 2027')
        conflict = client.put(url, data=json.dumps(body), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY).configuration['_draftRevision'], 2)
        admin108 = self.clients[108]
        token108 = admin108.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        shared = other.json()
        response108 = admin108.put(url, data=json.dumps(shared), content_type='application/json', HTTP_X_CSRFTOKEN=token108)
        self.assertEqual(response108.status_code, 200)
        self.assertEqual(response108.json()['revision'], 3)
        self.assertEqual(admin108.get(url).json(), client.get(url).json())
        self.assertEqual(Client().get(url).status_code, 401)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_admin_without_enrolment_access_cannot_read_or_write_shared_draft(self):
        StaffUser.objects.filter(pk=114).update(access='coach')
        client = self.clients[114]
        url = '/enrolment_api/programme-structure/draft/'
        # The enrolment gate intentionally hides inaccessible resources as 404.
        self.assertEqual(client.get(url).status_code, 404)
        security = client.get('/learner_api/enrollment-csrf/')
        self.assertEqual(security.status_code, 200)
        denied = client.put(url, data=json.dumps({'structure':self.structure,'baseVersion':16,'revision':1}),
                            content_type='application/json', HTTP_X_CSRFTOKEN=security.json()['csrfToken'])
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY).configuration, self.structure)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_cohort_period_save_reload_keeps_legacy_calendar_and_other_admins_in_sync(self):
        from .intake_calendar import DEFAULT_INTAKES
        from copy import deepcopy
        self.structure['intakeCalendar']=deepcopy(DEFAULT_INTAKES)
        cohort=self.structure['programmes'][0]['cohorts'][0]
        cohort.update(startDate='2027-02-01',assignmentPeriod={'startMonth':10,'startDay':15,'endMonth':2,'endDay':15})
        client=self.clients[114]
        token=client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        response=client.put('/enrolment_api/programme-structure/draft/',data=json.dumps({'structure':self.structure,'baseVersion':16,'revision':1}),content_type='application/json',HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code,200,response.content)
        restored=self.clients[108].get('/enrolment_api/programme-structure/draft/').json()['structure']
        self.assertEqual(restored['programmes'][0]['cohorts'][0]['assignmentPeriod'],cohort['assignmentPeriod'])
        self.assertEqual(restored['intakeCalendar'],DEFAULT_INTAKES)

    @patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1', 'API_REQUIRE_AUTH': '1'})
    def test_rebased_shared_draft_reloads_for_both_authenticated_admins_and_conflicts_stay_409(self):
        import tempfile
        from copy import deepcopy
        from .test_draft_rebase import published_fixtures
        from .draft_rebase import rebase_shared_draft
        base={'programmeStructure':deepcopy(self.structure)}
        current=deepcopy(base)
        current['programmeStructure']['programmes'][0]['epao']='Published EPAO'
        published_fixtures(base,current)
        with tempfile.TemporaryDirectory() as directory:
            rebase_shared_draft(expected_base=16,expected_revision=1,expected_published=20,backup_dir=directory,apply=True)
        url='/enrolment_api/programme-structure/draft/'
        first=self.clients[108].get(url); second=self.clients[114].get(url)
        self.assertEqual((first.status_code,second.status_code),(200,200))
        self.assertEqual(first.json(),second.json())
        loaded=first.json()
        self.assertEqual((loaded['baseVersion'],loaded['revision']),(20,2))
        self.assertEqual(loaded['structure']['programmes'][0]['cohorts'][0]['id'],'REAL-FEB')
        self.assertEqual(loaded['structure']['programmes'][0]['epao'],'Published EPAO')
        client=self.clients[114]
        csrf=client.get('/learner_api/enrollment-csrf/').json()['csrfToken']
        stale=client.put(url,data=json.dumps({'structure':self.structure,'baseVersion':16,'revision':1}),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(stale.status_code,409)
        changed=deepcopy(loaded['structure']); changed['programmes'][0]['description']='Shared edit'
        saved=client.put(url,data=json.dumps({'structure':changed,'baseVersion':20,'revision':2}),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(saved.status_code,200,saved.content)
        self.assertEqual(self.clients[108].get(url).json()['structure'],saved.json()['structure'])
