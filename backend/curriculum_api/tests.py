import json
import os
import threading
from datetime import date, datetime, timezone
from unittest.mock import patch
from types import SimpleNamespace

from django.db import connection
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from coach_api.tests_support.transaction_cases import CoachTransactionTestCase

from . import tutor_notifications, views
from .ksb_coverage import build_coverage


class ModuleAuthoringValidationTests(SimpleTestCase):
    def test_component_row_ksb_mappings_are_serialised_for_structure_payloads(self):
        row = {
            'ksb_mappings': json.dumps([{
                'id': 'MAP-1',
                'ksbId': 'K7',
                'code': 'K7',
                'description': 'Understand safe escalation.',
                'classification': 'main',
                'weightClass': 'hard',
                'weight': 50,
            }]),
        }

        mappings = [
            views.component_ksb_mapping_api_item(mapping)
            for mapping in views.component_ksb_mappings_from_row(row, [])
        ]

        self.assertEqual(mappings[0]['code'], 'K7')
        self.assertEqual(mappings[0]['weight'], 50)
        self.assertEqual(mappings[0]['weightClass'], 'hard')

    def test_uploaded_podcast_resource_url_is_valid_for_authoring_save(self):
        payload = {
            'title': 'Module',
            'weekStructure': [{
                'title': 'Week 1',
                'components': [{
                    'title': 'Uploaded podcast',
                    'type': 'podcast',
                    'expectedOtjh': 2,
                    'points': 15,
                    'reflectionRequired': False,
                    'workplaceEvidenceRequired': False,
                    'settings': {
                        'podcastSource': 'Audio File',
                        'podcastUrl': '/curriculum_api/curriculum/uploads/week-template/show.mp3',
                        'uploadedFileUrl': '/curriculum_api/curriculum/uploads/week-template/show.mp3',
                        'uploadedFileName': 'show.mp3',
                    },
                }],
            }],
        }

        errors = views.validate_module_authoring_payload(payload)

        self.assertFalse(
            any(item['path'] == 'weekStructure.0.components.0.settings.podcastUrl' for item in errors),
            errors,
        )

    def test_uploaded_reading_resource_url_is_valid_for_authoring_save(self):
        payload = {
            'title': 'Module',
            'weekStructure': [{
                'title': 'Week 1',
                'components': [{
                    'title': 'Uploaded reading',
                    'type': 'reading',
                    'expectedOtjh': 2,
                    'points': 15,
                    'reflectionRequired': False,
                    'workplaceEvidenceRequired': False,
                    'settings': {
                        'readingSource': 'File',
                        'resourceUrl': '/curriculum_api/curriculum/uploads/week-template/reading.pdf',
                        'uploadedFileUrl': '/curriculum_api/curriculum/uploads/week-template/reading.pdf',
                        'uploadedFileName': 'reading.pdf',
                    },
                }],
            }],
        }

        errors = views.validate_module_authoring_payload(payload)

        self.assertFalse(
            any(item['path'] == 'weekStructure.0.components.0.settings.resourceUrl' for item in errors),
            errors,
        )


class ComponentOwnedKsbMappingTests(TestCase):
    def setUp(self):
        views.reset_schema_ready_flags()
        views.ensure_module_authoring_tables()
        for table in (
            views.AUTHORING_KSB_MAPPINGS_TABLE,
            views.AUTHORING_COMPONENTS_TABLE,
            views.AUTHORING_WEEKS_TABLE,
            views.AUTHORING_MODULES_TABLE,
        ):
            views.authoring_delete(table)

    def _component_row(self, mappings):
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': 'MOD-KSB-SOT',
            'programme_id': 'PROG-KSB-SOT',
            'programme_name': 'Programme',
            'title': 'Module',
        })
        views.authoring_upsert(views.AUTHORING_WEEKS_TABLE, ['id'], {
            'id': 'WEEK-KSB-SOT',
            'module_catalogue_id': 'MOD-KSB-SOT',
            'week_number': 1,
            'title': 'Week',
        })
        return views.authoring_upsert(views.AUTHORING_COMPONENTS_TABLE, ['id'], {
            'id': 'COMP-KSB-SOT',
            'week_id': 'WEEK-KSB-SOT',
            'module_catalogue_id': 'MOD-KSB-SOT',
            'type': 'reading',
            'title': 'Component',
            'expected_otjh': 3,
            'points': 10,
            'ksb_mappings': views.json_db_value(mappings),
        })

    @patch('curriculum_api.views.ksb_exists_in_source', return_value=True)
    @patch('curriculum_api.views.source_record_exists', return_value=True)
    def test_weight_class_validation_requires_allowed_enum(self, _source, _ksb):
        valid = {
            'code': 'K1',
            'sourceType': 'standard',
            'sourceId': 'STD-1',
            'classification': 'main',
            'weight': 50,
            'weightClass': 'hard',
        }
        self.assertEqual(views.validate_ksb_mapping_payload(valid, 'mapping'), [])

        invalid = {**valid, 'weightClass': 'medium'}
        errors = views.validate_ksb_mapping_payload(invalid, 'mapping')
        self.assertTrue(any(error['path'] == 'mapping.weight_class' for error in errors))

    def test_component_stores_multiple_ksbs_with_distinct_weight_classes(self):
        row = self._component_row([
            {'id': 'MAP-1', 'ksb_code': 'K1', 'weight': 50, 'weight_class': 'hard'},
            {'id': 'MAP-2', 'ksb_code': 'S4', 'weight': 30, 'weight_class': 'soft'},
            {'id': 'MAP-3', 'ksb_code': 'B2', 'weight': 20, 'weight_class': 'possible'},
        ])

        mappings = views.component_ksb_mappings_from_row(row)
        self.assertEqual([mapping['ksb_code'] for mapping in mappings], ['K1', 'S4', 'B2'])
        self.assertEqual([mapping['weight'] for mapping in mappings], [50, 30, 20])
        self.assertEqual([mapping['weight_class'] for mapping in mappings], ['hard', 'soft', 'possible'])
        self.assertEqual(float(row['expected_otjh']), 3)

    def test_projection_sync_is_derived_from_component_source(self):
        row = self._component_row([
            {
                'id': 'MAP-1',
                'ksb_code': 'K1',
                'source_type': 'standard',
                'source_id': 'STD-1',
                'classification': 'main',
                'weight': 50,
                'weight_class': 'hard',
            },
            {
                'id': 'MAP-2',
                'ksb_code': 'S4',
                'source_type': 'standard',
                'source_id': 'STD-1',
                'classification': 'secondary',
                'weight': 30,
                'weight_class': 'soft',
            },
        ])

        views.sync_component_ksb_projection('COMP-KSB-SOT', component_row=row)
        rows = views.active_mapping_rows(views.authoring_fetch_all(
            views.AUTHORING_KSB_MAPPINGS_TABLE,
            'component_id = %s',
            ['COMP-KSB-SOT'],
            'ksb_code',
        ))

        self.assertEqual([row['ksb_code'] for row in rows], ['K1', 'S4'])
        self.assertEqual([float(row['weight']) for row in rows], [50, 30])
        self.assertEqual([row['weight_class'] for row in rows], ['hard', 'soft'])


class CrossCohortGroupNameCollisionTests(SimpleTestCase):
    """Group names repeat across cohorts, so identity matching must use ids.

    Regression cover for a wizard save that landed a module on a same-named
    group in a different cohort. The target group was then left with no module
    rows, which the read path reports as an unassigned tutor and a "TBD"
    delivery window because both are derived from the child modules.
    """

    AUG_GROUP = 'GROUP-AUG-0001'
    SEP_GROUP = 'GROUP-SEP-0001'
    AUG_COHORT = 'COHORT-AUG-0001'
    SEP_COHORT = 'COHORT-SEP-0001'

    def test_ids_conflict_when_both_sides_name_different_rows(self):
        self.assertTrue(views.identity_ids_conflict(self.SEP_GROUP, [self.AUG_GROUP]))
        self.assertFalse(views.identity_ids_conflict(self.SEP_GROUP, [self.SEP_GROUP]))

    def test_ids_do_not_conflict_when_either_side_is_unknown(self):
        # A legacy row with no id, or a caller that only knows the name, must
        # stay on the permissive name-based path rather than being rejected.
        self.assertFalse(views.identity_ids_conflict('', [self.AUG_GROUP]))
        self.assertFalse(views.identity_ids_conflict(self.SEP_GROUP, []))
        self.assertFalse(views.identity_ids_conflict(self.SEP_GROUP, ['', None]))

    def test_shared_group_name_alone_still_satisfies_the_name_matcher(self):
        # Documents *why* the id guard is needed: the pooled id+name comparison
        # cannot tell two cohorts' "G1-Wed" apart on its own.
        self.assertTrue(views.identity_values_match_context(
            [self.SEP_GROUP, 'G1-Wed'],
            [self.AUG_GROUP, 'G1-Wed'],
        ))

    def test_differing_cohort_ids_are_rejected_by_the_id_guard(self):
        self.assertTrue(views.identity_ids_conflict(self.SEP_COHORT, [self.AUG_COHORT]))


class CurriculumGroupModuleMatchingTests(SimpleTestCase):
    def test_staff_profile_name_ignores_placeholder_values(self):
        self.assertEqual(views.staff_profile_name({'name': 'EMPTY_STRING', 'email': 'EMPTY_STRING'}), '')
        self.assertEqual(views.staff_profile_email({'email': 'EMPTY_STRING'}), '')
        self.assertEqual(views.staff_profile_name({'name': 'Unassigned'}), '')
        self.assertTrue(views.is_blank_staff_assignment('EMPTY_STRING'))

    def test_blank_staff_profiles_are_not_returned_even_with_assignments(self):
        tutor_profiles = views.build_staff_profiles(
            [{
                'id': 'TUTOR-BLANK',
                'name': 'EMPTY_STRING',
                'email': 'EMPTY_STRING',
                'is_archived': False,
            }],
            'tutor',
            modules=[],
            groups=[],
        )
        coach_profiles = views.build_staff_profiles(
            [{
                'id': 'COACH-BLANK',
                'name': 'EMPTY_STRING',
                'email': 'EMPTY_STRING',
                'is_archived': False,
            }],
            'coach',
            modules=[],
            groups=[],
        )
        self.assertEqual(tutor_profiles, [])
        self.assertEqual(coach_profiles, [])

    def test_staff_user_profile_source_maps_access_row_to_curriculum_profile(self):
        source = views.staff_user_profile_source(
            {
                'id': 7,
                'uuid': '76d901ea-885f-4aa0-83d1-111111111111',
                'Username': 'Tutor One',
                'Email': 'tutor.one@example.com',
                'Phone_number': '07700 900123',
                'Position': 'Tutor',
                ' Status': 'FullUser',
                'Access': 'Tutor',
            },
            'tutor',
        )

        self.assertEqual(source['id'], '76d901ea-885f-4aa0-83d1-111111111111')
        self.assertEqual(source['staffUserId'], '7')
        self.assertEqual(source['name'], 'Tutor One')
        self.assertEqual(source['email'], 'tutor.one@example.com')
        self.assertEqual(source['phone'], '07700 900123')
        self.assertEqual(source['job_title'], 'Tutor')
        self.assertEqual(source['access'], 'tutor')
        self.assertFalse(source['is_archived'])

    @patch('curriculum_api.views.build_cohorts_and_groups', return_value=([], []))
    @patch('curriculum_api.views.get_program_config_rows', return_value=[])
    @patch('curriculum_api.views.get_module_rows', return_value=[])
    @patch('curriculum_api.views.get_training_rows', return_value=[])
    @patch('curriculum_api.views.fetch_staff_users_by_access')
    def test_staff_profile_collection_reads_staff_users_access(
        self,
        fetch_staff_users,
        _training_rows,
        _module_rows,
        _program_rows,
        _cohort_rows,
    ):
        fetch_staff_users.return_value = [
            {
                'id': 8,
                'uuid': 'a4832ae2-3b9e-43c5-b622-222222222222',
                'Username': 'Coach One',
                'Email': 'coach.one@example.com',
                'Phone_number': '',
                'Position': 'Coach',
                ' Status': 'FullUser',
                'Access': 'coach',
            },
        ]

        profiles = views.build_staff_user_profile_collection('coach')

        fetch_staff_users.assert_called_once_with('coach')
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]['id'], 'a4832ae2-3b9e-43c5-b622-222222222222')
        self.assertEqual(profiles[0]['role'], 'coach')
        self.assertEqual(profiles[0]['name'], 'Coach One')
        self.assertEqual(profiles[0]['access'], 'coach')
        self.assertEqual(profiles[0]['staffUserId'], '8')

    def test_module_belongs_to_group_uses_stored_module_ids(self):
        group = {
            'id': 'GROUP-1',
            'name': 'G1',
            'moduleIds': ['MOD-20260805143221794F2NN00'],
            'modules': ['Module'],
        }
        module = {
            'id': 'module-MOD-20260805143221794F2NN00',
            'moduleCatalogueId': 'MOD-20260805143221794F2NN00',
            'name': 'Renamed in catalogue',
            'groupId': '',
            'group': '',
        }

        self.assertTrue(views.module_belongs_to_group(module, group))

    def test_stored_module_ids_win_over_stale_module_group_context(self):
        group = {
            'id': 'GROUP-1',
            'name': 'G1',
            'moduleIds': ['MOD-20260805143221794F2NN00'],
            'modules': [],
        }
        module = {
            'moduleCatalogueId': 'MOD-20260805143221794F2NN00',
            'name': 'Module',
            'groupId': 'GROUP-OLD',
            'group': 'Old group',
        }

        self.assertTrue(views.module_belongs_to_group(module, group))

    def test_group_membership_uses_module_group_id_even_when_stored_module_ids_are_stale(self):
        group = {
            'id': 'GROUP-1',
            'moduleIds': ['MOD-KEEP'],
        }
        modules = [
            {'moduleCatalogueId': 'MOD-KEEP', 'groupId': 'GROUP-1'},
            {'moduleCatalogueId': 'MOD-STALE', 'groupId': 'GROUP-1'},
        ]

        filtered = [
            module for module in modules
            if views.module_belongs_to_group(module, group)
        ]

        self.assertEqual([module['moduleCatalogueId'] for module in filtered], ['MOD-KEEP', 'MOD-STALE'])


#: The stored holidays the cohort fixture below ticks two of. Christmas 27 closes
#: the Saturdays of 19 and 26 December, which is what pushes that module into the
#: new year.
CURRICULUM_HOLIDAY_ROWS = [
    {'id': 1080, 'label': 'Christmas 27', 'start_date': date(2026, 12, 19), 'end_date': date(2027, 1, 1), 'type': 'bank-holidays'},
    {'id': 1082, 'label': 'Easter 27', 'start_date': date(2027, 3, 21), 'end_date': date(2027, 3, 29), 'type': 'bank-holidays'},
    {'id': 1083, 'label': 'June 27', 'start_date': date(2027, 5, 30), 'end_date': date(2027, 6, 5), 'type': 'workshop'},
]


class TeamsMultiDayRecurrenceTests(SimpleTestCase):
    """A group delivering twice a week gets a recurrence that fires twice a week."""

    @staticmethod
    def _payload(dates):
        return {
            'scheduledOccurrences': [
                {'sessionNumber': index + 1, 'startDateTimeUtc': value, 'durationMinutes': 120}
                for index, value in enumerate(dates)
            ],
        }

    def test_recurrence_covers_every_delivery_day(self):
        # Mon + Thu, ten sessions from Monday 3 August 2026.
        dates = [
            '2026-08-03T08:00:00Z', '2026-08-06T08:00:00Z',
            '2026-08-10T08:00:00Z', '2026-08-13T08:00:00Z',
            '2026-08-17T08:00:00Z', '2026-08-20T08:00:00Z',
            '2026-08-24T08:00:00Z', '2026-08-27T08:00:00Z',
            '2026-08-31T08:00:00Z', '2026-09-03T08:00:00Z',
        ]
        payload = self._payload(dates)
        anchor = datetime(2026, 8, 3, 8, 0)
        days = views.teams_recurrence_weekdays(payload, anchor)
        self.assertEqual(days, ['monday', 'thursday'])
        slots = views.teams_recurrence_slot_count(payload, anchor, 'weekly', len(dates), days)
        self.assertEqual(slots, 10)
        recurrence = views.teams_event_recurrence('weekly', anchor, slots, days)
        self.assertEqual(recurrence['pattern']['daysOfWeek'], ['monday', 'thursday'])
        self.assertEqual(recurrence['range']['startDate'], '2026-08-03')
        self.assertEqual(recurrence['range']['numberOfOccurrences'], 10)

    def test_single_delivery_day_is_unchanged(self):
        dates = [
            '2026-07-30T12:30:00Z', '2026-08-06T12:30:00Z', '2026-08-13T12:30:00Z',
            '2026-08-20T12:30:00Z', '2026-08-27T12:30:00Z', '2026-09-03T12:30:00Z',
        ]
        payload = self._payload(dates)
        anchor = datetime(2026, 7, 30, 12, 30)
        days = views.teams_recurrence_weekdays(payload, anchor)
        self.assertEqual(days, ['thursday'])
        self.assertEqual(
            views.teams_recurrence_slot_count(payload, anchor, 'weekly', len(dates), days),
            6,
        )

    def test_holiday_shift_stretches_the_range(self):
        # Thursday 20 August is closed, so that session moves to the next Monday
        # slot and the run reaches a week further out than ten sessions would.
        dates = [
            '2026-08-03T08:00:00Z', '2026-08-06T08:00:00Z',
            '2026-08-10T08:00:00Z', '2026-08-13T08:00:00Z',
            '2026-08-17T08:00:00Z', '2026-08-24T08:00:00Z',
            '2026-08-27T08:00:00Z', '2026-08-31T08:00:00Z',
            '2026-09-03T08:00:00Z', '2026-09-07T08:00:00Z',
        ]
        payload = self._payload(dates)
        anchor = datetime(2026, 8, 3, 8, 0)
        days = views.teams_recurrence_weekdays(payload, anchor)
        self.assertEqual(days, ['monday', 'thursday'])
        # Eleven Mon/Thu slots between 3 August and 7 September: the extra one is
        # the closed 20 August, which the reconciliation deletes.
        self.assertEqual(
            views.teams_recurrence_slot_count(payload, anchor, 'weekly', len(dates), days),
            11,
        )

    def test_local_anchor_rebases_utc_session_dates(self):
        # The reschedule path anchors on naive local time while the sessions are
        # sent as UTC; an hour of BST must not move a session onto another day.
        dates = ['2026-08-03T08:00:00Z', '2026-08-06T08:00:00Z', '2026-08-10T08:00:00Z']
        days = views.teams_recurrence_weekdays(self._payload(dates), datetime(2026, 8, 3, 9, 0))
        self.assertEqual(days, ['monday', 'thursday'])


class TeamsAttendanceRosterTests(SimpleTestCase):
    def test_join_launches_override_closest_scheduled_week(self):
        occurrences = [
            {'id': 'WEEK-6', 'session_number': 6, 'scheduled_start': datetime(2026, 9, 7, 19)},
            {'id': 'WEEK-7', 'session_number': 7, 'scheduled_start': datetime(2026, 9, 14, 19)},
            {'id': 'WEEK-12', 'session_number': 12, 'scheduled_start': datetime(2026, 10, 26, 20)},
        ]
        launches = [
            {'id': 'JOIN-7', 'occurrence_id': 'WEEK-7', 'launched_at': datetime(2026, 9, 3, 10)},
            {'id': 'JOIN-12', 'occurrence_id': 'WEEK-12', 'launched_at': datetime(2026, 9, 3, 11)},
        ]

        week_7, launch_7 = views.launch_linked_live_occurrence(
            occurrences, datetime(2026, 9, 3, 10, 1), launches, 'REPORT-7'
        )
        week_12, launch_12 = views.launch_linked_live_occurrence(
            occurrences, datetime(2026, 9, 3, 11, 1), launches, 'REPORT-12'
        )

        self.assertEqual(week_7['id'], 'WEEK-7')
        self.assertEqual(launch_7['id'], 'JOIN-7')
        self.assertEqual(week_12['id'], 'WEEK-12')
        self.assertEqual(launch_12['id'], 'JOIN-12')

    def test_existing_report_launch_binding_wins_on_resync(self):
        occurrences = [
            {'id': 'WEEK-7', 'scheduled_start': datetime(2026, 9, 14, 19)},
            {'id': 'WEEK-12', 'scheduled_start': datetime(2026, 10, 26, 20)},
        ]
        launches = [
            {'id': 'JOIN-7', 'occurrence_id': 'WEEK-7', 'launched_at': datetime(2026, 9, 3, 10), 'attendance_report_id': 'REPORT-7'},
            {'id': 'JOIN-12', 'occurrence_id': 'WEEK-12', 'launched_at': datetime(2026, 9, 3, 10, 1)},
        ]

        occurrence, launch = views.launch_linked_live_occurrence(
            occurrences, datetime(2026, 9, 3, 10, 2), launches, 'REPORT-7'
        )

        self.assertEqual(occurrence['id'], 'WEEK-7')
        self.assertEqual(launch['id'], 'JOIN-7')

    def test_merges_distinct_rejoins_without_counting_a_resync_twice(self):
        first = [{'joinDateTime': '2026-09-02T09:00:00Z', 'leaveDateTime': '2026-09-02T09:10:00Z'}]
        second = [{'joinDateTime': '2026-09-03T09:00:00Z', 'leaveDateTime': '2026-09-03T09:05:00Z'}]

        merged = views.merge_attendance_intervals(first, second, first)

        self.assertEqual(len(merged), 2)
        self.assertEqual(views.attendance_interval_seconds(merged), 900)

    def test_adds_invited_non_attendees_to_completed_roster(self):
        roster = views.attendance_roster({
            'organizer_email': 'organizer@example.com',
            'attendees': ['present@example.com', 'absent@example.com'],
            'presenters': [],
        }, [{
            'id': 'ATT-1',
            'email': 'present@example.com',
            'display_name': 'Present',
            'total_attendance_seconds': 240,
            # Presence now requires more than three verified minutes.
            'intervals': [{
                'joinDateTime': '2026-09-02T09:00:00Z',
                'leaveDateTime': '2026-09-02T09:04:00Z',
            }],
        }], include_absent=True)

        by_email = {row['email']: row for row in roster}
        self.assertTrue(by_email['present@example.com']['attended'])
        self.assertFalse(by_email['absent@example.com']['attended'])
        self.assertFalse(by_email['organizer@example.com']['attended'])
        self.assertEqual(by_email['present@example.com']['join_count'], 1)

    def test_roster_requires_more_than_three_verified_minutes(self):
        series = {'attendees': ['learner@example.com']}
        for seconds, attended in [(0, False), (180, False), (181, True)]:
            with self.subTest(seconds=seconds):
                roster = views.attendance_roster(series, [{
                    'id': 'ATT-THRESHOLD',
                    'email': 'learner@example.com',
                    'total_attendance_seconds': seconds,
                    'intervals': [],
                }], include_absent=True)
                row = next(item for item in roster if item['email'] == 'learner@example.com')
                self.assertEqual(row['attended'], attended)

    def test_keeps_different_graph_reports_as_separate_attendance_runs(self):
        series = {
            'organizer_email': 'organizer@example.com',
            'attendees': ['learner@example.com'],
            'presenters': [],
        }
        rows = [
            {
                'id': 'ATT-DAY-1', 'email': 'organizer@example.com',
                'display_name': 'Organizer', 'intervals': [{}],
                'raw_data': {
                    'attendanceReportId': 'REPORT-1',
                    'attendanceReportStart': '2026-09-02T09:00:00Z',
                },
            },
            {
                'id': 'ATT-DAY-2', 'email': 'organizer@example.com',
                'display_name': 'Organizer', 'intervals': [{}],
                'raw_data': {
                    'attendanceReportId': 'REPORT-2',
                    'attendanceReportStart': '2026-09-03T09:00:00Z',
                },
            },
        ]

        roster = views.attendance_report_rosters(series, rows, include_absent=True)

        organizer_rows = [row for row in roster if row['email'] == 'organizer@example.com']
        learner_rows = [row for row in roster if row['email'] == 'learner@example.com']
        self.assertEqual(len(organizer_rows), 2)
        self.assertEqual(len(learner_rows), 2)
        self.assertEqual(
            {row['attendance_report_id'] for row in organizer_rows},
            {'REPORT-1', 'REPORT-2'},
        )
        self.assertEqual(len({row['id'] for row in learner_rows}), 2)


class CurriculumTeamsRequestTestCase(CoachTransactionTestCase):
    # HTTP middleware closes request connections. A TestCase outer atomic is
    # incompatible with that lifecycle and hides committed cross-alias reads.
    databases = {'default', 'enrolment'}

    def prepare_teams_fixtures(self, programme_id):
        from coach_api.tests_support.persisted_fixtures import curriculum_session, programme
        views.ensure_live_session_tracking_tables()
        # Unmanaged authoring tables are not included in Django's flush. These
        # suites own their synthetic authoring graph; delete children first.
        self.addCleanup(self.clear_teams_fixtures)
        self.clear_teams_fixtures()
        programme(programme_id)
        if programme_id == 'PROG-RESTORE':
            programme('PROG-SUMMARY')
            programme('PROG-WATCHED')
        self.account = curriculum_session(self, self.client)
        # Schedule delivery is outside these calendar/role tests. Preserve its
        # ledger/formatting behavior and replace only the outbound mail transport.
        mail = patch('curriculum_api.teams_schedule_delivery._send_message', return_value=('accepted', ''))
        mail.start()
        self.addCleanup(mail.stop)

    def clear_teams_fixtures(self):
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('DELETE FROM curriculum.session_result_jobs WHERE live_session_id IN '
                               '(SELECT id FROM curriculum.live_sessions)')
                cursor.execute('DELETE FROM curriculum.teams_calendar_create_claims WHERE module_key = %s',
                               ['draft:module-draft-1'])
        for table in (
            views.LIVE_SESSION_RECORDING_EVENTS_TABLE, views.LIVE_SESSION_ARTIFACTS_TABLE,
            views.LIVE_SESSION_ATTENDANCE_TABLE, views.LIVE_SESSION_JOIN_LAUNCHES_TABLE,
            views.LIVE_SESSION_OCCURRENCES_TABLE, views.LIVE_SESSIONS_TABLE,
            views.AUTHORING_KSB_MAPPINGS_TABLE, views.AUTHORING_COMPONENTS_TABLE,
            views.AUTHORING_WEEKS_TABLE, views.AUTHORING_COMPLETION_TABLE,
            views.AUTHORING_ADVANCED_TABLE, views.AUTHORING_MODULES_TABLE,
            views.COHORT_AUTHORING_DETAILS_TABLE,
        ):
            views.authoring_delete(table)
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM {views.authoring_table_name("programmes")} '
                           'WHERE programme_id IN (%s,%s,%s,%s)',
                           ['PROG-RESTORE', 'PROG-SYNC', 'PROG-SUMMARY', 'PROG-WATCHED'])


