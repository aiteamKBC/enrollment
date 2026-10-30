"""Small, read-only projections of the existing coach-owned Dashboard snapshot.

PostgreSQL projects JSON in the database, before it crosses the connection.
Warm section reads never rebuild the caseload or call Graph.
"""

from __future__ import annotations

from functools import cmp_to_key
from math import ceil, floor
import re

from django.db import connections
from django.db.models import Func, JSONField
from django.utils import timezone

from .service import CoachDashboardService
from .upcoming import INPUT_FIELDS
from .weeks import work_week
from .timing import dashboard_stage


IDENTITY_FIELDS = (
    "id", "name", "initials", "email", "learnerType", "enrolmentId", "aptemId",
    "programme", "programmeName", "cohortId", "cohortName", "group", "employer",
    "status", "rawProgramStatus", "enrollmentStatus",
)
OTJH_FIELDS = (
    "otjhCompleted", "otjhTarget", "otjhPlanned", "otjhMinimum",
    "otjhProgrammeStartDate", "startDate", "plannedEndDate",
    "otjhTargetAsOfToday", "otjhProgressAsOfToday", "otjhShortfallHours",
    "otjhDeltaHours", "otjhRagStatus", "otjhRagSource", "overallProgress",
    "overallProgressAvailable", "progressVariance",
)
ATTENDANCE_FIELDS = (
    "attendanceRate", "attendanceRateAvailable", "attendanceAvailable",
    "attendancePresent", "attendanceSessions", "attendanceAbsent",
    "attendanceConsecutiveMissed", "attendanceLastSession", "attendanceLastSessionDate",
)
TABLE_FIELDS = IDENTITY_FIELDS + OTJH_FIELDS + ATTENDANCE_FIELDS + (
    "activityProgress", "activityProgressAvailable", "componentsCompleted", "componentsPlanned",
    "currentModule", "currentWeek", "componentsTargetToDate", "displayStartDate", "displayEndDate",
    "lastActivity", "lastActivityDate", "lastActivityLabel", "lastContact", "lastSubmittedEvidence",
    "lastPr", "lastMcm", "lastProgressReview", "lastReview", "lastCoachingSession",
)
SUMMARY_FIELDS = IDENTITY_FIELDS + OTJH_FIELDS + (
    "evidenceCount", "evidenceCompletedCount", "evidenceCountAvailable",
)
SORT_FIELDS = ("ksbCompleted", "ksbTarget", "ksbProgress", "ksbProgressAvailable", "ksbStatus")
RISK_FIELDS = IDENTITY_FIELDS + OTJH_FIELDS + ATTENDANCE_FIELDS + ("lastPr", "lastMcm")
# Full event DTOs belong to the on-demand meetings section. The summary needs
# only date/source/status/learner identity to keep the existing weekly rule.
COUNT_EVENT_FIELDS = ("learnerId", "source", "status", "date", "scheduledDate", "targetDate", "originalDate",
                      "importedReviewType", "reviewTypeCode")
POPUP_EVENT_FIELDS = COUNT_EVENT_FIELDS + ("id", "eventKey", "calendarEventId", "enrolmentId", "learner", "title", "programme", "group", "scheduledTime", "timeLabel", "durationMinutes")
MARKING_FIELDS = (
    "id", "learnerId", "learner", "initials", "email", "programme", "group",
    "pendingEvidence", "acceptedEvidence", "referredEvidence", "totalEvidence",
    "lastSubmission", "lastSubmissionIso", "submittedAt", "isOverdue",
)


def _pick(item, fields):
    return {key: item[key] for key in fields if key in item}


def project_section(payload: dict, section: str) -> dict:
    """SQLite/cached-payload equivalent of the PostgreSQL projection."""
    result = {key: payload[key] for key in ("owner", "errors", "readModel") if key in payload}
    fields = {"summary": SUMMARY_FIELDS, "learners": TABLE_FIELDS + SORT_FIELDS, "risk": RISK_FIELDS}.get(section)
    if fields:
        result["learners"] = [_pick(item, fields) for item in payload.get("learners") or []]
    if section in {"summary", "meetings", "learners"}:
        meetings = payload.get("meetings") or {}
        result["meetings"] = dict(meetings)
        if section == "meetings":
            result["meetings"] = {"events": [_pick(item, INPUT_FIELDS) for item in meetings.get("events") or []]}
        if section in {"summary", "learners"}:
            result["meetings"]["events"] = [_pick(item, POPUP_EVENT_FIELDS if section == "summary" else COUNT_EVENT_FIELDS) for item in meetings.get("events") or []]
            if section == "learners":
                result["meetings"] = {"events": result["meetings"]["events"]}
        if section == "summary":
            marking = payload.get("marking") or {}
            result["marking"] = {
                "summary": marking.get("summary", {}),
                "items": [_pick(item, MARKING_FIELDS) for item in marking.get("items") or []],
            }
    return result


