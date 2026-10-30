import json
from time import perf_counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.db import DatabaseError, connection
from django.core.cache import cache
from django.db.utils import ConnectionDoesNotExist
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from old_otjh.service import ServiceError

from coach_api.models import CoachAbsenceReport, CoachCalendarEvent, CoachDashboardSnapshot
from coach_api.views import (
    build_generated_calendar_event,
    build_graph_event_payload,
    build_ksb_completed_details,
    build_monthly_risk_history,
    build_otjh_completed_entries,
    build_monthly_activity_learner,
    coach_caseload,
    coach_attendance_details,
    coach_manual_attendance,
    coach_source_attendance,
    coach_dashboard,
    coach_meeting_artifact_content_response,
    coach_meeting_expected_attendees,
    coach_meeting_transcript_text,
    coach_monthly_activity,
    coach_timetable_event_artifacts,
    coach_timetable_book_event,
    coach_timetable_schedule_event,
    coach_meeting_graph_target,
    coach_has_live_session_access,
    coach_staff_display_name,
    collect_generated_timetable,
    completed_ksb_codes,
    curriculum_monthly_target_hours,
    curriculum_monthly_target_hours_weeks,
    apply_attendance_summary,
    apply_aptem_variance_status,
    apply_otjh_to_date_metrics,
    apply_audit_hour_totals,
    apply_canonical_learner_metrics,
    case_file_table_metrics_snapshot,
    apply_canonical_ksb_evidence,
    apply_evidenced_ksb_count,
    caseload_audit_hour_totals,
    caseload_aptem_ids,
    caseload_canonical_metrics,
    caseload_canonical_attendance,
    caseload_dashboard_progress_projections,
    caseload_evidenced_ksb_counts,
    caseload_kbc_attendance_rates,
    caseload_latest_learning_activities,
    canonical_attendance_detail_rows,
    dashboard_attendance_rows,
    dashboard_monthly_risk_history,
    dashboard_review_history,
    attach_caseload_source_rows,
    fetch_caseload_learner_profiles,
    fetch_evidence_file_queue,
    fetch_source_schedule_rows,
    graph_organizer_mailbox,
    iterate_generated_schedule_dates,
    latest_learning_activity,
    monthly_event_is_between,
    reported_minutes,
    restore_case_file_table_metrics,
    route_absence_report_evidence,
    serialize_caseload_dashboard_learner,
    serialize_caseload_learner,
    normalize_program_status,
)
from learner_api.learner_detail import otjh_status_from_variance
from learner_api.models import LearnerProfile


class CoachProgrammeStatusTests(SimpleTestCase):
    def test_delivery_is_counted_as_active_for_coach_views(self):
        self.assertEqual(normalize_program_status("Delivery"), "active")
        self.assertEqual(normalize_program_status("Active"), "active")


def call_coach_view(view, request, *args, **kwargs):
    """Unit-test view logic below the integration-tested auth boundary."""
    request.coach_email = "coach@example.com"
    return unwrap(view)(request, *args, **kwargs)


class AuditKsbOverlayTests(SimpleTestCase):
    """KSB comes from the audit mapping keyed by Aptem id, as a count.

    The mapping spans several apprenticeship standards (71 distinct codes, with
    individual learners reaching 60) and carries no per-learner denominator, so
    a percentage here would be against the wrong total -- the learner's own
    workspace shows the same figure as a count for that reason.
    """

    def test_audit_count_does_not_replace_comparable_curriculum_progress(self):
        payload = {"ksbCompleted": 14, "ksbTarget": 14, "ksbProgress": 100}

        overlaid = apply_evidenced_ksb_count(payload, 23)

        self.assertEqual(overlaid["ksbEvidencedCount"], 23)
        self.assertEqual(overlaid["ksbCompleted"], 14)
        self.assertEqual(overlaid["ksbSource"], "audit")
        # The ratio remains internally consistent; the raw audit figure has no
        # compatible denominator and is exposed only through its dedicated key.
        self.assertEqual(overlaid["ksbProgress"], 100)
        self.assertEqual(overlaid["ksbTarget"], 14)

    def test_learners_without_audit_ksbs_keep_their_own(self):
        payload = {"ksbCompleted": 3, "ksbTarget": 14, "ksbProgress": 21}

        self.assertEqual(apply_evidenced_ksb_count(dict(payload), None), payload)

    @patch("coach_api.views.read_evidenced_ksb_counts_bulk")
    @patch("coach_api.views.audit_connection")
    def test_counts_are_keyed_by_profile_id(self, connection, read_bulk):
        read_bulk.return_value = {4321: 23}
        linked = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))
        unlinked = SimpleNamespace(id=8, _caseload_source=SimpleNamespace(aptem_id=None))

        self.assertEqual(caseload_evidenced_ksb_counts([linked, unlinked]), {7: 23})

    @patch("coach_api.views.audit_connection")
    def test_an_unreachable_audit_mirror_leaves_the_caseload_renderable(self, connection):
        connection.side_effect = DatabaseError("audit mirror down")
        linked = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))

        self.assertEqual(caseload_evidenced_ksb_counts([linked]), {})

    @patch("coach_api.views.audit_connection", side_effect=ConnectionDoesNotExist("audit"))
    def test_missing_audit_alias_leaves_the_caseload_renderable(self, _connection):
        linked = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))

        self.assertEqual(caseload_evidenced_ksb_counts([linked]), {})
        self.assertEqual(caseload_audit_hour_totals([linked]), {})


class CaseloadAptemIdTests(SimpleTestCase):
    def test_attached_source_rows_are_used_without_a_query(self):
        rows = [SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id=" 4321 "))]

        with patch("coach_api.views.fetch_caseload_aptem_ids") as lean:
            self.assertEqual(caseload_aptem_ids(rows), {7: 4321})
        lean.assert_not_called()

    def test_rows_without_a_source_fall_back_to_the_lean_query(self):
        """The dashboard skips the wide Created_users join, so its rows arrive
        with no `_caseload_source` and must still resolve."""
        rows = [SimpleNamespace(id=9, _caseload_source=None)]

        with patch("coach_api.views.fetch_caseload_aptem_ids", return_value={9: 77}) as lean:
            self.assertEqual(caseload_aptem_ids(rows), {9: 77})
        self.assertEqual(list(lean.call_args.args[0]), rows)


class DashboardAttendanceTests(SimpleTestCase):
    """Dashboard rows reuse the learner attendance summary for every learner."""

    def setUp(self):
        # Mock stored schedules/recoveries only; exercise the real shared
        # register and summary without touching a database or Microsoft.
        profiles = patch("learner_api.models.LearnerProfile.objects")
        self.profiles = profiles.start()
        self.addCleanup(profiles.stop)
        profile_by_enrolment = {193: 7, **{100 + index: index for index in range(1, 26)}}
        self.profiles.filter.side_effect = lambda *, enrolment_id: SimpleNamespace(
            values_list=lambda *args, **kwargs: [profile_by_enrolment[enrolment_id]])
        for target, kwargs in (
            ("learner_api.attendance_lectures.read_native_occurrences", {"return_value": []}),
            ("learner_api.attendance_confirmation.read_confirmations", {"return_value": {}}),
            ("learner_api.attendance_lectures._completed_catchup_occurrences", {"return_value": set()}),
            ("learner_api.attendance_lectures._apply_coach_source_adjustments", {"side_effect": lambda rows, learner_id: rows}),
        ):
            mocked = patch(target, **kwargs)
            value = mocked.start()
            self.addCleanup(mocked.stop)
            if target.endswith("read_native_occurrences"):
                self.scheduled = value

    def schedule_teams(self, records):
        self.scheduled.return_value = [
            {**row, "scheduled_start": timezone.make_aware(datetime.combine(row["session_date"], time(9))),
             "scheduled_end": timezone.make_aware(datetime.combine(row["session_date"], time(10)))}
            for row in records if row.get("source") == "microsoft-teams"
        ]

    @patch("coach_api.views.caseload_canonical_attendance")
    def test_aptem_rows_carry_the_learner_dashboard_rate(self, summaries):
        summaries.return_value = {7: {"sessions": 28, "present": 23, "absent": 5, "attendanceRate": 82, "lastSessionDate": date(2026, 9, 18)}}
        rows = [SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))]
        learners = [{"id": "7", "name": "A Learner", "email": "a@example.com"}]

        payload = dashboard_attendance_rows(rows, learners)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["attendance"], 82)
        self.assertTrue(payload[0]["hasAttendance"])
        self.assertEqual(payload[0]["present"], 23)
        self.assertEqual(payload[0]["lastSessionDate"], "2026-09-18")
        summaries.assert_called_once_with(rows)

    @patch("coach_api.views.caseload_canonical_attendance", return_value={})
    def test_learners_with_no_register_are_left_out(self, summaries):
        rows = [SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))]
        learners = [{"id": "7", "name": "A Learner", "email": "a@example.com"}]

        self.assertEqual(dashboard_attendance_rows(rows, learners), [])

    @patch("coach_api.views.caseload_canonical_attendance")
    def test_a_partial_learner_dict_does_not_break_the_dashboard(self, summaries):
        """This helper enriches whatever the serializer produced; a missing key
        must never turn the whole dashboard into a 503."""
        summaries.return_value = {2: {"sessions": 4, "present": 4, "absent": 0, "attendanceRate": 100}}
        rows = [SimpleNamespace(id=2, _caseload_source=SimpleNamespace(aptem_id="4321"))]

        payload = dashboard_attendance_rows(rows, [{"id": "2"}])

        self.assertEqual(payload[0]["learner"], None)
        self.assertEqual(payload[0]["attendance"], 100)

    @patch("coach_api.views.caseload_canonical_attendance", return_value={})
    def test_an_unreachable_register_leaves_the_dashboard_renderable(self, summaries):
        rows = [SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))]

        self.assertEqual(dashboard_attendance_rows(rows, [{"id": "7", "name": "A"}]), [])

    @patch("coach_api.views.caseload_canonical_attendance")
    def test_learner_without_aptem_keeps_existing_register(self, summaries):
        summaries.return_value = {8: {"sessions": 3, "present": 2, "absent": 1, "attendanceRate": 67}}
        row = SimpleNamespace(id=8, _caseload_source=SimpleNamespace(aptem_id=""))

        payload = dashboard_attendance_rows([row], [{"id": "8", "name": "B"}])

        self.assertEqual(payload[0]["attendance"], 67)
        summaries.assert_called_once_with([row])

    def test_no_query_runs_for_an_empty_caseload(self):
        with patch("coach_api.views.caseload_canonical_attendance") as summaries:
            self.assertEqual(dashboard_attendance_rows([], []), [])
        summaries.assert_not_called()

    @patch("coach_api.views.fetch_kbc_attendance_rates")
    @patch("coach_api.views.caseload_aptem_ids", return_value={7: 4321, 8: 9876})
    def test_coach_caseload_attendance_is_keyed_to_kbc_rates(self, aptem_ids, kbc_rates):
        kbc_rates.return_value = {
            "4321": {"sessions": 10, "present": 8, "absent": 2, "rate": 80},
        }

        profile_ids, metrics = caseload_kbc_attendance_rates([
            SimpleNamespace(id=7),
            SimpleNamespace(id=8),
        ])

        self.assertEqual(profile_ids, {7: 4321, 8: 9876})
        self.assertEqual(metrics, {7: {"sessions": 10, "present": 8, "absent": 2, "rate": 80}})
        kbc_rates.assert_called_once()
        self.assertEqual(list(kbc_rates.call_args.args[0]), [4321, 9876])

    @patch("coach_api.views.close_old_connections")
    @patch("learner_api.attendance_lectures.combined_attendance_rows")
    def test_caseload_counts_a_lecture_in_kbc_and_teams_once(self, combined, _close):
        day = date(2026, 9, 3)
        row = {"learner_id": 193, "learner_name": "A Learner", "learner_email": "learner@example.com",
               "session_date": day, "session_start_time": None, "session_end_time": None, "minutes_late": 0,
               "catchup_completed": False, "updated_at": None}
        combined.return_value = [
            {**row, "session_id": "kbc-1", "source": "kbc-attendance", "module_title": "Martech - Thur",
             "attendance_status": "present"},
            {**row, "session_id": "occ-1", "source": "microsoft-teams", "module_title": "Martech - Thur",
             "attendance_status": "present"},
            {**row, "session_id": "kbc-2", "source": "kbc-attendance", "module_title": "Social Media",
             "attendance_status": "absent"},
        ]
        learner = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(id=193, email="learner@example.com"))
        self.schedule_teams(combined.return_value)

        summary = caseload_canonical_attendance([learner])[7]

        self.assertEqual((summary["sessions"], summary["present"], summary["attendanceRate"]), (2, 1, 50))
        self.assertEqual(summary["absent"], 1)
        self.assertEqual([item["status"] for item in summary["sessionHistory"]], ["present", "absent"])
        self.profiles.filter.assert_called_once_with(enrolment_id=193)

    @patch("coach_api.views.fetch_verified_teams_attendance_rows")
    @patch("coach_api.views.fetch_kbc_attendance_rows_bulk")
    @patch("coach_api.views.combined_attendance_rows")
    def test_aptem_attendance_bulk_load_is_constant_for_the_caseload(self, combined, kbc_bulk, teams_bulk):
        day = date(2026, 9, 3)
        learners = [
            SimpleNamespace(
                id=index,
                _caseload_source=SimpleNamespace(id=100 + index, aptem_id=4000 + index,
                                                 username=f"Learner {index}", email=f"learner{index}@example.test"),
            )
            for index in range(1, 26)
        ]
        kbc_bulk.return_value = [
            {"learner_id": 100 + index, "session_id": f"kbc-{index}", "session_date": day,
             "module_title": "Module", "attendance_status": "present", "source": "kbc-attendance",
             "minutes_late": 0, "catchup_completed": False, "updated_at": None,
             "learner_name": f"Learner {index}", "learner_email": f"learner{index}@example.test"}
            for index in range(1, 26)
        ]
        teams_bulk.return_value = []

        result = caseload_canonical_attendance(learners)

        self.assertEqual(len(result), 25)
        kbc_bulk.assert_called_once()
        teams_bulk.assert_called_once()
        combined.assert_not_called()

    @patch("coach_api.views.fetch_verified_teams_attendance_rows")
    @patch("coach_api.views.fetch_kbc_attendance_rows_bulk")
    def test_aptem_attendance_accepts_the_same_mixed_source_timestamps_as_the_learner_register(
        self, kbc_bulk, teams_bulk,
    ):
        source = SimpleNamespace(id=193, aptem_id=8533, username="Learner", email="learner@example.test")
        common = {
            "learner_id": 193, "learner_name": "Learner", "learner_email": source.email,
            "session_date": date(2026, 9, 3), "module_title": "Module",
            "session_start_time": None, "session_end_time": None,
            "minutes_late": 0, "catchup_completed": False,
        }
        kbc_bulk.return_value = [{**common, "session_id": "kbc", "attendance_status": "present",
                                  "updated_at": datetime(2026, 9, 3, 10)}]
        teams_bulk.return_value = [{**common, "enrolment_id": 193,
                                    "session_date": date(2026, 9, 4), "module_title": "Other module",
                                    "session_id": "teams", "occurrence_id": "teams",
                                    "attendance_status": "absent", "updated_at": timezone.now()}]
        self.schedule_teams([{**row, "source": "microsoft-teams"} for row in teams_bulk.return_value])

        summary = caseload_canonical_attendance([
            SimpleNamespace(id=7, _caseload_source=source),
        ])[7]

        self.assertEqual((summary["present"], summary["sessions"], summary["attendanceRate"]), (1, 2, 50))

    @patch("learner_api.attendance_lectures.combined_attendance_rows", return_value=[])
    def test_caseload_includes_elapsed_unreported_sessions_and_saved_confirmations(self, combined):
        source = SimpleNamespace(id=193, email="learner@example.test")
        common = {"learner_id": 193, "learner_name": "Learner", "learner_email": source.email,
                  "source": "microsoft-teams", "session_date": date(2026, 9, 3),
                  "session_start_time": time(9), "session_end_time": time(10),
                  "module_title": "Module", "attendance_status": "pending", "minutes_late": 0,
                  "catchup_completed": False, "updated_at": None}
        self.schedule_teams([{**common, "session_id": "confirmed"}, {**common, "session_id": "missed"}])
        with patch("learner_api.attendance_confirmation.read_confirmations", return_value={
            "teams:confirmed-2026-09-03": {"seconds": 3600, "submitted_at": timezone.now()},
        }):
            summary = caseload_canonical_attendance([SimpleNamespace(id=7, _caseload_source=source)])[7]
        # Phase 4: elapsed sessions without evidence remain unmarked and do
        # not dilute the denominator; saved confirmation counts as present.
        self.assertEqual((summary["sessions"], summary["present"], summary["absent"], summary["attendanceRate"]),
                         (1, 1, 0, 100))
        self.assertEqual([item["status"] for item in summary["sessionHistory"]], ["present"])
        combined.assert_called_once_with(source)


class AttendanceDetailRowsTests(SimpleTestCase):
    @patch("learner_api.attendance_lectures.attendance_read_contract")
    def test_details_forward_the_authoritative_contract(self, read_contract):
        source = SimpleNamespace(id=901)
        summary = {"present": 2, "absent": 1, "totalCounted": 3, "attendanceRate": 67}
        history = [{"status": "present", "rawStatus": "absent", "effectiveStatus": "made_up"}]
        read_contract.return_value = {"summary": summary, "history": history}
        actual_summary, sessions = canonical_attendance_detail_rows(source, learner_profile_id=315)
        read_contract.assert_called_once_with(source, learner_profile_id=315)
        self.assertIs(actual_summary, summary)
        self.assertIs(sessions, history)

    @patch("coach_api.attendance_detail.detail_records")
    @patch("coach_api.views.resolve_caseload_source_row")
    @patch("coach_api.views.fetch_source_schedule_rows", return_value=({}, {}))
    @patch("coach_api.views.fetch_attendance_caseload_rows")
    def test_detail_view_fetches_only_the_requested_authorised_learner(
        self, fetch_rows, fetch_source, resolve_source, detail_rows,
    ):
        source = SimpleNamespace(id=901)
        row = SimpleNamespace(id=315, username="A Learner", email="learner@example.test",
            programme="Data", cohort="September", group_name="Group A", coach_name="Coach")
        fetch_rows.return_value = [row]
        resolve_source.return_value = source
        detail_rows.return_value = {"summary": {"sessions": 3, "present": 2, "absent": 1, "attendanceRate": 67},
            "records": [], "pagination": {"page": 1, "pageSize": 20, "total": 3, "hasMore": False}}
        request = RequestFactory().get("/coach_api/coach/attendance/details", {"learner_id": "315"})
        response = call_coach_view(coach_attendance_details, request)
        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        fetch_rows.assert_called_once_with("coach@example.com", learner_id="315", include_plan=False)
        fetch_source.assert_called_once_with([row], fields=(
            'id', 'username', 'email', 'aptem_id', 'employer_id', 'learner_type',
            'learner_start_date', 'learner_end_date'))
        detail_rows.assert_called_once_with(source, 315, request.GET)
        self.assertEqual(payload["learner"], {"id": "315", "name": "A Learner", "email": "learner@example.test",
            "programme": "Data", "cohort": "September", "group": "Group A"})
        self.assertEqual(payload["coach"], {"name": "Coach", "email": "coach@example.com"})
        self.assertIsNone(payload["tutor"])
        self.assertEqual(payload["summary"], detail_rows.return_value["summary"])
        self.assertEqual(set(payload), {"learner", "coach", "tutor", "summary", "records", "pagination"})


