"""The public apprenticeship eligibility form: validation (against the standard
form and a published layout), the postcode check, the insert, and that the
public endpoints are reachable without a session while nothing else is opened.

SimpleTestCase with a recording fake cursor and a stubbed postcode lookup: no
query reaches a database and no request leaves the machine.
"""
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase

from enrolment_api import eligibility_form as ef
from enrolment_api import postcode_lookup
from login.api_gate import rule_for

URL = "/enrolment_api/public/apprenticeship-application/"
TODAY = date(2026, 10, 14)

#: What the stubbed lookup knows; any other well-formed postcode does not exist.
PLACES = {
    "EX12MP": {"postcode": "EX1 2MP", "country": "England", "region": "South West", "district": "Exeter"},
    "EX34MP": {"postcode": "EX3 4MP", "country": "England", "region": "South West", "district": "East Devon"},
    "EH11YZ": {"postcode": "EH1 1YZ", "country": "Scotland", "region": "", "district": "City of Edinburgh"},
}


def fake_lookup(postcode):
    return PLACES.get(postcode_lookup.compact(postcode))


def stub_lookup(test, lookup=fake_lookup):
    p = patch.object(postcode_lookup, "lookup", side_effect=lookup)
    test.addCleanup(p.stop)
    return p.start()


def layout_with(*changes, extra=()):
    """The standard form as published, with `changes` applied to built-in items by key."""
    from enrolment_api.eligibility_layout import BUILTIN_SECTIONS

    sections = [
        {"slug": slug, "title": title, "builtin": True, "hidden": False,
         "items": [{"key": key, "builtin": True, "hidden": False} for key in keys]}
        for slug, (title, keys) in BUILTIN_SECTIONS.items()
    ]
    for key, patch_ in changes:
        for section in sections:
            for item in section["items"]:
                if item["key"] == key:
                    item.update(patch_)
    sections[-1]["items"].extend(extra)
    return {"sections": sections}


def question(key, type_, **extra):
    return {"key": key, "builtin": False, "hidden": False, "required": False, "label": key, "type": type_,
            "column": f"custom_{key[3:]}", **extra}


class FakeCursor:
    def __init__(self, fail=False):
        self.executed = []
        self.fail = fail

    def execute(self, sql, params=None):
        if self.fail:
            raise DatabaseError('relation "enrolment.eligibility_form" does not exist')
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (41,)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def application(**overrides):
    body = {
        "firstName": "Sam",
        "surname": "Example",
        "workEmail": "sam@employer.example",
        "phoneNumber": "",
        "fullAddress": "1 Example Street, Exampletown",
        "postcode": "EX1 2MP",
        "dateOfBirth": "1995-04-03",
        "employmentStatus": "In paid employment",
        "majorityHoursInEngland": True,
        "employerName": "Example Ltd",
        "employerAddress": "2 Business Park, Exampletown",
        "employerPostcode": "EX3 4MP",
        "representativeName": "Alex Manager",
        "representativeEmail": "alex@employer.example",
        "livesInUk": True,
        "residenceNotForEducation": True,
        "residencyStatus": "Pre-settled Status",
        "programme": "Associate Project Manager Level 4",
        "attendedFundedTraining": False,
        "fundedTrainingCompletedOn": None,
        "notPreviouslyCompletedProgramme": True,
    }
    body.update(overrides)
    return body


