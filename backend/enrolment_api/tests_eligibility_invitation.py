"""An eligible applicant on the public eligibility form becomes a learner and is
invited to set their password; anyone else is not.

SimpleTestCase throughout: the learner table, the login account and the mail
transport are all stubbed, so no query reaches a database and no email is sent.
"""
import json
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import DatabaseError, IntegrityError
from django.test import SimpleTestCase

from enrolment_api import eligibility_form as ef
from enrolment_api import eligibility_invitation as ei
from enrolment_api.tests_eligibility_form import FakeCursor, URL, application, stub_lookup
from learner_api.models import EnrolmentUser
from login import services
from login.models import SUBJECT_LEARNER

ROW = {
    "first_name": "Sam",
    "surname": "Example",
    "work_email": "sam@employer.example",
    "phone_number": None,
    "full_address": "1 Example Street",
    "postcode": "EX1 2MP",
    "date_of_birth": date(1995, 4, 3),
    "employer_name": "Example Ltd",
    "employer_address": "2 Business Park",
    "programme": "Associate Project Manager Level 4",
}


def started(test, target, attribute, **kwargs):
    p = patch.object(target, attribute, **kwargs)
    test.addCleanup(p.stop)
    return p.start()


class SubmitInvitesTests(SimpleTestCase):
    """Which submissions the form sends on to be invited."""

    def setUp(self):
        from django.test import RequestFactory

        self.factory = RequestFactory()
        self.lookup = stub_lookup(self)
        started(self, ef, "_published_layout", return_value=(None, None))
        self.invite = started(self, ei, "invite_applicant", return_value=ei.INVITED)

    def post(self, body):
        request = self.factory.post(
            URL, data=json.dumps(body), content_type="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        cursor = FakeCursor()
        with patch.object(ef, "connections", {ef.CONN: SimpleNamespace(cursor=lambda: cursor)}):
            response = ef.submit_application(request)
        return response, json.loads(response.content), cursor

    def test_an_eligible_applicant_is_saved_then_invited(self):
        response, data, cursor = self.post(application())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(data, {"submitted": True, "eligible": True, "reasons": [], "outcome": "invited"})
        self.assertEqual(len(cursor.executed), 1)
        self.invite.assert_called_once()
        row = self.invite.call_args.args[0]
        self.assertEqual((row["work_email"], row["eligible"]), ("sam@employer.example", True))

    def test_the_outcome_of_the_invitation_is_reported(self):
        for outcome in (ei.INVITE_PENDING, ei.EXISTING_ACCOUNT, ei.RETRY_LATER):
            self.invite.return_value = outcome
            self.assertEqual(self.post(application())[1]["outcome"], outcome)

    def test_an_applicant_ruled_out_by_postcode_is_not_invited(self):
        _response, data, cursor = self.post(application(postcode="EH1 1YZ"))
        self.assertEqual(data["outcome"], "notEligible")
        self.assertEqual(data["reasons"], ["Your postcode (EH1 1YZ) is outside England (Scotland)."])
        self.assertEqual(len(cursor.executed), 1)
        self.invite.assert_not_called()

    def test_postcodes_that_could_not_be_checked_invite_nobody(self):
        from enrolment_api import postcode_lookup

        def down(_postcode):
            raise postcode_lookup.PostcodeLookupUnavailable("timed out")

        self.lookup.side_effect = down
        with self.assertLogs("enrolment_api.eligibility_form", level="WARNING"):
            _response, data, cursor = self.post(application())
        self.assertEqual((data["eligible"], data["outcome"]), (None, "retryLater"))
        # Still saved, as unchecked.
        self.assertEqual(len(cursor.executed), 1)
        self.invite.assert_not_called()

    def test_invalid_answers_invite_nobody(self):
        response, _data, _cursor = self.post(application(surname=""))
        self.assertEqual(response.status_code, 400)
        self.invite.assert_not_called()


class LearnerFieldsTests(SimpleTestCase):
    def test_an_application_becomes_an_apprenticeship_learner(self):
        fields = ei.learner_fields(ROW)
        self.assertEqual(fields["learner_type"], "apprenticeship")
        self.assertEqual(fields["username"], "Sam Example")
        self.assertEqual(fields["email"], "sam@employer.example")
        self.assertEqual(fields["date_of_birth"], "1995-04-03")
        self.assertEqual((fields["address"], fields["current_postcode"]), ("1 Example Street", "EX1 2MP"))
        self.assertEqual((fields["employer"], fields["employer_address"]), ("Example Ltd", "2 Business Park"))
        self.assertEqual(fields["programme_status"], "Fresh user")
        self.assertIs(fields["invite_to_platform"], True)
        self.assertTrue(fields["enrolled_time_and_user"].endswith(" by Eligibility form"))

    def test_the_chosen_standard_is_a_target_not_a_placement(self):
        fields = ei.learner_fields(ROW)
        self.assertEqual(fields["target_programme"], "Associate Project Manager Level 4")
        for placement in ("programme", "cohort", "group", "start_date", "end_date"):
            self.assertNotIn(placement, fields)

    def test_a_date_of_birth_the_form_did_not_ask_is_left_empty(self):
        self.assertIsNone(ei.learner_fields({**ROW, "date_of_birth": None})["date_of_birth"])


class InviteApplicantTests(SimpleTestCase):
    def setUp(self):
        self.has_account = started(self, ei, "has_account", return_value=False)
        started(self, ei, "transaction", new=SimpleNamespace(atomic=lambda using=None: nullcontext()))
        self.learners = started(self, EnrolmentUser, "all_learners", new=MagicMock())
        self.learner = SimpleNamespace(id=812)
        self.learners.create.return_value = self.learner
        from learner_api import learner_progression

        self.advance = started(self, learner_progression, "advance_learner")
        self.send = started(self, services, "invite_eligible_applicant", return_value=(True, None))

    def test_creates_the_learner_and_sends_the_invitation(self):
        self.assertEqual(ei.invite_applicant(ROW, ip="203.0.113.5", user_agent="UA"), ei.INVITED)
        created = dict(self.learners.create.call_args.kwargs)
        expected = ei.learner_fields(ROW)
        # Stamped with the time of creation.
        created.pop("enrolled_time_and_user"), expected.pop("enrolled_time_and_user")
        self.assertEqual(created, expected)
        self.advance.assert_called_once_with(self.learner)
        self.send.assert_called_once_with(self.learner, ip="203.0.113.5", user_agent="UA")

    def test_an_address_with_an_account_is_neither_created_nor_invited(self):
        self.has_account.return_value = True
        self.assertEqual(ei.invite_applicant(ROW), ei.EXISTING_ACCOUNT)
        self.learners.create.assert_not_called()
        self.send.assert_not_called()

    def test_the_same_address_submitted_twice_at_once_is_an_existing_account(self):
        self.learners.create.side_effect = IntegrityError("duplicate key")
        self.assertEqual(ei.invite_applicant(ROW), ei.EXISTING_ACCOUNT)
        self.send.assert_not_called()

    def test_a_learner_that_could_not_be_created_asks_for_a_retry(self):
        self.learners.create.side_effect = DatabaseError("boom")
        with self.assertLogs("enrolment_api.eligibility_invitation", level="ERROR"):
            self.assertEqual(ei.invite_applicant(ROW), ei.RETRY_LATER)
        self.send.assert_not_called()

    def test_an_email_that_could_not_be_sent_is_pending_not_invited(self):
        self.send.return_value = (False, "not-configured: no mail credentials")
        with self.assertLogs("enrolment_api.eligibility_invitation", level="WARNING"):
            self.assertEqual(ei.invite_applicant(ROW), ei.INVITE_PENDING)

    def test_an_account_failure_after_creation_is_pending(self):
        from login.identity import AccountError

        self.send.side_effect = AccountError("Another learner account already uses this address.")
        with self.assertLogs("enrolment_api.eligibility_invitation", level="ERROR"):
            self.assertEqual(ei.invite_applicant(ROW), ei.INVITE_PENDING)

    def test_an_address_that_already_signs_in_is_reported_as_an_existing_account(self):
        self.send.return_value = (False, "existing-account")
        self.assertEqual(ei.invite_applicant(ROW), ei.EXISTING_ACCOUNT)


class HasAccountTests(SimpleTestCase):
    def setUp(self):
        from login.models import LoginAccount

        self.accounts = started(self, LoginAccount, "objects", new=MagicMock())
        self.learners = started(self, EnrolmentUser, "all_learners", new=MagicMock())
        self.accounts.filter.return_value.exists.return_value = False
        self.learners.annotate.return_value.filter.return_value.exists.return_value = False

    def test_a_login_account_counts_whatever_the_case_of_the_address(self):
        self.accounts.filter.return_value.exists.return_value = True
        self.assertTrue(ei.has_account("  Sam@Employer.Example "))
        self.accounts.filter.assert_called_once_with(email="sam@employer.example")

    def test_a_learner_record_without_an_account_counts(self):
        self.learners.annotate.return_value.filter.return_value.exists.return_value = True
        self.assertTrue(ei.has_account("sam@employer.example"))
        self.learners.annotate.return_value.filter.assert_called_once_with(_normalised_email="sam@employer.example")

    def test_a_new_address_has_no_account(self):
        self.assertFalse(ei.has_account("new@employer.example"))


class InviteEligibleApplicantTests(SimpleTestCase):
    """login.services.invite_eligible_applicant: the one invitation with no inviter."""

    def setUp(self):
        self.learner = SimpleNamespace(id=812)
        self.account = SimpleNamespace(has_password=False, subject_type=SUBJECT_LEARNER)
        self.ensure = started(self, services, "ensure_account", return_value=(self.account, True))
        self.send = started(self, services, "send_invitation", return_value=(object(), True, None))

    def test_a_new_learner_account_is_sent_the_invitation(self):
        self.assertEqual(services.invite_eligible_applicant(self.learner, ip="ip", user_agent="ua"), (True, None))
        self.ensure.assert_called_once_with(SUBJECT_LEARNER, 812, subject=self.learner)
        self.send.assert_called_once_with(self.account, invited_by="eligibility form", ip="ip", user_agent="ua")

    def test_an_existing_account_is_never_sent_a_set_password_link(self):
        self.ensure.return_value = (self.account, False)
        self.assertEqual(services.invite_eligible_applicant(self.learner), (False, "existing-account"))
        self.send.assert_not_called()

    def test_an_account_with_a_password_or_another_role_is_never_invited(self):
        for account in (SimpleNamespace(has_password=True, subject_type=SUBJECT_LEARNER),
                        SimpleNamespace(has_password=False, subject_type="staff")):
            self.ensure.return_value = (account, True)
            self.assertEqual(services.invite_eligible_applicant(self.learner), (False, "existing-account"))
        self.send.assert_not_called()

    def test_a_mail_failure_is_returned_not_raised(self):
        self.send.return_value = (object(), False, "graph-error: 503")
        self.assertEqual(services.invite_eligible_applicant(self.learner), (False, "graph-error: 503"))