class ManualAttendanceMutationTests(SimpleTestCase):
    @patch("coach_api.views._manual_attendance_learner")
    @patch("coach_api.views.CoachManualAttendance.objects")
    def test_create_persists_a_coach_owned_manual_record(self, objects, find_learner):
        profile = SimpleNamespace(_caseload_source=SimpleNamespace(id=901))
        find_learner.return_value = (profile, {
            "id": "315", "name": "A Learner", "email": "learner@example.com",
        })
        created = SimpleNamespace(
            id=9, learner_id=315, session_title="Extra session", module_name="Data",
            session_date=date(2026, 10, 1), status="absent",
        )
        objects.create.return_value = created
        objects.filter.return_value.order_by.return_value.first.return_value = None
        request = RequestFactory().post(
            "/coach_api/coach/attendance/manual",
            data=json.dumps({
                "learnerId": "315", "date": "2026-10-01", "module": "Data",
                "sessionTitle": "Extra session", "status": "absent",
            }),
            content_type="application/json",
        )

        response = call_coach_view(coach_manual_attendance, request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 201)
        self.assertEqual((payload["manualId"], payload["source"]), ("9", "coach-manual"))
        objects.create.assert_called_once_with(
            owner_email="coach@example.com", learner_id=315, enrolment_id=901,
            learner_name="A Learner", learner_email="learner@example.com",
            created_by="coach@example.com", session_date=date(2026, 10, 1),
            module_name="Data", session_title="Extra session", status="absent",
        )

    @patch("coach_api.views.CoachManualAttendance.objects")
    def test_delete_is_scoped_to_the_authenticated_coach(self, objects):
        record = MagicMock()
        objects.filter.return_value.first.return_value = record
        request = RequestFactory().delete("/coach_api/coach/attendance/manual/9")

        response = call_coach_view(coach_manual_attendance, request, record_id=9)

        self.assertEqual(response.status_code, 204)
        objects.filter.assert_called_once_with(id=9, owner_email__iexact="coach@example.com")
        record.delete.assert_called_once_with()

    @patch("coach_api.views.CoachManualAttendance.objects")
    def test_view_as_mode_cannot_mutate_attendance(self, objects):
        request = RequestFactory().post("/coach_api/coach/attendance/manual", data="{}", content_type="application/json")
        request.coach_view_as = True

        response = call_coach_view(coach_manual_attendance, request)

        self.assertEqual(response.status_code, 403)
        objects.create.assert_not_called()

    @patch("coach_api.views._manual_attendance_learner", return_value=(SimpleNamespace(id=315, _caseload_source=SimpleNamespace(id=901)), {"id": "315"}))
    @patch("coach_api.views.CoachAttendanceSourceAdjustment.objects")
    @patch("learner_api.attendance_lectures.lecture_register", return_value=[{"source": "microsoft-teams", "session_id": "occ-8"}])
    def test_source_edit_is_saved_for_the_authorised_learner(self, register, objects, _find_learner):
        request = RequestFactory().patch(
            "/coach_api/coach/attendance/source",
            data=json.dumps({
                "learnerId": "315", "source": "microsoft-teams", "sourceId": "occ-8",
                "date": "2026-10-01", "module": "Data", "sessionTitle": "Session 8",
                "status": "present",
            }),
            content_type="application/json",
        )

        response = call_coach_view(coach_source_attendance, request)

        self.assertEqual(response.status_code, 200)
        objects.update_or_create.assert_called_once_with(
            learner_id=315, source="microsoft-teams", source_id="occ-8",
            defaults={
                "owner_email": "coach@example.com", "session_date": date(2026, 10, 1),
                "module_name": "Data", "session_title": "Session 8", "status": "present",
                "is_deleted": False, "updated_by": "coach@example.com",
            },
        )

    @patch("coach_api.views._manual_attendance_learner", return_value=(SimpleNamespace(id=315, _caseload_source=SimpleNamespace(id=901)), {"id": "315"}))
    @patch("coach_api.views.CoachAttendanceSourceAdjustment.objects")
    @patch("learner_api.attendance_lectures.lecture_register", return_value=[{"source": "kbc-attendance", "session_id": "kbc-1"}])
    def test_source_delete_hides_only_that_learners_attendance(self, register, objects, _find_learner):
        request = RequestFactory().delete(
            "/coach_api/coach/attendance/source",
            data=json.dumps({"learnerId": "315", "source": "kbc-attendance", "sourceId": "kbc-1"}),
            content_type="application/json",
        )

        response = call_coach_view(coach_source_attendance, request)

        self.assertEqual(response.status_code, 204)
        objects.update_or_create.assert_called_once_with(
            learner_id=315, source="kbc-attendance", source_id="kbc-1",
            defaults={"owner_email": "coach@example.com", "updated_by": "coach@example.com", "is_deleted": True},
        )

    @patch("coach_api.views._manual_attendance_learner", return_value=(SimpleNamespace(id=315, _caseload_source=SimpleNamespace(id=901)), {"id": "315"}))
    @patch("coach_api.views.CoachAttendanceSourceAdjustment.objects")
    @patch("learner_api.attendance_lectures.lecture_register", return_value=[])
    def test_orphan_source_edit_and_delete_are_rejected_before_writes(self, register, objects, find_learner):
        for method in ('patch', 'delete'):
            request = getattr(RequestFactory(), method)("/coach_api/coach/attendance/source",
                data=json.dumps({"learnerId": "315", "source": "microsoft-teams", "sourceId": "another-learner-occurrence"}),
                content_type="application/json")
            self.assertEqual(call_coach_view(coach_source_attendance, request).status_code, 404)
        objects.update_or_create.assert_not_called()
        self.assertEqual(register.call_args.kwargs, {'learner_profile_id': 315, 'apply_adjustments': False})


class LatestLearnerActivityTests(SimpleTestCase):
    def test_newest_action_wins_across_progress_and_activity_feeds(self):
        latest = latest_learning_activity(
            [{"kind": "assignment", "componentTitle": "Older assignment", "submittedAt": "2026-09-17T10:00:00Z"}],
            [{"kind": "quiz", "title": "Latest quiz", "completedAt": "2026-09-19T12:30:00Z"}],
        )

        self.assertEqual(latest["date"], "2026-09-19T12:30:00+00:00")
        self.assertEqual(latest["display"], "19 Sep 2026")
        self.assertEqual(latest["label"], "Latest quiz")

    @patch("coach_api.views.connections")
    def test_bulk_query_fetches_only_latest_progress_and_feed_candidates(self, connections):
        cursor = connections["enrolment"].cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        self.assertEqual(caseload_latest_learning_activities([SimpleNamespace(id=7)]), {})

        query, params = cursor.execute.call_args.args
        self.assertEqual(query.count("DISTINCT ON (learner_id)"), 2)
        self.assertEqual(query.count("entry_order ASC, id ASC"), 2)
        self.assertIn("COALESCE(kind, '') <> 'activity_event'", query)
        self.assertEqual(params, [[7], [7]])

    @patch("coach_api.views.connections")
    def test_bulk_query_keeps_activity_feed_events_in_latest_activity(self, connections):
        cursor = connections["enrolment"].cursor.return_value.__enter__.return_value
        occurred_at = timezone.make_aware(datetime(2026, 9, 20, 11, 30))
        cursor.fetchall.return_value = [(
            7, "feed", "activity_event", "", None, None,
            "video", "Latest video", occurred_at,
        )]

        latest = caseload_latest_learning_activities([SimpleNamespace(id=7)])[7]

        self.assertEqual(latest["date"], occurred_at.isoformat())
        self.assertEqual(latest["label"], "Latest video")

    @patch("coach_api.views.connections")
    def test_bulk_query_preserves_progress_timestamp_timezone_format(self, connections):
        cursor = connections["enrolment"].cursor.return_value.__enter__.return_value
        submitted_at = datetime.fromisoformat("2026-09-20T10:30:00+00:00")
        cursor.fetchall.return_value = [(
            7, "progress", "component", "Latest component", submitted_at,
            None, "", "", None,
        )]

        latest = caseload_latest_learning_activities([SimpleNamespace(id=7)])[7]

        self.assertEqual(latest["date"], submitted_at.isoformat())


class DashboardReviewHistoryTests(SimpleTestCase):
    @patch("coach_api.views.connections")
    @patch("coach_api.views.caseload_aptem_ids", return_value={7: 4321})
    def test_reviews_are_batched_and_split_into_mcm_and_reviews(self, aptem_ids, connections):
        columns = [
            "learner_id",
            "id",
            "aptem_review_id",
            "review_name",
            "review_type",
            "reviewer_name",
            "learner_name",
            "planned_scheduled_date",
            "completed_date",
            "status",
            "review_data",
            "extraction_status",
            "last_error",
        ]
        cursor = connections["default"].cursor.return_value.__enter__.return_value
        cursor.description = [(column,) for column in columns]
        cursor.fetchall.return_value = [
            (7, 2, "A-2", "Monthly Coaching Meeting", "MCM", "Coach", "Learner",
             None, date(2026, 9, 10), "Finished", "{}", "complete", None),
            (7, 1, "A-1", "Progress Review", "Progress Review", "Coach", "Learner",
             date(2026, 8, 20), None, "Planned", "{}", "complete", None),
        ]

        payload = dashboard_review_history([SimpleNamespace(id=7)])

        self.assertEqual(payload[7]["aptemId"], "4321")
        self.assertEqual([item["type"] for item in payload[7]["mcm"]], ["MCM"])
        self.assertEqual([item["type"] for item in payload[7]["reviews"]], ["Progress Review"])
        cursor.execute.assert_called_once()


class AuditHourOverlayTests(SimpleTestCase):
    """The coach caseload must quote the same OTJ hours the learner's own
    workspace shows -- Audit TP Planned / LMS Actual -- instead of the
    training-plan reflection totals, which read as 0h for most learners."""

    base = {
        "otjhCompleted": 0.0,
        "otjhTarget": 1,
        "otjhPlanned": 8.6,
        "overallProgress": 0,
        "overallProgressAvailable": True,
    }

    def test_audit_pair_replaces_the_reflection_totals(self):
        overlaid = apply_audit_hour_totals(
            dict(self.base),
            {"audit_tp_planned": 353.0, "audit_lms_actual": 178.45},
        )

        self.assertEqual(overlaid["otjhCompleted"], 178.45)
        self.assertEqual(overlaid["otjhPlanned"], 353.0)
        self.assertEqual(overlaid["otjhSource"], "audit")

    def test_placeholder_target_falls_back_to_the_whole_audit_plan(self):
        """otjhTarget is floored at 1 when no week pacing was computed. That
        floor is not a ratio, so rescaling by it would invent a target."""
        overlaid = apply_audit_hour_totals(
            dict(self.base),
            {"audit_tp_planned": 353.0, "audit_lms_actual": 178.45},
        )

        self.assertEqual(overlaid["otjhTarget"], 353.0)
        self.assertEqual(overlaid["overallProgress"], 51)

    def test_real_week_pacing_is_carried_over_to_the_audit_plan(self):
        paced = dict(self.base, otjhTarget=4.3, otjhPlanned=8.6)

        overlaid = apply_audit_hour_totals(
            paced, {"audit_tp_planned": 353.0, "audit_lms_actual": 100.0}
        )

        self.assertEqual(overlaid["otjhTarget"], 176.5)

    def test_percentage_is_recomputed_so_the_card_agrees_with_itself(self):
        overlaid = apply_audit_hour_totals(
            dict(self.base, otjhTarget=8.6),
            {"audit_tp_planned": 353.0, "audit_lms_actual": 304.97},
        )

        self.assertEqual(overlaid["overallProgress"], 86)
        self.assertTrue(overlaid["overallProgressAvailable"])

    def test_a_missing_planned_figure_leaves_the_target_alone(self):
        overlaid = apply_audit_hour_totals(
            dict(self.base), {"audit_tp_planned": None, "audit_lms_actual": 12.5}
        )

        self.assertEqual(overlaid["otjhCompleted"], 12.5)
        self.assertEqual(overlaid["otjhTarget"], 1)
        self.assertEqual(overlaid["otjhPlanned"], 8.6)

    def test_a_zero_stored_plan_does_not_divide_by_zero(self):
        overlaid = apply_audit_hour_totals(
            dict(self.base, otjhPlanned=0),
            {"audit_tp_planned": 353.0, "audit_lms_actual": 50.0},
        )

        self.assertEqual(overlaid["otjhTarget"], 353.0)

    def test_learners_without_audit_figures_keep_their_own(self):
        self.assertEqual(apply_audit_hour_totals(dict(self.base), None), self.base)
        self.assertEqual(apply_audit_hour_totals(dict(self.base), {}), self.base)


class OtjhTargetContractTests(SimpleTestCase):
    def assert_contract(self, actual, target, planned, variance, status):
        payload = {
            "otjhCompleted": actual,
            "otjhTarget": target,
            "otjhPlanned": planned,
        }
        result = apply_aptem_variance_status(payload, "4317")
        self.assertEqual(result["otjhVariance"], variance)
        self.assertEqual(result["otjhStatus"], status)
        self.assertEqual(result["otjhPlanned"], planned)

    def test_valid_target_is_need_attention(self):
        self.assert_contract(242, 270, 576, -28.0, "Need Attention")

    def test_large_deficit_is_at_risk(self):
        self.assert_contract(200, 250, 576, -50.0, "At Risk")

    def test_twenty_hour_gap_needs_attention(self):
        self.assert_contract(250, 270, 576, -20.0, "Need Attention")

    def test_missing_target_leaves_existing_status_untouched(self):
        payload = {"otjhCompleted": 242, "otjhTarget": None, "otjhPlanned": 576}
        result = apply_aptem_variance_status(payload, "4317")
        self.assertIs(result, payload)
        self.assertNotIn("otjhVariance", result)
        self.assertNotIn("otjhStatus", result)


    def test_zero_target_leaves_existing_status_untouched(self):
        payload = {"otjhCompleted": 0, "otjhTarget": 0, "otjhPlanned": 576}
        result = apply_aptem_variance_status(payload, "4317")
        self.assertIs(result, payload)
        self.assertNotIn("otjhVariance", result)
        self.assertNotIn("otjhStatus", result)

    @patch("coach_api.views.read_audit_hour_totals_bulk")
    @patch("coach_api.views.audit_connection")
    def test_totals_are_keyed_by_profile_id_via_the_enrolment_aptem_id(
        self, connection, read_bulk
    ):
        read_bulk.return_value = {4321: {"audit_tp_planned": 353.0, "audit_lms_actual": 178.45}}
        linked = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))
        # No Aptem link, and a source row that was never resolved.
        unlinked = SimpleNamespace(id=8, _caseload_source=SimpleNamespace(aptem_id=None))
        sourceless = SimpleNamespace(id=9, _caseload_source=None)

        totals = caseload_audit_hour_totals([linked, unlinked, sourceless])

        self.assertEqual(totals, {7: {"audit_tp_planned": 353.0, "audit_lms_actual": 178.45}})
        self.assertEqual(list(read_bulk.call_args.args[1]), [4321])

    @patch("coach_api.views.audit_connection")
    def test_an_unreachable_audit_mirror_leaves_the_caseload_renderable(self, connection):
        connection.side_effect = DatabaseError("audit mirror down")
        linked = SimpleNamespace(id=7, _caseload_source=SimpleNamespace(aptem_id="4321"))

        self.assertEqual(caseload_audit_hour_totals([linked]), {})

    def test_no_query_runs_for_a_caseload_with_no_aptem_links(self):
        with patch("coach_api.views.audit_connection") as connection:
            self.assertEqual(caseload_audit_hour_totals([]), {})
            self.assertEqual(
                caseload_audit_hour_totals(
                    [SimpleNamespace(id=8, _caseload_source=SimpleNamespace(aptem_id=""))]
                ),
                {},
            )
        connection.assert_not_called()


class OtjhToDateContractTests(SimpleTestCase):
    def test_api_emits_one_paced_target_and_rag_contract(self):
        payload = {
            "otjhCompleted": 40,
            "otjhPlanned": 120,
            "otjhTarget": 999,
            "startDate": "01 Jun 2026",
            "plannedEndDate": "01 Jul 2026",
        }

        result = apply_otjh_to_date_metrics(payload, today=date(2026, 6, 15))

        self.assertEqual(result["otjhTargetAsOfToday"], 56.0)
        self.assertEqual(result["otjhProgressAsOfToday"], 71.43)
        self.assertEqual(result["otjhShortfallHours"], 16.0)
        self.assertEqual(result["otjhDeltaHours"], -16.0)
        self.assertEqual(result["otjhRagStatus"], "on-track")
        self.assertEqual(result["otjhRagSource"], "ssot:programme-plan-window")

    def test_missing_window_does_not_use_legacy_target_fallback(self):
        result = apply_otjh_to_date_metrics({
            "otjhCompleted": 9,
            "otjhPlanned": 0,
            "otjhTarget": 50,
            "startDate": "--",
            "plannedEndDate": "--",
        }, today=date(2026, 6, 15))

        self.assertIsNone(result["otjhTargetAsOfToday"])
        self.assertIsNone(result["otjhProgressAsOfToday"])
        self.assertEqual(result["otjhRagStatus"], "unavailable")
        self.assertEqual(result["otjhRagSource"], "unavailable")


