import copy
import json
import re
from types import SimpleNamespace
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.db import DatabaseError, connection, transaction
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from learner_api.models import EnrolmentUser, LearnerProfile
from learner_api import learning_plan
from . import test_programme_assignment as assignment_tests
from . import fixed_learning_plan as fixed
from .models import Assessment, CohortGroupAllocation, CohortGroupPool
from .programme_identity import CURRICULUM_PROGRAMME_IDS


class FixedLearningPlanTests(TestCase):
    databases = {'default', 'enrolment'}
    request = assignment_tests.ProgrammeAssignmentIntegrationTests.request
    submit = assignment_tests.ProgrammeAssignmentIntegrationTests.submit

    def setUp(self):
        assignment_tests.ProgrammeAssignmentIntegrationTests.setUp(self)
        self.module_config = json.loads(Path(__file__).with_name('modules_defaults.json').read_text(encoding='utf-8'))
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=None, cohort='Feb 2027', group='Group 1')
        LearnerProfile.objects.filter(pk=self.profile.pk).update(learning_plan=None)
        with connection.cursor() as cursor:
            for code, pid in CURRICULUM_PROGRAMME_IDS.items():
                cursor.execute('UPDATE curriculum.programmes SET programme_id=%s WHERE programme_id=%s', [pid, 'CAT-' + code])
                cursor.execute('INSERT INTO curriculum.cohorts (cohort_id,cohort_name,programme_id,programme_name,status) VALUES (%s,%s,%s,%s,%s)',
                    ['home-' + code, 'Feb 2027', pid, assignment_tests.CATALOGUE_NAMES[code], 'planned'])
                cursor.execute('INSERT INTO curriculum.groups (group_id,group_name,cohort_id,cohort_name,programme_id,programme_name) VALUES (%s,%s,%s,%s,%s,%s)',
                    ['g1-' + code, 'Group 1', 'home-' + code, 'Feb 2027', pid, assignment_tests.CATALOGUE_NAMES[code]])
        self.cohorts = patch.object(fixed.lms_readonly, 'get_programme_cohorts', side_effect=lambda pid: [
            {'programme_id': pid, 'cohort_id': 'home-' + self.code(pid), 'cohort_name': 'Feb 2027'}]).start()
        self.groups = patch.object(fixed.lms_readonly, 'get_cohort_groups', side_effect=lambda pid, cid: [
            {'programme_id': pid, 'cohort_id': cid, 'group_id': 'g1-' + self.code(pid), 'group_name': 'Group 1'}]).start()
        self.read = patch('programme_suitability.module_schedules.read_all', side_effect=self.offerings).start()
        self.addCleanup(patch.stopall)

    def code(self, pid):
        return next(code for code, value in CURRICULUM_PROGRAMME_IDS.items() if value == pid)

    def modules(self, code):
        programme = next(p for p in self.module_config['programmes'] if p['id'] == code)
        return list(fixed._modules(programme.get('modules', [])))

    def offerings(self, pid, cid):
        return [{'module_catalogue_id': 'lms-' + m['id'], 'programme_id': pid, 'cohort_id': cid,
            'cohort_name': 'Feb 2027', 'group_id': 'g1-' + self.code(pid), 'group_name': 'Group 1',
            'title': m['title'], 'start_date': '2027-02-01', 'end_date': '2027-06-01', 'cohort_status': 'planned',
            'sessions_number': 16, 'weeks_number': 20} for m in self.modules(self.code(pid))]

    def prepare(self, code):
        self.submit(pathway='marketing' if code.startswith('M') else 'project-controls')
        row = Assessment.objects.get()
        row.modules_configuration = copy.deepcopy(self.module_config)
        row.modules_configuration_version = 24
        row.save()
        return row

    def confirm(self, code):
        row = Assessment.objects.get()
        response = self.request({'revision': row.revision, 'action': 'select', 'selectedProgramme': code,
            'alternativeSelectionReason': 'My actual role fits this programme.'})
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        return json.loads(response.content)

    def test_me_mm_apm_confirmation_populates_pinned_fixed_modules_and_real_home_schedule(self):
        for code in ('ME_L4', 'MM_L6', 'APM_L4'):
            with self.subTest(programme=code):
                Assessment.objects.all().delete()
                EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme='', learning_plan=None)
                LearnerProfile.objects.filter(pk=self.profile.pk).update(programme='', programme_id='', learning_plan=None)
                self.prepare(code)
                self.assertIsNone(EnrolmentUser.all_learners.get(pk=self.source.pk).learning_plan)
                result = self.confirm(code)
                plan = self.source.learning_plan
                self.assertEqual([m['academicModuleId'] for m in plan], [m['id'] for m in self.modules(code)])
                self.assertEqual([m['moduleId'] for m in plan], ['lms-' + m['id'] for m in self.modules(code)])
                self.assertTrue(all(m['cohortId'] == 'home-' + code and m['groupId'] == 'g1-' + code for m in plan))
                self.assertTrue(all(m['schedulingStatus'] == 'scheduled' and m['startDate'] == '2027-02-01' for m in plan))
                self.assertEqual(result['review']['fixedLearningPlan']['status'], 'CREATED')
                self.read.assert_called_with(CURRICULUM_PROGRAMME_IDS[code], 'home-' + code)

    def test_recommendation_differs_from_confirmed_programme_and_is_not_used_for_plan(self):
        row = self.prepare('ME_L4')
        self.assertEqual(row.result['recommendedProgramme'], 'MM_L6')
        self.confirm('ME_L4')
        self.assertTrue(all(m['programmeId'] == CURRICULUM_PROGRAMME_IDS['ME_L4'] for m in self.source.learning_plan))

    def test_continue_confirmation_pins_modules_with_the_existing_configuration_service(self):
        self.submit()
        with patch('programme_suitability.modules_service.published_modules', return_value=(24, self.module_config)) as published:
            response = self.request({'revision': 1, 'action': 'continue'})
        self.assertEqual(response.status_code, 200, response.content)
        published.assert_called_once()
        self.source.refresh_from_db()
        self.assertEqual(len(self.source.learning_plan), len(self.modules('MM_L6')))
        self.assertEqual(Assessment.objects.get().modules_configuration_version, 24)

    def test_existing_saved_module_configuration_wins_over_newer_published_definitions(self):
        row = self.prepare('ME_L4')
        row.modules_configuration['programmes'][0]['modules'][0]['title'] = 'Saved academic title'
        row.save()
        with patch('programme_suitability.modules_service.published_modules', side_effect=AssertionError('Do not repin fixed definitions')):
            self.confirm('ME_L4')
        self.assertEqual(self.source.learning_plan[0]['moduleTitle'], 'Saved academic title')
        self.assertEqual(self.source.learning_plan[0]['configurationVersion'], 24)

    def test_missing_lms_dates_still_saves_all_academic_modules_without_invented_schedule(self):
        self.prepare('ME_L4')
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        self.read.side_effect = None
        self.read.return_value = [{**o, 'start_date': None} for o in offers]
        self.confirm('ME_L4')
        for entry in self.source.learning_plan:
            self.assertTrue(entry['moduleId'].startswith('academic:ME_L4:'))
            self.assertIsNone(entry['lmsOfferingId'])
            self.assertEqual(entry['schedulingStatus'], 'pending')
            self.assertEqual((entry['startDate'], entry['endDate']), ('', ''))

    def test_unavailable_lms_does_not_prevent_academic_plan_or_poison_confirmation_transaction(self):
        self.prepare('MM_L6')
        self.cohorts.side_effect = DatabaseError('isolated unavailable source')
        self.confirm('MM_L6')
        self.assertEqual(len(self.source.learning_plan), len(self.modules('MM_L6')))
        self.assertTrue(all(m['schedulingStatus'] == 'pending' and not m['groupId'] for m in self.source.learning_plan))

    def test_real_matched_module_hours_and_fractional_total_ignore_other_groups(self):
        self.historical_unsaved()
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        for offer, hours in zip(offers, (Decimal('138.00'), Decimal('103.00'), Decimal('105.75'))):
            offer['total_otjh'] = hours
        self.read.side_effect = None
        self.read.return_value = offers + [{**offers[0], 'group_id': 'foreign-group', 'total_otjh': Decimal('9999')}]
        plan = fixed.preview_uninitialised_fixed_plan(self.source)
        self.assertEqual([m['hours'] for m in plan], [138, 103, 105.75])
        self.assertTrue(all(m['hoursPending'] is False for m in plan))
        self.assertEqual(learning_plan._totals(plan)['totalHours'], 346.75)
        with transaction.atomic():
            fixed.populate_fixed_learning_plan(Assessment.objects.select_for_update().get())
        self.source.refresh_from_db()
        self.assertEqual([m['hours'] for m in self.source.learning_plan], [138, 103, 105.75])

    def test_unknown_hours_are_pending_and_never_calculated_from_sessions_or_weeks(self):
        for raw in (None, 'not-a-number', float('nan'), float('inf'), -1):
            with self.subTest(raw=raw):
                self.assertEqual(fixed._module_hours({'total_otjh': raw, 'sessions_number': 16, 'weeks_number': 20}), (0, True))
        self.assertEqual(fixed._module_hours({'total_otjh': 0}), (0, False))

    def test_foreign_cohort_and_group_payloads_are_rejected_even_with_matching_titles(self):
        self.prepare('APM_L4')
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['APM_L4'], 'home-APM_L4')
        self.read.side_effect = None
        self.read.return_value = [{**o, 'cohort_id': 'foreign-home'} for o in offers] + [{**o, 'group_id': 'g2'} for o in offers]
        self.confirm('APM_L4')
        self.assertTrue(all(m['schedulingStatus'] == 'pending' for m in self.source.learning_plan))

    def test_missing_group_does_not_choose_one_or_query_other_cohort_schedules(self):
        self.prepare('ME_L4')
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(group='')
        self.confirm('ME_L4')
        self.read.assert_not_called()
        self.assertTrue(all(not m['groupId'] and not m['groupName'] for m in self.source.learning_plan))

    def test_existing_allocated_group_is_read_without_reserving_capacity(self):
        self.prepare('ME_L4')
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(group='')
        pool = CohortGroupPool.objects.create(programme_id=CURRICULUM_PROGRAMME_IDS['ME_L4'], cohort_id='home-ME_L4')
        allocation = CohortGroupAllocation.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship',
            pool=pool, group_id='g1-ME_L4', group_name='Group 1', seat=1, academic_ids=[], offerings=[],
            configuration_version=24, selection_revision=0)
        self.confirm('ME_L4')
        self.assertTrue(all(m['groupId'] == allocation.group_id for m in self.source.learning_plan))
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.group, '')

    def test_retry_refresh_and_later_manual_edits_do_not_duplicate_or_overwrite(self):
        self.prepare('ME_L4')
        self.confirm('ME_L4')
        initial = copy.deepcopy(self.source.learning_plan)
        row = Assessment.objects.get()
        self.assertEqual(self.request({'revision': row.revision - 1, 'action': 'select', 'selectedProgramme': 'ME_L4'}).status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, initial)
        edited = [{**initial[0], 'moduleTitle': 'Manual learner-specific content'}]
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=edited)
        self.assertEqual(self.request({'revision': row.revision, 'action': 'continue'}).status_code, 200)
        self.assertEqual(self.request(method='GET').status_code, 200)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, edited)

    def test_existing_manual_and_explicitly_cleared_plans_are_preserved(self):
        for stored in ([], [{'moduleId': 'manual', 'moduleTitle': 'Keep this', 'startDate': '2026-01-02'}]):
            with self.subTest(existing=stored):
                Assessment.objects.all().delete()
                EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme='', learning_plan=stored)
                LearnerProfile.objects.filter(pk=self.profile.pk).update(programme='', programme_id='')
                self.prepare('ME_L4')
                result = self.confirm('ME_L4')
                self.assertEqual(self.source.learning_plan, stored)
                self.assertEqual(result['review']['fixedLearningPlan']['status'], 'EXISTING_PLAN_PRESERVED')
        self.read.assert_not_called()

    def test_pcp_confirmation_remains_unchanged(self):
        self.prepare('PCP_L6')
        self.confirm('PCP_L6')
        self.assertIsNone(self.source.learning_plan)
        self.assertNotIn('fixedLearningPlan', Assessment.objects.get().review)
        self.cohorts.assert_not_called()
        self.read.assert_not_called()

    def test_learning_plan_failure_rolls_back_confirmation_and_does_not_change_status_or_documents(self):
        self.prepare('ME_L4')
        original = QuerySet.update
        def update(query, **fields):
            if 'learning_plan' in fields:
                raise DatabaseError('isolated plan write failure')
            return original(query, **fields)
        with patch.object(QuerySet, 'update', update), CaptureQueriesContext(connection) as queries:
            response = self.request({'revision': 1, 'action': 'select', 'selectedProgramme': 'ME_L4',
                'alternativeSelectionReason': 'Role choice'})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(Assessment.objects.get().selection_confirmed_at)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)
        self.assertEqual(self.source.programme_status, 'Onboarding')
        writes = [q['sql'].lower() for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
        self.assertFalse(any(re.search(r'(?:update|insert into|delete from)\s+[^\s]*(?:document|signature)', q) for q in writes))

    def test_existing_learning_plan_serializer_displays_saved_academic_rows_without_catalogue_substitution(self):
        self.prepare('ME_L4')
        self.read.side_effect = None
        self.read.return_value = []
        self.confirm('ME_L4')
        with (patch.object(learning_plan, 'learner_programme_modules', side_effect=AssertionError('No broad programme lookup')),
             patch.object(learning_plan, 'learner_group_module_ids', side_effect=AssertionError('No preset substitution')),
             patch.object(learning_plan, '_all_modules', return_value=[]),
             patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')),
             patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner', subject_type='learner', subject_id=self.source.pk))):
            response = learning_plan.learning_plan(self.factory.get('/learning-plan/4101/'), pk=self.source.pk)
            self.assertEqual(response.status_code, 200, response.content)
            data = json.loads(response.content)
        self.assertEqual(data['plan'], self.source.learning_plan)
        self.assertEqual(data['learner']['programmeId'], CURRICULUM_PROGRAMME_IDS['ME_L4'])
        self.assertEqual(len(data['plan']), 3)
        self.assertTrue(all(m['schedulingStatus'] == 'pending' and not m.get('orphaned') for m in data['plan']))

    def test_saved_plan_view_uses_recreated_home_modules_and_keeps_original_record(self):
        self.prepare('ME_L4')
        self.confirm('ME_L4')
        before = copy.deepcopy(self.source.learning_plan)
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        replacement = dict(offers[1], module_catalogue_id='new-social', total_otjh=Decimal('110.5'))
        references = [dict(module_catalogue_id=e['moduleId'], programme_id=e['programmeId'],
            cohort_id=e['cohortId'], group_id=e['groupId'], deleted_at='2026-11-18' if i == 1 else None)
            for i, e in enumerate(before)]
        with (patch.object(fixed.lms_readonly, 'get_saved_module_references', return_value=references),
              patch.object(fixed.lms_readonly, 'get_replacement_offerings', return_value=[replacement]),
              patch.object(learning_plan, '_all_modules', return_value=[]),
              patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')),
              CaptureQueriesContext(connection) as queries):
            data = learning_plan._serialize(self.source)
        self.assertTrue(data['saved'])
        self.assertEqual(len(data['plan']), 3)
        self.assertEqual(data['plan'][1]['moduleId'], 'new-social')
        self.assertEqual(data['plan'][1]['hours'], 110.5)
        self.assertEqual(data['plan'][0], before[0])
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, before)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

        # Saving an otherwise unchanged editable plan round-trips the effective
        # ID, keeps the original confirmation reference and cannot duplicate it.
        ids = [e['moduleId'] for e in data['plan']] + [before[1]['moduleId']]
        request = self.factory.patch('/learning-plan/4101/', json.dumps({'modules': ids}), content_type='application/json')
        with (patch.object(fixed.lms_readonly, 'get_saved_module_references', return_value=references),
              patch.object(fixed.lms_readonly, 'get_replacement_offerings', return_value=[replacement]),
              patch.object(learning_plan, '_all_modules', return_value=[]),
              patch.object(learning_plan, 'learner_programme_modules', return_value=[]),
              patch.object(learning_plan, 'learner_group_module_ids', return_value=[]),
              patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')),
              patch.object(learning_plan, 'sync_learning_plan_mirror'),
              patch.object(learning_plan, 'advance_learner'),
              patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner', subject_type='learner', subject_id=self.source.pk))):
            response = learning_plan.learning_plan(request, pk=self.source.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, before)

    def historical_unsaved(self):
        row = self.prepare('ME_L4')
        row.selected_programme = 'ME_L4'
        row.selection_confirmed_at = row.confirmed_at
        row.save()
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(programme='Marketing Executive Level 4')
        LearnerProfile.objects.filter(pk=self.profile.pk).update(programme_id=CURRICULUM_PROGRAMME_IDS['ME_L4'],
            cohort_id='home-ME_L4', cohort='Feb 2027', group_id='g1-ME_L4', group_name='Group 1')
        self.source.refresh_from_db()
        return row

    def test_previously_confirmed_unsaved_learner_gets_all_three_real_home_modules_instead_of_legacy_preset(self):
        row = self.historical_unsaved()
        real = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        real[-1]['title'] = 'Martech'
        self.read.side_effect = None
        self.read.return_value = real
        with (patch.object(learning_plan, 'learner_programme_modules', return_value=[{'moduleId': 'old', 'moduleTitle': 'Impact& Planning'}]) as legacy,
              patch.object(learning_plan, 'learner_group_module_ids', return_value=['old']) as preset,
              patch.object(learning_plan, '_all_modules', return_value=[]),
              patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')),
              patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner', subject_type='learner', subject_id=self.source.pk)),
              CaptureQueriesContext(connection) as queries):
            response = learning_plan.learning_plan(self.factory.get('/learning-plan/4101/'), pk=self.source.pk)
            self.assertEqual(response.status_code, 200, response.content)
            data = json.loads(response.content)
        self.assertEqual(len(data['plan']), 3)
        self.assertEqual([m['moduleTitle'] for m in data['plan']], ['Marketing Impact and Planning', 'Social Media', 'Martech'])
        self.assertEqual([m['lmsOfferingId'] for m in data['plan']], [o['module_catalogue_id'] for o in real])
        self.assertFalse(data['saved'])
        self.assertEqual(data['prefillSource'], 'confirmed-programme')
        legacy.assert_not_called()
        preset.assert_not_called()
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)
        self.assertEqual(Assessment.objects.get().review, row.review)
        from .module_schedules import project
        schedule = project('ME_L4', None, self.modules('ME_L4'), {'status': 'resolved', 'cohortId': 'home-ME_L4', 'groupId': 'g1-ME_L4'},
            today=date(2026, 11, 17), home_group={'status': 'assigned', 'groupId': 'g1-ME_L4'})
        by_id = {o['id']: o for module in schedule['modules'] for o in module['offerings']}
        for module in data['plan']:
            offering = by_id[module['lmsOfferingId']]
            self.assertEqual((module['moduleTitle'], module['startDate'], module['endDate']),
                (offering['lmsTitle'], offering['start'], offering['end']))

    def test_existing_plan_save_accepts_verified_home_modules_without_legacy_catalogue_substitution(self):
        self.historical_unsaved()
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        request = self.factory.patch('/learning-plan/4101/', json.dumps({'modules': [o['module_catalogue_id'] for o in offers]}), content_type='application/json')
        with (patch.object(learning_plan, '_all_modules', return_value=[]),
              patch.object(learning_plan, 'learner_programme_modules', return_value=[]),
              patch.object(learning_plan, 'learner_group_module_ids', return_value=[]),
              patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')),
              patch.object(learning_plan, 'sync_learning_plan_mirror') as mirror,
              patch.object(learning_plan, 'advance_learner'),
              patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner', subject_type='learner', subject_id=self.source.pk))):
            response = learning_plan.learning_plan(request, pk=self.source.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(json.loads(response.content)['saved'])
        self.source.refresh_from_db()
        self.assertEqual([m['moduleId'] for m in self.source.learning_plan], [o['module_catalogue_id'] for o in offers])
        self.assertTrue(all(m['assignmentSource'] == fixed.SOURCE for m in self.source.learning_plan))
        mirror.assert_called_once()

    def test_unsaved_preview_does_not_authorize_unverified_offering_ids(self):
        self.historical_unsaved()
        request = self.factory.patch('/learning-plan/4101/', json.dumps({'modules': ['unverified-other-cohort-offering']}), content_type='application/json')
        with (patch.object(learning_plan, '_all_modules', return_value=[]),
              patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner', subject_type='learner', subject_id=self.source.pk))):
            response = learning_plan.learning_plan(request, pk=self.source.pk)
        self.assertEqual(response.status_code, 400, response.content)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)

    def save_preview(self, token, role='admin', payload=None, csrf=True):
        request = self.factory.post('/learning-plan/4101/', json.dumps(payload or {
            'action': 'save-confirmed-programme', 'previewToken': token}), content_type='application/json')
        request._dont_enforce_csrf_checks = csrf
        account = SimpleNamespace(role=role, subject_type='staff' if role == 'admin' else 'learner', subject_id=self.source.pk)
        request.login_account = account
        with (patch('login.permissions.authenticate_request', return_value=account),
              patch.object(learning_plan, '_all_modules', return_value=[]),
              patch.object(learning_plan, '_meetings', return_value=([], 'no-dates'))):
            return learning_plan.learning_plan(request, pk=self.source.pk)

    def prepared_token(self):
        self.source.refresh_from_db()
        with patch.object(learning_plan, '_all_modules', return_value=[]), patch.object(learning_plan, '_meetings', return_value=([], 'no-dates')):
            response = learning_plan._serialize(self.source)
        self.assertFalse(response['saved'])
        self.assertTrue(response['confirmedPlanPreviewToken'])
        return response['confirmedPlanPreviewToken'], response['plan']

    def test_admin_can_save_prepared_preview_without_status_or_other_side_effects(self):
        self.historical_unsaved()
        token, plan = self.prepared_token()
        before = copy.deepcopy(EnrolmentUser.all_learners.values().get(pk=self.source.pk))
        with patch.object(learning_plan, 'sync_learning_plan_mirror') as mirror, patch.object(learning_plan, 'advance_learner') as advance:
            response = self.save_preview(token)
        self.assertEqual(response.status_code, 200, response.content)
        result = json.loads(response.content)
        self.assertTrue(result['saved'])
        self.assertIsNone(result['confirmedPlanPreviewToken'])
        self.assertEqual(result['plan'], plan)
        mirror.assert_not_called()
        advance.assert_not_called()
        after = EnrolmentUser.all_learners.values().get(pk=self.source.pk)
        self.assertEqual({k: v for k, v in before.items() if k != 'learning_plan'},
                         {k: v for k, v in after.items() if k != 'learning_plan'})
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.learning_plan)

    def test_save_prepared_preview_retry_is_idempotent_and_refresh_is_saved(self):
        self.historical_unsaved()
        token, plan = self.prepared_token()
        self.assertEqual(self.save_preview(token).status_code, 200)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.save_preview(token).status_code, 200)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, plan)
        self.assertIsNone(fixed.preview_uninitialised_fixed_plan(self.source))

    def test_save_prepared_preview_rejects_changes_since_display(self):
        self.historical_unsaved()
        token, _ = self.prepared_token()
        offers = self.offerings(CURRICULUM_PROGRAMME_IDS['ME_L4'], 'home-ME_L4')
        offers[0]['total_otjh'] = Decimal('900')
        self.read.side_effect = None
        self.read.return_value = offers
        self.assertEqual(self.save_preview(token).status_code, 409)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)

    def test_save_prepared_preview_does_not_overwrite_a_plan_saved_by_another_admin(self):
        self.historical_unsaved()
        token, _ = self.prepared_token()
        manual = [{'moduleId': 'admin-module', 'hours': 17}]
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=manual)
        self.assertEqual(self.save_preview(token).status_code, 409)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, manual)

    def test_save_prepared_preview_protects_later_manual_edits_even_on_retry(self):
        self.historical_unsaved()
        token, plan = self.prepared_token()
        self.assertEqual(self.save_preview(token).status_code, 200)
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=plan[:-1])
        self.assertEqual(self.save_preview(token).status_code, 409)
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, plan[:-1])

    def test_save_prepared_preview_requires_staff_csrf_and_token_only(self):
        self.historical_unsaved()
        token, _ = self.prepared_token()
        self.assertEqual(self.save_preview(token, role='learner').status_code, 403)
        self.assertEqual(self.save_preview(token, csrf=False).status_code, 403)
        self.assertEqual(self.save_preview(token, payload={'action': 'save-confirmed-programme', 'previewToken': token, 'modules': []}).status_code, 400)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)

    def test_save_prepared_preview_cannot_bypass_pcp_cohort_confirmation(self):
        row = self.historical_unsaved()
        token, _ = self.prepared_token()
        row.selected_programme = 'PCP_L6'
        row.save()
        self.assertEqual(self.save_preview(token).status_code, 409)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)

    def test_save_prepared_preview_rolls_back_if_final_write_fails(self):
        self.historical_unsaved()
        token, _ = self.prepared_token()
        original = Assessment.save
        def fail_review(instance, *args, **kwargs):
            if (instance.review or {}).get('fixedLearningPlan', {}).get('source') == 'admin-saved-preview':
                raise DatabaseError('isolated transaction failure')
            return original(instance, *args, **kwargs)
        with patch.object(Assessment, 'save', fail_review):
            self.assertEqual(self.save_preview(token).status_code, 503)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.learning_plan)
