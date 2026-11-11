"""Isolated adapter checks; the PostgreSQL append-only transaction is not mocked as evidence of runtime publishing."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings
from enrolment_api.programme_structure_views import programme_structure_publish, SHARED_DRAFT_KEY
from .models import ModuleConfigurationDraft
from .modules_configuration import defaults
from .programme_structure import from_legacy


@override_settings(PROGRAMME_STRUCTURE_PUBLISHING_ENABLED=True)
class PublicationAdapterTests(TestCase):
    def setUp(self):
        self.layout = {'steps': [], 'programmeModules': defaults(), 'skillsRadarCatalogues': {}}
        self.structure = from_legacy(self.layout['programmeModules'], {})
        self.structure['_draftRevision'] = 1
        ModuleConfigurationDraft.objects.create(actor_key=SHARED_DRAFT_KEY, base_version=16, configuration=self.structure)

    def call(self, revision=1, structure=None):
        request = RequestFactory().post('/enrolment_api/programme-structure/publish/',
            data=json.dumps({'structure': structure or self.structure, 'baseVersion': 16, 'revision': revision}),
            content_type='application/json')
        request.login_account = SimpleNamespace(pk=114, role='admin')
        view = programme_structure_publish
        while hasattr(view, '__wrapped__'):
            view = view.__wrapped__
        # This checks the adapter role gate/ORM, not login middleware.
        return view(request)

    def test_four_field_current_layout_and_saved_revision_delegate_losslessly(self):
        with patch('enrolment_api.wizard_layout.CONN', 'default'), patch('enrolment_api.wizard_layout.read_current', return_value=(16, self.layout, None, 'admin')), patch('enrolment_api.wizard_layout.publish_wizard_layout', return_value=JsonResponse({'version': 17})) as publisher:
            response = self.call()
        self.assertEqual(response.status_code, 200)
        body = json.loads(publisher.call_args.args[0].body)
        self.assertEqual(body['layout']['programmeModules'], self.layout['programmeModules'])
        self.assertEqual(body['layout']['programmeStructure'], self.structure)
        row = ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        self.assertEqual((row.base_version, row.configuration['_draftRevision']), (17, 2))

    def test_stale_revision_and_unsaved_changes_never_reach_publisher(self):
        changed = copy.deepcopy(self.structure)
        changed['programmes'][0]['title'] = 'Unsaved'
        with patch('enrolment_api.wizard_layout.CONN', 'default'), patch('enrolment_api.wizard_layout.read_current', return_value=(16, self.layout, None, 'admin')), patch('enrolment_api.wizard_layout.publish_wizard_layout') as publisher:
            self.assertEqual(self.call(revision=0).status_code, 409)
            self.assertEqual(self.call(structure=changed).status_code, 409)
        publisher.assert_not_called()

    def test_failed_official_publisher_preserves_shared_draft(self):
        with patch('enrolment_api.wizard_layout.CONN', 'default'), patch('enrolment_api.wizard_layout.read_current', return_value=(16, self.layout, None, 'admin')), patch('enrolment_api.wizard_layout.publish_wizard_layout', return_value=JsonResponse({'error': 'Concurrent publication'}, status=409)):
            self.assertEqual(self.call().status_code, 409)
        row = ModuleConfigurationDraft.objects.get(pk=SHARED_DRAFT_KEY)
        self.assertEqual((row.base_version, row.configuration), (16, self.structure))