class CanonicalCoachMetricsTests(SimpleTestCase):
    def test_workspace_metrics_replace_case_file_values_with_learner_overview(self):
        payload = {"otjhCompleted": 99, "otjhTarget": 123, "otjhPlanned": 123,
                   "ksbCompleted": 16, "ksbTarget": 16, "ksbProgress": 100,
                   "ksbProgressAvailable": True, "enrollmentStatus": "active"}
        metrics = {"migrated": True, "aptem_planned_total": 576,
                   "otjh": {"actual": 164.87, "completed_actual": 100, "planned": 256},
                   "programme": {"completed": 137, "total": 157, "percent": 87.3, "status": "ready"},
                   "ksb": {"completed": 498, "total": 584, "percent": 85.3, "status": "ready"}}

        result = apply_canonical_learner_metrics(payload, metrics, learner_workspace=True)

        self.assertEqual((result["otjhCompleted"], result["otjhTarget"]), (164.87, 576))
        self.assertEqual((result["componentsCompleted"], result["componentsPlanned"], result["activityProgress"]),
                         (137, 157, 87.3))
        self.assertEqual((result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]), (498, 584, 85.3))
        self.assertEqual(metrics["otjh"]["completed_actual"], 100)

    def test_workspace_unavailable_metrics_do_not_fall_back_to_old_case_file_values(self):
        for metrics in (None, {"otjh": {}, "programme": {}, "ksb": {"status": "unavailable"}}):
            with self.subTest(metrics=metrics):
                result = apply_canonical_learner_metrics({
                    "otjhCompleted": 99, "otjhTarget": 123, "otjhPlanned": 123,
                    "ksbCompleted": 16, "ksbTarget": 16, "ksbProgress": 100,
                    "ksbProgressAvailable": True, "enrollmentStatus": "active",
                }, metrics, learner_workspace=True)
                self.assertIsNone(result["otjhCompleted"])
                self.assertIsNone(result["otjhTarget"])
                self.assertIsNone(result["ksbCompleted"])
                self.assertFalse(result["ksbProgressAvailable"])
                self.assertFalse(result["activityProgressAvailable"])

    def test_workspace_native_metrics_use_canonical_plan_and_actual(self):
        result = apply_canonical_learner_metrics({"ksbProgressAvailable": False}, {
            "migrated": False, "otjh": {"actual": 4, "completed_actual": 2, "planned": 20},
            "programme": {"completed": 1, "total": 2, "percent": 50, "status": "ready"},
            "ksb": {"completed": 1, "total": 3, "percent": 33.3, "status": "ready"},
        }, learner_workspace=True)
        self.assertEqual((result["otjhCompleted"], result["otjhTarget"]), (4, 20))

    def canonical_loader_patches(self, *, read_metrics):
        mocks = {
            "canonical_learning": SimpleNamespace(metrics_bulk=MagicMock(return_value={})),
            "load_direct_progress_records_bulk": MagicMock(return_value={
                10: [{"componentId": "native-null"}],
                11: [{"componentId": "native-aptem"}],
            }),
            "load_subject_attempts_bulk": MagicMock(return_value={
                (11, 42): {("group-1", "activity-1")},
            }),
            "load_manual_hours_bulk": MagicMock(return_value={42: 7.5}),
            "load_reflection_submissions_bulk": MagicMock(return_value={
                ("commercial", "10"): [{"id": "reflection-null"}],
                ("apprenticeship", "11"): [{"id": "reflection-aptem"}],
            }),
            "load_audit_inputs_bulk": MagicMock(return_value={
                42: {"identity": ("learner@example.test",), "historical": [], "aptem_planned_total": 400},
            }),
            "load_native_progress_bulk": MagicMock(return_value={
                10: [{"componentId": "native-null", "passed": True}],
                11: [{"componentId": "native-aptem", "passed": True}],
            }),
            "_effective_plan_ids": MagicMock(side_effect=lambda source, _cache: [source.pk * 100]),
            "load_native_components_bulk": MagicMock(return_value={
                "1000": [{"id": "native-null"}],
                "1100": [{"id": "native-aptem"}],
            }),
            "load_planned_hours_documents_bulk": MagicMock(return_value={
                (10, "commercial"): {"otjh": {"plannedTotal": 24}},
            }),
            "load_contracts_bulk": MagicMock(return_value={42: {"training_plan_planned_hours": 400}}),
            "load_accepted_ksb_rows_bulk": MagicMock(return_value={11: [{"source_ref": "legacy"}]}),
            "load_export_links_bulk": MagicMock(return_value={}),
            "read_metrics": read_metrics,
        }
        return patch.multiple("coach_api.views", **mocks), mocks

    def test_null_aptem_identity_keeps_native_metrics_and_skips_aptem_lookups(self):
        captured = {}

        def read_metrics(source, kind, preloaded):
            captured[source.pk] = preloaded
            return {
                "programme": {"completed": len(preloaded["native_progress"]), "total": len(preloaded["native_components"])},
                "otjh": {"planned": (preloaded["planned_hours_document"] or {}).get("otjh", {}).get("plannedTotal")},
                "ksb": {"completed": len(preloaded["native_progress"])},
            }

        native = SimpleNamespace(pk=10, aptem_id=None, email="native@example.test")
        row = SimpleNamespace(id=100, learner_type="commercial", _caseload_source=native)

        patches, mocks = self.canonical_loader_patches(read_metrics=MagicMock(side_effect=read_metrics))
        with patches:
            result = caseload_canonical_metrics([row])

        self.assertEqual(result[100]["programme"], {"completed": 1, "total": 1})
        self.assertEqual(result[100]["otjh"]["planned"], 24)
        self.assertEqual(result[100]["ksb"]["completed"], 1)
        self.assertEqual(captured[10]["subject_attempts"], set())
        self.assertIsNone(captured[10]["audit_inputs"])
        self.assertIsNone(captured[10]["planned_hours_contract"])
        mocks["load_subject_attempts_bulk"].assert_called_once_with([])
        mocks["load_manual_hours_bulk"].assert_called_once_with([])
        mocks["load_audit_inputs_bulk"].assert_called_once_with([])

    def test_mixed_caseload_only_builds_external_keys_for_valid_aptem_ids(self):
        captured = {}

        def read_metrics(source, kind, preloaded):
            captured[source.pk] = preloaded
            return {"source": source.pk, "nativeProgress": preloaded["native_progress"]}

        rows = [
            SimpleNamespace(
                id=100,
                learner_type="commercial",
                _caseload_source=SimpleNamespace(pk=10, aptem_id=None, email="native@example.test"),
            ),
            SimpleNamespace(
                id=101,
                learner_type="apprenticeship",
                _caseload_source=SimpleNamespace(pk=11, aptem_id=" 42 ", email="learner@example.test"),
            ),
        ]

        patches, mocks = self.canonical_loader_patches(read_metrics=MagicMock(side_effect=read_metrics))
        with patches:
            result = caseload_canonical_metrics(rows)

        self.assertEqual(set(result), {100, 101})
        self.assertEqual(captured[10]["subject_attempts"], set())
        self.assertEqual(captured[11]["subject_attempts"], {("group-1", "activity-1")})
        self.assertEqual(result[101]["source"], 11)
        mocks["load_subject_attempts_bulk"].assert_called_once_with([(11, 42)])
        mocks["load_accepted_ksb_rows_bulk"].assert_called_once()

    def test_mixed_caseload_reuses_learner_dashboard_metrics_for_aptem(self):
        captured = {}

        def read_metrics(source, kind, preloaded):
            captured[source.pk] = preloaded
            return {"source": "existing", "programme": {"completed": 1, "total": 1}}

        native = SimpleNamespace(pk=10, aptem_id=None)
        aptem = SimpleNamespace(pk=11, aptem_id="42")
        rows = [
            SimpleNamespace(id=100, learner_type="commercial", enrolment_id=10, _caseload_source=native),
            SimpleNamespace(id=101, learner_type="apprenticeship", enrolment_id=11, _caseload_source=aptem),
        ]
        patches, mocks = self.canonical_loader_patches(read_metrics=MagicMock(side_effect=read_metrics))
        canonical = {
            "programme": {"completed": 443, "total": 499, "percent": 88.78, "status": "ready"},
            "otjh": {"actual": 308.11, "planned": None},
            "ksb": {"completed": 475, "total": 711, "percent": 66.81, "status": "ready"},
        }
        mocks["canonical_learning"].metrics_bulk.return_value = {11: canonical}
        with patches:
            result = caseload_canonical_metrics(rows)

        self.assertEqual(result[101]["programme"], {"completed": 443, "total": 499, "percent": 88.78, "status": "ready"})
        self.assertEqual(result[101]["otjh"], {"actual": 308.11, "planned": None})
        self.assertEqual(result[101]["ksb"], {"completed": 475, "total": 711, "percent": 66.81, "status": "ready"})
        self.assertEqual(result[100]["source"], "existing")
        self.assertEqual(set(captured), {10})
        mocks["load_direct_progress_records_bulk"].assert_called_once_with([10])
        mocks["canonical_learning"].metrics_bulk.assert_called_once_with([10, 11])

    def test_identity_review_for_one_learner_does_not_hide_the_rest_of_the_caseload(self):
        def read_metrics(source, kind, preloaded):
            if source.pk == 10:
                raise ServiceError('The consolidated learner identity needs review.',
                                   'identity_review_required', 409)
            return {"source": source.pk, "programme": {"completed": 1, "total": 1}}

        rows = [
            SimpleNamespace(id=100, learner_type="commercial",
                            _caseload_source=SimpleNamespace(pk=10, aptem_id=None)),
            SimpleNamespace(id=101, learner_type="commercial",
                            _caseload_source=SimpleNamespace(pk=11, aptem_id=None)),
        ]
        patches, _ = self.canonical_loader_patches(
            read_metrics=MagicMock(side_effect=read_metrics),
        )

        with patches:
            result = caseload_canonical_metrics(rows)

        self.assertNotIn(100, result)
        self.assertEqual(result[101]["programme"], {"completed": 1, "total": 1})

    def test_zero_learner_caseload_runs_no_metric_loaders(self):
        read_metrics = MagicMock()
        patches, mocks = self.canonical_loader_patches(read_metrics=read_metrics)
        with patches:
            self.assertEqual(caseload_canonical_metrics([]), {})

        read_metrics.assert_not_called()
        mocks["load_direct_progress_records_bulk"].assert_not_called()

    def test_canonical_learners_skip_the_legacy_per_learner_loader(self):
        canonical = {10: {"source": "canonical"}}
        read_metrics = MagicMock(return_value={"source": "legacy"})
        rows = [
            SimpleNamespace(id=100, learner_type="commercial",
                            _caseload_source=SimpleNamespace(pk=10, aptem_id=None)),
            SimpleNamespace(id=101, learner_type="commercial",
                            _caseload_source=SimpleNamespace(pk=11, aptem_id=None)),
        ]
        patches, mocks = self.canonical_loader_patches(read_metrics=read_metrics)
        mocks["canonical_learning"].metrics_bulk.return_value = canonical

        with patches:
            result = caseload_canonical_metrics(rows)

        self.assertEqual(result[100], canonical[10])
        self.assertEqual(result[101], {"source": "legacy"})
        read_metrics.assert_called_once()
        self.assertEqual(read_metrics.call_args.args[0].pk, 11)
        mocks["load_direct_progress_records_bulk"].assert_called_once_with([11])

    def test_failed_canonical_identity_is_unavailable_without_legacy_fallback(self):
        read_metrics = MagicMock(return_value={"source": "legacy"})
        rows = [
            SimpleNamespace(id=100, learner_type="commercial",
                            _caseload_source=SimpleNamespace(pk=10, aptem_id=None)),
        ]
        patches, mocks = self.canonical_loader_patches(read_metrics=read_metrics)
        mocks["canonical_learning"].metrics_bulk.return_value = {10: None}

        with patches:
            result = caseload_canonical_metrics(rows)

        self.assertEqual(result[100]["programme"]["status"], "unavailable")
        self.assertEqual(result[100]["ksb"]["status"], "unavailable")
        read_metrics.assert_not_called()
        mocks["load_direct_progress_records_bulk"].assert_not_called()

    def test_otjh_status_uses_agreed_variance_boundaries(self):
        self.assertEqual(otjh_status_from_variance(Decimal("-19.99")), "On track")
        self.assertEqual(otjh_status_from_variance(Decimal("-20")), "On track")
        self.assertEqual(otjh_status_from_variance(Decimal("-39.99")), "Need attention")
        self.assertEqual(otjh_status_from_variance(Decimal("-40")), "Need attention")
        self.assertEqual(otjh_status_from_variance(Decimal("-41")), "At risk")
        self.assertEqual(otjh_status_from_variance(Decimal("5")), "On track")

    def test_detailed_table_restores_the_case_file_otjh_and_ksb_figures(self):
        enriched = {
            "otjhCompleted": 163.2, "otjhTarget": 256,
            "ksbCompleted": 445, "ksbTarget": 777, "ksbProgress": 57,
            "ksbProgressAvailable": True, "ksbStatus": "In Progress",
            "activityProgress": 70, "attendancePresent": 38, "attendanceSessions": 41,
        }
        case_file = {
            "otjhTarget": 576,
            "ksbCompleted": 14, "ksbTarget": 14, "ksbProgress": 100,
            "ksbProgressAvailable": True, "ksbStatus": "Completed",
        }

        result = restore_case_file_table_metrics(enriched, case_file)

        self.assertEqual((result["otjhCompleted"], result["otjhTarget"]), (163.2, 576))
        self.assertEqual((result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]), (14, 14, 100))
        self.assertEqual(result["activityProgress"], 70)
        self.assertEqual((result["attendancePresent"], result["attendanceSessions"]), (38, 41))

    def test_case_file_snapshot_uses_the_source_learner_planned_hours(self):
        row = SimpleNamespace(_caseload_source=SimpleNamespace(planned_hours="576.00"))

        snapshot = case_file_table_metrics_snapshot(row, {
            "otjhTarget": 256, "otjhPlanned": 256,
            "ksbCompleted": 16, "ksbTarget": 16, "ksbProgress": 100,
            "ksbProgressAvailable": True, "ksbStatus": "Completed",
        })

        self.assertEqual((snapshot["otjhTarget"], snapshot["otjhPlanned"]), (576, 576))

    def test_caseload_table_uses_the_exact_profile_source_for_all_four_metrics(self):
        payload = {
            "otjhCompleted": 56.7, "otjhTarget": 82.9, "otjhPlanned": 400,
            "overallProgress": 68, "overallProgressAvailable": True,
            "ksbCompleted": 3, "ksbTarget": 20, "ksbProgress": 14,
            "ksbProgressAvailable": True, "enrollmentStatus": "active",
            "progressVariance": "-0.05", "otjhStatus": "Need attention",
        }
        metrics = {
            "programme": {"completed": 34, "total": 379, "percent": 8.97, "status": "ready"},
            "otjh": {"actual": 74.71, "planned": 581.75},
            "ksb": {"completed": 14, "total": 20, "percent": 70, "status": "ready"},
        }

        result = apply_canonical_learner_metrics(payload, metrics)

        self.assertEqual(result["otjhCompleted"], 74.71)
        self.assertEqual((result["otjhCompleted"], result["otjhTarget"], result["otjhPlanned"]),
                         (74.71, 581.75, 581.75))
        self.assertEqual(result["overallProgress"], 13)
        self.assertEqual(result["otjhStatus"], "At risk")
        self.assertEqual(result["programmeProgress"], 8.97)
        self.assertEqual((result["componentsCompleted"], result["componentsPlanned"]), (34, 379))
        self.assertEqual((result["activityProgress"], result["activityProgressAvailable"]), (8.97, True))
        self.assertEqual((result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]), (14, 20, 70))
        self.assertEqual(result["metricsSource"], "learner-dashboard")

    def test_aptem_overlay_uses_the_same_detail_plan_fallback_as_the_profile(self):
        payload = {
            "otjhCompleted": 111.9, "otjhTarget": 576, "otjhPlanned": 576,
            "overallProgress": 100, "overallProgressAvailable": True,
            "ksbCompleted": 14, "ksbTarget": 14, "ksbProgress": 100,
            "ksbProgressAvailable": True, "enrollmentStatus": "active",
        }
        metrics = {
            "migrated": True,
            "programme": {"completed": 284, "total": 381, "percent": 74.54, "status": "ready"},
            "otjh": {"actual": 281.9, "planned": 256},
            "ksb": {"completed": 14, "total": 14, "percent": 100, "status": "ready"},
        }

        result = apply_canonical_learner_metrics(payload, metrics)

        self.assertEqual((result["programmeCompleted"], result["programmeTarget"], result["programmeProgress"]), (284, 381, 74.54))
        self.assertEqual((result["componentsCompleted"], result["componentsPlanned"], result["activityProgress"]), (284, 381, 74.54))
        self.assertEqual((result["otjhCompleted"], result["otjhTarget"], result["otjhPlanned"]), (281.9, 256, 256))
        self.assertEqual(result["otjhActual"], 281.9)
        self.assertEqual((result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]), (14, 14, 100))
        self.assertEqual((result["overallProgress"], result["overallProgressAvailable"]), (74.54, True))
        self.assertEqual((result["metricsSource"], result["ksbSource"], result["otjhSource"]),
                         ("learner-dashboard", "learner-dashboard", "learner-dashboard"))

    def test_caseload_row_uses_the_case_file_metrics_instead_of_stored_fallbacks(self):
        payload = {
            "otjhCompleted": 70.5, "otjhTarget": None, "otjhPlanned": None,
            "overallProgress": 100, "overallProgressAvailable": True,
            "componentsCompleted": 27, "componentsPlanned": 27,
            "activityProgress": 100, "activityProgressAvailable": True,
            "ksbCompleted": 33, "ksbTarget": 33, "ksbProgress": 100,
            "ksbProgressAvailable": True, "enrollmentStatus": "active",
        }
        case_file_metrics = {
            "migrated": True,
            "programme": {"completed": 34, "total": 244, "percent": 13.92, "status": "ready"},
            "otjh": {"actual": 116.8, "completed_actual": 116.8, "planned": 352},
            "ksb": {"completed": 33, "total": 33, "percent": 100, "status": "ready"},
        }

        result = apply_canonical_learner_metrics(payload, case_file_metrics)

        self.assertEqual((result["otjhCompleted"], result["otjhTarget"]), (116.8, 352))
        self.assertEqual(result["overallProgress"], 13.92)
        self.assertEqual(
            (result["componentsCompleted"], result["componentsPlanned"], result["activityProgress"]),
            (34, 244, 13.92),
        )
        self.assertEqual((result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]), (33, 33, 100))

    def test_coach_overlay_uses_shared_metrics_and_profile_plan_fallback(self):
        from learner_api.canonical_learning import metrics_from_records

        records = [
            {"id": 1, "accepted": True, "completed": True, "actual_seconds": 5400,
             "ksbs": ["K1", "S1"], "segments": [], "sources": []},
            {"id": 2, "accepted": False, "completed": True, "actual_seconds": 900,
             "ksbs": ["K1"], "segments": [], "sources": []},
        ]
        canonical = metrics_from_records(records, {})
        result = apply_canonical_learner_metrics({
            "otjhCompleted": 999, "otjhTarget": 42, "otjhPlanned": 42,
            "ksbProgressAvailable": False, "enrollmentStatus": "active",
        }, canonical)

        self.assertEqual(
            (result["programmeCompleted"], result["programmeTarget"], result["programmeProgress"]),
            (canonical["programme"]["completed"], canonical["programme"]["total"], canonical["programme"]["percent"]),
        )
        self.assertEqual(result["otjhCompleted"], canonical["otjh"]["completed_actual"])
        self.assertEqual(result["otjhActual"], canonical["otjh"]["actual"])
        self.assertEqual(result["otjhPlanned"], 42)
        self.assertEqual(result["otjhTarget"], 42)
        self.assertEqual(
            (result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"]),
            (canonical["ksb"]["completed"], canonical["ksb"]["total"], canonical["ksb"]["percent"]),
        )

    def test_unavailable_metrics_leave_the_existing_snapshot_untouched(self):
        payload = {"otjhCompleted": 4, "otjhTarget": 5}
        self.assertIs(apply_canonical_learner_metrics(payload, None), payload)

    def test_unavailable_canonical_ksb_metrics_keep_the_caseload_snapshot(self):
        payload = {
            "ksbCompleted": 4,
            "ksbTarget": 20,
            "ksbProgress": 20,
            "ksbProgressAvailable": True,
            "ksbStatus": "Started",
        }
        metrics = {
            "programme": {"completed": 1, "total": 2, "percent": 50, "status": "ready"},
            "otjh": {},
            "ksb": {"completed": None, "total": None, "percent": None, "status": "unavailable"},
        }

        result = apply_canonical_learner_metrics(payload, metrics)

        self.assertEqual(
            (result["ksbCompleted"], result["ksbTarget"], result["ksbProgress"], result["ksbProgressAvailable"]),
            (4, 20, 20, True),
        )
        self.assertEqual(result["ksbStatus"], "Started")

    def test_missing_ksb_status_does_not_break_canonical_overlay(self):
        payload = {"otjhCompleted": 4, "otjhTarget": 5, "ksbProgressAvailable": False}
        metrics = {
            "programme": {"completed": 1, "total": 2, "percent": 50, "status": "ready"},
            "otjh": {},
            "ksb": {"completed": None, "total": None, "percent": None, "status": "unavailable"},
        }
        result = apply_canonical_learner_metrics(payload, metrics)
        self.assertNotIn("ksbStatus", result)
        self.assertEqual(result["metricsSource"], "learner-dashboard")

    def test_unavailable_programme_clears_activity_ratio_and_percentage_together(self):
        payload = {
            "componentsCompleted": 158, "componentsPlanned": 133,
            "activityProgress": 100, "activityProgressAvailable": True,
            "otjhCompleted": 4, "otjhTarget": 5, "ksbProgressAvailable": False,
        }
        metrics = {
            "programme": {"completed": None, "total": None, "percent": None, "status": "unavailable"},
            "otjh": {}, "ksb": {"status": "unavailable"},
        }

        result = apply_canonical_learner_metrics(payload, metrics)

        self.assertEqual((result["componentsCompleted"], result["componentsPlanned"]), (None, None))
        self.assertEqual((result["activityProgress"], result["activityProgressAvailable"]), (None, False))

    def test_ready_ksb_status_is_still_derived(self):
        payload = {"otjhCompleted": 4, "otjhTarget": 5, "ksbProgressAvailable": False}
        metrics = {
            "programme": {}, "otjh": {},
            "ksb": {"completed": 2, "total": 4, "percent": 50, "status": "ready"},
        }
        result = apply_canonical_learner_metrics(payload, metrics)
        self.assertEqual(result["ksbStatus"], "In Progress")

    def test_aptem_canonical_ksb_evidence_is_merged_and_parent_normalized(self):
        payload = {"ksbCompletedDetails": [{"code": "B1", "sources": []}]}
        metrics = {"_ksb_evidence_sources": [{
            "id": "audit:7:11",
            "title": "Historical activity",
            "typeLabel": "manual",
            "codes": ["B1.1", "B1"],
        }]}
        result = apply_canonical_ksb_evidence(payload, metrics, 987)
        self.assertEqual(len(result["ksbCompletedDetails"]), 1)
        self.assertEqual(len(result["ksbCompletedDetails"][0]["sources"]), 1)
        self.assertEqual(result["ksbCompletedDetails"][0]["sources"][0]["id"], "audit:7:11")

    def test_aptem_canonical_ksb_evidence_is_deduplicated(self):
        payload = {"ksbCompletedDetails": []}
        source = {"id": "manual:42", "title": "Accepted journal", "codes": ["K1"]}
        metrics = {"_ksb_evidence_sources": [source, dict(source)]}
        result = apply_canonical_ksb_evidence(payload, metrics, 987)
        self.assertEqual(len(result["ksbCompletedDetails"]), 1)
        self.assertEqual(len(result["ksbCompletedDetails"][0]["sources"]), 1)

    def test_failed_or_non_aptem_evidence_is_not_added(self):
        payload = {"ksbCompletedDetails": []}
        metrics = {"_ksb_evidence_sources": [{"id": "failed:1", "codes": ["S1"]}]}
        self.assertEqual(apply_canonical_ksb_evidence(payload, metrics, None)["ksbCompletedDetails"], [])


class SourceProfileIdentityTests(SimpleTestCase):
    @patch("coach_api.views.EnrolmentUser.all_learners")
    def test_source_rows_are_keyed_by_stable_enrolment_id(self, manager):
        source = SimpleNamespace(
            id=19,
            email="Mahmoud.Fouda@kentbusinesscollege.com",
            learner_type="commercial",
        )
        manager.filter.return_value = [source]
        profile = SimpleNamespace(
            id=2,
            enrolment_id=19,
            email="mahmoud.fouda@kentbusinesscollege.com",
        )

        commercial, apprenticeship = fetch_source_schedule_rows([profile])

        self.assertEqual(commercial, {2: source})
        self.assertEqual(apprenticeship, {})
        manager.filter.assert_called_once_with(pk__in={19: 2})

    @patch("coach_api.views.load_contracts_bulk")
    @patch("coach_api.views.fetch_source_schedule_rows")
    def test_source_attachment_adds_the_verified_contract_projection(self, fetch_sources, load_contracts):
        source = SimpleNamespace(id=19, aptem_id="4321", learner_type="apprenticeship")
        profile = SimpleNamespace(id=2, enrolment_id=19, programme="Apprenticeship")
        fetch_sources.return_value = ({}, {2: source})
        load_contracts.return_value = {4321: {
            "training_plan_planned_hours": 576,
            "program_start_date": "2026-08-26",
            "planned_end_date": "2027-08-25",
        }}

        attach_caseload_source_rows([profile])

        self.assertIs(profile._caseload_source, source)
        self.assertEqual(profile._caseload_contract["training_plan_planned_hours"], 576)
        load_contracts.assert_called_once_with([4321])


class CoachKsbEvidenceTests(SimpleTestCase):
    def test_video_evidence_uses_authored_component_name_instead_of_media_type(self):
        target = [{"code": "B1", "type": "Behaviours", "description": "Behaviour 1"}]
        progress = [{
            "kind": "video", "componentId": "VIDEO-1", "title": "Video",
            "passed": True, "ksbs": ["B1"],
        }]
        training_plan = [{"moduleTitle": "Module 1", "weeks": [{"components": [{
            "componentId": "VIDEO-1", "componentTitle": "Agile working techniques",
        }]}]}]

        details = build_ksb_completed_details(target, {"B1"}, progress, [], training_plan)

        self.assertEqual(details[0]["sources"][0]["title"], "Agile working techniques")
        self.assertEqual(details[0]["sources"][0]["componentId"], "VIDEO-1")

    def test_failed_quiz_codes_do_not_count_as_completed_ksbs(self):
        completed = completed_ksb_codes(
            [
                {"kind": "quiz", "passed": False, "ksbs": ["K1", "K2", "S1"]},
                {"kind": "video", "ksbs": ["K3.1", "B2.2"]},
            ],
            [],
        )

        self.assertEqual(completed, {"K3", "B2"})

    def test_failed_component_activity_does_not_count_even_with_valid_lineage(self):
        """The gate is the completion rule, not the kind.

        A Component recorded as not passed carries a real componentId, real
        curriculum lineage and a real authored KSB mapping — everything that
        makes it look legitimate to a report that only special-cases quizzes.
        """
        completed = completed_ksb_codes(
            [
                {
                    "kind": "component",
                    "componentId": "COMP-20260816E2E",
                    "componentType": "assignment",
                    "moduleId": "MOD-E2E",
                    "weekId": "WEEK-E2E",
                    "passed": False,
                    "ksbs": ["K1"],
                },
                {"kind": "component", "componentId": "COMP-OTHER", "passed": True, "ksbs": ["S2"]},
            ],
            [],
        )

        self.assertEqual(completed, {"S2"})

    def test_an_unresolved_quiz_attempt_does_not_count(self):
        self.assertEqual(completed_ksb_codes([{"kind": "quiz", "ksbs": ["K1"]}], []), set())

    def test_a_failed_activity_feed_entry_does_not_count(self):
        """Activity-feed rows carry `passed` too, and were never gated at all."""
        completed = completed_ksb_codes(
            [],
            [{"kind": "component", "componentId": "COMP-X", "passed": False, "ksbs": ["B1"]}],
        )

        self.assertEqual(completed, set())

    def test_ksb_completed_details_omit_a_failed_component(self):
        target = [{"code": "K1", "type": "Knowledge", "description": "Knowledge 1"}]
        failed = {
            "kind": "component",
            "componentId": "COMP-20260816E2E",
            "componentTitle": "E2E activity",
            "passed": False,
            "ksbs": ["K1"],
            "submittedAt": "2026-08-17T09:00:00Z",
        }

        # As the coach payload actually assembles it: the completed set comes
        # from completed_ksb_codes, so a failed-only K1 never becomes a row.
        self.assertEqual(
            build_ksb_completed_details(target, completed_ksb_codes([failed], []), [failed], [], []),
            [],
        )
        # And even when a code is completed by other evidence, the failed
        # attempt is not offered as the evidence for it.
        self.assertEqual(
            build_ksb_completed_details(target, {"K1"}, [failed], [], [])[0]["sources"], [],
        )

        succeeded = {**failed, "passed": True}
        passed_details = build_ksb_completed_details(
            target, completed_ksb_codes([succeeded], []), [succeeded], [], [],
        )
        self.assertEqual([item["code"] for item in passed_details], ["K1"])
        self.assertEqual(len(passed_details[0]["sources"]), 1)


class CoachCaseloadLegacyRelationTests(SimpleTestCase):
    """The caseload must not prefetch a relation the database no longer has.

    ``LearnerProfile.assigned_ksbs`` maps the pre-normalisation
    ``Learner.learner_ksbs`` snapshot, which is absent from the current
    database. ``LearnerProfile.ksbs`` tolerates that, but a queryset-level
    ``prefetch_related`` raises first, which turned the entire coach caseload
    into a 500 against the live schema. Probe, then prefetch — the same shape
    already used for the retired activity-events relation.
    """

    def _prefetches(self, *, ksbs_exists, events_exist):
        captured = {}

        class Queryset:
            def annotate(self, **kwargs):
                return self

            def filter(self, **kwargs):
                return self

            def prefetch_related(self, *names):
                captured['names'] = list(names)
                return self

            def order_by(self, *args):
                return self

            def __iter__(self):
                return iter(())

        with patch("coach_api.views.LearnerProfile") as profile:
            profile.objects = Queryset()
            with patch("coach_api.views.get_learner_db_alias", return_value="enrolment"):
                with patch("coach_api.views.learner_ksbs_relation_exists", return_value=ksbs_exists):
                    with patch("coach_api.views.learner_activity_events_relation_exists", return_value=events_exist):
                        with patch("coach_api.views.fetch_source_schedule_rows", return_value=({}, {})):
                            fetch_caseload_learner_profiles("coach@example.com")
        return captured.get('names', [])

    def test_dropped_legacy_ksb_relation_is_not_prefetched(self):
        names = self._prefetches(ksbs_exists=False, events_exist=False)

        self.assertNotIn("assigned_ksbs", names)
        self.assertNotIn("activity_events", names)
        # The current, authoritative KSB graph is still loaded.
        self.assertIn("ksb_assignment__profile_version__definitions", names)
        self.assertIn("progress_entries__ksb_links", names)

    def test_legacy_relations_are_prefetched_where_they_still_exist(self):
        names = self._prefetches(ksbs_exists=True, events_exist=True)

        self.assertIn("assigned_ksbs", names)
        self.assertIn("activity_events", names)


@override_settings(CACHES={
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "coach-caseload-view-tests",
    },
})
class CoachCaseloadViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_paginated_caseload_returns_workspace_metrics_without_restoring_old_snapshots(self):
        source = SimpleNamespace(id=193, aptem_id=8533, planned_hours=999)
        row = SimpleNamespace(id=7, _caseload_source=source)
        metrics = {"migrated": True, "aptem_planned_total": 576,
                   "otjh": {"actual": 164.87, "completed_actual": 100, "planned": 256},
                   "programme": {"completed": 137, "total": 157, "percent": 87.3, "status": "ready"},
                   "ksb": {"completed": 498, "total": 584, "percent": 85.3, "status": "ready"}}
        mocks = {
            "fetch_caseload_learner_profiles_by_ids": MagicMock(return_value=[row]),
            "serialize_caseload_learner": MagicMock(return_value={
                "id": "7", "otjhCompleted": 99, "otjhTarget": 999, "otjhPlanned": 999,
                "ksbCompleted": 16, "ksbTarget": 16, "ksbProgress": 100,
                "ksbProgressAvailable": True, "enrollmentStatus": "active",
            }),
            "caseload_canonical_metrics": MagicMock(return_value={7: metrics}),
            "caseload_audit_hour_totals": MagicMock(return_value={}),
            "caseload_evidenced_ksb_counts": MagicMock(return_value={}),
            "caseload_aptem_ids": MagicMock(return_value={7: 8533}),
            "caseload_latest_learning_activities": MagicMock(return_value={}),
            "dashboard_latest_completed_review_dates": MagicMock(return_value={}),
            "current_curriculum_ksb_items_for_learner": MagicMock(return_value=[]),
            "curriculum_expected_otjh_by_component_id": MagicMock(return_value={}),
            "coach_staff_display_name": MagicMock(return_value="Coach"),
            "dashboard_attendance_rows": MagicMock(return_value=[{
                "id": "7", "hasAttendance": True, "attendance": 51,
                "present": 23, "sessions": 45, "absent": 22,
            }]),
        }
        with patch.multiple("coach_api.views", **mocks), patch.object(LearnerProfile, "objects", self.PageQuery([7])):
            response = call_coach_view(coach_caseload, self.factory.get("/coach_api/coach/caseload", {"page": 1}))
        self.assertEqual(response.status_code, 200)
        learner = json.loads(response.content)["results"][0]
        self.assertEqual((learner["otjhCompleted"], learner["otjhTarget"]), (164.87, 576))
        self.assertEqual((learner["ksbCompleted"], learner["ksbTarget"], learner["ksbProgress"]), (498, 584, 85.3))
        self.assertEqual((learner["componentsCompleted"], learner["componentsPlanned"], learner["activityProgress"]),
                         (137, 157, 87.3))
        self.assertEqual((learner["attendancePresent"], learner["attendanceSessions"], learner["attendanceRate"]),
                         (23, 45, 51))
        mocks["caseload_canonical_metrics"].assert_called_once_with([row], learner_workspace=True)

    class PageQuery:
        class EmptyValues:
            def distinct(self): return self
            def order_by(self, *args): return self
            def __iter__(self): return iter(())
        def __init__(self, ids):
            self.ids = ids
        def annotate(self, **kwargs): return self
        def filter(self, *args, **kwargs): return self
        def exclude(self, **kwargs): return self
        def count(self): return len(self.ids)
        def order_by(self, *args): return self
        def values_list(self, *fields, **kwargs):
            if fields == ("id",): return self.ids
            return self.EmptyValues()

    @patch("coach_api.views.coach_staff_display_name", return_value="Coach Example")
    @patch("coach_api.views.dashboard_attendance_rows", return_value=[])
    @patch("coach_api.views.dashboard_latest_completed_review_dates", return_value={})
    @patch("coach_api.views.caseload_aptem_ids", return_value={})
    @patch("coach_api.views.dashboard_review_history", return_value={})
    @patch("coach_api.views.caseload_canonical_metrics", return_value={})
    @patch("coach_api.views.caseload_evidenced_ksb_counts", return_value={})
    @patch("coach_api.views.caseload_audit_hour_totals", return_value={})
    @patch("coach_api.views.caseload_latest_learning_activities", return_value={})
    @patch("coach_api.views.current_curriculum_ksb_items_for_learner", return_value=[])
    @patch("coach_api.views.curriculum_expected_otjh_by_component_id", return_value={})
    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.fetch_caseload_learner_profiles_by_ids")
    def test_paginated_caseload_enriches_only_requested_page(
        self, fetch_page, serialize, *_mocks,
    ):
        ids = list(range(1, 33))
        fetch_page.side_effect = lambda _owner, selected: [
            SimpleNamespace(id=value, coach_name="Coach Example") for value in selected
        ]
        serialize.side_effect = lambda row, **kwargs: {"id": str(row.id), "coachName": "Coach Example"}

        with patch.object(LearnerProfile, "objects", self.PageQuery(ids)):
            first_response = call_coach_view(coach_caseload, self.factory.get(
                "/coach_api/coach/caseload", {"owner_email": "coach@example.com", "page": 1, "page_size": 10},
            ))
            response = call_coach_view(coach_caseload, self.factory.get(
                "/coach_api/coach/caseload", {"owner_email": "coach@example.com", "page": 4, "page_size": 10},
            ))
        first_payload = json.loads(first_response.content)
        payload = json.loads(response.content)

        self.assertEqual(len(first_payload["results"]), 10)
        self.assertEqual(first_payload["pagination"]["totalPages"], 4)
        self.assertEqual(payload["pagination"], {
            "page": 4, "pageSize": 10, "total": 32, "totalPages": 4,
            "hasNext": False, "hasPrevious": True,
        })
        self.assertEqual([row["id"] for row in payload["results"]], ["31", "32"])
        self.assertEqual(fetch_page.call_args_list[-1].args, ("coach@example.com", [31, 32]))
        self.assertEqual([call.args[0].id for call in serialize.call_args_list[-2:]], [31, 32])

        with patch.object(LearnerProfile, "objects", self.PageQuery(list(range(1, 74)))):
            large_response = call_coach_view(coach_caseload, self.factory.get(
                "/coach_api/coach/caseload", {"owner_email": "coach@example.com", "page": 8, "page_size": 10},
            ))
        large_payload = json.loads(large_response.content)
        self.assertEqual(large_payload["pagination"]["totalPages"], 8)
        self.assertEqual([row["id"] for row in large_payload["results"]], ["71", "72", "73"])

    @patch("coach_api.views.serialize_caseload_dashboard_learner")
    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_coach_caseload_can_return_dashboard_summary_snapshots(
        self,
        fetch_rows,
        serialize_learner,
    ):
        row = SimpleNamespace(id=2)
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {"id": "2", "coachName": "Med Maher"}

        response = call_coach_view(coach_caseload,
            self.factory.get(
                "/coach_api/coach/caseload",
                {"owner_email": "coach@example.com", "summary": "1"},
            )
        )

        self.assertEqual(response.status_code, 200)
        fetch_rows.assert_called_once_with("coach@example.com")
        serialize_learner.assert_called_once_with(row)

    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_caseload_uses_cached_snapshots_by_default(
        self,
        fetch_rows,
        serialize_learner,
    ):
        row = SimpleNamespace(id=2)
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {"id": "2", "coachName": "Med Maher"}

        response = call_coach_view(coach_caseload,
            self.factory.get(
                "/coach_api/coach/caseload",
                {"owner_email": "coach@example.com"},
            )
        )

        self.assertEqual(response.status_code, 200)
        serialize_learner.assert_called_once_with(row, refresh_live_snapshots=False)

    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_caseload_allows_live_snapshot_refresh_when_requested(
        self,
        fetch_rows,
        serialize_learner,
    ):
        row = SimpleNamespace(id=2)
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {"id": "2", "coachName": "Med Maher"}

        response = call_coach_view(coach_caseload,
            self.factory.get(
                "/coach_api/coach/caseload",
                {"owner_email": "coach@example.com", "live": "1"},
            )
        )

        self.assertEqual(response.status_code, 200)
        serialize_learner.assert_called_once_with(row, refresh_live_snapshots=True)


