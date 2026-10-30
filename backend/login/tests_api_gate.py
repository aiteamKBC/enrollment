"""Tests for the API gate (``login.api_gate``).

These live in the ``login`` suite deliberately. ``config/settings.py`` turns the
gate off for ``manage.py test`` so the curriculum/audit/engagement suites --
which predate any authentication on those endpoints -- keep passing; these tests
turn it back on explicitly, so the gate is still covered rather than merely
disabled.

Nothing here reaches the database. An anonymous request has no ``kbc_session``
cookie and ``resolve_session`` short-circuits on a falsy token; the role tests
use a stub account rather than a row, because the gate reads nothing from one
but ``.role``. Hence ``SimpleTestCase``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase

from . import api_gate
from .api_gate import (
    ANY,
    RULES,
    STAFF,
    ApiSessionGateMiddleware,
    is_gated,
    refusal_for,
    rule_for,
)


class FailureLoggingTests(SimpleTestCase):
    """A broken auth backend must not drown the log it is diagnosed from.

    The gate runs on every request to a gated prefix, so a failure inside it
    arrives once per request. Logging each one with its traceback buries the
    first -- the only one that says what broke -- under thousands of copies of
    itself, at exactly the moment somebody is scrolling back to find it.
    """

    def setUp(self):
        super().setUp()
        api_gate._log_state.clear()
        self.addCleanup(api_gate._log_state.clear)

    @staticmethod
    def _fail(times, key="test"):
        """Drive ``times`` failures through the throttle, as the gate would."""
        for _ in range(times):
            try:
                raise RuntimeError("the auth backend is not answering")
            except RuntimeError:
                api_gate._log_failure(key, "Could not resolve something")

    def _rewind(self, key):
        """Move a key's last-logged time back past the interval."""
        last, suppressed = api_gate._log_state[key]
        api_gate._log_state[key] = (
            last - api_gate.LOG_INTERVAL_SECONDS - 1,
            suppressed,
        )

    def test_a_storm_of_failures_writes_one_traceback(self):
        with self.assertLogs("login", level="ERROR") as captured:
            self._fail(50)

        self.assertEqual(len(captured.records), 1)
        # Still a traceback, not a bare line: the one that gets through has to
        # carry the diagnosis.
        self.assertIsNotNone(captured.records[0].exc_info)

    def test_the_suppressed_failures_are_counted_not_lost(self):
        """Throttling must not turn a wide outage into a quiet one."""
        self._fail(50)
        self._rewind("test")

        with self.assertLogs("login", level="ERROR") as captured:
            self._fail(1)

        self.assertEqual(len(captured.records), 1)
        self.assertIn("49 further occurrence", captured.records[0].getMessage())

    def test_the_count_resets_once_it_has_been_reported(self):
        self._fail(50)
        self._rewind("test")
        self._fail(1)
        self._rewind("test")

        with self.assertLogs("login", level="ERROR") as captured:
            self._fail(1)

        self.assertNotIn("further occurrence", captured.records[0].getMessage())

    def test_one_kind_of_failure_does_not_silence_another(self):
        """The two identities fail for different reasons; both must be visible."""
        with self.assertLogs("login", level="ERROR") as captured:
            self._fail(10, key="session")
            self._fail(10, key="django-user")

        self.assertEqual(len(captured.records), 2)


def _gate_on():
    """The gate forced on, whatever the run's environment says."""
    return mock.patch.dict("os.environ", {"API_REQUIRE_AUTH": "1"}, clear=False)


def _account(role):
    """The gate reads only ``.role``, so a real row would prove nothing extra."""
    return SimpleNamespace(role=role)