class CurriculumTeamsMeetingTests(CurriculumTeamsRequestTestCase):
    def setUp(self):
        self.client = Client()
        views.reset_schema_ready_flags()
        views.ensure_module_authoring_tables()
        views.ensure_live_sessions_table()
        self.prepare_teams_fixtures('PROG-RESTORE')
        # `.env` is read into os.environ at startup, and a deployment that pins
        # its Teams organizer would outrank the one every test below passes in.
        # Unpinned is the default here; the tests that mean to exercise a pinned
        # organizer set it themselves.
        organizer_env = patch.dict(os.environ, {
            'MICROSOFT_TEAMS_ORGANIZER_EMAIL': '',
            'MICROSOFT_ORGANIZER_EMAIL': '',
        })
        organizer_env.start()
        self.addCleanup(organizer_env.stop)

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_creates_calendar_backed_teams_meeting(self, _settings, _credentials, graph_request):
        # Answered by path rather than by position: the create path also
        # reconciles the series' individual occurrences, and a fixed list would
        # have to be re-counted every time that reconciliation changes shape.
        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(
            event_id='event-1', meeting_id='meeting-1', join_suffix='example',
            starts=['2026-07-30T12:30:00Z', '2026-08-06T12:30:00Z', '2026-08-13T12:30:00Z',
                    '2026-08-20T12:30:00Z', '2026-08-27T12:30:00Z', '2026-09-03T12:30:00Z'],
        )
        verification_boundaries = []
        verify_calendar = views.verify_teams_calendar_with_standalones

        def verified_calendar(*args, **kwargs):
            event = verify_calendar(*args, **kwargs)
            verification_boundaries.append(graph_request.call_count)
            return event

        verifier = patch.object(views, 'verify_teams_calendar_with_standalones', side_effect=verified_calendar)
        verifier.start()
        self.addCleanup(verifier.stop)
        response = self.client.post(
            '/curriculum_api/curriculum/teams-meetings/',
            data=json.dumps({
                'title': 'Risk workshop',
                'organizerEmail': 'tutor@example.com',
                'attendees': ['student1@example.com', 'student2@example.com'],
                'presenters': ['presenter@example.com'],
                'coOrganizers': ['co-organizer@example.com'],
                'localStartDateTime': '2026-07-30T15:30',
                'startDateTimeUtc': '2026-07-30T12:30:00.000Z',
                'durationMinutes': 60,
                'repeat': 'weekly',
                'repeatOccurrences': 6,
                'lobbyBypass': 'invited',
                'recording': 'record-transcribe',
                'spokenLanguage': 'en-GB',
                'requestResponses': True,
                'allowNewTimeProposals': True,
                'hideAttendees': False,
                'transactionId': 'TEAMS-TEST',
                'moduleDraftId': 'module-draft-1',
                'moduleTitle': 'Risk module',
                'scheduledOccurrences': [
                    {
                        'sessionNumber': index + 1,
                        'startDateTimeUtc': value,
                        'durationMinutes': 60,
                    }
                    for index, value in enumerate([
                        '2026-07-30T12:30:00Z',
                        '2026-08-06T12:30:00Z',
                        '2026-08-13T12:30:00Z',
                        '2026-08-20T12:30:00Z',
                        '2026-08-27T12:30:00Z',
                        '2026-09-03T12:30:00Z',
                    ])
                ],
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual(result['meeting']['organizerEmail'], 'tutor@example.com')
        self.assertEqual(result['meeting']['eventId'], 'event-1')
        self.assertTrue(result['meeting']['liveSessionId'].startswith('LIVE-'))
        self.assertIn('teams.microsoft.com', result['meeting']['joinUrl'])
        self.assertEqual(result['meeting']['repeatOccurrences'], 6)
        self.assertEqual(result['meeting']['trackedOccurrences'], 6)
        self.assertEqual(result['meeting']['onlineMeetingId'], 'meeting-1')
        self.assertTrue(result['meeting']['trackingReady'])
        self.assertTrue(result['meeting']['settingsApplied'])

        create_call = graph_request.call_args_list[0]
        self.assertEqual(create_call.args[:2], ('POST', 'users/tutor%40example.com/events'))
        event_payload = create_call.kwargs['payload']
        self.assertTrue(event_payload['isOnlineMeeting'])
        calls = graph_request.call_args_list
        self.assertEqual(calls[0].kwargs['payload']['attendees'], [])
        self.assertEqual(len(verification_boundaries), 2)  # Before and after publishing.
        publications = [
            (index, call) for index, call in enumerate(calls)
            if call.args[0] in {'POST', 'PATCH'} and call.kwargs.get('payload', {}).get('attendees')
        ]
        self.assertEqual(len(publications), 1)
        publish_index, publish_call = publications[0]
        self.assertEqual(publish_call.args[:2], ('PATCH', 'users/tutor%40example.com/events/event-1'))
        self.assertGreaterEqual(publish_index, verification_boundaries[0])
        self.assertGreater(verification_boundaries[1], publish_index)
        self.assertCountEqual(
            [entry['emailAddress']['address'] for entry in publish_call.kwargs['payload']['attendees']],
            ['student1@example.com', 'student2@example.com', 'presenter@example.com', 'co-organizer@example.com'],
        )
        # No other invitation route can precede successful calendar verification.
        self.assertFalse(any(call.args[1].endswith('/forward') for call in calls[:verification_boundaries[0]]))
        self.assertTrue(all(call.args[1].startswith('users/tutor%40example.com/') for call in calls))
        self.assertEqual(event_payload['recurrence']['range']['numberOfOccurrences'], 6)
        meeting_patch = next(
            call for call in graph_request.call_args_list
            if call.args[0] == 'PATCH' and '/onlineMeetings/' in call.args[1]
        ).kwargs['payload']
        self.assertEqual(meeting_patch['allowedPresenters'], 'roleIsPresenter')
        self.assertEqual(meeting_patch['lobbyBypassSettings'], {
            'scope': 'invited',
            'isDialInBypassEnabled': False,
        })
        self.assertTrue(meeting_patch['allowRecording'])
        self.assertTrue(meeting_patch['recordAutomatically'])
        self.assertTrue(meeting_patch['allowTranscription'])
        self.assertEqual(meeting_patch['meetingSpokenLanguageTag'], 'en-GB')
        presenter = next(item for item in meeting_patch['participants']['attendees'] if item['upn'] == 'presenter@example.com')
        self.assertEqual(presenter['role'], 'presenter')
        self.assertEqual(
            {item['upn']: item['role'] for item in meeting_patch['participants']['attendees']},
            {'student1@example.com': 'attendee', 'student2@example.com': 'attendee',
             'presenter@example.com': 'presenter', 'co-organizer@example.com': 'coorganizer'},
        )
        live_session = views.authoring_fetch_all(
            views.LIVE_SESSIONS_TABLE,
            'id = %s',
            [result['meeting']['liveSessionId']],
        )[0]
        self.assertEqual(live_session['module_draft_id'], 'module-draft-1')
        self.assertEqual(live_session['repeat_pattern'], 'weekly')
        self.assertEqual(live_session['repeat_occurrences'], 6)
        self.assertEqual(live_session['status'], 'active')
        self.assertEqual(live_session['online_meeting_id'], 'meeting-1')
        # presenters is jsonb on PostgreSQL but text on SQLite, so the raw row
        # is a list on one vendor and a JSON string on the other. Decode it the
        # way every production reader does rather than asserting one vendor's
        # physical storage encoding.
        self.assertEqual(views.as_json_value(live_session['presenters'], []), ['presenter@example.com'])
        self.assertEqual(views.as_json_value(live_session['co_organizers'], []), ['co-organizer@example.com'])
        self.assertEqual(
            views.as_json_value(live_session['attendees'], []),
            ['student1@example.com', 'student2@example.com'],
        )
        occurrences = views.authoring_fetch_all(
            views.LIVE_SESSION_OCCURRENCES_TABLE,
            'live_session_id = %s',
            [result['meeting']['liveSessionId']],
        )
        self.assertEqual(len(occurrences), 6)

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    def test_refuses_duplicate_active_calendar_before_touching_graph(self, _credentials, graph_request):
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-EXISTING',
            'module_draft_id': 'module-draft-duplicate',
            'module_title': 'Risk module',
            'organizer_email': 'khaled@example.com',
            'graph_event_id': 'event-existing',
            'join_url': 'https://teams.microsoft.com/l/meetup-join/existing',
            'status': 'active',
        })

        response = self.client.post(
            '/curriculum_api/curriculum/teams-meetings/',
            data=json.dumps({
                'title': 'A second calendar',
                'moduleDraftId': 'module-draft-duplicate',
                'organizerEmail': 'another@example.com',
                'localStartDateTime': '2026-09-03T09:00',
                'startDateTimeUtc': '2026-09-03T08:00:00Z',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['code'], 'teams_calendar_already_exists')
        self.assertEqual(response.json()['existingMeeting']['liveSessionId'], 'LIVE-EXISTING')
        self.assertEqual(response.json()['existingMeeting']['organizerEmail'], 'khaled@example.com')
        graph_request.assert_not_called()

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_schedule_update_keeps_wizard_dates_when_shifted_instances_fail(self, _settings, _credentials, graph_request):
        live_session_id = 'LIVE-SHIFT-WARNING'
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-1',
            'module_title': 'Risk module',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 6,
            'join_url': 'https://teams.microsoft.com/l/meetup-join/example',
            'web_link': 'https://outlook.office.com/calendar/item',
            'lobby_bypass': 'invited',
            'recording': 'record-transcribe',
            'spoken_language': 'en-GB',
            'status': 'active',
        })
        previous_dates = ['2026-09-02', '2026-09-09', '2026-09-16',
                          '2026-09-23', '2026-09-30', '2026-10-07']
        for number, day in enumerate(previous_dates, 1):
            views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['id'], {
                'id': f'OCC-SHIFT-{number}', 'live_session_id': live_session_id, 'session_number': number,
                'scheduled_start': day + 'T08:30:00Z', 'scheduled_end': day + 'T10:30:00Z',
                'graph_event_id': 'event-1', 'join_url': 'https://teams.microsoft.com/l/meetup-join/example',
                'status': 'scheduled',
            })
        previous = views.authoring_fetch_all(
            views.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_session_id], 'session_number asc',
        )
        series_before = views.authoring_fetch_all(views.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])[0]
        # These previously delivered slots are history, not future sessions being cancelled.
        dropped_sessions = views.sessions_a_rewrite_would_drop
        clock = patch.object(views, 'sessions_a_rewrite_would_drop', side_effect=lambda *args, **kwargs:
            dropped_sessions(*args, **kwargs, now=datetime(2026, 11, 1, tzinfo=timezone.utc)))
        clock.start()
        self.addCleanup(clock.stop)
        from copy import deepcopy
        graph_instances = [
            {'id': f'instance-{number}',
             'start': {'dateTime': day + 'T08:30:00Z', 'timeZone': 'UTC'},
             'end': {'dateTime': day + 'T10:30:00Z', 'timeZone': 'UTC'}}
            for number, day in enumerate(previous_dates, 1)
        ]

        def graph_side_effect(method, path, payload=None, **kwargs):
            if method in {'GET', 'PATCH'} and path == 'users/tutor%40example.com/events/event-1':
                return {
                    'id': 'event-1',
                    'webLink': 'https://outlook.office.com/calendar/item',
                    'onlineMeeting': {'joinUrl': 'https://teams.microsoft.com/l/meetup-join/example'},
                }
            if method == 'GET' and path.startswith('users/tutor%40example.com/events/event-1/instances?'):
                return {'value': deepcopy(graph_instances)}
            # All responses lie in the requested instances window. One shift
            # succeeds (instance-5 -> 21 Oct); Graph refuses instance-4 -> 14 Oct
            # and still returns it at 23 Sept. Refusal is not deletion.
            if method == 'PATCH' and path.endswith('/instance-4'):
                raise RuntimeError('ErrorOccurrenceCrossingBoundary')
            if method == 'PATCH' and '/events/instance-' in path:
                instance = next(item for item in graph_instances if path.endswith('/' + item['id']))
                instance.update(deepcopy(payload))
                return deepcopy(instance)
            if method == 'GET' and 'onlineMeetings?' in path:
                return {'value': [{'id': 'meeting-1'}]}
            if method in {'PATCH', 'POST', 'DELETE'}:
                return {}
            raise AssertionError(f'Unexpected Graph call: {method} {path}')

        graph_request.side_effect = graph_side_effect

        response = self.client.patch(
            f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/schedule/',
            data=json.dumps({
                'title': 'Risk module',
                'organizerEmail': 'tutor@example.com',
                'eventId': 'event-1',
                'localStartDateTime': '2026-09-02T09:30',
                'startDateTimeUtc': '2026-09-02T08:30:00Z',
                'durationMinutes': 120,
                'repeat': 'weekly',
                'repeatOccurrences': 6,
                'scheduledOccurrences': [
                    {
                        'sessionNumber': index + 1,
                        'startDateTimeUtc': value,
                        'durationMinutes': 120,
                    }
                    for index, value in enumerate([
                        '2026-09-02T08:30:00Z',
                        '2026-09-09T08:30:00Z',
                        '2026-09-16T08:30:00Z',
                        '2026-10-07T08:30:00Z',
                        '2026-10-14T08:30:00Z',
                        '2026-10-21T08:30:00Z',
                    ])
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 502, response.content)
        result = response.json()
        self.assertTrue(result['partial'])
        self.assertEqual(result['liveSessionId'], live_session_id)
        self.assertNotIn('meeting', result)  # No successful synchronization payload.
        failure = next(error for error in result['errors'] if error['code'] == 'teams_shifted_occurrence_not_moved')
        self.assertEqual(failure['detail'], 'ErrorOccurrenceCrossingBoundary')
        self.assertIn('ErrorOccurrenceCrossingBoundary', result['detail'])
        calls = graph_request.call_args_list
        self.assertTrue(any(call.args[:2] == ('PATCH', 'users/tutor%40example.com/events/instance-4') for call in calls))
        self.assertFalse(any(call.args[1].endswith('/cancel') for call in calls))
        occurrences = views.authoring_fetch_all(
            views.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_session_id], 'session_number asc',
        )
        # Preserve the complete saved rows, including IDs, dates, status and timestamps.
        self.assertEqual(occurrences, previous)
        self.assertEqual([row['id'] for row in occurrences], [f'OCC-SHIFT-{n}' for n in range(1, 7)])
        self.assertEqual([row['scheduled_start'].date().isoformat() for row in occurrences], previous_dates)
        self.assertEqual(occurrences[3]['status'], 'scheduled')
        self.assertFalse(any(views.clean_str(row.get('online_meeting_id')) for row in occurrences))
        self.assertTrue(all(row['graph_event_id'] == 'event-1' for row in occurrences))
        self.assertTrue(all(row['join_url'] == 'https://teams.microsoft.com/l/meetup-join/example' for row in occurrences))
        saved = views.authoring_fetch_all(views.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])[0]
        warnings = views.as_json_value(saved['warnings'], [])
        self.assertIn(failure, warnings)
        self.assertTrue(any(w['code'] == 'teams_calendar_unverified' for w in warnings))
        self.assertEqual(
            {key: value for key, value in saved.items() if key not in {'warnings', 'updated_at'}},
            {key: value for key, value in series_before.items() if key not in {'warnings', 'updated_at'}},
        )
        self.assertEqual(next(item for item in graph_instances if item['id'] == 'instance-4')['start']['dateTime'],
                         '2026-09-23T08:30:00Z')
        self.assertEqual(
            [call for call in calls if call.args[0] in {'POST', 'DELETE'}], [],
            'A refused shift must not create a replacement or cancel an occurrence.',
        )

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    def test_artifact_sync_backfills_online_meeting_id_from_join_url(self, _credentials, graph_request):
        live_session_id = 'LIVE-MISSING-MEETING-ID'
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'organizer_email': 'tutor@example.com',
            'join_url': 'https://teams.microsoft.com/l/meetup-join/example',
            'online_meeting_id': '',
            'recording': 'record-transcribe',
            'lobby_bypass': 'invited',
            'status': 'active',
        })
        views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['id'], {
            'id': 'OCC-MISSING-MEETING-ID', 'live_session_id': live_session_id, 'session_number': 1,
            'scheduled_start': '2026-09-01T09:00:00Z', 'scheduled_end': '2026-09-01T10:00:00Z',
            'status': 'scheduled',
        })
        meeting_path = 'users/tutor%40example.com/onlineMeetings/meeting-recovered'

        def graph_response(method, path, payload=None):
            if method == 'GET' and path.startswith('users/tutor%40example.com/onlineMeetings?'):
                return {'value': [{
                    'id': 'meeting-recovered',
                    'meetingOptionsWebUrl': 'https://teams.microsoft.com/meetingOptions/recovered',
                }]}
            if method == 'PATCH' and path == meeting_path:
                return {}
            if method == 'GET' and path == meeting_path:
                # The worker now reads the meeting's dial-in details before its
                # result collections; that read must not consume a collection.
                return {'id': 'meeting-recovered'}
            if method == 'GET' and path in {
                f'{meeting_path}/attendanceReports', f'{meeting_path}/transcripts', f'{meeting_path}/recordings',
            }:
                return {'value': []}
            raise AssertionError(f'Unexpected Graph request: {method} {path}')

        graph_request.side_effect = graph_response

        # The HTTP contract now queues durable work. Drive the real worker
        # synchronously while keeping Graph and external archive I/O mocked.
        from django.core.management import call_command
        from io import StringIO
        with patch('curriculum_api.session_results.start_requested_sync') as dispatch:
            queued = self.client.post(
                f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/artifacts/',
                data='{}', content_type='application/json',
            )
        self.assertEqual(queued.status_code, 202, queued.content)
        dispatch.assert_called_once_with(live_session_id)
        with patch('curriculum_api.management.commands.process_session_results.archive_series', return_value=[]):
            call_command('process_session_results', limit=1, live_session_ids=[live_session_id], stdout=StringIO())
        response = self.client.get(f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/artifacts/')
        self.assertEqual(response.status_code, 200, response.content)
        saved = views.authoring_fetch_all(
            views.LIVE_SESSIONS_TABLE,
            'id = %s',
            [live_session_id],
        )[0]
        self.assertEqual(saved['online_meeting_id'], 'meeting-recovered')
        self.assertEqual(
            saved['meeting_options_url'],
            'https://teams.microsoft.com/meetingOptions/recovered',
        )
        self.assertIn('users/tutor%40example.com/onlineMeetings?', graph_request.call_args_list[0].args[1])
        # The lookup that failed at creation is the same one that gates the patch,
        # so a series that only just became resolvable is a series whose recording
        # was never switched on. Resolving it is the first chance to fix that, and
        # it has to be taken here -- otherwise granting the access policy repairs
        # the tracking and leaves every meeting still recording nothing.
        self.assertEqual(
            graph_request.call_args_list[1].args[:2],
            ('PATCH', 'users/tutor%40example.com/onlineMeetings/meeting-recovered'),
        )
        reapplied = graph_request.call_args_list[1].kwargs['payload']
        self.assertTrue(reapplied['allowRecording'])
        self.assertTrue(reapplied['recordAutomatically'])
        self.assertTrue(reapplied['allowTranscription'])
        self.assertEqual(
            graph_request.call_args_list[2].args[:2],
            ('GET', meeting_path),
        )
        self.assertEqual(
            graph_request.call_args_list[3].args[1],
            'users/tutor%40example.com/onlineMeetings/meeting-recovered/attendanceReports',
        )

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_requested_organizer_outranks_the_configured_default(
        self, _settings, _credentials, graph_request,
    ):
        # Deployments may grant the application access policy to multiple users.
        # The environment mailbox pre-fills the form but must not override the
        # organizer explicitly chosen for this new meeting.
        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(event_id='event-pinned', meeting_id='meeting-pinned', join_suffix='pinned')
        verification_boundaries = []
        verify_calendar = views.verify_teams_calendar_with_standalones

        def verified_calendar(*args, **kwargs):
            event = verify_calendar(*args, **kwargs)
            verification_boundaries.append(graph_request.call_count)
            return event

        verifier = patch.object(views, 'verify_teams_calendar_with_standalones', side_effect=verified_calendar)
        verifier.start()
        self.addCleanup(verifier.stop)
        with patch.dict(os.environ, {'MICROSOFT_TEAMS_ORGANIZER_EMAIL': 'organizer@example.com'}):
            response = self.client.post(
                '/curriculum_api/curriculum/teams-meetings/',
                data=json.dumps({
                    'title': 'Risk workshop',
                    'organizerEmail': 'tutor@example.com',
                    'attendees': ['student1@example.com'],
                    'presenters': ['tutor@example.com'],
                    'localStartDateTime': '2026-07-30T15:30',
                    'startDateTimeUtc': '2026-07-30T12:30:00.000Z',
                    'durationMinutes': 60,
                    'repeat': 'none',
                    'recording': 'record-transcribe',
                }),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['meeting']['organizerEmail'], 'tutor@example.com')
        self.assertEqual(
            graph_request.call_args_list[0].args[:2],
            ('POST', 'users/tutor%40example.com/events'),
        )
        calls = graph_request.call_args_list
        self.assertEqual(calls[0].kwargs['payload']['attendees'], [])
        self.assertEqual(len(verification_boundaries), 2)  # Before and after publishing.
        publications = [
            (index, call) for index, call in enumerate(calls)
            if call.args[0] in {'POST', 'PATCH'} and call.kwargs.get('payload', {}).get('attendees')
        ]
        self.assertEqual(len(publications), 1)
        publish_index, publish_call = publications[0]
        self.assertEqual(publish_call.args[:2], ('PATCH', 'users/tutor%40example.com/events/event-pinned'))
        self.assertGreaterEqual(publish_index, verification_boundaries[0])
        self.assertGreater(verification_boundaries[1], publish_index)
        self.assertCountEqual(
            [entry['emailAddress']['address'] for entry in publish_call.kwargs['payload']['attendees']],
            ['student1@example.com', 'tutor@example.com'],
        )
        # No other invitation route can precede successful calendar verification.
        self.assertFalse(any(call.args[1].endswith('/forward') for call in calls[:verification_boundaries[0]]))
        self.assertTrue(all(call.args[1].startswith('users/tutor%40example.com/') for call in calls))
        meeting_patch = next(
            call for call in calls
            if call.args[:2] == ('PATCH', 'users/tutor%40example.com/onlineMeetings/meeting-pinned')
        ).kwargs['payload']
        self.assertEqual(meeting_patch['allowedPresenters'], 'roleIsPresenter')
        self.assertEqual(
            {item['upn']: item['role'] for item in meeting_patch['participants']['attendees']},
            {'student1@example.com': 'attendee', 'tutor@example.com': 'presenter'},
        )

    def test_configured_owner_id_does_not_override_another_organizer(self):
        configured_id = '11111111-1111-1111-1111-111111111111'
        selected_id = '22222222-2222-2222-2222-222222222222'
        selected_join_url = (
            'https://teams.microsoft.com/l/meetup-join/test/0?'
            'context=%7B%22Oid%22%3A%22'
            f'{selected_id}%22%7D'
        )
        with patch.dict(os.environ, {
            'MICROSOFT_TEAMS_ORGANIZER_EMAIL': 'default@example.com',
            'MICROSOFT_TEAMS_ORGANIZER_ID': configured_id,
        }):
            self.assertEqual(
                views.teams_online_meeting_owner_id('default@example.com'),
                configured_id,
            )
            self.assertEqual(
                views.teams_online_meeting_owner_id('selected@example.com', selected_join_url),
                selected_id,
            )

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_record_and_transcribe_switches_on_all_three_graph_flags(
        self, _settings, _credentials, graph_request,
    ):
        # `allowRecording` alone only permits a human to press Record.
        # `recordAutomatically` is what starts it without anyone in the room
        # remembering to, and `allowTranscription` is what produces the transcript
        # the module's evidence relies on. All three, or the session is lost.
        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(event_id='event-rec', meeting_id='meeting-rec', join_suffix='rec')
        response = self.client.post(
            '/curriculum_api/curriculum/teams-meetings/',
            data=json.dumps({
                'title': 'Recorded session',
                'organizerEmail': 'tutor@example.com',
                'attendees': ['student1@example.com'],
                'localStartDateTime': '2026-07-30T15:30',
                'startDateTimeUtc': '2026-07-30T12:30:00.000Z',
                'durationMinutes': 60,
                'repeat': 'none',
                'recording': 'record-transcribe',
                'spokenLanguage': 'en-GB',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.json()['meeting']['settingsApplied'])
        patch_payload = next(
            call.kwargs['payload']
            for call in graph_request.call_args_list
            if call.args[:2] == ('PATCH', 'users/tutor%40example.com/onlineMeetings/meeting-rec')
        )
        self.assertTrue(patch_payload['allowRecording'])
        self.assertTrue(patch_payload['recordAutomatically'])
        self.assertTrue(patch_payload['allowTranscription'])
        self.assertEqual(patch_payload['meetingSpokenLanguageTag'], 'en-GB')

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_schedule_update_keeps_the_organizer_the_series_was_created_on(
        self, _settings, _credentials, graph_request,
    ):
        # Pinning an organizer must not repoint a series that already exists: this
        # event lives on one mailbox, and asking Graph for it on another is a 404
        # that would strand a live series mid-programme.
        live_session_id = 'LIVE-STORED-ORGANIZER'
        views.ensure_live_session_tracking_tables()
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'organizer_email': 'previous.tutor@example.com',
            'graph_event_id': 'event-existing',
            'online_meeting_id': 'meeting-existing',
            'module_title': 'Risk module',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 60,
            'repeat_pattern': 'none',
            'repeat_occurrences': 1,
            'join_url': 'https://teams.microsoft.com/l/meetup-join/existing',
            'recording': 'record-transcribe',
            'status': 'active',
        })
        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(
            event_id='event-existing', meeting_id='meeting-existing', join_suffix='existing',
            owner='previous.tutor@example.com', starts=['2026-09-02T08:30:00Z'],
        )

        with patch.dict(os.environ, {'MICROSOFT_TEAMS_ORGANIZER_EMAIL': 'organizer@example.com'}):
            response = self.client.patch(
                f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/schedule/',
                data=json.dumps({
                    'title': 'Risk module',
                    'organizerEmail': 'someone.else@example.com',
                    'localStartDateTime': '2026-09-09T09:30',
                    'startDateTimeUtc': '2026-09-09T08:30:00.000Z',
                    'durationMinutes': 60,
                    'repeat': 'none',
                }),
                content_type='application/json',
            )

        self.assertEqual(response.status_code, 200, response.content)
        paths = [call.args[1] for call in graph_request.call_args_list]
        self.assertTrue(all('previous.tutor%40example.com' in path for path in paths), paths)
        self.assertFalse(any('organizer%40example.com' in path for path in paths), paths)
        self.assertFalse(any('someone.else' in path for path in paths), paths)

    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_requires_organizer_when_no_default_is_configured(self, _settings, _credentials):
        with patch.dict('os.environ', {'MICROSOFT_TEAMS_ORGANIZER_EMAIL': ''}):
            response = self.client.post(
                '/curriculum_api/curriculum/teams-meetings/',
                data=json.dumps({'title': 'Live session'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Organizer email is required', response.json()['error'])

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_schedule_update_can_correct_who_presents(self, _settings, _credentials, graph_request):
        # Presenters are the one meeting option that used to be answerable only
        # at creation, which left a mis-invited series with no way back short of
        # recreating it. The dates here are unchanged on purpose.
        live_session_id = 'LIVE-PRESENTERS'
        views.ensure_live_session_tracking_tables()
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-1',
            'online_meeting_id': 'meeting-1',
            'module_title': 'Risk module',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 2,
            'join_url': 'https://teams.microsoft.com/l/meetup-join/example',
            'attendees': views.json_db_value(['learner@example.com']),
            'presenters': views.json_db_value([]),
            'status': 'active',
        })

        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(
            event_id='event-1', meeting_id='meeting-1', join_suffix='example', duration=120,
            starts=['2026-09-02T08:30:00Z', '2026-09-09T08:30:00Z'], attendees=['learner@example.com'],
        )

        response = self.client.patch(
            f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/schedule/',
            data=json.dumps({
                'title': 'Risk module',
                'organizerEmail': 'tutor@example.com',
                'eventId': 'event-1',
                'localStartDateTime': '2026-09-02T09:30',
                'startDateTimeUtc': '2026-09-02T08:30:00Z',
                'durationMinutes': 120,
                'repeat': 'weekly',
                'repeatOccurrences': 2,
                'attendees': ['learner@example.com'],
                'presenters': ['tutor@example.com'],
                'scheduledOccurrences': [
                    {'sessionNumber': 1, 'startDateTimeUtc': '2026-09-02T08:30:00Z', 'durationMinutes': 120},
                    {'sessionNumber': 2, 'startDateTimeUtc': '2026-09-09T08:30:00Z', 'durationMinutes': 120},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result['meeting']['presenters'], ['tutor@example.com'])
        self.assertEqual(result['warnings'], [])
        series = views.authoring_fetch_all(views.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])[0]
        self.assertEqual(views.as_json_value(series['presenters'], []), ['tutor@example.com'])

        # The invitation went out on the calendar event, and the presenter role
        # was applied to the onlineMeeting behind it.
        event_patch = next(
            call.kwargs.get('payload') or call.args[2]
            for call in graph_request.call_args_list
            if call.args[:2] == ('PATCH', 'users/tutor%40example.com/events/event-1')
        )
        self.assertEqual(
            [attendee['emailAddress']['address'] for attendee in event_patch['attendees']],
            ['tutor@example.com', 'learner@example.com'],
        )
        meeting_patch = next(
            call.kwargs.get('payload') or call.args[2]
            for call in graph_request.call_args_list
            if call.args[0] == 'PATCH' and 'onlineMeetings' in call.args[1]
        )
        self.assertEqual(meeting_patch['allowedPresenters'], 'roleIsPresenter')
        self.assertEqual(
            {item['upn']: item['role'] for item in meeting_patch['participants']['attendees']},
            {'tutor@example.com': 'presenter', 'learner@example.com': 'attendee'},
        )

    @patch('coach_api.views.microsoft_graph_request')
    @patch('coach_api.views.has_graph_credentials', return_value=True)
    @patch('coach_api.views.get_graph_settings', return_value={
        'tenant_id': 'tenant',
        'client_id': 'client',
        'client_secret': 'secret',
        'scope': 'https://graph.microsoft.com/.default',
        'base_url': 'https://graph.microsoft.com/v1.0',
        'timezone': 'GMT Standard Time',
    })
    def test_schedule_update_keeps_stored_people_when_they_are_not_sent(self, _settings, _credentials, graph_request):
        live_session_id = 'LIVE-DATES-ONLY'
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-2',
            'module_title': 'Risk module',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 1,
            'join_url': 'https://teams.microsoft.com/l/meetup-join/example',
            'attendees': views.json_db_value(['learner@example.com']),
            'presenters': views.json_db_value(['tutor@example.com']),
            'lobby_bypass': 'invited',
            'recording': 'record-transcribe',
            'spoken_language': 'en-GB',
            'status': 'active',
        })

        from coach_api.tests_support.graph_calendar import GraphCalendar
        graph_request.side_effect = GraphCalendar(
            event_id='event-2', meeting_id='meeting-2', join_suffix='example', duration=120,
            starts=['2026-09-02T08:30:00Z'], attendees=['tutor@example.com', 'learner@example.com'],
        )

        response = self.client.patch(
            f'/curriculum_api/curriculum/teams-meetings/{live_session_id}/schedule/',
            data=json.dumps({
                'title': 'Risk module',
                'organizerEmail': 'tutor@example.com',
                'eventId': 'event-2',
                'localStartDateTime': '2026-09-16T09:30',
                'startDateTimeUtc': '2026-09-16T08:30:00Z',
                'durationMinutes': 120,
                'repeat': 'none',
                'repeatOccurrences': 1,
                'scheduledOccurrences': [
                    {'sessionNumber': 1, 'startDateTimeUtc': '2026-09-16T08:30:00Z', 'durationMinutes': 120},
                ],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200, response.content)
        series = views.authoring_fetch_all(views.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])[0]
        self.assertEqual(views.as_json_value(series['presenters'], []), ['tutor@example.com'])
        self.assertEqual(views.as_json_value(series['attendees'], []), ['learner@example.com'])
        # No invitation list was sent, so the event patch must not rewrite one.
        event_patch = next(
            call.kwargs.get('payload') or call.args[2]
            for call in graph_request.call_args_list
            if call.args[:2] == ('PATCH', 'users/tutor%40example.com/events/event-2')
        )
        self.assertNotIn('attendees', event_patch)

        # Every save re-applies the stored meeting options, not only the saves that
        # change who is invited. Graph hands out a new online meeting at the tenant
        # defaults whenever the event gets one, so a series configured once at
        # creation quietly loses its automatic recording and transcript later.
        meeting_patch = next(
            call.kwargs.get('payload') or call.args[2]
            for call in graph_request.call_args_list
            if call.args[0] == 'PATCH' and 'onlineMeetings' in call.args[1]
        )
        self.assertTrue(meeting_patch['allowRecording'])
        self.assertTrue(meeting_patch['recordAutomatically'])
        self.assertTrue(meeting_patch['allowTranscription'])
        self.assertEqual(meeting_patch['meetingSpokenLanguageTag'], 'en-GB')
        # The stored invitation list still names the roles, so the meeting knows
        # these people belong to it rather than being guests who found the link.
        self.assertEqual(
            {item['upn']: item['role'] for item in meeting_patch['participants']['attendees']},
            {'tutor@example.com': 'presenter', 'learner@example.com': 'attendee'},
        )
        # The meeting ID the lookup resolved is kept, so the next sync does not
        # have to resolve it again.
        self.assertEqual(
            views.authoring_fetch_all(views.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])[0]['online_meeting_id'],
            'meeting-2',
        )

    def test_restore_gives_each_live_session_component_its_own_session(self):
        # A learner opening session two has to land in session two, on session
        # two's date. The series' values are right for the sessions that share
        # its meeting; a session Graph would only take as an event of its own
        # carries its own link, and every session carries its own date.
        views.ensure_live_session_tracking_tables()
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': 'MOD-RESTORE',
            'programme_id': 'PROG-RESTORE',
            'programme_name': 'Programme',
            'title': 'Risk module',
        })
        views.authoring_upsert(views.AUTHORING_WEEKS_TABLE, ['id'], {
            'id': 'WEEK-RESTORE',
            'module_catalogue_id': 'MOD-RESTORE',
            'week_number': 1,
            'title': 'Week 1',
        })
        for order, component_id in enumerate(['COMP-LIVE-1', 'COMP-LIVE-2'], start=1):
            views.authoring_upsert(views.AUTHORING_COMPONENTS_TABLE, ['id'], {
                'id': component_id,
                'week_id': 'WEEK-RESTORE',
                'module_catalogue_id': 'MOD-RESTORE',
                'type': 'live_session',
                'title': f'Session {order}',
                'display_order': order,
            })
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-RESTORE',
            'module_catalogue_id': 'MOD-RESTORE',
            'module_title': 'Risk module',
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-series',
            'online_meeting_id': 'meeting-series',
            'join_url': 'https://teams.microsoft.com/l/meetup-join/series',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 2,
            'status': 'active',
        })
        occurrences = [
            (1, '2026-09-02T08:30:00Z', '2026-09-02T10:30:00Z', '', '', ''),
            (2, '2026-09-16T08:30:00Z', '2026-09-16T10:00:00Z',
             'https://teams.microsoft.com/l/meetup-join/moved', 'event-moved', 'meeting-moved'),
        ]
        for number, start, end, join_url, event_id, meeting_id in occurrences:
            views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
                'id': f'OCC-RESTORE-{number}',
                'live_session_id': 'LIVE-RESTORE',
                'session_number': number,
                'scheduled_start': start,
                'scheduled_end': end,
                'join_url': join_url,
                'graph_event_id': event_id,
                'online_meeting_id': meeting_id,
                'status': 'scheduled',
            })

        response = self.client.post('/curriculum_api/curriculum/modules/MOD-RESTORE/teams-meetings/restore/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['updatedComponents'], 2)

        rows = {
            row['id']: row
            for row in views.authoring_fetch_all(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s', ['MOD-RESTORE'])
        }
        first = views.parse_json_value(rows['COMP-LIVE-1'].get('settings_json'), {})
        second = views.parse_json_value(rows['COMP-LIVE-2'].get('settings_json'), {})

        # Session one shares the series' meeting, so it keeps the series' link
        # and gets its own date and length.
        self.assertEqual(first['liveSessionUrl'], 'https://teams.microsoft.com/l/meetup-join/series')
        self.assertEqual(first['teamsOnlineMeetingId'], 'meeting-series')
        self.assertEqual(first['sessionDateTimeUtc'][:10], '2026-09-02')
        self.assertEqual(first['durationMinutes'], 120)
        self.assertEqual(first['teamsSessionNumber'], 1)

        # Session two was recreated on its own event, so everything that names a
        # meeting is its own -- and its shorter slot comes with it.
        self.assertEqual(second['liveSessionUrl'], 'https://teams.microsoft.com/l/meetup-join/moved')
        self.assertEqual(rows['COMP-LIVE-2']['live_sessions_link'], 'https://teams.microsoft.com/l/meetup-join/moved')
        self.assertEqual(second['teamsOnlineMeetingId'], 'meeting-moved')
        self.assertEqual(second['teamsEventId'], 'event-moved')
        self.assertEqual(second['sessionDateTimeUtc'][:10], '2026-09-16')
        self.assertEqual(second['durationMinutes'], 90)
        self.assertEqual(second['teamsSessionNumber'], 2)

    def _module_with_six_saturday_weeks(self):
        """A module that runs weekly on Saturdays across a closed Christmas.

        Two of its delivery days are closed, so sessions 2-6 run later than the
        plain weekly pattern and the module spills out of December into January.
        That spill is the point: it is what the week list has to be able to say.
        """
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': 'COHORT-XMAS',
            'cohort_name': 'C1',
            'programme_id': 'PROG-RESTORE',
            'programme_name': 'Programme',
            'start_date': '2026-08-01',
            'end_date': '2027-08-31',
            # The ticked ids are the selection. `selected_holidays` is only a
            # cache of what they pointed at when the cohort was last written,
            # and this is what a stale one looks like in production: eight
            # holidays ticked, one left in the cache, and Christmas missing from
            # it. Reading the cache first ran the module's sessions straight
            # through a closed fortnight.
            'holiday_ids': views.json_db_value(['1080', '1082']),
            'selected_holidays': views.json_db_value([
                {'id': 1083, 'label': 'June 27', 'startDate': '2027-05-30', 'endDate': '2027-06-05'},
            ]),
        })
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': 'MOD-WEEKS',
            'programme_id': 'PROG-RESTORE',
            'programme_name': 'Programme',
            'cohort_id': 'COHORT-XMAS',
            'title': 'Fouda-ss',
            'sessions_number': 6,
            # A Tuesday: the first session is the Saturday that follows it.
            'start_date': '2026-12-08',
            'session_week_day': 'Saturday',
            'session_start_time': '09:00',
            'session_end_time': '11:00',
        })
        for number in range(1, 7):
            views.authoring_upsert(views.AUTHORING_WEEKS_TABLE, ['id'], {
                'id': f'WEEK-{number}',
                'module_catalogue_id': 'MOD-WEEKS',
                'week_number': number,
                'display_order': number,
                'title': f'Week {number}',
            })
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-WEEKS',
            'module_catalogue_id': 'MOD-WEEKS',
            'module_title': 'Fouda-ss',
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-series',
            'online_meeting_id': 'meeting-series',
            'join_url': 'https://teams.microsoft.com/l/meetup-join/series',
            'start_datetime': '2026-12-12T09:00:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 6,
            'status': 'active',
        })
        for index, day in enumerate(['2026-12-12', '2026-12-19', '2026-12-26',
                                     '2027-01-02', '2027-01-09', '2027-01-16'], 1):
            views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['id'], {
                'id': f'OCC-WEEKS-{index}', 'live_session_id': 'LIVE-WEEKS', 'session_number': index,
                'scheduled_start': day + 'T09:00:00Z', 'scheduled_end': day + 'T11:00:00Z',
                'graph_event_id': 'event-series', 'join_url': 'https://teams.microsoft.com/l/meetup-join/series',
                'status': 'scheduled',
            })

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_structure_payload_dates_every_week_from_the_module_plan(self, _holidays):
        # The weeks are a timetable, not just a running count: each one carries
        # the day it actually runs.
        #
        # A closure does not move the week. Christmas shuts the rooms on the
        # Saturdays of 19 and 26 December, so the live sessions those weeks hold
        # run in January -- but the reading, the assignment and everything else
        # in them is not held in a room and stays on the week it was authored
        # into. The weeks therefore run straight through Christmas, six Saturdays
        # from the twelfth, and it is the SESSIONS that spill into the new year.
        self._module_with_six_saturday_weeks()

        payload = views.get_authoring_structure_payload('MOD-WEEKS')

        self.assertEqual(
            [week['sessionDate'] for week in payload['weekStructure']],
            ['2026-12-12', '2026-12-19', '2026-12-26', '2027-01-02', '2027-01-09', '2027-01-16'],
        )
        self.assertEqual(payload['weekStructure'][0]['sessionDay'], 'Saturday')
        self.assertEqual(payload['weekStructure'][0]['sessionStartTime'], '09:00')
        self.assertEqual(payload['weekStructure'][0]['sessionDurationMinutes'], 120)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_restore_can_give_every_week_its_own_live_session(self, _holidays):
        # Re-attaching is the one action that finishes the job: a created series
        # is only in front of the learner once every week has a session to open,
        # on that week's own date.
        self._module_with_six_saturday_weeks()
        views.authoring_upsert(views.AUTHORING_COMPONENTS_TABLE, ['id'], {
            'id': 'COMP-EXISTING',
            'week_id': 'WEEK-1',
            'module_catalogue_id': 'MOD-WEEKS',
            'type': 'live_session',
            'title': 'Kick-off',
            'display_order': 1,
        })

        response = self.client.post(
            '/curriculum_api/curriculum/modules/MOD-WEEKS/teams-meetings/restore/',
            data=json.dumps({'createMissingComponents': True}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        # Week 1 already had one; the other five were created rather than left
        # without a session.
        self.assertEqual(result['createdComponents'], 5)
        self.assertEqual(result['updatedComponents'], 1)

        weeks = result['module']['weekStructure']
        live_sessions = [
            [component for component in week['components'] if component['type'] == 'live-session']
            for week in weeks
        ]
        self.assertEqual([len(items) for items in live_sessions], [1, 1, 1, 1, 1, 1])
        # A ticked holiday no longer displaces the session -- every week keeps
        # its own Saturday, closed or not.
        self.assertEqual(
            [items[0]['settings']['sessionDate'] for items in live_sessions],
            ['2026-12-12', '2026-12-19', '2026-12-26', '2027-01-02', '2027-01-09', '2027-01-16'],
        )
        # Every one of them can be joined, and the created ones say when they
        # start and how long they run.
        self.assertTrue(all(
            items[0]['settings']['liveSessionUrl'] == 'https://teams.microsoft.com/l/meetup-join/series'
            for items in live_sessions
        ))
        created = live_sessions[1][0]
        self.assertEqual(created['settings']['sessionTime'], '09:00')
        self.assertEqual(created['settings']['durationMinutes'], 120)
        self.assertEqual(created['expectedOtjh'], 2.0)
        # Titled after the session, never after the week: a live session named
        # "Week 2" is read as a generated placeholder and hidden by the authoring
        # model.
        self.assertNotIn(created['title'], {'Week 2', ''})

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_session_plan_dates_a_week_that_is_not_saved_yet(self, _holidays):
        # Module Builder adds a week before anything is written, and week N is
        # session N -- so a seventh week on a stored six-week module has to be
        # given the seventh planned date. Asking here is what keeps that date the
        # one the structure payload will serve after the save.
        self._module_with_six_saturday_weeks()
        stored_weeks = views.get_authoring_structure_payload('MOD-WEEKS')['weekStructure']
        stored_module = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-WEEKS'])[0]

        response = self.client.get(
            '/curriculum_api/curriculum/modules/MOD-WEEKS/session-plan/', {'weeks': 7},
        )

        self.assertEqual(response.status_code, 200, response.content)
        plan = response.json()
        expected_dates = ['2026-12-12', '2026-12-19', '2026-12-26',
                          '2027-01-02', '2027-01-09', '2027-01-16', '2027-01-23']
        self.assertEqual(plan['weeks'], 7)
        self.assertEqual(len(plan['sessions']), 7)
        self.assertEqual([session['sessionNumber'] for session in plan['sessions']], list(range(1, 8)))
        self.assertEqual([session['date'] for session in plan['sessions']], expected_dates)
        self.assertEqual([week['sessionDate'] for week in stored_weeks], expected_dates[:6])
        self.assertTrue(all(session['day'] == 'Saturday' for session in plan['sessions']))
        self.assertEqual(plan['finalEndDate'], expected_dates[-1])
        # A holiday annotates its authored date; it cannot manufacture a move.
        self.assertEqual(plan['sessions'][1]['skippedHolidays'], ['2026-12-19'])
        self.assertEqual(plan['sessions'][2]['skippedHolidays'], ['2026-12-26'])
        self.assertEqual(plan['sessions'][0]['skippedHolidays'], [])
        self.assertFalse(any(session.get('rescheduled') or session.get('originalDate') for session in plan['sessions']))
        self.assertEqual(views.get_authoring_structure_payload('MOD-WEEKS')['weekStructure'], stored_weeks)
        self.assertEqual(views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-WEEKS'])[0], stored_module)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_session_plan_keeps_an_added_week_on_its_own_saturday(self, _holidays):
        # An added week runs on its own delivery day, ticked holiday or not: the
        # fourteenth Saturday of this module is 13 March 2027, which is nowhere
        # near the Easter the cohort ticked -- Christmas is what touches this
        # run, on the second Saturday, which still delivers and simply names it.
        self._module_with_six_saturday_weeks()

        response = self.client.get(
            '/curriculum_api/curriculum/modules/MOD-WEEKS/session-plan/', {'weeks': 14},
        )

        self.assertEqual(response.status_code, 200, response.content)
        plan = response.json()
        self.assertEqual(len(plan['sessions']), 14)
        self.assertEqual(plan['sessions'][-1]['date'], '2027-03-13')
        self.assertEqual(plan['sessions'][-1]['skippedHolidays'], [])
        self.assertEqual(plan['sessions'][1]['date'], '2026-12-19')
        self.assertEqual(plan['sessions'][1]['skippedHolidays'], ['2026-12-19'])
        self.assertEqual(plan['finalEndDate'], '2027-03-13')

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_session_plan_falls_back_to_the_stored_session_count(self, _holidays):
        self._module_with_six_saturday_weeks()
        stored_weeks = views.get_authoring_structure_payload('MOD-WEEKS')['weekStructure']
        stored_module = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-WEEKS'])[0]

        response = self.client.get('/curriculum_api/curriculum/modules/MOD-WEEKS/session-plan/')

        self.assertEqual(response.status_code, 200, response.content)
        plan = response.json()
        expected_dates = ['2026-12-12', '2026-12-19', '2026-12-26',
                          '2027-01-02', '2027-01-09', '2027-01-16']
        self.assertEqual(plan['weeks'], 6)
        self.assertEqual(len(plan['sessions']), 6)
        self.assertEqual([session['sessionNumber'] for session in plan['sessions']], list(range(1, 7)))
        self.assertEqual([session['date'] for session in plan['sessions']], expected_dates)
        self.assertEqual([week['sessionDate'] for week in stored_weeks], expected_dates[:6])
        self.assertTrue(all(session['day'] == 'Saturday' for session in plan['sessions']))
        self.assertEqual(plan['finalEndDate'], expected_dates[-1])
        # A holiday annotates its authored date; it cannot manufacture a move.
        self.assertEqual(plan['sessions'][1]['skippedHolidays'], ['2026-12-19'])
        self.assertEqual(plan['sessions'][2]['skippedHolidays'], ['2026-12-26'])
        self.assertEqual(plan['sessions'][0]['skippedHolidays'], [])
        self.assertFalse(any(session.get('rescheduled') or session.get('originalDate') for session in plan['sessions']))
        self.assertEqual(views.get_authoring_structure_payload('MOD-WEEKS')['weekStructure'], stored_weeks)
        self.assertEqual(views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-WEEKS'])[0], stored_module)

    def test_session_plan_refuses_a_module_it_cannot_find(self):
        # An empty plan would read as "these weeks have no dates", which is a
        # different answer from "there is no such module".
        response = self.client.get('/curriculum_api/curriculum/modules/MOD-NOPE/session-plan/', {'weeks': 3})

        self.assertEqual(response.status_code, 404, response.content)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_restore_without_the_flag_authors_nothing(self, _holidays):
        # The Module Builder calls this endpoint silently when it opens a module
        # whose join link went missing. Opening a module must never write
        # components nobody asked for.
        self._module_with_six_saturday_weeks()

        response = self.client.post('/curriculum_api/curriculum/modules/MOD-WEEKS/teams-meetings/restore/')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['createdComponents'], 0)
        self.assertEqual(
            views.authoring_fetch_all(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s', ['MOD-WEEKS']),
            [],
        )

    def test_recording_events_credit_the_signed_in_account_not_the_body(self):
        # Who watched a session back is part of that session's record, so it is
        # read from the request's account. A viewer named in the body is only a
        # fallback for a request with no session -- and must never override it.
        views.ensure_live_session_tracking_tables()
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': 'MOD-WATCHED',
            'programme_id': 'PROG-WATCHED',
            'programme_name': 'Programme',
            'title': 'Risk module',
        })
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-WATCHED',
            'module_catalogue_id': 'MOD-WATCHED',
            'organizer_email': 'tutor@example.com',
            'status': 'active',
        })
        views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
            'id': 'OCC-WATCHED',
            'live_session_id': 'LIVE-WATCHED',
            'session_number': 1,
            'scheduled_start': '2026-09-02T08:30:00Z',
            'scheduled_end': '2026-09-02T10:30:00Z',
            'status': 'held',
        })
        views.authoring_upsert(views.LIVE_SESSION_ARTIFACTS_TABLE, ['id'], {
            'id': 'ART-WATCHED',
            'occurrence_id': 'OCC-WATCHED',
            'artifact_type': 'recording',
            'graph_artifact_id': 'graph-recording-1',
        })

        # The login middleware attaches the account; the view is called directly
        # with the same attribute so the test is about the view's own precedence.
        request = RequestFactory().post(
            '/curriculum_api/curriculum/teams-meetings/LIVE-WATCHED/artifacts/ART-WATCHED/recording-events/',
            data=json.dumps({
                'viewer': {'email': 'someone.else@example.com', 'name': 'Not me'},
                'events': [
                    {'type': 'play', 'videoTimeSeconds': 0},
                    {'type': 'seeked', 'videoTimeSeconds': 600, 'skipFromSeconds': 60, 'skipToSeconds': 600},
                ],
            }),
            content_type='application/json',
        )
        request.login_account = SimpleNamespace(
            id=77,
            email='reviewer@kentbusinesscollege.com',
            display_name='Session Reviewer',
            role='curriculum',
        )
        response = views.curriculum_teams_recording_events(request, 'LIVE-WATCHED', 'ART-WATCHED')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['saved'], 2)
        rows = views.authoring_fetch_all(
            views.LIVE_SESSION_RECORDING_EVENTS_TABLE,
            'artifact_id = %s',
            ['ART-WATCHED'],
            'event_type',
        )
        self.assertEqual([row['event_type'] for row in rows], ['play', 'seeked'])
        self.assertEqual({row['viewer_email'] for row in rows}, {'reviewer@kentbusinesscollege.com'})
        self.assertEqual({row['viewer_name'] for row in rows}, {'Session Reviewer'})
        skip = next(row for row in rows if row['event_type'] == 'seeked')
        self.assertTrue(skip['skipped'])
        self.assertEqual(skip['skip_delta_seconds'], 540)

    def test_meeting_summary_reports_the_dates_teams_holds_when_asked(self):
        class DateTimeType(type):
            def __instancecheck__(cls, value):
                return isinstance(value, datetime)
        class BeforeSeptember(datetime, metaclass=DateTimeType):
            @classmethod
            def utcnow(cls):
                return cls(2026, 9, 1)
        clock = patch.object(views, 'datetime', BeforeSeptember)
        clock.start()
        self.addCleanup(clock.stop)
        views.ensure_live_session_tracking_tables()
        views.authoring_delete(views.LIVE_SESSION_OCCURRENCES_TABLE)
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': 'MOD-SUMMARY',
            'programme_id': 'PROG-SUMMARY',
            'programme_name': 'Programme',
            'title': 'Risk module',
        })
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-SUMMARY',
            'module_catalogue_id': 'MOD-SUMMARY',
            'module_title': 'Risk module',
            'organizer_email': 'tutor@example.com',
            'attendees': views.json_db_value(['learner@example.com']),
            'presenters': views.json_db_value(['tutor@example.com']),
            'graph_event_id': 'event-1',
            'start_datetime': '2026-09-02T08:30:00Z',
            'duration_minutes': 120,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 2,
            'status': 'active',
        })
        for index, start in enumerate(['2026-09-02T08:30:00Z', '2026-09-16T08:30:00Z'], start=1):
            views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
                'id': f'OCC-SUMMARY-{index}',
                'live_session_id': 'LIVE-SUMMARY',
                'session_number': index,
                'scheduled_start': start,
                'scheduled_end': start,
                'status': 'scheduled',
            })

        # Without the flag the payload stays the small counts-only shape the
        # catalogue pages already read.
        plain = self.client.get('/curriculum_api/curriculum/teams-meetings/summary/').json()['results']
        plain_row = next(row for row in plain if row['moduleCatalogueId'] == 'MOD-SUMMARY')
        self.assertNotIn('occurrenceDates', plain_row)
        self.assertEqual(plain_row['presenters'], ['tutor@example.com'])
        self.assertEqual(plain_row['eventId'], 'event-1')

        detailed = self.client.get(
            '/curriculum_api/curriculum/teams-meetings/summary/?occurrence_dates=1'
        ).json()['results']
        detailed_row = next(row for row in detailed if row['moduleCatalogueId'] == 'MOD-SUMMARY')
        self.assertEqual(
            [value[:10] for value in detailed_row['occurrenceDates']],
            ['2026-09-02', '2026-09-16'],
        )
        # The occurrence columns are `timestamp without time zone` holding UTC, so
        # a plain isoformat() hands the browser a time with no offset -- which
        # JavaScript reads as the *reader's own* local time. Read that way the
        # same calendar is in sync in London and two hours out in Cairo, so every
        # instant leaving here names its offset.
        for value in detailed_row['occurrenceDates']:
            self.assertRegex(value, r'(Z|[+-]\d{2}:\d{2})$')
        self.assertEqual(
            [views.parse_graph_datetime(value).astimezone(timezone.utc).hour
             for value in detailed_row['occurrenceDates']],
            [8, 8],
        )
        self.assertRegex(detailed_row['nextOccurrence'], r'(Z|[+-]\d{2}:\d{2})$')


