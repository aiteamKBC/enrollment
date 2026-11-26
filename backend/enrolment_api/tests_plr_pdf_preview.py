"""Synthetic PDF uploads against an isolated, network-blocked SQLite database."""
from contextlib import ExitStack
import json
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections, DatabaseError
from django.test import RequestFactory, TestCase, SimpleTestCase
from .models import ExtendedIlr, WizardPlr, WizardPlrRecord
from learner_api.models import EnrolmentUser
from .plr_pdf_preview import plr_pdf_preview
from .plr_fixtures import synthetic_pdf, synthetic_boundary_cases
from .plr_pdf import MAX_BYTES


class PdfPreviewTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.learner = EnrolmentUser.objects.create(username='Morgan Synthetic Learner',
            date_of_birth='1994-03-12', learner_type='apprenticeship', programme_status='Onboarding')
        self.scope = {'learner_kind': 'apprenticeship', 'learner_id': self.learner.pk}
        WizardPlr.objects.create(**self.scope, uln='1234509876')
        WizardPlrRecord.objects.create(**self.scope, record_ref='existing', subject='Keep this qualification', record_type='Manual')
        ExtendedIlr.objects.create(**self.scope, wizard_draft={'personalDetails': {'signature': 'existing'},
            'plr': {'uln': '1234509876', 'records': [{'id': 'existing', 'subject': 'Keep this qualification'}]}})

    def call(self, data=None, *, role='admin', name='synthetic.pdf', content_type='application/pdf', entered='', method='post', csrf=True, gate='1'):
        factory = RequestFactory()
        if method == 'post':
            req = factory.post('/preview/', {'file': SimpleUploadedFile(name, synthetic_pdf() if data is None else data,
                                                                       content_type=content_type), 'enteredUln': entered})
        else:
            req = getattr(factory, method)('/preview/')
        req._dont_enforce_csrf_checks = csrf
        req.user = SimpleNamespace(is_authenticated=role == 'admin', is_staff=role == 'admin')
        if role in ('learner', 'staff'):
            req.login_account = SimpleNamespace(is_active=True, role=role, subject_id=self.learner.pk,
                                                subject_type='learner' if role == 'learner' else 'staff')
        def forbid_write(execute, sql, params, many, context):
            if sql.lstrip().split()[0].upper() != 'SELECT':
                raise AssertionError('PDF preview attempted a database write.')
            return execute(sql, params, many, context)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': gate}))
            stack.enter_context(patch('enrolment_api.auth._accesses_of', return_value={'enrolment'}))
            for alias in connections:
                stack.enter_context(connections[alias].execute_wrapper(forbid_write))
            return plr_pdf_preview(req, kind='apprenticeship', learner_id=self.learner.pk)

    def test_synthetic_upload_mapping_statuses_and_no_database_writes(self):
        before = list(WizardPlrRecord.objects.values())
        saved = ExtendedIlr.objects.get(**self.scope).wizard_draft
        response = self.call()
        self.assertEqual(response.status_code, 200, response.content)
        preview = json.loads(response.content)
        self.assertTrue(preview['previewOnly']); self.assertTrue(preview['identityVerified'])
        self.assertEqual(preview['uln'], '1234509876')
        self.assertEqual(preview['counts'], {'learningEvents': 2, 'achievements': 1})
        event, withdrawn, achieved = preview['records']
        self.assertEqual(event['placeOfStudy'], 'Fictional Academy North Campus')
        self.assertEqual(event['subject'], 'Award in Fictional Payroll Practice (RQF)')
        self.assertEqual((event['level'], event['credits'], event['awardDate']), ('', '', ''))
        self.assertEqual(event['grade'], 'L1')
        self.assertEqual(withdrawn['completionStatus'], 'Withdrawn')
        self.assertEqual(withdrawn['outcomeStatus'], 'No Achievement')
        self.assertEqual((achieved['level'], achieved['grade'], achieved['awardDate']), ('3', '0', '2022-07-20'))
        self.assertTrue(all(r['recordType'] == 'Imported' for r in preview['records']))
        self.assertEqual(list(WizardPlrRecord.objects.values()), before)
        self.assertEqual(ExtendedIlr.objects.get(**self.scope).wizard_draft, saved)
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '1234509876')
        self.assertIn('no-store', response['Cache-Control'])

    def test_previously_rejected_row_layouts_preview_without_writes(self):
        before = list(WizardPlrRecord.objects.values())
        saved = ExtendedIlr.objects.get(**self.scope).wizard_draft
        for name, pdf, expected in synthetic_boundary_cases():
            with self.subTest(layout=name):
                response = self.call(pdf)
                self.assertEqual(response.status_code, 200, response.content)
                preview = json.loads(response.content)
                self.assertEqual([(r['referenceNumber'], r['subject']) for r in preview['records']], expected)
                self.assertTrue(preview['identityVerified'])
                self.assertEqual(list(WizardPlrRecord.objects.values()), before)
                self.assertEqual(ExtendedIlr.objects.get(**self.scope).wizard_draft, saved)

    def test_permissions_are_enforced_even_with_development_auth_bypass(self):
        for gate in ('0', '1'):
            for role in ('learner', 'staff', 'anonymous'):
                response = self.call(role=role, gate=gate)
                self.assertIn(response.status_code, (401, 403))

    def test_csrf_is_required_and_security_get_is_admin_only(self):
        self.assertEqual(self.call(csrf=False).status_code, 403)
        response = self.call(method='get')
        self.assertTrue(json.loads(response.content)['csrfToken'])
        self.assertEqual(self.call(method='get', role='learner').status_code, 403)

    def test_uln_name_and_dob_conflicts_block_preview(self):
        for data, entered, code in (
            (synthetic_pdf(uln='1111111111'), '', 'uln_mismatch'),
            (synthetic_pdf(), '1111111111', 'uln_mismatch'),
            (synthetic_pdf(name='Another Fictional Learner'), '', 'identity_mismatch'),
            (synthetic_pdf(dob='01 Jan 2000'), '', 'identity_mismatch'),
        ):
            response = self.call(data, entered=entered)
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(json.loads(response.content)['code'], code)
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_invalid_files_missing_uln_and_oversized_uploads_are_rejected(self):
        for kwargs in ({'data': b'not a PDF'}, {'data': synthetic_pdf(uln='')}, {'name': 'synthetic.txt'},
                       {'content_type': 'text/plain'}, {'data': b''}, {'data': b'%PDF-' + b'0' * MAX_BYTES}):
            response = self.call(**kwargs)
            self.assertIn(response.status_code, (400, 413), response.content)

    def test_zero_records_is_a_valid_preview_and_leaves_existing_records_untouched(self):
        response = self.call(synthetic_pdf([('Learning Events', []), ('Achievements', [])]))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['records'], [])
        self.assertEqual(json.loads(response.content)['counts'], {'learningEvents': 0, 'achievements': 0})
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_missing_selected_student_uln_and_identity_produce_warnings_not_invented_values(self):
        WizardPlr.objects.filter(**self.scope).update(uln='')
        self.learner.date_of_birth = None; self.learner.save(update_fields=['date_of_birth'])
        response = self.call()
        self.assertEqual(response.status_code, 200, response.content)
        result = json.loads(response.content)
        self.assertFalse(result['identityVerified'])
        self.assertEqual(len(result['warnings']), 2)
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '')

    def test_verification_database_error_is_safe_and_does_not_log_sensitive_text(self):
        with patch.object(WizardPlr.objects, 'filter', side_effect=DatabaseError('sensitive fictional learner values')), \
             self.assertLogs('enrolment_api.wizard_steps', level='ERROR') as logs:
            response = self.call()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('sensitive fictional learner values', ' '.join(logs.output))


