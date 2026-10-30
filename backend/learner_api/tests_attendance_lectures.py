"""Offline attendance regression tests; no database creation or mutation."""
from contextlib import nullcontext
import inspect
import json
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core import signing
from django.test import SimpleTestCase, RequestFactory
from django.urls import resolve
from django.utils import timezone

from .attendance_lectures import (
    _component_for, build_lectures, lecture_register, lecture_totals,
    read_legacy_metadata, read_workspace, report_id, session_key,
    read_native_occurrences, read_native_components, ATTENDANCE_SOURCE_FIELDS,
    _exclude_cancelled_kbc_mirrors,
)
from .attendance import _summarize_attendance
from .attendance_mode import _payload, _manager, MAX_AGE, SALT, review_attendance_mode
from .absence_reports import (
    _can_report_absence, _fetch_missed_sessions, _resolve_absent_attendance, learner_absence_reports,
)


def run_now(task, *args, **kwargs):
    """Stand-in for the after-commit email thread, so a test sees the email synchronously."""
    return task(*args, **kwargs)
from .management.commands.send_attendance_reminders import reminder_candidates


def register_row(**overrides):
    return {'session_id': '92_2026-09-01_business', 'learner_id': 12,
            'learner_name': 'Learner', 'learner_email': 'learner@example.test',
            'source': 'kbc-attendance', 'session_title': 'Business introduction',
            'module_title': 'Business', 'session_date': date(2026, 9, 1),
            'session_start_time': None, 'session_end_time': None,
            'attendance_status': 'absent', 'minutes_late': 0,
            'catchup_completed': False, 'updated_at': None, **overrides}