class SerializeCaseloadDashboardLearnerTests(SimpleTestCase):
    def _row(self, **overrides):
        defaults = dict(
            id=2,
            username="mahmoud fouda",
            email="learner@example.com",
            coach_name="Med Maher",
            coach_email="Med.Maher@kentbusinesscollege.com",
            coach_rag="green",
            enrolment_id=99,
            learner_type="apprenticeship",
            programme_status="Active",
            lifecycle_status="active",
            cohort="Cohort A",
            group="Group 1",
            programme="Programme A",
            start_date=None,
            end_date=None,
            gateway_review_date=None,
            minimum_hours=Decimal("0"),
            planned_hours=Decimal("111"),
            completed_hours=Decimal("3.4"),
            target_hours=Decimal("16"),
            progress_hours=Decimal("-12.6"),
            progress_variance=Decimal("-0.79"),
            otjh_status="At risk",
            training_plan=[],
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_attendance_defaults_to_unavailable_not_zero(self):
        """No attendance enrichment has run yet: this must read as
        unavailable, never a fake 0% that looks like a real measured rate."""
        payload = serialize_caseload_dashboard_learner(self._row())
        self.assertIsNone(payload["attendanceRate"])
        self.assertFalse(payload["attendanceRateAvailable"])

    def test_dashboard_dto_uses_aptem_backed_source_schedule_when_profile_is_blank(self):
        row = self._row(
            planned_hours=None,
            _caseload_source=SimpleNamespace(
                planned_hours="576.00",
                start_date=None,
                end_date=None,
                learner_start_date="2026-08-26",
                learner_end_date="2027-08-25",
            ),
        )

        payload = serialize_caseload_dashboard_learner(row)

        self.assertEqual(payload["otjhPlanned"], 576.0)
        self.assertEqual(payload["startDate"], "2026-08-26")
        self.assertEqual(payload["otjhProgrammeStartDate"], "26 Aug 2026")
        self.assertEqual(payload["plannedEndDate"], "25 Aug 2027")

    def test_dashboard_dto_prefers_the_verified_contract_schedule(self):
        row = self._row(
            planned_hours=None,
            _caseload_source=SimpleNamespace(
                planned_hours="400.00",
                learner_start_date="2026-01-01",
                learner_end_date="2026-12-31",
            ),
            _caseload_contract={
                "training_plan_planned_hours": Decimal("576.00"),
                "program_start_date": "2026-08-26",
                "planned_end_date": "2027-08-25",
            },
        )

        payload = serialize_caseload_dashboard_learner(row)

        self.assertEqual(payload["otjhPlanned"], 576.0)
        self.assertEqual(payload["startDate"], "2026-01-01")
        self.assertEqual(payload["otjhProgrammeStartDate"], "26 Aug 2026")
        self.assertEqual(payload["plannedEndDate"], "25 Aug 2027")

    def test_dashboard_table_date_uses_enrolment_without_changing_contract_dates(self):
        row = self._row(
            _caseload_source=SimpleNamespace(learner_start_date="2025-10-15", start_date="2020-01-01", end_date="2027-02-14"),
            _caseload_contract={"program_start_date": "2025-10-01", "planned_end_date": "2027-02-01"},
        )
        payload = serialize_caseload_dashboard_learner(row)
        self.assertEqual(payload["displayStartDate"], "15 Oct 2025")
        self.assertEqual(payload["startDate"], "2025-10-15")
        self.assertEqual(payload["otjhProgrammeStartDate"], "01 Oct 2025")
        self.assertEqual(payload["plannedEndDate"], "01 Feb 2027")

    def test_dashboard_dto_drops_repeated_and_obsolete_coach_fields(self):
        """coachName/coachEmail (already on `owner`) and coachRag (removed
        from the coach caseload flow) must not leak into this DTO, even
        though the source row still carries them for other serializers."""
        payload = serialize_caseload_dashboard_learner(self._row())
        for field in ("coachName", "coachEmail", "coachRag"):
            self.assertNotIn(field, payload)

    def test_dashboard_dto_has_no_full_review_payload(self):
        payload = serialize_caseload_dashboard_learner(self._row())
        for field in ("reviewHistory", "sections", "rawText", "mcm", "reviews"):
            self.assertNotIn(field, payload)


class ApplyAttendanceSummaryTests(SimpleTestCase):
    def test_overlays_the_one_canonical_attendance_figure(self):
        payload = {"attendanceRate": None, "attendanceRateAvailable": False}
        apply_attendance_summary(payload, {
            "hasAttendance": True,
            "attendance": 87,
            "present": 39,
            "sessions": 44,
            "absent": 5,
            "lastSession": "10 Sep 2026",
            "lastSessionDate": "2026-09-10",
        })
        self.assertEqual(payload["attendanceRate"], 87)
        self.assertTrue(payload["attendanceRateAvailable"])
        self.assertEqual(payload["attendancePresent"], 39)
        self.assertEqual(payload["attendanceSessions"], 44)
        self.assertEqual(payload["attendanceAbsent"], 5)
        self.assertEqual(payload["attendanceLastSession"], "10 Sep 2026")
        self.assertEqual(payload["attendanceLastSessionDate"], "2026-09-10")

    def test_missing_metrics_leaves_unavailable_placeholder_not_zero(self):
        payload = {"attendanceRate": None, "attendanceRateAvailable": False}
        apply_attendance_summary(payload, None)
        self.assertIsNone(payload["attendanceRate"])
        self.assertFalse(payload["attendanceRateAvailable"])

    def test_hasAttendance_false_does_not_overwrite_with_zero(self):
        payload = {"attendanceRate": None, "attendanceRateAvailable": False}
        apply_attendance_summary(payload, {"hasAttendance": False, "attendance": None})
        self.assertIsNone(payload["attendanceRate"])
        self.assertFalse(payload["attendanceRateAvailable"])


@override_settings(CACHES={
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "coach-dashboard-view-tests",
    },
})
class CoachDashboardViewTests(SimpleTestCase):
    def setUp(self):
        # Cached DTOs are normalized through the same bulk source read as snapshots.
        source_reads = patch("coach_api.services.dashboard.profile_dates.fetch_dashboard_profile_dates", return_value=[])
        source_reads.start()
        self.addCleanup(source_reads.stop)

    @patch("coach_api.dashboard_service.CoachDashboardService.build")
    @patch("coach_api.views.collect_generated_timetable")
    @patch("coach_api.views.coach_caseload")
    @patch("coach_api.views.coach_marking_queue")
    def test_dashboard_uses_summary_service_and_never_detailed_loaders(
        self, marking, caseload, timetable, build,
    ):
        cache.clear()
        build.return_value = {
            "owner": {"name": "Coach", "email": "coach@example.com"},
            "learners": [{"id": "2"}],
            "monthlyRisk": [],
            "meetings": {"events": []},
            "marking": {"summary": {"pendingItems": 0}, "items": []},
        }
        response = call_coach_view(
            coach_dashboard,
            RequestFactory().get("/coach_api/coach/dashboard"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["learners"], [{"id": "2"}])
        build.assert_called_once_with()
        timetable.assert_not_called()
        caseload.assert_not_called()
        marking.assert_not_called()

    @patch("coach_api.dashboard_service.CoachDashboardService.build")
    def test_dashboard_response_contract_is_the_service_contract(self, build):
        cache.clear()
        payload = {
            "owner": {"name": "Example Coach", "email": "coach@example.com"},
            "learners": [{
                "id": "7", "learnerType": "commercial", "otjhCompleted": 0,
                "otjhTarget": 40, "ksbProgress": None, "ksbProgressAvailable": False,
                "activityProgress": 0, "activityProgressAvailable": True,
                "attendanceRate": None, "attendanceRateAvailable": False,
                "lastActivity": "--", "lastActivityDate": None,
                "lastPr": None, "lastMcm": None,
            }],
            "monthlyRisk": [{"month": "2026-09", "label": "Sep", "count": 0}],
            "assignedGroups": [],
            "meetings": {
                "events": [], "summary": {"progressReviewRows": 0, "mcrRows": 0},
                "reviewGenerationIssues": [],
            },
            "marking": {"summary": {"pendingItems": 0}, "items": []},
            "errors": {},
        }
        build.return_value = payload

        response = call_coach_view(
            coach_dashboard, RequestFactory().get("/coach_api/coach/dashboard"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), payload)
        self.assertEqual(
            set(json.loads(response.content)),
            {"owner", "learners", "monthlyRisk", "assignedGroups", "meetings", "marking", "errors"},
        )

    @patch("coach_api.dashboard_cache.get_cached_coach_dashboard")
    def test_dashboard_cache_key_is_scoped_per_coach(self, get_cached):
        """Rapid coach switching must never read another coach's cached payload."""
        get_cached.return_value = {"learners": [{"id": "shared-cache-guard"}]}
        request_a = RequestFactory().get("/coach_api/coach/dashboard")
        request_a.coach_email = "coach-a@example.com"
        request_b = RequestFactory().get("/coach_api/coach/dashboard")
        request_b.coach_email = "coach-b@example.com"
        response_a = unwrap(coach_dashboard)(request_a)
        response_b = unwrap(coach_dashboard)(request_b)
        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(
            [call.args[0] for call in get_cached.call_args_list],
            ["coach-a@example.com", "coach-b@example.com"],
        )

    @patch("coach_api.dashboard_cache.get_cached_coach_dashboard")
    def test_view_as_uses_the_resolved_effective_coach_cache_identity(self, get_cached):
        get_cached.return_value = {"learners": []}
        request = RequestFactory().get(
            "/coach_api/coach/dashboard", {"viewAsCoach": "coach-a@example.com"},
        )
        # Installed by coach_access_required only after it validates the admin
        # and resolves the selected coach account.
        request.coach_email = "coach-a@example.com"
        request.coach_view_as = True

        unwrap(coach_dashboard)(request)

        get_cached.assert_called_once_with("coach-a@example.com")

    @override_settings(COACH_DASHBOARD_SNAPSHOT_MAX_AGE=30)
    @patch("coach_api.dashboard_view.schedule_coach_dashboard_refresh")
    @patch("coach_api.dashboard_cache.get_cached_coach_dashboard")
    def test_stale_cache_hit_returns_immediately_and_schedules_refresh(self, get_cached, schedule):
        get_cached.return_value = {
            "learners": [],
            "readModel": {
                "refreshedAt": (timezone.now() - timedelta(seconds=31)).isoformat(),
            },
        }
        request = RequestFactory().get("/coach_api/coach/dashboard")
        request.coach_email = "coach@example.com"

        response = unwrap(coach_dashboard)(request)

        self.assertEqual(response.status_code, 200)
        schedule.assert_called_once_with("coach@example.com", reason="stale-cache-hit")

    @patch("coach_api.dashboard_cache.get_cached_coach_dashboard")
    def test_authentication_is_checked_before_dashboard_cache_lookup(self, get_cached):
        request = RequestFactory().get("/coach_api/coach/dashboard")
        request.session = {}

        response = coach_dashboard(request)

        self.assertIn(response.status_code, {401, 403})
        get_cached.assert_not_called()

    @override_settings(COACH_DASHBOARD_CACHE_TTL=90)
    @patch("coach_api.dashboard_service.CoachDashboardService.build")
    def test_first_request_misses_and_second_request_hits_final_response_cache(self, build):
        cache.clear()
        build.return_value = {"owner": {"email": "coach@example.com"}, "learners": [
            {"id": "1", "startDate": None, "displayStartDate": None, "displayEndDate": "--", "otjhProgrammeStartDate": "--"},
        ]}
        request = RequestFactory().get("/coach_api/coach/dashboard")
        request.coach_email = "coach@example.com"

        first = unwrap(coach_dashboard)(request)
        second = unwrap(coach_dashboard)(request)

        self.assertEqual(json.loads(first.content), json.loads(second.content))
        self.assertEqual(first["X-LMS-Cache"], "MISS")
        self.assertEqual(second["X-LMS-Cache"], "HIT")
        build.assert_called_once_with()

    @override_settings(COACH_DASHBOARD_CACHE_TTL=90)
    @patch("coach_api.dashboard_service.CoachDashboardService.build")
    def test_different_effective_coaches_never_share_cached_payloads(self, build):
        cache.clear()
        build.side_effect = lambda: {"learners": [{"id": str(build.call_count)}]}
        for identity in ("coach-a@example.com", "coach-b@example.com"):
            request = RequestFactory().get("/coach_api/coach/dashboard")
            request.coach_email = identity
            unwrap(coach_dashboard)(request)
        self.assertEqual(build.call_count, 2)

    @override_settings(COACH_DASHBOARD_CACHE_TTL=90)
    @patch("coach_api.dashboard_service.CoachDashboardService.build")
    def test_expired_cache_recomputes_dashboard(self, build):
        from coach_api.dashboard_cache import coach_dashboard_cache_key

        cache.clear()
        identity = "coach@example.com"
        cache.set(coach_dashboard_cache_key(identity), {"learners": [{"id": "stale"}]}, timeout=-1)
        build.return_value = {"learners": [{"id": "fresh"}]}
        request = RequestFactory().get("/coach_api/coach/dashboard")
        request.coach_email = identity

        response = unwrap(coach_dashboard)(request)

        self.assertEqual(json.loads(response.content)["learners"], [{"id": "fresh"}])
        build.assert_called_once_with()

    def test_invalidation_removes_only_the_selected_coachs_cache(self):
        from coach_api.dashboard_cache import (
            cache_coach_dashboard,
            get_cached_coach_dashboard,
            invalidate_coach_dashboard_cache,
        )

        cache.clear()
        cache_coach_dashboard("coach-a@example.com", {"learners": [{"id": "a"}]})
        cache_coach_dashboard("coach-b@example.com", {"learners": [{"id": "b"}]})
        invalidate_coach_dashboard_cache("coach-a@example.com")

        self.assertIsNone(get_cached_coach_dashboard("coach-a@example.com"))
        self.assertEqual(get_cached_coach_dashboard("coach-b@example.com")["learners"], [{"id": "b"}])

    @patch("coach_api.dashboard_refresh.schedule_coach_dashboard_refresh")
    @patch("coach_api.dashboard_cache.invalidate_coach_dashboard_cache")
    def test_successful_coach_mutation_uses_central_invalidation_hook(self, invalidate, schedule):
        from coach_api.auth import _invalidate_dashboard_after_mutation

        view = lambda request: None
        request = RequestFactory().post("/coach_api/coach/mutation")
        response = SimpleNamespace(status_code=200)

        self.assertIs(
            _invalidate_dashboard_after_mutation(view, request, response, "coach@example.com"),
            response,
        )
        invalidate.assert_called_once_with("coach@example.com")
        schedule.assert_called_once_with("coach@example.com", reason="coach-mutation")


class CoachDashboardBackgroundRefreshTests(SimpleTestCase):
    @override_settings(COACH_DASHBOARD_SNAPSHOT_MAX_AGE=30)
    def test_only_timestamped_stale_snapshots_schedule_revalidation(self):
        from coach_api.dashboard_refresh import snapshot_needs_refresh

        now = timezone.now()
        self.assertFalse(snapshot_needs_refresh({"learners": []}, now=now))
        self.assertFalse(snapshot_needs_refresh({
            "readModel": {"refreshedAt": (now - timedelta(seconds=29)).isoformat()},
        }, now=now))
        self.assertTrue(snapshot_needs_refresh({
            "readModel": {"refreshedAt": (now - timedelta(seconds=30)).isoformat()},
        }, now=now))
        self.assertTrue(snapshot_needs_refresh({
            "readModel": {"version": 14, "refreshedAt": now.isoformat()},
        }, now=now))

    @patch("coach_api.dashboard_cache.cache_coach_dashboard")
    @patch("read_models.repository.put_read_model")
    @patch("coach_api.read_model.build_coach_dashboard")
    def test_background_worker_refreshes_snapshot_and_final_cache(self, build, put_read_model, cache_payload):
        from coach_api.dashboard_refresh import _refresh_worker

        refreshed_at = timezone.now()
        build.return_value = {"learners": []}
        put_read_model.return_value = SimpleNamespace(
            schema_version=3,
            refreshed_at=refreshed_at,
        )

        _refresh_worker("coach@example.com", "test-event")

        build.assert_called_once_with("coach@example.com")
        put_read_model.assert_called_once()
        cache_payload.assert_called_once_with(
            "coach@example.com",
            {"learners": [], "readModel": {"version": 3, "refreshedAt": refreshed_at.isoformat()}},
        )

    @patch("coach_api.dashboard_cache.cache.get", side_effect=ConnectionError("redis unavailable"))
    def test_cache_outage_falls_through_to_persistent_snapshot(self, _cache_get):
        from coach_api.dashboard_cache import get_cached_coach_dashboard

        self.assertIsNone(get_cached_coach_dashboard("coach@example.com"))

    @patch("coach_api.dashboard_signals.enqueue_coach_dashboard_refresh")
    @patch("coach_api.dashboard_signals.invalidate_coach_dashboard_cache")
    @patch("coach_api.dashboard_signals.transaction.on_commit")
    def test_domain_event_invalidates_and_refreshes_only_after_commit(
        self, on_commit, invalidate, schedule,
    ):
        from coach_api.dashboard_signals import _enqueue_after_commit

        _enqueue_after_commit(
            " Coach@Example.com ", using="enrolment", reason="learner-progress",
        )

        on_commit.assert_called_once()
        self.assertEqual(on_commit.call_args.kwargs, {"using": "enrolment"})
        invalidate.assert_not_called()
        schedule.assert_called_once_with(
            "coach@example.com", using="enrolment", reason="learner-progress",
        )

        on_commit.call_args.args[0]()

        invalidate.assert_called_once_with("coach@example.com")

    @patch("coach_api.dashboard_signals._enqueue_after_commit")
    def test_progress_event_uses_cached_learner_without_an_extra_query(self, enqueue):
        from coach_api.dashboard_signals import learner_progress_changed

        learner = SimpleNamespace(coach_email="coach@example.com")
        progress = SimpleNamespace(
            learner_id=7,
            _state=SimpleNamespace(fields_cache={"learner": learner}),
        )

        learner_progress_changed(None, instance=progress, using="enrolment")

        enqueue.assert_called_once_with(
            "coach@example.com", using="enrolment", reason="learner-progress",
        )

    @patch("coach_api.dashboard_signals._enqueue_after_commit")
    def test_profile_calendar_and_absence_events_target_the_owner(self, enqueue):
        from coach_api.dashboard_signals import (
            coach_absence_changed,
            coach_calendar_changed,
            learner_profile_changed,
        )

        learner_profile_changed(
            None,
            instance=SimpleNamespace(coach_email="profile@example.com"),
            using="enrolment",
        )
        coach_calendar_changed(
            None,
            instance=SimpleNamespace(owner_email="calendar@example.com"),
            using="default",
        )
        coach_absence_changed(
            None,
            instance=SimpleNamespace(owner_email="absence@example.com"),
            using="default",
        )

        self.assertEqual(enqueue.call_args_list, [
            call("profile@example.com", using="enrolment", reason="learner-profile"),
            call("calendar@example.com", using="default", reason="coach-calendar"),
            call("absence@example.com", using="default", reason="coach-absence"),
        ])


class CoachDashboardReadModelTests(SimpleTestCase):
    def setUp(self):
        # Snapshot date normalization performs separate read-only source reads.
        source_reads = patch("coach_api.services.dashboard.profile_dates.fetch_dashboard_profile_dates", return_value=[])
        source_reads.start()
        self.addCleanup(source_reads.stop)

    def test_live_dashboard_uses_learner_overview_metrics_after_legacy_enrichment(self):
        from coach_api.dashboard_service import CoachDashboardService

        row = SimpleNamespace(id=7, training_plan=[])
        canonical = {"migrated": True, "aptem_planned_total": 576,
                     "otjh": {"actual": 164.87, "completed_actual": 100, "planned": 256},
                     "programme": {"completed": 137, "total": 157, "percent": 87.3, "status": "ready"},
                     "ksb": {"completed": 498, "total": 584, "percent": 85.3, "status": "ready"}}
        mocks = {
            "fetch_caseload_learner_profiles": MagicMock(return_value=[row]),
            "serialize_caseload_learner": MagicMock(return_value={
                "id": "7", "otjhCompleted": 99, "otjhTarget": 999, "otjhPlanned": 999,
                "ksbCompleted": 16, "ksbTarget": 16, "ksbProgress": 100, "ksbProgressAvailable": True,
            }),
            "caseload_canonical_metrics": MagicMock(return_value={7: canonical}),
            "caseload_dashboard_progress_projections": MagicMock(return_value={}),
            "caseload_audit_hour_totals": MagicMock(return_value={7: {"audit_tp_planned": 999, "audit_lms_actual": 99}}),
            "caseload_evidenced_ksb_counts": MagicMock(return_value={}),
            "caseload_aptem_ids": MagicMock(return_value={7: 8533}),
            "curriculum_expected_otjh_by_component_id": MagicMock(return_value={}),
            "caseload_progress_history": MagicMock(return_value={}),
            "build_monthly_risk_history": MagicMock(return_value=[]),
            "coach_staff_display_name": MagicMock(return_value="Coach"),
            "resolve_coach_review_events": MagicMock(return_value={"events": []}),
            "fetch_standalone_event_records": MagicMock(return_value=[]),
            "review_type_fields_by_template": MagicMock(return_value={}),
            "collect_tracked_live_session_events": MagicMock(return_value=[]),
            "dashboard_attendance_rows": MagicMock(return_value=[]),
            "fetch_official_assigned_groups": MagicMock(return_value=[]),
        }
        with patch.multiple("coach_api.views", **mocks), patch(
            "coach_api.services.dashboard.service.dashboard_marking_projection", return_value={"items": []},
        ):
            learner = CoachDashboardService("coach@example.com").build_live()["learners"][0]
        self.assertEqual((learner["ksbCompleted"], learner["ksbTarget"], learner["ksbProgress"]), (498, 584, 85.3))
        self.assertEqual((learner["otjhCompleted"], learner["otjhTarget"]), (164.87, 576))
        self.assertEqual((learner["componentsCompleted"], learner["componentsPlanned"], learner["activityProgress"]),
                         (137, 157, 87.3))
        mocks["caseload_canonical_metrics"].assert_called_once_with([row], learner_workspace=True)

    @patch("coach_api.views.caseload_latest_learning_activities", return_value={
        7: {"display": "01 Sep 2026", "date": "2026-09-01", "label": "Reading"},
    })
    @patch("coach_api.views.caseload_completed_ksb_codes", return_value={7: {"K1"}})
    def test_dashboard_projection_keeps_ksb_fallback_but_cannot_overwrite_activities(
        self, _completed_codes, _latest,
    ):
        row = SimpleNamespace(id=7, ksbs=[{"code": "K1"}])

        payload = caseload_dashboard_progress_projections([row])[7]

        self.assertEqual(
            (payload["ksbCompleted"], payload["ksbTarget"], payload["ksbProgress"]),
            (1, 1, 100),
        )
        self.assertTrue(payload["ksbProgressAvailable"])
        self.assertEqual(payload["lastActivityLabel"], "Reading")
        self.assertNotIn("componentsCompleted", payload)
        self.assertNotIn("componentsPlanned", payload)
        self.assertNotIn("activityProgress", payload)
        self.assertNotIn("activityProgressAvailable", payload)

    @patch("coach_api.views.caseload_latest_learning_activities", return_value={})
    @patch("coach_api.views.caseload_completed_ksb_codes", return_value={})
    def test_dashboard_projection_limits_ksb_fallback_to_noncanonical_rows(
        self, completed_codes, latest_activities,
    ):
        canonical = SimpleNamespace(id=7, ksbs=[{"code": "K1"}])
        fallback = SimpleNamespace(id=8, ksbs=[{"code": "K2"}])

        caseload_dashboard_progress_projections(
            [canonical, fallback],
            ksb_rows=[fallback],
        )

        completed_codes.assert_called_once_with([fallback])
        latest_activities.assert_called_once_with([canonical, fallback])

    @patch("coach_api.dashboard_service.CoachDashboardSnapshot.objects")
    @patch("coach_api.dashboard_service.CoachDashboardService.build_live")
    def test_snapshot_read_is_one_lookup_and_never_rebuilds_live_aggregates(self, build_live, objects):
        from coach_api.dashboard_service import CoachDashboardService

        snapshot = SimpleNamespace(
            payload={"learners": [{"id": "1"}], "meetings": {"events": []}},
            refreshed_at=timezone.now(),
        )
        objects.filter.return_value.only.return_value.first.return_value = snapshot

        payload = CoachDashboardService("coach@example.com").build()

        self.assertEqual(payload["learners"], [{"id": "1", "startDate": None,
                                             "displayStartDate": None, "otjhProgrammeStartDate": "--"}])
        self.assertIn("readModel", payload)
        objects.filter.assert_called_once_with(owner_email="coach@example.com", schema_version=19)
        objects.filter.return_value.only.assert_called_once_with("payload", "refreshed_at")
        objects.filter.return_value.only.return_value.first.assert_called_once_with()
        build_live.assert_not_called()

    @patch("coach_api.dashboard_service.CoachDashboardSnapshot.objects")
    @patch("coach_api.dashboard_service.CoachDashboardService.refresh_metric_projection")
    def test_previous_metric_schema_snapshot_is_served_until_background_refresh(self, refresh_metrics, objects):
        from coach_api.dashboard_service import CoachDashboardService

        objects.filter.return_value.only.return_value.first.return_value = None
        previous = SimpleNamespace(
            payload={"learners": [{"id": "stale"}]},
            refreshed_at=timezone.now(),
            schema_version=13,
        )
        objects.filter.return_value.order_by.return_value.only.return_value.first.return_value = previous

        payload = CoachDashboardService("coach@example.com").build()

        self.assertEqual(payload["learners"], [{"id": "stale", "startDate": None,
                                             "displayStartDate": None, "otjhProgrammeStartDate": "--"}])
        self.assertEqual(payload["readModel"]["version"], 13)
        self.assertEqual(objects.filter.call_args_list[0].kwargs,
                         {"owner_email": "coach@example.com", "schema_version": 19})
        self.assertEqual(objects.filter.call_args_list[1].kwargs,
                         {"owner_email": "coach@example.com"})
        refresh_metrics.assert_not_called()

    def test_previous_response_cache_namespace_cannot_serve_legacy_metrics(self):
        import hashlib
        from coach_api.dashboard_cache import coach_dashboard_cache_key

        identity = "coach@example.com"
        old_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        old_key = f"coach-dashboard-summary:v2:{old_hash}"

        self.assertNotEqual(coach_dashboard_cache_key(identity), old_key)
        self.assertTrue(coach_dashboard_cache_key(identity).startswith("coach-dashboard-summary:v4:"))

    @patch("coach_api.dashboard_service.CoachDashboardSnapshot.objects")
    def test_snapshot_metric_upgrade_uses_the_same_canonical_values_as_caseload(self, objects):
        from learner_api.canonical_learning import metrics_from_records
        from coach_api.dashboard_service import CoachDashboardService

        canonical = metrics_from_records([
            {"id": 1, "accepted": True, "completed": True, "actual_seconds": 3600,
             "ksbs": ["K1"], "segments": [], "sources": []},
            {"id": 2, "accepted": False, "completed": True, "actual_seconds": 0,
             "ksbs": ["K1"], "segments": [], "sources": []},
        ], {})
        canonical["otjh"]["planned"] = 576
        canonical["aptem_planned_total"] = 576
        previous = {"learners": [{
            "id": "7", "otjhCompleted": 111, "otjhTarget": 42,
            "ksbCompleted": 14, "ksbTarget": 14, "ksbProgress": 100,
            "ksbProgressAvailable": True, "enrollmentStatus": "active",
        }], "meetings": {"events": [{"id": "preserved"}]}}
        row = SimpleNamespace(id=7)
        with patch.multiple(
            "coach_api.views",
            fetch_caseload_learner_profiles=MagicMock(return_value=[row]),
            serialize_caseload_learner=MagicMock(return_value={
                "otjhTarget": 42,
                "ksbCompleted": 14, "ksbTarget": 14, "ksbProgress": 100,
                "ksbProgressAvailable": True, "ksbStatus": "Completed",
            }),
            caseload_canonical_metrics=MagicMock(return_value={7: canonical}),
            caseload_aptem_ids=MagicMock(return_value={7: 8533}),
            dashboard_attendance_rows=MagicMock(return_value=[{
                "id": "7", "attendance": 82, "hasAttendance": True,
                "present": 23, "sessions": 28, "absent": 5,
            }]),
        ):
            payload = CoachDashboardService("coach@example.com").refresh_metric_projection(previous)

        learner = payload["learners"][0]
        self.assertEqual(
            (learner["programmeCompleted"], learner["programmeTarget"], learner["programmeProgress"]),
            (canonical["programme"]["completed"], canonical["programme"]["total"], canonical["programme"]["percent"]),
        )
        self.assertEqual(
            (learner["ksbCompleted"], learner["ksbTarget"], learner["ksbProgress"]),
            (canonical["ksb"]["completed"], canonical["ksb"]["total"], canonical["ksb"]["percent"]),
        )
        self.assertEqual((learner["otjhCompleted"], learner["otjhTarget"]),
                         (canonical["otjh"]["actual"], 576))
        self.assertEqual((learner["attendancePresent"], learner["attendanceSessions"], learner["attendanceRate"]),
                         (23, 28, 82))
        self.assertEqual(payload["meetings"], previous["meetings"])
        self.assertEqual(previous["learners"][0]["ksbProgress"], 100)
        objects.update_or_create.assert_called_once()

    @patch("coach_api.dashboard_service.CoachDashboardSnapshot.objects")
    @patch("coach_api.dashboard_service.CoachDashboardService.build_live")
    def test_refresh_returns_the_new_snapshot_timestamp(self, build_live, objects):
        from coach_api.dashboard_service import CoachDashboardService

        refreshed_at = timezone.now()
        build_live.return_value = {"learners": []}
        objects.update_or_create.return_value = (
            SimpleNamespace(refreshed_at=refreshed_at), True,
        )

        payload = CoachDashboardService("coach@example.com").refresh()

        self.assertEqual(payload["readModel"], {
            "version": 19, "refreshedAt": refreshed_at.isoformat(),
        })


@override_settings(
    COACH_DASHBOARD_CACHE_TTL=90,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "coach-dashboard-performance-tests",
        },
    },
)
class CoachDashboardSnapshotPerformanceBaselineTests(TestCase):
    databases = {"default"}

    def setUp(self):
        cache.clear()
        self.payload = {
            "owner": {"name": "Baseline Coach", "email": "baseline@example.com"},
            "learners": [{
                "id": str(index), "name": f"Learner {index}",
                "learnerType": "commercial" if index % 2 else "apprenticeship",
                "otjhCompleted": 0, "otjhTarget": 40,
                "ksbProgress": None, "ksbProgressAvailable": False,
                "activityProgress": 0, "activityProgressAvailable": True,
                "attendanceRate": None, "attendanceRateAvailable": False,
                "lastActivity": "--", "lastPr": None, "lastMcm": None,
            } for index in range(1, 11)],
            "monthlyRisk": [], "assignedGroups": [],
            "meetings": {"events": [], "summary": {}, "reviewGenerationIssues": []},
            "marking": {"summary": {"pendingItems": 0}, "items": []}, "errors": {},
        }
        CoachDashboardSnapshot.objects.create(
            owner_email="baseline@example.com", payload=self.payload, schema_version=17,
        )

    def request(self):
        request = RequestFactory().get("/coach_api/coach/dashboard")
        request.coach_email = "baseline@example.com"
        return unwrap(coach_dashboard)(request)

    def test_snapshot_cache_miss_and_hit_query_baseline(self):
        with CaptureQueriesContext(connection) as miss_queries:
            miss_started = perf_counter()
            miss = self.request()
            miss_ms = (perf_counter() - miss_started) * 1000
        with CaptureQueriesContext(connection) as hit_queries:
            hit_started = perf_counter()
            hit = self.request()
            hit_ms = (perf_counter() - hit_started) * 1000

        self.assertEqual(miss.status_code, 200)
        self.assertEqual(hit.status_code, 200)
        self.assertEqual(json.loads(miss.content)["learners"], self.payload["learners"])
        self.assertEqual(len(miss_queries), 1)
        self.assertEqual(len(hit_queries), 0)
        print(json.dumps({
            "dashboardBaseline": {
                "database": "Django isolated test database",
                "learners": 10,
                "cacheMissQueries": len(miss_queries),
                "cacheHitQueries": len(hit_queries),
                "cacheMissMs": round(miss_ms, 3),
                "cacheHitMs": round(hit_ms, 3),
                "dbTimeMs": round(sum(float(query.get("time", 0)) for query in miss_queries) * 1000, 3),
                "payloadBytes": len(miss.content),
                "slowestSqlFingerprint": "coach_dashboard_snapshot owner_email/schema_version lookup",
            }
        }, sort_keys=True))