class CleanApplicationTests(SimpleTestCase):
    def test_a_complete_application_maps_every_answer_to_its_column(self):
        row, errors = ef.clean_application(application(), today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(set(row), set(ef.COLUMNS))
        self.assertEqual(row["first_name"], "Sam")
        self.assertEqual(row["date_of_birth"], date(1995, 4, 3))
        self.assertIs(row["majority_hours_in_england"], True)
        self.assertEqual(row["residency_status"], "Pre-settled Status")
        # The optional phone number, left blank, is stored as NULL, not "".
        self.assertIsNone(row["phone_number"])

    def test_every_required_answer_is_reported_when_missing(self):
        row, errors = ef.clean_application({}, today=TODAY)
        required = {key for key, _col, _kind, req, _lim in ef.FIELDS if req}
        self.assertEqual(set(errors), required)
        self.assertNotIn("phoneNumber", errors)
        self.assertNotIn("fundedTrainingCompletedOn", errors)

    def test_completion_date_is_required_only_after_yes(self):
        _row, errors = ef.clean_application(application(attendedFundedTraining=True), today=TODAY)
        self.assertEqual(errors, {"fundedTrainingCompletedOn": ef.REQUIRED})

        row, errors = ef.clean_application(
            application(attendedFundedTraining=True, fundedTrainingCompletedOn="2026-03-01"), today=TODAY,
        )
        self.assertEqual(errors, {})
        self.assertEqual(row["funded_training_completed_on"], date(2026, 3, 1))

    def test_a_completion_date_left_over_from_a_changed_answer_is_not_stored(self):
        row, errors = ef.clean_application(
            application(attendedFundedTraining=False, fundedTrainingCompletedOn="not a date"), today=TODAY,
        )
        self.assertEqual(errors, {})
        self.assertIsNone(row["funded_training_completed_on"])

    def test_answers_outside_the_form_are_refused(self):
        _row, errors = ef.clean_application(application(
            employmentStatus="Retired",
            programme="Some Other Programme",
            livesInUk="yes",
            workEmail="not-an-email",
            dateOfBirth="2030-01-01",
            firstName="x" * 101,
        ), today=TODAY)
        self.assertEqual(set(errors), {
            "employmentStatus", "programme", "livesInUk", "workEmail", "dateOfBirth", "firstName",
        })
        self.assertEqual(errors["dateOfBirth"], "The date cannot be in the future.")

    def test_no_eligibility_rule_is_applied(self):
        # Collect only: staff assess eligibility, so a "No" or a young applicant is stored as given.
        _row, errors = ef.clean_application(application(
            livesInUk=False, majorityHoursInEngland=False, notPreviouslyCompletedProgramme=False,
            dateOfBirth="2015-01-01",
        ), today=TODAY)
        self.assertEqual(errors, {})

    def test_single_line_answers_are_trimmed(self):
        row, _errors = ef.clean_application(application(firstName="  Sam \n ", postcode=" EX1   2MP "), today=TODAY)
        self.assertEqual(row["first_name"], "Sam")
        self.assertEqual(row["postcode"], "EX1 2MP")

    def test_a_malformed_postcode_is_not_a_form_error(self):
        # It makes the applicant not eligible on submit instead (see SubmitApplicationTests).
        row, errors = ef.clean_application(application(postcode="ss", employerPostcode="EX3 4MP"), today=TODAY)
        self.assertEqual(errors, {})
        self.assertEqual(row["postcode"], "ss")


class PublishedLayoutTests(SimpleTestCase):
    """What a layout published from the builder changes about what is accepted."""

    def test_a_removed_question_is_neither_required_nor_stored(self):
        layout = layout_with(("dateOfBirth", {"hidden": True}), ("phoneNumber", {"hidden": True}))
        row, errors = ef.clean_application(application(dateOfBirth="", phoneNumber="07000 000000"), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertIsNone(row["date_of_birth"])
        self.assertIsNone(row["phone_number"])

    def test_a_hidden_section_asks_none_of_its_questions(self):
        layout = layout_with()
        layout["sections"][2]["hidden"] = True  # Residency
        row, errors = ef.clean_application(application(livesInUk=None, residencyStatus=""), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertIsNone(row["lives_in_uk"])

    def test_required_can_be_switched_either_way(self):
        layout = layout_with(("fullAddress", {"required": False}), ("phoneNumber", {"required": True}))
        _row, errors = ef.clean_application(application(fullAddress="", phoneNumber=""), today=TODAY, layout=layout)
        self.assertEqual(errors, {"phoneNumber": ef.REQUIRED})

    def test_the_completion_date_can_be_made_optional(self):
        layout = layout_with(("fundedTrainingCompletedOn", {"required": False}))
        _row, errors = ef.clean_application(application(attendedFundedTraining=True), today=TODAY, layout=layout)
        self.assertEqual(errors, {})

    def test_the_questions_that_identify_the_applicant_are_always_asked(self):
        layout = layout_with(("firstName", {"hidden": True}), ("workEmail", {"required": False}))
        _row, errors = ef.clean_application(application(firstName="", workEmail=""), today=TODAY, layout=layout)
        self.assertEqual(set(errors), {"firstName", "workEmail"})

    def test_choices_are_checked_against_the_published_list(self):
        layout = layout_with(("programme", {"options": ["Data Analyst Level 4"]}))
        row, errors = ef.clean_application(application(programme="Data Analyst Level 4"), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertEqual(row["programme"], "Data Analyst Level 4")
        _row, errors = ef.clean_application(application(), today=TODAY, layout=layout)
        self.assertEqual(errors, {"programme": "Choose one of the listed options."})

    def test_custom_answers_are_checked_by_type_and_go_to_their_columns(self):
        layout = layout_with(extra=[
            question("cf_years", "number", required=True),
            question("cf_start", "date"),
            question("cf_mentor", "yesno"),
            question("cf_shift", "dropdown", options=["Day", "Night"]),
            question("cf_notes", "text"),
        ])
        row, errors = ef.clean_application(application(custom={
            "cf_years": "3.5", "cf_start": "2027-01-04", "cf_mentor": True, "cf_shift": "Night", "cf_notes": " a \n b ",
            "cf_unknown": "ignored",
        }), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertEqual(row["custom_years"], Decimal("3.5"))
        # A custom date may be in the future.
        self.assertEqual(row["custom_start"], date(2027, 1, 4))
        self.assertIs(row["custom_mentor"], True)
        self.assertEqual(row["custom_shift"], "Night")
        self.assertEqual(row["custom_notes"], "a b")
        self.assertNotIn("custom_unknown", row)

        _row, errors = ef.clean_application(application(custom={
            "cf_years": "lots", "cf_start": "soon", "cf_mentor": "yes", "cf_shift": "Evening",
        }), today=TODAY, layout=layout)
        self.assertEqual(errors, {
            "cf_years": "Enter a number.", "cf_start": "Enter a valid date.", "cf_mentor": "Choose Yes or No.",
            "cf_shift": "Choose one of the listed options.",
        })
        _row, errors = ef.clean_application(application(), today=TODAY, layout=layout)
        self.assertEqual(errors, {"cf_years": ef.REQUIRED})

    def test_a_conditional_question_is_asked_only_for_its_answers(self):
        layout = layout_with(extra=[
            question("cf_visa", "dropdown", options=["Skilled", "Other"],
                     condition={"field": "residencyStatus", "values": ["Skilled Worker Visa", "Other"]}),
            question("cf_which", "text", required=True, condition={"field": "cf_visa", "values": ["Other"]}),
            question("cf_why", "text", required=True, condition={"field": "livesInUk", "values": ["No"]}),
        ])
        answers = {"cf_visa": "Other", "cf_which": "Family visa"}
        row, errors = ef.clean_application(application(residencyStatus="Other", custom=answers), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertEqual(row["custom_which"], "Family visa")
        # "Why" follows a No to livesInUk, so it is required now.
        _row, errors = ef.clean_application(application(residencyStatus="Other", livesInUk=False, custom=answers), today=TODAY, layout=layout)
        self.assertEqual(errors, {"cf_why": ef.REQUIRED})
        # Once the first answer changes, the whole chain is not asked: no error, nothing stale stored.
        row, errors = ef.clean_application(application(custom=answers), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertIsNone(row["custom_visa"])
        self.assertIsNone(row["custom_which"])

    def test_a_removed_custom_question_is_not_asked(self):
        layout = layout_with(extra=[question("cf_years", "number", required=True, hidden=True)])
        row, errors = ef.clean_application(application(custom={"cf_years": "4"}), today=TODAY, layout=layout)
        self.assertEqual(errors, {})
        self.assertNotIn("custom_years", row)


class SubmitApplicationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.lookup = stub_lookup(self)
        p = patch.object(ef, "_published_layout", return_value=(None, None))
        self.addCleanup(p.stop)
        self.published = p.start()
        # Inviting an eligible applicant is covered in tests_eligibility_invitation.
        p = patch.object(ef.eligibility_invitation, "invite_applicant", return_value="invited")
        self.addCleanup(p.stop)
        p.start()

    def post(self, body, cursor=None, **headers):
        headers.setdefault("HTTP_X_REQUESTED_WITH", "XMLHttpRequest")
        data = body if isinstance(body, (str, bytes)) else json.dumps(body)
        request = self.factory.post(URL, data=data, content_type="application/json", **headers)
        cursor = cursor or FakeCursor()
        connections = {ef.CONN: SimpleNamespace(cursor=lambda: cursor)}
        with patch.object(ef, "connections", connections):
            return ef.submit_application(request), cursor

    def test_inserts_one_row_and_echoes_nothing_stored(self):
        response, cursor = self.post(application(id=7, submitted_at="2000-01-01", Password="x", postcode="ex12mp"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(json.loads(response.content),
                         {"submitted": True, "eligible": True, "reasons": [], "outcome": "invited"})
        self.assertEqual(len(cursor.executed), 1)
        sql, params = cursor.executed[0]
        self.assertTrue(sql.startswith("INSERT INTO enrolment.eligibility_form ("))
        # Only the form's own columns: the client cannot set id, timestamps or anything else.
        self.assertIn(f"({', '.join(ef.INSERT_COLUMNS)})", sql)
        self.assertEqual(len(params), len(ef.INSERT_COLUMNS))
        stored = dict(zip(ef.INSERT_COLUMNS, params))
        self.assertEqual(stored["programme"], "Associate Project Manager Level 4")
        # Each postcode is saved as written, with where it is.
        self.assertEqual(stored["postcode"], "EX1 2MP")
        self.assertEqual((stored["postcode_country"], stored["postcode_region"], stored["postcode_district"]),
                         ("England", "South West", "Exeter"))
        self.assertEqual(stored["employer_postcode_district"], "East Devon")
        self.assertIsNone(stored["layout_version"])
        self.assertEqual((stored["eligible"], stored["ineligible_reason"]), (True, None))

    def submitted(self, body):
        response, cursor = self.post(body)
        self.assertEqual(response.status_code, 201)
        return json.loads(response.content), dict(zip(ef.INSERT_COLUMNS, cursor.executed[0][1]))

    def test_a_malformed_postcode_is_saved_and_makes_the_applicant_not_eligible(self):
        data, stored = self.submitted(application(postcode="ss"))
        self.assertIs(data["eligible"], False)
        self.assertEqual(data["reasons"], ["Your postcode (ss) is not a valid UK postcode."])
        self.assertEqual(stored["postcode"], "ss")
        self.assertIsNone(stored["postcode_country"])
        self.assertIs(stored["eligible"], False)
        self.assertEqual(stored["ineligible_reason"], "Your postcode (ss) is not a valid UK postcode.")

    def test_an_unknown_postcode_is_saved_and_makes_the_applicant_not_eligible(self):
        data, stored = self.submitted(application(employerPostcode="zz99zz"))
        self.assertIs(data["eligible"], False)
        self.assertEqual(data["reasons"], ["Your employer's postcode (ZZ9 9ZZ) could not be found."])
        self.assertEqual(stored["ineligible_reason"], data["reasons"][0])
        # The other postcode is still located.
        self.assertEqual(stored["postcode_district"], "Exeter")

    def test_a_postcode_outside_england_is_saved_with_its_country_and_not_eligible(self):
        data, stored = self.submitted(application(postcode="EH1 1YZ"))
        self.assertEqual((stored["postcode_country"], stored["postcode_region"]), ("Scotland", None))
        self.assertIs(data["eligible"], False)
        self.assertEqual(data["reasons"], ["Your postcode (EH1 1YZ) is outside England (Scotland)."])
        self.assertIs(stored["eligible"], False)

    def test_an_employer_outside_england_also_rules_the_applicant_out(self):
        data, _stored = self.submitted(application(postcode="ss", employerPostcode="EH1 1YZ"))
        self.assertIs(data["eligible"], False)
        self.assertEqual(data["reasons"], [
            "Your postcode (ss) is not a valid UK postcode.",
            "Your employer's postcode (EH1 1YZ) is outside England (Scotland).",
        ])

    def test_without_the_lookup_service_the_application_is_saved_unlocated(self):
        def down(_postcode):
            raise postcode_lookup.PostcodeLookupUnavailable("timed out")

        self.lookup.side_effect = down
        with self.assertLogs("enrolment_api.eligibility_form", level="WARNING"):
            response, cursor = self.post(application())
        self.assertEqual(response.status_code, 201)
        stored = dict(zip(ef.INSERT_COLUMNS, cursor.executed[0][1]))
        self.assertEqual(stored["postcode"], "EX1 2MP")
        self.assertTrue(all(stored[column] is None for column in ef.LOCATION_COLUMNS))
        # Not checked is not "eligible": staff see it was never decided.
        self.assertIsNone(stored["eligible"])
        self.assertIsNone(json.loads(response.content)["eligible"])

    def test_custom_answers_are_inserted_into_their_columns(self):
        layout = layout_with(extra=[question("cf_years", "number", required=True)])
        self.published.return_value = (4, layout)
        response, cursor = self.post(application(layoutVersion=4, custom={"cf_years": 2}))
        self.assertEqual(response.status_code, 201, response.content)
        sql, params = cursor.executed[0]
        self.assertIn(f'{", ".join(ef.INSERT_COLUMNS)}, "custom_years")', sql)
        self.assertEqual(params[-1], Decimal("2"))
        self.assertEqual(params[ef.INSERT_COLUMNS.index("layout_version")], 4)

    def test_answers_to_an_older_form_are_refused_so_the_page_can_reload_it(self):
        self.published.return_value = (5, layout_with())
        response, cursor = self.post(application(layoutVersion=4))
        self.assertEqual(response.status_code, 409)
        self.assertIs(json.loads(response.content)["formChanged"], True)
        self.assertEqual(cursor.executed, [])
        # The first publish also changes the form a page loaded beforehand was shown.
        self.assertEqual(self.post(application(layoutVersion=None))[0].status_code, 409)

    def test_a_page_from_before_the_builder_is_checked_against_the_form_in_force(self):
        self.published.return_value = (5, layout_with(("dateOfBirth", {"hidden": True})))
        response, cursor = self.post(application(dateOfBirth="not asked"))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(dict(zip(ef.INSERT_COLUMNS, cursor.executed[0][1]))["date_of_birth"])

    def test_the_layout_version_must_be_a_number(self):
        for version in ("4", True, 1.5):
            self.assertEqual(self.post(application(layoutVersion=version))[0].status_code, 400)

    def test_an_unreadable_layout_is_reported_without_saving(self):
        self.published.side_effect = DatabaseError("boom")
        with self.assertLogs("enrolment_api.eligibility_form", level="ERROR"):
            response, cursor = self.post(application())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("boom", response.content.decode())
        self.assertEqual(cursor.executed, [])

    def test_invalid_answers_are_returned_by_field_and_nothing_is_saved(self):
        response, cursor = self.post(application(surname="", programme="Other"))
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertEqual(set(payload["fields"]), {"surname", "programme"})
        self.assertEqual(cursor.executed, [])

    def test_requires_the_csrf_header(self):
        request = self.factory.post(URL, data=json.dumps(application()), content_type="application/json")
        self.assertEqual(ef.submit_application(request).status_code, 403)

    def test_only_post_is_accepted(self):
        request = self.factory.get(URL, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(ef.submit_application(request).status_code, 405)

    def test_rejects_malformed_and_oversized_bodies(self):
        self.assertEqual(self.post("{not json")[0].status_code, 400)
        self.assertEqual(self.post("[1, 2]")[0].status_code, 400)
        self.assertEqual(self.post(application(fullAddress="x" * ef.MAX_BODY_BYTES))[0].status_code, 413)

    def test_a_database_failure_is_reported_without_its_details(self):
        with self.assertLogs("enrolment_api.eligibility_form", level="ERROR"):
            response, _cursor = self.post(application(), cursor=FakeCursor(fail=True))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("relation", response.content.decode())


class TableTests(SimpleTestCase):
    def test_ddl_creates_every_column_the_insert_writes(self):
        cursor = FakeCursor()
        ef.ensure_table(cursor)
        statements = [sql for sql, _params in cursor.executed]
        self.assertEqual(statements[0], "CREATE SCHEMA IF NOT EXISTS enrolment")
        self.assertTrue(statements[1].startswith("CREATE TABLE IF NOT EXISTS enrolment.eligibility_form ("))
        for column in ef.INSERT_COLUMNS:
            self.assertIn(f" {column} ", statements[1])
        # Only the answers that identify the applicant are NOT NULL.
        for column in ("first_name", "surname", "work_email"):
            self.assertIn(f" {column} text NOT NULL,", statements[1])
        self.assertIn(" date_of_birth date,", statements[1])
        # Never alters or drops an existing table.
        self.assertFalse(any("DROP" in sql or "ALTER" in sql for sql in statements))

    def test_upgrade_only_adds_columns_and_relaxes_not_null(self):
        cursor = FakeCursor()
        ef.upgrade_table(cursor)
        statements = [sql for sql, _params in cursor.executed]
        added = [sql for sql in statements if "ADD COLUMN IF NOT EXISTS" in sql]
        relaxed = [sql for sql in statements if sql.endswith("DROP NOT NULL")]
        self.assertEqual(len(added) + len(relaxed), len(statements))
        self.assertEqual(len(added), len(ef.ADDED_COLUMNS))
        self.assertEqual(len(relaxed), len(ef.FIELDS) - len(ef.LOCKED))
        self.assertFalse(any(column in sql for sql in relaxed for column in ("first_name", "surname", "work_email")))


class GateTests(SimpleTestCase):
    def setUp(self):
        stub_lookup(self)
        p = patch.object(ef.eligibility_invitation, "invite_applicant", return_value="invited")
        self.addCleanup(p.stop)
        p.start()

    def test_only_the_exact_public_paths_are_open(self):
        for path in (URL, "/enrolment_api/public/eligibility-form-layout/", "/enrolment_api/public/postcode-lookup/"):
            self.assertIsNone(rule_for(path))
            self.assertIsNotNone(rule_for(path + "extra/"))
        self.assertIsNotNone(rule_for("/enrolment_api/public/"))
        self.assertIsNotNone(rule_for("/enrolment_api/wizard-layout/"))
        # The builder's own copy of the layout, with who published it, is not public.
        self.assertIsNotNone(rule_for("/enrolment_api/eligibility-form-layout/"))
        self.assertIsNotNone(rule_for("/enrolment_api/eligibility-form-layout/publish/"))

    def test_reachable_without_a_session_through_the_full_stack(self):
        cursor = FakeCursor()
        connections = {ef.CONN: SimpleNamespace(cursor=lambda: cursor)}
        with patch.dict("os.environ", {"API_REQUIRE_AUTH": "1"}), patch.object(ef, "connections", connections), \
                patch.object(ef, "_published_layout", return_value=(None, None)):
            response = self.client.post(
                URL, data=json.dumps(application()), content_type="application/json",
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            lookup = self.client.get("/enrolment_api/public/postcode-lookup/", {"postcode": "EX1 2MP"},
                                     HTTP_X_REQUESTED_WITH="XMLHttpRequest")
            neighbour = self.client.get("/enrolment_api/wizard-layout/")
            builder = self.client.get("/enrolment_api/eligibility-form-layout/")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(cursor.executed), 1)
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(json.loads(lookup.content)["district"], "Exeter")
        # The rest of the app is still closed to the same anonymous caller.
        self.assertEqual(neighbour.status_code, 401)
        self.assertEqual(builder.status_code, 401)
