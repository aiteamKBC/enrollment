import json
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from django.db import DatabaseError

from .active_users import (
    ComponentReferenceError,
    DeletedComponentReferenceError,
    OrphanComponentReferenceError,
    _canonical_ksb_items,
    _coerce_ksb_items,
    _fetch_ksb_items,
    _ksb_version_hash,
    _reported_minutes,
    _resolve_programme_id,
    completed_hours_from_progress,
    component_reference_exists,
    component_reference_state,
    refresh_learner_ksb_snapshot,
    hydrate_training_plan,
    save_progress_record,
)
from .active_users import connections as active_users_connections
from .absence_reports import (
    KBC_ATTENDANCE_ID_BASE,
    KBC_ATTENDANCE_ID_RANGE,
    _fetch_missed_sessions,
    _kbc_attendance_report_id,
    _resolve_absent_attendance,
)
from .components import _assignment_form_ready, submit_component_progress
from . import evidence_storage, progress_rules
from .progress_rules import (
    progress_achievement_status,
    progress_counts_as_achieved,
    progress_record_counts_as_achieved,
)
from . import attendance as attendance_module
from . import teams_attendance as teams_attendance_module
from .attendance import _summarize_attendance, learner_attendance
from .evidence_storage import (
    blob_url,
    parse_blob_url,
    resolve_read_url,
    upload_to_quarantine,
)
from .identity import learner_profile_for_source
from .learner_detail import (
    _active_profile_for_source,
    _annotate_otjh,
    _append_week_quizzes,
    _display_quiz_title,
    _matching_module_ids_for_quiz_record,
    _resolve_from_master,
    _schedule_based_week_target,
    _sequential_week_target,
    _week_target_rows,
)
from .learner_progression import ACTIVE_STATUS, READY_TO_ENROL_STATUS, _as_date, advance_learner
from .mappers import to_learner_detail
from .models import LearnerProfile, _progress_entry_activity, _serialise_quiz_ref
from .reflection_submissions import get_reflection_submission
from .time_tracking import issue_tracking_session
from .teams_attendance import (
    _attendance_interval_bounds,
    _module_expected_emails,
    sync_verified_teams_attendance_reporting,
)


class LearnerQuizReferenceTests(SimpleTestCase):
    def test_serialises_numeric_quiz_reference_without_breaking_text_ids(self):
        self.assertEqual(_serialise_quiz_ref("42"), 42)
        self.assertEqual(_serialise_quiz_ref("quiz-42"), "quiz-42")
        self.assertIsNone(_serialise_quiz_ref(None))

    @patch("learner_api.learner_detail._otjh_by_legacy_title", return_value={})
    @patch(
        "learner_api.learner_detail._otjh_by_component_id",
        return_value={"video-1": 2.0, "quiz-1": 0.5},
    )
    def test_quiz_hours_are_not_included_in_planned_otjh(self, _by_id, _by_title):
        components = [
            {"componentId": "video-1", "module": "M1", "week": "W1", "type": "video"},
            {"componentId": "quiz-1", "module": "M1", "week": "W1", "type": "quiz", "isQuiz": True},
        ]

        annotated, total = _annotate_otjh(components)
        week_rows = _week_target_rows({
            "week": [{"module": "M1", "week": "W1", "moduleId": None, "weekId": None}],
            "components": annotated,
        })

        self.assertEqual(total, 2.0)
        self.assertEqual(annotated[1]["expectedOtjh"], 0.5)
        self.assertEqual(week_rows[0]["otjh"], 2.0)


class LearnerDetailPrefetchTests(SimpleTestCase):
    def test_progress_projection_reuses_loaded_history_and_nested_relations(self):
        from .models import LearnerProgressEntry, LearnerProgressKsb, LearnerQuizAnswer

        profile = LearnerProfile(id=19)
        progress = LearnerProgressEntry(id=1, learner=profile, kind="quiz", quiz_ref="42", passed=True)
        answer = LearnerQuizAnswer(id=2, progress=progress, question_ref=7, chosen_answer_ref=8, is_correct=True)
        answer._prefetched_objects_cache = {"chosen_answers": [], "correct_answers": []}
        progress._prefetched_objects_cache = {
            "ksb_links": [LearnerProgressKsb(progress=progress, position=1, ksb_code="K1")],
            "quiz_answers": [answer],
        }
        profile._prefetched_objects_cache = {"progress_entries": [progress]}
        # SimpleTestCase refuses any database access: repeated projections must
        # keep both achievements and answers without issuing another query.
        first = profile.training_plan_progress
        self.assertEqual(profile.training_plan_progress, first)
        self.assertEqual(first[0]["ksbs"], ["K1"])
        self.assertEqual(first[0]["questions"][0]["chosenAnswerId"], 8)
        self.assertTrue(first[0]["passed"])

    def test_empty_prefetched_history_needs_no_database_read(self):
        profile = LearnerProfile(id=19)
        profile._prefetched_objects_cache = {"progress_entries": []}
        self.assertEqual(profile.training_plan_progress, [])

    def test_soft_archived_progress_is_not_in_the_active_projection(self):
        from django.utils import timezone
        from .models import LearnerProgressEntry

        profile = LearnerProfile(id=19)
        archived = LearnerProgressEntry(
            id=1, learner=profile, kind="component", component_ref="old-component",
            deleted_at=timezone.now(),
        )
        archived._prefetched_objects_cache = {"ksb_links": [], "quiz_answers": []}
        profile._prefetched_objects_cache = {"progress_entries": [archived]}

        self.assertEqual(profile.training_plan_progress, [])

    @patch("learner_api.learner_detail.prefetch_related_objects")
    @patch("learner_api.learner_detail.learner_profile_for_source")
    def test_prefetches_the_complete_progress_graph(self, resolve_profile, prefetch):
        source = SimpleNamespace(email="learner@example.com")
        profile = Mock()
        resolve_profile.return_value = profile

        self.assertIs(_active_profile_for_source(source, 19), profile)

        resolve_profile.assert_called_once_with(source, 19)
        prefetch.assert_called_once_with(
            [profile],
            "ksb_assignment__profile_version__definitions",
            "progress_entries__ksb_links",
            "progress_entries__quiz_answers__chosen_answers",
            "progress_entries__quiz_answers__correct_answers",
        )

    @patch("learner_api.learner_detail.prefetch_related_objects")
    @patch("learner_api.learner_detail.learner_profile_for_source", return_value=None)
    def test_skips_prefetch_when_the_learner_has_no_profile(self, _resolve_profile, prefetch):
        self.assertIsNone(_active_profile_for_source(SimpleNamespace(), 19))
        prefetch.assert_not_called()


class LearnerReflectionStatusTests(SimpleTestCase):
    def test_loads_all_statuses_in_one_read_without_running_schema_ddl(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            ("quiz", "quiz-68", "accepted"),
            ("reading", "COMP-1", "submitted_for_tutor_review"),
        ]
        connection = MagicMock()
        connection.cursor.return_value = cursor
        request = RequestFactory().get(
            "/learner_api/reflection/submissions/",
            {"learnerKind": "commercial", "learnerId": "19"},
        )

        with patch("learner_api.reflection_submissions.connections", {"enrolment": connection}), \
                patch("login.permissions.authenticate_request", return_value=SimpleNamespace(role="learner", subject_id=19)):
            response = get_reflection_submission(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.content)["statuses"],
            [
                {"activityType": "quiz", "activityId": "quiz-68", "status": "accepted"},
                {
                    "activityType": "reading",
                    "activityId": "COMP-1",
                    "status": "submitted_for_tutor_review",
                },
            ],
        )
        sql = " ".join(call.args[0].lower() for call in cursor.execute.call_args_list)
        self.assertNotIn("create ", sql)
        self.assertNotIn("alter ", sql)
        self.assertNotIn("drop ", sql)


class AssignmentFormReadinessTests(SimpleTestCase):
    def _connection_with_payload(self, payload):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        cursor.fetchone.return_value = None if payload is None else (payload,)
        connection = MagicMock()
        connection.cursor.return_value = cursor
        return connection, cursor

    def test_requires_all_three_written_sections(self):
        connection, _cursor = self._connection_with_payload({
            "assignmentAnswer": "My answer",
            "whatYouLearned": "What I learned",
            "businessImpact": "",
        })

        with patch("learner_api.components.connections", {"enrolment": connection}):
            self.assertFalse(_assignment_form_ready("COMP-1", "commercial", "19"))

    def test_accepts_complete_json_assignment_payload(self):
        connection, cursor = self._connection_with_payload(json.dumps({
            "assignmentAnswer": "My answer",
            "whatYouLearned": "What I learned",
            "businessImpact": "Faster customer response",
        }))

        with patch("learner_api.components.connections", {"enrolment": connection}), \
                patch("learner_api.monthly_assignment.assignment_checks", return_value=[{"passed": True}]) as checks:
            self.assertTrue(_assignment_form_ready("COMP-1", "commercial", "19"))

        self.assertEqual(checks.call_args.args[0]["activityId"], "COMP-1")
        self.assertEqual(checks.call_args.args[0]["learnerId"], "19")

        params = cursor.execute.call_args.args[1]
        self.assertEqual(params, ["commercial", "19", "COMP-1", ""])
        self.assertIn("assignment_topic_id = %s", cursor.execute.call_args.args[0])


class LearnerProgressionTests(SimpleTestCase):
    def _learner(self, status, start_date="2026-09-01", learner_type="apprenticeship", plan=None):
        return SimpleNamespace(
            pk=19,
            learner_type=learner_type,
            programme_status=status,
            start_date=start_date,
            learning_plan=plan,
            training_plan=None,
            save=Mock(),
        )

    @patch("learner_api.learner_progression.compliance_documents_complete", return_value=True)
    @patch("learner_api.learner_progression._programme_start_date", return_value=date(2026, 9, 1))
    @patch("learner_api.learner_progression.timezone.localdate", return_value=date(2026, 8, 8))
    def test_completed_documents_move_delivery_learner_to_ready_to_enrol(self, _today, _start, complete):
        learner = self._learner("Delivery")

        self.assertEqual(advance_learner(learner), READY_TO_ENROL_STATUS)
        self.assertEqual(learner.programme_status, READY_TO_ENROL_STATUS)
        learner.save.assert_called_once_with(update_fields=["programme_status"])
        complete.assert_called_once_with("apprenticeship", 19)

    @patch("learner_api.active_users.sync_active_user")
    @patch("learner_api.learner_progression._programme_start_date", return_value=date(2026, 8, 8))
    @patch("learner_api.learner_progression.timezone.localdate", return_value=date(2026, 8, 8))
    def test_ready_learner_becomes_active_on_their_start_date(self, _today, _start, sync):
        learner = self._learner(READY_TO_ENROL_STATUS)

        self.assertEqual(advance_learner(learner), ACTIVE_STATUS)
        self.assertEqual(learner.programme_status, ACTIVE_STATUS)
        learner.save.assert_called_once_with(update_fields=["programme_status"])
        sync.assert_called_once_with(learner)

    def test_parses_legacy_text_start_dates(self):
        self.assertEqual(_as_date("2026-08-08"), date(2026, 8, 8))
        self.assertEqual(_as_date("2026-08-08T09:30:00Z"), date(2026, 8, 8))

    @patch("learner_api.active_users.sync_active_user")
    @patch("learner_api.learner_progression._programme_start_date", return_value=date(2026, 8, 8))
    @patch("learner_api.learner_progression.timezone.localdate", return_value=date(2026, 8, 8))
    def test_commercial_waits_for_an_assigned_plan_even_after_start_date(self, _today, _start, sync):
        learner = self._learner("Delivery", learner_type="commercial")

        self.assertIsNone(advance_learner(learner))
        self.assertEqual(learner.programme_status, "Delivery")
        sync.assert_not_called()

    @patch("learner_api.active_users.sync_active_user")
    @patch("learner_api.learner_progression._programme_start_date", return_value=date(2026, 8, 8))
    @patch("learner_api.learner_progression.timezone.localdate", return_value=date(2026, 8, 8))
    def test_commercial_becomes_active_after_start_date_with_assigned_plan(self, _today, _start, sync):
        learner = self._learner("Delivery", learner_type="commercial", plan=[{"moduleId": "mod-1"}])

        with patch("learner_api.learner_progression._has_platform_invitation", return_value=True):
            self.assertEqual(advance_learner(learner), "Active")
        self.assertEqual(learner.programme_status, "Active")
        sync.assert_called_once_with(learner)

    @patch("learner_api.active_users.sync_active_user")
    @patch("learner_api.learner_progression._has_assigned_learning_plan", return_value=True)
    @patch("learner_api.learner_progression._programme_start_date", return_value=date(2026, 8, 8))
    @patch("learner_api.learner_progression.timezone.localdate", return_value=date(2026, 8, 8))
    def test_already_active_commercial_learner_is_not_resynced_on_every_read(self, _today, _start, _plan, sync):
        # Regression: re-reading an already-active commercial learner (e.g.
        # every GET /learner_api/enrolment-users/) used to call
        # sync_active_user() unconditionally on every call, whether or not a
        # transition happened — a full atomic LearnerProfile upsert per
        # learner, per read. It must only fire on an actual status change.
        learner = self._learner("Active", learner_type="commercial", plan=[{"moduleId": "mod-1"}])

        self.assertIsNone(advance_learner(learner))
        self.assertEqual(learner.programme_status, "Active")
        learner.save.assert_not_called()
        sync.assert_not_called()