class TeamsCalendarSyncVerdictTests(CurriculumTeamsRequestTestCase):
    """The Teams Meetings badge's real verdict: the module's *current* plan,
    recomputed from the authoring rows, against what Teams actually holds.

    Module: Monday-only or Monday+Thursday, 09:00-10:00 London time (08:00 UTC
    in September, BST), starting Monday 2026-09-07. Session dates below are
    picked from that pattern: 07/14/21 Sept are the Mondays, 10/17/24 Sept the
    Thursdays.
    """

    def setUp(self):
        self.client = Client()
        views.reset_schema_ready_flags()
        views.ensure_module_authoring_tables()
        views.ensure_live_session_tracking_tables()
        self.prepare_teams_fixtures('PROG-SYNC')
        organizer_env = patch.dict(os.environ, {
            'MICROSOFT_TEAMS_ORGANIZER_EMAIL': '',
            'MICROSOFT_ORGANIZER_EMAIL': '',
            'MICROSOFT_GRAPH_TIMEZONE_IANA': '',
        })
        organizer_env.start()
        self.addCleanup(organizer_env.stop)

    def _module(self, module_id, cohort_id='', **overrides):
        row = {
            'module_catalogue_id': module_id,
            'programme_id': 'PROG-SYNC',
            'programme_name': 'Programme',
            'cohort_id': cohort_id,
            'title': 'Risk module',
            'sessions_number': 2,
            'start_date': '2026-09-07',
            'session_week_day': 'Monday',
            'session_start_time': '09:00',
            'session_end_time': '10:00',
        }
        row.update(overrides)
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], row)
        # A sessions_number alone is no longer an authored timetable. Persist
        # one live component per intended delivery slot, including both slots
        # within a Monday/Thursday week. The real resolver dates these rows.
        per_week = len(row['session_week_day'].split(','))
        for index in range(row['sessions_number']):
            week_number, offset = divmod(index, per_week)
            week_id = f'{module_id}-W{week_number + 1}'
            views.authoring_upsert(views.AUTHORING_WEEKS_TABLE, ['id'], {
                'id': week_id, 'module_catalogue_id': module_id,
                'week_number': week_number + 1, 'display_order': week_number + 1,
                'title': 'Synthetic week',
            })
            views.authoring_upsert(views.AUTHORING_COMPONENTS_TABLE, ['id'], {
                'id': f'{module_id}-LIVE{index + 1}', 'week_id': week_id,
                'module_catalogue_id': module_id, 'type': 'live_session',
                'title': 'Synthetic session', 'display_order': offset + 1,
            })
        return views.authoring_fetch_all(
            views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', [module_id],
        )[0]

    def _cohort(self, cohort_id, holiday_ids=None):
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': cohort_id,
            'cohort_name': 'C1',
            'programme_id': 'PROG-SYNC',
            'programme_name': 'Programme',
            'start_date': '2026-08-01',
            'end_date': '2027-08-31',
            'holiday_ids': views.json_db_value(holiday_ids or []),
            'selected_holidays': views.json_db_value([]),
        })

    @staticmethod
    def _occurrence_rows(*starts, status='scheduled'):
        return [
            {'scheduled_start': start, 'status': status}
            for start in starts
        ]

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_exact_match_is_in_sync(self, _holidays):
        module = self._module('MOD-SYNC-1', sessions_number=2)  # Mondays 07, 14 Sept
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-09-07T08:00:00Z', '2026-09-14T08:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'in-sync')
        self.assertEqual(verdict['differingOccurrences'], 0)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_extra_teams_weekday_is_out_of_sync(self, _holidays):
        # Plan is Monday only; Teams also holds a Thursday nobody authored.
        module = self._module('MOD-SYNC-2', sessions_number=1, session_week_day='Monday')
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-09-07T08:00:00Z', '2026-09-10T08:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'out-of-sync')
        self.assertIn('extra_in_teams', verdict['syncReasons'])
        self.assertEqual(verdict['differingOccurrences'], 1)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_missing_teams_weekday_is_out_of_sync(self, _holidays):
        # Plan is Monday + Thursday; Teams only ever got the Monday.
        module = self._module('MOD-SYNC-3', sessions_number=2, session_week_day='Monday,Thursday')
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-09-07T08:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'out-of-sync')
        self.assertIn('missing_from_teams', verdict['syncReasons'])
        self.assertEqual(verdict['differingOccurrences'], 1)

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_reordered_dates_are_still_in_sync(self, _holidays):
        module = self._module('MOD-SYNC-4', sessions_number=2)  # Mondays 07, 14 Sept
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-09-14T08:00:00Z', '2026-09-07T08:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'in-sync')

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_holiday_keeps_authored_date_and_matching_teams_is_in_sync(self, _holidays):
        # Case A: a holiday annotates the authored date without moving it.
        self._cohort('COHORT-SYNC-5', holiday_ids=['1080'])
        module = self._module(
            'MOD-SYNC-5', cohort_id='COHORT-SYNC-5', sessions_number=2,
            start_date='2026-12-14', session_week_day='Monday',
        )
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-12-14T09:00:00Z', '2026-12-21T09:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'in-sync')

        self.assertEqual(verdict['expectedOccurrenceCount'], 2)
        self.assertEqual(verdict['trackedOccurrenceCount'], 2)
        self.assertEqual(verdict['differingOccurrences'], 0)
        self.assertEqual(verdict['syncReasons'], [])
        plan = views.module_session_plan_for_count(module, 2)
        self.assertEqual([session['date'] for session in plan['sessions']], ['2026-12-14', '2026-12-21'])
        self.assertEqual(plan['sessions'][1]['skippedHolidays'], ['2026-12-21'])
        self.assertFalse(any(session.get('rescheduled') for session in plan['sessions']))
        self.assertFalse(views.session_overrides(module))

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_explicit_session_move_requires_teams_to_match_effective_date(self, _holidays):
        self._cohort('COHORT-SYNC-6', holiday_ids=['1080'])
        module = self._module(
            'MOD-SYNC-6', cohort_id='COHORT-SYNC-6', sessions_number=2,
            start_date='2026-12-14', session_week_day='Monday',
        )
        before = views.module_session_plan_for_count(module, 2)
        self.assertEqual([session['date'] for session in before['sessions']], ['2026-12-14', '2026-12-21'])
        self.assertEqual(before['sessions'][1]['skippedHolidays'], ['2026-12-21'])
        # An actual persisted exception, rather than holiday selection, moves session 2.
        views.update_authoring_rows(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-SYNC-6'], {
            'session_overrides': views.json_db_value({'2': {
                'date': '2027-01-04', 'startTime': '09:00', 'endTime': '10:00', 'durationMinutes': 60,
            }}),
        })
        module = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-SYNC-6'])[0]
        plan = views.module_session_plan_for_count(module, 2)
        self.assertEqual([session['date'] for session in plan['sessions']], ['2026-12-14', '2027-01-04'])
        self.assertTrue(plan['sessions'][1]['rescheduled'])
        self.assertEqual(plan['sessions'][1]['originalDate'], '2026-12-21')
        self.assertEqual(plan['sessions'][0], before['sessions'][0])
        # Case B: Teams agrees with the explicit effective schedule.
        matched = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-12-14T09:00:00Z', '2027-01-04T09:00:00Z'),
        )
        self.assertEqual(matched['syncState'], 'in-sync')
        self.assertEqual(matched['expectedOccurrenceCount'], 2)
        self.assertEqual(matched['trackedOccurrenceCount'], 2)
        self.assertEqual(matched['differingOccurrences'], 0)
        self.assertEqual(matched['syncReasons'], [])
        # Case C: retaining the old date after that move is a real disagreement.
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-12-14T09:00:00Z', '2026-12-21T09:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'out-of-sync')
        self.assertCountEqual(verdict['syncReasons'], ['extra_in_teams', 'missing_from_teams'])
        self.assertEqual(verdict['differingOccurrences'], 1)
        self.assertEqual(verdict['missingFromTeams'], ['2027-01-04T09:00:00Z'])
        self.assertEqual(verdict['extraInTeams'], ['2026-12-21T09:00:00Z'])

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_changed_start_date_is_out_of_sync(self, _holidays):
        # Teams holds the dates from the module's old start date; the module
        # was since moved a week later.
        module = self._module('MOD-SYNC-7', sessions_number=2, start_date='2026-09-14')
        verdict = views.teams_calendar_sync_verdict(
            module, self._occurrence_rows('2026-09-07T08:00:00Z', '2026-09-14T08:00:00Z'),
        )
        self.assertEqual(verdict['syncState'], 'out-of-sync')
        self.assertIn('extra_in_teams', verdict['syncReasons'])
        self.assertIn('missing_from_teams', verdict['syncReasons'])

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_cohorts_are_read_once_for_every_module_that_shares_one(self, _holidays):
        # Three modules, one cohort: the holiday selection is the same for all
        # three, so the grouped comparison must read the cohort table once, not
        # once per module.
        self._cohort('COHORT-SYNC-8', holiday_ids=['1080'])
        for suffix in ('A', 'B', 'C'):
            self._module(
                f'MOD-SYNC-8{suffix}', cohort_id='COHORT-SYNC-8', sessions_number=2,
                start_date='2026-12-14', session_week_day='Monday',
            )
        occurrences_by_module = {
            f'MOD-SYNC-8{suffix}': self._occurrence_rows('2026-12-14T09:00:00Z', '2026-12-21T09:00:00Z')
            for suffix in ('A', 'B', 'C')
        }

        cohort_reads = []
        original_fetch_all = views.authoring_fetch_all

        def counting_fetch_all(table, *args, **kwargs):
            if table == views.COHORT_AUTHORING_DETAILS_TABLE:
                cohort_reads.append((args, kwargs))
            return original_fetch_all(table, *args, **kwargs)

        with patch('curriculum_api.views.authoring_fetch_all', side_effect=counting_fetch_all), \
             patch.object(views, 'teams_calendar_sync_verdict', wraps=views.teams_calendar_sync_verdict) as compare:
            verdicts = views.teams_calendar_sync_verdicts(
                ['MOD-SYNC-8A', 'MOD-SYNC-8B', 'MOD-SYNC-8C'], occurrences_by_module,
            )

        self.assertEqual(len(cohort_reads), 1)
        self.assertEqual(cohort_reads[0][0][1], ['COHORT-SYNC-8'])
        _holidays.assert_called_once()
        self.assertEqual(compare.call_count, 3)
        shared_holidays = compare.call_args_list[0].kwargs['holidays']
        self.assertTrue(shared_holidays)
        self.assertTrue(all(call.kwargs['holidays'] is shared_holidays for call in compare.call_args_list))
        self.assertEqual(set(verdicts), {'MOD-SYNC-8A', 'MOD-SYNC-8B', 'MOD-SYNC-8C'})
        for verdict in verdicts.values():
            self.assertEqual(verdict['syncState'], 'in-sync')
            self.assertEqual(verdict['expectedOccurrenceCount'], 2)
            self.assertEqual(verdict['trackedOccurrenceCount'], 2)
            self.assertEqual(verdict['differingOccurrences'], 0)
        for call in compare.call_args_list:
            plan = views.module_session_plan_for_count(call.args[0], 2, holidays=shared_holidays)
            self.assertEqual([session['date'] for session in plan['sessions']], ['2026-12-14', '2026-12-21'])
            self.assertEqual(plan['sessions'][1]['skippedHolidays'], ['2026-12-21'])
            self.assertFalse(any(session.get('rescheduled') for session in plan['sessions']))

    def test_missing_verification_data_is_not_reported_as_in_sync(self):
        # A read that failed must never read as agreement.
        module = self._module('MOD-SYNC-9', sessions_number=2)
        unreadable = views.teams_calendar_sync_verdict(
            module, [], occurrences_readable=False,
        )
        self.assertEqual(unreadable['syncState'], 'unverified')

        # A module row that has gone (an orphaned live session) is the same story.
        orphaned = views.teams_calendar_sync_verdict(
            None, self._occurrence_rows('2026-09-07T08:00:00Z'),
        )
        self.assertEqual(orphaned['syncState'], 'unverified')

        # And a series with nothing tracked against it yet has nothing to agree
        # with the plan -- it is not silently "in sync" by default.
        nothing_tracked = views.teams_calendar_sync_verdict(module, [])
        self.assertEqual(nothing_tracked['syncState'], 'unverified')

    @patch('curriculum_api.views.get_holiday_rows', return_value=CURRICULUM_HOLIDAY_ROWS)
    def test_summary_endpoint_reports_a_correctly_synced_module_as_in_sync(self, _holidays):
        module = self._module('MOD-SYNC-10', sessions_number=2)
        views.authoring_upsert(views.LIVE_SESSIONS_TABLE, ['id'], {
            'id': 'LIVE-SYNC-10',
            'module_catalogue_id': 'MOD-SYNC-10',
            'module_title': module['title'],
            'organizer_email': 'tutor@example.com',
            'graph_event_id': 'event-10',
            'start_datetime': '2026-09-07T08:00:00Z',
            'duration_minutes': 60,
            'repeat_pattern': 'weekly',
            'repeat_occurrences': 2,
            'status': 'active',
        })
        for index, start in enumerate(['2026-09-07T08:00:00Z', '2026-09-14T08:00:00Z'], start=1):
            views.authoring_upsert(views.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
                'id': f'OCC-SYNC-10-{index}',
                'live_session_id': 'LIVE-SYNC-10',
                'session_number': index,
                'scheduled_start': start,
                'scheduled_end': start,
                'status': 'scheduled',
            })

        response = self.client.get(
            '/curriculum_api/curriculum/teams-meetings/summary/?occurrence_dates=1'
        ).json()['results']
        row = next(item for item in response if item['moduleCatalogueId'] == 'MOD-SYNC-10')
        self.assertEqual(row['syncState'], 'in-sync')
        self.assertEqual(row['differingOccurrenceCount'], 0)


def staff_user_row(name, email='', access='tutor', row_id=1, status='FullUser'):
    """One ``enrolment.Staff_users`` row, as curriculum reads it.

    Curriculum has no staff table of its own to seed: a tutor or a coach exists
    because an administrator invited them and gave them that access, so a test
    that needs one hands this to ``fetch_staff_users_by_access``.
    """
    return {
        'id': row_id,
        'uuid': f'{access}-uuid-{row_id}',
        'Username': name,
        'Email': email or f'{views.slugify(name)}@example.com',
        'Phone_number': '',
        'Position': access.title(),
        ' Status': status,
        'Access': access,
        'Created_at': None,
        'Updated_at': None,
    }


def staff_directory(*rows):
    """A ``fetch_staff_users_by_access`` stand-in for the rows given."""
    def fetch(access):
        return [row for row in rows if views.clean_str(row.get('Access')).lower() == views.clean_str(access).lower()]
    return fetch


