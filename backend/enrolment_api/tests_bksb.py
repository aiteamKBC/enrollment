import json
import os
from contextlib import nullcontext
from unittest.mock import patch
from types import SimpleNamespace

from django.conf import settings
from django.test import Client, RequestFactory, SimpleTestCase

from . import bksb


class _Response:
    status_code = 200
    content = b"true"

    def json(self):
        return True


class _JsonResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = b"{}"

    def json(self):
        return self.payload


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return _Response()


class BksbHelpersTests(SimpleTestCase):
    def test_status_separates_dashboard_access_from_assessment_completion(self):
        row = SimpleNamespace(
            subject="english",
            status="not_started",
            bksb_user_id=72459167,
            course_subject="ENG_NEW",
            course_name="Functional Skills English",
            result=None,
            level=None,
            assessment_date=None,
            last_synced_at=None,
            last_error=None,
            attempts=[],
        )
        payload = bksb._status_json(row)
        self.assertEqual(payload["accessStatus"], "ready")
        self.assertTrue(payload["bksbAccountReady"])
        self.assertEqual(payload["status"], "not_started")

    def test_status_without_mapping_is_not_ready(self):
        row = SimpleNamespace(
            subject="english",
            status="not_started",
            bksb_user_id=None,
            course_subject=None,
            course_name=None,
            result=None,
            level=None,
            assessment_date=None,
            last_synced_at=None,
            last_error=None,
            attempts=[],
        )
        payload = bksb._status_json(row)
        self.assertEqual(payload["accessStatus"], "not_ready")
        self.assertFalse(payload["bksbAccountReady"])

    def test_vite_3002_origins_are_trusted_for_csrf(self):
        self.assertIn("http://127.0.0.1:3002", settings.CSRF_TRUSTED_ORIGINS)
        self.assertIn("http://localhost:3002", settings.CSRF_TRUSTED_ORIGINS)

    def test_vite_3002_origin_with_enrollment_csrf_cookie_is_accepted(self):
        """Exercise Django's real middleware, including the custom cookie name."""
        client = Client(enforce_csrf_checks=True)
        learner = SimpleNamespace()
        row = SimpleNamespace(pk=1, bksb_user_id=None, save=lambda **kwargs: None)
        with patch.dict("os.environ", {"API_REQUIRE_AUTH": "0", "ENROLMENT_API_REQUIRE_AUTH": "0", "BKSB_ENABLED": "0"}, clear=False), patch.object(
            bksb, "_learner", return_value=learner
        ), patch.object(
            bksb.BksbClient, "licensed_courses", return_value=[]
        ):
            bootstrap = client.get("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/")
        self.assertEqual(bootstrap.status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value

        with patch.dict("os.environ", {"API_REQUIRE_AUTH": "0", "ENROLMENT_API_REQUIRE_AUTH": "0", "BKSB_AUTO_LOGIN_ALLOWED_HOSTS": "login.example.test"}, clear=False), patch.object(
            bksb.BksbClient, "licensed_courses", return_value=[{"CourseSubject": "ENG_NEW", "CourseName": "English", "IsLicensed": True}]
        ), patch.object(
            bksb, "_get_or_create_row", return_value=row
        ), patch.object(bksb, "_learner", return_value=learner), patch.object(
            bksb, "_provision_account_for_auto_login", side_effect=lambda current, *args: (setattr(current, "bksb_user_id", 72459166) or (current, 72459166))
        ), patch.object(
            bksb, "_ensure_enrolled", return_value="ENG_NEW"
        ), patch.object(
            bksb.BksbInitialAssessment.objects, "select_for_update", return_value=SimpleNamespace(get=lambda **kwargs: row)
        ), patch.object(bksb.transaction, "atomic", return_value=nullcontext()), patch.object(
            bksb.BksbClient, "auto_login_link", return_value={"Link": "https://login.example.test/one-time"}
        ):
            response = client.post(
                "/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/",
                HTTP_ORIGIN="http://localhost:3002",
                HTTP_X_CSRFTOKEN=token,
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(response.status_code, 200)

    def test_auto_login_endpoint_requires_authentication(self):
        request = RequestFactory().post("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/")
        with patch.dict("os.environ", {"ENROLMENT_API_REQUIRE_AUTH": "1"}, clear=False):
            response = bksb.auto_login_account(request, "apprenticeship", 1601)
        self.assertEqual(response.status_code, 401)

    def test_auto_login_endpoint_requires_csrf_before_provider_call(self):
        request = RequestFactory().post("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/")
        request._dont_enforce_csrf_checks = False
        with patch.dict("os.environ", {"ENROLMENT_API_REQUIRE_AUTH": "0"}, clear=False), patch.object(
            bksb.BksbClient, "auto_login_link", side_effect=AssertionError("provider must not be called")
        ):
            response = bksb.auto_login_account(request, "apprenticeship", 1601)
        self.assertEqual(response.status_code, 403)

    def test_auto_login_endpoint_returns_only_validated_link_and_no_store_headers(self):
        row = SimpleNamespace(pk=1, bksb_user_id=None, save=lambda **kwargs: None)
        learner = SimpleNamespace()
        request = RequestFactory().post("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/")
        request._dont_enforce_csrf_checks = True
        with patch.dict("os.environ", {"ENROLMENT_API_REQUIRE_AUTH": "0", "BKSB_AUTO_LOGIN_ALLOWED_HOSTS": "login.example.test"}, clear=False), patch.object(
            bksb.BksbClient, "licensed_courses", return_value=[{"CourseSubject": "ENG_NEW", "CourseName": "English", "IsLicensed": True}]
        ), patch.object(
            bksb, "_get_or_create_row", return_value=row
        ), patch.object(bksb, "_learner", return_value=learner), patch.object(
            bksb, "_provision_account_for_auto_login", side_effect=lambda current, *args: (setattr(current, "bksb_user_id", 72459166) or (current, 72459166))
        ), patch.object(
            bksb, "_ensure_enrolled", return_value="ENG_NEW"
        ), patch.object(
            bksb.BksbInitialAssessment.objects, "select_for_update", return_value=SimpleNamespace(get=lambda **kwargs: row)
        ), patch.object(bksb.transaction, "atomic", return_value=nullcontext()), patch.object(
            bksb.BksbClient, "auto_login_link", return_value={"Token": "secret", "Link": "https://login.example.test/one-time", "Expiry": "soon"}
        ):
            response = bksb.auto_login_account(request, "apprenticeship", 1601)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {"url": "https://login.example.test/one-time", "expiresAt": "soon"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_auto_login_provider_failure_is_sanitised(self):
        row = SimpleNamespace(pk=1, bksb_user_id=72459166)
        request = RequestFactory().post("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/")
        request._dont_enforce_csrf_checks = True
        with patch.dict("os.environ", {"ENROLMENT_API_REQUIRE_AUTH": "0", "BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"}, clear=False), patch.object(
            bksb.BksbClient, "licensed_courses", return_value=[{"CourseSubject": "ENG_NEW", "CourseName": "English", "IsLicensed": True}]
        ), patch.object(
            bksb, "_get_or_create_row", return_value=row
        ), patch.object(
            bksb, "_provision_account_for_auto_login", side_effect=bksb.BksbError("BKSB returned HTTP 503.")
        ), patch.object(bksb, "_learner", return_value=SimpleNamespace()):
            response = bksb.auto_login_account(request, "apprenticeship", 1601)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.content), {"error": "BKSB returned HTTP 503."})

    def test_auto_login_suppression_failure_returns_reconciliation_error(self):
        row = SimpleNamespace(pk=1, bksb_user_id=72459169)
        request = RequestFactory().post("/enrolment_api/wizard/apprenticeship/1601/initial-assessments/bksb-account/")
        request._dont_enforce_csrf_checks = True
        message = "BKSB account was created, but first-login password-change suppression failed; manual reconciliation is required."
        with patch.dict("os.environ", {"ENROLMENT_API_REQUIRE_AUTH": "0", "BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"}, clear=False), patch.object(
            bksb.BksbClient, "licensed_courses", return_value=[{"CourseSubject": "ENG_NEW", "CourseName": "English", "IsLicensed": True}]
        ), patch.object(
            bksb, "_get_or_create_row", return_value=row
        ), patch.object(
            bksb, "_provision_account_for_auto_login", side_effect=bksb.BksbError(message)
        ), patch.object(bksb, "_learner", return_value=SimpleNamespace()):
            response = bksb.auto_login_account(request, "apprenticeship", 1601)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.content), {"error": message})

    def test_auto_login_client_uses_documented_read_only_endpoint(self):
        client = bksb.BksbClient.__new__(bksb.BksbClient)
        calls = []
        client._request = lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {
            "UserId": 72459166, "Token": "do-not-log", "Link": "https://login.example.test/a", "Expiry": "2026-10-31T12:00:00Z"
        }
        result = client.auto_login_link(72459166)
        self.assertEqual(result["UserId"], 72459166)
        self.assertEqual(calls, [("GET", "api/users/user/72459166/getAutoLoginLink", {})])

    def test_create_user_sends_a_unique_high_entropy_password_not_a_default(self):
        client = bksb.BksbClient.__new__(bksb.BksbClient)
        payloads = []

        def request(method, path, **kwargs):
            self.assertEqual((method, path), ("POST", "api/users/createUser"))
            payloads.append(kwargs["payload"])
            return {"UserId": len(payloads), "Username": kwargs["payload"]["Username"]}

        client._request = request
        first = client.create_user(
            username="enrolment-apprenticeship-1601",
            first_name="Test",
            last_name="Learner",
            email="learner@example.test",
            dob=None,
        )
        second = client.create_user(
            username="enrolment-apprenticeship-1602",
            first_name="Test",
            last_name="Learner",
            email="learner2@example.test",
            dob=None,
        )

        first_password = payloads[0]["Password"]
        second_password = payloads[1]["Password"]
        self.assertEqual(first["UserId"], 1)
        self.assertEqual(second["UserId"], 2)
        self.assertNotEqual(first_password, second_password)
        self.assertGreaterEqual(len(first_password), 32)
        self.assertTrue(any(char in bksb._PASSWORD_LOWER for char in first_password))
        self.assertTrue(any(char in bksb._PASSWORD_UPPER for char in first_password))
        self.assertTrue(any(char in bksb._PASSWORD_DIGITS for char in first_password))
        self.assertTrue(any(char in bksb._PASSWORD_SYMBOLS for char in first_password))
        self.assertNotEqual(first_password, "password")
        self.assertNotIn("Password", first)

    def test_remove_request_change_password_uses_documented_post_without_body(self):
        client = bksb.BksbClient.__new__(bksb.BksbClient)
        calls = []
        client._request = lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {"Status": "Success"}

        self.assertEqual(client.remove_request_change_password(72459166), {"Status": "Success"})
        self.assertEqual(
            calls,
            [("POST", "api/users/user/72459166/removeRequestChangePassword", {})],
        )

    def test_auto_login_url_requires_explicit_https_allowlist(self):
        with patch.dict("os.environ", {"BKSB_AUTO_LOGIN_ALLOWED_HOSTS": "login.example.test"}, clear=False):
            self.assertEqual(
                bksb._validated_auto_login_url("https://login.example.test/path?token=redacted"),
                "https://login.example.test/path?token=redacted",
            )
            with self.assertRaises(bksb.BksbError):
                bksb._validated_auto_login_url("http://login.example.test/path")
            with self.assertRaises(bksb.BksbError):
                bksb._validated_auto_login_url("https://evil.example.test/path")

    def test_auto_login_token_is_not_written_to_diagnostic_log(self):
        class Response:
            status_code = 200
            content = b'{"Token":"secret-token","Link":"https://login.example.test/a"}'

            def json(self):
                return {"Token": "secret-token", "Link": "https://login.example.test/a"}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def request(self, *args, **kwargs):
                return Response()

        with patch.dict("os.environ", {"BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"}, clear=False), patch(
            "enrolment_api.bksb.httpx.Client", Client
        ), patch("enrolment_api.bksb.logger.info") as info:
            payload = bksb.BksbClient().auto_login_link(72459166)
        rendered = " ".join(str(call) for call in info.call_args_list)
        self.assertEqual(payload["Link"], "https://login.example.test/a")
        self.assertNotIn("secret-token", rendered)

    def test_auto_login_diagnostic_operation_does_not_include_user_id_or_query(self):
        self.assertEqual(
            bksb._safe_operation("GET", "api/users/user/72459166/getAutoLoginLink?token=secret"),
            "GET api/users/user/:id/getAutoLoginLink",
        )

    def test_v5_user_payload_prefers_integer_legacy_id_over_guid(self):
        self.assertEqual(
            bksb._legacy_user_id({
                "UserId": "0183b4a3-06ab-40cf-bac2-42a7a695fcc6",
                "LegacyUserId": 42,
            }),
            42,
        )
        self.assertIsNone(bksb._legacy_user_id("0183b4a3-06ab-40cf-bac2-42a7a695fcc6"))

    def test_direct_username_lookup_accepts_integer_and_treats_only_404_as_missing(self):
        client = bksb.BksbClient.__new__(bksb.BksbClient)
        client._request = lambda *args, **kwargs: {"UserId": 72459166}
        self.assertEqual(client.user_id_for_username("enrolment-apprenticeship-1601"), 72459166)

        client._request = lambda *args, **kwargs: (_ for _ in ()).throw(bksb.BksbError("BKSB returned HTTP 404."))
        self.assertIsNone(client.user_id_for_username("missing-user"))

        client._request = lambda *args, **kwargs: (_ for _ in ()).throw(bksb.BksbError("BKSB returned HTTP 403."))
        with self.assertRaises(bksb.BksbError):
            client.user_id_for_username("forbidden-user")

    def test_v5_session_ids_and_types_are_not_treated_as_legacy(self):
        session = {
            "SessionId": "4b929a2b-5650-4f24-bf9d-b4ae686429a3",
            "UserId": "0cdd7126-96e4-4eb4-ae1e-dd0660491803",
            "LegacyUserId": 42,
            "AssessmentType": 1,
            "SessionStatus": 2,
        }
        self.assertTrue(bksb._is_initial_session(session))
        self.assertFalse(bksb._session_complete(session))
        self.assertFalse(bksb._session_void(session))
        self.assertEqual(bksb._v5_user_id(session, 42), session["UserId"])
        self.assertFalse(bksb._is_initial_session({"AssessmentType": "DiagnosticAssessment"}))
        self.assertTrue(bksb._session_void({"SessionStatus": 3}))

    def test_result_label_does_not_become_a_level_without_documented_level(self):
        result, level, _ = bksb._result_value({"Result": "Pass"})
        self.assertEqual(result, "Pass")
        self.assertIsNone(level)

    def test_only_explicit_categorical_provider_levels_are_extracted(self):
        self.assertEqual(bksb._categorical_level("Level 2"), "Level 2")
        self.assertEqual(bksb._categorical_level("Entry 3"), "Entry 3")
        self.assertIsNone(bksb._categorical_level("2.6"))
        self.assertIsNone(bksb._categorical_level("Pass"))

    def test_overall_level_uses_parent_v5_report_row_only(self):
        report = {
            "AssessmentEOAData": [
                {"IsParent": False, "AbilityMeasurement": 2.6, "AbilityMeasurementName": "Level 2"},
                {"IsParent": True, "AbilityMeasurement": 2.6, "AbilityMeasurementName": "Level 2"},
            ]
        }
        self.assertEqual(bksb._overall_level_from_report(report), "Level 2")
        self.assertIsNone(bksb._overall_level_from_report({
            "AssessmentEOAData": [{"IsParent": True, "AbilityMeasurement": 2.6}]
        }))

    def test_course_boundaries_map_only_strictly_interior_measurements(self):
        levels = {
            "CourseSubject": "ENG_NEW",
            "IsLicensed": True,
            "Levels": [
                {"LevelName": "Entry 2", "LevelShortName": "E2", "LowerLevelBoundary": "-1", "UpperLevelBoundary": "0"},
                {"LevelName": "Level 1", "LevelShortName": "L1", "LowerLevelBoundary": "0", "UpperLevelBoundary": "1.95"},
                {"LevelName": "Level 2", "LevelShortName": "L2", "LowerLevelBoundary": "1.95", "UpperLevelBoundary": "3"},
            ],
        }
        for measurement, expected in (("-0.13", "Entry 2"), ("1.93", "Level 1"), ("1.97", "Level 2"), ("2.6", "Level 2")):
            self.assertEqual(
                bksb._map_measurement_to_course_level(levels, measurement, expected_course_subject="ENG_NEW"),
                (expected, "matched"),
            )
        self.assertEqual(
            bksb._map_measurement_to_course_level(levels, "1.95", expected_course_subject="ENG_NEW")[1],
            "boundary_inclusivity_unknown",
        )

    def test_course_boundary_validation_rejects_missing_overlap_gap_wrong_course_and_unlicensed(self):
        base = {
            "CourseSubject": "ENG_NEW",
            "IsLicensed": True,
            "Levels": [{"LevelName": "Level 1", "LowerLevelBoundary": "0", "UpperLevelBoundary": "2"}],
        }
        self.assertEqual(bksb._map_measurement_to_course_level(base, "1", expected_course_subject="MATHS_NEW")[1], "wrong_course")
        self.assertEqual(bksb._map_measurement_to_course_level({**base, "IsLicensed": False}, "1", expected_course_subject="ENG_NEW")[1], "course_not_licensed")
        self.assertEqual(bksb._map_measurement_to_course_level({**base, "Levels": []}, "1", expected_course_subject="ENG_NEW")[1], "missing_levels")
        overlap = {**base, "Levels": [
            {"LevelName": "Entry 3", "LowerLevelBoundary": "-1", "UpperLevelBoundary": "1.1"},
            {"LevelName": "Level 1", "LowerLevelBoundary": "1", "UpperLevelBoundary": "2"},
        ]}
        self.assertEqual(bksb._map_measurement_to_course_level(overlap, "1.05", expected_course_subject="ENG_NEW")[1], "boundary_overlap")
        gap = {**base, "Levels": [
            {"LevelName": "Entry 3", "LowerLevelBoundary": "-1", "UpperLevelBoundary": "0"},
            {"LevelName": "Level 1", "LowerLevelBoundary": "1", "UpperLevelBoundary": "2"},
        ]}
        self.assertEqual(bksb._map_measurement_to_course_level(gap, "0.5", expected_course_subject="ENG_NEW")[1], "boundary_gap")

    def test_overall_measurement_ignores_components_and_rejects_conflicts(self):
        report = {"AssessmentEOAData": [
            {"IsParent": False, "AbilityMeasurement": "2.6"},
            {"IsParent": True, "AbilityMeasurement": "2.6"},
        ]}
        self.assertEqual(bksb._overall_measurement_from_report(report), bksb.Decimal("2.6"))
        self.assertIsNone(bksb._overall_measurement_from_report({"AssessmentEOAData": [
            {"IsParent": True, "AbilityMeasurement": "1.9"},
            {"IsParent": True, "AbilityMeasurement": "2.1"},
        ]}))

    def test_course_level_v5_client_method_is_read_only(self):
        calls = []

        class Client(_Client):
            def request(self, method, path, **kwargs):
                calls.append((method, path, kwargs.get("params")))
                return _JsonResponse({"CourseSubject": "ENG_NEW", "IsLicensed": True, "Levels": []})

        with patch.dict("os.environ", {"BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"}, clear=False), patch(
            "enrolment_api.bksb.httpx.Client", side_effect=lambda **kwargs: Client(**kwargs)
        ):
            self.assertEqual(bksb.BksbClient().course_level_v5("ENG_NEW")["CourseSubject"], "ENG_NEW")
        self.assertEqual(calls, [("GET", "api/courses/courseLevelV5", {"courseSubject": "ENG_NEW"})])

    def test_status_sync_preserves_a_failed_launch_error_without_provider_mapping(self):
        row = bksb.BksbInitialAssessment(
            status=bksb.STATUS_ERROR,
            last_error="BKSB returned HTTP 403.",
            attempts=[],
        )
        row.save = lambda **kwargs: None
        bksb._sync_row(row, object(), {
            "CourseSubject": "ENG_NEW",
            "CourseName": "Functional Skills English",
            "IsReforms": True,
        })
        self.assertEqual(row.status, bksb.STATUS_ERROR)
        self.assertEqual(row.last_error, "BKSB returned HTTP 403.")

    def test_status_sync_preserves_a_failed_launch_error_with_provider_mapping(self):
        class Client:
            def v5_sessions(self, user_id, subject):
                return []

            def results(self, user_id, subject):
                return []

        row = bksb.BksbInitialAssessment(
            bksb_user_id=72459166,
            course_subject="ENG_NEW",
            course_is_reforms=True,
            status=bksb.STATUS_ERROR,
            last_error="BKSB returned HTTP 502.",
            attempts=[],
        )
        row.save = lambda **kwargs: None
        bksb._sync_row(row, Client(), {"CourseSubject": "ENG_NEW", "IsReforms": True})
        self.assertEqual(row.status, bksb.STATUS_ERROR)
        self.assertEqual(row.last_error, "BKSB returned HTTP 502.")

    def test_failed_launch_reconciliation_preserves_an_existing_provider_user(self):
        class Client:
            def user_id_for_username(self, username):
                return 72459166 if username == "enrolment-apprenticeship-1601" else None

            def user(self, user_id):
                return {"UserId": user_id, "Username": "enrolment-apprenticeship-1601", "Email": "learner@example.test"}

        row = bksb.BksbInitialAssessment()
        row.save = lambda **kwargs: None
        with patch.object(bksb, "_learner_identity", return_value={"email": "learner@example.test"}), patch.object(
            bksb, "_candidate_usernames", return_value=["enrolment-apprenticeship-1601"]
        ):
            bksb._reconcile_provider_user_mapping(row, Client(), "apprenticeship", 1601, object())
        self.assertEqual(row.bksb_user_id, 72459166)
        self.assertEqual(row.bksb_username, "enrolment-apprenticeship-1601")

    def test_mapping_survives_create_enrol_then_launch_failure(self):
        """A failed link request must not erase a successful provider mapping."""
        class Client:
            def __init__(self):
                self.course_reads = 0

            def user_id_for_username(self, username):
                return None

            def create_user(self, **kwargs):
                self.created = kwargs
                return {"UserId": 72459166, "Username": "enrolment-apprenticeship-1601"}

            def get_user_courses(self, user_id):
                self.course_reads += 1
                if self.course_reads == 1:
                    return [{"CourseSubject": "ENG_NEW", "IsEnrolled": False}]
                return [{"CourseSubject": "ENG_NEW", "IsEnrolled": True}]

            def enrol_course(self, user_id, course_subject):
                self.enrolled = (user_id, course_subject)

            def initial_session_link(self, user_id, course_subject):
                raise bksb.BksbError("BKSB returned HTTP 502.")

        row = bksb.BksbInitialAssessment()
        row.save = lambda **kwargs: None
        client = Client()
        course = {"CourseSubject": "ENG_NEW", "IsReforms": True}
        with patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "vv", "last_name": "bb", "email": "learner@example.test",
                "dob": None, "username": "", "aptem_id": "",
            }
        ), patch.object(bksb, "_candidate_usernames", return_value=[]):
            user_id = bksb._ensure_provider_user(row, client, "apprenticeship", 1601, object())
            subject = bksb._ensure_enrolled(row, client, user_id, course)
            self.assertEqual((user_id, subject), (72459166, "ENG_NEW"))
            with self.assertRaises(bksb.BksbError):
                client.initial_session_link(user_id, subject)

        self.assertEqual(row.bksb_user_id, 72459166)
        self.assertEqual(row.bksb_username, "enrolment-apprenticeship-1601")
        self.assertEqual(client.enrolled, (72459166, "ENG_NEW"))

    def test_existing_mapping_is_reused_without_create_user(self):
        class Client:
            def user(self, user_id):
                self.looked_up = user_id
                return {"UserId": user_id, "Username": "enrolment-apprenticeship-1601", "Email": "learner@example.test"}

            def create_user(self, **kwargs):
                raise AssertionError("createUser must not run for an existing mapping")

            def remove_request_change_password(self, user_id):
                raise AssertionError("Existing BKSB users must not have password-change requests removed")

        row = bksb.BksbInitialAssessment(bksb_user_id=72459166, bksb_username="enrolment-apprenticeship-1601")
        row.save = lambda **kwargs: None
        client = Client()
        with patch.dict("os.environ", {"BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE": "1"}, clear=False), patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "vv", "last_name": "bb", "email": "learner@example.test",
            }
        ):
            self.assertEqual(
                bksb._ensure_provider_user(row, client, "apprenticeship", 1601, object()),
                72459166,
            )
            bksb._remove_request_change_password_if_enabled(client, 72459166, False)
        self.assertEqual(client.looked_up, 72459166)

    def test_unmapped_subject_reuses_verified_existing_account_without_duplicate(self):
        class Client:
            def user_id_for_username(self, username):
                self.looked_up = username
                return 72459166

            def user(self, user_id):
                return {"UserId": user_id, "Username": "enrolment-apprenticeship-1601", "Email": "learner@example.test"}

            def create_user(self, **kwargs):
                raise AssertionError("A second subject must reuse the existing BKSB account")

        row = bksb.BksbInitialAssessment(subject="maths")
        row.save = lambda **kwargs: None
        client = Client()
        with patch.object(
            bksb, "_learner_identity", return_value={"first_name": "Test", "last_name": "Learner", "email": "learner@example.test"}
        ), patch.object(bksb, "_candidate_usernames", return_value=["enrolment-apprenticeship-1601"]):
            user_id = bksb._ensure_provider_user(row, client, "apprenticeship", 1601, object())
        self.assertEqual(user_id, 72459166)
        self.assertEqual(row.bksb_user_id, 72459166)

    def test_auto_login_provisions_a_new_account_then_verifies_and_enrols_english(self):
        class Client:
            def __init__(self):
                self.course_reads = 0
                self.created = []
                self.enrolments = []
                self.verified = []
                self.events = []

            def user_id_for_username(self, username):
                return None

            def create_user(self, **kwargs):
                self.created.append(kwargs)
                self.events.append("create")
                return {"UserId": 72459167, "Username": "enrolment-apprenticeship-1601"}

            def user(self, user_id):
                self.verified.append(user_id)
                self.events.append("verify")
                return {"UserId": user_id, "Email": "learner@example.test"}

            def get_user_courses(self, user_id):
                self.course_reads += 1
                self.events.append("course_read")
                if self.course_reads == 1:
                    return []
                return [{"CourseSubject": "ENG_NEW", "IsEnrolled": True}]

            def enrol_course(self, user_id, course_subject):
                self.enrolments.append((user_id, course_subject))
                self.events.append("enrol")

            def remove_request_change_password(self, user_id):
                self.suppressed = user_id
                self.events.append("suppress")
                return {"Status": "Success"}

        row = bksb.BksbInitialAssessment(pk=1)
        row.save = lambda **kwargs: None
        client = Client()
        learner = SimpleNamespace()
        course = {"CourseSubject": "ENG_NEW", "CourseName": "Functional Skills English", "IsReforms": True}
        with patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "Test", "last_name": "Learner", "email": "learner@example.test",
                "dob": None, "username": "", "aptem_id": "",
            }
        ), patch.object(bksb, "_candidate_usernames", return_value=[]), patch.object(
            bksb.BksbInitialAssessment.objects, "select_for_update", return_value=SimpleNamespace(get=lambda **kwargs: row)
        ), patch.object(bksb.transaction, "atomic", return_value=nullcontext()):
            provisioned_row, user_id = bksb._provision_account_for_auto_login(
                row, client, "apprenticeship", 1601, learner
            )
            subject = bksb._ensure_enrolled(provisioned_row, client, user_id, course)

        self.assertEqual(user_id, 72459167)
        self.assertEqual(subject, "ENG_NEW")
        self.assertEqual(len(client.created), 1)
        self.assertEqual(client.verified, [72459167])
        self.assertEqual(client.enrolments, [(72459167, "ENG_NEW")])
        self.assertEqual(client.course_reads, 2)
        self.assertEqual(client.suppressed, 72459167)
        self.assertEqual(client.events, ["create", "verify", "suppress", "course_read", "enrol", "course_read"])

    def test_existing_course_enrolment_is_not_written_again(self):
        class Client:
            def get_user_courses(self, user_id):
                return [{"CourseSubject": "ENG_NEW", "IsEnrolled": True}]

            def enrol_course(self, user_id, course_subject):
                raise AssertionError("An existing BKSB enrolment must not be duplicated")

        row = bksb.BksbInitialAssessment()
        self.assertEqual(
            bksb._ensure_enrolled(row, Client(), 72459166, {"CourseSubject": "ENG_NEW"}),
            "ENG_NEW",
        )

    def test_auto_login_provisioning_reconciles_mapping_when_later_step_fails(self):
        row = bksb.BksbInitialAssessment(pk=1, subject="maths")
        recovery = bksb.BksbInitialAssessment(pk=2)
        client = object()
        learner = object()
        row.save = lambda **kwargs: None
        with patch.object(
            bksb.BksbInitialAssessment.objects, "select_for_update", return_value=SimpleNamespace(get=lambda **kwargs: row)
        ), patch.object(bksb.transaction, "atomic", return_value=nullcontext()), patch.object(
            bksb, "_ensure_provider_user", side_effect=bksb.BksbError("BKSB returned HTTP 502.")
        ), patch.object(bksb, "_get_or_create_row", return_value=recovery) as get_or_create, patch.object(
            bksb, "_reconcile_provider_user_mapping"
        ) as reconcile:
            with self.assertRaises(bksb.BksbError):
                bksb._provision_account_for_auto_login(row, client, "apprenticeship", 1601, learner)
        get_or_create.assert_called_once_with("apprenticeship", 1601, "maths")
        reconcile.assert_called_once_with(recovery, client, "apprenticeship", 1601, learner)

    def test_mapping_is_kept_when_optional_password_suppression_fails(self):
        class Client:
            def user_id_for_username(self, username):
                return None

            def create_user(self, **kwargs):
                return {"UserId": 72459168, "Username": "enrolment-apprenticeship-1601"}

            def remove_request_change_password(self, user_id):
                raise bksb.BksbError("BKSB returned HTTP 403.")

            def user(self, user_id):
                return {"UserId": user_id, "Email": "learner@example.test"}

        row = bksb.BksbInitialAssessment(pk=1)
        row.save = lambda **kwargs: None
        client = Client()
        with patch.dict("os.environ", {"BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE": "1"}, clear=False), patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "Test", "last_name": "Learner", "email": "learner@example.test",
                "dob": None, "username": "", "aptem_id": "",
            }
        ), patch.object(bksb, "_candidate_usernames", return_value=[]), patch.object(
            bksb.BksbInitialAssessment.objects, "select_for_update", return_value=SimpleNamespace(get=lambda **kwargs: row)
        ), patch.object(bksb.transaction, "atomic", return_value=nullcontext()), patch.object(
            bksb, "_get_or_create_row", return_value=row
        ):
            with self.assertRaises(bksb.BksbError):
                bksb._provision_account_for_auto_login(row, client, "apprenticeship", 1601, object())
        self.assertEqual(row.bksb_user_id, 72459168)

    def test_password_change_suppression_is_disabled_by_default_for_new_users(self):
        class Client:
            def user_id_for_username(self, username):
                return None

            def create_user(self, **kwargs):
                self.created = kwargs
                return {"UserId": 72459166, "Username": "enrolment-apprenticeship-1601"}

            def remove_request_change_password(self, user_id):
                raise AssertionError("Suppression must remain disabled by default")

        row = bksb.BksbInitialAssessment()
        row.save = lambda **kwargs: None
        client = Client()
        with patch.dict("os.environ", {"BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE": "0"}, clear=False), patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "Test", "last_name": "Learner", "email": "learner@example.test",
                "dob": None, "username": "", "aptem_id": "",
            }
        ), patch.object(bksb, "_candidate_usernames", return_value=[]):
            self.assertEqual(
                bksb._ensure_provider_user(row, client, "apprenticeship", 1601, object()),
                72459166,
            )
        self.assertEqual(row.bksb_user_id, 72459166)

    def test_password_change_suppression_is_called_only_for_new_users_when_enabled(self):
        class Client:
            def user_id_for_username(self, username):
                return None

            def create_user(self, **kwargs):
                return {"UserId": 72459166, "Username": "enrolment-apprenticeship-1601"}

            def remove_request_change_password(self, user_id):
                self.removed = user_id
                return {"Status": "Success"}

        row = bksb.BksbInitialAssessment()
        row.save = lambda **kwargs: None
        client = Client()
        with patch.dict("os.environ", {"BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE": "1"}, clear=False), patch.object(
            bksb, "_learner_identity", return_value={
                "first_name": "Test", "last_name": "Learner", "email": "learner@example.test",
                "dob": None, "username": "", "aptem_id": "",
            }
        ), patch.object(bksb, "_candidate_usernames", return_value=[]):
            self.assertEqual(bksb._ensure_provider_user(row, client, "apprenticeship", 1601, object()), 72459166)
            bksb._remove_request_change_password_if_enabled(client, 72459166, True)
        self.assertEqual(client.removed, 72459166)

    def test_password_change_suppression_requires_explicit_provider_success(self):
        class Client:
            def remove_request_change_password(self, user_id):
                return {"Status": "Failure", "Description": "permission denied"}

        with patch.dict("os.environ", {"BKSB_REMOVE_REQUEST_CHANGE_PASSWORD_ON_CREATE": "1"}, clear=False):
            with self.assertRaisesRegex(bksb.BksbError, "was not confirmed"):
                bksb._remove_request_change_password_if_enabled(Client(), 72459168, True)

    def test_v5_sync_keeps_guid_session_and_does_not_infer_level(self):
        class Client:
            def v5_sessions(self, user_id, subject):
                self.user_id = user_id
                return [{
                    "SessionId": "4b929a2b-5650-4f24-bf9d-b4ae686429a3",
                    "UserId": "0cdd7126-96e4-4eb4-ae1e-dd0660491803",
                    "LegacyUserId": 42,
                    "AssessmentType": 1,
                    "SessionStatus": 1,
                    "DateCompleted": "2026-10-31T10:00:00Z",
                }]

            def results(self, user_id, subject):
                return [{"SessionId": "4b929a2b-5650-4f24-bf9d-b4ae686429a3", "Result": "Pass"}]

        row = bksb.BksbInitialAssessment(
            bksb_user_id=42,
            course_subject="ENG_NEW",
            course_is_reforms=True,
            attempts=[],
        )
        row.save = lambda **kwargs: None
        client = Client()
        bksb._sync_row(row, client, {
            "CourseSubject": "ENG_NEW",
            "CourseName": "Functional Skills English",
            "IsReforms": True,
        })
        self.assertEqual(client.user_id, 42)
        self.assertEqual(row.provider_session_id, "4b929a2b-5650-4f24-bf9d-b4ae686429a3")
        self.assertEqual(row.status, bksb.STATUS_COMPLETED)
        self.assertEqual(row.result, "Pass")
        self.assertIsNone(row.level)
        self.assertEqual(row.attempts[0]["userId"], "0cdd7126-96e4-4eb4-ae1e-dd0660491803")

    def test_completed_session_without_result_evidence_is_not_marked_completed(self):
        class Client:
            def v5_sessions(self, user_id, subject):
                return [{
                    "SessionId": "v5-session",
                    "UserId": "v5-user",
                    "AssessmentType": 1,
                    "SessionStatus": 1,
                    "DateCompleted": "2026-11-01T10:00:00Z",
                }]

            def results(self, user_id, subject):
                return []

            def report_data(self, user_id, session_id, *, reforms):
                return {}

        row = bksb.BksbInitialAssessment(
            bksb_user_id=42,
            course_subject="ENG_NEW",
            course_is_reforms=True,
            attempts=[],
        )
        row.save = lambda **kwargs: None
        bksb._sync_row(row, Client(), {"CourseSubject": "ENG_NEW", "IsReforms": True})
        self.assertEqual(row.status, bksb.STATUS_IN_PROGRESS)

    def test_v5_report_data_is_completion_evidence_when_result_collection_lags(self):
        class Client:
            def v5_sessions(self, user_id, subject):
                return [{
                    "SessionId": "v5-session",
                    "UserId": "v5-user",
                    "AssessmentType": 1,
                    "SessionStatus": 1,
                    "DateCompleted": "2026-11-01T10:00:00Z",
                }]

            def results(self, user_id, subject):
                return []

            def report_data(self, user_id, session_id, *, reforms):
                return {"AssessmentEOAData": [{"IsParent": True, "AbilityMeasurement": 2.1}]}

            def course_level_v5(self, subject):
                return {
                    "CourseSubject": subject,
                    "IsLicensed": True,
                    "Levels": [
                        {"LevelName": "Entry 3", "LowerLevelBoundary": "-1", "UpperLevelBoundary": "2"},
                        {"LevelName": "Level 2", "LowerLevelBoundary": "2", "UpperLevelBoundary": "3"},
                    ],
                }

        row = bksb.BksbInitialAssessment(
            bksb_user_id=42,
            course_subject="ENG_NEW",
            course_is_reforms=True,
            attempts=[],
        )
        row.save = lambda **kwargs: None
        bksb._sync_row(row, Client(), {"CourseSubject": "ENG_NEW", "IsReforms": True})
        self.assertEqual(row.status, bksb.STATUS_COMPLETED)
        self.assertEqual(row.level, "Level 2")

    def test_result_from_an_older_session_does_not_complete_new_session(self):
        class Client:
            def v5_sessions(self, user_id, subject):
                return [{
                    "SessionId": "new-session",
                    "UserId": "v5-user",
                    "AssessmentType": 1,
                    "SessionStatus": 2,
                    "DateStarted": "2026-11-01T10:00:00Z",
                }]

            def results(self, user_id, subject):
                return [{
                    "SessionId": "old-session",
                    "Result": "Pass",
                    "ResultDate": "2026-10-31T10:00:00Z",
                }]

        row = bksb.BksbInitialAssessment(
            bksb_user_id=42,
            course_subject="ENG_NEW",
            course_is_reforms=True,
            attempts=[],
        )
        row.save = lambda **kwargs: None
        bksb._sync_row(row, Client(), {"CourseSubject": "ENG_NEW", "IsReforms": True})
        self.assertEqual(row.status, bksb.STATUS_IN_PROGRESS)

    def test_course_discovery_requires_a_licensed_subject(self):
        course = {"IsLicensed": True, "CourseSubject": "Functional Skills English", "CourseName": "English"}
        self.assertEqual(bksb._course_subject(bksb._course_for_subject([course], "english")), "Functional Skills English")
        with self.assertRaises(bksb.BksbError):
            bksb._course_for_subject([], "maths")

    def test_unlicensed_course_is_not_selected(self):
        course = {"IsLicensed": False, "CourseSubject": "Maths"}
        with self.assertRaises(bksb.BksbError):
            bksb._course_for_subject([course], "maths")

    def test_reformed_courses_keep_the_provider_subject_codes(self):
        courses = [
            {"CourseName": "Functional Skills English", "CourseSubject": "ENG_NEW", "IsLicensed": True, "IsReforms": True},
            {"CourseName": "Functional Skills Maths", "CourseSubject": "MATHS_NEW", "IsLicensed": True, "IsReforms": True},
        ]
        self.assertEqual(bksb._course_subject(bksb._course_for_subject(courses, "english")), "ENG_NEW")
        self.assertEqual(bksb._course_subject(bksb._course_for_subject(courses, "maths")), "MATHS_NEW")

    def test_reformed_direct_launch_is_opt_in_for_both_subjects(self):
        courses = [
            {"CourseSubject": "ENG_NEW", "IsLicensed": True},
            {"CourseSubject": "MATHS_NEW", "IsLicensed": True},
        ]
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("BKSB_ENABLE_DIRECT_LAUNCH_ENG_NEW", None)
            os.environ.pop("BKSB_ENABLE_DIRECT_LAUNCH_MATHS_NEW", None)
            self.assertFalse(bksb._direct_launch_enabled(courses[0]))
            self.assertFalse(bksb._direct_launch_enabled(courses[1]))
        with patch.dict("os.environ", {"BKSB_ENABLE_DIRECT_LAUNCH_MATHS_NEW": "1"}, clear=False):
            self.assertTrue(bksb._direct_launch_enabled(courses[1]))

    def test_v5_session_result_and_report_requests_are_read_only(self):
        calls = []

        class Client(_Client):
            def request(self, method, path, **kwargs):
                calls.append((method, path, kwargs.get("params")))
                payloads = {
                    "api/assessmentSessions/getAssessmentSessionsForCourseV5": [{"SessionId": "v5-session"}],
                    "api/results/initialAssessment/42/bySubject": {"InitialAssessmentResults": [{"Result": "Pass", "Level": "Entry 3"}]},
                    "api/assessmentSessions/GetEOAReportDataForAssessmentSessionV5": {"Result": "Pass"},
                    "api/assessmentSessions/getEOAReportLinkForSession": {"Link": "https://example.test/report"},
                    "api/assessmentSessions/getInitialAssessmentSessionLinkForCourse": {"Link": "https://example.test/assessment"},
                }
                return _JsonResponse(payloads[path])

        with patch.dict(
            "os.environ",
            {"BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"},
            clear=False,
        ), patch("enrolment_api.bksb.httpx.Client", side_effect=lambda **kwargs: Client(**kwargs)):
            client = bksb.BksbClient()
            self.assertEqual(client.v5_sessions(42, "ENG_NEW")[0]["SessionId"], "v5-session")
            self.assertEqual(client.results(42, "ENG_NEW")[0]["Result"], "Pass")
            self.assertEqual(client.report_data(42, "v5-session", reforms=True)["Result"], "Pass")
            self.assertEqual(client.report_link(42, "v5-session")["Link"], "https://example.test/report")
            self.assertEqual(client.initial_session_link(42, "ENG_NEW")["Link"], "https://example.test/assessment")

        self.assertEqual([method for method, _, _ in calls], ["GET"] * 5)
        self.assertEqual([path for _, path, _ in calls], [
            "api/assessmentSessions/getAssessmentSessionsForCourseV5",
            "api/results/initialAssessment/42/bySubject",
            "api/assessmentSessions/GetEOAReportDataForAssessmentSessionV5",
            "api/assessmentSessions/getEOAReportLinkForSession",
            "api/assessmentSessions/getInitialAssessmentSessionLinkForCourse",
        ])
        self.assertEqual(calls[-1][2], {"courseSubject": "ENG_NEW", "userId": 42})

    def test_legacy_and_v5_completion_statuses_are_normalised(self):
        self.assertTrue(bksb._session_complete({"SessionStatus": "Complete"}))
        self.assertTrue(bksb._session_complete({"SessionStatus": 1}))
        self.assertFalse(bksb._session_complete({"SessionStatus": "InProgress"}))
        self.assertTrue(bksb._session_complete({"SessionStatus": 0, "DateCompleted": "2026-10-01T10:00:00Z"}))

    def test_attempt_history_deduplicates_provider_sessions(self):
        attempts = bksb._attempts([
            {"SessionId": 10, "SessionStatus": 0, "DateStarted": "2026-10-01"},
            {"SessionId": 10, "SessionStatus": "Complete", "DateStarted": "2026-10-01", "DateCompleted": "2026-10-02"},
            {"SessionId": "v5-session", "SessionStatus": "InProgress"},
        ])
        self.assertEqual({item["sessionId"] for item in attempts}, {"10", "v5-session"})
        self.assertEqual(next(item for item in attempts if item["sessionId"] == "10")["status"], "complete")

    def test_attempt_history_keeps_v5_guid_user_id_for_future_reads(self):
        attempts = bksb._attempts([{
            "SessionId": "4b929a2b-5650-4f24-bf9d-b4ae686429a3",
            "UserId": "0cdd7126-96e4-4eb4-ae1e-dd0660491803",
            "SessionStatus": 2,
        }])
        row = bksb.BksbInitialAssessment(attempts=attempts)
        self.assertEqual(
            bksb._stored_v5_user_id(row),
            "0cdd7126-96e4-4eb4-ae1e-dd0660491803",
        )

    def test_identity_matching_never_links_a_different_email(self):
        identity = {"email": "learner@example.test", "first_name": "Test", "last_name": "Learner"}
        self.assertTrue(bksb._identity_matches({"Email": "learner@example.test"}, identity))
        self.assertFalse(bksb._identity_matches({"Email": "someone-else@example.test"}, identity))

    def test_client_uses_basic_auth_and_never_requires_frontend_credentials(self):
        with patch.dict(
            "os.environ",
            {"BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"},
            clear=False,
        ), patch("enrolment_api.bksb.httpx.Client", side_effect=lambda **kwargs: _Client(**kwargs)) as factory:
            self.assertTrue(bksb.BksbClient().test_connection())
            kwargs = factory.call_args.kwargs
            self.assertEqual(kwargs["auth"], ("access", "secret"))
            self.assertNotIn("access", str({"response": True}))

    def test_provider_diagnostics_do_not_log_query_values(self):
        with patch.dict(
            "os.environ",
            {"BKSB_ENABLED": "1", "BKSB_ACCESS_KEY_ID": "access", "BKSB_SECRET_ACCESS_KEY": "secret"},
            clear=False,
        ), patch("enrolment_api.bksb.httpx.Client", side_effect=lambda **kwargs: _Client(**kwargs)), patch(
            "enrolment_api.bksb.logger.info"
        ) as info:
            bksb.BksbClient().username_exists("learner@example.test")

        rendered = "\n".join(
            call.args[0] % call.args[1:]
            for call in info.call_args_list
        )
        self.assertIn("GET api/users/isUsernameExists -> HTTP 200", rendered)
        self.assertNotIn("learner@example.test", rendered)

    def test_provider_diagnostics_remove_path_identifiers(self):
        self.assertEqual(
            bksb._safe_operation("GET", "api/users/user/72459166"),
            "GET api/users/user/:id",
        )
        self.assertEqual(
            bksb._safe_operation("GET", "api/results/initialAssessment/72459166/bySubject?courseSubject=ENG_NEW"),
            "GET api/results/initialAssessment/:id/bySubject",
        )

    def test_disabled_client_fails_closed(self):
        with patch.dict("os.environ", {"BKSB_ENABLED": "0", "BKSB_ACCESS_KEY_ID": "", "BKSB_SECRET_ACCESS_KEY": ""}, clear=False):
            with self.assertRaises(bksb.BksbNotConfigured):
                bksb.BksbClient().licensed_courses()