class DashboardSectionProjection(Func):
    """Allowlisted JSON projection; SQL and keys never come from HTTP input."""

    output_field = JSONField()

    def __init__(self, expression, section):
        self.section = section
        super().__init__(expression)

    def as_postgresql(self, compiler, connection, **extra_context):
        payload_sql, payload_params = compiler.compile(self.source_expressions[0])
        params = []

        def source(path):
            params.extend(payload_params)
            # All paths are fixed literals declared in this module.
            return payload_sql + "".join(f"->'{key}'" for key in path)

        def array(path, fields):
            expression = source(path)
            params.extend(fields)
            placeholders = ", ".join(["%s"] * len(fields))
            return (
                "(SELECT COALESCE(jsonb_agg((SELECT COALESCE(jsonb_object_agg(k, v), '{}'::jsonb) "
                f"FROM jsonb_each(item) AS members(k, v) WHERE k IN ({placeholders})) ORDER BY ordinal), '[]'::jsonb) "
                f"FROM jsonb_array_elements(COALESCE(NULLIF({expression}, 'null'::jsonb), '[]'::jsonb)) "
                "WITH ORDINALITY AS elements(item, ordinal))"
            )

        pairs = []

        def add(key, expression):
            pairs.append(f"'{key}', {expression}")

        for key in ("owner", "errors"):
            add(key, source((key,)))
        fields = {"summary": SUMMARY_FIELDS, "learners": TABLE_FIELDS + SORT_FIELDS, "risk": RISK_FIELDS}.get(self.section)
        if fields:
            add("learners", array(("learners",), fields))
        if self.section == "summary":
            add("meetings", "jsonb_build_object('events', " + array(("meetings", "events"), POPUP_EVENT_FIELDS)
                + ", 'summary', " + source(("meetings", "summary"))
                + ", 'reviewGenerationIssues', " + source(("meetings", "reviewGenerationIssues")) + ")")
            add("marking", "jsonb_build_object('summary', " + source(("marking", "summary"))
                + ", 'items', " + array(("marking", "items"), MARKING_FIELDS) + ")")
        elif self.section == "meetings":
            add("meetings", "jsonb_build_object('events', " + array(("meetings", "events"), INPUT_FIELDS) + ")")
        elif self.section == "learners":
            add("meetings", "jsonb_build_object('events', " + array(("meetings", "events"), COUNT_EVENT_FIELDS) + ")")
        return "jsonb_build_object(" + ", ".join(pairs) + ")", params


def load_section(owner_email: str, section: str) -> dict | None:
    """Read the latest persisted projection without a synchronous cold rebuild."""
    from coach_api.models import CoachDashboardSnapshot

    queryset = CoachDashboardSnapshot.objects.filter(owner_email=owner_email).order_by("-refreshed_at")
    if connections[queryset.db].vendor == "postgresql":
        snapshot = queryset.annotate(
            section_payload=DashboardSectionProjection("payload", section),
        ).values("section_payload", "schema_version", "refreshed_at").first()
        if snapshot is None:
            return None
        payload = snapshot["section_payload"]
        version, refreshed_at = snapshot["schema_version"], snapshot["refreshed_at"]
    else:
        snapshot = queryset.only("payload", "schema_version", "refreshed_at").first()
        if snapshot is None:
            return None
        payload = project_section(snapshot.payload, section)
        version, refreshed_at = snapshot.schema_version, snapshot.refreshed_at
    payload["readModel"] = {"version": version, "refreshedAt": refreshed_at.isoformat()}
    if section != "meetings":
        payload = CoachDashboardService(owner_email).normalize_start_dates(payload, refresh_otjh=True)
        with dashboard_stage("otjh_enrichment"):
            normalize_otjh_contract(payload)
    if section == "learners":
        _overlay_latest_activity(payload)
    return payload