class TrainingPlanHydrationTests(SimpleTestCase):
    @patch("learner_api.active_users.connections")
    def test_selected_modules_are_expanded_with_authored_weeks_and_components(self, connections):
        cursor = MagicMock()
        connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [
            ("mod-1", "Module 1", None, None, None, None, None, 11, "Week 1", 1, 101, "Watch this", "video", Decimal("1.25")),
            ("mod-1", "Module 1", None, None, None, None, None, 11, "Week 1", 1, 102, "Quiz", "quiz", Decimal("0.50")),
            ("mod-1", "Module 1", None, None, None, None, None, 12, "Week 2", 2, 103, "Read this", "reading", None),
        ]

        result = hydrate_training_plan([{"moduleId": "mod-1", "moduleTitle": "Old title"}])

        self.assertEqual(result, [{
            "moduleId": "mod-1",
            "moduleTitle": "Module 1",
            "startDate": "", "endDate": "",
            "weeks": [
                {"weekId": "11", "weekTitle": "Week 1", "sessionDate": "", "components": [
                    {"componentId": "101", "componentTitle": "Watch this", "type": "video", "expectedOtjh": 1.25},
                    {"componentId": "102", "componentTitle": "Quiz", "type": "quiz", "expectedOtjh": 0.5},
                ]},
                {"weekId": "12", "weekTitle": "Week 2", "sessionDate": "", "components": [
                    {"componentId": "103", "componentTitle": "Read this", "type": "reading", "expectedOtjh": None},
                ]},
            ],
        }])


class LearnerActivityFeedProjectionTests(SimpleTestCase):
    def test_returns_empty_activity_feed_when_no_progress_is_prefetched(self):
        learner = LearnerProfile()
        learner._prefetched_objects_cache = {"progress_entries": []}

        self.assertEqual(learner.activity_feed_entries(), [])

    @patch("learner_api.models._progress_entry_activity")
    def test_uses_prefetched_progress_entries_when_available(self, project_entry):
        learner = LearnerProfile()
        learner._prefetched_objects_cache = {
            "progress_entries": [
                SimpleNamespace(feed_kind="video", feed_key="keep"),
                SimpleNamespace(feed_kind="", feed_key="skip"),
            ]
        }
        project_entry.side_effect = lambda entry: {"kind": entry.feed_key, "at": entry.feed_key}

        self.assertEqual(
            learner.activity_feed_entries(newest_first=True),
            [{"kind": "keep", "at": "keep"}],
        )
        project_entry.assert_called_once()


class ProgressActivityProjectionTests(SimpleTestCase):
    def test_projects_feed_fields_from_the_progress_entry(self):
        occurred_at = datetime(2026, 8, 2, 10, 30, tzinfo=timezone.utc)
        entry = SimpleNamespace(
            kind="video",
            feed_kind="video",
            feed_action="Watched video",
            feed_title="Project planning",
            feed_detail="2h",
            feed_occurred_at=occurred_at,
            component_ref="COMP-1",
            component_type="video",
            quiz_ref=None,
            module_title="Module 1",
            week_title="Week 1",
            component_title="Project planning",
            passed=None,
            submitted_at=occurred_at,
            reported_time="2h",
        )

        self.assertEqual(
            _progress_entry_activity(entry),
            {
                "kind": "video",
                "action": "Watched video",
                "title": "Project planning",
                "detail": "2h",
                "componentId": "COMP-1",
                "componentType": "video",
                "quizId": None,
                "module": "Module 1",
                "week": "Week 1",
                "passed": None,
                "at": "2026-08-02T10:30:00+00:00",
            },
        )


class LearnerProfileResolutionTests(SimpleTestCase):
    """How a Created_users row is matched to its "Learner".learners profile.

    ``enrolment_id`` is the real link and is tried first. Email is only a
    fallback for profiles that predate that column — it is a poor key, because a
    corrected address breaks it and two people sharing one collide, which is
    exactly why the explicit column exists.
    """

    def setUp(self):
        patcher = patch('learner_api.identity.EnrolmentUser.all_learners.filter')
        self.source_filter = patcher.start()
        self.twins = self.source_filter.return_value.exclude.return_value.values_list.return_value
        self.twins.__getitem__.return_value = []
        self.addCleanup(patcher.stop)

    @staticmethod
    def _returns(*results):
        """Make ``LearnerProfile.objects.filter(...).first()`` yield each result
        in turn, so the *order* of the lookups can be asserted."""
        calls = []

        def fake_filter(**kwargs):
            calls.append(kwargs)
            outcome = results[len(calls) - 1] if len(calls) <= len(results) else None
            return SimpleNamespace(first=lambda: outcome)

        return fake_filter, calls

    @patch("learner_api.identity.LearnerProfile.objects.filter")
    def test_prefers_the_explicit_enrolment_link(self, profile_filter):
        expected = SimpleNamespace(id=2, enrolment_id=19)
        fake, calls = self._returns(expected)
        profile_filter.side_effect = fake

        result = learner_profile_for_source(
            SimpleNamespace(email=" Learner@Example.com "), 19, active_only=True,
        )

        self.assertIs(result, expected)
        # One lookup, on the link — email is never consulted when it resolves.
        self.assertEqual(calls, [{"enrolment_id": 19, "lifecycle_status": "active"}])

    @patch("learner_api.identity.LearnerProfile.objects.filter")
    def test_falls_back_to_email_when_the_link_is_not_set(self, profile_filter):
        """Profiles created before enrolment_id existed still resolve."""
        expected = SimpleNamespace(id=2, enrolment_id=None, save=lambda **kw: None)
        fake, calls = self._returns(None, expected)
        profile_filter.side_effect = fake

        result = learner_profile_for_source(
            SimpleNamespace(email=" Learner@Example.com "), 19, active_only=True,
        )

        self.assertIs(result, expected)
        self.assertEqual(calls, [
            {"enrolment_id": 19, "lifecycle_status": "active"},
            {"email__iexact": "Learner@Example.com", "enrolment_id__isnull": True, "lifecycle_status": "active"},
        ])

    @patch("learner_api.identity.LearnerProfile.objects.filter")
    def test_an_email_match_repairs_the_missing_link(self, profile_filter):
        """Self-healing: the inferred link is written back, so the fallback
        empties itself instead of being consulted forever."""
        saved = {}
        expected = SimpleNamespace(
            id=2, enrolment_id=None,
            save=lambda **kwargs: saved.update(kwargs),
        )
        fake, _calls = self._returns(None, expected)
        profile_filter.side_effect = fake

        learner_profile_for_source(
            SimpleNamespace(email="learner@example.com"), 19, active_only=True,
        )

        self.assertEqual(expected.enrolment_id, 19)
        self.assertEqual(saved, {"update_fields": ["enrolment_id"]})

    @patch("learner_api.identity.LearnerProfile.objects.filter")
    def test_falls_back_to_source_id_only_when_source_has_no_email(self, profile_filter):
        expected = SimpleNamespace(id=19)
        fake, calls = self._returns(None, expected)
        profile_filter.side_effect = fake

        result = learner_profile_for_source(
            SimpleNamespace(email="  "), 19, active_only=True,
        )

        self.assertIs(result, expected)
        self.assertEqual(calls, [
            {"enrolment_id": 19, "lifecycle_status": "active"},
            {"pk": 19, "enrolment_id__isnull": True, "lifecycle_status": "active"},
        ])

    @patch("learner_api.identity.LearnerProfile.objects.filter")
    def test_does_not_cross_link_an_unmatched_email_by_source_id(self, profile_filter):
        """The safety property: when a learner HAS an email and it matches
        nobody, resolution stops. Falling through to the primary key would hand
        back whichever unrelated profile happened to share that number — the two
        tables' id sequences are independent."""
        fake, calls = self._returns(None, None)
        profile_filter.side_effect = fake

        result = learner_profile_for_source(
            SimpleNamespace(email="missing@example.com"), 19, active_only=True,
        )

        self.assertIsNone(result)
        # Never a third, pk-based lookup.
        self.assertEqual(calls, [
            {"enrolment_id": 19, "lifecycle_status": "active"},
            {"email__iexact": "missing@example.com", "enrolment_id__isnull": True, "lifecycle_status": "active"},
        ])

    @patch('learner_api.identity.LearnerProfile.objects.filter')
    def test_duplicate_source_email_does_not_claim_unlinked_work(self, profile_filter):
        profile = SimpleNamespace(id=2, enrolment_id=None, save=Mock())
        profile_filter.side_effect = self._returns(None, profile)[0]
        self.twins.__getitem__.return_value = [501]
        self.assertIsNone(learner_profile_for_source(SimpleNamespace(email='shared@example.com'), 500))
        profile.save.assert_not_called()

    @patch('learner_api.identity.LearnerProfile.objects.filter')
    def test_a_learner_enrolled_twice_is_named_in_the_log_with_both_ids(self, profile_filter):
        """The refusal above is invisible to callers: they see only a missing
        profile and report it as a missing coach or an inactive learner. Both
        ids have to reach the log, or the real fault — one person enrolled
        twice — has to be found by hand."""
        profile_filter.side_effect = self._returns(None, SimpleNamespace(id=2, enrolment_id=None, save=Mock()))[0]
        self.twins.__getitem__.return_value = [501, 502]

        with self.assertLogs('learner_api.identity', level='WARNING') as logged:
            learner_profile_for_source(SimpleNamespace(email='shared@example.com'), 500)

        message = logged.output[0]
        self.assertIn('shared@example.com', message)
        for pk in ('500', '501', '502'):
            self.assertIn(pk, message)
        self.assertIn('merge_duplicate_enrolments', message)


