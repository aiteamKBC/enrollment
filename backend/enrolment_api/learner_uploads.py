"""Learner-supplied enrolment files: validation and the Azure upload route.

Shared by the Extended ILR's eligibility evidence and the CV/Job Description
step's documents. The files are learner-supplied, so they take the same route
as learner evidence (see learner_api.evidence): upload to
AZURE_QUARANTINE_CONTAINER, scan, then move to AZURE_APPROVED_CONTAINER, or to
AZURE_REJECTED_CONTAINER on a hit. What comes back is the record the caller
stores against the learner — including `path`, the approved blob's URL.
Downloads go out as short-lived SAS URLs, so the container stays private.
"""
import logging
import uuid
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone

from learner_api.evidence import _scan
from learner_api.evidence_storage import blob_url, delete_blob, move_blob, upload_to_quarantine

logger = logging.getLogger(__name__)

#: Identity documents, certificates, transcripts and CVs arrive as scans,
#: photos, PDFs or Word documents.
ALLOWED_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}
#: Sent by a browser that does not recognise the file; trusted only alongside
#: one of our extensions.
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
MAX_BYTES = 25 * 1024 * 1024  # 25 MB


def error(message, status):
    return JsonResponse({"error": message}, status=status)


def type_allowed(uploaded):
    content_type = (getattr(uploaded, "content_type", "") or "").split(";")[0].strip().lower()
    suffix = Path(getattr(uploaded, "name", "") or "").suffix.lower()
    if content_type in ALLOWED_TYPES:
        return True
    return content_type in GENERIC_TYPES and suffix in ALLOWED_EXTENSIONS


def invalid_upload(f):
    """An error response for a file we will not accept, or None."""
    if not f:
        return error("A 'file' part is required.", 400)
    if not type_allowed(f):
        return error("Unsupported file type. Upload a PDF, an image (PNG/JPEG) or a Word document.", 400)
    if f.size > MAX_BYTES:
        return error("File exceeds the 25 MB size limit.", 400)
    return None


def uploaded_by(request):
    account = getattr(request, "login_account", None)
    if account is not None:
        return f"{account.role}:{account.subject_id}"
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return f"django:{user.get_username()}"
    return None


def items(value):
    """Stored file records, ignoring anything that is not one (e.g. a hand edit)."""
    return [item for item in (value or []) if isinstance(item, dict) and item.get("id")]


def public(item):
    """What the browser sees. The container, blob name and path stay server-side:
    they locate the file rather than describe it, and downloads go through SAS."""
    return {
        "id": item["id"],
        "filename": item.get("filename") or "",
        "contentType": item.get("contentType") or "",
        "sizeBytes": item.get("sizeBytes"),
        "uploadedAt": item.get("uploadedAt"),
    }


def discard(container, blob_name):
    """Remove a blob that ended up with no record pointing at it. Never raises."""
    try:
        delete_blob(container, blob_name)
    except Exception as exc:  # already gone / SDK / network
        logger.warning("Could not remove unrecorded upload %s/%s: %s", container, blob_name, exc)


def remove_blob(item):
    """Delete a stored file's blob. A blob that has already gone is not a failure."""
    if item.get("container") and item.get("blobName"):
        try:
            delete_blob(item["container"], item["blobName"])
        except Exception as exc:  # already deleted / SDK / network
            logger.warning("Upload blob delete failed for %s/%s: %s", item["container"], item["blobName"], exc)


def store(f, blob_prefix, request):
    """Upload `f` under `blob_prefix`/ through quarantine -> scan -> approved.

    Returns (record, None) once the file is in the approved container, or
    (None, error response). A file failing the scan is moved to the rejected
    container and not returned, so the caller records nothing.
    """
    file_id = uuid.uuid4()
    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else "bin"
    blob_name = f"{blob_prefix}/{file_id}.{ext}"
    quarantine = settings.AZURE_QUARANTINE_CONTAINER

    try:
        upload_to_quarantine(f, blob_name, f.content_type)
    except Exception as exc:  # SDK / network / duplicate-blob errors
        logger.warning("Upload to quarantine failed: %s", exc)
        return None, error("Upload to storage failed.", 502)

    verdict = _scan(quarantine, blob_name)
    if verdict != "clean":
        try:
            move_blob(quarantine, settings.AZURE_REJECTED_CONTAINER, blob_name)
        except Exception as exc:
            logger.warning("Could not move rejected upload %s: %s", blob_name, exc)
        return None, error("The file did not pass the security scan and was not stored.", 422)

    approved = settings.AZURE_APPROVED_CONTAINER
    try:
        move_blob(quarantine, approved, blob_name)
    except Exception as exc:
        logger.warning("Upload promotion (%s -> %s) failed: %s", quarantine, approved, exc)
        return None, error("Could not finalise upload.", 502)

    return {
        "id": str(file_id),
        "filename": f.name,
        "contentType": f.content_type,
        "sizeBytes": f.size,
        "container": approved,
        "blobName": blob_name,
        "path": blob_url(approved, blob_name),
        "scanResult": verdict,
        "uploadedBy": uploaded_by(request),
        "uploadedAt": timezone.now().isoformat(),
    }, None