def normalize_otjh_contract(payload):
    """Refresh only derived OTJH scalars at read time, including stale caches.

    Actual hours and the programme plan/window stay untouched. All sections
    use the same backend rule and business date before filtering or counting.
    """
    from coach_api import views as domain

    today = timezone.localdate()
    for learner in payload.get("learners") or []:
        if not any(key in learner for key in ("otjhCompleted", "otjhPlanned", "otjhTarget", "otjhTargetAsOfToday")):
            continue
        domain.apply_otjh_to_date_metrics(learner, today=today)
    return payload


def _overlay_latest_activity(payload):
    from coach_api import views as domain

    completed = {}
    for event in (payload.pop("meetings", None) or {}).get("events") or []:
        day = domain.parse_schedule_date(event.get("scheduledDate") or event.get("date") or event.get("targetDate"))
        if event.get("status") == "completed" and day:
            key = str(event.get("learnerId"))
            completed[key] = max(completed.get(key, day), day)
    for learner in payload.get("learners") or []:
        day = completed.get(str(learner.get("id")))
        attendance = domain.parse_schedule_date(learner.get("attendanceLastSessionDate"))
        latest = max(filter(None, (day, attendance)), default=None)
        activity = domain.parse_schedule_date(learner.get("lastActivityDate"))
        if latest:
            learner["lastContact"] = latest.isoformat()
            if activity is None or latest > activity:
                learner.update(lastActivity=latest.isoformat(), lastActivityDate=latest.isoformat(), lastActivityLabel="Attendance")


def canonical_meeting_source(value):
    """Dashboard aliases only; keep stored meeting/Graph identity unchanged."""
    normalized = str(value or "").strip().lower().replace("_", "-")
    return {
        "mcr": "mcr", "mcm": "mcr", "monthly-coaching": "mcr",
        "pr": "progress-review", "progress-review": "progress-review",
        "catch-up": "catch-up",
    }.get(normalized)


def weekly_meeting_rows(events, learners, monday, friday):
    from coach_api import views as domain
    from coach_api.review_categories import review_event_category

    by_id = {str(item["id"]): item for item in learners if item.get("id") is not None and _is_active(item)}
    for event in events:
        source = canonical_meeting_source(review_event_category(event))
        learner = by_id.get(str(event.get("learnerId"))) if event.get("learnerId") is not None else None
        day = domain.parse_schedule_date(event.get("scheduledDate") or event.get("date") or event.get("targetDate"))
        if source and learner and day and monday <= day <= friday and event.get("status") not in {"cancelled", "completed"}:
            yield source, event, learner, day


def summarize_meetings(payload):
    meetings = payload.get("meetings") or {}
    events = meetings.get("events", []) or []
    # Same Monday-Friday display-date/status rule as isEventThisWeek. Use the
    # configured business timezone, never the server machine's local date.
    today = timezone.localdate()
    monday, friday = work_week(today)
    learners = _visible_learners(payload)
    weekly_rows = list(weekly_meeting_rows(events, learners, monday, friday))
    counts = {"progressReviews": 0, "monthlyCoaching": 0, "catchUps": 0}
    keys = {"progress-review": "progressReviews", "mcr": "monthlyCoaching", "catch-up": "catchUps"}
    for source, event, learner, day in weekly_rows:
        counts[keys[source]] += 1
    summary = meetings.get("summary") or {}
    available = (any(canonical_meeting_source(event.get("source")) in {"progress-review", "mcr"} for event in events)
                 or any((summary.get(key) or 0) > 0 for key in ("progressReviewRows", "mcrRows", "learnersWithDates"))
                 or not meetings.get("reviewGenerationIssues"))
    marking = payload.get("marking") or {}
    marking_summary = marking.get("summary") or {}
    pending = marking_summary.get("pendingItems")
    if pending is None and not (payload.get("errors") or {}).get("marking"):
        pending = len({str(item.get("id")) for item in marking.get("items") or [] if item.get("id")})
    payload.pop("weeklyCounts", None)
    payload.pop("totals", None)
    payload.update({
        "totalLearners": len(learners),
        "otjh": {
            "needAttention": sum(_is_active(item) and item.get("otjhRagStatus") == "need-attention" for item in learners),
        },
        "pendingMarking": pending,
        "meetingsThisWeek": {
            "pr": counts["progressReviews"] if available else None,
            "mcm": counts["monthlyCoaching"] if available else None,
            "catchUps": counts["catchUps"],
        },
    })
    return dashboard_popup_contract(payload, weekly_rows)


