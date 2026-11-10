"""Synthetic staff requests and recording SELECT cursors; no database or network."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve

from login.api_gate import rule_for
from . import eligibility_applications as api, eligibility_form as form

URL = "/enrolment_api/eligibility-applications/"
PENDING = {
    "id": 42, "first_name": "Sam", "surname": "Example", "work_email": "sam@example.test",
    "submitted_at": "2026-11-03T10:00:00+00:00", "employer_name": "Example Ltd",
    "employer_postcode": "EH1 1YZ", "main_workplace_postcode": None,
    "workplace_arrangement": "remote_hybrid_multiple", "assessment_status": "pending_assessment",
    "assessment_reason": "There is insufficient verified workplace-location evidence.",
}


class ReadCursor:
    def __init__(self, rows=(), total=None):
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.executed = []

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if not sql.startswith("SELECT "):
            raise AssertionError("Review APIs may only SELECT.")
        self.executed.append((sql, deepcopy(params)))

    def fetchone(self):
        return (self.total,) if self.executed[-1][0].startswith("SELECT count(*)") else ((self.rows[0],) if self.rows else None)

    def fetchall(self):
        return [(row,) for row in self.rows]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class EligibilityApplicationsTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = SimpleNamespace(role="staff", subject_type="staff", subject_id=7)
        for attribute, options in (
            ("authenticate_request", {"side_effect": lambda _request: self.account}),
            ("_accesses_of", {"return_value": frozenset({"enrolment"})}),
        ):
            active = patch(f"login.permissions.{attribute}", **options)
            self.addCleanup(active.stop)
            setattr(self, attribute, active.start())
        self.cursor = ReadCursor([deepcopy(PENDING)])
        active = patch.object(api, "connections", {form.CONN: SimpleNamespace(cursor=lambda: self.cursor)})
        self.addCleanup(active.stop)
        active.start()
        self.guards = []
        for target in ("enrolment_api.eligibility_form.ensure_table", "enrolment_api.eligibility_form.upgrade_table",
                       "enrolment_api.eligibility_invitation.invite_applicant", "enrolment_api.postcode_lookup.lookup"):
            active = patch(target, side_effect=AssertionError("Read-only review must not run workflows."))
            self.addCleanup(active.stop)
            self.guards.append(active.start())

    def get(self, query=None, application_id=None):
        request = self.factory.get(URL, data=query or {})
        response = api.eligibility_applications(request) if application_id is None else api.eligibility_application_detail(request, application_id)
        return response, json.loads(response.content)

    def test_staff_with_enrolment_and_super_admin_grants_can_read_both_apis(self):
        for role, grants in (("staff", {"enrolment"}), ("staff", {"coach", "enrolment"}), ("admin", {"super-admin"})):
            for application_id in (None, 42):
                with self.subTest(role=role, grants=grants, detail=application_id):
                    self.account.role = role
                    self._accesses_of.return_value = frozenset(grants)
                    response, _data = self.get(application_id=application_id)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertIn("private", response["Cache-Control"])

    def test_anonymous_and_other_roles_and_ungranted_staff_are_refused_before_read(self):
        for account, grants, expected in (
            (None, set(), 401), (SimpleNamespace(role="learner"), {"enrolment"}, 403),
            (SimpleNamespace(role="employer"), {"enrolment"}, 403),
            (SimpleNamespace(role="staff"), {"curriculum"}, 403),
            (SimpleNamespace(role="staff"), set(), 403),
        ):
            for application_id in (None, 42):
                with self.subTest(account=account, detail=application_id):
                    self.account = account
                    self._accesses_of.return_value = frozenset(grants)
                    response, _data = self.get(application_id=application_id)
                    self.assertEqual(response.status_code, expected)
        self.assertEqual(self.cursor.executed, [])

    def test_default_is_pending_assessment_not_invitation_or_retry_outcomes(self):
        response, data = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["applications"][0], {**PENDING, "id": "42"})
        self.assertEqual((data["total"], data["page"], data["pageSize"]), (1, 1, 25))
        for sql, params in self.cursor.executed:
            self.assertIn("to_jsonb(a)->>'assessment_status' = %s", sql)
            self.assertEqual(params[0], "pending_assessment")
            self.assertNotIn("retryLater", sql)
            self.assertNotIn("submission_response", sql)
        self.assertTrue(all(not guard.called for guard in self.guards))

    def test_all_and_legacy_filters_do_not_infer_assessment_status(self):
        self.cursor = ReadCursor([{**PENDING, "assessment_status": None, "assessment_reason": None}])
        _response, data = self.get({"status": "legacy"})
        self.assertIsNone(data["applications"][0]["assessment_status"])
        self.assertTrue(all("to_jsonb(a)->>'assessment_status' IS NULL" in sql for sql, _params in self.cursor.executed))
        self.cursor.executed.clear()
        self.get({"status": "all"})
        self.assertTrue(all(" WHERE " not in sql for sql, _params in self.cursor.executed))

    def test_supported_status_filters_are_bound_parameters(self):
        for status in (form.PROVISIONAL_ELIGIBLE, form.NOT_ELIGIBLE):
            self.get({"status": status})
            self.assertEqual(self.cursor.executed[-1][1], [status, 25, 0])

    def test_search_and_pagination_are_bounded_parameterised_and_stably_ordered(self):
        self.cursor = ReadCursor([json.dumps(PENDING)], total=60)
        query = "sam%' OR 1=1 --"
        response, data = self.get({"search": query, "page": 2, "pageSize": 25})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((data["page"], data["total"]), (2, 60))
        sql, params = self.cursor.executed[-1]
        self.assertNotIn(query, sql)
        self.assertIn("strpos(lower(concat_ws", sql)
        self.assertIn("ORDER BY a.submitted_at DESC NULLS LAST, a.id DESC", sql)
        self.assertEqual(params, [form.PENDING_ASSESSMENT, query, 25, 25])
        self.assertEqual(data["applications"][0]["id"], "42")
        self.get({"page": 999})
        self.assertEqual(self.cursor.executed[-1][1][-1], 50)

    def test_empty_queue(self):
        self.cursor = ReadCursor()
        response, data = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data, {"applications": [], "total": 0, "page": 1, "pageSize": 25})

    def test_invalid_filters_do_not_reach_persistence(self):
        for query in ({"status": "retryLater"}, {"status": "pendingAssessment"},
                      {"status": "approved"}, {"status": "rejected"}, {"search": "x" * 201},
                      {"page": "0"}, {"page": "-1"}, {"page": "1.5"}, {"page": "1000001"},
                      {"pageSize": "101"}, {"pageSize": "no"}, {"page": "²"}):
            response, _data = self.get(query)
            self.assertEqual(response.status_code, 400, query)
        self.assertEqual(self.cursor.executed, [])

    def test_details_include_saved_declarations_verified_geography_and_recorded_outcome(self):
        record = {key: None for key in api.DETAIL_COLUMNS}
        record.update(PENDING, majority_hours_in_england=False, postcode_country="Wales",
                      employer_postcode_country="Scotland", main_workplace_country=None,
                      residency_status="Pre-settled Status", eligible=None, invitation_outcome="pendingAssessment")
        self.cursor = ReadCursor([json.dumps(record)])
        response, data = self.get(application_id=42)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["application"], {**record, "id": "42"})
        sql, params = self.cursor.executed[0]
        self.assertEqual(params, [42])
        self.assertIn("->'submission_response'->'outcome'", sql)
        self.assertNotIn("submission_fingerprint", sql)
        self.assertNotIn("custom_", sql)
        self.assertNotIn("SELECT *", sql)
        self.assertTrue(all(not guard.called for guard in self.guards))

    def test_projection_tolerates_legacy_schema_and_limits_json_function_arguments(self):
        projection = api._projection(api.DETAIL_COLUMNS, outcome=True)
        self.assertIn("to_jsonb(a)->'assessment_status'", projection)
        self.assertNotIn("a.assessment_status", projection)
        self.assertIn(" || ", projection)
        self.cursor = ReadCursor([{**PENDING, "assessment_status": None, "workplace_arrangement": None,
                                   "eligible": True, "invitation_outcome": None}])
        _response, data = self.get(application_id=42)
        self.assertIsNone(data["application"]["assessment_status"])
        self.assertIsNone(data["application"]["workplace_arrangement"])
        self.assertIsNone(data["application"]["invitation_outcome"])

    def test_not_found_and_out_of_range_ids(self):
        self.cursor = ReadCursor()
        self.assertEqual(self.get(application_id=42)[0].status_code, 404)
        self.cursor.executed.clear()
        for value in (0, 9_223_372_036_854_775_808):
            self.assertEqual(self.get(application_id=value)[0].status_code, 404)
        self.assertEqual(self.cursor.executed, [])

    def test_every_mutation_method_is_refused_without_application_queries(self):
        for method in ("post", "put", "patch", "delete", "head", "options"):
            request = getattr(self.factory, method)(URL)
            for view, args in ((api.eligibility_applications, ()), (api.eligibility_application_detail, (42,))):
                response = view(request, *args)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET")
        self.assertEqual(self.cursor.executed, [])
        self.assertTrue(all(not guard.called for guard in self.guards))

    def test_database_failure_is_retryable_and_does_not_leak_sql(self):
        with patch.object(self.cursor, "execute", side_effect=DatabaseError("private database details")), self.assertLogs(api.logger, level="ERROR"):
            for application_id in (None, 42):
                response, data = self.get(application_id=application_id)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(data, {"error": api.UNAVAILABLE})

    @override_settings(DEBUG=False, ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=False)
    def test_read_access_does_not_activate_workplace_screening_or_require_upgrades(self):
        self.assertFalse(form.workplace_enabled())
        self.assertEqual(self.get()[0].status_code, 200)
        self.assertTrue(all(not guard.called for guard in self.guards))

    def test_routes_are_protected_and_not_added_to_public_exemptions(self):
        self.assertEqual(resolve(URL).func, api.eligibility_applications)
        self.assertEqual(resolve(URL + "42/").func, api.eligibility_application_detail)
        self.assertIsNotNone(rule_for(URL))
        self.assertIsNotNone(rule_for(URL + "42/"))
        # Exercise real URL routing/middleware as well as the direct view tests.
        self.account = None
        with patch("login.sessions.authenticate_request", return_value=None):
            self.assertEqual(self.client.get(URL).status_code, 401)
