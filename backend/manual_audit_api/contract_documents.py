"""Manual-audit contract uploads and reversible archive state.

Unlike the automatic audit workspace (which stores uploads in the shared
``fetching_evidence.aptem_cv_contracts_probe`` table), manual uploads live in
``"Manual_audit".contract_uploads`` so they never appear in the automatic
system. Shared source contracts remain visible read-only; archiving/renaming
one of them is stored as an overlay row in
``"Manual_audit".contract_document_archive``.

Contract ids therefore come in two forms:

* ``"<int>"``        -> a shared source contract from the probe table
* ``"manual-<int>"`` -> a contract uploaded through the manual workspace
"""

import json
import mimetypes
import os
import re
import uuid

from django.db import DatabaseError, connections, transaction
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from audit_api.learner_exclusions import is_excluded_learner
from .common import CONN, _azure_service_client, _error, _has_audit_permission
from .common import CONN, _azure_service_client, _error, _has_audit_permission, db_is_read_only


MAX_CONTRACT_SIZE = 25 * 1024 * 1024
ALLOWED_CONTRACT_EXTENSIONS = {
    ".csv", ".doc", ".docx", ".jpeg", ".jpg", ".pdf", ".png",
    ".ppt", ".pptx", ".txt", ".xls", ".xlsx",
}
MANUAL_CONTRACT_PREFIX = "manual-"


def ensure_contract_archive_table(cursor):
    if db_is_read_only(cursor):
        return
    cursor.execute(
        '''
        create table if not exists "Manual_audit".contract_document_archive (
            contract_id bigint primary key,
            learner_id bigint not null,
            archived_at timestamptz,
            deleted_at timestamptz,
            display_name text,
            archived_by text,
            updated_at timestamptz not null default now()
        )
        '''
    )
    cursor.execute(
        '''
        create index if not exists manual_contract_document_archive_learner_idx
        on "Manual_audit".contract_document_archive (learner_id, archived_at)
        '''
    )


def ensure_contract_uploads_table(cursor):
    if db_is_read_only(cursor):
        return
    cursor.execute(
        '''
        create table if not exists "Manual_audit".contract_uploads (
            id bigserial primary key,
            learner_id bigint not null,
            document_name text not null,
            display_name text,
            status text not null default 'Uploaded',
            date timestamptz not null default now(),
            programme text,
            azure_path text,
            original_filename text,
            content_type text,
            size bigint,
            uploaded_by text,
            archived_at timestamptz,
            deleted_at timestamptz,
            archived_by text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        '''
    )
    cursor.execute(
        '''
        create index if not exists manual_contract_uploads_learner_idx
        on "Manual_audit".contract_uploads (learner_id, date)
        '''
    )


def parse_manual_contract_id(contract_id):
    """Return ``("manual", int)`` or ``("source", int)``; raises ValueError."""
    text = str(contract_id or "").strip()
    if text.startswith(MANUAL_CONTRACT_PREFIX):
        return "manual", int(text[len(MANUAL_CONTRACT_PREFIX):])
    return "source", int(text)


