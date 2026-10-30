"""Read-only API for the normalized ``Last_audit`` LMS mirror.

These endpoints keep the frontend's existing OTJ ledger contract and use
``Last_audit.learners`` as the Aptem-complete learner source. ``learner_id`` is
the optional LMS id; Aptem identity remains available even when it is NULL.

Hours are deliberately conservative: ``mapped_seconds`` is canonical and is
only converted to hours at the JSON boundary.  Until the hours-mapping job has
populated a learner's rows, the response marks hours as unavailable rather than
deriving or inventing them from activity status or video duration.
"""

import json
import re
import time
from urllib.parse import parse_qs, urlparse

from django.db import DatabaseError, connections
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from .db_source import cache_scope, resolve
from .learner_exclusions import is_excluded_learner


CONNECTION_ALIAS = "audit"

# The unfiltered cohort answer changes only when the mirror re-syncs, yet the
# journal/search pages request it on every visit and Neon takes seconds to
# aggregate it (~300k activity rows -> 1.3MB). Serve repeats from memory,
# pre-serialized so the 1.3MB is not re-encoded on every hit.
# Keyed by database source so the live workspace and the HOURS-TEST clone
# never serve each other's cohort.
_COHORT_CACHE_TTL_SECONDS = 300
_cohort_cache = {}

# Keep every mixed-case schema reference in one place.  The mirror is created
# outside this Django project, so these are intentionally unmanaged SQL tables.
LEARNERS = '"Last_audit"."learners"'
GROUPS = '"Last_audit"."groups"'
GROUP_LEARNERS = '"Last_audit"."group_learners"'
ACTIVITIES = '"Last_audit"."activities"'
ACTIVITY_RESULTS = '"Last_audit"."activity_results"'
LEARNER_ATTENDANCE = '"Last_audit"."learner_attendance"'
# Per-activity OTJH hours mapped by the fetch-evidence pipeline (planned + actual
# with reporting method + timestamps). Keyed (learner_id, kind, ref); ref = the
# LMS activity_id (video/audio/reading_quiz) or the attendance source_key.
ACTIVITY_PLANNED_HOURS = '"Last_audit"."activity_planned_hours"'
ACTIVITY_ACTUAL_HOURS = '"Last_audit"."activity_actual_hours"'

# LEFT JOIN fragment shared by the per-learner feed and the cross-learner view,
# so every learner's row carries their own planned / actual / method / timestamp.
_OTJH_JOIN = f"""
    LEFT JOIN {ACTIVITY_PLANNED_HOURS} ph
           ON ph.learner_id = r.learner_id AND ph.ref = r.activity_id::text
    LEFT JOIN {ACTIVITY_ACTUAL_HOURS} ah
           ON ah.learner_id = r.learner_id AND ah.ref = r.activity_id::text
"""
_OTJH_COLS = ("ph.planned_hours AS otjh_planned, ah.actual_hours AS otjh_actual, "
              "ah.reporting_method AS otjh_method, ah.timestamp_label AS otjh_timestamp, "
              "ah.start_time AS otjh_from, ah.end_time AS otjh_to")


def _connection():
    """The audit connection for this request — the live branch, or the clone
    when the request came in under the HOURS-TEST mount."""
    return connections[resolve(CONNECTION_ALIAS)]


def _dict_rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


_MONTH_KEY_RE = re.compile(r"^\d{4}-\d{2}$")