class AttendanceSummaryTests(SimpleTestCase):
    def test_summarizes_session_rows_for_the_learner_page(self):
        updated_at = datetime(2026, 7, 21, 14, 28, tzinfo=timezone.utc)
        common = {
            'learner_id': 2,
            'learner_name': 'Test Learner',
            'learner_email': 'learner@example.com',
            'catchup_completed': False,
            'updated_at': updated_at,
        }
        rows = [
            {**common, 'session_date': date(2026, 7, 15), 'attendance_status': 'absent', 'minutes_late': 0},
            {**common, 'session_date': date(2026, 7, 8), 'attendance_status': 'absent', 'minutes_late': 0, 'catchup_completed': True},
            {**common, 'session_date': date(2026, 7, 1), 'attendance_status': 'present', 'minutes_late': 12},
            {**common, 'session_date': date(2026, 6, 24), 'attendance_status': 'present', 'minutes_late': 0},
            {**common, 'session_date': date(2026, 6, 17), 'attendance_status': 'present', 'minutes_late': 0},
        ]

        summary = _summarize_attendance(rows)

        self.assertEqual(summary['sessions'], 5)
        self.assertEqual(summary['present'], 3)
        self.assertEqual(summary['absent'], 2)
        self.assertEqual(summary['late'], 1)
        self.assertEqual(summary['catchup'], 1)
        self.assertEqual(summary['attendanceRate'], 60)
        self.assertEqual(summary['risk'], 'red')
        self.assertEqual(summary['consecutiveMissed'], 2)
        self.assertEqual(summary['lastSessionDate'], '2026-07-15')
        self.assertEqual(summary['updatedAt'], '2026-07-21T14:28:00+00:00')

    def test_returns_none_without_session_rows(self):
        self.assertIsNone(_summarize_attendance([]))

    @override_settings(TIME_ZONE='Europe/London')
    def test_only_counts_sessions_up_to_now_in_the_business_timezone(self):
        common = {
            'learner_id': 2, 'learner_name': 'Test Learner',
            'learner_email': 'learner@example.com', 'minutes_late': 0,
            'catchup_completed': False, 'updated_at': None,
        }
        now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)  # 13:00 UK
        rows = [
            {**common, 'session_date': date(2026, 9, 12), 'attendance_status': 'present'},
            {**common, 'session_date': date(2026, 9, 13), 'session_start_time': time(12, 30), 'attendance_status': 'late'},
            {**common, 'session_date': date(2026, 9, 13), 'session_start_time': time(13), 'attendance_status': 'absent'},
            {**common, 'session_date': date(2026, 9, 13), 'session_start_time': time(14), 'attendance_status': 'absent'},
            {**common, 'session_date': date(2026, 9, 14), 'attendance_status': 'present', 'source': 'microsoft-teams'},
        ]
        summary = _summarize_attendance(rows, now=now)
        self.assertEqual((summary['present'], summary['sessions'], summary['attendanceRate']), (2, 3, 67))
        self.assertEqual(len(summary['sessionHistory']), 3)
        self.assertEqual(summary['lastSessionDate'], '2026-09-13')
        self.assertEqual(summary['source'], 'kbc-attendance')
        self.assertIsNone(_summarize_attendance(rows[3:], now=now))

    def test_late_status_counts_as_attended(self):
        rows = [
            {
                'learner_id': 2,
                'learner_name': 'Test Learner',
                'learner_email': 'learner@example.com',
                'session_date': date(2026, 7, 1),
                'attendance_status': 'late',
                'minutes_late': 15,
                'catchup_completed': False,
                'updated_at': None,
            },
            {
                'learner_id': 2,
                'learner_name': 'Test Learner',
                'learner_email': 'learner@example.com',
                'session_date': date(2026, 7, 8),
                'attendance_status': 'absent',
                'minutes_late': 0,
                'catchup_completed': False,
                'updated_at': None,
            },
        ]

        summary = _summarize_attendance(rows)

        self.assertEqual(summary['present'], 1)
        self.assertEqual(summary['late'], 1)
        self.assertEqual(summary['absent'], 1)
        self.assertEqual(summary['attendanceRate'], 50)

    def test_non_attendance_workflow_status_does_not_dilute_rate(self):
        common = {
            'learner_id': 2,
            'learner_name': 'Test Learner',
            'learner_email': 'learner@example.com',
            'session_date': date(2026, 7, 1),
            'minutes_late': 0,
            'catchup_completed': False,
            'updated_at': None,
        }

        summary = _summarize_attendance([
            {**common, 'attendance_status': 'present'},
            {**common, 'attendance_status': 'catchup'},
        ])

        self.assertEqual(summary['sessions'], 1)
        self.assertEqual(summary['attendanceRate'], 100)


class KbcAbsenceSessionTests(SimpleTestCase):
    def setUp(self):
        self.learner = SimpleNamespace(
            aptem_id='92',
            username='Test Learner',
            email='learner@example.com',
        )
        self.absent_row = {
            'session_id': '92_2026-08-14_project_management',
            'session_date': date(2026, 8, 14),
            'attendance_status': 'absent',
            'session_title': 'Ray-Project Management Office (PMO)',
            'session_type': 'KBC attendance',
            'session_start_time': None,
            'session_end_time': None,
            'coach_name': '',
            'module_title': 'Ray-Project Management Office (PMO)',
        }

    @patch('learner_api.absence_reports.lecture_register')
    def test_missed_session_dropdown_uses_absent_kbc_rows(self, fetch_rows):
        fetch_rows.return_value = [
            self.absent_row,
            {
                **self.absent_row,
                'session_id': '92_2026-08-21_project_management',
                'session_date': date(2026, 8, 21),
                'attendance_status': 'present',
            },
        ]

        sessions = _fetch_missed_sessions(self.learner, 272)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['sessionId'], self.absent_row['session_id'])
        self.assertEqual(sessions[0]['dateIso'], '2026-08-14')
        self.assertEqual(sessions[0]['title'], self.absent_row['session_title'])
        fetch_rows.assert_called_once_with(self.learner)

    @patch('learner_api.absence_reports.lecture_register')
    def test_submission_resolves_the_selected_kbc_session(self, fetch_rows):
        fetch_rows.return_value = [self.absent_row]

        attendance_id = _resolve_absent_attendance(
            self.learner,
            272,
            self.absent_row['session_id'],
            self.absent_row['session_title'].upper(),
            self.absent_row['session_date'],
            None,
        )

        self.assertEqual(
            attendance_id,
            _kbc_attendance_report_id(self.absent_row['session_id']),
        )

    def test_kbc_report_id_is_stable_and_fits_a_signed_bigint(self):
        first = _kbc_attendance_report_id(self.absent_row['session_id'])
        second = _kbc_attendance_report_id(self.absent_row['session_id'])

        self.assertEqual(first, second)
        self.assertGreaterEqual(first, KBC_ATTENDANCE_ID_BASE)
        self.assertLess(first, KBC_ATTENDANCE_ID_BASE + KBC_ATTENDANCE_ID_RANGE)
        self.assertLessEqual(first, 9_223_372_036_854_775_807)


class TeamsAttendanceEligibilityTests(SimpleTestCase):
    # Eligibility moved off the session's own invite list and onto the learners
    # actually assigned to the session's module (commit 38a6d8b5 replaced
    # _session_expected_emails with _module_expected_emails). The cases below
    # are the same three questions asked of the successor.
    def test_expects_every_learner_assigned_to_the_sessions_module(self):
        session = SimpleNamespace(module_catalogue_id='MOD-1')
        assigned = {'MOD-1': {'learner@example.com', 'second@example.com'}}

        self.assertEqual(
            _module_expected_emails(session, assigned),
            {'learner@example.com', 'second@example.com'},
        )

    def test_a_module_with_nobody_assigned_expects_nobody(self):
        self.assertEqual(
            _module_expected_emails(SimpleNamespace(module_catalogue_id='MOD-1'), {}),
            set(),
        )
        self.assertEqual(
            _module_expected_emails(SimpleNamespace(module_catalogue_id=None), {'': set()}),
            set(),
        )

    def test_an_identity_filter_narrows_but_never_widens_the_expected_set(self):
        session = SimpleNamespace(module_catalogue_id='MOD-1')
        assigned = {'MOD-1': {'learner@example.com', 'second@example.com'}}

        self.assertEqual(
            _module_expected_emails(session, assigned, {'learner@example.com', 'stranger@example.com'}),
            {'learner@example.com'},
        )

    def test_tracks_first_join_and_last_leave_across_intervals(self):
        first, last = _attendance_interval_bounds([
            {
                'joinDateTime': '2026-07-01T09:15:00Z',
                'leaveDateTime': '2026-07-01T09:30:00Z',
            },
            {
                'joinDateTime': '2026-07-01T09:00:00Z',
                'leaveDateTime': '2026-07-01T10:00:00Z',
            },
        ])

        self.assertEqual(first.isoformat(), '2026-07-01T09:00:00+00:00')
        self.assertEqual(last.isoformat(), '2026-07-01T10:00:00+00:00')


class LearnerAttendanceEndpointTests(SimpleTestCase):
    @patch('learner_api.attendance_confirmation.read_confirmations', return_value={})
    @patch('learner_api.attendance_lectures.read_native_occurrences', return_value=[])
    @patch('learner_api.attendance.fetch_verified_teams_attendance_rows', return_value=[])
    @patch('learner_api.attendance.fetch_kbc_attendance_rows', return_value=[])
    def test_reads_kbc_register_with_the_enrolments_aptem_id(self, fetch_rows, fetch_teams, scheduled, confirmations):
        enrolment_id, learner_profile_id = 19, 315
        source = SimpleNamespace(
            id=enrolment_id,
            username='Test Learner',
            email='learner@example.com',
            aptem_id='92',
        )
        source_model = MagicMock()
        source_model.DoesNotExist = type('SourceDoesNotExist', (Exception,), {})
        source_model.all_learners.only.return_value.get.return_value = source

        with patch('learner_api.catchup_outcomes.sync_catchup_outcomes') as sync, \
             patch('learner_api.attendance_lectures._completed_catchup_occurrences', return_value=set()), \
             patch('learner_api.models.LearnerProfile.objects') as profiles, \
             patch('coach_api.models.CoachAttendanceSourceAdjustment.objects') as adjustments, patch.dict(
            attendance_module.SOURCE_MODELS,
            {'apprenticeship': source_model},
            clear=True,
        ):
            profiles.filter.return_value.values_list.return_value = [learner_profile_id]
            adjustments.filter.return_value = []
            response = learner_attendance.__wrapped__(
                RequestFactory().get('/learner_api/attendance/apprenticeship/19/'),
                'apprenticeship',
                19,
            )

        self.assertEqual(response.status_code, 200)
        profiles.filter.assert_called_once_with(enrolment_id=enrolment_id)
        profiles.filter.return_value.values_list.assert_called_once_with('id', flat=True)
        adjustments.filter.assert_called_once_with(learner_id=learner_profile_id)
        sync.assert_called_once_with(learner_email=source.email)
        scheduled.assert_called_once_with(source, module_ids=None)
        confirmations.assert_called_once_with(enrolment_id)
        fetch_rows.assert_called_once_with(
            aptem_id='92',
            learner_id=19,
            learner_name='Test Learner',
            learner_email='learner@example.com',
        )

    @patch('learner_api.attendance.fetch_verified_teams_attendance_rows')
    @patch('learner_api.attendance.fetch_kbc_attendance_rows', return_value=[])
    def test_kbc_source_uses_only_aptem_register(self, fetch_rows, fetch_teams):
        source = SimpleNamespace(
            id=19,
            username='Test Learner',
            email='learner@example.com',
            aptem_id='92',
        )
        source_model = MagicMock()
        source_model.DoesNotExist = type('SourceDoesNotExist', (Exception,), {})
        source_model.all_learners.only.return_value.get.return_value = source

        with patch.dict(attendance_module.SOURCE_MODELS, {'apprenticeship': source_model}, clear=True):
            response = learner_attendance.__wrapped__(
                RequestFactory().get('/learner_api/attendance/apprenticeship/19/?source=kbc'),
                'apprenticeship',
                19,
            )

        self.assertEqual(response.status_code, 200)
        fetch_rows.assert_called_once_with(
            aptem_id='92', learner_id=19,
            learner_name='Test Learner', learner_email='learner@example.com',
        )
        fetch_teams.assert_not_called()

    @patch('learner_api.attendance.fetch_kbc_attendance_rows')
    def test_kbc_source_skips_learners_without_an_aptem_id(self, fetch_rows):
        source = SimpleNamespace(id=19, username='Test Learner', email='learner@example.com', aptem_id=None)
        source_model = MagicMock()
        source_model.DoesNotExist = type('SourceDoesNotExist', (Exception,), {})
        source_model.all_learners.only.return_value.get.return_value = source

        with patch.dict(attendance_module.SOURCE_MODELS, {'apprenticeship': source_model}, clear=True):
            response = learner_attendance.__wrapped__(
                RequestFactory().get('/learner_api/attendance/apprenticeship/19/?source=kbc'),
                'apprenticeship',
                19,
            )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'attendance': None})
        fetch_rows.assert_not_called()


