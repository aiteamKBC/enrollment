import json
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory, SimpleTestCase
from django.middleware.csrf import get_token

from .rpl_report import suitability_rpl_issued_documents, suitability_rpl_signatures


class SuitabilityRplSignatureWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.issue = SimpleNamespace(
            pk=uuid.uuid4(), learner_id=1601, learner_kind='apprenticeship',
            template_snapshot={
                'pdfSha256': 'a' * 64,
                'pdfTrust': 'server-rendered-v1',
                'reportId': '4',
                'reportVersion': 1,
                'reportRevision': 7,
            },
        )
        self.artifact = SimpleNamespace(payload={
            'kind': 'suitability-rpl-issued-pdf-v1',
            'sha256': 'a' * 64,
            'reportId': '4',
            'reportVersion': 1,
            'reportRevision': 7,
        })
        self.account = SimpleNamespace(
            id=11, is_active=True, role='admin', subject_type='staff', subject_id=77,
        )
        self.view = inspect.unwrap(suitability_rpl_signatures)

    def _request(self, method='get', body=None, account=None):
        request = getattr(self.factory, method)(
            f'/enrolment_api/programme-suitability/apprenticeship/1601/suitability-rpl/issued/{self.issue.pk}/signatures/',
            data=body or b'', content_type='application/json',
        )
        request.login_account = account or self.account
        if method.lower() == 'post':
            csrf_token = get_token(request)
            request.COOKIES['csrftoken'] = csrf_token
            request.META['HTTP_X_CSRFTOKEN'] = csrf_token
        return request

    def test_get_returns_pending_status_for_exact_issued_version(self):
        request = self._request()
        rows = Mock()
        rows.order_by.return_value = []
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)), \
             patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects.filter', return_value=rows):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertFalse(payload['status']['fullySigned'])
        self.assertFalse(payload['status']['signatures']['admin']['signed'])
        self.assertTrue(payload['status']['signingEnabled'])

    def test_wrapped_get_uses_real_login_required_gate(self):
        request = self._request()
        rows = Mock()
        rows.order_by.return_value = []
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)), \
             patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects.filter', return_value=rows):
            response = suitability_rpl_signatures(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 200)

    def test_untrusted_client_rendered_pdf_is_not_signable(self):
        self.issue.template_snapshot['pdfTrust'] = 'client-submitted-unverified'
        request = self._request('post', json.dumps({
            'role': 'admin', 'name': 'Assessor', 'signature': 'data:image/png;base64,AA==',
            'consent': True, 'consentText': 'I consent to signing this issued version.',
        }).encode())
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 409)
        self.assertIn('server renderer', json.loads(response.content)['error'])

    def test_signature_rejects_artifact_report_binding_mismatch(self):
        artifact = SimpleNamespace(payload={**self.artifact.payload, 'reportRevision': 8})
        request = self._request('post', json.dumps({
            'role': 'admin', 'name': 'Assessor', 'signature': 'data:image/png;base64,AA==',
            'consent': True, 'consentText': 'I consent to signing this issued version.',
        }).encode())
        with patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, artifact)):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 409)
        self.assertIn('version binding', json.loads(response.content)['error'])

    def test_role_must_match_authenticated_account(self):
        request = self._request('post', json.dumps({'role': 'learner'}).encode())
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 403)

    def test_enrolment_staff_with_grant_can_sign_as_assessor(self):
        staff = SimpleNamespace(id=21, is_active=True, role='staff', subject_type='staff', subject_id=88)
        request = self._request('post', json.dumps({'role': 'admin'}).encode(), account=staff)
        with patch('programme_suitability.rpl_report.enrolment_auth._accesses_of', return_value={'enrolment'}), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn('signature image', json.loads(response.content)['error'])

    def test_authorised_employer_can_sign_the_same_issued_version(self):
        employer = SimpleNamespace(id=31, is_active=True, role='employer', subject_type='employer', subject_id=501)
        request = self._request('post', json.dumps({
            'role': 'employer', 'name': 'Line Manager', 'signature': 'data:image/png;base64,AA==',
            'consent': True, 'consentText': 'I consent to signing this issued version.',
        }).encode(), account=employer)
        missing = Mock(); missing.exists.return_value = False
        stored = SimpleNamespace(signer_role='employer', signer_name='Line Manager', signed_at=None, signature_method='drawn')
        rows = Mock(); rows.order_by.return_value = [stored]
        manager = Mock(); manager.filter.side_effect = [missing, rows]; manager.create.return_value = stored
        with patch('programme_suitability.rpl_report.enrolment_auth._employs', return_value=True), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)), \
             patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects', manager), \
             patch('programme_suitability.rpl_report.transaction.atomic'):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(manager.create.call_args.kwargs['signer_role'], 'employer')

    def test_duplicate_signature_is_rejected_for_same_issued_version_and_role(self):
        request = self._request('post', json.dumps({
            'role': 'admin', 'name': 'Assessor', 'signature': 'data:image/png;base64,AA==',
            'consent': True, 'consentText': 'I consent to signing this issued version.',
        }).encode())
        existing = Mock()
        existing.exists.return_value = True
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)), \
             patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects.filter', return_value=existing), \
             patch('programme_suitability.rpl_report.transaction.atomic'):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 409)

    def test_signature_binds_exact_issue_revision_hash_and_identity(self):
        request = self._request('post', json.dumps({
            'role': 'admin', 'name': 'Assessor', 'signature': 'data:image/png;base64,AA==',
            'consent': True, 'consentText': 'I consent to signing this issued version.',
        }).encode())
        missing = Mock()
        missing.exists.return_value = False
        stored = SimpleNamespace(signer_role='admin', signer_name='Assessor', signed_at=None, signature_method='drawn')
        rows = Mock()
        rows.order_by.return_value = [stored]
        manager = Mock()
        manager.filter.side_effect = [missing, rows]
        manager.create.return_value = stored
        with patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)), \
             patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects', manager), \
             patch('programme_suitability.rpl_report.transaction.atomic'):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 201)
        kwargs = manager.create.call_args.kwargs
        self.assertEqual(kwargs['issued_document_id'], self.issue.pk)
        self.assertEqual(kwargs['report_id'], 4)
        self.assertEqual(kwargs['report_revision'], 7)
        self.assertEqual(kwargs['pdf_sha256'], 'a' * 64)
        self.assertEqual(kwargs['login_account_id'], 11)
        self.assertEqual(kwargs['signer_subject_id'], 77)

    def test_status_is_complete_only_when_all_required_roles_are_signed(self):
        rows = [SimpleNamespace(signer_role=role, signer_name=role.title(), signed_at=None, signature_method='drawn') for role in ('admin', 'learner', 'employer')]
        manager = Mock()
        manager.order_by.return_value = rows
        with patch('programme_suitability.rpl_report.SuitabilityRplDocumentSignature.objects.filter', return_value=manager):
            request = self._request()
            with patch('login.permissions.authenticate_request', return_value=self.account), \
                 patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)):
                response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertTrue(json.loads(response.content)['status']['fullySigned'])

    def test_learner_cannot_sign_another_learner_document(self):
        learner = SimpleNamespace(id=12, is_active=True, role='learner', subject_type='learner', subject_id=1602)
        request = self._request('get', account=learner)
        with patch('login.permissions.authenticate_request', return_value=learner), \
             patch('programme_suitability.rpl_report._signature_issue', return_value=(self.issue, self.artifact)):
            response = self.view(request, 'apprenticeship', 1601, self.issue.pk)
        self.assertEqual(response.status_code, 404)

    def test_issued_documents_lists_only_immutable_envelopes(self):
        issue = SimpleNamespace(pk=self.issue.pk, learner_id=1601, issued_at=None,
                                template_snapshot={'pdfTrust': 'server-rendered-v1'})
        request = self._request()
        view = inspect.unwrap(suitability_rpl_issued_documents)
        manager = Mock()
        manager.order_by.return_value = [issue]
        artifact = SimpleNamespace(payload={'kind': 'suitability-rpl-issued-pdf-v1'})
        status = {
            'issueId': str(self.issue.pk), 'reportId': '4', 'reportVersion': 1,
            'reportRevision': 7, 'pdfSha256': 'a' * 64, 'pdfUrl': '/pdf',
            'requiredRoles': ['admin', 'learner', 'employer'],
            'signatures': {role: {'signed': False, 'name': '', 'signedAt': None, 'method': ''}
                          for role in ('admin', 'learner', 'employer')},
            'fullySigned': False, 'signingEnabled': True, 'signingStatus': 'pending',
        }
        with patch('login.permissions.authenticate_request', return_value=self.account), \
             patch('programme_suitability.rpl_report.IssuedTemplate.objects.filter', return_value=manager), \
             patch('programme_suitability.rpl_report.RenderHistory.objects.filter', return_value=Mock(first=Mock(return_value=artifact))), \
             patch('programme_suitability.rpl_report._signature_status', return_value=status):
            response = view(request, 'apprenticeship', 1601)
        payload = json.loads(response.content)
        self.assertEqual(len(payload['documents']), 1)
        self.assertEqual(payload['documents'][0]['title'], 'Suitability & RPL Report')

    def test_issued_documents_do_not_allow_another_learner(self):
        learner = SimpleNamespace(id=12, is_active=True, role='learner', subject_type='learner', subject_id=1602)
        request = self._request(account=learner)
        view = inspect.unwrap(suitability_rpl_issued_documents)
        with patch('login.permissions.authenticate_request', return_value=learner):
            response = view(request, 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 404)