def _month_number_map(value):
    """A {"YYYY-MM": hours} JSON map as plain floats. Aptem's monthly plan
    arrives as jsonb (Decimals) or as a JSON string depending on the driver."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    result = {}
    for month, hours in value.items():
        if not _MONTH_KEY_RE.match(str(month)):
            continue
        try:
            result[str(month)] = round(float(hours), 2)
        except (TypeError, ValueError):
            continue
    return result


def _as_int(value, *, default=None, minimum=None, maximum=None):
    if value in (None, ""):
        return default
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"value must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"value must be at most {maximum}")
    return parsed


def _hours(seconds):
    if seconds is None:
        return None
    return round(float(seconds) / 3600, 2)


def _string_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if value in (None, ""):
        return []
    return [str(value)]


def _activity_content_url(video_url, reading_url, reading_type=None, audio_url=None):
    """Return browser-renderable content, unwrapping PDF-only Office URLs.
    Audio activities carry their player URL only inside raw->audio->iframe_url
    — callers pass it as ``audio_url`` (all 702 audio rows have NO other URL)."""
    if video_url:
        return video_url
    if audio_url and not reading_url:
        return audio_url
    if not reading_url or str(reading_type or "").strip().lower() != "pdf":
        return reading_url
    try:
        parsed = urlparse(reading_url)
        if parsed.netloc.lower() != "view.officeapps.live.com":
            return reading_url
        source = parse_qs(parsed.query).get("src", [None])[0]
        if source and urlparse(source).scheme in ("http", "https"):
            return source
    except (TypeError, ValueError):
        pass
    return reading_url


def _duration_min_sql(alias=""):
    """SQL for configured_duration_min with the audio fallback.  The ingestion
    pipeline stores audio durations only inside raw->audio, never on the
    column, so the column alone is NULL for every audio activity.  The regex
    guard keeps a malformed raw value from failing the whole query."""
    prefix = f"{alias}." if alias else ""
    json_path = f"{prefix}raw #>> '{{audio,configured_duration_min}}'"
    return (
        f"COALESCE({prefix}configured_duration_min, "
        f"(CASE WHEN {json_path} ~ '^[0-9]+(\\.[0-9]+)?$' THEN {json_path} END)::numeric)"
    )


def _quiz_attempt_payload(row, component_id):
    """Merge the shared quiz definition with one learner's graded answers."""
    questions = _json_list(row.get("quiz_questions"))
    answers = _json_list(row.get("quiz_answers"))
    is_quiz = row.get("quiz_id") is not None or bool(questions)
    base = {
        "component_id": str(component_id),
        "source_activity_id": int(row["activity_id"]),
        "aptem_id": int(row["aptem_id"]),
        "is_quiz": is_quiz,
    }
    if not is_quiz:
        return {**base, "state": "not_quiz", "attempt": None}

    attempted = bool(
        row.get("quiz_attempted")
        or row.get("quiz_attempt_number")
        or answers
    )
    if not attempted:
        return {**base, "state": "not_attempted", "attempt": None}

    answers_by_question = {
        str(answer.get("question_id")): answer
        for answer in answers
        if isinstance(answer, dict) and answer.get("question_id") is not None
    }
    source_questions = questions or answers
    normalized_questions = []
    for index, question in enumerate(source_questions, start=1):
        if not isinstance(question, dict):
            continue
        question_id = question.get("question_id")
        answer = answers_by_question.get(str(question_id), {})
        correct_answers = _string_list(answer.get("correct_answer"))
        learner_answers = _string_list(answer.get("learner_answer"))
        options = _json_list(question.get("options"))
        if not options:
            # Some historical attempts retain the graded answer body but no
            # option list. Preserve the visible selected/correct values.
            option_values = list(dict.fromkeys(correct_answers + learner_answers))
            options = [{"option_body": value, "option_order": order}
                       for order, value in enumerate(option_values, start=1)]
        normalized_options = []
        for option_index, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                continue
            option_text = str(option.get("option_body") or option.get("option_text") or "")
            normalized_options.append({
                "option_text": option_text,
                "option_order": option.get("option_order") or option_index,
                "is_correct": option_text in correct_answers,
                "is_selected": option_text in learner_answers,
            })
        is_correct = answer.get("is_correct")
        if not isinstance(is_correct, bool):
            is_correct = bool(learner_answers) and set(learner_answers) == set(correct_answers)
        normalized_questions.append({
            "question_id": int(question_id) if question_id is not None else index,
            "question_order": question.get("question_order") or index,
            "question_text": (
                question.get("question_body")
                or answer.get("question_body")
                or f"Question {index}"
            ),
            "question_type": question.get("question_type") or answer.get("question_type") or "",
            "is_correct": is_correct,
            "answer_options": normalized_options,
            "correct_answers": correct_answers,
            "learner_selected_answers": learner_answers,
        })

    passed = row.get("quiz_passed") is True
    return {
        **base,
        "state": "attempted",
        "attempt": {
            "title": row.get("title") or f"Quiz {row['activity_id']}",
            "status": "passed" if passed else "failed",
            "score": float(row["quiz_score"]) if row.get("quiz_score") is not None else None,
            "maximum_score": (
                float(row["quiz_maximum_score"])
                if row.get("quiz_maximum_score") is not None else None
            ),
            "attempt_number": int(row["quiz_attempt_number"] or 0),
            "quiz_body": {
                "description": row.get("quiz_body"),
                "questions": normalized_questions,
            },
        },
    }


def _category(activity_type):
    value = (activity_type or "activity").strip()
    return "reading+quiz" if value.lower() == "reading+quiz" else value.lower()


def _has_quiz(row):
    return row.get("quiz_id") is not None or bool(_json_list(row.get("quiz_questions")))


def _has_reading(row):
    return bool(
        row.get("reading_iframe_url")
        or row.get("reading_text_body")
        or row.get("reading_type")
    )


def _activity_category(row):
    # The source type/content is authoritative.  A recorded lesson can contain
    # "PPT" or "PowerPoint" in its title while still being a real video (for
    # example, a tutor presenting slides).  Inferring the category from that
    # word made those lessons disappear from the media-duration workflow.
    category = _category(row.get("activity_type"))
    if category != "reading+quiz":
        return category
    has_reading = _has_reading(row)
    has_quiz = _has_quiz(row)
    if has_reading and has_quiz:
        return "reading+quiz"
    if has_reading:
        return "reading"
    if has_quiz:
        return "quiz"
    return category


def _is_completed(row):
    if str(row.get("status") or "").strip().lower() == "completed":
        return True
    if row.get("video_completed"):
        return True
    if _has_quiz(row):
        quiz_completed = row.get("quiz_passed") is True
        return bool(quiz_completed and (not _has_reading(row) or row.get("reading_viewed")))
    if _has_reading(row):
        return row.get("reading_viewed") is True
    return False


def _activity_ref(group_id, activity_id):
    return f"la:{group_id}:{activity_id}"


def _attendance_ref(source_key):
    return f"att:{source_key}"


def _attendance_payload(row):
    activity_hours = row.get("activity_hours")
    activity_date = row.get("attendance_date")
    attended = row.get("attendance_value") == 1 or str(row.get("attendance_status") or "").lower() in {
        "present", "attended", "attend",
    }
    return {
        "activity_id": _attendance_ref(row["source_key"]),
        "source_activity_id": row["source_key"],
        "group_id": None,
        "group_name": row.get("module"),
        "learner_id": int(row["aptem_id"]),
        "lms_learner_id": int(row["learner_id"]) if row.get("learner_id") is not None else None,
        "learner_name": row.get("learner_name") or f"Aptem learner {row['aptem_id']}",
        "date": activity_date.isoformat() if activity_date else None,
        "month": activity_date.strftime("%Y-%m") if activity_date else "",
        "month_label": activity_date.strftime("%B %Y") if activity_date else "Not dated",
        "category": "attendance",
        # The requested lecture_name is the display title. Module remains as
        # supporting context rather than replacing the source lecture name.
        "activity": row.get("lecture_name") or row.get("module") or "Attendance session",
        "activity_subtitle": row.get("module") or row.get("attendance_status"),
        "planned": None,
        "actual": float(activity_hours) if activity_hours is not None else 0.0,
        "mapped_seconds": int(round(float(activity_hours) * 3600)) if activity_hours is not None else None,
        "hours_mapped": activity_hours is not None,
        "timestamp_from": None,
        "timestamp_to": None,
        "timestamp_display": "attended" if attended else "not attended",
        "status": row.get("attendance_status") or ("Present" if attended else "Absent"),
        "completed": attended,
        "video_started": None,
        "video_completed": None,
        "reading_viewed": None,
        "quiz_attempted": None,
        "quiz_passed": None,
        "quiz_score": None,
        "quiz_maximum_score": None,
        "configured_duration_minutes": None,
        "ksbs": None,
        "iframe_url": None,
        "source": "Last_audit",
    }