class PrefixMatchingTests(SimpleTestCase):
    def test_every_ungated_api_is_ungated_on_purpose(self):
        """The exemptions, asserted so removing one has to be a deliberate act."""
        # Signing in cannot require being signed in.
        self.assertFalse(is_gated("/login_api/login/"))
        self.assertFalse(is_gated("/login_api/health/"))
        # Chat gates itself per-view (IsAuthenticated + participant checks).
        self.assertFalse(is_gated("/api/chat/conversations/"))
        # Django's admin has its own login.
        self.assertFalse(is_gated("/django_admin/"))
        # The calendar OAuth callbacks authenticate on their signed `state`, and
        # arrive as a redirect from Google/Microsoft rather than from the SPA.
        self.assertFalse(is_gated("/api/calendar/google/callback"))
        self.assertFalse(is_gated("/api/calendar/microsoft/callback"))
        # The learner's actual calendar data is elsewhere, and is gated.
        self.assertTrue(is_gated("/learner_api/learners/learner/1/calendar/"))
        # Office Online fetches a preview file with no session; the view checks
        # a signed, expiring token naming one upload.
        self.assertFalse(is_gated("/curriculum_api/office-preview/token/handout.doc"))
        # The upload itself, and the rest of the console, stay gated.
        self.assertTrue(is_gated("/curriculum_api/curriculum/uploads/_legacy_files/42/handout.doc"))
        self.assertTrue(is_gated("/curriculum_api/office-previewer/"))

    def test_the_previously_open_apis_are_gated(self):
        """The five apps that had no authentication of any kind."""
        for path in (
            "/curriculum_api/curriculum/overview/",
            "/audit_api/learners/",
            "/manual_audit_api/plans/",
            "/engagement_api/clubs/",
            "/quiz_api/quizzes/",
        ):
            with self.subTest(path=path):
                self.assertTrue(is_gated(path))

    def test_batch_transport_is_gated(self):
        """Both spellings. The fan-out is checked separately, in config/batch."""
        self.assertTrue(is_gated("/api/batch/"))
        self.assertTrue(is_gated("/coach_api/_batch/"))

    def test_longest_prefix_wins(self):
        """The presentation carve-out has to beat the general curriculum rule."""
        self.assertEqual(
            rule_for("/curriculum_api/curriculum/presentations/slides/")[1], ANY
        )
        self.assertEqual(rule_for("/curriculum_api/curriculum/overview/")[1], STAFF)

    def test_prefixes_end_in_a_slash(self):
        """So "/audit_api_public/" could never be matched by "/audit_api"."""
        for prefix, _roles in RULES:
            with self.subTest(prefix=prefix):
                self.assertTrue(prefix.startswith("/"))
                self.assertTrue(prefix.endswith("/"))


class PrefixRoleTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        # These tests isolate role/prefix admission. Learner programme and
        # previous-record gates have separate suites and need real identities.
        for target in ('old_otjh.gate.refusal', 'learner_api.programme_access.refusal'):
            patcher = mock.patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)