def dashboard_popup_contract(payload, weekly_rows):
    """Public DTO only; snapshot inputs and diagnostic metadata stay internal."""
    learners = _visible_learners(payload)
    rows = []
    for item in learners:
        rows.append({
            **_pick(item, ("id", "name")),
            # Upcoming learner meetings still use the Dashboard's established
            # Active/Delivery population. Identity without status empties it.
            "programmeStatus": item.get("rawProgramStatus"),
            "programme": next((item.get(key) for key in ("programmeName", "programme", "cohortName") if item.get(key) not in (None, "", "--")), None),
            "group": item.get("group"),
            "otjh": {"completed": item.get("otjhCompleted"), "target": item.get("otjhTargetAsOfToday"),
                     "planned": item.get("otjhPlanned"), "ragStatus": item.get("otjhRagStatus") or "unavailable"},
        })
    # First occurrence of each stable ID is the canonical popup row. The
    # count and rendering population must never apply different status rules.
    unique_rows = {}
    for row in rows:
        if row.get("id") is not None:
            unique_rows.setdefault(str(row["id"]), row)
    rows = list(unique_rows.values())
    at_risk_rows = [row for row in rows if row["otjh"]["ragStatus"] == "at-risk"]
    payload["otjh"]["atRisk"] = len(at_risk_rows)
    meetings_popup = {key: {"count": payload["meetingsThisWeek"][key], "items": []} for key in ("pr", "mcm", "catchUps")}
    for source, event, learner, day in weekly_rows:
        key = {"progress-review": "pr", "mcr": "mcm", "catch-up": "catchUps"}[source]
        meetings_popup[key]["items"].append({
            **_pick(event, ("id", "eventKey", "calendarEventId", "enrolmentId")),
            "learnerId": event.get("learnerId"), "learnerName": event.get("learner") or event.get("title") or learner.get("name"),
            "programme": event.get("programme"), "group": event.get("group"), "date": day.isoformat(),
            "time": event.get("scheduledTime") or event.get("timeLabel"),
            "durationMinutes": event.get("durationMinutes") or 60, "status": event.get("status"),
        })
    # Inputs may be learner aggregates or legacy individual submissions.
    # Group only by stable learner identity and preserve each input's weight.
    marking_rows = {}
    seen = set()
    for item in (payload.get("marking") or {}).get("items") or []:
        identity = item.get("learnerId")
        if identity is None:
            continue
        submission_id = item.get("id")
        if submission_id is not None:
            key = (str(identity), str(submission_id))
            if key in seen:
                continue
            seen.add(key)
        count = int(item.get("pendingEvidence", 1))
        if count <= 0:
            continue
        oldest = item.get("submittedAt") or item.get("lastSubmissionIso")
        row = marking_rows.setdefault(str(identity), {
            "learnerId": str(identity), "learnerName": item.get("learner"),
            "programme": item.get("programme"), "group": item.get("group"),
            "pendingCount": 0, "oldestPendingDate": None,
            **_pick(item, ("initials", "totalEvidence", "isOverdue")),
        })
        row["pendingCount"] += count
        if item.get("isOverdue"):
            row["isOverdue"] = True
        if oldest and (row["oldestPendingDate"] is None or oldest < row["oldestPendingDate"]):
            row["oldestPendingDate"] = oldest
    marking_items = list(marking_rows.values())
    # Unavailable marking remains null; a successful projection counts items,
    # never the number of learner groups.
    if payload["pendingMarking"] is not None:
        payload["pendingMarking"] = sum(row["pendingCount"] for row in marking_items)
    return {
        "owner": _pick(payload.get("owner") or {}, ("name",)),
        "summary": _pick(payload, ("totalLearners", "otjh", "pendingMarking", "meetingsThisWeek")),
        "learnerPopup": {"all": rows},
        "markingPopup": {"count": payload["pendingMarking"], "items": marking_items},
        "meetingsPopup": meetings_popup,
    }


def _is_active(item):
    status = str(item.get("rawProgramStatus") or "").strip().lower()
    normalized = re.sub(r"[\s_-]+", "", status)
    return normalized in {"active", "delivery"}