def _parse_activity_ref(value):
    """Return ``(group_id, activity_id)``; group is optional for numeric IDs."""
    raw = str(value or "").strip()
    if raw.startswith("la:"):
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError("invalid Last_audit activity reference")
        return int(parts[1]), int(parts[2])
    return None, int(raw)


def _activity_payload(row):
    mapped_seconds = row.get("mapped_seconds")
    if mapped_seconds is None and row.get("mapped_hours") is not None:
        mapped_seconds = int(round(float(row["mapped_hours"]) * 3600))
    # OTJH pipeline values (from activity_planned_hours / activity_actual_hours)
    # take precedence over the reserved mapped_* columns when the join supplied
    # them. Absent (other callers) -> fall back to the previous behaviour.
    otjh_planned = row.get("otjh_planned")
    otjh_actual = row.get("otjh_actual")
    otjh_from = row.get("otjh_from")
    otjh_to = row.get("otjh_to")
    if otjh_actual is not None:
        actual_hours = float(otjh_actual)
        hours_mapped = True
    else:
        hours_mapped = mapped_seconds is not None
        actual_hours = _hours(mapped_seconds) if hours_mapped else 0.0
    group_id = int(row["group_id"])
    activity_id = int(row["activity_id"])
    activity_date = row.get("activity_date")
    activity_date_iso = activity_date.isoformat() if activity_date else None
    return {
        "activity_id": _activity_ref(group_id, activity_id),
        "source_activity_id": activity_id,
        "group_id": group_id,
        "group_name": row.get("group_name"),
        "learner_id": int(row["aptem_id"]),
        "lms_learner_id": int(row["learner_id"]),
        "learner_name": row.get("learner_name") or f"Learner {row['learner_id']}",
        "date": activity_date_iso,
        "month": activity_date.strftime("%Y-%m") if activity_date else "",
        "month_label": activity_date.strftime("%B %Y") if activity_date else "Not dated",
        "category": _activity_category(row),
        "activity": row.get("title") or f"Activity {activity_id}",
        "activity_subtitle": row.get("status"),
        "planned": float(otjh_planned) if otjh_planned is not None else 0.0,
        "actual": actual_hours,
        "mapped_seconds": mapped_seconds,
        "hours_mapped": hours_mapped,
        "reporting_method": row.get("otjh_method"),
        "timestamp_from": otjh_from.strftime("%H:%M:%S") if otjh_from else None,
        "timestamp_to": otjh_to.strftime("%H:%M:%S") if otjh_to else None,
        "timestamp_display": row.get("otjh_timestamp") or "",
        "status": row.get("status"),
        "completed": _is_completed(row),
        "video_started": row.get("video_started"),
        "video_completed": row.get("video_completed"),
        "reading_viewed": row.get("reading_viewed"),
        "quiz_attempted": row.get("quiz_attempted"),
        "quiz_passed": row.get("quiz_passed"),
        "quiz_score": float(row["quiz_score"]) if row.get("quiz_score") is not None else None,
        "quiz_maximum_score": (
            float(row["quiz_maximum_score"])
            if row.get("quiz_maximum_score") is not None else None
        ),
        "has_reading": _has_reading(row),
        "has_quiz": _has_quiz(row),
        "configured_duration_minutes": (
            float(row["configured_duration_min"])
            if row.get("configured_duration_min") is not None else None
        ),
        "ksbs": None,
        "iframe_url": _activity_content_url(
            row.get("video_iframe_url"),
            row.get("reading_iframe_url"),
            row.get("reading_type"),
            row.get("audio_iframe_url"),
        ),
        "source": "Last_audit",
    }


@require_GET
def health(_request: HttpRequest) -> JsonResponse:
    try:
        with _connection().cursor() as cursor:
            cursor.execute(
                f"SELECT (SELECT count(*) FROM {LEARNERS} WHERE aptem_id IS NOT NULL), "
                f"(SELECT count(*) FROM {ACTIVITY_RESULTS}), current_database()"
            )
            learners, results, database = cursor.fetchone()
    except DatabaseError as error:
        return JsonResponse(
            {"ok": False, "source": "Last_audit", "error": str(error)}, status=503
        )
    return JsonResponse({
        "ok": True,
        "source": "Last_audit",
        "database": database,
        "learners": learners,
        "activity_results": results,
    })


