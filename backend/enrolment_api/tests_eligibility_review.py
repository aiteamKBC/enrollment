"""Review transactions with synthetic applications; no database, accounts or mail.

The persistence double checks lock/CAS/unique contracts and restores data on
rollback. PostgreSQL lock scheduling and DDL still require a disposable server.
"""
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve

from . import eligibility_review as review
from . import eligibility_form as form

URL = "/enrolment_api/eligibility-applications/42/reviews/"


class ReviewCursor:
    def __init__(self):
        self.application = {"id": 42, "assessment_status": "pending_assessment", "eligible": None,
                            "review_status": None, "review_version": None, "ready": True,
                            "submission_fingerprint": "a" * 64, "submission_response": {"outcome": "pendingAssessment"}}
        self.history = []
        self.executed = []
        self.result = None
        self.available = True
        self.fail_update = False

    @contextmanager
    def atomic(self, **_kwargs):
        before = deepcopy((self.application, self.history))
        try:
            yield
        except Exception:
            self.application, self.history = before
            raise

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.executed.append((sql, deepcopy(params)))
        if "FROM enrolment.eligibility_form a WHERE" in sql:
            self.result = (deepcopy(self.application),) if self.application else None
        elif "to_regclass" in sql:
            self.result = ("enrolment.eligibility_application_reviews" if self.available else None,)
        elif sql.startswith("SELECT reviewer_id"):
            entry = next((entry for entry in self.history if entry["request_id"] == params[1]), None)
            self.result = (entry["reviewerId"], entry["status"], entry["notes"], entry["version"]) if entry else None
        elif sql.startswith("SELECT jsonb_build_object"):
            self.result = [(json.dumps({**{key: value for key, value in entry.items() if key != "request_id"}, "reviewerId": str(entry["reviewerId"])}),)
                           for entry in reversed(self.history)]
        elif sql.startswith("INSERT INTO enrolment.eligibility_application_reviews"):
            app_id, actor_id, actor_name, previous, status, reason, version, request_id = params
            assert not any(entry["version"] == version or entry["request_id"] == request_id for entry in self.history)
            self.history.append({"id": str(len(self.history) + 1), "applicationId": str(app_id), "version": version,
                                 "reviewerId": actor_id, "reviewerName": actor_name, "previousStatus": previous,
                                 "status": status, "notes": reason, "decidedAt": "2026-11-03T12:00:00+00:00", "request_id": request_id})
        elif sql.startswith("UPDATE enrolment.eligibility_form SET review_status"):
            status, version, app_id, expected = params
            if self.fail_update or expected != (self.application["review_version"] or 0):
                self.result = None
            else:
                self.application.update(review_status=status, review_version=version)
                self.result = (app_id,)
        else:
            raise AssertionError(f"Unexpected review SQL: {sql}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@override_settings(DEBUG=True, ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=True)
class EligibilityReviewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cursor = ReviewCursor()
        self.actor = SimpleNamespace(id=7, role="staff", display_name="Example Reviewer", email="reviewer@example.test")
        self.grants = {"enrolment"}
        def authenticate(request):
            request.login_account = self.actor
            return self.actor
        targets = (
            patch("login.permissions.authenticate_request", side_effect=authenticate),
            patch("login.permissions._accesses_of", side_effect=lambda _actor: frozenset(self.grants)),
            patch.object(review, "connections", {form.CONN: SimpleNamespace(cursor=lambda: self.cursor)}),
            patch.object(review.transaction, "atomic", side_effect=lambda **kwargs: self.cursor.atomic(**kwargs)),
        )
        for active in targets:
            active.start()
            self.addCleanup(active.stop)
        self.guards = []
        for name in ("enrolment_api.eligibility_invitation.invite_applicant", "enrolment_api.eligibility_invitation.has_account",
                     "login.services.invite_eligible_applicant", "learner_api.models.EnrolmentUser.all_learners.create"):
            active = patch(name, side_effect=AssertionError("Review must never create or invite a learner."))
            self.guards.append(active.start())
            self.addCleanup(active.stop)

    def call(self, body=None, method="post", header=True):
        options = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if header else {}
        request = getattr(self.factory, method)(URL, data=json.dumps(body) if body is not None else None,
                                               content_type="application/json", **options)
        response = review.application_reviews(request, 42)
        return response, json.loads(response.content)

    def decision(self, **changes):
        return {"action": "approved", "notes": "Employer evidence checked.", "expectedVersion": 0, "requestId": str(uuid4()), **changes}

    def test_each_initial_decision_records_session_identity_and_preserves_submission_evidence(self):
        for status in review.STATUSES:
            with self.subTest(status=status):
                self.cursor = ReviewCursor()
                before = deepcopy(self.cursor.application)
                response, data = self.call(self.decision(action=status))
                self.assertEqual(response.status_code, 201)
                self.assertEqual(data["status"], status)
                self.assertEqual(data["version"], 1)
                entry = data["history"][0]
                self.assertEqual((entry["reviewerId"], entry["reviewerName"]), ("7", "Example Reviewer"))
                self.assertEqual(entry["previousStatus"], "pending_assessment")
                self.assertEqual(entry["status"], status)
                self.assertEqual(entry["notes"], "Employer evidence checked.")
                self.assertTrue(entry["decidedAt"])
                for key, value in before.items():
                    if key not in ("review_status", "review_version"):
                        self.assertEqual(self.cursor.application[key], value)
                self.assertIn("FOR UPDATE", self.cursor.executed[0][0])
                update = next(sql for sql, _params in self.cursor.executed if sql.startswith("UPDATE"))
                self.assertIn("COALESCE(review_version, 0) = %s", update)
                self.assertNotIn("submission_response", update)
                self.assertTrue(all(not guard.called for guard in self.guards))

    def test_evidence_request_can_transition_to_approval_or_rejection(self):
        for status in ("approved", "rejected"):
            self.cursor = ReviewCursor()
            self.call(self.decision(action="more_evidence_required"))
            response, data = self.call(self.decision(action=status, expectedVersion=1))
            self.assertEqual(response.status_code, 201)
            self.assertEqual(data["version"], 2)
            self.assertEqual(len(data["history"]), 2)
            self.assertEqual(data["history"][0]["previousStatus"], "more_evidence_required")
            self.assertEqual(data["allowedActions"], [])

    def test_terminal_and_repeated_evidence_transitions_are_forbidden(self):
        for initial in review.STATUSES:
            for action in review.STATUSES:
                if initial == "more_evidence_required" and action != initial:
                    continue
                self.cursor = ReviewCursor()
                self.call(self.decision(action=initial))
                response, _data = self.call(self.decision(action=action, expectedVersion=1))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(len(self.cursor.history), 1)

    def test_old_and_automatically_screened_records_are_not_reviewed_or_reclassified(self):
        for status, eligible in ((None, True), (None, None), (form.PROVISIONAL_ELIGIBLE, True), (form.NOT_ELIGIBLE, False)):
            self.cursor = ReviewCursor()
            self.cursor.application.update(assessment_status=status, eligible=eligible)
            before = deepcopy(self.cursor.application)
            response, data = self.call(method="get")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["allowedActions"], [])
            self.assertEqual(self.call(self.decision())[0].status_code, 409)
            self.assertEqual(self.cursor.application, before)
            self.assertEqual(self.cursor.history, [])

    def test_notes_versions_choices_and_reviewer_forgery_are_rejected_before_queries(self):
        bad = [{"notes": value} for value in ("", "  ", None, False, "x" * 2001, "bad\x00reason")]
        bad += [{"action": value} for value in ("invited", "eligible", True, [])]
        bad += [{"expectedVersion": value} for value in (None, -1, True, "0")]
        bad += [{"requestId": value} for value in ("not-uuid", None, False)]
        bad += [{key: value} for key, value in (("reviewerId", 999), ("eligible", True), ("reviewerName", "Someone else"))]
        for changes in bad:
            self.assertEqual(self.call(self.decision(**changes))[0].status_code, 400, changes)
        self.assertEqual(self.cursor.executed, [])

    def test_anonymous_roles_and_missing_enrolment_grants_cannot_read_or_decide(self):
        for role, grants, code in ((None, set(), 401), ("learner", {"enrolment"}, 403),
                                   ("employer", {"enrolment"}, 403), ("staff", {"coach"}, 403)):
            self.actor = None if role is None else SimpleNamespace(id=8, role=role)
            self.grants = grants
            for method in ("get", "post"):
                self.assertEqual(self.call(self.decision() if method == "post" else None, method=method)[0].status_code, code)
        self.assertEqual(self.cursor.executed, [])

    def test_super_admin_can_decide(self):
        self.actor.role = "admin"
        self.grants = {"super-admin"}
        self.assertEqual(self.call(self.decision())[0].status_code, 201)

    def test_identical_request_is_idempotent_and_a_changed_payload_or_actor_conflicts(self):
        body = self.decision()
        self.assertEqual(self.call(body)[0].status_code, 201)
        self.assertEqual(self.call(body)[0].status_code, 200)
        self.assertEqual(len(self.cursor.history), 1)
        self.assertEqual(self.call({**body, "notes": "Different reason"})[0].status_code, 409)
        self.actor.id = 8
        self.assertEqual(self.call(body)[0].status_code, 409)
        self.assertEqual(len(self.cursor.history), 1)

    def test_replayed_old_request_returns_current_state_after_later_decision(self):
        body = self.decision(action="more_evidence_required")
        self.call(body)
        self.call(self.decision(expectedVersion=1))
        response, data = self.call(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((data["status"], data["version"]), ("approved", 2))
        self.assertEqual(len(self.cursor.history), 2)

    def test_second_reviewer_with_same_viewed_version_conflicts(self):
        self.call(self.decision())
        self.actor.id = 8
        self.assertEqual(self.call(self.decision(action="rejected"))[0].status_code, 409)
        self.assertEqual(len(self.cursor.history), 1)
        self.assertEqual(self.cursor.application["review_status"], "approved")

    def test_failed_cas_rolls_back_the_audit_entry(self):
        self.cursor.fail_update = True
        before = deepcopy(self.cursor.application)
        with self.assertLogs(review.logger, level="ERROR"):
            self.assertEqual(self.call(self.decision())[0].status_code, 503)
        self.assertEqual(self.cursor.application, before)
        self.assertEqual(self.cursor.history, [])

    def test_corrupt_or_missing_audit_history_fails_closed(self):
        self.cursor.application.update(review_status="more_evidence_required", review_version=1)
        with self.assertLogs(review.logger, level="ERROR"):
            self.assertEqual(self.call(self.decision(expectedVersion=1))[0].status_code, 503)
        self.assertEqual(self.cursor.history, [])

    def test_missing_application_and_legacy_schema(self):
        self.cursor.application = None
        self.assertEqual(self.call(method="get")[0].status_code, 404)
        self.cursor = ReviewCursor()
        self.cursor.available = False
        self.cursor.application["ready"] = False
        response, data = self.call(method="get")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data["available"])
        self.assertFalse(data["enabled"])
        self.assertEqual(data["history"], [])
        with self.assertLogs(review.logger, level="ERROR"):
            self.assertEqual(self.call(self.decision())[0].status_code, 503)

    def test_header_and_method_guards(self):
        self.assertEqual(self.call(self.decision(), header=False)[0].status_code, 403)
        for method in ("put", "delete", "patch"):
            self.assertEqual(self.call(self.decision(), method=method)[0].status_code, 405)
        self.assertEqual(self.cursor.executed, [])
        self.assertEqual(resolve(URL).func, review.application_reviews)

    @override_settings(ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=False)
    def test_flag_off_allows_history_but_never_decisions(self):
        self.assertEqual(self.call(self.decision())[0].status_code, 403)
        self.assertEqual(self.cursor.executed, [])
        response, data = self.call(method="get")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(data["enabled"])
        self.assertEqual(data["allowedActions"], [])

    @override_settings(DEBUG=False)
    def test_production_safety_guard_cannot_be_removed_by_flag(self):
        self.assertEqual(self.call(self.decision())[0].status_code, 403)
        self.assertEqual(self.cursor.executed, [])

    def test_prepared_sql_is_additive_and_matches_fresh_install_and_python_constraints(self):
        root = Path(__file__).resolve().parents[2]
        upgrade = (root / "database/eligibility_review_phase2c2a.sql").read_text(encoding="utf-8")
        fresh = (root / "database/schema.sql").read_text(encoding="utf-8")
        normalized = " ".join(upgrade.split())
        for column, definition in review.COLUMN_DEFINITIONS:
            self.assertIn(f"{column} {definition}", " ".join(fresh.split()))
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {column} {definition}", normalized)
        self.assertIn(review.CONSISTENCY, " ".join(fresh.split()))
        audit_sql = upgrade[upgrade.index("CREATE TABLE IF NOT EXISTS"):upgrade.rindex("COMMIT;")]
        self.assertIn(audit_sql, fresh)
        self.assertIn("UNIQUE (application_id, version)", audit_sql)
        self.assertIn("UNIQUE (application_id, request_id)", audit_sql)
        self.assertIn("BEFORE UPDATE OR DELETE", audit_sql)
        self.assertIn("BEFORE TRUNCATE", audit_sql)
        self.assertNotIn("UPDATE enrolment.eligibility_form", upgrade)
        self.assertNotIn("DROP COLUMN", upgrade)
        self.assertNotIn("ALTER COLUMN assessment_status", upgrade)
