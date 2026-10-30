"""Integration tests for the Coach authentication and ownership boundary.

These tests deliberately use Django's Client and the real ``kbc_session``
records.  Calling the view functions directly would skip the middleware and
permission decorators that this suite exists to pin down.

Run with the enrolment test runner because login/staff tables are unmanaged::

    python manage.py test coach_api.tests_security \
        --testrunner=login.test_runner.EnrolmentTestRunner
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client
from .tests_support.transaction_cases import CoachTransactionTestCase

from learner_api.models import StaffUser
from login.identity import ensure_account
from login.models import LoginAccount, LoginSession
from login.sessions import COOKIE_NAME, issue_session

from .models import CoachAbsenceReport, CoachCalendarEvent


COACH_ENDPOINTS = (
    ("POST", "/coach_api/migrated-reviews/imported-review%3Asynthetic/reopen", {"reasonCode": "correction-required"}),
    ("GET", "/coach_api/migrated-reviews/imported-review%3Asynthetic/revisions/1/pdf", None),
    ("GET", "/coach_api/coach/reviews/imported-review%3Asynthetic/previous", None),
    ("GET", "/coach_api/coach/reviews/synthetic-native/previous", None),
    ("POST", "/coach_api/migrated-reviews/imported-review%3Asynthetic/progress", {"progressVersion": "synthetic"}),
    ("GET", "/coach_api/coach/dashboard", None),
    ("GET", "/coach_api/coach/caseload", None),
    ("GET", "/coach_api/coach/learners/101/case-file", None),
    ("GET", "/coach_api/coach/learners/101/next-session", None),
    ("GET", "/coach_api/coach/learners/101/reviews", None),
    ("GET", "/coach_api/coach/caseload/101/coach-rag", None),
    ("PATCH", "/coach_api/coach/caseload/101/coach-rag", {"coachRag": "green"}),
    ("GET", "/coach_api/coach/attendance", None),
    ("GET", "/coach_api/coach/attendance/details?learner_id=101", None),
    ("GET", "/coach_api/coach/absence-reports", None),
    ("PATCH", "/coach_api/coach/absence-reports", {"id": 1, "status": "approved"}),
    ("GET", "/coach_api/coach/evidence-awaiting-review", None),
    ("GET", "/coach_api/coach/marking-queue", None),
    (
        "PATCH",
        "/coach_api/coach/marking-queue/00000000-0000-0000-0000-000000000001",
        {"decision": "accepted"},
    ),
    ("GET", "/coach_api/coach/monthly-activity", None),
    ("GET", "/coach_api/coach/timetable", None),
    (
        "POST",
        "/coach_api/coach/timetable/events/book",
        {
            "learnerId": 101,
            "sessionType": "catch-up",
            "scheduledDate": "2099-01-01",
            "scheduledTime": "10:00",
        },
    ),
    (
        "POST",
        "/coach_api/coach/timetable/events/schedule",
        {
            "eventKey": "mcr:101:1",
            "scheduledDate": "2099-01-01",
            "scheduledTime": "10:00",
        },
    ),
    (
        "POST",
        "/coach_api/coach/timetable/events/action",
        {"eventKey": "mcr:101:1", "action": "start"},
    ),
)


class CoachAuthorizationSecurityTests(CoachTransactionTestCase):
    # Real middleware performs request connection cleanup; use autocommit as
    # production requests do, with real persisted sessions on both aliases.
    databases = {"default", "enrolment"}

    def setUp(self):
        from django.core.cache import cache
        from .models import CoachDashboardSnapshot
        cache.clear()
        CoachDashboardSnapshot.objects.filter(owner_email__in=['coach-a@example.com', 'coach-b@example.com']).delete()
        from read_models.models import ReadModel
        ReadModel.objects.filter(model_key='coach.dashboard', scope_type='coach',
                                 scope_id__in=['coach-a@example.com', 'coach-b@example.com']).delete()
        self.client = Client(SERVER_NAME="localhost")
        self._accounts: list[LoginAccount] = []
        self._staff: list[StaffUser] = []

    def tearDown(self):
        from .models import CoachDashboardSnapshot
        CoachDashboardSnapshot.objects.filter(owner_email__in=['coach-a@example.com', 'coach-b@example.com']).delete()
        from read_models.models import ReadModel
        ReadModel.objects.filter(model_key='coach.dashboard', scope_type='coach',
                                 scope_id__in=['coach-a@example.com', 'coach-b@example.com']).delete()
        CoachAbsenceReport.objects.filter(attendance_id=5001).delete()
        account_ids = [account.id for account in self._accounts]
        if account_ids:
            LoginSession.objects.filter(account_id__in=account_ids).delete()
            LoginAccount.objects.filter(id__in=account_ids).delete()
        staff_ids = [staff.id for staff in self._staff]
        if staff_ids:
            StaffUser.objects.filter(id__in=staff_ids).delete()
        super().tearDown()

    def _make_staff_account(self, *, email: str, access: str) -> LoginAccount:
        staff = StaffUser.objects.create(
            username=email.split("@", 1)[0],
            email=email,
            position="Caseowner",
            access=access,
            type="Admin",
            status="FullUser",
        )
        account, _ = ensure_account("staff", staff.id, subject=staff)
        self._staff.append(staff)
        self._accounts.append(account)
        return account

    def _make_non_staff_account(self, *, role: str) -> LoginAccount:
        account = LoginAccount.objects.create(
            subject_type=role,
            subject_id=9_000_000 + len(self._accounts),
            email=f"{role}-{uuid.uuid4().hex[:10]}@kbc.invalid",
            display_name=f"Test {role}",
            role=role,
            is_active=True,
        )
        self._accounts.append(account)
        return account

    def _authenticate(self, account: LoginAccount) -> None:
        token, _session, _ttl = issue_session(account)
        self.client.cookies[COOKIE_NAME] = token

    def _request(self, method: str, path: str, payload=None):
        if payload is None:
            return self.client.generic(method, path)
        return self.client.generic(
            method,
            path,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_every_coach_route_rejects_anonymous_requests_before_view_work(self):
        for method, path, payload in COACH_ENDPOINTS:
            with self.subTest(method=method, path=path):
                response = self._request(method, path, payload)
                self.assertEqual(response.status_code, 401)

    def test_every_coach_route_rejects_a_learner_session(self):
        self._authenticate(self._make_non_staff_account(role="learner"))
        for method, path, payload in COACH_ENDPOINTS:
            with self.subTest(method=method, path=path):
                response = self._request(method, path, payload)
                self.assertEqual(response.status_code, 403)

    @patch("coach_api.migrated_previous.previous_review")
    def test_imported_previous_context_uses_the_authenticated_coach(self, context):
        from django.http import JsonResponse
        context.return_value = JsonResponse({"available": False, "reason": "No earlier session."})
        account = self._make_staff_account(email="context-coach@kbc.invalid", access="coach")
        self._authenticate(account)
        path = "/coach_api/coach/reviews/imported-review%3Asynthetic/previous"
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        context.assert_called_once_with("context-coach@kbc.invalid", "imported-review:synthetic")
        context.reset_mock()
        response = self.client.get(path, {"ownerEmail": "other@kbc.invalid"})
        self.assertEqual(response.status_code, 403)
        context.assert_not_called()

    def test_removed_coach_message_routes_return_not_found(self):
        self.assertEqual(self.client.get("/coach_api/coach/messages").status_code, 404)
        self.assertEqual(self.client.get("/coach_api/coach/messages/101").status_code, 404)
        self.assertEqual(
            self._request(
                "POST", "/coach_api/coach/messages/101", {"body": "Hello"}
            ).status_code,
            404,
        )

    @patch("coach_api.migrated_reopening_views._locked_overlay", return_value=None)
    @patch("coach_api.migrated_reopening_views._coach_review")
    def test_aptem_reopen_is_assigned_coach_only_including_admin_view_as(self, source, locked):
        owner = self._make_staff_account(email="reopen-coach@kbc.invalid", access="coach")
        source.return_value = (owner.email, {"migratedForm": True})
        path = "/coach_api/migrated-reviews/imported-review%3Asynthetic/reopen"
        payload = {"reasonCode": "correction-required", "answerVersion": "synthetic"}
        self._authenticate(owner)
        self.assertEqual(self._request("POST", path, payload).status_code, 409)
        locked.assert_called_once()
        locked.reset_mock()
        for account in (
            self._make_staff_account(email="other-reopen-coach@kbc.invalid", access="coach"),
            self._make_non_staff_account(role="employer"),
            self._make_staff_account(email="reopen-admin@kbc.invalid", access="super-admin"),
        ):
            self._authenticate(account)
            self.assertEqual(self._request("POST", path, payload).status_code, 403)
        self.assertEqual(self._request("POST", path + "?viewAsCoach=" + owner.email, payload).status_code, 403)
        locked.assert_not_called()

    @patch("coach_api.views.coach_staff_display_name", return_value="Coach A")
    @patch("coach_api.views.collect_tracked_live_session_events", return_value=[])
    @patch(
        "coach_api.views.collect_generated_timetable",
        return_value={"events": [], "summary": {}, "owner_name": "Coach A"},
    )
    @patch("coach_api.views.fetch_caseload_dashboard_profiles", return_value=[])
    def test_coach_identity_comes_from_session_and_legacy_match_is_accepted(
        self,
        _profiles,
        _timetable,
        _live_events,
        _display_name,
    ):
        account = self._make_staff_account(email="coach-a@example.com", access="coach")
        self._authenticate(account)

        without_legacy = self.client.get("/coach_api/coach/dashboard")
        matching_legacy = self.client.get(
            "/coach_api/coach/dashboard", {"owner_email": "  COACH-A@example.com  "}
        )

        self.assertEqual(without_legacy.status_code, 200)
        self.assertEqual(without_legacy.json()["owner"]["email"], "coach-a@example.com")
        self.assertEqual(matching_legacy.status_code, 200)
        self.assertEqual(matching_legacy.json()["owner"]["email"], "coach-a@example.com")

    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_query_owner_email_tampering_is_rejected_before_data_access(self, profiles):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )

        response = self.client.get(
            "/coach_api/coach/dashboard", {"owner_email": "coach-b@example.com"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "coach_identity_mismatch")
        profiles.assert_not_called()

    @staticmethod
    def _case_file_profile():
        return SimpleNamespace(
            id=101, enrolment_id=501, aptem_id=None, learner_type="apprenticeship",
            full_name="Owned Learner", email="owned@example.test", programme="Programme A",
            programme_status="Active", cohort="Cohort A", group_name="Group A",
            lifecycle_status="active", coach_name="Coach A", coach_email="coach-a@example.com",
            coach_rag="green", start_date=None, end_date=None, gateway_review_date=None,
        )

    @patch("coach_api.views.fetch_case_file_shell")
    def test_case_file_shell_allows_an_owned_learner(self, fetch_shell):
        fetch_shell.return_value = (self._case_file_profile(), None)
        self._authenticate(self._make_staff_account(email="coach-a@example.com", access="coach"))

        response = self.client.get("/coach_api/coach/learners/101/case-file")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["identity"]["learnerId"], "101")
        fetch_shell.assert_called_once_with("coach-a@example.com", 101)

    @patch("coach_api.views.fetch_case_file_shell", return_value=(None, None))
    def test_case_file_shell_hides_another_coachs_learner(self, fetch_shell):
        self._authenticate(self._make_staff_account(email="coach-a@example.com", access="coach"))

        response = self.client.get("/coach_api/coach/learners/202/case-file")

        self.assertEqual(response.status_code, 404)
        fetch_shell.assert_called_once_with("coach-a@example.com", 202)

    @patch("coach_api.views.fetch_case_file_shell")
    def test_case_file_shell_view_as_uses_the_selected_coach_scope(self, fetch_shell):
        fetch_shell.return_value = (self._case_file_profile(), None)
        self._make_staff_account(email="coach-a@example.com", access="coach")
        self._authenticate(self._make_staff_account(email="admin@example.com", access="super-admin"))

        response = self.client.get(
            "/coach_api/coach/learners/101/case-file", {"viewAsCoach": "coach-a@example.com"}
        )

        self.assertEqual(response.status_code, 200)
        fetch_shell.assert_called_once_with("coach-a@example.com", 101)

    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_json_owner_email_tampering_cannot_create_calendar_data(
        self, fetch_caseload, sync_graph
    ):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        before = CoachCalendarEvent.objects.count()

        response = self._request(
            "POST",
            "/coach_api/coach/timetable/events/book",
            {
                "ownerEmail": "coach-b@example.com",
                "learnerId": 202,
                "sessionType": "catch-up",
                "scheduledDate": "2099-01-01",
                "scheduledTime": "10:00",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(CoachCalendarEvent.objects.count(), before)
        fetch_caseload.assert_not_called()
        sync_graph.assert_not_called()

    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_super_admin_cannot_impersonate_a_coach_on_normal_endpoints(self, profiles):
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )

        response = self.client.get(
            "/coach_api/coach/dashboard", {"owner_email": "coach-a@example.com"}
        )

        self.assertEqual(response.status_code, 403)
        profiles.assert_not_called()

    @patch("coach_api.views.LearnerProfile.objects.annotate")
    def test_coach_cannot_update_another_coachs_learner_rag(self, annotate):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        owned_queryset = MagicMock()
        owned_queryset.update.return_value = 0
        annotate.return_value.filter.return_value = owned_queryset

        response = self._request(
            "PATCH",
            "/coach_api/coach/caseload/202/coach-rag",
            {"coachRag": "red"},
        )

        self.assertEqual(response.status_code, 404)
        annotate.return_value.filter.assert_called_once_with(
            id=202,
            coach_email_key="coach-a@example.com",
        )
        owned_queryset.update.assert_called_once_with(coach_rag="red")

    @patch("coach_api.views.LearnerProfile.objects.annotate")
    def test_coach_cannot_review_another_coachs_marking_submission(
        self, annotate
    ):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        annotate.return_value.filter.return_value.values_list.return_value = [101]
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        enrolment_connection = MagicMock()
        enrolment_connection.cursor.return_value.__enter__.return_value = cursor
        submission_id = "00000000-0000-0000-0000-000000000001"

        with patch("coach_api.views.connections", {"enrolment": enrolment_connection}):
            response = self._request(
                "PATCH",
                f"/coach_api/coach/marking-queue/{submission_id}",
                {"decision": "accepted"},
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(cursor.execute.call_count, 1)
        update_sql = cursor.execute.call_args.args[0].lower()
        self.assertIn("update", update_sql)
        self.assertIn("learner_id = any", update_sql)

    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_coach_cannot_book_for_another_coachs_learner(self, fetch_caseload, sync_graph):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        fetch_caseload.return_value = [SimpleNamespace(id=101)]
        before = CoachCalendarEvent.objects.count()

        response = self._request(
            "POST",
            "/coach_api/coach/timetable/events/book",
            {
                "learnerId": 202,
                "sessionType": "catch-up",
                "scheduledDate": "2099-01-01",
                "scheduledTime": "10:00",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(CoachCalendarEvent.objects.count(), before)
        sync_graph.assert_not_called()

    @patch("coach_api.views.fetch_owner_active_learner_profiles", return_value=[])
    def test_coach_cannot_update_another_coachs_absence_report(self, _active_rows):
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        report = CoachAbsenceReport.objects.create(
            attendance_id=5001,
            owner_email="coach-b@example.com",
            owner_name="Coach B",
            learner_id=202,
            learner_name="Learner B",
            learner_email="learner-b@example.com",
            session_title="Session",
            session_date="2099-01-01",
            reason="Test",
        )

        response = self._request(
            "PATCH",
            "/coach_api/coach/absence-reports",
            {"id": report.id, "status": "approved"},
        )

        self.assertEqual(response.status_code, 404)
        report.refresh_from_db()
        self.assertEqual(report.status, CoachAbsenceReport.STATUS_PENDING)

    @patch("coach_api.views.coach_staff_display_name", return_value="Coach A")
    @patch("coach_api.views.collect_tracked_live_session_events", return_value=[])
    @patch(
        "coach_api.views.collect_generated_timetable",
        return_value={"events": [], "summary": {}, "owner_name": "Coach A"},
    )
    @patch("coach_api.views.fetch_caseload_learner_profiles", return_value=[])
    def test_super_admin_view_as_reads_the_named_coachs_workspace(
        self,
        profiles,
        _timetable,
        _live_events,
        _display_name,
    ):
        self._make_staff_account(email="coach-a@example.com", access="coach")
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )

        response = self.client.get(
            "/coach_api/coach/dashboard", {"viewAsCoach": "  COACH-A@example.com  "}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["owner"]["email"], "coach-a@example.com")
        profiles.assert_called_once_with("coach-a@example.com")

    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_super_admin_without_a_selection_is_told_to_choose_a_coach(self, profiles):
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )

        response = self.client.get("/coach_api/coach/dashboard")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "coach_selection_required")
        profiles.assert_not_called()

    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_view_as_only_resolves_accounts_that_hold_coach_access(self, profiles):
        self._make_staff_account(email="designer@example.com", access="curriculum")
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )

        for target in ("designer@example.com", "nobody@example.com"):
            with self.subTest(target=target):
                response = self.client.get(
                    "/coach_api/coach/dashboard", {"viewAsCoach": target}
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "coach_not_found")
        profiles.assert_not_called()

    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    def test_view_as_cannot_write_as_the_coach_it_is_reading(
        self, fetch_caseload, sync_graph
    ):
        self._make_staff_account(email="coach-a@example.com", access="coach")
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )
        before = CoachCalendarEvent.objects.count()

        response = self._request(
            "POST",
            "/coach_api/coach/timetable/events/book",
            {
                "viewAsCoach": "coach-a@example.com",
                "learnerId": 101,
                "sessionType": "catch-up",
                "scheduledDate": "2099-01-01",
                "scheduledTime": "10:00",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "coach_view_as_read_only")
        self.assertEqual(CoachCalendarEvent.objects.count(), before)
        fetch_caseload.assert_not_called()
        sync_graph.assert_not_called()

    @patch("coach_api.views.fetch_caseload_dashboard_profiles")
    def test_a_coach_cannot_view_as_another_coach(self, profiles):
        self._make_staff_account(email="coach-b@example.com", access="coach")
        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )

        response = self.client.get(
            "/coach_api/coach/dashboard", {"viewAsCoach": "coach-b@example.com"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "coach_identity_mismatch")
        profiles.assert_not_called()

    @patch(
        "coach_api.views.caseload_counts_by_coach",
        return_value={"coach-a@example.com": {"total": 7, "active": 5}},
    )
    def test_coach_directory_lists_coach_accounts_for_an_admin(self, _counts):
        self._make_staff_account(email="coach-a@example.com", access="coach")
        self._make_staff_account(email="designer@example.com", access="curriculum")
        self._authenticate(
            self._make_staff_account(email="admin@example.com", access="super-admin")
        )

        response = self.client.get("/coach_api/coaches")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        listed = {coach["email"]: coach for coach in payload["coaches"]}
        self.assertIn("coach-a@example.com", listed)
        self.assertNotIn("designer@example.com", listed)
        self.assertNotIn("admin@example.com", listed)
        self.assertEqual(listed["coach-a@example.com"]["caseloadCount"], 7)
        self.assertEqual(listed["coach-a@example.com"]["activeLearnerCount"], 5)
        self.assertTrue(payload["caseloadCountsAvailable"])

    def test_coach_directory_is_refused_to_a_coach_and_to_anonymous_callers(self):
        self.assertEqual(self.client.get("/coach_api/coaches").status_code, 401)

        self._authenticate(
            self._make_staff_account(email="coach-a@example.com", access="coach")
        )
        self.assertEqual(self.client.get("/coach_api/coaches").status_code, 403)

    def test_batch_transport_cannot_bypass_the_coach_authentication_boundary(self):
        response = self._request(
            "POST",
            "/coach_api/_batch/",
            {
                "requests": [
                    {"id": "caseload", "url": "/coach_api/coach/caseload"}
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["responses"][0]["status"], 401)