class CurriculumPersistenceFixtures:
    """Schema setup and payload builders shared by the persistence test suites.

    Split out from ``CurriculumPersistenceTests`` so sibling suites can reuse the
    fixture without inheriting — and therefore re-running — every test in it.
    """

    def setUp(self):
        views.reset_schema_ready_flags()
        views.invalidate_curriculum_cache()
        self.client = Client()
        self.ensure_programmes_table()
        self.ensure_ksb_profiles_table()
        views.ensure_module_authoring_tables()
        self.clear_curriculum_tables()
        self.seed_ksb_profile()

    def ensure_programmes_table(self):
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('create schema if not exists curriculum')
                table_name = 'curriculum.programmes'
            else:
                table_name = 'programmes'
            cursor.execute(
                f"""
                create table if not exists {table_name} (
                    id varchar(128) primary key,
                    programme_id varchar(128),
                    program_id varchar(128),
                    name varchar(255),
                    sub varchar(255),
                    standard varchar(255),
                    level varchar(64),
                    owner varchar(255),
                    created_by varchar(255),
                    color varchar(32),
                    description text,
                    structure_type varchar(32),
                    status varchar(32),
                    is_active boolean,
                    is_archived boolean,
                    created_at timestamp,
                    updated_at timestamp
                )
                """
            )

    def ensure_ksb_profiles_table(self):
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('create schema if not exists curriculum')
                table_name = 'curriculum.ksb_profiles'
                json_type = 'jsonb'
            else:
                table_name = 'ksb_profiles'
                json_type = 'text'
            cursor.execute(
                f"""
                create table if not exists {table_name} (
                    id varchar(128) primary key,
                    name varchar(255),
                    programme_name varchar(255),
                    programme_id varchar(128),
                    ksb_profile_id varchar(128),
                    legacy_numeric_id bigint,
                    ksb_items {json_type},
                    is_active boolean,
                    created_at timestamp,
                    updated_at timestamp
                )
                """
            )

    def clear_curriculum_tables(self):
        table_names = [
            views.AUTHORING_KSB_MAPPINGS_TABLE,
            views.AUTHORING_COMPONENTS_TABLE,
            views.AUTHORING_WEEKS_TABLE,
            views.AUTHORING_COMPLETION_TABLE,
            views.AUTHORING_ADVANCED_TABLE,
            views.AUTHORING_MODULES_TABLE,
            views.GROUPS_TABLE,
            views.COHORT_AUTHORING_DETAILS_TABLE,
            'ksb_profiles',
            'programmes',
        ]
        with connection.cursor() as cursor:
            for table in table_names:
                cursor.execute(f'delete from {views.authoring_table_name(table)}')

    def seed_ksb_profile(self):
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                insert into {views.authoring_table_name('ksb_profiles')}
                (id, name, programme_name, programme_id, ksb_profile_id, ksb_items, is_active)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    'KSBP-DATA',
                    'Data Analyst KSBs',
                    'Data Analyst',
                    'PROG-DATA',
                    'KSBP-DATA',
                    json.dumps([{'id': 'KSB-K1', 'code': 'K1', 'type': 'knowledge', 'description': 'Data basics'}]),
                    True,
                ],
            )

    def post_json(self, path, payload):
        return self.client.post(path, data=json.dumps(payload), content_type='application/json')

    def patch_json(self, path, payload):
        return self.client.patch(path, data=json.dumps(payload), content_type='application/json')

    def tree_payload(self, programme_id='PROG-DATA', cohort_id='COHORT-DATA-1', group_id='GROUP-DATA-1', module_id='MOD-DATA-1', tutor='Tutor One'):
        """One programme -> cohort -> group -> module, ready to POST.

        `tutor` is a parameter because the fixture keeps the same weekday, time
        and start date whichever ids it is given: a test that builds a *second*
        tree has to name a different tutor, or the save is refused as a
        double-booking (see tests_tutor_schedule_conflicts).
        """
        return {
            'programme': {
                'id': programme_id,
                'name': 'Data Analyst',
                'standard': 'Data Analyst',
                'level': '4',
                'structureType': 'scheduled',
            },
            'cohorts': [{
                'id': cohort_id,
                'name': 'September 2026',
                'programmeId': programme_id,
                'startDate': '2026-09-01',
                'endDate': '2027-08-31',
                'durationMonths': 12,
                'groups': [{
                    'id': group_id,
                    'name': 'Group A',
                    'coach': 'Coach One',
                    'tutor': tutor,
                    'weekDays': 'Wednesday',
                    'startTime': '10:00',
                    'endTime': '12:00',
                    'modules': [{
                        'moduleName': 'Data Foundations',
                        'catalogueId': module_id,
                        'startDate': '2026-09-02',
                        'endDate': '2026-09-09',
                        'sessionsNumber': 2,
                        'weekDays': 'Wednesday',
                        'coach': 'Coach One',
                        'tutor': tutor,
                        'weekStructure': [{
                            'id': 'WEEK-DATA-1',
                            'weekNumber': 1,
                            'title': 'Week 1',
                            'components': [{
                                'id': 'COMP-DATA-1',
                                'type': 'reading',
                                'title': 'Read data brief',
                                'ksbMappings': [{
                                    'id': 'KSBMAP-DATA-1',
                                    'code': 'K1',
                                    'description': 'Data basics',
                                    'classification': 'main',
                                    # weight_class is the canonical delivery
                                    # weighting; 'main' maps to 'hard'.
                                    'weight_class': 'hard',
                                    'weight': 1,
                                    'sourceType': 'framework',
                                    'sourceId': 'KSBP-DATA',
                                }],
                            }],
                        }],
                    }],
                }],
            }],
            'removeMissing': False,
            'hydrationComplete': True,
        }

    def row(self, table, key, value):
        rows = views.authoring_fetch_all(table, f'{key} = %s', [value])
        self.assertTrue(rows, f'Expected {table}.{key}={value}')
        return rows[0]

    def count(self, table, key, value):
        return len(views.authoring_fetch_all(table, f'{key} = %s', [value]))

    def assertRemovedFromCurriculum(self, cohort_id, group_id):
        """A removed cohort/group is soft-deleted, not physically dropped.

        Curriculum rows are retained on delete (migrations 0040/0041) so that
        historical learner progress stays joinable to the curriculum it was
        delivered against. "Removed" therefore means two things, and both are
        asserted here: the row carries its soft-delete markers, and it no
        longer appears in the read model the UI builds from.
        """
        cohort = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', cohort_id)
        self.assertTrue(views.row_has_deleted_at(cohort), f'{cohort_id} should carry deleted_at')
        self.assertEqual(cohort['status'], 'archived')

        group = self.row(views.GROUPS_TABLE, 'group_id', group_id)
        self.assertTrue(views.row_has_deleted_at(group), f'{group_id} should carry deleted_at')

        cohorts, groups = views.build_cohorts_and_groups()
        self.assertNotIn(cohort_id, [item.get('id') for item in cohorts])
        self.assertNotIn(group_id, [item.get('id') for item in groups])

    def resolve_structures(self, *identifiers):
        response = self.post_json(
            '/curriculum_api/curriculum/modules/resolve-structures/',
            {'modules': [{'requestId': item, 'identifier': item} for item in identifiers]},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return {item['requestId']: item for item in response.json()['results']}


class CurriculumPersistenceHarness(CurriculumPersistenceFixtures, TestCase):
    """Persistence fixtures for tests that do not cross HTTP connection cleanup."""


class CurriculumPersistenceTransactionHarness(CurriculumPersistenceFixtures, CoachTransactionTestCase):
    """Use real transactions when HTTP middleware releases request connections."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.clear_curriculum_tables)


class CurriculumPersistenceTests(CurriculumPersistenceHarness):
    @patch.object(views, 'find_skills_england_standard', return_value={'id': 'st0845-v1-1'})
    def test_ksb_profile_can_link_and_unlink_its_parent_standard(self, _standard):
        response = self.patch_json(
            '/curriculum_api/curriculum/ksb-frameworks/KSBP-DATA/',
            {'standardSourceId': 'standard:st0845-v1-1'},
        )
        self.assertEqual(response.status_code, 200, response.content)
        profile = self.row('ksb_profiles', 'id', 'KSBP-DATA')
        self.assertEqual(profile['standard_source_id'], 'st0845-v1-1')

        response = self.patch_json(
            '/curriculum_api/curriculum/ksb-frameworks/KSBP-DATA/',
            {'standardSourceId': ''},
        )
        self.assertEqual(response.status_code, 200, response.content)
        profile = self.row('ksb_profiles', 'id', 'KSBP-DATA')
        self.assertEqual(profile['standard_source_id'], '')

    def test_tree_save_persists_components_without_a_module_builder_save(self):
        """A wizard-created module's components must land in the normalized tables
        immediately, so resolve-structures reports them with no manual Save."""
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)

        result = self.resolve_structures('MOD-DATA-1')['MOD-DATA-1']
        self.assertTrue(result['found'])
        self.assertTrue(result['hasComponents'])
        self.assertEqual(result['componentCount'], 1)

    def seed_same_titled_module(self, module_catalogue_id, component_count):
        """Persist a second module sharing 'Data Foundations' as its title."""
        payload = self.tree_payload(
            programme_id='PROG-OTHER',
            cohort_id='COHORT-OTHER-1',
            group_id='GROUP-OTHER-1',
            module_id=module_catalogue_id,
            tutor='Tutor Two',
        )
        module = payload['cohorts'][0]['groups'][0]['modules'][0]
        week = module['weekStructure'][0]
        # Distinct from the first module's week: a shared id would move the week
        # (and its components) onto this module instead of copying it.
        week['id'] = f'WEEK-{module_catalogue_id}'
        template = week['components'][0]
        week['components'] = [
            {**template, 'id': f'COMP-{module_catalogue_id}-{index}'}
            for index in range(component_count)
        ]
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

    def test_title_only_resolve_refuses_to_guess_between_same_titled_modules(self):
        """Resolving by title must never silently pick a winner.

        Two modules can legitimately share a title across programmes. Before this
        guard the endpoint ranked title matches by component count and returned the
        heaviest, so a group could be served another programme's content with no
        signal that a guess had been made. The ambiguity is reported instead.
        """
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)
        # Deliberately heavier than MOD-DATA-1 (1 component): under the old
        # component-count tie-break this is exactly the row that would have won.
        self.seed_same_titled_module('MOD-OTHER-1', component_count=3)

        result = self.resolve_structures('Data Foundations')['Data Foundations']

        self.assertFalse(result['found'])
        self.assertTrue(result['ambiguous'])
        # Not 'missing': the content exists, so the UI must not tell the user to
        # go create the module in Module Builder.
        self.assertFalse(result['missing'])
        self.assertEqual(
            sorted(result['ambiguousCatalogueIds']),
            ['MOD-DATA-1', 'MOD-OTHER-1'],
        )
        self.assertIsNone(result.get('module'))

    def test_exact_id_wins_over_a_colliding_title(self):
        """An id is an assertion, not a guess: title collisions must not weaken it."""
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.seed_same_titled_module('MOD-OTHER-1', component_count=3)

        result = self.resolve_structures('MOD-DATA-1')['MOD-DATA-1']

        self.assertTrue(result['found'])
        self.assertFalse(result.get('ambiguous', False))
        # The heavier same-titled module must not be substituted in.
        self.assertEqual(result['catalogueId'], 'MOD-DATA-1')
        self.assertEqual(result['componentCount'], 1)

    def test_unique_title_still_resolves(self):
        """The title fallback stays useful when it is unambiguous."""
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)

        result = self.resolve_structures('Data Foundations')['Data Foundations']

        self.assertTrue(result['found'])
        self.assertEqual(result['catalogueId'], 'MOD-DATA-1')

    def test_tree_save_persists_module_scheduling_values(self):
        payload = self.tree_payload()
        module = payload['cohorts'][0]['groups'][0]['modules'][0]
        module.update({
            'startDate': '2026-09-18',
            'endDate': '2026-09-30',
            'sessionsNumber': 1,
            'weekDays': 'Friday',
            'startTime': '09:30',
            'endTime': '11:30',
            'tutor': 'Unassigned',
        })

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(views.format_date(row['start_date']), '2026-09-18')
        self.assertEqual(views.format_date(row['end_date']), '2026-09-30')
        self.assertEqual(row['sessions_number'], 1)
        self.assertEqual(row['session_week_day'], 'Friday')
        self.assertEqual(row['session_start_time'], '09:30')
        self.assertEqual(row['session_end_time'], '11:30')
        self.assertIsNone(row['tutor_name'])

    def test_tree_update_resaves_visible_module_delivery_and_builder_weeks(self):
        first = self.tree_payload()
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', first)
        self.assertEqual(response.status_code, 200, response.content)

        second = self.tree_payload()
        second['partialTree'] = True
        second['cohorts'][0]['groups'][0]['modulesPartial'] = True
        module = second['cohorts'][0]['groups'][0]['modules'][0]
        module.update({
            'startDate': '2026-09-16',
            'endDate': '2026-10-14',
            'sessionsNumber': 5,
            'weeks': 5,
            'tutor': 'Tutor Two',
            'weekStructure': [
                {
                    'id': f'WEEK-DATA-{index}',
                    'weekNumber': index,
                    'title': f'Week {index}',
                    'components': [],
                }
                for index in range(1, 6)
            ],
        })

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', second)
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(views.format_date(row['start_date']), '2026-09-16')
        self.assertEqual(views.format_date(row['end_date']), '2026-10-14')
        self.assertEqual(row['sessions_number'], 5)
        self.assertEqual(row['tutor_name'], 'Tutor Two')
        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 5)

        result = self.resolve_structures('MOD-DATA-1')['MOD-DATA-1']
        self.assertTrue(result['found'])
        self.assertEqual(len(result['module']['weekStructure']), 5)

    def test_tree_save_programme_is_active_even_when_client_sends_draft(self):
        payload = self.tree_payload(programme_id='PROG-DRAFT')
        payload['programme']['name'] = 'Draft Programme'
        payload['programme']['status'] = 'draft'

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['programme']['status'], 'active')

        response = self.client.get('/curriculum_api/curriculum/programmes/')
        self.assertEqual(response.status_code, 200, response.content)
        active = next(item for item in response.json()['results'] if item['sourceId'] == 'PROG-DRAFT')
        self.assertEqual(active['status'], 'active')

        response = self.client.get('/curriculum_api/curriculum/programmes/?visibility=all')
        self.assertEqual(response.status_code, 200, response.content)
        active = next(item for item in response.json()['results'] if item['sourceId'] == 'PROG-DRAFT')
        self.assertEqual(active['status'], 'active')

        payload['programme']['status'] = 'active'
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        response = self.client.get('/curriculum_api/curriculum/programmes/')
        self.assertEqual(response.status_code, 200, response.content)
        active = next(item for item in response.json()['results'] if item['sourceId'] == 'PROG-DRAFT')
        self.assertEqual(active['status'], 'active')

    def test_integer_weeks_count_never_replaces_the_authored_week_structure(self):
        """`weeks` is a session count in the attachment payload. Treating it as a
        structure silently dropped every authored week and component."""
        payload = self.tree_payload()
        module = payload['cohorts'][0]['groups'][0]['modules'][0]
        module['weeks'] = 2  # the integer form the wizard sends alongside weekStructure

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.row(views.AUTHORING_WEEKS_TABLE, 'id', 'WEEK-DATA-1')['module_catalogue_id'], 'MOD-DATA-1')

    def test_reattaching_without_a_structure_keeps_the_stored_components(self):
        """The wizard re-sends an attachment carrying only the integer session
        count. That must not wipe the weeks/components already stored."""
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)

        payload = self.tree_payload()
        module = payload['cohorts'][0]['groups'][0]['modules'][0]
        module.pop('weekStructure')
        module['weeks'] = 5  # integer session count only, no authored structure

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        # Falls back to the stored structure rather than treating 5 as the weeks.
        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        result = self.resolve_structures('MOD-DATA-1')['MOD-DATA-1']
        self.assertTrue(result['hasComponents'])
        self.assertEqual(result['componentCount'], 1)

    def test_group_module_attachment_persists_supplied_components(self):
        """POST /groups/<id>/modules/ with authored weeks must write the normalized
        rows straight away instead of waiting for a Module Builder save."""
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        response = self.post_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/modules/', {
            'moduleName': 'Attached Module',
            'catalogueId': 'MOD-ATTACH-1',
            'sessionsNumber': 1,
            'startDate': '2026-09-16',
            'weekStructure': [{
                'id': 'WEEK-ATTACH-1',
                'weekNumber': 1,
                'title': 'Week 1',
                'components': [{
                    'id': 'COMP-ATTACH-1',
                    'type': 'reading',
                    'title': 'Attached reading',
                }],
            }],
        })
        self.assertIn(response.status_code, (200, 201), response.content)

        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-ATTACH-1'), 1)
        result = self.resolve_structures('MOD-ATTACH-1')['MOD-ATTACH-1']
        self.assertTrue(result['found'])
        self.assertTrue(result['hasComponents'])
        self.assertEqual(result['componentCount'], 1)

    def test_attachment_session_count_handles_both_weeks_forms(self):
        self.assertEqual(views.attachment_session_count({'sessionsNumber': 4, 'weeks': 9}), 4)
        self.assertEqual(views.attachment_session_count({'weeks': 6}), 6)
        self.assertEqual(views.attachment_session_count({'weeks': '3'}), 3)
        self.assertEqual(views.attachment_session_count({'weeks': [{'id': 'W1'}, {'id': 'W2'}]}), 2)
        self.assertEqual(views.attachment_session_count({}), 0)

    def test_attachment_week_structure_rejects_non_list_weeks(self):
        self.assertEqual(views.attachment_week_structure({'weeks': 5}), [])
        self.assertEqual(views.attachment_week_structure({'weeks': '5'}), [])
        self.assertEqual(views.attachment_week_structure({'weeks': [1, 2]}), [])
        self.assertEqual(views.attachment_week_structure({'weekStructure': [], 'weeks': 5}), [])

        authored = [{'id': 'WEEK-1', 'components': []}]
        self.assertEqual(views.attachment_week_structure({'weeks': authored}), authored)
        self.assertEqual(views.attachment_week_structure({'weekStructure': authored, 'weeks': 5}), authored)
        # Falls back to what is already stored only when nothing is supplied.
        self.assertEqual(views.attachment_week_structure({}, {'weekStructure': authored}), authored)

    def test_save_rejects_an_integer_week_structure_instead_of_dropping_weeks(self):
        errors = views.validate_module_authoring_payload({'title': 'Module', 'weekStructure': 4})
        self.assertTrue(any(item['path'] == 'weekStructure' for item in errors), errors)

        with self.assertRaises(views.ModuleAuthoringValidationError):
            views.save_module_authoring_structure('MOD-INT-WEEKS', {'title': 'Module', 'weekStructure': 4})

    def test_intentionally_empty_module_resolves_as_empty_not_missing(self):
        payload = self.tree_payload()
        payload['cohorts'][0]['groups'][0]['modules'][0]['weekStructure'] = []

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        result = self.resolve_structures('MOD-DATA-1')['MOD-DATA-1']
        self.assertTrue(result['found'])
        self.assertFalse(result.get('missing', False))
        self.assertFalse(result['hasComponents'])
        self.assertEqual(result['componentCount'], 0)

    def test_unknown_module_resolves_as_missing(self):
        result = self.resolve_structures('MOD-NOT-A-REAL-MODULE')['MOD-NOT-A-REAL-MODULE']
        self.assertFalse(result['found'])
        self.assertTrue(result['missing'])
        self.assertEqual(result['componentCount'], 0)

    def test_resaving_the_tree_does_not_duplicate_weeks_or_components(self):
        for _ in range(2):
            response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
            self.assertEqual(response.status_code, 200, response.content)

        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_KSB_MAPPINGS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)

    def test_create_programme_tree_with_component_ksb_mapping(self):
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)

        cohort = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        group = self.row(views.GROUPS_TABLE, 'group_id', 'GROUP-DATA-1')
        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        component = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-DATA-1')
        mapping = self.row(views.AUTHORING_KSB_MAPPINGS_TABLE, 'id', 'KSBMAP-DATA-1')

        self.assertEqual(cohort['programme_id'], 'PROG-DATA')
        self.assertIn('GROUP-DATA-1', views.as_json_value(cohort['group_ids'], []))
        self.assertEqual(group['cohort_id'], 'COHORT-DATA-1')
        self.assertEqual(module['group_id'], 'GROUP-DATA-1')
        self.assertEqual(module['tutor_name'], 'Tutor One')
        self.assertEqual(component['module_catalogue_id'], 'MOD-DATA-1')
        self.assertEqual(mapping['component_id'], 'COMP-DATA-1')

    def test_live_session_link_is_saved_in_dedicated_component_column(self):
        payload = self.tree_payload()
        component = payload['cohorts'][0]['groups'][0]['modules'][0]['weekStructure'][0]['components'][0]
        component.update({
            'type': 'live-session',
            'title': 'Live data workshop',
            'settings': {
                'liveSessionUrl': 'https://teams.microsoft.com/l/meetup-join/test-meeting',
                'teamsLiveSessionId': 'LIVE-TEST-1',
            },
        })

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        saved = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-DATA-1')
        self.assertEqual(
            saved['live_sessions_link'],
            'https://teams.microsoft.com/l/meetup-join/test-meeting',
        )

    def test_global_ksb_coverage_uses_framework_definitions(self):
        response = self.client.get('/curriculum_api/curriculum/ksb-coverage/')
        self.assertEqual(response.status_code, 200, response.content)

        payload = response.json()
        self.assertEqual(payload['scope'], 'all')
        self.assertEqual(payload['summary']['overall']['required'], 1)
        self.assertEqual(payload['summary']['overall']['unmapped'], 1)
        self.assertEqual(payload['items'][0]['code'], 'K1')
        self.assertEqual(payload['items'][0]['source_id'], 'KSBP-DATA')

    def seed_second_ksb_profile(self):
        """A second active profile, so the union of every profile is visibly
        different from any single one of them."""
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                insert into {views.authoring_table_name('ksb_profiles')}
                (id, name, programme_name, programme_id, ksb_profile_id, ksb_items, is_active)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    'KSBP-OTHER',
                    'Marketing Manager KSBs',
                    'Marketing Manager',
                    'PROG-MARKETING',
                    'KSBP-OTHER',
                    json.dumps([{'id': 'KSB-S1', 'code': 'S1', 'type': 'skill', 'description': 'Campaign planning'}]),
                    True,
                ],
            )
        views.invalidate_curriculum_cache()

    def test_programme_coverage_reads_the_ksb_source_stored_on_the_programme(self):
        """The programme row's own `ksb_profile_source_id` decides the required set.

        It is the value the Programme form writes and the one that cascades onto
        the programme's modules, but the coverage path used to ignore it and fall
        back to matching the programme *name* against a profile. A programme
        whose name matched nothing silently got the union of every profile.
        """
        self.seed_second_ksb_profile()
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)
        views.update_rows('programmes', 'programme_id = %s', ['PROG-DATA'], {
            'name': 'A name no profile lists',
            'ksb_profile_source_id': 'KSBP-DATA',
        })
        views.invalidate_curriculum_cache()

        payload = self.client.get('/curriculum_api/curriculum/programmes/PROG-DATA/ksb-coverage/').json()

        self.assertEqual(payload['source']['origin'], 'programme')
        self.assertEqual(payload['source']['id'], 'KSBP-DATA')
        self.assertEqual(payload['source']['type'], 'framework')
        # Only the stored profile's KSBs -- the other profile's S1 is not required here.
        self.assertEqual([item['code'] for item in payload['items']], ['K1'])

    def test_programme_coverage_says_when_no_source_identified_it(self):
        """With nothing stored and no name to match, the union of every profile
        still answers -- but the response says that is what happened, so a page
        can tell "no source set" apart from "this source has these KSBs"."""
        self.seed_second_ksb_profile()
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.assertEqual(response.status_code, 200, response.content)
        views.update_rows('programmes', 'programme_id = %s', ['PROG-DATA'], {
            'name': 'A name no profile lists',
            'ksb_profile_source_id': '',
        })
        views.update_authoring_rows(views.AUTHORING_MODULES_TABLE, 'programme_id = %s', ['PROG-DATA'], {
            'programme_name': 'A name no profile lists',
            'ksb_profile_source_id': '',
        })
        views.invalidate_curriculum_cache()

        payload = self.client.get('/curriculum_api/curriculum/programmes/PROG-DATA/ksb-coverage/').json()

        self.assertEqual(payload['source']['origin'], 'all-profiles')
        self.assertEqual(payload['source']['id'], '')
        self.assertEqual(sorted(item['code'] for item in payload['items']), ['K1', 'S1'])

    def test_delete_ksb_framework_removes_profile_row(self):
        response = self.client.delete('/curriculum_api/curriculum/ksb-frameworks/KSBP-DATA/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['deleted'])
        self.assertFalse(views.authoring_fetch_all('ksb_profiles', 'id = %s', ['KSBP-DATA']))

    def test_required_count_can_exclude_mapping_only_ksbs(self):
        coverage = build_coverage(
            [{'code': 'K1', 'type': 'knowledge', 'source_type': 'framework', 'source_id': 'KSBP-DATA'}],
            [
                {'id': 'MAP-1', 'module_catalogue_id': 'MOD-1', 'ksb_code': 'K1', 'source_type': 'framework', 'source_id': 'KSBP-DATA', 'weight': 100},
                {'id': 'MAP-2', 'module_catalogue_id': 'MOD-1', 'ksb_code': 'K99', 'source_type': 'framework', 'source_id': 'KSBP-DATA', 'weight': 100},
            ],
            [{'module_catalogue_id': 'MOD-1', 'title': 'Module 1'}],
            [],
            [],
            include_mapping_only=False,
        )

        self.assertEqual(coverage['summary']['overall']['required'], 1)
        self.assertEqual([item['code'] for item in coverage['items']], ['K1'])

    def test_coverage_mappings_report_component_otjh(self):
        coverage = build_coverage(
            [{'code': 'K1', 'type': 'knowledge', 'source_type': 'framework', 'source_id': 'KSBP-DATA'}],
            [
                {'id': 'MAP-1', 'module_catalogue_id': 'MOD-1', 'component_id': 'COMP-1', 'ksb_code': 'K1', 'source_type': 'framework', 'source_id': 'KSBP-DATA', 'weight': 100},
                {'id': 'MAP-2', 'module_catalogue_id': 'MOD-1', 'ksb_code': 'K1', 'source_type': 'framework', 'source_id': 'KSBP-DATA', 'weight': 50},
            ],
            [{'module_catalogue_id': 'MOD-1', 'title': 'Module 1'}],
            [],
            [{'id': 'COMP-1', 'title': 'Component 1', 'type': 'reading', 'expected_otjh': '1.50'}],
        )

        mappings = {mapping['mapping_id']: mapping for mapping in coverage['items'][0]['mappings']}
        # Component-level mapping reports the component's expected OTJH.
        self.assertEqual(mappings['MAP-1']['component_otjh'], 1.5)
        self.assertEqual(mappings['MAP-1']['componentOtjh'], 1.5)
        # A module-level mapping is not attached to a component, so it has none.
        self.assertEqual(mappings['MAP-2']['component_otjh'], 0)

    def test_add_cohort_without_removing_existing_when_hydration_incomplete(self):
        first = self.tree_payload()
        self.post_json('/curriculum_api/curriculum/programmes/tree/', first)
        second = self.tree_payload(cohort_id='COHORT-DATA-2', group_id='GROUP-DATA-2', module_id='MOD-DATA-2', tutor='Tutor Two')
        second['removeMissing'] = True
        second['hydrationComplete'] = False

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', second)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')['status'], 'archived')
        # curriculum.groups lost its `status` column in migration 0013; group
        # removal is now recorded as a soft delete, so "still live" means no
        # deleted_at rather than a non-archived status.
        self.assertIsNone(self.row(views.GROUPS_TABLE, 'group_id', 'GROUP-DATA-1')['deleted_at'])

    def test_tree_save_removes_missing_rows_when_hydrated(self):
        first = self.tree_payload()
        self.post_json('/curriculum_api/curriculum/programmes/tree/', first)
        second = self.tree_payload(cohort_id='COHORT-DATA-2', group_id='GROUP-DATA-2', module_id='MOD-DATA-2', tutor='Tutor Two')
        second['removeMissing'] = True
        second['hydrationComplete'] = True

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', second)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['removedMissing'])
        self.assertRemovedFromCurriculum('COHORT-DATA-1', 'GROUP-DATA-1')

    def test_tree_save_explicitly_removes_deleted_cohort(self):
        first = self.tree_payload()
        self.post_json('/curriculum_api/curriculum/programmes/tree/', first)
        second = self.tree_payload(cohort_id='COHORT-DATA-2', group_id='GROUP-DATA-2', module_id='MOD-DATA-2', tutor='Tutor Two')
        second['removeMissing'] = False
        second['hydrationComplete'] = False
        second['removeCohortIds'] = ['COHORT-DATA-1']

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', second)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('COHORT-DATA-1', response.json()['removedCohortIds'])
        self.assertIn('GROUP-DATA-1', response.json()['removedGroupIds'])
        self.assertRemovedFromCurriculum('COHORT-DATA-1', 'GROUP-DATA-1')

    def test_tree_save_persists_an_authored_apprenticeship_end_date(self):
        payload = self.tree_payload()
        payload['cohorts'][0]['epaMonths'] = 5
        payload['cohorts'][0]['apprenticeshipEndOverride'] = '2028-02-29'

        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        self.assertEqual(views.format_date(row['apprenticeship_end_override']), '2028-02-29')
        # The cache SQL consumers read follows the authored date, not the EPA sum
        # (2027-08-31 + 5 months would be 2028-01-31).
        self.assertEqual(views.format_date(row['apprenticeship_end_date']), '2028-02-29')
        self.assertEqual(views.parse_epa_months(row['epa_months']), 5)

        cohort = response.json()['cohorts'][0]
        self.assertEqual(cohort['apprenticeshipEndDate'], '2028-02-29')
        self.assertEqual(cohort['apprenticeshipEndOverride'], '2028-02-29')

    def test_an_omitted_apprenticeship_end_date_keeps_the_authored_one(self):
        payload = self.tree_payload()
        payload['cohorts'][0]['epaMonths'] = 5
        payload['cohorts'][0]['apprenticeshipEndOverride'] = '2028-02-29'
        self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)

        # A partial save that never mentions the date must not drop it.
        later = self.tree_payload()
        later['cohorts'][0]['epaMonths'] = 5
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', later)
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        self.assertEqual(views.format_date(row['apprenticeship_end_override']), '2028-02-29')
        self.assertEqual(views.format_date(row['apprenticeship_end_date']), '2028-02-29')

    def test_clearing_the_authored_date_restores_the_epa_calculation(self):
        payload = self.tree_payload()
        payload['cohorts'][0]['epaMonths'] = 5
        payload['cohorts'][0]['apprenticeshipEndOverride'] = '2028-02-29'
        self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)

        cleared = self.tree_payload()
        cleared['cohorts'][0]['epaMonths'] = 5
        cleared['cohorts'][0]['apprenticeshipEndOverride'] = None
        response = self.post_json('/curriculum_api/curriculum/programmes/tree/', cleared)
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        self.assertIsNone(row['apprenticeship_end_override'])
        self.assertEqual(views.format_date(row['apprenticeship_end_date']), '2028-01-31')
        self.assertEqual(response.json()['cohorts'][0]['apprenticeshipEndDate'], '2028-01-31')

    def test_patching_only_the_apprenticeship_end_date_keeps_the_epa_period(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.patch_json('/curriculum_api/curriculum/cohorts/COHORT-DATA-1/', {'epaMonths': 5})

        response = self.patch_json(
            '/curriculum_api/curriculum/cohorts/COHORT-DATA-1/',
            {'apprenticeshipEndDate': '2028-02-29'},
        )
        self.assertEqual(response.status_code, 200, response.content)

        row = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        self.assertEqual(views.parse_epa_months(row['epa_months']), 5)
        self.assertEqual(views.format_date(row['apprenticeship_end_override']), '2028-02-29')
        self.assertEqual(views.format_date(row['apprenticeship_end_date']), '2028-02-29')

    def test_programme_card_counts_cohorts_without_delivery_modules(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': 'COHORT-DATA-2',
            'cohort_name': 'October 2026',
            'programme_id': 'PROG-DATA',
            'programme_name': 'Data Analyst',
            'start_date': '2026-10-01',
            'end_date': '2027-09-30',
            'status': 'planned',
            'group_ids': views.json_db_value([]),
            'module_names': views.json_db_value([]),
            'holiday_ids': views.json_db_value([]),
        })
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': 'COHORT-DATA-ARCHIVED',
            'cohort_name': 'Archived Cohort',
            'programme_id': 'PROG-DATA',
            'programme_name': 'Data Analyst',
            'status': 'archived',
            'group_ids': views.json_db_value([]),
            'module_names': views.json_db_value([]),
            'holiday_ids': views.json_db_value([]),
        })

        programmes = views.build_programmes(
            views.get_training_rows(),
            views.get_program_config_rows(),
            views.get_ksb_profile_rows(),
        )
        programme = next(item for item in programmes if item['id'] == 'PROG-DATA')

        self.assertEqual(programme['cohorts'], 2)
        self.assertEqual(programme['modules'], 1)

    def test_programme_detail_only_returns_the_selected_programme_tree(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        other = self.tree_payload(
            programme_id='PROG-OTHER',
            cohort_id='COHORT-OTHER-1',
            group_id='GROUP-OTHER-1',
            module_id='MOD-OTHER-1',
        )
        other['programme']['name'] = 'Other Programme'
        other['programme']['standard'] = 'Other Standard'
        other_module = other['cohorts'][0]['groups'][0]['modules'][0]
        other_module['weekStructure'][0]['id'] = 'WEEK-OTHER-1'
        other_module['weekStructure'][0]['components'][0]['id'] = 'COMP-OTHER-1'
        self.post_json('/curriculum_api/curriculum/programmes/tree/', other)

        response = self.client.get('/curriculum_api/curriculum/programmes/PROG-DATA/detail/')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()

        self.assertEqual([item['id'] for item in payload['flat']['cohorts']], ['COHORT-DATA-1'])
        self.assertEqual([item['id'] for item in payload['flat']['groups']], ['GROUP-DATA-1'])
        self.assertEqual([item['moduleCatalogueId'] for item in payload['flat']['modules']], ['MOD-DATA-1'])
        self.assertEqual(payload['flat']['modules'][0]['name'], 'Data Foundations')
        self.assertEqual(payload['flat']['modules'][0]['weekStructure'][0]['id'], 'WEEK-DATA-1')
        self.assertEqual([item['id'] for item in payload['cohorts']], ['COHORT-DATA-1'])
        self.assertEqual([item['id'] for item in payload['cohorts'][0]['groups']], ['GROUP-DATA-1'])
        self.assertEqual([item['moduleCatalogueId'] for item in payload['cohorts'][0]['groups'][0]['modules']], ['MOD-DATA-1'])

    def test_post_add_module_does_not_detach_existing_modules(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        response = self.post_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/modules/', {
            'moduleName': 'Data Visualisation',
            'catalogueId': 'MOD-DATA-2',
            'startDate': '2026-09-16',
            'sessionsNumber': 1,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')['group_id'], 'GROUP-DATA-1')
        self.assertEqual(self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-2')['group_id'], 'GROUP-DATA-1')

    def test_patch_replace_group_modules_detaches_omitted_modules(self):
        self.test_post_add_module_does_not_detach_existing_modules()
        response = self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/modules/', {'modules': [{
            'moduleName': 'Data Visualisation',
            'catalogueId': 'MOD-DATA-2',
            'startDate': '2026-09-16',
            'sessionsNumber': 1,
        }]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')['group_id'])
        self.assertEqual(self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-2')['group_id'], 'GROUP-DATA-1')

    def test_move_group_updates_both_cohort_group_ids_and_modules(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        second = self.tree_payload(cohort_id='COHORT-DATA-2', group_id='GROUP-DATA-2', module_id='MOD-DATA-2', tutor='Tutor Two')
        second['removeMissing'] = False
        self.post_json('/curriculum_api/curriculum/programmes/tree/', second)

        response = self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {'cohortId': 'COHORT-DATA-2'})
        self.assertEqual(response.status_code, 200, response.content)
        old_ids = views.as_json_value(self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')['group_ids'], [])
        new_ids = views.as_json_value(self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-2')['group_ids'], [])
        self.assertNotIn('GROUP-DATA-1', old_ids)
        self.assertIn('GROUP-DATA-1', new_ids)
        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(module['cohort_id'], 'COHORT-DATA-2')
        self.assertEqual(module['programme_id'], 'PROG-DATA')

    def test_editing_group_delivery_slot_moves_existing_module_sessions(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        response = self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {
            'weekDays': 'Friday',
            'startTime': '13:00',
            'endTime': '15:00',
        })
        self.assertEqual(response.status_code, 200, response.content)

        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(module['session_week_day'], 'Friday')
        self.assertEqual(module['session_start_time'], '13:00')
        self.assertEqual(module['session_end_time'], '15:00')

        sessions = views.build_sessions_from_authoring_modules(
            views.safe_authoring_module_rows(),
            {},
        )
        module_sessions = [item for item in sessions if item['moduleCatalogueId'] == 'MOD-DATA-1']
        self.assertTrue(module_sessions)
        self.assertTrue(all(item['day'] == 'Friday' for item in module_sessions))
        self.assertTrue(all(item['startTime'] == '13:00' for item in module_sessions))
        self.assertTrue(all(item['endTime'] == '15:00' for item in module_sessions))

    def test_editing_group_delivery_slot_persists_the_unbooked_live_session_date(self):
        payload = self.tree_payload()
        payload['cohorts'][0]['groups'][0]['modules'][0]['weekStructure'][0]['components'] = [{
            'id': 'COMP-LIVE-1',
            'type': 'live-session',
            'title': 'Live Teams Session 1',
            'settings': {
                'sessionDate': '2026-09-02',
                'sessionDay': 'Wednesday',
                'sessionTime': '10:00',
                'durationMinutes': 120,
                'customSetting': 'preserve-me',
            },
        }]
        self.post_json('/curriculum_api/curriculum/programmes/tree/', payload)

        response = self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {
            'weekDays': 'Friday',
            'startTime': '13:00',
            'endTime': '15:00',
        })
        self.assertEqual(response.status_code, 200, response.content)

        component = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-LIVE-1')
        settings = views.as_json_value(component['settings_json'], {})
        self.assertEqual(settings['sessionDate'], '2026-09-04')
        self.assertEqual(settings['sessionDay'], 'Friday')
        # A component-specific clock is an authored override, not group data.
        self.assertEqual(settings['sessionTime'], '10:00')
        self.assertEqual(settings['customSetting'], 'preserve-me')

    def test_id_edits_preserve_parent_relationships(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.patch_json('/curriculum_api/curriculum/cohorts/COHORT-DATA-1/', {'name': 'Renamed Cohort'})
        self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {'name': 'Renamed Group', 'coach': 'Coach Two', 'tutor': 'Tutor Two'})
        self.patch_json('/curriculum_api/curriculum/modules/MOD-DATA-1/', {'name': 'Renamed Module'})

        cohort = self.row(views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id', 'COHORT-DATA-1')
        group = self.row(views.GROUPS_TABLE, 'group_id', 'GROUP-DATA-1')
        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(cohort['programme_id'], 'PROG-DATA')
        self.assertEqual(group['cohort_id'], 'COHORT-DATA-1')
        self.assertEqual(group['programme_id'], 'PROG-DATA')
        self.assertEqual(group['coach_name'], 'Coach Two')
        # Tutor ownership moved from curriculum.groups to curriculum.modules
        # in migration 0023, which dropped groups.tutor_name.
        self.assertEqual(module['tutor_name'], 'Tutor Two')
        self.assertEqual(module['cohort_id'], 'COHORT-DATA-1')
        self.assertEqual(module['group_id'], 'GROUP-DATA-1')

    def test_module_patch_alias_preserves_components_and_delivery_metadata(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        response = self.patch_json('/curriculum_api/curriculum/modules/catalogue-module-MOD-DATA-1/', {
            'name': 'Renamed From Wizard',
        })
        self.assertEqual(response.status_code, 200, response.content)

        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(module['title'], 'Renamed From Wizard')
        self.assertEqual(module['programme_id'], 'PROG-DATA')
        self.assertEqual(module['cohort_id'], 'COHORT-DATA-1')
        self.assertEqual(module['group_id'], 'GROUP-DATA-1')
        self.assertEqual(module['session_week_day'], 'Wednesday')
        self.assertEqual(module['session_start_time'], '10:00')
        self.assertEqual(module['session_end_time'], '12:00')
        self.assertEqual(self.count(views.AUTHORING_WEEKS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)
        self.assertEqual(self.count(views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id', 'MOD-DATA-1'), 1)

    def test_component_post_patch_delete_writes_components_table(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        created = self.post_json('/curriculum_api/curriculum/components/', {
            'id': 'COMP-API-1',
            'module': 'Data Foundations',
            'moduleCatalogueId': 'MOD-DATA-1',
            'weekId': 'WEEK-DATA-1',
            'weekNumber': 1,
            'title': 'API reading',
            'type': 'reading',
            'expectedOtjh': 1.5,
        })
        self.assertEqual(created.status_code, 201, created.content)
        component = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-API-1')
        self.assertEqual(component['module_catalogue_id'], 'MOD-DATA-1')
        self.assertEqual(component['week_id'], 'WEEK-DATA-1')
        self.assertEqual(component['title'], 'API reading')

        patched = self.patch_json('/curriculum_api/curriculum/components/COMP-API-1/', {
            'title': 'API reading updated',
            'points': 7,
        })
        self.assertEqual(patched.status_code, 200, patched.content)
        component = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-API-1')
        self.assertEqual(component['title'], 'API reading updated')
        self.assertEqual(component['points'], 7)

        deleted = self.client.delete('/curriculum_api/curriculum/components/COMP-API-1/')
        self.assertEqual(deleted.status_code, 200, deleted.content)
        component = self.row(views.AUTHORING_COMPONENTS_TABLE, 'id', 'COMP-API-1')
        self.assertTrue(views.row_has_deleted_at(component))

    def test_group_read_path_reports_tutor_assigned_through_modules(self):
        """A tutor assigned to a group must be readable back from the group payload.

        groups.tutor_name was dropped in migration 0023, so the group PATCH fans the
        tutor out to the group's module rows. The group read path must recover it
        from there instead of always reporting 'Unassigned'.
        """
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {'tutor': 'Tutor Two'})

        detail = next(
            item for item in views.group_authoring_detail_rows()
            if item['id'] == 'GROUP-DATA-1'
        )
        self.assertEqual(detail['tutor'], 'Tutor Two')

        _cohorts, groups = views.build_cohorts_and_groups()
        group = next(item for item in groups if item['id'] == 'GROUP-DATA-1')
        self.assertEqual(group['tutor'], 'Tutor Two')

    def test_group_reports_tutor_from_wizard_payload(self):
        """The tree payload assigns 'Tutor One' to the group, so the read path
        must report that name rather than the old hardcoded 'Unassigned'."""
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        detail = next(
            item for item in views.group_authoring_detail_rows()
            if item['id'] == 'GROUP-DATA-1'
        )
        self.assertEqual(detail['tutor'], 'Tutor One')

    def test_group_without_tutor_reports_unassigned(self):
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())
        self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {'tutor': ''})

        detail = next(
            item for item in views.group_authoring_detail_rows()
            if item['id'] == 'GROUP-DATA-1'
        )
        self.assertEqual(detail['tutor'], 'Unassigned')

    @patch('curriculum_api.views.fetch_staff_users_by_access')
    def test_group_staff_assignments_accept_email_identifiers_and_store_canonical_names(self, fetch_staff_users):
        # A screen can hand over whichever identifier it holds; the name stored
        # on the curriculum is the one the staff directory has for that person,
        # so two screens naming them differently still write one name.
        fetch_staff_users.side_effect = staff_directory(
            staff_user_row('Coach Two', 'coach.two@example.com', access='coach', row_id=1),
            staff_user_row('Tutor Two', 'tutor.two@example.com', access='tutor', row_id=2),
        )
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        response = self.patch_json('/curriculum_api/curriculum/groups/GROUP-DATA-1/', {
            'coach': 'coach.two@example.com',
            'tutor': 'tutor.two@example.com',
        })
        self.assertEqual(response.status_code, 200, response.content)

        group = self.row(views.GROUPS_TABLE, 'group_id', 'GROUP-DATA-1')
        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')

        self.assertEqual(group['coach_name'], 'Coach Two')
        # Tutor ownership moved from curriculum.groups to curriculum.modules
        # in migration 0023, which dropped groups.tutor_name.
        self.assertEqual(module['tutor_name'], 'Tutor Two')
        self.assertEqual(module['group_id'], 'GROUP-DATA-1')
        # The assignment lives here and nowhere else: the person's own record is
        # in Users, and it carries no curriculum of its own to keep in step.
        self.assertEqual(
            [profile['name'] for profile in views.build_staff_user_profile_collection('coach')],
            ['Coach Two'],
        )

class CurriculumStaffAreReadFromTheDirectoryTests(CurriculumPersistenceHarness):
    """Curriculum reads its tutors and coaches; it never owns them.

    A tutor or a coach is a staff user an administrator invited and gave that
    access to (``enrolment.Staff_users.Access``). Curriculum used to keep its own
    ``tutors``/``coaches`` rows as well -- created through this API, and derived
    from any name typed onto a delivery row -- which meant the same person could
    exist twice, a tutor could exist whom nobody had granted tutor access, and
    deleting them in Users left the curriculum copy behind. Now there is one
    source, and these tests hold that: the list reads the directory, and the
    write verbs are refused rather than quietly filling a second table.
    """

    def directory(self, *rows):
        patcher = patch(
            'curriculum_api.views.fetch_staff_users_by_access',
            side_effect=staff_directory(*rows),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def staff(self, role, visibility=''):
        path = f'/curriculum_api/curriculum/{"tutors" if role == "tutor" else "coaches"}/'
        if visibility:
            path += f'?visibility={visibility}'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return body.get('results') if isinstance(body, dict) else body

    def test_the_list_is_the_staff_who_hold_that_access(self):
        self.directory(
            staff_user_row('Tutor One', 'tutor.one@example.com', access='tutor', row_id=1),
            staff_user_row('Coach One', 'coach.one@example.com', access='coach', row_id=2),
        )
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        self.assertEqual([item['name'] for item in self.staff('tutor')], ['Tutor One'])
        self.assertEqual([item['name'] for item in self.staff('coach')], ['Coach One'])
        # And the curriculum they hold is read off the curriculum itself.
        tutor = self.staff('tutor')[0]
        coach = self.staff('coach')[0]
        self.assertIn('MOD-DATA-1', tutor['assignedModuleIds'])
        self.assertEqual(tutor['moduleCount'], 1)
        self.assertIn('GROUP-DATA-1', coach['assignedGroupIds'])
        self.assertEqual(coach['groupCount'], 1)

    def test_a_name_typed_onto_a_module_does_not_invent_a_tutor(self):
        # The old derived stand-in: a delivery row naming somebody was enough to
        # make them a tutor. Access is what makes somebody a tutor now.
        self.directory()
        self.post_json('/curriculum_api/curriculum/programmes/tree/', self.tree_payload())

        module = self.row(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id', 'MOD-DATA-1')
        self.assertEqual(views.clean_str(module.get('tutor_name')), 'Tutor One')
        self.assertEqual(self.staff('tutor'), [])

    def test_an_archived_staff_user_is_out_of_the_operational_list(self):
        self.directory(
            staff_user_row('Tutor Gone', 'gone@example.com', access='tutor', row_id=3, status='archived'),
            staff_user_row('Tutor Here', 'here@example.com', access='tutor', row_id=4),
        )

        self.assertEqual([item['name'] for item in self.staff('tutor')], ['Tutor Here'])
        self.assertEqual(
            sorted(item['name'] for item in self.staff('tutor', visibility='all')),
            ['Tutor Gone', 'Tutor Here'],
        )

    def test_one_person_can_be_asked_for_by_any_identifier_a_screen_holds(self):
        self.directory(staff_user_row('Tutor One', 'tutor.one@example.com', access='tutor', row_id=7))

        for identifier in ('tutor-uuid-7', 'tutor.one@example.com', 'Tutor One'):
            response = self.client.get(f'/curriculum_api/curriculum/tutors/{identifier}/')
            self.assertEqual(response.status_code, 200, (identifier, response.content))
            self.assertEqual(response.json()['profile']['name'], 'Tutor One')

        missing = self.client.get('/curriculum_api/curriculum/tutors/nobody@example.com/')
        self.assertEqual(missing.status_code, 404)

    def test_curriculum_refuses_to_create_change_or_delete_a_staff_member(self):
        self.directory(staff_user_row('Tutor One', 'tutor.one@example.com', access='tutor', row_id=7))

        created = self.post_json('/curriculum_api/curriculum/tutors/', {
            'name': 'Tutor Invented',
            'email': 'invented@example.com',
        })
        patched = self.patch_json('/curriculum_api/curriculum/tutors/tutor-uuid-7/', {'name': 'Renamed'})
        deleted = self.client.delete('/curriculum_api/curriculum/tutors/tutor-uuid-7/')

        for response in (created, patched, deleted):
            self.assertEqual(response.status_code, 405, response.content)
            self.assertIn('managed in Users', response.json()['error'])
        # Nothing was written, so the list still answers with the directory.
        self.assertEqual([item['name'] for item in self.staff('tutor')], ['Tutor One'])


class CurriculumCacheTests(SimpleTestCase):
    """Guards the authoring cache's correctness properties.

    The cache has a process-local L1 and a Django-cache L2, so the risky failure
    modes are about invalidation: a stale entry surviving a write, or a failed
    build being memoised.
    """

    def setUp(self):
        views.invalidate_curriculum_cache()
        self.addCleanup(views.invalidate_curriculum_cache)

    def test_value_is_cached_and_factory_runs_once(self):
        calls = []

        def factory():
            calls.append(1)
            return 'value'

        results = [views.cached_curriculum_value('k', factory) for _ in range(3)]
        self.assertEqual(results, ['value'] * 3)
        self.assertEqual(len(calls), 1)

    def test_invalidation_forces_a_rebuild(self):
        views.cached_curriculum_value('k', lambda: 'first')
        views.invalidate_curriculum_cache()
        self.assertEqual(views.cached_curriculum_value('k', lambda: 'second'), 'second')

    def test_failed_build_is_not_cached(self):
        def boom():
            raise RuntimeError('build failed')

        with self.assertRaises(RuntimeError):
            views.cached_curriculum_value('k', boom)
        # A failure must not be memoised, otherwise one transient DB error would
        # serve an error (or a hole) for the whole TTL.
        self.assertNotIn('k', views._CURRICULUM_CACHE)
        self.assertEqual(views.cached_curriculum_value('k', lambda: 'recovered'), 'recovered')

    def test_slow_build_does_not_block_other_keys(self):
        started = threading.Event()
        release = threading.Event()
        other = {}

        def slow():
            started.set()
            release.wait(5)
            return 'slow'

        builder = threading.Thread(target=lambda: views.cached_curriculum_value('slow', slow))
        builder.start()
        try:
            self.assertTrue(started.wait(5))
            reader = threading.Thread(
                target=lambda: other.setdefault('value', views.cached_curriculum_value('fast', lambda: 'fast'))
            )
            reader.start()
            reader.join(3)
            # The factory must run outside the lock; otherwise a cold-database build
            # serialises every other curriculum read behind it.
            self.assertEqual(other.get('value'), 'fast')
        finally:
            release.set()
            builder.join(5)

    def test_write_during_an_in_flight_build_discards_the_stale_result(self):
        started = threading.Event()
        release = threading.Event()

        def slow():
            started.set()
            release.wait(5)
            return 'pre-write rows'

        builder = threading.Thread(target=lambda: views.cached_curriculum_value('race', slow))
        builder.start()
        try:
            self.assertTrue(started.wait(5))
            views.invalidate_curriculum_cache()
        finally:
            release.set()
            builder.join(5)

        self.assertNotIn('race', views._CURRICULUM_CACHE)
        self.assertEqual(views.cached_curriculum_value('race', lambda: 'post-write rows'), 'post-write rows')

    def test_invalidation_is_global_so_every_write_clears_every_key(self):
        # Curriculum writes cascade widely: a module edit changes the overview, the
        # programme tree, the module list and any resolve-structures entry naming it.
        # Rather than enumerate that fan-out per write, invalidation clears the whole
        # cache — the trade-off is a cold rebuild after each write, in exchange for
        # making a missed key impossible. This test fails if it is ever narrowed to
        # targeted eviction without the per-key mapping being proven first.
        for key in ('overview:operational:compact', 'overview:all:full', 'modules:all:enriched'):
            views.cached_curriculum_value(key, lambda: 'pre-write')
        views.cached_curriculum_value(
            views.structure_payloads_cache_key(['MOD-A'], include_staff=False),
            lambda: 'pre-write',
        )
        self.assertNotEqual(views._CURRICULUM_CACHE, {})

        views.invalidate_curriculum_cache()

        self.assertEqual(views._CURRICULUM_CACHE, {})

    def test_invalidation_forgets_missing_tables_but_keeps_known_ones(self):
        """A curriculum write is not DDL.

        A table that was there before the write is still there after it, and
        re-asking information_schema for every one of them cost seconds on the
        rebuild that runs straight after a save. A cached *miss* is still dropped:
        the ensure_* helpers provision tables, so "not there" can go stale.
        """
        views._TABLE_EXISTS_CACHE['curriculum.example'] = True
        views._TABLE_EXISTS_CACHE['curriculum.not_yet_provisioned'] = False
        views.invalidate_curriculum_cache()
        self.assertEqual(views._TABLE_EXISTS_CACHE, {'curriculum.example': True})


class CurriculumPayloadPerformanceTests(SimpleTestCase):
    def test_compact_payload_does_not_build_session_details(self):
        rows = {
            'training': [],
            'modules': [],
            'authoring_modules': [],
            'ksb_profiles': [],
            'program_configs': [],
            'holidays': [],
            'tutors': [],
            'coaches': [],
            'tutor_modules': [],
        }
        with patch.object(views, 'build_sessions', side_effect=AssertionError('must stay lazy')), \
             patch.object(views, 'build_sessions_from_authoring_modules', side_effect=AssertionError('must stay lazy')), \
             patch.object(views, 'build_programmes', return_value=[]), \
             patch.object(views, 'build_cohorts_and_groups', return_value=([], [])), \
             patch.object(views, 'build_ksb_data', return_value=([], [])):
            payload = views.build_curriculum_payload_from_rows(rows, compact=True)
        self.assertEqual(payload['sessions'], [])

    def test_compact_module_list_is_whitelisted_not_subtracted(self):
        """`/curriculum/modules/?compact=true` returns the whitelist, nothing else.

        A field added to a module row later must not reach compact callers until
        somebody puts it in COMPACT_MODULE_LIST_FIELDS deliberately -- that is the
        whole point of projecting onto an allowlist rather than deleting keys.
        """
        module = {
            'id': 'MOD-1', 'name': 'Module', 'programmeId': 'PROG-1', 'programme': 'Programme',
            'weeks': 4, 'ksbCount': 2, 'notes': 'Notes', 'color': '#6941c6',
            'weeklySchedule': [{'week': 1}], 'sessionHolidays': [], 'deliveryWeeks': 4,
            # None of these may survive the projection.
            'weekStructure': [{'id': 'WEEK-1', 'components': [{'id': 'COMP-1'}]}],
            'sessionNames': ['Week 1', 'Week 2', 'Week 3'],
            'deliveryMetadata': {'teamsMeetingUrl': 'https://teams.example.invalid/x'},
            'author': '', 'assignments': 0, 'qualityScore': 72, 'sourceType': 'authoring',
            'deliveryRowId': 17, 'legacyModuleId': 'old', 'invalidModuleCatalogueId': 'bad',
            'somethingAddedNextYear': 'must not leak',
        }

        compact = views.compact_module_list_rows([module])[0]

        self.assertEqual(
            set(compact),
            (set(module) & views.COMPACT_MODULE_LIST_FIELDS) | {'sessionNamesCount'},
        )
        # The fields the edit form reopens a module with survive: dropping these
        # would not just blank a screen, the next save would write the blank back.
        for field in ('weeklySchedule', 'sessionHolidays', 'deliveryWeeks', 'notes', 'color'):
            self.assertEqual(compact[field], module[field])
        # sessionNames goes, but its count stays -- four callers read `.length`.
        self.assertNotIn('sessionNames', compact)
        self.assertEqual(compact['sessionNamesCount'], 3)
        # Teams meeting identity is not in a list response at all.
        self.assertNotIn('deliveryMetadata', compact)
        # The source row is untouched: it is shared with the non-compact response.
        self.assertIn('weekStructure', module)

    def test_compact_module_list_keeps_every_field_its_filters_read(self):
        """The endpoint filters after projecting, so the filter keys must survive."""
        for field in ('programmeId', 'cohortId', 'groupId', 'status'):
            self.assertIn(field, views.COMPACT_MODULE_LIST_FIELDS)

    def test_operational_payload_drops_archived_authoring_module_from_delivery_list(self):
        """A soft-archived authoring row must not leak through compact delivery rows."""
        rows = {
            'training': [],
            'modules': [],
            'authoring_modules': [
                {'module_catalogue_id': 'MOD-ARCHIVED', 'status': 'archived'},
                {'module_catalogue_id': 'MOD-LIVE', 'status': 'draft'},
            ],
            'ksb_profiles': [],
            'program_configs': [],
            'holidays': [],
            'tutors': [],
            'coaches': [],
            'tutor_modules': [],
        }
        delivery_rows = [
            {'id': 'MOD-ARCHIVED', 'moduleCatalogueId': 'MOD-ARCHIVED', 'catalogueId': 'MOD-ARCHIVED'},
            {'id': 'MOD-LIVE', 'moduleCatalogueId': 'MOD-LIVE', 'catalogueId': 'MOD-LIVE'},
        ]
        with patch.object(views, 'build_modules', return_value=delivery_rows), \
             patch.object(views, 'build_programmes', return_value=[]), \
             patch.object(views, 'build_cohorts_and_groups', return_value=([], [])), \
             patch.object(views, 'build_ksb_data', return_value=([], [])), \
             patch.object(views, 'attach_module_assignment_counts'):
            payload = views.build_curriculum_payload_from_rows(rows, compact=True)

        self.assertEqual(
            [module['moduleCatalogueId'] for module in payload['modules']],
            ['MOD-LIVE'],
        )

    def test_pagination_is_opt_in_and_reports_total_count(self):
        request = RequestFactory().get('/curriculum/modules/', {'page': 2, 'page_size': 2})
        results, metadata = views.paginate_curriculum_results(request, ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(results, ['c', 'd'])
        self.assertEqual(metadata, {
            'count': 5,
            'page': 2,
            'pageSize': 2,
            'pages': 3,
            'hasNext': True,
            'hasPrevious': True,
        })

    def test_pagination_caps_untrusted_page_size(self):
        request = RequestFactory().get('/curriculum/components/', {'page_size': 100000})
        results, metadata = views.paginate_curriculum_results(request, list(range(300)))
        self.assertEqual(len(results), 250)
        self.assertEqual(metadata['pageSize'], 250)

    def test_a_read_scope_reads_each_table_once(self):
        """build_programmes() asks the same whole-table questions once per programme.

        programme_component_ksb_mapping_count() and programme_learner_ksb_progress()
        each re-read authoring modules, programmes, cohorts, groups and ksb_profiles
        in full, so a ten-programme payload issued ~300 round trips for ~30 rows. The
        scope collapses those repeats; without it a remote database made every write
        response wait tens of seconds.
        """
        reads = []

        @views.scoped_curriculum_read
        def fetch(table, where_sql='', params=None):
            reads.append((table, where_sql, tuple(params or ())))
            return []

        with views.curriculum_read_scope():
            fetch('modules')
            fetch('modules')
            fetch('weeks', 'module_catalogue_id = %s', ['MOD-A'])
            fetch('weeks', 'module_catalogue_id = %s', ['MOD-A'])
            fetch('weeks', 'module_catalogue_id = %s', ['MOD-B'])

        self.assertEqual(reads, [
            ('modules', '', ()),
            ('weeks', 'module_catalogue_id = %s', ('MOD-A',)),
            ('weeks', 'module_catalogue_id = %s', ('MOD-B',)),
        ])

    def test_outside_a_read_scope_every_call_still_hits_the_database(self):
        """The memo must not outlive a build: a write path reads back what it wrote."""
        reads = []

        @views.scoped_curriculum_read
        def fetch(table):
            reads.append(table)
            return []

        fetch('modules')
        fetch('modules')
        self.assertEqual(reads, ['modules', 'modules'])

    def test_a_read_scope_does_not_survive_its_own_block(self):
        reads = []

        @views.scoped_curriculum_read
        def fetch(table):
            reads.append(table)
            return []

        with views.curriculum_read_scope():
            fetch('modules')
        with views.curriculum_read_scope():
            fetch('modules')
        self.assertEqual(reads, ['modules', 'modules'])

    def test_a_nested_scope_reuses_the_outer_one(self):
        """build_curriculum_payload() opens a scope and calls a function that opens
        another; the inner block must not tear the outer memo down on exit."""
        reads = []

        @views.scoped_curriculum_read
        def fetch(table):
            reads.append(table)
            return []

        with views.curriculum_read_scope():
            with views.curriculum_read_scope():
                fetch('modules')
            fetch('modules')
        self.assertEqual(reads, ['modules'])

    def test_an_unkeyable_argument_reads_rather_than_raising(self):
        """A caller must never be broken by an argument the memo cannot key on."""
        class Unhashable:
            __hash__ = None

        reads = []

        @views.scoped_curriculum_read
        def fetch(spec):
            reads.append(spec)
            return []

        with views.curriculum_read_scope():
            fetch(Unhashable())
            fetch(Unhashable())
        self.assertEqual(len(reads), 2)

    def test_programme_detail_payload_builds_inside_a_read_scope(self):
        """The wizard's detail endpoint pays the same per-programme read tax.

        build_curriculum_programme_tree_detail_payload() calls build_programmes(),
        so without a scope it re-read authoring modules, programmes, cohorts, groups
        and ksb_profiles once per programme: ~250 round trips to a remote database
        (18-30s) instead of ~40 (~3s). The wizard opens on that response, so a cold
        cache left the cohort step spinning for half a minute.
        """
        scopes = []

        def probe(identifier, visibility):
            scopes.append(getattr(views._CURRICULUM_READ_SCOPE, 'memo', None) is not None)
            return None

        with patch.object(views, '_build_curriculum_programme_tree_detail_payload', probe):
            views.build_curriculum_programme_tree_detail_payload('PROG-1', 'all')

        self.assertEqual(scopes, [True])

    def test_equal_list_arguments_share_one_read(self):
        """Query params arrive as lists, which are not hashable on their own."""
        reads = []

        @views.scoped_curriculum_read
        def fetch(table, params):
            reads.append((table, tuple(params)))
            return []

        with views.curriculum_read_scope():
            fetch('weeks', ['MOD-A', 'MOD-B'])
            fetch('weeks', ['MOD-A', 'MOD-B'])
            fetch('weeks', ['MOD-B', 'MOD-A'])
        self.assertEqual(reads, [
            ('weeks', ('MOD-A', 'MOD-B')),
            ('weeks', ('MOD-B', 'MOD-A')),
        ])


class ProgrammePermanentDeleteTests(SimpleTestCase):
    """A permanent programme delete has to satisfy the guards, not remove them.

    Migration 0038 gave cohorts, groups, modules and week_templates ON DELETE
    RESTRICT foreign keys into curriculum.programmes, which is why a hand-written
    ``delete from curriculum."programmes"`` is rejected with
    cohorts_programme_id_fkey. The application answer is ordering: delete the
    children first, inside one transaction, and leave the constraints in place.
    """

    def test_an_empty_id_list_never_widens_to_the_whole_table(self):
        """The property everything else rests on: no ids means no statement.

        A clause builder that fell back to a default predicate for an empty list
        would turn 'delete this programme's components' into 'delete every
        component'.
        """
        self.assertEqual(views.curriculum_in_clause('cohort_id', []), ('', []))
        self.assertEqual(views.curriculum_in_clause('cohort_id', ['', '  ']), ('', []))
        self.assertEqual(views.curriculum_where_any([('', []), ('', [])]), ('', []))

    def test_clauses_are_ored_with_their_params_in_order(self):
        first = views.curriculum_in_clause('programme_id', ['PROG-1'])
        second = views.curriculum_in_clause('group_id', ['GROUP-1', 'GROUP-2'])
        where_sql, params = views.curriculum_where_any([first, ('', []), second])
        self.assertEqual(where_sql, '"programme_id" in (%s) or "group_id" in (%s, %s)')
        self.assertEqual(params, ['PROG-1', 'GROUP-1', 'GROUP-2'])

    def test_identifier_candidates_collapse_to_the_distinct_ids(self):
        candidates = views.programme_identifier_candidates(
            'PROG-1',
            {'id': 'PROG-1', 'sourceId': 'PROG-1-legacy'},
            {'programme_id': 'PROG-1'},
        )
        self.assertEqual(candidates, ['PROG-1', 'PROG-1-legacy'])

    def _record_permanent_delete(self):
        """Run the delete with every query stubbed, returning the tables hit."""
        deletes = []
        detaches = []

        def record(table, where_sql, params):
            deletes.append(table)
            self.assertTrue(where_sql, f'{table} was deleted without a where clause')
            self.assertTrue(params, f'{table} was deleted without parameters')
            return [{'id': 'row'}]

        plan = {
            'candidates': ['PROG-1'],
            'childIds': {
                'cohortIds': ['COHORT-1'],
                'groupIds': ['GROUP-1'],
                'moduleIds': ['MOD-1'],
                'weekIds': ['WEEK-1'],
                'componentIds': ['COMP-1'],
                'weekTemplateIds': ['TPL-1'],
            },
        }
        # The detach reads the module and week rows it is about to orphan, which
        # this SimpleTestCase has no database for. It has its own DB-backed cover
        # in tests_content_library; here only the ordering of the deletes matters.
        with patch.object(views, 'table_exists', lambda table: True), \
                patch.object(views, 'delete_rows', record), \
                patch.object(
                    views, 'detach_programme_content_to_library',
                    lambda plan, removed: detaches.append(plan),
                ), \
                patch.object(views, 'invalidate_curriculum_cache', lambda: None):
            removed = views.permanently_delete_programme_structure(plan)
        return deletes, detaches, removed

    def test_children_are_deleted_before_their_parents(self):
        """Ordering is the whole point, so it is pinned table by table."""
        deletes, _, removed = self._record_permanent_delete()

        for child, parent in (
            ('ksb_mappings', 'modules'),
            ('quiz_course_links', 'modules'),
            ('module_details', 'modules'),
            ('module_completion_criteria', 'modules'),
            ('live_sessions', 'modules'),
            ('modules', 'groups'),
            ('groups', 'cohorts'),
            ('cohorts', 'programmes'),
        ):
            self.assertLess(
                deletes.index(child), deletes.index(parent),
                f'{child} must be deleted before {parent}',
            )
        self.assertEqual(deletes[-1], 'programmes')
        self.assertEqual(removed['programmes'], 1)

    def test_authored_content_is_detached_rather_than_deleted(self):
        """Weeks, components, quizzes and templates outlive their programme.

        They are the reusable output of Curriculum Studio, so a permanent
        programme delete unhooks them into the library instead of destroying
        them. Only the delivery scaffolding is actually deleted.
        """
        deletes, detaches, _ = self._record_permanent_delete()

        for table in ('components', 'weeks', 'quizzes', 'week_templates', 'week_template_components'):
            self.assertNotIn(table, deletes, f'{table} must not be deleted by a programme delete')
        self.assertEqual(len(detaches), 1, 'content must be detached exactly once')

    def test_the_detach_runs_before_the_parents_are_removed(self):
        """It reads the module and week titles, so it cannot run afterwards."""
        order = []
        plan = {
            'candidates': ['PROG-1'],
            'childIds': {'moduleIds': ['MOD-1'], 'weekIds': ['WEEK-1'], 'componentIds': ['COMP-1']},
        }
        with patch.object(views, 'table_exists', lambda table: True), \
                patch.object(
                    views, 'delete_rows',
                    lambda table, where_sql, params: order.append(('delete', table)) or [{'id': 'row'}],
                ), \
                patch.object(
                    views, 'detach_programme_content_to_library',
                    lambda plan, removed: order.append(('detach', None)),
                ), \
                patch.object(views, 'invalidate_curriculum_cache', lambda: None):
            views.permanently_delete_programme_structure(plan)

        self.assertEqual(order[0], ('detach', None))

    def test_nothing_is_deleted_without_a_programme_id(self):
        deletes = []
        with patch.object(views, 'table_exists', lambda table: True),              patch.object(views, 'delete_rows', lambda *args: deletes.append(args)):
            removed = views.permanently_delete_programme_structure({'candidates': [], 'childIds': {}})
        self.assertEqual(deletes, [])
        self.assertEqual(removed, {})

    def test_a_live_programme_is_refused_before_any_query_runs(self):
        """is_archived is the switch that turns an irreversible delete on."""
        response = views.permanent_programme_delete_response(
            'PROG-1', {'status': 'active'}, {'programme_id': 'PROG-1', 'is_archived': False},
        )
        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.content)
        self.assertEqual(payload['reason'], 'programme-not-archived')
        self.assertFalse(payload['deleted'])

    def test_permanent_delete_is_opt_in_per_request(self):
        factory = RequestFactory()
        self.assertFalse(views.request_wants_permanent_programme_delete(
            factory.delete('/curriculum/programmes/PROG-1/')))
        self.assertFalse(views.request_wants_permanent_programme_delete(
            factory.delete('/curriculum/programmes/PROG-1/?permanent=false')))
        for query in ('permanent=true', 'hard=1', 'mode=permanent'):
            self.assertTrue(views.request_wants_permanent_programme_delete(
                factory.delete(f'/curriculum/programmes/PROG-1/?{query}')), query)

    def test_learner_delivery_rows_are_reported_not_deleted(self):
        """Learner training plans reference this content with ON DELETE RESTRICT.

        A learner's plan is their delivery record, so a programme delete reports
        it as a blocker instead of removing it.
        """
        self.assertEqual(
            [key for _table, _column, key in views.PROGRAMME_PERMANENT_DELETE_LEARNER_LINKS],
            ['moduleIds', 'weekIds', 'componentIds'],
        )
        with patch.object(views.connection, 'vendor', 'sqlite'):
            self.assertEqual(views.programme_permanent_delete_blockers({'moduleIds': ['MOD-1']}), {})


class KsbFrameworkLibraryVisibilityTests(SimpleTestCase):
    """The framework library is a catalogue of standards, not a view of programmes.

    Frameworks used to be filtered down to those a currently-visible programme
    pointed at, which hid every profile that no live programme had picked up yet
    -- including one the user had only just created. Deactivating a profile stays
    the way to take it out of the catalogue.
    """

    @staticmethod
    def _profile(profile_id, name, programme_ids, is_active=True, standard_source_id=''):
        return {
            'id': profile_id,
            'name': name,
            'programme_ids': json.dumps(programme_ids),
            'knowledge_codes': json.dumps(['K1', 'K2']),
            'skill_codes': json.dumps(['S1']),
            'behaviour_codes': json.dumps([]),
            'ksb_items': json.dumps([]),
            'is_active': is_active,
            'standard_source_id': standard_source_id,
            'created_by': 'Tester',
        }

    def _payload(self, profiles, programmes):
        rows = {
            'training': [],
            'modules': [],
            'authoring_modules': [],
            'ksb_profiles': profiles,
            'program_configs': [],
            'holidays': [],
            'tutors': [],
            'coaches': [],
            'tutor_modules': [],
        }
        with patch.object(views, 'build_programmes', return_value=programmes),              patch.object(views, 'build_modules', return_value=[]),              patch.object(views, 'build_cohorts_and_groups', return_value=([], [])):
            return views.build_curriculum_payload_from_rows(rows, compact=True)

    def test_a_framework_no_live_programme_points_at_is_still_listed(self):
        payload = self._payload(
            [
                self._profile('KSBP-1', 'Associate Project Manager', ['PROG-LIVE']),
                self._profile('KSBP-2', 'Marketing Manager', ['PROG-RETIRED']),
                self._profile('KSBP-3', 'Brand new profile', []),
            ],
            [{'name': 'Live programme', 'standard': 'Associate Project Manager', 'sourceId': 'PROG-LIVE'}],
        )
        self.assertEqual(
            sorted(item['name'] for item in payload['ksbFrameworks']),
            ['Associate Project Manager', 'Brand new profile', 'Marketing Manager'],
        )
        self.assertEqual(payload['stats']['ksbFrameworks'], 3)
        self.assertEqual(len(payload['ksbSets']), 3)

    def test_deactivating_a_profile_is_what_removes_it(self):
        payload = self._payload(
            [
                self._profile('KSBP-1', 'Associate Project Manager', ['PROG-LIVE']),
                self._profile('KSBP-2', 'Retired standard', ['PROG-LIVE'], is_active=False),
            ],
            [{'name': 'Live programme', 'standard': 'Associate Project Manager', 'sourceId': 'PROG-LIVE'}],
        )
        self.assertEqual([item['name'] for item in payload['ksbFrameworks']], ['Associate Project Manager'])

    def test_the_all_visibility_still_carries_inactive_profiles(self):
        rows = {
            'training': [],
            'modules': [],
            'authoring_modules': [],
            'ksb_profiles': [self._profile('KSBP-2', 'Retired standard', [], is_active=False)],
            'program_configs': [],
            'holidays': [],
            'tutors': [],
            'coaches': [],
            'tutor_modules': [],
        }
        with patch.object(views, 'build_programmes', return_value=[]),              patch.object(views, 'build_modules', return_value=[]),              patch.object(views, 'build_cohorts_and_groups', return_value=([], [])):
            payload = views.build_curriculum_payload_from_rows(rows, visibility='all', compact=True)
        self.assertEqual([item['name'] for item in payload['ksbFrameworks']], ['Retired standard'])

    def test_framework_and_set_expose_the_explicit_parent_standard(self):
        payload = self._payload(
            [self._profile('KSBP-1', 'Custom project profile', [], standard_source_id='st0845-v1-1')],
            [],
        )
        self.assertEqual(payload['ksbFrameworks'][0]['standardSourceId'], 'st0845-v1-1')
        self.assertEqual(payload['ksbSets'][0]['standardSourceId'], 'st0845-v1-1')


class StructurePayloadsCacheKeyTests(SimpleTestCase):
    """Guards the cache identity used by the resolve-structures endpoint.

    Two properties matter and pull in opposite directions: requests that would
    produce different bodies must never share an entry, while requests that differ
    only in identifier order or duplication must share one, or the endpoint caches
    nothing useful.
    """

    def key(self, ids, **options):
        options = {'include_staff': False, 'include_quality': False, 'include_extra': False, **options}
        return views.structure_payloads_cache_key(ids, **options)

    def test_each_shaping_flag_changes_the_key(self):
        base = self.key(['MOD-A'])
        for flag in ('include_staff', 'include_quality', 'include_extra'):
            with self.subTest(flag=flag):
                self.assertNotEqual(base, self.key(['MOD-A'], **{flag: True}))

    def test_every_flag_combination_is_distinct(self):
        keys = {
            self.key(['MOD-A'], include_staff=staff, include_quality=quality, include_extra=extra)
            for staff in (False, True)
            for quality in (False, True)
            for extra in (False, True)
        }
        # Eight combinations, eight entries: no pair of differently-shaped responses
        # can be served from one another's cache slot.
        self.assertEqual(len(keys), 8)

    def test_an_added_option_does_not_collide_with_the_current_shape(self):
        # The key is derived from the options actually passed, so a future caller that
        # introduces a flag gets a fresh entry rather than inheriting today's body.
        self.assertNotEqual(self.key(['MOD-A']), self.key(['MOD-A'], include_learners=False))

    def test_reordered_and_duplicated_identifiers_reuse_one_entry(self):
        # Safe to share: the builder returns a mapping keyed by catalogue id, so the
        # request order never reaches the response.
        self.assertEqual(self.key(['MOD-A', 'MOD-B']), self.key(['MOD-B', 'MOD-A']))
        self.assertEqual(self.key(['MOD-A', 'MOD-B']), self.key(['MOD-B', 'MOD-A', 'MOD-A']))
        self.assertEqual(self.key(['MOD-A', ' MOD-B ']), self.key(['MOD-B', 'MOD-A']))

    def test_different_identifier_sets_do_not_collide(self):
        self.assertNotEqual(self.key(['MOD-A']), self.key(['MOD-B']))
        self.assertNotEqual(self.key(['MOD-A']), self.key(['MOD-A', 'MOD-B']))
        # A subset must not read a superset's entry: the builder only returns rows for
        # the ids it was given, so the bodies genuinely differ.
        self.assertNotEqual(self.key(['MOD-A', 'MOD-B']), self.key(['MOD-A', 'MOD-B', 'MOD-C']))

    def test_identifier_count_is_not_confusable_by_concatenation(self):
        # 'MOD-A' + 'B' must not hash the same as 'MOD-AB': ids are space-joined and
        # the count is in the key, so neither can be forged from the other.
        self.assertNotEqual(self.key(['MOD-A', 'B']), self.key(['MOD-AB']))

    def test_key_length_is_bounded_for_large_requests(self):
        # A resolve request may name hundreds of modules; the key must stay short or the
        # process cache holds megabytes of keys for the whole TTL. Only the count digits
        # vary with input size — the identifiers themselves are hashed to a fixed width.
        one = self.key(['MOD-A'])
        many = self.key([f'MOD-{index}' for index in range(500)])
        self.assertLess(len(many), 160)
        self.assertEqual(len(many) - len(one), len('500') - len('1'))

    def test_distinct_flags_produce_distinct_cached_values(self):
        # End-to-end through the cache itself, not just the key function.
        views.invalidate_curriculum_cache()
        self.addCleanup(views.invalidate_curriculum_cache)
        lite = views.cached_curriculum_value(self.key(['MOD-A']), lambda: 'lite')
        full = views.cached_curriculum_value(self.key(['MOD-A'], include_extra=True), lambda: 'full')
        self.assertEqual((lite, full), ('lite', 'full'))

    def test_equivalent_requests_hit_one_cached_build(self):
        views.invalidate_curriculum_cache()
        self.addCleanup(views.invalidate_curriculum_cache)
        builds = []

        def factory():
            builds.append(1)
            return 'payloads'

        views.cached_curriculum_value(self.key(['MOD-A', 'MOD-B']), factory)
        views.cached_curriculum_value(self.key(['MOD-B', 'MOD-A']), factory)
        self.assertEqual(len(builds), 1)


class CurriculumReferenceEtagTests(SimpleTestCase):
    """Covers the ETag/304 behaviour on the reference-data helper."""

    def _request(self, if_none_match=None):
        headers = {'If-None-Match': if_none_match} if if_none_match else {}
        return RequestFactory().get('/curriculum/standards/', headers=headers)

    def test_first_response_carries_a_validator_and_a_body(self):
        response = views.reference_json_response(self._request(), {'results': [1, 2, 3]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['ETag'])
        self.assertEqual(json.loads(response.content)['results'], [1, 2, 3])

    def test_matching_validator_returns_304_with_no_body(self):
        payload = {'results': [1, 2, 3]}
        etag = views.reference_json_response(self._request(), payload)['ETag']
        revalidated = views.reference_json_response(self._request(etag), payload)
        self.assertEqual(revalidated.status_code, 304)
        self.assertEqual(revalidated.content, b'')
        self.assertEqual(revalidated['ETag'], etag)

    def test_changed_content_changes_the_validator_and_returns_200(self):
        etag = views.reference_json_response(self._request(), {'results': [1]})['ETag']
        changed = views.reference_json_response(self._request(etag), {'results': [1, 2]})
        # A stale validator must never yield 304, otherwise a standards import would
        # stay invisible to every client that already holds the old ETag.
        self.assertEqual(changed.status_code, 200)
        self.assertNotEqual(changed['ETag'], etag)

    def test_reference_responses_are_never_publicly_cacheable(self):
        response = views.reference_json_response(self._request(), {'results': []})
        # These endpoints sit behind authentication, so a shared cache must not keep
        # them even though the content is user-independent.
        self.assertIn('private', response['Cache-Control'])
        self.assertNotIn('public', response['Cache-Control'])

    def test_handles_a_multi_value_if_none_match_header(self):
        payload = {'results': ['x']}
        etag = views.reference_json_response(self._request(), payload)['ETag']
        response = views.reference_json_response(self._request(f'"other", {etag}'), payload)
        self.assertEqual(response.status_code, 304)


class ProgrammeLearnerKsbProgressTests(TestCase):
    """The programme card's KSB bar reports learner achievement, not design mapping.

    ``ksbMapped``/``ksbTotal`` answer "how much of the standard does the design
    touch". ``learnerKsbProgressPercentage`` answers "how much have the learners
    actually evidenced". They are separate fields because they are separate
    questions, and the card must never silently substitute one for the other.
    """

    def setUp(self):
        views.reset_schema_ready_flags()
        views.invalidate_curriculum_cache()
        # Borrow the persistence suite's schema fixtures so the helper runs its
        # real query path instead of bailing out on a missing table — a test that
        # only exercises the except branch would pass no matter what the maths did.
        CurriculumPersistenceTests.ensure_programmes_table(self)
        CurriculumPersistenceTests.ensure_ksb_profiles_table(self)
        views.ensure_module_authoring_tables()

    PROGRESS_FIELDS = (
        'learnerKsbProgressPercentage',
        'learnerKsbConsumedWeight',
        'learnerKsbExpectedWeight',
        'learnerKsbLearnerCount',
        'learnerKsbCodesStarted',
        'learnerKsbCodesComplete',
        'learnerKsbCodesTotal',
    )

    def test_missing_programme_reports_zero_rather_than_raising(self):
        result = views.programme_learner_ksb_progress('PROG-DOES-NOT-EXIST', [])
        for field in self.PROGRESS_FIELDS:
            self.assertIn(field, result)
        self.assertEqual(result['learnerKsbProgressPercentage'], 0)
        self.assertEqual(result['learnerKsbLearnerCount'], 0)

    def test_result_always_carries_every_field_so_the_card_reads_one_shape(self):
        # A card that receives `undefined` for the percentage renders an empty
        # bar that is indistinguishable from real 0% progress, so the contract is
        # that the key is always present.
        result = views.programme_learner_ksb_progress('', None)
        self.assertEqual(set(self.PROGRESS_FIELDS) - set(result), set())

    def test_percentage_never_exceeds_one_hundred(self):
        # Guards the cap: an over-weighted activity must not push a programme
        # past 100%, which would make the bar overflow its track.
        result = views.programme_learner_ksb_progress('PROG-DOES-NOT-EXIST', [])
        self.assertLessEqual(result['learnerKsbProgressPercentage'], 100)
        self.assertGreaterEqual(result['learnerKsbProgressPercentage'], 0)


class ProgrammeConfigSelectionTests(SimpleTestCase):
    def test_display_config_prefers_latest_active_duplicate_when_timestamps_are_strings(self):
        old_archived = {
            'programme_id': 'PROG-OLD',
            'name': 'User Flow',
            'is_archived': True,
            'updated_at': '2026-08-17T16:19:19.470000+00:00',
        }
        latest_active = {
            'programme_id': 'PROG-NEW',
            'name': 'User Flow',
            'is_archived': False,
            'updated_at': '2026-08-17T16:42:41.350000+00:00',
        }

        selected = views.unique_program_configs_for_display([old_archived, latest_active])

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['programme_id'], 'PROG-NEW')
        self.assertEqual(
            views.preferred_program_config_by_name([old_archived, latest_active], 'User Flow')['programme_id'],
            'PROG-NEW',
        )

    def test_is_archived_false_is_the_programme_display_source_of_truth(self):
        config = {
            'programme_id': 'PROG-SHOW',
            'name': 'User Flow',
            'is_archived': False,
            'status': 'archived',
            'deleted_at': '2026-08-17T16:19:19.470000+00:00',
        }

        self.assertFalse(views.is_archived_program_config(config))
        self.assertEqual(views.programme_config_status(config), 'active')

    def test_build_programmes_returns_active_unarchived_programme_rows(self):
        configs = [
            {
                'programme_id': 'PROG-FOUDA',
                'name': 'Fouda-Programme',
                'is_active': True,
                'is_archived': False,
                'updated_at': '2026-08-17T15:42:32.000000+00:00',
            },
            {
                'programme_id': 'PROG-USER-FLOW',
                'name': 'User Flow',
                'is_active': True,
                'is_archived': False,
                'updated_at': '2026-08-17T16:42:41.350000+00:00',
            },
            {
                'programme_id': 'PROG-MBA',
                'name': 'MBA',
                'is_active': False,
                'is_archived': True,
                'updated_at': '2026-08-17T17:42:41.350000+00:00',
            },
            {
                'programme_id': 'PROG-DISABLED',
                'name': 'Disabled Programme',
                'is_active': False,
                'is_archived': False,
                'updated_at': '2026-08-17T18:42:41.350000+00:00',
            },
        ]

        with patch('curriculum_api.views.free_programme_fetch_all', return_value=[]), \
             patch('curriculum_api.views.cohort_authoring_detail_rows', return_value=[]), \
             patch('curriculum_api.views.active_learner_programme_counts', return_value=({}, {})), \
             patch('curriculum_api.views.programme_learner_ksb_progress', return_value={}), \
             patch('curriculum_api.views.programme_component_ksb_mapping_count', return_value=0):
            programmes = views.build_programmes([], configs, [], include_config_only=False)

        self.assertEqual([programme['sourceId'] for programme in programmes], ['PROG-FOUDA', 'PROG-USER-FLOW'])
        self.assertEqual([programme['isArchived'] for programme in programmes], [False, False])
        self.assertEqual([programme['isActive'] for programme in programmes], [True, True])

    def test_active_duplicate_names_are_not_collapsed_for_display(self):
        configs = [
            {'programme_id': 'PROG-A', 'name': 'User Flow', 'is_archived': False},
            {'programme_id': 'PROG-B', 'name': 'User Flow', 'is_archived': False},
        ]

        selected = views.unique_program_configs_for_display(configs)

        self.assertEqual([config['programme_id'] for config in selected], ['PROG-A', 'PROG-B'])


class ProgrammeIntegrityRegressionTests(TestCase):
    """Regression cover for the Curriculum programme integrity audit.

    Each test here pins a bug that allowed programme rows or their children to
    be corrupted: stale identity aliases, placeholder programme ids, global
    free-course deletes, and unguarded hard deletes.
    """

    def setUp(self):
        views.reset_schema_ready_flags()
        views.invalidate_curriculum_cache()
        self.client = Client()
        self._create_programmes_table()
        views.ensure_module_authoring_tables()
        views.ensure_free_programme_tables()
        self._clear()

    def _create_programmes_table(self):
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('create schema if not exists curriculum')
                table = 'curriculum.programmes'
            else:
                table = 'programmes'
            cursor.execute(
                f"""
                create table if not exists {table} (
                    programme_id varchar(128) primary key,
                    name varchar(255),
                    level varchar(64),
                    color varchar(32),
                    description text,
                    is_archived boolean,
                    ksb_profile_source_id varchar(128),
                    created_at timestamp,
                    updated_at timestamp
                )
                """
            )

    def _clear(self):
        tables = [
            views.FREE_PROGRAMME_COMPONENTS_TABLE,
            views.FREE_PROGRAMME_MODULES_TABLE,
            views.FREE_COURSES_TABLE,
            views.AUTHORING_MODULES_TABLE,
            views.GROUPS_TABLE,
            views.COHORT_AUTHORING_DETAILS_TABLE,
            'programmes',
        ]
        with connection.cursor() as cursor:
            for table in tables:
                try:
                    cursor.execute(f'delete from {views.authoring_table_name(table)}')
                except Exception:
                    pass

    def _insert_programme(self, programme_id, name):
        views.insert_row('programmes', {
            'programme_id': programme_id,
            'name': name,
            'color': '#6941c6',
            'is_archived': False,
            'created_at': views.datetime.utcnow(),
            'updated_at': views.datetime.utcnow(),
        })

    def _programme_rows(self):
        return views.fetch_all(f'select * from {views.table_name("programmes")}')

    # ---------------- programme identity ----------------

    def test_identity_reads_the_real_programme_id_column(self):
        self.assertEqual(
            views.programme_config_identity({'programme_id': 'PROG-REAL'}),
            'PROG-REAL',
        )

    def test_identity_falls_back_to_legacy_aliases(self):
        self.assertEqual(views.programme_config_identity({'program_id': 'PROG-OLD'}), 'PROG-OLD')
        self.assertEqual(views.programme_config_identity({}), '')

    def test_existing_programme_is_updated_not_duplicated(self):
        self._insert_programme('PROG-KEEP', 'Keeper')

        result = views.ensure_programme_config_for_authoring('Keeper', 'PROG-KEEP')

        self.assertEqual(result['sourceId'], 'PROG-KEEP')
        rows = self._programme_rows()
        self.assertEqual(len(rows), 1, f'expected no duplicate row, got {rows}')

    def test_matching_by_id_survives_when_only_programme_id_is_set(self):
        # The original bug: matching read program_id/id only, so a row keyed on
        # programme_id never matched and a duplicate was inserted instead.
        self._insert_programme('PROG-ALIAS', 'Alias Programme')

        views.ensure_programme_config_for_authoring('Renamed Alias', 'PROG-ALIAS')

        rows = self._programme_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(views.clean_str(rows[0].get('name')), 'Renamed Alias')

    # ---------------- placeholder ids ----------------

    def test_placeholder_programme_ids_are_recognised(self):
        for value in ('programme-local', 'PROGRAMME-LOCAL', 'local', 'undefined', '', 'local-MOD-1'):
            self.assertTrue(views.is_placeholder_programme_id(value), value)
        self.assertFalse(views.is_placeholder_programme_id('PROG-20260101000000000000'))

    def test_placeholder_id_never_becomes_a_persisted_programme(self):
        views.ensure_programme_config_for_authoring('Real Programme Name', 'programme-local')

        ids = [views.programme_config_identity(row) for row in self._programme_rows()]
        self.assertNotIn('programme-local', ids)
        for value in ids:
            self.assertTrue(value.startswith('PROG-'), f'unexpected programme id {value!r}')

    def test_placeholder_id_attaches_to_existing_programme_by_name(self):
        self._insert_programme('PROG-EXIST', 'Shared Name')

        result = views.ensure_programme_config_for_authoring('Shared Name', 'programme-local')

        self.assertEqual(result['sourceId'], 'PROG-EXIST')
        self.assertEqual(len(self._programme_rows()), 1)

    def test_programme_name_is_never_used_as_an_identifier(self):
        views.ensure_programme_config_for_authoring('Fouda-Programme', '')

        ids = [views.programme_config_identity(row) for row in self._programme_rows()]
        self.assertNotIn('Fouda-Programme', ids)
        self.assertTrue(all(value.startswith('PROG-') for value in ids), ids)

    # ---------------- free-course deletion scoping ----------------

    def _seed_free_course(self, programme_id, course_id, week_id):
        views.free_programme_upsert(views.FREE_COURSES_TABLE, ['id'], {
            'id': course_id,
            'course_name': f'Course {course_id}',
            'display_order': 1,
        })
        views.free_programme_upsert(views.FREE_PROGRAMME_MODULES_TABLE, ['id'], {
            'id': week_id,
            'course_id': course_id,
            'course_name': f'Course {course_id}',
            'week_number': 1,
            'display_order': 1,
        })
        views.free_programme_upsert(views.FREE_PROGRAMME_COMPONENTS_TABLE, ['id'], {
            'id': f'COMP-{week_id}',
            'free_module_id': week_id,
            'programme_id': programme_id,
            'type': 'reading',
            'title': 'Component',
            'display_order': 1,
        })

    def _free_ids(self, table):
        return {
            views.clean_str(row.get('id'))
            for row in views.free_programme_fetch_all(table)
        }

    def test_deleting_one_programmes_free_courses_leaves_the_other_intact(self):
        self._insert_programme('PROG-AAA', 'Programme A')
        self._insert_programme('PROG-BBB', 'Programme B')
        self._seed_free_course('PROG-AAA', 'COURSE-A', 'WEEK-A')
        self._seed_free_course('PROG-BBB', 'COURSE-B', 'WEEK-B')

        views.delete_free_programme_data('PROG-AAA')

        self.assertNotIn('COURSE-A', self._free_ids(views.FREE_COURSES_TABLE))
        self.assertNotIn('WEEK-A', self._free_ids(views.FREE_PROGRAMME_MODULES_TABLE))
        # Programme B must be completely untouched.
        self.assertIn('COURSE-B', self._free_ids(views.FREE_COURSES_TABLE))
        self.assertIn('WEEK-B', self._free_ids(views.FREE_PROGRAMME_MODULES_TABLE))
        remaining = views.free_programme_fetch_all(views.FREE_PROGRAMME_COMPONENTS_TABLE)
        self.assertEqual(
            {views.clean_str(row.get('programme_id')) for row in remaining},
            {'PROG-BBB'},
        )

    def test_free_course_delete_without_a_programme_id_deletes_nothing(self):
        self._insert_programme('PROG-AAA', 'Programme A')
        self._seed_free_course('PROG-AAA', 'COURSE-A', 'WEEK-A')

        weeks, courses = views.delete_free_programme_data('')

        self.assertEqual((weeks, courses), (set(), set()))
        self.assertIn('COURSE-A', self._free_ids(views.FREE_COURSES_TABLE))
        self.assertIn('WEEK-A', self._free_ids(views.FREE_PROGRAMME_MODULES_TABLE))

    def test_no_unconditional_delete_predicates_remain_in_curriculum_views(self):
        import inspect
        source = inspect.getsource(views)
        self.assertNotIn("free_programme_delete(FREE_PROGRAMME_MODULES_TABLE, '1 = 1')", source)
        self.assertNotIn("free_programme_delete(FREE_COURSES_TABLE, '1 = 1')", source)

    # ---------------- delete / archive safety ----------------

    def _seed_cohort(self, programme_id, cohort_id):
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': cohort_id,
            'cohort_name': 'Cohort',
            'programme_id': programme_id,
            'programme_name': 'Programme A',
        })

    def test_programme_with_dependents_is_archived_not_deleted(self):
        self._insert_programme('PROG-DEP', 'Programme A')
        self._seed_cohort('PROG-DEP', 'COHORT-DEP')

        response = self.client.delete('/curriculum_api/curriculum/programmes/PROG-DEP/')

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body.get('archived'))
        self.assertFalse(body.get('permanent'))
        # The programme row and its cohort must both survive.
        rows = self._programme_rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(views.truthy(rows[0].get('is_archived')))
        cohorts = views.authoring_fetch_all(views.COHORT_AUTHORING_DETAILS_TABLE)
        self.assertEqual(len(cohorts), 1)

    def test_archived_programme_can_be_restored_with_its_children(self):
        """Restore is the inverse of archive: the row comes back, so do its children."""
        self._insert_programme('PROG-RESTORE', 'Programme A')
        self._seed_cohort('PROG-RESTORE', 'COHORT-RESTORE')
        self.client.delete('/curriculum_api/curriculum/programmes/PROG-RESTORE/')

        response = self.client.post('/curriculum_api/curriculum/programmes/PROG-RESTORE/restore/')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json().get('restored'))
        rows = self._programme_rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(views.truthy(rows[0].get('is_archived')))
        self.assertEqual(views.programme_config_status(rows[0]), 'active')
        cohorts = views.authoring_fetch_all(views.COHORT_AUTHORING_DETAILS_TABLE)
        self.assertEqual(len(cohorts), 1)
        self.assertIsNone(cohorts[0].get('deleted_at'))
        self.assertFalse(views.truthy(cohorts[0].get('is_programme_deleted')))

    def test_restoring_a_live_programme_is_rejected(self):
        self._insert_programme('PROG-LIVE', 'Programme A')

        response = self.client.post('/curriculum_api/curriculum/programmes/PROG-LIVE/restore/')

        self.assertEqual(response.status_code, 409, response.content)

    def test_restore_of_unknown_programme_returns_404(self):
        response = self.client.post('/curriculum_api/curriculum/programmes/PROG-NOPE/restore/')
        self.assertEqual(response.status_code, 404)

    def test_restore_leaves_independently_archived_children_alone(self):
        """A cohort archived on its own is not revived by restoring the programme.

        The cascade stamps deleted_via_parent; a standalone archive does not, so
        the two are told apart and restore only undoes its own cascade.
        """
        self._insert_programme('PROG-MIXED', 'Programme A')
        self._seed_cohort('PROG-MIXED', 'COHORT-OWN')
        # Archive the cohort by itself first - no parent marker.
        views.authoring_soft_delete(
            views.COHORT_AUTHORING_DETAILS_TABLE, 'cohort_id = %s', ['COHORT-OWN'],
        )
        self.client.delete('/curriculum_api/curriculum/programmes/PROG-MIXED/')

        response = self.client.post('/curriculum_api/curriculum/programmes/PROG-MIXED/restore/')

        self.assertEqual(response.status_code, 200, response.content)
        cohorts = views.authoring_fetch_all(views.COHORT_AUTHORING_DETAILS_TABLE)
        self.assertEqual(len(cohorts), 1)
        self.assertIsNotNone(cohorts[0].get('deleted_at'))

    def test_restore_rejects_non_post(self):
        self._insert_programme('PROG-METHOD', 'Programme A')
        response = self.client.get('/curriculum_api/curriculum/programmes/PROG-METHOD/restore/')
        self.assertEqual(response.status_code, 405)

    def test_dependency_counts_report_children(self):
        self._insert_programme('PROG-DEP2', 'Programme A')
        self._seed_cohort('PROG-DEP2', 'COHORT-X')

        counts = views.programme_dependency_counts('PROG-DEP2', {'programme_id': 'PROG-DEP2'})

        self.assertEqual(counts.get(views.COHORT_AUTHORING_DETAILS_TABLE), 1)

    def test_unknown_programme_delete_returns_404(self):
        response = self.client.delete('/curriculum_api/curriculum/programmes/PROG-DOES-NOT-EXIST/')
        self.assertEqual(response.status_code, 404)

    # ---------------- archive visibility ----------------

    def test_is_archived_is_the_single_archive_signal(self):
        self.assertTrue(views.is_archived_program_config({'is_archived': True}))
        self.assertFalse(views.is_archived_program_config({'is_archived': False}))
        # A legacy is_active=false row is NOT treated as archived any more.
        self.assertFalse(views.is_archived_program_config({'is_active': False, 'is_archived': False}))
        self.assertEqual(views.programme_config_status({'is_active': False, 'is_archived': False}), 'active')

    # ---------------- module authoring ----------------

    def test_saving_a_module_without_a_programme_creates_no_junk_programme(self):
        self.assertIsNone(views.ensure_programme_config_for_authoring('Unassigned programme', 'programme-local'))
        self.assertEqual(self._programme_rows(), [])


class CohortPracticalEndDateRuleTests(SimpleTestCase):
    """One rule for the practical end date, shared by the preview and every write.

    The editor's preview and the three write paths (the programme tree save, the
    cohort create and the cohort PATCH) used to reach the date by different code.
    A cohort created through the API therefore landed on a different date from
    the same cohort created in the UI -- same inputs, two answers. They now all
    call ``cohort_practical_end_date``, and these tests hold that rule still.

    The rule itself is the contracted one: start plus duration, less a day. A
    cohort's dates are signed dates, so the holidays ticked on it do not move
    them -- what a holiday moves is the delivery it clashes with, which is the
    module session plan (see AuthoringModuleSessionHolidayTests). The selection
    is still passed in here by every caller, so these tests are what stop it
    quietly acquiring an effect on the contract date again.
    """

    HOLIDAY_ROWS = [
        {'id': 'HOL-1', 'label': 'Oct 27', 'start_date': '2027-09-26', 'end_date': '2027-10-02'},
        {'id': 'HOL-2', 'label': 'Summer 27', 'start_date': '2027-07-25', 'end_date': '2027-08-07'},
        # Past the base end date of a 24 month cohort starting 2026-02-01.
        {'id': 'HOL-3', 'label': 'Later', 'start_date': '2028-05-01', 'end_date': '2028-05-07'},
    ]

    def test_a_ticked_holiday_does_not_move_the_contracted_date(self):
        # HOL-1 is a 7 day closure inside the period. The cohort still ends on
        # the day its duration says: the closure moves the sessions it clashes
        # with, not the contract.
        self.assertEqual(
            views.format_date(views.cohort_practical_end_date(
                '2026-02-01', 24, ['HOL-1'], self.HOLIDAY_ROWS,
            )),
            '2028-01-31',
        )

    def test_the_rule_matches_the_duration_alone_when_no_holiday_applies(self):
        self.assertEqual(
            views.format_date(views.cohort_practical_end_date(
                '2026-02-01', 24, ['HOL-3'], self.HOLIDAY_ROWS,
            )),
            '2028-01-31',
        )

    def test_an_empty_selection_leaves_the_duration_rule_alone(self):
        # Nothing ticked means no holiday applies, so the date is start plus
        # duration, less a day, and nothing else. An end date only moves because
        # someone picked the holiday that moves it -- the editor's picker and
        # build_sessions both hold the same rule, so the dates and the sessions
        # still agree.
        self.assertEqual(
            views.format_date(views.cohort_practical_end_date(
                '2026-02-01', 24, [], self.HOLIDAY_ROWS,
            )),
            '2028-01-31',
        )

    def test_no_number_of_picks_moves_the_contracted_date(self):
        # Two closures inside the period, 21 days between them, and the date is
        # still the one the duration gives. The editor says so in place of the
        # amber extension hint it used to render.
        self.assertEqual(
            views.format_date(views.cohort_practical_end_date(
                '2026-02-01', 24, ['HOL-1', 'HOL-2'], self.HOLIDAY_ROWS,
            )),
            '2028-01-31',
        )

    def test_the_rule_is_idempotent(self):
        # Asking again with the same inputs answers the same date. Nothing about
        # a cohort's own dates accumulates, so reopening the drawer cannot creep
        # the date outwards.
        first = views.format_date(views.cohort_practical_end_date(
            '2026-02-01', 24, ['HOL-1'], self.HOLIDAY_ROWS,
        ))
        second = views.format_date(views.cohort_practical_end_date(
            '2026-02-01', 24, ['HOL-1'], self.HOLIDAY_ROWS,
        ))
        self.assertEqual(first, second)
        self.assertEqual(first, '2028-01-31')

    def test_no_start_date_or_duration_has_no_rule_to_apply(self):
        self.assertIsNone(views.cohort_practical_end_date('', 24, ['HOL-1'], self.HOLIDAY_ROWS))
        self.assertIsNone(views.cohort_practical_end_date('2026-02-01', 0, ['HOL-1'], self.HOLIDAY_ROWS))

    def test_the_rule_agrees_with_the_preview_endpoint(self):
        # The guarantee that matters: what an editor is shown is what a save stores.
        request = RequestFactory().post(
            '/curriculum_api/curriculum/preview/cohort-end-date/',
            data=json.dumps({
                'startDate': '2026-02-01',
                'durationMonths': 24,
                'epaMonths': 5,
                'holidays': [{'label': 'Oct 27', 'startDate': '2027-09-26', 'endDate': '2027-10-02'}],
            }),
            content_type='application/json',
        )
        previewed = json.loads(views.curriculum_preview_cohort_end_date(request).content)
        stored = views.format_date(views.cohort_practical_end_date(
            '2026-02-01', 24, ['HOL-1'], self.HOLIDAY_ROWS,
        ))
        self.assertEqual(previewed['practicalEndDate'], stored)


class CohortAppliedHolidaysTests(SimpleTestCase):
    """Which holidays a cohort's dates and sessions are both measured against.

    A cohort's holidays are its dates: every England bank holiday inside the
    period, and nothing else. There is nothing to tick, so these tests are what
    stop a stored selection -- ids into the authored holiday table curriculum no
    longer reads -- quietly acquiring an effect again.
    """

    HOLIDAY_ROWS = [
        {'id': 'HOL-1', 'title': 'In period', 'holiday_date': '2027-09-26'},
        {'id': 'HOL-2', 'title': 'Out of period', 'holiday_date': '2028-05-01'},
        {'id': 'HOL-3', 'title': 'On the last day', 'holiday_date': '2028-01-31'},
        {'id': 'HOL-4', 'title': 'The day after', 'holiday_date': '2028-02-01'},
    ]

    def test_every_holiday_in_the_period_applies(self):
        applied = views.cohort_applied_holidays(
            None, self.HOLIDAY_ROWS, '2026-02-01', '2028-01-31',
        )
        self.assertEqual([item['id'] for item in applied], ['HOL-1', 'HOL-3'])

    def test_a_stored_selection_cannot_narrow_the_period(self):
        # The ids are numeric keys into the authored table nobody reads any
        # more. A cohort carrying them still gets its whole period, not one
        # holiday, and not none.
        applied = views.cohort_applied_holidays(
            ['1079', '1090'], self.HOLIDAY_ROWS, '2026-02-01', '2028-01-31',
        )
        self.assertEqual([item['id'] for item in applied], ['HOL-1', 'HOL-3'])

    def test_an_empty_selection_still_applies_the_period(self):
        # The old rule was that nothing ticked meant nothing skipped, which is
        # how a cohort came to run straight through Christmas Day. Its dates
        # answer now, so an untouched cohort skips its bank holidays.
        applied = views.cohort_applied_holidays(
            [], self.HOLIDAY_ROWS, '2026-02-01', '2028-01-31',
        )
        self.assertEqual([item['id'] for item in applied], ['HOL-1', 'HOL-3'])

    def test_the_period_is_inclusive_at_both_ends(self):
        # HOL-3 lands on the last day and is in; HOL-4 is the next day and is out.
        applied = views.cohort_applied_holidays(
            None, self.HOLIDAY_ROWS, '2027-09-26', '2028-01-31',
        )
        self.assertEqual([item['id'] for item in applied], ['HOL-1', 'HOL-3'])

    def test_a_holiday_outside_the_period_is_dropped(self):
        # It cannot take delivery days out of a period it does not touch.
        applied = views.cohort_applied_holidays(
            None, self.HOLIDAY_ROWS, '2026-02-01', '2027-01-31',
        )
        self.assertEqual(applied, [])

    def test_the_holidays_come_back_in_date_order(self):
        applied = views.cohort_applied_holidays(
            None, list(reversed(self.HOLIDAY_ROWS)), '2026-02-01', '2028-05-31',
        )
        self.assertEqual(
            [item['startDate'] for item in applied],
            ['2027-09-26', '2028-01-31', '2028-02-01', '2028-05-01'],
        )

    def test_they_do_not_move_the_cohort_contract_date(self):
        # The period decides which holidays apply; the holidays never decide the
        # period. A cohort's dates are signed dates.
        self.assertEqual(
            views.format_date(views.cohort_practical_end_date(
                '2026-02-01', 24, None, self.HOLIDAY_ROWS,
            )),
            '2028-01-31',
        )

    def test_details_report_the_period_as_both_selected_and_in_range(self):
        details = views.cohort_holiday_details(
            self.HOLIDAY_ROWS, ['1079'], '2026-02-01', '2028-01-31',
        )
        self.assertEqual(details['holidayIds'], ['HOL-1', 'HOL-3'])
        self.assertEqual(details['selectedHolidays'], details['holidaysInRange'])
        self.assertEqual(details['summary'], {'global': 4, 'inRange': 2, 'selected': 2})

    def test_a_period_with_no_end_date_has_no_holidays(self):
        # Better than every holiday ever published: a cohort half way through
        # being typed has no period yet, so it has nothing to skip.
        self.assertEqual(
            views.cohort_applied_holidays(None, self.HOLIDAY_ROWS, '2026-02-01', ''),
            [],
        )


class CohortHolidayExclusionTests(SimpleTestCase):
    """A cohort can untick a holiday in its own period; scheduling honours it.

    ``excluded_ids`` is a deny-list, not the selection itself: a stale or
    unmatched id has nothing to remove, so it can never narrow the period the
    way the old ``holiday_ids`` selection used to (see
    ``CohortAppliedHolidaysTests``). Only an id that actually names a holiday
    inside the period has any effect.
    """

    HOLIDAY_ROWS = [
        {'id': 'HOL-1', 'title': 'In period', 'holiday_date': '2027-09-26'},
        {'id': 'HOL-2', 'title': 'Out of period', 'holiday_date': '2028-05-01'},
        {'id': 'HOL-3', 'title': 'On the last day', 'holiday_date': '2028-01-31'},
    ]

    def test_an_excluded_holiday_is_dropped_from_selected_but_not_in_range(self):
        details = views.cohort_holiday_details(
            self.HOLIDAY_ROWS, ['HOL-1'], '2026-02-01', '2028-01-31',
        )
        self.assertEqual(details['holidayIds'], ['HOL-3'])
        self.assertEqual([item['id'] for item in details['selectedHolidays']], ['HOL-3'])
        self.assertEqual([item['id'] for item in details['holidaysInRange']], ['HOL-1', 'HOL-3'])
        self.assertEqual(details['excludedHolidayIds'], ['HOL-1'])
        self.assertEqual(details['summary'], {'global': 3, 'inRange': 2, 'selected': 1})

    def test_an_id_outside_the_period_excludes_nothing(self):
        # A stale exclusion -- the holiday moved out of range, or the id never
        # matched one to begin with -- simply has nothing to remove.
        details = views.cohort_holiday_details(
            self.HOLIDAY_ROWS, ['HOL-2', 'not-a-real-id'], '2026-02-01', '2028-01-31',
        )
        self.assertEqual(details['holidayIds'], ['HOL-1', 'HOL-3'])
        self.assertEqual(details['excludedHolidayIds'], [])

    def test_no_exclusions_means_every_holiday_in_the_period_applies(self):
        # Both an empty list and no list at all mean nothing is excluded --
        # there is no absent-vs-empty ambiguity for a deny-list.
        with_none = views.cohort_holiday_details(self.HOLIDAY_ROWS, None, '2026-02-01', '2028-01-31')
        with_empty = views.cohort_holiday_details(self.HOLIDAY_ROWS, [], '2026-02-01', '2028-01-31')
        self.assertEqual(with_none['holidayIds'], ['HOL-1', 'HOL-3'])
        self.assertEqual(with_empty['holidayIds'], ['HOL-1', 'HOL-3'])

    def test_scheduling_skips_every_holiday_from_the_start_except_the_excluded_one(self):
        # scheduling_holidays_from is open-ended past the cohort's own period
        # (AuthoringModuleSessionHolidayTests covers why); excluding a holiday
        # inside the period must not touch the ones after it.
        rows = [
            {'id': 'HOL-1', 'title': 'Excluded', 'holiday_date': '2027-09-26'},
            {'id': 'HOL-2', 'title': 'After the period', 'holiday_date': '2028-05-01'},
        ]
        applied = views.scheduling_holidays_from(rows, '2027-01-01', ['HOL-1'])
        self.assertEqual([item['id'] for item in applied], ['HOL-2'])

    def test_cohort_selected_holidays_by_id_honours_the_cohorts_own_exclusions(self):
        cohort = {
            'id': 'COHORT-1', 'startDate': '2027-01-01',
            'excludedHolidayIds': ['HOL-1'],
        }
        rows = [
            {'id': 'HOL-1', 'title': 'Excluded', 'holiday_date': '2027-09-26'},
            {'id': 'HOL-2', 'title': 'Still applies', 'holiday_date': '2028-05-01'},
        ]
        holidays = views.cohort_selected_holidays_by_id([cohort], rows)['COHORT-1']
        self.assertEqual([item['id'] for item in holidays], ['HOL-2'])


class AuthoringModuleSessionHolidayTests(SimpleTestCase):
    """The session list a module publishes skips its cohort's bank holidays.

    These are the dates every downstream screen reads: the calendar, the module
    workspace's schedule, and the Teams Meetings page that sends them to the
    Microsoft calendar. They used to be generated from the delivery day alone,
    so a module whose cohort ran through Boxing Day still listed a session on it
    -- and the Teams calendar was then built on that date, while the module
    form's own preview (which asks the backend with the holidays attached)
    showed the shifted plan. One module, two answers.

    A cohort's holidays are the England bank holidays inside its own dates, so
    the map these tests build is keyed off the cohort's period, not off any
    stored selection.

    The dates themselves are now the module's own authored live sessions -- one
    component per session, each carrying its date -- stubbed in ``setUp``. What
    is under test here is what a ticked holiday does to them, which is: name
    itself on the session whose day it falls on, and nothing else.
    """

    #: Real England bank holidays around the 2026 Christmas period. 26 December
    #: 2026 is a Saturday, so Boxing Day moves to Monday the 28th -- the only one
    #: of these that lands on the module's delivery day.
    HOLIDAY_ROWS = [
        {'id': 'england-and-wales:2026-12-25', 'title': 'Christmas Day', 'holiday_date': '2026-12-25'},
        {'id': 'england-and-wales:2026-12-28', 'title': 'Boxing Day', 'holiday_date': '2026-12-28', 'notes': 'Substitute day'},
        {'id': 'england-and-wales:2027-01-01', 'title': 'New Year’s Day', 'holiday_date': '2027-01-01'},
        # A Monday, but well after the cohort period used below.
        {'id': 'england-and-wales:2027-03-29', 'title': 'Easter Monday', 'holiday_date': '2027-03-29'},
    ]

    MODULE = {
        'module_catalogue_id': 'MOD-1',
        'title': 'Fouda',
        'status': 'published',
        'cohort_id': 'COHORT-1',
        'cohort_name': 'C1',
        'group_id': 'GROUP-1',
        'group_name': 'G1-mon',
        'programme_name': 'MSN',
        'sessions_number': 6,
        'start_date': '2026-12-21',
        'session_week_day': 'Monday',
        'session_start_time': '09:00',
        'session_end_time': '11:00',
    }

    #: The six Mondays this module delivers on, authored as six live sessions --
    #: one per week, each carrying its own date. They are what the module
    #: delivers now: `sessions_number` generates nothing, so a module with no
    #: authored live sessions publishes no sessions at all and these tests would
    #: have nothing to assert about. See tests_authored_live_sessions.py.
    SESSION_DATES = ['2026-12-21', '2026-12-28', '2027-01-04', '2027-01-11', '2027-01-18', '2027-01-25']

    def setUp(self):
        self._real_fetch_all = views.authoring_fetch_all
        views.authoring_fetch_all = self._fetch_all
        self.addCleanup(setattr, views, 'authoring_fetch_all', self._real_fetch_all)

    def _fetch_all(self, table, where_sql='', params=None, order_sql='', **kwargs):
        if table == views.AUTHORING_WEEKS_TABLE:
            return [
                {'id': f'WEEK-{index + 1}', 'module_catalogue_id': 'MOD-1',
                 'display_order': index + 1, 'week_number': index + 1, 'title': f'W{index + 1}'}
                for index in range(len(self.SESSION_DATES))
            ]
        if table == views.AUTHORING_COMPONENTS_TABLE:
            return [
                {'id': f'COMP-{index + 1}', 'week_id': f'WEEK-{index + 1}',
                 'module_catalogue_id': 'MOD-1', 'type': 'live_session',
                 'title': f'Live {index + 1}', 'display_order': 1,
                 'settings_json': {'sessionDate': date, 'sessionTime': '09:00', 'durationMinutes': 120},
                 'live_sessions_link': ''}
                for index, date in enumerate(self.SESSION_DATES)
            ]
        return []

    def holidays_by_cohort(self, start_date='2026-12-01', end_date='2027-02-28'):
        return views.cohort_selected_holidays_by_id(
            [{'id': 'COHORT-1', 'startDate': start_date, 'endDate': end_date}],
            self.HOLIDAY_ROWS,
        )

    def test_sessions_run_on_their_own_dates_holidays_or_not(self):
        sessions = views.build_sessions_from_authoring_modules(
            [self.MODULE], self.holidays_by_cohort(),
        )
        # A ticked holiday moves nothing: every session runs on its own weekly
        # Monday, Boxing Day included. Christmas Day and New Year's Day are both
        # Fridays and touch nothing here regardless, because nothing delivers on
        # a Friday.
        self.assertEqual(
            [session['date'] for session in sessions],
            ['2026-12-21', '2026-12-28', '2027-01-04', '2027-01-11', '2027-01-18', '2027-01-25'],
        )

    def test_the_flagged_session_names_the_holiday_on_its_own_day(self):
        sessions = views.build_sessions_from_authoring_modules(
            [self.MODULE], self.holidays_by_cohort(),
        )
        # The reader has to be able to see the clash, which is what the Teams
        # page and the module form both render from this field.
        self.assertEqual(sessions[1]['date'], '2026-12-28')
        self.assertEqual(sessions[1]['skippedHolidays'], ['2026-12-28'])
        self.assertEqual(
            [session['skippedHolidays'] for session in sessions if session is not sessions[1]],
            [[], [], [], [], []],
        )

    def test_a_plan_that_runs_past_the_cohort_end_still_flags(self):
        """The reason the scheduling set has no end bound.

        Nothing a plan runs over moves it any more, so a plan long enough
        finishes *after* the cohort's practical end date on its own steady
        pattern. Resolving holidays only up to that date would leave a session
        that overshoots unable to say it landed on one -- here Easter Monday, on
        the module's own delivery day, well past the cohort end.
        """
        cohort = {'id': 'COHORT-1', 'startDate': '2026-10-05', 'endDate': '2027-03-25'}
        holidays = views.cohort_selected_holidays_by_id([cohort], self.HOLIDAY_ROWS)['COHORT-1']
        plan = views.build_module_session_plan('2026-10-05', 26, 'Monday', holidays)
        dates = [session['date'] for session in plan['sessions']]

        # 26 straight Mondays from 5 Oct 2026, Easter Monday included.
        self.assertIn('2027-03-29', dates)
        self.assertEqual(dates[-1], '2027-03-29')
        self.assertIn(['2027-03-29'], [session['skippedHolidays'] for session in plan['sessions']])

    def test_the_cohort_itself_still_lists_only_its_own_period(self):
        # The other half of the same rule: what the drawer shows and the cohort
        # row records is the narrower in-period set, not the scheduling one.
        details = views.cohort_holiday_details(
            self.HOLIDAY_ROWS, None, '2026-12-01', '2027-02-28',
        )
        self.assertEqual(
            [holiday['startDate'] for holiday in details['holidaysInRange']],
            ['2026-12-25', '2026-12-28', '2027-01-01'],
        )
        # Easter Monday is outside the period, so the cohort does not claim it,
        # even though a module of that cohort would still step over it.
        self.assertNotIn(
            '2027-03-29',
            [holiday['startDate'] for holiday in details['holidaysInRange']],
        )

    def test_holidays_before_the_cohort_starts_do_not_apply(self):
        # Nothing delivers before the cohort starts, so the set begins there.
        holidays = views.cohort_selected_holidays_by_id(
            [{'id': 'COHORT-1', 'startDate': '2027-01-02'}], self.HOLIDAY_ROWS,
        )['COHORT-1']
        self.assertEqual(
            [holiday['startDate'] for holiday in holidays],
            ['2027-03-29'],
        )

    def test_the_planner_with_no_delivery_day_still_dates_a_plain_run_of_weeks(self):
        # No weekday means no slot to skip onto, so the planner stays a plain
        # count of weeks rather than losing its dates entirely.
        #
        # Asked of the planner rather than of the session list: the session list
        # is the authored live sessions and generates nothing, so a delivery day
        # it never reads cannot change it. The planner is still what dates a
        # module's weeks in the builder, so the fallback is pinned there.
        module = {**self.MODULE, 'session_week_day': ''}
        plan = views.module_delivery_session_plan(
            module, 6, views.parse_date('2026-12-21'), self.holidays_by_cohort()['COHORT-1'],
        )
        dates = [session['date'] for session in plan['sessions']]
        self.assertEqual(len(dates), 6)
        self.assertEqual(dates[0], '2026-12-21')
        self.assertEqual(dates[1], '2026-12-28')

    def test_a_cohort_with_no_start_date_still_flags_every_bank_holiday(self):
        """An imported cohort missing its dates still gets every applicable holiday.

        There is no period to resolve, so the whole calendar applies rather than
        none of it. A session still runs on its own day, ticked holiday or not --
        it is flagged, not moved, so the author can see the clash.
        """
        holidays = views.cohort_selected_holidays_by_id([{'id': 'COHORT-1'}], self.HOLIDAY_ROWS)
        self.assertEqual(len(holidays['COHORT-1']), len(self.HOLIDAY_ROWS))
        sessions = views.build_sessions_from_authoring_modules([self.MODULE], holidays)
        self.assertEqual(
            [session['date'] for session in sessions],
            ['2026-12-21', '2026-12-28', '2027-01-04', '2027-01-11', '2027-01-18', '2027-01-25'],
        )
        self.assertEqual(sessions[1]['skippedHolidays'], ['2026-12-28'])


class CohortContractDateHolidayTests(SimpleTestCase):
    """A cohort's dates are contracted dates: its holidays do not move them.

    A ticked holiday used to push the practical end date out, and the EPA window
    with it. It no longer does. What a closure moves is the delivery it clashes
    with: the module session plan shifts later (AuthoringModuleSessionHolidayTests)
    while the signed cohort dates stay exactly where the duration puts them.

    The preview endpoint is where an editor reads those dates, so it is where the
    rule is held. ``holidayExtensionDays``/``holidayExtensions`` are still in the
    response for readers that ask for them, and are always empty -- the tests
    below are what stop an extension quietly growing back into a contract date.
    """

    def preview(self, **body):
        request = RequestFactory().post(
            '/curriculum_api/curriculum/preview/cohort-end-date/',
            data=json.dumps(body),
            content_type='application/json',
        )
        return json.loads(views.curriculum_preview_cohort_end_date(request).content)

    def test_a_holiday_inside_the_period_leaves_both_end_dates_alone(self):
        # A 7 day closure inside a 24 month cohort: the practical end date is
        # still start plus duration less a day, and the EPA window is counted
        # from there.
        result = self.preview(
            startDate='2026-02-01',
            durationMonths=24,
            epaMonths=5,
            holidays=[{'startDate': '2027-09-26', 'endDate': '2027-10-02'}],
        )
        self.assertEqual(result['baseEndDate'], '2028-01-31')
        self.assertEqual(result['practicalEndDate'], '2028-01-31')
        self.assertEqual(result['apprenticeshipEndDate'], '2028-06-30')
        self.assertEqual(result['holidayExtensionDays'], 0)
        self.assertEqual(result['holidayExtensions'], [])

    def test_no_holidays_leaves_the_duration_rule_untouched(self):
        result = self.preview(startDate='2026-02-01', durationMonths=24, epaMonths=5)
        self.assertEqual(result['holidayExtensionDays'], 0)
        self.assertEqual(result['practicalEndDate'], '2028-01-31')
        self.assertEqual(result['baseEndDate'], '2028-01-31')
        self.assertEqual(result['apprenticeshipEndDate'], '2028-06-30')

    def test_a_holiday_straddling_the_end_date_moves_nothing_either(self):
        # The case that used to be arithmetic worth arguing about -- how many of
        # its days fell inside the period -- now changes nothing at all here.
        result = self.preview(
            startDate='2026-02-01',
            durationMonths=24,
            epaMonths=5,
            holidays=[{'startDate': '2028-01-31', 'endDate': '2028-02-06'}],
        )
        self.assertEqual(result['practicalEndDate'], '2028-01-31')
        self.assertEqual(result['holidayExtensionDays'], 0)

    def test_the_screenshot_cohort_keeps_its_contracted_dates(self):
        # The worked case from the delivery team's MBA Feb-2026 cohort: four
        # holidays, 37 days between them, and the same two dates it was signed
        # with. Those 37 days are absorbed by the module schedules instead.
        result = self.preview(
            startDate='2026-02-01',
            durationMonths=24,
            epaMonths=5,
            holidays=[
                {'startDate': '2027-07-25', 'endDate': '2027-08-07'},
                {'startDate': '2027-09-26', 'endDate': '2027-10-02'},
                {'startDate': '2027-12-19', 'endDate': '2028-01-02'},
                {'startDate': '2028-01-31', 'endDate': '2028-02-06'},
            ],
        )
        self.assertEqual(result['baseEndDate'], '2028-01-31')
        self.assertEqual(result['practicalEndDate'], '2028-01-31')
        self.assertEqual(result['apprenticeshipEndDate'], '2028-06-30')
        self.assertEqual(result['holidayExtensionDays'], 0)

    def test_an_authored_practical_end_date_still_wins(self):
        # A human who typed a date has accounted for what they meant to, and the
        # EPA window follows the date they set rather than the rule.
        result = self.preview(
            startDate='2026-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2028-03-01',
            holidays=[{'startDate': '2027-09-26', 'endDate': '2027-10-02'}],
        )
        self.assertEqual(result['practicalEndDate'], '2028-03-01')
        self.assertEqual(result['apprenticeshipEndDate'], '2028-08-01')
        self.assertTrue(result['practicalEndIsManual'])
        # The calculated date is still reported, so the editor can offer "reset".
        self.assertEqual(result['calculatedEndDate'], '2028-01-31')

    def test_the_answer_is_stable_when_fed_back_in(self):
        # Re-previewing the same inputs must answer the same dates, or the drawer
        # would move them a little further every time it is reopened.
        holidays = [{'startDate': '2027-09-26', 'endDate': '2027-10-02'}]
        first = self.preview(startDate='2026-02-01', durationMonths=24, epaMonths=5, holidays=holidays)
        second = self.preview(startDate='2026-02-01', durationMonths=24, epaMonths=5, holidays=holidays)
        self.assertEqual(first['practicalEndDate'], second['practicalEndDate'])
        self.assertEqual(first['baseEndDate'], second['baseEndDate'])
        self.assertEqual(second['practicalEndDate'], '2028-01-31')

    def test_the_contracted_duration_is_reported_alongside_the_effective_one(self):
        # The duration the user typed is never rewritten. What sits next to it is
        # how long the cohort actually runs -- which now differs only when
        # somebody authored a date of their own.
        with_holidays = self.preview(
            startDate='2026-02-01',
            durationMonths=24,
            epaMonths=5,
            holidays=[{'label': 'Oct 27', 'startDate': '2027-09-26', 'endDate': '2027-10-02'}],
        )
        self.assertEqual(with_holidays['durationMonths'], 24)
        self.assertEqual(with_holidays['effectiveDurationMonths'], 24)

        authored = self.preview(
            startDate='2026-02-01', durationMonths=24, epaMonths=5, practicalEndDate='2028-03-01',
        )
        self.assertEqual(authored['durationMonths'], 24)
        # 1 Feb 2026 to 1 Mar 2028 is a day into the 26th month, so that is what
        # the cohort effectively runs -- the typed date is reported by its own
        # length, never by the duration that was contracted.
        self.assertEqual(authored['effectiveDurationMonths'], 26)

    def test_the_response_says_where_holidays_do_apply(self):
        # The rule travels with the answer: a reader who sees an unmoved date is
        # told what the holidays they ticked actually affect.
        result = self.preview(startDate='2026-02-01', durationMonths=24, epaMonths=5)
        self.assertIn('holidays extend clashing module schedules, not cohort dates', result['rule'])


class HolidayDayArithmeticTests(SimpleTestCase):
    """Counting the days a set of closures takes out of a period.

    ``holiday_extension_days``/``holiday_extension_breakdown`` no longer move any
    cohort date -- they are the shared arithmetic for "how much of this period
    did these closures cover", and the counting rules are the part worth holding:
    whole calendar days, clamped to the period, and a shared day counted once.
    """

    PERIOD = ('2026-02-01', '2028-01-31')

    def days(self, holidays):
        return views.holiday_extension_days(holidays, *self.PERIOD)

    def breakdown(self, holidays):
        return views.holiday_extension_breakdown(holidays, *self.PERIOD)

    def test_weekends_inside_a_holiday_are_counted(self):
        # A calendar span, not a working-day budget: a two week closure covers
        # fourteen dates whether or not delivery would have run on all of them.
        self.assertEqual(self.days([{'startDate': '2027-07-25', 'endDate': '2027-08-07'}]), 14)

    def test_only_the_days_inside_the_period_are_counted(self):
        # 31 Jan 2028 - 06 Feb 2028 against a period ending 31 Jan 2028: one day.
        self.assertEqual(self.days([{'startDate': '2028-01-31', 'endDate': '2028-02-06'}]), 1)
        self.assertEqual(self.days([{'startDate': '2028-05-01', 'endDate': '2028-05-07'}]), 0)

    def test_overlapping_holidays_are_not_double_counted(self):
        overlapping = [
            {'label': 'First', 'startDate': '2027-09-26', 'endDate': '2027-10-02'},
            {'label': 'Second', 'startDate': '2027-09-29', 'endDate': '2027-10-05'},
        ]
        # 26 Sept - 05 Oct inclusive is 10 distinct days, not 7 + 7.
        self.assertEqual(self.days(overlapping), 10)
        # And the named lines add up to exactly that, so a total and its own
        # explanation cannot disagree.
        self.assertEqual(sum(item['days'] for item in self.breakdown(overlapping)), 10)

    def test_the_breakdown_names_each_holiday_by_the_days_that_landed_inside(self):
        self.assertEqual(
            self.breakdown([
                {'label': 'Summer 27', 'startDate': '2027-07-25', 'endDate': '2027-08-07'},
                {'label': 'Feb 28', 'startDate': '2028-01-31', 'endDate': '2028-02-06'},
                {'label': 'Later', 'startDate': '2028-05-01', 'endDate': '2028-05-07'},
            ]),
            [
                {'label': 'Summer 27', 'startDate': '2027-07-25', 'endDate': '2027-08-07', 'days': 14},
                # Reported by the part that counted, not by its own full span.
                {'label': 'Feb 28', 'startDate': '2028-01-31', 'endDate': '2028-01-31', 'days': 1},
            ],
        )


class CohortEpaPeriodTests(SimpleTestCase):
    """The apprenticeship end date is the practical one plus the EPA period.

    The two end dates use different month arithmetic on purpose. A cohort's
    practical end date is the last day *before* the duration elapses (a 24 month
    cohort starting 2026-09-01 ends 2028-08-31), whereas the EPA window is
    counted forward from a date that has already passed, so it keeps the same day
    of the month.
    """

    def test_epa_period_is_added_to_the_practical_end_date(self):
        # The worked example from the delivery team: a practical period ending
        # 2027-03-04 with a 5 month EPA period ends 2027-08-04.
        self.assertEqual(
            views.format_date(views.calculate_apprenticeship_end_date('2027-03-04', 5)),
            '2027-08-04',
        )

    def test_epa_period_rolls_over_the_year(self):
        self.assertEqual(
            views.format_date(views.calculate_apprenticeship_end_date('2027-11-30', 3)),
            '2028-02-29',
        )

    def test_epa_period_clamps_to_a_shorter_target_month(self):
        # 31 January + 1 month has no 31st to land on.
        self.assertEqual(
            views.format_date(views.calculate_apprenticeship_end_date('2027-01-31', 1)),
            '2027-02-28',
        )

    def test_no_epa_period_means_no_apprenticeship_end_date(self):
        # Distinct from "ends the same day": nothing is offered until a period is
        # recorded, so the wizard can prompt for one.
        self.assertIsNone(views.calculate_apprenticeship_end_date('2027-03-04', None))
        self.assertIsNone(views.calculate_apprenticeship_end_date('2027-03-04', 0))
        self.assertIsNone(views.calculate_apprenticeship_end_date('', 5))

    def test_epa_months_parses_only_whole_non_negative_months(self):
        self.assertEqual(views.parse_epa_months('5'), 5)
        self.assertEqual(views.parse_epa_months(0), 0)
        self.assertIsNone(views.parse_epa_months(''))
        self.assertIsNone(views.parse_epa_months(None))
        self.assertIsNone(views.parse_epa_months('-3'))
        self.assertIsNone(views.parse_epa_months('not a number'))

    def test_an_absent_epa_period_keeps_the_stored_one(self):
        # A partial save that never mentions the EPA period must not drop it.
        self.assertEqual(views.payload_epa_months({'name': 'Sep-2026'}, {'epa_months': 5}), 5)
        # An explicit blank clears it.
        self.assertIsNone(views.payload_epa_months({'epaMonths': ''}, {'epa_months': 5}))
        self.assertEqual(views.payload_epa_months({'epaMonths': 3}, {'epa_months': 5}), 3)

    def test_serialized_cohort_exposes_both_end_dates(self):
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0001',
            'cohort_name': 'Nov-2025',
            'start_date': '2025-11-05',
            'end_date': '2027-03-04',
            'epa_months': 5,
        })
        self.assertEqual(detail['startDate'], '2025-11-05')
        self.assertEqual(detail['endDate'], '2027-03-04')
        self.assertEqual(detail['practicalEndDate'], '2027-03-04')
        self.assertEqual(detail['epaMonths'], 5)
        self.assertEqual(detail['apprenticeshipEndDate'], '2027-08-04')

    def test_a_moved_practical_end_date_moves_the_apprenticeship_one(self):
        # The stored apprenticeship_end_date is a cache, so a row whose practical
        # end date moved without the cache being rewritten must still read true.
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0002',
            'end_date': '2027-04-04',
            'epa_months': 5,
            'apprenticeship_end_date': '2027-08-04',
        })
        self.assertEqual(detail['apprenticeshipEndDate'], '2027-09-04')

    def test_cohort_without_an_epa_period_reports_no_apprenticeship_end_date(self):
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0003',
            'end_date': '2027-03-04',
        })
        self.assertEqual(detail['endDate'], '2027-03-04')
        self.assertIsNone(detail['epaMonths'])
        self.assertEqual(detail['apprenticeshipEndDate'], '')


class ApprenticeshipEndDateOverrideTests(SimpleTestCase):
    """The apprenticeship end date is editable, and the edit wins.

    The calculated date is only a default. A cohort that has to move it — a break
    in learning, a resit window, an EPAO with no capacity — usually lands on a
    date no whole number of EPA months reaches, so the authored date has to
    override the arithmetic rather than be reverse-engineered into months.
    """

    def test_an_authored_date_wins_over_the_epa_calculation(self):
        self.assertEqual(
            views.format_date(views.cohort_apprenticeship_end_date('2027-03-04', 5, '2027-09-30')),
            '2027-09-30',
        )

    def test_no_override_falls_back_to_the_epa_calculation(self):
        self.assertEqual(
            views.format_date(views.cohort_apprenticeship_end_date('2027-03-04', 5, None)),
            '2027-08-04',
        )
        self.assertEqual(
            views.format_date(views.cohort_apprenticeship_end_date('2027-03-04', 5, '')),
            '2027-08-04',
        )

    def test_an_override_stands_in_with_no_epa_period_at_all(self):
        # A cohort can now carry an apprenticeship end date without anyone having
        # to invent an EPA period to produce it.
        self.assertEqual(
            views.format_date(views.cohort_apprenticeship_end_date('2027-03-04', None, '2027-09-30')),
            '2027-09-30',
        )

    def test_an_unreadable_override_falls_back_rather_than_raising(self):
        self.assertEqual(
            views.format_date(views.cohort_apprenticeship_end_date('2027-03-04', 5, 'not a date')),
            '2027-08-04',
        )

    def test_an_absent_override_keeps_the_stored_one(self):
        # A partial save that never mentions the date must not drop it.
        self.assertEqual(
            views.format_date(views.payload_apprenticeship_end_override(
                {'name': 'Sep-2026'},
                {'apprenticeship_end_override': '2027-09-30'},
            )),
            '2027-09-30',
        )
        # An explicit blank clears it and hands the date back to the EPA rule.
        self.assertIsNone(views.payload_apprenticeship_end_override(
            {'apprenticeshipEndDate': ''},
            {'apprenticeship_end_override': '2027-09-30'},
        ))
        self.assertEqual(
            views.format_date(views.payload_apprenticeship_end_override(
                {'apprenticeshipEndDate': '2028-01-15'},
                {'apprenticeship_end_override': '2027-09-30'},
            )),
            '2028-01-15',
        )

    def test_a_round_tripped_cohort_cannot_promote_a_calculated_date(self):
        # A serialized cohort carries the effective apprenticeshipEndDate next to
        # the authored apprenticeshipEndOverride. Reading the effective one as an
        # override would freeze a calculated date into an authored one, so the
        # explicit key wins.
        self.assertIsNone(views.payload_apprenticeship_end_override({
            'apprenticeshipEndDate': '2027-08-04',
            'apprenticeshipEndOverride': '',
        }))

    def test_serialized_cohort_reports_the_override_and_flags_it(self):
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0004',
            'end_date': '2027-03-04',
            'epa_months': 5,
            'apprenticeship_end_override': '2027-09-30',
        })
        self.assertEqual(detail['practicalEndDate'], '2027-03-04')
        self.assertEqual(detail['epaMonths'], 5)
        self.assertEqual(detail['apprenticeshipEndDate'], '2027-09-30')
        self.assertEqual(detail['apprenticeshipEndOverride'], '2027-09-30')

    def test_a_calculated_cohort_reports_no_override(self):
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0005',
            'end_date': '2027-03-04',
            'epa_months': 5,
        })
        self.assertEqual(detail['apprenticeshipEndDate'], '2027-08-04')
        self.assertEqual(detail['apprenticeshipEndOverride'], '')

    def test_an_override_does_not_move_with_the_practical_end_date(self):
        # The opposite of the calculated case: an authored date is the date the
        # delivery team was given, so a shifted practical period leaves it alone.
        detail = views.serialize_cohort_authoring_detail({
            'cohort_id': 'COHORT-EPA-0006',
            'end_date': '2027-04-04',
            'epa_months': 5,
            'apprenticeship_end_override': '2027-09-30',
        })
        self.assertEqual(detail['apprenticeshipEndDate'], '2027-09-30')

    def test_cohort_view_payload_carries_both_dates(self):
        cohort = views.curriculum_cohort_from_authoring_detail({
            'cohortId': 'COHORT-EPA-0007',
            'endDate': '2027-03-04',
            'epaMonths': 5,
            'apprenticeshipEndOverride': '2027-09-30',
        })
        self.assertEqual(cohort['practicalEndDate'], '2027-03-04')
        self.assertEqual(cohort['apprenticeshipEndDate'], '2027-09-30')
        self.assertEqual(cohort['apprenticeshipEndOverride'], '2027-09-30')


class CohortEndDatePreviewTests(SimpleTestCase):
    """The preview the cohort editors call while the user types.

    Both end dates are shown to the delivery team as real, editable dates rather
    than as blank override boxes, so the preview has to accept a practical end
    date that was nudged a day either way and count the EPA window from *that*
    date -- while still reporting what the duration rule would have given, so the
    editor can offer a way back to it.
    """

    def preview(self, **body):
        request = RequestFactory().post(
            '/curriculum_api/curriculum/preview/cohort-end-date/',
            data=json.dumps(body),
            content_type='application/json',
        )
        return json.loads(views.curriculum_preview_cohort_end_date(request).content)

    def test_practical_end_date_is_calculated_when_none_is_authored(self):
        result = self.preview(startDate='2025-02-01', durationMonths=24, epaMonths=5)
        self.assertEqual(result['practicalEndDate'], '2027-01-31')
        self.assertEqual(result['calculatedEndDate'], '2027-01-31')
        self.assertEqual(result['apprenticeshipEndDate'], '2027-06-30')
        self.assertFalse(result['practicalEndIsManual'])

    def test_an_authored_practical_end_date_moves_the_epa_window(self):
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2027-02-02',
        )
        self.assertEqual(result['practicalEndDate'], '2027-02-02')
        self.assertEqual(result['apprenticeshipEndDate'], '2027-07-02')
        self.assertTrue(result['practicalEndIsManual'])

    def test_the_calculated_practical_end_date_is_reported_alongside_the_authored_one(self):
        # What the editor's "reset to the calculated date" needs, without a
        # second round trip that sends no practical end date.
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2027-02-02',
        )
        self.assertEqual(result['calculatedEndDate'], '2027-01-31')

    def test_an_authored_date_matching_the_rule_is_not_reported_as_manual(self):
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2027-01-31',
        )
        self.assertFalse(result['practicalEndIsManual'])

    def test_no_practical_end_date_leaves_the_calculation_alone(self):
        # The editor sends null in automatic mode; it must not read as a date.
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate=None,
        )
        self.assertEqual(result['practicalEndDate'], '2027-01-31')
        self.assertFalse(result['practicalEndIsManual'])

    def test_an_authored_apprenticeship_end_date_still_wins_outright(self):
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2027-02-02',
            apprenticeshipEndOverride='2027-07-15',
        )
        self.assertEqual(result['apprenticeshipEndDate'], '2027-07-15')
        self.assertEqual(result['apprenticeshipEndOverride'], '2027-07-15')

    def test_a_practical_end_date_before_the_start_date_warns_rather_than_fails(self):
        result = self.preview(
            startDate='2025-02-01',
            durationMonths=24,
            epaMonths=5,
            practicalEndDate='2024-12-01',
        )
        self.assertEqual(result['practicalEndDate'], '2024-12-01')
        self.assertIn('The practical end date is before the cohort start date.', result['warnings'])


class TutorAssignmentNotificationTests(TestCase):
    """Mailing a tutor the delivery facts when a module lands on them."""

    def setUp(self):
        views.reset_schema_ready_flags()
        views.invalidate_curriculum_cache()
        views.ensure_module_authoring_tables()
        with connection.cursor() as cursor:
            for table in (
                views.AUTHORING_MODULES_TABLE,
                views.GROUPS_TABLE,
                views.COHORT_AUTHORING_DETAILS_TABLE,
            ):
                cursor.execute(f'delete from {views.authoring_table_name(table)}')
        # Provisioned against empty tables, so the seed records nothing and every
        # assignment these tests make afterwards counts as new.
        tutor_notifications.ensure_notification_table()
        # Who the tutors are comes from the staff directory; which module each one
        # holds comes from the module row. There is no third place to seed.
        self.directory = []
        patcher = patch(
            'curriculum_api.views.fetch_staff_users_by_access',
            side_effect=lambda access: [
                row for row in self.directory
                if views.clean_str(row.get('Access')).lower() == views.clean_str(access).lower()
            ],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- fixtures ----------------------------------------------------------

    def seed_tutor(self, name='Amira Hassan', email='amira@example.com'):
        row = staff_user_row(name, email, access='tutor', row_id=1)
        # An address the directory does not hold is an empty one, not a default.
        row['Email'] = email
        self.directory = [row]
        views.invalidate_curriculum_cache()
        return row

    def seed_delivery(self, module_id='MOD-ALPHA', title='Data Handling', tutor_name=''):
        views.authoring_upsert(views.COHORT_AUTHORING_DETAILS_TABLE, ['cohort_id'], {
            'cohort_id': 'COH-1',
            'cohort_name': 'September 2026',
            'programme_id': 'PROG-1',
            'programme_name': 'Data Analyst L4',
            'start_date': '2026-09-01',
            'end_date': '2027-06-30',
        })
        views.authoring_upsert(views.GROUPS_TABLE, ['group_id'], {
            'group_id': 'GRP-1',
            'group_name': 'Group A',
            'cohort_id': 'COH-1',
            'cohort_name': 'September 2026',
            'programme_id': 'PROG-1',
            'programme_name': 'Data Analyst L4',
            'coach_name': 'Sam Coach',
            'session_week_day': 'Tuesday',
            'session_start_time': '09:00',
            'session_end_time': '12:00',
        })
        views.authoring_upsert(views.AUTHORING_MODULES_TABLE, ['module_catalogue_id'], {
            'module_catalogue_id': module_id,
            'title': title,
            'programme_id': 'PROG-1',
            'programme_name': 'Data Analyst L4',
            'cohort_id': 'COH-1',
            'cohort_name': 'September 2026',
            'group_id': 'GRP-1',
            'group_name': 'Group A',
            'tutor_name': tutor_name,
            'sessions_number': 8,
            'start_date': '2026-09-08',
            'end_date': '2026-11-03',
            'total_otjh': 24,
        })

    def sent_mail(self):
        return patch.object(
            tutor_notifications.email_azure, 'send_mail', return_value=(True, None)
        )

    # -- tests -------------------------------------------------------------

    def test_assignment_mail_carries_the_full_delivery_context(self):
        """The mail has to answer "what, for whom, when" without opening the LMS."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()

        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs['to'], 'amira@example.com')
        self.assertIn('Data Handling', kwargs['subject'])

        body = kwargs['text_body']
        self.assertIn('Data Handling', body)
        self.assertIn('MOD-ALPHA', body)
        self.assertIn('Data Analyst L4', body)
        self.assertIn('September 2026', body)
        self.assertIn('Group A', body)
        # Schedule falls back to the group when the module row carries none.
        self.assertIn('Tuesday 09:00-12:00', body)
        self.assertIn('2026-09-08 to 2026-11-03', body)
        self.assertRegex(body, r'Sessions:\s+8')
        self.assertRegex(body, r'OTJ hours:\s+24 hours')
        self.assertIn('Sam Coach', body)
        self.assertIn('Data Handling', kwargs['html_body'])

    def test_the_same_assignment_is_not_mailed_twice(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        with self.sent_mail():
            tutor_notifications.dispatch_assignment_notifications()
        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()

        send.assert_not_called()

    def test_several_modules_saved_together_produce_one_mail(self):
        """A wizard save that attaches three modules is one assignment, not three."""
        self.seed_tutor()
        self.seed_delivery(module_id='MOD-A', title='Module A', tutor_name='Amira Hassan')
        self.seed_delivery(module_id='MOD-B', title='Module B', tutor_name='Amira Hassan')
        self.seed_delivery(module_id='MOD-C', title='Module C', tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()

        send.assert_called_once()
        body = send.call_args.kwargs['text_body']
        self.assertIn('Module A', body)
        self.assertIn('Module B', body)
        self.assertIn('Module C', body)
        self.assertIn('3 modules', send.call_args.kwargs['subject'])

    def test_a_tutor_with_no_email_is_skipped_and_stays_notifiable(self):
        """No address is not a delivered notification — record nothing, retry later."""
        self.seed_tutor(email='')
        self.seed_delivery(tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_not_called()
        self.assertEqual(tutor_notifications.ledger_rows(), [])

        self.seed_tutor(email='amira@example.com')
        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_called_once()

    def test_reassignment_after_removal_is_mailed_again(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with self.sent_mail():
            tutor_notifications.dispatch_assignment_notifications()

        views.update_authoring_rows(
            views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-ALPHA'],
            {'tutor_name': ''},
        )
        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_not_called()
        self.assertEqual(tutor_notifications.ledger_rows(), [])

        views.update_authoring_rows(
            views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-ALPHA'],
            {'tutor_name': 'Amira Hassan'},
        )
        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_called_once()

    def test_a_failed_send_is_retried_then_given_up_on(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        failing = patch.object(
            tutor_notifications.email_azure, 'send_mail', return_value=(False, 'graph 503')
        )
        for _ in range(tutor_notifications.MAX_SEND_ATTEMPTS):
            with failing as send:
                tutor_notifications.dispatch_assignment_notifications()
                send.assert_called_once()

        with failing as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_not_called()

    def test_a_deleted_module_is_not_mailed_and_frees_its_ledger_row(self):
        """A module removed from the programme is not a teaching assignment."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with self.sent_mail():
            tutor_notifications.dispatch_assignment_notifications()

        views.update_authoring_rows(
            views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-ALPHA'],
            {'is_programme_deleted': True},
        )
        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()
        send.assert_not_called()
        self.assertEqual(tutor_notifications.ledger_rows(), [])

    def test_assignments_predating_the_ledger_are_seeded_rather_than_mailed(self):
        """First deployment must not mail every tutor their whole back catalogue."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with connection.cursor() as cursor:
            cursor.execute(
                'drop table if exists '
                + views.table_name(tutor_notifications.NOTIFICATION_TABLE)
            )
        views.reset_schema_ready_flags()

        with self.sent_mail() as send:
            tutor_notifications.dispatch_assignment_notifications()

        send.assert_not_called()
        rows = tutor_notifications.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'seeded')

    def test_assigning_a_tutor_no_longer_mails_them(self):
        """Assignment emails are sent from the module workspace, never on save.

        save_tree_group_modules and every other assignment path still reach
        notify_staff_assignment_change; it now schedules nothing.
        """
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                views.notify_staff_assignment_change()
        send.assert_not_called()
        self.assertEqual(callbacks, [])
        self.assertEqual(tutor_notifications.ledger_rows(), [])

    def test_staff_email_the_tutor_of_one_module_on_request(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        self.assertEqual(tutor_notifications.module_tutor_status('MOD-ALPHA'), {
            'moduleId': 'MOD-ALPHA', 'tutor': {'name': 'Amira Hassan', 'hasEmail': True}, 'lastSent': None,
        })

        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_module_tutor_email('MOD-ALPHA')
        self.assertEqual((status, payload['sent'], payload['tutor']), (200, True, 'Amira Hassan'))
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs['to'], 'amira@example.com')
        self.assertIn('Data Handling', send.call_args.kwargs['text_body'])
        self.assertEqual(tutor_notifications.module_tutor_status('MOD-ALPHA')['lastSent']['status'], 'sent')

        # Sending again is allowed (the button asks first) and still one row.
        with self.sent_mail() as send:
            tutor_notifications.send_module_tutor_email('MOD-ALPHA')
        send.assert_called_once()
        self.assertEqual(len(tutor_notifications.ledger_rows()), 1)

    def test_several_deliveries_of_one_module_are_one_email(self):
        """A drawer save that attaches three groups is one decision, not three.

        The tutor is put on one delivery per group in a single save, and
        `email_azure.tutor_assignment_message` has always taken a list for
        exactly this. Three mails in the same second would describe the same
        assignment three times.
        """
        self.seed_tutor()
        self.seed_delivery(module_id='MOD-ALPHA', title='Data Handling', tutor_name='Amira Hassan')
        self.seed_delivery(module_id='MOD-BETA', title='Statistics', tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_tutor_email(['MOD-ALPHA', 'MOD-BETA'])

        self.assertEqual((status, payload['sent'], payload['modules']), (200, True, 2))
        send.assert_called_once()
        body = send.call_args.kwargs['text_body']
        self.assertIn('Data Handling', body)
        self.assertIn('Statistics', body)
        # One mail, but each module keeps its own ledger row so the workspace can
        # still say when that module last went out.
        self.assertEqual(len(tutor_notifications.ledger_rows()), 2)
        for module_id in ('MOD-ALPHA', 'MOD-BETA'):
            self.assertEqual(tutor_notifications.module_tutor_status(module_id)['lastSent']['status'], 'sent')

    def test_the_same_delivery_named_twice_is_still_one_entry(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_tutor_email(['MOD-ALPHA', 'MOD-ALPHA', ''])
        self.assertEqual((status, payload['modules']), (200, 1))
        send.assert_called_once()
        self.assertEqual(len(tutor_notifications.ledger_rows()), 1)

    def test_modules_with_different_tutors_are_refused_not_split(self):
        """Who the one mail is addressed to has to be unambiguous."""
        amira = staff_user_row('Amira Hassan', 'amira@example.com', access='tutor', row_id=1)
        amira['Email'] = 'amira@example.com'
        ben = staff_user_row('Ben Carter', 'ben@example.com', access='tutor', row_id=2)
        ben['Email'] = 'ben@example.com'
        self.directory = [amira, ben]
        views.invalidate_curriculum_cache()
        self.seed_delivery(module_id='MOD-ALPHA', title='Data Handling', tutor_name='Amira Hassan')
        self.seed_delivery(module_id='MOD-BETA', title='Statistics', tutor_name='Ben Carter')

        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_tutor_email(['MOD-ALPHA', 'MOD-BETA'])
        self.assertEqual(status, 409)
        self.assertIn('same tutor', payload['error'])
        send.assert_not_called()
        self.assertEqual(tutor_notifications.ledger_rows(), [])

    def test_naming_no_module_sends_nothing(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_tutor_email([])
        self.assertEqual(status, 400)
        send.assert_not_called()

    def test_the_tick_reads_its_state_from_the_ledger_not_from_the_drawer(self):
        """Nothing sent yet reads as unchecked; a send makes it checked."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        before = tutor_notifications.tutor_email_status(['MOD-ALPHA'], 'Amira Hassan')
        self.assertEqual((before['total'], before['emailed'], before['lastSentAt']), (1, 0, None))
        self.assertEqual(before['tutor'], {'name': 'Amira Hassan', 'hasEmail': True})

        with self.sent_mail():
            tutor_notifications.send_tutor_email(['MOD-ALPHA'])

        after = tutor_notifications.tutor_email_status(['MOD-ALPHA'], 'Amira Hassan')
        self.assertEqual((after['total'], after['emailed']), (1, 1))
        self.assertTrue(after['lastSentAt'])
        self.assertTrue(after['deliveries'][0]['emailed'])

    def test_a_failed_send_does_not_count_as_emailed(self):
        """The ledger records the attempt; the tutor still heard nothing."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with patch.object(tutor_notifications.email_azure, 'send_mail', return_value=(False, 'graph 503')):
            _payload, status = tutor_notifications.send_tutor_email(['MOD-ALPHA'])
        self.assertEqual(status, 502)

        state = tutor_notifications.tutor_email_status(['MOD-ALPHA'], 'Amira Hassan')
        self.assertEqual(state['emailed'], 0)
        self.assertIsNone(state['lastSentAt'])
        # The attempt is visible, but it is not a notification.
        self.assertEqual(state['deliveries'][0]['lastSent']['status'], 'failed')

    def test_the_previous_tutor_emailed_state_never_carries_over(self):
        amira = staff_user_row('Amira Hassan', 'amira@example.com', access='tutor', row_id=1)
        amira['Email'] = 'amira@example.com'
        ben = staff_user_row('Ben Carter', 'ben@example.com', access='tutor', row_id=2)
        ben['Email'] = 'ben@example.com'
        self.directory = [amira, ben]
        views.invalidate_curriculum_cache()
        self.seed_delivery(tutor_name='Amira Hassan')
        with self.sent_mail():
            tutor_notifications.send_tutor_email(['MOD-ALPHA'])

        self.assertEqual(tutor_notifications.tutor_email_status(['MOD-ALPHA'], 'Amira Hassan')['emailed'], 1)
        # The drawer asking about the tutor somebody just picked, before it is
        # saved onto the module. Amira's record is hers and says nothing here.
        handed_over = tutor_notifications.tutor_email_status(['MOD-ALPHA'], 'Ben Carter')
        self.assertEqual((handed_over['emailed'], handed_over['lastSentAt']), (0, None))
        self.assertEqual(handed_over['tutor'], {'name': 'Ben Carter', 'hasEmail': True})

    def test_a_partly_notified_module_reports_the_mix(self):
        """Two deliveries, one told: neither "emailed" nor "not emailed"."""
        self.seed_tutor()
        self.seed_delivery(module_id='MOD-ALPHA', title='Data Handling', tutor_name='Amira Hassan')
        self.seed_delivery(module_id='MOD-BETA', title='Statistics', tutor_name='Amira Hassan')
        with self.sent_mail():
            tutor_notifications.send_tutor_email(['MOD-ALPHA'])

        mixed = tutor_notifications.tutor_email_status(['MOD-ALPHA', 'MOD-BETA'], 'Amira Hassan')
        self.assertEqual((mixed['total'], mixed['emailed']), (2, 1))
        self.assertEqual(
            {d['moduleId']: d['emailed'] for d in mixed['deliveries']},
            {'MOD-ALPHA': True, 'MOD-BETA': False},
        )

        # Finishing the job covers both in one message, and the mix resolves.
        with self.sent_mail() as send:
            tutor_notifications.send_tutor_email(['MOD-ALPHA', 'MOD-BETA'])
        send.assert_called_once()
        done = tutor_notifications.tutor_email_status(['MOD-ALPHA', 'MOD-BETA'], 'Amira Hassan')
        self.assertEqual((done['total'], done['emailed']), (2, 2))

    def test_a_manual_send_explains_why_it_cannot_go(self):
        self.seed_delivery(tutor_name='')
        with self.sent_mail() as send:
            payload, status = tutor_notifications.send_module_tutor_email('MOD-ALPHA')
            self.assertEqual(status, 409)
            self.assertIn('Assign a tutor', payload['error'])
            self.seed_tutor(email='')
            self.seed_delivery(tutor_name='Amira Hassan')
            payload, status = tutor_notifications.send_module_tutor_email('MOD-ALPHA')
            self.assertEqual(status, 409)
            self.assertIn('no email address', payload['error'])
            self.assertEqual(tutor_notifications.send_module_tutor_email('MOD-MISSING')[1], 404)
        send.assert_not_called()

    def test_a_failed_manual_send_is_reported_and_recorded(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')
        with patch.object(tutor_notifications.email_azure, 'send_mail', return_value=(False, 'mailbox unavailable')):
            payload, status = tutor_notifications.send_module_tutor_email('MOD-ALPHA')
        self.assertEqual((status, payload['code']), (502, 'send_failed'))
        self.assertEqual(tutor_notifications.module_tutor_status('MOD-ALPHA')['lastSent']['status'], 'failed')

    def test_nothing_is_sent_while_the_transaction_is_still_open(self):
        """A save that rolls back must not have told anybody it happened."""
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        with self.sent_mail() as send:
            with self.captureOnCommitCallbacks(execute=False):
                views.notify_staff_assignment_change()
            send.assert_not_called()

    def test_the_feature_can_be_switched_off(self):
        self.seed_tutor()
        self.seed_delivery(tutor_name='Amira Hassan')

        with patch.dict(os.environ, {'TUTOR_ASSIGNMENT_EMAILS': 'false'}):
            with self.sent_mail() as send:
                tutor_notifications.dispatch_assignment_notifications()
        send.assert_not_called()


class ModuleTotalOtjhTests(CurriculumPersistenceHarness):
    """The stored module total is the sum of every component's expected OTJH.

    Nothing else feeds it: not the week count, not the session plan, and not a
    total sent by the client. Adding a component adds its hours; removing one
    takes them away again.
    """

    def component(self, title, otjh):
        return {'id': f'COMP-{title}', 'title': title, 'type': 'reading', 'expectedOtjh': otjh, 'points': 0}

    def save(self, weeks, **extra):
        payload = {'title': 'OTJH module', 'programmeName': 'OTJH Programme', 'weekStructure': weeks, **extra}
        views.save_module_authoring_structure('MOD-OTJH', payload, repair_links=False)
        row = views.authoring_fetch_all(views.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', ['MOD-OTJH'])[0]
        return float(row['total_otjh'])

    def test_total_is_the_sum_across_every_week_and_component(self):
        # Week 1: 2h + 3h. Week 2: 4h. The module is 9h.
        total = self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2), self.component('B', 3)]},
            {'id': 'WEEK-2', 'title': 'Week 2', 'components': [self.component('C', 4)]},
        ])
        self.assertEqual(total, 9)

    def test_adding_a_component_adds_its_hours(self):
        self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2), self.component('B', 3)]},
            {'id': 'WEEK-2', 'title': 'Week 2', 'components': [self.component('C', 4)]},
        ])
        # A 5h component on week 1 and a 0.5h one on week 2: 9 + 5.5 = 14.5h.
        total = self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [
                self.component('A', 2), self.component('B', 3), self.component('D', 5),
            ]},
            {'id': 'WEEK-2', 'title': 'Week 2', 'components': [self.component('C', 4), self.component('E', 0.5)]},
        ])
        self.assertEqual(total, 14.5)

    def test_removing_a_component_takes_its_hours_back(self):
        self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2), self.component('B', 3)]},
        ])
        total = self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2)]},
        ])
        self.assertEqual(total, 2)

    def test_a_total_sent_by_the_client_is_ignored(self):
        total = self.save(
            [{'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2), self.component('B', 3)]}],
            totalOtjh=40,
            declaredTotalOtjh=40,
        )
        self.assertEqual(total, 5)

    def test_each_component_row_stores_its_own_expected_otjh(self):
        self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [self.component('A', 2), self.component('B', 3)]},
        ])
        rows = views.authoring_fetch_all(
            views.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s', ['MOD-OTJH'],
        )
        self.assertEqual(sorted(float(row['expected_otjh']) for row in rows), [2, 3])

    def test_total_adds_component_minutes_not_raw_decimal_dust(self):
        total = self.save([
            {'id': 'WEEK-1', 'title': 'Week 1', 'components': [
                self.component('A', 0.33),
                self.component('B', 0.33),
                self.component('C', 0.33),
            ]},
        ])
        self.assertEqual(total, 1)
