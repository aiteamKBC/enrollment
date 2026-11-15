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

    def test_approval_preserves_screening_and_links_new_learner(self):
        application = {'id': 42, 'eligible': False, 'work_email': 'sam@example.test'}
        cursor = Cursor([(application,), (1,), None])
        learner = SimpleNamespace(pk=812)
        login = SimpleNamespace(subject_type='learner', subject_id=812, is_active=True, has_password=False)
        request = self.factory.post('/enrolment_api/eligibility-applications/42/exception/',
            data=json.dumps({'reason': 'Employer evidence was checked by Admissions',
                             'confirmEmail': 'sam@example.test'}), content_type='application/json')
        request.COOKIES['enrollment_csrftoken'] = 'a' * 32
        request.META['HTTP_X_CSRFTOKEN'] = 'a' * 32
        with patch.object(api, '_candidate', return_value=None), \
             patch.object(api.EnrolmentUser.all_learners, 'create', return_value=learner) as create, \
             patch.object(api, 'learner_fields', return_value={'email': 'sam@example.test'}), \
             patch.object(api, 'ensure_account', return_value=(login, True)), \
             patch.object(api.Invitation.objects, 'filter', return_value=SimpleNamespace(exists=lambda: False)), \
             patch.object(api, 'invite_subject', return_value={'emailSent': True, 'error': None}) as invite:
            response = self.invoke(request, cursor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['invitation'], 'sent')
        create.assert_called_once_with(email='sam@example.test')
        invite.assert_called_once()
        self.assertTrue(any('INSERT INTO enrolment.eligibility_learner_links' in sql and
            values == [42, 812, 7, 'Employer evidence was checked by Admissions']
            for sql, values in cursor.statements))
        self.assertFalse(any('UPDATE enrolment.eligibility_form' in sql for sql, _ in cursor.statements))

    def test_accepted_link_advances_only_the_learner_status(self):
        cursor = Cursor([(links.TABLE,), (812,)])
        connection = SimpleNamespace(cursor=lambda: cursor)
        queryset = MagicMock()
        with patch.object(links, 'connections', {'enrolment': connection}), \
             patch.object(links.transaction, 'atomic', lambda **_: nullcontext()), \
             patch('learner_api.models.EnrolmentUser.all_learners.filter', return_value=queryset) as selected:
            links.mark_activated(SimpleNamespace(subject_type='learner', subject_id=812))
        selected.assert_called_once_with(pk=812)
        queryset.filter.assert_called_once()
        queryset.filter.return_value.update.assert_called_once_with(programme_status='Onboarding')

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
             patch('learner_api.models.EnrolmentUser.all_learners.filter', return_value=query):
            query.values.return_value.first.return_value = {'learner_type': 'apprenticeship', 'programme_status': 'Fresh user'}
            response = protected(request, learner_id=812)
            self.assertEqual(response.status_code, 403)
            query.values.return_value.first.return_value = {'learner_type': 'apprenticeship', 'programme_status': 'Onboarding'}
            self.assertEqual(protected(request, learner_id=812).status_code, 200)
