"""Creating a learner writes exactly one record, and only after the checks pass.

The POST used to call ``_create_enrolment_user`` twice: once before the first
session's slot was validated, and again after. Two things followed from that,
and both are what these tests pin down.

* The learner was created by the first call, so the second hit the duplicate
  email guard inside ``_create_enrolment_user`` and raised. Nothing caught it
  there, so the browser got a 500 with an HTML error page for a learner that
  had in fact been created -- "Could not create user" next to a new row in the
  directory.
* The slot checks sat between the two calls, so a weekend date or a missing
  case owner was refused *after* the learner already existed. The comment above
  those checks says they run before anything is written; they did not.

``SimpleTestCase``: the write itself is patched, so nothing here touches the
database. What is being asserted is the view's ordering and error handling, not
what creation does.
"""
import json
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from . import views
from .mappers import ValidationError


def body(response):
    return json.loads(response.content.decode("utf-8"))


class CreateWritesOnceTests(SimpleTestCase):
    def post(self, payload, *, fails_with=None):
        """Run the create endpoint, counting writes. Returns (response, writes)."""
        writes = []

        def fake_create(request, fields, **kwargs):
            writes.append(dict(fields))
            if fails_with is not None:
                raise fails_with
            return {"id": "1", "name": fields.get("username", "")}

        request = RequestFactory().post(
            "/learner_api/enrolment-users/", json.dumps(payload),
            content_type="application/json",
        )
        with patch.object(views, "_create_enrolment_user", side_effect=fake_create), \
             patch.dict("os.environ", {"LEARNER_API_REQUIRE_AUTH": "0"}):
            return views.enrolment_users(request), writes

    def test_a_learner_is_created_exactly_once(self):
        """Twice meant the second call tripped the first call's own email guard."""
        response, writes = self.post({"username": "A", "email": "a@kbc.test"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(writes), 1)

    def test_a_duplicate_email_is_refused_rather_than_crashing(self):
        """The guard raises ValidationError, which is a 400 the form can show --
        not the 500 and HTML page the browser used to get."""
        response, _ = self.post(
            {"username": "A", "email": "taken@kbc.test"},
            fails_with=ValidationError("A learner with this email already exists."),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("already exists", body(response)["error"])

    def test_a_slot_on_a_weekend_is_refused_before_anything_is_written(self):
        """2026-09-26 is a Saturday. The learner must not exist afterwards."""
        response, writes = self.post({
            "username": "A", "email": "a@kbc.test",
            "firstSessionDate": "2026-09-26", "firstSessionTime": "10:00",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(writes, [])

    def test_a_session_without_a_case_owner_is_refused_before_the_write(self):
        """There is nobody to meet, so the learner is not created either."""
        response, writes = self.post({
            "username": "A", "email": "a@kbc.test",
            "firstSessionDate": "2026-09-24", "firstSessionTime": "10:00",
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(writes, [])

    def test_the_booked_slot_reaches_the_write(self):
        """The surviving call is the one that carries the session, so booking
        still happens on the create that succeeds."""
        with patch.object(views, "validate_slot", return_value=None):
            response, writes = self.post({
                "username": "A", "email": "a@kbc.test", "caseOwner": "Omar Badr",
                "firstSessionDate": "2026-09-24", "firstSessionTime": "10:00",
            })

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(writes), 1)
