import datetime
import decimal
import hashlib
import json
import mimetypes
import os
import re
import uuid
from collections import defaultdict
from html import escape
from urllib.parse import quote, unquote, urlsplit

from django.db import DatabaseError, connections
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from django.views.decorators.csrf import csrf_exempt
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from .db_source import resolve

try:
    from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas
except ImportError:  # pragma: no cover - handled at runtime when Azure is not installed.
    BlobServiceClient = None
    BlobSasPermissions = None
    generate_blob_sas = None


AUDIT_SCHEMA = "Audit"
MAIN_TABLE = "Aptem_LMS_matching"
SIGNOFF_TABLE = "monthly_audit_signoffs"
STUDENT_SOURCE_DATA_COLUMN = "Learner_source_data"
AUDIT_VERSION = "aptem-lms-reconciliation-v1"
SOURCE_DATA_SUMMARY_LIMIT = 120
DEFAULT_KBC_ATTENDANCE_DATABASE = "AiTeamKBC"
DEFAULT_ASSIGNMENT_DATABASE = "fetching_attendence"
MONTHLY_HOURS_SCHEMA = "fetching_evidence"
MONTHLY_HOURS_TABLE = "learner_hours_monthly"
LIVE_SESSION_HOURS = 2
DEFAULT_ASSIGNMENT_REPORT_CONTAINER = "evidence-approved"
TEST_RECORD_FILTER_SQL = """
not (
    coalesce("Learner_name", '') ilike '%%(test)%%'
    or coalesce("Programme_name", '') ilike '%%(test)%%'
    or coalesce("Learner_name", '') ~* '(^|[^a-z])test([^a-z]|$)'
)
"""


def _kbc_attendance_connection_string():
    connection_string = os.environ.get("KBCDATABASE", "")
    if not connection_string:
        return ""
    database_name = os.environ.get("KBC_ATTENDANCE_DATABASE", DEFAULT_KBC_ATTENDANCE_DATABASE)
    conninfo = conninfo_to_dict(connection_string)
    conninfo["dbname"] = database_name
    return make_conninfo(**conninfo)


def _assignment_connection_string():
    connection_string = (
        os.environ.get("ASSESSMENT_FETCH_DATABASE_URL", "")
        or os.environ.get("FETCHING_ATTENDENCE_DATABASE", "")
    )
    if not connection_string:
        return ""
    conninfo = conninfo_to_dict(connection_string)
    if not conninfo.get("dbname"):
        conninfo["dbname"] = os.environ.get("ASSESSMENT_FETCH_DATABASE", DEFAULT_ASSIGNMENT_DATABASE)
    return make_conninfo(**conninfo)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _has_audit_permission(request, write=False):
    if os.environ.get("AUDIT_API_REQUIRE_AUTH", "").lower() not in {"1", "true", "yes"}:
        return True
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    permission = "audit.export" if write else "audit.view"
    return user.has_perm(permission)


def _json_safe(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _rows_from_cursor(cur):
    columns = [col[0] for col in cur.description]
    return [
        {column: _json_safe(value) for column, value in zip(columns, row)}
        for row in cur.fetchall()
    ]


def _parse_json_value(value, path, warnings):
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            warnings.append(_warning("invalid_json", f"{path} contains invalid JSON text.", path))
            return None
    warnings.append(_warning("invalid_json_type", f"{path} is not a JSON object, array, or text value.", path))
    return None


def _warning(code, message, path="", severity="warning"):
    return {"code": code, "message": message, "path": path, "severity": severity}


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return str(value)


def _number(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, decimal.Decimal)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None else None


def _date(value):
    normalized = _text(value)
    if not normalized:
        return None
    for parser in (
        lambda v: datetime.datetime.fromisoformat(v.replace("Z", "+00:00")),
        lambda v: datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),
        lambda v: datetime.datetime.strptime(v, "%Y-%m-%d"),
        lambda v: datetime.datetime.strptime(v, "%d/%m/%Y"),
    ):
        try:
            return parser(normalized).date()
        except ValueError:
            continue
    return None


def _date_iso(value):
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _month_key(date_value):
    normalized = _text(date_value)
    if re.match(r"^\d{4}-\d{2}$", normalized):
        return normalized
    parsed = _date(date_value)
    return parsed.strftime("%Y-%m") if parsed else None


def _month_label(month_key):
    try:
        parsed = datetime.datetime.strptime(month_key, "%Y-%m")
    except ValueError:
        return "Undated / Needs Review"
    return parsed.strftime("%B %Y")


def _today():
    return datetime.date.today()


def _is_future_date(date_value, today=None):
    parsed = _date(date_value)
    return bool(parsed and parsed > (today or _today()))