class RoleRuleTests(PrefixRoleTestCase):
    """Which roles each prefix serves.

    The expectations here were read off what actually calls each prefix in the
    SPA. The two carve-outs are the ones a guess would get wrong, so they are
    asserted from both directions.
    """

    def _refusal(self, path, role):
        with _gate_on():
            return refusal_for(path, _account(role))

    def test_staff_only_apis_refuse_learners_and_employers(self):
        for path in (
            "/curriculum_api/curriculum/overview/",
            "/coach_api/coach/caseload/",
            "/quiz_api/quizzes/",
            "/audit_api/learners/",
            "/hours_test_api/last-audit/cohort/",
            "/manual_audit_api/plans/",
        ):
            for role in ("learner", "employer"):
                with self.subTest(path=path, role=role):
                    refusal = self._refusal(path, role)
                    self.assertIsNotNone(refusal)
                    self.assertEqual(refusal.status_code, 403)
                    self.assertEqual(
                        json.loads(refusal.content)["code"], "forbidden"
                    )

    def test_staff_only_apis_admit_staff_and_admin(self):
        for path in ("/curriculum_api/curriculum/overview/", "/audit_api/learners/"):
            for role in ("staff", "admin"):
                with self.subTest(path=path, role=role):
                    self.assertIsNone(self._refusal(path, role))

    def test_learners_may_read_presentations(self):
        """pages/learner/video-watch renders SlideDeckViewer, which reads this."""
        self.assertIsNone(
            self._refusal("/curriculum_api/curriculum/presentations/slides/", "learner")
        )

    def test_allowlisted_diagnostics_transport_accepts_every_signed_in_role(self):
        path = "/curriculum_api/performance/record/"
        for role in ("learner", "employer", "staff", "admin"):
            with self.subTest(role=role):
                self.assertIsNone(self._refusal(path, role))

        with _gate_on():
            refusal = refusal_for(path, None)
        self.assertEqual(refusal.status_code, 401)

    def test_learners_may_read_the_curriculum_refresh_counter(self):
        path = "/curriculum_api/curriculum/cache-epoch/"
        self.assertIsNone(self._refusal(path, "learner"))
        self.assertEqual(self._refusal(path, "employer").status_code, 403)

    def test_activity_recording_accepts_signed_in_roles_but_not_audit_reads(self):
        path = "/curriculum_api/activity/record/"
        for role in ("learner", "employer", "staff", "admin"):
            with self.subTest(role=role):
                self.assertIsNone(self._refusal(path, role))
        with _gate_on():
            self.assertEqual(refusal_for(path, None).status_code, 401)
        for role in ("learner", "employer"):
            for restricted in ("/curriculum_api/activity/", "/curriculum_api/activity/people/",
                               "/curriculum_api/curriculum/overview/"):
                with self.subTest(role=role, path=restricted):
                    self.assertEqual(self._refusal(restricted, role).status_code, 403)

    def test_programme_audit_material_tables_are_staff_only(self):
        path = "/curriculum_api/curriculum/programme-audit/materials/"
        for role in ("learner", "employer"):
            with self.subTest(role=role):
                refusal = self._refusal(path, role)
                self.assertIsNotNone(refusal)
                self.assertEqual(refusal.status_code, 403)

    def test_learners_may_read_authored_uploads(self):
        path = "/curriculum_api/curriculum/uploads/_legacy_files/42/handout.pdf"
        self.assertIsNone(self._refusal(path, "learner"))
        refusal = self._refusal(path, "employer")
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.status_code, 403)

    def test_learners_may_use_engagement(self):
        """The learner clubs, events and rewards pages are built on this API."""
        for path in (
            "/engagement_api/clubs/",
            "/engagement_api/events/",
            "/engagement_api/rewards/",
        ):
            with self.subTest(path=path):
                self.assertIsNone(self._refusal(path, "learner"))

    def test_role_rules_do_not_apply_to_anonymous_callers(self):
        """No session is 401, never 403 -- it must not say what role would do."""
        with _gate_on():
            refusal = refusal_for("/curriculum_api/curriculum/overview/", None)
        self.assertEqual(refusal.status_code, 401)

    def test_admin_site_operator_is_not_role_checked(self):
        """A Django-auth session has no platform role; it is an operator."""
        with _gate_on():
            self.assertIsNone(
                refusal_for(
                    "/curriculum_api/curriculum/overview/",
                    None,
                    django_user_is_authenticated=True,
                )
            )


