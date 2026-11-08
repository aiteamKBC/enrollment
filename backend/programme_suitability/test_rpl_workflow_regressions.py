import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from .models import SuitabilityCvExtraction, SuitabilityRplReport
from .rpl_report import ReportInputError, _ai_input, _validate_finalisation


class RplWorkflowRegressionTests(TestCase):
    databases = {'default'}

    def report(self, **kwargs):
        defaults = dict(learner_kind='apprenticeship', learner_id=1601, source_snapshot={'evidence': {'cv': {'status': 'provided', 'sourceDocuments': [{'id': 'cv-a', 'container': 'approved', 'blobName': '1601/a.pdf'}]}}}, cv_source_document_id='cv-a', cv_source_container='approved', cv_source_blob_name='1601/a.pdf', cv_source_blob_hash='a'*64, cv_extractor_version='cv-text-v1', relevant_prior_learning=False, reduction_justified=False, suitability_outcome='suitable', suitability_justification='ok', suitability_reviewed_at='2026-01-01', assessor_evidence_review='review', no_reduction_reason='none', rpl_justification='rpl', rpl_reviewed_at='2026-01-01', rpl_ai_metadata={'cvEvidenceVersion': {'documentId': 'cv-a', 'sha256': 'a'*64}})
        defaults.update(kwargs); return SuitabilityRplReport(**defaults)

    def test_stale_approval_and_finalisation_are_blocked(self):
        report = self.report(cv_source_blob_hash='b'*64)
        with patch('programme_suitability.rpl_report.extract_approved_cv', side_effect=ReportInputError('changed')):
            with self.assertRaises(ReportInputError): _validate_finalisation(report, [])

    def test_ai_evidence_metadata_requires_exact_version(self):
        report = self.report()
        self.assertEqual(report.rpl_ai_metadata['cvEvidenceVersion']['documentId'], 'cv-a')
        self.assertEqual(report.rpl_ai_metadata['cvEvidenceVersion']['sha256'], 'a'*64)
        self.assertNotEqual(report.rpl_ai_metadata['cvEvidenceVersion']['sha256'], 'b'*64)

    def test_assessor_acknowledgement_persists_audit_fields_and_is_version_specific(self):
        report = self.report(cv_extraction_acknowledged=True, cv_extraction_acknowledgement_reason='Unreadable scan', cv_extraction_acknowledged_by=9, cv_extraction_acknowledged_at='2026-01-01')
        self.assertTrue(report.cv_extraction_acknowledged)
        self.assertEqual(report.cv_extraction_acknowledgement_reason, 'Unreadable scan')
        self.assertEqual(report.cv_extraction_acknowledged_by, 9)
        self.assertEqual(report.cv_source_blob_hash, 'a'*64)

    def test_deleted_cv_extraction_cannot_be_used(self):
        row = SuitabilityCvExtraction.objects.create(learner_kind='apprenticeship', learner_id=1601, source_document_id='cv-delete', source_container='approved', source_blob_name='x.pdf', source_blob_hash='a'*64, status='ready', extracted_text='PRIVATE')
        SuitabilityCvExtraction.objects.filter(pk=row.pk).delete()
        self.assertFalse(SuitabilityCvExtraction.objects.filter(source_document_id='cv-delete').exists())

    def test_historical_snapshot_without_provenance_requires_refresh(self):
        report = self.report(cv_source_blob_hash='', cv_source_document_id='')
        self.assertEqual(report.cv_source_blob_hash, '')
        self.assertEqual(report.cv_source_document_id, '')

    def test_extraction_statuses_are_distinct_and_private(self):
        for status in ('pending', 'ready', 'failed', 'unsupported'):
            SuitabilityCvExtraction.objects.create(learner_kind='apprenticeship', learner_id=1601, source_document_id=f'cv-{status}', source_container='approved', source_blob_name=f'{status}.pdf', source_blob_hash=status.ljust(64, '0'), status=status, extracted_text='PRIVATE SECRET')
        self.assertEqual(set(SuitabilityCvExtraction.objects.values_list('status', flat=True)), {'pending', 'ready', 'failed', 'unsupported'})
        self.assertNotIn('PRIVATE SECRET', json.dumps({'status': 'ready'}))

    def test_private_cv_secret_is_not_in_ai_metadata_or_snapshot(self):
        report = self.report(source_snapshot={'evidence': {'cv': {'status': 'provided', 'sourceDocuments': [{'id': 'cv-a'}]}}})
        self.assertNotIn('PRIVATE-CV-SECRET', json.dumps(report.source_snapshot))
        self.assertNotIn('PRIVATE-CV-SECRET', json.dumps(report.rpl_ai_metadata))