class MonthlyRiskHistoryTests(SimpleTestCase):
    @patch("coach_api.views.LearnerProgressEntry.objects")
    @patch("coach_api.views.curriculum_expected_otjh_by_component_id", return_value={})
    @patch("coach_api.views.monthly_target_training_plan", return_value=[])
    def test_dashboard_reuses_each_hydrated_plan_in_history_builder(
        self, training_plan, expected_otjh, progress_entries
    ):
        queryset = MagicMock()
        progress_entries.filter.return_value = queryset
        queryset.only.return_value = queryset
        queryset.order_by.return_value = []
        learners = [
            SimpleNamespace(id=7, status="active", programme_status="active", start_date=None),
            SimpleNamespace(id=8, status="active", programme_status="active", start_date=None),
        ]

        history = dashboard_monthly_risk_history(learners, today=date(2026, 9, 18))

        self.assertEqual(training_plan.call_count, 2)
        self.assertEqual(len(history), 6)

    def test_counts_month_end_otjh_status_and_uses_current_snapshot_for_open_month(self):
        training_plan = [{
            "moduleTitle": "Module 1",
            "weeks": [
                {
                    "weekTitle": f"Week {index}",
                    "components": [{"componentId": f"component-{index}"}],
                }
                for index in range(1, 7)
            ],
        }]
        learner = SimpleNamespace(
            id=7,
            status="active",
            programme_status="active",
            start_date=date(2026, 4, 1),
            otjh_status="At risk",
            training_plan=training_plan,
        )
        progress = {
            7: [
                {
                    "kind": "component",
                    "componentId": "component-1",
                    "reportedTime": "10 hours",
                    "submittedAt": "2026-04-15T10:00:00Z",
                },
                {
                    "kind": "component",
                    "componentId": "component-2",
                    "reportedTime": "45 hours",
                    "submittedAt": "2026-07-10T10:00:00Z",
                },
            ],
        }

        history = build_monthly_risk_history(
            [learner],
            progress,
            {f"component-{index}": 10 for index in range(1, 7)},
            today=date(2026, 9, 18),
        )

        self.assertEqual(
            [(point["month"], point["count"]) for point in history],
            [
                ("2026-04", 1),
                ("2026-05", 1),
                ("2026-06", 1),
                ("2026-07", 0),
                ("2026-08", 0),
                ("2026-09", 1),
            ],
        )


