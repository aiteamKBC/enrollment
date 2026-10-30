"""Learner evidence upload/list/download API (Azure Blob Storage backed).

Lifecycle (see EVIDENCE_CLOUD.md):
    upload -> QUARANTINE container + evidence_files row (status 'pending')
           -> scan (see _scan; no scanner infra yet -> TODO)
           -> promote to APPROVED (status 'approved') and record the approved
              blob in "Learner"."Evidence"  ── OR ── move to REJECTED.
    download -> short-lived SAS URL, ONLY from the approved container, ONLY for
                approved rows.

Learners are addressed by (kind, id) like the rest of learner_api — this project
has no auth_user-based learners, so the generic spec's auth_user/DRF model is
adapted to plain CSRF-exempt JSON views keyed on kind+id.

    POST /learner_api/evidence/<kind>/<pk>/upload/   (multipart: file, section_ref)
    GET  /learner_api/evidence/<kind>/<pk>/          (optional ?section_ref= &status=)
    GET  /learner_api/evidence/<kind>/<pk>/<file_id>/download/
    DELETE /learner_api/evidence/<kind>/<pk>/<file_id>/   (own file, pre-submission)
"""
import json
import logging
from pathlib import Path
import uuid

from django.conf import settings
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .evidence_tables import ensure_evidence_tables
from .evidence_storage import (
    azure_configured, upload_to_quarantine, move_blob, blob_url, get_download_sas,
    delete_blob,
)
from login.permissions import learner_self_or_admin, learner_self_or_staff

logger = logging.getLogger(__name__)

VALID_KINDS = {"commercial", "apprenticeship"}

#: Returned when the marking lookup itself fails. Distinct from "" (never
#: submitted) so a database problem locks the file rather than unlocking it.
MARKING_STATUS_UNKNOWN = "unknown"
#: What a learner may upload as evidence. Browsers send the type, so this is
#: keyed on it — but a browser with no mapping for .docx sends
#: application/octet-stream, and Windows sometimes sends the legacy
#: application/msword for a .docx, so the extension is accepted as corroboration
#: in _evidence_type_allowed rather than trusting the header alone.
ALLOWED_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "video/mp4",
    # Word
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    # PowerPoint
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
}

#: Extensions matching ALLOWED_TYPES, for the upload whose declared type is
#: missing or generic. Both have to line up with the accept list the learner
#: page offers (frontend AssignmentEvidence).
ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".mp4",
    ".doc", ".docx", ".ppt", ".pptx", ".ppsx",
}

#: Types a browser sends when it does not recognise the file, which on their own
#: say nothing — accepted only when the extension is one of ours.
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


def _evidence_type_allowed(uploaded):
    """Whether this upload is a kind of evidence we accept.

    A declared type we know is enough. A generic or absent type is accepted on
    the strength of the extension, because otherwise a learner on a machine with
    no .docx mapping simply cannot submit their assignment.
    """
    content_type = (getattr(uploaded, "content_type", "") or "").split(";")[0].strip().lower()
    suffix = Path(getattr(uploaded, "name", "") or "").suffix.lower()
    if content_type in ALLOWED_TYPES:
        return True
    if content_type in GENERIC_TYPES and suffix in ALLOWED_EXTENSIONS:
        return True
    return False
MAX_BYTES = 50 * 1024 * 1024  # 50 MB


#: What an evidence write returns so it can be recorded. `container` and
#: `blob_name` are not here: together they locate the file in Azure, which is a
#: way to fetch it rather than a fact about it -- the same rule this codebase
#: applies to meeting join links.
EVIDENCE_AUDIT_COLUMNS = (
    'id', 'learner_kind', 'learner_id', 'section_ref', 'component_ref',
    'progress_entry_id', 'original_filename', 'content_type', 'size_bytes',
    'status', 'scan_result', 'uploaded_by', 'uploaded_at', 'reviewed_at',
)
EVIDENCE_AUDIT_SQL = ', '.join(f'"{column}"' for column in EVIDENCE_AUDIT_COLUMNS)