class TeamsAttendanceSyncTests(SimpleTestCase):
    def setUp(self):
        shared = patch('curriculum_api.shared_session_register.shared_register_rows', return_value=[])
        self.shared_rows = shared.start()
        self.addCleanup(shared.stop)

    @patch('learner_api.teams_attendance.ensure_teams_attendance_reporting_columns')
    @patch('learner_api.teams_attendance._verified_occurrence_ids', return_value=[])
    @patch('learner_api.teams_attendance.fetch_verified_teams_attendance_rows', return_value=[])
    @patch('learner_api.teams_attendance.router.db_for_write', return_value='default')
    def test_module_sync_hides_stale_rows_even_when_the_report_was_removed(
        self,
        _database,
        _fetch_rows,
        _occurrences,
        ensure_schema,
    ):
        cursor = MagicMock()
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        connection = MagicMock()
        connection.cursor.return_value = cursor_context

        with patch.object(
            teams_attendance_module,
            'connections',
            {'default': connection},
        ), patch.object(
            teams_attendance_module.transaction,
            'atomic',
            side_effect=lambda **_kwargs: nullcontext(),
        ):
            count = sync_verified_teams_attendance_reporting(module_refs=['MOD-1'])

        self.assertEqual(count, 0)
        # Synchronization validates existing columns; it must never run schema DDL.
        ensure_schema.assert_not_called()
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any('SELECT attended_seconds, occurrence_id, is_expected, catchup_completed' in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().upper().startswith(('ALTER ', 'CREATE ', 'DROP ')) for sql in statements))
        cursor.executemany.assert_not_called()
        sql, params = cursor.execute.call_args.args
        self.assertIn('module_catalogue_id = ANY(%s)', sql)
        self.assertEqual(params, [['MOD-1'], []])

    def test_shared_only_sync_persists_the_module_register_without_duplicate_rewards(self):
        rows = [{'occurrence_id': 'O', 'module_catalogue_id': 'SHARED', 'learner_profile_id': 7}]
        self.shared_rows.return_value = rows
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        with patch.object(teams_attendance_module, 'connections', {'default': connection}), \
             patch.object(teams_attendance_module.router, 'db_for_write', return_value='default'), \
             patch.object(teams_attendance_module, 'fetch_verified_teams_attendance_rows', return_value=[]), \
             patch.object(teams_attendance_module, '_verified_occurrence_ids', return_value=[]), \
             patch.object(teams_attendance_module.transaction, 'atomic', side_effect=lambda **kwargs: nullcontext()), \
             patch.object(teams_attendance_module, 'upsert_live_session_register') as save, \
             patch.object(teams_attendance_module, '_award_attendance_points') as award:
            self.assertEqual(sync_verified_teams_attendance_reporting(module_refs=['SHARED']), 1)
        save.assert_called_once_with(cursor, rows)
        cursor.executemany.assert_not_called()  # Shared deliveries do not duplicate the legacy report.
        award.assert_called_once_with([])
        self.shared_rows.assert_called_once_with(database='default', module_refs=['SHARED'],
                                                 learner_ids=None, learner_emails=None, all_learners=False)


class LearnerQuizModuleMatchingTests(SimpleTestCase):
    def test_matches_quiz_to_module_by_programme_and_module_name(self):
        quiz_record = {
            "module": "Risk Management",
            "programme_id": "PROG-1",
            "programme": "Fouda-Programme",
        }
        modules_by_id = {
            "MOD-RISK": {
                "module": "Risk Management",
                "programmeId": "PROG-1",
                "programme": "Fouda-Programme",
            },
            "MOD-OTHER": {
                "module": "Fouda-Module",
                "programmeId": "PROG-1",
                "programme": "Fouda-Programme",
            },
        }

        self.assertEqual(
            _matching_module_ids_for_quiz_record(quiz_record, modules_by_id, explicit_module_ids=set()),
            ["MOD-RISK"],
        )

    def test_explicit_module_assignments_win_over_metadata_guessing(self):
        quiz_record = {
            "module": "Risk Management",
            "programme_id": "PROG-1",
            "programme": "Fouda-Programme",
        }
        modules_by_id = {
            "MOD-RISK": {
                "module": "Risk Management",
                "programmeId": "PROG-1",
                "programme": "Fouda-Programme",
            },
        }

        self.assertEqual(
            _matching_module_ids_for_quiz_record(quiz_record, modules_by_id, explicit_module_ids={"MOD-RISK"}),
            ["MOD-RISK"],
        )


class ScriptedCursor:
    def __init__(self, results):
        self._results = list(results)
        self._current = []
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        self._current = self._results.pop(0) if self._results else []

    def fetchall(self):
        return list(self._current)

    def fetchone(self):
        return self._current[0] if self._current else None


class ScriptedConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self

    def __enter__(self):
        return self._cursor

    def __exit__(self, exc_type, exc, tb):
        return False


class LearnerReflectionQuestionTests(SimpleTestCase):
    def _resolved_component(self, reflection_question, reflection_required=True):
        cursor = ScriptedCursor([
            [("MOD-1", "Module 1")],
            [("WEEK-1", "MOD-1", "Week 1", 1, 0)],
            [(
                "COMP-1", "WEEK-1", "MOD-1", "quiz", "Quiz component", "",
                {"linkedQuizId": 7}, "", 0,
                [{"code": "K1", "weight": 10}], reflection_required, reflection_question, False,
            )],
            [],
            [(7, "Quiz title", 3, 30, "Minutes")],
        ])
        weeks = [{
            "module": "Module 1", "week": "Week 1",
            "moduleId": "MOD-1", "weekId": "WEEK-1",
        }]
        with patch("learner_api.learner_detail.connections", {"enrolment": ScriptedConnection(cursor)}):
            _modules, _weeks, components = _resolve_from_master(["Module 1"], weeks, [])
        return components[0]

    def test_exposes_custom_component_reflection_question(self):
        self.assertEqual(
            self._resolved_component("What changed in your workplace?")["reflectionQuestion"],
            "What changed in your workplace?",
        )

    def test_keeps_null_reflection_question_for_frontend_fallback(self):
        self.assertIsNone(self._resolved_component(None)["reflectionQuestion"])

    def test_exposes_whether_reflection_is_required(self):
        self.assertFalse(self._resolved_component(None, False)["reflectionRequired"])


class LearnerSoftDeletedCurriculumVisibilityTests(SimpleTestCase):
    def test_live_resolution_keeps_parent_deleted_rows_eligible_but_excludes_missing_rows(self):
        cursor = ScriptedCursor([[], [], []])
        weeks = [{
            "module": "Deleted module", "week": "Deleted week",
            "moduleId": "MOD-DELETED", "weekId": "WEEK-DELETED",
        }]
        components = [{
            "module": "Deleted module", "week": "Deleted week", "component": "Deleted assignment",
            "moduleId": "MOD-DELETED", "weekId": "WEEK-DELETED", "componentId": "COMP-DELETED",
        }]

        with patch("learner_api.learner_detail.connections", {"enrolment": ScriptedConnection(cursor)}):
            resolved = _resolve_from_master(["Deleted module"], weeks, components)

        self.assertEqual(resolved, ([], [], []))
        module_query, week_query, component_query = [" ".join(query.lower().split()) for query in cursor.queries[:3]]
        self.assertIn("m.deleted_at is null or m.deleted_via_parent is not null", module_query)
        self.assertIn("deleted_at is null or deleted_via_parent is not null", week_query)
        self.assertIn("deleted_at is null or deleted_via_parent is not null", component_query)


class LearnerWeekQuizVisibilityTests(SimpleTestCase):
    def test_explicit_module_links_surface_quiz_without_module_or_week_metadata(self):
        weeks = [{
            "module": "Fouda-Module",
            "week": "Week 1",
            "moduleId": "MOD-FOUDA",
            "weekId": "WEEK-1",
        }]
        components = []
        cursor = ScriptedCursor([
            [("MOD-FOUDA", "Fouda-Module", "PROG-1", "Fouda-Programme")],
            [],
            [(90, "MOD-FOUDA", "")],
            [(90, "", "test aya", 7, 60, "Minutes", "PROG-1", "Fouda-Programme", "")],
            [],
        ])

        with patch("learner_api.learner_detail.connections", {"enrolment": ScriptedConnection(cursor)}):
            next_weeks, next_components = _append_week_quizzes(weeks, components)

        self.assertEqual(len(next_weeks), 2)
        self.assertIn(
            {
                "module": "Fouda-Module",
                "week": "Module assessments",
                "moduleId": "MOD-FOUDA",
                "weekId": "quiz-module-assessments::MOD-FOUDA",
            },
            next_weeks,
        )
        self.assertEqual(len(next_components), 1)
        self.assertEqual(next_components[0]["component"], "Quiz · test aya")
        self.assertTrue(next_components[0]["isQuiz"])
        self.assertEqual(next_components[0]["moduleId"], "MOD-FOUDA")
        self.assertEqual(next_components[0]["weekId"], "quiz-module-assessments::MOD-FOUDA")
        self.assertEqual(next_components[0]["quizMeta"]["quizId"], 90)

    def legacy_test_explicit_module_week_links_place_quiz_into_the_real_week(self):
        weeks = [{
            "module": "Risk Management",
            "week": "Week 1",
            "moduleId": "MOD-RISK",
            "weekId": "WEEK-RISK-1",
        }]
        components = []
        cursor = ScriptedCursor([
            [("MOD-RISK", "Risk Management", "PROG-1", "Fouda-Programme")],
            [],
            [(92, "MOD-RISK", "WEEK-RISK-1")],
            [(92, "", "Aya test 2", 7, 60, "Minutes", "PROG-1", "Fouda-Programme", "")],
            [],
        ])

        with patch("learner_api.learner_detail.connections", {"enrolment": ScriptedConnection(cursor)}):
            next_weeks, next_components = _append_week_quizzes(weeks, components)

        self.assertEqual(next_weeks, weeks)
        self.assertEqual(len(next_components), 1)
        self.assertEqual(next_components[0]["component"], "Quiz Â· Aya test 2")
        self.assertEqual(next_components[0]["moduleId"], "MOD-RISK")
        self.assertEqual(next_components[0]["weekId"], "WEEK-RISK-1")
        self.assertEqual(next_components[0]["quizMeta"]["quizId"], 92)

    def test_explicit_module_week_links_place_quiz_into_the_real_week_v2(self):
        weeks = [{
            "module": "Risk Management",
            "week": "Week 1",
            "moduleId": "MOD-RISK",
            "weekId": "WEEK-RISK-1",
        }]
        components = []
        cursor = ScriptedCursor([
            [("MOD-RISK", "Risk Management", "PROG-1", "Fouda-Programme")],
            [],
            [(92, "MOD-RISK", "WEEK-RISK-1")],
            [(92, "", "Aya test 2", 7, 60, "Minutes", "PROG-1", "Fouda-Programme", "")],
            [],
        ])

        with patch("learner_api.learner_detail.connections", {"enrolment": ScriptedConnection(cursor)}):
            next_weeks, next_components = _append_week_quizzes(weeks, components)

        self.assertEqual(next_weeks, weeks)
        self.assertEqual(len(next_components), 1)
        self.assertEqual(next_components[0]["component"], _display_quiz_title("Aya test 2"))
        self.assertEqual(next_components[0]["moduleId"], "MOD-RISK")
        self.assertEqual(next_components[0]["weekId"], "WEEK-RISK-1")
        self.assertEqual(next_components[0]["quizMeta"]["quizId"], 92)