def _visible_learners(payload):
    from coach_api import views as domain

    return [item for item in payload.get("learners") or [] if not domain.is_hidden_caseload_programme_status(
        item.get("rawProgramStatus") if item.get("rawProgramStatus") not in (None, "", "--") else item.get("enrollmentStatus"),
    )]


def learner_table_row(item):
    """Public All Learners DTO; internal filter/sort/snapshot fields stay private.

    Preserve unavailable metrics as null rather than presenting fabricated zeros.
    OTJH scalars were already normalized by the backend before pagination.
    """
    def present(*keys):
        return next((item[key] for key in keys if item.get(key) not in (None, "", "--")), None)

    row = {
        "id": item.get("id"), "name": item.get("name"), "initials": item.get("initials"),
        "programme": item.get("programmeName") or item.get("programme"),
        "programmeStatus": item.get("rawProgramStatus"),
        "otjh": {
            "completed": item.get("otjhCompleted"),
            "targetToDate": item.get("otjhTargetAsOfToday"),
            "planned": item.get("otjhPlanned"),
            "progress": item.get("otjhProgressAsOfToday"),
            "ragStatus": item.get("otjhRagStatus") or "unavailable",
        },
        "activities": {
            "completed": item.get("componentsCompleted"), "total": item.get("componentsPlanned"),
            "progress": (item.get("activityProgress") if item.get("activityProgressAvailable") else
                         floor(min(max((item.get("componentsCompleted") or 0) / item["componentsPlanned"] * 100, 0), 100) + 0.5)
                         if (item.get("componentsPlanned") or 0) > 0 else None),
        },
        "attendance": {"rate": item.get("attendanceRate") if item.get("attendanceAvailable", item.get("attendanceRateAvailable")) else None},
        "startDate": item.get("startDate"),
        "lastActivity": {"date": present("lastActivityDate", "lastActivity", "attendanceLastSession", "lastSubmittedEvidence", "lastContact")},
        "lastPr": present("lastProgressReview", "lastPr"),
        "lastMcm": present("lastReview", "lastMcm"),
    }
    # Both identities are consumed by the case-file navigation and its tabs.
    row.update(_pick(item, ("learnerType", "enrolmentId")))
    return row


def all_learners(payload: dict) -> dict:
    """Complete coach dataset using the existing row DTO, with no per-row I/O."""
    learners = _visible_learners(payload)
    result = paginate_learners(payload, page=1, page_size=max(len(learners), 1),
                               search="", cohort="all", status="all", otjh_status="all",
                               sort="risk", direction="desc")
    # The table DTO deliberately omits identity/filter fields. Send these once
    # alongside it so local operations preserve the existing row JSON shape.
    result["learnerFilterData"] = {
        str(item["id"]): {**_pick(item, ("email", "cohortId", "cohortName", "group")), "urgency": _urgency(item)}
        for item in learners
    }
    return result


