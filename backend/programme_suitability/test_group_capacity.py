import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from learner_api.models import EnrolmentUser
from .models import Assessment, CohortGroupAllocation, CohortGroupPool
from . import group_capacity as capacity
from .modules_rules import available_modules


class GroupCapacityTests(TestCase):
    def setUp(self):
        self.config = json.loads(Path(__file__).with_name('modules_defaults.json').read_text(encoding='utf-8'))
        self.modules = self.config['programmes'][0]['modules']
        self.source = EnrolmentUser.all_learners.using('default').create(id=9101, programme='Marketing Executive Level 4', cohort='Feb 2027')
        self.row = self.learner(9101)
        self.pool = CohortGroupPool.objects.create(programme_id='PROG-ME-L4', cohort_id='home',
            verified_members={'g1': [], 'g2': []}, verified_at=timezone.now(), verification_evidence='Isolated verified roster fixture')
        self.groups = [dict(group_id=f'g{i}', group_name=f'Group {i}', programme_id='PROG-ME-L4', cohort_id='home') for i in (1, 2)]
        self.offerings = [dict(module_catalogue_id=f'{g["group_id"]}-{m["id"]}', programme_id='PROG-ME-L4',
            cohort_id='home', cohort_name='Feb 2027', group_id=g['group_id'], group_name=g['group_name'],
            title=m['title'], start_date='2027-02-01', end_date='2027-06-20', cohort_status='planned')
            for g in self.groups for m in self.modules]
        self.addCleanup(patch.stopall)
        self.cohorts = patch.object(capacity.lms_readonly, 'get_programme_cohorts', return_value=[dict(
            programme_id='PROG-ME-L4', cohort_id='home', cohort_name='Feb 2027')]).start()
        self.group_read = patch.object(capacity.lms_readonly, 'get_cohort_groups', return_value=self.groups).start()
        self.schedule_read = patch('programme_suitability.module_schedules.read_all', return_value=self.offerings).start()

    def learner(self, identifier):
        if identifier != 9101:
            EnrolmentUser.all_learners.using('default').create(id=identifier, programme='Marketing Executive Level 4', cohort='Feb 2027')
        return Assessment.objects.create(learner_id=identifier, learner_kind='apprenticeship', configuration_version=1,
            configuration={}, pathway='marketing', selected_programme='ME_L4', selection_confirmed_at=timezone.now(),
            modules_configuration=self.config, modules_configuration_version=24,
            review={'programmeAssignment': {'cohortId': 'home', 'cohortName': 'Feb 2027'}})

    def allocate(self, learner_id=9101, revision=0):
        return capacity.allocate('apprenticeship', learner_id, revision)

    def test_viewing_unassigned_group_one_eligibility_never_reserves_a_seat(self):
        home = dict(status='resolved', cohortId='home')
        with CaptureQueriesContext(connection) as queries:
            result = capacity.preview_home_group(self.row, home)
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['groupId'], 'g1')
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))

    def test_twentieth_confirmed_learner_gets_last_g1_seat_then_twenty_first_g2(self):
        self.pool.verified_members = {'g1': list(range(1, 20)), 'g2': []}
        self.pool.save()
        self.assertEqual(self.allocate()['groupId'], 'g1')
        self.assertEqual(CohortGroupAllocation.objects.get().seat, 20)
        self.learner(9102)
        self.assertEqual(self.allocate(9102)['groupId'], 'g2')

    def test_repeat_confirmation_returns_original_and_never_writes_learner_or_plan(self):
        before = copy.deepcopy(self.source.__dict__)
        with CaptureQueriesContext(connection) as queries:
            first = self.allocate()
            again = self.allocate()
        self.assertEqual(first, again)
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)
        self.source.refresh_from_db(using='default')
        for key in ('programme', 'cohort', 'group', 'learning_plan', 'training_plan'):
            self.assertEqual(getattr(self.source, key), before[key])
        for q in queries:
            if q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')):
                self.assertIn('cohort_group_allocations', q['sql'])

    def test_previously_verified_g1_member_stays_g1_when_it_is_full(self):
        self.pool.verified_members = {'g1': [9101, *range(1, 20)], 'g2': []}
        self.pool.save()
        self.source.group = 'Group 1'
        self.source.save(using='default')
        self.assertEqual(self.allocate()['groupId'], 'g1')
        self.assertEqual(CohortGroupAllocation.objects.get().seat, 1)
        self.assertEqual(self.allocate()['groupId'], 'g1')

    def test_saved_group_remains_visible_before_migration_or_roster_verification(self):
        home = dict(status='resolved', source='lms', cohortId='home', groupId='g1', groupName='Group 1')
        with patch.object(capacity, 'choose', side_effect=OperationalError('fixture missing migration')) as choose:
            result = capacity.preview_home_group(self.row, home)
        self.assertEqual(result['status'], 'assigned')
        self.assertEqual(result['groupId'], 'g1')
        choose.assert_not_called()

    def test_missing_migration_is_clear_unavailable_without_erroring_the_timeline(self):
        with patch.object(capacity, 'choose', side_effect=OperationalError('fixture missing migration')):
            result = capacity.preview_home_group(self.row, dict(status='resolved', cohortId='home'))
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['code'], 'capacity_preparation_pending')
        self.assertIsNone(result['groupId'])

    def test_missing_roster_never_assumes_empty_memberships(self):
        self.pool.verified_members = None
        self.pool.save()
        with self.assertRaises(capacity.CapacityUnavailable) as error:
            self.allocate()
        self.assertEqual(error.exception.code, 'memberships_unverified')
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_g2_missing_or_incomplete_never_uses_other_cohort(self):
        self.pool.verified_members = {'g1': list(range(1, 21))}
        self.pool.save()
        self.group_read.return_value = self.groups[:1]
        with self.assertRaises(capacity.CapacityUnavailable):
            self.allocate()
        self.pool.verified_members['g2'] = []
        self.pool.save()
        self.group_read.return_value = self.groups
        self.schedule_read.return_value = [o for o in self.offerings if o['group_id'] == 'g1']
        self.schedule_read.return_value += [{**o, 'cohort_id': 'another-cohort'} for o in self.offerings if o['group_id'] == 'g2']
        with self.assertRaises(capacity.CapacityUnavailable) as error:
            self.allocate()
        self.assertEqual(error.exception.code, 'schedule_incomplete')
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_incomplete_g1_does_not_skip_to_g2(self):
        self.schedule_read.return_value = self.offerings[1:]
        with self.assertRaises(capacity.CapacityUnavailable) as error:
            self.allocate()
        self.assertEqual(error.exception.code, 'schedule_incomplete')

    def test_duplicate_delivery_missing_dates_and_tentative_names_cannot_authorize_allocation(self):
        alternatives = [
            self.offerings + [{**self.offerings[0], 'module_catalogue_id': 'another-id'}],
            [{**o, 'start_date': None} for o in self.offerings],
            [{**o, 'title': 'Unverified alternative title'} for o in self.offerings],
        ]
        for offerings in alternatives:
            self.schedule_read.return_value = offerings
            with self.assertRaises(capacity.CapacityUnavailable):
                self.allocate()
        self.assertFalse(CohortGroupAllocation.objects.exists())

    def test_conflicting_cohort_id_is_not_silently_replaced_by_equal_label(self):
        self.row.review['programmeAssignment']['cohortId'] = 'old-unresolved-id'
        self.row.save()
        with self.assertRaises(capacity.CapacityUnavailable) as error:
            self.allocate()
        self.assertEqual(error.exception.code, 'cohort_identity_unresolved')

    def test_unique_learner_seat_and_database_capacity_constraints(self):
        self.allocate()
        record = CohortGroupAllocation.objects.get()
        values = {f.attname: getattr(record, f.attname) for f in record._meta.concrete_fields if f.name not in ('id', 'created_at')}
        for changes in ({}, {'learner_id': 9991}, {'learner_id': 9991, 'seat': 21}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                CohortGroupAllocation.objects.create(**{**values, **changes})

    def test_failed_insert_rolls_back_and_does_not_consume_a_place(self):
        with patch('django.db.models.query.QuerySet.create', side_effect=IntegrityError('fixture insert failure')):
            with self.assertRaises(IntegrityError):
                self.allocate()
        self.assertFalse(CohortGroupAllocation.objects.exists())
        self.assertEqual(self.allocate()['groupId'], 'g1')

    def test_all_forty_seats_then_no_more_capacity(self):
        for offset in range(40):
            if offset:
                self.learner(9101 + offset)
            self.assertEqual(self.allocate(9101 + offset)['groupId'], 'g1' if offset < 20 else 'g2')
        self.learner(9141)
        with self.assertRaises(capacity.CapacityUnavailable):
            self.allocate(9141)
        self.assertEqual(CohortGroupAllocation.objects.filter(group_id='g1').count(), 20)
        self.assertEqual(CohortGroupAllocation.objects.filter(group_id='g2').count(), 20)

    def test_stale_revision_and_existing_allocation_changes_are_blocked(self):
        with self.assertRaises(capacity.CapacityUnavailable):
            self.allocate(revision=99)
        self.allocate()
        self.row.modules_configuration['programmes'][0]['modules'].pop()
        self.row.save()
        with self.assertRaises(capacity.CapacityUnavailable) as error:
            self.allocate()
        self.assertEqual(error.exception.code, 'allocation_locked')
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)

    def test_pcp_active_pathway_only_and_confirmed_bundle_validation(self):
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == 'chartered')
        modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.source.programme = 'Project Controls Professional Level 6'
        self.source.save(using='default')
        self.row.selected_programme = 'PCP_L6'
        self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 24,
            'modulesByPathway': {'chartered': [m['id'] for m in modules], 'operational': []}}
        self.row.save()
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        self.cohorts.return_value[0]['programme_id'] = 'PROG-PCP-L6'
        self.group_read.return_value = [{**g, 'programme_id': 'PROG-PCP-L6'} for g in self.groups]
        self.schedule_read.return_value = [{**self.offerings[0], 'programme_id': 'PROG-PCP-L6',
            'title': m['title'], 'module_catalogue_id': m['id']} for m in modules]
        self.assertEqual(self.allocate()['groupId'], 'g1')
        self.assertEqual(CohortGroupAllocation.objects.get().academic_ids, [m['id'] for m in modules])
        self.assertEqual(len(CohortGroupAllocation.objects.get().offerings), 6)

    def test_overlapping_confirmations_use_isolated_file_database_and_cannot_overbook(self):
        # A separate process avoids changing any test-runner aliases or touching
        # live credentials. Two actual SQLite connections overlap and retry a
        # lock loser; PostgreSQL additionally serializes on select_for_update.
        script = Path(__file__).with_name('capacity_concurrency_fixture.py')
        result = subprocess.run([sys.executable, str(script)], cwd=script.parents[1],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, 'DJANGO_SETTINGS_MODULE': 'programme_suitability.test_settings'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['groups'], ['g1', 'g2'])