class LearnerKsbSnapshotTests(SimpleTestCase):
    def test_versioned_ksb_content_is_canonical_and_deduplicated(self):
        items = _canonical_ksb_items([
            {"code": " k1 ", "number": "1", "type": "Knowledge", "description": "First"},
            {"code": "K1", "number": "1", "type": "Knowledge", "description": "Duplicate"},
            {"code": "S2", "number": "2", "type": "Skills", "description": "Second"},
        ])

        self.assertEqual([item["code"] for item in items], ["K1", "S2"])
        self.assertEqual(items[0]["description"], "First")
        self.assertEqual(len(_ksb_version_hash(items)), 64)
        self.assertEqual(_ksb_version_hash(items), _ksb_version_hash(list(items)))

    def test_version_hash_changes_when_a_definition_changes(self):
        original = [{"code": "K1", "number": "1", "type": "Knowledge", "description": "Original"}]
        changed = [{**original[0], "description": "Updated"}]

        self.assertNotEqual(_ksb_version_hash(original), _ksb_version_hash(changed))

    def test_reported_minutes_treats_small_bare_numbers_as_hours_and_large_values_as_minutes(self):
        self.assertEqual(_reported_minutes("2"), 120.0)
        self.assertEqual(_reported_minutes("2h"), 120.0)
        self.assertEqual(_reported_minutes("1.5"), 90.0)
        self.assertEqual(_reported_minutes("120"), 120.0)
        self.assertEqual(_reported_minutes("120 min"), 120.0)

    def test_completed_hours_dedupes_repeated_otj_progress(self):
        progress = [
            {"kind": "video", "reportedTime": "2", "ksbs": ["K3.1", "S1.2"], "submittedAt": "2026-07-27T08:00:00Z"},
            {"kind": "video", "reportedTime": "2", "ksbs": ["S1.2", "K3.1"], "submittedAt": "2026-07-27T09:00:00Z"},
            {"kind": "video", "reportedTime": "1", "ksbs": ["K4"], "submittedAt": "2026-07-27T10:00:00Z"},
            {"kind": "component", "componentId": "component-1", "reportedTime": "3h"},
            {"kind": "component", "componentId": "component-1", "reportedTime": "3h"},
        ]

        self.assertEqual(completed_hours_from_progress(progress), "6")

    def test_completed_hours_uses_highest_reported_attempt_per_activity(self):
        progress = [
            {
                "kind": "video", "componentId": "component-1",
                "attempt": 1, "reportedTime": "1h", "submittedAt": "2026-07-27T08:00:00Z",
            },
            {
                "kind": "video", "componentId": "component-1",
                "attempt": 2, "reportedTime": "3h", "submittedAt": "2026-07-27T09:00:00Z",
            },
            {
                "kind": "component", "componentId": "component-2",
                "attempt": 1, "reportedTime": "2h", "submittedAt": "2026-07-27T10:00:00Z",
            },
        ]

        # Highest video attempt (3h) + the other activity (2h).
        self.assertEqual(completed_hours_from_progress(progress), "5")

    def test_completed_hours_include_actual_quiz_time(self):
        progress = [{"kind": "quiz", "quizId": "42", "reportedTime": "30 min"}]

        self.assertEqual(completed_hours_from_progress(progress), "0.5")

    def test_completed_hours_prefers_learner_reported_time_for_known_components(self):
        progress = [
            {"kind": "video", "componentId": "component-1", "reportedTime": "120"},
            {"kind": "component", "componentId": "component-2", "reportedTime": "5"},
        ]
        components = [
            {"componentId": "component-1", "expectedOtjh": 1.5},
            {"componentId": "component-2", "expectedOtjh": 2},
        ]

        # Learner input is authoritative: 120 is 120 minutes and 5 is 5 hours.
        self.assertEqual(completed_hours_from_progress(progress, components), "7")

    def test_completed_hours_prefers_manual_time_spent_input(self):
        progress = [{
            "kind": "video", "componentId": "component-1",
            "reportedTime": "60", "claimedSeconds": 7200,
            "verifiedSeconds": 11,
            "timeTrackingSource": "signed_session_capped_active_playback:input",
        }]

        # The explicit 2h Time spent input wins over the default reported 60m.
        self.assertEqual(completed_hours_from_progress(progress), "2")

    def test_completed_hours_recognises_existing_capped_manual_input(self):
        progress = [{
            "kind": "video", "componentId": "component-1",
            "reportedTime": "60", "claimedSeconds": 5400,
            "verifiedSeconds": 11,
            "timeTrackingSource": "signed_session_capped_active_playback",
        }]

        self.assertEqual(completed_hours_from_progress(progress), "1.5")

    def test_completed_hours_uses_verified_historical_import_time(self):
        progress = [{
            "kind": "component", "componentId": "component-1",
            "reportedTime": "", "claimedSeconds": 26418634,
            "verifiedSeconds": 7200,
            "timeTrackingSource": "historical_import",
        }]

        # An automatic import's raw duration is not a learner-entered value;
        # only the verified duration is eligible for OTJH credit.
        self.assertEqual(completed_hours_from_progress(progress), "2")

    def test_new_platform_activity_appends_without_double_counting_imported_otjh(self):
        progress = [
            {
                "kind": "component", "componentId": "component-1",
                "attempt": 1, "reportedTime": "", "verifiedSeconds": 7200,
                "timeTrackingSource": "historical_import",
                "submittedAt": "2025-01-01T09:00:00Z",
            },
            {
                "kind": "component", "componentId": "component-1",
                "attempt": 2, "reportedTime": "3h", "verifiedSeconds": 1800,
                "timeTrackingSource": "signed_session_capped_active_playback:input",
                "submittedAt": "2026-09-10T09:00:00Z",
            },
        ]

        # Both audit rows remain in history, while the activity contributes one
        # OTJH value (the highest defensible attempt) to the learner total.
        self.assertEqual(len(progress), 2)
        self.assertEqual(completed_hours_from_progress(progress), "3")

    def test_completed_hours_counts_reported_time_before_tracked_time(self):
        # The learner entered 2h for both activities, so that input is used even
        # when the automatic timer captured a much shorter duration.
        progress = [
            {
                "kind": "component", "componentId": "component-1",
                "expectedOtjh": 2, "reportedTime": "2h", "verifiedSeconds": 1800,
            },
            {
                "kind": "component", "componentId": "component-2",
                "expectedOtjh": 2, "reportedTime": "2h", "verifiedSeconds": 33,
            },
        ]
        components = [
            {"componentId": "component-1", "expectedOtjh": 2},
            {"componentId": "component-2", "expectedOtjh": 2},
        ]

        self.assertEqual(completed_hours_from_progress(progress, components), "4")

    def test_completed_hours_counts_reported_time_when_tracked_time_is_zero(self):
        progress = [{
            "kind": "component", "componentId": "component-1",
            "expectedOtjh": 3, "reportedTime": "3h", "verifiedSeconds": 0,
        }]

        self.assertEqual(completed_hours_from_progress(progress), "3")

    def test_completed_hours_uses_tracked_time_when_no_time_was_reported(self):
        progress = [{
            "kind": "component", "componentId": "component-1",
            "expectedOtjh": 3, "reportedTime": "", "verifiedSeconds": 1800,
        }]

        self.assertEqual(completed_hours_from_progress(progress), "0.5")

    def test_coerce_ksb_items_parses_profile_json_payload(self):
        items = _coerce_ksb_items(
            '[{"code":"1","type":"K","title":"Knowledge 1","description":"Knowledge 1","displayOrder":2},'
            '{"code":"2","type":"S","title":"Skill 2","description":"Skill 2","displayOrder":1}]'
        )

        self.assertEqual(
            items,
            [
                {"code": "S2", "number": "2", "type": "Skills", "description": "Skill 2"},
                {"code": "K1", "number": "1", "type": "Knowledge", "description": "Knowledge 1"},
            ],
        )

    def test_to_learner_detail_exposes_progress_ksb_codes_from_all_progress_types(self):
        source = SimpleNamespace(
            id=42,
            username="Test Learner",
            email="learner@example.com",
            phone_number="",
            programme="Programme A",
            programme_status="Active",
            cohort="Cohort A",
            group="Group 1",
            training_plan=[],
            employer="Employer A",
            line_manager="Manager A",
        )
        learner_profile = SimpleNamespace(
            training_plan_progress=[
                {"kind": "quiz", "ksbs": ["K1"]},
                {"kind": "video", "ksbs": ["S2"]},
                {"kind": "component", "ksbs": ["B3", "K1"]},
            ],
            activity_feed_entries=lambda newest_first=False: [],
            ksbs=[{"code": "K1", "number": "1", "type": "Knowledge", "description": "Knowledge 1"}],
        )

        with patch("learner_api.mappers._component_marking_statuses", return_value={}), \
                patch("learner_api.active_users.cohort_dates", return_value=(None, None)):
            detail = to_learner_detail(source, learner_profile)

        self.assertEqual(detail["progressKsbCodes"], ["B3", "K1", "S2"])

    @patch("learner_api.active_users.replace_learner_ksbs")
    @patch("learner_api.active_users._fetch_ksb_items", return_value=[])
    def test_refresh_learner_ksb_snapshot_preserves_existing_rows_when_lookup_is_empty(self, fetch_ksbs, replace_ksbs):
        learner = SimpleNamespace(id=42)
        source = SimpleNamespace(programme="Programme A", training_plan=[])

        result = refresh_learner_ksb_snapshot(learner, source, training_plan=[])

        self.assertEqual(result, [])
        fetch_ksbs.assert_called_once_with("Programme A", training_plan=[])
        replace_ksbs.assert_not_called()

    @patch("learner_api.active_users._fetch_ksb_items_from_profile_source")
    @patch("learner_api.active_users._resolve_ksb_profile_source_id")
    @patch("learner_api.active_users._fetch_ksb_items_from_plan_mappings")
    @patch("learner_api.active_users._fetch_ksb_items_for_programme", return_value=[])
    @patch("learner_api.active_users._resolve_programme_id", return_value="PROG-1")
    def test_fetch_ksb_items_prefers_profile_source_before_plan_mappings(
        self,
        resolve_programme_id,
        fetch_for_programme,
        fetch_from_plan_mappings,
        resolve_profile_source,
        fetch_from_profile_source,
    ):
        fetch_from_profile_source.return_value = [
            {"code": "K2", "number": "2", "type": "Knowledge", "description": "Knowledge 2"},
        ]
        resolve_profile_source.return_value = "KSBP-1"

        result = _fetch_ksb_items("Programme A", training_plan=[{"moduleId": "MOD-1"}])

        self.assertEqual(result, fetch_from_profile_source.return_value)
        resolve_programme_id.assert_called_once_with("Programme A", training_plan=[{"moduleId": "MOD-1"}])
        fetch_for_programme.assert_not_called()
        resolve_profile_source.assert_called_once_with(
            programme_id="PROG-1",
            programme="Programme A",
            training_plan=[{"moduleId": "MOD-1"}],
        )
        fetch_from_profile_source.assert_called_once_with("KSBP-1")
        fetch_from_plan_mappings.assert_not_called()

    @patch("learner_api.active_users._fetch_ksb_items_from_profile_source", return_value=[
        {"code": "K2", "number": "2", "type": "Knowledge", "description": "Knowledge 2"},
    ])
    @patch("learner_api.active_users._resolve_ksb_profile_source_id", return_value="KSBP-1")
    @patch("learner_api.active_users._fetch_ksb_items_from_plan_mappings", return_value=[
        {"code": "K1", "number": "1", "type": "Knowledge", "description": "Knowledge 1"},
    ])
    @patch("learner_api.active_users._fetch_ksb_items_for_programme", return_value=[])
    @patch("learner_api.active_users._resolve_programme_id", return_value="PROG-1")
    def test_fetch_ksb_items_uses_profile_source_even_if_plan_mappings_exist(
        self,
        resolve_programme_id,
        fetch_for_programme,
        fetch_from_plan_mappings,
        resolve_profile_source,
        fetch_from_profile_source,
    ):
        result = _fetch_ksb_items("Programme A", training_plan=[{"moduleId": "MOD-1"}])

        self.assertEqual(result, fetch_from_profile_source.return_value)
        resolve_programme_id.assert_called_once_with("Programme A", training_plan=[{"moduleId": "MOD-1"}])
        fetch_for_programme.assert_not_called()
        resolve_profile_source.assert_called_once_with(
            programme_id="PROG-1",
            programme="Programme A",
            training_plan=[{"moduleId": "MOD-1"}],
        )
        fetch_from_profile_source.assert_called_once_with("KSBP-1")
        fetch_from_plan_mappings.assert_not_called()

    @patch("learner_api.active_users._fetch_ksb_items_from_profile_source", return_value=[])
    @patch("learner_api.active_users._resolve_ksb_profile_source_id", return_value="KSBP-1")
    @patch("learner_api.active_users._fetch_ksb_items_from_plan_mappings", return_value=[
        {"code": "K1", "number": "1", "type": "Knowledge", "description": "Knowledge 1"},
    ])
    @patch("learner_api.active_users._fetch_ksb_items_for_programme", return_value=[])
    @patch("learner_api.active_users._resolve_programme_id", return_value="PROG-1")
    def test_fetch_ksb_items_falls_back_to_plan_mappings_when_profile_source_is_empty(
        self,
        resolve_programme_id,
        fetch_for_programme,
        fetch_from_plan_mappings,
        resolve_profile_source,
        fetch_from_profile_source,
    ):
        result = _fetch_ksb_items("Programme A", training_plan=[{"moduleId": "MOD-1"}])

        self.assertEqual(result, fetch_from_plan_mappings.return_value)
        resolve_programme_id.assert_called_once_with("Programme A", training_plan=[{"moduleId": "MOD-1"}])
        fetch_for_programme.assert_called_once_with("PROG-1", "Programme A")
        resolve_profile_source.assert_called_once_with(
            programme_id="PROG-1",
            programme="Programme A",
            training_plan=[{"moduleId": "MOD-1"}],
        )
        fetch_from_profile_source.assert_called_once_with("KSBP-1")
        fetch_from_plan_mappings.assert_called_once_with(
            programme_id="PROG-1",
            programme="Programme A",
            training_plan=[{"moduleId": "MOD-1"}],
        )


