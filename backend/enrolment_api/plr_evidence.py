"""Certificate/evidence files for the wizard's Personal Learning Record entries.

Each file goes to Azure through quarantine -> scan -> approved (see
learner_uploads) and is recorded on its PLR entry's enrolment."Wizard_Plr_Records"
row in "Evidence": a jsonb array with one object per file, whose `path` is the
approved blob's URL.

A file can be attached while the entry is still being added, before the draft
has been saved; the row is created then, keyed on the entry's client-side id
(Record_ref), and the draft save fills in the rest. Removing an entry and saving
deletes its row and its files (see wizard_steps.project_draft).

    GET    /enrolment_api/wizard/<kind>/<id>/plr-evidence/
    POST   /enrolment_api/wizard/<kind>/<id>/plr-evidence/            (multipart: file, record_ref)
    GET    /enrolment_api/wizard/<kind>/<id>/plr-evidence/<file_id>/download/
    DELETE /enrolment_api/wizard/<kind>/<id>/plr-evidence/<file_id>/
"""
import logging
import re

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.evidence_storage import azure_configured, get_download_sas

from . import learner_uploads as uploads
from .auth import enrolment_login_required
from .extended_ilr import KINDS
from .models import WizardPlrRecord

logger = logging.getLogger(__name__)

#: Record_ref goes into the blob name, so only a plain id is accepted.
RECORD_REF = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_error = uploads.error


def _public(row, item):
    return {**uploads.public(item), "recordRef": row.record_ref}


def _find(kind, learner_id, file_id, lock=False):
    """(row, item, items) holding `file_id` among this learner's records, or Nones."""
    rows = WizardPlrRecord.objects.filter(learner_kind=kind, learner_id=learner_id)
    if lock:
        rows = rows.select_for_update()
    for row in rows:
        items = uploads.items(row.evidence)
        item = next((i for i in items if i["id"] == str(file_id)), None)
        if item is not None:
            return row, item, items
    return None, None, None


@enrolment_login_required
@csrf_exempt
def plr_evidence(request, kind, learner_id):
    """GET lists every entry's files; POST attaches one to an entry."""
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    try:
        if not KINDS[kind].objects.filter(pk=learner_id).exists():
            return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.warning("Could not look up learner for PLR evidence: %s", exc)
        return _error("Could not load the learner.", 502)

    if request.method == "GET":
        try:
            rows = list(WizardPlrRecord.objects.filter(learner_kind=kind, learner_id=learner_id))
        except DatabaseError as exc:
            logger.warning("Could not list PLR evidence: %s", exc)
            return _error("Could not load evidence.", 502)
        return JsonResponse({"results": [
            _public(row, item) for row in rows for item in uploads.items(row.evidence)
        ]})

    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)

    record_ref = (request.POST.get("record_ref") or "").strip()
    if not RECORD_REF.match(record_ref):
        return _error("record_ref is required (letters, digits, '-' or '_').", 400)
    f = request.FILES.get("file")
    refused = uploads.invalid_upload(f)
    if refused:
        return refused

    item, failed = uploads.store(f, f"{kind}/{learner_id}/plr/{record_ref}", request)
    if failed:
        return failed

    try:
        with transaction.atomic(using="enrolment"):
            # Locked so two uploads at once each append to what the other wrote.
            row, _ = WizardPlrRecord.objects.select_for_update().get_or_create(
                learner_kind=kind, learner_id=learner_id, record_ref=record_ref,
            )
            row.evidence = [*uploads.items(row.evidence), item]
            row.save(update_fields=["evidence", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not record PLR evidence: %s", exc)
        uploads.discard(item["container"], item["blobName"])
        return _error("Could not record the uploaded file.", 502)

    return JsonResponse(_public(row, item), status=201)


@enrolment_login_required
def download_plr_evidence(request, kind, learner_id, file_id):
    """Short-lived SAS URL for one stored file."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)
    try:
        _row, item, _items = _find(kind, learner_id, file_id)
    except DatabaseError as exc:
        logger.warning("Could not look up PLR evidence for download: %s", exc)
        return _error("Could not load evidence.", 502)
    if item is None or not item.get("container") or not item.get("blobName"):
        return _error("Evidence not found.", 404)
    return JsonResponse({"url": get_download_sas(item["container"], item["blobName"])})


@enrolment_login_required
@csrf_exempt
def delete_plr_evidence(request, kind, learner_id, file_id):
    """Remove one file: the blob, then its record."""
    if request.method != "DELETE":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    try:
        with transaction.atomic(using="enrolment"):
            row, item, items = _find(kind, learner_id, file_id, lock=True)
            if item is None:
                return _error("Evidence not found.", 404)
            # Blob first: removing the record but not the file would strand a
            # certificate nothing points at.
            if azure_configured():
                uploads.remove_blob(item)
            row.evidence = [i for i in items if i["id"] != str(file_id)]
            row.save(update_fields=["evidence", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not remove PLR evidence: %s", exc)
        return _error("Could not remove the evidence record.", 502)
    return JsonResponse({"id": str(file_id), "deleted": True})
