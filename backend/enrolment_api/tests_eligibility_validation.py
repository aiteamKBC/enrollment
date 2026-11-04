"""Date/residency consistency regressions; no persistence or external services."""
import json
from datetime import date
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from enrolment_api import eligibility_form as form
from enrolment_api.tests_eligibility_form import application, layout_with, URL

TODAY = date(2026, 11, 3)


class DateConsistencyTests(SimpleTestCase):
    def test_dates_require_exact_iso_calendar_dates_and_dob_cannot_be_future(self):
        for value in ("20261101", "2026-W44-7", "2026-2-01", "2026-02-29", "2026-04-31", "2026-13-01", "not a date", 20261101, True):
            with self.subTest(value=value):
                _row, errors = form.clean_application(application(dateOfBirth=value), today=TODAY)
                self.assertEqual(errors["dateOfBirth"], "Enter a valid date.")
        _row, errors = form.clean_application(application(dateOfBirth="2026-11-04"), today=TODAY)
        self.assertEqual(errors["dateOfBirth"], "The date cannot be in the future.")
        row, errors = form.clean_application(application(dateOfBirth="2000-02-29"), today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(row["date_of_birth"], date(2000, 2, 29))

    def test_recent_birth_is_a_valid_date_but_cannot_precede_employment_or_three_year_history(self):
        _row, errors = form.clean_application(application(dateOfBirth="2026-11-01"), today=TODAY)
        self.assertEqual(set(errors), {"startDateWithEmployer", "residentPrev3Years"})
        self.assertEqual(errors["startDateWithEmployer"], "Enter a date after your date of birth.")

    def test_under_age_is_valid_input_for_a_not_eligible_screening_not_a_date_error(self):
        row, errors = form.clean_application(application(
            dateOfBirth="2011-11-04", startDateWithEmployer="2026-12-01",
        ), today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(row["start_date_with_employer"], date(2026, 12, 1))
        self.assertNotIn("apprenticeship_start_date", row)
        _row, errors = form.clean_application(application(
            dateOfBirth="2026-11-01", startDateWithEmployer="2026-11-02", residentPrev3Years=False, yearsInUk="0",
        ), today=TODAY)
        self.assertEqual(errors, {})  # Age is a screening decision after validation.

    def test_employment_date_required_calendar_valid_and_future_arrangements_allowed(self):
        for value, message in ((None, form.REQUIRED), ("", form.REQUIRED), ("2026-02-30", "Enter a valid date.")):
            _row, errors = form.clean_application(application(startDateWithEmployer=value), today=TODAY)
            self.assertEqual(errors, {"startDateWithEmployer": message})
        _row, errors = form.clean_application(application(startDateWithEmployer="2027-02-28"), today=TODAY)
        self.assertEqual(errors, {})

    def test_all_collected_event_dates_must_be_after_birth(self):
        for field, extra in (("startDateWithEmployer", {}), ("expectedLeavingDate", {"fullTimeEducationPrior": True}),
                             ("fundedTrainingCompletedOn", {"attendedFundedTraining": True})):
            for value in ("1990-01-01", "1995-04-03"):
                with self.subTest(field=field, value=value):
                    _row, errors = form.clean_application(application(**extra, **{field: value}), today=TODAY)
                    self.assertEqual(errors, {field: "Enter a date after your date of birth."})

    def test_three_year_residency_history_uses_calendar_age_boundary(self):
        for birth, invalid in (("2023-11-04", True), ("2023-11-03", False)):
            _row, errors = form.clean_application(application(dateOfBirth=birth, startDateWithEmployer="2026-11-02"), today=TODAY)
            self.assertEqual("residentPrev3Years" in errors, invalid)

    def test_full_years_residence_cannot_exceed_lifetime_but_is_not_a_funding_test(self):
        for birth, years, invalid in (("2017-11-04", "9", True), ("2017-11-03", "9", False),
                                      ("2016-11-04", "10 or more", True), ("2016-11-03", "10 or more", False)):
            _row, errors = form.clean_application(application(dateOfBirth=birth, startDateWithEmployer="2026-11-02",
                                                               residentPrev3Years=False, yearsInUk=years), today=TODAY)
            self.assertEqual("yearsInUk" in errors, invalid)
        # Historical total residence >=3 does not prove continuous ordinary residence.
        _row, errors = form.clean_application(application(residentPrev3Years=False, yearsInUk="10 or more"), today=TODAY)
        self.assertEqual(errors, {})

    def test_dob_is_required_despite_old_layouts_and_stale_follow_ups_are_ignored(self):
        row, errors = form.clean_application(application(dateOfBirth="bad", yearsInUk="9", expectedLeavingDate="1900-01-01"),
                                             today=TODAY, layout=layout_with(("dateOfBirth", {"hidden": True})))
        self.assertEqual(errors, {"dateOfBirth": "Enter a valid date."})
        for field in ("years_in_uk", "expected_leaving_date"):
            self.assertIsNone(row[field])

    def test_nationality_residence_and_immigration_answers_do_not_imply_each_other(self):
        _row, errors = form.clean_application(application(countryOfResidence="France", nationality="France", livesInUk=True,
                                                        residencyStatus="UK Citizen", residenceNotForEducation=False), today=TODAY)
        self.assertEqual(errors, {})  # Dual nationality and residency exceptions need evidence.

    def test_direct_api_cannot_bypass_invalid_dates_or_chronology(self):
        for enabled in (False, True):
            for changes, field in (({"dateOfBirth": "2026-02-30"}, "dateOfBirth"),
                                   ({"dateOfBirth": "2026-11-04"}, "dateOfBirth"),
                                   ({"startDateWithEmployer": "1990-01-01"}, "startDateWithEmployer"),
                                   ({"dateOfBirth": "2026-11-01", "residentPrev3Years": True}, "residentPrev3Years")):
                with self.subTest(enabled=enabled, changes=changes), override_settings(DEBUG=True, ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=enabled), \
                        patch.object(form, "_published_layout", return_value=(None, None)), \
                        patch.object(form, "connections") as persistence, \
                        patch.object(form.postcode_lookup, "lookup") as lookup, \
                        patch.object(form.eligibility_invitation, "invite_applicant") as invite:
                    request = RequestFactory().post(URL, json.dumps(application(workplaceArrangement="same", **changes)),
                                                    content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                    with patch.object(form.timezone, "localdate", return_value=TODAY):
                        response = form.submit_application(request)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, json.loads(response.content)["fields"])
                    persistence.__getitem__.assert_not_called()
                    lookup.assert_not_called()
                    invite.assert_not_called()
