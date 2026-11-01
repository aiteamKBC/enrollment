import copy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .configuration import ConfigurationError
from .modules_configuration import defaults, legacy_defaults, validate
from .modules_rules import available_modules, credits, validate_active_selection
from .modules_service import selection_complete, validate_snapshot_choices
from .completion import submission_error


class PcpRulesTests(SimpleTestCase):
    def setUp(self):
        self.config = validate(defaults())
        self.paths = self.config['programmes'][3]['pathways']

    def mandatory(self, path):
        return [m['id'] for m in available_modules(path['modules']) if m['isMandatory']]

    def optional(self, path):
        return [m['id'] for m in available_modules(path['modules']) if not m['isMandatory']]

    def test_defaults_have_exact_notes_mandatory_credit_totals_and_optional_counts(self):
        notes = [
            'Ideal for professionals involved in day-to-day project delivery, including planning, scheduling, risk management, and monitoring project performance.',
            'Ideal for professionals involved in programme or portfolio management, PMO leadership, governance, and strategic decision-making.',
            'Designed for professionals seeking to combine hands-on project delivery with strategic leadership, developing a broader professional skill set while supporting progression towards Chartered Project Professional (ChPP) recognition.',
        ]
        for path, note, mandatory_credits, count in zip(self.paths, notes, [4, 4, 5], [2, 2, 1]):
            with self.subTest(path=path['id']):
                self.assertEqual(path['description'], note)
                modules = available_modules(path['modules'])
                self.assertEqual(credits([m for m in modules if m['isMandatory']]), Decimal(mandatory_credits))
                self.assertEqual(path['requiredOptionalModulesCount'], count)
                self.assertEqual(path['targetCredits'], 6)
        self.assertNotIn('certified-pmo', self.mandatory(self.paths[2]))
        self.assertEqual(len(self.mandatory(self.paths[2])), 5)

    def test_each_path_allows_partial_progress_but_only_exact_complete_selections(self):
        for path in self.paths:
            with self.subTest(path=path['id']):
                ids = self.mandatory(path)
                validate_active_selection(path, ids, complete=False)
                with self.assertRaises(ConfigurationError): validate_active_selection(path, ids, complete=True)
                full = ids + self.optional(path)[:path['requiredOptionalModulesCount']]
                validate_active_selection(path, full, complete=True)
                with self.assertRaises(ConfigurationError): validate_active_selection(path, full + [self.optional(path)[-1]], complete=False)
                with self.assertRaises(ConfigurationError): validate_active_selection(path, full[1:], complete=False)

    def test_invalid_or_impossible_published_rules_are_rejected(self):
        changes = [
            lambda p: p.update(requiredOptionalModulesCount=4),
            lambda p: p.update(requiredOptionalModulesCount=-1),
            lambda p: p.update(requiredOptionalModulesCount=1.5),
            lambda p: p.update(requiredOptionalModulesCount=None),
            lambda p: p.update(targetCredits=3),
            lambda p: p.update(targetCredits=6.5),
            lambda p: p.update(targetCredits=None),
            lambda p: p['modules'][0].update(selectable=False),
            lambda p: p.update(allowModuleSelection=False),
        ]
        for change in changes:
            config = defaults(); change(config['programmes'][3]['pathways'][0])
            with self.subTest(change=change), self.assertRaises(ConfigurationError): validate(config)
        config = defaults(); config['programmes'][3]['pathways'][2]['modules'][0]['isMandatory'] = True
        with self.assertRaises(ConfigurationError): validate(config)
        config = defaults(); config['programmes'][3]['pathways'][2]['modules'][0]['credits'] = 1
        with self.assertRaises(ConfigurationError): validate(config)

    def test_admin_can_change_mandatory_flags_count_and_notes_independently_of_order(self):
        config = defaults(); path = config['programmes'][3]['pathways'][0]
        path['description'] = 'Edited note'
        path['modules'][2]['isMandatory'] = False
        path['requiredOptionalModulesCount'] = 3
        path['modules'].reverse()
        saved = validate(config)['programmes'][3]['pathways'][0]
        self.assertEqual(saved['description'], 'Edited note')
        self.assertEqual(set(self.mandatory(saved)), {'pmp', 'ai-project-control'})
        validate_active_selection(saved, self.mandatory(saved) + self.optional(saved)[:3], complete=True)

    def test_zero_optional_rule_can_have_locked_mandatory_only_modules(self):
        config = defaults(); path = config['programmes'][3]['pathways'][0]
        path.update(requiredOptionalModulesCount=0, targetCredits=4, allowModuleSelection=False)
        path = validate(config)['programmes'][3]['pathways'][0]
        validate_active_selection(path, self.mandatory(path), complete=True)

    def test_snapshot_saves_reject_tampering_and_historical_configs_are_not_upgraded(self):
        path = self.paths[0]; ids = self.mandatory(path) + self.optional(path)[:2]
        raw = {'programme': 'PCP_L6', 'pathway': path['id'], 'configurationVersion': 8,
               'modulesByPathway': {path['id']: ids}}
        validate_snapshot_choices(raw, 8, self.config, complete=True)
        for invalid in [dict(raw, configurationVersion=9), dict(raw, pathway='strategic'),
                        dict(raw, modulesByPathway={'operational': ids + ['wrong-module']})]:
            with self.assertRaises(ConfigurationError): validate_snapshot_choices(invalid, 8, self.config, complete=True)
        restored = validate_snapshot_choices(dict(raw, modulesByPathway={'operational': self.optional(path)[:2]}), 8, self.config, complete=True)
        self.assertTrue(set(self.mandatory(path)) <= set(restored['modulesByPathway']['operational']))
        legacy = validate(legacy_defaults()); original = copy.deepcopy(legacy)
        old = {'programme': 'PCP_L6', 'pathway': 'operational', 'modulesByPathway': {'operational': ['pmp']}}
        with self.assertRaises(ConfigurationError): validate_snapshot_choices(old, 0, legacy, complete=True)
        self.assertEqual(legacy, original)

    def test_final_api_guard_requires_saved_valid_choices_for_pcp_but_not_other_programmes(self):
        now = 'confirmed'
        row = SimpleNamespace(learner_id=41, learner_kind='apprenticeship', confirmed_at=now, selection_confirmed_at=now,
            selected_programme='PCP_L6', modules_configuration=self.config, modules_configuration_version=8)
        path = self.paths[0]
        saved = {'programme': 'PCP_L6', 'pathway': 'operational', 'configurationVersion': 8,
                 'modulesByPathway': {'operational': self.mandatory(path)}}
        learner = SimpleNamespace(pk=41, onboarding_status='In progress')
        with patch('enrolment_api.wizard_layout.current_layout', return_value={'programmeSuitability': {'enabled': True}}), \
             patch('programme_suitability.completion.Assessment.objects') as objects, \
             patch('programme_suitability.modules_service.saved_choices', side_effect=lambda *_: saved), \
             patch('programme_suitability.modules_service.pinned_configuration', side_effect=lambda *_: (row, 8, self.config)):
            objects.filter.return_value.first.return_value = row
            self.assertIsNotNone(submission_error(learner))
            self.assertFalse(selection_complete(row, saved))
            saved['modulesByPathway']['operational'] += self.optional(path)[:2]
            self.assertTrue(selection_complete(row, saved))
            self.assertIsNone(submission_error(learner))
            row.selected_programme = 'ME_L4'
            self.assertIsNone(submission_error(learner))
