"""Workplace screening: synthetic requests, recording cursors, no DB or network."""
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings

from enrolment_api import eligibility_form as ef, eligibility_layout as el, postcode_lookup as pl
from enrolment_api.tests_eligibility_form import FakeCursor, URL, application, fake_lookup, layout_with


class ReceiptCursor(FakeCursor):
    """Recording persistence double with the unique fingerprint contract."""
    def __init__(self):
        super().__init__()
        self.rows = {}
        self.result = None

    def execute(self, sql, params=None):
        super().execute(sql, params)
        if sql.startswith("INSERT"):
            optional = ("expected_leaving_date",) if '"expected_leaving_date"' in sql else ()
            row = dict(zip(ef.INSERT_COLUMNS + ef.WORKPLACE_COLUMNS + ef.SCREENING_COLUMNS + optional, params))
            key = row["submission_fingerprint"]
            if key in self.rows:
                self.result = None
            else:
                row["id"] = len(self.rows) + 1
                self.rows[key] = row
                self.result = (row["id"],)
        elif sql.startswith("SELECT"):
            row = self.rows.get(params[0])
            self.result = (row["id"], row["eligible"], row["assessment_status"], row["submission_response"]) if row else None
        elif sql.startswith("UPDATE"):
            row = next(row for row in self.rows.values() if row["id"] == params[1])
            row["submission_response"] = json.loads(params[0])

    def fetchone(self):
        result, self.result = self.result, None
        return result