def _week_key(date_value):
    parsed = _date(date_value)
    if not parsed:
        return None
    day_bucket = ((parsed.day - 1) // 7) * 7 + 1
    start = parsed.replace(day=day_bucket)
    end = min(start + datetime.timedelta(days=6), _last_day_of_month(parsed))
    return start.isoformat(), start.isoformat(), end.isoformat(), f"{start.day}-{end.day} {start.strftime('%b')}"


def _last_day_of_month(value):
    if value.month == 12:
        return value.replace(day=31)
    return value.replace(month=value.month + 1, day=1) - datetime.timedelta(days=1)


def _status_bucket(status):
    normalized = _text(status).lower().replace(" ", "")
    if "complete" in normalized or normalized in {"passed", "done", "present", "attended", "attend"}:
        return "completed"
    if "progress" in normalized or "started" in normalized or "visited" in normalized:
        return "in_progress"
    return "not_started"


def _relevant_date(source, row, warnings, path):
    completion_keys = (
        "completed_at",
        "completed_date",
        "completion_date",
        "Course Completed At",
        "Latest Quiz Submitted At",
        "submitted_at",
        "submission_date",
    )
    for key in completion_keys:
        value = row.get(key)
        if _date(value):
            return _date_iso(value), key
    for key in ("end_date", "End Date", "Course End Date"):
        value = row.get(key)
        if _date(value):
            return _date_iso(value), key
    for key in ("start_date", "Start Date", "Course Started At", "Registered At"):
        value = row.get(key)
        if _date(value):
            return _date_iso(value), key
    warnings.append(_warning("missing_relevant_date", f"{source} item has no reliable structured date.", path))
    return None, None


def _date_warnings(row, warnings, path):
    start = _date(row.get("start_date") or row.get("Course Started At"))
    end = _date(row.get("end_date") or row.get("Course Completed At"))
    if start and end and start > end:
        warnings.append(_warning("invalid_date_range", "Start date is later than end date.", path))


def _programme_key(row):
    return _text(row.get("Programme_name")) or "programme-not-available"


def _aptem_components(row, warnings):
    parsed = _parse_json_value(row.get("Aptem_components"), "Aptem_components", warnings)
    if parsed is None:
        warnings.append(_warning("no_aptem_data", "No Aptem components are available.", "Aptem_components", "info"))
        return []
    components = parsed.get("components") if isinstance(parsed, dict) else parsed
    if not isinstance(components, list):
        warnings.append(_warning("invalid_aptem_shape", "Aptem_components must contain a components array.", "Aptem_components"))
        return []
    if not components:
        warnings.append(_warning("no_aptem_data", "No Aptem components are available.", "Aptem_components", "info"))
    return components


def _lms_items(row, warnings):
    parsed = _parse_json_value(row.get("LMS_modules_details"), "LMS_modules_details", warnings)
    if parsed is None:
        if not _text(row.get("LMS_Summary")):
            warnings.append(_warning("no_lms_data", "No LMS module details are available.", "LMS_modules_details", "info"))
        return []
    items = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        warnings.append(_warning("invalid_lms_shape", "LMS_modules_details must contain an items array.", "LMS_modules_details"))
        return []
    if not items and not _text(row.get("LMS_Summary")):
        warnings.append(_warning("no_lms_data", "No LMS module details are available.", "LMS_modules_details", "info"))
    return items


def _normalize_aptem_item(component, index):
    warnings = []
    path = f"Aptem_components.components[{index}]"
    if not isinstance(component, dict):
        return None, [_warning("invalid_aptem_item", "Aptem component is not an object.", path)]
    _date_warnings(component, warnings, path)
    relevant_date, date_source = _relevant_date("Aptem", component, warnings, path)
    hours = _number(component.get("hours"))
    planned_hours = _number(component.get("planned_hours"))
    source_id = _text(component.get("id") or component.get("component_id") or component.get("source_id")) or f"aptem-{index}"
    return {
        "id": f"aptem:{source_id}",
        "source": "Aptem",
        "source_id": source_id,
        "activity_name": _text(component.get("name")) or "Unnamed Aptem activity",
        "type": _text(component.get("type")) or "Not available",
        "status": _text(component.get("status")) or "Not available",
        "actual_hours": hours,
        "planned_hours": planned_hours,
        "hours_variance": (hours - planned_hours) if hours is not None and planned_hours is not None else None,
        "start_date": _date_iso(component.get("start_date")),
        "end_date": _date_iso(component.get("end_date")),
        "relevant_date": relevant_date,
        "date_source": date_source,
        "match_status": "Needs Review",
        "match_reason": "No stable Aptem/LMS matching key was available in the source record.",
        "matched_source_ids": [],
        "warning_codes": [warning["code"] for warning in warnings],
        "warnings": warnings,
        "raw": component,
    }, warnings


def _normalize_lms_item(item, index):
    warnings = []
    path = f"LMS_modules_details.items[{index}]"
    if not isinstance(item, dict):
        return None, [_warning("invalid_lms_item", "LMS item is not an object.", path)]
    relevant_date, date_source = _relevant_date("LMS", item, warnings, path)
    tracked_seconds = _integer(item.get("Total Tracked Time Seconds") or item.get("Course Elapsed Seconds") or item.get("Material Time Seconds"))
    source_id = _text(item.get("Course ID") or item.get("row_number") or item.get("User ID")) or f"lms-{index}"
    return {
        "id": f"lms:{source_id}:{index}",
        "source": "LMS",
        "source_id": source_id,
        "course_module": _text(item.get("Module/Course") or item.get("Course")) or "Not available",
        "component_name": _text(item.get("Latest Quiz Title") or item.get("Completed Material Titles") or item.get("Module/Course") or item.get("Course")) or "Not available",
        "component_type": _text(item.get("from") or "LMS module"),
        "completion_status": _text(item.get("Course Status") or item.get("Latest Quiz Status")) or "Not available",
        "tracked_seconds": tracked_seconds,
        "quiz_attempts": _integer(item.get("Quiz Attempts Count") or item.get("Quizzes Attempted")),
        "quiz_score": _number(item.get("Latest Quiz Score (%)") or item.get("Average Best Quiz Score (%)") or item.get("Overall Quiz Accuracy (%)")),
        "tutor": _text(item.get("Tutor Name")),
        "course_started_at": _date_iso(item.get("Course Started At") or item.get("Registered At")),
        "course_completed_at": _date_iso(item.get("Course Completed At")),
        "relevant_date": relevant_date,
        "date_source": date_source,
        "match_status": "Needs Review",
        "match_reason": "No stable Aptem/LMS matching key was available in the source record.",
        "matched_source_ids": [],
        "warning_codes": [warning["code"] for warning in warnings],
        "warnings": warnings,
        "raw": item,
    }, warnings


def _attendance_status(value):
    number = _integer(value)
    if number == 1:
        return "Present"
    if number == 0:
        return "Absent"
    return _text(value) or "Not available"


def _normalize_attendance_item(row, index):
    session_date = _date_iso(row.get("date") or row.get("Date") or row.get("session_date"))
    attendance_value = row.get("Attendance") if "Attendance" in row else row.get("attendance")
    status = _attendance_status(attendance_value)
    recorded_activity_hours = _number(_value_from_keys(row, "activity", "Activity", "hours", "Hours"))
    actual_hours = recorded_activity_hours if recorded_activity_hours is not None else (LIVE_SESSION_HOURS if status == "Present" else 0)
    planned_hours = recorded_activity_hours if recorded_activity_hours is not None else LIVE_SESSION_HOURS
    source_id = _text(row.get("key") or row.get("Key") or row.get("id") or row.get("ID")) or f"attendance-{index}"
    module = _text(row.get("module") or row.get("Module"))
    warnings = []
    if not session_date:
        warnings.append(_warning("missing_attendance_date", "Attendance row has no reliable date.", "KBCDATABASE.public.kbc_attendance.date"))
    return {
        "id": f"attendance:{source_id}:{index}",
        "source": "Aptem",
        "source_id": source_id,
        "activity_name": module or "Attendance session",
        "type": "Attendance",
        "status": status,
        "actual_hours": actual_hours,
        "planned_hours": planned_hours,
        "hours_variance": _round_hours(actual_hours - planned_hours),
        "start_date": session_date,
        "end_date": session_date,
        "relevant_date": session_date,
        "date_source": "KBCDATABASE.public.kbc_attendance.date",
        "match_status": "Matched",
        "match_reason": "Matched from KBCDATABASE attendance by Aptem ID.",
        "matched_source_ids": [],
        "warning_codes": [warning["code"] for warning in warnings],
        "warnings": warnings,
        "raw": row,
    }


def _value_from_keys(row, *keys):
    if not isinstance(row, dict):
        return None
    lowered = {str(key).lower(): key for key in row.keys()}
    for key in keys:
        if key in row:
            return row.get(key)
        original = lowered.get(str(key).lower())
        if original is not None:
            return row.get(original)
    return None


def _assignment_source_rows(row):
    safe_row = _json_safe(dict(row))
    assignment_value = _value_from_keys(safe_row, "assignments", "Assignments", "assignment", "evidence")
    parsed = _parse_json_value(assignment_value, "fetching_attendence.public.assessment_fetch.assignments", []) if isinstance(assignment_value, str) else assignment_value
    if isinstance(parsed, list):
        return [_prepare_assignment_source_row(item) for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [_prepare_assignment_source_row(parsed)]
    return [_prepare_assignment_source_row(safe_row)]


def _assignment_blob_url(blob_name):
    text = _text(blob_name)
    if not text:
        return ""
    container = os.environ.get("ASSIGNMENT_REPORT_CONTAINER", DEFAULT_ASSIGNMENT_REPORT_CONTAINER)
    return f"/audit_api/blob/?container={quote(container)}&blob={quote(text, safe='')}"


def _prepare_assignment_source_row(row):
    safe_row = _json_safe(row)
    evidence = _value_from_keys(safe_row, "evidence", "Evidence")
    if isinstance(evidence, list):
        prepared_evidence = []
        for entry in evidence:
            if not isinstance(entry, dict):
                prepared_evidence.append(entry)
                continue
            prepared = dict(entry)
            file_blob = _value_from_keys(prepared, "file_blob", "FileBlob")
            if file_blob and not _value_from_keys(prepared, "file_blob_url"):
                prepared["file_blob_url"] = _assignment_blob_url(file_blob)
            note_blob = _value_from_keys(prepared, "note_blob", "NoteBlob")
            if note_blob and not _value_from_keys(prepared, "note_blob_url"):
                prepared["note_blob_url"] = _assignment_blob_url(note_blob)
            report_blob = _value_from_keys(prepared, "report_blob", "ReportBlob")
            if report_blob and not _value_from_keys(prepared, "assessment_report_blob_url"):
                prepared["assessment_report_blob_url"] = _assignment_blob_url(report_blob)
            prepared_evidence.append(prepared)
        safe_row["evidence"] = prepared_evidence
    return safe_row


def _assignment_item_rows_from_assignment_rows(rows):
    items = []
    index = 0
    for row in rows:
        for assignment_row in _assignment_source_rows(row):
            items.append(_normalize_assignment_item(assignment_row, index))
            index += 1
    return items


def _fetch_evidence_details_for_ids(learner_ids):
    ids = sorted({_text(learner_id) for learner_id in learner_ids if learner_id not in (None, "")})
    if not ids:
        return {}
    try:
        with connections[resolve("enrolment")].cursor() as cur:
            cur.execute(
                """
                select learner_id::text,
                       evidence_id,
                       source_file_url,
                       file_blob,
                       note_blob,
                       report_blob,
                       assessment_report_url,
                       feedbacks
                from fetching_evidence.evidence_items
                where learner_id::text = any(%s)
                """,
                [ids],
            )
            grouped = defaultdict(dict)
            for row in _rows_from_cursor(cur):
                if isinstance(row.get("feedbacks"), str):
                    row["feedbacks"] = _parse_json_value(row.get("feedbacks"), "fetching_evidence.evidence_items.feedbacks", [])
                evidence_id = _text(row.get("evidence_id"))
                learner_id = _text(row.get("learner_id"))
                if learner_id and evidence_id:
                    grouped[learner_id][evidence_id] = _json_safe(row)
            return grouped
    except Exception:
        return {}


def _enrich_assignment_items_with_evidence_details(learner_id, items, details_by_learner=None):
    details = (details_by_learner or _fetch_evidence_details_for_ids([learner_id])).get(_text(learner_id), {})
    if not details:
        return items
    for item in items:
        evidence = item.get("raw", {}).get("evidence")
        if not isinstance(evidence, list):
            continue
        enriched = []
        for entry in evidence:
            if not isinstance(entry, dict):
                enriched.append(entry)
                continue
            prepared = dict(entry)
            detail = details.get(_text(prepared.get("evidence_id")))
            if detail:
                for key in ("source_file_url", "file_blob", "note_blob", "report_blob", "assessment_report_url", "feedbacks"):
                    if detail.get(key) not in (None, "", []) and prepared.get(key) in (None, "", []):
                        prepared[key] = detail.get(key)
            enriched.append(prepared)
        item["raw"]["evidence"] = _prepare_assignment_source_row({"evidence": enriched})["evidence"]
    return items


def _normalize_assignment_item(row, index):
    raw = _value_from_keys(row, "raw") if isinstance(_value_from_keys(row, "raw"), dict) else row
    evidence = _value_from_keys(row, "evidence", "Evidence") or _value_from_keys(raw, "Evidence") or []
    assignment_value = _value_from_keys(row, "assignments", "Assignments", "assignment", "evidence") or evidence
    submitted_date = _date_iso(
        _value_from_keys(row, "due_date", "DueDate")
        or _value_from_keys(raw, "DueDate")
        or _value_from_keys(row, "last_submission_date", "LastSubmissionDate")
        or _value_from_keys(raw, "LastSubmissionDate")
        or _value_from_keys(row, "completed_date", "CompletedDate")
        or _value_from_keys(raw, "CompletedDate")
        or _value_from_keys(row, "submitted_at", "submission_date", "date", "created_at", "fetched_at")
    )
    source_id = _text(
        _value_from_keys(row, "component_id", "Id", "id", "assessment_id")
        or _value_from_keys(raw, "Id", "ComponentId")
        or _value_from_keys(row, "learner_id", "Learner_ID", "LearnerId")
    ) or f"assignment-{index}"
    title = _text(
        _value_from_keys(row, "component_name", "ComponentName", "assignment_title", "title", "assessment")
        or _value_from_keys(raw, "ComponentName")
        or _value_from_keys(row, "programme", "Program")
        or _value_from_keys(raw, "Program")
    ) or "Assignment evidence"
    status = _text(_value_from_keys(row, "status", "Status") or _value_from_keys(raw, "Status")) or ("Completed" if evidence else "Not started")
    actual_hours = _number(_value_from_keys(row, "actual_hours", "ActualHours") or _value_from_keys(raw, "ActualHours"))
    if actual_hours is None:
        minutes = _number(_value_from_keys(row, "otjh_minutes", "EvidencedMinutesTracked") or _value_from_keys(raw, "EvidencedMinutesTracked"))
        actual_hours = _round_hours(minutes / 60) if minutes is not None else 0
    planned_hours = _number(_value_from_keys(row, "planned_hours", "PlannedHours") or _value_from_keys(raw, "PlannedHours")) or 0
    return {
        "id": f"assignment:{source_id}:{index}",
        "source": "Aptem",
        "source_id": source_id,
        "activity_name": title,
        "type": _text(_value_from_keys(row, "component_type", "ComponentType") or _value_from_keys(raw, "ComponentType")) or "Assignment",
        "status": status,
        "actual_hours": actual_hours,
        "planned_hours": planned_hours,
        "hours_variance": _round_hours(actual_hours - planned_hours),
        "start_date": submitted_date,
        "end_date": submitted_date,
        "relevant_date": submitted_date,
        "date_source": "fetching_attendence.public.assessment_fetch.assignments[].due_date/completed/submission date",
        "match_status": "Matched" if assignment_value else "Needs Review",
        "match_reason": "Matched from assessment_fetch by learner_id.",
        "matched_source_ids": [],
        "warning_codes": [],
        "warnings": [],
        "raw": row,
    }


def _kbc_attendance_table(cur):
    table_name = "kbc_attendance"
    cur.execute(
        """
        select 1
        from information_schema.tables
        where table_schema = 'public' and table_name = %s
        limit 1
        """,
        (table_name,),
    )
    return table_name if cur.fetchone() else ""


def _kbc_attendance_columns(cur, table_name):
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = 'public' and table_name = %s
        """,
        (table_name,),
    )
    return {row["column_name"] for row in cur.fetchall()}


def _fetch_kbc_attendance_items(aptem_id):
    if aptem_id in (None, ""):
        return []
    connection_string = _kbc_attendance_connection_string()
    if not connection_string:
        return []
    try:
        with psycopg.connect(connection_string, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                table_name = _kbc_attendance_table(cur)
                if not table_name:
                    return []
                columns = _kbc_attendance_columns(cur, table_name)
                id_filters = []
                params = []
                if "ID" in columns:
                    id_filters.append('"ID"::text = %s')
                    params.append(str(aptem_id))
                if "aptem_id" in columns:
                    id_filters.append('aptem_id::text = %s')
                    params.append(str(aptem_id))
                if not id_filters:
                    return []
                cur.execute(
                    f"""
                    select *
                    from public.{table_name}
                    where {" or ".join(id_filters)}
                    order by {"date" if "date" in columns else '"ID"'} nulls last
                    """,
                    tuple(params),
                )
                return [
                    _normalize_attendance_item(_json_safe(dict(row)), index)
                    for index, row in enumerate(cur.fetchall())
                ]
    except Exception:
        return []


def _fetch_kbc_attendance_items_for_ids(aptem_ids):
    ids = sorted({str(aptem_id) for aptem_id in aptem_ids if aptem_id not in (None, "")})
    if not ids:
        return {}
    connection_string = _kbc_attendance_connection_string()
    if not connection_string:
        return {}
    try:
        with psycopg.connect(connection_string, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                table_name = _kbc_attendance_table(cur)
                if not table_name:
                    return {}
                columns = _kbc_attendance_columns(cur, table_name)
                id_expr = None
                if "ID" in columns:
                    id_expr = '"ID"::text'
                elif "aptem_id" in columns:
                    id_expr = "aptem_id::text"
                if not id_expr:
                    return {}
                cur.execute(
                    f"""
                    select *
                    from public.{table_name}
                    where {id_expr} = any(%s)
                    order by {id_expr}, {"date" if "date" in columns else id_expr} nulls last
                    """,
                    (ids,),
                )
                grouped = defaultdict(list)
                for index, row in enumerate(cur.fetchall()):
                    safe_row = _json_safe(dict(row))
                    learner_key = _text(safe_row.get("ID") or safe_row.get("aptem_id"))
                    if learner_key:
                        grouped[learner_key].append(_normalize_attendance_item(safe_row, index))
                return grouped
    except Exception:
        return {}


def _fetch_assignment_items(learner_id, include_evidence=True):
    """One learner's normalized assignment items. ``include_evidence=False``
    skips the evidence-detail enrichment query for callers that only need
    titles/dates/status/hours (e.g. the journal month auto-import)."""
    if learner_id in (None, ""):
        return []
    connection_string = _assignment_connection_string()
    if not connection_string:
        return _fetch_assignment_items_for_ids([learner_id], include_evidence=include_evidence).get(_text(learner_id), [])
    try:
        with psycopg.connect(connection_string, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = 'public' and table_name = 'assessment_fetch'
                    """
                )
                columns = {row["column_name"] for row in cur.fetchall()}
                if not columns:
                    return []
                learner_column = _first_column(columns, ("learner_id", "Learner_ID", "aptem_id", "ID"))
                assignment_column = _first_column(columns, ("assignments", "Assignments", "assignment", "evidence"))
                if not learner_column or not assignment_column:
                    return []
                date_column = _first_column(columns, ("submitted_at", "submission_date", "date", "created_at", "fetched_at"))
                order_sql = _quote_column(date_column) if date_column else _quote_column(learner_column)
                cur.execute(
                    f"""
                    select *
                    from public.assessment_fetch
                    where {_quote_column(learner_column)}::text = %s
                    order by {order_sql} nulls last
                    """,
                    [str(learner_id)],
                )
                items = _assignment_item_rows_from_assignment_rows(cur.fetchall())
                if not include_evidence:
                    return items
                return _enrich_assignment_items_with_evidence_details(learner_id, items)
    except Exception:
        return []


def _fetch_assignment_items_for_ids(learner_ids, include_evidence=True):
    ids = sorted({str(learner_id) for learner_id in learner_ids if learner_id not in (None, "")})
    if not ids:
        return {}
    connection_string = _assignment_connection_string()
    details_by_learner = _fetch_evidence_details_for_ids(ids) if include_evidence else {}
    if not connection_string:
        try:
            with connections[resolve("enrolment")].cursor() as cur:
                cur.execute(
                    """
                    select learner_id::text as learner_id, assignments
                    from fetching_evidence.assessment_fetch
                    where learner_id::text = any(%s)
                    order by learner_id::text, fetched_at nulls last
                    """,
                    [ids],
                )
                grouped = defaultdict(list)
                for row in _rows_from_cursor(cur):
                    learner_key = _text(row.get("learner_id"))
                    grouped[learner_key].extend(_assignment_item_rows_from_assignment_rows([row]))
                if include_evidence:
                    for learner_key, items in list(grouped.items()):
                        grouped[learner_key] = _enrich_assignment_items_with_evidence_details(learner_key, items, details_by_learner)
                return grouped
        except Exception:
            return {}
    try:
        with psycopg.connect(connection_string, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select column_name
                    from information_schema.columns
                    where table_schema = 'public' and table_name = 'assessment_fetch'
                    """
                )
                columns = {row["column_name"] for row in cur.fetchall()}
                learner_column = _first_column(columns, ("learner_id", "Learner_ID", "aptem_id", "ID"))
                assignment_column = _first_column(columns, ("assignments", "Assignments", "assignment", "evidence"))
                if not learner_column or not assignment_column:
                    return {}
                date_column = _first_column(columns, ("submitted_at", "submission_date", "date", "created_at", "fetched_at"))
                order_sql = _quote_column(date_column) if date_column else _quote_column(learner_column)
                cur.execute(
                    f"""
                    select *
                    from public.assessment_fetch
                    where {_quote_column(learner_column)}::text = any(%s)
                    order by {_quote_column(learner_column)}::text, {order_sql} nulls last
                    """,
                    [ids],
                )
                grouped = defaultdict(list)
                for row in cur.fetchall():
                    safe_row = _json_safe(dict(row))
                    learner_key = _text(safe_row.get(learner_column))
                    if learner_key:
                        grouped[learner_key].extend(_assignment_item_rows_from_assignment_rows([safe_row]))
                if include_evidence:
                    for learner_key, items in list(grouped.items()):
                        grouped[learner_key] = _enrich_assignment_items_with_evidence_details(learner_key, items, details_by_learner)
                return grouped
    except Exception:
        return {}


def _parse_monthly_hours_map(value):
    parsed = _parse_json_value(value, "fetching_evidence.learner_hours_monthly.monthly_hours", [])
    result = {}
    if isinstance(parsed, dict):
        for key, raw in parsed.items():
            month_key = _month_key(key)
            hours = _number(raw)
            if month_key and hours is not None:
                result[month_key] = _round_hours(hours)
    elif isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            month_key = _month_key(entry.get("month") or entry.get("month_key") or entry.get("date"))
            hours = _number(entry.get("hours") or entry.get("value") or entry.get("planned") or entry.get("completed"))
            if month_key and hours is not None:
                result[month_key] = _round_hours(hours)
    return result


def _fetch_monthly_hours_for_ids(learner_ids):
    ids = sorted({str(learner_id) for learner_id in learner_ids if learner_id not in (None, "")})
    if not ids:
        return {}
    try:
        with connections[resolve("enrolment")].cursor() as cur:
            cur.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = %s and table_name = %s
                """,
                [MONTHLY_HOURS_SCHEMA, MONTHLY_HOURS_TABLE],
            )
            columns = {row["column_name"] for row in _rows_from_cursor(cur)}
            learner_column = _first_column(columns, ("learner_id", "Learner_ID", "aptem_id", "ID"))
            if not learner_column or "planned_hours_monthly" not in columns or "completed_hours_monthly" not in columns:
                return {}
            cur.execute(
                f"""
                select {_quote_column(learner_column)}::text as learner_id,
                       planned_hours_monthly,
                       completed_hours_monthly
                from {MONTHLY_HOURS_SCHEMA}.{MONTHLY_HOURS_TABLE}
                where {_quote_column(learner_column)}::text = any(%s)
                """,
                [ids],
            )
            grouped = {}
            for row in _rows_from_cursor(cur):
                learner_key = _text(row.get("learner_id"))
                if not learner_key:
                    continue
                grouped[learner_key] = {
                    "planned": _parse_monthly_hours_map(row.get("planned_hours_monthly")),
                    "completed": _parse_monthly_hours_map(row.get("completed_hours_monthly")),
                }
            return grouped
    except (KeyError, DatabaseError):
        return {}


def _fetch_monthly_hours(learner_id):
    return _fetch_monthly_hours_for_ids([learner_id]).get(str(learner_id), {"planned": {}, "completed": {}})


def _parse_lms_summary(text):
    progress = None
    tracked_seconds = None
    if not text:
        return {"lms_progress": None, "tracked_seconds": None}
    progress_match = re.search(r"Progress\s*\(%\)\D+(\d+(?:\.\d+)?)", text)
    if progress_match:
        progress = float(progress_match.group(1))
    time_match = re.search(r"Time:\s*(\d+):(\d+):(\d+)", text)
    if time_match:
        hours, minutes, seconds = [int(part) for part in time_match.groups()]
        tracked_seconds = hours * 3600 + minutes * 60 + seconds
    return {"lms_progress": progress, "tracked_seconds": tracked_seconds}


def _parse_quiz_summary(text):
    if not text:
        return {"quiz_attempts": None}
    attempts = [int(value) for value in re.findall(r"Attempts:\s*(\d+)", text)]
    return {"quiz_attempts": sum(attempts) if attempts else None}


def _programme_structure(row):
    return _parse_json_value(row.get("programme_structure"), "learner_match.programme_structure", [])


def _week_date_from_title(value):
    text = _text(value)
    patterns = (
        r"(\d{4}-\d{1,2}-\d{1,2})",
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
    )
    match = re.search(patterns[0], text)
    if match:
        return _date_iso(match.group(1))
    match = re.search(patterns[1], text)
    if not match:
        return None
    day, month, year = match.groups()
    year = f"20{year}" if len(year) == 2 else year
    return _date_iso(f"{year}-{int(month):02d}-{int(day):02d}")


def _programme_week_date(month, week):
    return (
        _date_iso(week.get("date"))
        or _week_date_from_title(week.get("matchedBy"))
        or _week_date_from_title(week.get("week"))
        or _date_iso(month.get("date"))
    )


def _normalize_programme_component(component, week, month, component_index):
    relevant_date = _programme_week_date(month, week)
    kind = _text(component.get("kind") or component.get("materialType") or component.get("postType")) or "LMS component"
    title = _text(component.get("title")) or _text(week.get("week")) or "LMS activity"
    completed = bool(component.get("completed") or component.get("passed") or component.get("attempted"))
    status = "Completed" if completed else "Not started"
    source_id = _text(component.get("componentId") or component.get("id") or component_index)
    score = _number(component.get("bestScorePercent") or component.get("score") or component.get("scorePercent"))
    return {
        "id": f"lms-programme:{week.get('sectionId') or week.get('week') or 'week'}:{source_id}:{component_index}",
        "source": "LMS",
        "source_id": source_id,
        "course_module": _text(week.get("course")) or _text(month.get("month")) or "LMS",
        "component_name": title,
        "component_type": kind,
        "completion_status": status,
        "tracked_seconds": None,
        "quiz_attempts": 1 if component.get("attempted") else 0 if kind.lower() == "quiz" else None,
        "quiz_score": score,
        "tutor": "",
        "course_started_at": None,
        "course_completed_at": relevant_date if completed else None,
        "relevant_date": relevant_date,
        "date_source": "Audit.learner_match.programme_structure.months[].weeks[]",
        "match_status": "Matched",
        "match_reason": _text(week.get("matchedBy")) or "Matched from learner_match programme structure.",
        "matched_source_ids": [],
        "warning_codes": [],
        "warnings": [],
        "raw": component,
    }


def _programme_week_shell(month, week, index):
    week_date = _programme_week_date(month, week)
    week_parts = _week_key(week_date) if week_date else None
    if week_parts:
        week_key, start, end, label = week_parts
    else:
        month_key = _month_key(month.get("date")) or "undated"
        week_key = f"{month_key}:programme-week-{index + 1}"
        start = _date_iso(month.get("date"))
        end = start
        label = _text(week.get("week")) or f"Week {index + 1}"
    return {
        "week_key": week_key,
        "label": _text(week.get("week")) or label,
        "start_date": start,
        "end_date": end,
        "aptem_items": [],
        "lms_items": [],
        "source_column": "Audit.learner_match.programme_structure",
        "source_note": _text(week.get("matchedBy")),
        "source_modules": [_text(week.get("course"))] if _text(week.get("course")) else [],
    }


def _item_in_week(item, week):
    item_date = _date(item.get("relevant_date"))
    start = _date(week.get("start_date"))
    end = _date(week.get("end_date"))
    return bool(item_date and start and end and start <= item_date <= end)


def _append_week_items(week, attendance_items, assignment_items):
    used_attendance = []
    used_assignments = []
    for item in attendance_items:
        if _item_in_week(item, week):
            week["aptem_items"].append(item)
            used_attendance.append(item.get("id"))
    for item in assignment_items:
        if _item_in_week(item, week):
            week["aptem_items"].append(item)
            used_assignments.append(item.get("id"))
    return set(used_attendance), set(used_assignments)


def _append_item_date_weeks(month, month_key, candidate_items, today):
    by_week = {week["week_key"]: week for week in month["weeks"]}
    used_ids = set()
    for item in sorted(candidate_items, key=lambda entry: (entry.get("relevant_date") or "", entry.get("id") or "")):
        if _month_key(item.get("relevant_date")) != month_key or _is_future_date(item.get("relevant_date"), today):
            continue
        week_parts = _week_key(item.get("relevant_date"))
        if not week_parts:
            continue
        week_key, start, end, label = week_parts
        week = by_week.get(week_key)
        if week is None:
            week = {
                "week_key": week_key,
                "label": label,
                "start_date": start,
                "end_date": end,
                "aptem_items": [],
                "lms_items": [],
                "source_column": "KBCDATABASE.public.kbc_attendance.date",
                "source_note": "Week created from dated activity rows for display.",
                "source_modules": [],
            }
            by_week[week_key] = week
            month["weeks"].append(week)
        if item.get("source") == "LMS":
            week["lms_items"].append(item)
        else:
            week["aptem_items"].append(item)
        used_ids.add(item.get("id"))
    month["weeks"] = sorted(month["weeks"], key=lambda week: week["week_key"])
    return used_ids


def _month_summary_from_weeks(month, monthly_hours=None):
    items = [item for week in month["weeks"] for item in week["aptem_items"] + week["lms_items"]] + month["undated_items"]
    aptem_items = [item for item in items if item.get("source") == "Aptem"]
    lms_items = [item for item in items if item.get("source") == "LMS"]
    statuses = [_status_bucket(item.get("status") or item.get("completion_status")) for item in items]
    month_key = month.get("month_key")
    planned_from_table = (monthly_hours or {}).get("planned", {}).get(month_key)
    completed_from_table = (monthly_hours or {}).get("completed", {}).get(month_key)
    month["summary"] = {
        "actual_hours": completed_from_table,
        "planned_hours": planned_from_table,
        "aptem_items": len(aptem_items),
        "lms_items": len(lms_items),
        "completed": statuses.count("completed"),
        "in_progress": statuses.count("in_progress"),
        "not_started": statuses.count("not_started"),
        "warnings": sum(len(item.get("warnings") or []) for item in items),
    }
    return month


def _group_programme_structure_months(programme_structure, attendance_items=None, assignment_items=None, monthly_hours=None, today=None):
    if not isinstance(programme_structure, dict):
        return []
    today = today or _today()
    attendance_items = attendance_items or []
    assignment_items = assignment_items or []
    used_attendance_ids = set()
    used_assignment_ids = set()
    months = []
    for month_index, source_month in enumerate(programme_structure.get("months") or []):
        if not isinstance(source_month, dict):
            continue
        month_key = _month_key(source_month.get("date")) or _month_key(_programme_week_date(source_month, {})) or "undated"
        if _is_future_date(source_month.get("date"), today):
            continue
        month = {
            "month_key": month_key,
            "label": _text(source_month.get("month")) or _month_label(month_key),
            "summary": {},
            "weeks": [],
            "undated_items": [],
            "signoffs": {"learner": None, "coach": None},
        }
        for week_index, source_week in enumerate(source_month.get("weeks") or []):
            if not isinstance(source_week, dict):
                continue
            week = _programme_week_shell(source_month, source_week, week_index)
            if _is_future_date(week.get("start_date"), today):
                continue
            for component_index, component in enumerate(source_week.get("components") or []):
                if isinstance(component, dict):
                    week["lms_items"].append(_normalize_programme_component(component, source_week, source_month, component_index))
            attendance_ids, assignment_ids = _append_week_items(week, attendance_items, assignment_items)
            used_attendance_ids.update(attendance_ids)
            used_assignment_ids.update(assignment_ids)
            month["weeks"].append(week)
        dated_month_items = [
            item
            for item in attendance_items + assignment_items
            if item.get("id") not in used_attendance_ids
            and item.get("id") not in used_assignment_ids
            and month_key != "undated"
            and _month_key(item.get("relevant_date")) == month_key
            and item.get("relevant_date")
        ]
        dated_item_ids = _append_item_date_weeks(month, month_key, dated_month_items, today)
        for item_id in dated_item_ids:
            if _text(item_id).startswith("attendance:"):
                used_attendance_ids.add(item_id)
            else:
                used_assignment_ids.add(item_id)
        for item in attendance_items + assignment_items:
            if item.get("id") in used_attendance_ids or item.get("id") in used_assignment_ids:
                continue
            if month_key != "undated" and _month_key(item.get("relevant_date")) == month_key:
                month["undated_items"].append(item)
                if item.get("id", "").startswith("attendance:"):
                    used_attendance_ids.add(item.get("id"))
                else:
                    used_assignment_ids.add(item.get("id"))
        months.append(_month_summary_from_weeks(month, monthly_hours))
    remaining_items = [
        item
        for item in attendance_items + assignment_items
        if item.get("id") not in used_attendance_ids and item.get("id") not in used_assignment_ids
    ]
    if remaining_items:
        months.append(_month_summary_from_weeks({
            "month_key": "undated",
            "label": "Undated / Needs Review",
            "summary": {},
            "weeks": [],
            "undated_items": remaining_items,
            "signoffs": {"learner": None, "coach": None},
        }, monthly_hours))
    if not months:
        return _group_months(attendance_items + assignment_items, [], monthly_hours=monthly_hours)
    return sorted(months, key=lambda month: month["month_key"], reverse=True)


def _build_audit_payload(row, monthly_hours=None):
    warnings = []
    programme_structure = _programme_structure(row)
    aptem_components = []
    lms_source_items = []
    aptem_items = _fetch_kbc_attendance_items(row.get("Learner_ID"))
    assignment_items = _fetch_assignment_items(row.get("Learner_ID"))
    monthly_hours = monthly_hours if monthly_hours is not None else _fetch_monthly_hours(row.get("Learner_ID"))
    programme_aptem_items = []
    lms_items = []

    for index, component in enumerate(aptem_components):
        item, item_warnings = _normalize_aptem_item(component, index)
        warnings.extend(item_warnings)
        if item:
            programme_aptem_items.append(item)
            aptem_items.append(item)

    for index, source_item in enumerate(lms_source_items):
        item, item_warnings = _normalize_lms_item(source_item, index)
        warnings.extend(item_warnings)
        if item:
            lms_items.append(item)

    if not isinstance(programme_structure, dict) or not isinstance(programme_structure.get("months"), list):
        warnings.append(_warning("missing_programme_structure", "Audit.learner_match.programme_structure is required for this report.", "Audit.learner_match.programme_structure", "error"))
        months = []
    else:
        months = _group_programme_structure_months(programme_structure, aptem_items, assignment_items, monthly_hours)
    lms_items = [
        item
        for month in months
        for week in month["weeks"]
        for item in week["lms_items"]
    ]
    aptem_items = [
        item
        for month in months
        for week in month["weeks"]
        for item in week["aptem_items"]
    ] + [
        item
        for month in months
        for item in month["undated_items"]
        if item.get("source") == "Aptem"
    ]
    signoffs = _empty_signoffs_by_month(months)
    lms_summary = _summary_from_lms_items(lms_items) or {}
    quiz_summary = _summary_from_quiz_items(lms_items) or {}
    components_completed = sum(1 for item in programme_aptem_items if _status_bucket(item["status"]) == "completed")
    total_planned_hours = _round_hours(sum(monthly_hours.get("planned", {}).values())) if monthly_hours.get("planned") else None
    completed_otjh = _round_hours(sum(monthly_hours.get("completed", {}).values())) if monthly_hours.get("completed") else None
    if not monthly_hours.get("planned"):
        warnings.append(_warning(
            "missing_monthly_planned_hours",
            "Monthly planned hours were not found in fetching_evidence.learner_hours_monthly.planned_hours_monthly for this learner.",
            "fetching_evidence.learner_hours_monthly.planned_hours_monthly",
            "error",
        ))
    if not monthly_hours.get("completed"):
        warnings.append(_warning(
            "missing_monthly_completed_hours",
            "Monthly completed hours were not found in fetching_evidence.learner_hours_monthly.completed_hours_monthly for this learner.",
            "fetching_evidence.learner_hours_monthly.completed_hours_monthly",
            "error",
        ))

    payload = {
        "learnerId": str(row.get("Learner_ID") or ""),
        "learner": {
            "id": row.get("Learner_ID"),
            "name": row.get("Learner_name"),
            "programme_name": row.get("Programme_name"),
            "programme_key": _programme_key(row),
            "programme_start_date": None,
            "employer": None,
            "epa": None,
            "epao": None,
            "company_logo_url": os.environ.get("AUDIT_COMPANY_LOGO_URL") or None,
        },
        "summary": {
            "completed_otjh": completed_otjh,
            "approved_hours": None,
            "planned_hours_month": None,
            "planned_hours_to_date": None,
            "total_programme_planned_hours": total_planned_hours,
            "ksb_progression": None,
            "lms_progress": lms_summary.get("lms_progress"),
            "tracked_seconds": lms_summary.get("tracked_seconds"),
            "components_completed": components_completed,
            "components_total": len(programme_aptem_items) if aptem_components else None,
            "quiz_attempts": quiz_summary.get("quiz_attempts"),
        },
        "months": months,
        "signoffs": signoffs,
        "warnings": _dedupe_warnings(warnings),
        "field_sources": _field_sources(),
        "source_status": {
            "has_aptem_data": bool(programme_aptem_items),
            "has_attendance_data": len(aptem_items) > len(programme_aptem_items),
            "has_assignment_data": bool(assignment_items),
            "has_lms_data": bool(lms_items),
            "has_monthly_planned_hours": bool(monthly_hours.get("planned")),
            "has_monthly_completed_hours": bool(monthly_hours.get("completed")),
            "lms_summary_fallback": False,
            "quiz_summary_fallback": False,
        },
        "audit_version": AUDIT_VERSION,
    }
    _apply_monthly_planned_totals(payload, monthly_hours)
    payload["snapshot_hash"] = _snapshot_hash(payload)
    return payload


def _activity_category(item):
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    if item.get("source") == "Aptem" and item.get("type") == "Attendance" and ("Attendance" in raw or "attendance" in raw):
        return "live_session"
    text = (
        f"{item.get('type')} {item.get('activity_name')}"
        if item.get("source") == "Aptem"
        else f"{item.get('component_type')} {item.get('component_name')} {item.get('course_module')}"
    )
    normalized = text.lower().replace("-", "_").replace(" ", "_")
    if "quiz" in normalized or "reading" in normalized or "material" in normalized:
        return "quiz_reading"
    if "video" in normalized or "recording" in normalized:
        return "video"
    if "assignment" in normalized or "evidence" in normalized or "portfolio" in normalized:
        return "assignment"
    if "self_study" in normalized or "podcast" in normalized or "powerpoint" in normalized:
        return "self_study"
    if "assessment" in normalized or "reflection" in normalized:
        return "assessment"
    return "other"


def _compact_activity(item):
    source = item.get("source")
    if source == "Aptem":
        planned_hours = _number(item.get("planned_hours")) or 0
        actual_hours = _number(item.get("actual_hours")) or 0
        status = _text(item.get("status"))
        title = _text(item.get("activity_name") or item.get("type")) or "Programme activity"
        if _activity_category(item) == "live_session":
            attendance_value = ""
            raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
            if "Attendance" in raw:
                attendance_value = _text(raw.get("Attendance"))
            elif "attendance" in raw:
                attendance_value = _text(raw.get("attendance"))
            subtitle_parts = [_text(item.get("relevant_date")), f"Attendance {attendance_value}" if attendance_value else "", status]
            subtitle = " - ".join(part for part in subtitle_parts if part)
        else:
            subtitle = _text(item.get("type") or item.get("match_status"))
    else:
        planned_hours = 0
        actual_hours = (_integer(item.get("tracked_seconds")) or 0) / 3600
        status = _text(item.get("completion_status"))
        title = _text(item.get("component_name") or item.get("course_module")) or "Online learning"
        subtitle = _text(item.get("course_module") or item.get("component_type") or item.get("match_status"))
    return {
        "id": item.get("id"),
        "source": source,
        "sourceId": item.get("source_id"),
        "title": title,
        "subtitle": subtitle,
        "category": _activity_category(item),
        "relevantDate": item.get("relevant_date"),
        "plannedHours": _round_hours(planned_hours),
        "actualHours": _round_hours(actual_hours),
        "done": _status_bucket(status) == "completed",
    }


def _activity_sources_for_category(category):
    if category in {"quiz_reading", "quiz", "reading", "video", "self_study"}:
        return {"LMS"}
    if category == "live_session":
        return {"Aptem"}
    return {"Aptem", "LMS"}


def _activity_matches_category(activity, category_filter):
    return (
        not category_filter
        or activity["category"] == category_filter
        or (category_filter == "quiz_reading" and activity["category"] in {"quiz", "reading"})
    )


def _sort_activity_summaries(activities):
    return sorted(
        activities,
        key=lambda item: (
            item.get("relevantDate") or "9999-12-31",
            _text(item.get("title")).lower(),
            _text(item.get("subtitle")).lower(),
            _text(item.get("id")),
        ),
    )


def _learner_activity_summaries(row, category_filter="", attendance_items=None, assignment_items=None):
    warnings = []
    activities = []
    programme_structure = _programme_structure(row)
    if isinstance(programme_structure, dict):
        if assignment_items is None:
            assignment_items = _fetch_assignment_items(row.get("Learner_ID")) if category_filter in {"", "assignment", "assessment", "other"} else []
        months = _group_programme_structure_months(programme_structure, attendance_items or [], assignment_items)
        for month in months:
            for week in month["weeks"]:
                for item in week["aptem_items"] + week["lms_items"]:
                    if not _is_future_date(item.get("relevant_date")):
                        compact = _compact_activity(item)
                        if _activity_matches_category(compact, category_filter):
                            activities.append(compact)
            for item in month["undated_items"]:
                if not _is_future_date(item.get("relevant_date")):
                    compact = _compact_activity(item)
                    if _activity_matches_category(compact, category_filter):
                        activities.append(compact)
        return _sort_activity_summaries(activities)
    sources = _activity_sources_for_category(category_filter)
    if "Aptem" in sources:
        for item in attendance_items or []:
            if item and not _is_future_date(item.get("relevant_date")):
                compact = _compact_activity(item)
                if _activity_matches_category(compact, category_filter):
                    activities.append(compact)
        for index, component in enumerate(_aptem_components(row, warnings)):
            item, item_warnings = _normalize_aptem_item(component, index)
            warnings.extend(item_warnings)
            if item and not _is_future_date(item.get("relevant_date")):
                compact = _compact_activity(item)
                if _activity_matches_category(compact, category_filter):
                    activities.append(compact)
    if "LMS" in sources:
        for index, source_item in enumerate(_lms_items(row, warnings)):
            item, item_warnings = _normalize_lms_item(source_item, index)
            warnings.extend(item_warnings)
            if item and not _is_future_date(item.get("relevant_date")):
                compact = _compact_activity(item)
                if _activity_matches_category(compact, category_filter):
                    activities.append(compact)
    return _sort_activity_summaries(activities)


def _activity_stats_from_results(results, learner_count):
    stats = _empty_activity_stats()
    for learner in results:
        for activity in learner.get("activities") or []:
            _add_activity_stat(stats, activity)
    total_activities = sum(bucket["activities"] for bucket in stats.values())
    total_actual_hours = _round_hours(sum(bucket["actualHours"] for bucket in stats.values()))
    total_planned_hours = _round_hours(sum(bucket["plannedHours"] for bucket in stats.values()))
    total_done = sum(bucket["done"] for bucket in stats.values())
    return {
        "learners": learner_count,
        "activities": total_activities,
        "actualHours": total_actual_hours,
        "plannedHours": total_planned_hours,
        "done": total_done,
        "categories": stats,
    }


def _empty_activity_stats():
    return {
        "live_session": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "quiz_reading": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "video": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "assignment": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "self_study": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "assessment": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
        "other": {"activities": 0, "actualHours": 0, "plannedHours": 0, "done": 0},
    }


def _add_activity_stat(stats, activity):
    category = activity.get("category") or "other"
    if category in {"quiz", "reading"}:
        category = "quiz_reading"
    if category not in stats:
        category = "other"
    bucket = stats[category]
    bucket["activities"] += 1
    bucket["actualHours"] = _round_hours(bucket["actualHours"] + (activity.get("actualHours") or 0))
    bucket["plannedHours"] = _round_hours(bucket["plannedHours"] + (activity.get("plannedHours") or 0))
    if activity.get("done"):
        bucket["done"] += 1


def _raw_activity_stats(row):
    warnings = []
    activities = []
    aptem_components = _parse_json_value(row.get("Aptem_components"), "Aptem_components", warnings)
    aptem_items = aptem_components.get("components") if isinstance(aptem_components, dict) else aptem_components
    if isinstance(aptem_items, list):
        for index, component in enumerate(aptem_items):
            if not isinstance(component, dict):
                continue
            item = {
                "id": f"aptem:{_text(component.get('id') or component.get('component_id') or index)}",
                "source": "Aptem",
                "source_id": _text(component.get("id") or component.get("component_id") or index),
                "activity_name": _text(component.get("name")) or "Programme activity",
                "type": _text(component.get("type")),
                "status": _text(component.get("status")),
                "actual_hours": _number(component.get("hours")) or 0,
                "planned_hours": _number(component.get("planned_hours")) or 0,
                "relevant_date": _date_iso(component.get("end_date") or component.get("start_date")),
            }
            if not _is_future_date(item["relevant_date"]):
                activities.append(_compact_activity(item))

    lms_modules = _parse_json_value(row.get("LMS_modules_details"), "LMS_modules_details", warnings)
    lms_items = lms_modules.get("items") if isinstance(lms_modules, dict) else lms_modules
    if isinstance(lms_items, list):
        for index, source_item in enumerate(lms_items):
            if not isinstance(source_item, dict):
                continue
            item = {
                "id": f"lms:{_text(source_item.get('Course ID') or source_item.get('row_number') or index)}:{index}",
                "source": "LMS",
                "source_id": _text(source_item.get("Course ID") or source_item.get("row_number") or index),
                "course_module": _text(source_item.get("Module/Course") or source_item.get("Course")),
                "component_name": _text(source_item.get("Latest Quiz Title") or source_item.get("Completed Material Titles") or source_item.get("Module/Course") or source_item.get("Course")),
                "component_type": _text(source_item.get("from") or "LMS module"),
                "completion_status": _text(source_item.get("Course Status") or source_item.get("Latest Quiz Status")),
                "tracked_seconds": _integer(source_item.get("Total Tracked Time Seconds") or source_item.get("Course Elapsed Seconds") or source_item.get("Material Time Seconds")) or 0,
                "relevant_date": _date_iso(
                    source_item.get("Course Completed At")
                    or source_item.get("Latest Quiz Submitted At")
                    or source_item.get("Course Started At")
                    or source_item.get("Registered At")
                ),
            }
            if not _is_future_date(item["relevant_date"]):
                activities.append(_compact_activity(item))
    return activities


def learner_activity_stats(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    search = (request.GET.get("search") or "").strip()
    include_test = (request.GET.get("includeTest") or request.GET.get("include_test") or "").strip().lower() in {"1", "true", "yes"}
    stats = _empty_activity_stats()

    try:
        with connections[resolve("enrolment")].cursor() as cur:
            _require_learner_match(cur)
            source_sql = f'from "{AUDIT_SCHEMA}".learner_match'
            learner_id_sql = "aptem_id::text"
            name_column = "learner_name"
            programme_column = "programme_structure::jsonb ->> 'programme'"
            test_filter_sql = _learner_match_test_filter_sql()
            where_parts = []
            query_params = []
            if search:
                pattern = f"%{search}%"
                where_parts.append(
                    f"""
                    {learner_id_sql} ilike %s
                    or coalesce({name_column}, '') ilike %s
                    or coalesce({programme_column}, '') ilike %s
                    """
                )
                query_params.extend([pattern, pattern, pattern])
            if not include_test:
                where_parts.append(test_filter_sql)
            where_sql = f"where {' and '.join(f'({part})' for part in where_parts)}" if where_parts else ""
            select_sql = _learner_match_select_sql()
            cur.execute(
                f"""
                select count(*)
                {source_sql}
                {where_sql}
                """,
                query_params,
            )
            learner_count = cur.fetchone()[0]
            cur.execute(
                f"""
                select {select_sql}
                {source_sql}
                {where_sql}
                """,
                query_params,
            )
            rows = _rows_from_cursor(cur)
    except KeyError:
        return _error("The enrolment database connection is not configured.", 500)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    learner_ids = [row.get("Learner_ID") for row in rows]
    attendance_by_learner = _fetch_kbc_attendance_items_for_ids(learner_ids)
    assignments_by_learner = _fetch_assignment_items_for_ids(learner_ids)
    for row in rows:
        learner_key = str(row.get("Learner_ID") or "")
        programme_structure = _programme_structure(row)
        if isinstance(programme_structure, dict):
            months = _group_programme_structure_months(
                programme_structure,
                attendance_by_learner.get(learner_key, []),
                assignments_by_learner.get(learner_key, []),
            )
            for month in months:
                for week in month["weeks"]:
                    for item in week["aptem_items"] + week["lms_items"]:
                        if not _is_future_date(item.get("relevant_date")):
                            _add_activity_stat(stats, _compact_activity(item))
                for item in month["undated_items"]:
                    if not _is_future_date(item.get("relevant_date")):
                        _add_activity_stat(stats, _compact_activity(item))
            continue
        for attendance_item in attendance_by_learner.get(learner_key, []):
            if not _is_future_date(attendance_item.get("relevant_date")):
                _add_activity_stat(stats, _compact_activity(attendance_item))
        for assignment_item in assignments_by_learner.get(learner_key, []):
            if not _is_future_date(assignment_item.get("relevant_date")):
                _add_activity_stat(stats, _compact_activity(assignment_item))
        for activity in _raw_activity_stats(row):
            _add_activity_stat(stats, activity)

    total_activities = sum(bucket["activities"] for bucket in stats.values())
    total_actual_hours = _round_hours(sum(bucket["actualHours"] for bucket in stats.values()))
    total_planned_hours = _round_hours(sum(bucket["plannedHours"] for bucket in stats.values()))
    total_done = sum(bucket["done"] for bucket in stats.values())
    payload = {
        "learners": learner_count,
        "activities": total_activities,
        "actualHours": total_actual_hours,
        "plannedHours": total_planned_hours,
        "done": total_done,
        "categories": stats,
    }
    return JsonResponse(payload)


def _build_student_source_data(row):
    warnings = []
    aptem_components = _parse_json_value(row.get("Aptem_components"), "Aptem_components", warnings)
    lms_modules_details = _parse_json_value(row.get("LMS_modules_details"), "LMS_modules_details", warnings)
    aptem_items = aptem_components.get("components") if isinstance(aptem_components, dict) else aptem_components
    lms_items = lms_modules_details.get("items") if isinstance(lms_modules_details, dict) else lms_modules_details
    if not isinstance(aptem_items, list):
        aptem_items = []
    if not isinstance(lms_items, list):
        lms_items = []

    return {
        "schema_version": "learner-source-data-v6",
        "programme": _programme_tree(row, aptem_items, lms_items),
        "aptem": {
            "months_source": "Aptem_components grouped by component end_date/start_date",
        },
        "lms": {
            "weeks_source": "LMS_modules_details grouped by Course Completed At/Course Started At",
            "modules_source": "LMS_modules_details.items[].Module/Course",
            "components_or_lecture_summaries_source": "LMS_modules_details.items[].Completed Material Titles split by |, plus Latest Quiz Title",
            "summary": row.get("LMS_Summary"),
            "quiz_summary": row.get("Quiz_summary"),
        },
        "warnings": _dedupe_warnings(warnings),
        "source_columns": {
            "programme": ["Programme_name", "Aptem_components"],
            "months": ["Aptem_components"],
            "weeks": ["LMS_modules_details"],
            "modules": ["LMS_modules_details"],
            "components_or_lecture_summaries": ["LMS_modules_details"],
        },
    }


def _programme_tree(row, aptem_items, lms_items):
    programme = _aptem_programme(row, aptem_items)
    programme["learner"] = {
        "id": row.get("Learner_ID"),
        "name": row.get("Learner_name"),
        "completed_otjh": row.get("Completed_OTJH"),
    }
    programme["modules"] = _programme_modules(aptem_items, lms_items)
    return programme


def _aptem_programme(row, aptem_items):
    dates = [_date(item.get("start_date")) for item in aptem_items if isinstance(item, dict) and _date(item.get("start_date"))]
    end_dates = [_date(item.get("end_date")) for item in aptem_items if isinstance(item, dict) and _date(item.get("end_date"))]
    return {
        "name": row.get("Programme_name"),
        "source": "Aptem",
        "start_date": min(dates).isoformat() if dates else None,
        "end_date": max(end_dates).isoformat() if end_dates else None,
        "components_count": len(aptem_items),
    }


def _programme_modules(aptem_items, lms_items):
    aptem_months = _aptem_month_map(aptem_items)
    modules = {}
    for item in lms_items:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("Module/Course") or item.get("Course")) or "Not available"
        module = modules.setdefault(name, {
            "name": name,
            "source": "LMS",
            "course_id": item.get("Course ID"),
            "category": item.get("Course Category"),
            "status": item.get("Course Status") or item.get("Latest Quiz Status"),
            "progress_percent": _number(item.get("Progress (%)")),
            "components_total": _integer(item.get("Components Total")),
            "components_completed": _integer(item.get("Components Completed")),
            "months": {},
        })
        weeks = _lms_weeks_for_item(item)
        month_key = _month_key(weeks[0].get("date")) if weeks else "undated"
        month = module["months"].setdefault(month_key, {
            "month_key": month_key,
            "label": _month_label(month_key),
            "source": "Aptem",
            "aptem_components": aptem_months.get(month_key, []),
            "weeks": {},
        })
        summaries = _lms_components_or_lecture_summaries(item)
        for week, week_summaries in zip(weeks, _split_across_weeks(summaries, weeks)):
            week_entry = month["weeks"].setdefault(week["week_key"], {
                **week,
                "lectures_or_components": [],
                "lectures_or_components_count": 0,
                "allocation_note": "LMS does not provide per-lecture dates; completed material titles are distributed across the LMS month weeks in source order.",
            })
            for component in week_summaries:
                if component not in week_entry["lectures_or_components"]:
                    week_entry["lectures_or_components"].append(component)
            week_entry["lectures_or_components_count"] = len(week_entry["lectures_or_components"])
            week_entry["lectures_or_components"] = week_entry["lectures_or_components"][:SOURCE_DATA_SUMMARY_LIMIT]

    result = []
    for module in modules.values():
        months = []
        for month in module["months"].values():
            month["weeks"] = sorted(month["weeks"].values(), key=lambda week: week["week_key"])
            months.append(month)
        module["months"] = sorted(months, key=lambda month: month["month_key"], reverse=True)
        result.append(module)
    return sorted(result, key=lambda module: module["name"])


def _aptem_month_map(aptem_items):
    months = {}
    for component in aptem_items:
        if not isinstance(component, dict):
            continue
        month_key = _month_key(component.get("end_date") or component.get("start_date")) or "undated"
        months.setdefault(month_key, []).append(_aptem_month_component(component))
    return months


def _source_data_months(aptem_items, lms_items):
    month_map = {}
    for component in aptem_items:
        if not isinstance(component, dict):
            continue
        month_key = _month_key(component.get("end_date") or component.get("start_date")) or "undated"
        month = month_map.setdefault(month_key, {
            "month_key": month_key,
            "label": _month_label(month_key),
            "source": "Aptem",
            "aptem_month_components": [],
            "lms_weeks": [],
        })
        month["aptem_month_components"].append(_aptem_month_component(component))

    for item in lms_items:
        if not isinstance(item, dict):
            continue
        week = _lms_week(item)
        month_key = _month_key(week.get("date")) if week else None
        month_key = month_key or "undated"
        month = month_map.setdefault(month_key, {
            "month_key": month_key,
            "label": _month_label(month_key),
            "source": "Aptem",
            "aptem_month_components": [],
            "lms_weeks": [],
        })
        if week:
            week["modules"] = [_lms_module_summary(item)]
            summaries = _lms_components_or_lecture_summaries(item)
            week["components_or_lecture_summaries_count"] = len(summaries)
            week["components_or_lecture_summaries"] = summaries[:SOURCE_DATA_SUMMARY_LIMIT]
            month["lms_weeks"].append(week)

    return sorted(month_map.values(), key=lambda month: month["month_key"], reverse=True)


def _aptem_month_component(component):
    return {
        "name": component.get("name"),
        "type": component.get("type"),
        "status": component.get("status"),
        "hours": _number(component.get("hours")),
        "planned_hours": _number(component.get("planned_hours")),
        "start_date": _date_iso(component.get("start_date")),
        "end_date": _date_iso(component.get("end_date")),
    }


def _lms_week(item):
    date_value = (
        item.get("Course Completed At")
        or item.get("Latest Quiz Submitted At")
        or item.get("Course Started At")
        or item.get("Registered At")
    )
    week_parts = _week_key(date_value)
    if not week_parts:
        return None
    week_key, start, end, label = week_parts
    return {
        "week_key": week_key,
        "label": label,
        "date": _date_iso(date_value),
        "start_date": start,
        "end_date": end,
        "source": "LMS",
    }


def _lms_weeks_for_item(item):
    date_value = (
        item.get("Course Completed At")
        or item.get("Latest Quiz Submitted At")
        or item.get("Course Started At")
        or item.get("Registered At")
    )
    parsed = _date(date_value)
    if not parsed:
        return [{
            "week_key": "undated",
            "label": "Undated / Needs Review",
            "date": None,
            "start_date": None,
            "end_date": None,
            "source": "LMS",
        }]

    first_day = parsed.replace(day=1)
    last_day = _last_day_of_month(parsed)
    weeks = []
    start = first_day
    while start <= last_day:
        end = min(start + datetime.timedelta(days=6), last_day)
        weeks.append({
            "week_key": start.isoformat(),
            "label": f"{start.day}-{end.day} {start.strftime('%b')}",
            "date": parsed.isoformat(),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "source": "LMS",
        })
        start = end + datetime.timedelta(days=1)
    return weeks


def _split_across_weeks(items, weeks):
    if not weeks:
        return []
    buckets = [[] for _ in weeks]
    if not items:
        return buckets
    for index, item in enumerate(items):
        bucket_index = min((index * len(weeks)) // len(items), len(weeks) - 1)
        buckets[bucket_index].append(item)
    return buckets


def _lms_module_summary(item):
    return {
        "name": _text(item.get("Module/Course") or item.get("Course")) or "Not available",
        "course_id": item.get("Course ID"),
        "status": item.get("Course Status") or item.get("Latest Quiz Status"),
        "progress_percent": _number(item.get("Progress (%)")),
        "components_total": _integer(item.get("Components Total")),
        "components_completed": _integer(item.get("Components Completed")),
        "materials_completed": _integer(item.get("Materials Completed")),
        "quizzes_attempted": _integer(item.get("Quizzes Attempted") or item.get("Quiz Attempts Count")),
    }


def _lms_components_or_lecture_summaries(item):
    values = []
    completed_titles = _text(item.get("Completed Material Titles"))
    if completed_titles:
        values.extend(title.strip() for title in completed_titles.split("|") if title.strip())
    latest_quiz = _text(item.get("Latest Quiz Title"))
    if latest_quiz:
        values.append(latest_quiz)
    seen = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _summary_from_lms_items(lms_items):
    if not lms_items:
        return None
    progress_values = [_number(item["raw"].get("Progress (%)")) for item in lms_items if _number(item["raw"].get("Progress (%)")) is not None]
    tracked_values = [item.get("tracked_seconds") for item in lms_items if item.get("tracked_seconds") is not None]
    return {
        "lms_progress": round(sum(progress_values) / len(progress_values), 2) if progress_values else None,
        "tracked_seconds": max(tracked_values) if tracked_values else None,
    }


def _summary_from_quiz_items(lms_items):
    attempts = [item.get("quiz_attempts") for item in lms_items if item.get("quiz_attempts") is not None]
    return {"quiz_attempts": sum(attempts)} if attempts else None


def _sum_unique_planned_hours(aptem_items, until_month=None, only_month=None):
    seen = set()
    total = decimal.Decimal("0")
    for item in aptem_items:
        planned = item.get("planned_hours")
        if planned is None:
            continue
        month = _month_key(item.get("relevant_date"))
        if only_month and month != only_month:
            continue
        if until_month and (not month or month > until_month):
            continue
        key = (item.get("source_id"), item.get("activity_name"), item.get("start_date"), item.get("end_date"))
        if key in seen:
            continue
        seen.add(key)
        total += decimal.Decimal(str(planned))
    return float(total)


def _group_months(aptem_items, lms_items, monthly_hours=None, today=None):
    today = today or _today()
    month_map = {}

    def ensure_month(key):
        if key not in month_map:
            month_map[key] = {
                "month_key": key,
                "label": _month_label(key),
                "summary": {
                    "actual_hours": 0,
                    "planned_hours": 0,
                    "aptem_items": 0,
                    "lms_items": 0,
                    "completed": 0,
                    "in_progress": 0,
                    "not_started": 0,
                    "warnings": 0,
                },
                "weeks": [],
                "undated_items": [],
                "signoffs": {"learner": None, "coach": None},
            }
        return month_map[key]

    week_maps = defaultdict(dict)
    all_items = [(item, "aptem_items") for item in aptem_items] + [(item, "lms_items") for item in lms_items]
    for item, list_key in all_items:
        relevant = item.get("relevant_date")
        if _is_future_date(relevant, today):
            continue
        month = _month_key(relevant)
        if not month:
            ensure_month("undated")["undated_items"].append(item)
            continue
        month_entry = ensure_month(month)
        week_parts = _week_key(relevant)
        if not week_parts:
            month_entry["undated_items"].append(item)
            continue
        week_key, start, end, label = week_parts
        if week_key not in week_maps[month]:
            week_maps[month][week_key] = {
                "week_key": week_key,
                "label": label,
                "start_date": start,
                "end_date": end,
                "aptem_items": [],
                "lms_items": [],
            }
        week_maps[month][week_key][list_key].append(item)

    for key, month in month_map.items():
        if key != "undated":
            month["weeks"] = sorted(week_maps[key].values(), key=lambda week: week["week_key"])
        month_aptem = [item for week in month["weeks"] for item in week["aptem_items"]]
        month_lms = [item for week in month["weeks"] for item in week["lms_items"]]
        for item in month["undated_items"]:
            (month_aptem if item.get("source") == "Aptem" else month_lms).append(item)
        statuses = [_status_bucket(item.get("status") or item.get("completion_status")) for item in month_aptem + month_lms]
        planned_from_table = (monthly_hours or {}).get("planned", {}).get(key)
        completed_from_table = (monthly_hours or {}).get("completed", {}).get(key)
        month["summary"] = {
            "actual_hours": completed_from_table,
            "planned_hours": planned_from_table,
            "aptem_items": len(month_aptem),
            "lms_items": len(month_lms),
            "completed": statuses.count("completed"),
            "in_progress": statuses.count("in_progress"),
            "not_started": statuses.count("not_started"),
            "warnings": sum(len(item.get("warnings") or []) for item in month_aptem + month_lms),
        }
    return sorted(month_map.values(), key=lambda month: month["month_key"], reverse=True)


def _round_hours(value):
    return float(decimal.Decimal(str(value)).quantize(decimal.Decimal("0.01")))


def _apply_monthly_planned_totals(payload, monthly_hours=None):
    for month in payload["months"]:
        if month["month_key"] == "undated":
            continue
        planned_from_table = (monthly_hours or {}).get("planned", {}).get(month["month_key"])
        completed_from_table = (monthly_hours or {}).get("completed", {}).get(month["month_key"])
        month["summary"]["planned_hours"] = planned_from_table
        month["summary"]["actual_hours"] = completed_from_table
    if payload["months"]:
        first_month = payload["months"][0]["month_key"]
        if first_month != "undated":
            payload["summary"]["planned_hours_month"] = (monthly_hours or {}).get("planned", {}).get(first_month)
            payload["summary"]["planned_hours_to_date"] = _round_hours(
                sum(
                    value
                    for month_key, value in (monthly_hours or {}).get("planned", {}).items()
                    if month_key <= first_month
                )
            ) if (monthly_hours or {}).get("planned") else None
        else:
            payload["summary"]["planned_hours_month"] = None
            payload["summary"]["planned_hours_to_date"] = None


def _dedupe_warnings(warnings):
    seen = set()
    result = []
    for warning in warnings:
        key = (warning.get("code"), warning.get("path"), warning.get("message"))
        if key in seen:
            continue
        seen.add(key)
        result.append(warning)
    return result


def _snapshot_hash(payload):
    snapshot = {
        "learner": payload["learner"],
        "summary": payload["summary"],
        "months": [
            {key: value for key, value in month.items() if key != "signoffs"}
            for month in payload["months"]
        ],
        "warnings": payload["warnings"],
        "audit_version": AUDIT_VERSION,
    }
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _empty_signoffs_by_month(months):
    return {month["month_key"]: {"learner": None, "coach": None} for month in months}


def _field_sources():
    return {
        "learner.id": {"table": "Audit.learner_match", "column": "aptem_id", "join_key": "primary row", "fallback": None},
        "learner.name": {"table": "Audit.learner_match", "column": "learner_name", "join_key": "primary row", "fallback": None},
        "learner.programme_name": {"table": "Audit.learner_match", "column": "programme", "join_key": "primary row", "fallback": None},
        "learner.programme_start_date": {"table": None, "column": None, "join_key": None, "fallback": None},
        "learner.employer": {"table": None, "column": None, "join_key": None, "fallback": None},
        "learner.epa": {"table": None, "column": None, "join_key": None, "fallback": None},
        "learner.epao": {"table": None, "column": None, "join_key": None, "fallback": None},
        "summary.completed_otjh": {"table": "fetching_evidence.learner_hours_monthly", "column": "completed_hours_monthly", "join_key": "Learner_ID -> learner_id", "fallback": None},
        "summary.approved_hours": {"table": None, "column": None, "join_key": None, "fallback": None},
        "summary.ksb_progression": {"table": None, "column": None, "join_key": None, "fallback": None},
        "summary.planned_hours": {"table": "fetching_evidence.learner_hours_monthly", "column": "planned_hours_monthly", "join_key": "Learner_ID -> learner_id", "fallback": None},
        "summary.lms": {"table": "Audit.learner_match", "column": "programme_structure", "join_key": "Learner_ID -> aptem_id", "fallback": None},
        "summary.quiz": {"table": "Audit.learner_match", "column": "programme_structure.months[].weeks[].components[kind=quiz]", "join_key": "Learner_ID -> aptem_id", "fallback": None},
        "programme.months_weeks": {"table": "Audit.learner_match", "column": "programme_structure", "join_key": "Learner_ID -> aptem_id", "fallback": None},
        "attendance.sessions": {"table": "KBCDATABASE.public.kbc_attendance", "column": "ID/date/Attendance/module", "join_key": "Learner_ID -> ID/aptem_id", "fallback": None},
        "assignments.evidence": {"table": "fetching_attendence.public.assessment_fetch", "column": "learner_id/programme/assignments", "join_key": "Learner_ID -> learner_id", "fallback": None},
        "source_data.combined": {"table": "Audit.learner_match", "column": "programme_structure", "join_key": "Learner_ID -> aptem_id", "fallback": None},
        "company.logo": {"table": "environment", "column": "AUDIT_COMPANY_LOGO_URL", "join_key": None, "fallback": None},
    }


def learner_audit(request, learner_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    try:
        with connections[resolve("enrolment")].cursor() as cur:
            _require_learner_match(cur)
            cur.execute(
                f"""
                select {_learner_match_select_sql()}
                from "{AUDIT_SCHEMA}".learner_match
                where aptem_id::text = %s
                """,
                [str(learner_id)],
            )
            rows = _rows_from_cursor(cur)
            if not rows:
                return _error("Learner audit record was not found.", 404)
            row = rows[0]
            monthly_hours = _fetch_monthly_hours(learner_id)
            payload = _build_audit_payload(row, monthly_hours)
            try:
                _attach_signoffs(cur, payload)
            except DatabaseError:
                pass
    except KeyError:
        return _error("The enrolment database connection is not configured.", 500)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    return JsonResponse(payload)


def _learner_summary(row):
    warnings = []
    aptem_components = _aptem_components(row, warnings)
    component_count = len(aptem_components)
    lms_items = _lms_items(row, warnings)
    return {
        "learnerId": str(row.get("Learner_ID") or ""),
        "fullName": row.get("Learner_name") or "",
        "programName": row.get("Programme_name") or "",
        "completedOtjh": row.get("Completed_OTJH"),
        "aptemComponentCount": component_count if aptem_components else None,
        "hasAptemData": bool(aptem_components),
        "hasLmsData": bool(lms_items) or bool(_text(row.get("LMS_Summary"))),
        "warnings": _dedupe_warnings(warnings),
    }


def _learner_list_summary(row):
    return {
        "learnerId": str(row.get("Learner_ID") or ""),
        "fullName": row.get("Learner_name") or "",
        "programName": row.get("Programme_name") or "",
        "completedOtjh": row.get("Completed_OTJH"),
        "aptemComponentCount": row.get("aptem_component_count"),
        "hasAptemData": bool(row.get("has_aptem_data")),
        "hasLmsData": bool(row.get("has_lms_data")),
        "warnings": [],
    }


def _learner_list_detail(row, include_audit=False, include_activities=False, activity_category="", attendance_items=None, assignment_items=None):
    summary = _learner_summary(row) if include_audit else _learner_list_summary(row)
    if include_audit:
        summary["audit"] = _build_audit_payload(row)
    if include_activities:
        summary["activities"] = _learner_activity_summaries(row, activity_category, attendance_items, assignment_items)
    return summary


def _quote_column(column):
    return '"' + column.replace('"', '""') + '"'


def _first_column(columns, candidates):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    lower_map = {str(column).lower(): column for column in columns}
    for candidate in candidates:
        match = lower_map.get(str(candidate).lower())
        if match:
            return match
    return None


def _audit_table_exists(cur, table_name):
    cur.execute(
        """
        select 1
        from information_schema.tables
        where table_schema = %s and table_name = %s
        limit 1
        """,
        [AUDIT_SCHEMA, table_name],
    )
    return bool(cur.fetchone())


def _audit_table_columns(cur, table_name):
    cur.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = %s
        """,
        [AUDIT_SCHEMA, table_name],
    )
    return {row[0] for row in cur.fetchall()}


def _can_use_learner_match(cur):
    if not _audit_table_exists(cur, "learner_match"):
        return False
    columns = _audit_table_columns(cur, "learner_match")
    return "programme_structure" in columns and bool(_first_column(columns, ("aptem_id", "Learner_ID", "learner_id")))


def _require_learner_match(cur):
    if not _can_use_learner_match(cur):
        raise DatabaseError("Audit.learner_match.programme_structure is required for learner audit reports.")


def _learner_match_select_sql(include_structure=True):
    structure_column = "programme_structure" if include_structure else "null::json as programme_structure"
    return f"""
        aptem_id as "Learner_ID",
        learner_name as "Learner_name",
        (programme_structure::jsonb ->> 'programme') as "Programme_name",
        null::numeric as "Completed_OTJH",
        null::integer as aptem_component_count,
        false as has_aptem_data,
        (programme_structure is not null) as has_lms_data,
        null::json as "Aptem_components",
        null::json as "LMS_modules_details",
        ''::text as "LMS_Summary",
        ''::text as "Quiz_summary",
        learner_email,
        lms_id,
        {structure_column}
    """


def _learner_match_test_filter_sql():
    return """
    not (
        coalesce(learner_name, '') ilike '%%(test)%%'
        or coalesce(programme_structure::jsonb ->> 'programme', '') ilike '%%(test)%%'
        or coalesce(learner_name, '') ~* '(^|[^a-z])test([^a-z]|$)'
    )
    """


def learner_audit_list(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    search = (request.GET.get("search") or "").strip()
    include_test = (request.GET.get("includeTest") or request.GET.get("include_test") or "").strip().lower() in {"1", "true", "yes"}
    include_audit = (request.GET.get("includeAudit") or request.GET.get("include_audit") or "").strip().lower() in {"1", "true", "yes"}
    include_activities = (request.GET.get("includeActivities") or request.GET.get("include_activities") or "").strip().lower() in {"1", "true", "yes"}
    activity_category = (request.GET.get("activityCategory") or request.GET.get("activity_category") or "").strip()
    limit_raw = (request.GET.get("limit") or "").strip()
    page_raw = (request.GET.get("page") or "").strip()
    page_size_raw = (request.GET.get("pageSize") or request.GET.get("page_size") or "").strip()
    limit = None
    if limit_raw:
        try:
            limit = max(1, int(limit_raw))
        except ValueError:
            limit = None
    page = 1
    page_size = None
    if page_raw or page_size_raw:
        try:
            page = max(1, int(page_raw or "1"))
        except ValueError:
            page = 1
        try:
            page_size = min(100, max(1, int(page_size_raw or "25")))
        except ValueError:
            page_size = 25
        limit = page_size
    offset = (page - 1) * page_size if page_size else 0
    total_count = None
    try:
        with connections[resolve("enrolment")].cursor() as cur:
            _require_learner_match(cur)
            list_columns_sql = _learner_match_select_sql(include_structure=include_audit or include_activities)
            source_sql = f'from "{AUDIT_SCHEMA}".learner_match'
            name_column = "learner_name"
            programme_column = "programme_structure::jsonb ->> 'programme'"
            learner_id_sql = "aptem_id::text"
            test_filter_sql = _learner_match_test_filter_sql()
            where_parts = []
            query_params = []
            if search:
                pattern = f"%{search}%"
                where_parts.append(
                    f"""
                    {learner_id_sql} ilike %s
                    or coalesce({name_column}, '') ilike %s
                    or coalesce({programme_column}, '') ilike %s
                    """
                )
                query_params.extend([pattern, pattern, pattern])
            if not include_test:
                where_parts.append(test_filter_sql)
            where_sql = f"where {' and '.join(f'({part})' for part in where_parts)}" if where_parts else ""
            if page_size:
                cur.execute(
                    f"""
                    select count(*)
                    {source_sql}
                    {where_sql}
                    """,
                    query_params,
                )
                total_count = cur.fetchone()[0]
            limit_sql = ""
            if limit:
                limit_sql = f"limit {limit}"
                if page_size:
                    limit_sql += f" offset {offset}"
            cur.execute(
                f"""
                select {list_columns_sql}
                {source_sql}
                {where_sql}
                order by coalesce({name_column}, ''), {learner_id_sql}
                {limit_sql}
                """,
                query_params,
            )
            rows = _rows_from_cursor(cur)
    except KeyError:
        return _error("The enrolment database connection is not configured.", 500)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    learner_ids = [row.get("Learner_ID") for row in rows]
    activity_sources = _activity_sources_for_category(activity_category)
    attendance_by_learner = {}
    assignment_by_learner = {}
    if include_activities and "Aptem" in activity_sources:
        attendance_by_learner = _fetch_kbc_attendance_items_for_ids(learner_ids)
    if include_activities and activity_category in {"", "assignment", "assessment", "other"}:
        assignment_by_learner = _fetch_assignment_items_for_ids(learner_ids)
    count = total_count if total_count is not None else len(rows)
    response_page_size = page_size or len(rows)
    results = [
        _learner_list_detail(
            row,
            include_audit,
            include_activities,
            activity_category,
            attendance_by_learner.get(str(row.get("Learner_ID") or ""), []),
            assignment_by_learner.get(str(row.get("Learner_ID") or ""), []),
        )
        for row in rows
    ]
    payload = {
        "count": count,
        "page": page,
        "pageSize": response_page_size,
        "totalPages": ((count + response_page_size - 1) // response_page_size) if response_page_size else 1,
        "results": results,
    }
    if include_activities:
        payload["activityStats"] = _activity_stats_from_results(results, len(results))
    return JsonResponse(payload)


def _azure_service_client():
    if BlobServiceClient is None:
        raise RuntimeError("The azure-storage-blob package is not installed.")
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    if not connection_string:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured.")
    return BlobServiceClient.from_connection_string(connection_string)


def _azure_connection_parts():
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "").strip()
    parts = {}
    for chunk in connection_string.split(";"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parts[key] = value
    return parts


def _blob_client_with_fallback(service, container, blob_name):
    client = service.get_blob_client(container=container, blob=blob_name)
    try:
        client.get_blob_properties()
        return client
    except Exception as exc:
        fallback_container = f"{container}s" if not container.endswith("s") else ""
        if not fallback_container or "ContainerNotFound" not in str(exc):
            raise
        fallback_client = service.get_blob_client(container=fallback_container, blob=blob_name)
        fallback_client.get_blob_properties()
        return fallback_client


def _signed_blob_url(client):
    parts = _azure_connection_parts()
    connection_sas = (parts.get("SharedAccessSignature") or "").lstrip("?")
    if connection_sas:
        separator = "&" if "?" in client.url else "?"
        return f"{client.url}{separator}{connection_sas}"

    account_name = parts.get("AccountName") or getattr(client, "account_name", "")
    account_key = parts.get("AccountKey")
    if not account_name or not account_key or generate_blob_sas is None or BlobSasPermissions is None:
        return client.url

    token = generate_blob_sas(
        account_name=account_name,
        container_name=client.container_name,
        blob_name=client.blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30),
    )
    return f"{client.url}?{token}"


def _parse_contract_azure_path(value):
    """Return the fixed contract container/blob stored in an ``az://`` URI."""
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.lower() != "az" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Invalid contract Azure path.")

    path = unquote(parsed.path).lstrip("/")
    try:
        container, blob_name = path.split("/", 1)
    except ValueError as exc:
        raise ValueError("Invalid contract Azure path.") from exc

    blob_parts = blob_name.replace("\\", "/").split("/")
    if (
        container != "contracts"
        or not blob_name.startswith("aptem_cv_contracts_probe/")
        or any(part in {"", ".", ".."} for part in blob_parts)
    ):
        raise ValueError("Invalid contract Azure path.")
    return container, blob_name


def contract_file(request, contract_id):
    """Render an audited Azure contract without exposing its storage path."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    try:
        with connections[resolve("enrolment")].cursor() as cursor:
            from .contract_documents import ensure_contract_archive_table

            ensure_contract_archive_table(cursor)
            cursor.execute(
                '''
                select contracts.azure_path,
                       coalesce(nullif(archive.display_name, ''), contracts.document_name)
                from fetching_evidence.aptem_cv_contracts_probe contracts
                left join "Audit".contract_document_archive archive
                  on archive.contract_id = contracts.id
                where contracts.id = %s
                limit 1
                ''',
                [contract_id],
            )
            row = cursor.fetchone()
    except (DatabaseError, KeyError):
        return _error("Could not load contract metadata.", 502)

    if not row:
        return _error("Contract not found.", 404)
    if not row[0]:
        return _error("This contract is not available in Azure.", 404)

    try:
        container, blob_name = _parse_contract_azure_path(row[0])
        service = _azure_service_client()
        client = _blob_client_with_fallback(service, container, blob_name)
        signed_url = _signed_blob_url(client)
    except ValueError:
        return _error("This contract has an invalid Azure path.", 409)
    except RuntimeError as exc:
        return _error(str(exc), 503)
    except Exception:
        return _error("The contract could not be opened from Azure.", 502)

    download_requested = (request.GET.get("download") or "").strip().lower() in {"1", "true", "yes"}
    extension = os.path.splitext(blob_name)[1].lower()
    document_name = str(row[1] or "Contract").strip()[:180] or "Contract"
    if not os.path.splitext(document_name)[1] and extension:
        document_name = f"{document_name}{extension}"

    if not download_requested and extension in {".doc", ".docx", ".docm", ".xls", ".xlsx", ".ppt", ".pptx"}:
        viewer_url = f"https://view.officeapps.live.com/op/embed.aspx?src={quote(signed_url, safe='')}"
        response = HttpResponse(
            f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(document_name)}</title>
  <style>
    html, body, iframe {{ width: 100%; height: 100%; margin: 0; border: 0; background: #f5f3ec; }}
  </style>
</head>
<body><iframe src="{escape(viewer_url, quote=True)}" title="{escape(document_name, quote=True)}"></iframe></body>
</html>''',
            content_type="text/html; charset=utf-8",
        )
        response["Content-Security-Policy"] = (
            "default-src 'none'; frame-src https://view.officeapps.live.com; "
            "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        )
        response["Cache-Control"] = "private, no-store"
        return response

    try:
        downloader = client.download_blob()
        properties = downloader.properties
        guessed_content_type = mimetypes.guess_type(blob_name)[0]
        stored_content_type = getattr(getattr(properties, "content_settings", None), "content_type", None)
        content_type = (
            guessed_content_type
            or stored_content_type
            or "application/octet-stream"
        )
        response = StreamingHttpResponse(downloader.chunks(), content_type=content_type)
        content_length = getattr(properties, "size", None)
        if content_length is not None:
            response["Content-Length"] = str(content_length)
    except Exception:
        return _error("The contract could not be streamed from Azure.", 502)

    response["Content-Disposition"] = content_disposition_header(download_requested, document_name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def evidence_file(request, evidence_id):
    """Open an Aptem evidence submission from its audited Azure manifest."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    raw_learner_id = (request.GET.get("learner_id") or "").strip()
    try:
        learner_id = int(raw_learner_id) if raw_learner_id else None
    except ValueError:
        return _error("learner_id must be an integer.", 400)

    try:
        if str(evidence_id).startswith("audit-"):
            from .evidence_documents import uploaded_evidence_location

            row = uploaded_evidence_location(str(evidence_id), learner_id)
        else:
            if not str(evidence_id).isdigit():
                return _error("Evidence ID is invalid.", 400)
            conditions = ["item ->> 'evidence_id' = %s"]
            params = [str(evidence_id)]
            if learner_id is not None:
                conditions.append("evidence.learner_id = %s")
                params.append(learner_id)
            with connections[resolve("enrolment")].cursor() as cursor:
                cursor.execute(
                    f'''
                    select evidence.azure_manifest ->> 'container',
                           item -> 'submission' ->> 'blob',
                           item -> 'submission' ->> 'filename',
                           item -> 'submission' ->> 'status'
                    from fetching_evidence.learner_evidence evidence
                    cross join lateral json_array_elements(
                        case
                            when json_typeof(evidence.azure_manifest -> 'items') = 'array'
                                then evidence.azure_manifest -> 'items'
                            else '[]'::json
                        end
                    ) item
                    where {' and '.join(conditions)}
                    order by evidence.fetched_at desc nulls last, evidence.id desc
                    limit 1
                    ''',
                    params,
                )
                row = cursor.fetchone()
    except (DatabaseError, KeyError):
        return _error("Could not load the evidence Azure manifest.", 502)

    if not row:
        return _error("Evidence file was not found in the Azure manifest.", 404)
    container, blob_name, filename, manifest_status = row
    if manifest_status != "present" or not container or not blob_name:
        return _error("Evidence file is not available in Azure.", 404)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", container):
        return _error("Evidence manifest contains an invalid container.", 409)
    blob_parts = str(blob_name).replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in blob_parts):
        return _error("Evidence manifest contains an invalid blob path.", 409)

    try:
        service = _azure_service_client()
        client = _blob_client_with_fallback(service, container, blob_name)
        signed_url = _signed_blob_url(client)
    except RuntimeError as exc:
        return _error(str(exc), 503)
    except Exception:
        return _error("The evidence file could not be opened from Azure.", 502)

    document_name = str(filename or blob_parts[-1] or f"Evidence {evidence_id}").strip()[:180]
    extension = os.path.splitext(document_name)[1].lower() or os.path.splitext(blob_name)[1].lower()
    if extension in {".doc", ".docx", ".docm", ".xls", ".xlsx", ".ppt", ".pptx"}:
        viewer_url = f"https://view.officeapps.live.com/op/embed.aspx?src={quote(signed_url, safe='')}"
        response = HttpResponse(
            f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(document_name)}</title>
  <style>html, body, iframe {{ width: 100%; height: 100%; margin: 0; border: 0; }}</style>
</head>
<body><iframe src="{escape(viewer_url, quote=True)}" title="{escape(document_name, quote=True)}"></iframe></body>
</html>''',
            content_type="text/html; charset=utf-8",
        )
        response["Content-Security-Policy"] = (
            "default-src 'none'; frame-src https://view.officeapps.live.com; "
            "style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
        )
        response["Cache-Control"] = "private, no-store"
        return response

    download_requested = (request.GET.get("download") or "").strip().lower() in {"1", "true", "yes"}
    try:
        downloader = client.download_blob()
        properties = downloader.properties
        guessed_content_type = mimetypes.guess_type(document_name)[0] or mimetypes.guess_type(blob_name)[0]
        stored_content_type = getattr(getattr(properties, "content_settings", None), "content_type", None)
        response = StreamingHttpResponse(
            downloader.chunks(),
            content_type=guessed_content_type or stored_content_type or "application/octet-stream",
        )
        content_length = getattr(properties, "size", None)
        if content_length is not None:
            response["Content-Length"] = str(content_length)
    except Exception:
        return _error("The evidence file could not be streamed from Azure.", 502)

    response["Content-Disposition"] = content_disposition_header(download_requested, document_name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def audit_blob(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _has_audit_permission(request):
        return _error("Authentication or audit permission is required.", 403)

    container = (request.GET.get("container") or "").strip()
    blob_name = unquote((request.GET.get("blob") or "").strip())
    if not container or not blob_name:
        return _error("Missing Azure container or blob.", 400)

    try:
        service = _azure_service_client()
        client = _blob_client_with_fallback(service, container, blob_name)
        return HttpResponseRedirect(_signed_blob_url(client))
    except RuntimeError as exc:
        return _error(str(exc), 503)
    except Exception as exc:
        return _error(f"Azure blob error: {exc}", 502)


# Set once this process has provisioned the sign-off table. The dedup DELETE
# below is a one-time migration, not a recurring repair: the table declares
# `unique (learner_id, programme_key, report_month, signer_role)`, so once it
# exists a duplicate cannot be inserted and that delete can never match a row.
# It was running -- as a full self-join over the table -- on every audit read.
_SIGNOFF_TABLE_READY = False


def _ensure_signoff_table(cur):
    """Provision the sign-off table, once per process.

    The flag is set only after every statement below succeeds, so a partial
    failure leaves it clear and the next caller retries. A schema changed by
    hand underneath a running process is not picked up until it restarts.
    """
    global _SIGNOFF_TABLE_READY
    if _SIGNOFF_TABLE_READY:
        return
    cur.execute(
        f"""
        create table if not exists "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}" (
            id bigserial primary key,
            learner_id text not null,
            programme_key text not null,
            report_month text not null,
            signer_role text not null check (signer_role in ('learner', 'coach')),
            signer_name text,
            review_confirmed boolean default false,
            signature_data text,
            signed_at timestamp with time zone,
            snapshot_hash text,
            audit_version text,
            created_at timestamp with time zone default now(),
            updated_at timestamp with time zone default now(),
            unique (learner_id, programme_key, report_month, signer_role)
        )
        """
    )
    cur.execute(
        f"""
        delete from "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}" existing
        using "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}" duplicate
        where existing.learner_id = duplicate.learner_id
          and existing.programme_key = duplicate.programme_key
          and existing.report_month = duplicate.report_month
          and existing.signer_role = duplicate.signer_role
          and existing.ctid < duplicate.ctid
        """
    )
    cur.execute(
        f"""
        drop index if exists "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}_learner_id_uidx"
        """
    )
    cur.execute(
        f"""
        create unique index if not exists "{SIGNOFF_TABLE}_scope_uidx"
        on "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}"
        (learner_id, programme_key, report_month, signer_role)
        """
    )
    _SIGNOFF_TABLE_READY = True


def _signoff_row(row, current_hash):
    if not row:
        return None
    stale = bool(row.get("snapshot_hash") and row.get("snapshot_hash") != current_hash)
    return {
        "id": row.get("id"),
        "signer_role": row.get("signer_role"),
        "signer_name": row.get("signer_name") or "",
        "review_confirmed": bool(row.get("review_confirmed")),
        "signature_data": row.get("signature_data") or "",
        "signed_at": row.get("signed_at"),
        "snapshot_hash": row.get("snapshot_hash"),
        "audit_version": row.get("audit_version"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "is_stale": stale,
        "status_message": "Signature requires renewal - audit data changed after sign-off." if stale else "",
    }


def _attach_signoffs(cur, payload):
    _ensure_signoff_table(cur)
    learner_id = str(payload["learner"]["id"] or "")
    programme_key = payload["learner"]["programme_key"]
    month_keys = [month["month_key"] for month in payload["months"]]
    if not month_keys:
        return
    cur.execute(
        f"""
        select *
        from "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}"
        where learner_id = %s and programme_key = %s and report_month = any(%s)
        """,
        [learner_id, programme_key, month_keys],
    )
    rows = _rows_from_cursor(cur)
    by_scope = {(row["report_month"], row["signer_role"]): row for row in rows}
    for month in payload["months"]:
        month["signoffs"] = {
            "learner": _signoff_row(by_scope.get((month["month_key"], "learner")), payload["snapshot_hash"]),
            "coach": _signoff_row(by_scope.get((month["month_key"], "coach")), payload["snapshot_hash"]),
        }
    payload["signoffs"] = {month["month_key"]: month["signoffs"] for month in payload["months"]}


@csrf_exempt
def learner_signoff(request, learner_id):
    if not _has_audit_permission(request, write=request.method != "GET"):
        return _error("Authentication or audit permission is required.", 403)

    month_key = (request.GET.get("month") or "").strip() or "all"
    try:
        with connections[resolve("enrolment")].cursor() as cur:
            _require_learner_match(cur)
            cur.execute(
                f"""
                select {_learner_match_select_sql()}
                from "{AUDIT_SCHEMA}".learner_match
                where aptem_id::text = %s
                """,
                [str(learner_id)],
            )
            rows = _rows_from_cursor(cur)
            if not rows:
                return _error("Learner audit record was not found.", 404)
            audit_payload = _build_audit_payload(rows[0])
            programme_key = audit_payload["learner"]["programme_key"]
            snapshot_hash = audit_payload["snapshot_hash"]
            _ensure_signoff_table(cur)

            if request.method == "GET":
                cur.execute(
                    f"""
                    select * from "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}"
                    where learner_id = %s and programme_key = %s and report_month = %s
                    """,
                    [str(learner_id), programme_key, month_key],
                )
                signoffs = {"learner": None, "coach": None}
                for row in _rows_from_cursor(cur):
                    signoffs[row["signer_role"]] = _signoff_row(row, snapshot_hash)
                return JsonResponse({"learnerId": str(learner_id), "month": month_key, "signoffs": signoffs})

            if request.method != "POST":
                return _error("Method not allowed.", 405)

            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except ValueError:
                return _error("Invalid JSON body.", 400)

            report_month = _text(payload.get("monthKey") or month_key or "all")
            roles = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
            if not roles:
                roles = {
                    "learner": {
                        "signerName": payload.get("learnerSignerName"),
                        "signature": payload.get("learnerSignature"),
                        "confirmed": payload.get("learnerConfirmed"),
                        "signedAt": payload.get("learnerSignedAt"),
                    },
                    "coach": {
                        "signerName": payload.get("coachSignerName"),
                        "signature": payload.get("coachSignature"),
                        "confirmed": payload.get("coachConfirmed"),
                        "signedAt": payload.get("coachSignedAt"),
                    },
                }

            for role in ("learner", "coach"):
                role_payload = roles.get(role) or {}
                cur.execute(
                    f"""
                    insert into "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}" (
                        learner_id, programme_key, report_month, signer_role, signer_name,
                        review_confirmed, signature_data, signed_at, snapshot_hash, audit_version, updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    on conflict (learner_id, programme_key, report_month, signer_role) do update set
                        signer_name = excluded.signer_name,
                        review_confirmed = excluded.review_confirmed,
                        signature_data = excluded.signature_data,
                        signed_at = excluded.signed_at,
                        snapshot_hash = excluded.snapshot_hash,
                        audit_version = excluded.audit_version,
                        updated_at = now()
                    """,
                    [
                        str(learner_id),
                        programme_key,
                        report_month,
                        role,
                        _text(role_payload.get("signerName")),
                        bool(role_payload.get("confirmed")),
                        _text(role_payload.get("signature")),
                        role_payload.get("signedAt") or None,
                        snapshot_hash,
                        AUDIT_VERSION,
                    ],
                )

            cur.execute(
                f"""
                select * from "{AUDIT_SCHEMA}"."{SIGNOFF_TABLE}"
                where learner_id = %s and programme_key = %s and report_month = %s
                """,
                [str(learner_id), programme_key, report_month],
            )
            signoffs = {"learner": None, "coach": None}
            for row in _rows_from_cursor(cur):
                signoffs[row["signer_role"]] = _signoff_row(row, snapshot_hash)
            return JsonResponse({"learnerId": str(learner_id), "month": report_month, "signoffs": signoffs})
    except KeyError:
        return _error("The enrolment database connection is not configured.", 500)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