def _record_evidence_file(row, *, deleted=False):
    """Record an evidence upload or deletion in the Audit Trail. Never raises.

    Evidence is what an audit of off-the-job hours ultimately rests on, so who
    uploaded a file, whether the scan accepted it, and who later removed it are
    exactly the questions this exists to answer.
    """
    if not row:
        return
    try:
        from system_audit.writes import record_table_rows

        record_table_rows(
            'evidence_files',
            [dict(zip(EVIDENCE_AUDIT_COLUMNS, row))],
            reason='deleted' if deleted else '',
            deleted=deleted,
            using='enrolment',
        )
    except Exception:
        logger.warning('Could not record an evidence file write.', exc_info=True)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _conn():
    return connections["enrolment"]


def _scan(_data_container, _blob_name) -> str:
    """Return a scan verdict: 'clean' | 'infected'.

    TODO: no malware scanner is wired up yet (no ClamAV daemon / Defender event
    subscription in this environment). Until one exists we optimistically treat
    uploads as clean so the quarantine->approved promotion works end-to-end.
    When a scanner is available, download the blob and inspect it here (see
    EVIDENCE_CLOUD.md §6) and return 'infected' on a hit.
    """
    return "clean"


def _record_approved_evidence(cur, blob_name, original_filename, approved_path):
    """Insert the approved blob into "Learner"."Evidence" (the requested index).
    Idempotent on Azure_id so a re-promote doesn't duplicate the row."""
    cur.execute('SELECT 1 FROM "Learner"."Evidence" WHERE "Azure_id" = %s LIMIT 1', [blob_name])
    if cur.fetchone():
        return
    cur.execute(
        'INSERT INTO "Learner"."Evidence" ("Azure_id", "Evidence_name", "Evidence_path") '
        "VALUES (%s, %s, %s)",
        [blob_name, original_filename, approved_path],
    )


def _learner_profile_ids_for_source(cur, learner_id):
    from .reflection_submissions import _learner_profile_ids_for_source as resolve
    return resolve(cur, learner_id)


def _training_plan_details(value):
    # Raw PostgreSQL cursors may return JSON/JSONB as text. The client expects
    # an object: forwarding text loses the entered title, placement and KSBs.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def _evidence_lineage(kind, learner_id, section_ref):
    section_ref = str(section_ref or "").strip()
    lineage = {"component_ref": None, "progress_entry_id": None}
    if not section_ref:
        return lineage
    try:
        with _conn().cursor() as cur:
            cur.execute("select id from curriculum.components where id = %s limit 1", [section_ref])
            if cur.fetchone():
                lineage["component_ref"] = section_ref
            learner_ids = _learner_profile_ids_for_source(cur, learner_id)
            if not learner_ids:
                return lineage
            cur.execute(
                """
                select p.id
                  from "Learner"."learner_progress_entries" p
                 where p.component_ref = %s
                   and p.learner_id::text = any(%s)
                 order by p.submitted_at desc nulls last, p.id desc
                 limit 1
                """,
                [section_ref, learner_ids],
            )
            row = cur.fetchone()
            if row:
                lineage["progress_entry_id"] = row[0]
    except DatabaseError:
        return lineage
    return lineage


