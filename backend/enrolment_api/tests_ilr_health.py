"""Pure validation tests: no external database, mail, or learner records."""
from django.test import SimpleTestCase

from .ilr_health import HEALTH_CODES, validate_ilr_additions, working_hours_value


class IlrHealthValidationTests(SimpleTestCase):
    def test_all_22_published_categories_and_a_primary_are_accepted(self):
        self.assertEqual(len(HEALTH_CODES), 22)
        validate_ilr_additions({
            'longTermDisability': True, 'healthConditionCodes': ['12', '93'],
            'primaryHealthConditionCode': '12', 'additionalSupportDetails': 'Quiet room.',
            'employmentStatus': 'In paid employment', 'workingHoursPerWeek': '37.5',
            'niNumber': 'AB 12 34 56 C', 'niApplied': None,
        }, finalise=True)
        self.assertEqual(str(working_hours_value('37.5')), '37.5')

    def test_arbitrary_duplicate_and_inconsistent_codes_are_rejected(self):
        base = {'longTermDisability': True}
        for change in (
            {'healthConditionCodes': ['999']},
            {'healthConditionCodes': ['12', '12']},
            {'healthConditionCodes': ['12'], 'primaryHealthConditionCode': '14'},
            {'healthConditionCodes': ['98', '12']},
            {'healthConditionCodes': ['98', '99']},
            {'longTermDisability': False, 'healthConditionCodes': ['12']},
            {'longTermDisability': None, 'healthConditionCodes': ['12']},
            {'longTermDisability': 'Yes'},
            {'additionalSupportDetails': 'Needs help'},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_ilr_additions({**base, **change})

    def test_nondisclosure_needs_no_primary_and_no_coded_condition(self):
        validate_ilr_additions({
            'longTermDisability': True, 'healthConditionCodes': ['98'],
            'niApplied': True,
        }, finalise=True)

    def test_finalisation_requires_condition_and_primary_when_health_is_yes(self):
        for change in ({}, {'healthConditionCodes': ['12']}):
            with self.subTest(change=change), self.assertRaisesMessage(ValueError, 'health conditions'):
                validate_ilr_additions({'longTermDisability': True, 'niApplied': True, **change}, finalise=True)

    def test_hours_are_conditional_and_bounded(self):
        for hours in ('0', '-1', '168.01', '37.555', 'NaN', 'Infinity', 'abc'):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                validate_ilr_additions({'employmentStatus': 'In paid employment', 'workingHoursPerWeek': hours})
        with self.assertRaisesMessage(ValueError, 'working hours'):
            validate_ilr_additions({'employmentStatus': 'In paid employment', 'niApplied': True}, finalise=True)
        with self.assertRaisesMessage(ValueError, 'Clear working hours'):
            validate_ilr_additions({'employmentStatus': 'Not in paid employment', 'workingHoursPerWeek': '10'})

    def test_ni_confirmation_is_only_required_when_number_absent(self):
        validate_ilr_additions({'niNumber': 'AB 12 34 56 C'}, finalise=True)
        with self.assertRaisesMessage(ValueError, 'Confirm that you have applied'):
            validate_ilr_additions({'niNumber': '', 'niApplied': None}, finalise=True)
        with self.assertRaisesMessage(ValueError, 'valid National Insurance'):
            validate_ilr_additions({'niNumber': 'invalid'}, finalise=False)
