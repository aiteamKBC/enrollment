"""Pure cohort display/identity checks: no environment loading or database I/O."""
import copy
import unittest
from datetime import date

from .home_cohort import resolve_home_cohort
from .intake_calendar import resolve_intake
from .programme_identity import CURRICULUM_PROGRAMME_IDS, find_structure_programme
from .programme_structure import resolve_assigned_cohort


class HomeCohortTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'programmes': [
            {'id': 'ME_L4', 'curriculumProgrammeId': 'PROG-ME-L4', 'cohorts': [
                {'id': 'actual-cohort', 'programmeId': 'ME_L4', 'name': 'February 2027 - Group A'}]},
            {'id': 'MM_L6', 'curriculumProgrammeId': 'PROG-MM-L6', 'cohorts': [
                {'id': 'other-cohort', 'programmeId': 'MM_L6', 'name': 'Other home cohort'}]},
        ]}
        self.layout = {'programmeStructure': self.snapshot}

    def resolve(self, **changes):
        args = dict(programme_id='ME_L4', confirmed_at='confirmed',
                    persisted_programme_id='PROG-ME-L4', cohort_id='actual-cohort',
                    layout=self.layout, layout_version=11)
        args.update(changes)
        return resolve_home_cohort(**args)

    def test_confirmed_programme_assignment_resolves_verbatim_without_changes(self):
        original = copy.deepcopy(self.layout)
        result = self.resolve()
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['name'], 'February 2027 - Group A')
        self.assertEqual(result['programmeId'], 'ME_L4')
        self.assertEqual(result['cohortId'], 'actual-cohort')
        self.assertEqual(self.layout, original)
        self.assertEqual(self.resolve(persisted_programme_id='ME_L4'), result)

    def test_each_verified_id_translates_without_name_matching(self):
        for canonical, curriculum in CURRICULUM_PROGRAMME_IDS.items():
            snapshot = {'programmes': [{'id': canonical, 'title': 'Renamed display label'}]}
            self.assertEqual(find_structure_programme(snapshot, curriculum)['id'], canonical)
            self.assertEqual(find_structure_programme(snapshot, canonical)['id'], canonical)
            self.assertIsNone(find_structure_programme(snapshot, 'Renamed display label'))

    def test_conflicting_or_ambiguous_bindings_do_not_resolve(self):
        self.snapshot['programmes'][0]['curriculumProgrammeId'] = 'WRONG'
        self.assertEqual(self.resolve()['status'], 'pending')
        self.snapshot['programmes'][0]['curriculumProgrammeId'] = 'PROG-ME-L4'
        self.snapshot['programmes'].append(copy.deepcopy(self.snapshot['programmes'][0]))
        self.assertEqual(self.resolve()['status'], 'pending')

    def test_no_display_for_different_persisted_programme_or_unconfirmed_selection(self):
        self.assertEqual(self.resolve(persisted_programme_id='PROG-MM-L6')['status'], 'pending')
        self.assertEqual(self.resolve(confirmed_at=None)['reason'], 'programme_not_confirmed')

    def test_missing_assignment_does_not_infer_a_cohort_from_a_publication(self):
        for changes in [dict(cohort_id=None), dict(persisted_programme_id=None)]:
            result = self.resolve(**changes)
            self.assertEqual(result['reason'], 'assignment_missing')
            self.assertIsNone(result['name'])

    def test_legacy_layout_and_missing_snapshot_never_supply_a_generated_february(self):
        for layout in [None, {}, {'programmeModules': {'programmes': []}}]:
            result = self.resolve(layout=layout)
            self.assertEqual(result['reason'], 'canonical_publication_missing')
            self.assertIsNone(result['name'])

    def test_unknown_duplicate_or_wrong_programme_cohort_is_pending(self):
        self.assertEqual(self.resolve(cohort_id='inferred-feb-2027')['status'], 'pending')
        cohort = self.snapshot['programmes'][0]['cohorts'][0]
        cohort['programmeId'] = 'MM_L6'
        self.assertEqual(self.resolve()['status'], 'pending')
        cohort['programmeId'] = 'ME_L4'
        self.snapshot['programmes'][0]['cohorts'].append(copy.deepcopy(cohort))
        self.assertIsNone(resolve_assigned_cohort(self.snapshot, 'PROG-ME-L4', 'actual-cohort'))

    def test_historical_versions_and_completed_cohorts_keep_saved_labels(self):
        self.snapshot['programmes'][0]['cohorts'][0]['status'] = 'completed'
        for version in [7, 11, 16, 20]:
            result = self.resolve(layout_version=version)
            self.assertEqual(result['status'], 'resolved')
            self.assertEqual(result['layoutVersion'], version)
            self.assertEqual(result['name'], 'February 2027 - Group A')


class ExistingIntakeBoundaryTests(unittest.TestCase):
    def test_existing_fifteenth_boundaries_and_december_next_february(self):
        for day, expected in [
            (date(2027, 2, 15), ('Feb', 2027)), (date(2027, 2, 16), ('Jun', 2027)),
            (date(2027, 6, 15), ('Jun', 2027)), (date(2027, 6, 16), ('Oct', 2027)),
            (date(2027, 10, 14), ('Oct', 2027)), (date(2027, 10, 15), ('Feb', 2028)),
            (date(2027, 12, 1), ('Feb', 2028)), (date(2027, 12, 31), ('Feb', 2028)),
        ]:
            with self.subTest(day=day):
                self.assertEqual(resolve_intake(day), expected)