class ResolveProgrammeIdTests(SimpleTestCase):
    PLAN = [{"moduleId": "MOD-SAFEGUARDING"}, {"moduleId": "MOD-PCP"}]

    def _resolve(self, cursor, programme):
        connections = {"default": ScriptedConnection(cursor), "enrolment": ScriptedConnection(cursor)}
        with patch.dict(active_users_connections, connections, clear=False):
            return _resolve_programme_id(programme, training_plan=self.PLAN)

    def test_shared_module_from_another_programme_does_not_win(self):
        # The shared module's owner sorts first; the learner's own programme
        # must still be chosen, or they are offered another standard's KSBs.
        cursor = ScriptedCursor([[
            ("PROG-MM-L6", "Marketing Manager Level 6"),
            ("PROG-PCP-L6", "Project Controls Professional Level 6"),
        ]])

        result = self._resolve(cursor, "Project Controls Professional Level 6")

        self.assertEqual(result, "PROG-PCP-L6")
        self.assertEqual(len(cursor.queries), 1)

    def test_programme_name_with_cohort_suffix_matches_its_module_owner(self):
        cursor = ScriptedCursor([[
            ("PROG-MM-L6", "Marketing Manager Level 6"),
            ("PROG-PCP-L6", "Project Controls Professional Level 6"),
        ]])

        result = self._resolve(cursor, "Project Controls Professional Level 6 - Feb 2026")

        self.assertEqual(result, "PROG-PCP-L6")

    def test_single_module_owner_is_used_without_a_name_lookup(self):
        cursor = ScriptedCursor([[("PROG-PCP-L6", "Project Controls Professional Level 6")]])

        result = self._resolve(cursor, "Renamed Programme")

        self.assertEqual(result, "PROG-PCP-L6")
        self.assertEqual(len(cursor.queries), 1)

    def test_unmatched_owners_fall_back_to_the_programme_name(self):
        cursor = ScriptedCursor([
            [("PROG-A", "Programme A"), ("PROG-B", "Programme B")],
            [("PROG-C",)],
        ])

        self.assertEqual(self._resolve(cursor, "Programme C"), "PROG-C")

    def test_first_module_owner_is_the_last_resort(self):
        cursor = ScriptedCursor([
            [("PROG-A", "Programme A"), ("PROG-B", "Programme B")],
            [],
        ])

        self.assertEqual(self._resolve(cursor, "Unknown Programme"), "PROG-A")


class LearnerOtjhTargetTests(SimpleTestCase):
    def test_schedule_based_week_target_uses_weeks_started_by_today_across_modules(self):
        week_rows = [
            {"moduleId": "MOD-1", "weekId": "W1", "otjh": 16.0},
            {"moduleId": "MOD-1", "weekId": "W2", "otjh": 6.5},
            {"moduleId": "MOD-1", "weekId": "W3", "otjh": 7.0},
            {"moduleId": "MOD-2", "weekId": "W4", "otjh": 2.0},
        ]
        module_start_by_id = {
            "MOD-1": date(2026, 7, 21),
            "MOD-2": date(2026, 8, 1),
        }
        week_offset_by_id = {
            "W1": 0,
            "W2": 1,
            "W3": 2,
            "W4": 0,
        }

        target = _schedule_based_week_target(
            week_rows,
            module_start_by_id,
            week_offset_by_id,
            today=date(2026, 8, 1),
        )

        self.assertEqual(target, 24.5)

    def test_sequential_week_target_falls_back_to_learner_start_date(self):
        week_rows = [
            {"otjh": 16.0},
            {"otjh": 6.5},
            {"otjh": 7.0},
            {"otjh": 6.5},
        ]

        target = _sequential_week_target(
            week_rows,
            learner_start_date=date(2026, 7, 21),
            today=date(2026, 8, 1),
        )

        self.assertEqual(target, 22.5)


@override_settings(
    AZURE_STORAGE_ACCOUNT="lmsstorage",
    AZURE_STORAGE_KEY="test-key",
    AZURE_QUARANTINE_CONTAINER="evidence-quarantine",
    AZURE_APPROVED_CONTAINER="evidence-approved",
    AZURE_REJECTED_CONTAINER="evidence-rejected",
    AZURE_SAS_TTL_MINUTES=15,
)
class EvidenceStorageUrlTests(SimpleTestCase):
    @patch("learner_api.evidence_storage.BlobServiceClient")
    def test_service_client_splits_multi_megabyte_uploads_into_small_blocks(self, service_client_class):
        evidence_storage._service_client(upload_block_bytes=256 * 1024)

        kwargs = service_client_class.call_args.kwargs
        self.assertEqual(kwargs["max_single_put_size"], 256 * 1024)
        self.assertEqual(kwargs["max_block_size"], 256 * 1024)

    @patch("learner_api.evidence_storage._service_client")
    def test_upload_rewinds_file_before_sending_it_to_azure(self, service_client):
        upload = BytesIO(b"complete-file-content")
        upload.read()
        blob_client = (
            service_client.return_value
            .get_blob_client.return_value
        )

        upload_to_quarantine(upload, "absence-reports/file.png", "image/png")

        uploaded_stream = blob_client.upload_blob.call_args.args[0]
        self.assertEqual(b"complete-file-content", uploaded_stream.read())
        self.assertEqual(blob_client.upload_blob.call_args.kwargs["max_concurrency"], 2)
        self.assertEqual(blob_client.upload_blob.call_args.kwargs["connection_timeout"], 60)
        self.assertEqual(blob_client.upload_blob.call_args.kwargs["read_timeout"], 60)

    @patch("learner_api.evidence_storage._service_client")
    def test_direct_upload_can_extend_timeout_for_large_recordings(self, service_client):
        evidence_storage.upload_blob(
            BytesIO(b"synthetic-recording"), "private-recordings", "lesson.mp4", "video/mp4",
            retry_total=4, read_timeout=180,
        )

        service_client.assert_called_once_with(upload_block_bytes=None, retry_total=4)
        options = service_client.return_value.get_blob_client.return_value.upload_blob.call_args.kwargs
        self.assertEqual(options["read_timeout"], 180)
        self.assertEqual(options["connection_timeout"], 60)

    def test_canonical_url_round_trips_container_and_blob_name(self):
        url = blob_url(
            "evidence-quarantine",
            "absence-reports/apprenticeship/42/file name.pdf",
        )

        self.assertEqual(
            parse_blob_url(url),
            (
                "evidence-quarantine",
                "absence-reports/apprenticeship/42/file name.pdf",
            ),
        )
        self.assertIn("file%20name.pdf", url)

    def test_rejects_urls_from_another_storage_account(self):
        self.assertIsNone(
            parse_blob_url(
                "https://otheraccount.blob.core.windows.net/"
                "evidence-quarantine/file.pdf"
            )
        )

    @patch("learner_api.evidence_storage.generate_blob_sas", return_value="signed-token")
    def test_resolves_allowed_blob_to_short_lived_sas(self, generate_sas):
        stored_url = blob_url("evidence-quarantine", "absence-reports/file.pdf")

        result = resolve_read_url(stored_url, {"evidence-quarantine"})

        self.assertEqual(f"{stored_url}?signed-token", result)
        generate_sas.assert_called_once()

    def test_does_not_sign_blob_from_disallowed_container(self):
        stored_url = blob_url("evidence-rejected", "absence-reports/file.pdf")

        self.assertEqual("", resolve_read_url(stored_url, {"evidence-approved"}))

    def test_preserves_legacy_local_evidence_url(self):
        self.assertEqual(
            "/media/absence-evidence/legacy.pdf",
            resolve_read_url(
                "/media/absence-evidence/legacy.pdf",
                {"evidence-approved"},
            ),
        )


class OrphanComponentReferenceTests(SimpleTestCase):
    """component_ref must resolve, or the write is rejected outright.

    These pin the fix for the defect that produced the historical orphan set:
    component_ref was persisted straight from the client, so a stale or
    frontend-generated id created a row referring to nothing while the request
    still reported success. Curriculum, Coach and reporting all join on
    component_ref, so such a row is invisible everywhere it matters.
    """

    def _connections(self, cursor):
        return {"default": ScriptedConnection(cursor), "enrolment": ScriptedConnection(cursor)}

    def test_known_component_resolves(self):
        cursor = ScriptedCursor([[(1,)]])
        with patch.dict(active_users_connections, self._connections(cursor), clear=False):
            self.assertTrue(component_reference_exists("COMP-REAL"))

    def test_unknown_component_does_not_resolve(self):
        cursor = ScriptedCursor([[], []])
        with patch.dict(active_users_connections, self._connections(cursor), clear=False):
            self.assertFalse(component_reference_exists("COMP-GHOST"))

    def test_blank_component_id_never_resolves(self):
        self.assertFalse(component_reference_exists(""))
        self.assertFalse(component_reference_exists(None))

    def test_unreachable_database_is_not_reported_as_a_missing_component(self):
        """A lookup that could not run is not evidence of absence.

        Answering "does not exist" here would reject a perfectly valid write
        whenever the database hiccuped, turning an outage into data loss.
        """
        class ExplodingConnection:
            def cursor(self):
                raise DatabaseError("connection refused")

        exploding = {"default": ExplodingConnection(), "enrolment": ExplodingConnection()}
        with patch.dict(active_users_connections, exploding, clear=False):
            with self.assertRaises(DatabaseError):
                component_reference_exists("COMP-REAL")

    def test_save_progress_record_rejects_an_unknown_component(self):
        learner = SimpleNamespace(pk=1)
        with patch("learner_api.active_users.component_reference_state", return_value=("unknown", "")):
            with self.assertRaises(OrphanComponentReferenceError) as raised:
                save_progress_record(learner, {"kind": "component", "componentId": "COMP-GHOST"})
        self.assertIn("COMP-GHOST", str(raised.exception))

    def test_non_component_activity_is_not_rejected(self):
        """A standalone quiz carries no componentId and must still be storable.

        The guard exists to stop fake component links, not to force every
        activity to be component-based.
        """
        learner = SimpleNamespace(pk=1)
        with patch("learner_api.active_users.component_reference_state") as state:
            with patch("learner_api.active_users.transaction.atomic", side_effect=RuntimeError("reached the write")):
                with self.assertRaises(RuntimeError):
                    save_progress_record(learner, {"kind": "quiz", "quizId": "64"})
        state.assert_not_called()


