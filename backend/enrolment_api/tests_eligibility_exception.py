"""Focused exception and activation checks; no live database or email."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase
from django.http import JsonResponse

from . import auth as enrolment_auth, eligibility_exception as api, eligibility_link as links
from .eligibility_invitation import learner_fields
from learner_api import learning_plan


class Cursor:
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))

    def fetchone(self):
        return next(self.results)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ExceptionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = SimpleNamespace(pk=7, role="admin", subject_type="staff", subject_id=7)

    def invoke(self, request, cursor):
        def authenticate(req):
            req.login_account = self.account
            return self.account
        connection = SimpleNamespace(cursor=lambda: cursor)
        with patch('login.permissions.authenticate_request', side_effect=authenticate), \
             patch('login.permissions._accesses_of', return_value=frozenset({'super-admin'})), \
             patch.object(api, 'connections', {'enrolment': connection}), \
             patch.object(api.transaction, 'atomic', lambda **_: nullcontext()):
            return api.eligibility_exception(request, application_id=42)

    def test_staff_without_admin_role_cannot_approve(self):
        self.account.role = 'staff'
        response = self.invoke(self.factory.post('/enrolment_api/eligibility-applications/42/exception/',
            data=json.dumps({'reason': 'A valid documented exception', 'confirmEmail': 'sam@example.test'}),
            content_type='application/json'), Cursor([]))
        self.assertEqual(response.status_code, 403)

    def test_saved_json_date_can_create_learner(self):
        fields = learner_fields({'first_name': 'Sam', 'surname': 'Example',
            'work_email': 'sam@example.test', 'date_of_birth': '2001-04-23'})
        self.assertEqual(fields['date_of_birth'], '2001-04-23')

    def test_approval_preserves_screening_and_stages_invitation(self):
        application = {'id': 42, 'eligible': False, 'work_email': 'sam@example.test'}
        cursor = Cursor([(application,), (1,), None])
        request = self.factory.post('/enrolment_api/eligibility-applications/42/exception/',
            data=json.dumps({'reason': 'Employer evidence was checked by Admissions',
                             'confirmEmail': 'sam@example.test'}), content_type='application/json')
        request.COOKIES['enrollment_csrftoken'] = 'a' * 32
        request.META['HTTP_X_CSRFTOKEN'] = 'a' * 32
        with patch.object(api, '_candidate', return_value=None), \
             patch.object(api, 'has_account', return_value=False), \
             patch.object(api, 'dispatch', return_value='invited') as invite:
            response = self.invoke(request, cursor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['invitation'], 'sent')
        invite.assert_called_once()
        self.assertTrue(any('INSERT INTO enrolment.eligibility_learner_links' in sql and
            values == [42, 7, 'Employer evidence was checked by Admissions']
            for sql, values in cursor.statements))
        self.assertFalse(any('UPDATE enrolment.eligibility_form' in sql for sql, _ in cursor.statements))

    def test_accepted_link_records_acceptance_without_advancing_status(self):
        cursor = Cursor([(links.TABLE,), (812,)])
        connection = SimpleNamespace(cursor=lambda: cursor)
        with patch.object(links, 'connections', {'enrolment': connection}), \
             patch.object(links.transaction, 'atomic', lambda **_: nullcontext()):
            links.mark_activated(SimpleNamespace(subject_type='learner', subject_id=812))
        self.assertTrue(any('accepted_at=COALESCE' in sql and values == [812]
                            for sql, values in cursor.statements))

    def test_old_signed_reviews_cannot_promote_a_fresh_learner(self):
        learner = SimpleNamespace(programme_status='Fresh user', save=MagicMock())
        with patch.object(learning_plan, 'onboarding_complete', return_value=True), \
             patch.object(learning_plan.EnrolmentUser.all_learners, 'filter') as selected:
            selected.return_value.first.return_value = learner
            self.assertIsNone(learning_plan.promote_learner_if_ready('apprenticeship', 812))
        learner.save.assert_not_called()
        self.assertEqual(learner.programme_status, 'Fresh user')

    def test_fresh_apprentice_cannot_call_enrolment_api_with_a_session(self):
        account = SimpleNamespace(role='learner', subject_id=812, is_active=True)
        request = self.factory.get('/enrolment_api/wizard-bootstrap/apprenticeship/812/')
        request.login_account = account
        protected = enrolment_auth.enrolment_login_required(lambda _request, **_: JsonResponse({'opened': True}))
        # functools.wraps does not replace a lambda's __name__; the route has
        # a learner id, so this exercises the same status branch as bootstrap.
        query = MagicMock()
        with patch.object(enrolment_auth, '_may_access', return_value=True), \
             patch.object(links, 'may_enter_onboarding', return_value=False), \
             patch('learner_api.models.EnrolmentUser.all_learners.filter', return_value=query):
            query.values.return_value.first.return_value = {'learner_type': 'apprenticeship', 'programme_status': 'Fresh user'}
            response = protected(request, learner_id=812)
            self.assertEqual(response.status_code, 403)
            query.values.return_value.first.return_value = {'learner_type': 'apprenticeship', 'programme_status': 'Onboarding'}
            self.assertEqual(protected(request, learner_id=812).status_code, 200)
