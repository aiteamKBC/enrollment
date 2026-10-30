"""Proof of identification and residency for the Extended ILR's Eligibility section.

The files are learner-supplied, so they go to Azure through quarantine -> scan
-> approved (see learner_uploads). Each stored file is recorded on the
learner's enrolment."Extended_ILR" row in "Eligibility_evidence" — a jsonb
array with one object per file, whose `path` is the approved blob's URL.

Kept out of "Learner"."evidence_files" on purpose: that table feeds the
learner's Evidence Library and portfolio, where a passport scan has no place.

Files can be removed until the ILR is Completed (learner and provider have both
signed); from then on they are part of the signed record and are locked.

    GET    /enrolment_api/extended-ilr/<kind>/<id>/eligibility-evidence/
    POST   /enrolment_api/extended-ilr/<kind>/<id>/eligibility-evidence/   (multipart: file)
    GET    /enrolment_api/extended-ilr/<kind>/<id>/eligibility-evidence/<file_id>/download/
    DELETE /enrolment_api/extended-ilr/<kind>/<id>/eligibility-evidence/<file_id>/
"""
import logging

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.evidence_storage import azure_configured, get_download_sas

from . import learner_uploads as uploads
from .auth import enrolment_login_required
from .extended_ilr import KINDS
from .models import ExtendedIlr

logger = logging.getLogger(__name__)

#: Blob-name segment, so these files are told apart from assignment evidence in
#: the shared containers.
SECTION = "ilr-eligibility"

LOCKED_MESSAGE = "The Extended ILR has been signed, so its eligibility evidence can no longer be changed."

_error = uploads.error


def _items(row):
    return uploads.items(row.eligibility_evidence if row is not None else None)


def _listing(row):
    return {
        "results": [uploads.public(item) for item in _items(row)],
        "locked": bool(row is not None and row.completed),
    }


def _learner_name(kind, learner_id):
    """The learner's name, or None if there is no such learner."""
    learner = KINDS[kind].objects.filter(pk=learner_id).first()
    if learner is None:
        return None
    return "" if learner.username is None else str(learner.username).strip()


@enrolment_login_required
@csrf_exempt
def eligibility_evidence(request, kind, learner_id):
    """GET lists the stored files; POST uploads one."""
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    try:
        learner_name = _learner_name(kind, learner_id)
    except DatabaseError as exc:
        logger.warning("Could not look up learner for ILR evidence: %s", exc)
        return _error("Could not load the learner.", 502)
    if learner_name is None:
        return _error("Learner not found.", 404)

    if request.method == "GET":
        try:
            row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).first()
        except DatabaseError as exc:
            logger.warning("Could not list ILR eligibility evidence: %s", exc)
            return _error("Could not load evidence.", 502)
        return JsonResponse(_listing(row))

    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)

    f = request.FILES.get("file")
    refused = uploads.invalid_upload(f)
    if refused:
        return refused

    # Checked before anything reaches Azure so a locked record never costs an
    # upload; checked again under the row lock below, which is the one that counts.
    try:
        if ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id, completed=True).exists():
            return _error(LOCKED_MESSAGE, 409)
    except DatabaseError as exc:
        logger.warning("Could not read the ILR before an evidence upload: %s", exc)
        return _error("Could not load the Extended ILR.", 502)

    item, failed = uploads.store(f, f"{kind}/{learner_id}/{SECTION}", request)
    if failed:
        return failed

    try:
        with transaction.atomic(using="enrolment"):
            # Locked so two uploads at once each append to what the other wrote,
            # and a signature landing mid-upload is seen. The row is created if
            # the learner uploads before the wizard has saved anything; the ILR
            # save upserts on the same (kind, id) and leaves this column alone.
            row, _ = ExtendedIlr.objects.select_for_update().get_or_create(
                learner_kind=kind, learner_id=learner_id,
                defaults={"learner_name": learner_name},
            )
            if row.completed:
                locked = True
            else:
                locked = False
                row.eligibility_evidence = [*_items(row), item]
                row.save(update_fields=["eligibility_evidence", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not record ILR eligibility evidence: %s", exc)
        uploads.discard(item["container"], item["blobName"])
        return _error("Could not record the uploaded file.", 502)

    if locked:
        uploads.discard(item["container"], item["blobName"])
        return _error(LOCKED_MESSAGE, 409)

    return JsonResponse(uploads.public(item), status=201)


@enrolment_login_required
def download_eligibility_evidence(request, kind, learner_id, file_id):
    """Short-lived SAS URL for one stored file."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)
    try:
        row = ExtendedIlr.objects.filter(learner_kind=kind, learner_id=learner_id).first()
    except DatabaseError as exc:
        logger.warning("Could not look up ILR evidence for download: %s", exc)
        return _error("Could not load evidence.", 502)

    item = next((i for i in _items(row) if i["id"] == str(file_id)), None)
    if item is None or not item.get("container") or not item.get("blobName"):
        return _error("Evidence not found.", 404)
    return JsonResponse({"url": get_download_sas(item["container"], item["blobName"])})


@enrolment_login_required
@csrf_exempt
def delete_eligibility_evidence(request, kind, learner_id, file_id):
    """Remove one file — refused once the ILR is signed by both parties."""
    if request.method != "DELETE":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)

    try:
        with transaction.atomic(using="enrolment"):
            row = (
                ExtendedIlr.objects.select_for_update()
                .filter(learner_kind=kind, learner_id=learner_id)
                .first()
            )
            items = _items(row)
            item = next((i for i in items if i["id"] == str(file_id)), None)
            if item is None:
                return _error("Evidence not found.", 404)
            if row.completed:
                return _error(LOCKED_MESSAGE, 409)

            # Blob first, as learner_api.evidence.delete_evidence does: removing
            # the record but not the file would strand a passport scan nothing
            # points at.
            if azure_configured():
                uploads.remove_blob(item)

            row.eligibility_evidence = [i for i in items if i["id"] != str(file_id)]
            row.save(update_fields=["eligibility_evidence", "updated_at"])
    except DatabaseError as exc:
        logger.warning("Could not remove ILR eligibility evidence: %s", exc)
        return _error("Could not remove the evidence record.", 502)

    return JsonResponse({"id": str(file_id), "deleted": True})
