"""Future-assignment consumer checks; protected publications are mock fixtures."""
import copy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .future_cohort import confirmation_local_date, resolve_future_cohort
from .home_cohort import cohort_label_for_board
from .programme_assignment import automatic_cohort_window


class FutureCohortTests(SimpleTestCase):
    def setUp(self):
        self.row = SimpleNamespace(learner_id=2001, learner_kind='apprenticeship',
                                   selected_programme='ME_L4', modules_configuration_version=31)
        self.layout = {'programmeStructure': {'programmes': [{
            'id': 'ME_L4', 'curriculumProgrammeId': 'PROG-ME-L4', 'cohorts': [{
                'id': 'actual-october', 'programmeId': 'ME_L4', 'name': 'Real October', 'status': 'planned',
                'assignmentPeriod': {'year': 2027, 'startMonth': 6, 'startDay': 16, 'endMonth': 10, 'endDay': 15},
            }],
        }]}}
        self.pin = patch('programme_suitability.future_cohort.learner_published_layout',
                         return_value=(31, self.layout, 'layout-pin')).start()
        self.current = patch('programme_suitability.future_cohort.read_current', return_value=(32, self.layout)).start()
        patch('programme_suitability.future_cohort.connections', {'enrolment': MagicMock()}).start()
        self.resolve = patch('programme_suitability.programme_assignment.resolve_automatic_cohort',
                             return_value=('actual-october', 'Real October', False)).start()
        self.addCleanup(patch.stopall)

    def test_default_assignment_boundaries_include_october_fifteenth(self):
        cases = [(date(2027, 2, 15), ('Feb', 2027)), (date(2027, 2, 16), ('Jun', 2027)),
                 (date(2027, 6, 15), ('Jun', 2027)), (date(2027, 6, 16), ('Oct', 2027)),
                 (date(2027, 10, 15), ('Oct', 2027)), (date(2027, 10, 16), ('Feb', 2028)),
                 (date(2027, 11, 1), ('Feb', 2028)), (date(2027, 12, 31), ('Feb', 2028))]
        for day, expected in cases:
            with self.subTest(day=day):
                self.assertEqual(automatic_cohort_window(day), expected)

    @override_settings(TIME_ZONE='UTC')
    def test_uk_date_is_explicit_and_selection_timestamp_wins(self):
        selected = datetime(2027, 6, 15, 23, 30, tzinfo=timezone.utc)
        submitted = datetime(2027, 6, 14, 12, tzinfo=timezone.utc)
        self.assertEqual(confirmation_local_date(selected, submitted), date(2027, 6, 16))
        self.assertEqual(confirmation_local_date(None, selected), date(2027, 6, 16))
        self.assertEqual(confirmation_local_date(datetime(2027, 10, 15, 23, 30, tzinfo=timezone.utc)), date(2027, 10, 16))
        with self.assertRaises(ValueError):
            confirmation_local_date(datetime(2027, 10, 15))

    def test_valid_published_and_pinned_assignment_uses_existing_resolver(self):
        before = copy.deepcopy(self.layout)
        result = resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15))
        self.assertEqual(result, ('actual-october', 'Real October', False))
        self.resolve.assert_called_once_with('default', 'PROG-ME-L4', date(2027, 10, 15), None)
        self.pin.assert_called_once_with('apprenticeship', 2001, module_version=31)
        self.assertEqual(self.layout, before)

    def test_assignment_metadata_uses_published_name_and_exact_publication(self):
        self.resolve.return_value = ('actual-october', 'Unpublished catalogue rename', False)
        self.assertEqual(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 7, 1),
                                              include_publication=True), {
            'cohortId': 'actual-october', 'name': 'Real October', 'publicationVersion': 31, 'fallback': False})

    def test_previous_cohort_fallback_cannot_substitute_for_the_real_target_intake(self):
        self.resolve.return_value = ('actual-october', 'Real October', True)
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 12, 1)))

    def test_containing_window_for_wrong_intake_still_blocks_assignment(self):
        self.layout['programmeStructure']['programmes'][0]['cohorts'][0]['assignmentPeriod'].update(
            startMonth=2, startDay=16)
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 3, 1)))

    def test_missing_current_or_pinned_canonical_structure_cannot_use_legacy_names(self):
        for latest, pinned in [((32, {}), (31, self.layout, 'layout-pin')),
                               ((32, self.layout), (12, {}, 'layout-pin'))]:
            self.current.return_value = latest; self.pin.return_value = pinned
            self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))
        self.resolve.assert_not_called()

    def test_old_october_period_blocks_without_rewriting_it(self):
        self.layout['programmeStructure']['programmes'][0]['cohorts'][0]['assignmentPeriod']['endDay'] = 14
        original = copy.deepcopy(self.layout)
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))
        self.assertEqual(self.layout, original)

    def test_conflicting_period_blocks_even_away_from_cutoff_date(self):
        self.layout['programmeStructure']['programmes'][0]['cohorts'][0]['assignmentPeriod']['endDay'] = 14
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 7, 1)))

    def test_each_cutoff_resolves_only_the_real_matching_published_window(self):
        cases = [(date(2027, 2, 15), 2027, 10, 16, 2, 15),
                 (date(2027, 2, 16), 2027, 2, 16, 6, 15),
                 (date(2027, 6, 15), 2027, 2, 16, 6, 15),
                 (date(2027, 6, 16), 2027, 6, 16, 10, 15),
                 (date(2027, 10, 15), 2027, 6, 16, 10, 15),
                 (date(2027, 10, 16), 2028, 10, 16, 2, 15),
                 (date(2027, 12, 31), 2028, 10, 16, 2, 15)]
        for day, year, sm, sd, em, ed in cases:
            with self.subTest(day=day):
                self.layout['programmeStructure']['programmes'][0]['cohorts'][0]['assignmentPeriod'] = {
                    'year': year, 'startMonth': sm, 'startDay': sd, 'endMonth': em, 'endDay': ed}
                self.assertEqual(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', day),
                                 ('actual-october', 'Real October', False))

    def test_conflicting_explicit_calendar_is_not_reinterpreted(self):
        from .intake_calendar import DEFAULT_INTAKES
        self.layout['intakeCalendar'] = copy.deepcopy(DEFAULT_INTAKES)
        original = copy.deepcopy(self.layout)
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))
        self.resolve.assert_not_called()
        self.assertEqual(self.layout, original)

    def test_invalid_explicit_calendar_does_not_fall_back_to_generated_defaults(self):
        self.layout['intakeCalendar'] = []
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 7, 1)))
        self.resolve.assert_not_called()

    def test_wrong_programme_missing_pin_or_missing_cohort_stays_pending(self):
        self.row.selected_programme = 'MM_L6'
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))
        self.row.selected_programme = 'ME_L4'
        self.pin.side_effect = LookupError('Unavailable historical pin')
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))
        self.pin.side_effect = None
        self.resolve.return_value = ('different-cohort', 'Same display name', False)
        self.assertIsNone(resolve_future_cohort(self.row, 'default', 'PROG-ME-L4', date(2027, 10, 15)))

    @override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=False)
    def test_disabled_board_lookup_preserves_legacy_label_without_reading_profiles(self):
        source = SimpleNamespace(pk=1601, learner_type='apprenticeship')
        with patch('programme_suitability.home_cohort.read_home_cohort') as lookup:
            self.assertEqual(cohort_label_for_board(source, 'October 2026'), 'October 2026')
        lookup.assert_not_called()

    @override_settings(PROGRAMME_STRUCTURE_COHORT_LEARNER_ENABLED=True)
    def test_old_pending_learner_keeps_existing_board_text_when_lookup_is_enabled(self):
        source = SimpleNamespace(pk=1601, learner_type='apprenticeship')
        with patch('programme_suitability.models.Assessment.objects') as manager, \
             patch('programme_suitability.home_cohort.read_home_cohort',
                   return_value={'status': 'pending', 'name': None}) as lookup:
            manager.filter.return_value.only.return_value.first.return_value = self.row
            self.assertEqual(cohort_label_for_board(source, 'October 2026'), 'October 2026')
        lookup.assert_called_once()
