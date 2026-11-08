"""Bounded extraction from approved learner CV blobs; never reads quarantine."""
import hashlib
import io
import os
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from learner_api import evidence_storage
from .models import SuitabilityCvExtraction

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 100_000
EXTRACTOR_VERSION = 'cv-text-v1'

class CvExtractionError(ValueError):
    pass

def _text_pdf(data):
    from pypdf import PdfReader
    return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data), strict=False).pages)

def _text_docx(data):
    from docx import Document
    return '\n'.join(p.text for p in Document(io.BytesIO(data)).paragraphs)

def extract_approved_cv(kind, learner_id, item):
    """Extract one exact approved manifest item and persist a private version."""
    if not isinstance(item, dict) or item.get('docKind') != 'cv':
        raise CvExtractionError('The source document is not a CV.')
    container = str(item.get('container') or '')
    blob_name = str(item.get('blobName') or '')
    document_id = str(item.get('id') or '')
    approved = str(getattr(settings, 'AZURE_APPROVED_CONTAINER', 'evidence-approved'))
    if not document_id or not blob_name or container != approved:
        raise CvExtractionError('Only an approved CV document may be extracted.')
    filename = os.path.basename(str(item.get('filename') or blob_name)).lower()
    if not filename.endswith(('.pdf', '.docx')):
        return SuitabilityCvExtraction.objects.update_or_create(
            learner_kind=kind, learner_id=learner_id, source_document_id=document_id,
            source_blob_hash='unsupported', defaults={'source_container': container, 'source_blob_name': blob_name,
            'source_filename': filename, 'status': SuitabilityCvExtraction.STATUS_UNSUPPORTED, 'error_code': 'unsupported_type'})[0]
    data = evidence_storage.download_blob_bytes(container, blob_name, max_bytes=MAX_BYTES)
    digest = hashlib.sha256(data).hexdigest()
    with transaction.atomic():
        row, _ = SuitabilityCvExtraction.objects.select_for_update().get_or_create(
            learner_kind=kind, learner_id=learner_id, source_document_id=document_id, source_blob_hash=digest,
            defaults={'source_container': container, 'source_blob_name': blob_name, 'source_filename': filename,
                      'source_content_type': str(item.get('contentType') or ''), 'extractor_version': EXTRACTOR_VERSION})
        if row.status == SuitabilityCvExtraction.STATUS_READY:
            return row
        try:
            text = _text_pdf(data) if filename.endswith('.pdf') else _text_docx(data)
            text = text.replace('\x00', '').strip()
            if len(text) > MAX_CHARS:
                raise CvExtractionError('The CV exceeds the permitted extracted-text limit.')
            row.status = SuitabilityCvExtraction.STATUS_READY
            row.extracted_text = text
            row.error_code = ''
            row.extracted_at = timezone.now()
        except CvExtractionError:
            raise
        except Exception as exc:
            row.status = SuitabilityCvExtraction.STATUS_FAILED
            row.error_code = type(exc).__name__[:80]
            row.extracted_text = ''
        row.save()
        return row
