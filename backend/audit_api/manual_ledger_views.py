"""Employee-arranged monthly ledger for the auditor-copy workspace.

The audit-copy journal no longer auto-arranges hours from the ``Last_audit``
mirror.  Instead employees build each learner's monthly report by hand: they
pick raw attendance / video / audio / reading+quiz rows (or type an assignment)
and decide the planned and actual hours themselves.  Original planned hours
come only from ``Last_audit.learners.planned_hours_monthly``; actual hours have
no automatic source at all.

Everything the employees arrange is stored in its own schema,
``Learner.learner_journal_rows``, so the ``Last_audit`` mirror stays a pure
read-only import target.  Both schemas live in the same Neon database (the
``audit`` connection alias), which keeps the joins to ``Last_audit`` cheap.
"""

import datetime
import json
import re

import psycopg
from psycopg.rows import dict_row

from django.db import DatabaseError, IntegrityError, connections, transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from learner_api import evidence_storage

from .db_source import resolve

from .views import (
    _fetch_assignment_items,
    _fetch_monthly_hours,
    _kbc_attendance_connection_string,
)

from .last_audit_ledger_views import (
    ACTIVITIES,
    ACTIVITY_RESULTS,
    CONNECTION_ALIAS,
    GROUPS,
    GROUP_LEARNERS,
    LEARNERS,
    LEARNER_ATTENDANCE,
    _activity_content_url,
    _as_int,
    _activity_category,
    _connection,
    _dict_rows,
    _duration_min_sql,
    _is_completed,
    _json_list,
    _session_key,
)

MANUAL_ROWS = '"Learner"."learner_journal_rows"'
MANUAL_DOCS = '"Learner"."manual_activity_documents"'
READING_QUIZ_PAIRS = '"curriculum"."source_reading_quiz_pairs"'
GROUP_ACTIVITIES = '"Last_audit"."group_activities"'
# Aptem evidence files, already mirrored to Azure by the fetch service — the
# source that lets ANY assignment row carry an in-system preview.
EVIDENCE_ITEMS = '"fetching_evidence"."evidence_items"'
EVIDENCE_CONTAINER = "fetch-aptem-evidences"

ASSIGNMENT_CONTAINER = "learner-assignments"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# The four retrievable categories map onto Last_audit.activities.activity_type
# (attendance comes from learner_attendance instead); assignment is typed by
# hand and has no source table at all.
SOURCE_CATEGORIES = {"video", "audio", "reading+quiz"}
CATEGORIES = SOURCE_CATEGORIES | {"attendance", "assignment"}
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

# The ledger closes at August 2026: every learner's month list runs from their
# first planned month up to (and including) this month, and no manual rows can
# be filed after it.
LEDGER_END_MONTH = "2026-08"

# Every register session counts as one fixed 2.5-hour block; attending awards
# the same 2.5 hours as actual time, an absence keeps the plan but awards none.
ATTENDANCE_SESSION_HOURS = 2.5

ROW_COLUMNS = (
    "id, aptem_id, learner_id, month, category, source_ref, group_id, "
    "activity_id, title, activity_date, activity_time, planned_hours, "
    "actual_hours, timestamp_label, completion_note, accepted, created_by, "
    "updated_by, created_at, updated_at"
)


# The schema DDL is idempotent but costs six server round trips; running it
# once per process keeps every manual endpoint fast on the remote database.


def _ensure_manual_tables(cursor):
    """The migrated journal is provisioned centrally; reads never create it."""
    cursor.execute("SELECT to_regclass(%s),to_regclass(%s),to_regclass(%s)",
                   [MANUAL_ROWS, MANUAL_DOCS, READING_QUIZ_PAIRS])
    if not all(cursor.fetchone()):
        raise DatabaseError('The learner journal tables are not available.')



def _month_label(month):
    try:
        return datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except (TypeError, ValueError):
        return month


def _month_range(start, end):
    """Every "YYYY-MM" from ``start`` to ``end`` inclusive."""
    year, month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    months = []
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return months


def _load_learner(cursor, aptem_id):
    cursor.execute(
        f"""
        SELECT aptem_id, learner_id, learner_name, learner_email,
               programme_name, programme_status, coach_name, coach_email,
               planned_hours_total, planned_hours_monthly
        FROM {LEARNERS} WHERE aptem_id = %s
        """,
        [aptem_id],
    )
    rows = _dict_rows(cursor)
    if rows:
        return rows[0]

    # An ILR-backed Aptem learner can legitimately precede the normalized
    # Last_audit roster.  The manual report is keyed by Aptem ID, so retaining
    # that real identity lets the learner use the report immediately without
    # inventing an LMS match.
    cursor.execute(
        """
        SELECT aptem."ID" AS aptem_id,
               NULL::bigint AS learner_id,
               aptem."FullName" AS learner_name,
               aptem."Email" AS learner_email,
               COALESCE(
                   NULLIF(btrim(aptem."Program Name"), ''),
                   NULLIF(btrim(aptem."Group"), '')
               ) AS programme_name,
               NULLIF(btrim(aptem."Program-Status"), '') AS programme_status,
               NULLIF(btrim(aptem."OwnerName"), '') AS coach_name,
               NULLIF(btrim(aptem."OwnerEmail"), '') AS coach_email,
               ilr.planned_hours AS planned_hours_total,
               COALESCE(monthly.planned_hours_monthly, '{}'::jsonb) AS planned_hours_monthly,
               ilr.learn_start_date AS programme_start_date
        FROM "LMS"."Aptem_users" aptem
        JOIN LATERAL (
            SELECT delivery.planned_hours, delivery.learn_start_date
            FROM "Audit".ilr_learning_deliveries delivery
            WHERE (
                NULLIF(btrim(aptem."Email"), '') IS NOT NULL
                AND lower(btrim(delivery.email)) = lower(btrim(aptem."Email"))
            ) OR (
                NULLIF(btrim(aptem."FullName"), '') IS NOT NULL
                AND lower(btrim(concat_ws(' ', delivery.given_names, delivery.family_name))) =
                    lower(btrim(aptem."FullName"))
            )
            ORDER BY delivery.aim_seq_number,
                     delivery.updated_at DESC NULLS LAST,
                     delivery.id DESC
            LIMIT 1
        ) ilr ON TRUE
        LEFT JOIN fetching_evidence.learner_hours_monthly monthly
          ON monthly.learner_id = aptem."ID"
        WHERE aptem."ID" = %s
        LIMIT 1
        """,
        [aptem_id],
    )
    rows = _dict_rows(cursor)
    return rows[0] if rows else None


def _planned_monthly(learner):
    raw = learner.get("planned_hours_monthly")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raw = None
    if not isinstance(raw, dict):
        return {}
    monthly = {}
    for key, value in raw.items():
        if not MONTH_RE.match(str(key)):
            continue
        try:
            monthly[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return monthly


def _num(value):
    return float(value) if value is not None else None


def _group_names(cursor, group_ids):
    """Course (LMS group) names for the rows, one round trip."""
    ids = sorted({int(value) for value in group_ids if value is not None})
    if not ids:
        return {}
    cursor.execute(f"SELECT group_id, group_name FROM {GROUPS} WHERE group_id = ANY(%s)", [ids])
    return {int(item["group_id"]): item["group_name"] for item in _dict_rows(cursor)}


def _attendance_modules(cursor, source_refs):
    """Register module per attendance row, keyed by the att: source key."""
    keys = sorted({ref[4:] for ref in source_refs if ref and ref.startswith("att:")})
    if not keys:
        return {}
    cursor.execute(
        f"SELECT source_key, module FROM {LEARNER_ATTENDANCE} WHERE source_key = ANY(%s)",
        [keys],
    )
    return {item["source_key"]: item.get("module") for item in _dict_rows(cursor)}


def _activity_durations(cursor, activity_ids):
    """Configured LMS media durations keyed by activity id, one round trip.

    Audio durations historically live only in ``raw.audio``; using the shared
    SQL expression keeps saved journal rows consistent with the retrieve flow.
    """
    ids = sorted({int(value) for value in activity_ids if value is not None})
    if not ids:
        return {}
    cursor.execute(
        f"""
        SELECT activity_id, {_duration_min_sql()} AS configured_duration_min
        FROM {ACTIVITIES}
        WHERE activity_id = ANY(%s)
        """,
        [ids],
    )
    return {
        int(item["activity_id"]): _num(item.get("configured_duration_min"))
        for item in _dict_rows(cursor)
    }


# Tutors write the lecture's real date into the activity title ("P1 -Marketing
# Definitions 2-5-2025"), while the LMS stores its own activity_date — often the
# day the content was uploaded, sometimes a whole year out. UK order (day first).
_TITLE_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})(?!\d)")


def _title_date(title, category):
    """The date written inside an LMS activity's own title, when it is a real
    date and disagrees with nothing else. Returns None for anything doubtful —
    typos like 2/5/5025 or 31/11 simply do not parse. Attendance keeps the
    register's date and assignments keep Aptem's, so both are left alone."""
    if category not in SOURCE_CATEGORIES:
        return None
    match = _TITLE_DATE_RE.search(str(title or ""))
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if not 2020 <= year <= 2035:
        return None
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def _row_payload(
    row, documents=None, *, source_course=None, module=None,
    duration_minutes=None,
):
    activity_date = row.get("activity_date")
    category = row["category"]
    if str(row.get("source_ref") or "").startswith("la:"):
        category = _activity_category({
            "activity_type": category,
            "title": row.get("title"),
        })
    # Surfaced only when it contradicts the stored date: the journal then offers
    # a one-click correction and the employee decides row by row.
    title_date = _title_date(row.get("title"), category)
    if title_date == activity_date:
        title_date = None
    return {
        # Where the activity came from: the LMS group ("course") for content
        # rows, the register module for attendance rows. Listing-only fields —
        # write endpoints return them as null.
        "source_course": source_course,
        "module": module,
        "id": int(row["id"]),
        "aptem_id": int(row["aptem_id"]),
        "learner_id": int(row["learner_id"]) if row.get("learner_id") is not None else None,
        "month": row["month"],
        "month_label": _month_label(row["month"]),
        "category": category,
        "source_ref": row.get("source_ref"),
        "group_id": int(row["group_id"]) if row.get("group_id") is not None else None,
        "activity_id": int(row["activity_id"]) if row.get("activity_id") is not None else None,
        "duration_minutes": _num(
            duration_minutes
            if duration_minutes is not None
            else row.get("configured_duration_min")
        ),
        "title": row["title"],
        "activity_date": activity_date.isoformat() if activity_date else None,
        # The lecture date the title itself names, when it differs from above.
        "title_date": title_date.isoformat() if title_date else None,
        "activity_time": row["activity_time"].strftime("%H:%M") if row.get("activity_time") else None,
        "planned_hours": _num(row.get("planned_hours")) or 0.0,
        "actual_hours": _num(row.get("actual_hours")) or 0.0,
        "timestamp_label": row.get("timestamp_label") or "",
        "completion_note": row.get("completion_note"),
        "accepted": row.get("accepted") is not False,
        "created_by": row.get("created_by"),
        "updated_by": row.get("updated_by"),
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "documents": documents or [],
    }


# Azure-mirrored Aptem docs carry the evidence id as a filename prefix
# ("<folder>/<evidence_id>-<filename>"); hand uploads follow no such scheme.
_EVIDENCE_BLOB_RE = re.compile(r"(?:^|/)(\d+)-([^/]+)$")


def _evidence_doc_identity(row):
    """(evidence_group, doc_kind) pairing a mirrored Aptem submission with its
    assessor marking report; hand-uploaded files are (None, "upload")."""
    if row.get("uploaded_by") != "aptem-evidence" or row.get("container") != EVIDENCE_CONTAINER:
        return None, "upload"
    match = _EVIDENCE_BLOB_RE.search(str(row.get("blob_name") or ""))
    if not match:
        return None, "upload"
    kind = "report" if match.group(2).lower().startswith("assessmentreport") else "evidence"
    return match.group(1), kind


