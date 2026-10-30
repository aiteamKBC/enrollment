"""Files uploaded to the wizard builder's custom "File upload" fields.

Same route as every other learner-supplied enrolment file (see learner_uploads):
quarantine -> scan -> approved in Azure. Each stored file is recorded in the
field's own jsonb column — the one the wizard builder assigned it, in its step's
table (see wizard_layout) — as one object per file carrying its `path`.

    GET    /enrolment_api/wizard/<kind>/<id>/custom-uploads/<field_key>/
    POST   /enrolment_api/wizard/<kind>/<id>/custom-uploads/<field_key>/              (multipart: file)
    GET    /enrolment_api/wizard/<kind>/<id>/custom-uploads/<field_key>/<file_id>/download/
    DELETE /enrolment_api/wizard/<kind>/<id>/custom-uploads/<field_key>/<file_id>/

The draft save never writes these columns (project_custom_fields skips uploads),
so a save racing an upload cannot drop a file.
"""
import json
import logging

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from learner_api.evidence_storage import azure_configured, get_download_sas

from . import learner_uploads as uploads
from .auth import enrolment_login_required
from .extended_ilr import KINDS
from .wizard_layout import COLUMN_RE, CONN, TABLE_RE, _q, current_layout, custom_fields

logger = logging.getLogger(__name__)

_error = uploads.error


def _field(field_key, for_upload):
    """(table, column) of an upload field, or an error response.

    New files are only accepted while the field is in the wizard; existing ones
    stay listable and removable after it is removed, so nothing is stranded.
    """
    try:
        found = custom_fields(current_layout()).get(field_key)
    except DatabaseError as exc:
        logger.warning("Could not read the wizard layout for an upload: %s", exc)
        return None, _error("Could not load the wizard layout.", 502)
    item = found[0] if found else None
    if not item or item.get("type") != "upload":
        return None, _error("No such upload field.", 404)
    if for_upload and item.get("hidden"):
        return None, _error("This field has been removed from the wizard.", 400)
    table, column = item.get("table"), item.get("column")
    if not (isinstance(table, str) and TABLE_RE.match(table) and isinstance(column, str) and COLUMN_RE.match(column)):
        return None, _error("This field has no storage.", 500)
    return (table, column), None


def _check_learner(kind, learner_id):
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    try:
        if not KINDS[kind].objects.filter(pk=learner_id).exists():
            return _error("Learner not found.", 404)
    except DatabaseError as exc:
        logger.warning("Could not look up learner for custom uploads: %s", exc)
        return _error("Could not load the learner.", 502)
    return None


def _decode(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return uploads.items(value)


def _read(cursor, table, column, kind, learner_id, lock=False):
    cursor.execute(
        f"SELECT {_q(column, COLUMN_RE)} FROM enrolment.{_q(table, TABLE_RE)} "
        'WHERE "Learner_kind" = %s AND "Learner_id" = %s' + (" FOR UPDATE" if lock else ""),
        [kind, learner_id],
    )
    row = cursor.fetchone()
    return _decode(row[0]) if row else []


def _write(cursor, table, column, kind, learner_id, items):
    qc = _q(column, COLUMN_RE)
    cursor.execute(
        f'INSERT INTO enrolment.{_q(table, TABLE_RE)} ("Learner_kind", "Learner_id", {qc}) VALUES (%s, %s, %s::jsonb) '
        f'ON CONFLICT ("Learner_kind", "Learner_id") DO UPDATE SET {qc} = EXCLUDED.{qc}, "Updated_at" = now()',
        [kind, learner_id, json.dumps(items)],
    )


@enrolment_login_required
@csrf_exempt
def custom_uploads(request, kind, learner_id, field_key):
    """GET lists the field's stored files; POST uploads one."""
    refused = _check_learner(kind, learner_id)
    if refused:
        return refused
    if request.method not in ("GET", "POST"):
        return _error("Method not allowed.", 405)
    storage, refused = _field(field_key, for_upload=request.method == "POST")
    if refused:
        return refused
    table, column = storage

    if request.method == "GET":
        try:
            with connections[CONN].cursor() as cursor:
                items = _read(cursor, table, column, kind, learner_id)
        except DatabaseError as exc:
            logger.warning("Could not list custom uploads: %s", exc)
            return _error("Could not load documents.", 502)
        return JsonResponse({"results": [uploads.public(i) for i in items]})

    if not azure_configured():
        return _error("Document storage is not configured.", 503)
    f = request.FILES.get("file")
    refused = uploads.invalid_upload(f)
    if refused:
        return refused

    item, failed = uploads.store(f, f"{kind}/{learner_id}/custom/{field_key}", request)
    if failed:
        return failed

    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                # Make sure the row exists, then lock it, so two uploads at once
                # each append to what the other wrote.
                cursor.execute(
                    f'INSERT INTO enrolment.{_q(table, TABLE_RE)} ("Learner_kind", "Learner_id") VALUES (%s, %s) '
                    'ON CONFLICT ("Learner_kind", "Learner_id") DO NOTHING',
                    [kind, learner_id],
                )
                items = _read(cursor, table, column, kind, learner_id, lock=True)
                _write(cursor, table, column, kind, learner_id, [*items, item])
    except DatabaseError as exc:
        logger.warning("Could not record custom upload: %s", exc)
        uploads.discard(item["container"], item["blobName"])
        return _error("Could not record the uploaded file.", 502)

    return JsonResponse(uploads.public(item), status=201)


@enrolment_login_required
def download_custom_upload(request, kind, learner_id, field_key, file_id):
    """Short-lived SAS URL for one stored file."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    if not azure_configured():
        return _error("Document storage is not configured.", 503)
    storage, refused = _field(field_key, for_upload=False)
    if refused:
        return refused
    table, column = storage
    try:
        with connections[CONN].cursor() as cursor:
            items = _read(cursor, table, column, kind, learner_id)
    except DatabaseError as exc:
        logger.warning("Could not look up custom upload for download: %s", exc)
        return _error("Could not load documents.", 502)
    item = next((i for i in items if i["id"] == str(file_id)), None)
    if item is None or not item.get("container") or not item.get("blobName"):
        return _error("Document not found.", 404)
    return JsonResponse({"url": get_download_sas(item["container"], item["blobName"])})


@enrolment_login_required
@csrf_exempt
def delete_custom_upload(request, kind, learner_id, field_key, file_id):
    """Remove one file: the blob, then its record."""
    if request.method != "DELETE":
        return _error("Method not allowed.", 405)
    if kind not in KINDS:
        return _error(f"Unknown learner kind '{kind}'.", 400)
    storage, refused = _field(field_key, for_upload=False)
    if refused:
        return refused
    table, column = storage
    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                items = _read(cursor, table, column, kind, learner_id, lock=True)
                item = next((i for i in items if i["id"] == str(file_id)), None)
                if item is None:
                    return _error("Document not found.", 404)
                # Blob first: removing the record but not the file would strand
                # a document nothing points at.
                if azure_configured():
                    uploads.remove_blob(item)
                _write(cursor, table, column, kind, learner_id, [i for i in items if i["id"] != str(file_id)])
    except DatabaseError as exc:
        logger.warning("Could not remove custom upload: %s", exc)
        return _error("Could not remove the document record.", 502)
    return JsonResponse({"id": str(file_id), "deleted": True})
