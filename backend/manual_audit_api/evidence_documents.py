"""Manual-audit evidence uploads and date overlays.

Same behaviour as the automatic audit workspace, but every write lands in
``"Manual_audit".learner_evidence_overrides`` and learners are validated
against ``"Manual_audit".learners`` — so manual evidence work never appears in
the automatic system.
"""

import datetime
import json
import os
import re
import uuid

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from audit_api.learner_exclusions import is_excluded_learner
from .common import CONN, _azure_service_client, _error, _has_audit_permission
from .common import CONN, _azure_service_client, _error, _has_audit_permission, db_is_read_only


MAX_EVIDENCE_SIZE = 25 * 1024 * 1024
ALLOWED_EVIDENCE_EXTENSIONS = {
    ".csv", ".doc", ".docx", ".jpeg", ".jpg", ".pdf", ".png",
    ".ppt", ".pptx", ".txt", ".xls", ".xlsx",
}


def ensure_evidence_override_table(cursor):
    if db_is_read_only(cursor):
        return
    cursor.execute(
        '''
        create table if not exists "Manual_audit".learner_evidence_overrides (
            evidence_id text primary key,
            learner_id bigint not null,
            source_evidence_id bigint,
            is_uploaded boolean not null default false,
            document_name text,
            component_name text,
            evidence_kind text,
            evidence_status text,
            evidence_date date not null,
            azure_container text,
            azure_blob_name text,
            archived_at timestamptz,
            deleted_at timestamptz,
            archived_by text,
            uploaded_by text,
            created_at timestamptz not null default now(),
            updated_at timestamptz not null default now()
        )
        '''
    )
    cursor.execute(
        '''
        create index if not exists manual_learner_evidence_overrides_learner_idx
        on "Manual_audit".learner_evidence_overrides (learner_id, evidence_date)
        '''
    )