def _doc_payload(row, *, with_sas=True):
    url = None
    if with_sas and evidence_storage.azure_configured():
        try:
            url = evidence_storage.get_read_sas(row["container"], row["blob_name"])
        except Exception:
            url = None
    evidence_group, doc_kind = _evidence_doc_identity(row)
    return {
        "id": int(row["id"]),
        "manual_activity_id": int(row["manual_activity_id"]),
        "display_name": row["display_name"],
        "content_type": row.get("content_type"),
        "size_bytes": int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
        "uploaded_by": row.get("uploaded_by"),
        "uploaded_at": row["uploaded_at"].isoformat() if row.get("uploaded_at") else None,
        "download_url": url,
        "evidence_group": evidence_group,
        "doc_kind": doc_kind,
    }


_DOC_KIND_RANK = {"evidence": 0, "report": 1}


def _paired_docs(docs):
    """Each Aptem evidence file immediately followed by its marking report.
    The sync inserts everything in one transaction, so uploaded_at ties and
    the stored order is arbitrary; hand uploads keep upload order at the end."""
    mirrored = [doc for doc in docs if doc["evidence_group"] is not None]
    uploads = [doc for doc in docs if doc["evidence_group"] is None]
    mirrored.sort(key=lambda doc: (int(doc["evidence_group"]), _DOC_KIND_RANK.get(doc["doc_kind"], 2), doc["id"]))
    return mirrored + uploads


def _documents_by_row(cursor, row_ids):
    if not row_ids:
        return {}
    cursor.execute(
        f"""
        SELECT id, manual_activity_id, aptem_id, month, container, blob_name,
               display_name, content_type, size_bytes, uploaded_by, uploaded_at
        FROM {MANUAL_DOCS}
        WHERE manual_activity_id = ANY(%s) AND deleted_at IS NULL
        ORDER BY uploaded_at, id
        """,
        [list(row_ids)],
    )
    grouped = {}
    for row in _dict_rows(cursor):
        grouped.setdefault(int(row["manual_activity_id"]), []).append(_doc_payload(row))
    return {row_id: _paired_docs(docs) for row_id, docs in grouped.items()}


# --- validation ------------------------------------------------------------

def _valid_hours(value, field):
    if value in (None, ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number")
    if number < 0 or number > 50:
        raise ValueError(f"{field} must be between 0 and 50 hours")
    return round(number, 4)


def _valid_date(value):
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("activity_date must use YYYY-MM-DD format")


def _valid_month(value):
    month = str(value or "").strip()
    if not MONTH_RE.match(month):
        raise ValueError("month must use YYYY-MM format")
    if month > LEDGER_END_MONTH:
        raise ValueError(f"months after {_month_label(LEDGER_END_MONTH)} are closed for editing")
    return month


def _valid_title(value):
    title = str(value or "").strip()
    if not title:
        raise ValueError("title is required")
    return title[:500]


def _parse_source_ref(category, raw):
    """Return ``(source_ref, group_id, activity_id)`` for a new manual row."""
    ref = str(raw or "").strip()
    # A deliberately hand-entered row can use any display category without
    # pretending to point at an LMS/attendance source record.
    if not ref:
        return None, None, None
    if category == "assignment":
        # Auto-imported assignments point back at their Aptem component so the
        # month sync stays idempotent; hand-typed assignments carry no ref.
        if ref.startswith("asg:") and len(ref) > 4:
            return ref, None, None
        raise ValueError("assignment rows are manual and take no source_ref")
    if category == "attendance":
        if not ref.startswith("att:") or len(ref) <= 4:
            raise ValueError("attendance rows need an att:<source_key> reference")
        return ref, None, None
    if category == "reading+quiz" and ref.startswith("rq:"):
        parts = ref.split(":")
        if len(parts) >= 4:
            try:
                return ref, int(parts[1]), int(parts[2])
            except ValueError:
                pass
        raise ValueError("reading+quiz bundle reference must contain a group id and at least two activity ids")
    parts = ref.split(":")
    if ref.startswith("la:") and len(parts) == 3:
        try:
            return ref, int(parts[1]), int(parts[2])
        except ValueError:
            raise ValueError("source_ref must look like la:<group_id>:<activity_id>")
    raise ValueError(f"{category} rows need a la:<group_id>:<activity_id> reference")


@csrf_exempt
def reading_quiz_pairs(request: HttpRequest) -> JsonResponse:
    """Persist an explicit bundle of two or more activities for one LMS group."""
    if request.method not in {"POST", "DELETE"}:
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body or b"{}")
        group_id = int(body.get("group_id"))
        raw_activity_ids = body.get("activity_ids")
        if raw_activity_ids is None:
            raw_activity_ids = [body.get("reading_activity_id"), body.get("quiz_activity_id")]
        activity_ids = list(dict.fromkeys(int(value) for value in raw_activity_ids))
    except (TypeError, ValueError):
        return JsonResponse({"error": "group_id and activity_ids must be integers"}, status=400)
    if len(activity_ids) < 2:
        return JsonResponse({"error": "Choose at least two different activities."}, status=400)
    anchor_id, member_ids = activity_ids[0], activity_ids[1:]
    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"SELECT activity_id FROM {GROUP_ACTIVITIES} WHERE group_id = %s AND activity_id = ANY(%s)",
                [group_id, activity_ids],
            )
            if len({int(row[0]) for row in cursor.fetchall()}) != len(activity_ids):
                return JsonResponse({"error": "All activities must belong to the selected group."}, status=400)
            if request.method == "DELETE":
                cursor.execute(
                    f"DELETE FROM {READING_QUIZ_PAIRS} WHERE group_id=%s AND reading_activity_id=%s",
                    [group_id, anchor_id],
                )
                return JsonResponse({"ok": True, "deleted": cursor.rowcount})
            cursor.execute(
                f"""
                SELECT reading_activity_id, quiz_activity_id
                FROM {READING_QUIZ_PAIRS}
                WHERE group_id = %s
                  AND (reading_activity_id = ANY(%s) OR quiz_activity_id = ANY(%s))
                """,
                [group_id, activity_ids, activity_ids],
            )
            if cursor.fetchone():
                return JsonResponse({"error": "One or more selected activities already belong to a bundle."}, status=409)
            created = 0
            for member_id in member_ids:
                cursor.execute(
                    f"""
                    INSERT INTO {READING_QUIZ_PAIRS} (group_id, reading_activity_id, quiz_activity_id, created_by)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (group_id, reading_activity_id, quiz_activity_id) DO NOTHING
                    """,
                    [group_id, anchor_id, member_id, _actor(body.get("created_by"))],
                )
                created += cursor.rowcount
    except DatabaseError as error:
        return JsonResponse({"error": "Could not save the Reading + Quiz link.", "details": str(error)}, status=503)
    return JsonResponse({"ok": True, "created": created, "group_id": group_id,
                         "activity_ids": activity_ids})


def _actor(value):
    return str(value or "").strip()[:200] or None


# --- endpoints ---------------------------------------------------------------

@require_GET
def summary(request: HttpRequest) -> JsonResponse:
    """Original planned hours vs employee-arranged sums, per month."""
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    if aptem_id is None:
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    try:
        with _connection().cursor() as cursor:
            learner = _load_learner(cursor, aptem_id)
            if not learner:
                return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"""
                SELECT month,
                       COALESCE(sum(planned_hours), 0) AS planned,
                       COALESCE(sum(actual_hours) FILTER (WHERE accepted), 0) AS actual,
                       COALESCE(sum(actual_hours) FILTER (WHERE NOT accepted), 0) AS not_accepted,
                       count(*) AS row_count
                FROM {MANUAL_ROWS}
                WHERE aptem_id = %s AND deleted_at IS NULL
                GROUP BY month
                """,
                [aptem_id],
            )
            arranged = {row["month"]: row for row in _dict_rows(cursor)}
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the manual ledger.", "details": str(error)},
            status=503,
        )

    planned_monthly = _planned_monthly(learner)
    # The actual hours the learner's own programme record already holds for each
    # month. Reference only — it is never written into the report; employees use
    # it to see how their arranged claim compares with what the source recorded.
    try:
        recorded_monthly = _fetch_monthly_hours(aptem_id).get("completed") or {}
    except Exception:
        recorded_monthly = {}
    # Continuous month list: the learner's first known month (the start-date
    # month, since the plan begins there) through the fixed ledger end. Months
    # with manual rows outside that window stay visible so no data hides.
    known = set(planned_monthly) | set(arranged)
    programme_start_date = learner.get("programme_start_date")
    if programme_start_date:
        known.add(str(programme_start_date)[:7])
    window_starts = [month for month in known if month <= LEDGER_END_MONTH]
    month_list = (
        _month_range(min(window_starts), LEDGER_END_MONTH)
        if window_starts else [LEDGER_END_MONTH]
    )
    month_list += sorted(month for month in arranged if month > LEDGER_END_MONTH)
    months = []
    # Running totals across the month list: each month carries itself plus every
    # month before it, so an employee can read progress-to-date at a glance.
    recorded_running = 0.0
    arranged_running = 0.0
    for month in month_list:
        row = arranged.get(month)
        recorded_month = recorded_monthly.get(month)
        if recorded_month is not None:
            recorded_running = round(recorded_running + float(recorded_month), 2)
        arranged_running = round(arranged_running + (_num(row["actual"]) if row else 0.0), 2)
        months.append({
            "month": month,
            "label": _month_label(month),
            "original_planned": planned_monthly.get(month),
            # What the source recorded as actual for this month (display only).
            "recorded_actual": recorded_month,
            # …and the same figure accumulated from the first month to this one.
            "recorded_actual_cumulative": recorded_running,
            "arranged_actual_cumulative": arranged_running,
            "arranged_planned": _num(row["planned"]) if row else 0.0,
            # Claimed = accepted rows only; rejected hours are reported apart.
            "arranged_actual": _num(row["actual"]) if row else 0.0,
            "arranged_not_accepted": _num(row["not_accepted"]) if row else 0.0,
            "row_count": int(row["row_count"]) if row else 0,
        })
    return JsonResponse({
        "source": "Learner.learner_journal_rows",
        "ledger_end_month": LEDGER_END_MONTH,
        "aptem_id": aptem_id,
        "learner_id": int(learner["learner_id"]) if learner.get("learner_id") is not None else None,
        "learner_name": learner.get("learner_name"),
        "learner_email": learner.get("learner_email"),
        "programme_name": learner.get("programme_name"),
        "programme_status": learner.get("programme_status"),
        "coach_name": learner.get("coach_name"),
        "coach_email": learner.get("coach_email"),
        "planned_hours_total": _num(learner.get("planned_hours_total")),
        "months": months,
        "arranged_planned_total": round(sum(m["arranged_planned"] for m in months), 2),
        "arranged_actual_total": round(sum(m["arranged_actual"] for m in months), 2),
        # Whole-programme total of the source's own recorded actual hours.
        "recorded_actual_total": round(sum(
            float(m["recorded_actual"]) for m in months if m["recorded_actual"] is not None
        ), 2),
    })


@require_GET
def cohort_totals(request: HttpRequest) -> JsonResponse:
    """Every learner's arranged ledger totals — the SAME accepted-actual sum
    each learner's monthly report shows — so the cohort table and the journal
    can never disagree. Whole cohort in one grouped query, with the per-month
    breakdown the search page's month filter needs.

    Deliberately uncached: employees edit these rows all day, and a stale
    Actual column is worse than a slower one (the query is ~100ms)."""
    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"""
                SELECT aptem_id, month,
                       COALESCE(sum(planned_hours), 0) AS planned,
                       COALESCE(sum(actual_hours) FILTER (WHERE accepted), 0) AS actual,
                       COALESCE(sum(actual_hours) FILTER (WHERE NOT accepted), 0) AS not_accepted,
                       count(*) AS row_count
                FROM {MANUAL_ROWS}
                WHERE deleted_at IS NULL
                GROUP BY aptem_id, month
                """
            )
            rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the arranged ledger totals.", "details": str(error)},
            status=503,
        )

    by_learner = {}
    for row in rows:
        aptem_id = int(row["aptem_id"])
        entry = by_learner.setdefault(aptem_id, {
            "aptem_id": aptem_id,
            "planned": 0.0, "actual": 0.0, "not_accepted": 0.0, "row_count": 0,
            "months": {},
        })
        month_totals = {
            "planned": _num(row["planned"]),
            "actual": _num(row["actual"]),
            "not_accepted": _num(row["not_accepted"]),
            "row_count": int(row["row_count"]),
        }
        entry["months"][row["month"]] = month_totals
        for field in ("planned", "actual", "not_accepted", "row_count"):
            entry[field] += month_totals[field]
    for entry in by_learner.values():
        for field in ("planned", "actual", "not_accepted"):
            entry[field] = round(entry[field], 2)
    return JsonResponse({
        "source": "Learner.learner_journal_rows",
        "count": len(by_learner),
        "items": list(by_learner.values()),
    })


