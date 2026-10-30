"""An employer signs their own learner's compliance documents — and only as the
employer.

The Written Agreement, Training Plan and Apprenticeship Agreement sign views sit
behind enrolment_login_required, which answered every employer with 404 "Not
found.", so the employer's Sign button could never work.

SimpleTestCase with the database layer mocked: no query reaches a database.
"""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import auth
from learner_api import apprenticeship_agreement, training_plan_document, written_agreement

SIGN_VIEWS = (
    (written_agreement.sign_written_agreement, "learner"),
    (training_plan_document.sign_training_plan, "apprentice"),
    (apprenticeship_agreement.sign_agreement, "apprentice"),
)


def _employer(subject_id=7):
    return SimpleNamespace(role="employer", subject_id=subject_id)


class AccessTests(SimpleTestCase):
    def may_access(self, account, view_name, learner_employer_id):
        with patch("learner_api.models.EnrolmentUser.all_learners") as learners:
            learners.filter.return_value.values_list.return_value.first.return_value = learner_employer_id
            return auth._may_access(SimpleNamespace(login_account=account), view_name, {"pk": 670})

    def test_an_employer_may_sign_for_a_learner_they_employ(self):
        for view_name in auth.EMPLOYER_LEARNER_VIEWS:
            self.assertTrue(self.may_access(_employer(7), view_name, 7), view_name)

    def test_not_for_another_employers_learner(self):
        self.assertFalse(self.may_access(_employer(8), "sign_written_agreement", 7))

    def test_not_for_a_learner_with_no_employer(self):
        self.assertFalse(self.may_access(_employer(7), "sign_training_plan", None))

    def test_no_other_enrolment_view_opens_up(self):
        self.assertFalse(self.may_access(_employer(7), "issue_written_agreement", 7))
        self.assertFalse(self.may_access(_employer(7), "written_agreement", 7))


class PartyTests(SimpleTestCase):
    def post(self, view, party):
        request = RequestFactory().post(
            "/", data=json.dumps({"party": party, "name": "Test Employer", "signature": ""}),
            content_type="application/json",
        )
        request.login_account = _employer(7)
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}), \
                patch.object(auth, "is_authenticated", return_value=True), \
                patch.object(auth, "_employs", return_value=True):
            return view(request, pk=670)

    def test_an_employer_cannot_sign_as_another_party(self):
        for view, other_party in SIGN_VIEWS:
            response = self.post(view, other_party)
            self.assertEqual(response.status_code, 403, view.__name__)
            self.assertIn("only sign as the employer", json.loads(response.content)["error"])
        # And not as the provider, where the document has one.
        self.assertEqual(self.post(written_agreement.sign_written_agreement, "provider").status_code, 403)

    def test_the_party_rule_leaves_other_accounts_alone(self):
        request = SimpleNamespace(login_account=SimpleNamespace(role="admin", subject_id=1))
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}):
            self.assertIsNone(auth.employer_signing_party_error(request, "provider"))
            self.assertIsNone(auth.employer_signing_party_error(SimpleNamespace(login_account=_employer()), "employer"))