@csrf_exempt
# Learners and admins can upload to the selected learner's portfolio.
@learner_self_or_admin(kwarg="pk")
def upload_evidence(request, kind, pk):
    """Multipart upload -> quarantine -> (scan) -> promote to approved.
    On approval the blob is recorded in "Learner"."Evidence"."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if kind not in VALID_KINDS:
        return _error("Unknown learner kind.", 400)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)

    f = request.FILES.get("file")
    section_ref = (request.POST.get("section_ref") or "").strip()
    if not f or not section_ref:
        return _error("file and section_ref are required.", 400)
    if not _evidence_type_allowed(f):
        return _error(
            "Unsupported file type. Upload a PDF, Word or PowerPoint document, "
            "an image (PNG/JPEG) or an MP4 video.",
            400,
        )
    if f.size > MAX_BYTES:
        return _error("File exceeds the 50 MB size limit.", 400)

    # Training-plan context the file was uploaded against (module/week/component
    # identity+titles), stored verbatim so the row stays traceable even if the
    # curriculum is later restructured. Optional — posted as a JSON string field.
    training_plan_details = None
    raw_details = request.POST.get("training_plan_details")
    if raw_details:
        try:
            training_plan_details = json.loads(raw_details)
        except ValueError:
            return _error("training_plan_details must be valid JSON.", 400)
    lineage = _evidence_lineage(kind, pk, section_ref)

    ensure_evidence_tables()

    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else "bin"
    file_id = uuid.uuid4()
    blob_name = f"{kind}/{pk}/{section_ref}/{file_id}.{ext}"
    quarantine = settings.AZURE_QUARANTINE_CONTAINER

    try:
        upload_to_quarantine(f, blob_name, f.content_type)
    except Exception as exc:  # SDK / network / duplicate-blob errors
        logger.warning("Evidence upload to quarantine failed: %s", exc)
        return _error("Upload to storage failed.", 502)

    # Record the pending row.
    try:
        with _conn().cursor() as cur:
            cur.execute(
                """
                insert into "Learner"."evidence_files"
                  (id, learner_kind, learner_id, section_ref, container, blob_name,
                   original_filename, content_type, size_bytes, status, uploaded_by, uploaded_at,
                   "Training_plan_details", component_ref, progress_entry_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s)
                """,
                [str(file_id), kind, str(pk), section_ref, quarantine, blob_name,
                 f.name, f.content_type, f.size, str(pk), timezone.now(),
                 json.dumps(training_plan_details) if training_plan_details is not None else None,
                 lineage.get("component_ref"), lineage.get("progress_entry_id")],
            )
    except DatabaseError as exc:
        logger.warning("Could not record evidence_files row: %s", exc)
        return _error("Could not record evidence metadata.", 502)

    # Scan + route. (No async worker infra yet -> inline; move to Celery/RQ later.)
    verdict = _scan(quarantine, blob_name)
    approved = settings.AZURE_APPROVED_CONTAINER
    rejected = settings.AZURE_REJECTED_CONTAINER
    dest = approved if verdict == "clean" else rejected
    status = "approved" if verdict == "clean" else "rejected"

    try:
        move_blob(quarantine, dest, blob_name)
    except Exception as exc:
        logger.warning("Evidence promotion (%s -> %s) failed: %s", quarantine, dest, exc)
        return _error("Could not finalise upload.", 502)

    try:
        with _conn().cursor() as cur:
            cur.execute(
                'update "Learner"."evidence_files" '
                "set container = %s, status = %s, scan_result = %s, reviewed_at = %s "
                "where id = %s "
                "returning " + EVIDENCE_AUDIT_SQL,
                [dest, status, verdict, timezone.now(), str(file_id)],
            )
            # Recorded here rather than at the insert above. The insert writes a
            # `pending` row that this statement settles moments later in the
            # same request, so recording both would report one upload as two
            # edits -- and the second is the one that says whether the file was
            # accepted at all.
            _record_evidence_file(cur.fetchone())
            if status == "approved":
                _record_approved_evidence(cur, blob_name, f.name, blob_url(approved, blob_name))
    except DatabaseError as exc:
        logger.warning("Could not update evidence status / record approval: %s", exc)
        return _error("Upload stored but metadata update failed.", 502)

    return JsonResponse(
        {"id": str(file_id), "status": status, "scanResult": verdict,
         "filename": f.name, "sectionRef": section_ref},
        status=201,
    )


# The learner's own evidence portfolio; staff assess it, so both may read (A5).
@learner_self_or_staff(kwarg="pk")
def list_evidence(request, kind, pk):
    """The learner's evidence, newest first. Optional ?section_ref= & ?status= filters."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in VALID_KINDS:
        return _error("Unknown learner kind.", 400)
    ensure_evidence_tables()

    where = ["learner_kind = %s", "learner_id = %s"]
    params = [kind, str(pk)]
    section_ref = (request.GET.get("section_ref") or "").strip()
    status = (request.GET.get("status") or "").strip()
    if section_ref:
        where.append("section_ref = %s")
        params.append(section_ref)
    if status:
        where.append("status = %s")
        params.append(status)

    try:
        with _conn().cursor() as cur:
            cur.execute(
                "select id, original_filename, content_type, size_bytes, status, "
                'scan_result, section_ref, uploaded_at, "Training_plan_details", component_ref, progress_entry_id '
                'from "Learner"."evidence_files" '
                f"where {' and '.join(where)} "
                "order by uploaded_at desc",
                params,
            )
            rows = cur.fetchall()
    except DatabaseError as exc:
        logger.warning("Could not list evidence: %s", exc)
        return _error("Could not load evidence.", 502)

    # Whether each file may still be removed, answered the same way delete_evidence
    # answers it, so the page never offers a Remove the server will refuse.
    #
    # Cached per section: every file under one activity shares its answer, and
    # an assignment can carry several.
    marking_by_section = {}

    def _marking(section_ref):
        if section_ref not in marking_by_section:
            marking_by_section[section_ref] = _marking_status(kind, pk, section_ref)
        return marking_by_section[section_ref]

    return JsonResponse({"results": [
        {
            "id": str(r[0]), "filename": r[1], "contentType": r[2], "sizeBytes": r[3],
            "status": r[4], "scanResult": r[5], "sectionRef": r[6],
            "uploadedAt": r[7].isoformat() if r[7] else None,
            "trainingPlanDetails": _training_plan_details(r[8]),
            "componentRef": r[9],
            "progressEntryId": r[10],
            # False once the activity has been handed in: the file is part of
            # what was submitted for marking and is no longer the learner's to
            # withdraw. The UI hides Remove on this rather than offering an
            # action the server will refuse.
            "canDelete": _marking(r[6]) == "",
            # The coach's decision on the activity this file belongs to, or ""
            # when it has not been handed in. Distinct from `status` above,
            # which is the malware-scan verdict: a file can be stored and clean
            # while no coach has ever looked at it.
            "markingStatus": _marking(r[6]),
        }
        for r in rows
    ]})