@require_GET
def groups(request: HttpRequest) -> JsonResponse:
    """The learner's LMS groups (modules) with per-category activity counts."""
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    if aptem_id is None:
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    try:
        with _connection().cursor() as cursor:
            learner = _load_learner(cursor, aptem_id)
            if not learner:
                return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
            if learner.get("learner_id") is None:
                return JsonResponse({"aptem_id": aptem_id, "lms_matched": False, "groups": []})
            cursor.execute(
                f"""
                SELECT g.group_id, g.group_name,
                       count(a.activity_id) FILTER (
                           WHERE lower(a.activity_type) = 'video'
                       ) AS video,
                       count(a.activity_id) FILTER (WHERE lower(a.activity_type) = 'audio') AS audio,
                       count(a.activity_id) FILTER (
                           WHERE lower(a.activity_type) = 'reading+quiz'
                       ) AS reading_quiz
                FROM {GROUP_LEARNERS} gl
                JOIN {GROUPS} g ON g.group_id = gl.group_id
                LEFT JOIN {GROUP_ACTIVITIES} ga ON ga.group_id = g.group_id
                LEFT JOIN {ACTIVITIES} a ON a.activity_id = ga.activity_id
                WHERE gl.learner_id = %s
                GROUP BY g.group_id, g.group_name
                ORDER BY g.group_name
                """,
                [learner["learner_id"]],
            )
            rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit groups.", "details": str(error)},
            status=503,
        )
    return JsonResponse({
        "aptem_id": aptem_id,
        "lms_matched": True,
        "groups": [{
            "group_id": int(row["group_id"]),
            "group_name": row["group_name"],
            "counts": {
                "video": int(row["video"]),
                "audio": int(row["audio"]),
                "reading+quiz": int(row["reading_quiz"]),
            },
        } for row in rows],
    })


@require_GET
def group_activities(request: HttpRequest) -> JsonResponse:
    """The WHOLE group's activities in one category, with this learner's
    completion label. Deliberately group-wide: the learner's own
    activity_results listing is unreliable, so employees pick from the module
    catalogue instead."""
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
        group_id = _as_int(request.GET.get("group_id"), minimum=1)
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id and group_id must be integers"}, status=400)
    category = (request.GET.get("category") or "").strip().lower()
    if aptem_id is None or group_id is None:
        return JsonResponse({"error": "aptem_id and group_id are required"}, status=400)
    if category not in SOURCE_CATEGORIES:
        return JsonResponse(
            {"error": f"category must be one of: {', '.join(sorted(SOURCE_CATEGORIES))}"},
            status=400,
        )
    search = (request.GET.get("search") or "").strip()
    try:
        with _connection().cursor() as cursor:
            learner = _load_learner(cursor, aptem_id)
            if not learner:
                return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
            # Employees may not know the learner's real modules — never serve a
            # catalogue for a group this learner is not actually linked to.
            cursor.execute(
                f"SELECT 1 FROM {GROUP_LEARNERS} WHERE learner_id = %s AND group_id = %s",
                [learner.get("learner_id"), group_id],
            )
            if not cursor.fetchone():
                return JsonResponse(
                    {"error": "This learner is not a member of that group."}, status=404,
                )
            conditions = [
                "ga.group_id = %s",
                "lower(a.activity_type) IN ('video', 'audio', 'reading+quiz')",
            ]
            params = [group_id, learner.get("learner_id")]
            if search:
                conditions.append("a.title ILIKE %s")
                params.append(f"%{search}%")
            # DISTINCT ON collapses duplicate result rows (a learner can hold
            # results for the same activity under several groups).
            cursor.execute(
                f"""
                SELECT DISTINCT ON (a.activity_id)
                       a.activity_id, a.title, a.activity_date, a.activity_type,
                       a.quiz_id, a.quiz_questions, a.reading_type,
                       a.reading_iframe_url, a.reading_text_body,
                       {_duration_min_sql('a')} AS configured_duration_min,
                       ga.position,
                       r.learner_id AS result_learner_id, r.status,
                       r.video_completed, r.reading_viewed, r.quiz_passed
                FROM {GROUP_ACTIVITIES} ga
                JOIN {ACTIVITIES} a ON a.activity_id = ga.activity_id
                LEFT JOIN {ACTIVITY_RESULTS} r
                       ON r.activity_id = a.activity_id AND r.learner_id = %s
                WHERE {' AND '.join(conditions)}
                ORDER BY a.activity_id, r.learner_id NULLS LAST
                """,
                # learner_id placeholder sits inside the JOIN, before WHERE params
                [params[1], params[0], *params[2:]],
            )
            rows = sorted(
                (
                    row for row in _dict_rows(cursor)
                    if _activity_category(row) == category
                ),
                key=lambda row: (row.get("position") or 0, row["activity_id"]),
            )
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit group activities.", "details": str(error)},
            status=503,
        )

    activities = []
    for row in rows:
        if row.get("result_learner_id") is None:
            state = "no_record"
        else:
            state = "completed" if _is_completed(row) else "not_completed"
        activity_date = row.get("activity_date")
        activities.append({
            "activity_id": int(row["activity_id"]),
            "source_ref": f"la:{group_id}:{int(row['activity_id'])}",
            "title": row.get("title") or f"Activity {row['activity_id']}",
            "activity_date": activity_date.isoformat() if activity_date else None,
            # The configured media length — the employee's anchor when deciding
            # the actual hours to award.
            "duration_minutes": _num(row.get("configured_duration_min")),
            "completion": {"state": state},
        })
    return JsonResponse({
        "aptem_id": aptem_id,
        "group_id": group_id,
        "category": category,
        "count": len(activities),
        "activities": activities,
    })


def _source_attendance_rows(aptem_id):
    """This learner's register rows straight from the ORIGINAL
    ``AiTeamKBC public.kbc_attendance`` table — the mirror is not consulted, so
    the dropdown always shows what the register holds right now."""
    dsn = _kbc_attendance_connection_string()
    if not dsn:
        return None
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT "key" AS source_key, date AS attendance_date,
                       "Attendance" AS attendance_value, attendance_status,
                       module, lecture_name, activity AS activity_hours
                FROM public.kbc_attendance
                WHERE "ID" = %s AND "key" IS NOT NULL
                ORDER BY date DESC NULLS LAST, "key"
                """,
                [aptem_id],
            )
            return cursor.fetchall()


@require_GET
def attendance_options(request: HttpRequest) -> JsonResponse:
    """Every attendance row for the learner — attended AND absent — so the
    employee can add either and still see what the register recorded."""
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    if aptem_id is None:
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)

    rows = None
    source = "kbc_attendance"
    try:
        rows = _source_attendance_rows(aptem_id)
    except Exception:
        rows = None  # transient source failure (flaky DNS) — fall back below
    if rows is None:
        # Mirror fallback keeps the flow usable when the source is unreachable.
        source = "Last_audit-mirror"
        try:
            with _connection().cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT source_key, attendance_date, attendance_value,
                           attendance_status, module, lecture_name, activity_hours
                    FROM {LEARNER_ATTENDANCE}
                    WHERE aptem_id = %s
                    ORDER BY attendance_date DESC NULLS LAST, source_key
                    """,
                    [aptem_id],
                )
                rows = _dict_rows(cursor)
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not read attendance.", "details": str(error)},
                status=503,
            )
    options = []
    for row in rows:
        attended = row.get("attendance_value") == 1 or str(
            row.get("attendance_status") or ""
        ).lower() in {"present", "attended", "attend"}
        attendance_date = row.get("attendance_date")
        options.append({
            "source_key": row["source_key"],
            "source_ref": f"att:{row['source_key']}",
            "attendance_date": attendance_date.isoformat() if attendance_date else None,
            "lecture_name": row.get("lecture_name"),
            "module": row.get("module"),
            "attended": attended,
            "attendance_status": row.get("attendance_status") or ("Present" if attended else "Absent"),
            "activity_hours": _num(row.get("activity_hours")),
        })
    return JsonResponse({
        "aptem_id": aptem_id,
        "source": source,
        "count": len(options),
        "options": options,
    })


PATCHABLE_FIELDS = {
    "title", "activity_date", "month", "planned_hours", "actual_hours",
    "timestamp_label", "accepted",
}

# Awarding actual hours to an LMS activity the learner never finished marks it
# complete: the audit team evidenced the learning even though the LMS flag
# never flipped.  Clearing those hours puts the row back.  The swap is scoped
# to this ONE pair of notes, so a genuine LMS "completed", an Aptem
# assignment's own status word and attendance rows (NULL) are never rewritten.
LMS_INCOMPLETE_NOTE = "not_completed"
HOURS_COMPLETION_NOTE = "completed_by_hours"


def _completion_note_for_hours(note, actual_hours):
    """The completion note a row should carry for the hours it now claims."""
    if actual_hours and actual_hours > 0:
        return HOURS_COMPLETION_NOTE if note == LMS_INCOMPLETE_NOTE else note
    return LMS_INCOMPLETE_NOTE if note == HOURS_COMPLETION_NOTE else note


def _hours_completion_assignment(values):
    """SET fragment + params keeping ``completion_note`` in step with the hours.

    Returns ``("", [])`` when the patch does not touch ``actual_hours``.  The
    new hours must be passed again as parameters: inside an UPDATE, reading the
    column would still yield the OLD value.
    """
    if "actual_hours" not in values:
        return "", []
    hours = values["actual_hours"]
    fragment = (
        ", completion_note = CASE"
        f" WHEN %s > 0 AND completion_note = '{LMS_INCOMPLETE_NOTE}'"
        f" THEN '{HOURS_COMPLETION_NOTE}'"
        f" WHEN %s <= 0 AND completion_note = '{HOURS_COMPLETION_NOTE}'"
        f" THEN '{LMS_INCOMPLETE_NOTE}'"
        " ELSE completion_note END"
    )
    return fragment, [hours, hours]


def _validate_new_row(item):
    """Validate one create payload; shared by the single POST and the bulk save."""
    month = _valid_month(item.get("month"))
    category = str(item.get("category") or "").strip().lower()
    title = _valid_title(item.get("title"))
    if str(item.get("source_ref") or "").strip().startswith("la:"):
        category = _activity_category({"activity_type": category, "title": title})
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    source_ref, group_id, activity_id = _parse_source_ref(category, item.get("source_ref"))
    actual_hours = _valid_hours(item.get("actual_hours"), "actual_hours")
    return {
        "month": month,
        "category": category,
        "source_ref": source_ref,
        "group_id": group_id,
        "activity_id": activity_id,
        "title": title,
        "activity_date": _valid_date(item.get("activity_date")),
        "planned_hours": _valid_hours(item.get("planned_hours"), "planned_hours"),
        "actual_hours": actual_hours,
        "timestamp_label": str(item.get("timestamp_label") or "").strip()[:100],
        # A row added straight from the picker with hours already on it counts
        # as evidenced too — same rule as editing hours onto a saved row.
        "completion_note": _completion_note_for_hours(
            str(item.get("completion_note") or "").strip()[:200] or None, actual_hours,
        ),
        "accepted": item.get("accepted") is not False,
    }


