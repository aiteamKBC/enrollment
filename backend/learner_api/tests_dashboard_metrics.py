"""Database-free regression tests: imported baselines and later LMS progress."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase, RequestFactory

from .dashboard_metrics import (programme_totals, ksb_totals, read_planned_hours,
                                activity_planned_hours, read_metrics, learner_metrics,
                                metrics_from_loaded, _read_legacy_metrics)
from .student_activity import _direct_progress_otjh
from .attendance import combined_attendance_rows, _summarize_attendance


class DashboardMetricsTests(SimpleTestCase):
    def setUp(self):
        # Legacy calculation helpers are retained only for migration comparison;
        # the live read_metrics entry point is covered separately as SSOT-only.
        self.enterContext(patch('learner_api.dashboard_metrics.canonical_learning.enabled', return_value=False))

    def test_authored_snake_case_ksb_mapping_counts_after_completion(self):
        native = [{'id': 'reading', 'ksb_mappings': json.dumps([{'ksb_code': 'K1.1', 'weight': 1}])}]
        before = ksb_totals(native, [])
        after = ksb_totals(native, [{'componentId': 'reading', 'kind': 'component'}])
        self.assertEqual((before['completed'], before['total']), (0, 1))
        self.assertEqual((after['completed'], after['total'], after['percent']), (1, 1, 100))

    def test_old_and_new_completions_union_by_exact_activity_identity(self):
        old = [{'group_id': 2, 'activity_id': 10, 'status': 'completed'},
               {'group_id': 2, 'activity_id': 11, 'status': 'notstarted'}]
        native = [{'id': 'imported'}, {'id': 'new'}]
        progress = [{'componentId': 'imported', 'kind': 'component'},
                    {'componentId': 'new', 'kind': 'quiz', 'passed': True}]
        result = programme_totals(old, native, progress, {('2', '11')}, {'imported': ('2', '10')})
        self.assertEqual((result['completed'], result['total'], result['percent']), (3, 3, 100))
        self.assertEqual(result['historicalCompleted'], 1)

    def test_failed_and_unfinished_quizzes_never_complete_components(self):
        native = [{'id': 'a'}, {'id': 'b'}, {'id': 'c', 'quiz_id': '9'}]
        progress = [{'componentId': 'a', 'kind': 'component', 'passed': False},
                    {'componentId': 'b', 'kind': 'quiz', 'passed': None},
                    {'quizId': 9, 'kind': 'quiz', 'passed': True}]
        result = programme_totals([], native, progress, set(), {})
        self.assertEqual((result['completed'], result['total']), (1, 3))

    def test_repeated_attempts_do_not_inflate_progress(self):
        result = programme_totals([], [{'id': 'a'}], [{'componentId': 'a', 'kind': 'video'}] * 3, set(), {})
        self.assertEqual((result['completed'], result['total']), (1, 1))

    def test_reading_with_quiz_requires_both(self):
        old = [{'group_id': 1, 'activity_id': 1, 'quiz_id': 9, 'reading_type': 'pdf',
                'quiz_passed': True, 'reading_viewed': False}]
        self.assertEqual(programme_totals(old, [], [], set(), {})['completed'], 0)

    def test_empty_programme_does_not_invent_a_percentage(self):
        self.assertIsNone(programme_totals([], [], [], set(), {})['percent'])

    def test_hour_added_to_accepted_ledger_once(self):
        progress = [{'componentId': 'a', 'kind': 'component', 'claimedSeconds': 3600,
                     'timeTrackingSource': 'component:input', 'expectedOtjh': 10}] * 2
        self.assertEqual(round(1171.34 + _direct_progress_otjh(progress), 2), 1172.34)

    def test_ksb_occurrences_count_again_in_another_component(self):
        native = [{'id': 'a', 'ksb_mappings': [{'code': 'K1'}, {'code': 'S1'}]},
                  {'id': 'b', 'ksb_mappings': [{'code': 'K1'}]}]
        result = ksb_totals(native, [{'componentId': 'a', 'kind': 'component'}])
        self.assertEqual((result['completed'], result['total'], result['percent']), (2, 3, 66.67))

    def test_unmapped_historical_activity_makes_ksb_unavailable(self):
        result = ksb_totals([{'id': 'a', 'ksb_mappings': ['K1']}], [],
                            [{'group_id': 1, 'activity_id': 2, 'ksb_mappings': None}])
        self.assertEqual((result['status'], result['reason']), ('unavailable', 'activity_points_missing'))
        self.assertEqual((result['completed'], result['total'], result['percent']), (None, None, None))
        self.assertEqual(result['unmappedActivities'], 1)
        self.assertEqual(result['mappedCompleted'], 0)
        self.assertEqual(result['mappedTotal'], 1)

    def test_historical_and_new_ksb_points_union_with_saved_attempts(self):
        old = [{'group_id': 1, 'activity_id': 2, 'ksb_mappings': ['K1', 'S1'], 'status': 'completed'},
               {'group_id': 1, 'activity_id': 3, 'ksb_mappings': ['K1']}]
        native = [{'id': 'same', 'ksb_mappings': ['K1', 'S1']}, {'id': 'new', 'ksb_mappings': ['B1']}]
        result = ksb_totals(native, [{'componentId': 'new'}], old, {('1', '3')}, {'same': ('1', '2')})
        self.assertEqual((result['completed'], result['total']), (4, 4))
        self.assertEqual(result['historicalCompleted'], 2)
        self.assertEqual(sum(item['total'] for item in result['codes']), 4)

    def test_ksb_completion_survives_repeated_failed_attempt(self):
        progress = [{'componentId': 'a', 'passed': True, 'kind': 'quiz'},
                    {'componentId': 'a', 'passed': False, 'kind': 'quiz'}]
        self.assertEqual(ksb_totals([{'id': 'a', 'ksb_mappings': ['K1']}], progress)['completed'], 1)

    def test_contract_snapshot_is_authoritative_and_can_be_zero(self):
        with patch('learner_api.dashboard_metrics.TrainingPlanDocument.objects') as manager, \
             patch('learner_api.dashboard_metrics.find_contract') as imported:
            query = manager.using.return_value.filter.return_value.order_by.return_value.values.return_value
            query.first.return_value = {'otjh': {'plannedTotal': 0}}
            self.assertEqual(read_planned_hours(SimpleNamespace(pk=125, aptem_id=92), 'commercial', MagicMock()), 0)
            imported.assert_not_called()

    def test_imported_contract_used_when_no_local_document(self):
        with patch('learner_api.dashboard_metrics.TrainingPlanDocument.objects') as manager, \
             patch('learner_api.dashboard_metrics.find_contract', return_value={'training_plan_planned_hours': 867}) as imported:
            manager.using.return_value.filter.return_value.order_by.return_value.values.return_value.first.return_value = None
            self.assertEqual(read_planned_hours(SimpleNamespace(pk=125, aptem_id=92), 'commercial', MagicMock()), 867)
            self.assertEqual(imported.call_args.args[1], 92)

    def test_preloaded_contract_preserves_total_without_single_learner_query(self):
        contract = {'training_plan_planned_hours': 867}
        with patch('learner_api.dashboard_metrics.find_contract',
                   side_effect=AssertionError('contract must already be loaded')):
            self.assertEqual(
                read_planned_hours(
                    SimpleNamespace(pk=125, aptem_id=92),
                    'commercial',
                    MagicMock(),
                    None,
                    contract,
                ),
                867,
            )

    def test_new_learner_never_reads_imported_contract(self):
        with patch('learner_api.dashboard_metrics.TrainingPlanDocument.objects') as manager, \
             patch('learner_api.dashboard_metrics.find_contract') as imported:
            manager.using.return_value.filter.return_value.order_by.return_value.values.return_value.first.return_value = None
            self.assertIsNone(read_planned_hours(SimpleNamespace(pk=125, aptem_id=None), 'commercial', MagicMock()))
            imported.assert_not_called()

    def test_activity_target_sums_the_whole_plan_and_deduplicates_components(self):
        native = [{'id': 'video', 'expected_hours': '1.25', 'module_id': 'first'},
                  {'id': 'reading', 'expected_hours': 2.5, 'module_id': 'second'},
                  {'id': 'reflection', 'expected_hours': 0.75, 'module_id': 'second'}]
        self.assertEqual(activity_planned_hours([], native + [native[0]], {}), 4.5)

    def test_activity_target_unions_exact_historical_and_native_placements(self):
        old = [{'group_id': 2, 'activity_id': 10, 'expected_hours': 1},
               {'group_id': 2, 'activity_id': 11, 'expected_hours': 3},
               {'group_id': 3, 'activity_id': 11, 'expected_hours': 3}]
        native = [{'id': 'imported', 'expected_hours': 2}, {'id': 'new', 'expected_hours': 0.5}]
        links = {'imported': ('2', '10')}
        self.assertEqual(activity_planned_hours(old + [old[0]], native, links), 8.5)

    def test_missing_native_hours_can_use_the_exact_historical_mapping(self):
        old = [{'group_id': 2, 'activity_id': 10, 'expected_hours': 1.25}]
        self.assertEqual(activity_planned_hours(old, [{'id': 'imported'}], {'imported': ('2', '10')}), 1.25)

    def test_missing_or_invalid_activity_hours_do_not_create_a_partial_target(self):
        self.assertIsNone(activity_planned_hours([], [], {}))
        for value in (None, '', 'unknown', -1, float('inf'), float('nan')):
            with self.subTest(value=value):
                self.assertIsNone(activity_planned_hours([], [
                    {'id': 'known', 'expected_hours': 2}, {'id': 'missing', 'expected_hours': value}], {}))
        self.assertEqual(activity_planned_hours([], [{'id': 'zero', 'expected_hours': 0}], {}), 0)

    def test_current_activity_hours_update_the_fallback_without_completing_it(self):
        native = [{'id': 'activity', 'expected_hours': 1.25}]
        self.assertEqual(activity_planned_hours([], native, {}), 1.25)
        native[0]['expected_hours'] = 2.75
        self.assertEqual(activity_planned_hours([], native, {}), 2.75)

    def test_missing_snapshot_hours_still_allow_an_imported_contract_total(self):
        with patch('learner_api.dashboard_metrics.TrainingPlanDocument.objects') as manager, \
             patch('learner_api.dashboard_metrics.find_contract', return_value={'training_plan_planned_hours': 867}):
            manager.using.return_value.filter.return_value.order_by.return_value.values.return_value.first.return_value = {'otjh': {}}
            self.assertEqual(read_planned_hours(SimpleNamespace(pk=125, aptem_id=92), 'commercial', MagicMock()), 867)

    def test_metrics_return_activity_sum_when_document_or_its_total_is_missing(self):
        source = SimpleNamespace(pk=101, aptem_id=None, email='test@example.com')
        native = [{'id': 'video', 'expected_hours': 1.5, 'ksb_mappings': []},
                  {'id': 'reading', 'expected_hours': 2.25, 'ksb_mappings': []}]
        for document, expected in ((None, 3.75), ({'otjh': {}}, 3.75),
                                   ({'otjh': {'plannedTotal': None}}, 3.75),
                                   ({'otjh': {'plannedTotal': 0}}, 0),
                                   ({'otjh': {'plannedTotal': 90}}, 90)):
            with self.subTest(document=document), \
                 patch('learner_api.dashboard_metrics.connections') as connections, \
                 patch('learner_api.dashboard_metrics._direct_progress_records', return_value=[]), \
                 patch('learner_api.dashboard_metrics._effective_plan_ids', return_value=['module-one', 'module-two']), \
                 patch('learner_api.dashboard_metrics.rows', side_effect=[native, []]), \
                 patch('learner_api.dashboard_metrics.TrainingPlanDocument.objects') as manager:
                manager.using.return_value.filter.return_value.order_by.return_value.values.return_value.first.return_value = document
                cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
                cursor.fetchall.return_value = [('module-one', 'First module'), ('module-two', 'Second module')]
                result = _read_legacy_metrics(source, 'commercial')
                self.assertEqual(result['otjh']['planned'], expected)
                self.assertEqual(result['otjh']['actual'], 0)
                query, params = cursor.execute.call_args_list[0].args
                self.assertIn('c.expected_otjh AS expected_hours', query)
                self.assertIn('c.module_catalogue_id=ANY(%s)', query)
                self.assertIn("c.deleted_at IS NULL OR COALESCE(c.deleted_via_parent, '') <> ''", query)
                self.assertEqual(params, [['module-one', 'module-two']])

    def test_migrated_metrics_keep_audited_inventory_and_refresh_saved_progress(self):
        source = SimpleNamespace(pk=125, aptem_id=92, email='test@example.com')
        historical = [
            {'group_id': 2, 'activity_id': 10, 'status': 'completed', 'ksb_mappings': ['K1', 'S1']},
            {'group_id': 2, 'activity_id': 11, 'ksb_mappings': ['K1']},
            {'group_id': 2, 'activity_id': 12, 'ksb_mappings': ['S2']},
        ]
        native = [{'id': 'imported', 'ksb_mappings': ['K1', 'S1']},
                  {'id': 'new', 'ksb_mappings': ['B1']}]
        progress = [{'componentId': 'imported', 'kind': 'component'},
                    {'componentId': 'new', 'kind': 'component'}]
        # An external inventory refresh used to replace the audited target,
        # changing programme totals and introducing activities with no KSB map.
        with patch('learner_api.subject_source.read_learner',
                   side_effect=AssertionError('Metrics must use the saved audit')) as live:
            for saved_attempts, expected in (([(2, 11)], (3, 4, 75, 4, 5, 80)),
                                             ([(2, 11), (2, 12)], (4, 4, 100, 5, 5, 100))):
                with self.subTest(attempts=saved_attempts), \
                     patch('learner_api.dashboard_metrics.connections') as connections, \
                     patch('learner_api.dashboard_metrics._direct_progress_records', return_value=[]), \
                     patch('learner_api.dashboard_metrics._effective_plan_ids', return_value=['module-one']), \
                     patch('learner_api.dashboard_metrics.rows', side_effect=[native, progress, historical]), \
                     patch('learner_api.dashboard_metrics.read_planned_hours', return_value=867), \
                     patch('learner_api.dashboard_metrics.read_aptem_planned_total', return_value=867), \
                     patch('learner_api.dashboard_metrics.read_accepted_ksb_rows', return_value=[]):
                    cursor = connections.__getitem__.return_value.cursor.return_value.__enter__.return_value
                    cursor.fetchone.side_effect = [(source.email,), (1171.34, [])]
                    cursor.fetchall.side_effect = [
                        [
                            (2, 10, 'Module one', 'Imported activity', date(2026, 1, 1)),
                            (2, 11, 'Module one', 'Historical activity 11', date(2026, 1, 2)),
                            (2, 12, 'Module one', 'Historical activity 12', date(2026, 1, 3)),
                        ],
                        saved_attempts,
                        [(2, '10', 'imported')],
                    ]
                    result = _read_legacy_metrics(source, 'commercial')
                    self.assertEqual(tuple(result[metric][key] for metric in ('programme', 'ksb')
                                           for key in ('completed', 'total', 'percent')), expected)
                    self.assertEqual(result['ksb']['historicalCompleted'], 2)
                    self.assertEqual(result['otjh'], {'historical': 1171.34, 'new': 0,
                                                     'actual': 1171.34, 'completed_actual': None, 'planned': 867})
            live.assert_not_called()

    def test_live_metrics_read_is_ssot_only(self):
        source = SimpleNamespace(pk=125, aptem_id=92, email='test@example.com')
        expected = {'otjh': {'actual': 12.5, 'planned': 20}}
        with patch('learner_api.dashboard_metrics.canonical_learning.metrics', return_value=expected) as canonical, \
             patch('learner_api.dashboard_metrics.connections') as legacy_connections:
            self.assertEqual(read_metrics(source, 'commercial'), expected)
        canonical.assert_called_once_with(125)
        legacy_connections.__getitem__.assert_not_called()

    def test_accepted_ledger_title_is_used_for_ksb_evidence(self):
        source = SimpleNamespace(pk=125, aptem_id=92, email='test@example.com')
        with patch('learner_api.dashboard_metrics.connections'), \
             patch('learner_api.dashboard_metrics.read_planned_hours', return_value=0):
            result = metrics_from_loaded(
                source,
                'commercial',
                migrated=True,
                native=[],
                progress=[],
                direct_progress=[],
                historical=[],
                attempts=set(),
                links={},
                history_ready=True,
                manual_hours=0,
                preloaded={
                    'aptem_planned_total': 0,
                    'reflection_submissions': [],
                    'accepted_ksb_rows': [{
                        'id': 6266,
                        'source_ref': 'manual:6266',
                        'activity_id': 81224,
                        'activity_title': 'P1 - Introduction to Strategic Marketing',
                        'ksb_mappings': ['B1'],
                    }],
                },
            )

        self.assertEqual(
            result['_ksb_evidence_sources'][0]['title'],
            'P1 - Introduction to Strategic Marketing',
        )

    def test_endpoint_scopes_identity_and_returns_retryable_error(self):
        source = SimpleNamespace(pk=125, aptem_id=92, email='test@example.com')
        model = MagicMock()
        model.DoesNotExist = type('Missing', (Exception,), {})
        model.all_learners.only.return_value.get.return_value = source
        with patch.dict('learner_api.dashboard_metrics.SOURCE_MODELS', {'commercial': model}), \
             patch('learner_api.dashboard_metrics.read_metrics', side_effect=DatabaseError('offline')):
            response = learner_metrics.__wrapped__.__wrapped__(RequestFactory().get('/'), 'commercial', 125)
        self.assertEqual(response.status_code, 503)
        model.all_learners.only.return_value.get.assert_called_once_with(pk=125)
        self.assertNotIn('offline', response.content.decode())


class LearnerOverviewMetricsEndpointTests(SimpleTestCase):
    def call(self, query, payload):
        source = SimpleNamespace(pk=125)
        model = MagicMock()
        model.DoesNotExist = type('Missing', (Exception,), {})
        model.all_learners.only.return_value.get.return_value = source
        with patch.dict('learner_api.dashboard_metrics.SOURCE_MODELS', {'commercial': model}), \
             patch('learner_api.dashboard_metrics.read_metrics', return_value=payload) as ordinary, \
             patch('learner_api.dashboard_metrics.canonical_learning.metrics_bulk', return_value={125: payload}) as overview, \
             patch('learner_api.dashboard_metrics.coach_otjh_target_to_date', return_value=123.87) as target_to_date:
            response = learner_metrics.__wrapped__.__wrapped__(RequestFactory().get('/', query), 'commercial', 125)
        model.all_learners.only.return_value.get.assert_called_once_with(pk=125)
        return response, ordinary, overview, target_to_date

    def test_explicit_overview_uses_same_metrics_as_coach_caseload(self):
        payload = {'ksb': {'completed': 498, 'total': 584, 'percent': 85.3},
                   'otjh': {'actual': 164.87, 'planned': 576}, '_ksb_evidence_sources': []}
        response, ordinary, overview, target_to_date = self.call({'view': 'learner-overview'}, payload)
        self.assertEqual(response.status_code, 200)
        ordinary.assert_not_called()
        overview.assert_called_once_with([125], learner_workspace=True, include_ksb_points=True)
        self.assertEqual(json.loads(response.content)['ksb']['total'], 584)
        self.assertEqual(json.loads(response.content)['otjh']['targetToDate'], 123.87)
        target_to_date.assert_called_once_with(ANY, payload['otjh'])
        self.assertNotIn('_ksb_evidence_sources', json.loads(response.content))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_overview_target_uses_the_learning_plan_without_an_ssot_plan(self):
        source = SimpleNamespace(pk=125)
        model = MagicMock()
        model.DoesNotExist = type('Missing', (Exception,), {})
        model.all_learners.only.return_value.get.return_value = source
        payload = {'ksb': {}, 'otjh': {'actual': 6.59, 'planned': None}}
        with patch.dict('learner_api.dashboard_metrics.SOURCE_MODELS', {'commercial': model}), \
             patch('learner_api.dashboard_metrics.canonical_learning.metrics_bulk', return_value={125: payload}), \
             patch('learner_api.dashboard_metrics.canonical_learning.profile', return_value={'id': 7}), \
             patch('learner_api.dashboard_metrics.plan_planned_hours', return_value=576.13) as plan, \
             patch('learner_api.dashboard_metrics.coach_otjh_target_to_date', return_value=39.25) as target_to_date:
            response = learner_metrics.__wrapped__.__wrapped__(
                RequestFactory().get('/', {'view': 'learner-overview'}), 'commercial', 125)
        plan.assert_called_once_with(source, {'id': 7})
        target_to_date.assert_called_once_with(ANY, {'actual': 6.59, 'planned': 576.13, 'targetToDate': 39.25})
        self.assertEqual(json.loads(response.content)['otjh']['planned'], 576.13)
        self.assertEqual(json.loads(response.content)['otjh']['targetToDate'], 39.25)

    def test_existing_metrics_call_retains_its_source(self):
        response, ordinary, overview, target_to_date = self.call({}, {'ksb': {'status': 'empty'}})
        self.assertEqual(response.status_code, 200)
        ordinary.assert_called_once()
        overview.assert_not_called()
        target_to_date.assert_not_called()

    def test_missing_overview_identity_does_not_fallback_to_other_totals(self):
        response, ordinary, overview, target_to_date = self.call({'view': 'learner-overview'}, None)
        self.assertEqual(response.status_code, 409)
        ordinary.assert_not_called()
        target_to_date.assert_not_called()


class CombinedAttendanceTests(SimpleTestCase):
    def row(self, **changes):
        return {'learner_id': 1, 'enrolment_id': 125, 'learner_name': 'Test',
                'learner_email': 'test@example.com', 'session_date': date(2026, 9, 1),
                'session_id': 'one', 'attendance_status': 'present', 'minutes_late': 0,
                'catchup_completed': False, 'updated_at': None, **changes}

    def test_new_learner_uses_only_teams_and_excludes_other_enrolment(self):
        source = SimpleNamespace(id=125, aptem_id=None, email='test@example.com')
        with patch('learner_api.attendance.fetch_kbc_attendance_rows') as old, \
             patch('learner_api.attendance.fetch_verified_teams_attendance_rows', return_value=[
                 self.row(), self.row(enrolment_id=126)]):
            rows = combined_attendance_rows(source)
        old.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['learner_id'], 125)

    def test_combines_counts_and_deduplicates_reconnects(self):
        source = SimpleNamespace(id=125, aptem_id=92, email='test@example.com')
        with patch('learner_api.attendance.fetch_kbc_attendance_rows', return_value=[self.row(attendance_status='absent')]), \
             patch('learner_api.attendance.fetch_verified_teams_attendance_rows', return_value=[self.row(), self.row()]):
            summary = _summarize_attendance(combined_attendance_rows(source))
        self.assertEqual((summary['sessions'], summary['present'], summary['attendanceRate']), (2, 1, 50))
        self.assertEqual(summary['source'], 'combined')

    def test_reads_updated_legacy_register_each_time(self):
        source = SimpleNamespace(id=125, aptem_id=92, email='test@example.com')
        with patch('learner_api.attendance.fetch_kbc_attendance_rows', side_effect=[[], [self.row()]]) as old, \
             patch('learner_api.attendance.fetch_verified_teams_attendance_rows', return_value=[]):
            self.assertEqual(len(combined_attendance_rows(source)), 0)
            self.assertEqual(len(combined_attendance_rows(source)), 1)
        self.assertEqual(old.call_count, 2)

    def test_credit_projection_keeps_saved_register_outcome_over_teams(self):
        source = SimpleNamespace(id=125, aptem_id=None, email='test@example.com')
        saved = self.row(session_id='occ-1', occurrence_id='occ-1',
                         attendance_status='absent', source='live-register')
        teams = [self.row(session_id='occ-1', occurrence_id='occ-1'),
                 self.row(session_id='occ-2', occurrence_id='occ-2')]
        schedule = [{'session_id': 'occ-1'}]
        with patch('learner_api.attendance._live_register_rows', return_value=[saved]), \
             patch('learner_api.attendance.fetch_verified_teams_attendance_rows', return_value=teams) as fetch:
            rows = combined_attendance_rows(source, credit_projection=True,
                                            register_schedule=schedule)
        fetch.assert_called_once_with(learner_emails=[source.email],
                                      include_group_metadata=False,
                                      register_schedule=schedule)
        self.assertEqual({row['session_id']: row['attendance_status'] for row in rows},
                         {'occ-1': 'absent', 'occ-2': 'present'})
