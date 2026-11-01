from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from enrolment_api import wizard_layout
from .completion import submission_error
from .configuration import defaults
from .wizard_step import LABEL, SLUG, with_suitability_step


class WizardStepTests(SimpleTestCase):
    def test_old_layout_is_adapted_without_changing_history_configuration_or_answers(self):
        old = {'steps': [{'slug': 'ilr', 'items': []}, {'slug': 'cv-job', 'items': []}],
               'programmeSuitability': defaults(), 'texts': {'cv.experience.label': 'Saved wording'}}
        original = deepcopy(old)
        resolved = with_suitability_step(old)
        self.assertEqual([s['slug'] for s in resolved['steps']], ['ilr', SLUG, 'cv-job'])
        self.assertEqual(resolved['steps'][1]['label'], LABEL)
        self.assertEqual(old, original)
        self.assertEqual(with_suitability_step(resolved), resolved)
        self.assertIs(resolved['programmeSuitability'], old['programmeSuitability'])
        self.assertEqual(wizard_layout._payload((7, old, None, 'Admin'))['version'], 7)

    def test_existing_position_and_custom_fields_survive_and_enabled_step_cannot_be_hidden(self):
        step = {'slug': SLUG, 'label': 'Saved title', 'hidden': True, 'items': [{'key': 'cf_saved'}]}
        layout = {'steps': [step, {'slug': 'cv-job'}], 'programmeSuitability': defaults()}
        resolved = with_suitability_step(layout)
        self.assertFalse(resolved['steps'][0]['hidden'])
        self.assertTrue(step['hidden'])
        self.assertEqual(resolved['steps'][0]['items'], step['items'])
        self.assertEqual(resolved['steps'][0]['label'], 'Saved title')

    def test_new_step_storage_is_builtin_and_only_added_custom_fields_need_ddl(self):
        raw = {'steps': [{'slug': SLUG, 'label': LABEL, 'items': []}]}
        layout, plan = wizard_layout.normalize_layout(raw, None)
        self.assertTrue(layout['steps'][0]['builtin'])
        self.assertEqual(plan, {})
        raw['steps'][0]['items'] = [{'label': 'Provider question', 'type': 'text'}]
        layout, plan = wizard_layout.normalize_layout(raw, None)
        self.assertEqual(layout['steps'][0]['items'][0]['table'], 'Wizard_Programme_Suitability')
        self.assertTrue(plan['Wizard_Programme_Suitability']['create'])

    def test_hidden_cv_cannot_bypass_the_standalone_assessment_on_final_submission(self):
        layout = {'steps': [{'slug': 'cv-job', 'hidden': True}], 'programmeSuitability': defaults()}
        learner = SimpleNamespace(pk=41, onboarding_status='In progress')
        with patch.object(wizard_layout, 'current_layout', return_value=layout), \
                patch('programme_suitability.completion.Assessment.objects') as assessments:
            assessments.filter.return_value.first.return_value = None
            self.assertIsNotNone(submission_error(learner))
            assessments.filter.return_value.first.return_value = SimpleNamespace(
                confirmed_at='submitted', selection_confirmed_at='confirmed')
            self.assertIsNone(submission_error(learner))