def _validate_patch_values(patch):
    """Whitelist + validate PATCH fields; shared by PATCH and the bulk save."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch (object) is required")
    unknown = set(patch) - PATCHABLE_FIELDS
    if unknown:
        raise ValueError(f"patch cannot change: {', '.join(sorted(unknown))}")
    values = {}
    if "title" in patch:
        values["title"] = _valid_title(patch["title"])
    if "activity_date" in patch:
        values["activity_date"] = _valid_date(patch["activity_date"])
    if "month" in patch:
        values["month"] = _valid_month(patch["month"])
    # month and activity_date stay one pair: a dated row always lives on the
    # month its date belongs to. A date-only patch moves the row there; a
    # month that contradicts its own date is rejected outright.
    if values.get("activity_date") is not None:
        date_month = values["activity_date"].strftime("%Y-%m")
        if "month" in values and values["month"] != date_month:
            raise ValueError("activity_date must fall inside the patched month")
        if "month" not in values:
            values["month"] = _valid_month(date_month)
    if "planned_hours" in patch:
        values["planned_hours"] = _valid_hours(patch["planned_hours"], "planned_hours")
    if "actual_hours" in patch:
        values["actual_hours"] = _valid_hours(patch["actual_hours"], "actual_hours")
    if "timestamp_label" in patch:
        values["timestamp_label"] = str(patch["timestamp_label"] or "").strip()[:100]
    if "accepted" in patch:
        values["accepted"] = bool(patch["accepted"])
    return values


ROW_VALUES_PLACEHOLDER = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
INSERT_ROW_PREFIX = f"""
    INSERT INTO {MANUAL_ROWS} (
        aptem_id, learner_id, month, category, source_ref,
        group_id, activity_id, title, activity_date, activity_time,
        planned_hours, actual_hours, timestamp_label,
        completion_note, accepted, created_by
    ) VALUES """
INSERT_ROW_SQL = INSERT_ROW_PREFIX + ROW_VALUES_PLACEHOLDER


def _insert_params(aptem_id, learner_id, values, created_by):
    return [
        aptem_id, learner_id, values["month"], values["category"],
        values["source_ref"], values["group_id"], values["activity_id"],
        values["title"], values["activity_date"], values.get("activity_time"),
        values["planned_hours"], values["actual_hours"],
        values["timestamp_label"], values["completion_note"],
        values["accepted"], created_by,
    ]


@csrf_exempt
def rows(request: HttpRequest) -> JsonResponse:
    """List/create/update/soft-delete the employee-arranged activity rows."""
    if request.method == "GET":
        try:
            aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
        except (TypeError, ValueError):
            return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
        if aptem_id is None:
            return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
        month = (request.GET.get("month") or "").strip()
        conditions = ["aptem_id = %s", "deleted_at IS NULL"]
        params = [aptem_id]
        if month:
            conditions.append("month = %s")
            params.append(month)
        try:
            with _connection().cursor() as cursor:
                _ensure_manual_tables(cursor)
                cursor.execute(
                    f"""
                    SELECT {ROW_COLUMNS} FROM {MANUAL_ROWS}
                    WHERE {' AND '.join(conditions)}
                    ORDER BY activity_date NULLS LAST, id
                    """,
                    params,
                )
                row_data = _dict_rows(cursor)
                documents = _documents_by_row(
                    cursor, [int(row["id"]) for row in row_data
                             if row["category"] == "assignment"],
                )
                group_names = _group_names(cursor, [row.get("group_id") for row in row_data])
                attendance_modules = _attendance_modules(
                    cursor, [row.get("source_ref") for row in row_data
                             if row["category"] == "attendance"],
                )
                activity_durations = _activity_durations(
                    cursor, [row.get("activity_id") for row in row_data],
                )
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not read the manual ledger.", "details": str(error)},
                status=503,
            )
        items = [
            _row_payload(
                row,
                documents.get(int(row["id"])),
                source_course=group_names.get(row.get("group_id")),
                module=attendance_modules.get((row.get("source_ref") or "")[4:]),
                duration_minutes=activity_durations.get(row.get("activity_id")),
            )
            for row in row_data
        ]
        return JsonResponse({
            "aptem_id": aptem_id,
            "month": month or None,
            "count": len(items),
            "planned_sum": round(sum(item["planned_hours"] for item in items), 2),
            # Claimed hours = accepted rows only, matching the summary tiles.
            "actual_sum": round(sum(item["actual_hours"] for item in items if item["accepted"]), 2),
            "not_accepted_sum": round(sum(item["actual_hours"] for item in items if not item["accepted"]), 2),
            "rows": items,
        })

    if request.method not in {"POST", "PATCH", "DELETE"}:
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Request body must be JSON."}, status=400)

    if request.method == "POST":
        try:
            aptem_id = int(body.get("aptem_id"))
            month = _valid_month(body.get("month"))
            category = str(body.get("category") or "").strip().lower()
            if category not in CATEGORIES:
                raise ValueError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
            source_ref, group_id, activity_id = _parse_source_ref(category, body.get("source_ref"))
            title = _valid_title(body.get("title"))
            activity_date = _valid_date(body.get("activity_date"))
            planned = _valid_hours(body.get("planned_hours"), "planned_hours")
            actual = _valid_hours(body.get("actual_hours"), "actual_hours")
            timestamp_label = str(body.get("timestamp_label") or "").strip()[:100]
            completion_note = str(body.get("completion_note") or "").strip()[:200] or None
            accepted = body.get("accepted") is not False
        except (TypeError, ValueError) as error:
            return JsonResponse({"error": str(error) or "Invalid payload."}, status=400)
        try:
            with _connection().cursor() as cursor:
                learner = _load_learner(cursor, aptem_id)
                if not learner:
                    return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
                _ensure_manual_tables(cursor)
                cursor.execute(
                    f"""
                    INSERT INTO {MANUAL_ROWS} (
                        aptem_id, learner_id, month, category, source_ref,
                        group_id, activity_id, title, activity_date,
                        planned_hours, actual_hours, timestamp_label,
                        completion_note, accepted, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {ROW_COLUMNS}
                    """,
                    [
                        aptem_id, learner.get("learner_id"), month, category,
                        source_ref, group_id, activity_id, title, activity_date,
                        planned, actual, timestamp_label, completion_note,
                        accepted, _actor(body.get("created_by")),
                    ],
                )
                created = _dict_rows(cursor)[0]
        except IntegrityError:
            return JsonResponse(
                {"error": f"This activity is already on the learner's {_month_label(month)} report."},
                status=409,
            )
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not save the manual row.", "details": str(error)},
                status=503,
            )
        return JsonResponse(_row_payload(created), status=201)

    try:
        row_id = int(body.get("id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "id (int) is required"}, status=400)

    if request.method == "DELETE":
        try:
            with _connection().cursor() as cursor:
                _ensure_manual_tables(cursor)
                cursor.execute(
                    f"""
                    UPDATE {MANUAL_ROWS}
                    SET deleted_at = now(), updated_at = now(), updated_by = %s
                    WHERE id = %s AND deleted_at IS NULL
                    """,
                    [_actor(body.get("updated_by")), row_id],
                )
                if cursor.rowcount == 0:
                    return JsonResponse({"error": "Manual row was not found."}, status=404)
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not delete the manual row.", "details": str(error)},
                status=503,
            )
        return JsonResponse({"ok": True, "id": row_id})

    # PATCH
    patch = body.get("patch")
    if not isinstance(patch, dict) or not patch:
        return JsonResponse({"error": "patch (object) is required"}, status=400)
    unknown = set(patch) - PATCHABLE_FIELDS
    if unknown:
        return JsonResponse(
            {"error": f"patch cannot change: {', '.join(sorted(unknown))}",
             "editable": sorted(PATCHABLE_FIELDS)},
            status=400,
        )
    try:
        values = {}
        if "title" in patch:
            values["title"] = _valid_title(patch["title"])
        if "activity_date" in patch:
            values["activity_date"] = _valid_date(patch["activity_date"])
        if "month" in patch:
            values["month"] = _valid_month(patch["month"])
        if "planned_hours" in patch:
            values["planned_hours"] = _valid_hours(patch["planned_hours"], "planned_hours")
        if "actual_hours" in patch:
            values["actual_hours"] = _valid_hours(patch["actual_hours"], "actual_hours")
        if "timestamp_label" in patch:
            values["timestamp_label"] = str(patch["timestamp_label"] or "").strip()[:100]
        if "accepted" in patch:
            values["accepted"] = bool(patch["accepted"])
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    assignments = ", ".join(f"{field} = %s" for field in values)
    note_sql, note_params = _hours_completion_assignment(values)
    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"""
                UPDATE {MANUAL_ROWS}
                SET {assignments}{note_sql}, updated_by = %s, updated_at = now()
                WHERE id = %s AND deleted_at IS NULL
                RETURNING {ROW_COLUMNS}
                """,
                [*values.values(), *note_params, _actor(body.get("updated_by")), row_id],
            )
            updated = _dict_rows(cursor)
            if not updated:
                return JsonResponse({"error": "Manual row was not found."}, status=404)
            documents = _documents_by_row(cursor, [row_id])
    except IntegrityError:
        return JsonResponse(
            {"error": "The learner already has this activity on that month's report."},
            status=409,
        )
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not update the manual row.", "details": str(error)},
            status=503,
        )
    return JsonResponse(_row_payload(updated[0], documents.get(row_id)))


def _safe_filename(name):
    base = str(name or "").strip().replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(". ")
    return cleaned[:150] or "upload.bin"


_container_checked = False


def _ensure_assignment_container():
    """Create the container once per process; Azure ignores re-creates."""
    global _container_checked
    if _container_checked:
        return
    try:
        evidence_storage._service_client().create_container(ASSIGNMENT_CONTAINER)
    except Exception:
        pass  # already exists (or a transient failure the upload will surface)
    _container_checked = True


@csrf_exempt
def documents(request: HttpRequest) -> JsonResponse:
    """Uploaded evidence files for manual assignment rows.

    Blobs live in the ``learner-assignments`` container, foldered per learner,
    month and row: ``{aptem_id}/{YYYY-MM}/{row_id}/{filename}``.
    """
    if request.method == "GET":
        try:
            row_id = _as_int(request.GET.get("manual_activity_id"), minimum=1)
        except (TypeError, ValueError):
            return JsonResponse({"error": "manual_activity_id (int) is required"}, status=400)
        if row_id is None:
            return JsonResponse({"error": "manual_activity_id (int) is required"}, status=400)
        try:
            with _connection().cursor() as cursor:
                _ensure_manual_tables(cursor)
                grouped = _documents_by_row(cursor, [row_id])
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not read assignment documents.", "details": str(error)},
                status=503,
            )
        return JsonResponse({"manual_activity_id": row_id, "documents": grouped.get(row_id, [])})

    if request.method == "DELETE":
        try:
            body = json.loads(request.body or b"{}")
            doc_id = int(body.get("id"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "id (int) is required"}, status=400)
        try:
            with _connection().cursor() as cursor:
                _ensure_manual_tables(cursor)
                cursor.execute(
                    f"UPDATE {MANUAL_DOCS} SET deleted_at = now() "
                    f"WHERE id = %s AND deleted_at IS NULL",
                    [doc_id],
                )
                if cursor.rowcount == 0:
                    return JsonResponse({"error": "Document was not found."}, status=404)
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not delete the document.", "details": str(error)},
                status=503,
            )
        return JsonResponse({"ok": True, "id": doc_id})

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)

    if not evidence_storage.azure_configured():
        return JsonResponse({"error": "Azure storage is not configured."}, status=503)
    try:
        row_id = int(request.POST.get("manual_activity_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "manual_activity_id (int) is required"}, status=400)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "file is required"}, status=400)
    if upload.size > MAX_UPLOAD_BYTES:
        return JsonResponse({"error": "File is larger than 50 MB."}, status=400)

    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"""
                SELECT id, aptem_id, month, category FROM {MANUAL_ROWS}
                WHERE id = %s AND deleted_at IS NULL
                """,
                [row_id],
            )
            row = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the manual row.", "details": str(error)},
            status=503,
        )
    if not row:
        return JsonResponse({"error": "Manual row was not found."}, status=404)
    row = row[0]
    if row["category"] != "assignment":
        return JsonResponse({"error": "Only assignment rows take uploads."}, status=400)

    display_name = _safe_filename(upload.name)
    blob_name = f"{row['aptem_id']}/{row['month']}/{row_id}/{display_name}"
    try:
        _ensure_assignment_container()
        evidence_storage.upload_blob(
            upload, ASSIGNMENT_CONTAINER, blob_name,
            upload.content_type or "application/octet-stream",
        )
    except Exception as error:
        return JsonResponse(
            {"error": "Azure upload failed.", "details": str(error)}, status=502,
        )
    try:
        with _connection().cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {MANUAL_DOCS} (
                    manual_activity_id, aptem_id, month, container, blob_name,
                    display_name, content_type, size_bytes, uploaded_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, manual_activity_id, aptem_id, month, container,
                          blob_name, display_name, content_type, size_bytes,
                          uploaded_by, uploaded_at
                """,
                [
                    row_id, row["aptem_id"], row["month"], ASSIGNMENT_CONTAINER,
                    blob_name, display_name,
                    upload.content_type or "application/octet-stream",
                    upload.size, _actor(request.POST.get("uploaded_by")),
                ],
            )
            created = _dict_rows(cursor)[0]
    except DatabaseError as error:
        # keep storage tidy if the record could not be written
        try:
            evidence_storage.delete_blob(ASSIGNMENT_CONTAINER, blob_name)
        except Exception:
            pass
        return JsonResponse(
            {"error": "Could not record the uploaded document.", "details": str(error)},
            status=503,
        )
    return JsonResponse(_doc_payload(created), status=201)