def _safe_upload_filename(value):
    filename = os.path.basename(str(value or "").replace("\\", "/")).strip()
    filename = re.sub(r'[^A-Za-z0-9._() -]+', "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:180] or "contract"


def _learner_upload_metadata(cursor, learner_id):
    cursor.execute(
        '''
        select learner_name, learner_email, programme_name
        from "Manual_audit".learners
        where aptem_id = %s
        limit 1
        ''',
        [learner_id],
    )
    learner = cursor.fetchone()
    if not learner or is_excluded_learner(learner_id, learner[0]):
        return None
    return {
        "name": learner[0] or f"Learner {learner_id}",
        "email": learner[1],
        "programme": learner[2] or "",
    }


@csrf_exempt
def upload_contract(request):
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)

    try:
        learner_id = int((request.POST.get("learner_id") or "").strip())
    except (TypeError, ValueError):
        return _error("learner_id must be an integer.", 400)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return _error("A document file is required.", 400)
    if uploaded_file.size <= 0:
        return _error("The selected document is empty.", 400)
    if uploaded_file.size > MAX_CONTRACT_SIZE:
        return _error("The document must be 25 MB or smaller.", 400)

    filename = _safe_upload_filename(uploaded_file.name)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_CONTRACT_EXTENSIONS:
        return _error("This document type is not supported.", 400)
    document_name = (request.POST.get("document_name") or filename).strip()[:180] or filename

    try:
        with connections[CONN].cursor() as cursor:
            learner = _learner_upload_metadata(cursor, learner_id)
    except (KeyError, DatabaseError):
        return _error("Could not validate the learner.", 503)
    if not learner:
        return _error("Learner not found.", 404)

    upload_id = uuid.uuid4()
    blob_name = f"manual_audit_contracts/{learner_id}/uploads/{upload_id}/{filename}"
    try:
        service = _azure_service_client()
        container = os.environ.get("AZURE_CONTRACTS_CONTAINER", "contracts").strip() or "contracts"
        client = service.get_blob_client(container=container, blob=blob_name)
        client.upload_blob(uploaded_file.chunks(), overwrite=False)
        account_name = getattr(service, "account_name", "")
        if not account_name:
            raise RuntimeError("The Azure storage account name is unavailable.")
        azure_path = f"az://{account_name}/{container}/{blob_name}"
    except RuntimeError as error:
        return _error(str(error), 503)
    except Exception:
        return _error("The document could not be uploaded to Azure.", 502)

    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_contract_uploads_table(cursor)
                cursor.execute(
                    '''
                    insert into "Manual_audit".contract_uploads (
                        learner_id, document_name, status, programme, azure_path,
                        original_filename, content_type, size, uploaded_by
                    ) values (%s, %s, 'Uploaded', %s, %s, %s, %s, %s, %s)
                    returning id, date
                    ''',
                    [
                        learner_id, document_name, learner["programme"], azure_path,
                        uploaded_file.name,
                        uploaded_file.content_type or mimetypes.guess_type(filename)[0],
                        uploaded_file.size,
                        (request.POST.get("uploaded_by") or "").strip()[:200] or None,
                    ],
                )
                upload_row_id, uploaded_at = cursor.fetchone()
    except (KeyError, DatabaseError):
        try:
            client.delete_blob(delete_snapshots="include")
        except Exception:
            pass
        return _error("The uploaded document could not be saved.", 503)

    return JsonResponse({
        "ok": True,
        "contract_id": f"{MANUAL_CONTRACT_PREFIX}{upload_row_id}",
        "document_name": document_name,
        "uploaded_at": uploaded_at.isoformat() if uploaded_at else None,
    }, status=201)


