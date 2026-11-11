import copy
import json
from types import SimpleNamespace
from django.test import RequestFactory, TestCase
from enrolment_api.programme_structure_views import programme_structure_draft, SHARED_DRAFT_KEY
from programme_suitability.models import ModuleConfigurationDraft

class SharedStructureDraftTests(TestCase):
    """Real isolated ORM persistence and view permissions; no session middleware mocking."""
    def setUp(self):
        self.structure = {'schemaVersion': 2, '_draftRevision': 1, 'programmes': [{
            'id': 'ME_L4', 'title': 'Marketing Executive', 'modules': [], 'ksbs': [], 'mappings': [],
            'cohorts': [{'id': 'COHORT-20260917092025244736D2820D958B44', 'programmeId': 'ME_L4',
                         'name': 'Feb 2027', 'modules': [], 'mappings': []}]}]}
        ModuleConfigurationDraft.objects.create(actor_key=SHARED_DRAFT_KEY,base_version=16,configuration=self.structure)
        ModuleConfigurationDraft.objects.create(actor_key='platform:108',base_version=8,configuration={'programmes': []})

    def call(self, actor=108, method='get', data=None, role='admin'):
        request = getattr(RequestFactory(), method)('/enrolment_api/programme-structure/draft/',
            data=json.dumps(data or {}) if method!='get' else None, content_type='application/json')
        request.login_account=SimpleNamespace(pk=actor,role=role)
        # Exercise the view's real role guard and ORM; auth/CSRF middleware are tested separately.
        view=programme_structure_draft
        while hasattr(view, '__wrapped__'):
            view=view.__wrapped__
        return view(request)

    def test_admin_108_and_114_load_exactly_the_same_persisted_cohorts(self):
        first=self.call(108); second=self.call(114)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.content,second.content)
        self.assertEqual(json.loads(first.content)['structure'],self.structure)
        self.assertEqual(ModuleConfigurationDraft.objects.count(),2)

    def test_save_from_admin_114_reload_as_108_and_reject_stale_108_write(self):
        changed=copy.deepcopy(self.structure); changed['programmes'][0]['title']='Updated name'
        response=self.call(114,'put',{'structure':changed,'baseVersion':16,'revision':1})
        self.assertEqual(response.status_code,200)
        self.assertEqual(json.loads(response.content)['revision'],2)
        loaded=json.loads(self.call(108).content)
        self.assertEqual(loaded['structure']['programmes'][0]['title'],'Updated name')
        stale=self.call(108,'put',{'structure':self.structure,'baseVersion':16,'revision':1})
        self.assertEqual(stale.status_code,409)
        self.assertEqual(self.call(114).content,self.call(108).content)
        self.assertEqual(ModuleConfigurationDraft.objects.get(pk='platform:108').base_version,8)

    def test_wrong_base_cannot_overwrite_shared_draft(self):
        response=self.call(114,'put',{'structure':self.structure,'baseVersion':8,'revision':1})
        self.assertEqual(response.status_code,409)
        self.assertEqual(ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY).configuration,self.structure)

    def test_learner_cannot_read_or_save_shared_configuration(self):
        self.assertEqual(self.call(role='learner').status_code,403)
        self.assertEqual(self.call(114,'put',{'structure':self.structure,'baseVersion':16,'revision':1},role='learner').status_code,403)

    def test_invalid_save_is_read_only_and_delete_requires_current_revision(self):
        self.assertEqual(self.call(114,'put',{'structure':{},'revision':1,'baseVersion':16}).status_code,400)
        self.assertEqual(self.call(114,'delete',{'revision':0}).status_code,409)
        self.assertEqual(ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY).configuration,self.structure)

    def test_promote_reviewed_setup_in_place_without_copy_or_configuration_changes(self):
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        row=ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        ModuleConfigurationDraft.objects.filter(pk=SHARED_DRAFT_KEY).update(actor_key='programme-structure:platform:108')
        with tempfile.TemporaryDirectory() as directory:
            call_command('share_programme_structure_draft',source='programme-structure:platform:108',base_version=16,revision=1,backup=str(Path(directory)/'backup.json'),apply=True,verbosity=0)
            self.assertTrue((Path(directory)/'backup.json').exists())
        restored=ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        self.assertEqual(restored.configuration,row.configuration)
        self.assertEqual(restored.updated_at,row.updated_at)
        self.assertEqual(ModuleConfigurationDraft.objects.count(),2)
        self.assertFalse(ModuleConfigurationDraft.objects.filter(pk='programme-structure:platform:108').exists())