def _ledger_participants(cursor, condition, params):
    cursor.execute(
        f"""
        SELECT m.id, m.aptem_id, m.learner_id, m.month, m.category,
               m.source_ref, m.group_id, m.activity_id, m.title,
               m.activity_date, m.planned_hours, m.actual_hours,
               m.timestamp_label, m.completion_note, m.accepted,
               m.created_by, m.updated_by, m.created_at, m.updated_at,
               l.learner_name
        FROM {MANUAL_ROWS} m
        LEFT JOIN {LEARNERS} l ON l.aptem_id = m.aptem_id
        WHERE m.deleted_at IS NULL AND {condition}
        ORDER BY lower(COALESCE(l.learner_name, '')), m.aptem_id, m.month
        """,
        params,
    )
    row_data = _dict_rows(cursor)
    documents = _documents_by_row(
        cursor, [int(row["id"]) for row in row_data if row["category"] == "assignment"],
    )
    participants = []
    for row in row_data:
        payload = _row_payload(row, documents.get(int(row["id"])))
        payload["learner_name"] = row.get("learner_name") or f"Aptem learner {row['aptem_id']}"
        participants.append(payload)
    return participants


def _mark_on_report(source_participants, participants):
    """Stamp each source participant with whether (and in which months) the
    activity already sits on their employee-arranged report."""
    months_by_aptem = {}
    for row in participants:
        months_by_aptem.setdefault(row["aptem_id"], set()).add(row["month"])
    for item in source_participants:
        months = sorted(months_by_aptem.get(item["aptem_id"]) or [])
        item["on_report"] = bool(months)
        item["report_months"] = months
    return source_participants


def _attendance_source_participants(cursor, session_key):
    """Everyone the register recorded for this session — attended AND absent —
    deduped per learner (duplicate register keys collapse, attended wins)."""
    cursor.execute(
        f"""
        SELECT la.aptem_id, la.attendance_value, la.attendance_status,
               l.learner_name
        FROM {LEARNER_ATTENDANCE} la
        LEFT JOIN {LEARNERS} l ON l.aptem_id = la.aptem_id
        WHERE substring(la.source_key from position('_' in la.source_key) + 1) = %s
        ORDER BY lower(COALESCE(l.learner_name, '')), la.aptem_id
        """,
        [session_key],
    )
    by_aptem = {}
    for row in _dict_rows(cursor):
        aptem_id = int(row["aptem_id"]) if row.get("aptem_id") is not None else None
        attended = row.get("attendance_value") == 1 or str(
            row.get("attendance_status") or ""
        ).lower() in {"present", "attended", "attend"}
        current = by_aptem.get(aptem_id)
        if current and (current["status"] == "attended" or not attended):
            continue
        by_aptem[aptem_id] = {
            "aptem_id": aptem_id,
            "learner_name": row.get("learner_name") or f"Aptem learner {aptem_id}",
            "status": "attended" if attended else "absent",
        }
    return list(by_aptem.values())


def _activity_source_participants(cursor, activity_id):
    """Every learner whose LMS group carries this activity, with their own
    completion state from ``activity_results``."""
    cursor.execute(
        f"""
        SELECT DISTINCT ON (gl.learner_id)
               gl.learner_id, l.aptem_id, l.learner_name,
               r.learner_id AS result_learner_id,
               r.status, r.video_completed, r.reading_viewed, r.quiz_passed
        FROM {GROUP_ACTIVITIES} ga
        JOIN {GROUP_LEARNERS} gl ON gl.group_id = ga.group_id
        LEFT JOIN {LEARNERS} l ON l.learner_id = gl.learner_id
        LEFT JOIN {ACTIVITY_RESULTS} r
          ON r.group_id = gl.group_id
         AND r.activity_id = ga.activity_id
         AND r.learner_id = gl.learner_id
        WHERE ga.activity_id = %s
        ORDER BY gl.learner_id, r.learner_id NULLS LAST
        """,
        [activity_id],
    )
    participants = []
    for row in _dict_rows(cursor):
        if row.get("result_learner_id") is None:
            status = "no_record"
        else:
            status = "completed" if _is_completed(row) else "not_completed"
        aptem_id = int(row["aptem_id"]) if row.get("aptem_id") is not None else None
        participants.append({
            "aptem_id": aptem_id,
            "learner_name": row.get("learner_name") or f"LMS learner {row['learner_id']}",
            "status": status,
        })
    participants.sort(key=lambda item: item["learner_name"].lower())
    return participants


def _companion_reading_part(cursor, group_id, activity_id, quiz_title):
    """The ONE reading sibling for a quiz-only activity: "P1 Q1: …" carries
    just the quiz (raw->reading is null at the source) — the lesson's P1 PDF
    lives in the P1-PPT / P1-TB activity right above it in the same group.
    Walk backwards from the quiz's position, stop at the previous lesson
    (another quiz or a different P-number), and return the best match:
    PPT slides first, textbook otherwise."""
    match = re.search(r"\bQ\s*-?\s*(\d+)", str(quiz_title or ""), re.I)
    if not match:
        return None
    number = int(match.group(1))
    if group_id is None:
        cursor.execute(
            f"SELECT group_id FROM {GROUP_ACTIVITIES} WHERE activity_id = %s ORDER BY group_id LIMIT 1",
            [activity_id],
        )
        found = cursor.fetchone()
        if not found:
            return None
        group_id = int(found[0])
    # Only the curriculum window right above the quiz — a lesson holds ~6-8
    # materials. A full-group scan (265 rows WITH their text bodies) took
    # ~5s per page load; this window runs in ~0.2s.
    cursor.execute(
        f"""
        WITH me AS (
            SELECT position FROM {GROUP_ACTIVITIES}
            WHERE group_id = %s AND activity_id = %s
        )
        SELECT a.activity_id, a.title, a.video_iframe_url, a.reading_type,
               a.reading_iframe_url,
               (a.reading_text_body IS NOT NULL AND length(a.reading_text_body) > 0) AS has_text
        FROM {GROUP_ACTIVITIES} ga
        JOIN {ACTIVITIES} a ON a.activity_id = ga.activity_id, me
        WHERE ga.group_id = %s
          AND ga.position < me.position AND ga.position >= me.position - 12
        ORDER BY ga.position DESC, a.activity_id
        """,
        [group_id, activity_id, group_id],
    )
    rows = _dict_rows(cursor)  # nearest sibling first
    # "P1-PPT-…", "TB-P1-…" and "P 1 …" all count as lesson-1 readings.
    same_number = re.compile(rf"^\s*(?:[A-Za-z]{{1,4}}-)?P\s*-?\s*{number}\b", re.I)
    any_p = re.compile(r"^\s*(?:[A-Za-z]{1,4}-)?P\s*-?\s*(\d+)\b", re.I)
    any_q = re.compile(r"^\s*(?:[A-Za-z]{1,4}-)?Q\s*-?\s*\d", re.I)
    candidates = []
    for row in rows:
        title = str(row.get("title") or "")
        p_match = any_p.match(title)
        if any_q.match(title) or (p_match and int(p_match.group(1)) != number):
            break  # crossed into the previous lesson
        if not same_number.match(title):
            continue
        if not row.get("reading_iframe_url") and not row.get("has_text"):
            continue  # video/audio siblings keep their own ledger rows
        candidates.append(row)
    if not candidates:
        return None
    best = next((row for row in candidates if "ppt" in str(row.get("title") or "").lower()), candidates[0])
    content_url = _activity_content_url(
        best.get("video_iframe_url"),
        best.get("reading_iframe_url"),
        best.get("reading_type"),
    )
    text_body = None
    if not content_url and best.get("has_text"):
        # Text bodies are heavy — fetch only the one that will render.
        cursor.execute(
            f"SELECT reading_text_body FROM {ACTIVITIES} WHERE activity_id = %s",
            [int(best["activity_id"])],
        )
        fetched = cursor.fetchone()
        text_body = fetched[0] if fetched else None
    return {
        "activity_id": int(best["activity_id"]),
        "title": str(best.get("title") or ""),
        "content_url": content_url,
        "reading_text_body": text_body,
        "reading_type": best.get("reading_type"),
        "quiz": None,
    }