class CoachTimetableWindowTests(SimpleTestCase):
    @patch("coach_api.views.StaffUser.objects.annotate")
    def test_live_session_access_comes_from_staff_user_coach_grant(self, annotate):
        annotate.return_value.filter.return_value.exists.return_value = True

        self.assertTrue(coach_has_live_session_access(" Coach@Example.com "))
        annotate.return_value.filter.assert_called_once_with(
            staff_email_key="coach@example.com",
            staff_access_key="coach",
        )

    @patch("coach_api.views.StaffUser.objects.annotate")
    def test_live_session_access_rejects_non_coach_staff(self, annotate):
        annotate.return_value.filter.return_value.exists.return_value = False

        self.assertFalse(coach_has_live_session_access("enrolment@example.com"))

    @patch("coach_api.views.StaffUser.objects.annotate")
    def test_coach_display_name_comes_from_staff_user(self, annotate):
        annotate.return_value.filter.return_value.only.return_value.first.return_value = SimpleNamespace(
            username="Test Coach"
        )

        self.assertEqual(coach_staff_display_name("coach@example.com"), "Test Coach")

    def test_generated_schedule_dates_respect_requested_window(self):
        generated_dates = list(
            iterate_generated_schedule_dates(
                date(2026, 1, 1),
                date(2026, 2, 28),
                timedelta(days=7),
                range_start=date(2026, 1, 20),
                range_end=date(2026, 2, 2),
            )
        )

        self.assertEqual(
            generated_dates,
            [
                (3, date(2026, 1, 22)),
                (4, date(2026, 1, 29)),
            ],
        )

    def test_generated_timetable_events_include_source_detail_identity(self):
        learner = SimpleNamespace(
            id=42,
            username="Test Learner",
            email="learner@example.com",
            programme="Test Programme",
            cohort="C1",
            learner_type="commercial",
            enrolment_id=9001,
        )

        event = build_generated_calendar_event(
            learner=learner,
            owner_email="coach@example.com",
            owner_name="Test Coach",
            event_type="progress-review",
            sequence=1,
            target_date=date(2026, 9, 5),
        )

        self.assertEqual(event["learnerId"], "42")
        self.assertEqual(event["learnerType"], "commercial")
        self.assertEqual(event["enrolmentId"], "9001")

    @patch("coach_api.views.Employer.objects.filter")
    def test_progress_review_graph_invites_learner_and_employer_from_coach_calendar(self, employer_filter):
        employer_filter.return_value.only.return_value.first.return_value = SimpleNamespace(
            full_name="Employer Contact",
            email="employer@example.com",
        )
        learner = SimpleNamespace(
            id=42,
            username="Test Learner",
            email="learner@example.com",
            programme="Test Programme",
            cohort="C1",
            learner_type="commercial",
            enrolment_id=9001,
        )
        source_row = SimpleNamespace(
            employer_id=77,
            line_manager="Line Manager",
            employer="Test Employer",
        )

        event = build_generated_calendar_event(
            learner=learner,
            owner_email="coach@example.com",
            owner_name="Test Coach",
            event_type="progress-review",
            sequence=1,
            target_date=date(2026, 9, 5),
            source_row=source_row,
        )
        record = CoachCalendarEvent(
            event_key=event["eventKey"],
            owner_email="coach@example.com",
            owner_name="Test Coach",
            learner_id=42,
            learner_name="Test Learner",
            learner_email="learner@example.com",
            event_type="progress-review",
            sequence=1,
            target_date=date(2026, 9, 5),
            scheduled_date=date(2026, 9, 5),
            scheduled_time=time(9, 0),
            duration_minutes=60,
        )

        payload = build_graph_event_payload(record, event)
        attendees = [item["emailAddress"]["address"] for item in payload["attendees"]]

        self.assertEqual(event["employerEmail"], "employer@example.com")
        self.assertEqual(graph_organizer_mailbox(record, event), "coach@example.com")
        # Organizer mailbox and attendee delivery are separate contracts. The
        # existing builder explicitly includes the owning coach once.
        self.assertEqual(attendees, ["learner@example.com", "employer@example.com", "coach@example.com"])
        self.assertEqual([item["type"] for item in payload["attendees"]], ["required"] * 3)
        self.assertEqual(len(attendees), len(set(attendees)))

    @patch("coach_api.views.fetch_calendar_event_records", return_value={})
    @patch("coach_api.views.fetch_standalone_event_records", return_value=[])
    @patch("coach_api.views.resolve_coach_review_events", return_value={
        "events": [], "reviewGenerationIssues": [], "aptemProfileIds": set(),
        "sourceCounts": {
            "progressReviewRows": 0, "mcrRows": 0, "reviewRows": 0,
            "learnersWithDates": 0, "reviewAnchorSkipped": 0,
            "reviewAnchorSkipReasons": {}, "aptemReviewRows": 0,
            "curriculumReviewRows": 0, "aptemLearners": 0, "curriculumLearners": 0,
        },
    })
    @patch("coach_api.views.build_learner_profile_map", return_value={})
    @patch("coach_api.views.fetch_caseload_timetable_profiles", return_value=[])
    @patch("coach_api.views.fetch_owner_active_learner_profiles", return_value=[])
    @patch("coach_api.views.coach_staff_display_name", return_value="")
    @patch("coach_api.views.collect_live_session_events", side_effect=RuntimeError("legacy staff profile schema"))
    def test_collect_generated_timetable_ignores_live_session_errors(
        self,
        collect_live_session_events,
        coach_staff_display_name,
        fetch_owner_active_learner_profiles,
        fetch_caseload_timetable_profiles,
        build_learner_profile_map,
        resolve_coach_review_events,
        fetch_standalone_event_records,
        fetch_calendar_event_records,
    ):
        payload = collect_generated_timetable("coach@example.com")

        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["summary"]["sourceCounts"]["liveSessionRows"], 0)
        collect_live_session_events.assert_called_once()
        coach_staff_display_name.assert_called_once_with("coach@example.com")
        fetch_owner_active_learner_profiles.assert_called_once_with("coach@example.com")
        fetch_caseload_timetable_profiles.assert_called_once_with("coach@example.com")
        build_learner_profile_map.assert_called_once_with([])
        resolve_coach_review_events.assert_called_once_with(
            "coach@example.com", "Med Maher", [], start_date=None, end_date=None,
        )
        fetch_standalone_event_records.assert_called_once_with("coach@example.com")
        fetch_calendar_event_records.assert_not_called()


class CoachTimetableBookingConflictTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.future_date = (date.today() + timedelta(days=30)).isoformat()

    @patch("coach_api.views.CoachCalendarEvent.objects.filter")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.fetch_owner_name", return_value="Coach Example")
    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=True)
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_book_event_does_not_create_catch_up_or_support_when_learner_busy(
        self,
        fetch_caseload_learner_profiles,
        coach_learner_personal_calendar_conflicts,
        fetch_owner_name,
        sync_calendar_event_to_graph,
        calendar_event_filter,
    ):
        learner = SimpleNamespace(
            id=7,
            username="Test User",
            email="learner@example.com",
            coach_name="Coach Example",
        )
        fetch_caseload_learner_profiles.return_value = [learner]
        calendar_event_filter.return_value.first.return_value = None

        for session_type in ("catch-up", "student-support"):
            with self.subTest(session_type=session_type):
                request = self.factory.post(
                    "/coach_api/coach/timetable/events/book",
                    data=json.dumps(
                        {
                            "ownerEmail": "coach@example.com",
                            "learnerId": 7,
                            "sessionType": session_type,
                            "scheduledDate": self.future_date,
                            "scheduledTime": "10:00",
                            "durationMinutes": 60,
                            "timezoneOffsetMinutes": -180,
                        }
                    ),
                    content_type="application/json",
                    HTTP_IDEMPOTENCY_KEY=f"busy-{session_type}",
                )

                response = call_coach_view(coach_timetable_book_event, request)

                self.assertEqual(response.status_code, 409)
                self.assertIn("busy at that time", response.content.decode())

        self.assertEqual(coach_learner_personal_calendar_conflicts.call_count, 2)
        sync_calendar_event_to_graph.assert_not_called()

    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.CoachCalendarEvent.objects.get_or_create")
    @patch("coach_api.views.build_learner_profile_map")
    @patch("coach_api.views.fetch_owner_active_learner_profiles")
    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=True)
    @patch("coach_api.views.find_catchup_template_event")
    @patch("coach_api.views.find_catchup_calendar_record", return_value=(None, "Coach Example"))
    def test_schedule_template_does_not_create_catch_up_when_learner_busy(
        self,
        find_catchup_calendar_record,
        find_catchup_template_event,
        coach_learner_personal_calendar_conflicts,
        fetch_owner_active_learner_profiles,
        build_learner_profile_map,
        get_or_create,
        sync_calendar_event_to_graph,
    ):
        learner = SimpleNamespace(id=7)
        fetch_owner_active_learner_profiles.return_value = [learner]
        build_learner_profile_map.return_value = {7: learner}
        find_catchup_template_event.return_value = (
            {
                "eventKey": "coach-catchup-template:coach@example.com:7",
                "learnerId": "7",
                "learner": "Test User",
                "email": "learner@example.com",
                "sequence": 1,
            },
            "Coach Example",
        )
        request = self.factory.post(
            "/coach_api/coach/timetable/events/schedule",
            data=json.dumps(
                {
                    "ownerEmail": "coach@example.com",
                    "eventKey": "coach-catchup-template:coach@example.com:7",
                    "scheduledDate": self.future_date,
                    "scheduledTime": "10:00",
                    "durationMinutes": 45,
                    "timezoneOffsetMinutes": -180,
                }
            ),
            content_type="application/json",
        )

        response = call_coach_view(coach_timetable_schedule_event, request)

        self.assertEqual(response.status_code, 409)
        self.assertIn("busy at that time", response.content.decode())
        find_catchup_calendar_record.assert_called_once_with(
            "coach@example.com",
            "coach-catchup-template:coach@example.com:7",
        )
        get_or_create.assert_not_called()
        sync_calendar_event_to_graph.assert_not_called()


class CoachMeetingArtifactTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_transcript_vtt_is_normalised_to_readable_text(self):
        """Cue identifiers out, speaker names in.

        The expected value gained its ``Coach:`` prefix deliberately. The
        extracted transcript exists to be summarised, and a recap built from
        an unattributed wall of text cannot tell what the coach committed to
        from what the learner did, so it credits actions to the wrong person.
        The second line has no ``<v>`` tag and so stays unattributed. See
        coach_api.tests_meeting_summary.MeetingTranscriptExtractionTests for
        the full Teams export shape.
        """
        self.assertEqual(
            coach_meeting_transcript_text(
                """WEBVTT

1
00:00:01.000 --> 00:00:03.000
<v Coach>Welcome to the review.</v>

2
00:00:04.000 --> 00:00:06.000
Learner progress looks strong.
"""
            ),
            "Coach: Welcome to the review.\nLearner progress looks strong.",
        )

    def test_artifacts_endpoint_returns_transcript_and_recording(self):
        record = CoachCalendarEvent(
            event_key="mcr:42:1:2026-09-01",
            owner_email="coach@example.com",
            owner_name="Coach Owner",
            learner_name="Test Learner",
            learner_email="learner@example.com",
            event_type="mcr",
            status="scheduled",
        )
        request = self.factory.post("/coach_api/coach/timetable/events/mcr:42:1:2026-09-01/artifacts")
        request.coach_email = "coach@example.com"

        def graph_response(_method, path, *, payload=None):
            self.assertIsNone(payload)
            if path.endswith("/attendanceReports"):
                return {"value": [{"id": "report-1", "meetingStartDateTime": "2026-09-01T10:00:00Z"}]}
            if "attendanceReports/report-1" in path:
                return {
                    "id": "report-1",
                    "meetingStartDateTime": "2026-09-01T10:00:00Z",
                    "meetingEndDateTime": "2026-09-01T10:30:00Z",
                    "totalParticipantCount": 1,
                    "attendanceRecords": [
                        {
                            "id": "attendee-1",
                            "emailAddress": "learner@example.com",
                            "role": "Attendee",
                            "attendanceIntervals": [
                                {
                                    "joinDateTime": "2026-09-01T10:05:00Z",
                                    "leaveDateTime": "2026-09-01T10:25:00Z",
                                }
                            ],
                        }
                    ],
                }
            if path.endswith("/transcripts"):
                return {"value": [{"id": "transcript-1", "createdDateTime": "2026-09-01T10:00:00Z"}]}
            if path.endswith("/recordings"):
                return {"value": [{"id": "recording-1", "endDateTime": "2026-09-01T10:30:00Z"}]}
            return {"value": []}

        with patch("coach_api.views.coach_meeting_artifact_record", return_value=record), \
             patch("coach_api.views.has_graph_credentials", return_value=True), \
             patch("coach_api.views.coach_meeting_graph_target", return_value=("users/coach/onlineMeetings/meeting-1", None)), \
             patch("coach_api.views.fetch_coach_meeting_transcript_content", return_value={
                 "transcript_vtt": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello learner.",
                 "transcript_text": "Hello learner.",
                 "transcript_content_type": "text/vtt",
                 "transcript_fetched_at": "2026-09-01T10:31:00+00:00",
                 "transcript_fetch_error": "",
             }) as fetch_transcript, \
             patch("coach_api.views.microsoft_graph_request", side_effect=graph_response):
            response = unwrap(coach_timetable_event_artifacts)(request, record.event_key)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(item["artifact_type"], item["id"]) for item in payload["artifacts"]],
            [("transcript", "transcript-1"), ("recording", "recording-1")],
        )
        self.assertNotIn("transcript_text", payload["artifacts"][0])
        self.assertNotIn("transcript_vtt", payload["artifacts"][0])
        fetch_transcript.assert_called_once_with("users/coach/onlineMeetings/meeting-1", "transcript-1")
        self.assertEqual(payload["attendance"]["reportCount"], 1)
        self.assertEqual(payload["attendance"]["attendedCount"], 1)
        self.assertEqual(payload["attendance"]["expectedCount"], 2)
        self.assertEqual(payload["attendance"]["expectedAttendedCount"], 1)
        self.assertEqual(payload["attendance"]["expectedAbsentCount"], 1)
        self.assertEqual(payload["attendance"]["records"][0]["displayName"], "Learner")
        self.assertEqual(payload["attendance"]["records"][0]["totalAttendanceSeconds"], 1200)
        self.assertEqual(
            [(item["role"], item["email"]) for item in payload["attendance"]["expectedAttendees"]],
            [("coach", "coach@example.com"), ("learner", "learner@example.com")],
        )

    def test_artifacts_endpoint_default_reads_stored_snapshot_only(self):
        record = CoachCalendarEvent(
            event_key="mcr:42:1:2026-09-01",
            owner_email="coach@example.com",
            learner_name="Test Learner",
            event_type="mcr",
            status="completed",
        )
        request = self.factory.get("/coach_api/coach/timetable/events/mcr:42:1:2026-09-01/artifacts")
        request.coach_email = "coach@example.com"
        stored_snapshot = {
            "attendance": {"reportCount": 0, "records": [], "tracker": []},
            "artifacts": [{"id": "recording-1", "artifact_type": "recording", "graph_artifact_id": "recording-1"}],
            "attendanceReports": [],
            "attendanceTracker": {"tracker": []},
            "errors": [],
            "partial": False,
            "storage": {"stored": True, "syncedAt": "2026-09-01T10:35:00+00:00"},
        }

        with patch("coach_api.views.coach_meeting_artifact_record", return_value=record), \
             patch("coach_api.views.stored_coach_meeting_snapshot", return_value=stored_snapshot) as stored, \
             patch("coach_api.views.stored_coach_meeting_summary", return_value=None), \
             patch("coach_api.views.fetch_coach_meeting_graph_snapshot") as graph_fetch:
            response = unwrap(coach_timetable_event_artifacts)(request, record.event_key)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["artifacts"][0]["id"], "recording-1")
        self.assertEqual(payload["storage"]["stored"], True)
        stored.assert_called_once_with(record)
        graph_fetch.assert_not_called()

    def test_transcript_content_prefers_stored_database_copy(self):
        record = CoachCalendarEvent(
            event_key="mcr:42:1:2026-09-01",
            owner_email="coach@example.com",
            event_type="mcr",
        )
        request = self.factory.get(
            "/coach_api/coach/timetable/events/mcr:42:1:2026-09-01/artifacts/transcript/transcript-1/content?preview=1"
        )

        with patch("coach_api.views.stored_coach_meeting_transcript", return_value={
            "transcript_vtt": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nStored transcript.",
            "transcript_content_type": "text/vtt",
        }) as stored, \
             patch("coach_api.views.has_graph_credentials", return_value=False), \
             patch("coach_api.views.coach_meeting_transcript_selection", return_value={"artifactIds": ["transcript-1"], "code": None}):
            response = coach_meeting_artifact_content_response(
                request,
                record,
                record.event_key,
                "transcript",
                "transcript-1",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nStored transcript.")
        self.assertIn("inline", response["Content-Disposition"])
        stored.assert_called_once_with(record, "transcript-1")

    def test_combined_transcript_content_returns_one_text_file(self):
        record = CoachCalendarEvent(
            event_key="mcr:42:1:2026-09-01",
            owner_email="coach@example.com",
            event_type="mcr",
        )
        request = self.factory.get(
            "/coach_api/coach/timetable/events/mcr:42:1:2026-09-01/artifacts/transcript/combined/content?preview=1"
        )

        with patch("coach_api.views.stored_coach_meeting_transcript_for_summary", return_value={
            "artifactId": "transcript-1,transcript-2",
            "text": "First transcript segment.\n\nSecond transcript segment.",
        }) as stored, patch("coach_api.views.coach_meeting_transcript_selection", return_value={
            "artifactIds": ["transcript-1", "transcript-2"], "code": None,
        }):
            response = coach_meeting_artifact_content_response(
                request,
                record,
                record.event_key,
                "transcript",
                "combined",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.content.decode(),
            "First transcript segment.\n\nSecond transcript segment.",
        )
        self.assertIn("text/plain", response["Content-Type"])
        self.assertIn("transcript.txt", response["Content-Disposition"])
        stored.assert_called_once_with(record, artifact_ids=["transcript-1", "transcript-2"])

    def test_progress_review_expected_attendees_include_employer(self):
        record = CoachCalendarEvent(
            event_key="progress-review:42:1:2026-09-01",
            owner_email="coach@example.com",
            owner_name="Coach Owner",
            learner_id=42,
            learner_name="Test Learner",
            learner_email="learner@example.com",
            event_type="progress-review",
            status="completed",
        )
        learner = SimpleNamespace(
            id=42,
            full_name="Test Learner",
            email="learner@example.com",
            programme="",
            lifecycle_status="active",
        )

        with patch("coach_api.views.coach_meeting_record_learner", return_value=learner), \
             patch("coach_api.views.resolve_caseload_source_row", return_value=SimpleNamespace()), \
             patch("coach_api.views.learner_employer_attendee", return_value={"name": "Line Manager", "email": "manager@example.com"}):
            expected = coach_meeting_expected_attendees(record)

        self.assertEqual(
            [(item["role"], item["email"]) for item in expected],
            [
                ("coach", "coach@example.com"),
                ("learner", "learner@example.com"),
                ("employer", "manager@example.com"),
            ],
        )

    @patch("coach_api.views.microsoft_graph_request")
    def test_graph_target_recovers_join_url_from_calendar_event(self, graph_request):
        record = CoachCalendarEvent(
            event_key="catch-up:42:1:2026-09-01",
            owner_email="coach@example.com",
            graph_organizer_email="coach@example.com",
            graph_event_id="event-1",
            meeting_link="https://outlook.office.com/calendar/item/event-1",
        )
        graph_request.side_effect = [
            {"onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/example"}},
            {"value": [{"id": "meeting-1"}]},
        ]

        base, error = coach_meeting_graph_target(record)

        self.assertIsNone(error)
        self.assertEqual(base, "users/coach%40example.com/onlineMeetings/meeting-1")


@override_settings(
    AZURE_STORAGE_ACCOUNT="lmsstorage",
    AZURE_STORAGE_KEY="test-key",
    AZURE_QUARANTINE_CONTAINER="evidence-quarantine",
    AZURE_APPROVED_CONTAINER="evidence-approved",
    AZURE_REJECTED_CONTAINER="evidence-rejected",
)
class AbsenceEvidenceRoutingTests(SimpleTestCase):
    @patch("coach_api.views.move_blob")
    def test_approval_moves_quarantine_blob_to_approved(self, move_blob):
        report = SimpleNamespace(
            evidence_image_url=(
                "https://lmsstorage.blob.core.windows.net/"
                "evidence-quarantine/absence-reports/42/file.pdf"
            )
        )

        moved = route_absence_report_evidence(
            report,
            CoachAbsenceReport.STATUS_APPROVED,
        )

        self.assertEqual(
            (
                "evidence-quarantine",
                "evidence-approved",
                "absence-reports/42/file.pdf",
            ),
            moved,
        )
        move_blob.assert_called_once_with(
            "evidence-quarantine",
            "evidence-approved",
            "absence-reports/42/file.pdf",
        )
        self.assertIn("/evidence-approved/", report.evidence_image_url)

    @patch("coach_api.views.move_blob")
    def test_decline_moves_quarantine_blob_to_rejected(self, move_blob):
        report = SimpleNamespace(
            evidence_image_url=(
                "https://lmsstorage.blob.core.windows.net/"
                "evidence-quarantine/absence-reports/42/file.pdf"
            )
        )

        route_absence_report_evidence(
            report,
            CoachAbsenceReport.STATUS_DECLINED,
        )

        move_blob.assert_called_once_with(
            "evidence-quarantine",
            "evidence-rejected",
            "absence-reports/42/file.pdf",
        )
        self.assertIn("/evidence-rejected/", report.evidence_image_url)

    @patch("coach_api.views.move_blob")
    def test_legacy_local_evidence_is_left_untouched(self, move_blob):
        report = SimpleNamespace(
            evidence_image_url="/media/absence-evidence/legacy.pdf",
        )

        moved = route_absence_report_evidence(
            report,
            CoachAbsenceReport.STATUS_APPROVED,
        )

        self.assertIsNone(moved)
        self.assertEqual(
            "/media/absence-evidence/legacy.pdf",
            report.evidence_image_url,
        )
        move_blob.assert_not_called()


class MonthlyActivityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_reported_minutes_treats_small_bare_numbers_as_hours_and_large_values_as_minutes(self):
        self.assertEqual(reported_minutes("2"), 120.0)
        self.assertEqual(reported_minutes("2h"), 120.0)
        self.assertEqual(reported_minutes("1.5"), 90.0)
        self.assertEqual(reported_minutes("120"), 120.0)
        self.assertEqual(reported_minutes("90 min"), 90.0)

    def test_monthly_event_includes_scheduled_date_even_when_target_is_in_another_month(self):
        event = {
            "targetDate": "2026-10-26",
            "scheduledDate": "2026-09-07",
            "date": "2026-09-07",
        }

        self.assertTrue(monthly_event_is_between(event, date(2026, 9, 1), date(2026, 9, 30)))

    def test_monthly_target_uses_training_plan_weeks_and_component_expected_otjh(self):
        training_plan = [{
            "moduleTitle": "Module A",
            "weeks": [
                {
                    "weekTitle": "Week 1",
                    "components": [
                        {"componentId": "component-1"},
                        {"componentId": "component-2"},
                    ],
                },
                {
                    "weekTitle": "Week 2",
                    "components": [
                        {"componentId": "component-3"},
                    ],
                },
            ],
        }]

        self.assertEqual(
            curriculum_monthly_target_hours_weeks(training_plan),
            [["component-1", "component-2"], ["component-3"]],
        )
        self.assertEqual(
            curriculum_monthly_target_hours(
                training_plan,
                date(2026, 8, 24),
                date(2026, 8, 1),
                date(2026, 8, 31),
                {"component-1": 1.5, "component-2": 2.0, "component-3": 3.0},
            ),
            6.5,
        )

    def test_monthly_target_falls_back_to_expected_otjh_embedded_in_plan(self):
        training_plan = [{
            "moduleTitle": "Module A",
            "weeks": [{
                "weekTitle": "Week 1",
                "components": [
                    {"id": "component-1", "expectedOtjh": "1.25"},
                    {"component_id": "component-2", "expected_otjh": "2.75"},
                ],
            }],
        }]

        self.assertEqual(
            curriculum_monthly_target_hours_weeks(training_plan),
            [["component-1", "component-2"]],
        )
        self.assertEqual(
            curriculum_monthly_target_hours(
                training_plan,
                date(2026, 8, 3),
                date(2026, 8, 1),
                date(2026, 8, 31),
                {},
            ),
            4.0,
        )

    def test_monthly_target_uses_caseload_source_start_date_when_profile_start_is_blank(self):
        row = SimpleNamespace(
            id=42,
            programme="TEST USER FLOW",
            start_date=None,
            training_plan=[{
                "moduleTitle": "Module A",
                "weeks": [{
                    "weekTitle": "Week 1",
                    "components": [{"componentId": "component-1"}],
                }],
            }],
            training_plan_progress=[],
            activity_feed=[],
            _caseload_source=SimpleNamespace(start_date=date(2026, 8, 26)),
        )
        learner = {
            "id": "42",
            "name": "Mahmoud Fouda",
            "initials": "MF",
            "email": "mahmoud@example.com",
            "cohortName": "Aug-2026",
            "group": "G1-Wed",
            "otjhStatus": "On Track",
            "otjhTarget": 120,
            "otjhCompleted": 40,
        }

        result = build_monthly_activity_learner(
            row,
            learner,
            events=[],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            expected_otjh_by_component_id={"component-1": 2.5},
        )

        self.assertEqual(result["otjh"]["monthlyTarget"], 2.5)

    @patch("coach_api.views.hydrate_training_plan")
    @patch("coach_api.views.fetch_verified_teams_attendance_rows", return_value=[])
    @patch("coach_api.views.curriculum_expected_otjh_by_component_id", return_value={"component-1": 2.5})
    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.collect_generated_timetable", return_value={"events": [], "owner_name": "Med Maher"})
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_monthly_activity_hydrates_training_plan_for_monthly_target(
        self,
        fetch_rows,
        collect_generated_timetable,
        serialize_learner,
        expected_lookup,
        fetch_attendance,
        hydrate_plan,
    ):
        source = SimpleNamespace(start_date=date(2026, 8, 26), learning_plan=[])
        row = SimpleNamespace(
            id=42,
            coach_name="Med Maher",
            programme="TEST USER FLOW",
            start_date=None,
            training_plan=[],
            training_plan_progress=[],
            activity_feed=[],
            _caseload_source=source,
        )
        hydrated = [{
            "moduleTitle": "Module A",
            "weeks": [{
                "weekTitle": "Week 1",
                "components": [{"componentId": "component-1"}],
            }],
        }]
        hydrate_plan.return_value = hydrated
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {
            "id": "42",
            "name": "Mahmoud Fouda",
            "initials": "MF",
            "email": "mahmoud@example.com",
            "cohortName": "Aug-2026",
            "group": "G1-Wed",
            "programme": "TEST USER FLOW",
            "enrollmentStatus": "active",
            "otjhStatus": "On Track",
            "otjhTarget": 120,
            "otjhCompleted": 40,
        }

        response = call_coach_view(coach_monthly_activity,
            self.factory.get(
                "/coach_api/coach/monthly-activity",
                {"owner_email": "coach@example.com", "month": "2026-08"},
            )
        )

        payload = json.loads(response.content)
        hydrate_plan.assert_called_with(source.learning_plan)
        expected_lookup.assert_called_once_with(["component-1"])
        self.assertEqual(payload["learners"][0]["otjh"]["monthlyTarget"], 2.5)

    def test_build_monthly_activity_learner_dedupes_duplicate_feed_items(self):
        row = SimpleNamespace(
            programme="Marketing",
            training_plan_progress=[],
            activity_feed=[
                {
                    "kind": "quiz",
                    "quizId": "quiz-1",
                    "attempt": "1",
                    "date": "2026-07-15",
                    "quizName": "Quiz A",
                    "detail": "Score 8/10",
                },
                {
                    "kind": "quiz",
                    "quizId": "quiz-1",
                    "attempt": "1",
                    "date": "2026-07-15",
                    "quizName": "Quiz A",
                    "detail": "Score 8/10",
                },
                {
                    "kind": "quiz",
                    "quizId": "quiz-1",
                    "attempt": "2",
                    "date": "2026-07-15",
                    "quizName": "Quiz A",
                    "detail": "Score 9/10",
                },
            ],
        )

        learner = {
            "id": "42",
            "name": "Test Learner",
            "initials": "TL",
            "email": "learner@example.com",
            "cohortName": "Cohort A",
            "group": "Group 1",
            "otjhStatus": "On Track",
            "otjhTarget": 120,
            "otjhCompleted": 40,
        }

        result = build_monthly_activity_learner(
            row,
            learner,
            events=[],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )

        self.assertEqual(len(result["activities"]), 2)
        self.assertEqual(
            [activity["id"] for activity in result["activities"]],
            [
                "feed:quiz|quiz-1|1|2026-07-15|Quiz A",
                "feed:quiz|quiz-1|2|2026-07-15|Quiz A",
            ],
        )

    def test_monthly_activity_log_excludes_unscheduled_calendar_events(self):
        row = SimpleNamespace(
            programme="Marketing",
            training_plan_progress=[],
            activity_feed=[],
        )
        learner = {
            "id": "42",
            "name": "Test Learner",
            "initials": "TL",
            "email": "learner@example.com",
            "cohortName": "Cohort A",
            "group": "Group 1",
            "otjhStatus": "On Track",
            "otjhTarget": 120,
            "otjhCompleted": 40,
        }
        events = [{
            "id": "event-1",
            "eventKey": "event-1",
            "learnerId": "42",
            "email": "learner@example.com",
            "source": "mcr",
            "title": "Monthly Coaching",
            "status": "needs-schedule",
            "targetDate": "2026-08-31",
        }]

        result = build_monthly_activity_learner(
            row,
            learner,
            events=events,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )

        self.assertEqual(result["activities"], [])
        self.assertEqual(result["coaching"]["needsSchedule"], 1)
        self.assertIn("1 session need schedule", result["needsAction"])

    @patch("coach_api.views.curriculum_expected_otjh_by_component_id", return_value={"component-1": 1.5})
    def test_build_otjh_completed_entries_prefers_curriculum_expected_otjh(self, expected_lookup):
        entries = build_otjh_completed_entries(
            [
                {
                    "kind": "video",
                    "componentId": "component-1",
                    "componentTitle": "Pre-recorded video",
                    "reportedTime": "120",
                    "submittedAt": "2026-07-18T08:05:37Z",
                }
            ],
            [],
            [{
                "moduleTitle": "Module A",
                "weeks": [{
                    "weekTitle": "Week 1",
                    "components": [{
                        "componentId": "component-1",
                        "componentTitle": "Pre-recorded video",
                    }],
                }],
            }],
        )

        expected_lookup.assert_called_once_with(["component-1"])
        self.assertEqual(entries[0]["hours"], 1.5)
        self.assertEqual(entries[0]["reportedTime"], "120")

    @patch("coach_api.views.fetch_verified_teams_attendance_rows", return_value=[])
    @patch("coach_api.views.build_monthly_activity_learner")
    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.collect_generated_timetable", return_value={"events": [], "owner_name": "Med Maher"})
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_monthly_activity_uses_cached_snapshots_by_default(
        self,
        fetch_rows,
        collect_generated_timetable,
        serialize_learner,
        build_monthly_activity_learner,
        fetch_attendance,
    ):
        row = SimpleNamespace(id=2, coach_name="Med Maher")
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {"enrollmentStatus": "active"}
        build_monthly_activity_learner.return_value = {
            "status": "on-track",
            "learning": {"total": 0, "quizzes": 0, "videos": 0, "components": 0},
            "coaching": {"total": 0, "booked": 0, "needsSchedule": 0},
            "evidence": {"submitted": 0},
            "ksb": {"codes": []},
            "otjh": {"monthlyHours": 0},
            "needsAction": [],
            "activities": [],
        }

        response = call_coach_view(coach_monthly_activity,
            self.factory.get(
                "/coach_api/coach/monthly-activity",
                {"owner_email": "coach@example.com", "month": "2026-08"},
            )
        )

        self.assertEqual(response.status_code, 200)
        serialize_learner.assert_called_once_with(row, refresh_live_snapshots=False)
        fetch_attendance.assert_called_once_with(
            [2],
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        collect_generated_timetable.assert_called_once_with(
            "coach@example.com",
            include_live_sessions=False,
            include_scheduler_queues=False,
        )

    @patch("coach_api.views.fetch_verified_teams_attendance_rows", return_value=[])
    @patch("coach_api.views.build_monthly_activity_learner")
    @patch("coach_api.views.serialize_caseload_learner")
    @patch("coach_api.views.collect_generated_timetable", return_value={"events": [], "owner_name": "Med Maher"})
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_monthly_activity_allows_live_snapshot_refresh_when_requested(
        self,
        fetch_rows,
        collect_generated_timetable,
        serialize_learner,
        build_monthly_activity_learner,
        fetch_attendance,
    ):
        row = SimpleNamespace(id=2, coach_name="Med Maher")
        fetch_rows.return_value = [row]
        serialize_learner.return_value = {"enrollmentStatus": "active"}
        build_monthly_activity_learner.return_value = {
            "status": "on-track",
            "learning": {"total": 0, "quizzes": 0, "videos": 0, "components": 0},
            "coaching": {"total": 0, "booked": 0, "needsSchedule": 0},
            "evidence": {"submitted": 0},
            "ksb": {"codes": []},
            "otjh": {"monthlyHours": 0},
            "needsAction": [],
            "activities": [],
        }

        response = call_coach_view(coach_monthly_activity,
            self.factory.get(
                "/coach_api/coach/monthly-activity",
                {"owner_email": "coach@example.com", "month": "2026-08", "live": "1"},
            )
        )

        self.assertEqual(response.status_code, 200)
        serialize_learner.assert_called_once_with(row, refresh_live_snapshots=True)
        fetch_attendance.assert_called_once()


class EvidenceQueueSnapshotTests(SimpleTestCase):
    @patch("coach_api.views.serialize_caseload_learner", return_value={"id": ""})
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_fetch_evidence_file_queue_uses_cached_snapshots(
        self,
        fetch_rows,
        serialize_learner,
    ):
        row = SimpleNamespace(id=2)
        fetch_rows.return_value = [row]

        items, learners = fetch_evidence_file_queue("coach@example.com")

        self.assertEqual(items, [])
        self.assertEqual(learners, [{"id": ""}])
        serialize_learner.assert_called_once_with(row, refresh_live_snapshots=False)


class CaseloadOtjhSnapshotTests(SimpleTestCase):
    @patch("coach_api.views.current_curriculum_ksb_items_for_learner", return_value=[])
    @patch("coach_api.views.learner_activity_feed_entries", return_value=[])
    @patch("coach_api.views.refresh_caseload_learner_ksb_snapshot")
    @patch("coach_api.views.refresh_learner_otjh_snapshot")
    def test_serialize_caseload_learner_uses_live_otjh_snapshot(
        self,
        refresh_snapshot,
        refresh_ksb_snapshot,
        learner_activity_feed_entries,
        _current_ksbs,
    ):
        row = SimpleNamespace(
            id=2,
            username="mahmopud fouda",
            full_name="mahmopud fouda",
            email="learner@example.com",
            coach_name="Med Maher",
            coach_email="Med.Maher@kentbusinesscollege.com",
            programme_status="Active",
            lifecycle_status="active",
            cohort="Cohort A",
            group="Group 1",
            start_date=None,
            end_date=None,
            gateway_review_date=None,
            coach_rag="",
            minimum_hours=Decimal("0"),
            planned_hours=Decimal("111"),
            completed_hours=Decimal("3.4"),
            target_hours=Decimal("16"),
            progress_hours=Decimal("-12.6"),
            progress_variance=Decimal("-0.79"),
            otjh_status="At risk",
            training_plan=[],
            training_plan_progress=[],
            ksbs=[],
            save=lambda **kwargs: None,
        )

        def apply_live_snapshot(learner, *, source=None, detail=None):
            learner.completed_hours = Decimal("29")
            learner.target_hours = Decimal("41.5")
            learner.planned_hours = Decimal("111")
            learner.progress_hours = Decimal("-12.5")
            learner.progress_variance = Decimal("-0.30")
            learner.otjh_status = "At risk"
            return {}

        refresh_snapshot.side_effect = apply_live_snapshot

        payload = serialize_caseload_learner(row)

        refresh_ksb_snapshot.assert_called_once_with(row)
        refresh_snapshot.assert_called_once_with(row, source=None)
        learner_activity_feed_entries.assert_called_once_with(row)
        self.assertEqual(payload["otjhCompleted"], 29.0)
        self.assertEqual(payload["otjhTarget"], 41.5)
        self.assertEqual(payload["otjhPlanned"], 111.0)
        self.assertEqual(float(payload["progressVariance"]), -0.3)

    @patch("coach_api.views.current_curriculum_ksb_items_for_learner", return_value=[])
    @patch("coach_api.views.learner_activity_feed_entries", return_value=[])
    @patch("coach_api.views.refresh_learner_ksb_snapshot")
    @patch("coach_api.views.refresh_learner_otjh_snapshot", return_value={})
    def test_serialize_caseload_learner_uses_live_ksb_snapshot(
        self,
        refresh_otjh_snapshot,
        refresh_ksb_snapshot,
        learner_activity_feed_entries,
        _current_ksbs,
    ):
        row = SimpleNamespace(
            id=5,
            username="mahmopud fouda",
            full_name="mahmopud fouda",
            email="learner@example.com",
            coach_name="Med Maher",
            coach_email="Med.Maher@kentbusinesscollege.com",
            programme="Project Management",
            programme_status="Active",
            lifecycle_status="active",
            cohort="Cohort A",
            group="Group 1",
            start_date=None,
            end_date=None,
            gateway_review_date=None,
            coach_rag="",
            minimum_hours=Decimal("0"),
            planned_hours=Decimal("111"),
            completed_hours=Decimal("29"),
            target_hours=Decimal("41.5"),
            progress_hours=Decimal("-12.5"),
            progress_variance=Decimal("-0.30"),
            otjh_status="At risk",
            training_plan=[{"moduleId": "mod-1"}],
            training_plan_progress=[
                {"ksbs": [{"code": "K1"}, {"code": "S1"}]},
            ],
            ksbs=[],
            _prefetched_objects_cache={"assigned_ksbs": ["stale"]},
            _caseload_source=SimpleNamespace(id=5, programme="Project Management"),
            save=lambda **kwargs: None,
        )

        def apply_live_ksb_snapshot(learner, source, training_plan=None):
            learner.ksbs = [{"code": "K1"}, {"code": "S1"}, {"code": "B1"}]
            return learner.ksbs

        refresh_ksb_snapshot.side_effect = apply_live_ksb_snapshot

        payload = serialize_caseload_learner(row)

        refresh_ksb_snapshot.assert_called_once_with(
            row,
            row._caseload_source,
            training_plan=row.training_plan,
        )
        refresh_otjh_snapshot.assert_called_once_with(row, source=row._caseload_source)
        learner_activity_feed_entries.assert_called_once_with(row)
        self.assertEqual(row._prefetched_objects_cache, {})
        self.assertEqual(payload["ksbCompleted"], 2)
        self.assertEqual(payload["ksbTarget"], 3)
        self.assertEqual(payload["ksbProgress"], 67)
        self.assertEqual(payload["knowledgeCompleted"], 1)
        self.assertEqual(payload["knowledgeTarget"], 1)
        self.assertEqual(payload["skillsCompleted"], 1)
        self.assertEqual(payload["skillsTarget"], 1)
        self.assertEqual(payload["behavioursCompleted"], 0)
        self.assertEqual(payload["behavioursTarget"], 1)

    @patch("coach_api.views.current_curriculum_ksb_items_for_learner", return_value=[])
    @patch("coach_api.views.learner_activity_feed_entries", return_value=[])
    @patch("coach_api.views.refresh_learner_ksb_snapshot")
    @patch("coach_api.views.refresh_learner_otjh_snapshot", return_value={})
    def test_already_assigned_learner_skips_the_ksb_snapshot_refresh(
        self,
        refresh_otjh_snapshot,
        refresh_ksb_snapshot,
        learner_activity_feed_entries,
        _current_ksbs,
    ):
        # Regression: a learner who already has a KsbProfileVersion pinned at
        # enrolment can never get a different one from
        # refresh_learner_ksb_snapshot (see replace_learner_ksbs — "an
        # existing learner stays pinned"), so calling it is a guaranteed
        # no-op that still opens a transaction + two get_or_create round
        # trips per learner on every `?live=1` caseload read. It must be
        # skipped once ksb_assignment already exists.
        row = SimpleNamespace(
            id=5, username="mahmopud fouda", full_name="mahmopud fouda", email="learner@example.com",
            coach_name="Med Maher", coach_email="Med.Maher@kentbusinesscollege.com",
            programme="Project Management", programme_status="Active", lifecycle_status="active",
            cohort="Cohort A", group="Group 1", start_date=None, end_date=None, gateway_review_date=None,
            coach_rag="", minimum_hours=Decimal("0"), planned_hours=Decimal("111"), completed_hours=Decimal("29"),
            target_hours=Decimal("41.5"), progress_hours=Decimal("-12.5"), progress_variance=Decimal("-0.30"),
            otjh_status="At risk", training_plan=[{"moduleId": "mod-1"}],
            training_plan_progress=[{"ksbs": [{"code": "K1"}]}],
            ksbs=[{"code": "K1"}],
            ksb_assignment=SimpleNamespace(profile_version=SimpleNamespace(version_hash="abc123")),
            _caseload_source=SimpleNamespace(id=5, programme="Project Management"),
            save=lambda **kwargs: None,
        )

        serialize_caseload_learner(row)

        refresh_ksb_snapshot.assert_not_called()

    @patch("coach_api.views.current_curriculum_ksb_items_for_learner", return_value=[])
    @patch("coach_api.views.learner_activity_feed_entries", return_value=[])
    @patch("coach_api.views.refresh_learner_ksb_snapshot")
    @patch("coach_api.views.refresh_learner_otjh_snapshot", return_value={})
    @patch("coach_api.views.curriculum_expected_otjh_by_component_id", return_value={})
    def test_serialize_caseload_learner_rolls_subcodes_up_to_parent_ksbs(
        self,
        curriculum_expected_lookup,
        refresh_otjh_snapshot,
        refresh_ksb_snapshot,
        learner_activity_feed_entries,
        _current_ksbs,
    ):
        row = SimpleNamespace(
            id=6,
            username="test learner",
            full_name="test learner",
            email="learner@example.com",
            coach_name="Med Maher",
            coach_email="Med.Maher@kentbusinesscollege.com",
            programme="Project Management",
            programme_status="Active",
            lifecycle_status="active",
            cohort="Cohort A",
            group="Group 1",
            start_date=None,
            end_date=None,
            gateway_review_date=None,
            coach_rag="",
            minimum_hours=Decimal("0"),
            planned_hours=Decimal("111"),
            completed_hours=Decimal("29"),
            target_hours=Decimal("41.5"),
            progress_hours=Decimal("-12.5"),
            progress_variance=Decimal("-0.30"),
            otjh_status="At risk",
            training_plan=[{
                "moduleId": "mod-1",
                "moduleTitle": "Module A",
                "weeks": [{
                    "weekId": "week-1",
                    "weekTitle": "Week 1",
                    "components": [{
                        "componentId": "component-1",
                        "componentTitle": "Stakeholder Workshop",
                    }],
                }],
            }],
            training_plan_progress=[
                {
                    "kind": "component",
                    "componentId": "component-1",
                    "reportedTime": "2h",
                    "submittedAt": "2026-08-02T10:00:00Z",
                    "ksbs": ["K3.1", "S1.2", "B2.2"],
                },
                {
                    "kind": "component",
                    "componentId": "component-1",
                    "reportedTime": "2h",
                    "submittedAt": "2026-08-02T11:00:00Z",
                    "ksbs": ["K3.1", "S1.2", "B2.2"],
                },
            ],
            ksbs=[],
            _prefetched_objects_cache={"assigned_ksbs": ["stale"]},
            _caseload_source=SimpleNamespace(id=6, programme="Project Management"),
            save=lambda **kwargs: None,
        )

        def apply_live_ksb_snapshot(learner, source, training_plan=None):
            learner.ksbs = [
                {"code": "K3", "type": "Knowledge", "description": "Stakeholder mapping"},
                {"code": "S1", "type": "Skills", "description": "Stakeholder communication"},
                {"code": "B2", "type": "Behaviours", "description": "Professional ownership"},
            ]
            return learner.ksbs

        refresh_ksb_snapshot.side_effect = apply_live_ksb_snapshot

        payload = serialize_caseload_learner(row)

        refresh_ksb_snapshot.assert_called_once_with(
            row,
            row._caseload_source,
            training_plan=row.training_plan,
        )
        refresh_otjh_snapshot.assert_called_once_with(row, source=row._caseload_source)
        learner_activity_feed_entries.assert_called_once_with(row)
        self.assertEqual(payload["ksbCompleted"], 3)
        self.assertEqual(payload["ksbTarget"], 3)
        self.assertEqual(payload["ksbProgress"], 100)
        self.assertEqual(payload["knowledgeCompleted"], 1)
        self.assertEqual(payload["knowledgeTarget"], 1)
        self.assertEqual(payload["skillsCompleted"], 1)
        self.assertEqual(payload["skillsTarget"], 1)
        self.assertEqual(payload["behavioursCompleted"], 1)
        self.assertEqual(payload["behavioursTarget"], 1)
        self.assertEqual(len(payload["otjhCompletedEntries"]), 1)
        self.assertEqual(payload["otjhCompletedEntries"][0]["title"], "Stakeholder Workshop")
        self.assertEqual(payload["otjhCompletedEntries"][0]["hours"], 2.0)
        self.assertEqual(payload["otjhCompletedEntries"][0]["completedDate"], "02 Aug 2026")
        self.assertEqual(payload["otjhCompletedEntries"][0]["ksbs"], ["B2", "K3", "S1"])
        self.assertEqual(payload["ksbCompletedDetailCount"], 3)
        self.assertEqual(
            [item["code"] for item in payload["ksbCompletedDetails"]],
            ["K3", "S1", "B2"],
        )
        for item in payload["ksbCompletedDetails"]:
            self.assertEqual(len(item["sources"]), 1)
            self.assertEqual(item["sources"][0]["title"], "Stakeholder Workshop")
            self.assertEqual(item["sources"][0]["module"], "Module A")
            self.assertEqual(item["sources"][0]["week"], "Week 1")
            self.assertEqual(item["sources"][0]["completedDate"], "02 Aug 2026")