class ComponentWriteSoftDeleteTests(SimpleTestCase):
    """New activity may not target deleted curriculum; history still resolves.

    Two different questions are asked of the same ``component_ref``:

    * *historical read* — does this id name a Component at all? Soft-deleted
      counts, because a learner legitimately completed something that was
      archived afterwards and every report joins on that id.
    * *new authoritative write* — may new delivery be recorded against it? A
      withdrawn Component may not, including when the withdrawal came from an
      ancestor (week / module / group / cohort / programme).
    """

    # Column order of _COMPONENT_LINEAGE_DELETED_SQL.
    LEVELS = ("component", "week", "module", "group", "cohort", "programme")

    def _connections(self, cursor):
        return {"default": ScriptedConnection(cursor), "enrolment": ScriptedConnection(cursor)}

    def _lineage_row(self, *deleted_levels):
        return tuple(level in set(deleted_levels) for level in self.LEVELS)

    def _state(self, *deleted_levels):
        cursor = ScriptedCursor([[self._lineage_row(*deleted_levels)]])
        with patch.dict(active_users_connections, self._connections(cursor), clear=False):
            return component_reference_state("COMP-UNDER-TEST")

    def test_component_with_clean_lineage_is_valid_for_new_activity(self):
        self.assertEqual(self._state(), ("active", ""))

    def test_unknown_component_reports_unknown_not_deleted(self):
        cursor = ScriptedCursor([[], []])
        with patch.dict(active_users_connections, self._connections(cursor), clear=False):
            self.assertEqual(component_reference_state("COMP-GHOST"), ("unknown", ""))

    def test_blank_component_id_is_unknown(self):
        self.assertEqual(component_reference_state(""), ("unknown", ""))
        self.assertEqual(component_reference_state(None), ("unknown", ""))

    def test_directly_deleted_component_is_rejected_for_new_activity(self):
        self.assertEqual(self._state("component"), ("deleted", "component"))

    def test_deletion_through_each_ancestor_is_detected_and_named(self):
        """Parent-driven deletion is the case a component-only check misses."""
        for level in ("week", "module", "group", "cohort", "programme"):
            with self.subTest(deleted_level=level):
                self.assertEqual(self._state(level), ("deleted", level))

    def test_unreachable_database_is_not_reported_as_deleted(self):
        """Same principle as the orphan check: no answer is not an answer.

        Treating an unreachable lookup as "deleted" would reject valid learner
        work for the duration of a database hiccup.
        """
        class ExplodingConnection:
            def cursor(self):
                raise DatabaseError("connection refused")

        exploding = {"default": ExplodingConnection(), "enrolment": ExplodingConnection()}
        with patch.dict(active_users_connections, exploding, clear=False):
            with self.assertRaises(DatabaseError):
                component_reference_state("COMP-REAL")

    def test_historical_read_still_resolves_a_soft_deleted_component(self):
        """The read helper must NOT inherit the write restriction.

        Progress rows already pointing at an archived Component have to stay
        traceable, so existence is all this asks.
        """
        cursor = ScriptedCursor([[(1,)]])
        with patch.dict(active_users_connections, self._connections(cursor), clear=False):
            self.assertTrue(component_reference_exists("COMP-20260719113158962888"))

    def test_save_progress_record_rejects_a_soft_deleted_component(self):
        learner = SimpleNamespace(pk=1)
        with patch("learner_api.active_users.component_reference_state", return_value=("deleted", "component")):
            with patch("learner_api.active_users.transaction.atomic", side_effect=AssertionError("must not write")):
                with self.assertRaises(DeletedComponentReferenceError) as raised:
                    save_progress_record(learner, {"kind": "component", "componentId": "COMP-DEAD"})
        self.assertIn("COMP-DEAD", str(raised.exception))
        self.assertIn("component", str(raised.exception))

    def test_save_progress_record_rejects_a_component_deleted_through_its_parent(self):
        learner = SimpleNamespace(pk=1)
        with patch("learner_api.active_users.component_reference_state", return_value=("deleted", "module")):
            with patch("learner_api.active_users.transaction.atomic", side_effect=AssertionError("must not write")):
                with self.assertRaises(DeletedComponentReferenceError) as raised:
                    save_progress_record(learner, {"kind": "component", "componentId": "COMP-ORPHANED-MODULE"})
        self.assertIn("module", str(raised.exception))

    def test_save_progress_record_accepts_an_active_component(self):
        learner = SimpleNamespace(pk=1)
        with patch("learner_api.active_users.component_reference_state", return_value=("active", "")):
            with patch("learner_api.active_users._component_learning_context", return_value={}):
                with patch("learner_api.active_users.transaction.atomic", side_effect=RuntimeError("reached the write")):
                    with self.assertRaises(RuntimeError):
                        save_progress_record(learner, {"kind": "component", "componentId": "COMP-LIVE"})

    def test_both_rejections_share_one_base_so_callers_map_them_to_one_status(self):
        self.assertTrue(issubclass(OrphanComponentReferenceError, ComponentReferenceError))
        self.assertTrue(issubclass(DeletedComponentReferenceError, ComponentReferenceError))


class ComponentWriteEndpointRejectionTests(SimpleTestCase):
    """The service-layer rejections must surface as a client error, not a 200."""

    def _post(self, component_id, *, access_time=None, payload=None, holiday=False, calendar_error=False):
        access_time = access_time or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        tracking = issue_tracking_session(
            activity_kind="component",
            activity_id=component_id,
            learner_kind="apprenticeship",
            learner_id="19",
            counting_mode="visible_page",
            issued_at=access_time - timedelta(seconds=20),
        )
        body = {"trackingToken": tracking["trackingToken"], "timeTakenSeconds": 0}
        body.update(payload or {})
        request = RequestFactory().post(
            f"/learner_api/components/{component_id}/complete/?kind=apprenticeship&learnerId=19",
            data=json.dumps(body),
            content_type="application/json",
        )
        view = submit_component_progress
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__
        holidays = {access_time.date(): [{'label': 'Christmas closure'}]} if holiday else {}
        calendar = ({'side_effect': DatabaseError} if calendar_error
                    else {'return_value': holidays})
        with patch("learner_api.components.timezone.now", return_value=access_time), patch(
            'learner_api.working_rules.learner_holiday_details', **calendar,
        ):
            return view(request, component_id)

    def _run(self, save_side_effect=None, tutor_validation_required=False, **post_options):
        profile = SimpleNamespace(training_plan_progress=[])
        with patch("learner_api.components.SOURCE_MODELS", {"apprenticeship": Mock()}) as models:
            models["apprenticeship"].objects.get.return_value = SimpleNamespace(id=19)
            with patch("learner_api.components._component_meta", return_value=("podcast", "Podcast")):
                with patch("learner_api.components.component_ksb_codes", return_value=["K1"]):
                    with patch("learner_api.components._completion_criteria", return_value=(True, None)):
                        with patch("learner_api.components.learner_profile_for_source", return_value=profile):
                            with patch("learner_api.components.tracking_session_already_used", return_value=False):
                                with patch(
                                    "learner_api.components.save_progress_record",
                                    side_effect=save_side_effect,
                                ), patch("learner_api.components.requires_tutor_validation", return_value=tutor_validation_required), patch(
                                    "learner_api.components.queue_for_marking"
                                ):
                                    return self._post("COMP-UNDER-TEST", **post_options)

    def test_explicit_reflection_skip_is_audited_on_the_progress_record(self):
        save = Mock()
        response = self._run(save, payload={"skipReflection": True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(save.call_args.args[1]["reflectionSkipped"], True)

    def test_reflection_skip_remains_optional_with_tutor_validation(self):
        save = Mock()
        response = self._run(save, tutor_validation_required=True, payload={"skipReflection": True})
        self.assertEqual(response.status_code, 200)
        self.assertIs(save.call_args.args[1]["reflectionSkipped"], True)

    def test_unknown_component_write_returns_400(self):
        response = self._run(OrphanComponentReferenceError("COMP-GHOST"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("COMP-GHOST", json.loads(response.content)["error"])

    def test_soft_deleted_component_write_returns_400(self):
        response = self._run(DeletedComponentReferenceError("COMP-DEAD", "week"))
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertIn("COMP-DEAD", payload["error"])
        self.assertIn("week", payload["error"])

    # An invalid Finish must leave NOTHING behind -- no completion, no
    # percentage change, no history entry. Each of these asserts that
    # save_progress_record was never called.
    def test_out_of_hours_finish_is_refused_with_a_reason_and_writes_nothing(self):
        save = Mock()
        response = self._run(
            save,
            access_time=datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc),   # Thursday 22:00
            payload={"timeTakenSeconds": 10, "timeEntrySource": "timer"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["validation"]["reason"], "outside_working_hours")
        save.assert_not_called()

    def test_weekend_finish_is_refused_with_a_weekend_reason(self):
        save = Mock()
        response = self._run(save, access_time=datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["validation"]["reason"], "weekend")
        save.assert_not_called()

    def test_applicable_holiday_finish_is_refused_and_names_the_holiday(self):
        save = Mock()
        response = self._run(save, holiday=True)
        self.assertEqual(response.status_code, 409)
        validation = json.loads(response.content)["validation"]
        self.assertEqual(validation["reason"], "holiday")
        self.assertEqual(validation["holidayName"], "Christmas closure")
        save.assert_not_called()

    def test_a_holiday_outside_this_learners_cohort_does_not_refuse_them(self):
        # holiday=False is the scoped resolver answering "no closure for this
        # component's cohort" even when the college has one that day.
        save = Mock()
        response = self._run(save, holiday=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[1]["submissionValidationReason"], "")

    def test_declared_working_instant_completes_and_keeps_the_real_click(self):
        save = Mock()
        click = datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc)   # Sunday 22:00
        response = self._run(
            save,
            access_time=click,
            payload={"timeTakenSeconds": 10, "declaredCompletedAt": "2026-01-16T14:30:00"},
        )
        self.assertEqual(response.status_code, 200)
        record = save.call_args.args[1]
        # The click is preserved; the declaration is stored separately.
        self.assertEqual(record["submittedAt"], click.isoformat())
        self.assertTrue(record["declaredCompletedAt"].startswith("2026-01-16T14:30"))
        self.assertEqual(record["submissionValidationReason"], "weekend")

    def test_declared_instant_outside_the_rules_is_accepted(self):
        # The dialog only warns: a declared Saturday is written as declared.
        save = Mock()
        response = self._run(
            save,
            access_time=datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc),
            payload={"timeTakenSeconds": 10, "declaredCompletedAt": "2026-01-17T14:30:00"},
        )
        self.assertEqual(response.status_code, 200)
        record = save.call_args.args[1]
        self.assertTrue(record["declaredCompletedAt"].startswith("2026-01-17T14:30"))
        self.assertEqual(record["submissionValidationReason"], "weekend")

    def test_declared_instant_cannot_be_in_the_future(self):
        save = Mock()
        response = self._run(
            save,
            access_time=datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc),
            payload={"declaredCompletedAt": "2026-01-20T14:30:00"},
        )
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    def test_calendar_failure_does_not_save_progress(self):
        save = Mock()
        response = self._run(save, calendar_error=True)
        self.assertEqual(response.status_code, 503)
        save.assert_not_called()

    def test_valid_finish_completes_immediately_with_no_declaration(self):
        save = Mock()
        response = self._run(save)
        self.assertEqual(response.status_code, 200)
        record = save.call_args.args[1]
        self.assertFalse(record['outsideWorkingHours'])
        self.assertIsNone(record['declaredCompletedAt'])
        self.assertEqual(record['submissionValidationReason'], '')

    def test_legacy_confirmation_fields_keep_their_meaning_and_are_not_reused(self):
        # The old consent booleans are no longer written by the new flow, so a
        # historical true still means exactly the consent it recorded.
        save = Mock()
        response = self._run(
            save,
            access_time=datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc),
            payload={"declaredCompletedAt": "2026-01-16T14:30:00",
                     "insideWorkingHoursConfirmed": True,
                     "outsideWorkingHoursConfirmed": True},
        )
        self.assertEqual(response.status_code, 200)
        record = save.call_args.args[1]
        self.assertFalse(record["insideWorkingHoursConfirmed"])
        self.assertFalse(record["outsideWorkingHoursConfirmed"])
        self.assertIsNone(record["insideWorkingHoursConfirmedAt"])
        self.assertIsNone(record["outsideWorkingHoursConfirmedAt"])


