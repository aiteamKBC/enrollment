import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.test import SimpleTestCase, RequestFactory

from enrollment_service import lms_readonly
from learner_api import curriculum, active_users
from . import lms_placement as placement
from .future_cohort import resolve_future_cohort
from .home_cohort import resolve_lms_home, read_lms_home
from .programme_identity import CURRICULUM_PROGRAMME_IDS


@patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': 'synthetic-only'})
class LmsPlacementTests(SimpleTestCase):
    def setUp(self):
        self.catalogue = patch.object(lms_readonly, 'get_programme_cohorts').start()
        self.modules = patch.object(lms_readonly, 'get_assigned_cohort').start()
        self.groups = patch.object(lms_readonly, 'get_cohort_groups').start()
        self.addCleanup(patch.stopall)
        self.cohort = dict(cohort_id='lms-feb', cohort_name='Feb 2027', programme_id='PROG-PCP-L6', status='planned')
        self.module = dict(programme_id='PROG-PCP-L6', cohort_id='lms-feb', cohort_name='Feb 2027',
                           module_catalogue_id='m1', title='Module', sessions_number=6,
                           group_id='g1', group_name='G1', start_date=None, end_date=None)
        self.catalogue.return_value = [self.cohort]
        self.modules.return_value = [self.module]
        self.groups.return_value = [{k: self.module[k] for k in ('group_id', 'group_name', 'programme_id', 'cohort_id')}]

    def test_each_programme_uses_its_own_lms_intake_for_new_confirmations(self):
        for canonical, identifier in CURRICULUM_PROGRAMME_IDS.items():
            self.catalogue.return_value = [{**self.cohort, 'programme_id': identifier}]
            row = SimpleNamespace(selected_programme=canonical)
            with patch('programme_suitability.future_cohort.learner_published_layout') as publication:
                result = resolve_future_cohort(row, 'unused', identifier, date(2026, 11, 17), include_publication=True)
            self.assertEqual(result['cohortId'], 'lms-feb')
            self.assertEqual(result['source'], 'lms')
            self.assertIsNone(result['publicationVersion'])
            self.catalogue.assert_called_with(identifier)
            publication.assert_not_called()

    def test_missing_intake_never_moves_a_student_to_an_older_one(self):
        self.catalogue.return_value = [{**self.cohort, 'cohort_name': 'October 2026'}]
        self.assertIsNone(placement.future_cohort('PCP_L6', date(2026, 11, 17)))

    def test_duplicate_inactive_or_cross_programme_catalogues_cannot_assign(self):
        for rows in [[self.cohort, {**self.cohort, 'cohort_id': 'duplicate'}],
                     [{**self.cohort, 'status': 'archived'}]]:
            self.catalogue.return_value = rows
            self.assertIsNone(placement.future_cohort('PCP_L6', date(2026, 11, 17)))
        self.catalogue.return_value = [{**self.cohort, 'programme_id': 'PROG-ME-L4'}]
        with self.assertRaises(lms_readonly.LmsReadUnavailable):
            placement.programme_cohorts('PROG-PCP-L6')

    def test_legacy_cohort_id_resolves_unique_equivalent_label_without_writes(self):
        self.modules.side_effect = [[], [self.module]]
        result = resolve_lms_home('PCP_L6', 'PROG-PCP-L6', 'old-local-id', cohort_name='February 2027')
        self.assertEqual(result['cohortId'], 'lms-feb')
        self.assertIsNone(result['groupId'])
        self.assertEqual(result['lmsModules'][0]['groupId'], 'g1')

    def test_missing_legacy_cohort_can_resolve_when_lms_admin_adds_it(self):
        self.modules.return_value = []
        self.catalogue.return_value = []
        missing = resolve_lms_home('PCP_L6', 'PROG-PCP-L6', 'old', cohort_name='Feb 2027')
        self.assertEqual(missing['reason'], 'assigned_cohort_not_in_lms')
        self.catalogue.return_value = [self.cohort]
        self.modules.side_effect = [[], [self.module]]
        self.assertEqual(resolve_lms_home('PCP_L6', 'PROG-PCP-L6', 'old', cohort_name='Feb 2027')['cohortId'], 'lms-feb')

    def test_legacy_alias_does_not_strip_group_or_campus_qualifiers(self):
        self.assertIsNone(placement.saved_cohort('PROG-PCP-L6', 'old', 'Feb 2027 Campus A')[0])

    def test_account_only_learner_manual_change_takes_precedence_over_old_audit(self):
        row = SimpleNamespace(selected_programme='PCP_L6', configuration={}, review={'programmeAssignment': {
            'status': 'APPLIED', 'programmeId': 'PROG-PCP-L6', 'programmeName': 'Project Controls Professional Level 6',
            'cohortId': 'old', 'cohortName': 'October 2026'}})
        source = SimpleNamespace(programme='Project Controls Professional Level 6', cohort='Feb 2027', group=None)
        with patch('learner_api.models.EnrolmentUser.all_learners.filter') as find:
            find.return_value.only.return_value.first.return_value = source
            result = read_lms_home(row, None, 42)
        self.assertEqual(result['cohortId'], 'lms-feb')
        self.assertEqual(row.review['programmeAssignment']['cohortId'], 'old')

    def test_admin_dropdowns_use_live_lms_data(self):
        factory = RequestFactory()
        response = curriculum.cohorts(factory.get('/', {'programme': 'Project Controls Professional Level 6'}))
        self.assertEqual(json.loads(response.content)['results'], ['Feb 2027'])
        response = curriculum.groups(factory.get('/', {'programme': 'Project Controls Professional Level 6', 'cohort': 'Feb 2027'}))
        self.assertEqual(json.loads(response.content)['results'], ['G1'])

    def test_group_dropdown_and_saved_ids_work_without_any_modules(self):
        self.modules.return_value = []
        response = curriculum.groups(RequestFactory().get('/', {'programme': 'Project Controls Professional Level 6', 'cohort': 'Feb 2027'}))
        self.assertEqual(json.loads(response.content)['results'], ['G1'])
        self.assertEqual(placement.placement_ids('PROG-PCP-L6', 'Feb 2027', 'G1'), ('lms-feb', 'g1'))
        self.modules.assert_not_called()

    def test_missing_groups_permission_is_not_reported_as_an_empty_dropdown(self):
        self.groups.side_effect = lms_readonly.LmsReadUnavailable('groups_access_unavailable')
        self.modules.return_value = []
        response = curriculum.groups(RequestFactory().get('/', {'programme': 'Project Controls Professional Level 6', 'cohort': 'Feb 2027'}))
        self.assertEqual(response.status_code, 503)
        self.assertIn('enable read access', json.loads(response.content)['error'])

    def test_group_results_cannot_leak_from_another_cohort(self):
        self.groups.return_value = [{**self.groups.return_value[0], 'cohort_id': 'another-cohort'}]
        with self.assertRaises(lms_readonly.LmsReadUnavailable):
            placement.cohort_groups('PROG-PCP-L6', 'lms-feb')

    def test_admin_edits_resolve_lms_ids_and_clearing_group_does_not_choose_one(self):
        self.assertEqual(active_users._linkable_placement_ids('PROG-PCP-L6', '', 'Feb 2027', 'Group 1'), ('lms-feb', 'g1'))
        self.assertEqual(active_users._linkable_placement_ids('PROG-PCP-L6', '', 'Feb 2027', ''), ('lms-feb', ''))

    def test_admin_mirror_replaces_old_ids_and_clears_old_group(self):
        profile = SimpleNamespace(programme='Project Controls Professional Level 6', programme_id='PROG-PCP-L6',
            cohort='October 2026', cohort_id='old-cohort', group_name='Old group', group_id='old-group',
            start_date=None, end_date=None, gateway_review_date=None, save=Mock())
        source = SimpleNamespace(programme=profile.programme, cohort='Feb 2027', group='', pk=42)
        with patch.object(active_users, 'learner_profile_for_source', return_value=profile), \
                patch.object(active_users, '_resolve_linkable_programme_id', return_value='PROG-PCP-L6'), \
                patch.object(active_users, 'cohort_dates', return_value=(None, None)):
            result = active_users.mirror_learner_placement(source)
        self.assertIs(result, profile)
        self.assertEqual(profile.cohort_id, 'lms-feb')
        self.assertIsNone(profile.group_id)
        self.assertEqual(profile.cohort, 'Feb 2027')
        profile.save.assert_called_once()

    def test_lms_outage_does_not_fall_back_to_local_cohort_options(self):
        self.catalogue.side_effect = lms_readonly.LmsReadUnavailable('read_failed')
        with patch.object(curriculum, '_rows') as local:
            response = curriculum.cohorts(RequestFactory().get('/', {'programme': 'Project Controls Professional Level 6'}))
        self.assertEqual(response.status_code, 503)
        local.assert_not_called()
