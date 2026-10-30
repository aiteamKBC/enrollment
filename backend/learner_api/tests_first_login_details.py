"""First sign-in details: who is asked, what is validated, what is written.

SimpleTestCase with the database layer mocked: Django refuses any real query
from these tests, so they are safe to run against any configured database.
"""
import json
from datetime import datetime, timezone
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from learner_api import first_login_details as f

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="
DONE = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)

VALID = {
    "title": "Ms",
    "dateOfBirth": "2000-05-17",
    "phone": "07700 900123",
    "country": "United Kingdom",
    "postcode": "ct11aa",
    "addressLine1": "1 High Street",
    "addressLine2": "",
    "townCity": "Canterbury",
    "county": "Kent",
    "signature": SIGNATURE,
}


def learner(**overrides):
    values = dict(
        pk=41, username="Test Learner", learner_type="apprenticeship", programme_status="Fresh user",
        title="", date_of_birth="", phone_number="", country="", current_postcode="",
        address_line_1="", address_line_2="", address_line_3="", address_line_4="",
    )
    values.update(overrides)
    row = MagicMock(**values)
    row.pk = values["pk"]
    return row


class CleanTests(SimpleTestCase):
    def test_accepts_and_normalises_a_complete_answer(self):
        values, signature, errors = f._clean(VALID)

        self.assertEqual(errors, {})
        self.assertEqual(signature, SIGNATURE)
        self.assertEqual(values["current_postcode"], "CT1 1AA")
        self.assertEqual(values["date_of_birth"], "2000-05-17")
        self.assertEqual(values["address_line_3"], "Canterbury")
        self.assertEqual(values["address_line_4"], "Kent")

    def test_reports_every_missing_answer(self):
        _values, _signature, errors = f._clean({"country": "United Kingdom"})

        self.assertEqual(
            set(errors),
            {"title", "dateOfBirth", "phone", "postcode", "addressLine1", "townCity", "signature"},
        )

    def test_rejects_a_future_date_of_birth_and_a_bad_signature(self):
        _values, _signature, errors = f._clean({**VALID, "dateOfBirth": "2999-01-01", "signature": "not-an-image"})

        self.assertIn("dateOfBirth", errors)
        self.assertEqual(errors["signature"], "Your signature is required.")

    def test_only_checks_the_postcode_format_for_the_uk(self):
        values, _signature, errors = f._clean({**VALID, "country": "Ireland", "postcode": "D02 X285"})

        self.assertEqual(errors, {})
        self.assertEqual(values["current_postcode"], "D02 X285")

    def test_rejects_an_oversized_signature(self):
        _values, _signature, errors = f._clean({**VALID, "signature": SIGNATURE + "A" * f.MAX_SIGNATURE_CHARS})

        self.assertIn("signature", errors)