@csrf_exempt
def archive_contract(request, contract_id):
    if request.method not in {"PATCH", "DELETE"}:
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error("Invalid JSON body.", 400)
    archived_by = str(body.get("archived_by") or "").strip()[:200] or None
    archived = None
    if request.method == "PATCH":
        archived = body.get("archived")
        if not isinstance(archived, bool):
            return _error("archived must be true or false.", 400)

    try:
        kind, numeric_id = parse_manual_contract_id(contract_id)
    except (TypeError, ValueError):
        return _error("Contract ID is invalid.", 400)

    try:
        with connections[CONN].cursor() as cursor:
            if kind == "manual":
                ensure_contract_uploads_table(cursor)
                if request.method == "DELETE":
                    cursor.execute(
                        '''
                        update "Manual_audit".contract_uploads
                        set deleted_at = now(), archived_by = coalesce(%s, archived_by), updated_at = now()
                        where id = %s and archived_at is not null and deleted_at is null
                        returning deleted_at
                        ''',
                        [archived_by, numeric_id],
                    )
                    deleted_row = cursor.fetchone()
                    if not deleted_row:
                        return _error("Only an archived contract can be deleted.", 409)
                    return JsonResponse({
                        "ok": True,
                        "contract_id": str(contract_id),
                        "deleted": True,
                        "deleted_at": deleted_row[0].isoformat(),
                    })
                cursor.execute(
                    '''
                    update "Manual_audit".contract_uploads
                    set archived_at = case when %s then now() else null end,
                        deleted_at = null, archived_by = %s, updated_at = now()
                    where id = %s and deleted_at is null
                    returning archived_at
                    ''',
                    [archived, archived_by, numeric_id],
                )
                row = cursor.fetchone()
                if not row:
                    return _error("Contract not found.", 404)
                archived_at = row[0]
            else:
                cursor.execute(
                    '''select learner_id from fetching_evidence.aptem_cv_contracts_probe where id = %s limit 1''',
                    [numeric_id],
                )
                row = cursor.fetchone()
                if not row:
                    return _error("Contract not found.", 404)
                ensure_contract_archive_table(cursor)
                if request.method == "DELETE":
                    cursor.execute(
                        '''
                        update "Manual_audit".contract_document_archive
                        set deleted_at = now(), archived_by = coalesce(%s, archived_by), updated_at = now()
                        where contract_id = %s and archived_at is not null and deleted_at is null
                        returning deleted_at
                        ''',
                        [archived_by, numeric_id],
                    )
                    deleted_row = cursor.fetchone()
                    if not deleted_row:
                        return _error("Only an archived contract can be deleted.", 409)
                    return JsonResponse({
                        "ok": True,
                        "contract_id": str(contract_id),
                        "deleted": True,
                        "deleted_at": deleted_row[0].isoformat(),
                    })
                cursor.execute(
                    '''
                    insert into "Manual_audit".contract_document_archive (
                        contract_id, learner_id, archived_at, deleted_at, archived_by, updated_at
                    ) values (%s, %s, case when %s then now() else null end, null, %s, now())
                    on conflict (contract_id) do update set
                        learner_id = excluded.learner_id,
                        archived_at = excluded.archived_at,
                        deleted_at = null,
                        archived_by = excluded.archived_by,
                        updated_at = now()
                    returning archived_at
                    ''',
                    [numeric_id, row[0], archived, archived_by],
                )
                archived_at = cursor.fetchone()[0]
    except (KeyError, DatabaseError):
        return _error("Could not update the contract archive state.", 503)

    return JsonResponse({
        "ok": True,
        "contract_id": str(contract_id),
        "archived": archived,
        "archived_at": archived_at.isoformat() if archived_at else None,
    })


@csrf_exempt
def rename_contract(request, contract_id):
    if request.method != "PATCH":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error("Invalid JSON body.", 400)
    display_name = re.sub(r"[\r\n]+", " ", str(body.get("document_name") or "")).strip()
    if not display_name:
        return _error("document_name is required.", 400)
    if len(display_name) > 180:
        return _error("document_name must be 180 characters or fewer.", 400)

    try:
        kind, numeric_id = parse_manual_contract_id(contract_id)
    except (TypeError, ValueError):
        return _error("Contract ID is invalid.", 400)

    try:
        with connections[CONN].cursor() as cursor:
            if kind == "manual":
                ensure_contract_uploads_table(cursor)
                cursor.execute(
                    '''
                    update "Manual_audit".contract_uploads
                    set display_name = %s, updated_at = now()
                    where id = %s and deleted_at is null
                    returning display_name
                    ''',
                    [display_name, numeric_id],
                )
                row = cursor.fetchone()
                if not row:
                    return _error("Contract not found.", 404)
                saved_name = row[0]
            else:
                cursor.execute(
                    '''select learner_id from fetching_evidence.aptem_cv_contracts_probe where id = %s limit 1''',
                    [numeric_id],
                )
                row = cursor.fetchone()
                if not row:
                    return _error("Contract not found.", 404)
                ensure_contract_archive_table(cursor)
                cursor.execute(
                    '''
                    insert into "Manual_audit".contract_document_archive (
                        contract_id, learner_id, display_name, updated_at
                    ) values (%s, %s, %s, now())
                    on conflict (contract_id) do update set
                        learner_id = excluded.learner_id,
                        display_name = excluded.display_name,
                        updated_at = now()
                    returning display_name
                    ''',
                    [numeric_id, row[0], display_name],
                )
                saved_name = cursor.fetchone()[0]
    except (KeyError, DatabaseError):
        return _error("Could not rename the contract.", 503)

    return JsonResponse({
        "ok": True,
        "contract_id": str(contract_id),
        "document_name": saved_name,
    })
