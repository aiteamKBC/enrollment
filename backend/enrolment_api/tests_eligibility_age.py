"""Approved application-age policy: mocked persistence, no accounts or mail."""
import json
from contextlib import nullcontext
from datetime import date, datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.utils import timezone

from enrolment_api import eligibility_form as form, eligibility_layout as layout
from enrolment_api.tests_eligibility_form import FakeCursor, application, fake_lookup, layout_with, URL
from enrolment_api.tests_eligibility_workplace import ReceiptCursor


class ApplicationAgeTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        for name, target, attribute, kwargs in (
            ("clock", form.timezone, "now", {"return_value": datetime(2026, 11, 3, 12, tzinfo=dt_timezone.utc)}),
            ("lookup", form.postcode_lookup, "lookup", {"side_effect": fake_lookup}),
            ("published", form, "_published_layout", {"return_value": (None, None)}),
            ("invite", form.eligibility_invitation, "invite_applicant", {"return_value": "invited"}),
        ):
            active = patch.object(target, attribute, **kwargs)
            self.addCleanup(active.stop)
            setattr(self, name, active.start())
        active = patch.object(form.transaction, "atomic", side_effect=lambda **_kwargs: nullcontext())
        self.addCleanup(active.stop)
        active.start()

    def post(self, birth, *, enabled=True, cursor=None, **changes):
        payload = application(dateOfBirth=birth, startDateWithEmployer="2026-11-02", residentPrev3Years=False,
                              yearsInUk="0", workplaceArrangement="same")
        payload.update(changes)
        cursor = cursor or (ReceiptCursor() if enabled else FakeCursor())
        with override_settings(DEBUG=True, ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=enabled), \
                patch.object(form, "connections", {form.CONN: SimpleNamespace(cursor=lambda: cursor)}):
            response = form.submit_application(self.factory.post(URL, json.dumps(payload), content_type="application/json",
                                                                 HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
        return response, json.loads(response.content), cursor

    def test_age_boundaries_on_both_screening_paths_ignore_forged_client_metadata(self):
        for enabled in (False, True):
            for birth, eligible in (("2010-11-04", False), ("2010-11-03", True), ("1995-04-03", True),
                                    ("1900-01-01", True), ("2026-11-01", False)):
                with self.subTest(enabled=enabled, birth=birth):
                    self.invite.reset_mock()
                    response, data, cursor = self.post(birth, enabled=enabled, age=99, eligible=True,
                                                       assessmentStatus="provisional_eligible", country="England")
                    self.assertEqual(response.status_code, 201)
                    self.assertIs(data["eligible"], eligible)
                    self.assertEqual(data["outcome"], "invited" if eligible else "notEligible")
                    self.assertEqual(self.invite.call_count, int(eligible))
                    if not eligible:
                        self.assertIn(form.APPLICATION_AGE_REASON, data["reasons"])
                        self.assertIn("office@kentbusinesscollege.org", data["reasons"][0])
                    if enabled:
                        row = next(iter(cursor.rows.values()))
                        self.assertIs(row["eligible"], eligible)
                        self.assertEqual(row["assessment_status"], form.PROVISIONAL_ELIGIBLE if eligible else form.NOT_ELIGIBLE)
                        self.assertEqual(row["assessment_reason"], None if eligible else form.APPLICATION_AGE_REASON)

    def test_invalid_missing_and_future_dob_are_field_errors_with_no_saving_or_inviting(self):
        for enabled in (False, True):
            for birth in (None, "", "bad", "2026-02-29", "2026-04-31", "20261101", "2026-11-04"):
                with self.subTest(enabled=enabled, birth=birth):
                    self.invite.reset_mock()
                    self.lookup.reset_mock()
                    response, data, cursor = self.post(birth, enabled=enabled)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("dateOfBirth", data["fields"])
                    self.assertNotIn("eligible", data)
                    self.assertEqual(cursor.executed, [])
                    self.lookup.assert_not_called()
                    self.invite.assert_not_called()

    def test_uk_calendar_date_is_independent_of_server_and_active_timezone(self):
        # UTC July 2 is already July 3 in Britain (BST).
        self.clock.return_value = datetime(2026, 7, 2, 23, 30, tzinfo=dt_timezone.utc)
        with override_settings(TIME_ZONE="America/Los_Angeles"), timezone.override("UTC"):
            self.assertEqual(form.application_date(), date(2026, 7, 3))
            response, data, _cursor = self.post("2010-07-03", startDateWithEmployer="2026-07-01")
        self.assertEqual(response.status_code, 201)
        self.assertIs(data["eligible"], True)

    def test_leap_day_birthdays_use_the_actual_calendar_anniversary(self):
        for day, expected in ((date(2024, 2, 28), 15), (date(2024, 2, 29), 16), (date(2024, 3, 1), 16)):
            self.assertEqual(form.age_on(date(2008, 2, 29), day), expected)

    def test_old_layout_cannot_hide_omit_or_make_dob_optional(self):
        raw = layout_with(("dateOfBirth", {"hidden": True, "required": False}))
        raw["sections"][0]["hidden"] = True
        public = layout.public_layout(raw)
        personal = public["sections"][0]
        self.assertFalse(personal["hidden"])
        birth = next(item for item in personal["items"] if item["key"] == "dateOfBirth")
        self.assertFalse(birth["hidden"])
        self.assertTrue(birth["required"])
        self.assertTrue(raw["sections"][0]["hidden"])  # No stored publication rewrite.
        self.published.return_value = (None, raw)
        response, data, _cursor = self.post(None)
        self.assertEqual(response.status_code, 400)
        self.assertIn("dateOfBirth", data["fields"])
        raw["sections"][0]["items"] = [item for item in raw["sections"][0]["items"] if item["key"] != "dateOfBirth"]
        self.assertTrue(any(item["key"] == "dateOfBirth" for item in layout.public_layout(raw)["sections"][0]["items"]))

    def test_under_age_retry_preserves_one_record_and_never_calls_account_creation(self):
        cursor = ReceiptCursor()
        with patch("learner_api.models.EnrolmentUser.all_learners.create") as create_learner:
            first, data, _cursor = self.post("2010-11-04", cursor=cursor)
            second, repeated, _cursor = self.post("2010-11-04", cursor=cursor)
            self.assertEqual((first.status_code, second.status_code), (201, 201))
            self.assertEqual(repeated, data)
            self.assertEqual(len(cursor.rows), 1)
            self.invite.assert_not_called()
            create_learner.assert_not_called()

    def test_pre_policy_receipts_are_preserved_and_cannot_replay_an_under_age_pass(self):
        cursor = ReceiptCursor()
        # Reproduce the historic receipt identity for the same answers.
        with patch.object(form, "MINIMUM_APPLICATION_AGE", 0):
            _response, historic, _cursor = self.post("2010-11-04", cursor=cursor)
        original = json.dumps(cursor.rows, sort_keys=True, default=str)
        self.invite.reset_mock()
        response, current, _cursor = self.post("2010-11-04", cursor=cursor)
        self.assertEqual(response.status_code, 201)
        self.assertIs(historic["eligible"], True)
        self.assertIs(current["eligible"], False)
        self.assertEqual(len(cursor.rows), 2)
        historic_only = {key: row for key, row in cursor.rows.items() if row["eligible"]}
        self.assertEqual(json.dumps(historic_only, sort_keys=True, default=str), original)
        self.invite.assert_not_called()

    def test_turning_16_allows_a_new_screening_without_changing_under_age_history(self):
        cursor = ReceiptCursor()
        _response, previous, _cursor = self.post("2010-11-04", cursor=cursor)
        original = json.dumps(cursor.rows, sort_keys=True, default=str)
        self.clock.return_value = datetime(2026, 11, 4, 12, tzinfo=dt_timezone.utc)
        response, current, _cursor = self.post("2010-11-04", cursor=cursor)
        self.assertEqual(response.status_code, 201)
        self.assertIs(previous["eligible"], False)
        self.assertIs(current["eligible"], True)
        self.assertEqual(len(cursor.rows), 2)
        previous_only = {key: row for key, row in cursor.rows.items() if not row["eligible"]}
        self.assertEqual(json.dumps(previous_only, sort_keys=True, default=str), original)