def _safe_filename(value):
    filename = os.path.basename(str(value or "").replace("\\", "/")).strip()
    filename = re.sub(r'[^A-Za-z0-9._() -]+', "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip(" .")
    return filename[:180] or "evidence"


def _evidence_date(value):
    try:
        return datetime.date.fromisoformat(str(value or "").strip())
    except ValueError as exc:
        raise ValueError("evidence_date must use YYYY-MM-DD.") from exc


def _learner_exists(cursor, learner_id):
    cursor.execute(
        '''select learner_name from "Manual_audit".learners where aptem_id = %s limit 1''',
        [learner_id],
    )
    learner = cursor.fetchone()
    return bool(learner) and not is_excluded_learner(learner_id, learner[0])


def uploaded_evidence_location(evidence_id, learner_id=None):
    conditions = ["evidence_id = %s", "is_uploaded = true"]
    params = [evidence_id]
    if learner_id is not None:
        conditions.append("learner_id = %s")
        params.append(learner_id)
    with connections[CONN].cursor() as cursor:
        ensure_evidence_override_table(cursor)
        cursor.execute(
            f'''
            select azure_container, azure_blob_name, document_name
            from "Manual_audit".learner_evidence_overrides
            where {' and '.join(conditions)} and deleted_at is null
            limit 1
            ''',
            params,
        )
        row = cursor.fetchone()
    if not row:
        return None
    return row[0], row[1], row[2], "present"


@csrf_exempt
def upload_evidence(request):
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        learner_id = int((request.POST.get("learner_id") or "").strip())
        evidence_date = _evidence_date(request.POST.get("evidence_date"))
    except (TypeError, ValueError) as error:
        return _error(str(error), 400)

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return _error("An evidence file is required.", 400)
    if uploaded_file.size <= 0:
        return _error("The selected evidence file is empty.", 400)
    if uploaded_file.size > MAX_EVIDENCE_SIZE:
        return _error("The evidence file must be 25 MB or smaller.", 400)
    filename = _safe_filename(uploaded_file.name)
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EVIDENCE_EXTENSIONS:
        return _error("This evidence file type is not supported.", 400)

    try:
        with connections[CONN].cursor() as cursor:
            if not _learner_exists(cursor, learner_id):
                return _error("Learner not found.", 404)
    except (KeyError, DatabaseError):
        return _error("Could not validate the learner.", 503)

    evidence_id = f"audit-{uuid.uuid4()}"
    blob_name = f"manual-audit-evidence/{learner_id}/{evidence_id}/{filename}"
    container = getattr(settings, "AZURE_APPROVED_CONTAINER", "evidence-approved")
    try:
        service = _azure_service_client()
        client = service.get_blob_client(container=container, blob=blob_name)
        client.upload_blob(uploaded_file.chunks(), overwrite=False)
    except RuntimeError as error:
        return _error(str(error), 503)
    except Exception:
        return _error("The evidence file could not be uploaded to Azure.", 502)

    document_name = (request.POST.get("document_name") or filename).strip()[:180] or filename
    component_name = (request.POST.get("component_name") or "Auditor uploaded evidence").strip()[:250]
    uploaded_by = (request.POST.get("uploaded_by") or "").strip()[:200] or None
    try:
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                ensure_evidence_override_table(cursor)
                cursor.execute(
                    '''
                    insert into "Manual_audit".learner_evidence_overrides (
                        evidence_id, learner_id, is_uploaded, document_name,
                        component_name, evidence_kind, evidence_status, evidence_date,
                        azure_container, azure_blob_name, uploaded_by
                    ) values (%s, %s, true, %s, %s, 'File', 'Uploaded', %s, %s, %s, %s)
                    ''',
                    [
                        evidence_id, learner_id, document_name, component_name,
                        evidence_date, container, blob_name, uploaded_by,
                    ],
                )
    except (KeyError, DatabaseError):
        try:
            client.delete_blob(delete_snapshots="include")
        except Exception:
            pass
        return _error("The uploaded evidence metadata could not be saved.", 503)

    return JsonResponse({
        "ok": True,
        "evidence_id": evidence_id,
        "document_name": document_name,
        "evidence_date": evidence_date.isoformat(),
    }, status=201)


@csrf_exempt
def update_evidence_date(request, evidence_id):
    if request.method != "PATCH":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
        learner_id = int(body.get("learner_id"))
        evidence_date = _evidence_date(body.get("evidence_date"))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return _error(str(error), 400)

    try:
        with connections[CONN].cursor() as cursor:
            ensure_evidence_override_table(cursor)
            if str(evidence_id).startswith("audit-"):
                cursor.execute(
                    '''
                    update "Manual_audit".learner_evidence_overrides
                    set evidence_date = %s, updated_at = now()
                    where evidence_id = %s and learner_id = %s and is_uploaded = true
                    returning evidence_id
                    ''',
                    [evidence_date, evidence_id, learner_id],
                )
                if not cursor.fetchone():
                    return _error("Uploaded evidence was not found.", 404)
            else:
                try:
                    source_evidence_id = int(evidence_id)
                except ValueError:
                    return _error("Evidence ID is invalid.", 400)
                cursor.execute(
                    '''
                    select 1
                    from fetching_evidence.learner_evidence evidence
                    cross join lateral jsonb_array_elements(
                        case when jsonb_typeof(evidence.evidence) = 'array'
                            then evidence.evidence else '[]'::jsonb end
                    ) item
                    where evidence.learner_id = %s and item ->> 'id' = %s
                    limit 1
                    ''',
                    [learner_id, str(source_evidence_id)],
                )
                if not cursor.fetchone():
                    return _error("Evidence was not found for this learner.", 404)
                cursor.execute(
                    '''
                    insert into "Manual_audit".learner_evidence_overrides (
                        evidence_id, learner_id, source_evidence_id, is_uploaded, evidence_date
                    ) values (%s, %s, %s, false, %s)
                    on conflict (evidence_id) do update set
                        learner_id = excluded.learner_id,
                        source_evidence_id = excluded.source_evidence_id,
                        evidence_date = excluded.evidence_date,
                        updated_at = now()
                    ''',
                    [str(source_evidence_id), learner_id, source_evidence_id, evidence_date],
                )
    except (KeyError, DatabaseError):
        return _error("Could not update the evidence date.", 503)

    return JsonResponse({
        "ok": True,
        "evidence_id": str(evidence_id),
        "evidence_date": evidence_date.isoformat(),
    })


@csrf_exempt
def archive_evidence(request, evidence_id):
    if request.method not in {"PATCH", "DELETE"}:
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
        learner_id = int(body.get("learner_id"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error("learner_id must be an integer.", 400)
    archived_by = str(body.get("archived_by") or "").strip()[:200] or None
    archived = None
    if request.method == "PATCH":
        archived = body.get("archived")
        if not isinstance(archived, bool):
            return _error("archived must be true or false.", 400)

    try:
        with connections[CONN].cursor() as cursor:
            ensure_evidence_override_table(cursor)
            evidence_key = str(evidence_id)
            if evidence_key.startswith("audit-"):
                if request.method == "DELETE":
                    cursor.execute(
                        '''
                        update "Manual_audit".learner_evidence_overrides
                        set deleted_at = now(), archived_by = coalesce(%s, archived_by), updated_at = now()
                        where evidence_id = %s and learner_id = %s and is_uploaded = true
                          and archived_at is not null and deleted_at is null
                        returning deleted_at
                        ''',
                        [archived_by, evidence_key, learner_id],
                    )
                else:
                    cursor.execute(
                        '''
                        update "Manual_audit".learner_evidence_overrides
                        set archived_at = case when %s then now() else null end,
                            deleted_at = null, archived_by = %s, updated_at = now()
                        where evidence_id = %s and learner_id = %s and is_uploaded = true
                        returning archived_at
                        ''',
                        [archived, archived_by, evidence_key, learner_id],
                    )
                changed = cursor.fetchone()
                if not changed:
                    message = "Only archived evidence can be deleted." if request.method == "DELETE" else "Uploaded evidence was not found."
                    return _error(message, 409 if request.method == "DELETE" else 404)
            else:
                try:
                    source_evidence_id = int(evidence_key)
                except ValueError:
                    return _error("Evidence ID is invalid.", 400)
                cursor.execute(
                    '''
                    select substring(item ->> 'created_date' from 1 for 10)::date
                    from fetching_evidence.learner_evidence evidence
                    cross join lateral jsonb_array_elements(
                        case when jsonb_typeof(evidence.evidence) = 'array'
                            then evidence.evidence else '[]'::jsonb end
                    ) item
                    where evidence.learner_id = %s and item ->> 'id' = %s
                      and coalesce(item ->> 'created_date', '') ~ '^\\d{4}-\\d{2}-\\d{2}'
                    order by evidence.fetched_at desc nulls last
                    limit 1
                    ''',
                    [learner_id, str(source_evidence_id)],
                )
                source = cursor.fetchone()
                if not source:
                    return _error("Evidence was not found for this learner.", 404)
                if request.method == "DELETE":
                    cursor.execute(
                        '''
                        update "Manual_audit".learner_evidence_overrides
                        set deleted_at = now(), archived_by = coalesce(%s, archived_by), updated_at = now()
                        where evidence_id = %s and learner_id = %s
                          and archived_at is not null and deleted_at is null
                        returning deleted_at
                        ''',
                        [archived_by, evidence_key, learner_id],
                    )
                    changed = cursor.fetchone()
                    if not changed:
                        return _error("Only archived evidence can be deleted.", 409)
                else:
                    cursor.execute(
                        '''
                        insert into "Manual_audit".learner_evidence_overrides (
                            evidence_id, learner_id, source_evidence_id, is_uploaded,
                            evidence_date, archived_at, deleted_at, archived_by
                        ) values (%s, %s, %s, false, %s,
                                  case when %s then now() else null end, null, %s)
                        on conflict (evidence_id) do update set
                            learner_id = excluded.learner_id,
                            source_evidence_id = excluded.source_evidence_id,
                            archived_at = excluded.archived_at,
                            deleted_at = null,
                            archived_by = excluded.archived_by,
                            updated_at = now()
                        returning archived_at
                        ''',
                        [evidence_key, learner_id, source_evidence_id, source[0], archived, archived_by],
                    )
                    changed = cursor.fetchone()
    except (KeyError, DatabaseError):
        return _error("Could not update the evidence archive state.", 503)

    if request.method == "DELETE":
        return JsonResponse({"ok": True, "evidence_id": str(evidence_id), "deleted": True})
    return JsonResponse({"ok": True, "evidence_id": str(evidence_id), "archived": archived})