@require_GET
def cohort(request: HttpRequest) -> JsonResponse:
    """Return Aptem learners, enriched only by their verified LMS match."""
    search = (request.GET.get("search") or "").strip()
    programme = (request.GET.get("programme") or "").strip()
    # Only the unfiltered call is cached: it is the hot path (every journal /
    # search page load) and the only expensive one; filtered calls stay live.
    cacheable = not search and not programme
    cached = _cohort_cache.get(cache_scope())
    if cacheable and cached and cached["expires_at"] > time.monotonic():
        return HttpResponse(cached["body"], content_type="application/json")
    conditions = ["l.aptem_id IS NOT NULL"]
    params = []
    if search:
        conditions.append("(l.learner_name ILIKE %s OR l.learner_email ILIKE %s)")
        params.append(f"%{search}%")
        params.append(f"%{search}%")
    if programme:
        conditions.append("l.programme_name = %s")
        params.append(programme)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    sql = f"""
        WITH learner_roster AS (
            SELECT l.aptem_id, l.learner_name, l.learner_email,
                   l.programme_name, l.programme_status,
                   l.coach_name, l.coach_email,
                   l.declared_lms_id, l.learner_id,
                   l.planned_hours_total, l.planned_hours_monthly
            FROM {LEARNERS} l

            UNION ALL

            SELECT aptem."ID" AS aptem_id,
                   aptem."FullName" AS learner_name,
                   aptem."Email" AS learner_email,
                   COALESCE(
                       NULLIF(btrim(aptem."Program Name"), ''),
                       NULLIF(btrim(aptem."Group"), '')
                   ) AS programme_name,
                   NULLIF(btrim(aptem."Program-Status"), '') AS programme_status,
                   NULLIF(btrim(aptem."OwnerName"), '') AS coach_name,
                   NULLIF(btrim(aptem."OwnerEmail"), '') AS coach_email,
                   NULL::bigint AS declared_lms_id,
                   NULL::bigint AS learner_id,
                   NULL::numeric AS planned_hours_total,
                   '{{}}'::jsonb AS planned_hours_monthly
            FROM "LMS"."Aptem_users" aptem
            WHERE aptem."ID" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {LEARNERS} existing
                  WHERE existing.aptem_id = aptem."ID"
              )
              AND EXISTS (
                  SELECT 1
                  FROM "Audit".ilr_learning_deliveries ilr_delivery
                  WHERE (
                      NULLIF(btrim(aptem."Email"), '') IS NOT NULL
                      AND lower(btrim(ilr_delivery.email)) = lower(btrim(aptem."Email"))
                  ) OR (
                      NULLIF(btrim(aptem."FullName"), '') IS NOT NULL
                      AND lower(btrim(concat_ws(' ', ilr_delivery.given_names, ilr_delivery.family_name))) =
                          lower(btrim(aptem."FullName"))
                  )
              )
        ), learner_groups AS (
            SELECT gl.learner_id,
                   array_agg(DISTINCT g.group_name ORDER BY g.group_name)
                       FILTER (WHERE g.group_name IS NOT NULL) AS groups,
                   array_agg(DISTINCT g.group_name ORDER BY g.group_name)
                       FILTER (WHERE g.group_name ILIKE '%%KSB%%') AS programme_groups
            FROM {GROUP_LEARNERS} gl
            JOIN {GROUPS} g ON g.group_id = gl.group_id
            GROUP BY gl.learner_id
        ), result_totals AS (
            SELECT learner_id,
                   count(*) AS activity_count,
                   count(*) FILTER (WHERE status = 'completed'
                                      OR video_completed IS TRUE
                                      OR quiz_passed IS TRUE) AS completed_count,
                   count(COALESCE(mapped_seconds, round(mapped_hours * 3600))) AS mapped_count,
                   COALESCE(sum(COALESCE(mapped_seconds, round(mapped_hours * 3600))), 0)
                       AS mapped_seconds
            FROM {ACTIVITY_RESULTS}
            GROUP BY learner_id
        ), attendance_totals AS (
            SELECT aptem_id,
                   count(*) AS attendance_count,
                   count(*) FILTER (
                       WHERE attendance_value = 1
                          OR lower(COALESCE(attendance_status, '')) IN ('present', 'attended', 'attend')
                   ) AS attended_count,
                   count(activity_hours) AS mapped_count,
                   COALESCE(sum(round(activity_hours * 3600)), 0) AS mapped_seconds
            FROM {LEARNER_ATTENDANCE}
            GROUP BY aptem_id
        ), attendance_month_rows AS (
            SELECT aptem_id,
                   to_char(attendance_date, 'YYYY-MM') AS month,
                   to_char(attendance_date, 'FMMonth YYYY') AS label,
                   COALESCE(sum(activity_hours), 0) AS actual
            FROM {LEARNER_ATTENDANCE}
            WHERE attendance_date IS NOT NULL
            GROUP BY aptem_id, date_trunc('month', attendance_date),
                     to_char(attendance_date, 'YYYY-MM'), to_char(attendance_date, 'FMMonth YYYY')
        ), attendance_months AS (
            SELECT aptem_id,
                   jsonb_agg(
                       jsonb_build_object(
                           'month', month, 'label', label, 'planned', 0,
                           'actual', actual, 'not_accepted', 0,
                           'att_actual', actual, 'asg_actual', 0,
                           'media_actual', 0, 'bundle_actual', 0,
                           'unallocated_actual', 0
                       ) ORDER BY month
                   ) AS months
            FROM attendance_month_rows
            GROUP BY aptem_id
        ), ilr_profiles AS (
            SELECT DISTINCT ON (lower(email))
                   lower(email) AS email_key,
                   planned_hours
            FROM "Audit".ilr_learning_deliveries
            WHERE email IS NOT NULL
              AND planned_hours IS NOT NULL
            ORDER BY lower(email), aim_seq_number,
                     updated_at DESC NULLS LAST, id DESC
        )
        SELECT l.aptem_id, l.learner_name, l.learner_email, l.programme_name,
               COALESCE(
                   CASE
                       WHEN lower(btrim(COALESCE(l.programme_status, ''))) IN ('', 'unknown') THEN NULL
                       ELSE btrim(l.programme_status)
                   END,
                   NULLIF(btrim(aptem."Program-Status"), '')
               ) AS programme_status,
               COALESCE(NULLIF(btrim(l.coach_name), ''), NULLIF(btrim(aptem."OwnerName"), '')) AS coach_name,
               COALESCE(NULLIF(btrim(l.coach_email), ''), NULLIF(btrim(aptem."OwnerEmail"), '')) AS coach_email,
               l.declared_lms_id,
               l.learner_id AS verified_lms_id,
               COALESCE(lg.groups, ARRAY[]::text[]) AS groups,
               COALESCE(rt.activity_count, 0) + COALESCE(at.attendance_count, 0) AS activity_count,
               COALESCE(rt.activity_count, 0) AS lms_activity_count,
               COALESCE(at.attendance_count, 0) AS attendance_count,
               COALESCE(rt.completed_count, 0) + COALESCE(at.attended_count, 0) AS completed_count,
               COALESCE(rt.mapped_count, 0) + COALESCE(at.mapped_count, 0) AS mapped_count,
               COALESCE(rt.mapped_count, 0) AS lms_mapped_count,
               COALESCE(rt.mapped_seconds, 0) AS lms_mapped_seconds,
               COALESCE(at.mapped_seconds, 0) AS attendance_seconds,
               ilr.planned_hours AS ilr_planned_hours,
               -- Aptem's OWN plan (mirror of LMS."Aptem_users"."Planned" and
               -- its monthly split) — what the learner's journal reports as the
               -- programme/monthly plan, so both screens quote one number.
               l.planned_hours_total AS aptem_planned_total,
               l.planned_hours_monthly AS aptem_planned_monthly,
               COALESCE(am.months, '[]'::jsonb) AS months
        FROM learner_roster l
        LEFT JOIN learner_groups lg ON lg.learner_id = l.learner_id
        LEFT JOIN result_totals rt ON rt.learner_id = l.learner_id
        LEFT JOIN attendance_totals at ON at.aptem_id = l.aptem_id
        LEFT JOIN attendance_months am ON am.aptem_id = l.aptem_id
        LEFT JOIN ilr_profiles ilr ON ilr.email_key = lower(l.learner_email)
        LEFT JOIN "LMS"."Aptem_users" aptem ON aptem."ID" = l.aptem_id
        {where}
        ORDER BY lower(COALESCE(l.learner_name, '')), l.aptem_id
    """
    try:
        with _connection().cursor() as cursor:
            cursor.execute(sql, params)
            rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit learners.", "details": str(error)},
            status=503,
        )

    rows = [
        row for row in rows
        if not is_excluded_learner(row.get("aptem_id"), row.get("learner_name"))
    ]
    all_programmes = sorted({row["programme_name"] for row in rows if row["programme_name"]})
    learners = []
    for row in rows:
        programmes = [row["programme_name"]] if row["programme_name"] else []
        mapped_count = int(row["lms_mapped_count"])
        lms_activity_count = int(row["lms_activity_count"])
        actual_hours_available = lms_activity_count > 0 and mapped_count == lms_activity_count
        lms_matched = row["verified_lms_id"] is not None
        flags = []
        if not lms_matched:
            flags.append("lms_not_matched")
        if int(row["lms_activity_count"]) > 0 and int(row["lms_mapped_count"]) == 0:
            flags.append("hours_not_mapped")
        learners.append({
            "aptem_id": int(row["aptem_id"]),
            "lms_id": int(row["verified_lms_id"]) if lms_matched else None,
            "declared_lms_id": int(row["declared_lms_id"]) if row["declared_lms_id"] is not None else None,
            "lms_matched": lms_matched,
            "learner_name": row["learner_name"] or f"Aptem learner {row['aptem_id']}",
            "learner_email": row["learner_email"],
            "programme": programmes[0] if programmes else "Unassigned",
            "programmes": programmes,
            "groups": list(row["groups"]),
            "withdrawn": str(row["programme_status"] or "").strip().lower() == "withdrawn",
            "programme_status": row["programme_status"] or "Unknown",
            "coach_name": row["coach_name"],
            "coach_email": row["coach_email"],
            # This is the same ILR value used by the rich learner profile.
            # A missing ILR row is unavailable data, not a real zero.
            "planned_total": float(row["ilr_planned_hours"]) if row["ilr_planned_hours"] is not None else 0.0,
            "planned_hours_available": row["ilr_planned_hours"] is not None,
            # Aptem's own plan, kept beside the ILR figure rather than replacing
            # it: the cohort table quotes Aptem (matching each learner's
            # journal), while the ILR total stays available for funding views.
            "aptem_planned_total": float(row["aptem_planned_total"]) if row["aptem_planned_total"] is not None else None,
            "aptem_planned_monthly": _month_number_map(row["aptem_planned_monthly"]),
            # Attendance source hours are not the learner's approved OTJ actual
            # total. Keep Actual unavailable until every LMS activity has an
            # explicit mapped duration; do not surface the attendance sum as a
            # misleading programme total.
            "actual_total": _hours(row["lms_mapped_seconds"]) if actual_hours_available else 0.0,
            "not_accepted_total": 0.0,
            "hours_mapped": actual_hours_available,
            "mapped_activity_count": mapped_count,
            "activity_count": int(row["activity_count"]),
            "lms_activity_count": lms_activity_count,
            "attendance_count": int(row["attendance_count"]),
            "completed_count": int(row["completed_count"]),
            "flags": flags,
            "months": _json_list(row["months"]),
        })
    response = JsonResponse({
        "source": "Last_audit",
        "programmes": all_programmes,
        "learners": learners,
    })
    if cacheable:
        _cohort_cache[cache_scope()] = {
            "body": response.content,
            "expires_at": time.monotonic() + _COHORT_CACHE_TTL_SECONDS,
        }
    return response