def paginate_learners(payload: dict, *, page: int, page_size: int, search: str,
                      cohort: str, status: str, otjh_status: str, sort: str, direction: str) -> dict:
    """Filter/sort the small snapshot DTOs before paging; never enrich per page."""
    from coach_api import views as domain

    learners = _visible_learners(payload)

    def text(value):
        return str(value or "--").strip()

    def options(key):
        return [{"value": value, "label": value} for value in sorted({text(item.get(key)) for item in learners} - {"--"}, key=str.casefold)]

    cohorts = {text(item.get("cohortId")) if text(item.get("cohortId")) != "--" else text(item.get("cohortName")): text(item.get("cohortName")) for item in learners}
    filter_options = {
        "cohort": [{"value": key, "label": label} for key, label in sorted(cohorts.items(), key=lambda pair: pair[1].casefold()) if label != "--"],
        "group": options("group"), "programStatus": options("rawProgramStatus"), "employer": [],
    }
    matched = []
    for item in learners:
        if search and not any(search.casefold() in text(item.get(key)).casefold() for key in ("name", "email", "programmeName", "programme")):
            continue
        if cohort.startswith("group:"):
            if text(item.get("group")) != cohort[6:]:
                continue
        elif cohort != "all":
            value = cohort.removeprefix("cohort:")
            if value not in (text(item.get("cohortId")), text(item.get("cohortName"))):
                continue
        if status != "all" and text(item.get("rawProgramStatus")) != status:
            continue
        if otjh_status != "all" and item.get("otjhRagStatus", "unavailable") != otjh_status:
            continue
        matched.append(item)

    def number(value):
        try:
            return float(value) if value is not None else None
        except (ValueError, TypeError):
            return None

    def date_value(value):
        return domain.parse_schedule_date(value)

    def value(item):
        if sort == "risk":
            return _urgency(item)
        if sort == "name":
            return text(item.get("name")).casefold()
        if sort == "otjh":
            return number(item.get("otjhProgressAsOfToday"))
        if sort == "components":
            planned = number(item.get("componentsPlanned"))
            return (number(item.get("componentsCompleted")) or 0) / planned if planned else None
        if sort == "attendance":
            return number(item.get("attendanceRate")) if item.get("attendanceAvailable", item.get("attendanceRateAvailable")) else None
        keys = {"start-date": ("startDate",), "activity": ("lastActivity", "attendanceLastSession", "lastSubmittedEvidence", "lastContact"),
                "progress-review": ("lastProgressReview", "lastPr"), "monthly-coaching": ("lastReview", "lastMcm")}
        return next((parsed for key in keys.get(sort, ()) if (parsed := date_value(item.get(key)))), None)

    def compare(left, right):
        a, b = value(left), value(right)
        tie = (text(left.get("name")).casefold() > text(right.get("name")).casefold()) - (text(left.get("name")).casefold() < text(right.get("name")).casefold())
        if a is None or b is None:
            return tie if a is b else 1 if a is None else -1
        delta = (a > b) - (a < b)
        return delta * (-1 if direction == "desc" else 1) or tie

    matched.sort(key=cmp_to_key(compare))
    total = len(matched)
    total_pages = ceil(total / page_size) if total else 0
    page = min(page, max(total_pages, 1))
    result = {key: value for key, value in payload.items() if key not in {"learners", "meetings"}}
    result.update({
        "results": [learner_table_row(item) for item in matched[(page - 1) * page_size:page * page_size]],
        "filterOptions": filter_options,
        "pagination": {"page": page, "pageSize": page_size, "total": total, "totalPages": total_pages,
                       "hasNext": page < total_pages, "hasPrevious": page > 1},
    })
    return result


def _urgency(item):
    """The embedded table's buildLearnerInsight urgency, applied before paging.

    Only scalar snapshot metrics are used. KSB scalars are retained internally
    for this existing sort and are excluded from the table response.
    """
    from coach_api import views as domain

    stage = re.sub(r"\s+", "", str(item.get("rawProgramStatus") or "").lower())
    if stage in {"break", "onbreak", "onabreak", "withdrawn", "readytoenrol"}:
        return 0
    severities = []
    rag = item.get("otjhRagStatus")
    if rag in {"at-risk", "need-attention"}:
        severities.append(3 if rag == "at-risk" else 2)
    rate = item.get("attendanceRate")
    # The embedded table's attendance DTO supplies neither risk nor missed
    # streaks to buildLearnerInsight; don't introduce new urgency signals here.
    if item.get("attendanceRateAvailable") and (item.get("componentsPlanned") or 0) > 0 and (rate or 0) < 25:
        severities.append(2)
    if item.get("ksbProgressAvailable") and (item.get("ksbTarget") or 0) > 0:
        if not item.get("ksbCompleted") or (item.get("ksbProgress") or 0) < 25 or str(item.get("ksbStatus") or "").lower() == "not started":
            severities.append(2)
    gateway = domain.parse_schedule_date(item.get("lastProgressReview") or item.get("lastPr") or item.get("lastReview") or item.get("lastMcm"))
    today = timezone.localdate()
    if gateway:
        days = (gateway - today).days
        if days <= 90:
            severities.append(3 if days < 0 else 2 if days <= 30 else 1)
    activity = domain.parse_schedule_date(item.get("lastActivityDate") or item.get("attendanceLastSessionDate"))
    if activity and (today - activity).days >= 28:
        severities.append(2)
    tier_weight = {3: 4000, 2: 3000, 1: 2000, 0: 1000}[max(severities, default=0)]
    behind = min(max(-(item.get("otjhDeltaHours") or 0), 0), 400)
    return tier_weight + severities.count(3) * 100 + len(severities) * 10 + behind / 10