class ProgressAchievementRuleTests(SimpleTestCase):
    """Failed / unresolved activity can never count as achieved KSB delivery.

    The rule used to be applied only to ``kind == 'quiz'``, and was safe purely
    because no failed row happened to carry Component lineage. These pin the
    rule itself, so a graded Component with a valid ``component_ref``, valid
    lineage and a valid KSB snapshot still cannot claim its KSBs on a failure.
    """

    def test_an_ungraded_completion_counts(self):
        self.assertTrue(progress_counts_as_achieved(kind="component", passed=None))
        self.assertTrue(progress_counts_as_achieved(kind="video", passed=None))

    def test_an_explicit_failure_never_counts_whatever_the_kind(self):
        for kind in ("component", "video", "quiz", "live_session", ""):
            with self.subTest(kind=kind):
                self.assertFalse(progress_counts_as_achieved(kind=kind, passed=False))

    def test_a_graded_kind_needs_an_explicit_pass(self):
        self.assertTrue(progress_counts_as_achieved(kind="quiz", passed=True))
        self.assertFalse(progress_counts_as_achieved(kind="quiz", passed=None))
        self.assertFalse(progress_counts_as_achieved(kind="QUIZ", passed=None))

    def test_a_passed_flag_on_an_ungraded_kind_is_honoured(self):
        self.assertTrue(progress_counts_as_achieved(kind="component", passed=True))

    def test_status_explains_why_a_row_was_excluded(self):
        self.assertEqual(progress_achievement_status(kind="component", passed=None), "achieved")
        self.assertEqual(progress_achievement_status(kind="component", passed=False), "failed")
        self.assertEqual(progress_achievement_status(kind="quiz", passed=None), "incomplete")
        self.assertEqual(progress_achievement_status(kind="quiz", passed=False), "failed")

    def test_record_form_reads_the_serialised_progress_shape(self):
        failed = {
            "kind": "component",
            "componentId": "COMP-20260816E2E",
            "ksbs": ["K1"],
            "passed": False,
        }
        self.assertFalse(progress_record_counts_as_achieved(failed))
        self.assertTrue(progress_record_counts_as_achieved({**failed, "passed": True}))
        self.assertFalse(progress_record_counts_as_achieved(None))
        self.assertFalse(progress_record_counts_as_achieved("not a record"))

    def test_the_rule_has_exactly_one_implementation(self):
        """No SQL twin to drift from this one — see the module docstring."""
        self.assertFalse(
            [name for name in dir(progress_rules) if "sql" in name.lower()],
            "a SQL variant of the completion rule would be a second implementation",
        )


class QuizSubmitWorkingRulesTests(SimpleTestCase):
    """A quiz Submit is a completion action, so it passes the same gate.

    Taking the quiz stays unrestricted; only the Submit click is judged. A
    refusal must grade nothing and store nothing -- no attempt, no progress
    record, no audit line, no OTJH -- so the learner's answers stay in the
    browser and the corrected Submit grades to exactly the same score.
    """

    QUIZ_ID = 77

    def _quiz(self):
        return {
            "id": self.QUIZ_ID, "title": "Week 1 quiz", "module": "Module 1",
            "programme": "P1", "weekId": "W1", "duration": 30, "timeUnit": "minutes",
            "passingGrade": 50, "randomizeQuestions": False, "randomizeAnswers": False,
            "questions": [{
                "id": 1, "text": "2 + 2?", "type": "single_choice", "points": 1,
                "sortOrder": 1, "explanation": None,
                "answers": [
                    {"id": 10, "text": "4", "isCorrect": True, "sortOrder": 1},
                    {"id": 11, "text": "5", "isCorrect": False, "sortOrder": 2},
                ],
            }],
        }

    def _post(self, save, *, access_time=None, payload=None, holiday=False):
        from . import quizzes as quiz_views

        access_time = access_time or datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        tracking = issue_tracking_session(
            activity_kind="quiz", activity_id=self.QUIZ_ID,
            learner_kind="apprenticeship", learner_id="19",
            counting_mode="active_quiz", issued_at=access_time - timedelta(seconds=20),
        )
        body = {
            "trackingToken": tracking["trackingToken"], "timeTakenSeconds": 0,
            "answers": {"1": 10},
        }
        body.update(payload or {})
        request = RequestFactory().post(
            f"/learner_api/quizzes/{self.QUIZ_ID}/submit/?kind=apprenticeship&learnerId=19",
            data=json.dumps(body), content_type="application/json",
        )
        view = quiz_views.submit_quiz_attempt
        while hasattr(view, "__wrapped__"):
            view = view.__wrapped__

        holidays = {access_time.date(): [{"label": "Christmas closure"}]} if holiday else {}
        models = {"apprenticeship": Mock()}
        models["apprenticeship"].objects.get.return_value = SimpleNamespace(id=19)
        profile = SimpleNamespace(training_plan_progress=[])
        with patch.object(quiz_views, "SOURCE_MODELS", models), \
             patch.object(quiz_views, "_fetch_quiz", return_value=self._quiz()), \
             patch.object(quiz_views, "learner_profile_for_source", return_value=profile), \
             patch.object(quiz_views, "tracking_session_already_used", return_value=False), \
             patch.object(quiz_views, "save_progress_record", save), \
             patch.object(quiz_views, "record_quiz_attempt") as audit, \
             patch.object(quiz_views.timezone, "now", return_value=access_time), \
             patch("learner_api.working_rules.learner_holiday_details", return_value=holidays):
            response = view(request, self.QUIZ_ID)
        return response, audit

    def test_valid_submit_scores_and_completes_with_no_declaration(self):
        save = Mock()
        response, audit = self._post(save)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertTrue(body["passed"])
        self.assertEqual(body["achievedScore"], 1)
        self.assertEqual(body["totalScore"], 1)
        save.assert_called_once()
        audit.assert_called_once()
        record = save.call_args.args[1]
        self.assertIsNone(record["declaredCompletedAt"])
        self.assertEqual(record["submissionValidationReason"], "")

    def test_diagram_is_returned_and_snapshotted_with_the_scored_question(self):
        quiz = self._quiz()
        quiz["questions"][0]["imageUrl"] = "data:image/png;base64,ZGlhZ3JhbQ=="
        save = Mock()
        with patch.object(self, "_quiz", return_value=quiz):
            response, _audit = self._post(save)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        expected = quiz["questions"][0]["imageUrl"]
        self.assertEqual(body["breakdown"][0]["imageUrl"], expected)
        self.assertEqual(save.call_args.args[1]["questions"][0]["imageUrl"], expected)
        self.assertTrue(body["passed"])

    def test_new_attempt_keeps_question_and_answer_text_for_later_review(self):
        save = Mock()
        response, _audit = self._post(save)
        self.assertEqual(response.status_code, 200)
        stored = save.call_args.args[1]["questions"][0]
        self.assertEqual(stored["questionText"], "2 + 2?")
        self.assertEqual(stored["chosenText"], "4")
        self.assertEqual(stored["correctText"], "4")
        self.assertEqual(stored["possible"], 1)

    def test_broken_answer_key_and_stale_selection_store_no_attempt(self):
        for broken_key in (False, True):
            with self.subTest(broken_key=broken_key):
                quiz = self._quiz()
                if broken_key:
                    quiz["questions"][0]["answers"][0]["isCorrect"] = False
                save = Mock()
                with patch.object(self, "_quiz", return_value=quiz):
                    response, audit = self._post(save, payload={"answers": {"1": 10 if broken_key else 999}})
                self.assertEqual(response.status_code, 409)
                save.assert_not_called()
                audit.assert_not_called()

    def test_out_of_hours_submit_is_refused_and_writes_nothing(self):
        save = Mock()
        response, audit = self._post(
            save, access_time=datetime(2026, 1, 15, 22, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            json.loads(response.content)["validation"]["reason"], "outside_working_hours",
        )
        save.assert_not_called()
        audit.assert_not_called()

    def test_weekend_submit_is_refused_and_writes_nothing(self):
        save = Mock()
        response, audit = self._post(
            save, access_time=datetime(2026, 1, 17, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["validation"]["reason"], "weekend")
        save.assert_not_called()
        audit.assert_not_called()

    def test_an_applicable_selected_holiday_is_refused_and_named(self):
        save = Mock()
        response, audit = self._post(save, holiday=True)
        self.assertEqual(response.status_code, 409)
        validation = json.loads(response.content)["validation"]
        self.assertEqual(validation["reason"], "holiday")
        self.assertEqual(validation["holidayName"], "Christmas closure")
        save.assert_not_called()
        audit.assert_not_called()

    def test_a_holiday_outside_this_learners_cohort_does_not_refuse_them(self):
        save = Mock()
        response, _ = self._post(save, holiday=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.args[1]["submissionValidationReason"], "")

    def test_a_declared_instant_outside_the_rules_is_accepted(self):
        # The dialog only warns: a declared Saturday is written as declared.
        save = Mock()
        response, audit = self._post(
            save,
            access_time=datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc),
            payload={"declaredCompletedAt": "2026-01-17T14:30:00"},
        )
        self.assertEqual(response.status_code, 200)
        record = save.call_args.args[1]
        self.assertTrue(record["declaredCompletedAt"].startswith("2026-01-17T14:30"))
        audit.assert_called_once()

    def test_a_declared_instant_cannot_be_in_the_future(self):
        save = Mock()
        response, _ = self._post(
            save,
            access_time=datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc),
            payload={"declaredCompletedAt": "2026-01-20T14:30:00"},
        )
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    def test_corrected_submit_keeps_the_score_the_attempt_and_the_real_click(self):
        click = datetime(2026, 1, 18, 22, 0, tzinfo=timezone.utc)   # Sunday 22:00
        refused = Mock()
        refusal, refused_audit = self._post(refused, access_time=click)
        self.assertEqual(refusal.status_code, 409)
        refused.assert_not_called()
        refused_audit.assert_not_called()

        # The same answers go back with a declared working instant.
        save = Mock()
        response, audit = self._post(
            save, access_time=click,
            payload={"declaredCompletedAt": "2026-01-16T14:30:00"},
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        # Identical academic result to the refused click: nothing was retaken.
        self.assertTrue(body["passed"])
        self.assertEqual(body["achievedScore"], 1)
        self.assertEqual(body["attempt"]["attempt"], 1)

        record = save.call_args.args[1]
        self.assertEqual(record["submittedAt"], click.isoformat())
        self.assertTrue(record["declaredCompletedAt"].startswith("2026-01-16T14:30"))
        self.assertEqual(record["submissionValidationReason"], "weekend")
        audit.assert_called_once()
        # The audit line keeps the real click, never the declaration.
        self.assertEqual(audit.call_args.kwargs["submitted_at"], click)
