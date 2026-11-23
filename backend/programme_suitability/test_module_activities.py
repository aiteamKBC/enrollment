"""Isolated learner-specific activities; no real connections or data writes."""
import copy
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from learner_api import module_activities as activities
from learner_api.models import EnrolmentUser
from . import test_group_capacity as fixtures
from .models import CohortGroupAllocation
from .programme_identity import CURRICULUM_PROGRAMME_IDS
from .modules_rules import available_modules


def raw_content(mid, pid, cid, gid):
    return {'modules': [{'module_catalogue_id': mid, 'programme_id': pid, 'cohort_id': cid,
        'group_id': gid, 'title': 'Real fixture LMS title', 'description': 'Authored description', 'ksb_profile_source_id': 'profile'}],
        'weeks': [dict(id='later', module_catalogue_id=mid, display_order=2, week_number=1, title='Later authored section'),
                  dict(id='earlier', module_catalogue_id=mid, display_order=1, week_number=2, title='Earlier authored section')],
        'components': [
            dict(id='assignment', module_catalogue_id=mid, week_id='earlier', display_order=2,
                 type='assignment', title='Synthetic evidence exercise', expected_otjh=Decimal('8'),
                 settings_json={'assignmentBrief': 'The existing brief', 'dueTiming': 'End of week',
                    'assignmentTopics': json.dumps([{'id': '2', 'name': 'Second topic', 'question': 'Second recorded question'},
                                                   {'id': '1', 'name': 'First topic', 'instructions': 'Recorded instructions'}])},
                 ksb_mappings=[{'code': 'K1', 'description': 'Authored knowledge'}]),
            dict(id='topic', module_catalogue_id=mid, week_id='earlier', display_order=1,
                 type='reading', title='Synthetic recorded topic', expected_otjh=Decimal('2.5'),
                 settings_json={'readingContent': 'Existing learning content'}, ksb_mappings=[]),
            dict(id='zero', module_catalogue_id=mid, week_id='later', display_order=1,
                 type='topic', title='Zero-hour activity', expected_otjh=Decimal('0'), settings_json={}, ksb_mappings=[])],
        'ksb_mappings': [
            dict(id='mapping1', module_catalogue_id=mid, week_id='earlier', component_id='assignment',
                 ksb_code='K1', ksb_description='Authored knowledge', weight=Decimal('1')),
            dict(id='mapping2', module_catalogue_id=mid, week_id='earlier', component_id='topic',
                 ksb_code='K1', ksb_description='Authored knowledge', weight=Decimal('1')),
            dict(id='mapping3', module_catalogue_id=mid, week_id=None, component_id=None,
                 ksb_code='B2', ksb_description='Authored behaviour', weight=Decimal('0'))],
        'ksb_profiles': [{'id': 'profile', 'ksb_items': []}], 'ksb_definitions': [], 'blockers': []}