class AttendanceLectureTests(SimpleTestCase):
    def setUp(self):
        assignments = patch('learner_api.attendance_lectures.effective_assigned_module_ids', return_value=['assigned-module'])
        assignments.start()
        self.addCleanup(assignments.stop)
        delivery = patch('learner_api.recorded_attendance.saved_delivery_mode', return_value='live')
        delivery.start()
        self.addCleanup(delivery.stop)
        profiles = patch('learner_api.models.LearnerProfile.objects')
        manager = profiles.start()
        manager.filter.return_value.values_list.return_value = []
        self.addCleanup(profiles.stop)
        confirmations = patch('learner_api.attendance_confirmation.read_confirmations', return_value={})
        confirmations.start()
        self.addCleanup(confirmations.stop)
        catchups = patch('learner_api.attendance_lectures._completed_catchup_occurrences', return_value=set())
        catchups.start()
        self.addCleanup(catchups.stop)

    def test_monthly_log_link_uses_exact_source_key_and_saved_report_month(self):
        row = register_row()
        result = build_lectures([row], {row['session_id']: {'log_month': '2026-08'}}, [], [], 'commercial', 12)[0]
        self.assertEqual(result['monthlyLog'], {'month': '2026-08', 'sourceRef': f"att:{row['session_id']}"})
        self.assertEqual(result['date'], '2026-09-01')

    def test_cancelled_native_session_suppresses_one_stale_kbc_mirror(self):
        legacy = register_row(
            module_title='PMI-SP Scheduling Professional',
            session_title='Lecture 3',
            session_date=date(2026, 9, 30),
        )
        cancelled = [{
            'module_title': 'PMI-SP Scheduling Professional',
            'session_date': date(2026, 9, 30),
            'session_number': 3,
        }]
        self.assertEqual(_exclude_cancelled_kbc_mirrors([legacy], [], cancelled), [])

    def test_cancelled_native_session_does_not_suppress_when_an_active_match_exists(self):
        legacy = register_row(
            module_title='PMI-SP Scheduling Professional',
            session_title='Lecture 3',
            session_date=date(2026, 9, 30),
        )
        active = register_row(
            source='microsoft-teams',
            module_title='PMI-SP Scheduling Professional',
            session_date=date(2026, 9, 30),
        )
        cancelled = [{
            'module_title': 'PMI-SP Scheduling Professional',
            'session_date': date(2026, 9, 30),
            'session_number': 3,
        }]
        self.assertEqual(_exclude_cancelled_kbc_mirrors([legacy], [active], cancelled), [legacy])

    def test_native_monthly_log_link_uses_occurrence_and_stored_month(self):
        row = register_row(source='microsoft-teams', session_id='occ-8', module_catalogue_id=3,
                           scheduled_start=datetime(2026, 8, 31, 23, 30, tzinfo=datetime_timezone.utc))
        result = build_lectures([row], {}, [], [], 'commercial', 12)[0]
        self.assertEqual(result['monthlyLog'], {'month': '2026-08', 'sourceRef': 'attendance:occ-8'})
        self.assertEqual(result['date'], '2026-09-01')

    def test_native_timestamp_columns_are_interpreted_as_utc(self):
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.side_effect = [
            [('business', 'Business')],
        ]
        raw = {'scheduled_start': datetime(2026, 9, 1, 23, 30), 'scheduled_end': datetime(2026, 9, 2, 0, 30),
               'updated_at': datetime(2026, 9, 1, 12), 'module_catalogue_id': 'business',
               'module_title': 'Business', 'session_number': 1}
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[raw]), \
             timezone.override('Europe/London'):
            result = read_native_occurrences(source)[0]
        self.assertEqual(result['session_date'], date(2026, 9, 2))
        self.assertEqual(result['session_start_time'].strftime('%H:%M'), '00:30')
        self.assertEqual(result['scheduled_start'].tzinfo, datetime_timezone.utc)

    def test_cancelled_native_session_is_visible_as_cancelled_and_not_counted(self):
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [('business', 'Business')]
        now = timezone.now()
        raw = {'session_id': 'occ-cancelled', 'occurrence_id': 'occ-cancelled', 'live_session_id': 'live-1',
               'scheduled_start': now - timedelta(hours=2), 'scheduled_end': now - timedelta(hours=1),
               'updated_at': now, 'module_title': 'Business', 'module_catalogue_id': 'business',
               'session_number': 5, 'occurrence_status': 'cancelled', 'live_session_status': 'active'}
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[raw]):
            result = read_native_occurrences(source)
        self.assertEqual(result[0]['attendance_status'], 'cancelled')
        lecture = build_lectures(result, {}, [], [], 'commercial', 12)[0]
        self.assertEqual(lecture['status'], 'cancelled')
        self.assertFalse(lecture['counted'])
        self.assertIsNone(lecture['catchupStatus'])

    def test_schedule_uses_current_module_assignment_without_stale_invitation_gate(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [('assigned-module', 'Current module title')],
            [],
            [('assigned-module', 'Current module title')],
        ]
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
            read_native_occurrences(SimpleNamespace(id=12, email=' LEARNER@example.test '))
        assignment_sql, assignment_params = cur.execute.call_args_list[0].args
        self.assertIn('cm.module_catalogue_id=ANY(%s)', assignment_sql)
        self.assertEqual(assignment_params, [['assigned-module']])
        schedule_sql, schedule_params = cur.execute.call_args_list[1].args
        self.assertEqual(len(cur.execute.call_args_list), 2)
        self.assertIn('teams_shared_source_module_id', schedule_sql)
        self.assertEqual(schedule_params, [['assigned-module'], 'learner@example.test', ['assigned-module'], ['assigned-module']])
        self.assertIn('s.module_catalogue_id=ANY(%s)', schedule_sql)
        self.assertIn('curriculum.live_session_attendance', schedule_sql)
        self.assertIn('a.total_attendance_seconds>180', schedule_sql)
        self.assertIn("liveSessionLinkOverride", schedule_sql)
        self.assertIn('COALESCE(replaced_attendance.occurrence_id,o.id)', schedule_sql)
        self.assertIn('m.title AS module_title', schedule_sql)
        self.assertNotIn('jsonb_array_elements_text', schedule_sql)
        self.assertNotIn('live_session_join_launches', schedule_sql)
        self.assertIn('target_module_title', schedule_sql)

    def test_schedule_reuses_already_loaded_home_assignment(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
            read_native_occurrences(
                SimpleNamespace(id=12, email='learner@example.test'),
                module_ids=['assigned-module', 'assigned-module', ''],
            )

        statements = [call.args[0] for call in cur.execute.call_args_list]
        self.assertFalse(any('Created_users' in sql for sql in statements))
        self.assertEqual(len(statements), 1)
        schedule_sql, schedule_params = cur.execute.call_args_list[0].args
        self.assertIn('s.module_catalogue_id=ANY(%s)', schedule_sql)
        self.assertEqual(schedule_params, [['assigned-module'], 'learner@example.test', ['assigned-module'], ['assigned-module']])

    def test_an_unreadable_combined_read_falls_back_to_the_separate_shared_lookup(self):
        from django.db import DatabaseError
        # A duplicate sharing another module's meeting has no series of its own.
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.execute.side_effect = [None, DatabaseError('combined unavailable'), None, None, None]
        cur.fetchall.side_effect = [
            [('alias-module', 'Alias')],
            [('alias-module', 'source-module')],
            [('alias-module', 'Alias')],
        ]
        now = timezone.now()
        raw = {
            'session_id': 'source-occ', 'occurrence_id': 'source-occ',
            'attendance_source_occurrence_id': 'source-occ', 'live_session_id': 'source-live',
            'module_catalogue_id': 'source-module', 'module_title': 'Source',
            'scheduled_start': now - timedelta(hours=2),
            'scheduled_end': now - timedelta(hours=1), 'updated_at': now,
            'session_number': 1, 'attendance_report_id': 'report-1', 'attended': True,
        }
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[raw]):
            result = read_native_occurrences(
                SimpleNamespace(id=12, username='Learner', email='learner@example.test')
            )
        schedule_sql, schedule_params = cur.execute.call_args_list[3].args
        self.assertIn('s.module_catalogue_id=ANY(%s)', schedule_sql)
        self.assertEqual(schedule_params, ['learner@example.test', ['alias-module', 'source-module']])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['module_catalogue_id'], 'alias-module')
        self.assertEqual(result[0]['module_title'], 'Alias')
        self.assertEqual(result[0]['session_id'], 'source-occ:alias-module')
        self.assertEqual(result[0]['attendance_source_occurrence_id'], 'source-occ')
        self.assertEqual(result[0]['attendance_status'], 'present')

    def test_shared_teams_duplicate_reads_its_source_modules_meeting(self):
        # A duplicate sharing another module's meeting has no series of its own.
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.side_effect = [
            [('alias-module', 'Alias')],
            [('alias-module', 'source-module')],
            [('alias-module', 'Alias')],
        ]
        now = timezone.now()
        raw = {
            'session_id': 'source-occ', 'occurrence_id': 'source-occ',
            'attendance_source_occurrence_id': 'source-occ', 'live_session_id': 'source-live',
            'module_catalogue_id': 'source-module', 'module_title': 'Source',
            'target_module_id': 'alias-module', 'target_module_title': 'Alias',
            'scheduled_start': now - timedelta(hours=2),
            'scheduled_end': now - timedelta(hours=1), 'updated_at': now,
            'session_number': 1, 'attendance_report_id': 'report-1', 'attended': True,
        }
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[raw]):
            result = read_native_occurrences(
                SimpleNamespace(id=12, username='Learner', email='learner@example.test')
            )
        schedule_sql, schedule_params = cur.execute.call_args_list[1].args
        self.assertIn('s.module_catalogue_id=ANY(%s)', schedule_sql)
        self.assertEqual(schedule_params, [['alias-module'], 'learner@example.test', ['alias-module'], ['alias-module']])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['module_catalogue_id'], 'alias-module')
        self.assertEqual(result[0]['module_title'], 'Alias')
        self.assertEqual(result[0]['session_id'], 'source-occ:alias-module')
        self.assertEqual(result[0]['attendance_source_occurrence_id'], 'source-occ')
        self.assertEqual(result[0]['attendance_status'], 'present')

    def test_effective_plan_is_not_augmented_by_stale_normalized_assignments(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [('assigned-module', 'Current module title')]
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
            read_native_occurrences(SimpleNamespace(id=12, email='learner@example.test'))
        assignment_sql = cur.execute.call_args_list[0].args[0]
        self.assertNotIn('learner_training_plan_modules', assignment_sql)
        self.assertEqual(cur.execute.call_args_list[0].args[1], [['assigned-module']])

    def test_confirmed_teams_attendee_is_present_when_the_saved_invite_is_missing(self):
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [('business', 'Business')]
        now = timezone.now()
        raw = {'session_id': 'occ-1', 'occurrence_id': 'occ-1', 'live_session_id': 'live-1',
               'scheduled_start': now-timedelta(hours=2), 'scheduled_end': now-timedelta(hours=1),
               'updated_at': now, 'module_title': 'Business', 'module_catalogue_id': 'business',
               'session_number': 1, 'attended': True}
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[raw]):
            result = read_native_occurrences(source)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['attendance_status'], 'present')
        self.assertEqual(result[0]['source'], 'microsoft-teams')

    def test_replaced_link_projects_source_evidence_onto_destination_occurrence(self):
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        now = timezone.now()
        scheduled = [register_row(
            source='microsoft-teams', session_id='destination-occ', occurrence_id='destination-occ',
            attendance_source_occurrence_id='source-occ', live_session_id='destination-live',
            module_catalogue_id='destination-module', module_title='Destination module', session_number=4,
            scheduled_start=now-timedelta(hours=2), scheduled_end=now-timedelta(hours=1),
            session_date=timezone.localdate(), attendance_status='absent',
        )]
        evidence = [register_row(
            source='microsoft-teams', session_id='source-occ', occurrence_id='source-occ',
            live_session_id='source-live', module_catalogue_id='source-module', module_title='Source module',
            scheduled_start=now-timedelta(hours=2), scheduled_end=now-timedelta(hours=1),
            session_date=timezone.localdate(), attendance_status='present',
        )]
        result = lecture_register(source, scheduled=scheduled, records=evidence, apply_adjustments=False)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['session_id'], 'destination-occ')
        self.assertEqual(result[0]['module_catalogue_id'], 'destination-module')
        self.assertEqual(result[0]['attendance_status'], 'present')

    def test_saved_live_register_outcome_overrides_teams_evidence(self):
        now = timezone.now()
        scheduled = register_row(
            source='microsoft-teams', session_id='occ-1', occurrence_id='occ-1',
            module_catalogue_id='module-1', live_session_id='live-1',
            scheduled_start=now - timedelta(hours=2),
            scheduled_end=now - timedelta(hours=1),
            attendance_status='present', attendance_report_id='report-1',
        )
        evidence = {**scheduled, 'attendance_status': 'present', 'attended_seconds': 900}
        manual = {**scheduled, 'source': 'live-register', 'attendance_status': 'absent',
                  'attendance_report_id': '', 'updated_at': now}

        result = lecture_register(
            SimpleNamespace(id=12), records=[evidence, manual],
            scheduled=[scheduled], apply_adjustments=False,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['attendance_status'], 'absent')
        self.assertEqual(result[0]['source'], 'microsoft-teams')
        self.assertEqual(result[0]['attendance_report_id'], 'report-1')

    def test_saved_shared_outcome_cannot_be_replaced_by_the_source_register(self):
        now = timezone.now()
        native = register_row(source='microsoft-teams', session_id='occ', occurrence_id='occ',
            module_catalogue_id='source', scheduled_start=now-timedelta(hours=2),
            scheduled_end=now-timedelta(hours=1), attendance_status='present', attendance_report_id='report')
        shared = {**native, 'session_id': 'occ:destination', 'occurrence_id': 'occ:destination',
                  'module_catalogue_id': 'destination', 'attendance_source_occurrence_id': 'occ'}
        records = [{**native, 'source': 'live-register'},
                   {**shared, 'source': 'live-register', 'attendance_status': 'absent'}]
        result = lecture_register(SimpleNamespace(id=12), records=records,
                                  scheduled=[native, shared], apply_adjustments=False)
        self.assertEqual({row['module_catalogue_id']: row['attendance_status'] for row in result},
                         {'source': 'present', 'destination': 'absent'})

    def test_schedule_excludes_deleted_modules_and_superseded_or_failed_series_and_occurrences(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [('assigned-module', 'Assigned')]
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
            read_native_occurrences(SimpleNamespace(id=12, email='learner@example.test'))
        sql = cur.execute.call_args_list[1].args[0]
        self.assertIn('m.deleted_at IS NULL', sql)
        self.assertIn('NOT coalesce(m.is_programme_deleted,false)', sql)
        self.assertIn("lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')", sql)
        self.assertIn("lower(btrim(o.status)) IN ('cancelled','canceled')", sql)

    def test_empty_plan_cannot_fall_back_to_all_teams_invitations(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = []
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows') as read_rows:
            self.assertEqual(read_native_occurrences(SimpleNamespace(id=12, email='learner@example.test')), [])
        cur.execute.assert_called_once()
        read_rows.assert_not_called()

    def test_exact_audit_key_and_learner_owned_activity_completion(self):
        row = register_row()
        activity = {'group_id': 20, 'group_name': 'Business', 'activity_id': 4,
                    'activity_date': date(2026, 9, 1), 'title': 'Recording',
                    'activity_type': 'video', 'video_completed': True}
        metadata = {row['session_id']: {'ksbs': [{'code': 'K1'}, {'code': 'S2'}], 'activity_hours': 17}}
        result = build_lectures([row], metadata, [activity], [], 'apprenticeship', 12)[0]
        self.assertEqual(result['ksbs'], ['K1', 'S2'])
        self.assertEqual(result['durationMinutes'], 120)
        self.assertEqual(result['status'], 'absent')
        self.assertEqual(result['catchupStatus'], 'completed')
        self.assertEqual(result['activities'][0]['activityId'], 4)
        totals = lecture_totals([result])
        self.assertEqual((totals['attended'], totals['absent'], totals['covered'], totals['attendanceRate']), (0, 1, 1, 0))

    def test_source_edits_are_reflected_and_missing_data_is_not_invented(self):
        row = register_row()
        first = build_lectures([row], {}, [], [], 'apprenticeship', 12)[0]
        self.assertEqual(first['ksbs'], [])
        # Live sessions are a fixed two-hour lecture; no audit hours are needed.
        self.assertEqual(first['durationMinutes'], 120)
        self.assertEqual(first['catchupStatus'], 'pending')
        second = build_lectures([{**row, 'attendance_status': 'present'}],
                                {row['session_id']: {'ksbs': ['K3']}}, [], [], 'apprenticeship', 12)[0]
        self.assertEqual(second['ksbs'], ['K3'])
        self.assertEqual(second['status'], 'present')
        self.assertIsNone(second['catchupStatus'])

    def test_upcoming_unsynced_and_late_have_separate_counting_rules(self):
        lectures = build_lectures([register_row(attendance_status=status) for status in
                                  ('present', 'late', 'absent', 'upcoming', 'pending', 'in_progress')],
                                 {}, [], [], 'apprenticeship', 12)
        totals = lecture_totals(lectures)
        self.assertEqual(totals, {'total': 6, 'attended': 2, 'absent': 1, 'covered': 0,
                                  'upcoming': 1, 'attendanceRate': 67})
        self.assertIsNone(lecture_totals([lectures[-1]])['attendanceRate'])

    def test_native_ids_are_per_learner_and_distinct_from_legacy(self):
        native = register_row(source='microsoft-teams', session_id='occ-1')
        self.assertEqual(session_key(native), 'teams:occ-1')
        self.assertNotEqual(report_id(native), report_id({**native, 'learner_id': 99}))
        self.assertNotEqual(report_id(native), report_id({**native, 'source': 'kbc-attendance'}))
        self.assertEqual(report_id(native), report_id(dict(native)))

    def test_same_date_other_module_does_not_link_activities(self):
        activity = {'group_id': 30, 'group_name': 'Other module', 'activity_id': 4,
                    'activity_date': date(2026, 9, 1), 'title': 'Recording',
                    'activity_type': 'video', 'video_completed': True}
        result = build_lectures([register_row()], {}, [activity], [], 'apprenticeship', 12)[0]
        self.assertEqual(result['activities'], [])
        self.assertEqual(result['catchupStatus'], 'pending')

    def test_legacy_material_ksbs_resolve_module_spelling_and_stay_lecture_scoped(self):
        row = register_row(module_title='Procurement Risk and Contract Management')
        activity = {'group_id': 20, 'group_name': 'Level 7 Procurement Risk, and Contract Management',
                    'activity_id': 4, 'title': 'Recording', 'activity_date': row['session_date'],
                    'activity_type': 'video', 'ksbs': ['K1', 'S2']}
        other_day = {**activity, 'activity_id': 5, 'activity_date': date(2026, 8, 1), 'ksbs': ['K99']}
        lectures = lambda meta: build_lectures([row], meta, [activity, other_day], [], 'commercial', 12)
        result = lectures({})[0]
        self.assertEqual(result['ksbs'], ['K1', 'S2'])
        self.assertEqual(result['ksbScope'], 'activities')
        self.assertEqual([a['activityId'] for a in result['activities']], [4])
        activity['ksbs'] = ['K7']
        self.assertEqual(lectures({})[0]['ksbs'], ['K7'])
        for saved in (['S6'], []):
            with self.subTest(saved=saved):
                self.assertEqual(lectures({row['session_id']: {'ksbs': saved}})[0]['ksbs'], saved)

    def test_module_ksb_fallback_is_labelled_and_does_not_complete_catchup(self):
        row = register_row(module_title='Ray-Project Management Office (PMO)')
        activity = {'group_id': 20, 'group_name': 'Ray -Project Management Office (PMO)',
                    'activity_id': 4, 'title': 'Recording', 'activity_date': date(2026, 8, 1),
                    'activity_type': 'video', 'ksbs': ['S27'], 'video_completed': True}
        result = build_lectures([row], {}, [activity], [], 'commercial', 12)[0]
        self.assertEqual(result['ksbs'], ['S27'])
        self.assertEqual(result['ksbScope'], 'module')
        self.assertEqual(result['activities'], [])
        self.assertEqual(result['catchupStatus'], 'pending')

    def test_ambiguous_legacy_groups_do_not_share_ksbs(self):
        row = register_row(module_title='Procurement')
        activity = {'group_id': 20, 'group_name': 'Level 7 Procurement', 'activity_id': 4,
                    'title': 'Recording', 'activity_date': row['session_date'], 'ksbs': ['K1']}
        other = {**activity, 'group_id': 21, 'group_name': 'Level 6 Procurement', 'ksbs': ['K2']}
        result = build_lectures([row], {}, [activity, other], [], 'commercial', 12)[0]
        self.assertEqual(result['ksbs'], [])
        self.assertEqual(result['activities'], [])

    def test_native_ksbs_fall_back_to_linked_week_then_assigned_module(self):
        row = register_row(source='microsoft-teams', session_id='occ-2', module_catalogue_id='m', live_session_id='live')
        component = {'id': 'c1', 'type': 'live_session', 'module_catalogue_id': 'm', 'week_id': 'w2',
                     'title': 'Live', 'description': '', 'completed': False,
                     'settings': {'teamsOccurrenceId': 'occ-2'}, 'ksb_mappings': []}
        activity = {**component, 'id': 'c2', 'type': 'video', 'ksb_mappings': ['K3']}
        other_week = {**activity, 'id': 'c3', 'week_id': 'w3', 'ksb_mappings': ['S4']}
        unrelated = {**activity, 'id': 'c4', 'module_catalogue_id': 'other', 'ksb_mappings': ['K99']}
        result = build_lectures([row], {}, [], [component, activity, other_week, unrelated], 'commercial', 12)[0]
        self.assertEqual(result['componentHref'], '/learner/component/commercial/12/c1')
        self.assertEqual((result['ksbs'], result['ksbScope']), (['K3'], 'activities'))
        result = build_lectures([row], {}, [], [activity, other_week, unrelated], 'commercial', 12)[0]
        self.assertEqual((result['ksbs'], result['ksbScope']), (['K3', 'S4'], 'module'))
        self.assertEqual(result['activities'], [])
        result = build_lectures([row], {}, [], [{**component, 'week_id': None}, {**activity, 'week_id': None}], 'commercial', 12)[0]
        self.assertEqual((result['ksbs'], result['ksbScope']), (['K3'], 'module'))
        self.assertEqual([a['id'] for a in result['activities']], ['c1'])

    def test_native_recurrence_matches_occurrence_not_just_meeting(self):
        row = register_row(source='microsoft-teams', session_id='occ-2', module_catalogue_id='m', live_session_id='live')
        first = {'id': 'c1', 'type': 'live_session', 'module_catalogue_id': 'm',
                 'settings': {'teamsLiveSessionId': 'live', 'teamsOccurrenceId': 'occ-1'}}
        second = {**first, 'id': 'c2', 'settings': {'teamsLiveSessionId': 'live', 'teamsOccurrenceId': 'occ-2'}}
        self.assertIs(_component_for(row, [first, second]), second)
        self.assertIsNone(_component_for(row, [first]))
        self.assertIsNone(_component_for(row, [second, dict(second)]))

    def test_stale_meeting_title_does_not_hide_a_distinct_historical_course(self):
        now = timezone.now()
        day = timezone.localdate() - timedelta(days=7)
        scheduled = register_row(source='microsoft-teams', session_id='occ-current',
            occurrence_id='occ-current', module_catalogue_id='current-module',
            module_title='Current programme', session_date=day, attendance_status='pending',
            scheduled_start=now-timedelta(days=7, hours=2), scheduled_end=now-timedelta(days=7))
        evidence = {**scheduled, 'module_title': 'Historical programme',
                    'attendance_status': 'present', 'attended_seconds': 600,
                    'attendance_report_id': 'saved-report'}
        historical = register_row(session_id='historic-1', module_title='Historical programme',
                                  session_date=day, attendance_status='absent')
        mirror = register_row(session_id='current-mirror', module_title='Current programme',
                              session_date=day, attendance_status='present')
        records = [evidence, historical, mirror]
        before = [dict(row) for row in records]
        register = lecture_register(SimpleNamespace(id=12), records=records,
                                    scheduled=[scheduled], apply_adjustments=False)
        self.assertEqual({row['session_id'] for row in register}, {'occ-current', 'historic-1'})
        current = next(row for row in register if row['session_id'] == 'occ-current')
        self.assertEqual(current['module_title'], 'Current programme')
        self.assertEqual(current['attendance_status'], 'present')
        self.assertEqual(current['attended_seconds'], 600)
        self.assertEqual(current['attendance_report_id'], 'saved-report')
        self.assertEqual(records, before)
        for kind in ('commercial', 'apprenticeship'):
            with self.subTest(kind=kind):
                lectures = build_lectures(register, {}, [], [], kind, 12)
                by_id = {row['moduleId']: row for row in lectures}
                self.assertEqual(set(by_id), {'native:current-module', 'legacy:historical programme'})
                self.assertEqual(by_id['native:current-module']['monthlyLog']['sourceRef'], 'attendance:occ-current')
                self.assertEqual(by_id['legacy:historical programme']['monthlyLog']['sourceRef'], 'att:historic-1')
                self.assertEqual(by_id['legacy:historical programme']['status'], 'absent')

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_native_schedule_deduplicates_reports_and_excludes_cancelled(self, combined, scheduled):
        now = timezone.now()
        ended = register_row(source='microsoft-teams', session_id='occ-1',
                             attendance_status='pending', scheduled_start=now-timedelta(hours=2),
                             scheduled_end=now-timedelta(hours=1))
        scheduled.return_value = [{**ended, 'attendance_status': 'pending'}]
        combined.return_value = [ended, {**ended, 'session_id': 'cancelled'}]
        source = SimpleNamespace(id=12)
        result = lecture_register(source)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['attendance_status'], 'unmarked')

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_only_ended_pending_teams_sessions_become_unmarked(self, combined, scheduled):
        now = timezone.now()
        ended = register_row(source='microsoft-teams', session_id='ended', attendance_status='pending',
                             scheduled_start=now-timedelta(hours=2), scheduled_end=now-timedelta(hours=1))
        active = register_row(source='microsoft-teams', session_id='active', attendance_status='in_progress',
                              scheduled_start=now-timedelta(minutes=30), scheduled_end=now+timedelta(minutes=30))
        attended = {**ended, 'session_id': 'attended', 'attendance_status': 'present'}
        scheduled.return_value = [ended, active, attended]
        combined.return_value = [ended, active, attended]
        result = {row['session_id']: row['attendance_status'] for row in lecture_register(SimpleNamespace(id=12))}
        self.assertEqual(result, {'ended': 'unmarked', 'active': 'in_progress', 'attended': 'present'})

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_ended_teams_lecture_without_an_attendance_report_can_be_reported(self, combined, scheduled):
        # Regression: before its Teams report syncs an ended lecture is 'unmarked',
        # and it vanished from the absence form and could not be submitted.
        now = timezone.now()
        ended = register_row(source='microsoft-teams', session_id='occ-1', occurrence_id='occ-1',
                             module_catalogue_id='mod-1', live_session_id='live-1', session_number=1,
                             attendance_status='pending', attendance_report_id=None,
                             session_date=timezone.localdate() - timedelta(days=2),
                             scheduled_start=now-timedelta(days=2, hours=2), scheduled_end=now-timedelta(days=2))
        scheduled.return_value = [ended]
        combined.return_value = []
        register = lecture_register(SimpleNamespace(id=12))
        self.assertEqual(register[0]['attendance_status'], 'unmarked')
        self.assertTrue(_can_report_absence(register[0]))
        with patch('learner_api.absence_reports.lecture_register', return_value=register), \
             patch('learner_api.absence_reports.eligible_alternatives_by_occurrence', return_value={}):
            form = _fetch_missed_sessions(SimpleNamespace(id=12), 12)
        self.assertEqual([(item['sessionId'], item['status']) for item in form], [('teams:occ-1', 'unmarked')])

        conn = MagicMock()
        with patch('learner_api.attendance_lectures.lecture_register', return_value=register), \
             patch('learner_api.attendance_lectures.read_legacy_metadata', return_value=({}, [])), \
             patch('learner_api.attendance_lectures.read_native_components', return_value=([], [])), \
             patch('learner_api.attendance_lectures.read_assigned_modules', return_value=[]), \
             patch('learner_api.attendance_mode.read_mode', return_value={'mode': 'live', 'requestedMode': None, 'updatedAt': None}), \
             patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
            lecture = read_workspace(SimpleNamespace(id=12, email='learner@example.test'), 'apprenticeship')['lectures'][0]
        self.assertEqual(lecture['status'], 'unmarked')
        self.assertTrue(lecture['canReportAbsence'])

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_kbc_and_teams_record_of_one_lecture_count_once(self, combined, scheduled):
        now = timezone.now()
        day = now.date() - timedelta(days=7)
        teams = register_row(source='microsoft-teams', session_id='occ-1', module_title='Martech - Thur',
                             module_catalogue_id='mod-1', session_date=day, attendance_status='pending',
                             scheduled_start=now-timedelta(days=7, hours=2), scheduled_end=now-timedelta(days=7))
        kbc = register_row(session_id='kbc-1', module_title='Martech  -  Thur', session_date=day,
                           attendance_status='present')
        other_module = register_row(session_id='kbc-2', module_title='Social Media', session_date=day,
                                    attendance_status='present')
        scheduled.return_value = [teams]
        combined.return_value = [kbc, other_module]
        rows = lecture_register(SimpleNamespace(id=12))
        self.assertEqual(sorted(row['session_id'] for row in rows), ['kbc-2', 'occ-1'])
        merged = next(row for row in rows if row['session_id'] == 'occ-1')
        self.assertEqual(merged['attendance_status'], 'unmarked')
        self.assertEqual(build_lectures([merged], {}, [], [], 'apprenticeship', 12)[0]['durationMinutes'], 120)
        summary = _summarize_attendance(rows)
        self.assertEqual((summary['sessions'], summary['present']), (1, 1))

    def test_recorded_learner_live_attendance_needs_no_recording_and_counts_once(self):
        for kind in ('commercial', 'apprenticeship'):
            for watched in (False, True):
                with self.subTest(kind=kind, watched=watched):
                    row = register_row(source='microsoft-teams', session_id='occ-1',
                        module_catalogue_id='mod-1', attendance_status='present',
                        raw_attendance_status='present', effective_attendance=1)
                    source = SimpleNamespace(id=12, email='learner@example.test', attendance_type='Recorded')
                    progress = [{'componentId': 'recording-1', 'status': 'completed'}] if watched else []
                    with patch('learner_api.attendance_lectures.lecture_register', return_value=[row]), \
                         patch('learner_api.attendance_lectures.read_legacy_metadata', return_value=({}, [])), \
                         patch('learner_api.attendance_lectures.read_native_components', return_value=([], progress)), \
                         patch('learner_api.attendance_lectures.read_assigned_modules', return_value=[]), \
                         patch('learner_api.attendance_mode.read_mode', return_value={
                             'mode': 'lazy', 'requestedMode': None, 'updatedAt': None}), \
                         patch('learner_api.attendance_lectures.connections', {'enrolment': MagicMock()}), \
                         patch('learner_api.attendance_lectures.dict_rows', return_value=[]):
                        result = read_workspace(source, kind)
                    self.assertEqual(result['mode']['mode'], 'lazy')
                    self.assertEqual((result['totals']['total'], result['totals']['attended'], result['totals']['absent']), (1, 1, 0))
                    lecture = result['lectures'][0]
                    self.assertEqual(lecture['status'], 'present')
                    self.assertIsNone(lecture['catchupStatus'])
                    self.assertIsNone(lecture['recovery'])
                    self.assertFalse(lecture['canReportAbsence'])

    def test_completed_teams_catchup_is_shown_as_covered(self):
        row = register_row(source='microsoft-teams', session_id='occ-1', module_catalogue_id='mod-1',
                           attendance_status='absent', catchup_completed=True, effective_attendance=1)
        lecture = build_lectures([row], {}, [], [], 'apprenticeship', 12)[0]
        self.assertEqual((lecture['status'], lecture['catchupStatus'], lecture['effectiveAttendance']),
                         ('present', 'completed', 1))
        pending = build_lectures([{**row, 'catchup_completed': False, 'effective_attendance': 0}],
                                 {}, [], [], 'apprenticeship', 12)[0]
        self.assertEqual(pending['catchupStatus'], 'pending')

    def _alternative_register(self, guest_status):
        now = timezone.now()
        original = register_row(source='microsoft-teams', session_id='occ-1', module_catalogue_id='mod-1',
                                attendance_status='absent', session_date=now.date() - timedelta(days=7),
                                scheduled_start=now-timedelta(days=7, hours=2), scheduled_end=now-timedelta(days=7))
        guest = {**original, 'session_id': 'occ-alt', 'module_title': 'Other group',
                 'eligibility_reason': 'approved_recovery_guest', 'attendance_status': guest_status}
        with patch('learner_api.attendance_lectures.read_native_occurrences', return_value=[original]), \
             patch('learner_api.attendance_lectures.combined_attendance_rows', return_value=[original, guest]), \
             patch('learner_api.attendance_lectures._approved_alternative_targets',
                   return_value={str(report_id(original)): 'occ-alt'}):
            return lecture_register(SimpleNamespace(id=12))

    def test_attended_alternative_session_makes_up_the_original_absence(self):
        rows = self._alternative_register('present')
        self.assertEqual([row['session_id'] for row in rows], ['occ-1'])
        self.assertEqual((rows[0]['attendance_status'], rows[0]['final_outcome'], rows[0]['catchup_completed']),
                         ('absent', 'made_up', True))
        summary = _summarize_attendance(rows)
        self.assertEqual((summary['sessions'], summary['present'], summary['absent']), (1, 1, 0))

    def test_missed_alternative_session_leaves_the_absence_unrecovered(self):
        rows = self._alternative_register('absent')
        self.assertFalse(rows[0].get('catchup_completed'))
        self.assertEqual(_summarize_attendance(rows)['absent'], 1)

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_kbc_row_is_kept_when_teams_match_is_ambiguous(self, combined, scheduled):
        now = timezone.now()
        day = now.date() + timedelta(days=7)
        first = register_row(source='microsoft-teams', session_id='occ-1', session_date=day,
                             attendance_status='upcoming', scheduled_start=now+timedelta(days=7),
                             scheduled_end=now+timedelta(days=7, hours=2))
        scheduled.return_value = [first, {**first, 'session_id': 'occ-2'}]
        combined.return_value = [register_row(session_id='kbc-1', session_date=day)]
        self.assertEqual(len(lecture_register(SimpleNamespace(id=12))), 3)

    @patch('learner_api.attendance_lectures.read_native_occurrences')
    @patch('learner_api.attendance_lectures.combined_attendance_rows')
    def test_future_zero_attendance_is_upcoming(self, combined, scheduled):
        scheduled.return_value = []
        combined.return_value = [register_row(session_date=timezone.localdate()+timedelta(days=1))]
        self.assertEqual(lecture_register(SimpleNamespace(id=12))[0]['attendance_status'], 'upcoming')

    def test_legacy_query_is_aptem_scoped_and_uses_exact_source_reference(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', side_effect=[[], [{'group_id': 20, 'group_name': 'Business'}], []]):
            read_legacy_metadata(SimpleNamespace(aptem_id='92', id=12), [register_row()])
        first_sql, first_params = cur.execute.call_args_list[0].args
        self.assertIn("r.source_ref='att:' || la.source_key", first_sql)
        self.assertIn('r.month AS log_month', first_sql)
        self.assertEqual(first_params, [92, ['92_2026-09-01_business']])
        self.assertEqual(cur.execute.call_args_list[1].args[1], [92])
        self.assertEqual(cur.execute.call_args_list[2].args[1], [12, 92, [20]])

    def test_historical_only_register_skips_curriculum_but_keeps_recent_progress(self):
        progress = [{'componentId': 'c', 'componentTitle': 'Recent reading', 'submittedAt': '2026-09-01'}]
        with patch('learner_api.attendance_lectures.connections') as databases, \
             patch('learner_api.attendance_lectures._direct_progress_records', return_value=progress):
            self.assertEqual(read_native_components(SimpleNamespace(id=12), set()), ([], progress))
        databases.__getitem__.assert_not_called()

    def test_native_only_register_does_not_load_historical_materials(self):
        with patch('learner_api.attendance_lectures.connections') as databases:
            self.assertEqual(read_legacy_metadata(SimpleNamespace(aptem_id=92), [register_row(source='microsoft-teams')]), ({}, []))
        databases.__getitem__.assert_not_called()

    def test_native_activity_query_intersects_invited_and_assigned_modules(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value = [('assigned',), ('unrelated',)]
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', return_value=[]), \
             patch('learner_api.attendance_lectures._direct_progress_records', return_value=[]):
            read_native_components(SimpleNamespace(id=12), {'assigned', 'not-assigned'})
        self.assertEqual(cur.execute.call_args.args[1], [['assigned']])

    def test_ambiguous_groups_skip_materials_without_choosing_another_course(self):
        conn = MagicMock()
        groups = [{'group_id': 20, 'group_name': 'Level 7 Procurement'}, {'group_id': 21, 'group_name': 'Level 6 Procurement'}]
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', side_effect=[[], groups]):
            self.assertEqual(read_legacy_metadata(SimpleNamespace(aptem_id=92, id=12), [register_row(module_title='Procurement')]), ({}, []))
        self.assertEqual(conn.cursor.return_value.__enter__.return_value.execute.call_count, 2)

    def test_lecture_group_filter_keeps_exact_audit_group_and_ignores_other_courses(self):
        row = register_row(module_title='Renamed course')
        metadata = {'source_key': row['session_id'], 'group_id': 21}
        groups = [{'group_id': 20, 'group_name': 'Unrelated'}, {'group_id': 21, 'group_name': 'Old course title'}]
        conn = MagicMock()
        with patch('learner_api.attendance_lectures.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_lectures.dict_rows', side_effect=[[metadata], groups, []]):
            read_legacy_metadata(SimpleNamespace(aptem_id=92, id=12), [row])
        self.assertEqual(conn.cursor.return_value.__enter__.return_value.execute.call_args.args[1], [12, 92, [21]])

    def test_module_resolution_is_shared_but_lecture_dates_keep_their_own_activities(self):
        from . import attendance_lectures as views
        first, second = register_row(), register_row(session_id='second', session_date=date(2026, 9, 8))
        activities = [{'group_id': 20, 'group_name': 'Business', 'activity_id': index, 'title': 'Recording',
                       'activity_date': row['session_date'], 'activity_type': 'video', 'video_completed': index == 1}
                      for index, row in enumerate([first, second], 1)]
        with patch.object(views, '_legacy_module_activities', wraps=views._legacy_module_activities) as resolve_module:
            result = build_lectures([first, second], {}, activities, [], 'commercial', 12)
        resolve_module.assert_called_once()
        self.assertEqual([lecture['activities'][0]['activityId'] for lecture in result], [1, 2])
        self.assertEqual([lecture['catchupStatus'] for lecture in result], ['completed', 'pending'])


class UnreportedLectureCatchupTests(SimpleTestCase):
    def test_missed_lecture_without_a_teams_report_is_made_up_by_its_completed_catchup(self):
        from .attendance_lectures import _apply_completed_catchups
        rows = [
            {'source': 'microsoft-teams', 'session_id': 'OCC-1', 'attendance_status': 'absent'},
            {'source': 'microsoft-teams', 'session_id': 'OCC-2', 'attendance_status': 'absent'},
            {'source': 'microsoft-teams', 'session_id': 'OCC-3', 'attendance_status': 'absent', 'effective_attendance': 0},
            {'source': 'microsoft-teams', 'session_id': 'OCC-4', 'attendance_status': 'present'},
        ]
        with patch('learner_api.attendance_lectures._completed_catchup_occurrences',
                   return_value={'OCC-1', 'OCC-3'}) as ledger:
            result = _apply_completed_catchups(rows, 12)
        self.assertEqual([row.get('effective_attendance') for row in result], [1, None, 0, None])
        self.assertTrue(result[0]['catchup_completed'])
        self.assertEqual(result[0]['attendance_status'], 'absent')
        self.assertEqual(ledger.call_args.args, (12, {'OCC-1', 'OCC-2'}))


class RecordingPlanViewsTests(SimpleTestCase):
    def test_recording_plan_shows_how_much_of_the_lecture_recording_was_watched(self):
        from .attendance_lectures import _attach_recording_views
        lectures = [
            {'sessionId': 'teams:OCC-1', 'source': 'microsoft-teams', 'recovery': {'method': 'recorded'}},
            {'sessionId': 'teams:OCC-2', 'source': 'microsoft-teams', 'recovery': {'method': 'recorded'}},
            {'sessionId': 'teams:OCC-3', 'source': 'microsoft-teams', 'recovery': {'method': 'catch-up'}},
        ]
        with patch('curriculum_api.recording_views.watched_by_occurrence',
                   return_value={'OCC-1': (2820, 7200)}) as watched:
            _attach_recording_views(lectures, 'apprenticeship', 101)
        watched.assert_called_once_with('apprenticeship', 101, ['OCC-1', 'OCC-2'])
        self.assertEqual(lectures[0]['recovery'], {'method': 'recorded', 'watchedSeconds': 2820, 'recordingSeconds': 7200})
        self.assertEqual(lectures[1]['recovery'], {'method': 'recorded', 'watchedSeconds': 0, 'recordingSeconds': 0})
        self.assertEqual(lectures[2]['recovery'], {'method': 'catch-up'})


class AlternativeRecoveryDetailsTests(SimpleTestCase):
    TARGET = {'dateIso': '2026-09-30', 'startTime': '09:00', 'endTime': '11:00', 'group': 'Group B',
              'title': 'MM21 — Session 9', 'joinUrl': 'https://teams.example/alt'}

    def details(self, local_now):
        from .attendance_lectures import _alternative_recovery
        with patch('learner_api.alternative_recovery.alternative_target_details', return_value=dict(self.TARGET)):
            return _alternative_recovery({'id': 4, 'catchup_event_key': 'alternative:OCC-9'},
                                         now=timezone.make_aware(local_now))

    def test_upcoming_alternative_links_to_the_calendar_without_a_join_link(self):
        recovery = self.details(datetime(2026, 9, 29, 12, 0))
        self.assertEqual(recovery['calendarKey'], 'absence-alternative:4')
        self.assertEqual((recovery['date'], recovery['startTime'], recovery['endTime'], recovery['group']),
                         ('2026-09-30', '09:00', '11:00', 'Group B'))
        self.assertNotIn('joinUrl', recovery)
        self.assertFalse(recovery['ended'])

    def test_join_link_opens_ten_minutes_before_and_closes_after_the_end(self):
        self.assertEqual(self.details(datetime(2026, 9, 30, 8, 50))['joinUrl'], 'https://teams.example/alt')
        self.assertNotIn('joinUrl', self.details(datetime(2026, 9, 30, 8, 49)))
        ended = self.details(datetime(2026, 9, 30, 11, 1))
        self.assertNotIn('joinUrl', ended)
        self.assertTrue(ended['ended'])


class AttendanceRecoveryDisplayTests(SimpleTestCase):
    def test_each_reported_lecture_carries_its_plan_and_where_to_open_it(self):
        from datetime import time
        from .attendance_lectures import _attach_recovery
        lectures = [{'reportId': str(n)} for n in range(1, 5)]
        reported = {
            '1': {'id': 11, 'recovery_method': 'catch-up', 'catchup_event_key': 'catch-up:6'},
            '2': {'id': 22, 'recovery_method': 'alternative', 'catchup_event_key': 'alternative:9'},
            '3': {'id': 33, 'recovery_method': 'recorded', 'catchup_event_key': ''},
        }
        rows = [
            {'event_key': 'catch-up:6', 'scheduled_date': date(2026, 9, 26), 'scheduled_time': time(10, 40),
             'duration_minutes': 30, 'meeting_link': 'https://teams.example/catch-up', 'status': 'scheduled'},
            {'event_key': 'absence-recording:33', 'scheduled_date': date(2026, 9, 27), 'scheduled_time': time(18, 0),
             'duration_minutes': 60, 'meeting_link': '', 'status': 'scheduled'},
        ]
        with patch('coach_api.models.CoachCalendarEvent.objects') as events,                 patch('learner_api.alternative_recovery.alternative_target_details', return_value=None):
            events.filter.return_value.values.return_value = rows
            _attach_recovery(lectures, reported, now=timezone.make_aware(datetime(2026, 9, 26, 10, 35)))
        self.assertEqual(lectures[0]['recovery'], {
            'method': 'catch-up', 'date': '2026-09-26', 'calendarKey': 'catch-up:6', 'startTime': '10:40',
            'endTime': '11:10', 'ended': False, 'joinUrl': 'https://teams.example/catch-up'})
        self.assertEqual(lectures[1]['recovery'],
                         {'method': 'alternative', 'date': None, 'calendarKey': 'absence-alternative:22'})
        self.assertEqual(lectures[2]['recovery'], {
            'method': 'recorded', 'date': '2026-09-27', 'calendarKey': 'absence-recording:33',
            'startTime': '18:00', 'endTime': '19:00', 'ended': False})
        self.assertIsNone(lectures[3]['recovery'])
        self.assertEqual(set(events.filter.call_args.kwargs['event_key__in']), {'catch-up:6', 'absence-recording:33'})


class AttendanceAbsenceTests(SimpleTestCase):
    def setUp(self):
        # Keep recording plans in the future relative to a deterministic clock.
        self.clock = self.enterContext(patch(
            'learner_api.absence_reports.timezone.localtime',
            return_value=datetime(2026, 9, 30, 9, tzinfo=datetime_timezone.utc),
        ))

    def test_reason_only_report_saves_without_optional_evidence(self):
        request = RequestFactory().post('/', {'sessionId': 'teams:future', 'sessionTitle': 'Lecture',
                                             'sessionDate': '2026-10-01', 'reasonCategory': 'illness',
                                             'recoveryMethod': 'recorded', 'recordingDate': '2026-10-02',
                                             'recordingTime': '18:00', 'recordingDurationMinutes': '60'})
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        with patch('learner_api.absence_reports._source_learner', return_value=source), \
             patch('learner_api.absence_reports._resolve_absent_attendance', return_value=(8000000000000000001, 'future')), \
             patch('learner_api.absence_reports.CoachAbsenceReport.objects') as manager, \
             patch('learner_api.absence_reports.learner_profile_for_source', return_value=SimpleNamespace(id=99)), \
             patch('learner_api.absence_reports.record_reported_absence') as record_absence, \
             patch('learner_api.absence_reports.transaction.atomic', return_value=nullcontext()), \
             patch('learner_api.absence_reports._recording_recovery_event') as calendar_event, \
             patch('learner_api.absence_reports.email_azure.send_mail', return_value=(True, 'sent')) as send_mail, \
             patch('learner_api.absence_reports._send_after_commit', side_effect=run_now), \
             patch('learner_api.absence_reports.resolve_read_url', return_value=''):
            manager.filter.return_value.exists.return_value = False
            manager.filter.return_value.count.return_value = 0
            manager.create.side_effect = lambda **fields: SimpleNamespace(
                id=1, created_at=self.clock.return_value, updated_at=self.clock.return_value, **fields,
            )
            response = inspect.unwrap(learner_absence_reports)(request, 'apprenticeship', 12)
        self.assertEqual(response.status_code, 201)
        payload = json.loads(response.content)
        self.assertEqual(payload['id'], 1)
        self.assertEqual(payload['attendanceId'], '8000000000000000001')
        self.assertEqual(payload['reference'], 'AR-0001')
        self.assertEqual(payload['status'], 'approved')
        self.assertEqual((payload['evidenceProvided'], payload['evidenceKind'],
                          payload['evidenceUrl'], payload['evidenceText']), (False, 'none', '', ''))
        self.assertFalse(manager.create.call_args.kwargs['evidence_provided'])
        self.assertEqual(manager.create.call_args.kwargs['evidence_kind'], 'none')
        self.assertEqual(manager.create.call_args.kwargs['status'], 'approved')
        self.assertEqual(manager.create.call_args.kwargs['recovery_method'], 'recorded')
        self.assertIsNone(manager.create.call_args.kwargs['catchup_event_key'])
        calendar_event.assert_called_once()
        record_absence.assert_called_once()
        send_mail.assert_called_once()
        self.assertIn('Watch the recording', send_mail.call_args.kwargs['text_body'])
        self.assertNotIn('Catch-up session', send_mail.call_args.kwargs['text_body'])
        self.assertNotIn('Alternative group session', send_mail.call_args.kwargs['text_body'])

    def test_a_catchup_report_can_be_saved_before_its_booking_links_to_it(self):
        request = RequestFactory().post('/', {'sessionId': 'teams:future', 'sessionTitle': 'Lecture',
                                             'sessionDate': '2026-10-01', 'reasonCategory': 'illness',
                                             'recoveryMethod': 'catch-up', 'catchupPending': '1'})
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        with patch('learner_api.absence_reports._source_learner', return_value=source), \
             patch('learner_api.absence_reports._resolve_absent_attendance', return_value=(8000000000000000001, 'future')), \
             patch('learner_api.absence_reports.CoachAbsenceReport.objects') as manager, \
             patch('learner_api.absence_reports.learner_profile_for_source', return_value=SimpleNamespace(id=99)), \
             patch('learner_api.absence_reports.record_reported_absence') as record_absence, \
             patch('learner_api.absence_reports.transaction.atomic', return_value=nullcontext()), \
             patch('learner_api.absence_reports._catchup_booking') as booking, \
             patch('learner_api.absence_reports.email_azure.send_mail', return_value=(True, 'sent')), \
             patch('learner_api.absence_reports._send_after_commit', side_effect=run_now), \
             patch('learner_api.absence_reports._serialize', return_value={'id': 1}):
            manager.filter.return_value.exists.return_value = False
            manager.filter.return_value.count.return_value = 0
            manager.create.return_value = SimpleNamespace(
                id=1, owner_email='coach@example.test', owner_name='Coach', learner_name='Learner',
                learner_email='learner@example.test', session_title='Lecture', recovery_method='catch-up',
            )
            response = inspect.unwrap(learner_absence_reports)(request, 'apprenticeship', 12)
        self.assertEqual(response.status_code, 201)
        booking.assert_not_called()
        self.assertEqual(manager.create.call_args.kwargs['recovery_method'], 'catch-up')
        self.assertIsNone(manager.create.call_args.kwargs['catchup_event_key'])
        # The ledger gets the catch-up from the booking that links to this report.
        self.assertEqual(record_absence.call_args.kwargs['recovery_method'], '')

    def submit_duplicate(self, saved):
        request = RequestFactory().post('/', {'sessionId': 'same', 'sessionTitle': 'Lecture',
                                             'sessionDate': '2026-09-01', 'reasonCategory': 'illness',
                                             'recoveryMethod': 'recorded', 'recordingDate': '2026-10-02',
                                             'recordingTime': '18:00'})
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        with patch('learner_api.absence_reports._source_learner', return_value=source), \
             patch('learner_api.absence_reports._resolve_absent_attendance', return_value=(123, '')), \
             patch('learner_api.absence_reports.CoachAbsenceReport.objects') as manager, \
             patch('learner_api.absence_reports._serialize', side_effect=lambda report: {'id': report.id}), \
             patch('learner_api.absence_reports._send_after_commit') as notify:
            manager.filter.return_value.exists.return_value = True
            manager.filter.return_value.first.return_value = saved
            response = inspect.unwrap(learner_absence_reports)(request, 'apprenticeship', 12)
        manager.create.assert_not_called()
        notify.assert_not_called()
        return response

    def test_duplicate_report_for_another_learner_is_rejected_before_writing(self):
        response = self.submit_duplicate(SimpleNamespace(id=7, learner_id=99))
        self.assertEqual(response.status_code, 409)

    def test_retried_report_returns_the_learners_saved_report(self):
        # The first attempt was saved after the proxy had timed out the request.
        response = self.submit_duplicate(SimpleNamespace(id=7, learner_id=12))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'id': 7})

    def save(self, *, profiles, create=None):
        request = RequestFactory().post('/', {'sessionId': 'teams:occ-1', 'sessionTitle': 'Lecture',
                                             'sessionDate': '2026-10-01', 'reasonCategory': 'illness',
                                             'recoveryMethod': 'recorded', 'recordingDate': '2026-10-02',
                                             'recordingTime': '18:00'})
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        with patch('learner_api.absence_reports._source_learner', return_value=source), \
             patch('learner_api.absence_reports._resolve_absent_attendance', return_value=(8000000000000000001, 'occ-1')), \
             patch('learner_api.absence_reports.CoachAbsenceReport.objects') as manager, \
             patch('learner_api.absence_reports.learner_profile_for_source', side_effect=profiles) as lookup, \
             patch('learner_api.absence_reports.record_reported_absence') as record_absence, \
             patch('learner_api.absence_reports.transaction.atomic', return_value=nullcontext()), \
             patch('learner_api.absence_reports._recording_recovery_event'), \
             patch('learner_api.absence_reports._send_after_commit'), \
             patch('learner_api.absence_reports._serialize', return_value={'id': 1}):
            manager.filter.return_value.exists.return_value = False
            manager.filter.return_value.count.return_value = 0
            manager.create.side_effect = create
            response = inspect.unwrap(learner_absence_reports)(request, 'apprenticeship', 12)
        return response, lookup, record_absence

    def test_lecture_absence_links_a_profile_that_is_not_marked_active(self):
        paused = SimpleNamespace(id=99, coach_name='Coach', coach_email='coach@example.test')
        response, lookup, record_absence = self.save(profiles=[None, paused])
        self.assertEqual(response.status_code, 201)
        self.assertTrue(lookup.call_args_list[0].kwargs['active_only'])
        self.assertNotIn('active_only', lookup.call_args_list[1].kwargs)
        self.assertEqual(record_absence.call_args.kwargs['learner_profile_id'], 99)

    def test_failed_save_without_evidence_does_not_mention_an_upload(self):
        profile = SimpleNamespace(id=99, coach_name='Coach', coach_email='coach@example.test')
        with self.assertLogs('learner_api.absence_reports', 'ERROR'):
            response, _, record_absence = self.save(profiles=[profile], create=RuntimeError('database unavailable'))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.content)['error'], 'The absence report could not be saved. Please retry.')
        record_absence.assert_not_called()

    def test_profile_lookup_failure_is_a_json_error(self):
        with self.assertLogs('learner_api.absence_reports', 'ERROR'):
            response, _, _ = self.save(profiles=RuntimeError('database unavailable'))
        self.assertEqual(response.status_code, 502)
        self.assertIn('learner profile', json.loads(response.content)['error'])

    def test_notification_email_runs_after_commit_off_the_request_and_never_raises(self):
        from . import absence_reports as absence
        task = MagicMock(side_effect=RuntimeError('Graph timed out'), __name__='_email_coach_recovery')
        with patch.object(absence.transaction, 'on_commit') as on_commit, \
             patch.object(absence.threading, 'Thread') as thread, \
             patch.object(absence.connections, 'close_all') as close_all:
            absence._send_after_commit(task, 'report', recovery_details='Monday')
            task.assert_not_called()
            on_commit.call_args.args[0]()
            self.assertTrue(thread.call_args.kwargs['daemon'])
            thread.return_value.start.assert_called_once()
            task.assert_not_called()
            with self.assertLogs('learner_api.absence_reports', 'ERROR'):
                thread.call_args.kwargs['target']()
        task.assert_called_once_with('report', recovery_details='Monday')
        close_all.assert_called_once()

    @patch('learner_api.absence_reports.lecture_register')
    def test_new_and_old_reportable_sessions_use_source_ids(self, read):
        read.return_value = [register_row(), register_row(source='microsoft-teams', session_id='upcoming', attendance_status='upcoming'),
                             register_row(session_id='present', attendance_status='present'), register_row(attendance_status='pending')]
        source = SimpleNamespace(id=12)
        with patch('learner_api.absence_reports.eligible_alternatives_by_occurrence',
                   return_value={'upcoming': [{'id': 'OCC-ALT'}]}) as alternatives:
            result = _fetch_missed_sessions(source, 12)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['sessionId'], 'teams:upcoming')
        # One batched lookup for every Teams lecture, not one per lecture.
        alternatives.assert_called_once_with(['upcoming'])
        self.assertEqual(result[1]['alternativeSessions'], [{'id': 'OCC-ALT'}])
        self.assertIsNotNone(_resolve_absent_attendance(source, 12, 'teams:upcoming', 'Business introduction', date(2026, 9, 1), None))
        self.assertIsNone(_resolve_absent_attendance(source, 12, 'present', 'Business introduction', date(2026, 9, 1), None))
        self.assertIsNone(_resolve_absent_attendance(source, 12, 'teams:other', 'Business introduction', date(2026, 9, 1), None))