@override_settings(DEBUG=True, ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=True)
class WorkplacePreviewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.original_invite = ef.eligibility_invitation.invite_applicant
        active = patch.object(ef.transaction, "atomic", side_effect=lambda **_kwargs: nullcontext())
        self.addCleanup(active.stop)
        active.start()
        for name, target, attribute, options in (
            ("lookup", pl, "lookup", {"side_effect": fake_lookup}),
            ("published", ef, "_published_layout", {"return_value": (None, None)}),
            ("invite", ef.eligibility_invitation, "invite_applicant", {"return_value": "invited"}),
        ):
            active = patch.object(target, attribute, **options)
            self.addCleanup(active.stop)
            setattr(self, name, active.start())

    def post(self, *, cursor=None, **changes):
        body = application(workplaceArrangement="same")
        body.update(changes)
        cursor = cursor or FakeCursor()
        request = self.factory.post(URL, data=json.dumps(body), content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        with patch.object(ef, "connections", {ef.CONN: SimpleNamespace(cursor=lambda: cursor)}):
            response = ef.submit_application(request)
        stored = dict(zip(ef.INSERT_COLUMNS + ef.WORKPLACE_COLUMNS, cursor.executed[0][1])) if cursor.executed else None
        return response, json.loads(response.content), cursor, stored

    def test_same_derives_employer_geography_and_discards_forged_or_stale_values(self):
        response, _data, _cursor, row = self.post(
            mainWorkplacePostcode="not-a-postcode", main_workplace_country="Wales",
            mainWorkplaceCountry="Wales", assessment_status="approved", assessment_reason="forged",
        )
        self.assertEqual(response.status_code, 201)
        for suffix in ("", "_country", "_region", "_district"):
            key = "main_workplace_postcode" if not suffix else f"main_workplace{suffix}"
            self.assertEqual(row[key], row[f"employer_postcode{suffix}"])
        self.assertEqual(row["assessment_status"], ef.PROVISIONAL_ELIGIBLE)
        self.assertIsNone(row["assessment_reason"])
        self.assertEqual(self.lookup.call_count, 2)  # applicant + employer, no third lookup

    def test_international_residential_addresses_reuse_country_and_skip_uk_postal_lookup(self):
        for postal in ("75001", "CF10 1EP", ""):
            with self.subTest(postal=postal):
                self.lookup.reset_mock()
                response, data, _cursor, row = self.post(
                    countryOfResidence="France", postcode=postal, residentialAddressLine1="10 Rue Exemple",
                    residentialTownCity="Paris", residentialCounty="Île-de-France",
                    workplaceArrangement="different", mainWorkplacePostcode="EX1 2MP",
                )
                self.assertEqual(response.status_code, 201)
                self.assertIs(data["eligible"], True)
                self.assertEqual(row["country_of_residence"], "France")
                self.assertEqual(row["full_address"], "10 Rue Exemple, Paris, Île-de-France")
                self.assertIsNone(row["postcode_country"])
                self.assertEqual(row["main_workplace_country"], "England")
                self.assertEqual(self.lookup.call_count, 2)  # company + workplace, never overseas postal code

    def test_collect_only_answers_do_not_silently_become_new_funding_rules(self):
        for changes in (
            {"livesInUk": False, "nationality": "France", "residencyStatus": "Other"},
            {"residentPrev3Years": False, "yearsInUk": "0"},
            {"residenceNotForEducation": False},
            {"fullTimeEducationPrior": True, "expectedLeavingDate": "2027-06-30"},
            {"attendedFundedTraining": True, "fundedTrainingCompletedOn": "2026-01-01"},
            {"startDateWithEmployer": "2027-01-01"},
        ):
            with self.subTest(changes=changes):
                response, data, _cursor, row = self.post(**changes)
                self.assertEqual(response.status_code, 201)
                self.assertIs(data["eligible"], True)
                self.assertEqual(row["assessment_status"], ef.PROVISIONAL_ELIGIBLE)

    def test_each_workplace_country_drives_screening_independently_of_employer_country(self):
        for code, country in (("CT1 1AA", "England"), ("CF10 1EP", "Wales"), ("EH1 1YZ", "Scotland"), ("BT1 5GS", "Northern Ireland")):
            with self.subTest(country=country):
                self.lookup.side_effect = lambda value: fake_lookup(value) if pl.compact(value) in ("EX12MP", "EX34MP") else {
                    "postcode": code, "country": country, "region": "", "district": "Workplace district",
                }
                response, data, _cursor, row = self.post(workplaceArrangement="different", mainWorkplacePostcode=code, main_workplace_country="forged")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(row["main_workplace_country"], country)
                self.assertEqual(row["main_workplace_postcode"], code)
                self.assertEqual(row["main_workplace_district"], "Workplace district")
                self.assertIsNone(row["main_workplace_region"])
                self.assertIs(data["eligible"], country == "England")
                self.assertEqual(data["outcome"], "invited" if country == "England" else "notEligible")

    def test_employer_country_never_overrides_a_verified_english_branch(self):
        for code, country in (("CT1 1AA", "England"), ("CF10 1EP", "Wales"), ("EH1 1YZ", "Scotland"), ("BT1 5GS", "Northern Ireland")):
            with self.subTest(country=country):
                self.invite.reset_mock()
                self.lookup.side_effect = lambda value: {"postcode": code, "country": country, "region": "", "district": "Company address"} if value == code else fake_lookup(value)
                response, data, _cursor, row = self.post(employerPostcode=code, workplaceArrangement="different", mainWorkplacePostcode="EX1 2MP", eligible=False, main_workplace_country="Wales")
                self.assertEqual(response.status_code, 201)
                self.assertEqual(row["employer_postcode_country"], country)
                self.assertEqual(row["main_workplace_country"], "England")
                self.assertIs(data["eligible"], True)
                self.assertEqual(data["reasons"], [])
                self.invite.assert_called_once()

    def test_other_screening_failure_preserves_its_reason_without_workplace_substitution(self):
        reason = "Independent residency eligibility has not been established."
        row = {"main_workplace_country": "England", "main_workplace_postcode": "EX1 2MP", "majority_hours_in_england": True}
        reasons = [reason]
        self.assertIs(ef.screen_workplace(row, False, reasons), False)
        self.assertEqual(reasons, [reason])
        self.assertEqual(row["assessment_status"], ef.NOT_ELIGIBLE)
        self.assertEqual(row["assessment_reason"], reason)
        self.invite.assert_not_called()

    def test_new_not_eligible_retries_reuse_one_record_and_never_invite(self):
        cursor = ReceiptCursor()
        first = self.post(cursor=cursor, majorityHoursInEngland=False)[1]
        second = self.post(cursor=cursor, majorityHoursInEngland=False, eligible=True, assessment_status="provisional_eligible")[1]
        self.assertEqual(first, second)
        self.assertEqual(first["outcome"], "notEligible")
        self.assertIs(first["eligible"], False)
        self.assertEqual(len(cursor.rows), 1)
        self.assertEqual(next(iter(cursor.rows.values()))["assessment_status"], ef.NOT_ELIGIBLE)
        self.invite.assert_not_called()

    def test_incomplete_lookup_blocks_completion_even_if_another_check_fails(self):
        def lookup(value):
            if value == "EX1 2MP":
                raise pl.PostcodeLookupUnavailable("synthetic residence lookup timeout")
            return fake_lookup(value)
        self.lookup.side_effect = lookup
        with self.assertLogs("enrolment_api.eligibility_form", level="WARNING"):
            response, data, cursor, _row = self.post(majorityHoursInEngland=False)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("outcome", data)
        self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def test_remote_requires_verifiable_location_before_completing_screening(self):
        for code in (None, "", "EX1 2MP"):
            with self.subTest(code=code):
                response, data, cursor, row = self.post(workplaceArrangement="remote_hybrid_multiple", mainWorkplacePostcode=code)
                if not code:
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(data["fields"], {"mainWorkplacePostcode": ef.REQUIRED})
                    self.assertEqual(cursor.executed, [])
                else:
                    self.assertEqual(response.status_code, 201)
                    self.assertIs(data["eligible"], True)
                    self.assertEqual(row["main_workplace_country"], "England")
                    self.assertEqual(row["assessment_status"], ef.PROVISIONAL_ELIGIBLE)

    def test_requiredness_and_choices_are_server_owned_even_with_layout_overrides(self):
        layout = layout_with(("workplaceArrangement", {"required": False, "options": ["forged"], "hidden": True}),
                             ("mainWorkplacePostcode", {"required": False, "hidden": True}))
        self.published.return_value = (None, layout)
        for changes, field in (
            ({"workplaceArrangement": None}, "workplaceArrangement"),
            ({"workplaceArrangement": ""}, "workplaceArrangement"),
            ({"workplaceArrangement": "forged"}, "workplaceArrangement"),
            ({"workplaceArrangement": True}, "workplaceArrangement"),
            ({"workplaceArrangement": "different", "mainWorkplacePostcode": ""}, "mainWorkplacePostcode"),
        ):
            with self.subTest(changes=changes):
                response, data, cursor, _row = self.post(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, data["fields"])
                self.assertEqual(cursor.executed, [])
                self.invite.assert_not_called()

    def test_bad_main_postcode_and_service_failure_do_not_save_or_invite(self):
        for arrangement in ("different", "remote_hybrid_multiple"):
            for code, message in (("ss", pl.INVALID_POSTCODE), ("ZZ9 9ZZ", pl.UNKNOWN_POSTCODE)):
                with self.subTest(arrangement=arrangement, code=code):
                    response, data, cursor, _row = self.post(workplaceArrangement=arrangement, mainWorkplacePostcode=code)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(data["fields"], {"mainWorkplacePostcode": message})
                    self.assertEqual(cursor.executed, [])
        def unavailable(value):
            if value == "CT1 1AA":
                raise pl.PostcodeLookupUnavailable("synthetic timeout")
            return fake_lookup(value)
        self.lookup.side_effect = unavailable
        response, data, cursor, _row = self.post(workplaceArrangement="different", mainWorkplacePostcode="CT1 1AA")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(data["fields"], {"mainWorkplacePostcode": ef.WORKPLACE_POSTCODE_UNAVAILABLE})
        self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def test_stale_workplace_answers_are_cleared_outside_paid_employment(self):
        row, errors = ef.clean_application(application(employmentStatus="Self employment", workplaceArrangement="different", mainWorkplacePostcode="ss"))
        self.assertEqual(errors, {"employmentStatus": ef.EMPLOYMENT_RESTRICTION_ERROR})
        self.assertTrue(all(row[column] is None for column in ef.WORKPLACE_COLUMNS))

    def test_no_working_hours_answer_is_not_eligible_for_every_arrangement(self):
        for arrangement in ef.WORKPLACE_ARRANGEMENTS:
            response, data, cursor, _row = self.post(workplaceArrangement=arrangement, mainWorkplacePostcode="EX1 2MP", majorityHoursInEngland=False)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(data["outcome"], "notEligible")
            self.assertIs(data["eligible"], False)
            self.assertEqual(_row["assessment_status"], ef.NOT_ELIGIBLE)
            self.assertIn("50%", _row["assessment_reason"])
        self.invite.assert_not_called()

    @override_settings(ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=False)
    def test_disabled_preview_accepts_legacy_payload_and_never_references_new_columns(self):
        response, _data, cursor, _row = self.post(workplaceEnabled=True, workplaceArrangement="forged", mainWorkplacePostcode="ss")
        self.assertEqual(response.status_code, 201)
        sql, params = cursor.executed[0]
        self.assertEqual(len(params), len(ef.INSERT_COLUMNS + ef.SCREENING_COLUMNS))
        self.assertTrue(all(column not in sql for column in ef.WORKPLACE_COLUMNS))
        self.assertEqual(el._workplace_feature(), {})

    @override_settings(DEBUG=False)
    def test_preview_cannot_be_activated_in_production_settings(self):
        self.assertFalse(ef.workplace_enabled())
        self.assertEqual(el._workplace_feature(), {})
        response, _data, cursor, _row = self.post(workplaceArrangement=None)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(cursor.executed[0][1]), len(ef.INSERT_COLUMNS + ef.SCREENING_COLUMNS))

    def test_old_published_layout_is_restored_without_modifying_the_original(self):
        layout = layout_with()
        for section in layout["sections"]:
            section["items"] = [item for item in section["items"] if item["key"] not in ef.WORKPLACE_KEYS]
        before = deepcopy(layout)
        visible = el.public_layout(layout)
        self.assertEqual(layout, before)
        keys = [item["key"] for section in visible["sections"] if section["slug"] == "employment" for item in section["items"]]
        position = keys.index("employerPostcode")
        self.assertEqual(keys[position + 1:position + 3], ["workplaceArrangement", "mainWorkplacePostcode"])
        self.assertIs(el._payload(None)["workplaceEnabled"], True)
        self.assertEqual(el._normalize_builtin({"key": "workplaceArrangement", "options": ["forged"]})["options"], list(ef.WORKPLACE_ARRANGEMENTS))

    def test_schema_and_upgrade_preserve_history_and_enable_assessment_constraints(self):
        root = Path(__file__).resolve().parents[2]
        upgrade = (root / "database" / "eligibility_workplace_phase2b.sql").read_text()
        fresh = (root / "database" / "schema.sql").read_text()
        sql = " ".join(line for line in upgrade.splitlines() if not line.lstrip().startswith("--"))
        self.assertNotRegex(sql, r"(?i)\b(UPDATE|DELETE|INSERT|DROP COLUMN|DROP TABLE|DEFAULT)\b")
        for column, definition in ef.WORKPLACE_COLUMN_DEFINITIONS + ef.SCREENING_COLUMN_DEFINITIONS:
            self.assertIn(f"{column} {definition}", " ".join(fresh.split()))
        for column, definition in ef.SCREENING_COLUMN_DEFINITIONS:
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {column} {definition}", " ".join(sql.split()))
        cursor = FakeCursor()
        ef.upgrade_table(cursor)
        self.assertEqual(sum("ADD COLUMN IF NOT EXISTS" in statement for statement, _params in cursor.executed), len(ef.ADDED_COLUMNS) + 9)

    def test_arrangement_country_and_hours_matrix(self):
        for arrangement in ef.WORKPLACE_ARRANGEMENTS:
            for code, country in (("CT1 1AA", "England"), ("CF10 1EP", "Wales"), ("EH1 1YZ", "Scotland"), ("BT1 5GS", "Northern Ireland")):
                for hours in (True, False):
                    with self.subTest(arrangement=arrangement, country=country, hours=hours):
                        self.invite.reset_mock()
                        self.lookup.side_effect = lambda value: {"postcode": code, "country": country, "region": "", "district": ""} if value == code else fake_lookup(value)
                        response, data, _cursor, row = self.post(
                            postcode="", workplaceArrangement=arrangement, majorityHoursInEngland=hours,
                            employerPostcode=code if arrangement == "same" else "EX3 4MP",
                            mainWorkplacePostcode=code if arrangement != "same" else "stale",
                        )
                        passed = hours and country == "England"
                        self.assertEqual(response.status_code, 201)
                        self.assertIs(data["eligible"], passed)
                        self.assertEqual(row["assessment_status"], ef.PROVISIONAL_ELIGIBLE if passed else ef.NOT_ELIGIBLE)
                        self.assertEqual(self.invite.call_count, int(passed))

    def test_hidden_or_optional_critical_fields_and_old_clients_cannot_bypass_screening(self):
        layout = layout_with(*[(key, {"hidden": True, "required": False}) for key in ef.SCREENING_REQUIRED_FIELDS])
        next(section for section in layout["sections"] if section["slug"] == "employment")["hidden"] = True
        self.published.return_value = (None, layout)
        for changes, field in (
            ({"workplaceArrangement": None}, "workplaceArrangement"),
            ({"employmentStatus": None}, "employmentStatus"),
            ({"majorityHoursInEngland": None}, "majorityHoursInEngland"),
            ({"majorityHoursInEngland": "Yes"}, "majorityHoursInEngland"),
            ({"employerPostcode": ""}, "employerPostcode"),
        ):
            response, data, cursor, _row = self.post(**changes)
            self.assertEqual(response.status_code, 400, changes)
            self.assertIn(field, data["fields"])
            self.assertEqual(cursor.executed, [])
        visible = el.public_layout(layout)
        self.assertFalse(next(section for section in visible["sections"] if section["slug"] == "employment")["hidden"])
        self.invite.assert_not_called()

    def test_residential_country_does_not_reject_a_verified_english_workplace(self):
        for code, country in (("CF10 1EP", "Wales"), ("EH1 1YZ", "Scotland"), ("BT1 5GS", "Northern Ireland")):
            for arrangement in ef.WORKPLACE_ARRANGEMENTS:
                with self.subTest(country=country, arrangement=arrangement):
                    self.invite.reset_mock()
                    self.lookup.side_effect = lambda value: {
                        "postcode": code, "country": country, "region": "", "district": "Home district",
                    } if value == code else fake_lookup(value)
                    response, data, _cursor, row = self.post(
                        postcode=code, workplaceArrangement=arrangement, mainWorkplacePostcode="EX1 2MP",
                        postcode_country="England", eligible=False, assessment_status="not_eligible",
                    )
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(row["postcode_country"], country)
                    self.assertEqual(row["main_workplace_country"], "England")
                    self.assertIs(data["eligible"], True)
                    self.assertEqual(data["assessmentStatus"], ef.PROVISIONAL_ELIGIBLE)
                    self.assertEqual(data["reasons"], [])
                    self.assertIsNone(row["ineligible_reason"])
                    self.invite.assert_called_once()

    @override_settings(ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=False)
    def test_legacy_residential_country_rejection_is_unchanged(self):
        for code, country in (("CF10 1EP", "Wales"), ("EH1 1YZ", "Scotland")):
            with self.subTest(country=country):
                self.lookup.side_effect = lambda value: {
                    "postcode": code, "country": country, "region": "", "district": "",
                } if value == code else fake_lookup(value)
                response, data, _cursor, row = self.post(postcode=code)
                self.assertEqual(response.status_code, 201)
                self.assertIs(data["eligible"], False)
                self.assertEqual(data["outcome"], "notEligible")
                self.assertEqual(data["reasons"], [f"Your postcode ({code}) is outside England ({country})."])
                self.assertEqual(row["ineligible_reason"], data["reasons"][0])
        self.invite.assert_not_called()

    def test_english_residence_does_not_approve_a_non_english_workplace(self):
        response, data, _cursor, row = self.post(workplaceArrangement="different", mainWorkplacePostcode="EH1 1YZ", main_workplace_country="England", eligible=True)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(row["postcode_country"], "England")
        self.assertEqual(row["main_workplace_country"], "Scotland")
        self.assertEqual(data["outcome"], "notEligible")
        self.assertIs(row["eligible"], False)
        self.assertIn("Scotland", data["reasons"][0])
        self.invite.assert_not_called()

    def test_residential_country_cannot_override_failed_hours_or_missing_workplace(self):
        for changes in ({"majorityHoursInEngland": False},
                        {"workplaceArrangement": "remote_hybrid_multiple", "mainWorkplacePostcode": ""},
                        {"workplaceArrangement": "different", "mainWorkplacePostcode": "EH1 1YZ"}):
            with self.subTest(changes=changes):
                response, data, cursor, row = self.post(postcode="EH1 1YZ", **changes)
                if changes.get("mainWorkplacePostcode") == "":
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(cursor.executed, [])
                else:
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(data["outcome"], "notEligible")
                    self.assertIs(row["eligible"], False)
                    self.assertTrue(data["reasons"])
                    self.assertTrue(row["assessment_reason"])
        self.invite.assert_not_called()

    def test_invalid_residential_postcode_requires_correction_before_a_result(self):
        for code, message in (("ss", pl.INVALID_POSTCODE), ("ZZ9 9ZZ", pl.UNKNOWN_POSTCODE)):
            with self.subTest(code=code):
                response, data, cursor, _row = self.post(postcode=code, postcode_country="England", eligible=True)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(data["fields"], {"postcode": message})
                self.assertEqual(cursor.executed, [])
                self.assertNotIn("outcome", data)
        self.invite.assert_not_called()

    def test_required_residential_postcode_and_residency_evidence_are_not_bypassed(self):
        self.published.return_value = (None, layout_with(("postcode", {"required": True})))
        for changes, field in (
            ({"postcode": ""}, "postcode"),
            ({"residencyStatus": None}, "residencyStatus"),
            ({"countryOfResidence": None}, "countryOfResidence"),
            ({"residentPrev3Years": None}, "residentPrev3Years"),
            ({"residentPrev3Years": False, "yearsInUk": None}, "yearsInUk"),
            ({"residenceNotForEducation": None}, "residenceNotForEducation"),
        ):
            with self.subTest(changes=changes):
                response, data, cursor, _row = self.post(eligible=True, **changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(data["fields"], {field: ef.REQUIRED})
                self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def test_prior_residential_rejection_receipt_is_not_reclassified_or_invited(self):
        cursor = ReceiptCursor()
        body = application(workplaceArrangement="same", postcode="EH1 1YZ")
        row, errors = ef.clean_application(body)
        self.assertEqual(errors, {})
        fingerprint = ef.submission_fingerprint(row, None)
        reason = "Your postcode (EH1 1YZ) is outside England (Scotland)."
        receipt = {"submitted": True, "eligible": False, "reasons": [reason], "outcome": "notEligible",
                   "assessmentStatus": ef.NOT_ELIGIBLE, "assessmentReason": reason}
        prior = {"id": 1, "eligible": False, "assessment_status": ef.NOT_ELIGIBLE,
                 "submission_fingerprint": fingerprint, "submission_response": deepcopy(receipt)}
        cursor.rows[fingerprint] = deepcopy(prior)
        response, data, _cursor, _row = self.post(cursor=cursor, postcode="EH1 1YZ")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data, receipt)
        self.assertEqual(cursor.rows, {fingerprint: prior})
        self.invite.assert_not_called()

    def test_unknown_country_and_technical_failure_are_retryable_errors(self):
        self.lookup.side_effect = lambda value: {"postcode": "EX3 4MP", "country": "", "region": "", "district": ""}
        response, data, cursor, _row = self.post(postcode="")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("outcome", data)
        self.assertEqual(cursor.executed, [])
        def unavailable(value):
            if value == "EX1 2MP":
                raise pl.PostcodeLookupUnavailable("timeout")
            return fake_lookup(value)
        self.lookup.side_effect = unavailable
        with self.assertLogs("enrolment_api.eligibility_form", level="WARNING"):
            response, data, cursor, _row = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("outcome", data)
        self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def test_forged_approval_cannot_replace_missing_workplace_evidence(self):
        response, data, cursor, _row = self.post(
            workplaceArrangement="remote_hybrid_multiple", mainWorkplacePostcode="",
            eligible=True, assessment_status="provisional_eligible", assessmentStatus="provisional_eligible",
            assessment_reason="approved", country="England", mainWorkplaceCountry="England", main_workplace_country="England",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["fields"], {"mainWorkplacePostcode": ef.REQUIRED})
        self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def seed_historical_pending(self, cursor, **changes):
        row, errors = ef.clean_application(application(workplaceArrangement="same", **changes))
        self.assertEqual(errors, {})
        fingerprint = ef.submission_fingerprint(row, None)
        row.update(id=1, eligible=None, assessment_status=ef.PENDING_ASSESSMENT,
                   assessment_reason="Historical case requiring assessment.", submission_fingerprint=fingerprint,
                   submission_response={"submitted": True, "eligible": None, "reasons": [],
                                        "outcome": ef.PENDING_OUTCOME, "assessmentStatus": ef.PENDING_ASSESSMENT,
                                        "assessmentReason": "Historical case requiring assessment."})
        cursor.rows[fingerprint] = row
        return row

    def test_pending_replays_reuse_one_record_and_never_invite(self):
        cursor = ReceiptCursor()
        prior = deepcopy(self.seed_historical_pending(cursor, majorityHoursInEngland=False))
        first, first_data, _cursor, _row = self.post(cursor=cursor, majorityHoursInEngland=False)
        second, second_data, _cursor, _row = self.post(cursor=cursor, majorityHoursInEngland=False, eligible=True)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first_data, second_data)
        self.assertEqual(first_data["outcome"], ef.PENDING_OUTCOME)
        self.assertEqual(next(iter(cursor.rows.values())), prior)
        self.assertEqual(len(cursor.rows), 1)
        self.invite.assert_not_called()

    def test_expected_leaving_date_is_preserved_on_not_eligible_submission_retries(self):
        cursor = ReceiptCursor()
        changes = {"fullTimeEducationPrior": True, "expectedLeavingDate": "2027-06-30", "majorityHoursInEngland": False}
        self.assertEqual(self.post(cursor=cursor, **changes)[0].status_code, 201)
        self.assertEqual(self.post(cursor=cursor, **changes)[0].status_code, 201)
        self.assertEqual(len(cursor.rows), 1)
        stored = next(iter(cursor.rows.values()))
        self.assertEqual(stored["expected_leaving_date"].isoformat(), "2027-06-30")
        self.assertEqual(stored["assessment_status"], ef.NOT_ELIGIBLE)
        self.invite.assert_not_called()

    def test_provisional_replays_do_not_duplicate_records_or_invitations(self):
        cursor = ReceiptCursor()
        self.post(cursor=cursor)
        self.post(cursor=cursor, employerPostcode="ex34mp", workEmail="SAM@EMPLOYER.EXAMPLE")
        self.assertEqual(len(cursor.rows), 1)
        self.invite.assert_called_once()

    def test_invitation_technical_retry_reuses_the_original_record(self):
        cursor = ReceiptCursor()
        self.invite.side_effect = ["retryLater", "invited"]
        self.assertEqual(self.post(cursor=cursor)[1]["outcome"], "retryLater")
        self.assertEqual(self.post(cursor=cursor)[1]["outcome"], "invited")
        self.assertEqual(self.post(cursor=cursor)[1]["outcome"], "invited")
        self.assertEqual(len(cursor.rows), 1)
        self.assertEqual(self.invite.call_count, 2)

    def test_missing_receipt_fails_closed_instead_of_reinviting(self):
        cursor = ReceiptCursor()
        self.post(cursor=cursor)
        next(iter(cursor.rows.values()))["submission_response"] = None
        self.invite.reset_mock()
        with self.assertLogs("enrolment_api.eligibility_form", level="ERROR"):
            response, _data, _cursor, _row = self.post(cursor=cursor)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(len(cursor.rows), 1)
        self.invite.assert_not_called()

    def test_invitation_helper_itself_refuses_pending_before_account_queries(self):
        with patch.object(ef.eligibility_invitation, "has_account") as accounts, patch("login.services.invite_eligible_applicant") as mail:
            outcome = self.original_invite({"assessment_status": ef.PENDING_ASSESSMENT, "eligible": None})
        self.assertEqual(outcome, ef.PENDING_OUTCOME)
        accounts.assert_not_called()
        mail.assert_not_called()

    def test_old_client_with_no_workplace_answers_cannot_default_to_same(self):
        request = self.factory.post(URL, data=json.dumps(application()), content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        cursor = FakeCursor()
        with patch.object(ef, "connections", {ef.CONN: SimpleNamespace(cursor=lambda: cursor)}):
            response = ef.submit_application(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.content)["fields"], {"workplaceArrangement": ef.REQUIRED})
        self.assertEqual(cursor.executed, [])
        self.invite.assert_not_called()

    def test_not_eligible_preserves_independent_reason_alongside_hours_failure(self):
        reason = "Independent residency eligibility has not been established."
        with patch.object(ef, "locate_postcodes", return_value=(False, [reason])):
            response, data, _cursor, row = self.post(workplaceArrangement="different", mainWorkplacePostcode="EX1 2MP", majorityHoursInEngland=False)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data["outcome"], "notEligible")
        self.assertEqual(data["reasons"], [reason, ef.WORKING_HOURS_SCREENING_REASON])
        self.assertIs(row["eligible"], False)
        self.invite.assert_not_called()

    def test_changed_answers_create_a_new_application_without_reclassifying_the_pending_one(self):
        cursor = ReceiptCursor()
        pending = deepcopy(self.seed_historical_pending(cursor, majorityHoursInEngland=False))
        self.post(cursor=cursor, majorityHoursInEngland=True)
        self.assertEqual(len(cursor.rows), 2)
        self.assertEqual(cursor.rows[pending["submission_fingerprint"]], pending)
        self.invite.assert_called_once()

    def test_historical_null_assessment_and_fingerprint_are_not_updated(self):
        cursor = ReceiptCursor()
        historical = {"id": 1, "eligible": True, "assessment_status": None, "assessment_reason": None,
                      "submission_fingerprint": None, "submission_response": None}
        cursor.rows["historical"] = deepcopy(historical)
        self.post(cursor=cursor, majorityHoursInEngland=False)
        self.assertEqual(cursor.rows["historical"], historical)
        self.assertEqual(len(cursor.rows), 2)
        self.assertTrue(all(params[1] != 1 for sql, params in cursor.executed if sql.startswith("UPDATE")))
