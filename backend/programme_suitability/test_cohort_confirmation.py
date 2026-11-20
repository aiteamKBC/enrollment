import copy
import json
from pathlib import Path
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from .cohort_confirmation import cohort_module_confirmation
from .models import CohortGroupAllocation
from .modules_rules import available_modules
from .test_group_capacity import GroupCapacityTests


class CohortConfirmationTests(TestCase):
    learner = GroupCapacityTests.learner

    def setUp(self):
        GroupCapacityTests.setUp(self)
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == 'chartered')
        self.modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.source.programme = 'Project Controls Professional Level 6'
        self.source.save(using='default')
        self.row.selected_programme = 'PCP_L6'
        self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 24,
            'modulesByPathway': {'chartered': [m['id'] for m in self.modules], 'operational': []}}
        self.row.save()
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        self.cohorts.return_value[0]['programme_id'] = 'PROG-PCP-L6'
        self.group_read.return_value = [{**g, 'programme_id': 'PROG-PCP-L6'} for g in self.groups]
        self.schedule_read.return_value = [{**self.offerings[0], **g, 'programme_id': 'PROG-PCP-L6',
            'title': m['title'], 'module_catalogue_id': f'{g["group_id"]}-{m["id"]}'} for g in self.groups for m in self.modules]

    def call(self, method='get', data=None, identifier=9101, role='learner', csrf=True):
        factory = RequestFactory()
        request = factory.get('/cohort-module-confirmation/') if method == 'get' else getattr(factory, method)(
            '/cohort-module-confirmation/', data=json.dumps(data or {'expectedRevision': 0}), content_type='application/json')
        request._dont_enforce_csrf_checks = csrf
        request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = SimpleNamespace(is_active=True, role=role, subject_id=identifier)
        with patch('enrolment_api.auth.auth_required', return_value=False):
            return cohort_module_confirmation(request, kind='apprenticeship', learner_id=identifier)

    def test_success_refresh_retry_and_atomic_plan_writes(self):
        before_source = copy.deepcopy(self.source.__dict__)
        before_row = copy.deepcopy(self.row.__dict__)
        with CaptureQueriesContext(connection) as queries:
            ready = self.call()
            first = self.call('put')
            again = self.call('put')
            refreshed = self.call()
        self.assertEqual(json.loads(ready.content)['status'], 'ready')
        self.assertEqual(first.status_code, 200)
        result = json.loads(first.content)
        self.assertEqual(result['status'], 'allocated')
        self.assertEqual(result['groupId'], 'g1')
        self.assertEqual(len(result['offerings']), 6)
        self.assertTrue(all(o['cohortId'] == 'home' and o['groupId'] == 'g1' for o in result['offerings']))
        self.assertEqual(first.content, again.content)
        self.assertEqual(first.content, refreshed.content)
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)
        self.source.refresh_from_db(using='default')
        self.row.refresh_from_db()
        for key, value in before_source.items():
            if key not in ('_state', 'learning_plan'):
                self.assertEqual(getattr(self.source, key), value)
        for key, value in before_row.items():
            if key not in ('_state', 'review'):
                self.assertEqual(getattr(self.row, key), value)
        for query in queries:
            if query['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')):
                self.assertTrue(any(table in query['sql'] for table in ('cohort_group_allocations', 'Created_users', 'programme_suitability_assessments')), query['sql'])

    def test_pcp_first_twenty_receive_g1_twenty_first_g2_and_readiness_does_not_reserve(self):
        for offset in range(21):
            identifier = 9101 + offset
            if offset:
                self.row.pk = None
                self.row.learner_id = identifier
                self.row.save(force_insert=True)
                self.source.pk = identifier
                self.source.uuid = uuid.uuid4()
                self.source.save(using='default', force_insert=True)
            self.assertEqual(self.call(identifier=identifier).status_code, 200)
            self.assertEqual(CohortGroupAllocation.objects.count(), offset)
            result = self.call('put', identifier=identifier)
            self.assertEqual(result.status_code, 200, result.content)
            self.assertEqual(json.loads(result.content)['groupId'], 'g1' if offset < 20 else 'g2')

    def test_group_one_fills_between_availability_and_click(self):
        self.assertEqual(self.call().status_code, 200)
        self.pool.verified_members = {'g1': list(range(1, 21)), 'g2': []}
        self.pool.save()
        self.assertEqual(json.loads(self.call('put').content)['groupId'], 'g2')

    def test_missing_or_incomplete_group_two_never_saves_partial_confirmation(self):
        self.pool.verified_members = {'g1': list(range(1, 21)), 'g2': []}
        self.pool.save()
        self.schedule_read.return_value = self.schedule_read.return_value[:-1]
        response = self.call('put')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['code'], 'schedule_incomplete')
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.group_read.return_value = self.group_read.return_value[:1]
        self.pool.verified_members.pop('g2')
        self.pool.save()
        self.assertEqual(self.call('put').status_code, 409)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_incomplete_inactive_or_stale_choices_cannot_confirm(self):
        self.assertEqual(self.call('put', {'expectedRevision': 99}).status_code, 409)
        self.row.modules_selection['modulesByPathway']['chartered'].remove('portfolio')
        self.row.save()
        self.assertEqual(self.call('put').status_code, 409)
        path = next(p for p in self.row.modules_configuration['programmes'] if p['id'] == 'PCP_L6')['pathways']
        next(p for p in path if p['id'] == 'chartered')['active'] = False
        self.row.save()
        self.assertEqual(self.call('put').status_code, 409)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_client_cannot_choose_group_delivery_or_other_cohort(self):
        self.assertEqual(self.call('put', {'expectedRevision': 0, 'groupId': 'g2'}).status_code, 400)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_fixed_programmes_and_nonlearners_cannot_confirm(self):
        for programme in ('ME_L4', 'MM_L6', 'APM_L4'):
            self.row.selected_programme = programme
            self.row.save()
            self.assertEqual(self.call('put').status_code, 409)
        self.assertEqual(self.call('put', role='admin').status_code, 403)
        self.assertEqual(self.call('put', role='employer').status_code, 403)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_ownership_and_csrf_are_enforced(self):
        request = RequestFactory().put('/cohort-module-confirmation/')
        request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = SimpleNamespace(is_active=True, role='learner', subject_id=9999)
        with patch('enrolment_api.auth.auth_required', return_value=True):
            self.assertEqual(cohort_module_confirmation(request, kind='apprenticeship', learner_id=9101).status_code, 404)
        self.assertEqual(self.call('put', csrf=False).status_code, 403)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_missing_migration_or_roster_is_unavailable_without_writes(self):
        with patch('programme_suitability.group_capacity.CohortGroupAllocation.objects.using', side_effect=OperationalError('fixture missing table')):
            response = self.call('put')
        self.assertEqual(response.status_code, 503)
        self.pool.verified_members = None
        self.pool.save()
        self.assertEqual(self.call('put').status_code, 409)
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_persisted_confirmation_loads_when_lms_is_unavailable(self):
        self.assertEqual(self.call('put').status_code, 200)
        from enrollment_service.lms_readonly import LmsReadUnavailable
        self.cohorts.side_effect = LmsReadUnavailable('isolated outage')
        self.assertEqual(json.loads(self.call().content)['status'], 'allocated')

    def test_concurrent_pcp_confirmations_use_the_shared_capacity_service(self):
        script = Path(__file__).with_name('capacity_concurrency_fixture.py')
        result = subprocess.run([sys.executable, str(script), '--pcp'], cwd=script.parents[1],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'programme_suitability.test_settings'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['groups'], ['g1', 'g2'])

    def plan(self):
        self.source.refresh_from_db(using='default')
        from learner_api.mappers import stored_training_plan
        return stored_training_plan(self.source)

    def test_plan_is_absent_until_confirmation_and_contains_only_active_home_bundle(self):
        self.row.modules_selection['modulesByPathway']['operational'] = ['pmp']
        self.row.save()
        self.schedule_read.return_value += [{**o, 'cohort_id': 'other', 'module_catalogue_id': 'other-' + o['module_catalogue_id']}
            for o in self.schedule_read.return_value[:6]]
        self.assertIsNone(self.plan())
        self.assertEqual(self.call().status_code, 200)
        self.assertIsNone(self.plan())
        self.assertEqual(self.call('put').status_code, 200)
        plan = self.plan()
        self.assertEqual({m['academicModuleId'] for m in plan}, {m['id'] for m in self.modules})
        self.assertEqual(len(plan), 6)
        self.assertTrue(all(m['cohortId'] == 'home' and m['groupId'] == 'g1' and
            m['moduleId'] == m['lmsOfferingId'] and m['startDate'] == '2027-02-01' and
            m['endDate'] == '2027-06-20' and m['pathway'] == 'chartered' for m in plan))
        self.assertEqual(self.source.cohort, 'Feb 2027')
        self.assertFalse(self.source.group)  # Allocation must not rewrite the saved learner placement.
        from learner_api.models import LearnerProfile
        self.assertFalse(LearnerProfile.objects.exists())

    def test_operational_and_strategic_use_only_their_valid_selected_modules(self):
        from itertools import combinations
        from .modules_rules import validate_active_selection
        from .configuration import ConfigurationError
        from learner_api.models import EnrolmentUser
        for pathway in ('operational', 'strategic'):
            with self.subTest(pathway=pathway):
                CohortGroupAllocation.objects.all().delete()
                EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=None)
                programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
                path = next(p for p in programme['pathways'] if p['id'] == pathway)
                modules = available_modules(path['modules'])
                mandatory = [m for m in modules if m.get('isMandatory')]
                optional = [m for m in modules if not m.get('isMandatory')]
                for choice in combinations(optional, path['requiredOptionalModulesCount']):
                    selected = mandatory + list(choice)
                    try:
                        validate_active_selection(path, [m['id'] for m in selected], complete=True)
                        break
                    except ConfigurationError:
                        continue
                self.row.review.pop('pcpLearningPlan', None)
                self.row.modules_selection['pathway'] = pathway
                self.row.modules_selection['modulesByPathway'][pathway] = [m['id'] for m in selected]
                self.row.save()
                self.schedule_read.return_value = [{**self.offerings[0], **g, 'programme_id': 'PROG-PCP-L6',
                    'title': m['title'], 'module_catalogue_id': f'{g["group_id"]}-{m["id"]}'} for g in self.groups for m in selected]
                response = self.call('put')
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual({m['academicModuleId'] for m in self.plan()}, {m['id'] for m in selected})
                self.assertTrue(all(m['pathway'] == pathway for m in self.plan()))
                self.row.refresh_from_db()

    def test_existing_manual_or_explicitly_cleared_plans_block_new_allocation(self):
        from learner_api.models import EnrolmentUser
        for plan in ([], [{'moduleId': 'manual', 'moduleTitle': 'Admin customised', 'hours': 12}]):
            EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=plan)
            response = self.call('put')
            self.assertEqual(response.status_code, 409)
            self.assertEqual(json.loads(response.content)['code'], 'learning_plan_conflict')
            self.assertEqual(self.plan(), plan)
            self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_manual_edits_after_confirmation_are_preserved_on_retry(self):
        from learner_api.models import EnrolmentUser
        self.assertEqual(self.call('put').status_code, 200)
        plan = self.plan()[:-1]
        EnrolmentUser.all_learners.filter(pk=self.source.pk).update(learning_plan=plan)
        self.assertEqual(self.call('put').status_code, 409)
        self.assertEqual(self.plan(), plan)
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)
        self.assertEqual(CohortGroupAllocation.objects.get().group_id, 'g1')

    def test_failure_saving_plan_rolls_back_new_allocation(self):
        with patch('programme_suitability.pcp_learning_plan.write_plan', side_effect=OperationalError('isolated write failure')):
            self.assertEqual(self.call('put').status_code, 503)
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.assertIsNone(self.plan())
        self.assertEqual(self.call('put').status_code, 200)
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)

    def test_existing_profile_and_its_custom_plan_are_respected(self):
        from learner_api.models import LearnerProfile
        profile = LearnerProfile.objects.create(enrolment_id=self.source.pk, programme_id='PROG-PCP-L6',
            cohort_id='home', cohort='Feb 2027', learning_plan=[{'moduleId': 'approved-plan'}])
        self.assertEqual(self.call('put').status_code, 409)
        self.assertFalse(CohortGroupAllocation.objects.exists())
        profile.learning_plan = None
        profile.save()
        self.assertEqual(self.call('put').status_code, 200)
        profile.refresh_from_db()
        self.assertIsNone(profile.learning_plan)
        self.assertEqual(len(self.plan()), 6)

    def test_hours_and_existing_serializer_use_exact_confirmed_offerings(self):
        from learner_api import learning_plan
        self.schedule_read.return_value = [{**o, 'total_otjh': '105.75'} for o in self.schedule_read.return_value]
        self.assertEqual(self.call('put').status_code, 200)
        plan = self.plan()
        self.assertTrue(all(m['hours'] == 105.75 and not m['hoursPending'] for m in plan))
        with patch.object(learning_plan, '_all_modules', return_value=[]), patch.object(learning_plan, '_meetings', return_value=([], '')):
            result = learning_plan._serialize(self.source)
        self.assertTrue(result['saved'])
        self.assertEqual(result['plan'], plan)
        self.assertEqual(result['learner']['group'], 'Group 1')
        self.assertEqual(result['totals']['totalHours'], 634.5)
        self.assertEqual(result['preset'], [])

    def test_issued_signed_training_plan_snapshot_is_byte_for_byte_untouched(self):
        from django.core.serializers.json import DjangoJSONEncoder
        from learner_api.models import TrainingPlanDocument
        # Real document model, isolated schema only; no document issuance/PDF hooks.
        editor = connection.schema_editor(collect_sql=True, atomic=False)
        editor.deferred_sql = []
        editor.create_model(TrainingPlanDocument)
        with connection.cursor() as cursor:
            for sql in editor.collected_sql:
                cursor.execute(sql)
        document = TrainingPlanDocument.objects.create(learner_kind='apprenticeship', learner_id=self.source.pk,
            learning_plan=[{'frozen': 'issued original'}], fully_signed=True,
            apprentice_signature='synthetic signed bytes', employer_signature='synthetic employer', provider_signature='synthetic provider')
        before = json.dumps(TrainingPlanDocument.objects.values().get(pk=document.pk), cls=DjangoJSONEncoder, sort_keys=True).encode()
        self.assertEqual(self.call('put').status_code, 200)
        after = json.dumps(TrainingPlanDocument.objects.values().get(pk=document.pk), cls=DjangoJSONEncoder, sort_keys=True).encode()
        self.assertEqual(before, after)

    def test_retry_and_get_do_not_write_or_consume_another_seat(self):
        self.assertEqual(self.call('put').status_code, 200)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.call('put').status_code, 200)
            self.assertEqual(self.call().status_code, 200)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)

    def test_failure_after_plan_write_rolls_back_both_plan_and_allocation(self):
        from .models import Assessment
        original = Assessment.save
        def fail_review(instance, *args, **kwargs):
            if 'pcpLearningPlan' in (instance.review or {}):
                raise OperationalError('isolated audit save failure')
            return original(instance, *args, **kwargs)
        with patch.object(Assessment, 'save', fail_review):
            self.assertEqual(self.call('put').status_code, 503)
        self.assertIsNone(self.plan())
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.row.refresh_from_db()
        self.assertNotIn('pcpLearningPlan', self.row.review)