class AttendanceLearnerLookupTests(SimpleTestCase):
    """Merged learner IDs work with either nominal kind in a saved URL."""

    def setUp(self):
        self.source = SimpleNamespace(id=12, learner_type='commercial')
        self.model = MagicMock()
        self.model.objects.filter.return_value.first.return_value = None
        self.model.all_learners.filter.return_value.first.return_value = self.source
        self.model.all_learners.filter.return_value.only.return_value.first.return_value = self.source
        self.models = {kind: self.model for kind in ('commercial', 'apprenticeship')}
        self.account = SimpleNamespace(role='learner', subject_id=12)

    def test_lecture_route_reads_commercial_learner_for_either_kind(self):
        with patch('learner_api.attendance_lectures.SOURCE_MODELS', self.models), \
             patch('learner_api.attendance_lectures.read_workspace', return_value={'lectures': []}) as read, \
             patch('login.permissions._auth_gate_enabled', return_value=True), \
             patch('login.permissions.authenticate_request', return_value=self.account):
            for kind in self.models:
                with self.subTest(kind=kind):
                    path = f'/learner_api/attendance/{kind}/12/lectures/'
                    match = resolve(path)
                    response = match.func(RequestFactory().get(path), **match.kwargs)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(json.loads(response.content)['lectures'], [])
                    self.assertTrue(json.loads(response.content)['csrfToken'])
                    read.assert_called_with(self.source, kind)
                    self.model.all_learners.filter.return_value.only.assert_called_with(*ATTENDANCE_SOURCE_FIELDS)

    def test_mode_route_reads_commercial_learner_for_either_kind(self):
        with patch('learner_api.attendance_mode.SOURCE_MODELS', self.models), \
             patch('learner_api.attendance_mode.read_mode', return_value={'mode': 'live'}) as read, \
             patch('login.permissions._auth_gate_enabled', return_value=True), \
             patch('login.permissions.authenticate_request', return_value=self.account):
            for kind in self.models:
                with self.subTest(kind=kind):
                    path = f'/learner_api/attendance/{kind}/12/mode/'
                    match = resolve(path)
                    response = match.func(RequestFactory().get(path), **match.kwargs)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(json.loads(response.content), {'mode': 'live'})
                    read.assert_called_with(self.source, kind)

    def test_another_learners_routes_are_rejected_before_lookup(self):
        with patch('learner_api.attendance_lectures.SOURCE_MODELS', self.models), \
             patch('learner_api.attendance_mode.SOURCE_MODELS', self.models), \
             patch('login.permissions._auth_gate_enabled', return_value=True), \
             patch('login.permissions.authenticate_request', return_value=self.account):
            for endpoint in ('lectures', 'mode'):
                path = f'/learner_api/attendance/commercial/99/{endpoint}/'
                match = resolve(path)
                response = match.func(RequestFactory().get(path), **match.kwargs)
                self.assertEqual(response.status_code, 404)
            self.model.all_learners.filter.assert_not_called()

    def test_absence_lookup_accepts_either_nominal_kind(self):
        from .absence_reports import _source_learner
        with patch('learner_api.absence_reports.EnrolmentUser', self.model), \
             patch('learner_api.absence_reports.CommercialUser', self.model):
            for kind in self.models:
                self.assertIs(_source_learner(kind, 12), self.source)
            self.assertIsNone(_source_learner('unknown', 12))


