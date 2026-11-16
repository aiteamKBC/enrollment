import json
from django.test import Client, TestCase
from django.utils import timezone
from datetime import timedelta

from learner_api.models import EnrolmentUser
from login.models import LoginAccount
from login.sessions import issue_session, COOKIE_NAME
from enrolment_api.models import WizardCvJob


class SuitabilityAuthFixtureMixin:
    def make_identity(self, role, email):
        account = LoginAccount.objects.create(subject_type='learner', subject_id=1601, email=email, display_name=role.title(), role=role, password_hash='', is_active=True)
        token, session, _ = issue_session(account, user_agent='suitability-test')
        client = Client(SERVER_NAME='testserver')
        client.cookies[COOKIE_NAME] = token
        return client, account, session

    def setUp(self):
        super().setUp()
        self.learner = EnrolmentUser.objects.create(id=1601, learner_type='apprenticeship', username='synthetic-1601', email='learner@example.invalid', programme='Project Manager Level 5')
        WizardCvJob.objects.create(learner_kind='apprenticeship', learner_id=1601, documents=[{'id': '00000000-0000-0000-0000-000000000001', 'docKind': 'cv', 'container': 'synthetic-approved', 'blobName': '1601/cv.pdf', 'filename': 'cv.pdf'}])
        WizardCvJob.objects.using('enrolment').create(learner_kind='apprenticeship', learner_id=1601, documents=[{'id': '00000000-0000-0000-0000-000000000001', 'docKind': 'cv', 'container': 'synthetic-approved', 'blobName': '1601/cv.pdf', 'filename': 'cv.pdf'}])
        self.admin_client, self.admin, _ = self.make_identity('admin', 'admin@example.invalid')
        self.staff_client, self.staff, _ = self.make_identity('staff', 'staff@example.invalid')


class SuitabilityAuthenticatedHttpSmokeTests(SuitabilityAuthFixtureMixin, TestCase):
    databases = {'default', 'enrolment'}

    def test_authenticated_admin_can_access_and_persist_report(self):
        url = '/apprenticeship/1601/suitability-rpl/'
        response = self.admin_client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('evidence', payload)
        save = self.admin_client.put(url, data=json.dumps({'revision': 0, 'relevantPriorLearning': False, 'reductionJustified': False, 'assessorEvidenceReview': 'Synthetic review', 'noReductionReason': 'No verified overlap', 'suitabilityOutcome': 'suitable', 'finalSummary': 'Proceed', 'suitabilityJustification': 'Suitable', 'suitabilityReviewed': False, 'rplJustification': 'Full programme', 'rplReviewed': False, 'reductions': [], 'finalise': False}), content_type='application/json')
        self.assertEqual(save.status_code, 200)
        self.assertEqual(self.admin_client.get(url).status_code, 200)

    def test_non_admin_and_anonymous_are_rejected(self):
        url = '/apprenticeship/1601/suitability-rpl/'
        self.assertEqual(self.staff_client.get(url).status_code, 403)
        self.assertEqual(Client().get(url).status_code, 401)