@require_GET
def activities(request: HttpRequest) -> JsonResponse:
    """Return LMS and linked attendance rows for one Aptem-linked learner."""
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
        limit = _as_int(request.GET.get("limit"), default=10000, minimum=1, maximum=20000)
        offset = _as_int(request.GET.get("offset"), default=0, minimum=0)
    except (TypeError, ValueError) as error:
        return JsonResponse({"error": f"Invalid query parameters: {error}"}, status=400)
    if aptem_id is None:
        return JsonResponse({"error": "aptem_id (int) is required"}, status=400)

    category = (request.GET.get("category") or "").strip()
    search = (request.GET.get("search") or "").strip()
    month = (request.GET.get("month") or "").strip()
    try:
        with _connection().cursor() as cursor:
            cursor.execute(
                f"SELECT learner_id, learner_name FROM {LEARNERS} WHERE aptem_id=%s",
                [aptem_id],
            )
            learner = cursor.fetchone()
            if not learner:
                return JsonResponse({"error": f"no Aptem learner {aptem_id}"}, status=404)
            learner_id, learner_name = learner
            if is_excluded_learner(aptem_id, learner_name):
                return JsonResponse({"error": "Learner not found."}, status=404)

            lms_rows = []
            if learner_id is not None and category.lower() != "attendance":
                conditions = ["r.learner_id = %s"]
                params = [learner_id]
                if month == "undated":
                    conditions.append("a.activity_date IS NULL")
                elif month:
                    conditions.append("to_char(a.activity_date, 'YYYY-MM') = %s")
                    params.append(month)
                if category:
                    conditions.append("lower(r.activity_type) = lower(%s)")
                    params.append("Reading+Quiz" if category.lower() == "reading+quiz" else category)
                if search:
                    conditions.append("(a.title ILIKE %s OR CAST(a.activity_id AS text) ILIKE %s)")
                    params.extend([f"%{search}%", f"%{search}%"])
                cursor.execute(f"""
                    SELECT r.group_id, r.learner_id, l.aptem_id, l.learner_name,
                           g.group_name, r.activity_id,
                           COALESCE(a.activity_type, r.activity_type) AS activity_type,
                           a.title, a.activity_date,
                           a.video_iframe_url, a.reading_iframe_url,
                           a.raw #>> '{{audio,iframe_url}}' AS audio_iframe_url,
                           a.reading_type, a.quiz_id, a.quiz_questions,
                           {_duration_min_sql('a')} AS configured_duration_min, r.status,
                           r.video_started, r.video_completed, r.reading_viewed,
                           r.quiz_attempted, r.quiz_passed, r.quiz_score,
                           r.quiz_maximum_score, r.mapped_seconds, r.mapped_hours,
                           {_OTJH_COLS}
                    FROM {ACTIVITY_RESULTS} r
                    JOIN {LEARNERS} l ON l.learner_id = r.learner_id
                    JOIN {ACTIVITIES} a ON a.activity_id = r.activity_id
                    LEFT JOIN {GROUPS} g ON g.group_id = r.group_id
                    {_OTJH_JOIN}
                    WHERE {' AND '.join(conditions)}
                """, params)
                lms_rows = _dict_rows(cursor)

            attendance_rows = []
            if month != "undated" and category.lower() in ("", "attendance"):
                conditions = ["la.aptem_id = %s"]
                params = [aptem_id]
                if month:
                    conditions.append("to_char(la.attendance_date, 'YYYY-MM') = %s")
                    params.append(month)
                if search:
                    conditions.append("(la.lecture_name ILIKE %s OR la.module ILIKE %s OR la.source_key ILIKE %s)")
                    params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
                cursor.execute(f"""
                    SELECT la.*, l.learner_name
                    FROM {LEARNER_ATTENDANCE} la
                    JOIN {LEARNERS} l ON l.aptem_id = la.aptem_id
                    WHERE {' AND '.join(conditions)}
                """, params)
                attendance_rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit activities.", "details": str(error)},
            status=503,
        )
    all_items = [_activity_payload(row) for row in lms_rows]
    all_items.extend(_attendance_payload(row) for row in attendance_rows)
    all_items.sort(key=lambda item: (
        item["date"] is None,
        item["date"] or "",
        (item.get("group_name") or "").lower(),
        item["activity"].lower(),
        str(item["activity_id"]),
    ))
    total = len(all_items)
    mapped = [item for item in all_items if item["hours_mapped"]]
    items = all_items[offset:offset + limit]
    return JsonResponse({
        "source": "Last_audit",
        "aptem_id": aptem_id,
        "learner_name": learner_name,
        "month": month or None,
        "count": total,
        "planned_total": 0.0,
        "actual_total": round(sum(item["actual"] for item in mapped), 2),
        "hours_mapped": bool(mapped),
        "limit": limit,
        "offset": offset,
        "activities": items,
    })