class AttendanceModeTests(SimpleTestCase):
    def test_manager_review_post_requires_csrf(self):
        with patch('learner_api.attendance_mode._state') as state:
            response = review_attendance_mode(RequestFactory().post('/', {'token': 'anything', 'decision': 'approve'}))
        self.assertEqual(response.status_code, 403)
        state.assert_not_called()

    def test_only_the_signed_review_path_is_public(self):
        from login.api_gate import rule_for
        self.assertIsNone(rule_for('/learner_api/attendance-mode/review/'))
        self.assertIsNotNone(rule_for('/learner_api/attendance/apprenticeship/12/mode/'))
        self.assertIsNotNone(rule_for('/learner_api/attendance/apprenticeship/12/lectures/'))
        self.assertIsNotNone(rule_for('/learner_api/attendance-mode/review/extra/'))

    def test_approval_preview_does_not_write_and_consumed_tokens_cannot_replay(self):
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        model = MagicMock()
        model.all_learners.filter.return_value.first.return_value = source
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        state = {'request_id': 'request-one', 'status': 'pending', 'manager_email': 'manager@example.test'}
        token = signing.dumps({'learner': 12, 'kind': 'apprenticeship', 'request': 'request-one'}, salt=SALT)
        with patch('learner_api.attendance_mode.SOURCE_MODELS', {'apprenticeship': model}), \
             patch('learner_api.attendance_mode.connections', {'enrolment': conn}), \
             patch('learner_api.attendance_mode.transaction.atomic', return_value=nullcontext()), \
             patch('learner_api.attendance_mode._state', return_value=state), \
             patch('learner_api.attendance_mode._manager', return_value='manager@example.test'):
            response = review_attendance_mode(RequestFactory().get('/', {'token': token}))
            self.assertEqual(response.status_code, 200)
            cur.execute.assert_not_called()
            post = RequestFactory().post('/', {'token': token, 'decision': 'approve'})
            post._dont_enforce_csrf_checks = True
            response = review_attendance_mode(post)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(cur.execute.call_args.args[1], ['lazy', 'approved', 12])
            self.assertIn('request_id=NULL', cur.execute.call_args.args[0])
            state.update(status='approved', request_id=None)
            cur.reset_mock()
            response = review_attendance_mode(post)
            self.assertEqual(response.status_code, 400)
            cur.execute.assert_not_called()

    def test_unsigned_and_expired_approval_tokens_do_not_read_or_write_data(self):
        with patch('learner_api.attendance_mode._state') as state:
            response = review_attendance_mode(RequestFactory().get('/', {'token': 'invalid'}))
            self.assertEqual(response.status_code, 400)
            state.assert_not_called()
            with patch('django.core.signing.time.time', return_value=0):
                token = signing.dumps({'learner': 12, 'kind': 'apprenticeship', 'request': 'old'}, salt=SALT)
            response = review_attendance_mode(RequestFactory().get('/', {'token': token}))
            self.assertEqual(response.status_code, 400)
            state.assert_not_called()

    def _post_mode(self, body, role, before):
        """Drive the mode view past its auth gate with the enrolment DB mocked."""
        from .attendance_mode import attendance_mode
        view = inspect.unwrap(attendance_mode)
        source = SimpleNamespace(id=12, username='Learner', email='learner@example.test')
        model = MagicMock()
        model.all_learners.filter.return_value.first.return_value = source
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        request = RequestFactory().post('/', json.dumps(body), content_type='application/json',
                                        HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        request.login_account = SimpleNamespace(role=role)
        with patch('learner_api.attendance_mode.SOURCE_MODELS', {'apprenticeship': model}),              patch('learner_api.attendance_mode.connections', {'enrolment': conn}),              patch('learner_api.attendance_mode.transaction.atomic', return_value=nullcontext()),              patch('learner_api.attendance_mode._ready', return_value=True),              patch('learner_api.attendance_mode._state', side_effect=[before, {**before, 'mode': body['mode'],
                   'requested_mode': None, 'status': 'active'}]),              patch('learner_api.attendance_mode._manager', return_value='manager@example.test') as manager,              patch('learner_api.attendance_mode.is_configured', return_value=True),              patch('learner_api.attendance_mode._send_request', return_value=True) as send:
            response = view(request, kind='apprenticeship', learner_id=12)
        return response, cur, manager, send

    def test_admin_direct_recorded_mode_applies_without_manager_approval_or_email(self):
        before = {'mode': 'live', 'requested_mode': 'lazy', 'status': 'pending', 'email_sent': True,
                  'request_id': 'old', 'updated_at': timezone.now()}
        response, cur, manager, send = self._post_mode({'mode': 'lazy', 'direct': True}, 'admin', before)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['mode'], 'lazy')
        update = cur.execute.call_args_list[-1]
        self.assertIn("status='active'", update.args[0])
        self.assertIn('request_id=NULL', update.args[0])
        self.assertEqual(update.args[1], ['lazy', 12])
        manager.assert_not_called()
        send.assert_not_called()

    def test_learner_cannot_skip_manager_approval(self):
        before = {'mode': 'live', 'status': 'active', 'email_sent': False}
        response, cur, manager, send = self._post_mode({'mode': 'lazy', 'direct': True}, 'learner', before)
        self.assertEqual(response.status_code, 403)
        cur.execute.assert_not_called()
        send.assert_not_called()

    def test_learner_recorded_request_still_asks_the_manager(self):
        before = {'mode': 'live', 'status': 'active', 'email_sent': False, 'request_id': None}
        response, cur, manager, send = self._post_mode({'mode': 'lazy'}, 'learner', before)
        self.assertEqual(response.status_code, 200)
        self.assertIn("status='pending'", cur.execute.call_args_list[-1].args[0])
        manager.assert_called_once()
        send.assert_called_once()

    def test_bulk_mode_read_is_staff_or_admin_and_returns_saved_modes(self):
        from .attendance_mode import attendance_modes
        conn = MagicMock()
        saved = [{'learner_id': 12, 'mode': 'lazy', 'requested_mode': None, 'status': 'active'},
                 {'learner_id': 13, 'mode': 'live', 'requested_mode': 'lazy', 'status': 'pending',
                  'updated_at': timezone.now()}]
        with patch('learner_api.attendance_mode.connections', {'enrolment': conn}),              patch('learner_api.attendance_mode._ready', return_value=True),              patch('learner_api.attendance_mode.rows', return_value=saved):
            with patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='learner')):
                self.assertEqual(attendance_modes(RequestFactory().get('/')).status_code, 403)
            conn.cursor.assert_not_called()
            with patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='staff')):
                self.assertEqual(attendance_modes(RequestFactory().get('/')).status_code, 200)
            with patch('login.permissions.authenticate_request', return_value=SimpleNamespace(role='admin')):
                response = attendance_modes(RequestFactory().get('/'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'available': True, 'modes': {
            '12': {'mode': 'lazy', 'requestedMode': None},
            '13': {'mode': 'live', 'requestedMode': 'lazy'},
        }})
        self.assertEqual(resolve('/learner_api/attendance-mode/').func.__name__, 'attendance_modes')

    def test_pending_pauses_reminders_but_keeps_live_allocation_until_approval(self):
        payload = _payload({'mode': 'live', 'requested_mode': 'lazy', 'status': 'pending', 'updated_at': timezone.now()})
        self.assertFalse(payload['remindersEnabled'])
        self.assertEqual(payload['mode'], 'live')
        self.assertEqual(payload['requestedMode'], 'lazy')

    def test_expired_requests_resume_live_reminders_and_can_be_requested_again(self):
        payload = _payload({'mode': 'live', 'requested_mode': 'lazy', 'status': 'pending',
                            'updated_at': timezone.now()-timedelta(seconds=MAX_AGE+1)})
        self.assertTrue(payload['remindersEnabled'])
        self.assertIsNone(payload['requestedMode'])
        self.assertEqual(payload['status'], 'expired')

    def test_approved_mode_pauses_and_declined_live_mode_resumes(self):
        self.assertFalse(_payload({'mode': 'lazy', 'status': 'approved'})['remindersEnabled'])
        self.assertTrue(_payload({'mode': 'live', 'status': 'declined'})['remindersEnabled'])

    @patch('learner_api.attendance_mode._extended_ilr_answers')
    def test_manager_cannot_be_the_requesting_learner(self, answers):
        source = SimpleNamespace(id=12, employer_id=None, email='learner@example.test')
        answers.return_value = {'employer': {'lineManagerEmail': ' Learner@example.test '}}
        self.assertEqual(_manager(source), '')
        answers.return_value = {'employer': {'lineManagerEmail': 'manager@example.test'}}
        self.assertEqual(_manager(source), 'manager@example.test')

    def test_reminders_skip_reported_covered_future_and_old_absences(self):
        today = date(2026, 9, 12)
        row = {'id': 'a', 'status': 'absent', 'catchupStatus': 'pending', 'absenceReport': None, 'date': '2026-09-11'}
        lectures = [row, {**row, 'absenceReport': {'id': 1}}, {**row, 'catchupStatus': 'completed'},
                    {**row, 'date': '2026-01-01'}, {**row, 'date': '2026-09-13'}, {**row, 'status': 'upcoming'}]
        self.assertEqual(reminder_candidates(lectures, {'remindersEnabled': True}, today), [row])
        self.assertEqual(reminder_candidates(lectures, {'remindersEnabled': False}, today), [])
