import base64
import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, RequestFactory

from . import rpl_report


def pdf_payload(report_id=7, revision=3, version=1, pdf=b'%PDF-1.7\nsynthetic\n%%EOF'):
    encoded = base64.b64encode(pdf).decode()
    return {
        'reportId': report_id,
        'reportRevision': revision,
        'reportVersion': version,
        'sha256': hashlib.sha256(pdf).hexdigest(),
        'pdf': encoded,
    }


class ReportIssuanceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = SimpleNamespace(role='admin', is_active=True, display_name='Synthetic assessor', email='admin@test.invalid')
        self.learner = SimpleNamespace(pk=1601, learner_type='apprenticeship')
        self.report = SimpleNamespace(
            pk=7, learner_kind='apprenticeship', learner_id=1601, status='finalised', report_version=1,
            revision=3, report_reference='RPL-1601-TEST', source_snapshot_hash='snapshot-hash',
            suitability_reviewed_at='2030-01-01', rpl_reviewed_at='2030-01-01',
        )

    def request(self, body, method='post'):
        request = getattr(self.factory, method)('/enrolment_api/programme-suitability/apprenticeship/1601/suitability-rpl/issue/', data=json.dumps(body), content_type='application/json')
        request.login_account = self.admin
        request._dont_enforce_csrf_checks = True
        return request

    def mocks(self):
        issue = SimpleNamespace(pk='issue-1')
        created_history = MagicMock()
        existing = MagicMock()
        existing.payload = {'sha256': hashlib.sha256(b'%PDF-1.7\nsynthetic\n%%EOF').hexdigest()}
        issued_manager = MagicMock()
        issued_manager.filter.return_value.first.return_value = None
        issued_manager.create.return_value = issue
        history_manager = MagicMock()
        history_manager.create.return_value = created_history
        return issue, issued_manager, history_manager

    def report_manager(self):
        manager = MagicMock()
        manager.prefetch_related.return_value = manager
        manager.filter.return_value.first.return_value = self.report
        return manager

    def test_finalised_report_is_issued_once_with_immutable_pdf_hash(self):
        issue, issued_manager, history_manager = self.mocks()
        report_manager = self.report_manager()
        with patch('login.permissions.authenticate_request', return_value=self.admin), \
             patch.object(rpl_report, '_learner', return_value=self.learner), \
             patch.object(rpl_report.SuitabilityRplReport, 'objects', report_manager), \
             patch.object(rpl_report, 'render_finalised_report', return_value=b'%PDF-1.7\ntrusted\n%%EOF'), \
             patch.object(rpl_report, 'transaction') as transaction, \
             patch.object(rpl_report.IssuedTemplate, 'objects', issued_manager), \
             patch.object(rpl_report.RenderHistory, 'objects', history_manager), \
             patch.object(rpl_report.document_service, 'database', return_value='default'), \
             patch.object(rpl_report.document_service, 'actor', return_value='Synthetic assessor'):
            transaction.atomic.return_value = nullcontext()
            response = rpl_report.issue_suitability_rpl_report(self.request({'reportId': 7, 'reportRevision': 3, 'reportVersion': 1}), 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(json.loads(response.content)['issued'])
        snapshot = issued_manager.create.call_args.kwargs['template_snapshot']
        self.assertEqual(snapshot['reportRevision'], 3)
        trusted_hash = hashlib.sha256(b'%PDF-1.7\ntrusted\n%%EOF').hexdigest()
        self.assertEqual(snapshot['pdfSha256'], trusted_hash)
        self.assertEqual(snapshot['signingStatus'], 'pending')
        self.assertEqual(snapshot['pdfTrust'], 'server-rendered-v1')
        self.assertEqual(issued_manager.create.call_args.kwargs['source_id'], 'report-7-v1-r3-server-v1')
        self.assertTrue(json.loads(response.content)['signingEnabled'])
        payload = history_manager.create.call_args.kwargs['payload']
        self.assertEqual(payload['kind'], 'suitability-rpl-issued-pdf-v1')
        self.assertNotIn('extractedText', json.dumps(payload))

    def test_browser_pdf_bytes_are_not_required_or_trusted_for_issuance(self):
        issue, issued_manager, history_manager = self.mocks()
        report_manager = self.report_manager()
        with patch('login.permissions.authenticate_request', return_value=self.admin), \
             patch.object(rpl_report, '_learner', return_value=self.learner), \
             patch.object(rpl_report.SuitabilityRplReport, 'objects', report_manager), \
             patch.object(rpl_report, 'render_finalised_report', return_value=b'%PDF-1.7\ncanonical\n%%EOF'), \
             patch.object(rpl_report, 'transaction') as transaction, \
             patch.object(rpl_report.IssuedTemplate, 'objects', issued_manager), \
             patch.object(rpl_report.RenderHistory, 'objects', history_manager), \
             patch.object(rpl_report.document_service, 'database', return_value='default'), \
             patch.object(rpl_report.document_service, 'actor', return_value='Synthetic assessor'):
            transaction.atomic.return_value = nullcontext()
            response = rpl_report.issue_suitability_rpl_report(self.request({'reportId': 7, 'reportRevision': 3, 'reportVersion': 1, 'pdf': 'not-used'}), 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(history_manager.create.call_args.kwargs['payload']['sha256'], hashlib.sha256(b'%PDF-1.7\ncanonical\n%%EOF').hexdigest())

    def test_report_revision_mismatch_cannot_issue_stale_pdf(self):
        with patch('login.permissions.authenticate_request', return_value=self.admin), \
             patch.object(rpl_report, '_learner', return_value=self.learner), \
             patch.object(rpl_report.SuitabilityRplReport, 'objects', self.report_manager()):
            response = rpl_report.issue_suitability_rpl_report(self.request(pdf_payload(revision=2)), 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn('changed before issuance', json.loads(response.content)['error'])

    def test_non_admin_cannot_issue(self):
        learner_account = SimpleNamespace(role='learner', is_active=True)
        with patch('login.permissions.authenticate_request', return_value=learner_account):
            response = rpl_report.issue_suitability_rpl_report(self.request(pdf_payload()), 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_issue(self):
        with patch('login.permissions.authenticate_request', return_value=None):
            response = rpl_report.issue_suitability_rpl_report(self.request(pdf_payload()), 'apprenticeship', 1601)
        self.assertEqual(response.status_code, 401)