@require_GET
def activity_ledger(request: HttpRequest) -> JsonResponse:
    """One activity's definition plus every employee-arranged row for it.

    ``ref`` is a manual row's ``source_ref`` (``la:…`` / ``att:…``) or
    ``row:<id>`` for source-less assignment rows.  Matching is deliberately
    wider than one manual row: content activities match on ``activity_id``
    across every group, attendance matches on the shared session key (the
    ``source_key`` minus its leading learner id), and assignments — having no
    shared source identity — show just their own row.

    ``source_participants`` lists everyone who actually did the activity at
    the source — the whole register session for attendance, every enrolled
    learner's completion for LMS content — independent of who has it filed on
    a monthly report.
    """
    ref = str(request.GET.get("ref") or "").strip()
    if not ref:
        return JsonResponse({"error": "ref is required"}, status=400)

    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)

            if ref.startswith("row:"):
                try:
                    row_id = int(ref[4:])
                except ValueError:
                    return JsonResponse({"error": "ref must look like row:<id>"}, status=400)
                participants = _ledger_participants(cursor, "m.id = %s", [row_id])
                if not participants:
                    return JsonResponse({"error": f"no manual row {row_id}"}, status=404)
                first = participants[0]
                return JsonResponse({
                    "ref": ref,
                    "category": first["category"],
                    "activity": {
                        "title": first["title"],
                        "activity_date": first["activity_date"],
                        "activity_type": first["category"],
                        "content_url": None,
                        "reading_text_body": None,
                        "quiz": None,
                    },
                    "participants": participants,
                    "source_participants": [],
                })

            if ref.startswith("rq:"):
                # rq:<group_id>:<activity_id>:<activity_id>… — a Reading+Quiz
                # bundle merged in the journal. Serve EVERY part's definition
                # (title + content + quiz) so the ledger shows all merged
                # content instead of nothing.
                try:
                    bundle_ids = [int(part) for part in ref.split(":")[2:]]
                    if not bundle_ids:
                        raise ValueError
                except ValueError:
                    return JsonResponse(
                        {"error": "ref must look like rq:<group>:<id>:<id>…"}, status=400,
                    )
                cursor.execute(
                    f"""
                    SELECT activity_id, activity_type, title, activity_date,
                           video_iframe_url, reading_type, reading_iframe_url,
                           raw #>> '{{audio,iframe_url}}' AS audio_iframe_url,
                           reading_text_body, quiz_id, quiz_body, quiz_questions,
                           quiz_maximum_score, quiz_passing_score,
                           {_duration_min_sql()} AS configured_duration_min
                    FROM {ACTIVITIES} WHERE activity_id = ANY(%s)
                    """,
                    [bundle_ids],
                )
                found = {int(row["activity_id"]): row for row in _dict_rows(cursor)}
                bundle = [found[i] for i in bundle_ids if i in found]
                if not bundle:
                    return JsonResponse(
                        {"error": f"no Last_audit activities for {ref}"}, status=404,
                    )
                participants = _ledger_participants(cursor, "m.source_ref = %s", [ref])
                source_participants = _mark_on_report(
                    _activity_source_participants(cursor, bundle_ids[0]), participants,
                )
                parts_payload = []
                for row in bundle:
                    questions = _json_list(row.get("quiz_questions"))
                    row_has_quiz = row.get("quiz_id") is not None or bool(questions)
                    parts_payload.append({
                        "activity_id": int(row["activity_id"]),
                        "title": row.get("title") or f"Activity {row['activity_id']}",
                        "content_url": _activity_content_url(
                            row.get("video_iframe_url"),
                            row.get("reading_iframe_url"),
                            row.get("reading_type"),
                            row.get("audio_iframe_url"),
                        ),
                        "reading_text_body": row.get("reading_text_body"),
                        "quiz": {
                            "description": row.get("quiz_body"),
                            "questions": questions,
                            "maximum_score": _num(row.get("quiz_maximum_score")),
                            "passing_score": _num(row.get("quiz_passing_score")),
                        } if row_has_quiz else None,
                    })
                first = bundle[0]
                first_date = first.get("activity_date")
                return JsonResponse({
                    "ref": ref,
                    "category": "reading+quiz",
                    "activity": {
                        "activity_id": int(first["activity_id"]),
                        "title": " + ".join(part["title"] for part in parts_payload),
                        "activity_date": first_date.isoformat() if first_date else None,
                        "activity_type": "reading+quiz",
                        "content_url": next(
                            (part["content_url"] for part in parts_payload if part["content_url"]), None,
                        ),
                        "reading_text_body": first.get("reading_text_body"),
                        "configured_duration_minutes": _num(first.get("configured_duration_min")),
                        "quiz": next((part["quiz"] for part in parts_payload if part["quiz"]), None),
                        "parts": parts_payload,
                    },
                    "participants": participants,
                    "source_participants": source_participants,
                })

            if ref.startswith("att:"):
                session_key = _session_key(ref)
                if not session_key:
                    return JsonResponse(
                        {"error": "ref must look like att:<id>_<YYYY-MM-DD>_<lecture>"},
                        status=400,
                    )
                cursor.execute(
                    f"""
                    SELECT attendance_date, lecture_name, module
                    FROM {LEARNER_ATTENDANCE}
                    WHERE substring(source_key from position('_' in source_key) + 1) = %s
                    LIMIT 1
                    """,
                    [session_key],
                )
                session = _dict_rows(cursor)
                participants = _ledger_participants(
                    cursor,
                    "m.category = 'attendance' AND "
                    "substring(substr(m.source_ref, 5) "
                    "from position('_' in substr(m.source_ref, 5)) + 1) = %s",
                    [session_key],
                )
                if not session and not participants:
                    return JsonResponse(
                        {"error": f"No attendance session for '{session_key}'."}, status=404,
                    )
                if session:
                    session = session[0]
                    session_date = session.get("attendance_date")
                    title = session.get("lecture_name") or session.get("module") or "Attendance session"
                    module = session.get("module")
                    date_iso = session_date.isoformat() if session_date else None
                else:
                    # Options now come from the live register, so a manual row
                    # can reference a session the mirror has not synced yet.
                    first = participants[0]
                    title = first["title"]
                    module = None
                    date_iso = first["activity_date"] or (
                        session_key[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", session_key) else None
                    )
                source_participants = _mark_on_report(
                    _attendance_source_participants(cursor, session_key), participants,
                )
                return JsonResponse({
                    "ref": ref,
                    "category": "attendance",
                    "activity": {
                        "title": title,
                        "activity_date": date_iso,
                        "activity_type": "attendance",
                        "module": module,
                        "content_url": None,
                        "reading_text_body": None,
                        "quiz": None,
                    },
                    "participants": participants,
                    "source_participants": source_participants,
                })

            # la:<group_id>:<activity_id> or a bare activity id
            parts = ref.split(":")
            try:
                activity_id = int(parts[2]) if ref.startswith("la:") and len(parts) == 3 else int(ref)
            except ValueError:
                return JsonResponse({"error": "ref is not a recognised activity reference"}, status=400)
            cursor.execute(
                f"""
                SELECT activity_id, activity_type, title, activity_date,
                       video_iframe_url, reading_type, reading_iframe_url,
                       raw #>> '{{audio,iframe_url}}' AS audio_iframe_url,
                       reading_text_body, quiz_id, quiz_body, quiz_questions,
                       quiz_maximum_score, quiz_passing_score,
                       {_duration_min_sql()} AS configured_duration_min
                FROM {ACTIVITIES} WHERE activity_id = %s
                """,
                [activity_id],
            )
            definition = _dict_rows(cursor)
            if not definition:
                return JsonResponse({"error": f"no Last_audit activity {activity_id}"}, status=404)
            definition = definition[0]
            participants = _ledger_participants(cursor, "m.activity_id = %s", [activity_id])
            source_participants = _mark_on_report(
                _activity_source_participants(cursor, activity_id), participants,
            )
            # A quiz-only activity ("P1 Q1: …") also shows its lesson's PDF:
            # the single best reading sibling renders above the quiz.
            companion = None
            has_own_content = bool(
                definition.get("video_iframe_url")
                or definition.get("reading_iframe_url")
                or definition.get("reading_text_body")
                or definition.get("audio_iframe_url")
            )
            has_own_quiz = (
                definition.get("quiz_id") is not None
                or bool(_json_list(definition.get("quiz_questions")))
            )
            if has_own_quiz and not has_own_content:
                ref_group_id = int(parts[1]) if ref.startswith("la:") and len(parts) == 3 else None
                companion = _companion_reading_part(
                    cursor, ref_group_id, activity_id, definition.get("title"),
                )
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the activity ledger.", "details": str(error)},
            status=503,
        )

    questions = _json_list(definition.get("quiz_questions"))
    has_quiz = definition.get("quiz_id") is not None or bool(questions)
    activity_date = definition.get("activity_date")
    quiz_payload = {
        "description": definition.get("quiz_body"),
        "questions": questions,
        "maximum_score": _num(definition.get("quiz_maximum_score")),
        "passing_score": _num(definition.get("quiz_passing_score")),
    } if has_quiz else None
    activity_payload = {
        "activity_id": int(definition["activity_id"]),
        "title": definition.get("title") or f"Activity {activity_id}",
        "activity_date": activity_date.isoformat() if activity_date else None,
        "activity_type": definition.get("activity_type"),
        "content_url": _activity_content_url(
            definition.get("video_iframe_url"),
            definition.get("reading_iframe_url"),
            definition.get("reading_type"),
            definition.get("audio_iframe_url"),
        ),
        "reading_text_body": definition.get("reading_text_body"),
        "configured_duration_minutes": _num(definition.get("configured_duration_min")),
        "quiz": quiz_payload,
    }
    if companion:
        # The lesson's PDF renders above the quiz via the ledger's parts UI.
        activity_payload["parts"] = [companion, {
            "activity_id": int(definition["activity_id"]),
            "title": definition.get("title") or f"Activity {activity_id}",
            "content_url": None,
            "reading_text_body": None,
            "quiz": quiz_payload,
        }]
    return JsonResponse({
        "ref": ref,
        "category": _activity_category(definition),
        "activity": activity_payload,
        "participants": participants,
        "source_participants": source_participants,
    })


# --- retrieve + bulk save (the journal's draft workflow) ---------------------

