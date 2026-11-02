import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from enrolment_api import wizard_layout
from .configuration import ConfigurationError, defaults as assessment_defaults
from .models import Assessment, ModuleConfigurationDraft
from .modules_configuration import KEY, defaults, legacy_defaults, integrate_layout, validate
from .modules_service import validate_choices
from . import modules_views


class ModulesConfigurationTests(SimpleTestCase):
    def test_defaults_keep_existing_ids_hierarchy_and_unset_pcp_sessions(self):
        config = validate(defaults())
        self.assertEqual(config['programmes'][0]['modules'][1]['sessions'], 14)
        pcp = config['programmes'][3]
        self.assertEqual([p['id'] for p in pcp['pathways']], ['operational', 'strategic', 'chartered'])
        self.assertEqual(len(pcp['pathways'][2]['modules'][0]['items']), 4)
        self.assertIsNone(pcp['pathways'][0]['modules'][0]['sessions'])

    def test_invalid_sessions_credits_ids_and_hierarchy_are_rejected(self):
        for field, value in [('sessions', -1), ('sessions', 1.5), ('sessions', True), ('credits', -0.1), ('credits', float('inf')), ('order', -1)]:
            with self.subTest(field=field, value=value):
                config = defaults(); config['programmes'][0]['modules'][0][field] = value
                with self.assertRaises(ConfigurationError): validate(config)
        for mutate in [
            lambda c: c['programmes'].append(copy.deepcopy(c['programmes'][0])),
            lambda c: c['programmes'][0]['modules'].append(copy.deepcopy(c['programmes'][0]['modules'][0])),
            lambda c: c['programmes'][3]['pathways'].append(copy.deepcopy(c['programmes'][3]['pathways'][0])),
            lambda c: c['programmes'][0].update(id='UNKNOWN'),
            lambda c: c['programmes'][0]['modules'][0].update(parentId='missing'),
            lambda c: c['programmes'][0]['modules'][0].update(items=[c['programmes'][0]['modules'][0]]),
        ]:
            config = defaults(); mutate(config)
            with self.assertRaises(ConfigurationError): validate(config)

    def test_new_paths_and_arbitrary_module_counts_and_fractional_credits_are_supported(self):
        config = defaults(); path = copy.deepcopy(config['programmes'][3]['pathways'][0])
        path.update(id='new-path', title='New pathway')
        config['programmes'][3]['pathways'].append(path)
        config['programmes'][0]['modules'] = [{**copy.deepcopy(config['programmes'][0]['modules'][0]), 'id': f'm-{i}', 'credits': 0.5} for i in range(220)]
        self.assertEqual(len(validate(config)['programmes'][0]['modules']), 220)
        self.assertEqual(len(validate(config)['programmes'][3]['pathways']), 4)

    def test_publication_preserves_omitted_content_as_inactive_without_mutating_previous(self):
        old = {KEY: defaults()}; original = copy.deepcopy(old)
        changed = defaults(); changed['programmes'][0]['modules'].pop()
        changed['programmes'][3]['pathways'].pop()
        layout = {}; integrate_layout({KEY: changed}, old, layout)
        self.assertFalse(layout[KEY]['programmes'][0]['modules'][-1]['active'])
        self.assertFalse(layout[KEY]['programmes'][3]['pathways'][-1]['active'])
        self.assertEqual(old, original)
        clean, _ = wizard_layout.normalize_layout({'steps': [{'slug': 'programme-suitability', 'items': []}], KEY: changed}, old)
        self.assertIn(KEY, clean)
        self.assertEqual(wizard_layout._payload((7, old, None, 'Admin'))['version'], 7)


class ModulesVersionAndAccessTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.config = validate(legacy_defaults())
        now = timezone.now()
        self.row = Assessment.objects.create(learner_id=41, learner_kind='apprenticeship', configuration_version=3,
            configuration=assessment_defaults(), pathway='project-controls', selected_programme='PCP_L6', confirmed_at=now,
            selection_confirmed_at=now)
        for p in [patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}),
                  patch('enrolment_api.auth._accesses_of', return_value={'enrolment'}),
                  patch('programme_suitability.modules_service.saved_choices', return_value=None),
                  patch('programme_suitability.modules_views.saved_choices', return_value=None),
                  patch('programme_suitability.modules_service.published_modules', side_effect=lambda: (7, copy.deepcopy(self.config))),
                  patch('programme_suitability.modules_views.published_modules', return_value=(7, self.config)),
                  patch.object(modules_views, 'learner_record', return_value=SimpleNamespace(pk=41))]:
            p.start(); self.addCleanup(p.stop)

    def request(self, endpoint=modules_views.learner_modules, data=None, method='GET', role='learner', owner=41, actor=99):
        request = self.factory.generic(method, '/', data=json.dumps(data) if data is not None else '', content_type='application/json')
        request.login_account = SimpleNamespace(pk=actor, is_active=True, role=role, subject_id=owner, subject_type=role)
        request._dont_enforce_csrf_checks = True
        kwargs = {'kind': 'apprenticeship', 'learner_id': 41} if endpoint is modules_views.learner_modules else {}
        return endpoint(request, **kwargs)

    def choices(self, **changes):
        return {'programme': 'PCP_L6', 'pathway': 'operational', 'modulesByPathway': {'operational': ['pmp']}, 'configurationVersion': 7, **changes}

    def test_snapshot_is_pinned_and_draft_or_later_publication_cannot_rename_historical_content(self):
        first = self.request(); self.assertEqual(first.status_code, 200)
        original = json.loads(first.content)
        self.config['programmes'][3]['pathways'][0]['modules'][0]['title'] = 'New published title'
        second = self.request()
        self.assertEqual(json.loads(second.content), original)
        self.row.refresh_from_db(); self.assertEqual(self.row.configuration_version, 3)
        self.assertEqual(self.row.modules_configuration_version, 7)
        self.assertEqual(self.row.selected_programme, 'PCP_L6')

    def test_new_enrolments_see_latest_published_content_and_confirmed_alternative(self):
        self.config['programmes'][0]['modules'][0]['title'] = 'New ME module'
        self.row.selected_programme = 'ME_L4'; self.row.save(update_fields=['selected_programme'])
        data = json.loads(self.request().content)
        self.assertEqual(data['selectedProgramme'], 'ME_L4')
        self.assertEqual(data['configuration']['programmes'][0]['modules'][0]['title'], 'New ME module')

    def test_legacy_choices_pin_original_defaults_even_if_admin_has_renamed_them(self):
        self.config['programmes'][3]['pathways'][0]['modules'][0]['title'] = 'Renamed'
        with patch('programme_suitability.modules_service._enrolment_finalized', return_value=True), patch('programme_suitability.modules_service.saved_choices', return_value={'programme': 'PCP_L6', 'pathway': 'operational', 'modulesByPathway': {'operational': ['pmp']}}):
            data = json.loads(self.request().content)
        self.assertEqual(data['configurationVersion'], 0)
        self.assertEqual(data['configuration']['programmes'][3]['pathways'][0]['modules'][0]['title'], 'Project Management Professional (PMP)')

    def test_unconfirmed_assessment_cannot_get_a_modules_snapshot(self):
        self.row.selection_confirmed_at = None; self.row.save(update_fields=['selection_confirmed_at'])
        self.assertEqual(self.request().status_code, 400)
        self.row.refresh_from_db(); self.assertIsNone(self.row.modules_configuration)

    def test_choices_validate_version_programme_pathway_modules_and_selection_settings(self):
        valid = validate_choices('apprenticeship', 41, self.choices())
        self.assertEqual(valid['configurationVersion'], 7)
        for changed in [self.choices(configurationVersion=999), self.choices(configurationVersion=True),
                        self.choices(pathway='unknown'), self.choices(programme='APM_L4'),
                        self.choices(modulesByPathway={'wrong-path': ['pmp']}),
                        self.choices(modulesByPathway={'operational': ['pmp', 'pmp']}),
                        self.choices(modulesByPathway={'operational': ['unknown']}),
                        {k: v for k, v in self.choices().items() if k != 'configurationVersion'}]:
            with self.subTest(changed=changed), self.assertRaises(ConfigurationError): validate_choices('apprenticeship', 41, changed)
        self.row.refresh_from_db()
        self.row.modules_configuration['programmes'][3]['pathways'][0]['allowModuleSelection'] = False
        self.row.save(update_fields=['modules_configuration'])
        with self.assertRaises(ConfigurationError): validate_choices('apprenticeship', 41, self.choices())

    def test_other_learners_and_employers_cannot_read_choices_or_write_configuration(self):
        self.assertEqual(self.request(owner=42).status_code, 404)
        self.assertIn(self.request(role='employer').status_code, (403, 404))
        self.assertEqual(self.request(method='PUT', data={}).status_code, 400)
        self.assertIn(self.request(modules_views.modules_draft, data={'configuration': self.config}, method='PUT').status_code, (403, 404))
        self.assertIn(self.request(modules_views.modules_defaults).status_code, (403, 404))

    def test_modules_drafts_are_private_and_do_not_publish_or_change_learner_snapshots(self):
        original = copy.deepcopy(self.config)
        draft = copy.deepcopy(self.config); draft['programmes'][0]['modules'][0]['sessions'] = 99
        saved = self.request(modules_views.modules_draft, data={'configuration': draft, 'baseVersion': 7}, method='PUT', role='staff')
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(ModuleConfigurationDraft.objects.count(), 1)
        own = self.request(modules_views.modules_draft, role='staff')
        self.assertEqual(json.loads(own.content)['configuration']['programmes'][0]['modules'][0]['sessions'], 99)
        other = self.request(modules_views.modules_draft, role='staff', actor=100)
        self.assertIsNone(json.loads(other.content)['configuration'])
        self.assertEqual(json.loads(self.request().content)['configuration'], original)
        stale = self.request(modules_views.modules_draft, data={'configuration': draft, 'baseVersion': 6}, method='PUT', role='staff')
        self.assertEqual(stale.status_code, 409)

    def test_only_admissions_staff_can_publish_module_configuration(self):
        request = self.factory.put('/', json.dumps({'layout': {'steps': [], KEY: self.config}}), content_type='application/json')
        request.login_account = SimpleNamespace(role='learner')
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}):
            self.assertEqual(wizard_layout.publish_wizard_layout(request).status_code, 403)
