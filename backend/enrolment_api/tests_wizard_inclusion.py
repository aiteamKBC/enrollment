"""The wizard's Inclusion step: the learner counts as done once the Safeguarding
database holds an inclusion response for one of their emails.

SimpleTestCase: the learner lookups and the Safeguarding connection are faked,
so no query reaches either database.
"""
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
from django.test import RequestFactory, SimpleTestCase

from coach_api.support_tickets import SupportTicketsUnavailable
from enrolment_api import wizard_inclusion as wi
from enrolment_api import wizard_layout as wl

URL = "/enrolment_api/wizard/apprenticeship/41/inclusion/"


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def queryset(first=None, exists=False, emails=()):
    qs = MagicMock()
    qs.first.return_value = first
    qs.exists.return_value = exists
    qs.values_list.return_value = list(emails)
    return qs


class InclusionStatusTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        for p in (
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.object(wi.EnrolmentUser.objects, "filter",
                         return_value=queryset(first=SimpleNamespace(pk=41, email=" Sam.Example@Employer.Example "))),
            patch.object(wi.LoginAccount.objects, "filter",
                         return_value=queryset(emails=["sam.example@employer.example", "sam@personal.example"])),
        ):
            p.start()
            self.addCleanup(p.stop)

    def get(self, kind="apprenticeship", learner_id=41, row=None, connect=None):
        cursor = FakeCursor(row if row is not None else {"responses": 0, "latest": None})
        connect = connect or (lambda: FakeConnection(cursor))
        with patch.object(wi, "_connect", side_effect=connect) as opened:
            response = wi.wizard_inclusion(self.factory.get(URL), kind=kind, learner_id=learner_id)
        return response, cursor, opened

    def test_any_response_for_one_of_the_learners_emails_completes_the_step(self):
        latest = datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc)
        response, cursor, _ = self.get(row={"responses": 2, "latest": latest})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {
            "applicable": True, "completed": True, "respondedAt": "2026-10-15T09:30:00+00:00",
        })
        self.assertEqual(response["Cache-Control"], "private, no-store")
        sql, params = cursor.executed[0]
        # Matched on email, trimmed and lower-cased on both sides, never on id.
        self.assertIn("from public.learner_inclusiveness_quiz_responses", sql)
        self.assertIn("where lower(trim(learner_email)) = any(%s)", sql)
        self.assertNotIn("learner_id", sql)
        self.assertEqual(params, [["sam.example@employer.example", "sam@personal.example"]])

    def test_no_response_yet_is_not_complete(self):
        response, _cursor, _ = self.get()
        self.assertEqual(json.loads(response.content), {"applicable": True, "completed": False, "respondedAt": None})

    def test_a_learner_without_an_email_is_not_complete_and_nothing_is_queried(self):
        with patch.object(wi.EnrolmentUser.objects, "filter", return_value=queryset(first=SimpleNamespace(pk=41, email=None))), \
                patch.object(wi.LoginAccount.objects, "filter", return_value=queryset()):
            response, _cursor, opened = self.get()
        self.assertEqual(json.loads(response.content)["completed"], False)
        opened.assert_not_called()

    def test_commercial_learners_do_not_take_the_step(self):
        with patch.object(wi.CommercialUser.objects, "filter", return_value=queryset(exists=True)):
            response, _cursor, opened = self.get(kind="commercial")
        self.assertEqual(json.loads(response.content), {"applicable": False})
        opened.assert_not_called()

    def test_unknown_learner_and_kind(self):
        with patch.object(wi.EnrolmentUser.objects, "filter", return_value=queryset(first=None)):
            self.assertEqual(self.get()[0].status_code, 404)
        with patch.object(wi.CommercialUser.objects, "filter", return_value=queryset(exists=False)):
            self.assertEqual(self.get(kind="commercial")[0].status_code, 404)
        self.assertEqual(self.get(kind="other")[0].status_code, 400)

    def test_an_unreachable_safeguarding_database_is_a_503_without_details(self):
        for failure in (SupportTicketsUnavailable("SafeGuarding_database_url is not configured."),
                        psycopg.OperationalError("password authentication failed for user x")):
            def boom(failure=failure):
                raise failure
            with self.assertLogs("enrolment_api.wizard_inclusion", level="WARNING"):
                response, _cursor, _ = self.get(connect=boom)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("password", response.content.decode())
            self.assertNotIn("SafeGuarding_database_url", response.content.decode())


class InclusionAccessTests(SimpleTestCase):
    """A learner reads only their own status; staff need enrolment access."""

    def setUp(self):
        self.factory = RequestFactory()
        p = patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"})
        p.start()
        self.addCleanup(p.stop)

    def request(self, role, subject_id, subject_type=None):
        request = self.factory.get(URL)
        request.user = SimpleNamespace(is_authenticated=False)
        if role:
            request.login_account = SimpleNamespace(is_active=True, role=role, subject_id=subject_id,
                                                    subject_type=subject_type or role)
        return request

    def call(self, request):
        with patch.object(wi, "response_summary", return_value=(True, None)) as summary, \
                patch.object(wi.EnrolmentUser.objects, "filter", return_value=queryset(first=SimpleNamespace(pk=41, email="a@b.example"))), \
                patch.object(wi.LoginAccount.objects, "filter", return_value=queryset()):
            return wi.wizard_inclusion(request, kind="apprenticeship", learner_id=41), summary

    def test_the_learner_reads_their_own_status(self):
        response, _ = self.call(self.request("learner", 41))
        self.assertEqual(response.status_code, 200)

    def test_another_learner_employer_or_anonymous_caller_is_refused(self):
        for request, status in (
            (self.request("learner", 42), 404),
            (self.request("employer", 7), 404),
            (self.request(None, None), 401),
        ):
            response, summary = self.call(request)
            self.assertEqual(response.status_code, status)
            summary.assert_not_called()

    def test_staff_need_enrolment_access(self):
        with patch("enrolment_api.auth._accesses_of", return_value=frozenset({"enrolment"})):
            self.assertEqual(self.call(self.request("staff", 3))[0].status_code, 200)
        with patch("enrolment_api.auth._accesses_of", return_value=frozenset({"coach"})):
            self.assertEqual(self.call(self.request("staff", 3))[0].status_code, 404)


class InclusionStepLayoutTests(SimpleTestCase):
    def test_the_builder_keeps_the_inclusion_step_as_built_in(self):
        layout, _plan = wl.normalize_layout({"steps": [
            {"slug": "skills-radar", "builtin": True, "items": []},
            {"slug": "inclusion", "builtin": True, "label": "Inclusion", "items": [{"key": "block.inclusion", "builtin": True}]},
            {"slug": "policies", "builtin": True, "items": []},
        ]}, None)
        step = layout["steps"][1]
        self.assertEqual((step["slug"], step["builtin"], step["label"]), ("inclusion", True, "Inclusion"))
        self.assertNotIn("table", step)
