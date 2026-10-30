"""PostgreSQL integration tests for Coach calendar booking consistency."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError, close_old_connections, connections
from django.test import Client, TransactionTestCase

from learner_api.models import StaffUser
from login.identity import ensure_account
from login.models import LoginAccount, LoginSession
from login.sessions import COOKIE_NAME, issue_session

from .models import CoachCalendarEvent, CoachCalendarSequence


class CoachCalendarRaceTests(TransactionTestCase):
    databases = {"default", "enrolment"}
    # Avoid Django's PostgreSQL TRUNCATE list pulling in the unrelated legacy
    # chat -> auth_user FK. This suite cleans its unmanaged login/staff rows.
    available_apps = ["coach_api"]
    reset_sequences = True

    def setUp(self):
        self.coach_email = f"coach-{uuid.uuid4().hex[:8]}@kbc.invalid"
        # The fresh unmanaged-table test provisioner predates Staff_users.uuid;
        # insert only the columns this auth integration needs.
        with connections["enrolment"].cursor() as cursor:
            cursor.execute(
                'INSERT INTO enrolment."Staff_users" '
                '("Username", "Email", "Position", "Access", "Type", " Status") '
                'VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                ["Calendar race coach", self.coach_email, "Caseowner", "coach", "Admin", "FullUser"],
            )
            staff_id = cursor.fetchone()[0]
        self.staff = SimpleNamespace(
            id=staff_id,
            username="Calendar race coach",
            email=self.coach_email,
            preferred_name="",
            position="Caseowner",
            access="coach",
        )
        self.account, _ = ensure_account("staff", self.staff.id, subject=self.staff)
        self.learner = SimpleNamespace(
            id=int(uuid.uuid4().hex[:7], 16),
            username="Calendar Learner",
            email="calendar-learner@kbc.invalid",
            coach_name="Calendar race coach",
        )

    def tearDown(self):
        CoachCalendarEvent.objects.filter(learner_id=self.learner.id).delete()
        CoachCalendarSequence.objects.filter(learner_id=self.learner.id).delete()
        LoginSession.objects.filter(account_id=self.account.id).delete()
        LoginAccount.objects.filter(id=self.account.id).delete()
        StaffUser.objects.filter(id=self.staff.id).delete()
        super().tearDown()

    def _client(self) -> tuple[Client, str]:
        client = Client(enforce_csrf_checks=True, SERVER_NAME="localhost")
        token, _session, _ttl = issue_session(self.account)
        client.cookies[COOKIE_NAME] = token
        csrf_response = client.get("/coach_api/csrf")
        self.assertEqual(csrf_response.status_code, 200)
        return client, csrf_response.json()["csrfToken"]

    def _book(
        self,
        client: Client,
        csrf_token: str,
        idempotency_key: str,
        *,
        scheduled_date: str = "2099-01-01",
        scheduled_time: str = "10:00",
        session_type: str = "catch-up",
        duration_minutes: int = 60,
    ):
        return client.post(
            "/coach_api/coach/timetable/events/book",
            data=json.dumps(
                {
                    "learnerId": self.learner.id,
                    "sessionType": session_type,
                    "scheduledDate": scheduled_date,
                    "scheduledTime": scheduled_time,
                    "durationMinutes": duration_minutes,
                }
            ),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=idempotency_key,
        )

    @staticmethod
    def _graph_success(record, _base_event):
        record.graph_event_id = f"graph-{record.operation_id}"
        record.graph_organizer_email = record.owner_email
        record.meeting_provider = "Microsoft Teams"
        record.meeting_link = "https://teams.microsoft.com/l/meetup-join/test"
        record.graph_web_link = "https://outlook.office.com/calendar/item/test"
        return ""

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_duplicate_idempotency_key_returns_one_event_and_one_graph_call(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()
        key = str(uuid.uuid4())

        first = self._book(client, csrf_token, key)
        second = self._book(client, csrf_token, key)

        self.assertEqual(first.status_code, 201)
        self.assertIn(second.status_code, {200, 201})
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(),
            1,
        )
        self.assertEqual(sync_graph.call_count, 1)
        self.assertEqual(first.json()["event"]["eventKey"], second.json()["event"]["eventKey"])

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_concurrent_duplicate_requests_use_separate_connections_and_create_once(
        self, sync_graph, caseload, _conflicts
    ):
        arrival = threading.Barrier(2)

        def caseload_at_barrier(_owner_email):
            arrival.wait(timeout=10)
            return [self.learner]

        caseload.side_effect = caseload_at_barrier
        sync_graph.side_effect = self._graph_success
        key = str(uuid.uuid4())
        clients = [self._client(), self._client()]

        def worker(pair):
            close_old_connections()
            try:
                return self._book(pair[0], pair[1], key)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(worker, clients))

        self.assertTrue(all(response.status_code in {200, 201} for response in responses))
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 1
        )
        self.assertEqual(sync_graph.call_count, 1)
        self.assertEqual(
            {response.json()["event"]["eventKey"] for response in responses},
            {CoachCalendarEvent.objects.get(owner_email=self.coach_email).event_key},
        )

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_concurrent_overlapping_different_session_types_create_only_one_booking(
        self, sync_graph, caseload, _conflicts
    ):
        arrival = threading.Barrier(2)

        def caseload_at_barrier(_owner_email):
            arrival.wait(timeout=10)
            return [self.learner]

        caseload.side_effect = caseload_at_barrier
        sync_graph.side_effect = self._graph_success
        clients = [self._client(), self._client()]
        keys = [str(uuid.uuid4()), str(uuid.uuid4())]

        def worker(args):
            index, pair = args
            close_old_connections()
            try:
                return self._book(
                    pair[0],
                    pair[1],
                    keys[index],
                    session_type=("catch-up", "student-support")[index],
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(worker, enumerate(clients)))

        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        records = list(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).order_by("sequence")
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(sync_graph.call_count, 1)

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_different_idempotency_keys_allow_adjacent_non_overlapping_bookings(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()

        first = self._book(client, csrf_token, str(uuid.uuid4()))
        second = self._book(
            client,
            csrf_token,
            str(uuid.uuid4()),
            scheduled_time="11:00",
            session_type="student-support",
        )

        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 2
        )
        self.assertEqual(sync_graph.call_count, 2)

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_partially_overlapping_booking_is_rejected(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()

        first = self._book(client, csrf_token, str(uuid.uuid4()))
        second = self._book(
            client,
            csrf_token,
            str(uuid.uuid4()),
            scheduled_time="10:30",
            session_type="student-support",
            duration_minutes=30,
        )

        self.assertEqual((first.status_code, second.status_code), (201, 409))
        self.assertIn("Would you like to reschedule", second.json()["message"])
        self.assertEqual(
            CoachCalendarEvent.objects.filter(learner_id=self.learner.id).count(), 1
        )
        self.assertEqual(sync_graph.call_count, 1)

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_same_session_on_same_day_offers_reschedule_instead_of_second_booking(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()

        first = self._book(client, csrf_token, str(uuid.uuid4()))
        second = self._book(
            client,
            csrf_token,
            str(uuid.uuid4()),
            scheduled_time="14:00",
        )

        self.assertEqual((first.status_code, second.status_code), (201, 409))
        self.assertIn("reschedule it instead", second.json()["message"])
        self.assertIn("Thursday, 1 January 2099 at 10:00", second.json()["message"])
        self.assertEqual(
            CoachCalendarEvent.objects.filter(learner_id=self.learner.id).count(), 1
        )

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_same_session_on_another_day_of_week_offers_reschedule(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()

        first = self._book(client, csrf_token, str(uuid.uuid4()))
        second = self._book(
            client,
            csrf_token,
            str(uuid.uuid4()),
            scheduled_date="2099-01-02",
            scheduled_time="14:00",
        )

        self.assertEqual((first.status_code, second.status_code), (201, 409))
        self.assertIn("reschedule it instead", second.json()["message"])
        self.assertIn("Thursday, 1 January 2099 at 10:00", second.json()["message"])

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_same_session_in_a_different_week_is_allowed(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()

        first = self._book(client, csrf_token, str(uuid.uuid4()))
        second = self._book(
            client,
            csrf_token,
            str(uuid.uuid4()),
            scheduled_date="2099-01-08",
        )

        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(
            CoachCalendarEvent.objects.filter(learner_id=self.learner.id).count(), 2
        )

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_booking_with_another_coach_still_blocks_the_same_learner_slot(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        CoachCalendarEvent.objects.create(
            event_key=f"other-coach-{uuid.uuid4().hex}",
            owner_email="other-coach@kbc.invalid",
            owner_name="Other Coach",
            learner_id=self.learner.id,
            learner_name=self.learner.username,
            learner_email=self.learner.email,
            event_type="student-support",
            sequence=1,
            target_date="2099-01-01",
            scheduled_date="2099-01-01",
            scheduled_time="10:15",
            duration_minutes=30,
            status=CoachCalendarEvent.STATUS_SCHEDULED,
        )
        client, csrf_token = self._client()

        response = self._book(client, csrf_token, str(uuid.uuid4()))

        self.assertEqual(response.status_code, 409)
        self.assertIn("Would you like to reschedule", response.json()["message"])
        self.assertEqual(
            CoachCalendarEvent.objects.filter(learner_id=self.learner.id).count(), 1
        )
        sync_graph.assert_not_called()

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph", return_value="Graph unavailable")
    def test_graph_failure_leaves_one_recoverable_failed_operation(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        client, csrf_token = self._client()
        key = str(uuid.uuid4())

        response = self._book(client, csrf_token, key)

        self.assertEqual(response.status_code, 201)
        record = CoachCalendarEvent.objects.get(idempotency_key=key)
        self.assertEqual(record.sync_state, CoachCalendarEvent.SYNC_FAILED)
        self.assertEqual(record.sync_attempt_count, 1)
        self.assertTrue(record.last_sync_attempt_at)
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 1
        )
        sync_graph.assert_called_once()

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.has_graph_credentials", return_value=True)
    @patch("coach_api.views.microsoft_graph_request")
    def test_ambiguous_graph_timeout_retries_with_same_graph_transaction_id(
        self, graph_request, _credentials, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        posts = []

        def graph(method, _path, *, payload=None):
            """Stand in for Graph: the first POST times out, the retry succeeds.

            A fixed ``side_effect`` list used to sit here, which tied the test to
            the exact number of calls the sync happens to make. The sync now
            reads back the event and probes the Teams meeting behind it, so the
            list ran dry mid-booking and a ``StopIteration`` surfaced as a 500 --
            a failure of the fixture, not of the retry this test is about. Any
            read answers with nothing; only the POSTs are the subject here.
            """
            if method != "POST":
                return {}
            posts.append(payload)
            if len(posts) == 1:
                raise RuntimeError("Microsoft Graph POST timed out")
            return {
                "id": "graph-after-timeout",
                "webLink": "https://outlook.office.com/calendar/item/test",
                "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/test"},
            }

        graph_request.side_effect = graph
        client, csrf_token = self._client()
        key = str(uuid.uuid4())

        first = self._book(client, csrf_token, key)
        second = self._book(client, csrf_token, key)

        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 1
        )
        # The retry is a retry of the same booking, not a second one: Graph is
        # asked to create the event twice, both times under one transaction id,
        # which is what stops the timeout leaving a duplicate in the calendar.
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["transactionId"], posts[1]["transactionId"])
        record = CoachCalendarEvent.objects.get(idempotency_key=key)
        self.assertEqual(record.sync_state, CoachCalendarEvent.SYNC_SYNCED)
        self.assertEqual(record.sync_attempt_count, 2)

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    @patch("coach_api.views.CoachCalendarEvent.objects.create", side_effect=DatabaseError("save failed"))
    def test_database_reservation_failure_never_calls_graph(
        self, _create, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        client, csrf_token = self._client()

        response = self._book(client, csrf_token, str(uuid.uuid4()))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 0
        )
        sync_graph.assert_not_called()

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.delete_calendar_event_from_graph", return_value="")
    @patch("coach_api.views.finalize_calendar_graph_sync", side_effect=DatabaseError("finalize failed"))
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_graph_success_then_finalization_failure_compensates_external_event(
        self, sync_graph, _finalize, delete_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()
        key = str(uuid.uuid4())

        response = self._book(client, csrf_token, key)

        self.assertEqual(response.status_code, 500)
        delete_graph.assert_called_once()
        record = CoachCalendarEvent.objects.get(idempotency_key=key)
        self.assertEqual(record.sync_state, CoachCalendarEvent.SYNC_FAILED)
        self.assertEqual(record.graph_event_id, "")

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.delete_calendar_event_from_graph", return_value="Graph delete failed")
    @patch("coach_api.views.finalize_calendar_graph_sync", side_effect=DatabaseError("finalize failed"))
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_compensation_failure_keeps_external_id_for_reconciliation(
        self, sync_graph, _finalize, delete_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()
        key = str(uuid.uuid4())

        response = self._book(client, csrf_token, key)

        self.assertEqual(response.status_code, 500)
        delete_graph.assert_called_once()
        record = CoachCalendarEvent.objects.get(idempotency_key=key)
        self.assertEqual(record.sync_state, CoachCalendarEvent.SYNC_RECONCILIATION)
        self.assertTrue(record.graph_event_id)

    @patch("coach_api.views.coach_learner_personal_calendar_conflicts", return_value=[])
    @patch("coach_api.views.fetch_caseload_learner_profiles")
    @patch("coach_api.views.sync_calendar_event_to_graph")
    def test_same_idempotency_key_with_different_payload_is_conflict_without_graph_retry(
        self, sync_graph, caseload, _conflicts
    ):
        caseload.return_value = [self.learner]
        sync_graph.side_effect = self._graph_success
        client, csrf_token = self._client()
        key = str(uuid.uuid4())
        first = self._book(client, csrf_token, key)
        changed_payload = {
            "learnerId": self.learner.id,
            "sessionType": "catch-up",
            "scheduledDate": "2099-01-01",
            "scheduledTime": "11:00",
            "durationMinutes": 60,
        }

        second = client.post(
            "/coach_api/coach/timetable/events/book",
            data=json.dumps(changed_payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_IDEMPOTENCY_KEY=key,
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(sync_graph.call_count, 1)
        self.assertEqual(
            CoachCalendarEvent.objects.filter(owner_email=self.coach_email).count(), 1
        )

    def test_database_constraint_rejects_duplicate_sequence_in_same_scope(self):
        common = {
            "owner_email": self.coach_email,
            "learner_id": self.learner.id,
            "learner_name": self.learner.username,
            "learner_email": self.learner.email,
            "event_type": "catch-up",
            "sequence": 1,
            "target_date": "2099-01-01",
        }
        prefix = uuid.uuid4().hex
        CoachCalendarEvent.objects.create(event_key=f"constraint-one-{prefix}", **common)
        with self.assertRaises(IntegrityError):
            CoachCalendarEvent.objects.create(event_key=f"constraint-two-{prefix}", **common)