class PreviewAuthenticationTests(SimpleTestCase):
    def test_preview_authentication_never_touches_sessions_but_still_refreshes_permissions(self):
        from login.sessions import authenticate_request, COOKIE_NAME
        for method in ('get', 'post'):
            request = getattr(RequestFactory(), method)('/enrolment_api/wizard/apprenticeship/41/plr-pdf-preview/')
            request.COOKIES[COOKIE_NAME] = 'synthetic-token'
            account = SimpleNamespace(role='admin')
            with patch('login.sessions.resolve_session', return_value=SimpleNamespace(account=account)), \
                 patch('login.sessions.touch_session') as touch, patch('login.sessions._refresh_staff_role') as refresh:
                self.assertIs(authenticate_request(request), account)
            touch.assert_not_called()
            refresh.assert_called_once_with(account, persist=False)

    def test_existing_enrolment_authentication_retains_session_renewal(self):
        from login.sessions import authenticate_request, COOKIE_NAME
        request = RequestFactory().get('/enrolment_api/extended-ilr/apprenticeship/41/')
        request.COOKIES[COOKIE_NAME] = 'synthetic-token'
        account = SimpleNamespace(role='admin')
        with patch('login.sessions.resolve_session', return_value=SimpleNamespace(account=account)), \
             patch('login.sessions.touch_session') as touch, patch('login.sessions._refresh_staff_role') as refresh:
            authenticate_request(request)
        touch.assert_called_once()
        refresh.assert_called_once_with(account)

    def test_read_only_role_refresh_applies_demotions_without_saving_them(self):
        from login.sessions import _refresh_staff_role
        from learner_api.models import StaffUser
        account = SimpleNamespace(role='admin', subject_type='staff', subject_id=1, save=MagicMock())
        row = SimpleNamespace(position='Synthetic Coach', access='coach')
        with patch.object(StaffUser.objects, 'filter') as query, patch('login.identity.role_for_staff', return_value='staff'):
            query.return_value.only.return_value.first.return_value = row
            _refresh_staff_role(account, persist=False)
        self.assertEqual(account.role, 'staff')
        account.save.assert_not_called()
