"""Replacement deliveries use isolated data; never connect to a real LMS."""
import copy
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase

from enrollment_service import lms_readonly
from .module_replacements import resolve_entries


class ReplacementTests(SimpleTestCase):
    def setUp(self):
        self.module = {'id': 'social-media', 'title': 'Social Media'}
        self.entry = dict(moduleId='old', lmsOfferingId='old', academicModuleId='social-media',
            assignmentSource='programme_suitability_fixed', programmeId='PROG-ME-L4',
            cohortId='home', groupId='g1', moduleTitle='Social Media', hours=103,
            startDate='2027-06-16', endDate='2027-09-21', weeks=[])
        self.reference = dict(module_catalogue_id='old', programme_id='PROG-ME-L4',
            cohort_id='home', group_id='g1', deleted_at='2026-11-18')
        self.offer = dict(module_catalogue_id='new', programme_id='PROG-ME-L4', cohort_id='home',
            group_id='g1', cohort_name='Feb 2027', group_name='Group 1', cohort_status='planned',
            title='Social Media', start_date='2027-06-17', end_date='2027-09-22',
            total_otjh=Decimal('104.75'), sessions_number=14, weeks_number=14)
        self.refs = patch.object(lms_readonly, 'get_saved_module_references', return_value=[self.reference]).start()
        self.offers = patch.object(lms_readonly, 'get_replacement_offerings', return_value=[self.offer]).start()
        self.addCleanup(patch.stopall)

    def resolve(self, entries=None, modules=None):
        return resolve_entries(entries or [self.entry], 'ME_L4', modules or [self.module])

    def test_soft_deleted_and_physically_deleted_use_actual_replacement_without_mutating_snapshot(self):
        before = copy.deepcopy(self.entry)
        for references in ([self.reference], []):
            self.refs.return_value = references
            result = self.resolve()[0]
            self.assertEqual(result['replacementStatus'], 'resolved')
            self.assertEqual(result['moduleId'], 'new')
            self.assertEqual(result['replacesLmsOfferingId'], 'old')
            self.assertEqual(result['hours'], 104.75)
            self.assertEqual(result['startDate'], '2027-06-17')
            self.assertEqual(result['endDate'], '2027-09-22')
            self.assertEqual((result['cohortId'], result['groupId']), ('home', 'g1'))
            self.assertEqual(self.entry, before)
        self.offers.assert_called_with('PROG-ME-L4', 'home', 'g1', limit=200, offset=0)

    def test_existing_active_delivery_is_never_replaced_by_a_lookalike(self):
        self.refs.return_value = [{**self.reference, 'deleted_at': None}]
        self.assertEqual(self.resolve(), [self.entry])
        self.offers.assert_not_called()

    def test_outage_is_not_evidence_of_deletion(self):
        self.refs.side_effect = lms_readonly.LmsReadUnavailable('connection_failed')
        self.assertEqual(self.resolve(), [self.entry])
        self.offers.assert_not_called()

    def test_moved_original_is_not_rebound(self):
        self.refs.return_value = [{**self.reference, 'cohort_id': 'different'}]
        self.assertEqual(self.resolve()[0]['replacementStatus'], 'conflict')
        self.offers.assert_not_called()

    def test_other_programmes_cohorts_and_groups_are_never_candidates(self):
        for field in ('programme_id', 'cohort_id', 'group_id'):
            with self.subTest(field=field):
                self.offers.return_value = [{**self.offer, field: 'other'}]
                self.assertEqual(self.resolve()[0]['replacementStatus'], 'unavailable')

    def test_multiple_valid_replacements_require_review(self):
        self.offers.return_value = [self.offer, {**self.offer, 'module_catalogue_id': 'another'}]
        result = self.resolve()[0]
        self.assertEqual(result['replacementStatus'], 'ambiguous')
        self.assertEqual(result['moduleId'], 'old')

    def test_invalid_dates_or_only_similar_titles_do_not_establish_identity(self):
        for changes in ({'start_date': None}, {'end_date': '2020-01-01'},
                        {'title': 'Social Media Advanced'}, {'title': 'Social Media copy'}):
            with self.subTest(changes=changes):
                self.offers.return_value = [{**self.offer, **changes}]
                self.assertEqual(self.resolve()[0]['replacementStatus'], 'unavailable')

    def test_administrator_confirmed_martech_alias_is_accepted(self):
        self.entry['academicModuleId'] = 'martech'
        self.module = {'id': 'martech', 'title': 'Marketing Technology (MarTech)'}
        self.offer['title'] = 'Martech'
        self.assertEqual(self.resolve()[0]['replacementStatus'], 'resolved')

    def test_customised_content_and_manual_plans_are_preserved(self):
        self.entry['weeks'] = [{'id': 'custom'}]
        self.assertEqual(self.resolve()[0]['replacementStatus'], 'conflict')
        self.offers.assert_not_called()
        self.entry['assignmentSource'] = 'manual'
        self.assertEqual(self.resolve(), [self.entry])

    def test_one_offering_cannot_replace_two_academic_modules(self):
        second = {**self.entry, 'moduleId': 'old2', 'lmsOfferingId': 'old2', 'academicModuleId': 'second'}
        result = self.resolve([self.entry, second], [self.module, {'id': 'second', 'title': 'Social Media'}])
        self.assertTrue(all(e['replacementStatus'] == 'conflict' for e in result))

    def test_missing_hours_remain_pending_instead_of_invented(self):
        del self.offer['total_otjh']
        result = self.resolve()[0]
        self.assertEqual(result['hours'], 0)
        self.assertTrue(result['hoursPending'])

    def test_failed_candidate_read_is_reported_without_changing_saved_reference(self):
        self.offers.side_effect = lms_readonly.LmsReadUnavailable('select_denied')
        result = self.resolve()[0]
        self.assertEqual(result['replacementStatus'], 'unavailable')
        self.assertEqual(result['moduleId'], 'old')
