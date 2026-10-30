"""Files uploaded on the wizard's CV/Job Description step.

The CV, the transcript of the learner's highest qualification in their
programme's field, and their English and Maths GCSE evidence. They go to Azure
through quarantine -> scan -> approved (see learner_uploads), and each stored
file is recorded on the learner's enrolment."Wizard_Cv_Job" row in "Documents":
a jsonb array with one object per file, carrying its `docKind` and `path` (the
approved blob's URL).

    GET    /enrolment_api/wizard/<kind>/<id>/cv-documents/
    POST   /enrolment_api/wizard/<kind>/<id>/cv-documents/            (multipart: file, doc_kind)
    GET    /enrolment_api/wizard/<kind>/<id>/cv-documents/<file_id>/download/
    DELETE /enrolment_api/wizard/<kind>/<id>/cv-documents/<file_id>/
"""
import logging

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.evidence_storage import azure_configured, get_download_sas

from . import learner_uploads as uploads
from .auth import enrolment_login_required
from .extended_ilr import KINDS
from .models import WizardCvJob

logger = logging.getLogger(__name__)

DOC_KINDS = {
    "cv": "CV",
    "transcript": "Qualification transcript",
    "gcse-english": "English GCSE evidence",
    "gcse-maths": "Maths GCSE evidence",
}

_error = uploads.error


def _items(row):
    return uploads.items(row.documents if row is not None else None)


def _public(item):
    return {**uploads.public(item), "docKind": item.get("docKind") or ""}


def _learner_exists(kind, learner_id):
    return KINDS[kind].objects.filter(pk=learner_id).exists()


@enrolment_login_required
@csrf_exempt
def cv_documents(request, kind, learner_id):
    """GET lists the stored files; POST uploads one."""
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    try:
        if not _learner_exists(kind, learner_id):
            return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.warning("Could not look up learner for CV documents: %s", exc)
        return _error("Could not load the learner.", 502)

    if request.method == "GET":
        try:
            row = WizardCvJob.objects.filter(learner_kind=kind, learner_id=learner_id).first()
        except DatabaseError as exc:
            logger.warning("Could not list CV documents: %s", exc)
            return _error("Could not load documents.", 502)
        return JsonResponse({"results": [_public(item) for item in _items(row)]})

    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not azure_configured():
        return _error("Document storage is not configured.", 503)

    doc_kind = (request.POST.get("doc_kind") or "").strip()
    if doc_kind not in DOC_KINDS:
        return _error(f"doc_kind must be one of: {', '.join(DOC_KINDS)}.", 400)
    f = request.FILES.get("file")
    refused = uploads.invalid_upload(f)
    if refused:
        return refused

    item, failed = uploads.store(f, f"{kind}/{learner_id}/cv-job/{doc_kind}", request)
    if failed:
        return failed
    item["docKind"] = doc_kind

    try:
        with transaction.atomic(using="enrolment"):
            # Locked so two uploads at once each append to what the other wrote.
            # Created if the learner uploads before the step has been saved; the
            # draft save upserts the same row and leaves this column alone.
            row, _ = WizardCvJob.objects.select_for_update().get_or_create(
                learner_kind=kind, learner_id=learner_id,
            )
            row.documents = [*_items(row), item]
            row.save(update_fields=["documents", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not record CV document: %s", exc)
        uploads.discard(item["container"], item["blobName"])
        return _error("Could not record the uploaded file.", 502)

    return JsonResponse(_public(item), status=201)


@enrolment_login_required
def download_cv_document(request, kind, learner_id, file_id):
    """Short-lived SAS URL for one stored file."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    if not azure_configured():
        return _error("Document storage is not configured.", 503)
    try:
        row = WizardCvJob.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    except DatabaseError as exc:
        logger.warning("Could not look up CV document for download: %s", exc)
        return _error("Could not load documents.", 502)

    item = next((i for i in _items(row) if i["id"] == str(file_id)), None)
    if item is None or not item.get("container") or not item.get("blobName"):
        return _error("Document not found.", 404)
    return JsonResponse({"url": get_download_sas(item["container"], item["blobName"])})


@enrolment_login_required
@csrf_exempt
def delete_cv_document(request, kind, learner_id, file_id):
    """Remove one file: the blob, then its record."""
    if request.method != "DELETE":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)

    try:
        with transaction.atomic(using="enrolment"):
            row = (
                WizardCvJob.objects.select_for_update()
                .filter(learner_kind=kind, learner_id=learner_id)
                .first()
            )
            items = _items(row)
            item = next((i for i in items if i["id"] == str(file_id)), None)
            if item is None:
                return _error("Document not found.", 404)
            # Blob first: removing the record but not the file would strand a
            # CV or certificate nothing points at.
            if azure_configured():
                uploads.remove_blob(item)
            row.documents = [i for i in items if i["id"] != str(file_id)]
            row.save(update_fields=["documents", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not remove CV document: %s", exc)
        return _error("Could not remove the document record.", 502)

    return JsonResponse({"id": str(file_id), "deleted": True})