def _collect_import_candidates(cursor, aptem_id, month, learner):
    """Gather everything retrievable from Last_audit for one learner-month.

    Shared by the retrieve-modal listing (``import_candidates``) and the
    journal's month auto-import (``rows_auto_import``) so both always agree on
    what a month contains.  Returns a dict with ``attendance_source``,
    ``attendance``, ``activities`` and ``already_added``.  DatabaseError is
    left to the caller.
    """
    attendance_rows = None
    attendance_source = "kbc_attendance"
    try:
        attendance_rows = _source_attendance_rows(aptem_id)
    except Exception:
        attendance_rows = None
    if attendance_rows is None:
        attendance_source = "Last_audit-mirror"
        cursor.execute(
            f"""
            SELECT source_key, attendance_date, attendance_value,
                   attendance_status, module, lecture_name
            FROM {LEARNER_ATTENDANCE}
            WHERE aptem_id = %s
            ORDER BY attendance_date, source_key
            """,
            [aptem_id],
        )
        attendance_rows = _dict_rows(cursor)

    content_rows = []
    if learner.get("learner_id") is not None:
        cursor.execute(
            f"""
            SELECT DISTINCT ON (gl.group_id, a.activity_id)
                   gl.group_id, g.group_name, a.activity_id, a.title, a.activity_date,
                   lower(a.activity_type) AS activity_type,
                   a.quiz_id, a.quiz_questions, a.reading_type,
                   a.reading_iframe_url, a.reading_text_body,
                   {_duration_min_sql('a')} AS configured_duration_min,
                   r.status, r.video_completed, r.reading_viewed,
                   r.quiz_passed
            FROM {GROUP_LEARNERS} gl
            JOIN {GROUPS} g ON g.group_id = gl.group_id
            JOIN {GROUP_ACTIVITIES} ga ON ga.group_id = gl.group_id
            JOIN {ACTIVITIES} a ON a.activity_id = ga.activity_id
            LEFT JOIN {ACTIVITY_RESULTS} r
              ON r.group_id = gl.group_id
             AND r.activity_id = a.activity_id
             AND r.learner_id = gl.learner_id
            WHERE gl.learner_id = %s
              AND lower(a.activity_type) IN ('video', 'audio', 'reading+quiz')
            ORDER BY gl.group_id, a.activity_id, r.learner_id NULLS LAST
            """,
            [learner["learner_id"]],
        )
        content_rows = _dict_rows(cursor)

    _ensure_manual_tables(cursor)
    group_ids = sorted({int(row["group_id"]) for row in content_rows if row.get("group_id") is not None})
    pairs = []
    if group_ids:
        cursor.execute(
            f"SELECT id, group_id, reading_activity_id, quiz_activity_id FROM {READING_QUIZ_PAIRS} WHERE group_id = ANY(%s)",
            [group_ids],
        )
        pairs = _dict_rows(cursor)
    cursor.execute(
        f"""
        SELECT source_ref FROM {MANUAL_ROWS}
        WHERE aptem_id = %s AND month = %s
          AND deleted_at IS NULL AND source_ref IS NOT NULL
        """,
        [aptem_id, month],
    )
    already_added = sorted({row[0] for row in cursor.fetchall()})

    # The register sometimes records one session twice under different keys;
    # collapse duplicates (same date + lecture) into one candidate, keeping
    # the copy already on the report so it cannot be added a second time.
    already_added_set = set(already_added)

    def _attendance_priority(item):
        return (
            2 if item["source_ref"] in already_added_set else 0
        ) + (1 if item["attended"] else 0)

    attendance = []
    session_index = {}
    for row in attendance_rows:
        attendance_date = row.get("attendance_date")
        date_iso = attendance_date.isoformat() if attendance_date else None
        if not date_iso or not date_iso.startswith(month):
            continue
        attended = row.get("attendance_value") == 1 or str(
            row.get("attendance_status") or ""
        ).lower() in {"present", "attended", "attend"}
        title = row.get("lecture_name") or row.get("module") or "Attendance session"
        candidate = {
            "source_ref": f"att:{row['source_key']}",
            "category": "attendance",
            "title": title,
            "group_name": row.get("module") or "Attendance",
            "activity_date": date_iso,
            "attended": attended,
            "timestamp_label": "attended" if attended else "not attended",
        }
        session_key = (date_iso, " ".join(title.lower().split()))
        existing_at = session_index.get(session_key)
        if existing_at is None:
            session_index[session_key] = len(attendance)
            attendance.append(candidate)
        elif _attendance_priority(candidate) > _attendance_priority(attendance[existing_at]):
            attendance[existing_at] = candidate

    activities = []
    for row in content_rows:
        activity_date = row.get("activity_date")
        activities.append({
            "group_id": int(row["group_id"]),
            "group_name": row.get("group_name") or f"Group {row['group_id']}",
            "activity_id": int(row["activity_id"]),
            "source_ref": f"la:{int(row['group_id'])}:{int(row['activity_id'])}",
            "category": _activity_category(row),
            "title": row.get("title") or f"Activity {row['activity_id']}",
            "activity_date": activity_date.isoformat() if activity_date else None,
            "duration_minutes": _num(row.get("configured_duration_min")),
            "completion": {"state": "completed" if _is_completed(row) else "not_completed"},
        })

    # Replace explicitly linked rows with one selectable bundle. Multiple rows
    # sharing reading_activity_id are the members of one multi-item bundle;
    # legacy two-item pairs naturally remain a bundle with one member.
    by_key = {(item["group_id"], item["activity_id"]): item for item in activities}
    consumed = set()
    bundles = []
    stored_bundles = {}
    for pair in pairs:
        key = (int(pair["group_id"]), int(pair["reading_activity_id"]))
        stored_bundles.setdefault(key, []).append(int(pair["quiz_activity_id"]))
    for (group_id, anchor_id), member_ids in stored_bundles.items():
        activity_ids = [anchor_id, *sorted(set(member_ids))]
        members = [by_key.get((group_id, activity_id)) for activity_id in activity_ids]
        if any(member is None for member in members):
            continue
        members = [member for member in members if member is not None]
        consumed.update((group_id, activity_id) for activity_id in activity_ids)
        states = [member["completion"]["state"] for member in members]
        bundles.append({
            "source_ref": f"rq:{group_id}:" + ":".join(str(activity_id) for activity_id in activity_ids),
            "category": "reading+quiz",
            "title": " + ".join(member["title"] for member in members),
            "activity_date": max(filter(None, [member.get("activity_date") for member in members]), default=None),
            "duration_minutes": None,
            "completion": {"state": "completed" if all(state == "completed" for state in states) else "not_completed"},
            "group_id": group_id,
            "group_name": members[0].get("group_name") or f"Group {group_id}",
            "activity_id": anchor_id,
            "pair": {"anchor_activity_id": anchor_id, "activity_ids": activity_ids,
                     "titles": [member["title"] for member in members]},
        })
    activities = [item for item in activities if (item["group_id"], item["activity_id"]) not in consumed] + bundles

    # EVERY Aptem assignment dated in the month — whatever its status — with
    # the source's own planned hours and evidenced OTJH time, so the employee
    # can retrieve any of them (auto-import still files only completed ones).
    assignments = []
    try:
        for item in _fetch_assignment_items(aptem_id, include_evidence=False):
            date_iso = str(item.get("relevant_date") or "")[:10]
            if not date_iso.startswith(month):
                continue
            source_id = str(item.get("source_id") or "").strip()
            if not source_id:
                continue
            status = str(item.get("status") or "").strip() or "Unknown"
            assignments.append({
                "source_ref": f"asg:{source_id}",
                "category": "assignment",
                "title": str(item.get("activity_name") or "Assignment")[:500],
                "group_name": str(item.get("type") or "Assignment"),
                "activity_date": date_iso or None,
                "planned_hours": _clamped_hours(item.get("planned_hours")),
                "actual_hours": _clamped_hours(item.get("actual_hours")),
                "status": status,
                "completion": {"state": "completed" if status.lower() == "completed" else "not_completed"},
            })
    except Exception:
        assignments = []

    return {
        "attendance_source": attendance_source,
        "attendance": attendance,
        "activities": activities,
        "assignments": assignments,
        "already_added": already_added,
    }


@require_GET
def document_url(request: HttpRequest) -> JsonResponse:
    """A short-lived read URL for one manual/evidence document, so the /doc
    preview page inside the system can embed it (PDF links land there too)."""
    try:
        doc_id = int(request.GET.get("id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "id (int) is required"}, status=400)
    try:
        with _connection().cursor() as cursor:
            _ensure_manual_tables(cursor)
            cursor.execute(
                f"""
                SELECT id, container, blob_name, display_name, content_type
                FROM {MANUAL_DOCS} WHERE id = %s AND deleted_at IS NULL
                """,
                [doc_id],
            )
            found = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the document.", "details": str(error)},
            status=503,
        )
    if not found:
        return JsonResponse({"error": f"no document {doc_id}"}, status=404)
    doc = found[0]
    url = None
    if evidence_storage.azure_configured():
        try:
            url = evidence_storage.get_read_sas(doc["container"], doc["blob_name"])
        except Exception:
            url = None
    if not url:
        return JsonResponse({"error": "The document store is not reachable."}, status=503)
    return JsonResponse({
        "id": int(doc["id"]),
        "name": doc["display_name"],
        "content_type": doc.get("content_type"),
        "url": url,
    })


@require_GET
def import_candidates(request: HttpRequest) -> JsonResponse:
    """Everything retrievable from Last_audit for one learner-month, so the
    journal can stage it as draft rows.

    Attendance comes from the live register (mirror fallback); content
    activities come from the complete catalogues of the LMS groups in which
    the learner is enrolled.  The learner's own ``activity_results`` row is
    left-joined only to supply completion state, so untouched course content
    remains selectable just like the Manual Audit course picker. Hours are not
    imported; assignments remain manual-entry.
    """
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    month = str(request.GET.get("month") or "").strip()
    if aptem_id is None or not MONTH_RE.match(month):
        return JsonResponse({"error": "aptem_id (int) and month (YYYY-MM) are required"}, status=400)

    try:
        with _connection().cursor() as cursor:
            learner = _load_learner(cursor, aptem_id)
            if not learner:
                return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
            candidates = _collect_import_candidates(cursor, aptem_id, month, learner)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read the import candidates.", "details": str(error)},
            status=503,
        )

    return JsonResponse({"aptem_id": aptem_id, "month": month, **candidates})


def _month_is_signed_off(aptem_id, month):
    """A month is locked once BOTH roles hold a live signature — mirrors the
    journal's own "signed" state (removal upserts an empty signature, so test
    emptiness, not row existence). Errors read as "not signed" so a missing
    table never blocks the journal."""
    # Transition signatures attest a review and must not lock shared imports.
    try:
        with connections[resolve("enrolment")].cursor() as cursor:
            cursor.execute(
                """
                SELECT count(DISTINCT signer_role)
                FROM "Audit"."monthly_audit_signoffs"
                WHERE learner_id = %s AND report_month = %s
                  AND coalesce(signature_data, '') <> ''
                  AND coalesce(audit_version, '') <> 'old-otjh-transition-v1'
                """,
                [str(aptem_id), month],
            )
            row = cursor.fetchone()
            return bool(row and int(row[0]) >= 2)
    except Exception:
        return False