def _grouped(participants):
    """Nest participants under their cohort so the UI can show them grouped."""
    buckets, order = {}, []
    for p in participants:
        key = (p.get("group_id"), p.get("group_name"))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(p)
    return [{"group_id": k[0], "group_name": k[1],
             "participant_count": len(buckets[k]), "participants": buckets[k]}
            for k in order]


@require_GET
def activity(request: HttpRequest) -> JsonResponse:
    """Return a shared definition and all learner results for one activity.

    Learners are grouped by their cohort (`groups[]`). For attendance the
    activity is a live session: we match on the session key (the source_key with
    the leading learner id removed = date + lecture), so every learner in that
    session appears — attended or not — not just the one whose key was clicked.
    """
    raw_id = request.GET.get("activity_id") or request.GET.get("component_id")
    if str(raw_id or "").startswith("att:"):
        session_key = _session_key(raw_id)      # 'att:{id}_{date}_{slug}' -> '{date}_{slug}'
        if not session_key:
            return JsonResponse({"error": "invalid attendance key"}, status=400)
        try:
            with _connection().cursor() as cursor:
                cursor.execute(f"""
                    SELECT la.*, l.learner_name
                    FROM {LEARNER_ATTENDANCE} la
                    JOIN {LEARNERS} l ON l.aptem_id = la.aptem_id
                    WHERE substring(la.source_key from position('_' in la.source_key) + 1) = %s
                    ORDER BY (la.attendance_value = 1) DESC,
                             lower(COALESCE(l.learner_name, '')), la.aptem_id
                """, [session_key])
                rows = _dict_rows(cursor)
        except DatabaseError as error:
            return JsonResponse(
                {"error": "Could not read Last_audit attendance.", "details": str(error)},
                status=503,
            )
        rows = [
            row for row in rows
            if not is_excluded_learner(row.get("aptem_id"), row.get("learner_name"))
        ]
        if not rows:
            return JsonResponse({"error": f"no Last_audit attendance {raw_id}"}, status=404)
        items = [_attendance_payload(row) for row in rows]
        first = items[0]
        participants = [{
            "learner_id": item["learner_id"],
            "learner_name": item["learner_name"],
            "found_as": "attendance",
            "activity": item["activity"],
            "completed": item["completed"],
            "reading_completed": False,
            "quiz_attempted": False,
            "quiz_passed": False,
            "actual": item["actual"] if item["hours_mapped"] else None,
            "planned": None,
            "reporting_method": "Attendance",
            "month": item["month"] or None,
            "date": item["date"],
            "timestamp_from": None,
            "timestamp_to": None,
            "timestamp_display": item["timestamp_display"],
            "item_title": item.get("group_name"),
            "status": item["status"],
            "group_id": None,
            "group_name": item.get("group_name"),
        } for item in items]
        return JsonResponse({
            "source": "Last_audit",
            "component_id": first["activity_id"],
            "source_activity_id": session_key,
            "session_key": session_key,
            "activity": first["activity"],
            "category": "attendance",
            "has_reading": False,
            "has_quiz": False,
            "participant_count": len(participants),
            "completed_count": sum(1 for p in participants if p["completed"]),
            "reading_completed_count": 0,
            "quiz_attempted_count": 0,
            "quiz_completed_count": 0,
            "items": [],
            "item_count": 0,
            "participants": participants,
            "groups": _grouped(participants),
        })
    try:
        group_id, activity_id = _parse_activity_ref(raw_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "activity_id is required"}, status=400)

    conditions = ["r.activity_id = %s"]
    params = [activity_id]
    if group_id is not None:
        conditions.append("r.group_id = %s")
        params.append(group_id)
    sql = f"""
        SELECT r.group_id, r.learner_id, l.aptem_id, l.learner_name,
               g.group_name, r.activity_id,
               COALESCE(a.activity_type, r.activity_type) AS activity_type,
               a.title, a.activity_date,
               a.video_iframe_url, a.reading_iframe_url,
               a.raw #>> '{{audio,iframe_url}}' AS audio_iframe_url,
               a.reading_type, a.quiz_id, a.quiz_questions,
               {_duration_min_sql('a')} AS configured_duration_min, r.status,
               r.video_started, r.video_completed, r.reading_viewed,
               r.quiz_attempted, r.quiz_passed, r.quiz_score,
               r.quiz_maximum_score, r.mapped_seconds, r.mapped_hours,
               {_OTJH_COLS}
        FROM {ACTIVITY_RESULTS} r
        JOIN {LEARNERS} l ON l.learner_id = r.learner_id
        JOIN {ACTIVITIES} a ON a.activity_id = r.activity_id
        LEFT JOIN {GROUPS} g ON g.group_id = r.group_id
        {_OTJH_JOIN}
        WHERE {' AND '.join(conditions)}
        ORDER BY lower(COALESCE(g.group_name, '')), r.group_id,
                 lower(COALESCE(l.learner_name, '')), l.learner_id
    """
    try:
        with _connection().cursor() as cursor:
            cursor.execute(sql, params)
            rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit activity.", "details": str(error)},
            status=503,
        )
    rows = [
        row for row in rows
        if not is_excluded_learner(row.get("aptem_id"), row.get("learner_name"))
    ]
    if not rows:
        return JsonResponse({"error": f"no Last_audit activity {raw_id}"}, status=404)

    items = [_activity_payload(row) for row in rows]
    first = items[0]
    participants = [{
        "learner_id": item["learner_id"],
        "learner_name": item["learner_name"],
        "found_as": item["category"],
        "activity": item["activity"],
        "completed": item["completed"],
        "reading_completed": item["reading_viewed"] is True,
        "quiz_attempted": item["quiz_attempted"] is True,
        "quiz_passed": item["quiz_passed"] is True,
        "actual": item["actual"] if item["hours_mapped"] else None,
        "planned": item["planned"] or None,
        "reporting_method": item.get("reporting_method"),
        "month": item["month"] or None,
        "date": item["date"],
        "timestamp_from": item["timestamp_from"],
        "timestamp_to": item["timestamp_to"],
        "timestamp_display": item["timestamp_display"],
        "item_title": None,
        "status": item["status"],
        "group_id": item["group_id"],
        "group_name": item["group_name"],
    } for item in items]
    return JsonResponse({
        "source": "Last_audit",
        "component_id": first["activity_id"],
        "source_activity_id": activity_id,
        "groups": _grouped(participants),
        "activity": first["activity"],
        "category": first["category"],
        "has_reading": first["has_reading"],
        "has_quiz": first["has_quiz"],
        "participant_count": len(participants),
        "completed_count": sum(1 for item in participants if item["completed"]),
        "reading_completed_count": sum(
            1 for item in participants if item["reading_completed"]
        ),
        "quiz_attempted_count": sum(
            1 for item in participants if item["quiz_attempted"]
        ),
        "quiz_completed_count": sum(
            1 for item in participants if item["quiz_passed"]
        ),
        "items": [],
        "item_count": 0,
        "participants": participants,
    })