class ModuleActivitiesTests(TestCase):
    learner = fixtures.GroupCapacityTests.learner

    def setUp(self):
        fixtures.GroupCapacityTests.setUp(self)
        self.source.group = 'Group 1'
        self.source.save()
        self.read = patch.object(activities, 'get_module_activities', side_effect=raw_content).start()
        self.addCleanup(patch.stopall)
        self.configure('ME_L4')

    def configure(self, code, modules=None):
        self.source.programme = {'ME_L4': 'Marketing Executive Level 4', 'MM_L6': 'Marketing Manager Level 6',
            'APM_L4': 'Associate Project Manager Level 4', 'PCP_L6': 'Project Controls Professional Level 6'}[code]
        self.row.selected_programme = code
        programme = next(p for p in self.config['programmes'] if p['id'] == code)
        modules = programme.get('modules', []) if modules is None else modules
        self.plan = [dict(moduleId='offering-' + m['id'], lmsOfferingId='offering-' + m['id'],
            academicModuleId=m['id'], moduleTitle=m['title'], programmeId=CURRICULUM_PROGRAMME_IDS[code],
            cohortId='home', cohortName='Feb 2027', groupId='g1', groupName='Group 1',
            hours=99.75, hoursPending=False, startDate='2027-02-01', endDate='2027-05-31',
            assignmentSource='programme_suitability_pcp' if code == 'PCP_L6' else 'programme_suitability_fixed') for m in modules]
        self.source.learning_plan = self.plan
        self.source.save()
        self.row.save()
        return self.plan

    def load(self):
        return activities.learner_module_activities(self.source.pk)

    def test_deleted_module_replacement_is_shared_by_plan_and_activities_without_writes(self):
        from .module_replacements import resolve_saved_learning_plan
        from enrollment_service import lms_readonly
        old = self.plan[0]
        offer = dict(module_catalogue_id='replacement', programme_id=old['programmeId'], cohort_id='home',
            group_id='g1', cohort_name='Feb 2027', group_name='Group 1', cohort_status='planned',
            title=old['moduleTitle'], start_date='2027-02-02', end_date='2027-06-02', total_otjh=Decimal('140'))
        references = [dict(module_catalogue_id=e['moduleId'], programme_id=e['programmeId'],
            cohort_id='home', group_id='g1', deleted_at='2026-11-18' if e == old else None) for e in self.plan]
        def content(mid, pid, cid, gid):
            if mid == old['moduleId']:
                return {'modules': [], 'blockers': [activities.issue('module_placement_not_found', 'modules')]}
            return raw_content(mid, pid, cid, gid)
        self.read.side_effect = content
        with patch.object(lms_readonly, 'get_saved_module_references', return_value=references), \
                patch.object(lms_readonly, 'get_replacement_offerings', return_value=[offer]), \
                CaptureQueriesContext(connection) as queries:
            projected = resolve_saved_learning_plan(self.source, self.plan)
            result = self.load()
        self.assertEqual(projected[0]['moduleId'], 'replacement')
        self.assertEqual(result['status'], 'available', result['blockers'])
        module = result['modules'][0]
        self.assertEqual(module['replacesLmsOfferingId'], old['moduleId'])
        self.assertEqual(module['moduleTotalOtjHours'], 140)
        self.assertTrue(module['weeks'])
        self.source.refresh_from_db()
        self.assertEqual(self.source.learning_plan, self.plan)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_activities_permission_failure_never_triggers_replacement_discovery(self):
        from . import module_replacements
        self.read.return_value = {'modules': None, 'blockers': [activities.issue('select_denied', 'modules')]}
        self.read.side_effect = None
        with patch.object(module_replacements, 'resolve_saved_learning_plan') as resolve:
            self.load()
        resolve.assert_not_called()

    def test_pcp_replacement_preserves_original_confirmation_and_capacity(self):
        from .module_replacements import resolve_saved_learning_plan
        from enrollment_service import lms_readonly
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == 'chartered')
        modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.configure('PCP_L6', modules)
        self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 24,
            'modulesByPathway': {'chartered': [m['id'] for m in modules]}}
        self.row.save()
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        allocation = CohortGroupAllocation.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship', pool=self.pool,
            group_id='g1', group_name='Group 1', seat=1, pathway='chartered', academic_ids=[m['id'] for m in modules],
            configuration_version=24, selection_revision=0, offerings=[dict(academicModuleId=e['academicModuleId'],
                offeringId=e['lmsOfferingId'], groupId='g1', cohortId='home', start=e['startDate'], end=e['endDate']) for e in self.plan])
        before = copy.deepcopy(allocation.offerings)
        old = self.plan[0]
        references = [dict(module_catalogue_id=e['moduleId'], programme_id=e['programmeId'], cohort_id='home',
            group_id='g1', deleted_at='2026-11-18' if e == old else None) for e in self.plan]
        offer = dict(module_catalogue_id='new-pcp', programme_id='PROG-PCP-L6', cohort_id='home', group_id='g1',
            title=old['moduleTitle'], cohort_status='planned', start_date='2027-02-02', end_date='2027-06-02')
        with patch.object(lms_readonly, 'get_saved_module_references', return_value=references), \
                patch.object(lms_readonly, 'get_replacement_offerings', return_value=[offer]):
            result = resolve_saved_learning_plan(self.source, self.plan)
        self.assertEqual(result[0]['moduleId'], 'new-pcp')
        allocation.refresh_from_db()
        self.assertEqual(allocation.offerings, before)
        self.assertEqual((allocation.seat, allocation.group_id), (1, 'g1'))
        self.assertEqual(CohortGroupAllocation.objects.count(), 1)

    def test_exact_confirmed_modules_only_for_me_mm_apm_and_no_writes(self):
        for code in ('ME_L4', 'MM_L6', 'APM_L4'):
            self.configure(code)
            self.read.reset_mock()
            before = copy.deepcopy(EnrolmentUser.all_learners.values().get(pk=self.source.pk))
            with CaptureQueriesContext(connection) as queries:
                result = self.load()
            self.assertEqual(result['status'], 'available', result['blockers'])
            self.assertEqual(len(result['modules']), len(self.plan))
            self.assertEqual(self.read.call_count, len(self.plan))
            for call, entry in zip(self.read.call_args_list, self.plan):
                self.assertEqual(call.args, (entry['lmsOfferingId'], entry['programmeId'], 'home', 'g1'))
            self.assertEqual(before, EnrolmentUser.all_learners.values().get(pk=self.source.pk))
            self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_order_assignment_topics_and_hours_are_not_duplicated(self):
        result = self.load()['modules'][0]
        self.assertEqual(result['moduleTotalOtjHours'], 99.75)
        self.assertEqual([w['sourceId'] for w in result['weeks']], ['earlier', 'later'])
        self.assertEqual([c['sourceId'] for c in result['weeks'][0]['components']], ['topic', 'assignment'])
        assignment = result['weeks'][0]['components'][1]
        self.assertEqual(assignment['activityOtjHours'], 8)
        self.assertEqual(assignment['assignment']['assignmentBrief'], 'The existing brief')
        self.assertEqual(assignment['timing'], {'dueTiming': 'End of week'})
        self.assertEqual([t['id'] for t in assignment['topics']], ['1', '2'])
        self.assertTrue(all(t['activityOtjHours'] is None for t in assignment['topics']))
        self.assertEqual(result['weeks'][1]['components'][0]['activityOtjHours'], 0)

    def test_coverage_is_deduplicated_but_relationships_and_descriptions_remain(self):
        module = self.load()['modules'][0]
        self.assertEqual({k['code'] for k in module['coveredKsbs']}, {'K1', 'B2'})
        knowledge = next(k for k in module['coveredKsbs'] if k['code'] == 'K1')
        self.assertEqual(len(knowledge['sources']), 3)
        self.assertEqual({s['componentId'] for s in knowledge['sources']}, {'assignment', 'topic'})
        self.assertEqual(module['weeks'][0]['components'][0]['coveredKsbs'][0]['code'], 'K1')

    def test_missing_permission_is_partial_not_an_empty_success(self):
        def denied(mid, pid, cid, gid):
            raw = raw_content(mid, pid, cid, gid)
            raw.update(weeks=None, components=None, ksb_mappings=None,
                blockers=[{'database': 'LMS', 'schema': 'curriculum', 'table': 'components', 'code': 'select_denied', 'columns': ['expected_otjh']}])
            return raw
        self.read.side_effect = denied
        result = self.load()
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['modules'][0]['weeks'])
        self.assertIsNone(result['modules'][0]['coveredKsbs'])
        self.assertTrue(any(b['table'] == 'components' and b['code'] == 'select_denied' for b in result['blockers']))

    def test_no_plan_or_pending_offering_never_discovers_modules_by_title(self):
        self.source.learning_plan = None
        self.source.save()
        self.assertEqual(self.load()['blockers'][0]['code'], 'persisted_learning_plan_required')
        self.read.assert_not_called()
        self.plan[0]['lmsOfferingId'] = None
        self.source.learning_plan = self.plan[:1]
        self.source.save()
        result = self.load()
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['blockers'][0]['code'], 'verified_lms_offering_required')
        self.read.assert_not_called()

    def test_other_cohort_and_duplicate_offering_ids_are_never_queried(self):
        self.plan[0]['cohortId'] = 'other-cohort'
        self.plan[1]['moduleId'] = self.plan[0]['moduleId']
        self.plan[1]['lmsOfferingId'] = self.plan[0]['lmsOfferingId']
        self.source.learning_plan = self.plan
        self.source.save()
        result = self.load()
        self.assertTrue(result['blockers'])
        self.assertTrue(all(call.args[0] not in {self.plan[0]['lmsOfferingId']} for call in self.read.call_args_list))

    def test_lms_identity_mismatch_and_orphan_components_are_reported(self):
        def wrong(mid, pid, cid, gid):
            raw = raw_content(mid, pid, cid, gid)
            raw['modules'][0]['cohort_id'] = 'other'
            return raw
        self.read.side_effect = wrong
        self.assertEqual(self.load()['status'], 'unavailable')
        def orphan(mid, pid, cid, gid):
            raw = raw_content(mid, pid, cid, gid)
            raw['components'][0]['week_id'] = 'foreign-module-week'
            return raw
        self.read.side_effect = orphan
        result = self.load()
        self.assertTrue(any(b['code'] == 'component_relationship_missing_or_ambiguous' for b in result['blockers']))
        self.assertFalse(any(c['sourceId'] == 'assignment' for m in result['modules'] for w in m['weeks'] for c in w['components']))

    def test_missing_hours_and_dates_are_not_invented_and_no_topic_fallback_is_created(self):
        def missing(mid, pid, cid, gid):
            raw = raw_content(mid, pid, cid, gid)
            raw['components'][0]['expected_otjh'] = None
            raw['components'][0]['settings_json'] = {'assignmentBrief': 'An authored standalone assignment'}
            return raw
        self.read.side_effect = missing
        module = self.load()['modules'][0]
        assignment = module['weeks'][0]['components'][1]
        self.assertIsNone(assignment['activityOtjHours'])
        self.assertIsNone(assignment['timing'])
        self.assertEqual(assignment['topics'], [])
        self.assertEqual(module['moduleTotalOtjHours'], 99.75)

    def test_pcp_requires_confirmed_allocation_and_excludes_other_pathway_modules(self):
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        path = next(p for p in programme['pathways'] if p['id'] == 'chartered')
        modules = [m for m in available_modules(path['modules']) if m.get('isMandatory') or m['id'] == 'portfolio']
        self.configure('PCP_L6', modules)
        self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'configurationVersion': 24,
            'modulesByPathway': {'chartered': [m['id'] for m in modules], 'operational': ['pmp']}}
        self.row.save()
        self.assertEqual(self.load()['blockers'][0]['code'], 'persisted_pcp_home_allocation_required')
        self.read.assert_not_called()
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        allocation = CohortGroupAllocation.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship', pool=self.pool,
            group_id='g1', group_name='Group 1', seat=1, pathway='chartered', academic_ids=[m['id'] for m in modules],
            configuration_version=24, selection_revision=0, offerings=[dict(academicModuleId=e['academicModuleId'],
                offeringId=e['lmsOfferingId'], groupId='g1', cohortId='home', start=e['startDate'], end=e['endDate']) for e in self.plan])
        result = self.load()
        self.assertEqual(result['status'], 'available', result['blockers'])
        self.assertEqual({m['academicModuleId'] for m in result['modules']}, {m['id'] for m in modules})
        self.assertNotIn('pmp', {m['academicModuleId'] for m in result['modules']})
        self.source.learning_plan = self.plan + [dict(self.plan[0], academicModuleId='pmp')]
        self.source.save()
        self.read.reset_mock()
        self.assertEqual(self.load()['status'], 'unavailable')
        self.read.assert_not_called()
        allocation.refresh_from_db()
        self.assertEqual((allocation.group_id, allocation.seat), ('g1', 1))

    def test_view_is_get_only_scoped_and_does_not_accept_module_overrides(self):
        request = RequestFactory().get('/activities/?modules=foreign&cohort=other')
        request.login_account = SimpleNamespace(is_active=True, role='learner', subject_id=999)
        request.user = SimpleNamespace(is_authenticated=False)
        with patch('enrolment_api.auth.auth_required', return_value=True):
            self.assertEqual(activities.learner_module_activities_view(request, 'apprenticeship', self.source.pk).status_code, 404)
        with patch('enrolment_api.auth.auth_required', return_value=False):
            response = activities.learner_module_activities_view(request, 'apprenticeship', self.source.pk)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(call.args[2] == 'home' for call in self.read.call_args_list))
            response = activities.learner_module_activities_view(RequestFactory().post('/activities/'), 'apprenticeship', self.source.pk)
            self.assertEqual(response.status_code, 405)

    def test_example_structured_output(self):
        result = self.load()
        # Standalone synthetic artifact for review, never persisted to a learner.
        folder = Path(__file__).resolve().parents[2] / '.cache' / 'step6a'
        folder.mkdir(exist_ok=True)
        (folder / 'example-output.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        self.assertEqual(result['status'], 'available')

    def test_pcp_all_active_pathways_use_only_their_confirmed_bundle(self):
        from itertools import combinations
        from .modules_rules import validate_active_selection
        from .configuration import ConfigurationError
        programme = next(p for p in self.config['programmes'] if p['id'] == 'PCP_L6')
        self.pool.programme_id = 'PROG-PCP-L6'
        self.pool.save()
        for path in programme['pathways']:
            with self.subTest(pathway=path['id']):
                CohortGroupAllocation.objects.all().delete()
                available = available_modules(path['modules'])
                mandatory = [m for m in available if m.get('isMandatory')]
                optional = [m for m in available if not m.get('isMandatory')]
                for chosen in combinations(optional, path['requiredOptionalModulesCount']):
                    modules = mandatory + list(chosen)
                    try:
                        validate_active_selection(path, [m['id'] for m in modules], complete=True)
                        break
                    except ConfigurationError:
                        continue
                self.configure('PCP_L6', modules)
                self.row.modules_selection = {'programme': 'PCP_L6', 'pathway': path['id'], 'configurationVersion': 24,
                    'modulesByPathway': {path['id']: [m['id'] for m in modules]}}
                self.row.save()
                CohortGroupAllocation.objects.create(learner_id=self.source.pk, learner_kind='apprenticeship', pool=self.pool,
                    group_id='g1', group_name='Group 1', seat=1, pathway=path['id'], academic_ids=[m['id'] for m in modules],
                    configuration_version=24, selection_revision=0, offerings=[dict(academicModuleId=e['academicModuleId'],
                        offeringId=e['lmsOfferingId'], groupId='g1', cohortId='home', start=e['startDate'], end=e['endDate']) for e in self.plan])
                self.read.reset_mock()
                result = self.load()
                self.assertEqual(result['status'], 'available', result['blockers'])
                self.assertEqual({m['academicModuleId'] for m in result['modules']}, {m['id'] for m in modules})
                self.assertEqual(self.read.call_count, len(modules))

    def test_malformed_identifiers_fail_closed_without_database_content_reads(self):
        self.plan[0]['lmsOfferingId'] = {'not': 'an identifier'}
        self.source.learning_plan = self.plan
        self.source.save()
        self.assertEqual(self.load()['blockers'][0]['code'], 'saved_module_identifier_invalid')
        self.read.assert_not_called()

    def test_inaccessible_connection_or_missing_module_is_explicitly_unavailable(self):
        self.read.return_value = {'modules': None, 'blockers': [{'database': 'LMS', 'schema': 'curriculum',
            'table': 'connection', 'code': 'connection_timeout'}]}
        self.read.side_effect = None
        result = self.load()
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['modules'][0]['weeks'])
        self.read.return_value = {'modules': [], 'blockers': [{'database': 'LMS', 'schema': 'curriculum',
            'table': 'modules', 'code': 'module_placement_not_found'}]}
        self.assertEqual(self.load()['blockers'][0]['code'], 'module_placement_not_found')

    def test_ksb_definition_enrichment_requires_the_exact_module_profile_version(self):
        def enriched(mid, pid, cid, gid):
            raw = raw_content(mid, pid, cid, gid)
            raw['components'][0]['ksb_mappings'] = []
            for mapping in raw['ksb_mappings']:
                if mapping['ksb_code'] == 'K1':
                    mapping['ksb_description'] = None
            raw['ksb_profile_versions'] = [{'id': 42, 'source_profile_id': 'profile'}]
            raw['ksb_definitions'] = [{'id': 7, 'profile_version_id': 42, 'code': 'K1', 'description': 'Verified version description'}]
            return raw
        self.read.side_effect = enriched
        module = self.load()['modules'][0]
        self.assertEqual(next(k for k in module['coveredKsbs'] if k['code'] == 'K1')['description'], 'Verified version description')
        def wrong_version(*args):
            raw = enriched(*args)
            raw['ksb_definitions'][0]['profile_version_id'] = 999
            return raw
        self.read.side_effect = wrong_version
        result = self.load()
        knowledge = next(k for k in result['modules'][0]['coveredKsbs'] if k['code'] == 'K1')
        self.assertIsNone(knowledge['description'])
        self.assertTrue(any(b['code'] == 'ksb_definition_relationship_unverified' for b in result['blockers']))

    def test_malformed_saved_configuration_or_plan_is_reported(self):
        self.row.modules_configuration = {'programmes': None}
        self.row.save()
        self.assertEqual(self.load()['blockers'][0]['code'], 'saved_academic_configuration_invalid')
        self.read.assert_not_called()
        self.row.modules_configuration = self.config
        self.row.save()
        self.source.learning_plan = [self.plan[0], 'invalid entry']
        self.source.save()
        self.assertEqual(self.load()['blockers'][0]['code'], 'saved_learning_plan_invalid')
        self.read.assert_not_called()
