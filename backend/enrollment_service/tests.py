import copy
import inspect
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import Resolver404, resolve
from learner_api import calendar
from .handoff_contract import Conflict, authenticated, encode, signature, validate
from .lms_receiver import application


def payload():
    return {'version': 1, 'rows': {
        'enrolment.Created_users': [{'id': 101, 'uuid': '09874f06-87ec-464b-ad39-9cefdd935126', 'Email': 'synthetic@example.invalid', 'Learner_type': 'apprenticeship'}],
        'login.Login_accounts': [{'id': 202, 'Subject_type': 'learner', 'Subject_id': 101, 'Email': 'synthetic@example.invalid', 'Role': 'learner'}],
    }}


class HandoffContractTests(SimpleTestCase):
    def test_signature_rejects_modified_expired_and_missing_credentials(self):
        secret, body = 's' * 40, encode(payload())
        signed = signature(secret, '1234', body)
        self.assertTrue(authenticated(secret, '1234', signed, body, now=1235))
        for key, timestamp, message, now in [(secret, '1234', body+b' ', 1235), (secret, '1234', body, 1295), ('', '1234', body, 1235), (secret, 'bad', body, 1235)]:
            self.assertFalse(authenticated(key, timestamp, signed, message, now=now))

    def test_valid_identity_and_no_staff_transfer(self):
        self.assertEqual(validate(payload())['id'], 101)
        for column, value in [('Role', 'admin'), ('Subject_type', 'staff'), ('Subject_id', 999), ('Email', 'someone-else@example.invalid')]:
            changed = payload()
            changed['rows']['login.Login_accounts'][0][column] = value
            with self.assertRaises(Conflict): validate(changed)

    def test_linked_staff_contract_transfers_no_staff_account_or_permissions(self):
        changed = payload()
        changed['rows']['login.Login_accounts'] = []
        changed['existingStaff'] = {'uuid': '7a21702c-fd8a-4443-b352-36fcfef520af', 'email': 'synthetic@example.invalid'}
        self.assertEqual(validate(changed)['id'],101)
        changed['existingStaff']['email'] = 'other@example.invalid'
        with self.assertRaises(Conflict): validate(changed)

    def test_rejects_cross_identity_profile_link(self):
        changed=payload()
        changed['rows']['enrolment.Created_users'][0]['learner_id']=999
        with self.assertRaises(Conflict): validate(changed)

    def test_rejects_cross_learner_documents_plan_and_study_meetings(self):
        for table, row in [
            ('enrolment.Wizard_Personal_Details', {'id': 4, 'Learner_id': 999}),
            ('Learner.learner_training_plan_modules', {'id': 5, 'learner_id': 999}),
            ('Learner.learner_training_plan_weeks', {'id': 6, 'plan_module_id': 999}),
            ('Learner.coach_calendar_event', {'id': 7, 'learner_id': 101, 'event_type': 'mcr'}),
            ('login.Login_sessions', {'id': 8}),
        ]:
            changed = payload()
            changed['rows'][table] = [row]
            with self.assertRaises(Conflict): validate(changed)

    def test_receiver_refuses_unsigned_request_without_connecting(self):
        body = encode(payload())
        environ = {'PATH_INFO': '/handoff', 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)}
        status = Mock()
        with patch('enrollment_service.lms_receiver.psycopg.connect') as connect:
            application(environ, status)
        self.assertEqual(status.call_args.args[0], '401 Unauthorized')
        connect.assert_not_called()

    def test_receiver_does_not_follow_a_misconfigured_database_target(self):
        body = encode(payload())
        timestamp = '1234'
        environ = {'PATH_INFO': '/handoff', 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body), 'HTTP_X_ENROLLMENT_TIME': timestamp, 'HTTP_X_ENROLLMENT_SIGNATURE': signature('s'*40, timestamp, body)}
        status = Mock()
        with patch.dict(os.environ, {'LMS_HANDOFF_SECRET': 's'*40, 'LMS_HANDOFF_DATABASE_HOST': 'expected.invalid', 'LMS_HANDOFF_DATABASE_URL': 'postgresql://different.invalid/db'}), patch('enrollment_service.handoff_contract.time.time', return_value=1234), patch('enrollment_service.lms_receiver.psycopg.connect') as connect:
            application(environ, status)
        self.assertEqual(status.call_args.args[0], '503 Service Unavailable')
        connect.assert_not_called()


