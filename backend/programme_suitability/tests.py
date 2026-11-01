import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from coach_api.csrf import coach_csrf_token

from enrolment_api.wizard_layout import LayoutError, normalize_layout
from .configuration import KEY, ConfigurationError, defaults, learner_configuration, validate
from .models import Assessment
from .scoring import calculate
from . import views
from .completion import submission_error

urlpatterns = [
    path("learner_api/enrollment-csrf/", coach_csrf_token),
    path("enrolment_api/programme-suitability/", include("programme_suitability.urls")),
]


def answers(config, pathway="marketing", indices=(1,)):
    return {q["id"]: [q["answers"][i]["id"] for i in indices]
            for q in config["questions"] if q["active"] and q["pathway"] == pathway}


class ScoringTests(SimpleTestCase):
    def setUp(self):
        self.config = defaults()

    def test_seed_contains_18_questions_72_answers_and_four_stable_programmes(self):
        config = validate(self.config)
        self.assertEqual(len(config["questions"]), 18)
        self.assertEqual(config["questions"][0]["title"], "Marketing Strategy and Decision-Making")
        self.assertEqual(sum(len(q["answers"]) for q in config["questions"]), 72)
        self.assertEqual({p["id"] for p in config["programmes"]}, {"ME_L4", "MM_L6", "APM_L4", "PCP_L6"})

    def test_weights_affect_correct_programme_and_question_caps_independently(self):
        for selected, low, high in [(["M01_A"], 4, 0), (["M01_D"], 0, 10),
                                    (["M01_A", "M01_B"], 10, 0), (["M01_B", "M01_C"], 8, 6),
                                    (["M01_A", "M01_B", "M01_C", "M01_D"], 10, 10)]:
            with self.subTest(selected=selected):
                result = calculate(self.config, "marketing", {"M01": selected})
                self.assertEqual(result["breakdown"][0]["points"], {"ME_L4": low, "MM_L6": high})

    def test_both_pathways_recommend_both_levels(self):
        for pathway, low, high in [("marketing", "ME_L4", "MM_L6"), ("project-controls", "APM_L4", "PCP_L6")]:
            for index, pid in [(1, low), (3, high)]:
                with self.subTest(pathway=pathway, pid=pid):
                    result = calculate(self.config, pathway, answers(self.config, pathway, (index,)), complete=True)
                    self.assertEqual(result["outcome"], f"RECOMMENDED_{pid}")
                    self.assertEqual(result["scores"][pid], 80 if index == 1 else 100)

    def test_aggregation_and_denominator_includes_unanswered_and_none_apply(self):
        result = calculate(self.config, "marketing", {"M01": ["M01_B"], "M02": ["M02_B"], "M03": []})
        self.assertEqual(result["availableWeight"], 71)
        self.assertEqual(result["scores"]["ME_L4"], round(14.4 / 71 * 100, 2))
        self.assertEqual(result["coverage"], round(28 / 71 * 100, 2))
        self.assertEqual(result["evidenceCoverage"], round(18 / 71 * 100, 2))

    def test_no_answers_or_all_none_is_insufficient_information(self):
        for responses in ({}, {q["id"]: [] for q in self.config["questions"] if q["pathway"] == "marketing"}):
            self.assertEqual(calculate(self.config, "marketing", responses)["outcome"], "INSUFFICIENT_INFORMATION")

    def test_required_unanswered_is_refused_only_on_confirmation(self):
        calculate(self.config, "marketing", {})
        with self.assertRaises(ConfigurationError):
            calculate(self.config, "marketing", {}, complete=True)
        calculate(self.config, "marketing", {qid: [] for qid in answers(self.config)}, complete=True)

    def test_equal_and_near_equal_scores_keep_a_score_recommendation_with_internal_review(self):
        for indices in [(0, 1, 2, 3), (1, 2), (0, 2)]:
            config = copy.deepcopy(self.config)
            if indices == (0, 2):
                for q in config["questions"]:
                    q["answers"][0]["affinities"] = {"ME_L4" if q["pathway"] == "marketing" else "APM_L4": 0.6}
            result = calculate(config, "marketing", answers(config, indices=indices))
            self.assertEqual(result["outcome"], "RECOMMENDED_ME_L4")
            self.assertEqual(result["admissionsReviewOutcome"], "FURTHER_REVIEW_REQUIRED")

    def test_select_all_tie_defaults_to_level_four_and_retains_mixed_evidence_flag(self):
        self.config["rules"].update(minimumScore=0, minimumDifference=0, minimumCriticalIndicators=0)
        result = calculate(self.config, "marketing", answers(self.config, indices=(0, 1, 2, 3)))
        self.assertIn("CONTRADICTORY_RESPONSIBILITIES", result["reasons"])
        self.assertEqual(result["recommendedProgramme"], "ME_L4")
        self.assertIn("SCORE_TIE_LEVEL_4_DEFAULT", result["recommendationFlags"])

    def test_two_distinct_critical_indicators_required_for_level_six(self):
        responses = answers(self.config, indices=(3,))
        responses["M03"] = []; responses["M06"] = []
        result = calculate(self.config, "marketing", responses)
        self.assertEqual(result["criticalIndicators"]["MM_L6"], ["M01"])
        self.assertIn("MISSING_CRITICAL_LEVEL_6_EVIDENCE", result["reasons"])
        self.config["rules"]["minimumCriticalIndicators"] = 1
        self.assertEqual(calculate(self.config, "marketing", responses)["recommendedProgramme"], "MM_L6")

    def test_unanswered_coverage_requires_review(self):
        self.config["rules"]["minimumScore"] = 0
        result = calculate(self.config, "marketing", {"M01": ["M01_B"]})
        self.assertIn("LOW_ASSESSMENT_COVERAGE", result["reasons"])

    def test_answer_affinity_can_contribute_to_multiple_programmes(self):
        self.config["questions"][0]["answers"][0]["affinities"] = {"ME_L4": 0.4, "MM_L6": 0.2}
        row = calculate(self.config, "marketing", {"M01": ["M01_A"]})["breakdown"][0]
        self.assertEqual(row["points"], {"ME_L4": 4, "MM_L6": 2})

    def test_edited_weight_and_affinity_change_new_results(self):
        first = calculate(self.config, "marketing", {"M01": ["M01_B"]})
        self.config["questions"][0]["weight"] = 100
        self.config["questions"][0]["answers"][1]["affinities"]["ME_L4"] = 0.5
        changed = calculate(self.config, "marketing", {"M01": ["M01_B"]})
        self.assertNotEqual(first["scores"], changed["scores"])
        self.assertEqual(changed["breakdown"][0]["points"]["ME_L4"], 50)

    def test_invalid_question_and_answer_references_are_refused(self):
        for response in [{"P01": ["P01_A"]}, {"M01": ["M02_A"]}, {"M01": ["M01_A", "M01_A"]}, {"M01": None}, {"M01": "M01_A"}]:
            with self.subTest(response=response):
                with self.assertRaises(ConfigurationError):
                    calculate(self.config, "marketing", response)

    def test_single_select_and_inactive_answers_are_enforced(self):
        self.config["questions"][0]["multiple"] = False
        with self.assertRaises(ConfigurationError): calculate(self.config, "marketing", {"M01": ["M01_A", "M01_B"]})
        self.config["questions"][0]["answers"][0]["active"] = False
        with self.assertRaises(ConfigurationError): calculate(self.config, "marketing", {"M01": ["M01_A"]})

    def test_bad_weights_and_rules_fail_validation_without_division_by_zero(self):
        for bad in [0, -1, float('inf'), float('nan'), True, "10"]:
            config = defaults(); config["questions"][0]["weight"] = bad
            with self.subTest(weight=bad):
                with self.assertRaises(ConfigurationError): calculate(config, "marketing", {})
        for q in self.config["questions"]: q["active"] = False
        with self.assertRaises(ConfigurationError): calculate(self.config, "marketing", {})
        self.config["enabled"] = False
        with self.assertRaises(ConfigurationError): calculate(self.config, "marketing", {})

    def test_config_edits_order_deactivation_and_more_than_four_answers(self):
        config = self.config
        q = config["questions"][0]
        q["order"] = 80; q["answers"][0]["order"] = 80
        q["answers"][1]["active"] = False
        q["answers"].append({"id": "M01_EXTRA", "text": "Another task", "active": True, "order": 5, "affinities": {"ME_L4": 0.5}})
        validate(config)
        self.assertEqual(len(q["answers"]), 5)
        public = learner_configuration(config)
        self.assertNotIn("weight", public["questions"][0])
        self.assertNotIn("affinities", public["questions"][0]["answers"][0])

    def test_duplicate_ids_bad_affinities_and_cross_pathway_critical_target_rejected(self):
        for change in [lambda c: c["questions"][0].update(id="M02"),
                       lambda c: c["questions"][0]["answers"][0].update(affinities={"ME_L4": 1.1}),
                       lambda c: c["questions"][0].update(criticalProgramme="PCP_L6")]:
            config = defaults(); change(config)
            with self.assertRaises(ConfigurationError): validate(config)

    def test_malformed_configuration_types_are_validation_errors(self):
        for change in [lambda c: c["programmes"][0].update(id=[]),
                       lambda c: c["questions"][0].update(pathway=[]),
                       lambda c: c["questions"][0].update(criticalProgramme=[]),
                       lambda c: c.update(schemaVersion=True)]:
            config = defaults(); change(config)
            with self.assertRaises(ConfigurationError): validate(config)
        with self.assertRaises(ConfigurationError): calculate(defaults(), [], {})


