"""Coach error responses must never disclose exception details."""

from __future__ import annotations

import base64
import json
from datetime import date, time
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase

from coach_api.models import CoachCalendarEvent
from coach_api.views import TEAMS_SYNC_TEMPORARY_MESSAGE, coach_timetable, sync_calendar_event_to_graph
from config.batch import _execute_get


SECRET_ERROR = 'relation "private.internal_table" does not exist; token=super-secret'


class CoachErrorSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @patch("coach_api.views.logger.exception")
    @patch("coach_api.views.collect_generated_timetable", side_effect=DatabaseError(SECRET_ERROR))
    def test_database_exception_is_logged_but_not_returned(self, _load, log_exception):
        request = self.factory.get("/coach_api/coach/timetable")
        request.coach_email = "coach@example.com"
        request.request_id = "request-safe-123"

        response = unwrap(coach_timetable)(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["error"], "database_unavailable")
        self.assertEqual(payload["request_id"], "request-safe-123")
        self.assertNotIn(SECRET_ERROR, response.content.decode())
        self.assertNotIn("internal_table", response.content.decode())
        log_exception.assert_called_once()

    @patch("coach_api.views.logger.exception")
    @patch("coach_api.views.microsoft_graph_request", side_effect=RuntimeError(SECRET_ERROR))
    @patch("coach_api.views.has_graph_credentials", return_value=True)
    def test_graph_exception_becomes_safe_warning(self, _credentials, _graph, log_exception):
        record = CoachCalendarEvent(
            owner_email="coach@example.com",
            learner_email="learner@example.com",
            learner_name="Learner",
            event_type="catch-up",
            scheduled_date=date(2026, 8, 21),
            scheduled_time=time(9, 30),
            duration_minutes=30,
        )

        warning = sync_calendar_event_to_graph(record, {"source": "catch-up", "title": "Catch-up"})

        self.assertNotIn("internal_table", warning)
        self.assertNotIn("super-secret", warning)
        self.assertEqual(warning, TEAMS_SYNC_TEMPORARY_MESSAGE)
        log_exception.assert_called_once()

    @patch("config.batch.logger.exception")
    @patch("config.batch.resolve")
    def test_batch_wrapper_does_not_reintroduce_exception_leakage(self, resolve, log_exception):
        def explode(_request):
            raise RuntimeError(SECRET_ERROR)

        resolve.return_value = SimpleNamespace(func=explode, args=(), kwargs={})
        request = self.factory.get("/api/batch/")
        request.COOKIES = {}

        result = _execute_get(
            request,
            {"id": "coach-dashboard", "url": "/coach_api/coach/dashboard", "headers": {}},
        )
        body = base64.b64decode(result["body"]).decode()

        self.assertEqual(result["status"], 500)
        self.assertNotIn("internal_table", body)
        self.assertNotIn("super-secret", body)
        self.assertEqual(json.loads(body)["error"], "internal_error")
        log_exception.assert_called_once()