def _clamped_hours(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(min(50.0, max(0.0, number)), 4)


def _report_display_name(evidence_label, report_blob):
    """Assessment-report label carrying the REPORT's own file extension.
    Reusing the learner file's extension mislabels the (PDF) report as .docx,
    which routes the /doc preview to the Office viewer and breaks it."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", str(evidence_label or "")).strip()
    match = re.search(r"\.[A-Za-z0-9]{1,6}$", str(report_blob or ""))
    return f"Assessment report - {stem}{match.group(0) if match else ''}"


def _attach_assignment_evidence_docs(cursor, aptem_id, month):
    """File the Azure-mirrored Aptem evidence files (submission + assessor
    report) as documents on the month's assignment rows, keyed by the
    ``asg:<component_id>`` source ref. Idempotent — existing documents are
    never duplicated — and it also heals rows added before this feature."""
    cursor.execute(
        f"""
        SELECT id, source_ref FROM {MANUAL_ROWS}
        WHERE aptem_id = %s AND month = %s AND deleted_at IS NULL
          AND category = 'assignment' AND source_ref LIKE 'asg:%%'
        """,
        [aptem_id, month],
    )
    rows_by_component = {}
    for row_id, source_ref in cursor.fetchall():
        try:
            rows_by_component[int(str(source_ref).split(":", 1)[1])] = int(row_id)
        except (IndexError, ValueError):
            continue
    if not rows_by_component:
        return 0
    cursor.execute(
        f"""
        SELECT component_id, evidence_id, evidence_name, file_blob, report_blob
        FROM {EVIDENCE_ITEMS}
        WHERE learner_id = %s AND component_id = ANY(%s)
          AND (file_blob IS NOT NULL OR report_blob IS NOT NULL)
        ORDER BY evidence_id
        """,
        [aptem_id, list(rows_by_component)],
    )
    inserted = 0
    for item in _dict_rows(cursor):
        row_id = rows_by_component.get(int(item["component_id"]) if item.get("component_id") is not None else -1)
        if row_id is None:
            continue
        evidence_label = str(item.get("evidence_name") or f"Evidence {item.get('evidence_id')}")
        for blob, label in (
            (item.get("file_blob"), evidence_label),
            (item.get("report_blob"), _report_display_name(evidence_label, item.get("report_blob"))),
        ):
            if not blob:
                continue
            cursor.execute(
                f"""
                INSERT INTO {MANUAL_DOCS} (
                    manual_activity_id, aptem_id, month, container,
                    blob_name, display_name, uploaded_by
                )
                SELECT %s, %s, %s, %s, %s, %s, 'aptem-evidence'
                WHERE NOT EXISTS (
                    SELECT 1 FROM {MANUAL_DOCS}
                    WHERE manual_activity_id = %s AND blob_name = %s AND deleted_at IS NULL
                )
                """,
                [row_id, aptem_id, month, EVIDENCE_CONTAINER, blob, label[:200], row_id, blob],
            )
            inserted += cursor.rowcount
    return inserted


_SOURCE_TIME_RE = re.compile(r"[T ](\d{2}):(\d{2}):(\d{2})")


def _readable_status(status):
    """Aptem ships CamelCase status words ("NotStarted", "EvidenceRequired");
    store them as human words so the journal chip reads naturally."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(status or "").strip())
    return " ".join(text.split()).lower() or "unknown"


def _submission_time(item):
    """The genuine submission clock time (HH:MM) when the source recorded one.
    Due/completed dates arrive as midnight stamps, so only a non-midnight
    timestamp counts as a real time; date-only sources return None."""
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    inner = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    for value in (
        raw.get("last_submission_date"), inner.get("LastSubmissionDate"),
        raw.get("completed_date"), inner.get("CompletedDate"),
        raw.get("due_date"), inner.get("DueDate"),
    ):
        match = _SOURCE_TIME_RE.search(str(value or ""))
        if match and (match.group(1), match.group(2), match.group(3)) != ("00", "00", "00"):
            return f"{match.group(1)}:{match.group(2)}"
    return None


def _assignment_import_values(aptem_id, month):
    """EVERY Aptem assignment dated inside the month — whatever its status —
    shaped as manual-row create values. Dates follow the audit workspace's
    ``relevant_date`` (due → submission → completed) so both screens bucket an
    assignment into the same month; hours come from the source (planned hours
    and the evidenced OTJH minutes), the source status lands in
    ``completion_note`` and the real submission clock time in
    ``activity_time``."""
    values = []
    for item in _fetch_assignment_items(aptem_id, include_evidence=False):
        date_iso = str(item.get("relevant_date") or "")[:10]
        if not date_iso.startswith(month):
            continue
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        status = _readable_status(item.get("status"))
        values.append({
            "month": month,
            "category": "assignment",
            "source_ref": f"asg:{source_id}",
            "group_id": None,
            "activity_id": None,
            "title": _valid_title(item.get("activity_name") or "Assignment"),
            "activity_date": _valid_date(date_iso),
            "activity_time": _submission_time(item),
            "planned_hours": _clamped_hours(item.get("planned_hours")),
            "actual_hours": _clamped_hours(item.get("actual_hours")),
            "timestamp_label": "input",
            "completion_note": status,
            "accepted": True,
        })
    return values


@csrf_exempt
def rows_auto_import(request: HttpRequest) -> JsonResponse:
    """Fill one learner-month with everything the sources already hold, so the
    journal opens pre-arranged and the employee only adds what is missing.

    Inserted per month: every register attendance session (attended
    2.5h/2.5h, absent 2.5h/0h), EVERY LMS activity dated inside the month —
    completed or not, the row's completion note carries the learner's real
    state (0h/0h — hours stay the employee's call) — and the learner's
    completed Aptem assignments (hours from the source).

    Idempotent by design: a source_ref ever filed for the month — even one the
    employee later deleted — is never re-inserted, so deletions stay
    respected; the live unique index guards races on top. Signed-off months
    and months past the ledger cap are left untouched.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body or b"{}")
        aptem_id = int(body.get("aptem_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    try:
        month = _valid_month(body.get("month"))
    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    actor = _actor(body.get("created_by")) or "auto-import"

    if _month_is_signed_off(aptem_id, month):
        return JsonResponse({
            "ok": True, "aptem_id": aptem_id, "month": month,
            "attendance_source": None, "created": 0, "skipped_existing": 0,
            "locked": True,
        })

    alias = resolve(CONNECTION_ALIAS)
    created = skipped_existing = 0
    attendance_source = None
    try:
        with transaction.atomic(using=alias):
            with connections[alias].cursor() as cursor:
                learner = _load_learner(cursor, aptem_id)
                if not learner:
                    return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
                candidates = _collect_import_candidates(cursor, aptem_id, month, learner)
                attendance_source = candidates["attendance_source"]

                pending = []
                for item in candidates["attendance"]:
                    pending.append({
                        "month": month,
                        "category": "attendance",
                        "source_ref": item["source_ref"],
                        "group_id": None,
                        "activity_id": None,
                        "title": _valid_title(item["title"]),
                        "activity_date": _valid_date(item["activity_date"]),
                        "planned_hours": ATTENDANCE_SESSION_HOURS,
                        "actual_hours": ATTENDANCE_SESSION_HOURS if item["attended"] else 0.0,
                        "timestamp_label": item["timestamp_label"],
                        "completion_note": None,
                        "accepted": True,
                    })
                for item in candidates["activities"]:
                    date_iso = item.get("activity_date") or ""
                    if not date_iso.startswith(month):
                        continue
                    # Only what the learner actually COMPLETED is filed
                    # automatically. Activities the LMS shows as unfinished stay
                    # out of the report and remain offered in "Add" → Retrieve,
                    # so filing them is always the employee's own decision.
                    if item["completion"]["state"] != "completed":
                        continue
                    pending.append({
                        "month": month,
                        "category": item["category"],
                        "source_ref": item["source_ref"],
                        "group_id": item.get("group_id"),
                        "activity_id": item.get("activity_id"),
                        "title": _valid_title(item["title"]),
                        "activity_date": _valid_date(date_iso),
                        "planned_hours": 0.0,
                        "actual_hours": 0.0,
                        "timestamp_label": "input",
                        "completion_note": item["completion"]["state"],
                        "accepted": True,
                    })
                pending.extend(_assignment_import_values(aptem_id, month))

                # Refs ever filed for this learner — deleted rows included, ANY
                # month — stay out: re-adding what an employee removed would
                # undo their work, and a row moved onto another month must not
                # reappear in its source-date month as a duplicate.
                cursor.execute(
                    f"""
                    SELECT source_ref FROM {MANUAL_ROWS}
                    WHERE aptem_id = %s AND source_ref IS NOT NULL
                    """,
                    [aptem_id],
                )
                ever_filed = {row[0] for row in cursor.fetchall()}

                seen = set()
                batch = []
                for values in pending:
                    ref = values["source_ref"]
                    if ref in ever_filed or ref in seen:
                        skipped_existing += 1
                        continue
                    seen.add(ref)
                    batch.append(values)
                if batch:
                    # One multi-VALUES statement: a month is dozens of rows and
                    # the database is remote, so per-row round trips dominate.
                    params = []
                    for values in batch:
                        params.extend(_insert_params(aptem_id, learner.get("learner_id"), values, actor))
                    cursor.execute(
                        INSERT_ROW_PREFIX
                        + ", ".join([ROW_VALUES_PLACEHOLDER] * len(batch))
                        + """
                        ON CONFLICT (aptem_id, month, source_ref)
                            WHERE deleted_at IS NULL AND source_ref IS NOT NULL
                            DO NOTHING
                        """,
                        params,
                    )
                    created = cursor.rowcount
                    skipped_existing += len(batch) - created
                # Keep UNTOUCHED assignment rows in step with the source:
                # status, submission time and hours refresh on every open.
                # "Untouched" = never edited by a human (updated_at still equals
                # created_at); the refresh deliberately leaves updated_at alone
                # so the row stays refreshable, and any employee edit freezes it.
                for values in pending:
                    if values["category"] != "assignment":
                        continue
                    cursor.execute(
                        f"""
                        UPDATE {MANUAL_ROWS}
                        SET completion_note = %s, activity_time = %s,
                            planned_hours = %s, actual_hours = %s,
                            updated_by = 'auto-refresh'
                        WHERE aptem_id = %s AND month = %s AND source_ref = %s
                          AND deleted_at IS NULL
                          AND (updated_by IS NULL OR updated_by = 'auto-refresh')
                          AND updated_at = created_at
                          AND (completion_note IS DISTINCT FROM %s
                               OR activity_time IS DISTINCT FROM %s::time
                               OR planned_hours IS DISTINCT FROM %s::numeric
                               OR actual_hours IS DISTINCT FROM %s::numeric)
                        """,
                        [
                            values["completion_note"], values.get("activity_time"),
                            values["planned_hours"], values["actual_hours"],
                            aptem_id, month, values["source_ref"],
                            values["completion_note"], values.get("activity_time"),
                            values["planned_hours"], values["actual_hours"],
                        ],
                    )
                # Give every assignment row its Azure-mirrored evidence files
                # as documents (also heals rows filed before this feature).
                _attach_assignment_evidence_docs(cursor, aptem_id, month)
    except IntegrityError as error:
        return JsonResponse(
            {"error": "The auto-import collided with an existing row.", "details": str(error)},
            status=409,
        )
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not auto-import the month.", "details": str(error)},
            status=503,
        )
    return JsonResponse({
        "ok": True,
        "aptem_id": aptem_id,
        "month": month,
        "attendance_source": attendance_source,
        "created": created,
        "skipped_existing": skipped_existing,
        "locked": False,
    })


@csrf_exempt
def rows_bulk(request: HttpRequest) -> JsonResponse:
    """Persist the journal's draft in one transaction: creates (retrieved or
    typed rows), whitelisted updates and soft deletes together.  Creates that
    collide with the live unique (aptem_id, month, source_ref) guard are
    skipped and reported rather than failing the batch."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        body = json.loads(request.body or b"{}")
        aptem_id = int(body.get("aptem_id"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)
    actor = _actor(body.get("updated_by") or body.get("created_by"))
    creates = body.get("creates") or []
    updates = body.get("updates") or []
    deletes = body.get("deletes") or []
    if not isinstance(creates, list) or not isinstance(updates, list) or not isinstance(deletes, list):
        return JsonResponse({"error": "creates, updates and deletes must be lists"}, status=400)

    validated_creates = []
    try:
        for index, item in enumerate(creates):
            values = _validate_new_row(item)
            validated_creates.append((str(item.get("key") or f"create-{index}"), values))
        validated_updates = []
        for item in updates:
            row_id = int(item.get("id"))
            validated_updates.append((row_id, _validate_patch_values(item.get("patch"))))
        delete_ids = [int(value) for value in deletes]
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": str(error) or "Invalid payload."}, status=400)

    alias = resolve(CONNECTION_ALIAS)
    created, skipped, missing, conflicts = [], [], [], []
    updated_count = deleted_count = 0
    try:
        with transaction.atomic(using=alias):
            with connections[alias].cursor() as cursor:
                learner = _load_learner(cursor, aptem_id)
                if not learner:
                    return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
                _ensure_manual_tables(cursor)
                for key, values in validated_creates:
                    cursor.execute(
                        INSERT_ROW_SQL
                        + f"""
                        ON CONFLICT (aptem_id, month, source_ref)
                            WHERE deleted_at IS NULL AND source_ref IS NOT NULL
                            DO NOTHING
                        RETURNING {ROW_COLUMNS}
                        """,
                        _insert_params(aptem_id, learner.get("learner_id"), values, actor),
                    )
                    inserted = _dict_rows(cursor)
                    if inserted:
                        created.append({"key": key, "row": _row_payload(inserted[0])})
                    else:
                        skipped.append({"key": key, "source_ref": values["source_ref"]})
                for row_id, values in validated_updates:
                    if not values:
                        continue
                    if "month" in values:
                        # Moving a sourced row onto a month that already lists
                        # the same source_ref would trip the live unique index
                        # and roll back the whole save — skip it and report it
                        # instead, so the rest of the draft still lands.
                        cursor.execute(
                            f"""
                            SELECT 1 FROM {MANUAL_ROWS} other
                            WHERE other.aptem_id = %s AND other.month = %s
                              AND other.deleted_at IS NULL AND other.id <> %s
                              AND other.source_ref IS NOT NULL
                              AND other.source_ref = (
                                  SELECT source_ref FROM {MANUAL_ROWS} WHERE id = %s
                              )
                            """,
                            [aptem_id, values["month"], row_id, row_id],
                        )
                        if cursor.fetchone():
                            conflicts.append(row_id)
                            continue
                    assignments = ", ".join(f"{field} = %s" for field in values)
                    note_sql, note_params = _hours_completion_assignment(values)
                    cursor.execute(
                        f"""
                        UPDATE {MANUAL_ROWS}
                        SET {assignments}{note_sql}, updated_by = %s, updated_at = now()
                        WHERE id = %s AND aptem_id = %s AND deleted_at IS NULL
                        """,
                        [*values.values(), *note_params, actor, row_id, aptem_id],
                    )
                    if cursor.rowcount:
                        updated_count += 1
                    else:
                        missing.append(row_id)
                if delete_ids:
                    cursor.execute(
                        f"""
                        UPDATE {MANUAL_ROWS}
                        SET deleted_at = now(), updated_at = now(), updated_by = %s
                        WHERE id = ANY(%s) AND aptem_id = %s AND deleted_at IS NULL
                        """,
                        [actor, delete_ids, aptem_id],
                    )
                    deleted_count = cursor.rowcount
                # Attach Azure evidence documents to any newly saved Aptem
                # assignment rows, month by month (refs look like asg:<id>).
                for month in sorted({values["month"] for _, values in validated_creates
                                     if str(values.get("source_ref") or "").startswith("asg:")}):
                    _attach_assignment_evidence_docs(cursor, aptem_id, month)
    except IntegrityError as error:
        return JsonResponse(
            {"error": "A change collided with an existing row.", "details": str(error)},
            status=409,
        )
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not save the journal changes.", "details": str(error)},
            status=503,
        )
    return JsonResponse({
        "ok": True,
        "created": created,
        "skipped": skipped,
        "updated": updated_count,
        "deleted": deleted_count,
        "missing": missing,
        "conflicts": conflicts,
    })