@override_settings(ENROLLMENT_ONLY=True)
class ServiceBoundaryTests(SimpleTestCase):
    @override_settings(LMS_URL='https://study.example.invalid')
    def test_progress_shift_stays_in_lms_and_preserves_identity_gate(self):
        route = resolve('/learner_api/module-shift/101/')
        for identity, expected in [(None,401),(SimpleNamespace(role='learner',subject_id=999),404),(SimpleNamespace(role='learner',subject_id=101),409)]:
            with patch('login.permissions.authenticate_request',return_value=identity):
                response=route.func(RequestFactory().post('/learner_api/module-shift/101/'),pk=101)
            self.assertEqual(response.status_code,expected)
            if expected==409:
                self.assertEqual(json.loads(response.content)['code'],'LMS_STUDY_REQUIRED')

    def test_study_routes_are_not_exposed(self):
        for path in ['/quiz_api/quizzes/', '/coach_api/coach/timetable/events/book', '/learner_api/assignment/submit/', '/curriculum_api/curriculum/teams-meetings/']:
            with self.assertRaises(Resolver404): resolve(path)
        self.assertEqual(resolve('/learner_api/lms-handoff/').url_name, 'enrollment-lms-handoff')

    def test_study_booking_fails_before_graph_for_both_learner_types(self):
        source = Mock()
        source.all_learners.filter.return_value.first.return_value = SimpleNamespace(id=101)
        with patch.object(calendar, 'SOURCE_MODELS', {'commercial': source, 'apprenticeship': source}), patch.object(calendar, '_calendar_profile', return_value=None), patch('coach_api.views.reserve_coach_calendar_booking') as reserve:
            for kind in ('commercial', 'apprenticeship'):
                request = RequestFactory().post('/book/', data=json.dumps({'sessionType': 'catch-up'}), content_type='application/json')
                response = inspect.unwrap(calendar.learner_calendar_book)(request, kind, 101)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(json.loads(response.content)['code'], 'LMS_MEETING_REQUIRED')
        reserve.assert_not_called()

    def test_only_onboarding_records_can_be_rescheduled_or_cancelled(self):
        for event_type, permitted in [('first-session', True), ('eligibility-review', True), ('catch-up', False), ('mcr', False)]:
            record = SimpleNamespace(event_type=event_type)
            with patch.object(calendar, '_learner_calendar_record', return_value=record):
                self.assertEqual(calendar._learner_booking_record('apprenticeship', 101, 'synthetic') is not None, permitted)

    def test_cookie_is_distinct_from_lms(self):
        from login.sessions import COOKIE_NAME
        self.assertEqual(COOKIE_NAME, 'enrollment_session')

    def test_handoff_csrf_bootstrap_accepts_learners_without_opening_staff_routes(self):
        from login.api_gate import refusal_for
        learner = SimpleNamespace(role='learner',subject_id=101)
        self.assertIsNone(refusal_for('/learner_api/enrollment-csrf/',learner))
        self.assertEqual(refusal_for('/coach_api/csrf',learner).status_code,403)
        response=resolve('/learner_api/enrollment-csrf/').func(RequestFactory().get('/learner_api/enrollment-csrf/'))
        self.assertEqual(response.status_code,200)
        self.assertTrue(json.loads(response.content)['csrfToken'])

    def test_bookings_keep_anonymous_and_other_learner_rejection(self):
        for identity, expected in [(None, 401), (SimpleNamespace(role='learner', subject_id=999), 404)]:
            with patch('login.permissions.authenticate_request', return_value=identity):
                response = calendar.learner_calendar_book(RequestFactory().post('/book/'), kind='apprenticeship', pk=101)
            self.assertEqual(response.status_code, expected)