class GateResponseTests(SimpleTestCase):
    """The middleware in isolation, so a passing request never reaches a view.

    Driving it with ``RequestFactory`` and a sentinel ``get_response`` is what
    keeps these database-free: the cases that *should* pass the gate would
    otherwise run the real curriculum view and query Neon.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.reached = []
        self.middleware = ApiSessionGateMiddleware(self._sentinel)

    def _sentinel(self, request):
        self.reached.append(request.path_info)
        return HttpResponse("view ran")

    def test_anonymous_request_is_refused(self):
        with _gate_on():
            response = self.middleware(
                self.factory.get("/curriculum_api/curriculum/stats/")
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(json.loads(response.content)["code"], "unauthenticated")
        self.assertEqual(self.reached, [], "the view must not have run")

    def test_refusal_names_no_record(self):
        """The body is identical for every gated path -- it leaks no route detail."""
        with _gate_on():
            a = self.middleware(self.factory.get("/curriculum_api/curriculum/stats/"))
            b = self.middleware(self.factory.get("/curriculum_api/does-not-exist/"))

        self.assertEqual(a.content, b.content)

    def test_android_browser_link_goes_to_fixed_login_without_running_download(self):
        with _gate_on():
            response = self.middleware(self.factory.get(
                "/learner_api/mobile/download/android/?next=https://evil.test",
                HTTP_ACCEPT="text/html,application/xhtml+xml",
            ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login?download=android")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.reached, [])

    def test_android_redirect_does_not_change_api_or_other_route_contracts(self):
        cases = [
            ("get", "/learner_api/mobile/download/android/", {}),
            ("get", "/learner_api/mobile/download/android/", {"HTTP_ACCEPT": "application/json"}),
            ("get", "/learner_api/mobile/download/android/", {"HTTP_ACCEPT": "text/html", "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}),
            ("post", "/learner_api/mobile/download/android/", {"HTTP_ACCEPT": "text/html"}),
            ("get", "/learner_api/mobile/bootstrap/", {"HTTP_ACCEPT": "text/html"}),
        ]
        for method, path, headers in cases:
            with self.subTest(method=method, path=path, headers=headers), _gate_on():
                response = self.middleware(getattr(self.factory, method)(path, **headers))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(json.loads(response.content)["code"], "unauthenticated")
        self.assertEqual(self.reached, [])

    def test_android_session_outage_is_not_a_login_redirect(self):
        with _gate_on(), mock.patch.object(api_gate, "session_unreadable", return_value=True):
            response = self.middleware(self.factory.get(
                "/learner_api/mobile/download/android/", HTTP_ACCEPT="text/html"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.reached, [])

    def test_authenticated_android_link_still_reaches_entitlement_checks(self):
        with _gate_on(), mock.patch.object(self.middleware, "_account", return_value=_account("learner")):
            response = self.middleware(self.factory.get(
                "/learner_api/mobile/download/android/", HTTP_ACCEPT="text/html"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.reached, ["/learner_api/mobile/download/android/"])

    def test_ungated_prefix_is_untouched(self):
        with _gate_on():
            response = self.middleware(self.factory.get("/login_api/health/"))

        self.assertEqual(self.reached, ["/login_api/health/"])
        self.assertEqual(response.status_code, 200)

    def test_preflight_is_not_refused(self):
        """OPTIONS carries no cookies by design; refusing it breaks the real request."""
        with _gate_on():
            self.middleware(self.factory.options("/curriculum_api/curriculum/stats/"))

        self.assertEqual(self.reached, ["/curriculum_api/curriculum/stats/"])

    def test_kill_switch_disables_the_gate(self):
        """API_REQUIRE_AUTH=0 -- local development only, never a deployment."""
        with mock.patch.dict("os.environ", {"API_REQUIRE_AUTH": "0"}, clear=False):
            self.middleware(self.factory.get("/curriculum_api/curriculum/stats/"))

        self.assertEqual(self.reached, ["/curriculum_api/curriculum/stats/"])

    def test_gate_is_on_when_the_variable_is_unset(self):
        """Unset must mean protected: a deployment that never heard of it is safe."""
        with mock.patch.dict("os.environ", clear=False) as environ:
            environ.pop("API_REQUIRE_AUTH", None)
            response = self.middleware(
                self.factory.get("/curriculum_api/curriculum/stats/")
            )

        self.assertEqual(response.status_code, 401)


class BatchSubrequestTests(PrefixRoleTestCase):
    """The batch transport must not be a way around the role rules.

    ``config.batch`` dispatches by calling the resolved view function directly,
    so no middleware sees a sub-request. If the gate were not applied there by
    hand, a learner could post a batch naming curriculum URLs and read what the
    front door refuses them.
    """

    def test_learner_subrequest_to_a_staff_api_is_refused(self):
        with _gate_on():
            refusal = refusal_for(
                "/curriculum_api/curriculum/overview/", _account("learner")
            )
        self.assertEqual(refusal.status_code, 403)

    def test_batch_applies_the_gate_before_resolving(self):
        """The refusal is returned as that item's status, not raised."""
        from config.batch import _execute_get

        parent = RequestFactory().post("/api/batch/")
        parent.login_account = _account("learner")

        with _gate_on():
            result = _execute_get(
                parent,
                {"id": "1", "url": "/curriculum_api/curriculum/overview/"},
            )

        self.assertEqual(result["status"], 403)

    def test_batch_still_serves_a_permitted_subrequest_path(self):
        """A learner batching an ANY prefix is not refused by the gate."""
        from config.batch import refusal_for as batch_refusal_for

        with _gate_on():
            self.assertIsNone(
                batch_refusal_for("/engagement_api/clubs/", _account("learner"))
            )


class MiddlewareIsInstalledTests(SimpleTestCase):
    """The gate only protects anything if it is actually in MIDDLEWARE.

    Goes through the full stack via the test client, unlike the tests above.
    Safe to do here because the request is refused before any view runs, so it
    still touches no database.
    """

    def test_a_real_request_is_refused_by_the_installed_stack(self):
        with _gate_on():
            response = Client().get("/curriculum_api/curriculum/stats/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthenticated")

    def test_it_runs_after_the_session_is_resolved(self):
        """Order matters: resolving the session first is what lets a real user in."""
        gate = "login.api_gate.ApiSessionGateMiddleware"
        resolver = "login.middleware.LoginSessionMiddleware"
        self.assertIn(gate, settings.MIDDLEWARE)
        self.assertLess(
            settings.MIDDLEWARE.index(resolver), settings.MIDDLEWARE.index(gate)
        )
