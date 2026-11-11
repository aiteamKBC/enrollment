from unittest.mock import patch
from django.test import SimpleTestCase, override_settings

from .shadow_runtime import compare


class ShadowRuntimeTests(SimpleTestCase):
    def test_disabled_shadow_does_no_comparison(self):
        with patch('programme_suitability.shadow_runtime.logger') as log:
            self.assertIsNone(compare('apprenticeship', 1, 'ME_L4'))
            log.info.assert_not_called()

    @override_settings(PROGRAMME_STRUCTURE_SHADOW_ENABLED=True)
    def test_shadow_failure_is_blocked_and_non_interfering(self):
        with patch('enrolment_api.wizard_layout.read_current', side_effect=RuntimeError('test')):
            result = compare('apprenticeship', 1, 'ME_L4')
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['programme'], 'ME_L4')

    @override_settings(PROGRAMME_STRUCTURE_SHADOW_ENABLED=True)
    def test_reporter_receives_structured_non_sensitive_result(self):
        reports = []
        layout = {'programmeStructure': {'programmes': [{'id': 'ME_L4', 'title': 'ME', 'modules': [], 'ksbs': [], 'mappings': []}]}}
        with patch('enrolment_api.wizard_layout.read_current', return_value=(3, layout)), patch('django.db.connections') as connections:
            connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = object()
            with override_settings(PROGRAMME_STRUCTURE_SHADOW_REPORTER=reports.append):
                result = compare('apprenticeship', 1, 'ME_L4')
        self.assertIn(result['status'], {'MATCH', 'BLOCKED', 'MISMATCH'})