# Mints a short-lived SAS URL to the learner's file; owner or staff only (A5).
@learner_self_or_staff(kwarg="pk")
def download_evidence(request, kind, pk, file_id):
    """Short-lived SAS URL for an APPROVED file, from the approved container only."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in VALID_KINDS:
        return _error("Unknown learner kind.", 400)
    if not azure_configured():
        return _error("Evidence storage is not configured.", 503)
    ensure_evidence_tables()

    try:
        with _conn().cursor() as cur:
            cur.execute(
                'select blob_name, status from "Learner"."evidence_files" '
                "where id = %s and learner_kind = %s and learner_id = %s",
                [str(file_id), kind, str(pk)],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not look up evidence for download: %s", exc)
        return _error("Could not load evidence.", 502)

    if not row:
        return _error("Evidence not found.", 404)
    blob_name, status = row
    if status != "approved":
        return _error("File is not available for download.", 409)

    url = get_download_sas(settings.AZURE_APPROVED_CONTAINER, blob_name)
    return JsonResponse({"url": url})


def _marking_status(kind, learner_id, section_ref):
    """The coach's decision on the activity this file belongs to, or ''.

    '' means the work has not been handed in, so no coach has seen it. Anything
    else is the status of the submission -- 'submitted_for_tutor_review' while
    it waits, 'accepted' once a coach has validated it.

    The learner's Evidence Library needs this because a file's own `status`
    column is the malware-scan verdict from the upload pipeline: 'approved'
    there means "scanned clean and stored", not "a coach agreed with it".
    Labelling a freshly-uploaded file "Validated" told the learner their
    evidence had been accepted when nobody had looked at it.
    """
    section_ref = str(section_ref or "").strip()
    if not section_ref:
        return ""
    try:
        with _conn().cursor() as cur:
            learner_ids = _learner_profile_ids_for_source(cur, learner_id)
            candidates = sorted({str(learner_id), *(learner_ids or [])})
            cur.execute(
                """
                select status
                  from "Learner"."learning_reflection_submissions"
                 where activity_id = %s
                   and learner_kind = %s
                   and learner_id::text = any(%s)
                 order by case status when 'accepted' then 0 when 'submitted_for_tutor_review' then 1 else 2 end,
                     submitted_at desc nulls last
                 limit 1
                """,
                [section_ref, kind, candidates],
            )
            row = cur.fetchone()
            return str(row[0] or "") if row else ""
    except DatabaseError as exc:
        logger.warning(
            "Could not read marking status for %s/%s: %s", kind, section_ref, exc
        )
        # Not "" -- that would read as "never submitted" and unlock a file the
        # delete gate exists to protect. Refusing is the recoverable direction:
        # a learner can ask a tutor, whereas a file removed from a submission a
        # coach has read cannot be put back. The library shows it as pending,
        # which is honest: we do not currently know.
        return MARKING_STATUS_UNKNOWN


def _is_submitted_for_marking(kind, learner_id, section_ref):
    """Whether this activity has been handed to a coach, so its evidence is
    no longer the learner's to withdraw.

    Expressed in terms of _marking_status so the list endpoint and the delete
    gate cannot drift apart: both ask one function the same question, and a
    change to the rule lands in one place.
    """
    return _marking_status(kind, learner_id, section_ref) != ""


@csrf_exempt
# Portfolio removal uses the same owner/admin permission as uploads.
@learner_self_or_admin(kwarg="pk")
def delete_evidence(request, kind, pk, file_id):
    """Remove one of the learner's own evidence files.

    Lets a learner correct a mistaken upload — the frontend's "reupload" is this
    delete followed by an ordinary upload, so there is no separate replace path.

    Refused once the file is attached to a submitted progress entry: from that
    point an assessor may be marking against it, and deleting it would pull the
    evidence out from under the marking queue. That link is re-derived here as
    well as read off the row, because the row's `progress_entry_id` is captured
    at upload time — null for the normal case of uploading *before* finishing
    the component, which is exactly the case that must stay deletable.
    """
    if request.method != "DELETE":
        return _error("Method not allowed.", 405)
    if kind not in VALID_KINDS:
        return _error("Unknown learner kind.", 400)
    ensure_evidence_tables()

    # Scoping the lookup by learner is the ownership check: another learner's
    # file id simply does not resolve here.
    try:
        with _conn().cursor() as cur:
            cur.execute(
                'select blob_name, container, section_ref, progress_entry_id '
                'from "Learner"."evidence_files" '
                "where id = %s and learner_kind = %s and learner_id = %s",
                [str(file_id), kind, str(pk)],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not look up evidence for delete: %s", exc)
        return _error("Could not load evidence.", 502)

    if not row:
        return _error("Evidence not found.", 404)
    blob_name, container, section_ref, progress_entry_id = row

    if _is_submitted_for_marking(kind, pk, section_ref):
        return _error(
            "This file has been submitted for marking and can no longer be removed. "
            "Ask your tutor if it needs to be replaced.",
            409,
        )

    # Blob first: a delete that removed the row but left the blob would strand
    # a file nothing points at any more. A blob that has already gone is not a
    # failure — the row still has to go.
    if azure_configured() and blob_name and container:
        try:
            delete_blob(container, blob_name)
        except Exception as exc:  # already deleted / SDK / network
            logger.warning("Evidence blob delete failed for %s/%s: %s", container, blob_name, exc)

    try:
        with _conn().cursor() as cur:
            cur.execute(
                'delete from "Learner"."evidence_files" '
                "where id = %s and learner_kind = %s and learner_id = %s "
                "returning " + EVIDENCE_AUDIT_SQL,
                [str(file_id), kind, str(pk)],
            )
            # Read from what the delete returned: that is the only moment the
            # row still exists, and a trail entry for a deletion that cannot say
            # what was deleted is the one entry that most needed to.
            _record_evidence_file(cur.fetchone(), deleted=True)
            # Approved uploads are also indexed in "Learner"."Evidence" by blob
            # name (see _record_approved_evidence); drop that entry too rather
            # than leave it pointing at a blob that no longer exists.
            if blob_name:
                cur.execute('delete from "Learner"."Evidence" where "Azure_id" = %s', [blob_name])
    except DatabaseError as exc:
        logger.warning("Could not delete evidence row: %s", exc)
        return _error("Could not remove the evidence record.", 502)

    return JsonResponse({"id": str(file_id), "deleted": True})
