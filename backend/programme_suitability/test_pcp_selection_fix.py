import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from enrolment_api.models import ExtendedIlr
from .configuration import ConfigurationError, defaults as assessment_defaults
from .models import Assessment
from .modules_configuration import defaults, legacy_defaults, validate
from .modules_service import pinned_configuration
from . import modules_views
from .management.commands.repair_pcp_rules import repaired_configuration


class PublishedPcpSelectionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory(); self.version = 7; self.config = validate(defaults())
        self.row = Assessment.objects.create(learner_id=41, learner_kind='apprenticeship', configuration_version=3,
            configuration=assessment_defaults(), pathway='project-controls', selected_programme='PCP_L6',
            confirmed_at=timezone.now(), selection_confirmed_at=timezone.now())
        self.draft = ExtendedIlr.objects.using('default').create(learner_kind='apprenticeship', learner_id=41,
            answers={'signed': 'existing'}, wizard_draft={'cvJob': {'experienceText': 'Keep my CV'}})
        for mock in [patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}),
                     patch('programme_suitability.modules_service.published_modules', side_effect=lambda: (self.version, copy.deepcopy(self.config))),
                     patch.object(modules_views, 'learner_record', return_value=SimpleNamespace(pk=41))]:
            mock.start(); self.addCleanup(mock.stop)

    def request(self, data=None, owner=41):
        request = self.factory.get('/') if data is None else self.factory.put('/', json.dumps(data), content_type='application/json')
        request.login_account = SimpleNamespace(pk=99, role='learner', subject_id=owner, subject_type='learner', is_active=True)
        request._dont_enforce_csrf_checks = True
        return modules_views.learner_modules(request, kind='apprenticeship', learner_id=41)

    def choice(self, optional=None):
        return {'programme': 'PCP_L6', 'pathway': 'operational', 'configurationVersion': self.version, 'expectedRevision': 0,
                'modulesByPathway': {'operational': ['planning-control', 'pmi-sp'] if optional is None else optional}}

    def test_strict_save_adds_mandatory_ids_from_server_and_keeps_other_wizard_data(self):
        response = self.request({**self.choice(), 'isMandatory': False, 'credits': 999, 'requiredOptionalModulesCount': 99})
        self.assertEqual(response.status_code, 200, response.content)
        saved = json.loads(response.content)['selection']
        self.assertEqual(set(saved['modulesByPathway']['operational']), {'planning-control', 'pmi-sp', 'pmp', 'ai-project-control', 'risk-apm'})
        self.draft.refresh_from_db(using='default')
        self.row.refresh_from_db()
        self.assertEqual(self.row.modules_selection, saved)
        self.assertNotIn('programmeModules', self.draft.wizard_draft)
        self.assertEqual(self.draft.wizard_draft['cvJob']['experienceText'], 'Keep my CV')
        self.assertEqual(self.draft.answers, {'signed': 'existing'})
        self.assertTrue(json.loads(self.request().content)['selectionComplete'])

    def test_strict_save_rejects_too_few_too_many_wrong_pathway_and_wrong_version_atomically(self):
        for choice in [self.choice(['planning-control', 'earned-value', 'pmi-sp']),
                       self.choice(['planning-control', 'pmi-pmo']), {**self.choice(), 'configurationVersion': 999}]:
            with self.subTest(choice=choice):
                response = self.request(choice)
                self.assertEqual(response.status_code, 400, response.content)
                self.draft.refresh_from_db(using='default')
                self.assertNotIn('programmeModules', self.draft.wizard_draft)
                self.row.refresh_from_db(); self.assertIsNone(self.row.modules_configuration)

    def test_incomplete_snapshot_receives_new_publication_without_rewriting_choices(self):
        original = json.loads(self.request().content)
        self.assertEqual(original['configurationVersion'], 7)
        incomplete = {'programme': 'PCP_L6', 'pathway': 'operational', 'configurationVersion': 7,
                      'modulesByPathway': {'operational': ['pmp', 'ai-project-control', 'risk-apm']}}
        self.draft.wizard_draft['programmeModules'] = incomplete; self.draft.save(using='default')
        self.version = 8
        path = self.config['programmes'][3]['pathways'][0]
        path['modules'][2]['isMandatory'] = False; path['requiredOptionalModulesCount'] = 3
        updated = json.loads(self.request().content)
        self.assertEqual(updated['configurationVersion'], 8)
        self.assertEqual(updated['configuration']['programmes'][3]['pathways'][0]['requiredOptionalModulesCount'], 3)
        self.assertFalse(updated['selectionComplete'])
        self.draft.refresh_from_db(using='default'); self.assertEqual(self.draft.wizard_draft['programmeModules'], incomplete)
        self.row.refresh_from_db(); self.assertEqual(self.row.history[-1]['fromVersion'], 7)

    def test_valid_historical_selection_keeps_its_original_published_rules(self):
        self.assertEqual(self.request(self.choice()).status_code, 200)
        self.version = 8
        self.config['programmes'][3]['pathways'][0]['modules'][2]['isMandatory'] = False
        self.config['programmes'][3]['pathways'][0]['requiredOptionalModulesCount'] = 3
        data = json.loads(self.request().content)
        self.assertEqual(data['configurationVersion'], 7)
        self.assertEqual(data['configuration']['programmes'][3]['pathways'][0]['requiredOptionalModulesCount'], 2)

    def test_submitted_legacy_enrolment_is_not_silently_rebound(self):
        self.row.modules_configuration = validate(legacy_defaults()); self.row.modules_configuration_version = 5
        self.row.save(update_fields=['modules_configuration', 'modules_configuration_version'])
        with patch('programme_suitability.modules_service._enrolment_finalized', return_value=True):
            _, version, config = pinned_configuration('apprenticeship', 41)
        self.assertEqual(version, 5); self.assertEqual(config['schemaVersion'], 1)

    def test_v1_admin_cannot_publish_mandatory_flags_without_optional_requirements(self):
        config = legacy_defaults(); config['programmes'][3]['pathways'][0]['modules'][0]['isMandatory'] = True
        with self.assertRaises(ConfigurationError): validate(config, publishing=True)

    def test_other_learner_cannot_commit_our_module_selection(self):
        self.assertEqual(self.request(self.choice(), owner=42).status_code, 404)

    def test_data_repair_only_fills_missing_rules_and_restores_hidden_mandatory_group(self):
        old = defaults(); old['schemaVersion'] = 1
        for path in old['programmes'][3]['pathways']:
            path.pop('requiredOptionalModulesCount')
        chartered = old['programmes'][3]['pathways'][2]
        chartered['targetCredits'] = None; chartered['modules'][0]['active'] = False
        original = copy.deepcopy(old)
        fixed = repaired_configuration(old, restore_mandatory_groups=True)
        self.assertEqual(old, original)
        self.assertEqual(fixed['programmes'][:3], old['programmes'][:3])
        self.assertEqual(fixed['programmes'][3]['pathways'][0]['requiredOptionalModulesCount'], 2)
        self.assertTrue(fixed['programmes'][3]['pathways'][2]['modules'][0]['active'])
        for before, after in zip(old['programmes'][3]['pathways'], fixed['programmes'][3]['pathways']):
            self.assertEqual(before['description'], after['description'])
            self.assertEqual([(m['id'], m['title'], m.get('sessions')) for m in before['modules']], [(m['id'], m['title'], m.get('sessions')) for m in after['modules']])
        self.assertEqual(repaired_configuration(fixed, restore_mandatory_groups=True), fixed)
