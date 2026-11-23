"""Focused ILR working-hours mapping tests; no database connection required."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from learner_api import ilr_document as ilr, training_plan_document as training

from .registry import seed, seeds


def learner():
    return SimpleNamespace(
        pk=1, username='Test Learner', date_of_birth='', legal_sex='',
        address='', address_line_1='', address_line_2='', address_line_3='', address_line_4='',
        phone_number='', email='', current_postcode='', national_insurance_number='',
        employer='', line_manager='', programme='', cohort='', group='',
        learner_start_date='', learner_end_date='',
        reference_number='SAMPLE001',
    )


class IlrWorkingHoursTests(SimpleTestCase):
    def test_active_ilr_template_uses_working_hours_and_retires_obsolete_picker_fields(self):
        content = json.dumps(seed('ilr'))
        fields = seeds()['fields']['ilr']
        self.assertIn('{{document.learnerDetails.workingHoursPerWeek}}', content)
        self.assertNotIn('{{document.learnerDetails.contractedHoursPerWeek}}', content)
        self.assertNotIn('{{document.learnerDetails.isSmallEmployer}}', content)
        self.assertEqual(fields['document.learnerDetails.workingHoursPerWeek']['label'], 'Working hours per week')
        self.assertNotIn('document.learnerDetails.contractedHoursPerWeek', fields)
        self.assertNotIn('document.learnerDetails.isSmallEmployer', fields)

    def test_ilr_and_training_plan_use_the_same_saved_working_hours_value(self):
        person = learner()
        for saved, expected in ((Decimal('55.00'), '55.00'), (Decimal('0.00'), '0.00'), (None, '')):
            with self.subTest(saved=saved):
                details = SimpleNamespace(
                    working_hours_per_week=saved,
                    at_address_since_birth=False,
                    years_at_address=None,
                )
                with patch.object(ilr, '_wizard_personal', return_value=None), \
                     patch.object(ilr, '_rpl_detail', return_value=None), \
                     patch.object(ilr, '_wizard_ilr_details', return_value=details), \
                     patch.object(ilr, '_wizard_plr', return_value=None), \
                     patch.object(ilr, '_extended_ilr', return_value=None):
                    ilr_details = ilr.derive_learner_details(person)
                with patch('enrolment_api.document_templates.learner_sources.document_dates', return_value=(None, None, 'learner-specific')), \
                     patch('enrolment_api.document_templates.learner_sources.employment_details', return_value=details), \
                     patch('enrolment_api.document_templates.learner_sources.programme_level', return_value=''), \
                     patch.object(training, '_extended_ilr_answers', return_value={}), \
                     patch.object(training, '_plan_modules', return_value=[]), \
                     patch.object(training, '_employer_address', return_value=''):
                    training_details = training.derive_content(person)['employment']
                self.assertEqual(ilr_details['workingHoursPerWeek'], expected)
                self.assertEqual(training_details['workingHoursPerWeek'], expected)
                # Legacy snapshots retain these keys without being reinterpreted.
                self.assertEqual(ilr_details['contractedHoursPerWeek'], '')
                self.assertIsNone(ilr_details['isSmallEmployer'])

    def test_ilr_uses_current_staff_status_snapshot_and_provider_reference(self):
        person = learner()
        details = SimpleNamespace(
            working_hours_per_week=Decimal('37.50'), at_address_since_birth=False,
            years_at_address=None, employment_status='Old status', employment_start_date=None,
        )
        history = [
            {'employmentStatus': 'In paid employment', 'dateStatusApplies': '2026-01-01',
             'agreementIdentifier': 'AGR123', 'employerIdentifier': '123456789'},
            {'employmentStatus': 'Future status', 'dateStatusApplies': '2999-01-01',
             'agreementIdentifier': 'FUTURE', 'employerIdentifier': '999999999'},
        ]
        extended = SimpleNamespace(wizard_draft={'ilrEmploymentStatusHistory': history})
        with patch.object(ilr, '_wizard_personal', return_value=None), \
             patch.object(ilr, '_rpl_detail', return_value=None), \
             patch.object(ilr, '_wizard_ilr_details', return_value=details), \
             patch.object(ilr, '_wizard_plr', return_value=None), \
             patch.object(ilr, '_extended_ilr', return_value=extended):
            actual = ilr.derive_learner_details(person)
        self.assertEqual(actual['learnerReferenceNumber'], 'SAMPLE001')
        self.assertEqual(actual['employmentStatus'], 'In paid employment')
        self.assertEqual(actual['dateStatusApplies'], '2026-01-01')
        self.assertEqual(actual['agreementIdentifier'], 'AGR123')
        self.assertEqual(actual['edrsErn'], '123456789')