class PublishingTests(SimpleTestCase):
    def test_config_publishes_in_existing_layout_and_older_clients_preserve_it(self):
        draft = {"steps": [{"slug": "cv-job", "label": "CV", "builtin": True, "items": []}], KEY: defaults()}
        published, _ = normalize_layout(draft, None)
        self.assertEqual(published[KEY], defaults())
        edited = copy.deepcopy(draft); edited[KEY]["questions"][0]["weight"] = 30
        newer, _ = normalize_layout(edited, published)
        self.assertEqual(published[KEY]["questions"][0]["weight"], 10)
        self.assertEqual(newer[KEY]["questions"][0]["weight"], 30)
        legacy, _ = normalize_layout({"steps": draft["steps"]}, published)
        self.assertEqual(legacy[KEY], published[KEY])

    def test_invalid_config_cannot_publish_and_old_layouts_still_work(self):
        old = {"steps": [{"slug": "cv-job", "label": "CV", "items": []}]}
        self.assertNotIn(KEY, normalize_layout(old, None)[0])
        old[KEY] = defaults(); old[KEY]["rules"]["minimumCoverage"] = 120
        with self.assertRaises(LayoutError): normalize_layout(old, None)


class AssessmentApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.config = defaults()
        self.staff = False
        self.auth = patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}); self.auth.start(); self.addCleanup(self.auth.stop)
        self.learner = SimpleNamespace(pk=41, onboarding_status="Not started")
        # These endpoint-policy tests isolate the cross-model assignment service;
        # test_programme_assignment exercises its real database and API integration.
        self.assignment = patch.object(views, "sync_confirmed_programme_to_learner", return_value={"status": "APPLIED"})
        self.assignment.start(); self.addCleanup(self.assignment.stop)
        self.exists = patch.object(views, "learner_record", return_value=self.learner); self.exists.start(); self.addCleanup(self.exists.stop)
        self.publish = patch.object(views, "published", side_effect=lambda: (3, copy.deepcopy(self.config))); self.publish.start(); self.addCleanup(self.publish.stop)
        self.grants = patch("enrolment_api.auth._accesses_of", return_value={"enrolment"}); self.grants.start(); self.addCleanup(self.grants.stop)
        self.mail = patch("programme_suitability.notifications.email_azure.is_configured", return_value=False)
        self.mail.start(); self.addCleanup(self.mail.stop)

    def request(self, data=None, method="PUT", learner=41, role="learner", view=views.assessment, csrf=True):
        request = getattr(self.factory, method.lower())('/suitability/', data=json.dumps(data or {}), content_type="application/json") if method != "GET" else self.factory.get('/suitability/')
        request.login_account = SimpleNamespace(is_active=True, role=role, subject_id=learner, subject_type="staff" if role == "staff" else "learner", email="synthetic@example.invalid")
        request._dont_enforce_csrf_checks = csrf
        return view(request, kind="apprenticeship", learner_id=41)

    def start(self, **extra):
        response = self.request({"revision": 0, "configurationVersion": 3, "pathway": "marketing", **extra})
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content)

    def test_get_does_not_create_a_submission(self):
        response = self.request(method="GET")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Assessment.objects.exists())

    def test_responses_scores_preference_and_review_are_saved_independently(self):
        first = self.start(responses=answers(self.config, indices=(3,)))
        second = self.request({"revision": first["revision"], "selectedProgramme": "ME_L4", "confirm": True})
        self.assertEqual(second.status_code, 200, second.content)
        row = Assessment.objects.get()
        self.assertEqual(row.result["recommendedProgramme"], "MM_L6")
        self.assertEqual(row.selected_programme, "ME_L4")
        self.assertEqual(row.original_preference, "ME_L4")
        self.assertEqual(row.review_status, "PENDING_ADMISSIONS_REVIEW")
        self.assertEqual(json.loads(self.request(method="GET").content)["responses"], answers(self.config, indices=(3,)))

    def test_snapshots_and_historical_results_survive_new_publication(self):
        self.start(responses=answers(self.config))
        before = Assessment.objects.get()
        self.config["questions"][0]["weight"] = 100
        fetched = json.loads(self.request(method="GET").content)
        self.assertEqual(fetched["result"], views.payload(before)["result"])
        self.assertEqual(fetched["configurationVersion"], 3)
        self.assertEqual(Assessment.objects.get().configuration["questions"][0]["weight"], 10)
        self.request({"revision": before.revision, "responses": answers(defaults())})
        self.assertEqual(Assessment.objects.get().result, before.result)

    def test_different_learner_employer_and_other_staff_grants_cannot_access(self):
        for learner, role in [(42, "learner"), (41, "employer")]:
            self.assertEqual(self.request(method="GET", learner=learner, role=role).status_code, 404)
        with patch("enrolment_api.auth._accesses_of", return_value={"coach"}):
            self.assertEqual(self.request(method="GET", role="staff").status_code, 404)

    def test_anonymous_and_csrf_requests_are_denied(self):
        request = self.factory.get('/'); request.user = SimpleNamespace(is_authenticated=False)
        self.assertEqual(views.assessment(request, kind="apprenticeship", learner_id=41).status_code, 401)
        self.assertEqual(self.request(csrf=False).status_code, 403)

    def test_ordinary_django_user_cannot_read_arbitrary_learner_data_or_publish_config(self):
        from enrolment_api.wizard_layout import publish_wizard_layout
        request = self.factory.get('/')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)
        self.assertEqual(views.assessment(request, kind="apprenticeship", learner_id=41).status_code, 403)
        request = self.factory.put('/', json.dumps({"layout": {KEY: self.config}}), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, is_superuser=False)
        self.assertEqual(publish_wizard_layout(request).status_code, 403)

    def test_server_ignores_client_scores_and_rejects_forged_answers(self):
        self.start(responses=answers(self.config), result={"recommendedProgramme": "MM_L6"}, scores={"MM_L6": 100})
        self.assertEqual(Assessment.objects.get().result["recommendedProgramme"], "ME_L4")
        response = self.request({"revision": 1, "responses": {"M01": ["P01_D"]}})
        self.assertEqual(response.status_code, 400)

    def test_partial_none_and_pathway_switch_are_persistent(self):
        self.start(responses={"M01": []})
        self.assertEqual(Assessment.objects.get().responses, {"M01": []})
        response = self.request({"revision": 1, "pathway": "project-controls", "responses": answers(self.config, "project-controls")})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Assessment.objects.get().result["recommendedProgramme"], "APM_L4")

    def test_stale_revisions_and_configuration_versions_are_conflicts(self):
        self.assertEqual(self.request({"revision": 0, "configurationVersion": 2, "pathway": "marketing"}).status_code, 409)
        self.start()
        self.assertEqual(self.request({"revision": 0, "responses": {}}).status_code, 409)

    def test_confirmation_validates_required_answers_and_pathway_preference(self):
        self.start()
        for extra in [{"confirm": True, "selectedProgramme": "ME_L4"}, {"selectedProgramme": "PCP_L6"}]:
            self.assertEqual(self.request({"revision": 1, **extra}).status_code, 400)

    def test_staff_override_is_audited_without_overwriting_preference_or_result(self):
        self.start(responses=answers(self.config), selectedProgramme="ME_L4", confirm=True)
        result = copy.deepcopy(Assessment.objects.get().result)
        response = self.request({"revision": 1, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6", "reason": "Verified additional strategic responsibility."}, role="staff", view=views.review)
        self.assertEqual(response.status_code, 200, response.content)
        row = Assessment.objects.get()
        self.assertEqual(row.selected_programme, "ME_L4")
        self.assertEqual(row.result, result)
        self.assertEqual(row.review["recommendedProgramme"], "MM_L6")
        self.assertEqual(row.history[-1]["action"], "ADMISSIONS_REVIEW")
        self.assertIn("review", json.loads(self.request(method="GET").content))

    def test_learner_cannot_review_or_edit_configuration(self):
        self.assertEqual(self.request(view=views.review).status_code, 403)
        for endpoint in (views.configuration_defaults, views.preview):
            request = self.factory.get('/'); request.login_account = SimpleNamespace(is_active=True, role="learner", subject_id=41)
            self.assertEqual(endpoint(request).status_code, 404)

    def test_review_email_reuses_transport_after_commit_and_records_actual_delivery(self):
        self.start(responses=answers(self.config), selectedProgramme="ME_L4", confirm=True)
        with patch("programme_suitability.notifications.email_azure.is_configured", return_value=True), \
             patch("programme_suitability.notifications.learner_email", return_value="synthetic@example.invalid"), \
             patch("programme_suitability.notifications.email_azure.send_mail", return_value=(True, "accepted")) as send:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.request({"revision": 1, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6", "reason": "Additional strategic responsibility."}, role="staff", view=views.review)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(send.called)
            self.assertEqual(send.call_count, 1)
        row = Assessment.objects.get()
        self.assertEqual(row.review["notificationStatus"], "SENT")
        self.assertEqual(row.original_preference, "ME_L4")

    def test_failed_mail_is_not_reported_as_sent_and_review_stays_available(self):
        self.start(responses=answers(self.config), selectedProgramme="ME_L4", confirm=True)
        with patch("programme_suitability.notifications.email_azure.is_configured", return_value=True), \
             patch("programme_suitability.notifications.learner_email", return_value="synthetic@example.invalid"), \
             patch("programme_suitability.notifications.email_azure.send_mail", return_value=(False, "unavailable")):
            with self.captureOnCommitCallbacks(execute=True):
                self.request({"revision": 1, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6", "reason": "Review complete."}, role="staff", view=views.review)
        self.assertEqual(Assessment.objects.get().review["notificationStatus"], "DELIVERY_FAILED")

    def test_disabled_mail_never_attempts_delivery(self):
        self.start(responses=answers(self.config), selectedProgramme="ME_L4", confirm=True)
        with patch("programme_suitability.notifications.email_azure.send_mail") as send:
            with self.captureOnCommitCallbacks(execute=True):
                self.request({"revision": 1, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6", "reason": "Review complete."}, role="staff", view=views.review)
            self.assertFalse(send.called)
        self.assertEqual(Assessment.objects.get().review["notificationStatus"], "AVAILABLE_IN_ASSESSMENT")

    def test_admin_preview_validates_draft_without_writing_applications(self):
        request = self.factory.post('/', data=json.dumps({"configuration": self.config, "pathway": "marketing", "responses": answers(self.config)}), content_type="application/json")
        request.login_account = SimpleNamespace(is_active=True, role="staff", subject_id=5, subject_type="staff")
        request._dont_enforce_csrf_checks = True
        result = views.preview(request)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(json.loads(result.content)["recommendedProgramme"], "ME_L4")
        self.assertFalse(Assessment.objects.exists())

    def test_no_published_configuration_is_nonblocking_and_does_not_create_data(self):
        with patch.object(views, "published", return_value=(None, None)):
            self.assertFalse(json.loads(self.request(method="GET").content)["available"])
            self.assertEqual(self.request({"revision": 0}).status_code, 409)
        self.assertFalse(Assessment.objects.exists())

    def test_submission_is_pending_selection_and_answers_cannot_reopen_it(self):
        self.assertEqual(json.loads(self.request(method="GET").content)["completionStatus"], "NOT_STARTED")
        draft = self.start(responses=answers(self.config), selectedProgramme="ME_L4")
        self.assertEqual(draft["completionStatus"], "IN_PROGRESS")
        saved = json.loads(self.request({"revision": 1, "confirm": True}).content)
        self.assertEqual(saved["completionStatus"], "SUBMITTED")
        self.assertEqual(saved["selectionStatus"], "SUBMITTED_PENDING_CONFIRMATION")
        self.assertIsNone(saved["selectionConfirmedAt"])
        self.assertEqual(self.request({"revision": 2, "responses": answers(self.config, indices=(3,))}).status_code, 403)
        self.assertEqual(Assessment.objects.get().responses, answers(self.config))

    def test_repeated_identical_confirmation_is_idempotent(self):
        self.start(responses=answers(self.config), selectedProgramme="ME_L4")
        first = self.request({"revision": 1, "confirm": True})
        second = self.request({"revision": 1, "confirm": True})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Assessment.objects.count(), 1)
        self.assertEqual(Assessment.objects.get().revision, 2)

    def test_previously_submitted_and_completed_applications_are_not_newly_trapped(self):
        for status in ("Submitted", "Completed"):
            self.learner.onboarding_status = status
            data = json.loads(self.request(method="GET").content)
            self.assertFalse(data["required"])
            self.assertIsNone(submission_error(self.learner))

    def test_server_submission_backstop_uses_real_assessment_not_a_client_marker(self):
        with patch("enrolment_api.wizard_layout.current_layout", return_value={KEY: self.config, "steps": []}):
            self.assertIsNotNone(submission_error(self.learner))
            self.start(responses=answers(self.config), selectedProgramme="ME_L4", confirm=True)
            self.assertIsNotNone(submission_error(self.learner))
            self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
            self.assertIsNone(submission_error(self.learner))

    def test_existing_raw_enrolment_submit_endpoint_cannot_bypass_assessment(self):
        from learner_api import views as learner_views
        request = self.factory.patch('/', json.dumps({"onboardingStatus": "Submitted", "programmeSuitability": {"status": "SUBMITTED"}}), content_type="application/json")
        request.login_account = SimpleNamespace(is_active=True, role="learner", subject_id=41)
        with patch("enrolment_api.wizard_layout.current_layout", return_value={KEY: self.config}), \
             patch.object(learner_views.EnrolmentUser.all_learners, "filter") as lookup, \
             patch.object(learner_views, "save_enrolment_fields") as save:
            lookup.return_value.first.return_value = self.learner
            response = learner_views.enrolment_user_detail(request, pk=41)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(save.called)

    @override_settings(ROOT_URLCONF="programme_suitability.tests", CSRF_COOKIE_NAME="enrollment_csrftoken",
                       CSRF_TRUSTED_ORIGINS=["http://localhost:3000", "http://127.0.0.1:3000"])
    def test_actual_cookie_token_origin_and_session_round_trip_for_both_pathways(self):
        account = SimpleNamespace(is_active=True, role="learner", subject_id=41, subject_type="learner")
        def authenticate(request):
            request.login_account = account
            request.login_session = None
        client = Client(enforce_csrf_checks=True)
        with patch("login.middleware._authenticate_with_file_retry", side_effect=authenticate):
            token = client.get('/learner_api/enrollment-csrf/').json()["csrfToken"]
            endpoint = '/enrolment_api/programme-suitability/apprenticeship/41/'
            self.assertEqual(client.get(endpoint).status_code, 200)
            self.assertEqual(client.put(endpoint, json.dumps({}), content_type="application/json", HTTP_ORIGIN="http://localhost:3000").status_code, 403)
            for revision, pathway in [(0, "marketing"), (1, "project-controls")]:
                response = client.put(endpoint, json.dumps({"revision": revision, "configurationVersion": 3,
                    "pathway": pathway, "responses": answers(self.config, pathway)}), content_type="application/json",
                    HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="http://localhost:3000", HTTP_HOST="127.0.0.1:8000")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["pathway"], pathway)
            forbidden = client.put(endpoint, json.dumps({"revision": 2}), content_type="application/json",
                HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://untrusted.invalid", HTTP_HOST="127.0.0.1:8000")
            self.assertEqual(forbidden.status_code, 403)

    def test_submission_defaults_to_the_authoritative_recommendation_for_all_four_programmes(self):
        for pathway, index, expected in [("marketing", 1, "ME_L4"), ("marketing", 3, "MM_L6"),
                                          ("project-controls", 1, "APM_L4"), ("project-controls", 3, "PCP_L6")]:
            with self.subTest(programme=expected):
                Assessment.objects.all().delete()
                data = self.start(pathway=pathway, responses=answers(self.config, pathway, (index,)))
                submitted = self.request({"revision": data["revision"], "action": "submit", "selectedProgramme": "",
                                         "recommendedProgramme": "forged", "scores": {"MM_L6": 100}})
                self.assertEqual(submitted.status_code, 200, submitted.content)
                result = json.loads(submitted.content)
                self.assertEqual(result["selectedProgramme"], expected)
                self.assertEqual(result["recommendedProgramme"], expected)
                self.assertEqual(result["programmeSelectionSource"], "SYSTEM_RECOMMENDATION")
                self.assertEqual(result["completionStatus"], "SUBMITTED")
                self.assertEqual(result["result"]["scores"][expected], 80 if index == 1 else 100)

    def test_both_level_four_recommendations_reject_a_forged_level_six_selection(self):
        for pathway, high in [("marketing", "MM_L6"), ("project-controls", "PCP_L6")]:
            Assessment.objects.all().delete()
            self.start(pathway=pathway, responses=answers(self.config, pathway), action="submit")
            before = Assessment.objects.get()
            response = self.request({"revision": before.revision, "action": "select", "selectedProgramme": high,
                                     "alternativeSelectionReason": "I would like the higher-level programme.",
                                     "result": {"recommendedProgramme": high}, "scores": {high: 100}})
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(Assessment.objects.get().selected_programme, before.selected_programme)

    def test_level_six_can_choose_level_four_without_rescoring_or_reopening_completion(self):
        for pathway, high, low in [("marketing", "MM_L6", "ME_L4"), ("project-controls", "PCP_L6", "APM_L4")]:
            Assessment.objects.all().delete()
            self.start(pathway=pathway, responses=answers(self.config, pathway, (3,)), action="submit")
            before = Assessment.objects.get()
            with patch.object(views, "calculate", side_effect=AssertionError("Selection must not recalculate scores")):
                response = self.request({"revision": before.revision, "action": "select", "selectedProgramme": low,
                                         "alternativeSelectionReason": "I prefer to develop practical delivery skills first."})
            self.assertEqual(response.status_code, 200, response.content)
            row = Assessment.objects.get()
            self.assertEqual(row.result, before.result)
            self.assertEqual(row.result["recommendedProgramme"], high)
            self.assertEqual(row.selected_programme, low)
            self.assertEqual(row.original_preference, high)
            self.assertEqual(row.programme_selection_source, "LEARNER_ALTERNATIVE")
            self.assertEqual(row.confirmed_at, before.confirmed_at)
            self.assertEqual(json.loads(self.request(method="GET").content)["selectedProgramme"], low)

    def test_review_required_submission_is_complete_without_assigning_a_programme(self):
        responses = {qid: [] for qid in answers(self.config)}
        self.start(responses=responses, action="submit")
        row = Assessment.objects.get()
        self.assertEqual(row.result["outcome"], "INSUFFICIENT_INFORMATION")
        self.assertEqual(row.selected_programme, "")
        data = json.loads(self.request(method="GET").content)
        self.assertEqual(data["completionStatus"], "SUBMITTED")
        self.assertEqual(data["allowedSelections"], [])
        self.assertEqual(data["programmeSelectionSource"], "PENDING_REVIEW")
        self.assertEqual(self.request({"revision": row.revision, "action": "continue"}).status_code, 200)
        with patch("enrolment_api.wizard_layout.current_layout", return_value={KEY: self.config}):
            self.assertIsNone(submission_error(self.learner))
        for pid in ("ME_L4", "MM_L6"):
            self.assertEqual(self.request({"revision": row.revision, "action": "select", "selectedProgramme": pid}).status_code, 403)

    def test_explicit_confirmation_is_persisted_for_all_four_programmes(self):
        for pathway, index, expected in [("marketing", 0, "ME_L4"), ("marketing", 3, "MM_L6"),
                                         ("project-controls", 0, "APM_L4"), ("project-controls", 3, "PCP_L6")]:
            with self.subTest(programme=expected):
                Assessment.objects.all().delete()
                submitted = self.start(pathway=pathway, responses=answers(self.config, pathway, indices=(index,)), action="submit")
                original = Assessment.objects.get()
                reopened = json.loads(self.request(method="GET").content)
                self.assertEqual(reopened["selectionStatus"], "SUBMITTED_PENDING_CONFIRMATION")
                self.assertIsNone(original.selection_confirmed_at)
                with patch("enrolment_api.wizard_layout.current_layout", return_value={KEY: self.config}):
                    self.assertIsNotNone(submission_error(self.learner))
                    with patch.object(views, "calculate", side_effect=AssertionError("No rescoring")):
                        confirmed = self.request({"revision": submitted["revision"], "action": "continue"})
                    self.assertEqual(confirmed.status_code, 200, confirmed.content)
                    if expected == 'PCP_L6':
                        self.assertIsNotNone(submission_error(self.learner))
                    else:
                        self.assertIsNone(submission_error(self.learner))
                persisted = json.loads(self.request(method="GET").content)
                self.assertEqual(persisted["selectionStatus"], "SELECTION_CONFIRMED")
                self.assertTrue(persisted["selectionConfirmedAt"])
                self.assertEqual(persisted["selectedProgramme"], expected)
                self.assertEqual(Assessment.objects.get().result, original.result)
                revision = Assessment.objects.get().revision
                self.assertEqual(self.request({"revision": submitted["revision"], "action": "continue"}).status_code, 200)
                self.assertEqual(Assessment.objects.get().revision, revision)

    def test_alternative_confirmation_locks_choice_and_preserves_original_result(self):
        for pathway, high, low in [("marketing", "MM_L6", "ME_L4"), ("project-controls", "PCP_L6", "APM_L4")]:
            with self.subTest(pathway=pathway):
                Assessment.objects.all().delete()
                submitted = self.start(pathway=pathway, responses=answers(self.config, pathway, indices=(3,)), action="submit")
                original = Assessment.objects.get()
                saved = self.request({"revision": submitted["revision"], "action": "select", "selectedProgramme": low,
                                      "alternativeSelectionReason": "I prefer to focus on delivery in my current role."})
                self.assertEqual(saved.status_code, 200, saved.content)
                row = Assessment.objects.get()
                self.assertIsNotNone(row.selection_confirmed_at)
                self.assertEqual(row.result, original.result)
                self.assertEqual(row.result["recommendedProgramme"], high)
                self.assertEqual(row.selected_programme, low)
                confirmation_time = row.selection_confirmed_at
                # A lost-response retry is idempotent, even with the previous revision.
                self.assertEqual(self.request({"revision": original.revision, "action": "select", "selectedProgramme": low}).status_code, 200)
                for action in ("select", "continue", "draft", "submit", "request_review"):
                    self.assertEqual(self.request({"revision": row.revision, "action": action, "selectedProgramme": high}).status_code, 403)
                row.refresh_from_db()
                self.assertEqual(row.selected_programme, low)
                self.assertEqual(row.selection_confirmed_at, confirmation_time)

    def test_submitted_answers_and_pathway_are_immutable_before_and_after_selection(self):
        self.start(responses=answers(self.config), action="submit")
        before = Assessment.objects.get()
        for confirmed in (False, True):
            if confirmed:
                self.assertEqual(self.request({"revision": before.revision, "action": "continue"}).status_code, 200)
            row = Assessment.objects.get()
            for action in ("draft", "submit", "select", "continue", "request_review"):
                for changed in ({"responses": answers(self.config, indices=(3,))},
                                {"pathway": "project-controls", "responses": answers(self.config, "project-controls")}):
                    with self.subTest(confirmed=confirmed, action=action, change=changed):
                        self.assertEqual(self.request({"revision": row.revision, "action": action, **changed}).status_code, 403)
            self.assertEqual(self.request({"revision": row.revision, "action": "draft"}).status_code, 403)
            row.refresh_from_db()
            self.assertEqual(row.responses, before.responses)
            self.assertEqual(row.configuration, before.configuration)
            self.assertEqual(row.result, before.result)
            self.assertEqual(row.revision, 2 if confirmed else 1)

    def test_published_question_edits_do_not_change_submitted_answer_snapshot(self):
        self.start(responses=answers(self.config), action="submit")
        before = json.loads(self.request(method="GET").content)
        self.config["questions"][0]["title"] = "New published title"
        self.config["questions"][0]["description"] = "New published question"
        self.config["questions"][0]["answers"][0]["text"] = "Replacement option"
        after = json.loads(self.request(method="GET").content)
        self.assertEqual(after["configuration"], before["configuration"])
        self.assertEqual(after["responses"], before["responses"])

    def test_staff_audited_override_remains_available_after_confirmation(self):
        self.start(responses=answers(self.config), action="submit")
        self.request({"revision": 1, "action": "continue"})
        before = Assessment.objects.get()
        response = self.request({"revision": before.revision, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6",
                                 "reason": "Verified additional responsibilities."}, role="staff", view=views.review)
        self.assertEqual(response.status_code, 200, response.content)
        row = Assessment.objects.get()
        self.assertEqual(row.review["recommendedProgramme"], "MM_L6")
        self.assertEqual(row.history[-1]["action"], "ADMISSIONS_REVIEW")
        self.assertEqual(row.selection_confirmed_at, before.selection_confirmed_at)
        self.assertEqual(row.selected_programme, before.selected_programme)
        self.assertEqual(row.result, before.result)

    def test_raw_enrolment_submit_cannot_bypass_pending_selection(self):
        self.start(responses=answers(self.config), action="submit")
        self.test_existing_raw_enrolment_submit_endpoint_cannot_bypass_assessment()

    def test_client_confirmation_fields_do_not_confirm_selection(self):
        submitted = self.start(responses=answers(self.config), action="submit",
                               selectionStatus="SELECTION_CONFIRMED", selectionConfirmedAt="2026-11-01T12:00:00Z")
        self.assertEqual(submitted["selectionStatus"], "SUBMITTED_PENDING_CONFIRMATION")
        self.assertIsNone(Assessment.objects.get().selection_confirmed_at)

    def test_new_alternative_selections_require_a_valid_reason_atomically(self):
        for pathway, low in [("marketing", "ME_L4"), ("project-controls", "APM_L4")]:
            Assessment.objects.all().delete()
            self.start(pathway=pathway, responses=answers(self.config, pathway, (3,)), action="submit")
            original = Assessment.objects.get()
            for extra in ({}, *({"alternativeSelectionReason": reason} for reason in (None, "", " \n\t ", 1, [], {}, "x" * 1001, "😀" * 501))):
                with self.subTest(pathway=pathway, extra=extra):
                    response = self.request({"revision": original.revision, "action": "select", "selectedProgramme": low, **extra})
                    self.assertEqual(response.status_code, 400, response.content)
                    row = Assessment.objects.get()
                    self.assertEqual(row.selected_programme, original.selected_programme)
                    self.assertIsNone(row.alternative_selection_reason)
                    self.assertIsNone(row.selection_confirmed_at)
                    self.assertEqual(row.revision, original.revision)
                    self.assertEqual(row.history, original.history)

    def test_alternative_reason_is_preserved_exactly_and_immutable_in_all_learner_actions(self):
        reason = "  I prefer hands-on delivery.\nThis matches my current role.  "
        for pathway, low in [("marketing", "ME_L4"), ("project-controls", "APM_L4")]:
            Assessment.objects.all().delete()
            self.start(pathway=pathway, responses=answers(self.config, pathway, (3,)), action="submit")
            original = Assessment.objects.get()
            request_data = {"revision": original.revision, "action": "select", "selectedProgramme": low,
                            "alternativeSelectionReason": reason}
            response = self.request(request_data)
            self.assertEqual(response.status_code, 200, response.content)
            row = Assessment.objects.get()
            self.assertIsNotNone(row.selection_confirmed_at)
            self.assertEqual(row.alternative_selection_reason, reason)
            self.assertEqual(row.result, original.result)
            self.assertEqual(row.responses, original.responses)
            self.assertEqual(json.loads(self.request(method="GET").content)["alternativeSelectionReason"], reason)
            self.assertEqual(self.request(request_data).status_code, 200)
            for action in ("draft", "submit", "continue", "select", "request_review"):
                for changed in (None, "", "Different reason"):
                    response = self.request({"revision": row.revision, "action": action, "selectedProgramme": low,
                                             "alternativeSelectionReason": changed})
                    self.assertEqual(response.status_code, 403, response.content)
            row.refresh_from_db()
            self.assertEqual(row.alternative_selection_reason, reason)
            self.assertEqual(row.revision, original.revision + 1)

    def test_reason_length_limit_accepts_1000_and_recommendation_needs_no_reason(self):
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        response = self.request({"revision": 1, "action": "select", "selectedProgramme": "ME_L4", "alternativeSelectionReason": "x" * 1000})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(Assessment.objects.get().alternative_selection_reason), 1000)
        Assessment.objects.all().delete()
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 200)
        self.assertIsNone(Assessment.objects.get().alternative_selection_reason)

    def test_old_confirmed_alternative_without_a_reason_remains_readable_and_locked(self):
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        row = Assessment.objects.get()
        row.selected_programme = "ME_L4"
        row.selection_confirmed_at = row.confirmed_at
        row.save()
        data = json.loads(self.request(method="GET").content)
        self.assertEqual(data["selectionStatus"], "SELECTION_CONFIRMED")
        self.assertIsNone(data["alternativeSelectionReason"])
        self.assertEqual(self.request({"revision": row.revision, "action": "select", "selectedProgramme": "ME_L4"}).status_code, 200)
        self.assertEqual(self.request({"revision": row.revision, "action": "select", "selectedProgramme": "ME_L4",
                                       "alternativeSelectionReason": "Retrospective reason"}).status_code, 403)

    def test_reason_is_available_to_admissions_and_preserved_in_existing_audit(self):
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        reason = "My current role focuses on practical delivery."
        self.request({"revision": 1, "action": "select", "selectedProgramme": "ME_L4", "alternativeSelectionReason": reason})
        row = Assessment.objects.get()
        response = self.request({"revision": row.revision, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6",
                                 "reason": "Verified responsibilities."}, role="staff", view=views.review)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["alternativeSelectionReason"], reason)
        self.assertEqual(Assessment.objects.get().history[-1]["alternativeSelectionReason"], reason)

    def test_reason_cannot_be_saved_separately_from_selection(self):
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        self.assertEqual(self.request({"revision": 1, "action": "continue", "alternativeSelectionReason": "Standalone reason"}).status_code, 400)
        row = Assessment.objects.get()
        self.assertIsNone(row.alternative_selection_reason)
        self.assertIsNone(row.selection_confirmed_at)

    def test_critical_indicator_failure_is_internal_and_does_not_block_the_score_winner(self):
        responses = answers(self.config, indices=(3,))
        responses["M03"] = []; responses["M06"] = []
        submitted = self.start(responses=responses, action="submit")
        self.assertGreater(submitted["result"]["scores"]["MM_L6"], 60)
        self.assertEqual(submitted["result"]["outcome"], "RECOMMENDED_MM_L6")
        self.assertEqual(submitted["result"]["admissionsReviewOutcome"], "FURTHER_REVIEW_REQUIRED")
        self.assertEqual(submitted["result"]["recommendedProgramme"], "MM_L6")
        self.assertEqual(submitted["selectedProgramme"], "MM_L6")
        self.assertEqual(submitted["completionStatus"], "SUBMITTED")

    def test_equal_scores_default_to_level_four_and_record_the_tie_internally(self):
        submitted = self.start(responses=answers(self.config, indices=(0, 1, 2, 3)), action="submit")
        self.assertEqual(submitted["result"]["outcome"], "RECOMMENDED_ME_L4")
        self.assertEqual(submitted["recommendedProgramme"], "ME_L4")
        self.assertEqual(submitted["selectedProgramme"], "ME_L4")
        self.assertIn("SCORE_TIE_LEVEL_4_DEFAULT", submitted["result"]["recommendationFlags"])
        self.assertEqual(submitted["assessmentStatus"], "SUBMITTED")

    def test_continue_requires_submission_and_an_already_saved_selection(self):
        self.start(responses=answers(self.config, indices=(3,)))
        self.assertEqual(self.request({"revision": 1, "action": "continue"}).status_code, 400)
        self.request({"revision": 1, "action": "submit"})
        row = Assessment.objects.get()
        self.assertEqual(self.request({"revision": row.revision, "action": "continue", "selectedProgramme": "ME_L4"}).status_code, 400)
        self.assertEqual(self.request({"revision": row.revision, "action": "continue"}).status_code, 200)

    def test_historical_selection_is_retained_without_silently_applying_new_selection_policy(self):
        self.start(responses=answers(self.config), action="submit")
        row = Assessment.objects.get()
        row.selected_programme = "MM_L6"; row.original_preference = "MM_L6"; row.programme_selection_source = "LEGACY"; row.save()
        self.assertEqual(json.loads(self.request(method="GET").content)["selectedProgramme"], "MM_L6")
        response = self.request({"revision": row.revision, "action": "submit", "selectedProgramme": ""})
        self.assertEqual(json.loads(response.content)["selectedProgramme"], "MM_L6")
        self.assertEqual(Assessment.objects.get().programme_selection_source, "LEGACY")

    def test_learner_review_request_is_persistent_and_preserved_by_authorised_staff_override(self):
        self.start(responses=answers(self.config), action="submit")
        response = self.request({"revision": 1, "action": "request_review", "reason": "Please review my additional strategic duties."})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Assessment.objects.get().review["learnerRequest"]["reason"], "Please review my additional strategic duties.")
        response = self.request({"revision": 2, "status": "REVIEW_COMPLETED", "recommendedProgramme": "MM_L6", "reason": "Additional duties verified."}, role="staff", view=views.review)
        self.assertEqual(response.status_code, 200)
        self.assertIn("learnerRequest", Assessment.objects.get().review)
        self.assertEqual(Assessment.objects.get().result["recommendedProgramme"], "ME_L4")

    def test_selections_from_another_pathway_are_rejected(self):
        self.start(responses=answers(self.config, indices=(3,)), action="submit")
        self.assertEqual(self.request({"revision": 1, "action": "select", "selectedProgramme": "APM_L4"}).status_code, 400)

    def test_actual_80_85_vs_52_96_weighted_scores_recommend_mm_despite_critical_requirements(self):
        self.config["rules"]["minimumCriticalIndicators"] = 4
        for question in self.config["questions"]:
            if question["pathway"] == "marketing":
                question["answers"][0]["affinities"] = {"ME_L4": 0.5296, "MM_L6": 0.8085}
        submitted = self.start(responses=answers(self.config, indices=(0,)), action="submit")
        self.assertEqual(submitted["result"]["scores"], {"ME_L4": 52.96, "MM_L6": 80.85})
        self.assertEqual(submitted["recommendedProgramme"], "MM_L6")
        self.assertEqual(submitted["selectedProgramme"], "MM_L6")
        self.assertEqual(submitted["result"]["admissionsReviewOutcome"], "FURTHER_REVIEW_REQUIRED")
        self.assertIn("MISSING_CRITICAL_LEVEL_6_EVIDENCE", submitted["result"]["admissionsFlags"])

    def test_ties_use_level_four_for_both_pathways(self):
        for pathway, low in [("marketing", "ME_L4"), ("project-controls", "APM_L4")]:
            Assessment.objects.all().delete()
            submitted = self.start(pathway=pathway, responses=answers(self.config, pathway, (0, 1, 2, 3)), action="submit")
            self.assertEqual(submitted["selectedProgramme"], low)
            self.assertIn("SCORE_TIE_LEVEL_4_DEFAULT", submitted["result"]["recommendationFlags"])

    def test_existing_positive_review_result_uses_saved_scores_and_continue_persists_the_default(self):
        self.start(responses={qid: [] for qid in answers(self.config)}, action="submit")
        row = Assessment.objects.get()
        row.result.update(outcome="FURTHER_REVIEW_REQUIRED", recommendedProgramme=None,
                          scores={"ME_L4": 52.96, "MM_L6": 80.85}, reasons=["MISSING_CRITICAL_LEVEL_6_EVIDENCE"])
        for key in ("recommendationPolicy", "admissionsReviewOutcome", "admissionsFlags"):
            row.result.pop(key, None)
        row.save()
        before = row.result.copy()
        with patch.object(views, "calculate", side_effect=AssertionError("Do not rescore saved answers")):
            response = self.request(method="GET")
            data = json.loads(response.content)
            self.assertEqual(data["recommendedProgramme"], "MM_L6")
            self.assertEqual(data["result"]["scores"]["MM_L6"], 80.85)
            self.assertEqual(Assessment.objects.get().result, before)
            continued = self.request({"revision": row.revision, "action": "continue"})
        self.assertEqual(continued.status_code, 200, continued.content)
        saved = Assessment.objects.get()
        self.assertEqual(saved.selected_programme, "MM_L6")
        self.assertEqual(saved.result["scores"], before["scores"])
        self.assertEqual(saved.history[-1]["result"], before)

    def test_score_threshold_does_not_suppress_a_valid_low_matching_index(self):
        submitted = self.start(responses=answers(self.config, indices=(0,)), action="submit")
        self.assertEqual(submitted["result"]["scores"]["ME_L4"], 40)
        self.assertEqual(submitted["recommendedProgramme"], "ME_L4")
        self.assertIn("BELOW_MINIMUM_MATCH", submitted["result"]["admissionsFlags"])
