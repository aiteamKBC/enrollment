"""Auditor-managed evidence uploads and date overlays."""

import datetime
import json
import mimetypes
import os
import re
import uuid

from django.conf import settings
from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .audit_trail import EVIDENCE_OVERRIDE_COLUMNS, EVIDENCE_OVERRIDE_SQL, record_audit_rows
from .db_source import resolve
from .learner_exclusions import is_excluded_learner
from .views import _azure_service_client, _has_audit_permission


CONN = "enrolment"
MAX_EVIDENCE_SIZE = 25 * 1024 * 1024
ALLOWED_EVIDENCE_EXTENSIONS = {
    ".csv", ".doc", ".docx", ".jpeg", ".jpg", ".pdf", ".png",
    ".ppt", ".pptx", ".txt", ".xls", ".xlsx",
}


def ensure_evidence_override_table(cursor):
    cursor.execute(
        '''
        select column_name
        from information_schema.columns
        where table_schema = 'Audit' and table_name = 'learner_evidence_overrides'
        '''
    )
    existing_columns = {row[0] for row in cursor.fetchall()}
    required_columns = {
        "evidence_id", "learner_id", "source_evidence_id", "is_uploaded",
        "document_name", "component_name", "evidence_kind", "evidence_status",
        "evidence_date", "azure_container", "azure_blob_name", "archived_at",
        "deleted_at", "archived_by", "uploaded_by", "source_activity_id",
        "source_activity_month", "source_activity_category", "created_at", "updated_at",
    }
    if required_columns.issubset(existing_columns):
        return
    cursor.execute(
        '''
        create table if not exists "Audit".learner_evidence_overrides (
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
            source_activity_id bigint,
            source_activity_month text,
            source_activity_category text,
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
        '''alter table "Audit".learner_evidence_overrides add column if not exists archived_at timestamptz'''
    )
    cursor.execute(
        '''alter table "Audit".learner_evidence_overrides add column if not exists deleted_at timestamptz'''
    )
    cursor.execute(
        '''alter table "Audit".learner_evidence_overrides add column if not exists archived_by text'''
    )
    cursor.execute(
        '''alter table "Audit".learner_evidence_overrides add column if not exists source_activity_id bigint'''
    )
    cursor.execute(
        '''alter table "Audit".learner_evidence_overrides add column if not exists source_activity_month text'''
    )
    cursor.execute(
        '''alter table "Audit".learner_evidence_overrides add column if not exists source_activity_category text'''
    )
    cursor.execute(
        '''
        create index if not exists learner_evidence_overrides_learner_idx
        on "Audit".learner_evidence_overrides (learner_id, evidence_date)
        '''
    )


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


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
        '''select learner_name from "Last_audit".learners where aptem_id = %s limit 1''',
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
    with connections[resolve(CONN)].cursor() as cursor:
        ensure_evidence_override_table(cursor)
        cursor.execute(
            f'''
            select azure_container, azure_blob_name, document_name
            from "Audit".learner_evidence_overrides
            where {' and '.join(conditions)} and deleted_at is null
              and azure_container is not null and azure_blob_name is not null
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
        with connections[resolve(CONN)].cursor() as cursor:
            if not _learner_exists(cursor, learner_id):
                return _error("Learner not found.", 404)
    except (KeyError, DatabaseError):
        return _error("Could not validate the learner.", 503)

    evidence_id = f"audit-{uuid.uuid4()}"
    blob_name = f"audit-evidence/{learner_id}/{evidence_id}/{filename}"
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
            with connections[resolve(CONN)].cursor() as cursor:
                ensure_evidence_override_table(cursor)
                cursor.execute(
                    '''
                    insert into "Audit".learner_evidence_overrides (
                        evidence_id, learner_id, is_uploaded, document_name,
                        component_name, evidence_kind, evidence_status, evidence_date,
                        azure_container, azure_blob_name, uploaded_by
                    ) values (%s, %s, true, %s, %s, 'File', 'Uploaded', %s, %s, %s, %s)
                    returning ''' + EVIDENCE_OVERRIDE_SQL + '''
                    ''',
                    [
                        evidence_id, learner_id, document_name, component_name,
                        evidence_date, container, blob_name, uploaded_by,
                    ],
                )
                record_audit_rows(
                    'learner_evidence_overrides', EVIDENCE_OVERRIDE_COLUMNS, cursor.fetchone(),
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
def select_activity_evidence(request):
    """Attach an activity already arranged on the monthly report as evidence."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request, write=True):
        return _error("Authentication or audit permission is required.", 403)
    try:
        body = json.loads(request.body or b"{}")
        learner_id = int(body.get("learner_id"))
        manual_activity_id = int(body.get("manual_activity_id"))
        evidence_date = _evidence_date(body.get("evidence_date"))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        return _error(str(error), 400)
    if learner_id <= 0 or manual_activity_id <= 0:
        return _error("learner_id and manual_activity_id must be positive integers.", 400)

    component_name = str(
        body.get("component_name") or "Selected monthly report activity"
    ).strip()[:250] or "Selected monthly report activity"
    selected_by = str(body.get("selected_by") or "").strip()[:200] or None

    activity_connection = resolve("audit") if "audit" in connections.databases else resolve(CONN)
    try:
        with connections[activity_connection].cursor() as cursor:
            cursor.execute(
                '''
                select title, activity_date, month, category
                from "Learner"."learner_journal_rows"
                where id = %s and aptem_id = %s and deleted_at is null
                limit 1
                ''',
                [manual_activity_id, learner_id],
            )
            activity = cursor.fetchone()
    except (KeyError, DatabaseError):
        return _error("Could not read the selected monthly report activity.", 503)
    if not activity:
        return _error("The selected activity was not found for this learner.", 404)

    try:
        with connections[resolve(CONN)].cursor() as cursor:
            if not _learner_exists(cursor, learner_id):
                return _error("Learner not found.", 404)
    except (KeyError, DatabaseError):
        return _error("Could not validate the learner.", 503)

    evidence_id = f"audit-activity-{uuid.uuid4()}"
    document_name = str(activity[0] or "Monthly report activity").strip()[:180]
    try:
        with transaction.atomic(using=CONN):
            with connections[resolve(CONN)].cursor() as cursor:
                ensure_evidence_override_table(cursor)
                cursor.execute(
                    '''
                    select evidence_id
                    from "Audit".learner_evidence_overrides
                    where learner_id = %s and source_activity_id = %s
                      and component_name = %s and evidence_date = %s
                      and deleted_at is null
                    limit 1
                    ''',
                    [learner_id, manual_activity_id, component_name, evidence_date],
                )
                existing = cursor.fetchone()
                if existing:
                    return JsonResponse({
                        "ok": True,
                        "evidence_id": existing[0],
                        "document_name": document_name,
                        "evidence_date": evidence_date.isoformat(),
                        "already_selected": True,
                    })
                cursor.execute(
                    '''
                    insert into "Audit".learner_evidence_overrides (
                        evidence_id, learner_id, is_uploaded, document_name,
                        component_name, evidence_kind, evidence_status, evidence_date,
                        source_activity_id, source_activity_month,
                        source_activity_category, uploaded_by
                    ) values (%s, %s, true, %s, %s, 'Activity', 'Selected', %s,
                              %s, %s, %s, %s)
                    returning ''' + EVIDENCE_OVERRIDE_SQL + '''
                    ''',
                    [
                        evidence_id, learner_id, document_name, component_name,
                        evidence_date, manual_activity_id, activity[2], activity[3],
                        selected_by,
                    ],
                )
                record_audit_rows(
                    'learner_evidence_overrides', EVIDENCE_OVERRIDE_COLUMNS, cursor.fetchone(),
                )
    except (KeyError, DatabaseError):
        return _error("Could not save the selected activity as evidence.", 503)

    return JsonResponse({
        "ok": True,
        "evidence_id": evidence_id,
        "document_name": document_name,
        "evidence_date": evidence_date.isoformat(),
        "activity_date": activity[1].isoformat() if activity[1] else None,
        "activity_month": activity[2],
        "activity_category": activity[3],
        "already_selected": False,
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
        with connections[resolve(CONN)].cursor() as cursor:
            ensure_evidence_override_table(cursor)
            if str(evidence_id).startswith("audit-"):
                cursor.execute(
                    '''
                    update "Audit".learner_evidence_overrides
                    set evidence_date = %s, updated_at = now()
                    where evidence_id = %s and learner_id = %s and is_uploaded = true
                    returning ''' + EVIDENCE_OVERRIDE_SQL + '''
                    ''',
                    [evidence_date, evidence_id, learner_id],
                )
                moved = cursor.fetchone()
                if not moved:
                    return _error("Uploaded evidence was not found.", 404)
                # Moving an evidence date moves which month its hours count in,
                # and this statement records no actor of its own -- only
                # `updated_at`. The trail is where the name comes from.
                record_audit_rows(
                    'learner_evidence_overrides', EVIDENCE_OVERRIDE_COLUMNS, moved,
                )
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
                    insert into "Audit".learner_evidence_overrides (
                        evidence_id, learner_id, source_evidence_id, is_uploaded, evidence_date
                    ) values (%s, %s, %s, false, %s)
                    on conflict (evidence_id) do update set
                        learner_id = excluded.learner_id,
                        source_evidence_id = excluded.source_evidence_id,
                        evidence_date = excluded.evidence_date,
                        updated_at = now()
                    returning ''' + EVIDENCE_OVERRIDE_SQL + '''
                    ''',
                    [str(source_evidence_id), learner_id, source_evidence_id, evidence_date],
                )
                record_audit_rows(
                    'learner_evidence_overrides', EVIDENCE_OVERRIDE_COLUMNS, cursor.fetchone(),
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
    if request.method == "PATCH":
        archived = body.get("archived")
        if not isinstance(archived, bool):
            return _error("archived must be true or false.", 400)

    try:
        with connections[resolve(CONN)].cursor() as cursor:
            ensure_evidence_override_table(cursor)
            evidence_key = str(evidence_id)
            if evidence_key.startswith("audit-"):
                if request.method == "DELETE":
                    cursor.execute(
                        '''
                        update "Audit".learner_evidence_overrides
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
                        update "Audit".learner_evidence_overrides
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
                        update "Audit".learner_evidence_overrides
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
                        insert into "Audit".learner_evidence_overrides (
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