@patch("learner_api.first_login_details._has_saved_signature", return_value=False)
@patch("learner_api.first_login_details.programme_status", side_effect=lambda row: row.programme_status)
@patch("learner_api.first_login_details.connections", {"enrolment": MagicMock()})
class ViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = SimpleNamespace(role="learner", subject_id=41, is_active=True, id=1, email="x@example.test")
        auth = patch("login.permissions.authenticate_request", side_effect=lambda request: self.account)
        auth.start()
        self.addCleanup(auth.stop)

    def get(self, pk=41):
        request = self.factory.get(f"/learner_api/first-login-details/{pk}/")
        request._dont_enforce_csrf_checks = True
        return f.first_login_details(request, pk=pk)

    def post(self, body, pk=41):
        request = self.factory.post(
            f"/learner_api/first-login-details/{pk}/", data=json.dumps(body), content_type="application/json"
        )
        request._dont_enforce_csrf_checks = True
        return f.first_login_details(request, pk=pk)

    # ---- who may reach it ------------------------------------------------------

    def test_another_learner_gets_not_found(self, *_):
        with patch.object(f, "_learner") as load:
            response = self.get(pk=99)

        self.assertEqual(response.status_code, 404)
        load.assert_not_called()

    def test_staff_cannot_read_or_complete_it_for_a_learner(self, *_):
        self.account = SimpleNamespace(role="staff", subject_id=7, is_active=True, id=2, email="s@example.test")
        with patch.object(f, "_learner") as load:
            self.assertEqual(self.get().status_code, 403)
            self.assertEqual(self.post(VALID).status_code, 403)

        load.assert_not_called()

    # ---- who is asked ----------------------------------------------------------

    def required_for(self, row, completion=(True, None)):
        with patch.object(f, "_learner", return_value=row), patch.object(f, "_completion", return_value=completion):
            response = self.get()
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_a_new_apprentice_is_asked_with_their_record_prefilled(self, *_):
        data = self.required_for(learner(phone_number="07700 900999", country=""))

        self.assertTrue(data["required"])
        self.assertEqual(data["details"]["phone"], "07700 900999")
        self.assertEqual(data["details"]["country"], "United Kingdom")
        self.assertEqual(data["signatoryName"], "Test Learner")

    def test_an_unset_status_counts_as_a_new_account(self, *_):
        self.assertTrue(self.required_for(learner(programme_status=""))["required"])

    def test_nobody_else_is_asked(self, *_):
        for row in (
            learner(learner_type="commercial"),
            learner(programme_status="Onboarding"),
            learner(programme_status="Active"),
        ):
            with self.subTest(type=row.learner_type, status=row.programme_status):
                self.assertFalse(self.required_for(row)["required"])

    def test_an_apprentice_who_already_finished_is_not_asked_again(self, *_):
        from datetime import datetime, timezone

        done = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)
        self.assertFalse(self.required_for(learner(), completion=(True, done))["required"])

    def test_nobody_is_asked_until_the_column_exists(self, *_):
        # Without it completion cannot be recorded, so the learner would be
        # stopped at every sign-in.
        self.assertFalse(self.required_for(learner(), completion=(False, None))["required"])

    # ---- what is written -------------------------------------------------------

    def submit(self, row, body=VALID, completion=(True, None)):
        cursor = MagicMock()
        users = MagicMock()
        users.all_learners.select_for_update.return_value.filter.return_value.first.return_value = row
        with patch.object(f, "EnrolmentUser", users), \
                patch.object(f, "connections", {"enrolment": MagicMock(cursor=MagicMock(return_value=cursor))}), \
                patch.object(f.transaction, "atomic", lambda **_: nullcontext()), \
                patch.object(f, "_learner", return_value=row), \
                patch.object(f, "_completion", side_effect=[completion, (True, DONE)]):
            response = self.post(body)
        return response, cursor

    def test_saves_the_details_and_signature_then_moves_to_onboarding(self, *_):
        row = learner()
        response, cursor = self.submit(row)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(row.programme_status, "Onboarding")
        self.assertEqual(row.current_postcode, "CT1 1AA")
        self.assertEqual(row.title, "Ms")
        fields = row.save.call_args.kwargs["update_fields"]
        # Only the columns these screens own, plus the status move — nothing else
        # on the learner row is rewritten.
        self.assertEqual(
            set(fields),
            {"title", "country", "current_postcode", "address_line_1", "address_line_2", "address_line_3",
             "address_line_4", "phone_number", "date_of_birth", "programme_status"},
        )
        sql, params = cursor.execute.call_args.args
        self.assertIn('"Learner_signature" = %s', sql)
        self.assertIn('"First_login_details_completed_at" = now()', sql)
        self.assertEqual(params, [SIGNATURE, "Test Learner", 41])
        self.assertEqual(json.loads(response.content)["programmeStatus"], "Onboarding")

    def test_a_retried_submit_rewrites_nothing(self, *_):
        from datetime import datetime, timezone

        row = learner(programme_status="Onboarding")
        response, cursor = self.submit(row, completion=(True, datetime(2026, 9, 27, tzinfo=timezone.utc)))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["alreadyCompleted"])
        row.save.assert_not_called()
        cursor.execute.assert_not_called()

    def test_refuses_a_learner_who_is_no_longer_new(self, *_):
        row = learner(programme_status="Delivery")
        response, cursor = self.submit(row)

        self.assertEqual(response.status_code, 409)
        row.save.assert_not_called()
        cursor.execute.assert_not_called()

    def test_refuses_an_incomplete_answer_without_touching_the_record(self, *_):
        row = learner()
        response, cursor = self.submit(row, body={**VALID, "signature": ""})

        self.assertEqual(response.status_code, 400)
        self.assertIn("signature", json.loads(response.content)["fields"])
        row.save.assert_not_called()
        cursor.execute.assert_not_called()