@require_GET
def quiz_attempt(request: HttpRequest) -> JsonResponse:
    """Return one Last_audit learner's normalized quiz definition and answers."""
    raw_id = request.GET.get("component_id") or request.GET.get("component")
    try:
        aptem_id = _as_int(request.GET.get("aptem_id"), minimum=1)
        group_id, activity_id = _parse_activity_ref(raw_id)
    except (TypeError, ValueError):
        return JsonResponse(
            {"error": "aptem_id and component_id are required"},
            status=400,
        )

    if is_excluded_learner(aptem_id):
        return JsonResponse({"error": "Learner not found."}, status=404)

    conditions = ["r.activity_id = %s", "l.aptem_id = %s"]
    params = [activity_id, aptem_id]
    if group_id is not None:
        conditions.append("r.group_id = %s")
        params.append(group_id)
    try:
        with _connection().cursor() as cursor:
            cursor.execute(f"""
                SELECT r.group_id, r.activity_id, l.aptem_id, l.learner_name,
                       a.title, a.quiz_id, a.quiz_body, a.quiz_questions,
                       r.quiz_attempted, r.quiz_passed, r.quiz_score,
                       r.quiz_maximum_score, r.quiz_attempt_number,
                       r.quiz_answers
                FROM {ACTIVITY_RESULTS} r
                JOIN {LEARNERS} l ON l.learner_id = r.learner_id
                JOIN {ACTIVITIES} a ON a.activity_id = r.activity_id
                WHERE {' AND '.join(conditions)}
                LIMIT 1
            """, params)
            rows = _dict_rows(cursor)
    except DatabaseError as error:
        return JsonResponse(
            {"error": "Could not read Last_audit quiz attempt.", "details": str(error)},
            status=503,
        )
    rows = [
        row for row in rows
        if not is_excluded_learner(row.get("aptem_id"), row.get("learner_name"))
    ]
    if not rows:
        return JsonResponse(
            {"error": "No matching Last_audit learner activity."},
            status=404,
        )
    component_id = (
        _activity_ref(group_id, activity_id)
        if group_id is not None else str(activity_id)
    )
    return JsonResponse(_quiz_attempt_payload(rows[0], component_id))


