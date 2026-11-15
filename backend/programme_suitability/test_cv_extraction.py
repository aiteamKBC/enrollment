import io
from unittest.mock import patch

from django.test import TestCase, override_settings

from .cv_extraction import CvExtractionError, extract_approved_cv


class CvExtractionTests(TestCase):
    databases = {'default'}

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_approved_pdf_is_hashed_and_stored(self, download):
        from reportlab.pdfgen import canvas
        stream = io.BytesIO(); pdf = canvas.Canvas(stream); pdf.drawString(20, 20, 'Synthetic CV evidence'); pdf.save()
        download.return_value = stream.getvalue()
        row = extract_approved_cv('apprenticeship', 1601, {'id': 'cv-1', 'docKind': 'cv', 'container': 'approved', 'blobName': 'apprenticeship/1601/cv.pdf', 'filename': 'cv.pdf'})
        self.assertEqual(row.status, 'ready')
        self.assertEqual(row.source_document_id, 'cv-1')
        self.assertTrue(row.source_blob_hash)

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    def test_quarantine_and_non_cv_documents_are_rejected(self):
        with self.assertRaisesMessage(CvExtractionError, 'approved'):
            extract_approved_cv('apprenticeship', 1601, {'id': 'cv-1', 'docKind': 'cv', 'container': 'quarantine', 'blobName': 'cv.pdf'})
        with self.assertRaisesMessage(CvExtractionError, 'CV'):
            extract_approved_cv('apprenticeship', 1601, {'id': 'cv-1', 'docKind': 'transcript', 'container': 'approved', 'blobName': 'x.pdf'})

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_synthetic_docx_extracts_text(self, download):
        from docx import Document
        stream = io.BytesIO(); doc = Document(); doc.add_paragraph('Synthetic DOCX CV experience'); doc.save(stream)
        download.return_value = stream.getvalue()
        row = extract_approved_cv('apprenticeship', 1601, {'id': 'docx-1', 'docKind': 'cv', 'container': 'approved', 'blobName': '1601/cv.docx', 'filename': 'cv.docx'})
        self.assertEqual(row.status, 'ready')
        self.assertIn('Synthetic DOCX CV experience', row.extracted_text)

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_scanned_pdf_is_failed_without_ocr_and_not_prior_learning_proof(self, download):
        download.return_value = b'%PDF-1.4 image-only synthetic document'
        row = extract_approved_cv('apprenticeship', 1601, {'id': 'scan-1', 'docKind': 'cv', 'container': 'approved', 'blobName': '1601/scan.pdf', 'filename': 'scan.pdf'})
        self.assertEqual(row.status, 'failed')
        self.assertEqual(row.extracted_text, '')

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_oversized_and_unreadable_documents_fail_without_storing_content(self, download):
        download.side_effect = ValueError('Blob exceeds the permitted preview size.')
        with self.assertRaisesMessage(ValueError, 'exceeds'):
            extract_approved_cv('apprenticeship', 1601, {'id': 'big-1', 'docKind': 'cv', 'container': 'approved', 'blobName': '1601/big.pdf', 'filename': 'big.pdf'})
        download.side_effect = None; download.return_value = b'not a PDF'
        row = extract_approved_cv('apprenticeship', 1601, {'id': 'bad-1', 'docKind': 'cv', 'container': 'approved', 'blobName': '1601/bad.pdf', 'filename': 'bad.pdf'})
        self.assertEqual(row.status, 'failed'); self.assertEqual(row.extracted_text, '')

    @override_settings(AZURE_APPROVED_CONTAINER='approved')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_replacement_hash_creates_new_record_and_never_reuses_old_text(self, download):
        download.side_effect = [b'%PDF-1.4 first', b'%PDF-1.4 second']
        item = {'id': 'replace-1', 'docKind': 'cv', 'container': 'approved', 'blobName': '1601/cv.pdf', 'filename': 'cv.pdf'}
        first = extract_approved_cv('apprenticeship', 1601, item)
        second = extract_approved_cv('apprenticeship', 1601, item)
        self.assertNotEqual(first.source_blob_hash, second.source_blob_hash)
        self.assertNotEqual(first.pk, second.pk)
