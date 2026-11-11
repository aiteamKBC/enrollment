"""Real LoginSession middleware, CSRF enforcement and shared ORM persistence."""
import json
import os
from unittest.mock import patch
from django.test import Client, TestCase
from learner_api.models import StaffUser
from login.models import LoginAccount
from login.sessions import issue_session, COOKIE_NAME
from .models import ModuleConfigurationDraft
from enrolment_api.programme_structure_views import SHARED_DRAFT_KEY


class SharedDraftAuthenticationTests(TestCase):
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
