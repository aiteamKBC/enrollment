"""Programme Structure enrolment flow: synthetic, in-memory, network blocked."""
import copy
import json
from datetime import date, datetime, timezone as utc
from decimal import Decimal
from unittest.mock import patch

from django.db import connection, DatabaseError
from django.test import TestCase, override_settings

from learner_api import curriculum
from learner_api.models import EnrolmentUser, LearnerProfile
from . import enrolment_catalogue as catalogue, views, modules_views
from .catalog_models import CatalogProgramme, CatalogCohort, CatalogGroup, CatalogModule
from .configuration import defaults
from .models import Assessment, CohortGroupAllocation
from .programme_assignment import CATALOGUE_NAMES
from . import test_programme_assignment as assignment_tests
from .modules_configuration import defaults as module_defaults


@override_settings(ENROLMENT_PROGRAMME_SOURCE='catalogue')
class CatalogueEnrolmentTests(TestCase):
    databases = {'default', 'enrolment'}
    request = assignment_tests.ProgrammeAssignmentIntegrationTests.request
    submit = assignment_tests.ProgrammeAssignmentIntegrationTests.submit

    def setUp(self):
        assignment_tests.ProgrammeAssignmentIntegrationTests.setUp(self)
        self.programmes = {}
        for identifier, code in catalogue.PROGRAMME_CODES.items():
            self.programmes[identifier] = CatalogProgramme.objects.create(
                name=CATALOGUE_NAMES[identifier], code=code, level=int(identifier[-1]),
                description='Synthetic catalogue description', total_cost=Decimal('6000'),
                epa_cost=Decimal('500'), status='draft')
        self.programme = self.programmes['ME_L4']
        self.cohort = self.add_cohort(self.programme, 'Feb 2027')
        self.group = CatalogGroup.objects.create(cohort=self.cohort, name='Synthetic group',
            weekday=2, start_time='09:00', end_time='11:00')
        self.module = CatalogModule.objects.create(programme=self.programme, name='Current catalogue module',
            weeks=14, lectures=20, books=1, reviews=2)

    def add_cohort(self, programme, name):
        return CatalogCohort.objects.create(programme=programme, name=name, start_date='2027-01-01',
            training_end_date='2028-01-31', epa_period=3, epa_unit='months')

    def confirm(self, with_profile=True):
        if not with_profile:
            LearnerProfile.objects.filter(pk=self.profile.pk)._raw_delete('default')
        self.submit()
        Assessment.objects.update(confirmed_at=datetime(2026, 11, 27, 12, tzinfo=utc.utc))
        response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                 'alternativeSelectionReason': 'Synthetic delivery preference.'})
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content)

    def test_exact_inclusive_intake_cutoffs_and_year_rollover(self):
        for value, expected in (
            ('2027-02-15', ('Feb', 2027)), ('2027-02-16', ('June', 2027)),
            ('2027-06-15', ('June', 2027)), ('2027-06-16', ('Oct', 2027)),
            ('2027-10-15', ('Oct', 2027)), ('2027-10-16', ('Feb', 2028)),
            ('2027-12-31', ('Feb', 2028)), ('2028-01-01', ('Feb', 2028)),
            ('2028-02-29', ('June', 2028)),
        ):
            with self.subTest(value=value):
                self.assertEqual(catalogue.intake(date.fromisoformat(value)), expected)

    def test_confirm_saves_catalogue_programme_and_cohort_but_not_group_or_plan(self):
        existing_plan = [{'moduleId': 'preserved-manual-module', 'title': 'Keep this plan'}]
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(group='Existing manual group', learning_plan=existing_plan)
        LearnerProfile.objects.filter(pk=self.profile.pk).update(group_name='Existing manual group',
            group_id='preserved-group-id', learning_plan=existing_plan)
        with patch('enrollment_service.lms_readonly.get_assigned_cohort', side_effect=AssertionError('No LMS reads')):
            result = self.confirm()
        self.source.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.source.programme, self.programme.name)
        self.assertEqual(self.source.cohort, self.cohort.name)
        self.assertEqual(self.profile.programme_id, str(self.programme.pk))
        self.assertEqual(self.profile.cohort_id, str(self.cohort.pk))
        self.assertEqual(self.source.group, 'Existing manual group')
        self.assertEqual(self.profile.group_id, 'preserved-group-id')
        self.assertEqual(self.source.learning_plan, existing_plan)
        self.assertEqual(self.profile.learning_plan, existing_plan)
        self.assertEqual(result['programmeAssignment']['source'], 'programme_catalogue')
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.assertNotIn('fixedLearningPlan', Assessment.objects.get().review)
        retry = self.request({'revision': result['revision'], 'action': 'select', 'selectedProgramme': 'ME_L4'})
        self.assertEqual(retry.status_code, 200, retry.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.learning_plan, existing_plan)

    def test_confirmation_without_profile_does_not_create_one_or_allocate_modules(self):
        self.confirm(with_profile=False)
        self.assertFalse(LearnerProfile.objects.filter(enrolment_id=self.source.pk).exists())
        self.source.refresh_from_db()
        self.assertEqual((self.source.programme, self.source.cohort), (self.programme.name, self.cohort.name))
        self.assertEqual(self.source.group, '')
        self.assertEqual(self.source.learning_plan, [])

    def test_saved_programme_and_cohort_appear_in_existing_account_apis(self):
        self.confirm()
        with patch('learner_api.mappers._bksb_assessment_rows', return_value={'english': [], 'maths': []}):
            assignment_tests.ProgrammeAssignmentIntegrationTests.assert_account_apis(self, self.programme.name)
            from learner_api.mappers import to_board
            self.source.refresh_from_db()
            board = to_board(self.source)
        self.assertEqual(board['programme']['cohort'], self.cohort.name)
        self.assertEqual(board['contact']['groupMembership'], '')

    def test_profile_failure_rolls_back_confirmation_programme_and_cohort(self):
        self.submit()
        with patch.object(LearnerProfile, 'save', side_effect=DatabaseError('Synthetic write failure')):
            response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                     'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(response.status_code, 503)
        self.source.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual((self.source.programme, self.source.cohort), ('', ''))
        self.assertFalse(self.profile.programme_id)
        self.assertIsNone(Assessment.objects.get().selection_confirmed_at)

    def test_module_storage_failure_is_reported_without_affecting_existing_enrolment(self):
        self.confirm()
        with patch('programme_suitability.enrolment_catalogue.programme_modules', side_effect=DatabaseError('Synthetic catalogue failure')):
            response = self.request(method='GET', endpoint=modules_views.learner_modules)
        self.assertEqual(response.status_code, 503)
        self.source.refresh_from_db()
        self.assertEqual((self.source.programme, self.source.cohort), (self.programme.name, self.cohort.name))
        self.assertEqual(self.source.learning_plan, [])

    def test_intake_uses_exam_submission_not_later_selection_date(self):
        june = self.add_cohort(self.programme, 'June 2027')
        self.submit()
        Assessment.objects.update(confirmed_at=datetime(2027, 6, 15, 12, tzinfo=utc.utc))
        # The real confirmation date is outside that intake; only exam time counts.
        result = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                               'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(result.status_code, 200, result.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.cohort, june.name)

    def test_exam_timestamp_uses_london_calendar_day(self):
        self.add_cohort(self.programme, 'June 2027')
        october = self.add_cohort(self.programme, 'Oct 2027')
        self.submit()
        # June 16 in London, although still June 15 in UTC.
        Assessment.objects.update(confirmed_at=datetime(2027, 6, 15, 23, 30, tzinfo=utc.utc))
        result = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                               'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(result.status_code, 200, result.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.cohort, october.name)

    def test_missing_or_ambiguous_intake_does_not_substitute_another_cohort(self):
        self.cohort.name = 'Feb 2026'; self.cohort.save(update_fields=['name'])
        result = self.confirm()
        self.source.refresh_from_db(); self.profile.refresh_from_db()
        self.assertEqual(self.source.programme, self.programme.name)
        self.assertEqual(self.source.cohort, '')
        self.assertFalse(self.profile.cohort_id)
        self.assertTrue(result['programmeAssignment']['automaticCohort']['pending'])
        self.assertEqual(result['programmeAssignment']['automaticCohort']['target'], 'Feb 2027')
        modules = self.request(method='GET', endpoint=modules_views.learner_modules)
        self.assertEqual(modules.status_code, 200, modules.content)
        self.assertIsNone(json.loads(modules.content)['assignedCohortName'])
        self.add_cohort(self.programme, 'Feb 2027')
        self.add_cohort(self.programme, 'February 2027')
        resolved, _ = catalogue.cohort_for(self.programme, date(2026, 11, 27))
        self.assertIsNone(resolved)

    def test_archived_programme_cannot_be_selected_and_confirmation_is_atomic(self):
        self.submit()
        self.programme.status = 'archived'; self.programme.save(update_fields=['status'])
        response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                 'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(response.status_code, 409, response.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.programme, '')
        self.assertIsNone(Assessment.objects.get().selection_confirmed_at)

    def test_existing_cohort_is_preserved_and_an_unverified_placement_is_not_overwritten(self):
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(cohort='February 2030')
        self.submit()
        response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                 'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(response.status_code, 409, response.content)
        self.source.refresh_from_db(); self.assertEqual(self.source.cohort, 'February 2030')
        self.assertEqual(self.source.programme, '')

    def test_catalogue_module_read_is_live_read_only_and_preserves_academic_snapshot(self):
        self.confirm()
        old = module_defaults()
        Assessment.objects.update(modules_configuration=old, modules_configuration_version=9,
                                   modules_selection={'preserved': 'old choice'})
        before = copy.deepcopy(Assessment.objects.values().get())
        self.module.name = 'Renamed catalogue module'; self.module.save(update_fields=['name'])
        CatalogModule.objects.create(programme=self.programmes['MM_L6'], name='Wrong programme module', weeks=1)
        def read_only(execute, sql, params, many, context):
            if sql.lstrip().split()[0].upper() != 'SELECT':
                raise AssertionError('Catalogue module GET attempted a write')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(read_only), patch('programme_suitability.home_cohort.read_home_cohort', side_effect=AssertionError('No LMS/placement lookup')):
            response = self.request(method='GET', endpoint=modules_views.learner_modules)
        self.assertEqual(response.status_code, 200, response.content)
        result = json.loads(response.content)
        self.assertEqual(result['source'], 'programme_catalogue')
        self.assertEqual(result['catalogueProgramme']['modules'][0]['title'], self.module.name)
        self.assertEqual(len(result['catalogueProgramme']['modules']), 1)
        self.assertEqual(result['configuration'], old)
        self.assertEqual(result['savedSelection'], {'preserved': 'old choice'})
        self.assertEqual(Assessment.objects.values().get(), before)

    def test_new_catalogue_module_reads_work_for_admin_and_student_without_pinning_or_writes(self):
        self.confirm()
        before = copy.deepcopy(Assessment.objects.values().get())
        for role in ('staff', 'learner'):
            with self.subTest(role=role):
                response = self.request(method='GET', role=role, endpoint=modules_views.learner_modules)
                self.assertEqual(response.status_code, 200, response.content)
                data = json.loads(response.content)
                self.assertEqual(data['catalogueProgramme']['title'], self.programme.name)
                self.assertEqual(data['assignedCohortName'], self.cohort.name)
                self.assertEqual(data['catalogueProgramme']['modules'][0]['id'], str(self.module.pk))
                self.assertEqual(Assessment.objects.values().get(), before)
        self.assertIsNone(Assessment.objects.get().modules_configuration)

    def test_a_completed_learner_is_not_reassigned_even_when_the_old_programme_is_empty(self):
        self.submit()
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(onboarding_status='Completed')
        response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                                 'alternativeSelectionReason': 'Synthetic reason'})
        self.assertEqual(response.status_code, 409, response.content)
        self.source.refresh_from_db()
        self.assertEqual((self.source.programme, self.source.cohort), ('', ''))

    def test_catalogue_lookup_options_do_not_call_lms(self):
        for endpoint, query, expected in (
            (curriculum.programmes, {}, self.programme.name),
            (curriculum.cohorts, {'programme': self.programme.name}, self.cohort.name),
            (curriculum.groups, {'programme': self.programme.name, 'cohort': self.cohort.name}, self.group.name),
        ):
            request = self.factory.get('/', query)
            with patch('enrollment_service.lms_readonly.get_programme_cohorts', side_effect=AssertionError('No LMS read')):
                response = endpoint(request)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertIn(expected, json.loads(response.content)['results'])

    def test_catalogue_names_and_ids_are_presented_without_changing_saved_scores(self):
        self.submit()
        before = Assessment.objects.get()
        response = self.request(method='GET')
        self.assertEqual(response.status_code, 200, response.content)
        data = json.loads(response.content)
        self.assertEqual(data['programmeSource'], 'catalogue')
        displayed = next(p for p in data['configuration']['programmes'] if p['id'] == 'ME_L4')
        self.assertEqual(displayed['catalogueId'], str(self.programme.pk))
        self.assertEqual(displayed['catalogueName'], self.programme.name)
        after = Assessment.objects.get()
        self.assertEqual(after.result, before.result)
        self.assertEqual(after.configuration, before.configuration)

    def test_pcp_programme_confirmation_stops_at_cohort_and_allocation_stays_paused(self):
        from .cohort_confirmation import cohort_module_confirmation
        from .completion import submission_error
        pcp = self.programmes['PCP_L6']
        cohort = self.add_cohort(pcp, 'Feb 2027')
        self.submit(pathway='project-controls')
        Assessment.objects.update(confirmed_at=datetime(2026, 11, 27, 12, tzinfo=utc.utc))
        response = self.request({'revision': 1, 'action': 'continue'})
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.programme, pcp.name)
        self.assertEqual(self.source.cohort, cohort.name)
        self.assertEqual(self.source.group, '')
        self.assertEqual(self.source.learning_plan, [])
        with patch('enrolment_api.wizard_layout.current_layout', return_value={'programmeSuitability': self.config}):
            self.assertIsNone(submission_error(self.source))
        response = self.request({'expectedRevision': 0}, endpoint=cohort_module_confirmation)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['code'], 'allocation_paused')
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_required_assessment_validation_is_not_bypassed(self):
        from .completion import submission_error
        with patch('enrolment_api.wizard_layout.current_layout', return_value={'programmeSuitability': self.config}):
            self.assertIsNotNone(submission_error(self.source))