# --- attendance sheet (one live session, every assigned learner) ------------
#
# An attendance ``source_key`` is ``"{aptem_id}_{YYYY-MM-DD}_{lecture_slug}"``.
# Dropping the leading learner id leaves the SESSION identity shared by every
# learner assigned to that lecture on that date — attended or not. That suffix
# is the join key for the "attendance sheet": one row per learner, each marked
# attended/absent from ``attendance_value``.

def _session_key(source_key):
    """Return the session identity (``source_key`` minus the leading learner id),
    i.e. ``"{date}_{lecture_slug}"``, or ``None`` if the key is not composite."""
    raw = str(source_key or "").strip()
    if raw.startswith("att:"):
        raw = raw[len("att:"):]
    if "_" not in raw:
        return None
    return raw.split("_", 1)[1]


def _attendance_sheet_payload(rows, session_key):
    """Build the roster response from raw ``learner_attendance`` rows.

    Kept separate from the view (and DB) so it is unit-testable the same way
    ``_attendance_payload`` is. ``rows`` all share one ``session_key``."""
    attendees = [_attendance_payload(row) for row in rows]
    attended = sum(1 for row in rows if row.get("attendance_value") == 1)
    first = rows[0] if rows else {}
    session_date = first.get("attendance_date")
    return {
        "session": {
            "session_key": session_key,
            "date": session_date.isoformat() if session_date else None,
            "lecture_name": first.get("lecture_name") or first.get("module"),
            "module": first.get("module"),
        },
        "items": attendees,
        "total": len(attendees),
        "counts": {
            "assigned": len(attendees),
            "attended": attended,
            "absent": len(attendees) - attended,
        },
        "planned_total": 0.0,
        "actual_total": round(sum(item["actual"] or 0 for item in attendees), 2),
        "limit": len(attendees),
        "offset": 0,
    }


@require_GET
def attendance_sheet(request: HttpRequest) -> JsonResponse:
    """Roster for one live session: every learner sharing the session key, each
    marked attended/absent. Given any single learner's attendance key (the full
    ``source_key`` or its ``att:`` ref), matches on the key with the leading
    learner id removed, so the whole assigned cohort is returned regardless of
    programme. Reads ``Last_audit.learner_attendance`` only; writes nothing."""
    session_key = _session_key(request.GET.get("key"))
    if not session_key:
        return JsonResponse(
            {"error": "key must look like <id>_<YYYY-MM-DD>_<lecture>"},
            status=400,
        )
    try:
        with _connection().cursor() as cursor:
            cursor.execute(f"""
                SELECT la.source_key, la.aptem_id, la.learner_id,
                       la.attendance_date, la.attendance_value, la.attendance_status,
                       la.module, la.lecture_name, la.activity_hours,
                       l.learner_name
                FROM {LEARNER_ATTENDANCE} la
                LEFT JOIN {LEARNERS} l ON l.aptem_id = la.aptem_id
                WHERE substring(la.source_key from position('_' in la.source_key) + 1) = %s
                ORDER BY (la.attendance_value = 1) DESC,
                         l.learner_name NULLS LAST, la.aptem_id
            """, [session_key])
            rows = _dict_rows(cursor)
    except (KeyError, DatabaseError) as error:
        return JsonResponse(
            {"error": "Could not read Last_audit attendance.", "details": str(error)},
            status=503,
        )
    rows = [
        row for row in rows
        if not is_excluded_learner(row.get("aptem_id"), row.get("learner_name"))
    ]
    if not rows:
        return JsonResponse(
            {"error": f"No attendance session for '{session_key}'."},
            status=404,
        )
    return JsonResponse(_attendance_sheet_payload(rows, session_key))
