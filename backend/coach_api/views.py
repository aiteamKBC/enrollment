from .review_sources import native_review_creation_policy, require_native_review_creation, ReviewCreationBlocked
import json
import logging
from time import perf_counter
import os
import re
import hashlib
import threading
from html import escape
from collections import defaultdict
from dataclasses import dataclass
# `time` below is datetime.time, so the sleep function is imported under its own
# name to avoid shadowing it.
from time import monotonic, perf_counter, sleep as _sleep
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, close_old_connections, connections, router, transaction
from django.db.utils import ConnectionDoesNotExist
from django.db.models import Max, Q
from django.db.models.functions import Lower, Trim
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from coach_api.auth import (
    attributed_write_view,
    authenticated_coach_email,
    coach_access_required,
    is_coach_view_as,
)
from coach_api.errors import coach_error, request_id_for
from coach_api.cache.learners import (
    acquire_caseload_lock,
    cache_caseload,
    coach_caseload_cache_key,
    coach_caseload_lock_key,
    get_cached_caseload,
    release_caseload_lock,
)
from coach_api.models import CoachAbsenceReport, CoachAttendanceSourceAdjustment, CoachCalendarEvent, CoachCalendarSequence, CoachDashboardSnapshot, CoachManualAttendance, ImportedReviewInstance
from coach_api import imported_booking_state
from coach_api.migrated_reviews import (
    EDITABLE_STATUSES as MIGRATED_EDITABLE_STATUSES,
    booking_event_type as migrated_booking_event_type,
    initial_local_status, programme_key as migrated_programme_key,
    render_sections as render_migrated_sections,
    review_family as migrated_review_family,
    source_is_completed as migrated_source_is_completed,
    validate_answers as validate_migrated_answers,
)
from coach_api.migrated_templates import (
    resolve_template as resolve_migrated_template, resolution_metadata,
    snapshot_for as migrated_template_snapshot, snapshot_assignment_valid,
)
from coach_api.services.learners.context import CaseloadRequestContext
from coach_api.validation import (
    ObjectValidator,
    ValidationError,
    parse_json_object,
    validate_month,
    validation_error_response,
)
from config.observability import metric_event
from login.permissions import require_access
from learner_api.evidence_storage import (
    azure_configured,
    blob_url,
    move_blob,
    parse_blob_url,
    resolve_read_url,
)
from learner_api.models import (
    CommercialUser,
    Employer,
    EnrolmentUser,
    LearnerAbsence,
    LearnerProgressEntry,
    LearnerProfile,
    StaffUser,
    learner_activity_events_relation_exists,
    learner_ksbs_relation_exists,
)
from learner_api.constants import ACCESS_COACH, ACCESS_SUPER_ADMIN
from learner_api.ksb_exclusions import and_not_excluded
from learner_api import canonical_learning
# The one audited shape for the submissions table, shared with the two learner
# write paths so all three record the same columns. See submission_audit.
from learner_api.submission_audit import (
    SUBMISSION_AUDIT_COLUMNS,
    SUBMISSION_AUDIT_SQL,
    record_submission_row,
)
from learner_api.active_users import components_target_to_date, completed_hours_value_from_progress, current_curriculum_ksb_items_for_learner, current_week_label, dedupe_otjh_progress_records, hydrate_training_plan, refresh_learner_ksb_snapshot
from learner_api.calendar_connections import (
    booking_conflicts as personal_calendar_booking_conflicts,
)
from learner_api.learner_detail import otjh_status_from_variance, refresh_learner_otjh_snapshot
from learner_api.dashboard_metrics import (read_metrics, load_subject_attempts_bulk,
                                            load_manual_hours_bulk, load_reflection_submissions_bulk,
                                            load_audit_inputs_bulk, load_native_progress_bulk,
                                            load_native_components_bulk, load_planned_hours_documents_bulk,
                                            load_contracts_bulk, load_accepted_ksb_rows_bulk)
from learner_api.dashboard_metrics import load_export_links_bulk
from learner_api.learning_plan import _effective_plan_ids
from learner_api.student_activity import load_direct_progress_records_bulk
from learner_api.ksb_codes import extract_ksb_codes, normalize_ksb_parent_code
from learner_api.progress_rules import progress_record_counts_as_achieved
from audit_api.last_audit_ledger_views import _connection as audit_connection
from learner_api.student_activity_access import student_activity_available
from learner_api import canonical_learning
from learner_api.student_activity_data import read_audit_hour_totals_bulk, read_evidenced_ksb_counts_bulk
from old_otjh.service import ServiceError
from learner_api.attendance import (
    _summarize_attendance,
    combined_attendance_rows,
    fetch_kbc_attendance_rates,
    fetch_kbc_attendance_rows_bulk,
    fetch_recent_kbc_attendance_rows,
)
from learner_api.review_history import REVIEW_TYPES, _sections_by_review, _serialize_review
from learner_api.teams_attendance import fetch_verified_teams_attendance_rows
from curriculum_api.views import (
    slugify as curriculum_slugify,
    actual_cohort_identity,
    actual_group_identity,
    apply_teams_meeting_options,
    attendance_display_name,
    attendance_identity,
    attendance_interval_seconds,
    AUTHORING_COMPONENTS_TABLE,
    AUTHORING_MODULES_TABLE,
    AUTHORING_WEEKS_TABLE,
    active_component_rows,
    active_week_rows,
    authoring_fetch_all,
    authoring_modules_as_training_rows,
    build_module_session_plan,
    COHORT_AUTHORING_DETAILS_TABLE,
    cohort_selected_holidays_by_cohort,
    delivery_days_per_week,
    get_program_config_rows,
    get_training_rows,
    england_non_delivery_reason,
    group_authoring_detail_rows,
    GROUPS_TABLE,
    is_operational_training_row,
    LIVE_SESSION_OCCURRENCES_TABLE,
    LIVE_SESSIONS_TABLE,
    parse_date,
    parse_graph_datetime,
    parse_int,
    parse_json_value,
    program_config_by_id,
    programme_config_by_identifier,
    programme_config_id,
    programme_identity,
    schedule_time_parts,
    teams_meeting_base_path,
    teams_online_meeting_from_join_url,
)
from curriculum_api import review_instances as curriculum_review_instances
from curriculum_api import review_types as curriculum_review_types
from .review_categories import imported_review_category, review_event_category
from curriculum_api import reviews as curriculum_reviews
from learner_api.review_progress_snapshot import (
    UnresolvedTrainingPlanTarget,
    build_progress_snapshot,
)


logger = logging.getLogger(__name__)

# Compatibility export for established imports. URL routing uses the thin
# Dashboard HTTP module directly; canonical rebuild helpers remain below until
# their cross-schema behavior can be split independently.
from .dashboard_view import coach_dashboard


def _coach_perf(endpoint, stage, started, *, learner_count=None, **extra):
    payload = {
        "event": "coach_perf", "endpoint": endpoint, "stage": stage,
        "duration_ms": round((perf_counter() - started) * 1000, 2),
    }
    if learner_count is not None:
        payload["learner_count"] = learner_count
    payload.update(extra)
    logger.info("coach_perf %s", payload)
PROGRESS_REVIEW_RESPONSE_IDS = {
    "attendance_issues",
    "workplace_training_since_review",
    "ksb_learning_activities",
    "evidence_timely",
    "other_progress_issues",
    "other_progress_issues_detail",
    "learner_attitude_pride_rating",
    "learner_collaboration_rating",
    "learner_time_management_rating",
    "learner_respect_empathy_rating",
    "learner_english_confidence_rating",
    "learner_maths_confidence_rating",
    "learner_wider_skills_rating",
    "learner_workplace_behaviours_rating",
    "learner_provider_safeguarding_confidence_rating",
    "learner_employer_safeguarding_confidence_rating",
    "learner_provider_support_rating",
    "learner_manager_support_rating",
    "learner_additional_comments",
    "manager_learning_application_rating",
    "manager_wider_skills_rating",
    "manager_workplace_behaviours_rating",
    "manager_valued_application",
    "manager_progress_summary",
    "tutor_learning_attitude_rating",
    "tutor_time_management_rating",
    "tutor_respect_empathy_rating",
    "tutor_english_maths_rating",
    "tutor_workplace_behaviours_rating",
    "tutor_strengths",
    "tutor_progress_summary",
    "safeguarding_understood",
    "prevent_understood",
    "safeguarding_reporting_understood",
    "safeguarding_reporting_process",
    "safeguarding_concerns",
    "safeguarding_concerns_detail",
    "key_theme",
    "key_theme_other",
    "key_theme_comments",
    "additional_learning_support",
    "additional_learning_support_detail",
    "health_adjustments",
    "health_adjustments_detail",
    "other_support_circumstances",
    "other_support_detail",
    "previous_targets_achieved",
    "previous_targets_detail",
    "targets_actions",
    "action_owners_dates",
    "rag_status",
    "rag_reason",
}
PROGRESS_REVIEW_OPTIONAL_RESPONSE_IDS = {
    "other_progress_issues_detail",
    "learner_additional_comments",
    "safeguarding_concerns_detail",
    "key_theme_other",
    "additional_learning_support_detail",
    "health_adjustments_detail",
    "other_support_detail",
    "previous_targets_detail",
}
PROGRESS_REVIEW_REQUIRED_RESPONSE_IDS = (
    PROGRESS_REVIEW_RESPONSE_IDS - PROGRESS_REVIEW_OPTIONAL_RESPONSE_IDS
)
PROGRESS_REVIEW_RATING_RESPONSE_IDS = {
    response_id
    for response_id in PROGRESS_REVIEW_RESPONSE_IDS
    if response_id.endswith("_rating")
}
PROGRESS_REVIEW_YES_NO_RESPONSE_IDS = {
    "attendance_issues",
    "workplace_training_since_review",
    "ksb_learning_activities",
    "evidence_timely",
    "other_progress_issues",
    "safeguarding_understood",
    "prevent_understood",
    "safeguarding_reporting_understood",
    "safeguarding_concerns",
    "additional_learning_support",
    "health_adjustments",
    "other_support_circumstances",
    "previous_targets_achieved",
}
PROGRESS_REVIEW_KEY_THEMES = {
    "Health & Safety",
    "Prevent",
    "British Values",
    "Equality, Diversity & Inclusion",
    "Online Safety",
    "Wellbeing",
    "Other",
}
MONTHLY_COACHING_RESPONSE_IDS = {
    "mcm_previous_meeting",
    "mcm_previous_summary",
    "mcm_presentation_summary",
    "mcm_knowledge_reflection",
    "mcm_skills_reflection",
    "mcm_behaviour_reflection",
    "mcm_behaviour_next_step",
    "mcm_next_month_focus",
    "mcm_expected_evidence",
    "mcm_learning_resources",
    "mcm_resources_read",
    "mcm_paid_hours_confirmed",
    "mcm_workload_manageable",
    "mcm_wellbeing_impact",
    "mcm_wellbeing_outcome",
    "mcm_safeguarding_contact_confidence",
    "mcm_wellbeing_support_confidence",
    "mcm_safe_and_respected",
    "mcm_online_safety_prevent",
    "mcm_raise_concerns_confidence",
    "mcm_provider_safeguarding_confidence",
    "mcm_learner_feedback",
    "mcm_curriculum_planned",
    "mcm_teaching_well_delivered",
    "mcm_resources_accessible",
    "mcm_assessment_feedback_helpful",
    "mcm_tutor_support",
    "mcm_overall_learning_progress",
    "mcm_next_meeting_booked",
    "mcm_next_meeting_date",
    "mcm_meeting_summary",
    "mcm_meeting_notes",
    "mcm_outcome",
}
MONTHLY_COACHING_REQUIRED_RESPONSE_IDS = (
    MONTHLY_COACHING_RESPONSE_IDS - {"mcm_meeting_notes"}
)
MONTHLY_COACHING_YES_NO_RESPONSE_IDS = {
    "mcm_paid_hours_confirmed",
    "mcm_next_meeting_booked",
}
MONTHLY_COACHING_AGREEMENT_RESPONSE_IDS = {
    "mcm_workload_manageable",
    "mcm_wellbeing_impact",
    "mcm_safeguarding_contact_confidence",
    "mcm_wellbeing_support_confidence",
    "mcm_safe_and_respected",
    "mcm_online_safety_prevent",
    "mcm_raise_concerns_confidence",
    "mcm_provider_safeguarding_confidence",
    "mcm_curriculum_planned",
    "mcm_teaching_well_delivered",
    "mcm_resources_accessible",
    "mcm_assessment_feedback_helpful",
    "mcm_tutor_support",
    "mcm_overall_learning_progress",
}
DEFAULT_ATTENDANCE_DATABASE = "AiTeamKBC"
DEFAULT_MARKING_OWNER_ID = 6452
# Delivery learners have completed the delivery step and are waiting for the
# invitation that moves them to the stored Active status. Coach-facing pages
# should keep them in the same working caseload while that hand-off is pending.
ATTENDANCE_INCLUDED_STATUSES = {"active", "break"}
MARKING_OVERDUE_DAYS = 7

#: A draft is work the learner saved but has not handed in, so it is not the
#: coach's to mark. Without this the queue's All tab listed drafts (rendered as
#: "Pending" by the SPA's fallback label) that no status tab would ever show.
#: ``is distinct from`` keeps any row with a NULL status, as before.
MARKING_QUEUE_EXCLUDE_DRAFTS_SQL = "status is distinct from 'draft'"

#: Ceiling on coach feedback. Generous rather than tight: the stored column is
#: unbounded text, and the AI-assisted draft alone runs to 6000-7500 characters
#: before the coach edits it. Mirrored by the SPA's own counter.
MARKING_FEEDBACK_MAX_LENGTH = 20000

#: Activity types marked as a work product rather than through the learner's
#: reflection on the activity. An assignment produces an artefact the coach
#: assesses against the KSBs it carries and the end-point assessment plan;
#: a video, reading, podcast or quiz is evidenced by the reflection written
#: about it, which is a shorter and different judgement. Mirrored in the SPA by
#: frontend/src/lib/markingKind.ts -- keep the two in step.
ASSIGNMENT_ACTIVITY_TYPES = ("assignment", "extra_activity")

#: The two values the queue's ``kind`` parameter accepts.
MARKING_KINDS = ("assignment", "reflection")
TIMETABLE_SCHEDULE_SLOTS = (9, 10, 11, 13, 14, 15, 16)
ENV_FILE_NAME = ".env"
LEARNER_ABSENCE_RELATION_CANDIDATES = (
    '"Learner"."Absence"',
    '"Learner"."absence"',
    '"learner"."Absence"',
    '"learner"."absence"',
)
LEARNER_EVIDENCE_FILES_RELATION_CANDIDATES = (
    '"Learner"."evidence_files"',
    '"learner"."evidence_files"',
)
COACH_RAG_LABELS = {
    "green": "Green",
    "amber": "Amber",
    "red": "Red",
}
# Legacy fixed-interval constant. No longer the source of truth for MCM/PR
# occurrence dates -- see resolve_curriculum_review_occurrences, which reads
# recurrence/eligibility from curriculum.review_templates instead. A Review
# Type never implies a recurrence: Curriculum's schedule is the only source
# of truth for when a Review of any type happens. Kept only as the fallback
# numbering base for progress_reviews_api.period's PPTX pack generation until
# a programme's Progress Review template is configured in Curriculum (see
# that module for why the two must agree). There is no MCM equivalent left:
# the old 30-day constant had no other consumer and has been removed.
TIMETABLE_PROGRESS_REVIEW_INTERVAL = timedelta(weeks=12)
TIMETABLE_DEFAULT_DURATION_MINUTES = 60

# Curriculum review_types.code values -- the Curriculum-side classification
# every Review template carries. Coach never matches a Review by its display
# name: a Review named "Monthly Learner Catch-up" whose TYPE is Monthly
# Coaching Meeting still routes to the MCR bucket, and renaming it changes
# nothing. These codes say "which of these two Coach pages" and nothing about
# a Review's business rules -- recurrence, questions, signatures and
# eligibility all come from the template itself.
REVIEW_TYPE_CODE_MCM = "mcm"
REVIEW_TYPE_CODE_PROGRESS_REVIEW = "progress_review"

# Every Curriculum Review of any other type -- a custom type a curriculum
# admin created, or a template not yet classified -- still belongs on the
# calendar, under this generic event type. It is a routing bucket only, and
# it deliberately leaves the two filters above untouched.
GENERIC_REVIEW_EVENT_TYPE = "review"

REVIEW_TYPE_EVENT_TYPES = {
    REVIEW_TYPE_CODE_MCM: "mcr",
    REVIEW_TYPE_CODE_PROGRESS_REVIEW: "progress-review",
}

# Only these event buckets are allowed to carry a Curriculum Review template.
# Booked sessions such as catch-up and student-support are separate workflows;
# a stale or malformed review_template_id on one of those rows must not turn it
# into a Review when it is serialized for the timetable.
CURRICULUM_REVIEW_EVENT_TYPES = frozenset({"mcr", "progress-review", GENERIC_REVIEW_EVENT_TYPE})


def review_event_type_for_type_code(review_type_code: str | None) -> str:
    """Which calendar bucket a Review's occurrences appear under, decided by
    its Review Type code alone.

    Lossy on purpose -- it exists to keep the two dedicated Coach pages
    working. Anything that needs to tell one custom Review Type from another
    reads the review_type_* fields instead (see review_type_event_fields).
    """
    return REVIEW_TYPE_EVENT_TYPES.get(clean_text(review_type_code).lower(), GENERIC_REVIEW_EVENT_TYPE)


def review_type_event_fields(review_type: dict | None) -> dict:
    """The Review Type block every review-driven Coach calendar event carries.

    Same four fields, same meanings, as the learner calendar sends (see
    learner_api.calendar._review_type_fields) so one frontend helper can
    bucket both:

        reviewTypeId        stable filter identity
        reviewTypeCode      stable routing code ('mcm', 'career_review', ...)
        reviewTypeName      the FILTER label -- never the card title, which
                            stays review_templates.name
        reviewTypeIsSystem  orders system types ahead of custom ones without
                            matching a code literal

    All four are None/False for a legacy occurrence whose template has no
    Review Type, which the frontend keeps in a generic "Review" bucket.
    """
    review_type = review_type or {}
    return {
        "reviewTypeId": clean_text(review_type.get("reviewTypeId")) or None,
        "reviewTypeCode": clean_text(review_type.get("reviewTypeCode")) or None,
        "reviewTypeName": clean_text(review_type.get("reviewTypeName")) or None,
        "reviewTypeIsSystem": bool(review_type.get("reviewTypeIsSystem")),
    }


# Last-resort wording for an occurrence whose Curriculum template could not be
# read at all. Every normal event takes its title from review_templates.name.
EVENT_TYPE_FALLBACK_TITLES = {
    "mcr": "Monthly Coaching",
    "progress-review": "Progress Review",
    GENERIC_REVIEW_EVENT_TYPE: "Review",
}


def review_type_fields_by_template(template_ids) -> dict:
    """{review_template_id: review_type_event_fields(...)} for a batch of rows.

    A STORED calendar row links to Curriculum only by ``review_template_id``,
    so its classification is whatever that template points at right now --
    reclassifying a Review in Curriculum re-buckets its existing events, which
    is the intent (Review Type is routing metadata; the frozen
    definition_snapshot on a review instance is what protects the answers).

    Batched deliberately: one pass over the wanted templates plus one tiny
    catalogue read, rather than a pair of queries per event.
    """
    wanted = {clean_text(template_id) for template_id in template_ids if clean_text(template_id)}
    if not wanted:
        return {}
    try:
        from curriculum_api import review_types as curriculum_review_types

        type_index = curriculum_review_types.review_type_index()
        resolved = {}
        for template_row in curriculum_reviews.get_review_template_rows(
            f"id in ({', '.join(['%s'] * len(wanted))})", sorted(wanted), include_deleted=True,
        ):
            template_id = template_row['id']
            type_row = type_index.get(clean_text(template_row.get("review_type_id")))
            if not type_row:
                continue
            resolved[template_id] = review_type_event_fields({
                "reviewTypeId": type_row.get("id"),
                "reviewTypeCode": type_row.get("code"),
                "reviewTypeName": type_row.get("name"),
                "reviewTypeIsSystem": type_row.get("is_system"),
            })
        return resolved
    except Exception:
        # Curriculum being unreachable costs the coach their Review filters,
        # never their calendar.
        logger.warning("Could not resolve Review Types for the coach calendar.", exc_info=True)
        return {}


def resolve_curriculum_programme_id(programme_name: str | None) -> str | None:
    """A learner's `programme` display name -> curriculum.programmes.programme_id.

    Returns None if the name does not resolve to any configured Curriculum
    programme -- callers must treat that as "no Curriculum Review templates
    are reachable for this learner right now", not fall back to any
    hard-coded schedule.
    """
    name = clean_text(programme_name)
    if not name:
        return None
    # A learner row carries the programme's *display* name ("Final Test"), while
    # programme_config_by_identifier matches ids and slugs -- so the slug form is
    # tried too, otherwise every learner looks like they are on no Curriculum
    # programme at all and no Review ever reaches their calendar.
    config = programme_config_by_identifier(name) or programme_config_by_identifier(curriculum_slugify(name))
    if not config:
        return None
    return programme_config_id(config) or None


def resolve_curriculum_review_occurrences(
    *,
    programme_id: str | None,
    learner_id: int,
    learner_status: str | None,
    learner_start_date: date,
    window_start: date,
    window_end: date,
    template_cache: dict[str, list[dict]],
    learner_scope: dict | None = None,
) -> list[dict]:
    """RAW occurrences of every enabled Review template on this learner's
    programme -- or [] if the programme has none configured, they are all
    disabled, or the learner is not currently eligible for any of them.

    There is deliberately no list of Review types here: Coach asks Curriculum
    what this learner's programme produces and renders whatever comes back.
    See curriculum_api.review_instances for the engine itself.
    """
    if not programme_id:
        return []
    try:
        return curriculum_review_instances.resolve_programme_review_occurrences(
            programme_id,
            learner_id,
            learner_status,
            learner_start_date,
            window_start,
            window_end,
            template_cache=template_cache,
            learner_scope=learner_scope,
        )
    except Exception as exc:  # Curriculum being unreachable must not blank the calendar entirely.
        logger.warning("Could not resolve Curriculum reviews for programme %s: %s", programme_id, exc)
        return []
MICROSOFT_GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"
MICROSOFT_GRAPH_DEFAULT_BASE_URL = "https://graph.microsoft.com/v1.0"
MICROSOFT_GRAPH_DEFAULT_TIMEZONE = "GMT Standard Time"
CATCH_UP_EVENT_TYPE = "catch-up"


def load_env_file() -> None:
    if getattr(load_env_file, "_loaded", False):
        return

    env_path = Path(__file__).resolve().parent.parent / ENV_FILE_NAME
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

    load_env_file._loaded = True


def get_aptem_connection_string() -> str:
    load_env_file()
    return os.environ.get("APTEMAUTOEXTRACTINGDATABASE", "")


def get_kbc_attendance_connection_string() -> str:
    load_env_file()
    connection_string = os.environ.get("KBCDATABASE", "")
    if not connection_string:
        raise RuntimeError("KBCDATABASE is not configured.")

    database_name = os.environ.get("KBC_ATTENDANCE_DATABASE", DEFAULT_ATTENDANCE_DATABASE)
    try:
        conninfo = conninfo_to_dict(connection_string)
        conninfo["dbname"] = database_name
        return make_conninfo(**conninfo)
    except Exception as exc:
        raise RuntimeError(f"Unable to prepare KBC attendance database connection: {exc}") from exc


def to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def to_int(value) -> int:
    return int(to_decimal(value))


def to_number(value) -> float:
    return float(to_decimal(value))


def percentage(numerator, denominator) -> int:
    denominator_value = to_decimal(denominator)
    if denominator_value <= 0:
        return 0
    numerator_value = to_decimal(numerator)
    value = int(round((numerator_value / denominator_value) * 100))
    return max(0, min(100, value))


def apply_otjh_to_date_metrics(payload: dict, *, today: date | None = None) -> dict:
    """Attach the canonical coach-facing OTJH target and RAG fields.

    ``otjhPlanned`` is the whole-programme plan and ``otjhCompleted`` is the
    active canonical actual.  The dashboard target is the portion of that plan
    due by the business date, paced over the programme window.  These fields
    are emitted by the API so cards, tables, filters and KPI legends do not
    each re-implement the denominator in the browser.

    A missing/invalid programme window stays unavailable. Legacy target fields
    can contain whole-programme totals and cannot supply target-to-date.
    """
    total = to_number(payload.get("otjhPlanned"))

    start = parse_date_value(payload.get("otjhProgrammeStartDate", payload.get("startDate")))
    end = parse_date_value(payload.get("plannedEndDate"))
    start_day = start.date() if isinstance(start, datetime) else start
    end_day = end.date() if isinstance(end, datetime) else end
    current_day = today or timezone.localdate()

    target: float | None = None
    target_source = "unavailable"
    if total > 0 and start_day and end_day and end_day > start_day:
        if current_day < start_day:
            target = 0.0
        elif current_day >= end_day:
            target = total
        else:
            elapsed_days = (current_day - start_day).days
            programme_days = (end_day - start_day).days
            target = max(0.0, min(total, total * elapsed_days / programme_days))
        target_source = "ssot:programme-plan-window"

    actual = to_number(payload.get("otjhCompleted"))
    # Classify the same precision exposed by the API, so a rounded 40h
    # shortfall cannot be labelled >40h by an unrounded intermediate.
    target = round(target, 2) if target is not None else None
    if target is None or payload.get("otjhCompleted") is None:
        status = "unavailable"
        percent = None
        shortfall = None
        delta = None
    else:
        shortfall = round(max(target - actual, 0.0), 2)
        delta = actual - target
        # Keep the frontend boundary exact: >40 is red, >20 is amber.
        status = "at-risk" if shortfall > 40 else "need-attention" if shortfall > 20 else "on-track"
        percent = max(0.0, min(100.0, (actual / target) * 100)) if target > 0 else None

    payload["otjhTargetAsOfToday"] = round(target, 2) if target is not None else None
    payload["otjhProgressAsOfToday"] = round(percent, 2) if percent is not None else None
    payload["otjhShortfallHours"] = round(shortfall, 2) if shortfall is not None else None
    payload["otjhDeltaHours"] = round(delta, 2) if delta is not None else None
    payload["otjhRagStatus"] = status
    payload["otjhRagSource"] = target_source
    return payload


def parse_variance(value: str | None) -> int:
    if not value:
        return 0
    match = re.search(r"-?\d+", value)
    return int(match.group()) if match else 0


def format_date(value) -> str:
    if not value:
        return "--"
    parsed = parse_date_value(value)
    if not parsed:
        return clean_text(value) or "--"
    return parsed.strftime("%d %b %Y")


def format_iso_date(value) -> str | None:
    if not value:
        return None
    return value.isoformat()


def parse_date_value(value):
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt)
        except ValueError:
            continue
    return None


def format_date_value(value) -> str:
    parsed = parse_date_value(value)
    if parsed:
        return parsed.strftime("%d %b %Y")
    return str(value).strip() if value not in (None, "") else "--"


def format_iso_date_value(value) -> str | None:
    parsed = parse_date_value(value)
    return parsed.date().isoformat() if isinstance(parsed, datetime) else parsed.isoformat() if parsed else None


def parse_time_value(value) -> time | None:
    if not value:
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)

    text = str(value).strip()
    if not text:
        return None

    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    return None


def format_time_value(value) -> str | None:
    parsed = parse_time_value(value)
    return parsed.strftime("%H:%M") if parsed else None


def parse_schedule_date(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip()
    if not text or text.lower() in {"null", "not yet", "()"}:
        return None

    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if iso_match:
        year, month, day = iso_match.groups()
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None

    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
    if not match:
        return None

    day, month, year = match.groups()
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def normalize_schedule_status(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text.lower() in {"null", "not yet", "()"}:
        return "--"

    bracket_match = re.search(r"\(([^)]+)\)", text)
    if bracket_match:
        text = bracket_match.group(1).strip()

    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if "completed" in normalized:
        return "Completed"
    if "not scheduled" in normalized:
        return "Not Scheduled"
    if "scheduled" in normalized:
        return "Scheduled"
    if "in progress" in normalized:
        return "In Progress"
    return text


def normalize_schedule_label(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\d{1,2}[-/]\d{1,2}[-/]\d{4}", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = text.strip(" -")
    return text or fallback


def build_initials(full_name: str | None) -> str:
    if not full_name:
        return "?"
    parts = [part for part in full_name.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def schedule_status_label(value: str | None) -> str:
    normalized = clean_text(value).lower()
    labels = {
        "not-scheduled": "Not Scheduled",
        "scheduled": "Scheduled",
        "in-progress": "In Progress",
        "awaiting-signature": "Awaiting Signature",
        "completed": "Completed",
        "cancelled": "Not Scheduled",
    }
    return labels.get(normalized, clean_text(value) or "Not Scheduled")


def build_timetable_event_key(learner_id: int, event_type: str, sequence: int, target_date: date) -> str:
    return f"{event_type}:{learner_id}:{sequence}:{target_date.isoformat()}"


def build_catchup_template_event_key(owner_email: str, learner_id: int) -> str:
    return f"{CATCH_UP_EVENT_TYPE}:{normalize_email(owner_email)}:{learner_id}:template"


# Titles for coach-calendar session types. The historical name is kept because
# it is referenced in a few serializers, but it is no longer the source of truth
# for "learner booked this" behaviour.
BOOKED_EVENT_TITLES = {
    "first-session": "First Session",
    "catch-up": "Catch-up Session",
    "student-support": "Student Support",
    "mcr": "Monthly Coaching",
    "progress-review": "Progress Review",
    "review": "Review",
    "gateway": "Gateway",
    "other": "Other",
    "eligibility-review": "Eligibility Review & FS Discussion",
    "workspace": "RPL And Experience",
    "training-plan": "Workplace Health & Safety Declaration",
    "uln-privacy": "ULN Privacy Notice & Learner Acknowledgement",
    "lms-introduction": "LMS Introduction",
}

# A newly invited learner's request for a one-to-one platform walkthrough with
# their case owner (login.lms_introduction). Saved as not-scheduled; the case
# owner places it from the timetable like a student-support request.
LMS_INTRODUCTION_EVENT_TYPE = "lms-introduction"

# Types whose copy should still say the learner booked the slot. Graph ownership
# is deliberately separate: coach-calendar meetings are always created on the
# coach/owner mailbox, with the learner as an attendee.
LEARNER_BOOKED_EVENT_TYPES = {
    CATCH_UP_EVENT_TYPE,
    "student-support",
    "gateway",
    "other",
    "eligibility-review",
    "workspace",
    "training-plan",
    "uln-privacy",
    LMS_INTRODUCTION_EVENT_TYPE,
}

# Session types the coach can book from their own timetable page.
COACH_BOOKABLE_EVENT_TYPES = ("catch-up", "student-support", "first-session")

# Calendar colour/type vocabulary for the booked types above.
BOOKED_EVENT_JSON_TYPES = {
    "student-support": "welfare",
    "gateway": "review",
    "other": "coaching",
    "eligibility-review": "review",
    "workspace": "review",
    "training-plan": "review",
    "uln-privacy": "review",
}


def get_graph_settings() -> dict[str, str]:
    """Credentials for the app-only Graph client (Teams meetings, calendar sync).

    MICROSOFT_GRAPH_* comes first and deliberately so. The bare MICROSOFT_CLIENT_ID
    name is shared with the learner calendar-connection OAuth app, which is a
    delegated-auth registration holding no application roles; whichever of the two
    the .env declares last used to win here, so adding that app silently repointed
    Teams booking at credentials Graph answers with 403 ErrorAccessDenied. The old
    names stay as a fallback for deployments not yet carrying the dedicated ones.
    """
    load_env_file()
    return {
        "tenant_id": clean_text(
            os.environ.get("MICROSOFT_GRAPH_TENANT_ID")
            or os.environ.get("MICROSOFT_TENANT_ID")
            or os.environ.get("TENANTID")
        ),
        "client_id": clean_text(
            os.environ.get("MICROSOFT_GRAPH_CLIENT_ID")
            or os.environ.get("MICROSOFT_CLIENT_ID")
            or os.environ.get("CLIENTID")
        ),
        "client_secret": clean_text(
            os.environ.get("MICROSOFT_GRAPH_CLIENT_SECRET")
            or os.environ.get("MICROSOFT_CLIENT_SECRET")
        ),
        "scope": clean_text(os.environ.get("MICROSOFT_GRAPH_SCOPE")) or MICROSOFT_GRAPH_DEFAULT_SCOPE,
        "base_url": clean_text(os.environ.get("MICROSOFT_GRAPH_BASE_URL")) or MICROSOFT_GRAPH_DEFAULT_BASE_URL,
        "timezone": clean_text(os.environ.get("MICROSOFT_GRAPH_TIMEZONE")) or MICROSOFT_GRAPH_DEFAULT_TIMEZONE,
    }


def has_graph_credentials() -> bool:
    settings = get_graph_settings()
    return all(settings[key] for key in ("tenant_id", "client_id", "client_secret", "scope", "base_url"))


# App-only Graph token, cached until shortly before it expires. Fetching one per
# call doubled every Graph round trip: a single Teams calendar create makes
# fifteen or more calls, and each one first paid a full trip to the token
# endpoint. Keyed on the credentials, so a rotated secret is never served a
# token issued for the old one. Locked because requests share worker threads.
_GRAPH_TOKEN_LOCK = threading.Lock()
_GRAPH_TOKEN_CACHE: dict = {"key": None, "value": None, "expires_at": 0.0}
#: Refresh this many seconds before the token actually expires.
_GRAPH_TOKEN_SKEW = 120


def forget_microsoft_graph_token() -> None:
    """Drop the cached token, so the next call asks Microsoft for a fresh one."""
    with _GRAPH_TOKEN_LOCK:
        _GRAPH_TOKEN_CACHE.update(key=None, value=None, expires_at=0.0)


def microsoft_graph_token() -> str:
    settings = get_graph_settings()
    if not has_graph_credentials():
        raise RuntimeError("Microsoft Graph credentials are not configured.")

    cache_key = hashlib.sha256(
        "\n".join(settings[name] for name in ("tenant_id", "client_id", "client_secret", "scope")).encode("utf-8")
    ).hexdigest()
    with _GRAPH_TOKEN_LOCK:
        if (_GRAPH_TOKEN_CACHE["key"] == cache_key and _GRAPH_TOKEN_CACHE["value"]
                and _GRAPH_TOKEN_CACHE["expires_at"] > monotonic()):
            return _GRAPH_TOKEN_CACHE["value"]
        return _fetch_microsoft_graph_token(settings, cache_key)


def _fetch_microsoft_graph_token(settings: dict, cache_key: str) -> str:
    token_url = f"https://login.microsoftonline.com/{settings['tenant_id']}/oauth2/v2.0/token"
    payload = urllib_parse.urlencode(
        {
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
            "scope": settings["scope"],
            "grant_type": "client_credentials",
        }
    ).encode("utf-8")
    request = urllib_request.Request(
        token_url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    # Transient DNS/network blips otherwise fail a booking outright and leave the
    # learner holding a slot nobody was told about, so retry the connection a
    # couple of times. HTTP errors are real rejections -- never retried.
    last_url_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib_request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            last_url_error = None
            break
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Microsoft token request failed: {exc.code} {detail}") from exc
        except urllib_error.URLError as exc:
            last_url_error = exc
            if attempt < 2:
                _sleep(1 + attempt)
    if last_url_error is not None:
        raise RuntimeError(f"Microsoft token request failed: {last_url_error}") from last_url_error

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError("Microsoft token response did not include access_token.")
    try:
        lifetime = int(data.get("expires_in", 3600))
    except (TypeError, ValueError):
        lifetime = 3600
    _GRAPH_TOKEN_CACHE.update(
        key=cache_key, value=access_token,
        expires_at=monotonic() + max(lifetime - _GRAPH_TOKEN_SKEW, 60),
    )
    return access_token


def microsoft_graph_request(
    method: str, path: str, *, payload: dict | None = None,
    extra_headers: dict | None = None,
) -> dict:
    """Call Microsoft Graph as the application.

    ``extra_headers`` carries request-scoped Graph preferences, notably
    ``Prefer: outlook.send-invitations="none"`` -- without it Graph emails every
    attendee on each write to a meeting, which turns a routine per-instance
    reconciliation into one message per instance per person.
    """
    started = perf_counter()
    settings = get_graph_settings()
    try:
        token = microsoft_graph_token()
    except Exception:
        metric_event(
            "graph_call",
            method=method.upper(),
            graph_status="token_error",
            latency_ms=round((perf_counter() - started) * 1000, 1),
        )
        raise
    url = f"{settings['base_url'].rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    for header_name, header_value in (extra_headers or {}).items():
        name = clean_text(header_name)
        # Never let a caller override authentication.
        if name and name.lower() not in {"authorization"}:
            headers[name] = str(header_value)
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    request = urllib_request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib_request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
    except urllib_error.HTTPError as exc:
        metric_event(
            "graph_call",
            method=method.upper(),
            graph_status=f"http_{exc.code}",
            latency_ms=round((perf_counter() - started) * 1000, 1),
        )
        if exc.code == 401:
            # A revoked or expired token must not stay cached for the next call.
            # This call is not retried: it is reported exactly as before.
            forget_microsoft_graph_token()
        detail = exc.read().decode("utf-8", errors="ignore")
        graph_code = ""
        graph_message = ""
        request_id = ""
        try:
            parsed_detail = json.loads(detail) if detail else {}
            graph_error = parsed_detail.get("error") if isinstance(parsed_detail, dict) else {}
            if isinstance(graph_error, dict):
                graph_code = str(graph_error.get("code") or "").strip()
                graph_message = str(graph_error.get("message") or "").strip()
                inner_error = graph_error.get("innerError") or graph_error.get("innererror") or {}
                if isinstance(inner_error, dict):
                    request_id = str(
                        inner_error.get("request-id")
                        or inner_error.get("requestId")
                        or ""
                    ).strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            graph_message = detail[:1500].strip()
        if not request_id and exc.headers:
            request_id = str(
                exc.headers.get("request-id")
                or exc.headers.get("client-request-id")
                or ""
            ).strip()
        diagnostics = [f"HTTP {exc.code}"]
        if graph_code:
            diagnostics.append(f"code={graph_code}")
        if graph_message:
            diagnostics.append(f"message={graph_message}")
        if request_id:
            diagnostics.append(f"request-id={request_id}")
        raise RuntimeError(
            f"Microsoft Graph {method.upper()} {path} failed: {'; '.join(diagnostics)}"
        ) from exc
    except urllib_error.URLError as exc:
        metric_event(
            "graph_call",
            method=method.upper(),
            graph_status="network_error",
            latency_ms=round((perf_counter() - started) * 1000, 1),
        )
        raise RuntimeError(f"Microsoft Graph {method.upper()} {path} failed: {exc}") from exc

    metric_event(
        "graph_call",
        method=method.upper(),
        graph_status="success",
        latency_ms=round((perf_counter() - started) * 1000, 1),
    )
    return json.loads(raw) if raw else {}


def is_hidden_caseload_programme_status(raw_status: str | None) -> bool:
    """Programme stages kept off the coach caseload; mirrors the frontend
    isHiddenCaseloadProgrammeStatus (pages/coach/caseload/lib/format.ts)."""
    normalized = re.sub(r"[^a-z0-9]+", "", (raw_status or "").lower())
    return normalized.startswith("onboarding") or normalized in {"withdrawn", "completed", "enteredepa", "epa"}


def normalize_program_status(raw_status: str | None) -> str:
    normalized = (raw_status or "").strip().lower().replace(" ", "")
    if normalized == "withdrawn":
        return "withdrawn"
    if normalized in {"break", "onbreak", "onabreak", "onmaternitybreak", "onillnessbreak", "onotherbreak"}:
        return "break"
    if normalized == "readytoenrol":
        return "ready-to-enrol"
    # Treat the pre-invitation Delivery state as active for coach-facing
    # summaries and attendance metrics. The raw programme status is still
    # serialized separately, so the UI can show that the invitation is pending.
    if normalized in {"active", "delivery"}:
        return "active"
    return "unknown"


def apply_curriculum_attendance_placements(learners: list[dict], rows, *, groups=None) -> None:
    """Resolve non-Aptem caseload placements without changing learner ownership.

    Curriculum and attendance share LearnerProfile identities. Legacy placements
    may carry names alone; resolve those only within their programme and cohort,
    and only when exactly one curriculum group matches. Existing ids take priority.
    This enriches the overview response only, without rewriting stored placements.
    """
    rows = list(rows)
    rows_by_id = {str(row.id): row for row in rows}
    aptem_ids, conflicts = resolve_effective_aptem_ids(rows)
    eligible = [learner for learner in learners
                if int(learner['id']) not in aptem_ids
                and int(learner['id']) not in conflicts]
    if not eligible:
        return
    groups = authoring_fetch_all(GROUPS_TABLE, ensure_tables=False) if groups is None else groups
    for learner in eligible:
        row = rows_by_id.get(str(learner['id']))
        if row is None:
            continue
        matches = []
        for group in groups:
            if clean_text(group.get('status')).casefold() in {'archived', 'deleted'}:
                continue
            group_id = clean_text(learner.get('groupId'))
            programme_id = clean_text(learner.get('programmeId'))
            cohort_id = clean_text(getattr(row, 'cohort_id', None))
            if group_id:
                if group_id != clean_text(group.get('group_id')):
                    continue
            elif clean_text(learner.get('groupName')).casefold() != clean_text(group.get('group_name')).casefold():
                continue
            if programme_id:
                if programme_id != clean_text(group.get('programme_id')):
                    continue
            elif clean_text(learner.get('programmeName')).casefold() != clean_text(group.get('programme_name')).casefold():
                continue
            if cohort_id:
                if cohort_id != clean_text(group.get('cohort_id')):
                    continue
            elif not group_id and clean_text(learner.get('cohortName')).casefold() != clean_text(group.get('cohort_name')).casefold():
                continue
            if clean_text(group.get('group_id')) and clean_text(group.get('programme_id')):
                matches.append(group)
        if len(matches) != 1:
            continue
        group = matches[0]
        learner.update({
            'programmeId': clean_text(group.get('programme_id')),
            'programmeName': clean_text(group.get('programme_name')),
            'groupId': clean_text(group.get('group_id')),
            'groupName': clean_text(group.get('group_name')),
            'group': clean_text(group.get('group_name')),
        })


def should_include_in_attendance_page(learner: dict) -> bool:
    return learner["enrollmentStatus"] in ATTENDANCE_INCLUDED_STATUSES


def should_include_in_attendance_metrics(learner: dict) -> bool:
    return learner["enrollmentStatus"] == "active"


def build_risk_flags(row: dict, hours_progress: int, ksb_progress: int) -> list[str]:
    flags: list[str] = []

    if row["coach_rag"] in {"Red", "Amber"}:
        flags.append(f'Coach RAG: {row["coach_rag"]}')
    if row["otjh_status"] == "Need Attention":
        flags.append("Hours need attention")
    if row["ksb_status"] == "Not Started":
        flags.append("KSBs not started")
    if row["comp_status"] == "Behind":
        flags.append("Components behind target")
    if row["progress_variance"]:
        flags.append(f'Variance {row["progress_variance"]}')
    if hours_progress < 25:
        flags.append("Low hours progress")
    if ksb_progress < 25:
        flags.append("Low KSB progress")

    seen: set[str] = set()
    deduped: list[str] = []
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            deduped.append(flag)
    return deduped[:4]


def determine_performance_status(row: dict, hours_progress: int, ksb_progress: int, component_progress: int) -> str:
    if row["program_status"] == "ReadyToEnrol":
        return "new-starter"
    if row["coach_rag"] in {"Red", "Amber"}:
        return "at-risk"
    if row["otjh_status"] == "Need Attention" and (hours_progress < 45 or ksb_progress < 35):
        return "at-risk"
    if hours_progress >= 80 and ksb_progress >= 75 and component_progress >= 20:
        return "high"
    return "on-track"


def fetch_caseload_rows(owner_email: str) -> list[dict]:
    connection_string = get_aptem_connection_string()
    if not connection_string:
        raise RuntimeError("APTEMAUTOEXTRACTINGDATABASE is not configured.")

    query = """
        select
            "ID" as learner_id,
            "FullName" as full_name,
            "Email" as learner_email,
            "Program Name" as cohort_name,
            "Program-Status" as program_status,
            "Group" as group_name,
            "Coach-RAG" as coach_rag,
            "OTJHoursStatus" as otjh_status,
            "KSBStatus" as ksb_status,
            "CompStatus" as comp_status,
            "ProgressVariance" as progress_variance,
            "TotalCompletedKSB" as total_completed_ksb,
            "TotalTargetKSB" as total_target_ksb,
            "Completed" as completed_hours,
            "Planned" as planned_hours,
            "Minimum" as minimum_hours,
            "Submitted" as submitted_hours,
            "Forecast" as forecast_hours,
            "Exepected" as expected_hours,
            "Progress-Hours" as progress_hours,
            "Assignment Evidence" as assignment_evidence,
            "LMS Evidence" as lms_evidence,
            "ExtraAct-Evidence" as extra_evidence,
            "CompletedCompCount" as completed_components,
            "TargetCompCount" as target_components,
            "Start-Date" as start_date,
            "End-Date" as end_date,
            "Gateway Review Date" as gateway_review_date,
            "OrganizationName" as organization_name,
            "OwnerName" as owner_name,
            "OwnerEmail" as owner_email,
            "OwnerPhone" as owner_phone,
            "ManagerName" as manager_name,
            "ManagerEmail" as manager_email,
            "Employer Email" as employer_email,
            "Learner Phone" as learner_phone
        from public.aptem_auto_extracting
        where "OwnerEmail" = %s
        order by "FullName"
    """

    with psycopg.connect(connection_string, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (owner_email,))
            return list(cur.fetchall())


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_person_name(value: str | None) -> str:
    return " ".join(clean_text(value).lower().split())


def clean_text(value) -> str:
    return "" if value in (None, "") else str(value).strip()


EMAIL_ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value) -> str:
    email = normalize_email(clean_text(value))
    return email if email and EMAIL_ADDRESS_RE.match(email) else ""


def row_value(row, *keys: str):
    if row is None:
        return None
    if isinstance(row, dict):
        lowered = {str(key).casefold(): value for key, value in row.items()}
        for key in keys:
            for candidate in {
                key,
                key.replace("_", " "),
                key.replace("_", ""),
                key.replace(" ", "_"),
            }:
                if candidate in row:
                    return row[candidate]
                lowered_value = lowered.get(candidate.casefold())
                if lowered_value is not None:
                    return lowered_value
        return None
    for key in keys:
        attr = key.replace(" ", "_").replace("-", "_")
        if hasattr(row, attr):
            return getattr(row, attr)
    return None


def iter_contact_dicts(value):
    if isinstance(value, dict):
        lowered_keys = {str(key).casefold() for key in value.keys()}
        if lowered_keys & {
            "email",
            "contactemail",
            "contact_email",
            "contact email",
            "linemanageremail",
            "line_manager_email",
            "line manager email",
        }:
            yield value
        for child in value.values():
            yield from iter_contact_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_contact_dicts(child)


def contact_value(contact: dict, *keys: str):
    lowered = {str(key).casefold(): value for key, value in contact.items()}
    for key in keys:
        for candidate in {
            key,
            key.replace("_", " "),
            key.replace("_", ""),
            key.replace(" ", "_"),
        }:
            if candidate in contact:
                return contact[candidate]
            lowered_value = lowered.get(candidate.casefold())
            if lowered_value is not None:
                return lowered_value
    return None


def learner_employer_attendee(
    learner: LearnerProfile | SimpleNamespace,
    source_row: CommercialUser | EnrolmentUser | SimpleNamespace | dict | None = None,
) -> dict[str, str]:
    """Best effort employer contact for Progress Review invites.

    The active LearnerProfile mirror intentionally stays lean, so employer
    contact details are resolved from the learner's source row when available.
    """

    source = source_row or getattr(learner, "_caseload_source", None)
    fallback_name = (
        clean_text(row_value(source, "line_manager", "Line_manager", "manager_name", "ManagerName", "Manager Name"))
        or clean_text(row_value(source, "employer", "Employer", "organization", "OrganizationName", "OrganisationName"))
    )

    employer_id = row_value(source, "employer_id", "Employer_id", "employerId", "EmployerId")
    if employer_id not in (None, ""):
        try:
            employer = Employer.objects.filter(pk=employer_id).only(
                "first_name",
                "surname",
                "email",
            ).first()
        except (DatabaseError, ValueError, TypeError):
            employer = None
        if employer is not None:
            employer_email = clean_email(getattr(employer, "email", None))
            employer_name = clean_text(getattr(employer, "full_name", None)) or fallback_name
            if employer_email:
                return {"name": employer_name or employer_email, "email": employer_email}
            fallback_name = employer_name or fallback_name

    direct_candidates = [
        (
            row_value(source, "employer_email", "Employer Email", "EmployerEmail"),
            fallback_name,
        ),
        (
            row_value(source, "manager_email", "ManagerEmail", "Manager Email"),
            clean_text(row_value(source, "manager_name", "ManagerName", "Manager Name")) or fallback_name,
        ),
    ]
    for email_value, name_value in direct_candidates:
        email = clean_email(email_value)
        if email:
            name = clean_text(name_value) or email
            return {"name": name, "email": email}

    contacts = row_value(source, "contacts", "Contacts")
    contact_candidates = []
    for contact in iter_contact_dicts(contacts):
        email = clean_email(
            contact_value(
                contact,
                "email",
                "Email",
                "contactEmail",
                "contact_email",
                "Contact_email",
                "Contact Email",
                "lineManagerEmail",
                "line_manager_email",
                "Line manager email",
            )
        )
        if not email:
            continue
        name = clean_text(
            contact_value(
                contact,
                "name",
                "Name",
                "fullName",
                "full_name",
                "Contact_name",
                "Contact Name",
                "lineManagerName",
                "Line manager name",
            )
        )
        role = clean_text(contact_value(contact, "type", "role", "Contact_role", "Contact Role"))
        priority = 0 if re.search(r"employer|manager", role, re.IGNORECASE) else 1
        contact_candidates.append((priority, name or fallback_name or email, email))

    if contact_candidates:
        _priority, name, email = sorted(contact_candidates, key=lambda item: item[0])[0]
        return {"name": name, "email": email}

    return {}


def parse_json_body(request) -> dict:
    return parse_json_object(request)


def normalize_coach_rag_value(value) -> str | None:
    normalized = clean_text(value).lower()
    if not normalized or normalized in {"--", "null", "none"}:
        return None
    if normalized in COACH_RAG_LABELS:
        return normalized
    raise ValueError("coachRag must be one of: green, amber, red.")


def format_coach_rag_value(value) -> str:
    return COACH_RAG_LABELS.get(clean_text(value).lower(), "")

def list_or_empty(value) -> list:
    return value if isinstance(value, list) else []


def count_planned_components(training_plan) -> int:
    total = 0
    for module in list_or_empty(training_plan):
        if not isinstance(module, dict):
            continue
        for week in list_or_empty(module.get("weeks")):
            if not isinstance(week, dict):
                continue
            total += sum(1 for component in list_or_empty(week.get("components")) if isinstance(component, dict))
    return total


def activity_completion_key(item: dict, index: int) -> str:
    quiz_id = item.get("quizId")
    if quiz_id not in (None, ""):
        return f"quiz:{quiz_id}"

    component_id = clean_text(item.get("componentId"))
    if component_id:
        return f"component:{component_id}"

    parts = [
        clean_text(item.get("module")),
        clean_text(item.get("week")),
        clean_text(item.get("kind")),
        clean_text(item.get("title")),
        clean_text(item.get("quizName")),
        clean_text(item.get("action")),
    ]
    compact = "|".join(part for part in parts if part)
    return compact or f"item:{index}"


def summarize_ksb_breakdown(target_codes: set[str], completed_codes: set[str]) -> dict[str, dict[str, int | None]]:
    completed_in_target = completed_codes & target_codes if target_codes else set()
    breakdown: dict[str, dict[str, int | None]] = {}

    for label, prefix in (("knowledge", "K"), ("skills", "S"), ("behaviours", "B")):
        target_for_type = {code for code in target_codes if code.startswith(prefix)}
        completed_for_type = {code for code in completed_in_target if code.startswith(prefix)}
        target_count = len(target_for_type)
        completed_count = len(completed_for_type)

        breakdown[label] = {
            "completed": completed_count if target_count else None,
            "target": target_count if target_count else None,
            "progress": percentage(completed_count, target_count) if target_count else None,
        }

    return breakdown


def ksb_sort_key(code: str) -> tuple[int, int, str]:
    normalized = normalize_ksb_parent_code(code)
    match = re.match(r"^([KSB])(\d+)$", normalized)
    if not match:
        return (9, 0, normalized)
    prefix_order = {"K": 0, "S": 1, "B": 2}
    return (prefix_order.get(match.group(1), 9), int(match.group(2)), normalized)


def ksb_type_label(code: str, explicit_type: str = "") -> str:
    value = clean_text(explicit_type).lower()
    if value.startswith("knowledge") or value == "k":
        return "Knowledge"
    if value.startswith("skill") or value == "s":
        return "Skills"
    if value.startswith("behaviour") or value.startswith("behavior") or value == "b":
        return "Behaviours"

    normalized = normalize_ksb_parent_code(code)
    if normalized.startswith("K"):
        return "Knowledge"
    if normalized.startswith("S"):
        return "Skills"
    if normalized.startswith("B"):
        return "Behaviours"
    return clean_text(explicit_type) or "KSB"


def ksb_target_lookup(values) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for value in list_or_empty(values):
        if isinstance(value, str):
            code = normalize_ksb_parent_code(value)
            explicit_type = ""
            description = ""
        elif isinstance(value, dict):
            code = normalize_ksb_parent_code(
                value.get("code") or value.get("Code") or value.get("id") or value.get("number")
            )
            explicit_type = clean_text(value.get("type") or value.get("ksb_type") or value.get("category"))
            description = clean_text(value.get("description") or value.get("label") or value.get("title") or value.get("name"))
        else:
            continue
        if not code:
            continue
        lookup.setdefault(code, {
            "code": code,
            "type": ksb_type_label(code, explicit_type),
            "description": description,
        })
    return lookup


def count_completed_components(progress_entries: list[dict]) -> int:
    seen: set[str] = set()
    for entry in progress_entries:
        if not isinstance(entry, dict):
            continue
        seen.add(activity_completion_key(entry, len(seen)))
    return len(seen)


def completed_ksb_codes(progress_entries: list[dict], activity_entries: list[dict]) -> set[str]:
    """KSB codes evidenced by activity the learner actually completed.

    A failed attempt is an attempt, not KSB evidence. Legacy failed quizzes can
    carry an entire profile's codes, which previously made coach totals jump to
    almost 100% while the learner page correctly showed only KSBs evidenced by
    completed activities. The gate is ``learner_api.progress_rules`` — the same
    rule Curriculum impact uses — so it holds for any kind, not just quizzes:
    ``passed=False`` on a Component with real lineage is excluded too.
    """
    completed: set[str] = set()
    for entry in [*progress_entries, *activity_entries]:
        if not isinstance(entry, dict):
            continue
        if not progress_record_counts_as_achieved(entry):
            continue
        completed.update(extract_ksb_codes(entry.get("ksbs")))
    return completed


def is_evidence_entry(entry: dict) -> bool:
    joined = " ".join(
        clean_text(entry.get(field))
        for field in ("kind", "title", "action", "detail", "quizName")
    ).lower()
    return "evidence" in joined


def derive_evidence_count(progress_entries: list[dict], activity_entries: list[dict]) -> int:
    seen: set[str] = set()
    for entry in [*progress_entries, *activity_entries]:
        if isinstance(entry, dict) and is_evidence_entry(entry):
            seen.add(activity_completion_key(entry, len(seen)))
    return len(seen)


def derive_completed_evidence_count(progress_entries: list[dict], activity_entries: list[dict]) -> int:
    completed_statuses = {"accepted", "approved", "complete", "completed", "validated"}
    seen: set[str] = set()
    for entry in [*progress_entries, *activity_entries]:
        if not isinstance(entry, dict) or not is_evidence_entry(entry):
            continue
        status = clean_text(entry.get("status") or entry.get("reviewStatus")).lower()
        if status in completed_statuses:
            seen.add(activity_completion_key(entry, len(seen)))
    return len(seen)


def derive_ksb_status(completed: int | None, target: int | None) -> str:
    if not target:
        return ""
    if not completed:
        return "Not Started"
    if completed >= target:
        return "Completed"
    return "In Progress"


def build_active_user_risk_flags(
    *,
    otjh_status: str,
    ksb_status: str,
    progress_variance: str,
    hours_progress: int,
    hours_available: bool,
    ksb_progress: int,
    ksb_available: bool,
    component_progress: int,
    component_available: bool,
) -> list[str]:
    flags: list[str] = []
    normalized_otjh = clean_text(otjh_status).lower().replace(" ", "")

    if normalized_otjh == "needattention":
        flags.append("Hours need attention")
    elif normalized_otjh == "atrisk":
        flags.append("OTJH at risk")

    if ksb_status == "Not Started":
        flags.append("KSBs not started")
    if progress_variance:
        flags.append(f"Variance {progress_variance}")
    if component_available and component_progress < 25:
        flags.append("Components behind target")
    if hours_available and hours_progress < 25:
        flags.append("Low hours progress")
    if ksb_available and ksb_progress < 25:
        flags.append("Low KSB progress")

    deduped: list[str] = []
    seen: set[str] = set()
    for flag in flags:
        if flag not in seen:
            seen.add(flag)
            deduped.append(flag)
    return deduped[:4]


def determine_active_user_status(
    *,
    program_status: str,
    otjh_status: str,
    progress_variance: str,
    hours_progress: int,
    hours_available: bool,
    ksb_progress: int,
    ksb_available: bool,
    component_progress: int,
    component_available: bool,
) -> str:
    """Legacy composite learner performance label, serialized as ``status``.

    Serializers and apply_canonical_learner_metrics use programme state and
    legacy OTJH variance/hours/KSB/component inputs. This is independent of
    the final target-to-date ``otjhRagStatus`` verdict; do not synchronize them.
    The standalone frontend may replace this label with engagement overallStatus.
    """
    if normalize_program_status(program_status) == "ready-to-enrol":
        return "new-starter"

    normalized_otjh = clean_text(otjh_status).lower().replace(" ", "")
    if normalized_otjh == "atrisk":
        return "at-risk"
    if normalized_otjh == "needattention" and (
        (hours_available and hours_progress < 45)
        or (ksb_available and ksb_progress < 35)
        or (component_available and component_progress < 35)
    ):
        return "at-risk"
    if (
        hours_available
        and ksb_available
        and component_available
        and hours_progress >= 80
        and ksb_progress >= 75
        and component_progress >= 75
    ):
        return "high"
    return "on-track"


def get_lms_row_program_status(row) -> str:
    return clean_text(getattr(row, "programme_status", None) or getattr(row, "status", None))


def get_learner_db_alias() -> str:
    return router.db_for_read(LearnerProfile) or "default"


def find_existing_relation(connection, relation_candidates: tuple[str, ...]) -> str | None:
    with connection.cursor() as cursor:
        for relation in relation_candidates:
            cursor.execute("select to_regclass(%s)", [relation])
            result = cursor.fetchone()
            if result and result[0]:
                return relation
    return None


def find_learner_absence_relation(connection) -> str | None:
    return find_existing_relation(connection, LEARNER_ABSENCE_RELATION_CANDIDATES)


def relation_schema_and_table(relation: str) -> tuple[str, str]:
    parts = [part.strip('"') for part in relation.split(".")]
    if len(parts) != 2:
        raise ValueError(f"Expected a schema-qualified relation, got {relation}")
    return parts[0], parts[1]


def quote_sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def relation_columns(connection, relation: str) -> dict[str, str]:
    schema, table = relation_schema_and_table(relation)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            select column_name
            from information_schema.columns
            where table_schema = %s and table_name = %s
            """,
            [schema, table],
        )
        return {str(row[0]).lower(): str(row[0]) for row in cursor.fetchall()}


def first_existing_column(columns: dict[str, str], *candidates: str) -> str | None:
    for candidate in candidates:
        match = columns.get(candidate.lower())
        if match:
            return match
    return None


def find_learner_absence_relation(connection) -> str | None:
    return find_existing_relation(connection, LEARNER_ABSENCE_RELATION_CANDIDATES)


def attach_caseload_source_rows(rows) -> None:
    """Attach each profile's enrolment/commercial source row as `_caseload_source`.

    The source row carries identity the LearnerProfile mirror does not -- the
    schedule fields, and the `aptem_id` that bridges to the audit mirror. The
    lookup is two batched queries for the whole caseload, not one per learner.
    """
    if not rows:
        return
    commercial_rows, enrolment_rows = fetch_source_schedule_rows(rows)
    # The verified training-plan contract is the authoritative source for the
    # programme total and its contractual start/end window.  Keep it attached
    # to the same request-local row as the Created_users source so serializers
    # can consume the API's existing contract projection without another query
    # per learner.
    def row_aptem_id(row, source=None):
        raw = getattr(source, "aptem_id", None) if source is not None else None
        raw = raw if raw not in (None, "") else getattr(row, "aptem_id", None)
        try:
            aptem_id = int(str(raw).strip()) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None
        return aptem_id if aptem_id and aptem_id > 0 else None

    contract_ids = []
    for row in rows:
        source = resolve_caseload_source_row(
            row,
            commercial_rows=commercial_rows,
            enrolment_rows=enrolment_rows,
        )
        aptem_id = row_aptem_id(row, source)
        if aptem_id and aptem_id > 0:
            contract_ids.append(aptem_id)
    try:
        contracts = load_contracts_bulk(contract_ids)
    except (DatabaseError, TypeError, ValueError) as exc:
        # Contract data is an enrichment.  A temporary contract-table failure
        # must not make the coach caseload unavailable; existing source/profile
        # schedule fallbacks remain valid.
        logger.warning("Could not load caseload training-plan contracts: %s", exc)
        contracts = {}
    for row in rows:
        source = resolve_caseload_source_row(
            row,
            commercial_rows=commercial_rows,
            enrolment_rows=enrolment_rows,
        )
        setattr(row, "_caseload_source", source)
        aptem_id = row_aptem_id(row, source)
        setattr(row, "_caseload_contract", contracts.get(aptem_id) if aptem_id else None)


def fetch_caseload_learner_profiles(owner_email: str) -> list[LearnerProfile | SimpleNamespace]:
    requested_owner = normalize_email(owner_email)
    learner_alias = get_learner_db_alias()
    prefetches = [
        "ksb_assignment__profile_version__definitions",
        "plan_modules__weeks__components",
        "progress_entries__ksb_links",
        "progress_entries__quiz_answers__correct_answers",
        "progress_entries__quiz_answers__chosen_answers",
    ]
    # Legacy relations are prefetched only where they still exist. A queryset
    # prefetch of a dropped table raises before LearnerProfile's own tolerant
    # accessors can fall back, which turned the whole caseload into a 500.
    if learner_ksbs_relation_exists(learner_alias):
        prefetches.insert(0, "assigned_ksbs")
    if learner_activity_events_relation_exists(learner_alias):
        prefetches.append("activity_events")
    queryset = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(coach_email_key=requested_owner)
        .prefetch_related(*prefetches)
        .order_by("full_name", "id")
    )
    rows = [
        row
        for row in queryset
        if clean_text(row.username)
    ]
    attach_caseload_source_rows(rows)
    return rows


def fetch_caseload_learner_profiles_by_ids(owner_email: str, profile_ids: list[int]):
    """Load the existing full caseload shape for one authorised page only."""
    if not profile_ids:
        return []
    requested_owner = normalize_email(owner_email)
    learner_alias = get_learner_db_alias()
    prefetches = [
        "ksb_assignment__profile_version__definitions", "plan_modules__weeks__components",
        "progress_entries__ksb_links", "progress_entries__quiz_answers__correct_answers",
        "progress_entries__quiz_answers__chosen_answers",
    ]
    if learner_ksbs_relation_exists(learner_alias):
        prefetches.insert(0, "assigned_ksbs")
    if learner_activity_events_relation_exists(learner_alias):
        prefetches.append("activity_events")
    rows_by_id = {
        int(row.id): row
        for row in LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email"))).filter(
            coach_email_key=requested_owner, id__in=profile_ids,
        ).prefetch_related(*prefetches)
        if clean_text(row.username)
    }
    rows = [rows_by_id[profile_id] for profile_id in profile_ids if profile_id in rows_by_id]
    attach_caseload_source_rows(rows)
    return rows


def fetch_all_learner_profiles(programme: str | None = None, cohort: str | None = None) -> list[LearnerProfile]:
    """Every learner with a resolved enrolment id, prefetched exactly like
    `fetch_caseload_learner_profiles` but WITHOUT the single-coach filter —
    for the engagement app's roster-wide analytics (attendance-risk /
    learner-engagement pages, the command-centre charts), which need every
    learner, not one coach's caseload.

    `enrolment_id` is the bridge to the engagement points economy's
    `learner_id` (== `Created_users.id` == `account.subject_id`) — see
    `LearnerProfile.enrolment_id` (models.py:500). A profile with no
    enrolment_id can't be attributed to an engagement learner_id, so it's
    excluded here (mirrors the same skip already applied in
    `learner_api.active_users.save_progress_record`'s points-award hook).
    """
    learner_alias = get_learner_db_alias()
    prefetches = [
        "ksb_assignment__profile_version__definitions",
        "plan_modules__weeks__components",
        "progress_entries__ksb_links",
        "progress_entries__quiz_answers__correct_answers",
        "progress_entries__quiz_answers__chosen_answers",
    ]
    if learner_ksbs_relation_exists(learner_alias):
        prefetches.insert(0, "assigned_ksbs")
    if learner_activity_events_relation_exists(learner_alias):
        prefetches.append("activity_events")

    queryset = LearnerProfile.objects.exclude(enrolment_id__isnull=True).prefetch_related(*prefetches)
    if programme:
        queryset = queryset.filter(programme__iexact=programme)
    if cohort:
        queryset = queryset.filter(cohort__iexact=cohort)
    queryset = queryset.order_by("full_name", "id")

    return [row for row in queryset if clean_text(row.username)]


def fetch_caseload_dashboard_profiles(owner_email: str) -> list[LearnerProfile]:
    """Return a lean learner snapshot for the coach dashboard first paint."""
    requested_owner = normalize_email(owner_email)
    learner_alias = get_learner_db_alias()
    dashboard_prefetches = [
        "ksb_assignment__profile_version__definitions",
        "plan_modules__weeks__components",
    ]
    if learner_ksbs_relation_exists(learner_alias):
        dashboard_prefetches.insert(0, "assigned_ksbs")
    queryset = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(coach_email_key=requested_owner)
        .only(
            "id",
            "full_name",
            "email",
            "programme",
            "programme_id",
            "programme_status",
            "cohort",
            "cohort_id",
            "group_name",
            "group_id",
            "lifecycle_status",
            "completed_hours",
            "target_hours",
            "minimum_hours",
            "planned_hours",
            "progress_hours",
            "progress_variance",
            "otjh_status",
            "coach_name",
            "coach_email",
            "coach_rag",
            "start_date",
            "end_date",
            "gateway_review_date",
            "learner_type",
            "enrolment_id",
            "aptem_id",
        )
        # For current_week_label(): a few extra batched queries (one per
        # related table, not one per learner) rather than a lazy per-row
        # fetch the first time each learner's .training_plan is touched.
        .prefetch_related(*dashboard_prefetches)
        .order_by("full_name", "id")
    )
    rows = [row for row in queryset if clean_text(row.username)]
    attach_caseload_source_rows(rows)
    return rows


def fetch_caseload_timetable_profiles(owner_email: str) -> list[LearnerProfile]:
    """Load only identity and review-schedule fields needed by the timetable.

    The dashboard loader also prefetches KSB evidence, quiz answers and learner
    activity. Reusing it here made every meetings page pay for those unrelated
    datasets and could exceed the database statement timeout before a single
    review event was returned.
    """
    requested_owner = normalize_email(owner_email)
    queryset = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(coach_email_key=requested_owner)
        .only(
            "id", "full_name", "email", "programme", "programme_id",
            "programme_status", "cohort", "cohort_id", "group_name", "group_id",
            "lifecycle_status", "coach_name", "coach_email", "start_date", "end_date",
            "gateway_review_date", "learner_type", "enrolment_id", "aptem_id",
        )
        .prefetch_related("plan_modules__weeks__components")
        .order_by("full_name", "id")
    )
    rows = [row for row in queryset if clean_text(row.username)]
    attach_caseload_source_rows(rows)
    return rows


def caseload_dashboard_progress_projections(rows, *, ksb_rows=None) -> dict[int, dict]:
    """Project established KSB fallback and activity metadata in bulk.

    Programme metrics belong exclusively to ``caseload_canonical_metrics``.
    KSB target-code counts remain the established Caseload fallback when the
    activity-point metric is unavailable; a ready canonical KSB metric overlays
    them later. Training-plan activity counts must never be copied here because
    they would overwrite the historical + native population with differently-
    scoped values such as 158 / 133.

    ``ksb_rows`` lets aggregate callers skip the historical KSB fallback for
    learners whose canonical KSB metric is already ready. Latest activity is
    still loaded for every row; only the redundant many-row KSB join is
    narrowed.
    """
    rows = list(rows or [])
    ksb_rows = rows if ksb_rows is None else list(ksb_rows or [])
    completed_codes = caseload_completed_ksb_codes(ksb_rows)
    latest_activities = caseload_latest_learning_activities(rows)
    projections = {}
    for row in rows:
        target_codes = set(ksb_target_lookup(row.ksbs))
        achieved_codes = completed_codes.get(int(row.id), set()) & target_codes
        target_count = len(target_codes)
        completed_count = len(achieved_codes)
        available = target_count > 0
        latest = latest_activities.get(int(row.id)) or {}
        projections[int(row.id)] = {
            "ksbCompleted": completed_count if available else None,
            "ksbTarget": target_count if available else None,
            "ksbStatus": derive_ksb_status(completed_count, target_count) if available else "",
            "ksbProgress": percentage(completed_count, target_count) if available else 0,
            "ksbProgressAvailable": available,
            "lastActivity": latest.get("display"),
            "lastActivityDate": latest.get("date"),
            "lastActivityLabel": latest.get("label"),
        }
    return projections


def caseload_completed_ksb_codes(rows) -> dict[int, set[str]]:
    """Load achieved KSB codes for a caseload without hydrating progress rows."""
    learner_ids = [int(row.id) for row in rows or [] if getattr(row, "id", None) is not None]
    if not learner_ids:
        return {}
    result: dict[int, set[str]] = defaultdict(set)
    alias = get_learner_db_alias()
    with connections[alias].cursor() as cursor:
        cursor.execute(
            '''SELECT p.learner_id,k.ksb_code
               FROM "Learner".learner_progress_entries p
               JOIN "Learner".learner_progress_ksbs k ON k.progress_id=p.id
               WHERE p.learner_id=ANY(%s) AND p.deleted_at IS NULL
                 AND p.passed IS NOT FALSE
                 AND btrim(lower(coalesce(p.kind,''))) <> 'activity_event'
                 AND (btrim(lower(coalesce(p.kind,''))) <> 'quiz' OR p.passed IS TRUE)''' + and_not_excluded('k', connections[alias]),
            [learner_ids],
        )
        for learner_id, raw_code in cursor.fetchall():
            code = normalize_ksb_parent_code(raw_code)
            if code:
                result[int(learner_id)].add(code)
    return result


def fetch_case_file_shell(owner_email: str, learner_id: int):
    """Load one coach-owned learner and its stable enrolment source only."""
    requested_owner = normalize_email(owner_email)
    profile = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(id=learner_id, coach_email_key=requested_owner)
        .only(
            "id", "full_name", "email", "enrolment_id", "aptem_id", "learner_type",
            "programme", "programme_status", "cohort", "group_name", "lifecycle_status",
            "coach_name", "coach_email", "coach_rag", "start_date", "end_date",
            "gateway_review_date",
        )
        .first()
    )
    if profile is None:
        return None, None
    source = None
    if profile.enrolment_id:
        source = (
            EnrolmentUser.all_learners.filter(pk=profile.enrolment_id)
            .only(
                "id", "aptem_id", "learner_type", "username", "email", "programme",
                "programme_status", "cohort", "group", "employer", "coach_name",
                "coach_email", "start_date", "learner_start_date", "learner_end_date", "end_date",
                "apprenticeship_end_date", "practical_period_end_date",
            )
            .first()
        )
    return profile, source


def serialize_case_file_shell(profile, source) -> dict:
    from .serializers.learner_profile import serialize_learner_profile_shell
    payload = serialize_learner_profile_shell(
        profile,
        source,
        canonical_start_date=caseload_profile_start_date(SimpleNamespace(_caseload_source=source)),
        clean_text=clean_text,
        format_date=format_date,
        student_activity_available=student_activity_available,
        format_coach_rag_value=format_coach_rag_value,
    )
    # Use the same learner/source window as the dashboard, independently of
    # the recorded learner dates displayed in the profile header.
    from .selectors.otjh import learner_programme_window
    schedule_start, schedule_end = learner_programme_window(profile, source)
    payload["profile"]["otjhProgrammeStartDate"] = format_date(schedule_start) if schedule_start else None
    payload["profile"]["otjhProgrammeEndDate"] = format_date(schedule_end) if schedule_end else None
    return payload


def fetch_attendance_caseload_rows(owner_email: str, *, learner_id: str | None = None, include_plan: bool = True) -> list[LearnerProfile]:
    requested_owner = normalize_email(owner_email)
    queryset = LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email"))).filter(
        coach_email_key=requested_owner,
    )
    if learner_id:
        queryset = queryset.filter(id=learner_id)
    queryset = (
        queryset
        .only(
            "id",
            "full_name",
            "email",
            "programme",
            "programme_id",
            "programme_status",
            "cohort",
            "cohort_id",
            "group_name",
            "group_id",
            "completed_hours",
            "target_hours",
            "minimum_hours",
            "planned_hours",
            "lifecycle_status",
            "coach_name",
            "coach_email",
            "learner_type",
            "enrolment_id",
            "aptem_id",
            "start_date",
            "end_date",
        )
        .order_by("full_name", "id")
    )
    if include_plan:
        queryset = queryset.prefetch_related("plan_modules__weeks__components")
    rows: list[LearnerProfile] = []
    for row in queryset:
        if not clean_text(row.username):
            continue
        if normalize_program_status(get_lms_row_program_status(row)) not in ATTENDANCE_INCLUDED_STATUSES:
            continue
        rows.append(row)
    return rows


def fetch_owner_active_learner_profiles(owner_email: str) -> list[LearnerProfile]:
    requested_owner = normalize_email(owner_email)
    queryset = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(lifecycle_status="active", coach_email_key=requested_owner)
        .only(
            "id",
            "full_name",
            "email",
            "programme",
            "cohort",
            "group_name",
            "coach_name",
            "coach_email",
            "start_date",
            "end_date",
            "gateway_review_date",
            "minimum_hours",
            "planned_hours",
            "completed_hours",
            "target_hours",
            "otjh_status",
            "programme_status",
            "lifecycle_status",
            "enrolment_id",
            "aptem_id",
        )
        .order_by("full_name", "id")
    )
    return [row for row in queryset if clean_text(row.username)]


def fetch_source_schedule_rows(
    learners: list[LearnerProfile | SimpleNamespace],
    *, fields: tuple[str, ...] | None = None,
) -> tuple[dict[int, CommercialUser], dict[int, EnrolmentUser]]:
    """Map profile ids to Created_users rows using stable enrolment ids."""
    if not learners:
        return {}, {}

    profile_ids_by_enrolment = {
        int(getattr(learner, "enrolment_id")): int(learner.id)
        for learner in learners
        if getattr(learner, "id", None) is not None
        and getattr(learner, "enrolment_id", None) is not None
    }
    if not profile_ids_by_enrolment:
        return {}, {}

    from .services.dashboard.timing import dashboard_stage

    # Evaluate inside the measured boundary. QuerySet construction does not
    # touch Postgres and cannot detect a broken SSL connection.
    with dashboard_stage("source_schedule_query") as stats:
        source_query = EnrolmentUser.all_learners.filter(pk__in=profile_ids_by_enrolment)
        if fields is not None:
            source_query = source_query.only(*fields)
        source_rows = list(source_query)
        stats["row_count"] = len(source_rows)

    commercial_rows = {}
    enrolment_rows = {}
    for row in source_rows:
        profile_id = profile_ids_by_enrolment.get(int(row.id))
        if profile_id is None:
            continue
        if clean_text(row.learner_type).casefold() == "commercial":
            commercial_rows[profile_id] = row
        else:
            enrolment_rows[profile_id] = row
    return commercial_rows, enrolment_rows


def profile_prefers_apprenticeship_source(profile: LearnerProfile | SimpleNamespace) -> bool:
    programme = clean_text(getattr(profile, "programme", "")).casefold()
    lifecycle = clean_text(getattr(profile, "lifecycle_status", "")).casefold()
    return "apprentice" in programme or programme.startswith("apm") or lifecycle == "onboarding"


def resolve_caseload_source_row(
    learner: LearnerProfile | SimpleNamespace,
    *,
    commercial_rows: dict[int, CommercialUser] | None = None,
    enrolment_rows: dict[int, EnrolmentUser] | None = None,
) -> CommercialUser | EnrolmentUser | None:
    learner_id = getattr(learner, "id", None)
    if learner_id is None:
        return None
    if commercial_rows is None and enrolment_rows is None:
        commercial_rows, enrolment_rows = fetch_source_schedule_rows([learner])
    commercial_rows = commercial_rows or {}
    enrolment_rows = enrolment_rows or {}
    commercial_row = commercial_rows.get(learner_id)
    enrolment_row = enrolment_rows.get(learner_id)
    if profile_prefers_apprenticeship_source(learner):
        return enrolment_row or commercial_row
    return commercial_row or enrolment_row


def learner_calendar_source_identity(
    learner: LearnerProfile | SimpleNamespace,
    *,
    commercial_rows: dict[int, CommercialUser] | None = None,
    enrolment_rows: dict[int, EnrolmentUser] | None = None,
) -> tuple[str, int] | None:
    source = resolve_caseload_source_row(
        learner,
        commercial_rows=commercial_rows,
        enrolment_rows=enrolment_rows,
    )
    if source is None or not getattr(source, "id", None):
        return None
    kind = "commercial" if clean_text(getattr(source, "learner_type", "")).casefold() == "commercial" else "apprenticeship"
    return kind, int(source.id)


def coach_learner_personal_calendar_conflicts(
    learner: LearnerProfile | SimpleNamespace,
    scheduled_date: date,
    scheduled_time: time,
    duration_minutes: int,
    timezone_offset_minutes: int = 0,
    *, exclude_record=None, business_time_zone=None,
) -> bool:
    identity = learner_calendar_source_identity(learner)
    if not identity:
        return False
    kind, source_id = identity
    exclusion = {} if exclude_record is None else {
        "exclude_scheduled_date": exclude_record.scheduled_date,
        "exclude_scheduled_time": exclude_record.scheduled_time,
        "exclude_duration_minutes": exclude_record.duration_minutes,
    }
    if business_time_zone:
        exclusion["business_time_zone"] = business_time_zone
    return personal_calendar_booking_conflicts(
        kind,
        source_id,
        scheduled_date,
        scheduled_time,
        duration_minutes,
        timezone_offset_minutes,
        **exclusion,
    )


def refresh_caseload_learner_ksb_snapshot(row: LearnerProfile | SimpleNamespace) -> None:
    if not callable(getattr(row, "save", None)):
        return
    # An existing learner stays pinned to the KSB profile version assigned at
    # enrolment (see replace_learner_ksbs) — once `ksb_assignment` exists,
    # refresh_learner_ksb_snapshot can only ever re-derive and no-op via its
    # own get_or_create calls. Skipping it here avoids opening a transaction
    # + two get_or_create round-trips per learner on every `?live=1` caseload
    # read — the same "unconditional per-row DB work on a read" shape as the
    # sync_active_user bug fixed in learner_progression.advance_learner.
    if getattr(row, "ksb_assignment", None) is not None:
        return
    source = getattr(row, "_caseload_source", None) or resolve_caseload_source_row(row)
    if source is None:
        return
    try:
        refresh_learner_ksb_snapshot(row, source, training_plan=getattr(row, "training_plan", None))
        prefetched = getattr(row, "_prefetched_objects_cache", None)
        if isinstance(prefetched, dict):
            prefetched.pop("assigned_ksbs", None)
    except Exception as exc:
        logger.warning(
            "Could not refresh live KSB snapshot for learner %s: %s",
            getattr(row, "id", None),
            exc,
        )


def resolve_schedule_window(
    learner_id: int,
    commercial_rows: dict[int, CommercialUser],
    enrolment_rows: dict[int, EnrolmentUser],
    learner: LearnerProfile | None = None,
) -> tuple[date | None, date | None]:
    commercial_row = commercial_rows.get(learner_id)
    if commercial_row:
        start_value = (
            getattr(commercial_row, "start_date", None)
            or getattr(commercial_row, "learner_start_date", None)
        )
        end_value = (
            getattr(commercial_row, "end_date", None)
            or getattr(commercial_row, "learner_end_date", None)
        )
    else:
        enrolment_row = enrolment_rows.get(learner_id)
        start_value = (
            getattr(enrolment_row, "start_date", None)
            or getattr(enrolment_row, "learner_start_date", None)
        ) if enrolment_row else None
        end_value = (
            getattr(enrolment_row, "end_date", None)
            or getattr(enrolment_row, "learner_end_date", None)
            or getattr(enrolment_row, "apprenticeship_end_date", None)
            or getattr(enrolment_row, "practical_period_end_date", None)
        ) if enrolment_row else None

    start_value = start_value or getattr(learner, "start_date", None)
    end_value = end_value or getattr(learner, "end_date", None)
    start_date = parse_date_value(start_value)
    end_date = parse_date_value(end_value)
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    if isinstance(end_date, datetime):
        end_date = end_date.date()
    return start_date, end_date


def caseload_schedule_values(row) -> tuple[object, object, object]:
    """Return the SSOT plan total and learner window for coach payloads.

    ``LearnerProfile`` is a projection.  The enrolment source row is the
    authoritative bridge to Aptem and may contain the learner-specific text
    dates while the profile's cohort-window mirror is blank.  Prefer the
    source row, then fall back to the profile, without fabricating a date.
    """
    contract = getattr(row, "_caseload_contract", None)
    source = getattr(row, "_caseload_source", None)

    def first_value(*names):
        for owner in (contract, source, row):
            if owner is None:
                continue
            for name in names:
                value = owner.get(name) if isinstance(owner, dict) else getattr(owner, name, None)
                if value not in (None, "") and clean_text(value).casefold() not in {"none", "null", "--", "n/a"}:
                    return value
        return None

    planned = first_value("training_plan_planned_hours", "planned_hours")
    start = first_value("program_start_date", "start_date", "learner_start_date")
    end = first_value(
        "planned_end_date", "end_date", "learner_end_date",
        "apprenticeship_end_date", "practical_period_end_date",
    )
    return planned, start, end


def caseload_profile_start_date(row):
    """Only Created_users.Learner_start_date, from the bulk enrolment lookup."""
    source = getattr(row, "_caseload_source", None)
    value = getattr(source, "learner_start_date", None)
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or not value.strip():
        return None
    # Parse the complete value: a valid date prefix must not hide invalid text.
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def caseload_display_start_date(row):
    """Compatibility display alias of the canonical enrolment start date."""
    return format_date(caseload_profile_start_date(row))


# Why a learner could not be anchored for Review recurrence. One vocabulary
# shared by the debug diagnostics, the timetable's diagnostic counts and the
# audit_review_anchor_dates command, so a reason string never has to be
# re-spelled at a call site.
REVIEW_ANCHOR_MISSING_ROW = "missing_created_users_row"
REVIEW_ANCHOR_MISSING_START = "missing_start_date"
REVIEW_ANCHOR_INVALID_START = "invalid_start_date"


def resolve_review_anchor_date(
    learner_id: int,
    commercial_rows: dict[int, CommercialUser],
    enrolment_rows: dict[int, EnrolmentUser],
) -> tuple[date | None, str | None]:
    """The anchor Review recurrence counts from -- STRICTLY the learner's own
    enrolment."Created_users"."Learner_start_date", or nothing at all.

    This is a distinct column from "Created_users"."Start_date" (the general
    enrolment/programme start date resolve_schedule_window and other
    scheduling legitimately read) -- the two can and do disagree for real
    learners, and only Learner_start_date is the authoritative business date
    for when Curriculum Review recurrence should count from. Using Start_date
    here was a bug: it produced missing/incorrect anchors for learners whose
    Start_date was blank or predated their actual Learner_start_date.

    Deliberately NOT ``resolve_schedule_window``. That helper ends with

        start_value = start_value or getattr(learner, "start_date", None)

    and "Learner"."learners".start_date is the profile mirror, which
    ``active_users.mirror_learner_placement`` stamps with the COHORT delivery
    window on every placement edit. For a window bound that fallback is
    reasonable -- it only decides how far ahead occurrences are listed. For the
    ANCHOR it is not: it silently turns "we do not know when this learner
    started" into "they started when their cohort did", and every Review date
    downstream inherits that guess with nothing to show it was guessed. A
    learner with no usable Learner_start_date must generate no Reviews,
    loudly -- never Start_date, never the profile mirror, never an Aptem
    column, never a cohort/programme date.

    The source-row preference (commercial first, then enrolment) matches
    ``resolve_schedule_window`` exactly, so no learner whose enrolment row
    carries a usable date sees their anchor move.

    The column is TEXT in Postgres (see EnrolmentUser.learner_start_date), so
    the raw value is always put through ``parse_date_value`` -- never compared
    to a ``date`` directly.

    Returns ``(anchor, None)`` or ``(None, reason)`` where reason is one of the
    REVIEW_ANCHOR_* constants above.
    """
    source_row = commercial_rows.get(learner_id) or enrolment_rows.get(learner_id)
    if source_row is None:
        return None, REVIEW_ANCHOR_MISSING_ROW

    raw_start = getattr(source_row, "learner_start_date", None)
    if not clean_text(raw_start):
        return None, REVIEW_ANCHOR_MISSING_START

    parsed = parse_date_value(raw_start)
    if isinstance(parsed, datetime):
        parsed = parsed.date()
    if parsed is None:
        return None, REVIEW_ANCHOR_INVALID_START
    return parsed, None


def log_review_anchor_skip(
    learner: LearnerProfile | SimpleNamespace,
    reason: str,
    *,
    programme_id: str | None,
    template_cache: dict[str, list[dict]] | None = None,
) -> None:
    """Say which Reviews were NOT generated for this learner, and why.

    One line per Review template the programme would have produced, so the
    message names the actual review_template_id / review_type_code a coach is
    missing rather than only the learner. Identifiers only -- no dates, no
    progress data, nothing beyond what is needed to find and fix the record.
    """
    try:
        templates = curriculum_review_instances.programme_review_template_identifiers(
            programme_id, template_cache=template_cache,
        )
    except Exception:  # a diagnostic must never be the thing that breaks a page
        templates = []

    base = (
        "Review generation skipped: no learner-specific start date. "
        "learner_id=%s learner_email=%s learner_name=%s programme_id=%s "
        "review_template_id=%s review_type_code=%s reason=%s"
    )
    learner_id = getattr(learner, "id", None)
    learner_email = clean_text(getattr(learner, "email", ""))
    learner_name = clean_text(getattr(learner, "full_name", "")) or clean_text(getattr(learner, "username", ""))
    if not templates:
        logger.debug(base, learner_id, learner_email, learner_name, programme_id, None, None, reason)
        return
    for template_id, type_code in templates:
        logger.debug(base, learner_id, learner_email, learner_name, programme_id, template_id, type_code, reason)


def learner_activity_feed_entries(row: LearnerProfile | SimpleNamespace, *, newest_first: bool = False) -> list[dict]:
    activity_reader = getattr(row, "activity_feed_entries", None)
    if callable(activity_reader):
        return [entry for entry in activity_reader(newest_first=newest_first) if isinstance(entry, dict)]
    return [entry for entry in list_or_empty(getattr(row, "activity_feed", [])) if isinstance(entry, dict)]


def latest_learning_activity(progress_entries: list[dict], activity_entries: list[dict]) -> dict | None:
    """Return the newest real learner action across progress and activity feeds."""
    def activity_instant(value):
        if isinstance(value, (date, datetime)):
            return value
        try:
            return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return parse_date_value(value)

    candidates = []
    for entry in [*progress_entries, *activity_entries]:
        if not isinstance(entry, dict):
            continue
        occurred_at = next(
            (
                activity_instant(entry.get(field))
                for field in ("submittedAt", "at", "completedAt", "startedAt", "date", "createdAt")
                if entry.get(field)
            ),
            None,
        )
        if not occurred_at:
            continue
        if isinstance(occurred_at, date) and not isinstance(occurred_at, datetime):
            sort_value = datetime.combine(occurred_at, time.min)
        else:
            sort_value = occurred_at.replace(tzinfo=None) if occurred_at.tzinfo else occurred_at
        title = clean_text(
            entry.get("componentTitle")
            or entry.get("title")
            or entry.get("quizTitle")
            or entry.get("activityTitle")
        ) or clean_text(entry.get("kind")) or "Learning activity"
        candidates.append((sort_value, occurred_at, title))

    if not candidates:
        return None
    _, occurred_at, title = max(candidates, key=lambda candidate: candidate[0])
    return {
        "date": occurred_at.isoformat(),
        "display": format_date_value(occurred_at),
        "label": title,
    }


def caseload_latest_learning_activities(rows) -> dict[int, dict]:
    """Bulk-load at most two latest candidates per learner.

    The previous bulk query removed the HTTP N+1 but still transferred every
    historical progress row to Python. PostgreSQL now picks the newest normal
    progress row and newest feed row for each learner; Python retains the
    established cross-source date/title selection.
    """
    learner_ids = [int(row.id) for row in rows or [] if getattr(row, "id", None) is not None]
    if not learner_ids:
        return {}
    progress_by_learner: dict[int, list[dict]] = defaultdict(list)
    feed_by_learner: dict[int, list[dict]] = defaultdict(list)
    query = '''
        WITH progress_candidates AS (
            SELECT DISTINCT ON (learner_id)
                   learner_id, 'progress' AS source_kind, kind, component_title,
                   submitted_at, started_at, feed_kind, feed_title, feed_occurred_at
              FROM "Learner".learner_progress_entries
             WHERE learner_id = ANY(%s)
               AND COALESCE(kind, '') <> 'activity_event'
               AND COALESCE(submitted_at, started_at) IS NOT NULL
             ORDER BY learner_id, COALESCE(submitted_at, started_at) DESC,
                      entry_order ASC, id ASC
        ),
        feed_candidates AS (
            SELECT DISTINCT ON (learner_id)
                   learner_id, 'feed' AS source_kind, kind, component_title,
                   submitted_at, started_at, feed_kind, feed_title, feed_occurred_at
              FROM "Learner".learner_progress_entries
             WHERE learner_id = ANY(%s)
               AND NULLIF(BTRIM(feed_kind), '') IS NOT NULL
               AND COALESCE(feed_occurred_at, submitted_at) IS NOT NULL
             ORDER BY learner_id, COALESCE(feed_occurred_at, submitted_at) DESC,
                      entry_order ASC, id ASC
        )
        SELECT * FROM progress_candidates
        UNION ALL
        SELECT * FROM feed_candidates
    '''
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute(query, [learner_ids, learner_ids])
        candidates = cursor.fetchall()

    for (
        learner_id, source_kind, kind, component_title, submitted_at,
        started_at, feed_kind, feed_title, feed_occurred_at,
    ) in candidates:
        profile_id = int(learner_id)
        if source_kind == "progress":
            progress_by_learner[profile_id].append({
                "kind": kind,
                "componentTitle": component_title,
                "submittedAt": submitted_at.isoformat() if submitted_at else "",
                "startedAt": started_at.isoformat() if started_at else "",
            })
        else:
            occurred_at = feed_occurred_at or submitted_at
            feed_by_learner[profile_id].append({
                "kind": feed_kind,
                "title": feed_title or component_title,
                "at": occurred_at.isoformat() if occurred_at else "",
            })

    activities = {}
    for profile_id in set(progress_by_learner) | set(feed_by_learner):
        candidate = latest_learning_activity(
            progress_by_learner.get(profile_id, []),
            feed_by_learner.get(profile_id, []),
        )
        if candidate:
            activities[profile_id] = candidate
    return activities


def apply_latest_learning_activity(payload: dict, activity: dict | None) -> dict:
    if not activity:
        return payload
    payload["lastActivityDate"] = activity["date"]
    payload["lastActivity"] = activity["display"]
    payload["lastActivityLabel"] = activity["label"]
    return payload


class ReviewEnrolmentIdentityUnavailable(DatabaseError):
    """A successful identity lookup found missing referenced enrolment rows."""

    def __init__(self, profile_ids):
        super().__init__("Review enrolment identity unavailable")
        self.profile_ids = frozenset(profile_ids)


def fetch_caseload_aptem_ids(learners, *, strict=False) -> dict[int, int]:
    """LearnerProfile id -> Aptem id, loading only the columns that needs.

    ``fetch_source_schedule_rows`` answers the same identity question but
    selects whole ``Created_users`` rows -- a very wide table -- because its
    callers go on to read schedule fields. The audit lookups only need the
    Aptem bridge, so this deferred-column variant keeps the dashboard's first
    paint from paying for columns nobody reads.
    """
    learners = list(learners or [])
    profile_ids_by_enrolment = {
        int(learner.enrolment_id): int(learner.id)
        for learner in learners or []
        if getattr(learner, "id", None) is not None
        and getattr(learner, "enrolment_id", None) is not None
    }
    if not profile_ids_by_enrolment:
        return {}
    try:
        source_rows = (
            EnrolmentUser.all_learners
            .filter(pk__in=profile_ids_by_enrolment)
            .values_list("id", "aptem_id")
        )
        pairs = list(source_rows)
    except DatabaseError as exc:
        if strict:
            raise
        logger.warning("Could not resolve caseload Aptem ids: %s", exc)
        return {}

    missing_enrolments = set(profile_ids_by_enrolment) - {int(pk) for pk, _ in pairs}
    if strict and missing_enrolments:
        raise ReviewEnrolmentIdentityUnavailable(
            int(learner.id) for learner in learners
            if getattr(learner, "id", None) is not None
            and getattr(learner, "enrolment_id", None) is not None
            and int(learner.enrolment_id) in missing_enrolments
        )
    aptem_by_profile: dict[int, int] = {}
    for enrolment_id, aptem_id in pairs:
        profile_id = profile_ids_by_enrolment.get(int(enrolment_id))
        if profile_id is None or not student_activity_available(aptem_id):
            continue
        aptem_by_profile[profile_id] = int(str(aptem_id).strip())
    return aptem_by_profile


def resolve_effective_aptem_ids(rows, *, strict=False) -> tuple[dict[int, int], set[int]]:
    """Resolve Aptem identity by stable ids only.

    Created_users.aptem_id is authoritative when present and the profile copy
    is a legacy fallback.  A disagreement is not guessed through: the learner
    is returned in the conflict set and belongs to neither review source until
    the identity is corrected.
    """
    from .review_sources import resolve_review_source

    rows = list(rows or [])
    unresolved = [row for row in rows if getattr(row, "_caseload_source", None) is None]
    fetched = (fetch_caseload_aptem_ids(unresolved, strict=True) if strict else fetch_caseload_aptem_ids(unresolved)) if unresolved else {}
    aptem_by_profile: dict[int, int] = {}
    conflicts: set[int] = set()
    for row in rows:
        profile_id = getattr(row, "id", None)
        if profile_id is None:
            continue
        profile_id = int(profile_id)
        source_row = getattr(row, "_caseload_source", None)
        source_raw = getattr(source_row, "aptem_id", None) if source_row is not None else fetched.get(profile_id)
        profile_raw = getattr(row, "aptem_id", None)
        source = resolve_review_source(source_raw, profile_raw)
        if source.kind == "conflict":
            conflicts.add(profile_id)
            continue
        if source.kind == "aptem":
            aptem_by_profile[profile_id] = source.aptem_id
    return aptem_by_profile, conflicts


def caseload_aptem_ids(rows) -> dict[int, int]:
    """Map LearnerProfile id -> Aptem id for a caseload.

    Prefer the enrolment source row and use LearnerProfile.aptem_id only as the
    legacy fallback. Learners with no Aptem link or conflicting ids are absent.
    """
    aptem_by_profile, _conflicts = resolve_effective_aptem_ids(rows)
    return aptem_by_profile


def caseload_evidenced_ksb_counts(rows) -> dict[int, int]:
    """Evidenced KSB counts for a caseload, keyed by LearnerProfile id.

    The coach surfaces recompute KSB progress per learner from the curriculum
    profile and every progress entry -- about a quarter-second each, which a
    40-learner caseload cannot absorb, and which reads 0 for learners whose
    evidence lives in the audit mirror rather than the LMS training plan. This
    is the same audit KSB mapping the figure is drawn from there, in one query.
    """
    aptem_by_profile = caseload_aptem_ids(rows)
    if not aptem_by_profile:
        return {}
    try:
        with audit_connection().cursor() as cursor:
            counts = read_evidenced_ksb_counts_bulk(cursor, aptem_by_profile.values())
    except (ConnectionDoesNotExist, DatabaseError) as exc:
        # A display upgrade, not a dependency: the caseload still renders.
        logger.warning("Could not read audit KSB counts for caseload: %s", exc)
        return {}
    return {
        profile_id: counts[aptem_id]
        for profile_id, aptem_id in aptem_by_profile.items()
        if aptem_id in counts
    }


def caseload_audit_hour_totals(rows) -> dict[int, dict]:
    """Whole-programme OTJ hours for a caseload, keyed by LearnerProfile id.

    The coach surfaces used to quote `Learner.learners.completed_hours`, which
    only counts time reflected against the LMS training plan. The learner's own
    workspace shows the Audit figures instead (TP Planned from
    "Last_audit".learners, LMS Actual from the accepted manual ledger), so a
    coach and their learner were reading two different numbers for the same
    thing. This pulls the learner-page figures for the whole caseload in one
    query so both sides agree.

    `aptem_id` is not on LearnerProfile; it comes off the enrolment source row
    already attached as `_caseload_source` by fetch_caseload_learner_profiles.
    Learners with no Aptem link, or with no row in the audit mirror, are absent
    from the result and keep their existing figures.
    """
    aptem_by_profile = caseload_aptem_ids(rows)
    if not aptem_by_profile:
        return {}
    try:
        with audit_connection().cursor() as cursor:
            totals = read_audit_hour_totals_bulk(cursor, aptem_by_profile.values())
    except (ConnectionDoesNotExist, DatabaseError) as exc:
        # The caseload must still render on its stored figures if the audit
        # mirror is unreachable -- this is a display upgrade, not a dependency.
        logger.warning("Could not read audit OTJ totals for caseload: %s", exc)
        return {}
    return {
        profile_id: totals[aptem_id]
        for profile_id, aptem_id in aptem_by_profile.items()
        if aptem_id in totals
    }


def caseload_canonical_metrics(rows, *, learner_workspace=False) -> dict[int, dict]:
    """Read the same live programme/KSB/OTJH facts as the learner dashboard.

    Coach list endpoints keep this server-side so clients do not issue one
    request per learner. Individual failures retain that learner's existing
    snapshot without making the rest of the caseload unavailable.
    """
    perf_started = perf_counter()
    work = []
    for row in rows or []:
        source = getattr(row, "_caseload_source", None)
        if source is None:
            continue
        kind = "commercial" if clean_text(getattr(row, "learner_type", "")).casefold() == "commercial" else "apprenticeship"
        work.append((int(row.id), source, kind))
    if not work:
        return {}

    all_work = work
    unavailable_metrics = {
        "migrated": True, "aptem_planned_total": None,
        "programme": {"completed": 0, "total": 0, "percent": None, "status": "unavailable"},
        "otjh": {"historical": None, "new": 0, "actual": None, "completed_actual": None, "planned": None},
        "ksb": {"completed": 0, "total": 0, "percent": None, "status": "unavailable", "codes": []},
        "_ksb_evidence_sources": [],
    }
    enrolment_ids = [int(source.pk) for _, source, _ in all_work]
    try:
        if learner_workspace:
            canonical_by_enrolment = canonical_learning.metrics_bulk(enrolment_ids, learner_workspace=True)
        else:
            canonical_by_enrolment = canonical_learning.metrics_bulk(enrolment_ids)
    except DatabaseError as exc:
        # Keep the established partial-success fallback if the consolidated
        # projection is temporarily unavailable.
        logger.warning("Could not bulk-read canonical coach metrics: %s", exc)
        canonical_by_enrolment = {enrolment_id: None for enrolment_id in enrolment_ids}
    work = [
        item for item in all_work
        if int(item[1].pk) not in canonical_by_enrolment
    ]
    result = {
        profile_id: canonical_by_enrolment[int(source.pk)] or unavailable_metrics
        for profile_id, source, _kind in all_work
        if int(source.pk) in canonical_by_enrolment
    }
    if not work:
        _coach_perf("dashboard", "canonical_metrics", perf_started, learner_count=len(all_work))
        return result

    enrolment_ids = [int(source.pk) for _, source, _ in work]
    direct_progress = load_direct_progress_records_bulk(enrolment_ids)
    # ``source.pk`` is the canonical internal enrolment identity. ``aptem_id``
    # is only an optional bridge to retained Aptem/audit data: native learners
    # created in this LMS legitimately have no value.  Normalize that bridge
    # once so no Aptem-dependent preload invents an identity or coerces NULL.
    aptem_by_enrolment = {
        int(source.pk): int(str(source.aptem_id).strip())
        for _, source, _ in work
        if student_activity_available(getattr(source, 'aptem_id', None))
    }
    attempt_keys = list(aptem_by_enrolment.items())
    subject_attempts = load_subject_attempts_bulk(attempt_keys)
    manual_hours = load_manual_hours_bulk([aptem for _, aptem in attempt_keys])
    reflection_keys = [(kind, int(source.pk)) for _, source, kind in work]
    reflection_submissions = load_reflection_submissions_bulk(reflection_keys)
    audit_inputs = load_audit_inputs_bulk([aptem for _, aptem in attempt_keys])
    native_progress = load_native_progress_bulk(enrolment_ids)
    preset_cache = {}
    plan_ids_by_enrolment = {
        int(source.pk): _effective_plan_ids(source, preset_cache)
        for _, source, _ in work
    }
    component_by_module = load_native_components_bulk(
        [module_id for ids in plan_ids_by_enrolment.values() for module_id in ids]
    )
    planned_documents = load_planned_hours_documents_bulk([
        (int(source.pk), kind) for _, source, kind in work
    ])
    contracts = load_contracts_bulk([aptem for _, aptem in attempt_keys])
    accepted_ksb_rows = load_accepted_ksb_rows_bulk([
        (int(source.pk), aptem_by_enrolment[int(source.pk)], kind)
        for _, source, kind in work
        if int(source.pk) in aptem_by_enrolment
    ])
    audit_groups = {
        int(item['group_id'])
        for audit in audit_inputs.values()
        for item in (audit or {}).get('historical', [])
        if item.get('group_id') is not None
    }
    export_links = load_export_links_bulk(audit_groups)

    def load_metrics_inputs(items):
        """Load canonical inputs in one bounded, request-owned DB phase.

        The calculation remains the existing ``read_metrics`` implementation;
        importantly, it is not fanned out into ORM threads.  Django connections
        are thread-local, so the previous per-learner executor multiplied DB
        connections and could nest under the dashboard executor.
        """
        loaded = []
        for item in items:
            profile_id, source, kind = item
            enrolment_id = int(source.pk)
            aptem_id = aptem_by_enrolment.get(enrolment_id)
            audit_input = audit_inputs.get(aptem_id) if aptem_id is not None else None
            try:
                loaded.append((profile_id, read_metrics(
                    source, kind,
                    preloaded={
                        'direct_progress': direct_progress.get(enrolment_id, []),
                        'subject_attempts': subject_attempts.get((enrolment_id, aptem_id), set()) if aptem_id is not None else set(),
                        'manual_hours': manual_hours.get(aptem_id) if aptem_id is not None else None,
                        'reflection_submissions': reflection_submissions.get((kind, str(source.pk)), []),
                        'audit_inputs': audit_input,
                        'historical_metadata_loaded': True,
                        'native_progress': native_progress.get(enrolment_id, []),
                        'effective_plan_ids': plan_ids_by_enrolment.get(enrolment_id, []),
                        'native_components': [
                            item for module_id in plan_ids_by_enrolment.get(enrolment_id, [])
                            for item in component_by_module.get(str(module_id), [])
                        ],
                        'planned_hours_document': planned_documents.get((enrolment_id, kind)),
                        'planned_hours_contract': contracts.get(aptem_id) if aptem_id is not None else None,
                        'aptem_planned_total': (audit_input or {}).get('aptem_planned_total'),
                        'accepted_ksb_rows': accepted_ksb_rows.get(enrolment_id, []),
                        'export_links': [
                            row
                            for group_id in {
                                int(item['group_id'])
                                for item in (audit_input or {}).get('historical', [])
                                if item.get('group_id') is not None
                            }
                            for row in export_links.get(group_id, [])
                        ],
                    },
                )))
                if os.environ.get("COACH_KSB_DIAGNOSTICS") == "1" and profile_id in {315, 316, 318, 319}:
                    metrics = loaded[-1][1] or {}
                    preloaded = {
                        'effective_plan_ids': plan_ids_by_enrolment.get(int(source.pk), []),
                        'native_components': [
                            component
                            for module_id in plan_ids_by_enrolment.get(int(source.pk), [])
                            for component in component_by_module.get(str(module_id), [])
                        ],
                        'native_progress': native_progress.get(int(source.pk), []),
                        'audit_inputs': audit_input,
                    }
                    native_components = preloaded['native_components']
                    audit_input = preloaded['audit_inputs'] or {}
                    historical = audit_input.get('historical') or []
                    native_mapping_count = sum(
                        len(extract_ksb_codes(component.get('ksb_mappings')))
                        for component in native_components
                    )
                    historical_mapping_count = sum(
                        len(extract_ksb_codes(activity.get('ksb_mappings')))
                        for activity in historical
                    )
                    ksb = metrics.get('ksb') or {}
                    logger.info("coach_ksb_diagnostic %s", {
                        'learnerProfileId': profile_id,
                        'effectivePlanIds': preloaded['effective_plan_ids'],
                        'nativeComponentsCount': len(native_components),
                        'nativeKsbMappingsCount': native_mapping_count,
                        'nativeProgressCount': len(preloaded['native_progress']),
                        'auditIdentityReady': bool(audit_input.get('identity')),
                        'historicalActivitiesCount': len(historical),
                        'historicalMappedKsbCount': historical_mapping_count,
                        'activityPointsMissing': ksb.get('reason') == 'activity_points_missing',
                        'historicalActivitiesMissing': ksb.get('reason') == 'historical_activities_missing',
                        'unmappedActivities': ksb.get('unmappedActivities'),
                        'mappedCompleted': ksb.get('mappedCompleted'),
                        'mappedTotal': ksb.get('mappedTotal'),
                        'historicalCompleted': ksb.get('historicalCompleted'),
                        'finalKsbStatus': ksb.get('status'),
                        'finalKsbCompleted': ksb.get('completed'),
                        'finalKsbTotal': ksb.get('total'),
                        'finalKsbPercent': ksb.get('percent'),
                    })
            except (DatabaseError, ValueError, ServiceError) as exc:
                logger.warning("Could not read canonical coach metrics for learner %s: %s", profile_id, exc)
                # Preserve partial-success semantics: one bad learner does not
                # discard metrics already loaded for the rest of the caseload.
        return loaded

    results = load_metrics_inputs(work)
    result.update({profile_id: metrics for profile_id, metrics in results if metrics is not None})
    _coach_perf("dashboard", "canonical_metrics", perf_started, learner_count=len(all_work))
    return result


def caseload_canonical_attendance(rows) -> dict[int, dict]:
    """Return the exact combined attendance summaries used by learner pages."""
    work = [
        (int(row.id), getattr(row, "_caseload_source", None))
        for row in rows or []
        if getattr(row, "_caseload_source", None) is not None
    ]
    if not work:
        return {}
    sources_by_profile = dict(work)
    from learner_api.attendance_lectures import lecture_register

    def summarize(profile_id, source, records=None):
        try:
            return _summarize_attendance(lecture_register(source, records=records))
        except Exception as exc:
            logger.warning("Could not read canonical coach attendance for learner %s: %s", profile_id, exc)
            return None
        finally:
            close_old_connections()

    aptem_work = [
        (profile_id, source) for profile_id, source in work
        if student_activity_available(getattr(source, "aptem_id", None))
    ]
    result = {}
    if aptem_work:
        identities = [
            {"aptem_id": source.aptem_id, "learner_id": source.id,
             "learner_name": getattr(source, "username", "") or "",
             "learner_email": getattr(source, "email", "") or ""}
            for _, source in aptem_work
        ]
        kbc_rows = fetch_kbc_attendance_rows_bulk(identities)
        emails = [item["learner_email"] for item in identities if item["learner_email"]]
        teams_rows = fetch_verified_teams_attendance_rows(learner_emails=emails) if emails else []
        profile_by_enrolment = {int(source.id): profile_id for profile_id, source in aptem_work}
        combined_by_profile = {profile_id: [] for profile_id, _ in aptem_work}
        for item in kbc_rows:
            profile_id = profile_by_enrolment.get(int(item["learner_id"]))
            if profile_id is not None:
                combined_by_profile[profile_id].append({**item, "source": "kbc-attendance"})
        for item in teams_rows:
            enrolment_id = to_int(item.get("enrolment_id"))
            profile_id = profile_by_enrolment.get(enrolment_id) if enrolment_id is not None else None
            if profile_id is not None:
                combined_by_profile[profile_id].append({**item, "source": "microsoft-teams"})
        for profile_id, items in combined_by_profile.items():
            unique = {}
            for item in items:
                updated_at = item.get("updated_at")
                if updated_at and timezone.is_naive(updated_at):
                    item = {**item, "updated_at": timezone.make_aware(updated_at, datetime_timezone.utc)}
                key = (item.get("source"), str(item.get("occurrence_id") or item.get("session_id")), item.get("session_date"))
                previous = unique.get(key)
                if previous is None or item.get("attendance_status") in {"present", "late"}:
                    unique[key] = item
            summary = summarize(profile_id, sources_by_profile[profile_id], list(unique.values()))
            if summary is not None:
                result[profile_id] = summary

    other_results = [(profile_id, summarize(profile_id, source))
                     for profile_id, source in work if (profile_id, source) not in aptem_work]
    result.update({profile_id: summary for profile_id, summary in other_results if summary is not None})
    return result


def caseload_kbc_attendance_rates(rows) -> tuple[dict[int, int], dict[int, dict]]:
    """Return KBC attendance metrics keyed by LearnerProfile id.

    The coach caseload table must use the KBC register for learners that have
    an Aptem identity. Keep the profile-to-Aptem mapping alongside the metrics
    so a learner with no KBC rows is not silently filled from Teams data.
    """
    aptem_by_profile = caseload_aptem_ids(rows)
    if not aptem_by_profile:
        return {}, {}
    try:
        rates = fetch_kbc_attendance_rates(aptem_by_profile.values())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read KBC attendance rates for coach caseload: %s", exc)
        rates = {}

    # Some KBC register imports carry a valid email but an empty or legacy ID.
    # Use the same KBC table's email index as a fallback, never the Teams
    # attendance projection.
    email_metrics: dict[str, dict] = {}
    email_keys = [normalize_email(getattr(row, "email", None)) for row in rows or []]
    email_keys = [email for email in email_keys if email]
    if email_keys:
        try:
            email_metrics = fetch_attendance_data(email_keys).get("metrics", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read KBC attendance rates by email: %s", exc)

    rows_by_profile = {int(row.id): row for row in rows or []}
    return aptem_by_profile, {
        profile_id: rates.get(str(aptem_id))
        or email_metrics.get(normalize_email(getattr(rows_by_profile[profile_id], "email", None)))
        for profile_id, aptem_id in aptem_by_profile.items()
        if rates.get(str(aptem_id))
        or email_metrics.get(normalize_email(getattr(rows_by_profile[profile_id], "email", None)))
    }


def apply_canonical_learner_metrics(payload: dict, metrics: dict | None, *, learner_workspace=False) -> dict:
    """Overlay the exact learner-profile metrics onto a coach learner row.

    The caseload table and learner case file are two views of the same learner
    facts. The Case File header prefers the canonical planned total, so the
    table must use that same denominator after audit enrichment.
    """
    if not metrics and not learner_workspace:
        return payload
    metrics = metrics or {}
    programme = metrics.get("programme") or {}
    ksb = metrics.get("ksb") or {}
    otjh = metrics.get("otjh") or {}

    canonical_plan = otjh.get("planned")
    # Match buildCoachLearnerCaseFileData's nullish fallback exactly:
    # canonical planned hours first, then learner-detail target hours.
    fallback_plan = payload.get("otjhPlanned")
    if fallback_plan is None:
        fallback_plan = payload.get("otjhTarget")
    if learner_workspace:
        # Overview prefers the Aptem programme plan and shows the canonical
        # actual total, rather than the Case File's completed-only total.
        if metrics.get("aptem_planned_total") is not None:
            canonical_plan = metrics["aptem_planned_total"]
        fallback_plan = None
    resolved_plan = canonical_plan if canonical_plan is not None else fallback_plan
    payload["otjhTarget"] = resolved_plan
    payload["otjhPlanned"] = resolved_plan
    completed_actual = otjh.get("actual") if learner_workspace else otjh.get("completed_actual", otjh.get("actual"))
    if learner_workspace:
        payload["otjhCompleted"] = completed_actual
    elif completed_actual is not None:
        payload["otjhCompleted"] = to_number(completed_actual)
    payload["otjhActual"] = otjh.get("actual")

    payload["programmeCompleted"] = programme.get("completed")
    payload["programmeTarget"] = programme.get("total")
    payload["programmeProgress"] = programme.get("percent")
    payload["programmeProgressAvailable"] = programme.get("status") == "ready"
    payload["componentsCompleted"] = programme.get("completed")
    payload["componentsPlanned"] = programme.get("total")
    payload["activityProgress"] = programme.get("percent")
    payload["activityProgressAvailable"] = programme.get("status") == "ready"
    # Preserve the established fallback when the canonical reader cannot
    # produce a complete KSB population. Dashboard payloads start unavailable,
    # so no ratio is invented here.
    if learner_workspace and ksb.get("status") != "ready":
        payload.update(ksbCompleted=None, ksbTarget=None, ksbProgress=None,
                       ksbProgressAvailable=False, ksbStatus="Unavailable")
    if ksb.get("status") == "ready":
        payload["ksbCompleted"] = ksb.get("completed")
        payload["ksbTarget"] = ksb.get("total")
        payload["ksbProgress"] = ksb.get("percent") or 0
        payload["ksbProgressAvailable"] = True
        payload["ksbStatus"] = derive_ksb_status(ksb.get("completed"), ksb.get("total"))

    target = to_number(payload.get("otjhTarget"))
    actual = to_number(payload.get("otjhCompleted"))
    hours_available = target > 0
    hours_progress = percentage(actual, target) if hours_available else 0
    if metrics.get("migrated"):
        payload["overallProgress"] = programme.get("percent")
        payload["overallProgressAvailable"] = programme.get("status") == "ready"
    else:
        payload["overallProgress"] = hours_progress
        payload["overallProgressAvailable"] = hours_available
    progress_variance = clean_text(payload.get("progressVariance"))
    payload["otjhStatus"] = otjh_status_from_variance(
        actual - target if target > 0 else None
    )
    component_available = programme.get("status") == "ready"
    component_progress = int(round(to_number(programme.get("percent")))) if component_available else 0
    ksb_available = payload["ksbProgressAvailable"]
    ksb_progress = int(round(to_number(payload["ksbProgress"]))) if ksb_available else 0
    payload["status"] = determine_active_user_status(
        program_status=payload.get("rawProgramStatus") or payload.get("enrollmentStatus") or "",
        otjh_status=payload.get("otjhStatus") or "", progress_variance=progress_variance,
        hours_progress=hours_progress, hours_available=hours_available,
        ksb_progress=ksb_progress, ksb_available=ksb_available,
        component_progress=component_progress, component_available=component_available,
    )
    payload["riskFlags"] = build_active_user_risk_flags(
        otjh_status=payload.get("otjhStatus") or "", ksb_status=payload.get("ksbStatus") or "", progress_variance=progress_variance,
        hours_progress=hours_progress, hours_available=hours_available,
        ksb_progress=ksb_progress, ksb_available=ksb_available,
        component_progress=component_progress, component_available=component_available,
    )
    payload["metricsSource"] = "learner-dashboard"
    if metrics.get("migrated"):
        payload["ksbSource"] = "learner-dashboard"
        payload["otjhSource"] = "learner-dashboard"
    return apply_otjh_to_date_metrics(payload)


def apply_canonical_ksb_evidence(payload: dict, metrics: dict | None, aptem_id) -> dict:
    """Merge already-loaded Aptem evidence into the existing browser shape."""
    try:
        if aptem_id is None or int(aptem_id) <= 0 or not metrics:
            return payload
    except (TypeError, ValueError):
        return payload
    sources = metrics.get("_ksb_evidence_sources") or []
    if not sources:
        return payload
    details = payload.setdefault("ksbCompletedDetails", [])
    by_code = {str(item.get("code") or "").upper(): item for item in details if isinstance(item, dict)}
    seen = {(code, str(source.get("id") or "")) for code, item in by_code.items() for source in item.get("sources", [])}
    for source in sources:
        for raw_code in source.get("codes") or []:
            code = normalize_ksb_parent_code(raw_code)
            if not code:
                continue
            detail = by_code.get(code)
            if detail is None:
                detail = {"code": code, "type": ksb_type_label(code), "description": "", "sources": []}
                details.append(detail)
                by_code[code] = detail
            key = (code, str(source.get("id") or ""))
            if key in seen:
                continue
            detail.setdefault("sources", []).append({
                "id": source.get("id"),
                "title": source.get("title") or "Historical activity",
                "typeLabel": source.get("typeLabel") or "Historical activity",
                "source": source.get("source") or "Aptem",
                "activityId": source.get("activityId") or source.get("id"),
                "completedAt": source.get("completedAt"),
                "status": source.get("status"),
                "module": source.get("module"),
                "componentId": source.get("componentId"),
            })
            seen.add(key)
    payload["ksbCompletedDetailCount"] = len(details)
    return payload


def case_file_table_metrics_snapshot(row, payload: dict) -> dict:
    """Capture the exact Case File header metrics before list enrichments."""
    snapshot = {
        key: payload.get(key)
        for key in (
            "otjhTarget", "otjhPlanned", "ksbCompleted", "ksbTarget",
            "ksbProgress", "ksbProgressAvailable", "ksbStatus",
        )
    }
    source = getattr(row, "_caseload_source", None)
    source_plan = to_number(getattr(source, "planned_hours", None))
    if source_plan > 0:
        snapshot["otjhTarget"] = source_plan
        snapshot["otjhPlanned"] = source_plan
    return snapshot


def restore_case_file_table_metrics(payload: dict, case_file_snapshot: dict) -> dict:
    """Keep the caseload table aligned with the learner Case File header.

    Canonical enrichment supplies programme activity and actual hours, but its
    Restore the source learner's planned hours (the Case File fallback when the
    canonical plan is unavailable) and its evidence-coverage KSB measure.
    """
    target = to_number(case_file_snapshot.get("otjhTarget"))
    if target > 0:
        payload["otjhTarget"] = target
        payload["otjhPlanned"] = case_file_snapshot.get("otjhPlanned") or target
    if case_file_snapshot.get("ksbProgressAvailable"):
        for key in ("ksbCompleted", "ksbTarget", "ksbProgress", "ksbProgressAvailable", "ksbStatus"):
            payload[key] = case_file_snapshot.get(key)
    return payload


def apply_aptem_variance_status(payload: dict, aptem_id) -> dict:
    """Set Aptem OTJH variance and RAG status from actual minus target."""
    if aptem_id in (None, ""):
        return payload
    actual = to_number(payload.get("otjhCompleted"))
    target = to_number(payload.get("otjhTarget"))
    if target <= 0:
        return payload

    variance = round(actual - target, 2)
    payload["otjhVariance"] = variance
    shortfall = -variance
    if shortfall >= 40:
        payload["otjhStatus"] = "At Risk"
    elif shortfall >= 20:
        payload["otjhStatus"] = "Need Attention"
    else:
        payload["otjhStatus"] = "On Track"
    return payload


def apply_evidenced_ksb_count(payload: dict, evidenced: int | None) -> dict:
    """Attach the audit KSB count without corrupting curriculum progress.

    Sent as a count rather than a percentage, matching what the learner's own
    workspace shows: the audit KSB mapping spans several standards and carries
    no per-learner denominator.  It therefore must not replace ``ksbCompleted``:
    that field and ``ksbTarget`` are parent-code curriculum figures, while the
    audit count contains distinct detailed codes.  Mixing them produced rows
    such as ``28 / 16`` with a separately capped 100% progress value.
    """
    if evidenced is None:
        return payload
    payload["ksbEvidencedCount"] = evidenced
    payload["ksbSource"] = "audit"
    return payload


def apply_audit_hour_totals(payload: dict, totals: dict | None) -> dict:
    """Overlay the Audit OTJ figures on one serialized caseload learner.

    Mirrors the learner workspace's own precedence (workspace/learner/page.tsx):
    the Audit pair replaces completed/planned when present, and the
    cumulative-to-date `otjhTarget` is rescaled by the same ratio so a card
    showing "x% - completed / target" stays internally consistent. A missing
    half of the pair leaves that half untouched.
    """
    if not totals:
        return payload
    planned = totals.get("audit_tp_planned")
    actual = totals.get("audit_lms_actual")
    if actual is not None:
        payload["otjhCompleted"] = actual
    if planned is not None:
        previous_planned = to_number(payload.get("otjhPlanned"))
        previous_target = to_number(payload.get("otjhTarget"))
        # otjhTarget paces the programme plan to the current week, so carry that
        # pacing over to the audit plan rather than dropping back to the whole
        # programme total, which would read as "behind" for everyone.
        #
        # The serializers floor otjhTarget at 1 (`max(..., 1)`), so a stored 1
        # against a larger plan is that placeholder rather than a real to-date
        # figure -- rescaling it would invent a target. Those learners fall back
        # to the whole audit plan, the same denominator their own workspace uses.
        ratio = previous_target / previous_planned if previous_planned > 0 else 0
        paced = previous_target > 1 and 0 < ratio <= 1
        payload["otjhTarget"] = max(round(planned * ratio, 2), 1) if paced else planned
        payload["otjhPlanned"] = planned
    # `overallProgress` is the OTJH-against-target percentage the cards print
    # beside the hours ratio, so it has to be recomputed off the overlaid pair
    # or the two would disagree on the same card.
    payload["overallProgress"] = percentage(payload["otjhCompleted"], payload["otjhTarget"])
    payload["overallProgressAvailable"] = True
    payload["otjhSource"] = "audit"
    return apply_otjh_to_date_metrics(payload)


def apply_attendance_summary(payload: dict, metrics: dict | None) -> dict:
    """Overlay the one canonical attendance summary on a serialized learner.

    ``metrics`` comes from ``dashboard_attendance_rows``, joined by the same
    stable LearnerProfile id every other dashboard enrichment uses. This is
    the only place attendance is written onto the dashboard learner payload,
    so the response never carries two attendance figures for the frontend to
    reconcile. A missing/failed lookup leaves the unavailable placeholder
    from ``serialize_caseload_dashboard_learner`` rather than a fake 0.
    """
    if not metrics:
        return payload
    has_attendance = bool(metrics.get("hasAttendance")) and metrics.get("attendance") is not None
    if not has_attendance:
        return payload
    payload["attendanceRate"] = to_number(metrics.get("attendance"))
    payload["attendanceRateAvailable"] = True
    payload["attendancePresent"] = metrics.get("present")
    payload["attendanceSessions"] = metrics.get("sessions")
    payload["attendanceAbsent"] = metrics.get("absent")
    payload["attendanceLastSession"] = metrics.get("lastSession")
    payload["attendanceLastSessionDate"] = metrics.get("lastSessionDate")
    return payload


def serialize_caseload_learner(
    row: LearnerProfile | SimpleNamespace,
    *,
    refresh_live_snapshots: bool = True,
    expected_otjh_by_component_id: dict[str, float] | None = None,
    curriculum_ksbs: list[dict] | None = None,
) -> dict:
    live_snapshot = {}
    source_row = getattr(row, "_caseload_source", None)
    if refresh_live_snapshots:
        refresh_caseload_learner_ksb_snapshot(row)
        if callable(getattr(row, "save", None)) and hasattr(row, "training_plan_progress"):
            try:
                # Keep coach-facing caseload cards aligned with the live learner
                # detail OTJ calculation instead of stale stored snapshot values.
                if source_row is None and getattr(row, "enrolment_id", None):
                    source_row = EnrolmentUser.all_learners.filter(pk=row.enrolment_id).first()
                live_snapshot = refresh_learner_otjh_snapshot(row, source=source_row)
            except Exception as exc:
                logger.warning(
                    "Could not refresh live OTJ snapshot for learner %s: %s",
                    getattr(row, "id", None),
                    exc,
                )

    progress_entries = [entry for entry in list_or_empty(row.training_plan_progress) if isinstance(entry, dict)]
    activity_entries = learner_activity_feed_entries(row)
    latest_activity = latest_learning_activity(progress_entries, activity_entries)
    otjh_completed_entries = build_otjh_completed_entries(
        progress_entries,
        activity_entries,
        row.training_plan,
        expected_by_id=expected_otjh_by_component_id,
    )
    planned_components = int(live_snapshot.get("componentsPlanned") or count_planned_components(row.training_plan))
    completed_components = count_completed_components(progress_entries)
    component_available = planned_components > 0
    component_progress = percentage(completed_components, planned_components) if component_available else 0

    schedule_planned, schedule_start, schedule_end = caseload_schedule_values(row)
    profile_start = caseload_profile_start_date(row)
    target_hours_value = (
        clean_text(row.target_hours)
        or clean_text(row.minimum_hours)
        or clean_text(schedule_planned)
    )
    hours_available = bool(clean_text(row.completed_hours) or target_hours_value)
    hours_progress = percentage(row.completed_hours, target_hours_value) if target_hours_value else 0

    if curriculum_ksbs is None:
        curriculum_ksbs = current_curriculum_ksb_items_for_learner(
            row,
            source=source_row,
            training_plan=getattr(row, "training_plan", None),
        )
    target_ksbs = curriculum_ksbs or row.ksbs
    target_ksb_lookup = ksb_target_lookup(target_ksbs)
    target_ksb_codes = set(target_ksb_lookup)
    completed_codes = completed_ksb_codes(progress_entries, activity_entries)
    ksb_completed_details = build_ksb_completed_details(
        target_ksbs,
        completed_codes,
        progress_entries,
        activity_entries,
        row.training_plan,
    )
    ksb_available = bool(target_ksb_codes)
    completed_target_codes = completed_codes & target_ksb_codes
    ksb_completed = len(completed_target_codes) if ksb_available else None
    ksb_target = len(target_ksb_codes) if ksb_available else None
    ksb_progress = percentage(ksb_completed, ksb_target) if ksb_available else 0
    ksb_status = derive_ksb_status(ksb_completed, ksb_target)
    ksb_breakdown = summarize_ksb_breakdown(target_ksb_codes, completed_codes)

    progress_variance = clean_text(row.progress_variance)
    otjh_status = clean_text(row.otjh_status)
    program_status = get_lms_row_program_status(row)
    performance_status = determine_active_user_status(
        program_status=program_status,
        otjh_status=otjh_status,
        progress_variance=progress_variance,
        hours_progress=hours_progress,
        hours_available=hours_available,
        ksb_progress=ksb_progress,
        ksb_available=ksb_available,
        component_progress=component_progress,
        component_available=component_available,
    )
    risk_flags = build_active_user_risk_flags(
        otjh_status=otjh_status,
        ksb_status=ksb_status,
        progress_variance=progress_variance,
        hours_progress=hours_progress,
        hours_available=hours_available,
        ksb_progress=ksb_progress,
        ksb_available=ksb_available,
        component_progress=component_progress,
        component_available=component_available,
    )

    cohort_name = clean_text(row.cohort) or "--"
    group_name = clean_text(row.group) or "--"
    programme_name = clean_text(
        getattr(source_row, "programme", None) or getattr(row, "programme", None),
    ) or "--"
    cohort_id = re.sub(r"[^a-z0-9]+", "-", cohort_name.lower()).strip("-") or "unassigned"
    # Same 'commercial'/'apprenticeship' discriminator learner_detail.py keys its
    # lookup on (see learner_calendar_source_identity above) — surfaced here so
    # the coach case file can request the learner's real detail record directly
    # instead of racing both kinds and silently rendering whichever answers first.
    learner_type = "commercial" if clean_text(getattr(row, "learner_type", "")).casefold() == "commercial" else "apprenticeship"
    # row.id is this LearnerProfile's own pk -- a different, disjoint sequence
    # from enrolment."Created_users".id (see LearnerProfile.enrolment_id's
    # docstring). /learner_api/learner-detail/<kind>/<pk>/ queries the latter
    # table directly, so callers that only had row.id would 404 against it.
    enrolment_id = str(row.enrolment_id) if getattr(row, "enrolment_id", None) else None
    current_week = current_week_label(row)
    components_target = components_target_to_date(row)

    return apply_latest_learning_activity({
        "id": str(row.id),
        "name": clean_text(row.username) or "Unknown learner",
        "initials": build_initials(row.username),
        "learnerType": learner_type,
        "enrolmentId": enrolment_id,
        "currentModule": current_week["module"] if current_week else None,
        "currentWeek": current_week["week"] if current_week else None,
        "componentsTargetToDate": components_target,
        "employer": "--",
        "programme": programme_name,
        "cohortId": cohort_id,
        "cohortName": cohort_name,
        "group": group_name,
        "status": performance_status,
        "enrollmentStatus": normalize_program_status(program_status),
        "riskFlags": risk_flags,
        "overallProgress": hours_progress,
        "overallProgressAvailable": hours_available,
        "attendanceRate": component_progress,
        "attendanceRateAvailable": component_available,
        "componentsCompleted": completed_components,
        "componentsPlanned": planned_components,
        "otjhCompleted": to_number(row.completed_hours),
        "otjhTarget": max(to_number(target_hours_value) if target_hours_value else 1, 1),
        "otjhMinimum": to_number(row.minimum_hours),
        "otjhPlanned": to_number(schedule_planned),
        "otjhCompletedEntries": otjh_completed_entries,
        "otjhCompletedEntryCount": len(otjh_completed_entries),
        "otjhProgressHours": clean_text(row.progress_hours) or "--",
        "otjhStatus": otjh_status,
        "ksbCompleted": ksb_completed,
        "ksbTarget": ksb_target,
        "ksbStatus": ksb_status,
        "ksbProgress": ksb_progress,
        "ksbProgressAvailable": ksb_available,
        "ksbCompletedDetails": ksb_completed_details,
        "ksbCompletedDetailCount": len(ksb_completed_details),
        "knowledgeCompleted": ksb_breakdown["knowledge"]["completed"],
        "knowledgeTarget": ksb_breakdown["knowledge"]["target"],
        "knowledgeProgress": ksb_breakdown["knowledge"]["progress"],
        "skillsCompleted": ksb_breakdown["skills"]["completed"],
        "skillsTarget": ksb_breakdown["skills"]["target"],
        "skillsProgress": ksb_breakdown["skills"]["progress"],
        "behavioursCompleted": ksb_breakdown["behaviours"]["completed"],
        "behavioursTarget": ksb_breakdown["behaviours"]["target"],
        "behavioursProgress": ksb_breakdown["behaviours"]["progress"],
        "evidenceCount": derive_evidence_count(progress_entries, activity_entries),
        "evidenceCompletedCount": derive_completed_evidence_count(progress_entries, activity_entries),
        "evidenceCountAvailable": bool(progress_entries or activity_entries),
        "nextCoaching": "--",
        "nextReview": "--",
        "lastContact": "--",
        "lastPr": None,
        "lastMcm": None,
        "lastAttendanceDate": "--",
        "lastProgressReview": "--",
        "lastReview": "--",
        "lastCoachingSession": "--",
        "lastSubmittedEvidence": "--",
        "recentFlag": risk_flags[0] if risk_flags else None,
        "email": clean_text(row.email) or None,
        "employerEmail": None,
        "employerPhone": None,
        "progressVariance": progress_variance or "--",
        "startDate": profile_start,
        "displayStartDate": format_date(profile_start),
        "displayEndDate": format_date(getattr(getattr(row, "_caseload_source", None), "learner_end_date", None)),
        "otjhProgrammeStartDate": format_date(schedule_start),
        "gatewayReviewDate": format_date(getattr(row, "gateway_review_date", None)),
        "plannedEndDate": format_date(schedule_end),
        "coachName": clean_text(row.coach_name) or None,
        "coachEmail": clean_text(row.coach_email) or None,
        "rawProgramStatus": program_status or "--",
        "coachRag": format_coach_rag_value(getattr(row, "coach_rag", None)),
    }, latest_activity)


def serialize_caseload_dashboard_learner(row: LearnerProfile | SimpleNamespace) -> dict:
    """Serialize only the fields the coach dashboard needs immediately."""
    schedule_planned, schedule_start, schedule_end = caseload_schedule_values(row)
    profile_start = caseload_profile_start_date(row)
    target_hours_value = (
        clean_text(getattr(row, "target_hours", None))
        or clean_text(getattr(row, "minimum_hours", None))
        or clean_text(schedule_planned)
    )
    hours_available = bool(clean_text(getattr(row, "completed_hours", None)) or target_hours_value)
    hours_progress = percentage(getattr(row, "completed_hours", None), target_hours_value) if target_hours_value else 0
    otjh_status = clean_text(getattr(row, "otjh_status", None))
    progress_variance = clean_text(getattr(row, "progress_variance", None))
    program_status = get_lms_row_program_status(row)
    performance_status = determine_active_user_status(
        program_status=program_status,
        otjh_status=otjh_status,
        progress_variance=progress_variance,
        hours_progress=hours_progress,
        hours_available=hours_available,
        ksb_progress=0,
        ksb_available=False,
        component_progress=0,
        component_available=False,
    )
    risk_flags = build_active_user_risk_flags(
        otjh_status=otjh_status,
        ksb_status="",
        progress_variance=progress_variance,
        hours_progress=hours_progress,
        hours_available=hours_available,
        ksb_progress=0,
        ksb_available=False,
        component_progress=0,
        component_available=False,
    )
    cohort_name = clean_text(getattr(row, "cohort", None)) or "--"
    group_name = clean_text(getattr(row, "group_name", None)) or "--"
    programme_name = clean_text(getattr(row, "programme", None)) or cohort_name
    cohort_id = re.sub(r"[^a-z0-9]+", "-", cohort_name.lower()).strip("-") or "unassigned"
    learner_type = "commercial" if clean_text(getattr(row, "learner_type", "")).casefold() == "commercial" else "apprenticeship"
    enrolment_id = str(row.enrolment_id) if getattr(row, "enrolment_id", None) else None
    current_week = current_week_label(row)
    components_target = components_target_to_date(row)

    return {
        "id": str(row.id),
        "name": clean_text(row.username) or "Unknown learner",
        "initials": build_initials(row.username),
        "learnerType": learner_type,
        "enrolmentId": enrolment_id,
        "currentModule": current_week["module"] if current_week else None,
        "currentWeek": current_week["week"] if current_week else None,
        "componentsTargetToDate": components_target,
        "employer": "--",
        "cohortId": cohort_id,
        "cohortName": cohort_name,
        "programme": programme_name,
        "group": group_name,
        "status": performance_status,
        "enrollmentStatus": normalize_program_status(program_status),
        "riskFlags": risk_flags,
        "overallProgress": hours_progress,
        "overallProgressAvailable": hours_available,
        # Placeholder until apply_attendance_summary overlays the one
        # canonical attendance figure below. Never a fake 0: a learner whose
        # attendance enrichment failed or has no register must read as
        # unavailable, not as "0% attendance".
        "attendanceRate": None,
        "attendanceRateAvailable": False,
        "attendancePresent": None,
        "attendanceSessions": None,
        "attendanceAbsent": None,
        "attendanceLastSession": None,
        "attendanceLastSessionDate": None,
        "otjhCompleted": to_number(getattr(row, "completed_hours", None)),
        "otjhTarget": max(to_number(target_hours_value) if target_hours_value else 1, 1),
        "otjhMinimum": to_number(getattr(row, "minimum_hours", None)),
        "otjhPlanned": to_number(schedule_planned),
        "otjhProgressHours": clean_text(getattr(row, "progress_hours", None)) or "--",
        "otjhStatus": otjh_status,
        "ksbCompleted": None,
        "ksbTarget": None,
        "ksbStatus": "",
        "ksbProgress": 0,
        "ksbProgressAvailable": False,
        "evidenceCount": 0,
        "evidenceCompletedCount": 0,
        "evidenceCountAvailable": False,
        "nextCoaching": "--",
        "nextReview": "--",
        "lastContact": "--",
        "lastPr": None,
        "lastMcm": None,
        "recentFlag": risk_flags[0] if risk_flags else None,
        "email": clean_text(getattr(row, "email", None)) or None,
        "progressVariance": progress_variance or "--",
        "startDate": profile_start,
        "displayStartDate": format_date(profile_start),
        "displayEndDate": format_date(getattr(getattr(row, "_caseload_source", None), "learner_end_date", None)),
        "otjhProgrammeStartDate": format_date(schedule_start),
        "gatewayReviewDate": format_date(getattr(row, "gateway_review_date", None)),
        "plannedEndDate": format_date(schedule_end),
        "rawProgramStatus": program_status or "--",
    }


def request_prefers_live_caseload_snapshots(request) -> bool:
    """Opt into expensive live KSB/OTJ recalculation when explicitly requested.

    The dashboard and coach list views only need the persisted caseload
    snapshot, so they stay fast by default. Drill-down callers can pass
    `?live=1` when they genuinely need a fresh recomputation.
    """
    return clean_text(request.GET.get("live")).casefold() in {"1", "true", "yes", "on"}


def serialize_attendance_source_learner(row: LearnerProfile) -> dict:
    target_hours_value = (
        clean_text(row.target_hours)
        or clean_text(row.minimum_hours)
        or clean_text(row.planned_hours)
    )
    hours_available = bool(clean_text(row.completed_hours) or target_hours_value)
    hours_progress = percentage(row.completed_hours, target_hours_value) if target_hours_value else 0
    programme_name = clean_text(getattr(row, "programme", None)) or "--"
    cohort_name = clean_text(getattr(row, "cohort", None)) or "--"
    group_name = clean_text(getattr(row, "group_name", None)) or "--"
    program_status = get_lms_row_program_status(row)
    learner_type = "commercial" if clean_text(getattr(row, "learner_type", "")).casefold() == "commercial" else "apprenticeship"
    enrolment_id = str(row.enrolment_id) if getattr(row, "enrolment_id", None) else None
    current_week = current_week_label(row)
    components_target = components_target_to_date(row)

    return {
        "id": str(row.id),
        "name": clean_text(row.username) or "Unknown learner",
        "initials": build_initials(row.username),
        "learnerType": learner_type,
        "enrolmentId": enrolment_id,
        "currentModule": current_week["module"] if current_week else None,
        "currentWeek": current_week["week"] if current_week else None,
        "componentsTargetToDate": components_target,
        "email": clean_text(row.email) or None,
        "employer": "--",
        "programmeName": programme_name,
        "programmeId": clean_text(getattr(row, "programme_id", None)) or None,
        "cohortName": cohort_name,
        "group": group_name,
        "groupName": group_name,
        "groupId": clean_text(getattr(row, "group_id", None)) or None,
        "enrollmentStatus": normalize_program_status(program_status),
        "overallProgress": hours_progress,
        "overallProgressAvailable": hours_available,
        "otjhCompleted": to_number(row.completed_hours),
        "otjhTarget": max(to_number(target_hours_value) if target_hours_value else 1, 1),
        "otjhPlanned": to_number(row.planned_hours),
        "ksbProgress": 0,
        "ksbProgressAvailable": False,
        "coachName": clean_text(row.coach_name) or None,
        "coachEmail": clean_text(row.coach_email) or None,
        "rawProgramStatus": program_status or "--",
    }


def parse_month_bounds(value: str | None) -> tuple[date, date, str, str]:
    return validate_month(value)


def date_only(value) -> date | None:
    parsed = parse_date_value(value)
    if isinstance(parsed, datetime):
        return parsed.date()
    return parsed if isinstance(parsed, date) else None


def entry_activity_date(entry: dict) -> date | None:
    # ``declaredCompletedAt`` leads: when a learner's Finish click fell outside
    # the working rules, the working instant they declared is the one the month
    # buckets, the hours count and the reports read. The click itself stays on
    # ``submittedAt`` as audit evidence and is never counted in its place.
    for field in ("declaredCompletedAt", "submittedAt", "at", "completedAt", "startedAt", "date", "createdAt"):
        parsed = date_only(entry.get(field))
        if parsed:
            return parsed
    return None


def entry_is_between(entry: dict, start_date: date, end_date: date) -> bool:
    entry_date = entry_activity_date(entry)
    return bool(entry_date and start_date <= entry_date <= end_date)


def reported_minutes(value) -> float:
    text = clean_text(value)
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            minutes = float(parts[0])
            seconds = float(parts[1]) if len(parts) > 1 else 0.0
            return max(0.0, minutes + seconds / 60.0)
        except (ValueError, IndexError):
            return 0.0

    lower_text = text.lower()
    hour_matches = [float(amount) for amount in re.findall(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", lower_text)]
    minute_matches = [float(amount) for amount in re.findall(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b", lower_text)]
    if hour_matches or minute_matches:
        return sum(hour_matches) * 60 + sum(minute_matches)

    match = re.search(r"\d+(?:\.\d+)?", lower_text)
    if not match:
        return 0.0
    value = float(match.group(0))
    return value if value > 24 else value * 60


def format_hours_number(hours: float) -> str:
    rounded = round(hours, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def monthly_activity_identity(entry: dict, index: int) -> str:
    entry_date = entry_activity_date(entry)
    date_key = entry_date.isoformat() if entry_date else ""
    key_parts = [
        clean_text(entry.get("kind")),
        clean_text(entry.get("quizId")),
        clean_text(entry.get("componentId")),
        clean_text(entry.get("attempt")),
        date_key,
        clean_text(entry.get("title") or entry.get("action") or entry.get("quizName")),
    ]
    key = "|".join(part for part in key_parts if part)
    return key or f"activity:{index}"


def monthly_activity_dedupe_identity(entry: dict, index: int) -> str:
    entry_date = entry_activity_date(entry)
    date_key = entry_date.isoformat() if entry_date else ""
    key_parts = [
        clean_text(entry.get("kind")),
        clean_text(entry.get("quizId")),
        clean_text(entry.get("componentId")),
        date_key,
    ]
    key = "|".join(part for part in key_parts if part)
    if key:
        return key
    return monthly_activity_identity(entry, index)


def monthly_learning_type(entry: dict) -> str:
    kind = clean_text(entry.get("kind")).lower()
    component_type = clean_text(entry.get("componentType") or entry.get("type")).lower().replace("-", "_")
    if kind == "quiz":
        return "Quiz"
    if kind == "video":
        return "Video"
    if component_type == "assignment":
        return "Assignment"
    if component_type == "podcast":
        return "Audio"
    if component_type == "reading":
        return "Reading"
    if component_type:
        return component_type.replace("_", " ").title()
    if kind == "component":
        return "Component"
    return clean_text(entry.get("action")) or "Activity"


def monthly_learning_title(entry: dict) -> str:
    return (
        clean_text(entry.get("title"))
        or clean_text(entry.get("quizName"))
        or clean_text(entry.get("componentTitle"))
        or monthly_learning_type(entry)
    )


def ksb_evidence_title(entry: dict, activity: dict | None, component_meta: dict) -> str:
    """Prefer an authored activity name over an imported media-type label."""
    candidates = [
        clean_text(entry.get("title")),
        clean_text(entry.get("quizName")),
        clean_text(entry.get("componentTitle")),
        clean_text((activity or {}).get("title")),
        clean_text(component_meta.get("title")),
    ]
    generic_labels = {"video", "audio", "quiz", "assignment", "activity", "component", "reading", "podcast", "live session"}
    return next((title for title in candidates if title and title.casefold() not in generic_labels), None) or next(
        (title for title in candidates if title), ""
    )


def monthly_learning_detail(entry: dict) -> str:
    detail = clean_text(entry.get("detail"))
    if detail:
        return detail

    reported_time = clean_text(entry.get("reportedTime"))
    grade = entry.get("grade")
    achieved = entry.get("achievedScore")
    total = entry.get("totalScore")
    if achieved not in (None, "") and total not in (None, ""):
        return f"Score {achieved}/{total}"
    if grade not in (None, ""):
        return f"Grade {round(to_number(grade) * 100)}%"
    if reported_time:
        minutes = reported_minutes(reported_time)
        return f"{format_hours_number(minutes / 60)}h" if minutes > 0 else reported_time
    return clean_text(entry.get("module") or entry.get("week")) or "--"


def curriculum_monthly_target_hours_weeks(training_plan) -> list[list[str]]:
    """Training plan components grouped by week, in plan order.

    A learner's plan is a flat sequence of weeks (module order, then week order
    within it) with no date of its own. Split out from
    `curriculum_monthly_target_hours` so callers can collect component ids
    across many learners and fetch `expected_otjh` in one batched query instead
    of one per learner.
    """
    weeks: list[list[str]] = []
    for module in list_or_empty(training_plan):
        if not isinstance(module, dict):
            continue
        for week in list_or_empty(module.get("weeks")):
            if not isinstance(week, dict):
                continue
            component_ids = [
                training_plan_component_id(component)
                for component in list_or_empty(week.get("components"))
                if isinstance(component, dict) and training_plan_component_id(component)
            ]
            weeks.append(component_ids)
    return weeks


def training_plan_has_components(training_plan) -> bool:
    return any(component_ids for component_ids in curriculum_monthly_target_hours_weeks(training_plan))


def training_plan_component_count(training_plan) -> int:
    return sum(len(component_ids) for component_ids in curriculum_monthly_target_hours_weeks(training_plan))


def monthly_target_training_plan(row: LearnerProfile | SimpleNamespace):
    plan = getattr(row, "training_plan", None)
    source = getattr(row, "_caseload_source", None)
    if source is None:
        return plan

    try:
        # Monthly reporting is read-only: resolve the latest curriculum tree in
        # memory and never mutate the learner's stored enrolment snapshot.
        source_plan = getattr(source, "training_plan", None) or getattr(source, "learning_plan", None)
        hydrated = hydrate_training_plan(source_plan)
    except Exception as exc:
        logger.warning(
            "Could not hydrate monthly target training plan for learner %s: %s",
            getattr(row, "id", None),
            exc,
        )
        return plan

    # Active_users can carry an old, partial snapshot. Do not accept it merely
    # because it contains one component: prefer the live curriculum hydration
    # whenever it contains more of the learner's authored plan.
    if training_plan_component_count(hydrated) > training_plan_component_count(plan):
        try:
            setattr(row, "training_plan", hydrated)
        except Exception:
            pass
        return hydrated
    return plan


def training_plan_component_id(component: dict) -> str:
    return clean_text(
        component.get("componentId")
        or component.get("component_id")
        or component.get("id")
    )


def training_plan_expected_otjh_lookup(training_plan) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for module in list_or_empty(training_plan):
        if not isinstance(module, dict):
            continue
        for week in list_or_empty(module.get("weeks")):
            if not isinstance(week, dict):
                continue
            for component in list_or_empty(week.get("components")):
                if not isinstance(component, dict):
                    continue
                component_id = training_plan_component_id(component)
                expected = component.get("expectedOtjh")
                if expected in (None, ""):
                    expected = component.get("expected_otjh")
                if not component_id or expected in (None, ""):
                    continue
                try:
                    lookup[component_id] = float(expected)
                except (TypeError, ValueError):
                    continue
    return lookup


def curriculum_monthly_target_hours(
    training_plan,
    learner_start_date: date | None,
    start_date: date,
    end_date: date,
    expected_by_id: dict[str, float],
) -> float:
    """The learner's own planned OTJH for one month, read off their training plan.

    A week's components count toward the month whose [start_date, end_date]
    window contains that week's start. This is an estimate, not the authored
    calendar: weeks are assumed contiguous with no gaps for holidays or breaks.

    `expected_by_id` is a pre-fetched, batched lookup (see
    `curriculum_monthly_target_hours_weeks` and `curriculum_expected_otjh_by_component_id`)
    — this function does no DB work of its own.
    """
    raw_weeks = [
        week
        for module in list_or_empty(training_plan)
        if isinstance(module, dict)
        for week in list_or_empty(module.get("weeks"))
        if isinstance(week, dict)
    ]
    if not raw_weeks:
        return 0.0

    plan_expected_by_id = training_plan_expected_otjh_lookup(training_plan)
    total_hours = 0.0
    for index, week in enumerate(raw_weeks):
        explicit_start = parse_date_value(
            week.get("sessionDate") or week.get("session_date")
            or week.get("startDate") or week.get("start_date")
            or week.get("weekStart") or week.get("week_start")
        )
        if isinstance(explicit_start, datetime):
            explicit_start = explicit_start.date()
        # Prefer authored curriculum week dates. Older plans have no dates, so
        # retain the contiguous-week calculation from the learner start date;
        # when that is also absent, anchor week 1 to the requested month.
        week_start = explicit_start or (
            (learner_start_date or start_date) + timedelta(weeks=index)
        )
        if not (start_date <= week_start <= end_date):
            continue
        component_ids = [
            training_plan_component_id(component)
            for component in list_or_empty(week.get("components"))
            if isinstance(component, dict) and training_plan_component_id(component)
        ]
        total_hours += sum(
            expected_by_id.get(component_id, plan_expected_by_id.get(component_id, 0.0))
            for component_id in component_ids
        )
    return round(total_hours, 1)


def monthly_target_start_date(row: LearnerProfile | SimpleNamespace) -> date | None:
    source = getattr(row, "_caseload_source", None)
    start_value = getattr(source, "start_date", None) if source is not None else None
    start_value = start_value or getattr(row, "start_date", None)
    start_date = parse_date_value(start_value)
    if isinstance(start_date, datetime):
        return start_date.date()
    return start_date


def recent_month_end_dates(today: date, count: int = 6) -> list[date]:
    """Month-end cut-offs ending with ``today`` for the open month."""
    first_of_this_month = today.replace(day=1)
    cutoffs: list[date] = []
    for months_back in range(count - 1, -1, -1):
        absolute_month = first_of_this_month.year * 12 + first_of_this_month.month - 1 - months_back
        year, zero_based_month = divmod(absolute_month, 12)
        month = zero_based_month + 1
        cutoffs.append(
            today
            if months_back == 0
            else date(year, month, monthrange(year, month)[1])
        )
    return cutoffs


def progress_entry_history_record(entry: LearnerProgressEntry) -> dict:
    submitted_at = (
        timezone.localtime(entry.submitted_at)
        if entry.submitted_at and timezone.is_aware(entry.submitted_at)
        else entry.submitted_at
    )
    started_at = (
        timezone.localtime(entry.started_at)
        if entry.started_at and timezone.is_aware(entry.started_at)
        else entry.started_at
    )
    return {
        "kind": entry.kind,
        "componentId": entry.component_ref,
        "quizId": entry.quiz_ref,
        "attempt": entry.attempt,
        "moduleTitle": entry.module_title,
        "weekTitle": entry.week_title,
        "componentTitle": entry.component_title,
        "expectedOtjh": entry.expected_otjh,
        "reportedTime": entry.reported_time,
        "submittedAt": submitted_at.isoformat() if submitted_at else "",
        "declaredCompletedAt": (
            entry.declared_completed_at.isoformat() if entry.declared_completed_at else ""
        ),
        "startedAt": started_at.isoformat() if started_at else "",
        "claimedSeconds": entry.claimed_seconds,
        "verifiedSeconds": entry.verified_seconds,
        "timeTrackingSource": entry.time_tracking_source,
    }


def caseload_progress_history(rows) -> dict[int, list[dict]]:
    """Load only the progress columns needed by the six-month risk chart."""
    learner_ids = [int(row.id) for row in rows or [] if getattr(row, "id", None) is not None]
    result: dict[int, list[dict]] = defaultdict(list)
    if not learner_ids:
        return result
    entries = (
        LearnerProgressEntry.objects
        .filter(learner_id__in=learner_ids)
        .exclude(kind="activity_event")
        # Must list every field progress_entry_history_record reads: a field
        # left out is deferred and costs one extra query per progress entry.
        .only(
            "learner_id", "kind", "component_ref", "quiz_ref", "attempt",
            "module_title", "week_title", "component_title", "expected_otjh",
            "reported_time", "submitted_at", "declared_completed_at", "started_at",
            "claimed_seconds", "verified_seconds", "time_tracking_source",
        )
        .order_by("learner_id", "entry_order", "id")
    )
    for entry in entries:
        result[int(entry.learner_id)].append(progress_entry_history_record(entry))
    return result


def historical_progress_date(record: dict) -> date | None:
    # Same rule as entry_activity_date: reporting follows the declared working
    # instant, audit keeps the real click.
    value = record.get("declaredCompletedAt") or record.get("submittedAt") or record.get("startedAt")
    parsed = parse_date_value(value)
    return parsed.date() if isinstance(parsed, datetime) else parsed


def build_monthly_risk_history(
    rows: list[LearnerProfile | SimpleNamespace],
    progress_by_learner: dict[int, list[dict]],
    expected_by_id: dict[str, float],
    *,
    today: date,
    hydrated_plans_by_learner: dict[int, object] | None = None,
) -> list[dict]:
    """Count active caseload learners whose OTJH variance was at risk.

    Closed months are reconstructed from timestamped OTJH activity and the
    cumulative target in the learner's current training plan. The open month
    deliberately uses the persisted current status, so its bar always agrees
    with the dashboard's current OTJH counter.
    """
    active_rows = [
        row for row in rows
        if normalize_program_status(get_lms_row_program_status(row)) == "active"
    ]
    hydrated_plans_by_learner = hydrated_plans_by_learner or {}
    learner_context = {
        int(row.id): (
            monthly_target_start_date(row),
            hydrated_plans_by_learner[int(row.id)]
            if int(row.id) in hydrated_plans_by_learner
            else monthly_target_training_plan(row),
        )
        for row in active_rows
    }
    points = []
    cutoffs = recent_month_end_dates(today)
    for cutoff in cutoffs:
        is_current_month = cutoff.year == today.year and cutoff.month == today.month
        at_risk = 0
        insufficient_history = False
        for row in active_rows:
            if is_current_month:
                normalized_status = re.sub(
                    r"[\s_-]+", "", clean_text(getattr(row, "otjh_status", None)).casefold()
                )
                at_risk += normalized_status == "atrisk"
                continue

            learner_start, training_plan = learner_context[int(row.id)]
            if learner_start is None or learner_start > cutoff:
                if learner_start is None:
                    insufficient_history = True
                continue
            target_hours = curriculum_monthly_target_hours(
                training_plan,
                learner_start,
                learner_start,
                cutoff,
                expected_by_id,
            )
            if target_hours <= 0:
                insufficient_history = True
                continue
            historical_progress = [
                record
                for record in progress_by_learner.get(int(row.id), [])
                if (historical_progress_date(record) or date.max) <= cutoff
            ]
            completed_hours = completed_hours_value_from_progress(historical_progress)
            shortfall = max(target_hours - completed_hours, 0)
            if shortfall > 40:
                at_risk += 1

        points.append({
            "month": cutoff.strftime("%Y-%m"),
            "label": cutoff.strftime("%b"),
            "count": at_risk,
            "available": not insufficient_history,
        })
    return points


def dashboard_monthly_risk_history(
    rows: list[LearnerProfile | SimpleNamespace],
    *,
    today: date,
) -> list[dict] | None:
    """Load the inputs for the six-month chart without writing snapshots."""
    active_rows = [
        row for row in rows
        if normalize_program_status(get_lms_row_program_status(row)) == "active"
    ]
    if not active_rows:
        return build_monthly_risk_history([], {}, {}, today=today)

    try:
        plans_by_learner = {
            int(row.id): monthly_target_training_plan(row)
            for row in active_rows
        }
        component_ids = [
            component_id
            for plan in plans_by_learner.values()
            for week in curriculum_monthly_target_hours_weeks(plan)
            for component_id in week
        ]
        expected_by_id = curriculum_expected_otjh_by_component_id(component_ids)
        progress_by_learner = caseload_progress_history(active_rows)
        return build_monthly_risk_history(
            active_rows,
            progress_by_learner,
            expected_by_id,
            today=today,
            hydrated_plans_by_learner=plans_by_learner,
        )
    except DatabaseError:
        logger.exception("coach_dashboard_monthly_risk_history_failed")
        return None


def training_plan_component_lookup(training_plan) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for module in list_or_empty(training_plan):
        if not isinstance(module, dict):
            continue
        module_title = clean_text(module.get("moduleTitle") or module.get("module"))
        for week in list_or_empty(module.get("weeks")):
            if not isinstance(week, dict):
                continue
            week_title = clean_text(week.get("weekTitle") or week.get("week"))
            for component in list_or_empty(week.get("components")):
                if not isinstance(component, dict):
                    continue
                component_id = training_plan_component_id(component)
                if not component_id:
                    continue
                lookup[component_id] = {
                    "module": module_title,
                    "week": week_title,
                    "title": clean_text(component.get("componentTitle") or component.get("title")),
                    "expectedOtjh": component.get("expectedOtjh") or component.get("expected_otjh"),
                }
    return lookup


def curriculum_expected_otjh_by_component_id(component_ids: list[str]) -> dict[str, float]:
    ids = sorted({clean_text(component_id) for component_id in component_ids if clean_text(component_id)})
    if not ids:
        return {}
    for alias in ("default", "enrolment"):
        try:
            with connections[alias].cursor() as cur:
                cur.execute(
                    "SELECT id, expected_otjh FROM curriculum.components WHERE id = ANY(%s)",
                    [ids],
                )
                values = {
                    component_id: float(expected)
                    for component_id, expected in cur.fetchall()
                    if expected is not None
                }
                if values:
                    return values
        except DatabaseError as exc:
            logger.warning("Could not look up component expected_otjh on %s: %s", alias, exc)
    return {}


def component_expected_otjh_hours(component_id: str, component_meta: dict, expected_by_id: dict[str, float], progress_entry: dict | None = None) -> float | None:
    if isinstance(progress_entry, dict):
        snapshot_expected = progress_entry.get("expectedOtjh") or progress_entry.get("expected_otjh")
        if snapshot_expected not in (None, ""):
            try:
                return float(snapshot_expected)
            except (TypeError, ValueError):
                pass
    if component_id in expected_by_id:
        return expected_by_id[component_id]
    expected = component_meta.get("expectedOtjh")
    if expected in (None, ""):
        return None
    try:
        return float(expected)
    except (TypeError, ValueError):
        return None


def build_otjh_completed_entries(
    progress_entries: list[dict],
    activity_entries: list[dict],
    training_plan,
    *,
    expected_by_id: dict[str, float] | None = None,
) -> list[dict]:
    component_lookup = training_plan_component_lookup(training_plan)
    activity_by_quiz: dict[str, dict] = {}
    activity_by_component: dict[str, dict] = {}

    for activity in activity_entries:
        if not isinstance(activity, dict):
            continue
        quiz_id = clean_text(activity.get("quizId"))
        component_id = clean_text(activity.get("componentId"))
        kind = clean_text(activity.get("kind")).lower()
        if quiz_id and quiz_id not in activity_by_quiz:
            activity_by_quiz[quiz_id] = activity
        if component_id:
            keyed_component = f"{kind}:{component_id}" if kind else component_id
            activity_by_component.setdefault(keyed_component, activity)
            activity_by_component.setdefault(component_id, activity)

    entries: list[dict] = []
    if expected_by_id is None:
        expected_by_id = curriculum_expected_otjh_by_component_id([
            clean_text(entry.get("componentId"))
            for entry in progress_entries
            if isinstance(entry, dict)
        ])
    for index, entry in enumerate(dedupe_otjh_progress_records(progress_entries)):
        if not isinstance(entry, dict):
            continue

        kind = clean_text(entry.get("kind")).lower()
        quiz_id = clean_text(entry.get("quizId"))
        component_id = clean_text(entry.get("componentId"))
        component_meta = component_lookup.get(component_id, {})
        expected_hours = component_expected_otjh_hours(component_id, component_meta, expected_by_id, entry)
        minutes = expected_hours * 60 if expected_hours is not None else reported_minutes(entry.get("reportedTime"))
        if minutes <= 0:
            continue

        activity = (
            activity_by_quiz.get(quiz_id)
            if quiz_id
            else activity_by_component.get(f"{kind}:{component_id}") or activity_by_component.get(component_id)
        )
        merged_entry = {
            **(activity or {}),
            **entry,
            "title": (
                clean_text(entry.get("title"))
                or clean_text(entry.get("quizName"))
                or clean_text(entry.get("componentTitle"))
                or clean_text((activity or {}).get("title"))
                or clean_text(component_meta.get("title"))
            ),
            "module": (
                clean_text(entry.get("moduleTitle") or entry.get("module"))
                or clean_text((activity or {}).get("module"))
                or clean_text(component_meta.get("module"))
            ),
            "week": (
                clean_text(entry.get("weekTitle") or entry.get("week"))
                or clean_text((activity or {}).get("week"))
                or clean_text(component_meta.get("week"))
            ),
        }
        # Declared first, exactly as entry_activity_date orders it: this month's
        # list is filtered by the declared instant, so the date shown beside an
        # entry has to be the same one, or a September list prints October dates.
        recorded_at = (
            clean_text(entry.get("declaredCompletedAt"))
            or clean_text(entry.get("submittedAt"))
            or clean_text((activity or {}).get("at"))
            or clean_text(entry.get("startedAt"))
        )
        entries.append({
            "id": f'{monthly_activity_identity(merged_entry, index)}:{index}',
            "title": monthly_learning_title(merged_entry),
            "typeLabel": monthly_learning_type(merged_entry),
            "kind": kind or "activity",
            "module": clean_text(merged_entry.get("module")) or "--",
            "week": clean_text(merged_entry.get("week")) or "--",
            "reportedTime": clean_text(entry.get("reportedTime")) or "--",
            "hours": round(minutes / 60, 2),
            "completedAt": recorded_at or "",
            "completedDate": format_date(recorded_at or entry_activity_date(entry)),
            "detail": monthly_learning_detail(merged_entry),
            "ksbs": sorted(extract_ksb_codes(entry.get("ksbs"))),
        })

    entries.sort(
        key=lambda item: (
            clean_text(item.get("completedAt")),
            clean_text(item.get("title")),
        ),
        reverse=True,
    )
    return entries


def build_ksb_completed_details(
    target_ksbs,
    completed_codes: set[str],
    progress_entries: list[dict],
    activity_entries: list[dict],
    training_plan,
) -> list[dict]:
    target_lookup = ksb_target_lookup(target_ksbs)
    if not target_lookup:
        return []

    completed_in_target = completed_codes & set(target_lookup)
    details_by_code = {
        code: {
            **target_lookup[code],
            "sources": [],
        }
        for code in completed_in_target
    }
    seen_sources_by_code = {code: set() for code in completed_in_target}

    component_lookup = training_plan_component_lookup(training_plan)
    activity_by_quiz: dict[str, dict] = {}
    activity_by_component: dict[str, dict] = {}

    for activity in activity_entries:
        if not isinstance(activity, dict):
            continue
        quiz_id = clean_text(activity.get("quizId"))
        component_id = clean_text(activity.get("componentId"))
        kind = clean_text(activity.get("kind")).lower()
        if quiz_id and quiz_id not in activity_by_quiz:
            activity_by_quiz[quiz_id] = activity
        if component_id:
            keyed_component = f"{kind}:{component_id}" if kind else component_id
            activity_by_component.setdefault(keyed_component, activity)
            activity_by_component.setdefault(component_id, activity)

    source_entries = [
        ("progress", entry)
        for entry in dedupe_otjh_progress_records(progress_entries)
        if isinstance(entry, dict) and progress_record_counts_as_achieved(entry)
    ]
    source_entries.extend(
        ("activity", entry)
        for entry in activity_entries
        if isinstance(entry, dict) and progress_record_counts_as_achieved(entry)
    )

    for index, (source_kind, entry) in enumerate(source_entries):
        entry_codes = extract_ksb_codes(entry.get("ksbs")) & completed_in_target
        if not entry_codes:
            continue

        kind = clean_text(entry.get("kind")).lower()
        quiz_id = clean_text(entry.get("quizId"))
        component_id = clean_text(entry.get("componentId"))
        activity = (
            activity_by_quiz.get(quiz_id)
            if quiz_id
            else activity_by_component.get(f"{kind}:{component_id}") or activity_by_component.get(component_id)
        )
        if source_kind == "activity":
            activity = None

        component_meta = component_lookup.get(component_id, {})
        merged_entry = {
            **(activity or {}),
            **entry,
            "title": ksb_evidence_title(entry, activity, component_meta),
            "module": (
                clean_text(entry.get("moduleTitle") or entry.get("module"))
                or clean_text((activity or {}).get("module"))
                or clean_text(component_meta.get("module"))
            ),
            "week": (
                clean_text(entry.get("weekTitle") or entry.get("week"))
                or clean_text((activity or {}).get("week"))
                or clean_text(component_meta.get("week"))
            ),
        }
        # Declared first, exactly as entry_activity_date orders it: this month's
        # list is filtered by the declared instant, so the date shown beside an
        # entry has to be the same one, or a September list prints October dates.
        recorded_at = (
            clean_text(entry.get("declaredCompletedAt"))
            or clean_text(entry.get("submittedAt"))
            or clean_text((activity or {}).get("at"))
            or clean_text(entry.get("startedAt"))
            or clean_text(entry.get("at"))
        )
        minutes = reported_minutes(entry.get("reportedTime"))
        source_key = activity_completion_key(merged_entry, index)
        source = {
            "id": source_key,
            "title": monthly_learning_title(merged_entry),
            "typeLabel": monthly_learning_type(merged_entry),
            "kind": kind or "activity",
            "componentId": component_id or None,
            "module": clean_text(merged_entry.get("module")) or "--",
            "week": clean_text(merged_entry.get("week")) or "--",
            "reportedTime": clean_text(entry.get("reportedTime")) or "--",
            "hours": round(minutes / 60, 2) if minutes > 0 else None,
            "completedAt": recorded_at or "",
            "completedDate": format_date(recorded_at or entry_activity_date(entry)),
            "detail": monthly_learning_detail(merged_entry),
        }

        for code in entry_codes:
            seen_sources = seen_sources_by_code[code]
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            details_by_code[code]["sources"].append(source)

    details = sorted(details_by_code.values(), key=lambda item: ksb_sort_key(item["code"]))
    for item in details:
        item["sources"].sort(
            key=lambda source: (
                clean_text(source.get("completedAt")),
                clean_text(source.get("title")),
            ),
            reverse=True,
        )
    return details


def monthly_learning_tone(entry: dict) -> str:
    kind = clean_text(entry.get("kind")).lower()
    if is_evidence_entry(entry):
        return "emerald"
    if kind == "quiz":
        return "amber"
    if kind == "video":
        return "red"
    return "primary"


def monthly_status_label(status: str) -> str:
    value = clean_text(status).lower()
    if value == CoachCalendarEvent.STATUS_NOT_SCHEDULED:
        return "Not Scheduled"
    if value == CoachCalendarEvent.STATUS_IN_PROGRESS:
        return "In progress"
    return value.replace("-", " ").title() if value else "--"


def monthly_event_is_unscheduled(status: str) -> bool:
    value = clean_text(status).lower().replace("_", "-")
    return value in {
        CoachCalendarEvent.STATUS_NOT_SCHEDULED,
        "needs-schedule",
        "need-schedule",
    } or value.replace("-", " ") in {"needs schedule", "need schedule"}


def monthly_event_display_date(event: dict) -> date | None:
    return date_only(event.get("scheduledDate") or event.get("date") or event.get("targetDate"))


def monthly_event_is_between(event: dict, start_date: date, end_date: date) -> bool:
    """Include a coaching event when either its target or booked date is in the month."""
    event_dates = {
        parsed
        for value in (event.get("scheduledDate"), event.get("targetDate"), event.get("date"))
        for parsed in [date_only(value)]
        if parsed
    }
    return any(start_date <= event_date <= end_date for event_date in event_dates)


def monthly_event_matches_learner(event: dict, learner: dict) -> bool:
    event_learner_id = clean_text(event.get("learnerId"))
    learner_id = clean_text(learner.get("id"))
    if event_learner_id and learner_id and event_learner_id == learner_id:
        return True

    event_email = normalize_email(event.get("email"))
    learner_email = normalize_email(learner.get("email"))
    return bool(event_email and learner_email and event_email == learner_email)


def monthly_event_type_label(event: dict) -> str:
    source = review_event_category(event)
    if source == "mcr":
        return "MCM"
    if source == "progress-review":
        return "PR"
    if source == CATCH_UP_EVENT_TYPE:
        return "Catch-up"
    if source == "student-support":
        return "Support"
    return clean_text(event.get("title")) or "Session"


def monthly_event_tone(event: dict) -> str:
    status = clean_text(event.get("status")).lower()
    if status == CoachCalendarEvent.STATUS_COMPLETED:
        return "emerald"
    if status == CoachCalendarEvent.STATUS_NOT_SCHEDULED:
        return "amber"
    if status == CoachCalendarEvent.STATUS_CANCELLED:
        return "red"
    return "primary"


def build_monthly_activity_item(
    *,
    item_id: str,
    item_date: date,
    item_type: str,
    title: str,
    detail: str,
    tone: str,
    source: str,
    status: str = "",
    time_label: str = "",
) -> dict:
    return {
        "id": item_id,
        "date": item_date.isoformat(),
        "type": item_type,
        "title": title,
        "detail": detail,
        "tone": tone,
        "source": source,
        "status": status,
        "timeLabel": time_label,
    }


def build_monthly_activity_learner(
    row: LearnerProfile | SimpleNamespace,
    learner: dict,
    events: list[dict],
    start_date: date,
    end_date: date,
    attendance_rows: list[dict] | None = None,
    expected_otjh_by_component_id: dict[str, float] | None = None,
) -> dict:
    progress_entries = [entry for entry in list_or_empty(row.training_plan_progress) if isinstance(entry, dict)]
    activity_entries = learner_activity_feed_entries(row)
    monthly_progress = [entry for entry in progress_entries if entry_is_between(entry, start_date, end_date)]
    monthly_feed = [entry for entry in activity_entries if entry_is_between(entry, start_date, end_date)]
    learner_events = [
        event
        for event in events
        if monthly_event_matches_learner(event, learner)
    ]

    deduped_monthly_progress = dedupe_otjh_progress_records(monthly_progress)
    monthly_hours = round(sum(reported_minutes(entry.get("reportedTime")) for entry in deduped_monthly_progress) / 60, 1)
    quizzes = sum(1 for entry in deduped_monthly_progress if clean_text(entry.get("kind")).lower() == "quiz")
    videos = sum(1 for entry in deduped_monthly_progress if clean_text(entry.get("kind")).lower() == "video")
    components = sum(1 for entry in deduped_monthly_progress if clean_text(entry.get("kind")).lower() == "component")
    reflections = sum(1 for entry in deduped_monthly_progress if clean_text(entry.get("feedback")))
    evidence_keys = {
        monthly_activity_dedupe_identity(entry, index)
        for index, entry in enumerate([*monthly_progress, *monthly_feed])
        if is_evidence_entry(entry)
    }
    monthly_ksb_codes = completed_ksb_codes(deduped_monthly_progress, [])

    active_learner_events = [
        event
        for event in learner_events
        if clean_text(event.get("status")).lower() != CoachCalendarEvent.STATUS_CANCELLED
    ]
    event_sources = [review_event_category(event) for event in active_learner_events]
    mcm_count = event_sources.count("mcr")
    review_count = event_sources.count("progress-review")
    catchup_count = event_sources.count(CATCH_UP_EVENT_TYPE)
    needs_schedule_count = sum(
        1 for event in learner_events if monthly_event_is_unscheduled(event.get("status"))
    )
    booked_count = sum(
        1
        for event in learner_events
        if clean_text(event.get("status")).lower()
        in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS, CoachCalendarEvent.STATUS_COMPLETED, "confirmed"}
    )

    activities: list[dict] = []
    seen_activity_keys: set[str] = set()
    credited_progress_keys: set[str] = set()

    for index, event in enumerate(learner_events):
        event_status = clean_text(event.get("status")).lower()
        if event_status == CoachCalendarEvent.STATUS_CANCELLED:
            continue
        event_date = monthly_event_display_date(event)
        if not event_date:
            continue
        activity_key = f"event:{clean_text(event.get('eventKey') or event.get('id')) or index}"
        seen_activity_keys.add(activity_key)
        activities.append(
            build_monthly_activity_item(
                item_id=activity_key,
                item_date=event_date,
                item_type=monthly_event_type_label(event),
                title=clean_text(event.get("title")) or monthly_event_type_label(event),
                detail=f"{monthly_status_label(clean_text(event.get('status')))} - {clean_text(event.get('timeLabel')) or 'Time TBC'}",
                tone=monthly_event_tone(event),
                source="calendar",
                status=clean_text(event.get("status")),
                time_label=clean_text(event.get("timeLabel")) or "Time TBC",
            )
        )

    # The journal and Actual hours must use the same credited records. Rendering
    # the raw activity feed first showed repeat submissions that OTJH
    # deduplication correctly counted once, making the visible rows disagree
    # with the headline total.
    for index, entry in enumerate(deduped_monthly_progress):
        identity = monthly_activity_identity(entry, index)
        dedupe_key = f"learning:{monthly_activity_dedupe_identity(entry, index)}"
        if dedupe_key in seen_activity_keys:
            continue
        seen_activity_keys.add(dedupe_key)
        credited_progress_keys.add(dedupe_key)
        entry_date = entry_activity_date(entry)
        if not entry_date:
            continue
        activities.append(
            build_monthly_activity_item(
                item_id=f"progress:{identity}",
                item_date=entry_date,
                item_type=monthly_learning_type(entry),
                title=monthly_learning_title(entry),
                detail=monthly_learning_detail(entry),
                tone=monthly_learning_tone(entry),
                source="training-plan-progress",
            )
        )

    # Keep standalone feed entries that have no matching credited progress
    # record. Multiple quiz attempts remain distinct audit rows.
    for index, entry in enumerate(monthly_feed):
        activity_key = f"feed:{monthly_activity_identity(entry, index)}"
        dedupe_key = f"learning:{monthly_activity_dedupe_identity(entry, index)}"
        if activity_key in seen_activity_keys or dedupe_key in credited_progress_keys:
            continue
        seen_activity_keys.add(activity_key)
        entry_date = entry_activity_date(entry)
        if not entry_date:
            continue
        activities.append(
            build_monthly_activity_item(
                item_id=activity_key,
                item_date=entry_date,
                item_type=monthly_learning_type(entry),
                title=monthly_learning_title(entry),
                detail=monthly_learning_detail(entry),
                tone=monthly_learning_tone(entry),
                source="activity-feed",
            )
        )

    for index, attendance_row in enumerate(attendance_rows or []):
        session_date = attendance_row.get("session_date")
        if not isinstance(session_date, date) or not (start_date <= session_date <= end_date):
            continue
        status = clean_text(attendance_row.get("attendance_status")).lower()
        activities.append(
            build_monthly_activity_item(
                item_id=f"attendance:{attendance_row.get('session_id')}:{index}",
                item_date=session_date,
                item_type="Attendance",
                title=clean_text(attendance_row.get("session_title")) or "Live session",
                detail="Present" if status == "present" else "Absent",
                tone="emerald" if status == "present" else "red",
                source="attendance",
                status=status,
            )
        )

    activities.sort(key=lambda item: (item["date"], item["title"]), reverse=True)

    needs_action: list[str] = []
    if not monthly_progress:
        needs_action.append("No learning activity this month")
    if mcm_count == 0:
        needs_action.append("Need MCM schedule")
    if review_count == 0:
        needs_action.append("Need PR schedule")
    if not evidence_keys:
        needs_action.append("No evidence this month")
    if monthly_hours <= 0:
        needs_action.append("No OTJH logged")
    if needs_schedule_count:
        needs_action.append(f"{needs_schedule_count} session{'s' if needs_schedule_count != 1 else ''} need schedule")

    otjh_status = clean_text(learner.get("otjhStatus")).lower()
    if otjh_status == "at risk" or len(needs_action) >= 3:
        monthly_status = "at-risk"
    elif needs_action:
        monthly_status = "need-attention"
    else:
        monthly_status = "on-track"

    last_activity = activities[0] if activities else None
    target_training_plan = monthly_target_training_plan(row)
    monthly_target_hours = curriculum_monthly_target_hours(
        target_training_plan,
        monthly_target_start_date(row),
        start_date,
        end_date,
        expected_otjh_by_component_id or {},
    )

    return {
        "id": learner["id"],
        "name": learner["name"],
        "initials": learner["initials"],
        "learnerType": learner.get("learnerType"),
        "enrolmentId": learner.get("enrolmentId"),
        "currentModule": learner.get("currentModule"),
        "currentWeek": learner.get("currentWeek"),
        "componentsTargetToDate": learner.get("componentsTargetToDate"),
        "email": learner.get("email"),
        "cohortName": learner.get("cohortName") or "--",
        "group": learner.get("group") or "--",
        "programme": clean_text(getattr(row, "programme", "")) or learner.get("cohortName") or "--",
        "status": monthly_status,
        "otjhStatus": learner.get("otjhStatus") or "--",
        "lastActivityDate": last_activity["date"] if last_activity else None,
        "lastActivityLabel": last_activity["title"] if last_activity else "--",
        "learning": {
            "total": len(deduped_monthly_progress),
            "quizzes": quizzes,
            "videos": videos,
            "components": components,
            "reflections": reflections,
        },
        "coaching": {
            "total": len(active_learner_events),
            "booked": booked_count,
            "needsSchedule": needs_schedule_count,
            "mcm": mcm_count,
            "progressReviews": review_count,
            "catchups": catchup_count,
        },
        "evidence": {
            "submitted": len(evidence_keys),
            "latestDate": next((item["date"] for item in activities if item["type"].lower() == "evidence"), None),
        },
        "ksb": {
            "touched": len(monthly_ksb_codes),
            "codes": sorted(monthly_ksb_codes),
        },
        "otjh": {
            "monthlyHours": monthly_hours,
            "monthlyHoursLabel": f"{format_hours_number(monthly_hours)}h",
            "monthlyTarget": monthly_target_hours,
            "progress": percentage(monthly_hours, monthly_target_hours),
            "completed": learner.get("otjhCompleted") or 0,
            "target": learner.get("otjhTarget") or 0,
        },
        "needsAction": needs_action[:5],
        "activities": activities,
    }


def fetch_evidence_file_queue(owner_email: str) -> tuple[list[dict], list[dict]]:
    active_rows = fetch_caseload_learner_profiles(owner_email)
    caseload_learners = [
        serialize_caseload_learner(row, refresh_live_snapshots=False)
        for row in active_rows
    ]
    caseload_by_id = {
        str(learner["id"]): learner
        for learner in caseload_learners
        if clean_text(learner.get("id"))
    }
    if not caseload_by_id:
        return [], caseload_learners

    connection = connections[get_learner_db_alias()]
    relation = find_existing_relation(connection, LEARNER_EVIDENCE_FILES_RELATION_CANDIDATES)
    if not relation:
        return [], caseload_learners

    columns = relation_columns(connection, relation)
    learner_id_column = first_existing_column(columns, "learner_id", "learnerid", "Learner ID")
    created_at_column = first_existing_column(columns, "created_at", "submitted_at", "uploaded_at", "updated_at")
    if not learner_id_column:
        return [], caseload_learners

    learner_ids = sorted(caseload_by_id.keys(), key=lambda value: int(value) if value.isdigit() else value)
    placeholders = ", ".join(["%s"] * len(learner_ids))
    last_submission_select = (
        f"max({quote_sql_identifier(created_at_column)}) as last_submission"
        if created_at_column
        else "null as last_submission"
    )
    query = f"""
        select
            {quote_sql_identifier(learner_id_column)}::text as learner_id,
            count(*)::int as evidence_count,
            {last_submission_select}
        from {relation}
        where {quote_sql_identifier(learner_id_column)}::text in ({placeholders})
        group by {quote_sql_identifier(learner_id_column)}::text
        order by evidence_count desc, learner_id
    """

    with connection.cursor() as cursor:
        cursor.execute(query, learner_ids)
        rows = cursor.fetchall()

    items = []
    today = date.today()
    for learner_id, evidence_count, last_submission in rows:
        learner = caseload_by_id.get(str(learner_id))
        if not learner:
            continue

        last_submission_date = parse_date_value(last_submission)
        last_submission_day = last_submission_date.date() if isinstance(last_submission_date, datetime) else last_submission_date
        elapsed_days = (today - last_submission_day).days if isinstance(last_submission_day, date) else 0
        count = to_int(evidence_count)
        items.append({
            "id": str(learner_id),
            "learnerId": str(learner_id),
            "learner": learner["name"],
            "initials": learner["initials"],
            "email": learner.get("email"),
            "programme": learner["cohortName"],
            "group": learner["group"],
            "pendingEvidence": count,
            "acceptedEvidence": 0,
            "referredEvidence": 0,
            "totalEvidence": count,
            "lastSubmission": format_date_value(last_submission),
            "lastSubmissionIso": format_iso_date_value(last_submission),
            "isOverdue": elapsed_days > MARKING_OVERDUE_DAYS,
        })

    return items, caseload_learners


def update_learner_coach_rag(learner_id: int, coach_rag: str | None) -> bool:
    return LearnerProfile.objects.filter(id=learner_id).update(coach_rag=coach_rag) > 0


def attendance_risk_from_rate(rate: int | None) -> str | None:
    if rate is None:
        return None
    if rate >= 90:
        return "green"
    if rate >= 80:
        return "amber"
    return "red"


def attendance_trend_from_records(records: list[dict]) -> str:
    if len(records) < 2:
        return "stable"

    recent = records[:4]
    previous = records[4:8]
    if not previous:
        return "up" if recent[0]["attendance"] == 1 else "down"

    recent_rate = sum(row["attendance"] for row in recent) / len(recent)
    previous_rate = sum(row["attendance"] for row in previous) / len(previous)
    if recent_rate > previous_rate + 0.05:
        return "up"
    if recent_rate < previous_rate - 0.05:
        return "down"
    return "stable"


def build_trend_point(row: dict) -> dict:
    attended = to_int(row["attended"])
    absent = to_int(row["absent"])
    total = attended + absent
    return {
        "label": row["label"],
        "value": percentage(absent, total),
        "sessionDate": format_iso_date(row["period"]),
        "attended": attended,
        "absent": absent,
        "onBreak": 0,
    }


def fetch_attendance_data(email_keys: list[str]) -> dict:
    if not email_keys:
        return {"metrics": {}, "records": {}, "trends": {"week": [], "month": [], "year": []}}

    unique_email_keys = sorted({email for email in email_keys if email})
    connection_string = get_kbc_attendance_connection_string()

    metrics: dict[str, dict] = {}
    records: dict[str, list[dict]] = {}
    trends: dict[str, list[dict]] = {"week": [], "month": [], "year": []}

    trend_queries = {
        "week": (
            "date_trunc('week', \"date\")::date",
            "'W' || to_char(date_trunc('week', \"date\"), 'IW')",
        ),
        "month": (
            "date_trunc('month', \"date\")::date",
            "to_char(date_trunc('month', \"date\"), 'Mon YYYY')",
        ),
        "year": (
            "date_trunc('year', \"date\")::date",
            "to_char(date_trunc('year', \"date\"), 'YYYY')",
        ),
    }

    with psycopg.connect(connection_string, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    lower(trim("Email")) as email_key,
                    count(*) filter (where "Attendance" in (0, 1)) as sessions,
                    count(*) filter (where "Attendance" = 1) as present,
                    count(*) filter (where "Attendance" = 0) as absent,
                    min("date") as first_session_date,
                    max("date") as last_session_date
                from public.kbc_attendance
                where lower(trim("Email")) = any(%s)
                  and "Attendance" in (0, 1)
                group by lower(trim("Email"))
                """,
                (unique_email_keys,),
            )
            for row in cur.fetchall():
                metrics[row["email_key"]] = {
                    "sessions": to_int(row["sessions"]),
                    "present": to_int(row["present"]),
                    "absent": to_int(row["absent"]),
                    "firstSessionDate": format_iso_date(row["first_session_date"]),
                    "lastSessionDate": format_iso_date(row["last_session_date"]),
                    "lastSession": format_date(row["last_session_date"]),
                }

            cur.execute(
                """
                select
                    lower(trim("Email")) as email_key,
                    "date" as session_date,
                    "Attendance" as attendance
                from public.kbc_attendance
                where lower(trim("Email")) = any(%s)
                  and "Attendance" in (0, 1)
                order by lower(trim("Email")), "date" desc
                """,
                (unique_email_keys,),
            )
            for row in cur.fetchall():
                records.setdefault(row["email_key"], []).append(
                    {
                        "date": row["session_date"],
                        "attendance": to_int(row["attendance"]),
                    }
                )

            for key, (period_expr, label_expr) in trend_queries.items():
                cur.execute(
                    f"""
                    select
                        {period_expr} as period,
                        {label_expr} as label,
                        count(*) filter (where "Attendance" = 1) as attended,
                        count(*) filter (where "Attendance" = 0) as absent
                    from public.kbc_attendance
                    where lower(trim("Email")) = any(%s)
                      and "Attendance" in (0, 1)
                    group by period, label
                    order by period
                    """,
                    (unique_email_keys,),
                )
                trends[key] = [build_trend_point(row) for row in cur.fetchall()]

    for email_key, email_records in records.items():
        consecutive_missed = 0
        for row in email_records:
            if row["attendance"] == 0:
                consecutive_missed += 1
            else:
                break
        metrics.setdefault(email_key, {})["consecutiveMissed"] = consecutive_missed
        metrics[email_key]["trend"] = attendance_trend_from_records(email_records)

    return {"metrics": metrics, "records": records, "trends": trends}


def fetch_learner_absence_data(email_keys: list[str]) -> dict:
    """Read fallback attendance summary rows when the legacy absence table exists."""
    """Read the optional legacy summary table when it still exists."""
    unique_email_keys = sorted({normalize_email(email) for email in email_keys if email})
    empty = {"metrics": {}, "records": {}, "trends": {"week": [], "month": [], "year": []}}
    if not unique_email_keys:
        return empty

    connection = connections[router.db_for_read(LearnerAbsence) or "default"]
    relation = find_learner_absence_relation(connection)
    if not relation:
        return empty

    columns = relation_columns(connection, relation)
    email_column = first_existing_column(columns, "learner_email", "email")
    sessions_column = first_existing_column(columns, "sessions")
    present_column = first_existing_column(columns, "present")
    absent_column = first_existing_column(columns, "absent")
    catchup_column = first_existing_column(columns, "catchup")
    if not all([email_column, sessions_column, present_column, absent_column, catchup_column]):
        return empty

    late_column = first_existing_column(columns, "late")
    risk_column = first_existing_column(columns, "risk")
    last_session_date_column = first_existing_column(columns, "last_session_date")
    consecutive_missed_column = first_existing_column(columns, "consecutive_missed")

    selected_aliases: dict[str, str | None] = {
        "learner_email": email_column,
        "sessions": sessions_column,
        "present": present_column,
        "absent": absent_column,
        "catchup": catchup_column,
        "late": late_column,
        "risk": risk_column,
        "last_session_date": last_session_date_column,
        "consecutive_missed": consecutive_missed_column,
    }
    select_columns = [
        f"{quote_sql_identifier(column)} as {quote_sql_identifier(alias)}" if column else f"null as {quote_sql_identifier(alias)}"
        for alias, column in selected_aliases.items()
    ]
    query = f"""
        select {", ".join(select_columns)}
        from {relation}
        where lower(trim({quote_sql_identifier(email_column)}::text)) = any(%s)
    """

    with connection.cursor() as cursor:
        cursor.execute(query, [unique_email_keys])
        result_columns = [column[0] for column in cursor.description]
        rows = [dict(zip(result_columns, row)) for row in cursor.fetchall()]

    metrics: dict[str, dict] = {}
    for row in rows:
        email_key = normalize_email(row["learner_email"])
        metrics[email_key] = {
            "sessions": to_int(row["sessions"]),
            "present": to_int(row["present"]),
            "absent": to_int(row["absent"]),
            "late": to_int(row.get("late")),
            "catchup": to_int(row["catchup"]),
            "risk": clean_text(row.get("risk")) or None,
            "lastSessionDate": format_iso_date(row.get("last_session_date")),
            "lastSession": format_date(row.get("last_session_date")),
            "consecutiveMissed": to_int(row.get("consecutive_missed")),
        }

    trends: dict[str, list[dict]] = {"week": [], "month": [], "year": []}
    trend_groups: dict[str, dict[tuple, dict]] = {"week": {}, "month": {}, "year": {}}
    for row in rows:
        session_date = row.get("last_session_date")
        if not session_date:
            continue
        week_start = session_date - timedelta(days=session_date.weekday())
        group_keys = {
            "week": ((week_start.year, week_start.isocalendar().week), week_start, f"W{week_start.isocalendar().week:02d}"),
            "month": ((session_date.year, session_date.month), session_date.replace(day=1), session_date.strftime("%b %Y")),
            "year": ((session_date.year,), session_date.replace(month=1, day=1), str(session_date.year)),
        }
        for view, (key, period, label) in group_keys.items():
            group = trend_groups[view].setdefault(key, {"period": period, "label": label, "attended": 0, "absent": 0})
            group["attended"] += to_int(row["present"])
            group["absent"] += to_int(row["absent"])

    for view, groups in trend_groups.items():
        for group in sorted(groups.values(), key=lambda item: item["period"]):
            total = group["attended"] + group["absent"]
            trends[view].append({
                "label": group["label"],
                "value": percentage(group["absent"], total),
                "sessionDate": format_iso_date(group["period"]),
                "attended": group["attended"],
                "absent": group["absent"],
                "onBreak": 0,
            })

    return {"metrics": metrics, "records": {}, "trends": trends}


def is_truthy_value(value) -> bool:
    text = clean_text(value).lower()
    return text in {"1", "true", "yes", "y", "completed", "complete", "done"}


def build_attendance_metrics_from_detail_rows(rows: list[dict]) -> dict:
    rows = sorted(
        rows,
        key=lambda item: (
            parse_date_value(item.get("session_date")) or datetime.min,
            clean_text(item.get("session_start_time")),
        ),
        reverse=True,
    )
    present = sum(1 for row in rows if normalize_attendance_detail_status(row.get("attendance_status")) == "present")
    absent = sum(1 for row in rows if normalize_attendance_detail_status(row.get("attendance_status")) == "absent")
    late = sum(1 for row in rows if to_int(row.get("minutes_late")) > 0)
    catchup = sum(1 for row in rows if is_truthy_value(row.get("catchup_completed")))
    absence_reasons: dict[str, int] = {}
    authorised_absent = 0
    unauthorised_absent = 0
    for row in rows:
        if normalize_attendance_detail_status(row.get("attendance_status")) != "absent":
            continue
        reason = clean_text(row.get("absence_reason"))
        if reason and reason.lower() not in {"--", "none", "n/a", "no reason", "no reason provided"}:
            authorised_absent += 1
            label = reason[:80]
        else:
            unauthorised_absent += 1
            label = "No Reason Provided"
        absence_reasons[label] = absence_reasons.get(label, 0) + 1
    last_session_date = None
    for row in rows:
        parsed_date = parse_date_value(row.get("session_date"))
        if parsed_date:
            last_session_date = parsed_date.date() if isinstance(parsed_date, datetime) else parsed_date
            break

    consecutive_missed = 0
    for row in rows:
        status = normalize_attendance_detail_status(row.get("attendance_status"))
        if status == "absent":
            consecutive_missed += 1
        elif status == "present":
            break

    recorded_sessions = present + absent
    attendance_rate = percentage(present, recorded_sessions) if recorded_sessions else None
    return {
        "sessions": len(rows),
        "present": present,
        "absent": absent,
        "late": late,
        "catchup": catchup,
        "authorisedAbsent": authorised_absent,
        "unauthorisedAbsent": unauthorised_absent,
        "absenceReasons": absence_reasons,
        "risk": attendance_risk_from_rate(attendance_rate),
        "lastSessionDate": format_iso_date(last_session_date),
        "lastSession": format_date(last_session_date),
        "consecutiveMissed": consecutive_missed,
    }


def empty_attendance_detail_summary() -> dict:
    return {
        "metrics": {},
        "metricsById": {},
        "records": {},
        "recordsById": {},
        "trends": {"week": [], "month": [], "year": []},
        "rows": [],
    }


def build_attendance_detail_summary_payload(rows: list[dict]) -> dict:
    rows_by_id: dict[int, list[dict]] = {}
    rows_by_email: dict[str, list[dict]] = {}
    for row in rows:
        learner_id = to_int(row.get("learner_id"))
        email_key = normalize_email(row.get("learner_email"))
        if learner_id:
            rows_by_id.setdefault(learner_id, []).append(row)
        if email_key:
            rows_by_email.setdefault(email_key, []).append(row)

    metrics_by_id = {
        learner_id: build_attendance_metrics_from_detail_rows(items)
        for learner_id, items in rows_by_id.items()
    }
    metrics_by_email = {
        email: build_attendance_metrics_from_detail_rows(items)
        for email, items in rows_by_email.items()
    }

    trend_groups: dict[str, dict[tuple, dict]] = {"week": {}, "month": {}, "year": {}}
    for row in rows:
        status = normalize_attendance_detail_status(row.get("attendance_status"))
        if status not in {"present", "absent"}:
            continue
        parsed_date = parse_date_value(row.get("session_date"))
        if not parsed_date:
            continue
        session_date = parsed_date.date() if isinstance(parsed_date, datetime) else parsed_date
        week_start = session_date - timedelta(days=session_date.weekday())
        group_keys = {
            "week": ((week_start.year, week_start.isocalendar().week), week_start, f"W{week_start.isocalendar().week:02d}"),
            "month": ((session_date.year, session_date.month), session_date.replace(day=1), session_date.strftime("%b %Y")),
            "year": ((session_date.year,), session_date.replace(month=1, day=1), str(session_date.year)),
        }
        for view, (key, period, label) in group_keys.items():
            group = trend_groups[view].setdefault(key, {"period": period, "label": label, "attended": 0, "absent": 0})
            if status == "present":
                group["attended"] += 1
            else:
                group["absent"] += 1

    trends: dict[str, list[dict]] = {"week": [], "month": [], "year": []}
    for view, groups in trend_groups.items():
        for group in sorted(groups.values(), key=lambda item: item["period"]):
            total = group["attended"] + group["absent"]
            trends[view].append({
                "label": group["label"],
                "value": percentage(group["absent"], total),
                "sessionDate": format_iso_date(group["period"]),
                "attended": group["attended"],
                "absent": group["absent"],
                "onBreak": 0,
            })

    return {
        "metrics": metrics_by_email,
        "metricsById": metrics_by_id,
        "records": rows_by_email,
        "recordsById": rows_by_id,
        "trends": trends,
        "rows": rows,
    }


def filter_attendance_detail_summary_data(summary_data: dict, learner_ids: list[int], email_keys: list[str]) -> dict:
    ids = {int(learner_id) for learner_id in learner_ids if learner_id}
    emails = {normalize_email(email) for email in email_keys if normalize_email(email)}
    if not ids and not emails:
        return empty_attendance_detail_summary()

    rows = [
        row
        for row in summary_data.get("rows", [])
        if (to_int(row.get("learner_id")) in ids) or (normalize_email(row.get("learner_email")) in emails)
    ]
    return build_attendance_detail_summary_payload(rows)


def fetch_attendance_detail_summary_data(
    learner_ids: list[int], email_keys: list[str], *, include_reported_participants: bool = False,
) -> dict:
    ids = sorted({int(learner_id) for learner_id in learner_ids if learner_id})
    emails = sorted({normalize_email(email) for email in email_keys if normalize_email(email)})
    empty = empty_attendance_detail_summary()
    if not ids and not emails:
        return empty

    if include_reported_participants:
        rows = fetch_verified_teams_attendance_rows(ids, emails, include_reported_participants=True)
    else:
        rows = fetch_verified_teams_attendance_rows(ids, emails)
    return build_attendance_detail_summary_payload(rows)


def fetch_marking_rows(case_owner: str, case_owner_id: int | None = None) -> list[dict]:
    connection_string = get_aptem_connection_string()
    if not connection_string:
        raise RuntimeError("APTEMAUTOEXTRACTINGDATABASE is not configured.")

    query = """
        select
            "FullName" as full_name,
            "Email" as learner_email,
            "Subscription Status" as subscription_status,
            "CaseOwner ID" as case_owner_id,
            "ElapsedDays" as elapsed_days,
            "Phone" as phone,
            "CountEvidencePending" as count_evidence_pending,
            "Evidence Accepted" as evidence_accepted,
            "Evidence Reffered" as evidence_referred,
            "Referred Closure" as referred_closure,
            "Total Evidence" as total_evidence,
            "Last Snapshot CountApproved" as last_snapshot_count_approved,
            "Last Snapshot Date" as last_snapshot_date,
            "Start-Date" as start_date,
            "Status" as status,
            "CaseOwner" as case_owner,
            "LearnerId" as learner_id,
            "LastSubDate" as last_sub_date
        from public."Require Marking"
        where lower(trim(coalesce("CaseOwner", ''))) = lower(trim(%s))
           or (%s is not null and "CaseOwner ID" = %s)
        order by coalesce("CountEvidencePending", 0) desc,
                 coalesce("ElapsedDays", 0) desc,
                 "FullName"
    """

    with psycopg.connect(connection_string, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (case_owner, case_owner_id, case_owner_id))
            return list(cur.fetchall())


def serialize_marking_row(row: dict, caseload_learner: dict | None) -> dict:
    pending_evidence = to_int(row["count_evidence_pending"])
    elapsed_days = to_int(row["elapsed_days"])
    normalized_status = normalize_program_status(row["status"])
    learner_id = row["learner_id"] or (caseload_learner or {}).get("id") or normalize_email(row["learner_email"])

    return {
        "id": str(learner_id),
        "learnerId": str(learner_id),
        "learner": row["full_name"] or (caseload_learner or {}).get("name") or "Unknown learner",
        "initials": build_initials(row["full_name"] or (caseload_learner or {}).get("name")),
        "email": row["learner_email"] or (caseload_learner or {}).get("email"),
        "programme": (caseload_learner or {}).get("cohortName") or "--",
        "group": (caseload_learner or {}).get("group") or "--",
        "status": row["status"] or "--",
        "enrollmentStatus": normalized_status,
        "isOnBreak": normalized_status == "break",
        "pendingEvidence": pending_evidence,
        "acceptedEvidence": to_int(row["evidence_accepted"]),
        "referredEvidence": to_int(row["evidence_referred"]),
        "referredClosure": to_int(row["referred_closure"]),
        "totalEvidence": to_int(row["total_evidence"]),
        "elapsedDays": elapsed_days,
        "isOverdue": elapsed_days > MARKING_OVERDUE_DAYS,
        "lastSubmission": format_date_value(row["last_sub_date"]),
        "lastSubmissionIso": format_iso_date_value(row["last_sub_date"]),
        "startDate": format_date_value(row["start_date"]),
        "module": None,
        "title": None,
        "type": None,
        "due": None,
        "words": None,
    }


def serialize_learner(row: dict) -> dict:
    hours_progress = percentage(row["completed_hours"], row["minimum_hours"] or row["planned_hours"])
    component_progress = percentage(row["completed_components"], row["target_components"])
    ksb_progress = percentage(row["total_completed_ksb"], row["total_target_ksb"])
    performance_status = determine_performance_status(row, hours_progress, ksb_progress, component_progress)
    cohort_name = (row["cohort_name"] or "").strip() or "Unassigned cohort"
    group_name = (row["group_name"] or "").strip() or "General"
    cohort_id = re.sub(r"[^a-z0-9]+", "-", cohort_name.lower()).strip("-") or "unassigned"
    evidence_count = (
        to_int(row["assignment_evidence"])
        + to_int(row["lms_evidence"])
        + to_int(row["extra_evidence"])
    )
    risk_flags = build_risk_flags(row, hours_progress, ksb_progress)

    return {
        "id": str(row["learner_id"]),
        "name": row["full_name"] or "Unknown learner",
        "initials": build_initials(row["full_name"]),
        "employer": row["organization_name"] or "Employer not set",
        "cohortId": cohort_id,
        "cohortName": cohort_name,
        "group": group_name,
        "status": performance_status,
        "enrollmentStatus": normalize_program_status(row["program_status"]),
        "riskFlags": risk_flags,
        "overallProgress": hours_progress,
        "attendanceRate": component_progress,
        "otjhCompleted": to_int(row["completed_hours"]),
        "otjhTarget": max(to_int(row["minimum_hours"]), to_int(row["planned_hours"]), 1),
        "otjhMinimum": to_int(row["minimum_hours"]),
        "otjhPlanned": to_int(row["planned_hours"]),
        "otjhSubmitted": to_int(row["submitted_hours"]),
        "otjhForecast": to_int(row["forecast_hours"]),
        "otjhExpected": to_int(row["expected_hours"]),
        "otjhProgressHours": row["progress_hours"] or "",
        "otjhStatus": row["otjh_status"] or "",
        "ksbCompleted": to_int(row["total_completed_ksb"]),
        "ksbTarget": to_int(row["total_target_ksb"]),
        "ksbStatus": row["ksb_status"] or "",
        "ksbProgress": ksb_progress,
        "evidenceCount": evidence_count,
        "nextCoaching": format_date(row["end_date"]),
        "nextReview": format_date(row["gateway_review_date"]),
        "lastContact": row["owner_name"] or "--",
        "lastAttendanceDate": format_date(row["start_date"]),
        "lastProgressReview": format_date(row["gateway_review_date"]),
        "lastReview": format_date(row["gateway_review_date"]),
        "lastCoachingSession": format_date(row["end_date"]),
        "lastSubmittedEvidence": "--",
        "recentFlag": risk_flags[0] if risk_flags else None,
        "email": row["learner_email"] or None,
        "employerEmail": row["employer_email"] or row["manager_email"] or None,
        "employerPhone": str(row["owner_phone"]) if row["owner_phone"] not in (None, "") else None,
        "progressVariance": row["progress_variance"] or "0%",
        "startDate": format_date(row["start_date"]),
        "gatewayReviewDate": format_date(row["gateway_review_date"]),
        "plannedEndDate": format_date(row["end_date"]),
        "coachName": row["owner_name"] or "Med Maher",
        "coachEmail": row["owner_email"] or "",
        "rawProgramStatus": row["program_status"] or "",
        "coachRag": row["coach_rag"] or "",
    }


def fetch_timetable_source_rows(case_owner: str) -> dict[str, list[dict]]:
    connection_string = get_aptem_connection_string()
    if not connection_string:
        raise RuntimeError("APTEMAUTOEXTRACTINGDATABASE is not configured.")

    with psycopg.connect(connection_string, row_factory=dict_row) as conn:
        progress_rows = conn.execute(
            """
            select *
            from public.progress_review
            where lower(trim(coalesce("CaseOwner", ''))) = lower(trim(%s))
            """,
            (case_owner,),
        ).fetchall()
        mcr_rows = conn.execute(
            """
            select *
            from public."MCR"
            where lower(trim(coalesce("CaseOwner", ''))) = lower(trim(%s))
            """,
            (case_owner,),
        ).fetchall()

    return {"progress_review": list(progress_rows), "mcr": list(mcr_rows)}


def build_caseload_matchers(rows: list[LearnerProfile | SimpleNamespace]) -> tuple[set[str], set[str]]:
    learner_emails: set[str] = set()
    learner_names: set[str] = set()

    for row in rows:
        email = normalize_email(getattr(row, "email", None))
        name = normalize_person_name(getattr(row, "username", None))
        if email:
            learner_emails.add(email)
        if name:
            learner_names.add(name)

    return learner_emails, learner_names


def timetable_row_matches_caseload(
    row: dict,
    learner_emails: set[str],
    learner_names: set[str],
) -> bool:
    email = normalize_email(row.get("Email"))
    if email and email in learner_emails:
        return True

    name = normalize_person_name(row.get("FullName"))
    if name and name in learner_names:
        return True

    return False


def timetable_event_status(source_status: str) -> str | None:
    normalized = source_status.lower()
    if normalized == "completed":
        return "completed"
    if normalized == "scheduled":
        return "scheduled"
    if normalized == "in progress":
        return "in-progress"
    if normalized == "not scheduled":
        return "not-scheduled"
    return None


def timetable_priority(event_status: str, event_type: str) -> str:
    if event_status == "in-progress":
        return "high"
    if event_type == "welfare":
        return "high"
    return "normal"


def build_timetable_event(
    *,
    source: str,
    source_row: dict,
    sequence: int,
    planned_value: str | None,
    status_value: str | None,
    fallback_title: str,
) -> tuple[dict | None, bool]:
    event_date = parse_schedule_date(planned_value) or parse_schedule_date(status_value)
    source_status = normalize_schedule_status(status_value)
    needs_scheduling = source_status == "Not Scheduled"
    event_status = timetable_event_status(source_status)
    if event_date is None or event_status is None:
        return None, needs_scheduling

    label = normalize_schedule_label(planned_value, fallback_title)
    label_lower = label.lower()
    if source == "mcr":
        event_type = "coaching"
        title = "Monthly Coaching"
    elif "personal support" in label_lower:
        event_type = "welfare"
        title = "Personal Support Plan"
    else:
        event_type = "review"
        title = "Progress Review"

    if event_status == "not-scheduled":
        title = "Not Scheduled"

    learner = source_row.get("FullName") or "Unknown learner"
    event = {
        "id": f"{source}-{source_row.get('ID') or normalize_email(source_row.get('Email'))}-{sequence}",
        "source": source,
        "sourceStatus": source_status,
        "sequence": sequence,
        "title": title,
        "type": event_type,
        "date": event_date.isoformat(),
        "year": event_date.year,
        "month": event_date.month - 1,
        "dayOfMonth": event_date.day,
        "dayOfWeek": event_date.weekday(),
        "startHour": 9,
        "endHour": 10,
        "timeLabel": "Time TBC",
        "isTimeEstimated": True,
        "learner": learner,
        "email": source_row.get("Email"),
        "programme": source_row.get("programme") or source_row.get("Status") or "--",
        "cohort": source_row.get("Group") or "--",
        "employer": source_row.get("Manager Name") or None,
        "managerEmail": source_row.get("Manager Email") or None,
        "platform": "--",
        "location": "--",
        "priority": timetable_priority(event_status, event_type),
        "status": event_status,
        "notes": f"{label} - {source_status}",
        "rawPlanned": planned_value or "--",
        "rawStatus": status_value or "--",
    }
    return event, needs_scheduling


def extract_progress_review_events(row: dict) -> tuple[list[dict], int]:
    events = []
    needs_scheduling = 0
    for sequence in range(1, 17):
        event, is_unscheduled = build_timetable_event(
            source="progress-review",
            source_row=row,
            sequence=sequence,
            planned_value=row.get(f"Review Planned Date{sequence}"),
            status_value=row.get(f"Review Status{sequence}"),
            fallback_title="Progress Review",
        )
        if is_unscheduled:
            needs_scheduling += 1
        if event:
            events.append(event)
    return events, needs_scheduling


def extract_mcr_events(row: dict) -> tuple[list[dict], int]:
    events = []
    needs_scheduling = 0
    for sequence in range(1, 23):
        event, is_unscheduled = build_timetable_event(
            source="mcr",
            source_row=row,
            sequence=sequence,
            planned_value=row.get(f"MCM{sequence}"),
            status_value=row.get(f"Status{sequence}"),
            fallback_title="Monthly Coaching",
        )
        if is_unscheduled:
            needs_scheduling += 1
        if event:
            events.append(event)
    return events, needs_scheduling


def assign_timetable_slots(events: list[dict]) -> list[dict]:
    day_taken_slots: dict[str, set[int]] = {}
    for event in events:
        if event.get("isTimeEstimated"):
            continue
        day_taken_slots.setdefault(event["date"], set()).add(int(event["startHour"]))

    for event in sorted(events, key=lambda item: (item["date"], item["type"], item["learner"])):
        if not event.get("isTimeEstimated"):
            continue
        taken_slots = day_taken_slots.setdefault(event["date"], set())
        slot = next(
            (candidate for candidate in TIMETABLE_SCHEDULE_SLOTS if candidate not in taken_slots),
            TIMETABLE_SCHEDULE_SLOTS[len(taken_slots) % len(TIMETABLE_SCHEDULE_SLOTS)],
        )
        event["startHour"] = slot
        event["endHour"] = slot + max(1, round((event.get("durationMinutes") or TIMETABLE_DEFAULT_DURATION_MINUTES) / 60))
        taken_slots.add(int(slot))
    return events


def summarize_timetable_events(events: list[dict], needs_scheduling: int) -> dict:
    completed_events = sum(1 for event in events if event["status"] == "completed")
    scheduled_events = sum(1 for event in events if event["status"] == "scheduled")
    in_progress_events = sum(1 for event in events if event["status"] == "in-progress")
    total_events = len(events)

    return {
        "totalEvents": total_events,
        "completedEvents": completed_events,
        "scheduledEvents": scheduled_events,
        "inProgressEvents": in_progress_events,
        "needsScheduling": needs_scheduling,
        "completionRate": percentage(completed_events, total_events),
        "coachingEvents": sum(1 for event in events if event["type"] == "coaching"),
        "reviewEvents": sum(1 for event in events if event["type"] == "review"),
        "supportEvents": sum(1 for event in events if event["type"] == "welfare"),
    }


def build_timetable_summary(
    events: list[dict],
    needs_scheduling: int,
    source_counts: dict,
    source_needs_scheduling: dict[str, int],
) -> dict:
    summary = summarize_timetable_events(events, needs_scheduling)
    summary.update(
        {
            "sourceBreakdown": {
                "mcr": summarize_timetable_events(
                    [event for event in events if event["source"] == "mcr"],
                    source_needs_scheduling.get("mcr", 0),
                ),
                "progressReview": summarize_timetable_events(
                    [event for event in events if review_event_category(event) == "progress-review"],
                    source_needs_scheduling.get("progress-review", 0),
                ),
                "catchUp": summarize_timetable_events(
                    [event for event in events if event["source"] == CATCH_UP_EVENT_TYPE],
                    source_needs_scheduling.get(CATCH_UP_EVENT_TYPE, 0),
                ),
                "support": summarize_timetable_events(
                    [event for event in events if event["source"] == "student-support"],
                    source_needs_scheduling.get("student-support", 0),
                ),
            },
            "timeAvailability": "Times are not available in MCR/progress_review; events are shown as Time TBC.",
            "sourceCounts": source_counts,
        }
    )
    return summary


def iterate_generated_schedule_dates(
    start_date: date,
    end_date: date,
    interval: timedelta,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
):
    current = start_date + interval
    sequence = 1
    if range_start and current < range_start:
        interval_days = max(int(interval.days), 1)
        skipped_steps = max((range_start - current).days // interval_days, 0)
        if skipped_steps:
            current += interval * skipped_steps
            sequence += skipped_steps
        while current < range_start:
            current += interval
            sequence += 1
    while current <= end_date:
        if range_end and current > range_end:
            break
        yield sequence, current
        current += interval
        sequence += 1


def generated_event_priority(status: str, target_date: date, display_date: date) -> str:
    today = datetime.utcnow().date()
    if status == CoachCalendarEvent.STATUS_NOT_SCHEDULED and target_date < today:
        return "high"
    if status in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS} and display_date <= today:
        return "high"
    return "normal"


def build_learner_profile_map(rows: list[LearnerProfile]) -> dict[int, LearnerProfile]:
    learner_profile_map: dict[int, LearnerProfile] = {}
    for row in rows:
        try:
            learner_id = int(getattr(row, "id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if learner_id > 0:
            learner_profile_map[learner_id] = row
    return learner_profile_map


def fetch_owner_name(owner_email: str, fallback: str = "Coach") -> str:
    staff_name = coach_staff_display_name(owner_email)
    if staff_name:
        return staff_name
    active_rows = fetch_owner_active_learner_profiles(owner_email)
    return next(
        (clean_text(row.coach_name) for row in active_rows if clean_text(row.coach_name)),
        fallback,
    )


def calendar_record_has_launch_url(record: CoachCalendarEvent) -> bool:
    return bool(clean_text(record.meeting_link) or clean_text(record.graph_web_link))


TEAMS_SYNC_PERMISSION_MESSAGE = (
    "Teams calendar sync needs updated Microsoft permissions. "
    "The event was saved locally only; ask your Microsoft 365 administrator to grant the booking application calendar access to the organiser mailbox."
)
TEAMS_SYNC_NOT_CONFIGURED_MESSAGE = "Teams calendar sync is not configured. The event was saved locally only."
# A coach session is recorded and transcribed like a taught one: the recording is
# the learner's catch-up route and the transcript is what the audit trail reads.
# A calendar event alone only ever opens at the tenant defaults -- nothing
# recording, nothing transcribed -- so every event this app creates has these
# applied to the Teams meeting behind it.
COACH_MEETING_RECORDING = "record-transcribe"
COACH_MEETING_LOBBY_BYPASS = "everyone"
TEAMS_SYNC_TEMPORARY_MESSAGE = (
    "Teams calendar sync could not be completed. "
    "The event was saved locally only; try again later or ask an admin to check Microsoft permissions."
)
TEAMS_SYNC_LINK_MISSING_MESSAGE = (
    "Teams did not return a meeting link. The appointment is saved. "
    "Try scheduling again after Microsoft sync is available."
)


def public_graph_sync_warning(raw_message: str | None) -> str:
    """Convert low-level Microsoft Graph errors into safe coach-facing copy."""
    message = clean_text(raw_message)
    if not message:
        return ""
    if message in {
        TEAMS_SYNC_PERMISSION_MESSAGE, TEAMS_SYNC_NOT_CONFIGURED_MESSAGE,
        TEAMS_SYNC_TEMPORARY_MESSAGE, TEAMS_SYNC_LINK_MISSING_MESSAGE,
    }:
        return message
    lowered = message.casefold()
    if "credentials are not configured" in lowered:
        return TEAMS_SYNC_NOT_CONFIGURED_MESSAGE
    if "erroraccessdenied" in lowered or "access is denied" in lowered or " 403 " in f" {lowered} ":
        return TEAMS_SYNC_PERMISSION_MESSAGE
    if "did not return a teams meeting link" in lowered or (
        "did not return" in lowered and "teams" in lowered
    ):
        return TEAMS_SYNC_LINK_MISSING_MESSAGE
    if "microsoft graph" in lowered or "microsoft token" in lowered:
        return TEAMS_SYNC_TEMPORARY_MESSAGE
    return TEAMS_SYNC_TEMPORARY_MESSAGE


def calendar_record_needs_schedule_repair(record: CoachCalendarEvent) -> bool:
    if record.status not in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS}:
        return False
    event_type = clean_text(record.event_type).lower()
    if event_type not in {"mcr", "progress-review", GENERIC_REVIEW_EVENT_TYPE, CATCH_UP_EVENT_TYPE}:
        return False
    return not calendar_record_has_launch_url(record)


def normalize_calendar_records(records: list[CoachCalendarEvent]) -> list[CoachCalendarEvent]:
    """Calendar reads must never reschedule bookings or delete Graph events."""
    return list(records)


def save_calendar_sync_warning(record: CoachCalendarEvent, *, reason: str) -> CoachCalendarEvent:
    record.last_graph_sync_error = public_graph_sync_warning(reason)
    record.save(update_fields=['last_graph_sync_error', 'updated_at'])
    return record


def build_catchup_note_lines(record: CoachCalendarEvent, target_date: date) -> list[str]:
    lines = [f"Catch-up session managed by the coach. Target date: {format_date(target_date)}."]
    if record.scheduled_date and record.scheduled_time:
        lines.append(
            f"Scheduled for {format_date(record.scheduled_date)} at {record.scheduled_time.strftime('%H:%M')}."
        )
    if clean_text(record.notes):
        lines.append(clean_text(record.notes))
    sync_warning = public_graph_sync_warning(record.last_graph_sync_error)
    if sync_warning:
        lines.append(f"Microsoft sync warning: {sync_warning}")
    return lines


def learner_detail_identity(learner: LearnerProfile | None) -> dict[str, str | None]:
    if learner is None:
        return {"learnerType": None, "enrolmentId": None}

    learner_type = (
        "commercial"
        if clean_text(getattr(learner, "learner_type", None)).casefold() == "commercial"
        else "apprenticeship"
    )
    enrolment_id = clean_text(getattr(learner, "enrolment_id", None))
    return {
        "learnerType": learner_type,
        "enrolmentId": enrolment_id or None,
    }


def build_catchup_template_event(
    learner: LearnerProfile,
    *,
    owner_email: str,
    owner_name: str,
) -> dict:
    target_date = date.today()
    learner_name = clean_text(getattr(learner, "username", None)) or "Unknown learner"
    learner_email = clean_text(getattr(learner, "email", None)) or None
    programme = clean_text(getattr(learner, "programme", None)) or "--"
    cohort = clean_text(getattr(learner, "cohort", None)) or "--"

    return {
        "eventKey": build_catchup_template_event_key(owner_email, int(learner.id)),
        "id": build_catchup_template_event_key(owner_email, int(learner.id)),
        "ownerEmail": owner_email,
        "ownerName": owner_name,
        "learnerId": str(learner.id),
        **learner_detail_identity(learner),
        "learner": learner_name,
        "email": learner_email,
        "programme": programme,
        "cohort": cohort,
        "source": CATCH_UP_EVENT_TYPE,
        "sequence": 1,
        "title": "Catch-up Session",
        "type": "coaching",
        "targetDate": target_date.isoformat(),
        "date": target_date.isoformat(),
        "year": target_date.year,
        "month": target_date.month - 1,
        "dayOfMonth": target_date.day,
        "dayOfWeek": target_date.weekday(),
        "startHour": 9,
        "endHour": 10,
        "durationMinutes": TIMETABLE_DEFAULT_DURATION_MINUTES,
        "timeLabel": "Time TBC",
        "isTimeEstimated": True,
        "priority": "normal",
        "status": CoachCalendarEvent.STATUS_NOT_SCHEDULED,
        "sourceStatus": schedule_status_label(CoachCalendarEvent.STATUS_NOT_SCHEDULED),
        "rawPlanned": target_date.isoformat(),
        "rawStatus": schedule_status_label(CoachCalendarEvent.STATUS_NOT_SCHEDULED),
        "notes": "Coach can create a catch-up session for this learner.",
        "scheduledDate": None,
        "scheduledTime": None,
        "meetingProvider": "",
        "meetingLink": "",
        "graphWebLink": "",
        "platform": "--",
        "location": "--",
        "syncWarning": "",
        "schedulerOnly": True,
    }


def build_catchup_calendar_event(
    record: CoachCalendarEvent,
    *,
    owner_name: str | None = None,
    learner: LearnerProfile | None = None,
    review_type_fields: dict | None = None,
) -> dict:
    """``review_type_fields`` is review_type_fields_by_template()'s batch,
    passed by callers shaping a whole list. A single-event caller can omit it
    and pay for one lookup."""
    event_type = clean_text(record.event_type).lower() or CATCH_UP_EVENT_TYPE
    review_template_id = clean_text(getattr(record, "review_template_id", ""))
    is_curriculum_review = event_type in CURRICULUM_REVIEW_EVENT_TYPES and bool(review_template_id)
    if review_type_fields is None and is_curriculum_review:
        review_type_fields = review_type_fields_by_template([review_template_id])
    record_review_type = (
        (review_type_fields or {}).get(review_template_id) or review_type_event_fields(None)
        if is_curriculum_review else review_type_event_fields(None)
    )
    if is_curriculum_review:
        event_type = review_event_type_for_type_code(record_review_type.get('reviewTypeCode'))
    else:
        # Do not leak malformed linkage from a non-Curriculum booking into the
        # client payload. The stored row remains untouched for reconciliation.
        review_template_id = ""
    event_title = {
        **BOOKED_EVENT_TITLES,
        "live-session": "Live Session",
        "employer-meeting": "Employer Meeting",
        "welfare": "Welfare Session",
        "review": "Review",
    }.get(event_type, event_type.replace("-", " ").title())
    if is_curriculum_review:
        event_title = resolve_review_display_title(event_type, review_template_id)
    target_date = record.target_date or record.scheduled_date or date.today()
    display_date = record.scheduled_date or target_date
    duration_minutes = record.duration_minutes or TIMETABLE_DEFAULT_DURATION_MINUTES
    meeting_provider = clean_text(record.meeting_provider)
    meeting_link = clean_text(record.meeting_link)
    graph_web_link = clean_text(record.graph_web_link)

    start_time = record.scheduled_time
    start_hour = 9
    end_hour = 10
    time_label = "Time TBC"
    is_time_estimated = True
    if start_time:
        start_hour = start_time.hour + (start_time.minute / 60)
        end_hour = start_hour + (duration_minutes / 60)
        time_label = f"{start_time.strftime('%H:%M')} - {duration_minutes} min"
        is_time_estimated = False

    learner_name = clean_text(record.learner_name) or clean_text(getattr(learner, "username", None)) or "Unknown learner"
    learner_email = clean_text(record.learner_email) or clean_text(getattr(learner, "email", None)) or None
    programme = clean_text(getattr(learner, "programme", None)) or "--"
    cohort = clean_text(getattr(learner, "cohort", None)) or "--"
    event_kind = "welfare" if event_type == "student-support" else ("live-session" if event_type == "live-session" else "coaching")
    if event_type in ('review', 'progress-review'):
        event_kind = 'review'
    note_text = (
        " ".join(build_catchup_note_lines(record, target_date))
        if event_type == CATCH_UP_EVENT_TYPE
        else (
            clean_text(record.notes)
            or ("Support session managed by the coach." if event_type == "student-support" else (f"{event_title} booked by the learner." if event_type in LEARNER_BOOKED_EVENT_TYPES else ""))
        )
    )

    from .booking_reservations import review_occupies_slot
    return {
        "eventKey": record.event_key,
        "id": record.event_key,
        "ownerEmail": clean_text(record.owner_email),
        "ownerName": clean_text(owner_name) or clean_text(record.owner_name) or "Med Maher",
        "learnerId": str(record.learner_id),
        **learner_detail_identity(learner),
        "learner": learner_name,
        "email": learner_email,
        "programme": programme,
        "cohort": cohort,
        "source": event_type,
        "sequence": int(record.sequence or 1),
        "title": event_title,
        "reviewTemplateId": review_template_id or None,
        "reviewInstanceId": clean_text(getattr(record, 'review_instance_id', '')) if is_curriculum_review else None,
        "occurrenceNumber": getattr(record, 'occurrence_number', None) or record.sequence,
        # A booked Review keeps its template's classification, so it filters
        # with the unbooked occurrences around it rather than dropping into
        # the generic bucket the moment somebody schedules it.
        **record_review_type,
        "type": event_kind,
        "targetDate": target_date.isoformat(),
        "date": display_date.isoformat(),
        "year": display_date.year,
        "month": display_date.month - 1,
        "dayOfMonth": display_date.day,
        "dayOfWeek": display_date.weekday(),
        "startHour": start_hour,
        "endHour": end_hour,
        "durationMinutes": duration_minutes,
        "timeLabel": time_label,
        "isTimeEstimated": is_time_estimated,
        "priority": generated_event_priority(record.status, target_date, display_date),
        "bookingReserved": review_occupies_slot(record),
        "status": record.status,
        "sourceStatus": schedule_status_label(record.status),
        "rawPlanned": target_date.isoformat(),
        "rawStatus": schedule_status_label(record.status),
        "notes": note_text,
        "scheduledDate": record.scheduled_date.isoformat() if record.scheduled_date else None,
        "scheduledTime": format_time_value(record.scheduled_time),
        "meetingProvider": meeting_provider,
        "meetingLink": meeting_link,
        "graphWebLink": graph_web_link,
        "platform": meeting_provider or ("Microsoft Teams" if meeting_link else "--"),
        "location": "Online" if meeting_link else "--",
        "syncWarning": public_graph_sync_warning(record.last_graph_sync_error),
        "operationId": str(record.operation_id),
        "syncState": record.sync_state,
        "syncAttemptCount": record.sync_attempt_count,
    }


def fetch_calendar_event_records(owner_email: str, event_keys: list[str]) -> dict[str, CoachCalendarEvent]:
    if not event_keys:
        return {}

    records = normalize_calendar_records(
        list(
            CoachCalendarEvent.objects.filter(
                owner_email__iexact=owner_email,
                event_key__in=event_keys,
            )
        )
    )
    result = {record.event_key: record for record in records}
    missing = [key for key in event_keys if key not in result and key.startswith("imported-review:")]
    if missing:
        from .local_mcm_bookings import legacy_mcm_calendar_records
        for key, matches in legacy_mcm_calendar_records(owner_email, missing).items():
            if len(matches) != 1:
                raise DatabaseError("Multiple local bookings are linked to this Monthly Coaching Meeting.")
            result[key] = matches[0]
    return result


def imported_review_calendar_rows(owner_email, event_key, *, lock=False):
    """Keep canonical records and legacy learner bookings on one review."""
    query = CoachCalendarEvent.objects.filter(event_key=event_key)
    if lock:
        query = query.select_for_update()
    records = list(query[:2])
    from .reconciliation_links import records_for_key
    records.extend(records_for_key(event_key, lock=lock))
    from .local_mcm_bookings import legacy_mcm_calendar_records
    # As with canonical keys, return a former coach's exact booking so the
    # caller's ownership check blocks a second meeting after reassignment.
    records.extend(legacy_mcm_calendar_records(None, [event_key], lock=lock).get(event_key, []))
    return list({record.pk: record for record in records}.values())


def fetch_catchup_event_records(owner_email: str) -> list[CoachCalendarEvent]:
    return normalize_calendar_records(
        list(
            CoachCalendarEvent.objects.filter(
                owner_email__iexact=owner_email,
                event_type__iexact=CATCH_UP_EVENT_TYPE,
            ).order_by("scheduled_date", "target_date", "scheduled_time", "learner_name")
        )
    )


def build_catchup_scheduler_events(
    owner_email: str,
    owner_name: str,
    active_rows: list[LearnerProfile],
    learner_profile_map: dict[int, LearnerProfile],
    persisted_records: list[CoachCalendarEvent],
) -> list[dict]:
    scheduler_events = [
        build_catchup_calendar_event(
            record,
            owner_name=owner_name,
            learner=learner_profile_map.get(record.learner_id),
        )
        for record in persisted_records
    ]

    selectable_record_ids: dict[int, bool] = {}
    for record in persisted_records:
        status = clean_text(record.status).lower()
        if status in {CoachCalendarEvent.STATUS_COMPLETED, CoachCalendarEvent.STATUS_IN_PROGRESS, "confirmed"}:
            continue
        selectable_record_ids[record.learner_id] = True

    for learner in active_rows:
        learner_id = int(getattr(learner, "id", 0) or 0)
        if learner_id <= 0 or selectable_record_ids.get(learner_id):
            continue
        scheduler_events.append(
            build_catchup_template_event(
                learner,
                owner_email=owner_email,
                owner_name=owner_name,
            )
        )

    return scheduler_events

def fetch_standalone_event_records(owner_email: str, learner_id: int | None = None) -> list[CoachCalendarEvent]:
    queryset = CoachCalendarEvent.objects.filter(owner_email__iexact=owner_email)
    if learner_id is not None:
        queryset = queryset.filter(learner_id=learner_id)
    return normalize_calendar_records(
        list(
            queryset.filter(
                ~Q(event_type__in=["mcr", "progress-review"])
                | Q(event_type__in=["mcr", "progress-review"], idempotency_key__startswith="learner-book:")
                | ~Q(review_template_id="")
                | Q(event_type__in=["mcr", "progress-review"], status__in=["scheduled", "in-progress", "awaiting-signature", "completed", "cancelled"])
                | Q(event_type__in=["mcr", "progress-review"], scheduled_date__isnull=False, scheduled_time__isnull=False)
            )
            .order_by("scheduled_date", "target_date", "scheduled_time", "learner_name")
        )
    )


def build_generated_calendar_event(
    *,
    learner: LearnerProfile,
    owner_email: str,
    owner_name: str,
    event_type: str,
    sequence: int,
    target_date: date,
    source_row: CommercialUser | EnrolmentUser | SimpleNamespace | dict | None = None,
    employer_attendee: dict[str, str] | None = None,
    review_template_id: str | None = None,
    review_title: str | None = None,
    review_type: dict | None = None,
    occurrence_source: str = curriculum_review_instances.OCCURRENCE_SOURCE_GENERATED,
    manual_addition_id: str | None = None,
) -> dict:
    source = clean_text(event_type) or GENERIC_REVIEW_EVENT_TYPE
    # The display title always comes from the Curriculum Review template that
    # produced this occurrence (review_templates.name, read live) -- never a
    # hard-coded "Monthly Coaching"/"Progress Review" string. The fallback
    # only fires for an occurrence with no resolvable template at all.
    title = clean_text(review_title) or EVENT_TYPE_FALLBACK_TITLES.get(source, "Review")
    employer_attendee = employer_attendee if employer_attendee is not None else (
        learner_employer_attendee(learner, source_row) if source == "progress-review" else {}
    )
    employer_name = clean_text((employer_attendee or {}).get("name"))
    employer_email = clean_email((employer_attendee or {}).get("email"))
    is_manual = occurrence_source == curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL
    if is_manual:
        # Identity-based, date-independent -- never rebuilt from target_date,
        # so editing/rescheduling a learner-specific addition never changes
        # this row's identity. See review_calendar_event_key_manual.
        event_key = curriculum_review_instances.review_calendar_event_key_manual(
            learner.id, review_template_id, manual_addition_id,
        )
    elif review_template_id:
        event_key = curriculum_review_instances.review_calendar_event_key(learner.id, review_template_id, sequence)
    else:
        event_key = build_timetable_event_key(learner.id, event_type, sequence, target_date)
    event = {
        "eventKey": event_key,
        "id": event_key,
        "ownerEmail": owner_email,
        "ownerName": owner_name,
        "learnerId": str(learner.id),
        **learner_detail_identity(learner),
        "learner": clean_text(learner.username) or "Unknown learner",
        "email": clean_text(learner.email) or None,
        "programme": clean_text(learner.programme) or "--",
        "cohort": clean_text(learner.cohort) or "--",
        "source": source,
        "sequence": sequence,
        "title": title,
        "reviewTemplateId": review_template_id,
        "occurrenceNumber": None if is_manual else sequence,
        "occurrenceSource": occurrence_source,
        "occurrenceRef": (review_type or {}).get("occurrenceRef") or (
            f"manual:{manual_addition_id}" if is_manual else f"generated:{sequence}"
        ),
        "additionId": manual_addition_id,
        "reasonCode": (review_type or {}).get("reasonCode") or "",
        "reason": (review_type or {}).get("reason") or "",
        # Classification, carried alongside `source` rather than replacing it.
        # `source` stays the routing bucket every scheduling, Teams, artifact
        # and theming path is keyed on; these say which Curriculum Review Type
        # the occurrence actually is, so the calendar can offer one filter per
        # type instead of collapsing every custom type into "review".
        **review_type_event_fields(review_type),
        "type": "coaching" if source == "mcr" else "review",
        "targetDate": target_date.isoformat(),
        "date": target_date.isoformat(),
        "year": target_date.year,
        "month": target_date.month - 1,
        "dayOfMonth": target_date.day,
        "dayOfWeek": target_date.weekday(),
        "startHour": 9,
        "endHour": 10,
        "durationMinutes": TIMETABLE_DEFAULT_DURATION_MINUTES,
        "timeLabel": "Time TBC",
        "isTimeEstimated": True,
        "priority": "normal",
        "status": CoachCalendarEvent.STATUS_NOT_SCHEDULED,
        "sourceStatus": "Not Scheduled",
        "meetingProvider": "",
        "meetingLink": "",
        "graphWebLink": "",
        "platform": "--",
        "location": "--",
        "notes": (
            f"Added for this learner only. Target date: {format_date(target_date)}."
            if is_manual else
            f"Generated from the Curriculum review schedule using the learner start date. Target date: {format_date(target_date)}."
        ),
        "rawPlanned": target_date.isoformat(),
        "rawStatus": "Not Scheduled",
    }
    if source == "progress-review":
        event["employer"] = employer_name or None
        event["employerName"] = employer_name or None
        event["employerEmail"] = employer_email or None
    return event


def build_booked_calendar_event(record: CoachCalendarEvent) -> dict:
    """Base event dict for a learner-booked session (catch-up / student-support).

    Unlike mcr / progress-review events these have no generated source row — the
    stored record IS the source, so the base event is rebuilt from it and then
    passed through overlay_calendar_record like any generated event.
    """
    title = BOOKED_EVENT_TITLES.get(record.event_type, "Coaching Session")
    template_id = clean_text(getattr(record, 'review_template_id', ''))
    if template_id:
        title = resolve_review_display_title(record.event_type, template_id)
    target_date = record.target_date or date.today()
    is_request = clean_text(record.status).lower() == CoachCalendarEvent.STATUS_NOT_SCHEDULED
    return {
        "eventKey": record.event_key,
        "id": record.event_key,
        "ownerEmail": record.owner_email,
        "ownerName": record.owner_name,
        "learnerId": str(record.learner_id),
        "learnerType": None,
        "enrolmentId": None,
        "learner": clean_text(record.learner_name) or "Unknown learner",
        "email": clean_text(record.learner_email) or None,
        "programme": "--",
        "cohort": "--",
        "source": record.event_type,
        "sequence": record.sequence,
        "title": title,
        "type": BOOKED_EVENT_JSON_TYPES.get(record.event_type, "coaching"),
        "reviewTemplateId": template_id or None,
        "occurrenceNumber": getattr(record, 'occurrence_number', None) or record.sequence,
        "targetDate": target_date.isoformat(),
        "date": target_date.isoformat(),
        "year": target_date.year,
        "month": target_date.month - 1,
        "dayOfMonth": target_date.day,
        "dayOfWeek": target_date.weekday(),
        "startHour": 9,
        "endHour": 10,
        "durationMinutes": record.duration_minutes or TIMETABLE_DEFAULT_DURATION_MINUTES,
        "timeLabel": "Time TBC",
        "isTimeEstimated": True,
        "priority": "normal",
        "status": record.status,
        "sourceStatus": schedule_status_label(record.status),
        "meetingProvider": "",
        "meetingLink": "",
        "graphWebLink": "",
        "platform": "--",
        "location": "--",
        "notes": f"{title} requested by the learner." if is_request else f"{title} booked by the learner.",
        "rawPlanned": target_date.isoformat(),
        "rawStatus": schedule_status_label(record.status),
    }


def event_note_lines(base_event: dict, record: CoachCalendarEvent | None) -> list[str]:
    if base_event.get("source") in LEARNER_BOOKED_EVENT_TYPES:
        is_request = bool(record and clean_text(record.status).lower() == CoachCalendarEvent.STATUS_NOT_SCHEDULED)
        verb = "requested" if is_request else "booked"
        lines = [f"{base_event.get('title') or 'Session'} {verb} by the learner."]
    else:
        lines = [f"Generated from learner start date. Target date: {format_date(parse_date_value(base_event.get('targetDate')))}."]
    if record and record.scheduled_date and record.scheduled_time:
        lines.append(
            f"Scheduled for {format_date(record.scheduled_date)} at {record.scheduled_time.strftime('%H:%M')}."
        )
    if record and clean_text(record.notes):
        lines.append(clean_text(record.notes))
    sync_warning = public_graph_sync_warning(record.last_graph_sync_error) if record else ""
    if sync_warning:
        lines.append(f"Microsoft sync warning: {sync_warning}")
    return lines


def overlay_calendar_record(base_event: dict, record: CoachCalendarEvent | None, imported_overlay=None, *, compact=False) -> dict:
    event = dict(base_event)
    # Aptem's status remains provenance. A verified LMS meeting controls the
    # calendar display status, while sourceStatus keeps the imported value.
    aptem_review = clean_text(base_event.get("reviewSource")).casefold() == "aptem"
    target_date = parse_date_value(base_event.get("targetDate"))
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if target_date is None:
        target_date = date.today()

    if record and record.scheduled_date:
        display_date = record.scheduled_date
    else:
        display_date = target_date

    start_time = record.scheduled_time if record and record.scheduled_time else (
        parse_time_value(base_event.get("scheduledTime")) if aptem_review else None
    )
    duration_minutes = record.duration_minutes if record and record.duration_minutes else TIMETABLE_DEFAULT_DURATION_MINUTES
    start_hour = 9
    end_hour = 10
    time_label = "Time TBC"
    is_time_estimated = True
    if start_time:
        start_hour = start_time.hour + (start_time.minute / 60)
        end_hour = start_hour + (duration_minutes / 60)
        time_label = f"{start_time.strftime('%H:%M')} - {duration_minutes} min"
        is_time_estimated = False

    if aptem_review:
        source_status = clean_text(base_event.get("status")) or CoachCalendarEvent.STATUS_NOT_SCHEDULED
        historical_completed = source_status.casefold() == CoachCalendarEvent.STATUS_COMPLETED
        verified_local_booking = bool(
            not historical_completed and record and record.sync_state == CoachCalendarEvent.SYNC_SYNCED
            and record.graph_event_id and calendar_record_has_launch_url(record)
            and record.status in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS}
        )
        status = record.status if verified_local_booking else source_status
    else:
        status = record.status if record else CoachCalendarEvent.STATUS_NOT_SCHEDULED
    meeting_visible = bool(record and (not aptem_review or historical_completed or verified_local_booking))
    meeting_link = clean_text(record.meeting_link) if meeting_visible else ""
    graph_web_link = clean_text(record.graph_web_link) if meeting_visible else ""
    meeting_provider = clean_text(record.meeting_provider) if meeting_visible else ""

    from .booking_reservations import review_occupies_slot
    event.update(
        {
            "bookingReserved": review_occupies_slot(record),
            "id": base_event["eventKey"],
            "date": display_date.isoformat(),
            "year": display_date.year,
            "month": display_date.month - 1,
            "dayOfMonth": display_date.day,
            "dayOfWeek": display_date.weekday(),
            "startHour": start_hour,
            "endHour": end_hour,
            "durationMinutes": duration_minutes,
            "timeLabel": time_label,
            "isTimeEstimated": is_time_estimated,
            "status": status,
            "sourceStatus": (base_event.get("sourceStatus") or schedule_status_label(source_status)) if aptem_review else schedule_status_label(status),
            "rawStatus": base_event.get("rawStatus") if aptem_review else schedule_status_label(status),
            "scheduledDate": (
                record.scheduled_date.isoformat() if record and record.scheduled_date
                else base_event.get("scheduledDate") if aptem_review else None
            ),
            "scheduledTime": (
                format_time_value(record.scheduled_time) if record and record.scheduled_time
                else base_event.get("scheduledTime") if aptem_review else None
            ),
            "meetingProvider": meeting_provider,
            "meetingLink": meeting_link,
            "graphWebLink": graph_web_link,
            "platform": meeting_provider or ("Microsoft Teams" if meeting_link else "--"),
            "location": "Online" if meeting_link else "--",
            "notes": "" if compact else " ".join(event_note_lines(base_event, record)),
            "reviewResponses": {} if compact else record.review_responses if record and isinstance(record.review_responses, dict) else {},
            "reviewCompletedAt": (
                base_event.get("reviewCompletedAt") if aptem_review
                else record.review_completed_at.isoformat() if record and record.review_completed_at else None
            ),
            # Set once this occurrence is first scheduled (see
            # ensure_review_instance_for_calendar_record) -- the frontend uses
            # its presence to route "open review" to the dynamic
            # Curriculum-driven form instead of any hard-coded one.
            "reviewInstanceId": clean_text(record.review_instance_id) if record else None,
            "managerSignedAt": record.manager_signed_at.isoformat() if record and record.manager_signed_at else None,
            "managerSignedBy": clean_text(record.manager_signed_by) if record else "",
            "priority": generated_event_priority(status, target_date, display_date),
            "syncWarning": public_graph_sync_warning(record.last_graph_sync_error) if record else "",
            "operationId": str(record.operation_id) if record else "",
            "syncState": record.sync_state if record else CoachCalendarEvent.SYNC_PENDING,
            "syncAttemptCount": record.sync_attempt_count if record else 0,
        }
    )
    if aptem_review:
        from .review_sources import apply_imported_state
        event = (apply_imported_state(event, record, imported_overlay, compact=True)
                 if compact else apply_imported_state(event, record, imported_overlay))
        event["priority"] = generated_event_priority(event["status"], target_date, display_date)
        event["platform"] = event.get("meetingProvider") or ("Microsoft Teams" if event.get("meetingLink") else "--")
        event["location"] = "Online" if event.get("meetingLink") else "--"
    return event


def build_graph_event_payload(record: CoachCalendarEvent, base_event: dict) -> dict:
    settings = get_graph_settings()
    if not record.scheduled_date or not record.scheduled_time:
        raise RuntimeError("Scheduled date and time are required before syncing to Microsoft Graph.")

    start_date_time = datetime.combine(record.scheduled_date, record.scheduled_time).replace(second=0, microsecond=0)
    end_date_time = start_date_time + timedelta(minutes=record.duration_minutes or TIMETABLE_DEFAULT_DURATION_MINUTES)
    learner_name = clean_text(record.learner_name) or "Learner"
    learner_email = clean_email(record.learner_email)
    source = clean_text(base_event.get("source")).lower()
    employer_email = clean_email(base_event.get("employerEmail") or base_event.get("managerEmail"))
    employer_name = clean_text(base_event.get("employerName") or base_event.get("employer")) or "Employer"
    title = clean_text(base_event.get("title")) or "Coaching Session"
    coach_name = clean_text(record.owner_name) or "Coach"
    scheduled_date_label = format_date(record.scheduled_date)
    scheduled_time_label = record.scheduled_time.strftime("%H:%M")
    duration_label = f"{record.duration_minutes or TIMETABLE_DEFAULT_DURATION_MINUTES} minutes"
    subject = (f"first session with {coach_name}-{learner_name}"
               if clean_text(record.event_type).lower() == "first-session"
               else f"{title} - {learner_name}")

    details = [
        ("Session", title),
        ("Learner", learner_name),
        ("Coach", coach_name),
        ("Date", scheduled_date_label),
        ("Time", scheduled_time_label),
        ("Duration", duration_label),
    ]
    if source in LEARNER_BOOKED_EVENT_TYPES:
        # Learner-booked copy: lead with who picked the slot and when, while the
        # Graph event itself still belongs to the coach/owner mailbox.
        intro = f"{learner_name} has booked this session for {scheduled_date_label} at {scheduled_time_label}."
        if learner_email:
            details.append(("Learner email", learner_email))
        if clean_text(record.notes):
            details.append(("Notes", clean_text(record.notes)))
    else:
        intro = f"You have been invited to a scheduled {title.lower()}."
        details.append(("Target date", format_date(record.target_date)))
    if source == "progress-review" and employer_email:
        details.append(("Employer attendee", f"{employer_name} ({employer_email})"))

    # Each row is one <p> carrying "Label: value" as a single text flow rather
    # than a <table> with the label and value split across cells. Outlook shows
    # this invitation body as real HTML, but most other mail/calendar clients
    # (Gmail, Google Calendar, Apple Mail) render their own native invite card
    # and only show a plain-text reduction of this body underneath it -- one
    # that a <table> tends to collapse into "LabelValue" with no separator.
    # A colon baked into the text itself keeps that reduction readable too.
    detail_rows = "".join(
        f"<p style=\"margin:0 0 10px;font-size:14px;line-height:1.5;\">"
        f"<span style=\"color:#64748b;\">{escape(label)}:</span> "
        f"<span style=\"color:#0f172a;font-weight:600;\">{escape(value)}</span></p>"
        for label, value in details
        if clean_text(value)
    )
    body_content = (
        "<div style=\"font-family:Segoe UI,Arial,sans-serif;color:#0f172a;\">"
        "<div style=\"max-width:640px;background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;\">"
        "<div style=\"padding:18px 24px;background:#123c69;color:#ffffff;\">"
        "<div style=\"font-size:12px;letter-spacing:1.5px;text-transform:uppercase;opacity:.8;\">KBC LMS</div>"
        f"<div style=\"margin-top:6px;font-size:20px;font-weight:700;\">{escape(title)}</div>"
        "</div>"
        "<div style=\"padding:22px 24px;\">"
        f"<p style=\"margin:0 0 18px;color:#334155;font-size:14px;line-height:1.6;\">{escape(intro)}</p>"
        "<div style=\"padding:12px 0;border-top:1px solid #e2e8f0;border-bottom:1px solid #e2e8f0;\">"
        f"{detail_rows}"
        "</div>"
        "<p style=\"margin:20px 0 0;color:#334155;font-size:13px;line-height:1.6;\">"
        "Please use the Microsoft Teams link included in this invitation to join the meeting.</p>"
        "<p style=\"margin:8px 0 0;color:#64748b;font-size:12px;line-height:1.5;\">"
        "If this time no longer works, please contact your coach before the scheduled start time.</p>"
        "</div>"
        "<div style=\"padding:14px 24px;background:#f8fafc;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:11.5px;\">"
        "Sent via KBC LMS."
        "</div>"
        "</div>"
        "</div>"
    )

    payload = {
        "subject": subject,
        "start": {
            "dateTime": start_date_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": settings["timezone"],
        },
        "end": {
            "dateTime": end_date_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": settings["timezone"],
        },
        "body": {
            "contentType": "HTML",
            "content": body_content,
        },
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
    }
    # Microsoft Graph documents transactionId specifically for suppressing
    # redundant event POSTs after an ambiguous client/network timeout. The
    # durable local operation UUID is stable for every retry of this booking.
    if not clean_text(record.graph_event_id) and record.operation_id:
        payload["transactionId"] = str(record.operation_id)
    attendees = []
    organizer = clean_email(graph_organizer_mailbox(record, base_event))

    def add_required_attendee(email_value: str, name_value: str, *, allow_organizer: bool = False) -> None:
        email = clean_email(email_value)
        if not email or (email == organizer and not allow_organizer):
            return
        if any(clean_email(a["emailAddress"]["address"]) == email for a in attendees):
            return
        attendees.append(
            {
                "emailAddress": {
                    "address": email,
                    "name": clean_text(name_value) or email,
                },
                "type": "required",
            }
        )

    add_required_attendee(learner_email, learner_name)
    if source == "progress-review":
        add_required_attendee(employer_email, employer_name)
    # Graph never emails the organizer, so the coach would otherwise only ever
    # find the session sitting on their calendar -- no invitation, nothing to
    # accept, and nothing to notice if the calendar view is not in front of
    # them. Inviting the coach alongside themselves is what actually sends the
    # mail, and every session type the coach organizes needs that.
    add_required_attendee(record.owner_email, coach_name, allow_organizer=True)

    if attendees:
        payload["attendees"] = attendees
    return payload


def graph_organizer_mailbox(record: CoachCalendarEvent, base_event: dict) -> str:
    """Which mailbox the Graph event is created on -- i.e. who organizes it.

    Graph never emails the organizer; the meeting just shows up on their
    calendar. Only the other attendees get an invite.

    Coach-calendar meetings are always owned by the coach/case owner mailbox.
    The learner (and, for PR, the employer) are invited as attendees.
    """
    if clean_text(record.graph_event_id) and clean_email(record.graph_organizer_email):
        return clean_email(record.graph_organizer_email)
    return clean_email(record.owner_email) or clean_email(record.learner_email)


def sync_calendar_event_to_graph(record: CoachCalendarEvent, base_event: dict) -> str:
    if not has_graph_credentials():
        return TEAMS_SYNC_NOT_CONFIGURED_MESSAGE

    payload = build_graph_event_payload(record, base_event)
    organizer_mailbox = graph_organizer_mailbox(record, base_event)
    owner_key = urllib_parse.quote(organizer_mailbox, safe="")
    # The meeting this booking already had, if any. A reschedule PATCHes only
    # subject/start/end, and Microsoft keeps the same onlineMeeting for it.
    previous_event_id = clean_text(record.graph_event_id)
    previous_join_url = clean_text(record.meeting_link)
    try:
        if clean_text(record.graph_event_id):
            # Preserve the existing Teams body and attendee list on reschedule.
            payload = {key: payload[key] for key in ('subject', 'start', 'end')}
            event_key = urllib_parse.quote(record.graph_event_id, safe="")
            response = microsoft_graph_request(
                "PATCH",
                f"users/{owner_key}/events/{event_key}",
                payload=payload,
            )
            if not response:
                response = microsoft_graph_request(
                    "GET",
                    f"users/{owner_key}/events/{event_key}",
                )
        else:
            response = microsoft_graph_request(
                "POST",
                f"users/{owner_key}/events",
                payload=payload,
            )
    except RuntimeError as exc:
        logger.exception("Unable to sync coach timetable event to Microsoft Graph")
        return public_graph_sync_warning(str(exc))

    response_event_id = clean_text(response.get("id")) or clean_text(record.graph_event_id)
    online_meeting = response.get("onlineMeeting") or {}
    response_web_link = clean_text(response.get("webLink"))
    response_join_url = clean_text(online_meeting.get("joinUrl"))

    if response_event_id and not (response_join_url or response_web_link):
        event_key = urllib_parse.quote(response_event_id, safe="")
        try:
            refreshed_response = microsoft_graph_request(
                "GET",
                f"users/{owner_key}/events/{event_key}",
            )
        except RuntimeError:
            refreshed_response = {}

        if refreshed_response:
            response = refreshed_response
            online_meeting = response.get("onlineMeeting") or {}
            response_event_id = clean_text(response.get("id")) or response_event_id
            response_web_link = clean_text(response.get("webLink"))
            response_join_url = clean_text(online_meeting.get("joinUrl"))

    record.graph_event_id = response_event_id
    record.graph_organizer_email = organizer_mailbox if response_event_id else ""
    record.meeting_provider = "Microsoft Teams" if (response_event_id or response_join_url or response_web_link) else ""
    record.meeting_link = response_join_url or record.meeting_link or response_web_link
    record.graph_web_link = response_web_link or record.graph_web_link

    # The booking is already saved and invited by this point. These options are
    # the difference between a session that records and transcribes itself and one
    # that opens with nothing running. Graph hands out a new online meeting, at the
    # tenant defaults, whenever an event gets one -- so they are applied to a new
    # meeting, or when Microsoft returns a different join link. A reschedule of the
    # same meeting sends no options PATCH: it changed none of them, and re-sending
    # them only turned an intermittent organiser 403 into a warning on a booking
    # that had worked (reapply_coach_teams_lobby repairs older meetings on demand).
    # A deployment without OnlineMeetings.ReadWrite.All and an application access
    # policy is logged, not surfaced: the meeting itself works.
    new_online_meeting = not previous_event_id or response_join_url != previous_join_url
    if response_join_url and new_online_meeting:
        applied, _meeting, option_warnings = apply_teams_meeting_options(
            organizer_mailbox,
            response_join_url,
            recording=COACH_MEETING_RECORDING,
            lobby_bypass=COACH_MEETING_LOBBY_BYPASS,
            attendees=[
                clean_text((attendee.get("emailAddress") or {}).get("address"))
                for attendee in payload.get("attendees") or []
            ],
        )
        if not applied:
            logger.warning(
                "Coach Teams meeting created without its recording options: %s",
                "; ".join(clean_text(warning.get("message")) for warning in option_warnings),
            )
    return ""


# Outcomes of an external Graph calendar deletion, used by
# delete_calendar_event_from_graph_detailed. Kept coarse on purpose: the only
# question a caller ever has is "is the external event gone, and if not, is it
# safe to carry on".
GRAPH_DELETE_SUCCESS = "SUCCESS"            # Graph accepted the DELETE
GRAPH_DELETE_ALREADY_GONE = "ALREADY_GONE"  # 404/410 -- nothing left to cancel
GRAPH_DELETE_NOT_REQUIRED = "NOT_REQUIRED"  # no graph_event_id on the row
GRAPH_DELETE_FAILED = "FAILED"              # auth, permission, network, 5xx


def delete_calendar_event_from_graph_detailed(record: CoachCalendarEvent) -> tuple[str, str]:
    """Cancel one row's external Microsoft event, reporting what happened.

    The single implementation of "remove this event from Microsoft"; both
    delete_calendar_event_from_graph (coach-facing, returns sanitised copy) and
    the cleanup_legacy_reviews management command go through here, so there is
    never a second Graph path to keep in step.

    Returns (outcome, detail). ``detail`` is the RAW Graph diagnostic, which is
    what makes the outcome classifiable at all -- public_graph_sync_warning
    deliberately collapses 404 and 500 into the same coach-facing sentence, so
    a caller that must tell "already deleted" from "server error" cannot use it.

    A missing graph_event_id is NOT_REQUIRED, not a failure: a row can carry a
    meeting_link or graph_web_link with no event id, and there is nothing
    addressable to delete.
    """
    if not clean_text(record.graph_event_id):
        return GRAPH_DELETE_NOT_REQUIRED, ""
    if not has_graph_credentials():
        return GRAPH_DELETE_FAILED, "Microsoft Graph credentials are not configured."

    # The event lives on whichever mailbox organized it. New coach-calendar
    # events use the coach/owner mailbox; older rows may still carry a different
    # graph_organizer_email, so keep respecting the stored organizer. Never
    # inferred from the learner.
    mailbox = clean_text(record.graph_organizer_email) or clean_text(record.owner_email)
    if not mailbox:
        return GRAPH_DELETE_FAILED, "No organizer mailbox stored on this row."
    owner_key = urllib_parse.quote(mailbox, safe="")
    event_key = urllib_parse.quote(record.graph_event_id, safe="")
    try:
        microsoft_graph_request("DELETE", f"users/{owner_key}/events/{event_key}")
    except RuntimeError as exc:
        detail = str(exc)
        # microsoft_graph_request formats HTTPError as "... failed: HTTP <code>; ..."
        if "HTTP 404" in detail or "HTTP 410" in detail or "ErrorItemNotFound" in detail:
            return GRAPH_DELETE_ALREADY_GONE, detail
        logger.exception("Unable to delete coach timetable event from Microsoft Graph")
        return GRAPH_DELETE_FAILED, detail
    return GRAPH_DELETE_SUCCESS, ""


def delete_calendar_event_from_graph(record: CoachCalendarEvent) -> str:
    """Coach-facing wrapper: '' when there is nothing to worry about, else a
    safe sentence. Behaviour is unchanged from before this was split -- an
    already-deleted event still reports no warning, which is what every
    existing caller expects."""
    outcome, detail = delete_calendar_event_from_graph_detailed(record)
    if outcome == GRAPH_DELETE_FAILED:
        return public_graph_sync_warning(detail)
    return ""


COACH_MEETING_ARTIFACT_ENDPOINTS = {
    "transcript": "transcripts",
    "recording": "recordings",
}
COACH_MEETING_ARTIFACTS_RELATION = '"Coach".coach_meeting_artifacts'
COACH_MEETING_ATTENDANCE_REPORTS_RELATION = '"Coach".coach_meeting_attendance_reports'
COACH_MEETING_ATTENDANCE_RELATION = '"Coach".coach_meeting_attendance'
COACH_MEETING_SUMMARIES_RELATION = '"Coach".coach_meeting_summaries'
COACH_MEETING_ATTENDANCE_STATUSES = {"attended", "absent", "pending", "extra"}
COACH_MEETING_SUMMARY_TYPES = {"mcr", "progress-review", GENERIC_REVIEW_EVENT_TYPE}
# Review types whose explicitly mapped Meeting Summary field may receive the
# transcript-generated suggestion.  The underlying Teams/transcript pipeline
# already supports both event types; this set keeps Review-form exposure
# deliberate and aligned with the Curriculum Form Builder validation.
REVIEW_MEETING_SUMMARY_REVIEW_TYPES = {
    curriculum_review_types.REVIEW_TYPE_CODE_MCM,
    curriculum_review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW,
}
COACH_MEETING_SUMMARY_MODEL = getattr(settings, "OPENAI_MEETING_SUMMARY_MODEL", "") or getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
# How much transcript the recap prompt may carry. The previous 18,000 was
# under an hour of speech, so a normal coaching meeting was summarised from
# its opening alone -- the agreed actions and next steps, which are stated at
# the end, never reached the model and nothing said so. This is still a
# fraction of the configured models' context windows (gpt-4.1-mini, gpt-5-mini)
# and the 5 MB upload ceiling above remains the hard input bound.
COACH_MEETING_SUMMARY_TRANSCRIPT_CHARS = 120_000


def graph_datetime_iso(value) -> str:
    parsed = parse_graph_datetime(value)
    if parsed:
        return parsed.isoformat()
    return clean_text(value)


def parse_jsonish(value, fallback):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


class ReviewTemplateUnavailableError(Exception):
    """Raised when a review-driven calendar event cannot be linked to its
    Curriculum review template at the moment it is FIRST scheduled. Callers
    must refuse to leave the event scheduled rather than persist an unlinked
    mcr/progress-review/review row -- see coach_timetable_schedule_event and
    learner_api.calendar.learner_calendar_book's generated-slot branch, which
    both check this before flipping status to scheduled. Rescheduling an
    already-linked row never triggers this: its template is not re-resolved.
    """


class LegacyReviewReconciliationRequiredError(ReviewTemplateUnavailableError):
    code = "LEGACY_REVIEW_RECONCILIATION_REQUIRED"


def require_review_template_for_first_linkage(review_template_id: str) -> dict:
    """Look up the Review Engine template a review-driven event is about to
    be linked to for the first time. Raises ReviewTemplateUnavailableError
    instead of returning falsy so a missing/deleted template can never be
    silently swallowed into a scheduled-but-unlinked row."""
    template_row = curriculum_reviews.get_review_template_row(review_template_id)
    if not template_row:
        raise ReviewTemplateUnavailableError(
            "This review's configured template is no longer available. "
            "Ask your programme team to check the review configuration before scheduling it."
        )
    return template_row


def ensure_review_instance_for_calendar_record(record: CoachCalendarEvent, base_event: dict) -> None:
    """Creates (idempotently) the durable curriculum.review_instances row for
    a Curriculum-driven occurrence the moment it is first scheduled, and
    links the two rows both ways. Scheduling/Teams/calendar state stays on
    CoachCalendarEvent, exactly as before -- this only adds the pointer back
    to the Review definition that produced the occurrence, it does not
    duplicate any scheduling data.

    Only ever called for FIRST-time linkage (record.review_instance_id not
    yet set) -- callers must not call this to "re-ensure" an already-linked
    row, since an already-linked instance must keep working even after its
    template is later archived/deleted.
    """
    if record.status != CoachCalendarEvent.STATUS_SCHEDULED:
        if record.status in {
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            CoachCalendarEvent.STATUS_COMPLETED,
        }:
            raise LegacyReviewReconciliationRequiredError(
                "LEGACY_REVIEW_RECONCILIATION_REQUIRED: this historical review "
                "has already advanced and cannot be given a newly scheduled instance."
            )
        raise ReviewTemplateUnavailableError(
            "A Review Instance can only be linked when the calendar review is scheduled."
        )

    template_row = require_review_template_for_first_linkage(record.review_template_id)
    occurrence_source = (base_event or {}).get('occurrenceSource', curriculum_review_instances.OCCURRENCE_SOURCE_GENERATED)
    is_manual = occurrence_source == curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL
    occurrence_ref = (base_event or {}).get('occurrenceRef') if is_manual else None
    instance = curriculum_review_instances.ensure_review_instance(
        template_row,
        learner_id=record.learner_id,
        learner_kind='',
        programme_id=template_row.get('programme_id'),
        occurrence_number=None if is_manual else (record.occurrence_number or record.sequence),
        target_date=record.target_date,
        coach_email=record.owner_email,
        actor=record.owner_email or 'coach',
        occurrence_source=occurrence_source,
        occurrence_ref=occurrence_ref,
    )
    if not instance:
        # Defensive invariant: a Curriculum-driven event must never be left
        # at status=scheduled without a review_instance_id. This is enforced
        # here rather than as a DB constraint so historical unlinked rows and
        # non-Curriculum event types are unaffected.
        raise ReviewTemplateUnavailableError(
            "This review could not be linked to the Curriculum review engine. Please try scheduling it again."
        )
    record.review_instance_id = instance.get('id')
    record.save(update_fields=["review_instance_id", "review_template_id", "occurrence_number", "updated_at"])
    curriculum_review_instances.link_calendar_event(instance.get('id'), record.pk, actor=record.owner_email or 'coach')
    # This runs at the moment the calendar row itself is actually booked (the
    # caller sets record.status = STATUS_SCHEDULED just before this, then
    # persists it) -- a real date/time now exists, so the linked review
    # instance can leave not-scheduled. Nothing before this point ever wrote
    # review_instances.status at all, so without this the instance would sit
    # at not-scheduled indefinitely and the scheduled -> in-progress
    # attendance transition (mark_review_instance_in_progress_from_attendance)
    # could never engage. Guarded/idempotent in the UPDATE itself.
    curriculum_review_instances.mark_review_instance_scheduled(instance.get('id'), actor=record.owner_email or 'coach')


def resolve_review_display_title(event_type: str, review_template_id: str | None) -> str:
    """The live Curriculum Review name for a stored review_template_id, or the
    legacy fallback label for an old row that predates this linkage."""
    if review_template_id:
        template_row = curriculum_reviews.get_review_template_row(review_template_id, include_deleted=True)
        name = clean_text((template_row or {}).get("name"))
        if name:
            return name
    return EVENT_TYPE_FALLBACK_TITLES.get(clean_text(event_type).lower(), "Monthly Coaching")


def default_meeting_summary_payload(event_type: str = "", review_template_id: str | None = None) -> dict:
    label = resolve_review_display_title(event_type, review_template_id)
    return {
        "title": f"{label} Recap",
        "overview": "",
        "keyPoints": [],
        "actions": [],
        "nextSteps": [],
        "support": [],
    }


def compact_text(value, limit=280) -> str:
    text = " ".join(clean_text(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "..."


def normalized_summary_list(value, *, limit=5, item_limit=180) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = compact_text(item, item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def normalize_meeting_summary_action(value) -> dict | None:
    if isinstance(value, str):
        title = compact_text(value, 180)
        return {"title": title, "owner": "Learner", "dueDate": "", "status": "To do"} if title else None
    if not isinstance(value, dict):
        return None
    title = compact_text(value.get("title") or value.get("action") or value.get("task"), 180)
    if not title:
        return None
    owner = compact_text(value.get("owner") or value.get("responsible") or "Learner", 60)
    status = compact_text(value.get("status") or "To do", 40)
    due_date = compact_text(value.get("dueDate") or value.get("due") or "", 40)
    return {
        "title": title,
        "owner": owner or "Learner",
        "dueDate": due_date,
        "status": status or "To do",
    }


def normalize_meeting_summary_payload(payload, event_type: str = "") -> dict:
    base = default_meeting_summary_payload(event_type)
    if not isinstance(payload, dict):
        return base
    title = compact_text(payload.get("title"), 100) or base["title"]
    overview = compact_text(payload.get("overview") or payload.get("summary"), 500)
    actions = []
    for item in payload.get("actions") or payload.get("agreedActions") or []:
        action = normalize_meeting_summary_action(item)
        if action:
            actions.append(action)
        if len(actions) >= 6:
            break
    return {
        "title": title,
        "overview": overview,
        "keyPoints": normalized_summary_list(payload.get("keyPoints") or payload.get("discussionPoints"), limit=6),
        "actions": actions,
        "nextSteps": normalized_summary_list(payload.get("nextSteps"), limit=5),
        "support": normalized_summary_list(payload.get("support") or payload.get("supportNeeded"), limit=4),
    }


def meeting_summary_plain_text(summary: dict) -> str:
    parts = []
    title = clean_text(summary.get("title"))
    overview = clean_text(summary.get("overview"))
    if title:
        parts.append(title)
    if overview:
        parts.append(overview)
    for heading, key in (
        ("Key points", "keyPoints"),
        ("Actions", "actions"),
        ("Next steps", "nextSteps"),
        ("Support", "support"),
    ):
        values = summary.get(key) or []
        if not values:
            continue
        parts.append(heading)
        for item in values:
            if isinstance(item, dict):
                text = clean_text(item.get("title"))
                owner = clean_text(item.get("owner"))
                due = clean_text(item.get("dueDate"))
                if owner or due:
                    text = f"{text} ({', '.join(part for part in [owner, due] if part)})"
            else:
                text = clean_text(item)
            if text:
                parts.append(f"- {text}")
    return "\n".join(parts)


def coach_meeting_artifact_record(owner_email: str, event_key: str) -> CoachCalendarEvent | None:
    if event_key.startswith("imported-review:"):
        return fetch_calendar_event_records(owner_email, [event_key]).get(event_key)
    return CoachCalendarEvent.objects.filter(
        owner_email__iexact=owner_email,
        event_key=event_key,
    ).first()


def coach_meeting_graph_target(record: CoachCalendarEvent) -> tuple[str, dict | None]:
    """Return the Graph onlineMeeting path for a scheduled coach calendar event."""
    organizer = clean_text(record.graph_organizer_email) or clean_text(record.owner_email)
    join_url = clean_text(record.meeting_link)
    meeting_id = ""
    graph_event_id = clean_text(record.graph_event_id)

    # Some old rows only kept the Outlook web link as meeting_link. The calendar
    # event still knows its onlineMeeting, so ask Graph for the Teams join URL
    # before trying to resolve the onlineMeeting by joinWebUrl.
    if graph_event_id and (not join_url or "teams.microsoft" not in join_url.casefold()):
        event_key = urllib_parse.quote(graph_event_id, safe="")
        owner_key = urllib_parse.quote(organizer, safe="")
        event = microsoft_graph_request("GET", f"users/{owner_key}/events/{event_key}")
        online_meeting = event.get("onlineMeeting") or {}
        join_url = clean_text(online_meeting.get("joinUrl")) or join_url
        meeting_id = clean_text(online_meeting.get("id"))

    if not organizer or not join_url:
        return "", {
            "detail": "This event does not have a Teams meeting link yet.",
            "code": "coach_meeting_link_missing",
        }

    if not meeting_id:
        graph_meeting = teams_online_meeting_from_join_url(organizer, join_url)
        meeting_id = clean_text(graph_meeting.get("id"))

    if not meeting_id:
        return "", {
            "detail": (
                "The Teams meeting exists, but Microsoft Graph cannot resolve its online meeting ID yet. "
                "Check OnlineMeetings.Read.All and the application access policy for the meeting organizer."
            ),
            "code": "coach_online_meeting_unresolved",
        }

    return teams_meeting_base_path(organizer, meeting_id, join_url), None


def serialize_coach_meeting_artifact(artifact_type: str, artifact: dict) -> dict:
    graph_artifact_id = clean_text(artifact.get("id"))
    return {
        "id": graph_artifact_id,
        "artifact_type": artifact_type,
        "graph_artifact_id": graph_artifact_id,
        "call_id": clean_text(artifact.get("callId")),
        "content_correlation_id": clean_text(artifact.get("contentCorrelationId")),
        "created_datetime": graph_datetime_iso(artifact.get("createdDateTime")),
        "end_datetime": graph_datetime_iso(artifact.get("endDateTime")),
        "metadata": artifact,
    }


def public_coach_meeting_artifact(artifact: dict) -> dict:
    public = {
        key: value
        for key, value in artifact.items()
        if key not in {
            "transcript_vtt",
            "transcript_text",
            "transcript_content_type",
            "transcript_fetched_at",
            "transcript_fetch_error",
        }
    }
    metadata = public.get("metadata")
    if isinstance(metadata, dict) and "_lms_booking_fingerprint" in metadata:
        public["metadata"] = {key: value for key, value in metadata.items()
                               if key != "_lms_booking_fingerprint"}
    return public


def coach_meeting_attendance_seconds(record: dict) -> int:
    if not isinstance(record, dict):
        return 0
    total = parse_int(record.get("totalAttendanceInSeconds"), 0)
    if total:
        return max(total, 0)
    return max(attendance_interval_seconds(record.get("attendanceIntervals")), 0)


def serialize_coach_meeting_attendance_record(report_id: str, record: dict, index: int) -> dict:
    if not isinstance(record, dict):
        record = {}
    display_name, identity_id = attendance_identity(record)
    display_name = display_name or attendance_display_name(record)
    intervals = record.get("attendanceIntervals")
    if not isinstance(intervals, list):
        intervals = []
    total_seconds = coach_meeting_attendance_seconds(record)
    email = clean_text(record.get("emailAddress") or record.get("email")).lower()
    graph_record_id = clean_text(record.get("id") or identity_id or email)
    return {
        "id": graph_record_id or f"{report_id}:{index}",
        "reportId": report_id,
        "graphRecordId": graph_record_id,
        "email": email,
        "displayName": display_name or email or "Unknown attendee",
        "role": clean_text(record.get("role")),
        "totalAttendanceSeconds": total_seconds,
        "attended": total_seconds > 0,
        "intervals": intervals,
    }


def serialize_coach_meeting_attendance_report(report: dict, detail: dict) -> dict:
    report_id = clean_text((detail or {}).get("id") or (report or {}).get("id"))
    records = (detail or {}).get("attendanceRecords") or []
    if not isinstance(records, list):
        records = []
    serialized_records = [
        serialize_coach_meeting_attendance_record(report_id, record, index)
        for index, record in enumerate(records, start=1)
    ]
    attended = sum(1 for record in serialized_records if record["attended"])
    return {
        "id": report_id,
        "meetingStartDateTime": graph_datetime_iso(
            (detail or {}).get("meetingStartDateTime") or (report or {}).get("meetingStartDateTime")
        ),
        "meetingEndDateTime": graph_datetime_iso(
            (detail or {}).get("meetingEndDateTime") or (report or {}).get("meetingEndDateTime")
        ),
        "totalParticipantCount": parse_int(
            (detail or {}).get("totalParticipantCount") or (report or {}).get("totalParticipantCount"),
            len(serialized_records),
        ),
        "records": serialized_records,
        "attendedCount": attended,
        "absentCount": max(len(serialized_records) - attended, 0),
    }


def coach_meeting_record_learner(record: CoachCalendarEvent) -> LearnerProfile | None:
    from .review_sources import meeting_profile
    try:
        return meeting_profile(record)
    except DatabaseError:
        return None


def coach_meeting_expected_attendees(record: CoachCalendarEvent) -> list[dict]:
    """Business-level people expected in a coach meeting.

    The organizer is expected to host the meeting even though Microsoft Graph
    does not put them in the event's attendee list. Required attendees are then
    inferred from the same rules used when creating the Teams calendar event:
    learner for every coach meeting, and employer/line manager for PR.
    """

    expected: list[dict] = []
    seen: set[str] = set()
    source = clean_text(record.event_type).lower()
    organizer_email = clean_email(record.graph_organizer_email or record.owner_email)
    learner = None

    def add_expected(role: str, name_value: str, email_value: str) -> None:
        email = clean_email(email_value)
        name = clean_text(name_value)
        if not email and not name:
            return
        key = email or f"{role}:{normalize_person_name(name)}"
        if key in seen:
            return
        seen.add(key)
        expected.append(
            {
                "id": f"{role}:{key}",
                "role": role,
                "name": name or email,
                "displayName": name or email,
                "email": email,
                "required": True,
            }
        )

    add_expected("coach", clean_text(record.owner_name) or "Coach", organizer_email)

    learner_email = clean_email(record.learner_email)
    learner_name = clean_text(record.learner_name)
    if (source == "progress-review" and getattr(record, "learner_id", None)) or not learner_email or not learner_name:
        learner = coach_meeting_record_learner(record)
        if learner is not None:
            learner_email = learner_email or clean_email(getattr(learner, "email", None))
            learner_name = learner_name or clean_text(getattr(learner, "full_name", None))
    add_expected("learner", learner_name or "Learner", learner_email)

    if source == "progress-review":
        if learner is None:
            learner = coach_meeting_record_learner(record)
        if learner is not None:
            try:
                source_row = resolve_caseload_source_row(learner)
            except DatabaseError:
                source_row = None
            employer_attendee = learner_employer_attendee(
                learner,
                source_row,
            )
        else:
            employer_attendee = {}
        add_expected(
            "employer",
            clean_text(employer_attendee.get("name")) or "Employer",
            clean_email(employer_attendee.get("email")),
        )

    return expected


def coach_attendance_match_value(value: str | None) -> str:
    email = clean_email(value)
    if email:
        return f"email:{email}"
    name = normalize_person_name(value)
    return f"name:{name}" if name else ""


def coach_attendance_record_matches_expected(record: dict, expected: dict) -> bool:
    expected_email = clean_email(expected.get("email"))
    record_email = clean_email(record.get("email"))
    if expected_email and record_email and expected_email == record_email:
        return True
    expected_name = normalize_person_name(expected.get("displayName") or expected.get("name"))
    record_name = normalize_person_name(record.get("displayName"))
    return bool(expected_name and record_name and expected_name == record_name)


def coach_attendance_record_confirms_expected(record: dict, expected: dict) -> bool:
    """Stricter than coach_attendance_record_matches_expected above.

    That function is used to build the attendance-tracker UI (present the
    coach with a best-effort row per expected attendee), and deliberately
    falls back to a normalized display-name match when email is missing --
    reasonable for a display hint, not for deciding a review's lifecycle.

    This one is the only thing allowed to say "the coach/learner genuinely
    attended" for the purpose of the scheduled -> in-progress transition
    (apply_teams_attendance_status_transition). No Microsoft/Entra object id
    is stored for any coach or learner account today (that would need a new
    field/migration, out of scope here), so a verified email match is the
    strongest identity available without one -- a name-only match is never
    sufficient, since two different people can share a display name.
    """
    expected_email = clean_email(expected.get("email"))
    record_email = clean_email(record.get("email"))
    return bool(expected_email and record_email and expected_email == record_email)


def _review_instance_confirmed_join_at(record: CoachCalendarEvent, attendance_records: list[dict]) -> datetime | None:
    """Earliest verified-email join time among this row's expected coach/
    learner with positive attendance duration, from already-fetched Graph
    attendance records -- or None. A zero-length interval is not evidence
    that somebody actually entered the meeting.

    Only the coach and learner roles count as "expected participants" for
    this transition (an employer/referrer joining a Progress Review does not,
    on its own, mean the coach/learner's meeting has started). Reuses
    coach_meeting_expected_attendees, the same attendee resolution the
    attendance-tracker UI already relies on.
    """
    expected_roles = {"coach", "learner"}
    expected_attendees = [
        attendee for attendee in coach_meeting_expected_attendees(record)
        if attendee.get("role") in expected_roles
    ]
    if not expected_attendees:
        return None

    earliest: datetime | None = None
    for attendee in expected_attendees:
        for attendance_record in attendance_records or []:
            if not isinstance(attendance_record, dict):
                continue
            if not coach_attendance_record_confirms_expected(attendance_record, attendee):
                continue
            record_has_positive_duration = parse_int(
                attendance_record.get("totalAttendanceSeconds"), 0,
            ) > 0
            for interval in attendance_record.get("intervals") or []:
                if not isinstance(interval, dict):
                    continue
                join_at = parse_graph_datetime(interval.get("joinDateTime"))
                if join_at is None:
                    continue
                leave_at = parse_graph_datetime(interval.get("leaveDateTime"))
                interval_has_positive_duration = bool(
                    leave_at is not None and leave_at > join_at
                )
                if not record_has_positive_duration and not interval_has_positive_duration:
                    continue
                if join_at.tzinfo is not None:
                    join_at = join_at.astimezone(timezone.UTC).replace(tzinfo=None)
                if earliest is None or join_at < earliest:
                    earliest = join_at
    return earliest


def apply_teams_attendance_status_transition(record: CoachCalendarEvent, attendance_records: list[dict]) -> bool:
    """The only path by which a linked MCM/Progress Review's canonical status
    (review_instances.status) can move scheduled -> in-progress. Reflects a
    real Microsoft Teams join by the expected coach or learner -- never that
    a form was opened, an answer was saved, "Start" was clicked, or the
    scheduled time passed.

    Deliberately takes already-fetched attendance records rather than calling
    Microsoft Graph itself: every caller (coach_timetable_event_artifacts,
    learner_calendar_event_artifacts, the sync_coach_meeting_snapshots
    management command) already resolved this exact linked Teams meeting via
    coach_meeting_graph_target/fetch_coach_meeting_graph_snapshot and fetched
    its attendanceReports for its own reasons (the artifacts panel, the
    snapshot sync) -- this reuses that result rather than issuing a second
    Graph call or a second meeting-identity resolution.

    A no-op for any row that is not linked to a review_instances row (catch-
    up/student-support, or a historical mcr/progress-review row scheduled
    before Curriculum templates existed) -- Live Sessions are never routed
    through this function at all, since they are not CoachCalendarEvent rows.

    Returns True if a transition actually happened.
    """
    if not clean_text(getattr(record, "review_instance_id", "")):
        return False
    joined_at = _review_instance_confirmed_join_at(record, attendance_records)
    if joined_at is None:
        return False
    from .reconciliation_links import for_calendar
    # Imported continuation has its own explicit start/submit/sign lifecycle.
    # Attendance remains stored against this same meeting, never the Native form.
    if for_calendar(record):
        return False
    updated = curriculum_review_instances.mark_review_instance_in_progress_from_attendance(
        record.review_instance_id, started_at=joined_at, actor="teams-attendance-sync",
    )
    if not updated:
        return False
    _sync_calendar_record_to_review_instance_status(updated)
    return True


def build_coach_meeting_attendance_tracker(
    expected_attendees: list[dict],
    actual_records: list[dict],
    *,
    has_report: bool,
) -> dict:
    tracker_rows = []
    matched_actual_indexes: set[int] = set()

    for expected in expected_attendees:
        matching_indexes = [
            index
            for index, record in enumerate(actual_records)
            if coach_attendance_record_matches_expected(record, expected)
        ]
        matched_actual_indexes.update(matching_indexes)
        matching_records = [actual_records[index] for index in matching_indexes]
        attended = any(record.get("attended") for record in matching_records)
        total_seconds = sum(
            max(parse_int(record.get("totalAttendanceSeconds"), 0), 0)
            for record in matching_records
        )
        actual_display_name = next(
            (clean_text(record.get("displayName")) for record in matching_records if clean_text(record.get("displayName"))),
            "",
        )
        status = "attended" if attended else ("absent" if has_report else "pending")
        tracker_rows.append(
            {
                "id": clean_text(expected.get("id")) or coach_attendance_match_value(expected.get("email") or expected.get("displayName")),
                "role": clean_text(expected.get("role")) or "attendee",
                "name": clean_text(expected.get("displayName") or expected.get("name")),
                "displayName": clean_text(expected.get("displayName") or expected.get("name")),
                "email": clean_email(expected.get("email")),
                "expected": True,
                "required": bool(expected.get("required", True)),
                "attended": attended,
                "status": status,
                "totalAttendanceSeconds": total_seconds,
                "actualDisplayName": actual_display_name,
                "actualRecordIds": [
                    clean_text(record.get("id"))
                    for record in matching_records
                    if clean_text(record.get("id"))
                ],
                "reportIds": sorted(
                    {
                        clean_text(record.get("reportId"))
                        for record in matching_records
                        if clean_text(record.get("reportId"))
                    }
                ),
            }
        )

    extra_rows = []
    for index, record in enumerate(actual_records):
        if index in matched_actual_indexes or not record.get("attended"):
            continue
        email = clean_email(record.get("email"))
        display_name = clean_text(record.get("displayName")) or email or "Unknown attendee"
        extra_rows.append(
            {
                "id": clean_text(record.get("id")) or f"extra:{index + 1}",
                "role": clean_text(record.get("role")) or "attendee",
                "name": display_name,
                "displayName": display_name,
                "email": email,
                "expected": False,
                "required": False,
                "attended": True,
                "status": "extra",
                "totalAttendanceSeconds": max(parse_int(record.get("totalAttendanceSeconds"), 0), 0),
                "actualDisplayName": display_name,
                "actualRecordIds": [clean_text(record.get("id"))] if clean_text(record.get("id")) else [],
                "reportIds": [clean_text(record.get("reportId"))] if clean_text(record.get("reportId")) else [],
            }
        )

    expected_attended_count = sum(1 for row in tracker_rows if row["status"] == "attended")
    expected_absent_count = sum(1 for row in tracker_rows if row["status"] == "absent")
    expected_pending_count = sum(1 for row in tracker_rows if row["status"] == "pending")
    return {
        "tracker": tracker_rows + extra_rows,
        "extraAttendees": extra_rows,
        "expectedCount": len(expected_attendees),
        "expectedAttendedCount": expected_attended_count,
        "expectedAbsentCount": expected_absent_count,
        "expectedPendingCount": expected_pending_count,
        "extraCount": len(extra_rows),
    }


def coach_meeting_snapshot_json(value, fallback):
    return json.dumps(value if value is not None else fallback, default=str)


def coach_meeting_snapshot_datetime(value):
    return parse_graph_datetime(value) if value else None


# Teams writes each spoken line as ``<v Speaker Name>text</v>``. The optional
# ``.class`` suffixes belong to the tag, not to the name.
VTT_VOICE_SPAN_RE = re.compile(r"<v(?:\.[^\s>]+)*\s+([^>]*)>", re.IGNORECASE)


def coach_meeting_transcript_text(vtt_content: str) -> str:
    """Return a readable, speaker-attributed transcript body from WebVTT.

    A cue is ``[identifier] / timing / payload``, and only the payload is
    speech. Tracking that structure matters for a real Teams export:

    * Teams writes a GUID as the cue identifier. Treating it as speech (as
      dropping only all-digit lines did) filled the prompt with identifiers
      instead of conversation, which mattered because the prompt is capped.
    * The header block after ``WEBVTT`` (``Kind:``, ``Language:``) is not
      speech either, and sits outside every cue.

    Speaker names are kept: a recap built from an unattributed wall of text
    cannot tell what the coach committed to from what the learner did, so it
    attributes actions to the wrong person.
    """
    lines = []
    in_payload = False
    for raw_line in clean_text(vtt_content).splitlines():
        line = raw_line.strip()
        if not line:
            # A blank line closes the current cue; anything before the next
            # timing line is an identifier or a block we do not read.
            in_payload = False
            continue
        upper = line.upper()
        if upper.startswith("WEBVTT") or upper.startswith("NOTE"):
            in_payload = False
            continue
        if "-->" in line:
            in_payload = True
            continue
        if not in_payload:
            continue
        voice = VTT_VOICE_SPAN_RE.search(line)
        speaker = clean_text(voice.group(1)) if voice else ""
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n".join(lines)


def uploaded_coach_meeting_transcript_text(upload) -> str:
    """Validate and extract a coach-supplied WebVTT summary source.

    Uploaded fallback transcripts are processed in memory. They are never
    stored as, or confused with, Microsoft Teams transcript artifacts.
    """
    filename = clean_text(getattr(upload, "name", ""))
    if not filename.lower().endswith(".vtt"):
        raise ValueError("Upload a WebVTT transcript with a .vtt file extension.")
    if int(getattr(upload, "size", 0) or 0) > COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES:
        raise ValueError("The transcript is too large. Upload a .vtt file no larger than 5 MB.")

    content = upload.read(COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES + 1)
    if not content or len(content) > COACH_MEETING_TRANSCRIPT_UPLOAD_MAX_BYTES:
        raise ValueError("The transcript is empty or larger than the 5 MB limit.")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("The .vtt transcript must use UTF-8 text encoding.") from exc
    if not decoded.lstrip().upper().startswith("WEBVTT"):
        raise ValueError("The uploaded file is not a valid WebVTT transcript.")

    transcript_text = coach_meeting_transcript_text(decoded)
    if not transcript_text:
        raise ValueError("The uploaded .vtt file does not contain any transcript text.")
    return transcript_text


def fetch_coach_meeting_transcript_content(base: str, artifact_id: str, *, safe_diagnostics=False) -> dict:
    graph_settings = get_graph_settings()
    path = (
        f"{base}/transcripts/"
        f"{urllib_parse.quote(clean_text(artifact_id), safe='')}/content"
    )
    url = f'{graph_settings["base_url"].rstrip("/")}/{path}'
    request_headers = {
        "Authorization": f"Bearer {microsoft_graph_token()}",
        "Accept": "text/vtt",
    }
    graph_request = urllib_request.Request(url, headers=request_headers, method="GET")
    try:
        graph_response = urllib_request.urlopen(graph_request, timeout=45)
        content_type = graph_response.headers.get("Content-Type") or "text/vtt"
        content = graph_response.read()
        graph_response.close()
    except urllib_error.HTTPError as exc:
        if safe_diagnostics:
            logger.warning("APTEM resource refresh: transcript content unavailable (HTTP %s).", exc.code)
        else:
            detail = exc.read().decode("utf-8", errors="ignore")
            logger.warning("Unable to fetch coach Teams transcript content: %s %s", exc.code, detail)
        return {
            "transcript_vtt": "",
            "transcript_text": "",
            "transcript_content_type": "",
            "transcript_fetch_error": f"Microsoft Graph returned {exc.code} for transcript content.",
        }
    except urllib_error.URLError as exc:
        if safe_diagnostics:
            logger.warning("APTEM resource refresh: transcript content unavailable (network).")
        else:
            logger.warning("Unable to fetch coach Teams transcript content: %s", exc)
        return {
            "transcript_vtt": "",
            "transcript_text": "",
            "transcript_content_type": "",
            "transcript_fetch_error": "Microsoft Graph could not return the transcript content.",
        }

    transcript_vtt = content.decode("utf-8", errors="replace")
    return {
        "transcript_vtt": transcript_vtt,
        "transcript_text": coach_meeting_transcript_text(transcript_vtt),
        "transcript_content_type": clean_text(content_type),
        "transcript_fetched_at": timezone.now().isoformat(),
        "transcript_fetch_error": "",
    }


def coach_meeting_snapshot_tables_ready(database: str) -> bool:
    try:
        connection = connections[database]
        with connection.cursor() as cursor:
            cursor.execute(
                "select to_regclass(%s), to_regclass(%s), to_regclass(%s)",
                [
                    COACH_MEETING_ARTIFACTS_RELATION,
                    COACH_MEETING_ATTENDANCE_REPORTS_RELATION,
                    COACH_MEETING_ATTENDANCE_RELATION,
                ],
            )
            result = cursor.fetchone()
        return bool(result and all(result))
    except Exception:
        return False


def coach_meeting_artifact_transcript_columns_ready(database: str) -> bool:
    try:
        connection = connections[database]
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select count(*)
                from information_schema.columns
                where table_schema = 'Coach'
                  and table_name = 'coach_meeting_artifacts'
                  and column_name in (
                    'transcript_vtt',
                    'transcript_text',
                    'transcript_content_type',
                    'transcript_fetched_at',
                    'transcript_fetch_error'
                  )
                """
            )
            result = cursor.fetchone()
        return bool(result and result[0] == 5)
    except Exception:
        return False


def coach_meeting_summaries_table_ready(database: str) -> bool:
    try:
        connection = connections[database]
        with connection.cursor() as cursor:
            cursor.execute("select to_regclass(%s)", [COACH_MEETING_SUMMARIES_RELATION])
            result = cursor.fetchone()
        return bool(result and result[0])
    except Exception:
        return False


def stored_coach_meeting_summary(record: CoachCalendarEvent) -> dict | None:
    database = router.db_for_read(CoachCalendarEvent) or "default"
    if not coach_meeting_summaries_table_ready(database):
        return None
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT current_summary, status, generated_at, edited_at, edited_by, model, error,
                       graph_event_id, transcript_artifact_id, transcript_hash
                FROM {COACH_MEETING_SUMMARIES_RELATION}
                WHERE event_key = %s AND calendar_event_id = %s AND owner_email = %s
                  AND learner_id IS NOT DISTINCT FROM %s
                LIMIT 1
                """,
                [record.event_key, record.pk, clean_email(record.owner_email), record.learner_id],
            )
            row = cursor.fetchone()
    except Exception:
        logger.exception("Unable to read coach meeting summary for event_key=%s", record.event_key)
        return None
    if not row:
        return None
    summary = parse_jsonish(row[0], {})
    from .migrated_session_scope import session_provenance
    if clean_text(row[7]) != clean_text(record.graph_event_id):
        return None
    if summary.get('_lmsSession') and summary['_lmsSession'] != session_provenance(record):
        return None
    transcript = stored_coach_meeting_transcript_for_summary(record)
    if (not transcript or clean_text(row[8]) != transcript['artifactId']
            or clean_text(row[9]) != hashlib.sha256(transcript['text'].encode('utf-8')).hexdigest()):
        return None
    return {
        "summary": normalize_meeting_summary_payload(summary, record.event_type),
        "status": clean_text(row[1]) or "ready",
        "generatedAt": row[2].isoformat() if row[2] else None,
        "editedAt": row[3].isoformat() if row[3] else None,
        "editedBy": clean_text(row[4]),
        "model": clean_text(row[5]),
        "error": clean_text(row[6]),
    }


def stored_coach_meeting_snapshot(record: CoachCalendarEvent, *, database=None, safe_diagnostics=False) -> dict:
    """Return the latest persisted Teams snapshot without contacting Graph."""

    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    expected_attendees = coach_meeting_expected_attendees(record)
    empty_tracker = build_coach_meeting_attendance_tracker(
        expected_attendees,
        [],
        has_report=False,
    )
    empty_attendance = {
        "reports": [],
        "records": [],
        "expectedAttendees": expected_attendees,
        "reportCount": 0,
        "attendedCount": 0,
        "absentCount": 0,
        "participantCount": 0,
        **empty_tracker,
    }
    empty_snapshot = {
        "attendance": empty_attendance,
        "artifacts": [],
        "attendanceReports": [],
        "attendanceTracker": empty_tracker,
        "errors": [],
        "partial": False,
        "storage": {"stored": False, "reason": "snapshot_tables_missing"},
    }
    if not coach_meeting_snapshot_tables_ready(database):
        return empty_snapshot

    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    artifact_type,
                    graph_artifact_id,
                    call_id,
                    content_correlation_id,
                    created_datetime,
                    end_datetime,
                    metadata,
                    last_seen_at,
                    graph_event_id
                FROM {COACH_MEETING_ARTIFACTS_RELATION}
                WHERE event_key = %s
                ORDER BY COALESCE(end_datetime, created_datetime) DESC NULLS LAST, updated_at DESC
                """,
                [record.event_key],
            )
            artifact_rows = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT
                    graph_report_id,
                    meeting_start_datetime,
                    meeting_end_datetime,
                    total_participant_count,
                    attended_count,
                    absent_count,
                    metadata,
                    last_seen_at
                FROM {COACH_MEETING_ATTENDANCE_REPORTS_RELATION}
                WHERE event_key = %s AND graph_event_id = %s
                ORDER BY COALESCE(meeting_end_datetime, meeting_start_datetime) DESC NULLS LAST, updated_at DESC
                """,
                [record.event_key, record.graph_event_id],
            )
            report_rows = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT
                    participant_key,
                    role,
                    display_name,
                    email,
                    expected,
                    required,
                    attended,
                    status,
                    total_attendance_seconds,
                    actual_display_name,
                    actual_record_ids,
                    report_ids,
                    raw_data,
                    synced_at
                FROM {COACH_MEETING_ATTENDANCE_RELATION}
                WHERE event_key = %s
                  AND is_current = true
                ORDER BY expected DESC, role ASC, display_name ASC, id ASC
                """,
                [record.event_key],
            )
            attendance_rows = cursor.fetchall()
    except Exception:
        if safe_diagnostics:
            logger.warning("APTEM resource refresh: stored snapshot unavailable.")
        else:
            logger.exception("Unable to read stored coach Teams snapshot for event_key=%s", record.event_key)
        snapshot = dict(empty_snapshot)
        snapshot["errors"] = ["Stored Teams artifacts could not be loaded."]
        snapshot["partial"] = True
        return snapshot

    artifacts = []
    latest_sync = None
    for row in artifact_rows:
        if row[7] and (latest_sync is None or row[7] > latest_sync):
            latest_sync = row[7]
        artifact_id = clean_text(row[1])
        artifacts.append(
            {
                "id": artifact_id,
                "artifact_type": clean_text(row[0]),
                "graph_artifact_id": artifact_id,
                "call_id": clean_text(row[2]),
                "content_correlation_id": clean_text(row[3]),
                "created_datetime": row[4].isoformat() if row[4] else "",
                "end_datetime": row[5].isoformat() if row[5] else "",
                "metadata": {**parse_jsonish(row[6], {}), '_lms_graph_event_id': clean_text(row[8])},
            }
        )

    attendance_reports = []
    records = []
    for row in report_rows:
        if row[7] and (latest_sync is None or row[7] > latest_sync):
            latest_sync = row[7]
        metadata = parse_jsonish(row[6], {})
        report_id = clean_text(row[0])
        report_records = metadata.get("records") if isinstance(metadata, dict) else []
        if not isinstance(report_records, list):
            report_records = []
        for record_row in report_records:
            if isinstance(record_row, dict):
                records.append(record_row)
        attendance_reports.append(
            {
                "id": report_id,
                "meetingStartDateTime": row[1].isoformat() if row[1] else "",
                "meetingEndDateTime": row[2].isoformat() if row[2] else "",
                "totalParticipantCount": max(parse_int(row[3], 0), 0),
                "attendedCount": max(parse_int(row[4], 0), 0),
                "absentCount": max(parse_int(row[5], 0), 0),
                "records": report_records,
                **({'_lms_verified_online_meeting_id': metadata['_lms_verified_online_meeting_id'],
                    '_lms_graph_event_id': record.graph_event_id,
                    **({'_lms_booking_fingerprint': metadata['_lms_booking_fingerprint']}
                       if metadata.get('_lms_booking_fingerprint') else {})}
                   if isinstance(metadata, dict) and metadata.get('_lms_verified_online_meeting_id') else {}),
            }
        )

    tracker_rows = []
    for row in attendance_rows:
        if row[13] and (latest_sync is None or row[13] > latest_sync):
            latest_sync = row[13]
        raw_data = parse_jsonish(row[12], {})
        if isinstance(raw_data, dict):
            tracker_row = dict(raw_data)
        else:
            tracker_row = {}
        tracker_row.update(
            {
                "id": clean_text(tracker_row.get("id")) or clean_text(row[0]),
                "role": clean_text(row[1]) or "attendee",
                "name": clean_text(tracker_row.get("name")) or clean_text(row[2]),
                "displayName": clean_text(row[2]),
                "email": clean_email(row[3]),
                "expected": bool(row[4]),
                "required": bool(row[5]),
                "attended": bool(row[6]),
                "status": normalise_coach_attendance_status(
                    {"status": row[7], "expected": row[4], "attended": row[6]},
                ),
                "totalAttendanceSeconds": max(parse_int(row[8], 0), 0),
                "actualDisplayName": clean_text(row[9]),
                "actualRecordIds": parse_jsonish(row[10], []),
                "reportIds": parse_jsonish(row[11], []),
            }
        )
        tracker_rows.append(tracker_row)

    extra_rows = [row for row in tracker_rows if not row.get("expected")]
    expected_rows = [row for row in tracker_rows if row.get("expected")]
    attendance_tracker = {
        "tracker": tracker_rows,
        "extraAttendees": extra_rows,
        "expectedCount": len(expected_rows) or len(expected_attendees),
        "expectedAttendedCount": sum(1 for row in expected_rows if row.get("status") == "attended"),
        "expectedAbsentCount": sum(1 for row in expected_rows if row.get("status") == "absent"),
        "expectedPendingCount": sum(1 for row in expected_rows if row.get("status") == "pending"),
        "extraCount": len(extra_rows),
    }
    if not tracker_rows:
        attendance_tracker = empty_tracker

    attendance = {
        "reports": attendance_reports,
        "records": records,
        "expectedAttendees": expected_attendees,
        "reportCount": len(attendance_reports),
        "attendedCount": sum(max(parse_int(report.get("attendedCount"), 0), 0) for report in attendance_reports),
        "absentCount": sum(max(parse_int(report.get("absentCount"), 0), 0) for report in attendance_reports),
        "participantCount": sum(max(parse_int(report.get("totalParticipantCount"), 0), 0) for report in attendance_reports),
        **attendance_tracker,
    }
    storage = {"stored": True}
    if latest_sync:
        storage["syncedAt"] = latest_sync.isoformat()
    return {
        "attendance": attendance,
        "artifacts": artifacts,
        "attendanceReports": attendance_reports,
        "attendanceTracker": attendance_tracker,
        "errors": [],
        "partial": False,
        "storage": storage,
    }


def stored_coach_meeting_transcript(record: CoachCalendarEvent, artifact_id: str) -> dict | None:
    database = router.db_for_read(CoachCalendarEvent) or "default"
    if (
        not coach_meeting_snapshot_tables_ready(database)
        or not coach_meeting_artifact_transcript_columns_ready(database)
    ):
        return None
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT transcript_vtt, transcript_content_type
                FROM {COACH_MEETING_ARTIFACTS_RELATION}
                WHERE event_key = %s
                  AND artifact_type = 'transcript'
                  AND graph_artifact_id = %s
                  AND transcript_vtt <> ''
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                [record.event_key, clean_text(artifact_id)],
            )
            row = cursor.fetchone()
    except Exception:
        logger.exception("Unable to read stored coach Teams transcript for event_key=%s", record.event_key)
        return None
    if not row:
        # Offloaded to blob storage: the column is cleared once the VTT is
        # archived, so an empty column is not the same as no transcript.
        from coach_api.recording_archive import archived_transcript_vtt

        archived = archived_transcript_vtt(record.event_key, clean_text(artifact_id), database)
        if archived:
            return {"transcript_vtt": archived, "transcript_content_type": "text/vtt"}
        return None
    return {
        "transcript_vtt": row[0],
        "transcript_content_type": row[1] or "text/vtt",
    }


def combine_coach_meeting_transcript_rows(rows: list[tuple[str, str]]) -> dict | None:
    """Combine all stored Teams transcript segments into one text source.

    Teams can split one meeting into multiple transcript artifacts when a
    recording is stopped and restarted. Keep the artifact IDs for traceability,
    but send the readable text to the summary as one chronological transcript.
    """

    artifact_ids: list[str] = []
    transcript_parts: list[str] = []
    for artifact_id, text in rows:
        artifact_id = clean_text(artifact_id)
        text = clean_text(text)
        if not text:
            continue
        if artifact_id:
            artifact_ids.append(artifact_id)
        transcript_parts.append(text)

    if not transcript_parts:
        return None
    return {
        "artifactId": ",".join(artifact_ids),
        "text": "\n\n".join(transcript_parts),
    }


def coach_meeting_transcript_selection(record: CoachCalendarEvent) -> dict:
    from .migrated_session_scope import stored_transcript_selection
    return stored_transcript_selection(record, stored_coach_meeting_snapshot(record))


def stored_coach_meeting_transcript_for_summary(record: CoachCalendarEvent, *, artifact_ids=None) -> dict | None:
    if artifact_ids is None:
        artifact_ids = coach_meeting_transcript_selection(record)['artifactIds']
    if not artifact_ids:
        return None
    artifact_filter = (" AND graph_artifact_id IN (" + ",".join(["%s"] * len(artifact_ids)) + ")") if artifact_ids is not None else ""
    database = router.db_for_read(CoachCalendarEvent) or "default"
    if (
        not coach_meeting_snapshot_tables_ready(database)
        or not coach_meeting_artifact_transcript_columns_ready(database)
    ):
        return None
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT graph_artifact_id, transcript_text
                FROM {COACH_MEETING_ARTIFACTS_RELATION}
                WHERE event_key = %s
                  AND artifact_type = 'transcript'
                  AND transcript_text <> ''
                  AND graph_event_id = %s
                  {artifact_filter}
                ORDER BY COALESCE(created_datetime, end_datetime) ASC NULLS LAST,
                         graph_artifact_id ASC
                """,
                [record.event_key, record.graph_event_id, *(artifact_ids or [])],
            )
            rows = cursor.fetchall()
    except Exception:
        logger.exception("Unable to read stored coach Teams transcript text for event_key=%s", record.event_key)
        return None
    return combine_coach_meeting_transcript_rows(rows)


@dataclass(frozen=True)
class MeetingSummaryContext:
    """The only meeting facts the recap prompt reads.

    ``CoachCalendarEvent`` already has this shape, which is why
    ``openai_meeting_summary`` takes either. A Review whose meeting was held
    outside Teams has no calendar row to read these from, but it still has a
    learner, a coach and a Review template -- see
    ``_review_instance_meeting_summary_context``.
    """

    event_type: str = ""
    review_template_id: str | None = None
    learner_name: str = ""
    owner_name: str = ""


def meeting_summary_transcript_excerpt(transcript_text: str) -> tuple[str, bool]:
    """The transcript the prompt carries, and whether anything was dropped.

    Returned rather than silently applied so callers can tell the coach that
    the recap was built from part of the meeting only.
    """
    excerpt = transcript_text[:COACH_MEETING_SUMMARY_TRANSCRIPT_CHARS]
    return excerpt, len(transcript_text) > len(excerpt)


def openai_meeting_summary(
    record: CoachCalendarEvent | MeetingSummaryContext, transcript_text: str,
) -> tuple[dict, str]:
    if not getattr(settings, "OPENAI_API_KEY", ""):
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The OpenAI client library is not installed on the server.") from exc

    meeting_label = resolve_review_display_title(record.event_type, record.review_template_id)
    system = (
        "You create concise learner-facing meeting recaps for a UK learning platform. "
        "Use a professional, supportive tone. Do not expose raw transcript wording, private speculation, "
        "sensitive personal information, or unverified accusations. Keep the recap factual and action-oriented. "
        "Return only valid JSON with keys: title, overview, keyPoints, actions, nextSteps, support. "
        "actions must be an array of objects with title, owner, dueDate, status."
    )
    user = (
        f"Meeting type: {meeting_label}\n"
        f"Learner: {clean_text(record.learner_name) or 'Learner'}\n"
        f"Coach: {clean_text(record.owner_name) or 'Coach'}\n\n"
        "Create a learner-visible recap from this transcript. "
        "Overview: 1-2 short sentences. Key points: 3-5 bullets. "
        "Actions: only agreed actions, with owner and dueDate if stated. "
        "Next steps: 2-4 clear steps. Support: include only if support needs were discussed.\n\n"
        f"Transcript:\n{meeting_summary_transcript_excerpt(transcript_text)[0]}"
    )
    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    completion_options = {
        "model": COACH_MEETING_SUMMARY_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        # This limit includes invisible reasoning tokens. The previous 1,200
        # token budget could therefore finish successfully with no visible
        # JSON content when a GPT-5 reasoning model was configured.
        "max_completion_tokens": 4000,
    }
    if COACH_MEETING_SUMMARY_MODEL.lower().startswith("gpt-5"):
        completion_options["reasoning_effort"] = "low"

    response = client.chat.completions.create(
        **completion_options,
    )
    choice = response.choices[0] if response.choices else None
    content = clean_text(choice.message.content if choice else "")
    if not content:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        raise RuntimeError(
            "The AI service returned an empty meeting summary "
            f"(finish_reason={clean_text(getattr(choice, 'finish_reason', '')) or 'unknown'}, "
            f"completion_tokens={getattr(usage, 'completion_tokens', None)}, "
            f"reasoning_tokens={getattr(details, 'reasoning_tokens', None)})."
        )
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The AI service returned an invalid meeting summary.") from exc
    summary = normalize_meeting_summary_payload(payload, record.event_type)
    if not clean_text(summary.get("overview")):
        raise RuntimeError("The AI service returned a meeting summary without an overview.")
    return summary, COACH_MEETING_SUMMARY_MODEL


def should_reuse_coach_meeting_summary(
    existing,
    transcript_hash: str,
    *,
    retry_failed: bool = False,
) -> bool:
    """Keep valid/edited summaries, but allow an explicit retry of a failed row."""
    if not existing:
        return False
    existing_hash = clean_text(existing[0])
    existing_status = clean_text(existing[1])
    if existing_status == "edited":
        return True
    if existing_hash != transcript_hash:
        return False
    return not (retry_failed and existing_status == "failed")


def ensure_coach_meeting_summary(
    record: CoachCalendarEvent,
    *,
    retry_failed: bool = False,
) -> dict | None:
    if clean_text(record.event_type).lower() not in COACH_MEETING_SUMMARY_TYPES:
        return None
    database = router.db_for_write(CoachCalendarEvent) or "default"
    if not coach_meeting_summaries_table_ready(database):
        return None

    transcript = stored_coach_meeting_transcript_for_summary(record)
    if not transcript:
        return stored_coach_meeting_summary(record)
    transcript_hash = hashlib.sha256(transcript["text"].encode("utf-8")).hexdigest()

    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                SELECT transcript_hash, status, current_summary
                FROM {COACH_MEETING_SUMMARIES_RELATION}
                WHERE event_key = %s
                LIMIT 1
                """,
                [record.event_key],
            )
            existing = cursor.fetchone()
    except Exception:
        logger.exception("Unable to inspect coach meeting summary for event_key=%s", record.event_key)
        return None

    cached = stored_coach_meeting_summary(record) if existing else None
    if existing and clean_text(existing[1]) == 'edited':
        # Retain a historical edited cache unchanged even when its source no
        # longer matches. It cannot be relabelled as the current session.
        return cached
    if cached and should_reuse_coach_meeting_summary(
        existing,
        transcript_hash,
        retry_failed=retry_failed,
    ):
        return cached

    now = timezone.now()
    try:
        summary, model = openai_meeting_summary(record, transcript["text"])
        status = "ready"
        error = ""
    except Exception as exc:  # noqa: BLE001 - stored as a user-visible sync state
        logger.exception("Unable to generate coach meeting summary for event_key=%s", record.event_key)
        fallback = cached['summary'] if cached else {}
        summary = normalize_meeting_summary_payload(fallback, record.event_type)
        model = COACH_MEETING_SUMMARY_MODEL
        status = "failed"
        error = clean_text(exc)

    from .migrated_session_scope import session_provenance
    summary = {**summary, '_lmsSession': session_provenance(record)}
    try:
        with transaction.atomic(using=database), connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {COACH_MEETING_SUMMARIES_RELATION} AS summary_target (
                    calendar_event_id,
                    event_key,
                    owner_email,
                    graph_event_id,
                    event_type,
                    learner_id,
                    learner_name,
                    transcript_artifact_id,
                    transcript_hash,
                    ai_summary,
                    current_summary,
                    summary_text,
                    model,
                    status,
                    generated_at,
                    error,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (event_key)
                DO UPDATE SET
                    calendar_event_id = EXCLUDED.calendar_event_id,
                    owner_email = EXCLUDED.owner_email,
                    graph_event_id = EXCLUDED.graph_event_id,
                    event_type = EXCLUDED.event_type,
                    learner_id = EXCLUDED.learner_id,
                    learner_name = EXCLUDED.learner_name,
                    transcript_artifact_id = EXCLUDED.transcript_artifact_id,
                    transcript_hash = EXCLUDED.transcript_hash,
                    ai_summary = EXCLUDED.ai_summary,
                    current_summary = CASE
                        WHEN summary_target.status = 'edited'
                        THEN summary_target.current_summary
                        ELSE EXCLUDED.current_summary
                    END,
                    summary_text = CASE
                        WHEN summary_target.status = 'edited'
                        THEN summary_target.summary_text
                        ELSE EXCLUDED.summary_text
                    END,
                    model = EXCLUDED.model,
                    status = CASE
                        WHEN summary_target.status = 'edited'
                        THEN summary_target.status
                        ELSE EXCLUDED.status
                    END,
                    generated_at = EXCLUDED.generated_at,
                    error = EXCLUDED.error,
                    updated_at = EXCLUDED.updated_at
                WHERE summary_target.status <> 'edited'
                """,
                [
                    getattr(record, "id", None),
                    record.event_key,
                    clean_email(record.owner_email),
                    clean_text(record.graph_event_id),
                    clean_text(record.event_type).lower(),
                    getattr(record, "learner_id", None),
                    clean_text(record.learner_name),
                    transcript["artifactId"],
                    transcript_hash,
                    coach_meeting_snapshot_json(summary, {}),
                    coach_meeting_snapshot_json(summary, {}),
                    meeting_summary_plain_text(summary),
                    model,
                    status,
                    now,
                    error,
                    now,
                ],
            )
    except Exception:
        logger.exception("Unable to persist coach meeting summary for event_key=%s", record.event_key)
        return None
    return stored_coach_meeting_summary(record)


def coach_attendance_participant_key(row: dict, index: int) -> str:
    row_id = clean_text(row.get("id"))
    if row_id:
        return row_id
    role = clean_text(row.get("role")).lower() or "attendee"
    email = clean_email(row.get("email"))
    if email:
        return f"{role}:email:{email}"
    name = normalize_person_name(row.get("displayName") or row.get("name"))
    if name:
        return f"{role}:name:{name}"
    return f"{role}:row:{index}"


def normalise_coach_attendance_status(row: dict) -> str:
    status = clean_text(row.get("status")).lower()
    if status in COACH_MEETING_ATTENDANCE_STATUSES:
        return status
    if row.get("expected") and row.get("attended"):
        return "attended"
    if row.get("expected"):
        return "absent"
    return "extra" if row.get("attended") else "pending"


def persist_coach_meeting_snapshots(
    record: CoachCalendarEvent,
    *,
    artifacts: list[dict],
    attendance_reports: list[dict],
    attendance_tracker: dict,
    imported_refresh: dict | None = None,
    safe_diagnostics=False,
) -> dict:
    """Persist a latest-known Teams snapshot when the manual SQL tables exist."""

    if imported_refresh is not None:
        from .migrated_session_scope import persist_imported_snapshot
        snapshot = {**imported_refresh['snapshot'], 'artifacts': artifacts,
                    'attendanceReports': attendance_reports, 'attendanceTracker': attendance_tracker}
        return persist_imported_snapshot(record, snapshot,
            expected_version=imported_refresh['expected_version'], revalidate=imported_refresh.get('revalidate'),
            preserve_existing=imported_refresh.get('preserve_existing', False),
            context=imported_refresh.get('context'), start_review=imported_refresh.get('start_review'),
            session=imported_refresh.get('session'), meeting_id=imported_refresh.get('meeting_id'))

    database = router.db_for_write(CoachCalendarEvent) or "default"
    if not coach_meeting_snapshot_tables_ready(database):
        return {"stored": False, "reason": "snapshot_tables_missing"}

    synced_at = timezone.now()
    calendar_event_id = getattr(record, "id", None)
    owner_email = clean_email(record.owner_email)
    graph_organizer_email = clean_email(record.graph_organizer_email or record.owner_email)
    graph_event_id = clean_text(record.graph_event_id)
    event_type = clean_text(record.event_type).lower()
    learner_id = getattr(record, "learner_id", None)
    learner_name = clean_text(record.learner_name)
    learner_email = clean_email(record.learner_email)
    transcript_columns_ready = coach_meeting_artifact_transcript_columns_ready(database)

    try:
        with transaction.atomic(using=database), connections[database].cursor() as cursor:
            for artifact in artifacts:
                artifact_type = clean_text(artifact.get("artifact_type")).lower()
                graph_artifact_id = clean_text(artifact.get("graph_artifact_id") or artifact.get("id"))
                if artifact_type not in COACH_MEETING_ARTIFACT_ENDPOINTS or not graph_artifact_id:
                    continue
                transcript_columns = ""
                transcript_placeholders = ""
                transcript_update = ""
                transcript_values = []
                if transcript_columns_ready:
                    transcript_columns = """
                        transcript_vtt,
                        transcript_text,
                        transcript_content_type,
                        transcript_fetched_at,
                        transcript_fetch_error,
                    """
                    transcript_placeholders = "%s, %s, %s, %s, %s,"
                    transcript_update = """
                        transcript_vtt = EXCLUDED.transcript_vtt,
                        transcript_text = EXCLUDED.transcript_text,
                        transcript_content_type = EXCLUDED.transcript_content_type,
                        transcript_fetched_at = EXCLUDED.transcript_fetched_at,
                        transcript_fetch_error = EXCLUDED.transcript_fetch_error,
                    """
                    transcript_values = [
                        clean_text(artifact.get("transcript_vtt")),
                        clean_text(artifact.get("transcript_text")),
                        clean_text(artifact.get("transcript_content_type")),
                        coach_meeting_snapshot_datetime(artifact.get("transcript_fetched_at")),
                        clean_text(artifact.get("transcript_fetch_error")),
                    ]
                cursor.execute(
                    f"""
                    INSERT INTO {COACH_MEETING_ARTIFACTS_RELATION} (
                        calendar_event_id,
                        event_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        artifact_type,
                        graph_artifact_id,
                        call_id,
                        content_correlation_id,
                        created_datetime,
                        end_datetime,
                        {transcript_columns}
                        metadata,
                        last_seen_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        {transcript_placeholders}
                        %s::jsonb, %s, %s
                    )
                    ON CONFLICT (event_key, artifact_type, graph_artifact_id)
                    DO UPDATE SET
                        calendar_event_id = EXCLUDED.calendar_event_id,
                        owner_email = EXCLUDED.owner_email,
                        graph_organizer_email = EXCLUDED.graph_organizer_email,
                        graph_event_id = EXCLUDED.graph_event_id,
                        call_id = EXCLUDED.call_id,
                        content_correlation_id = EXCLUDED.content_correlation_id,
                        created_datetime = EXCLUDED.created_datetime,
                        end_datetime = EXCLUDED.end_datetime,
                        {transcript_update}
                        metadata = EXCLUDED.metadata,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    [
                        calendar_event_id,
                        record.event_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        artifact_type,
                        graph_artifact_id,
                        clean_text(artifact.get("call_id")),
                        clean_text(artifact.get("content_correlation_id")),
                        coach_meeting_snapshot_datetime(artifact.get("created_datetime")),
                        coach_meeting_snapshot_datetime(artifact.get("end_datetime")),
                        *transcript_values,
                        coach_meeting_snapshot_json(artifact.get("metadata"), {}),
                        synced_at,
                        synced_at,
                    ],
                )

            for report in attendance_reports:
                graph_report_id = clean_text(report.get("id"))
                if not graph_report_id:
                    continue
                cursor.execute(
                    f"""
                    INSERT INTO {COACH_MEETING_ATTENDANCE_REPORTS_RELATION} (
                        calendar_event_id,
                        event_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        graph_report_id,
                        meeting_start_datetime,
                        meeting_end_datetime,
                        total_participant_count,
                        attended_count,
                        absent_count,
                        metadata,
                        last_seen_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (event_key, graph_report_id)
                    DO UPDATE SET
                        calendar_event_id = EXCLUDED.calendar_event_id,
                        owner_email = EXCLUDED.owner_email,
                        graph_organizer_email = EXCLUDED.graph_organizer_email,
                        graph_event_id = EXCLUDED.graph_event_id,
                        meeting_start_datetime = EXCLUDED.meeting_start_datetime,
                        meeting_end_datetime = EXCLUDED.meeting_end_datetime,
                        total_participant_count = EXCLUDED.total_participant_count,
                        attended_count = EXCLUDED.attended_count,
                        absent_count = EXCLUDED.absent_count,
                        metadata = EXCLUDED.metadata,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    [
                        calendar_event_id,
                        record.event_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        graph_report_id,
                        coach_meeting_snapshot_datetime(report.get("meetingStartDateTime")),
                        coach_meeting_snapshot_datetime(report.get("meetingEndDateTime")),
                        max(parse_int(report.get("totalParticipantCount"), 0), 0),
                        max(parse_int(report.get("attendedCount"), 0), 0),
                        max(parse_int(report.get("absentCount"), 0), 0),
                        coach_meeting_snapshot_json(report, {}),
                        synced_at,
                        synced_at,
                    ],
                )

            cursor.execute(
                f"""
                UPDATE {COACH_MEETING_ATTENDANCE_RELATION}
                SET is_current = false,
                    updated_at = %s
                WHERE event_key = %s
                  AND is_current = true
                """,
                [synced_at, record.event_key],
            )

            for index, row in enumerate(attendance_tracker.get("tracker") or [], start=1):
                if not isinstance(row, dict):
                    continue
                participant_key = coach_attendance_participant_key(row, index)
                status = normalise_coach_attendance_status(row)
                cursor.execute(
                    f"""
                    INSERT INTO {COACH_MEETING_ATTENDANCE_RELATION} (
                        calendar_event_id,
                        event_key,
                        participant_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        event_type,
                        learner_id,
                        learner_name,
                        learner_email,
                        role,
                        display_name,
                        email,
                        expected,
                        required,
                        attended,
                        status,
                        total_attendance_seconds,
                        actual_display_name,
                        actual_record_ids,
                        report_ids,
                        raw_data,
                        is_current,
                        synced_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                        true, %s, %s
                    )
                    ON CONFLICT (event_key, participant_key)
                    DO UPDATE SET
                        calendar_event_id = EXCLUDED.calendar_event_id,
                        owner_email = EXCLUDED.owner_email,
                        graph_organizer_email = EXCLUDED.graph_organizer_email,
                        graph_event_id = EXCLUDED.graph_event_id,
                        event_type = EXCLUDED.event_type,
                        learner_id = EXCLUDED.learner_id,
                        learner_name = EXCLUDED.learner_name,
                        learner_email = EXCLUDED.learner_email,
                        role = EXCLUDED.role,
                        display_name = EXCLUDED.display_name,
                        email = EXCLUDED.email,
                        expected = EXCLUDED.expected,
                        required = EXCLUDED.required,
                        attended = EXCLUDED.attended,
                        status = EXCLUDED.status,
                        total_attendance_seconds = EXCLUDED.total_attendance_seconds,
                        actual_display_name = EXCLUDED.actual_display_name,
                        actual_record_ids = EXCLUDED.actual_record_ids,
                        report_ids = EXCLUDED.report_ids,
                        raw_data = EXCLUDED.raw_data,
                        is_current = true,
                        synced_at = EXCLUDED.synced_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    [
                        calendar_event_id,
                        record.event_key,
                        participant_key,
                        owner_email,
                        graph_organizer_email,
                        graph_event_id,
                        event_type,
                        learner_id,
                        learner_name,
                        learner_email,
                        clean_text(row.get("role")).lower() or "attendee",
                        clean_text(row.get("displayName") or row.get("name")),
                        clean_email(row.get("email")),
                        bool(row.get("expected")),
                        bool(row.get("required")),
                        bool(row.get("attended")),
                        status,
                        max(parse_int(row.get("totalAttendanceSeconds"), 0), 0),
                        clean_text(row.get("actualDisplayName")),
                        coach_meeting_snapshot_json(row.get("actualRecordIds"), []),
                        coach_meeting_snapshot_json(row.get("reportIds"), []),
                        coach_meeting_snapshot_json(row, {}),
                        synced_at,
                        synced_at,
                    ],
                )
    except Exception:
        if safe_diagnostics:
            logger.warning("APTEM resource refresh: snapshot persistence failed.")
        else:
            logger.exception("Unable to persist coach Teams meeting snapshots for event_key=%s", record.event_key)
        return {"stored": False, "reason": "snapshot_persist_failed"}

    return {"stored": True, "syncedAt": synced_at.isoformat()}


def fetch_coach_meeting_graph_snapshot(record: CoachCalendarEvent, *, target_error_handler=None,
                                     transcript_collection_fetcher=None,
                                     transcript_content_selector=None,
                                     attendance_response_validator=None,
                                     target_resolver=None,
                                     independent_resources=False,
                                     safe_diagnostics=False) -> tuple[dict | None, dict | None, int]:
    """Fetch Teams artifacts and attendance for a coach calendar event.

    The returned snapshot is the single source used by the UI endpoint and by
    the manual backfill command, so both paths persist the same attendance
    logic.
    """

    if not has_graph_credentials():
        return None, {"detail": "Microsoft Graph credentials are not configured."}, 503

    try:
        base, error_payload = (target_resolver or coach_meeting_graph_target)(record)
    except (RuntimeError, TimeoutError) as exc:
        if target_error_handler is not None:
            return target_error_handler(exc)
        if isinstance(exc, TimeoutError):
            raise
        logger.exception("Unable to resolve coach meeting artifact target.")
        return (
            None,
            {
                "detail": "Microsoft Graph could not resolve this Teams meeting.",
                "code": "coach_artifact_target_unavailable",
            },
            409,
        )
    if error_payload:
        return None, error_payload, 409

    artifacts = []
    attendance_reports = []
    errors = []
    resource_errors = {}
    transcript_collection_complete = False
    attendance_collection_complete = False
    attendance_collection_distinct = True
    resource_exceptions = (RuntimeError, TimeoutError) if independent_resources else (RuntimeError,)
    def fetch_failure(resource, exc):
        status = getattr(getattr(exc, '__cause__', None), 'code', None)
        if resource_errors.get(resource) != 'permission':
            resource_errors[resource] = 'permission' if status in {401, 403} else 'fetch'
    event_key = clean_text(record.event_key)
    def log_failure(resource):
        if safe_diagnostics or independent_resources:
            logger.warning("APTEM resource refresh: %s unavailable.", resource)
        else:
            logger.exception("Unable to load coach Teams %s for event_key=%s", resource, event_key)

    try:
        response = microsoft_graph_request("GET", f"{base}/attendanceReports")
        if attendance_response_validator is not None:
            attendance_response_validator(response)
            attendance_collection_complete = True
            report_ids = [row['id'] for row in response['value']]
            attendance_collection_distinct = len(report_ids) == len(set(report_ids))
        fetched_report_ids = set()
        for report in response.get("value") or []:
            if not isinstance(report, dict):
                continue
            report_id = clean_text(report.get("id"))
            if not report_id:
                continue
            if attendance_response_validator is not None:
                if report_id in fetched_report_ids:
                    continue
                fetched_report_ids.add(report_id)
            try:
                report_key = urllib_parse.quote(report_id, safe="")
                detail = microsoft_graph_request(
                    "GET",
                    f"{base}/attendanceReports/{report_key}?$expand=attendanceRecords",
                )
                if attendance_response_validator is not None:
                    attendance_response_validator(detail, report=report)
            except resource_exceptions as exc:
                attendance_collection_complete = False
                fetch_failure('attendance', exc)
                log_failure('attendance report detail')
                errors.append("Microsoft Graph could not return attendance details for this meeting.")
                continue
            attendance_reports.append(serialize_coach_meeting_attendance_report(report, detail))
    except resource_exceptions as exc:
        attendance_collection_complete = False
        fetch_failure('attendance', exc)
        log_failure('attendance reports')
        errors.append("Microsoft Graph could not return attendance reports for this meeting.")

    for artifact_type, endpoint in COACH_MEETING_ARTIFACT_ENDPOINTS.items():
        try:
            if artifact_type == 'transcript' and transcript_collection_fetcher is not None:
                response = transcript_collection_fetcher(f"{base}/{endpoint}")
            else:
                response = microsoft_graph_request("GET", f"{base}/{endpoint}")
            if independent_resources and artifact_type == 'recording':
                if (not isinstance(response, dict) or not isinstance(response.get('value'), list)
                        or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                               or not row['id'].strip() for row in response['value'])):
                    raise RuntimeError('Incomplete recording metadata.')
                if response.get('@odata.nextLink'):
                    resource_errors[endpoint] = 'incomplete'
                    errors.append('Recording metadata is incomplete. Retry Check Session; saved resources have been kept.')
        except resource_exceptions as exc:
            fetch_failure(endpoint, exc)
            log_failure(endpoint)
            errors.append(f"Microsoft Graph could not return {endpoint} for this meeting.")
            continue
        if artifact_type == 'transcript' and attendance_response_validator is not None:
            rows = response.get('value')
            transcript_collection_complete = bool(isinstance(rows, list) and not response.get('@odata.nextLink')
                and all(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'].strip() for row in rows)
                and len({row['id'] for row in rows}) == len(rows))
        selected_transcript_ids = None
        if artifact_type == 'transcript' and transcript_content_selector is not None:
            metadata_rows = [serialize_coach_meeting_artifact('transcript', row)
                for row in response.get('value') or [] if isinstance(row, dict) and clean_text(row.get('id'))]
            selected_transcript_ids = set(transcript_content_selector(
                record, attendance_reports, metadata_rows))
        for artifact in response.get("value") or []:
            if isinstance(artifact, dict) and clean_text(artifact.get("id")):
                serialized_artifact = serialize_coach_meeting_artifact(artifact_type, artifact)
                if artifact_type == "transcript" and (selected_transcript_ids is None
                                                       or serialized_artifact['graph_artifact_id'] in selected_transcript_ids):
                    try:
                        transcript_content = fetch_coach_meeting_transcript_content(
                            base,
                            serialized_artifact["graph_artifact_id"],
                            **({'safe_diagnostics': True} if safe_diagnostics else {}),
                        )
                    except (RuntimeError, TimeoutError) as exc:
                        if not independent_resources:
                            raise
                        fetch_failure('transcripts', exc)
                        transcript_content = {'transcript_fetch_error': 'Microsoft could not return transcript content. Retry Check Session.'}
                    serialized_artifact.update(transcript_content)
                    if transcript_content.get("transcript_fetch_error"):
                        errors.append(transcript_content["transcript_fetch_error"])
                artifacts.append(serialized_artifact)

    expected_attendees = coach_meeting_expected_attendees(record)
    attendance_records = [
        attendance_record
        for report in attendance_reports
        for attendance_record in report["records"]
    ]
    attendance_tracker = build_coach_meeting_attendance_tracker(
        expected_attendees,
        attendance_records,
        has_report=bool(attendance_reports),
    )
    attendance = {
        "reports": attendance_reports,
        "records": attendance_records,
        "expectedAttendees": expected_attendees,
        "reportCount": len(attendance_reports),
        "attendedCount": sum(report["attendedCount"] for report in attendance_reports),
        "absentCount": sum(report["absentCount"] for report in attendance_reports),
        "participantCount": sum(report["totalParticipantCount"] for report in attendance_reports),
        **attendance_tracker,
    }
    snapshot = {
        "attendance": attendance,
        "artifacts": artifacts,
        "attendanceReports": attendance_reports,
        "attendanceTracker": attendance_tracker,
        "errors": errors,
        "partial": bool(errors),
        "resourceErrors": resource_errors,
    }
    if attendance_response_validator is not None:
        snapshot['attendanceCollectionComplete'] = attendance_collection_complete
        snapshot['attendanceCollectionDistinct'] = attendance_collection_distinct
        snapshot['transcriptCollectionComplete'] = transcript_collection_complete
    return snapshot, None, 207 if errors else 200


def coach_meeting_write_error(request, record):
    """Generic writes must not bypass the owned Review's lifecycle/adapter."""
    legacy = clean_text(record.idempotency_key).split(":")
    imported = record.event_key.startswith("imported-review:")
    psp_meeting = False
    if imported:
        from .personal_support_booking import can_refresh_meeting
        psp_meeting = can_refresh_meeting(authenticated_coach_email(request), record)
    if ((imported and not psp_meeting)
            or (len(legacy) in {5, 6} and legacy[:1] == ["learner-book"]
                and legacy[1] in {"mcm", "progress-review"})):
        return JsonResponse({"detail": "Use Check Session in the imported Review.",
                             "code": "IMPORTED_REVIEW_ACTION_REQUIRED"}, status=409)
    if record.status not in {"scheduled", "in-progress"}:
        return JsonResponse({"detail": "This meeting is read-only at its current stage."}, status=409)
    if record.review_instance_id:
        instance, error = _authorized_review_instance(request, record.review_instance_id)
        if error is not None:
            return error
        if (instance.get("calendar_event_id") != record.pk
                or instance.get("learner_id") != record.learner_id
                or instance.get("review_template_id") != record.review_template_id
                or instance.get("status") not in {"scheduled", "in-progress"}):
            return JsonResponse({"detail": "The Review is read-only or its meeting association changed."}, status=409)
    elif record.review_template_id:
        return JsonResponse({"detail": "The Review meeting association is incomplete."}, status=409)
    return None


@coach_access_required
@require_http_methods(["GET", "POST"])
def coach_timetable_event_artifacts(request, event_key):
    from .review_attendance_links import attendance_for_display
    owner_email = authenticated_coach_email(request)
    record = coach_meeting_artifact_record(owner_email, event_key)
    if not record:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)

    # Legacy refresh query parameters are deliberately inert. Only an explicit
    # CSRF-protected POST may contact providers or persist business state.
    if request.method == "POST":
        error = coach_meeting_write_error(request, record)
        if error is not None:
            return error
        # The guard above admits imported keys only for the verified PSP
        # exception. Native and all other generic callers keep their defaults.
        imported_psp = record.event_key.startswith("imported-review:")
        snapshot_options = {}
        if imported_psp:
            from .migrated_session_scope import (
                attendance_reports_for_refresh, scope_snapshot, snapshot_version, validate_attendance_response,
            )
            snapshot_options['attendance_response_validator'] = validate_attendance_response
            previous = stored_coach_meeting_snapshot(record)
        snapshot, error_payload, status_code = fetch_coach_meeting_graph_snapshot(record, **snapshot_options)
        if error_payload:
            return JsonResponse(error_payload, status=status_code)
        if imported_psp:
            reports = attendance_reports_for_refresh(record, snapshot, previous)
            scoped = scope_snapshot(record, {**snapshot, 'attendanceReports': reports})
            snapshot = {**snapshot, **{key: scoped[key] for key in (
                'attendance', 'attendanceReports', 'attendanceTracker',
            )}}

        # A manual Teams refresh is the explicit place where we reconcile
        # attendance evidence into review status. Plainly opening details only
        # reads the stored snapshot and performs no external Graph work.
        status_changed = apply_teams_attendance_status_transition(
            record, (snapshot.get("attendance") or {}).get("records") or [],
        )
        if status_changed:
            record.refresh_from_db(fields=["status"])

        storage_status = persist_coach_meeting_snapshots(
            record,
            artifacts=snapshot["artifacts"],
            attendance_reports=snapshot["attendanceReports"],
            attendance_tracker=snapshot["attendanceTracker"],
            **({'imported_refresh': {'snapshot': snapshot, 'expected_version': snapshot_version(previous),
                'revalidate': lambda current: coach_meeting_write_error(request, current) is None}}
               if imported_psp else {}),
        )
        if imported_psp and not storage_status.get('stored'):
            reason = storage_status.get('reason')
            stale = reason in {'snapshot_superseded', 'snapshot_context_changed'}
            return JsonResponse({'detail': 'Meeting evidence changed or could not be saved. Retry Check Teams.',
                                 'code': 'BOOKING_CONFLICT' if stale else 'SNAPSHOT_STORAGE_UNAVAILABLE'},
                                status=409 if stale else 503)
        meeting_summary = ensure_coach_meeting_summary(record, retry_failed=True)
    else:
        snapshot = stored_coach_meeting_snapshot(record)
        status_code = 200
        storage_status = snapshot.get("storage") or {"stored": False}
        meeting_summary = stored_coach_meeting_summary(record)

    return JsonResponse(
        {
            "event": {
                "eventKey": record.event_key,
                "source": clean_text(record.event_type),
                "status": clean_text(record.status),
                "learner": clean_text(record.learner_name),
                "scheduledDate": record.scheduled_date.isoformat() if record.scheduled_date else None,
                "scheduledTime": format_time_value(record.scheduled_time),
            },
            "attendance": attendance_for_display(record, snapshot["attendance"]),
            "artifacts": [public_coach_meeting_artifact(artifact) for artifact in snapshot["artifacts"]],
            "meetingSummary": meeting_summary,
            "errors": snapshot["errors"],
            "partial": snapshot["partial"],
            "storage": storage_status,
        },
        status=status_code,
    )


def coach_meeting_artifact_content_response(request, record, event_key, artifact_type, artifact_id,
                                            *, imported_review=False, session=None,
                                            transcript_ready=True):
    """Stream one Teams artifact for an already-authorised calendar record."""
    artifact_type = clean_text(artifact_type).lower()
    endpoint = COACH_MEETING_ARTIFACT_ENDPOINTS.get(artifact_type)
    if not endpoint:
        return JsonResponse({"detail": "Unsupported meeting artifact."}, status=400)

    safe_event_key = re.sub(r"[^A-Za-z0-9._-]+", "-", clean_text(event_key)).strip("-")[:120] or "coach-meeting"
    filename = f"{safe_event_key}-{artifact_type}.{'vtt' if artifact_type == 'transcript' else 'mp4'}"
    as_attachment = clean_text(request.GET.get("preview")).lower() not in {"1", "true", "yes"}
    disposition = "attachment" if as_attachment else "inline"

    if imported_review:
        from .migrated_session_scope import (has_verified_meeting_evidence, scope_snapshot,
                                             stored_transcript_selection)

        # Imported Review content starts with a stored, source-qualified read.
        # The selected call may be outside the planned booking; only a saved,
        # verified recording can become an on-demand Graph playback request.
        snapshot = stored_coach_meeting_snapshot(record)
        if snapshot.get('partial') or not (snapshot.get('storage') or {}).get('stored'):
            return JsonResponse({'detail': 'Saved meeting resources are temporarily unavailable.'}, status=503)
        meeting_scoped = has_verified_meeting_evidence(snapshot)
        scoped = scope_snapshot(record, snapshot, session=None if meeting_scoped else session,
                                meeting_scoped=meeting_scoped)
        if scoped['selectionError']:
            return JsonResponse({'detail': 'Meeting resources cannot be matched to one Review session.',
                                 'code': scoped['selectionError']}, status=409)
        if artifact_type == 'transcript':
            if session is not None and not transcript_ready:
                return JsonResponse({'detail': 'The complete saved transcript is not available yet.',
                                     'code': 'TRANSCRIPT_INCOMPLETE'}, status=409)
            selection = stored_transcript_selection(record, snapshot,
                session=None if meeting_scoped else session, meeting_scoped=meeting_scoped)
            if selection['code'] in {'TRANSCRIPT_SESSION_AMBIGUOUS', 'MEETING_ARTIFACT_CONFLICT'}:
                return JsonResponse({'detail': 'The saved transcript association is ambiguous.',
                                     'code': selection['code']}, status=409)
            allowed = selection['artifactIds']
            if (not allowed or (clean_text(artifact_id).lower() != 'combined' and artifact_id not in allowed)):
                return JsonResponse({'detail': 'The transcript is not available for this Review.',
                                     'code': 'TRANSCRIPT_UNAVAILABLE'}, status=404)
            if clean_text(artifact_id).lower() == 'combined':
                transcript = stored_coach_meeting_transcript_for_summary(record, artifact_ids=allowed)
                if (not transcript or
                        set(clean_text(transcript.get('artifactId')).split(',')) != set(allowed)):
                    return JsonResponse({'detail': 'The saved transcript is unavailable.'}, status=404)
                response = HttpResponse(transcript['text'], content_type='text/plain; charset=utf-8')
                response['Content-Disposition'] = f'{disposition}; filename="{safe_event_key}-transcript.txt"'
                return response
            stored_transcript = stored_coach_meeting_transcript(record, artifact_id)
            if not stored_transcript:
                return JsonResponse({'detail': 'The saved transcript is unavailable.'}, status=404)
            response = HttpResponse(stored_transcript['transcript_vtt'],
                                    content_type=stored_transcript['transcript_content_type'] or 'text/vtt')
            response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
            return response
        recording = next((row for row in scoped['artifacts']
                          if row.get('artifact_type') == 'recording'
                          and row.get('graph_artifact_id') == artifact_id
                          and (row.get('metadata') or {}).get('_lms_graph_event_id') == record.graph_event_id), None)
        if not recording:
            return JsonResponse({'detail': 'The recording is not available for this Review.'}, status=404)
        from coach_api.recording_archive import archived_recording_url
        archived_url = archived_recording_url(event_key, artifact_id)
        if archived_url:
            return HttpResponseRedirect(archived_url)
        from .imported_recording_playback import stream_imported_recording
        return stream_imported_recording(request, record, scoped, recording)

    if artifact_type == "transcript":
        selection = coach_meeting_transcript_selection(record)
        if selection['code'] == 'TRANSCRIPT_SESSION_AMBIGUOUS':
            return JsonResponse({'detail': 'The transcript cannot be matched to one booked session.',
                                 'code': selection['code']}, status=409)
        if clean_text(artifact_id).lower() != 'combined' and artifact_id not in selection['artifactIds']:
            return JsonResponse({'detail': 'The transcript is not available for this session.',
                                 'code': 'TRANSCRIPT_UNAVAILABLE'}, status=404)
        if clean_text(artifact_id).lower() == "combined":
            combined_transcript = stored_coach_meeting_transcript_for_summary(record, artifact_ids=selection['artifactIds'])
            if not combined_transcript:
                return JsonResponse({"detail": "The combined meeting transcript is not available."}, status=404)
            response = HttpResponse(
                combined_transcript["text"],
                content_type="text/plain; charset=utf-8",
            )
            combined_filename = f"{safe_event_key}-transcript.txt"
            response["Content-Disposition"] = f'{disposition}; filename="{combined_filename}"'
            return response
        stored_transcript = stored_coach_meeting_transcript(record, artifact_id)
        if stored_transcript:
            response = HttpResponse(
                stored_transcript["transcript_vtt"],
                content_type=stored_transcript["transcript_content_type"] or "text/vtt",
            )
            response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
            return response

    if artifact_type == "recording":
        # Prefer the archived copy: Graph is an availability risk and deletes
        # recordings on its own retention schedule, so once a recording is in
        # the container that is the copy worth serving. Redirecting hands the
        # bytes straight from Azure with range requests intact, instead of
        # streaming hundreds of megabytes back through Django.
        from coach_api.recording_archive import archived_recording_url

        archived_url = archived_recording_url(event_key, artifact_id)
        if archived_url:
            return HttpResponseRedirect(archived_url)

    if not has_graph_credentials():
        return JsonResponse({"detail": "Microsoft Graph credentials are not configured."}, status=503)

    try:
        base, error_payload = coach_meeting_graph_target(record)
    except RuntimeError:
        logger.exception("Unable to resolve coach meeting artifact content target.")
        return JsonResponse(
            {
                "detail": "Microsoft Graph could not resolve this Teams meeting.",
                "code": "coach_artifact_target_unavailable",
            },
            status=409,
        )
    if error_payload:
        return JsonResponse(error_payload, status=409)

    graph_settings = get_graph_settings()
    path = (
        f"{base}/{endpoint}/"
        f"{urllib_parse.quote(clean_text(artifact_id), safe='')}/content"
    )
    url = f'{graph_settings["base_url"].rstrip("/")}/{path}'
    request_headers = {
        "Authorization": f"Bearer {microsoft_graph_token()}",
        "Accept": "text/vtt" if artifact_type == "transcript" else "video/mp4",
    }
    range_header = clean_text(request.META.get("HTTP_RANGE")) if artifact_type == "recording" else ""
    if range_header:
        request_headers["Range"] = range_header

    graph_request = urllib_request.Request(url, headers=request_headers, method="GET")
    try:
        graph_response = urllib_request.urlopen(graph_request, timeout=45)
    except urllib_error.HTTPError as exc:
        if exc.code == 416 and artifact_type == "recording":
            response = HttpResponse(status=416)
            content_range = exc.headers.get("Content-Range")
            if content_range:
                response["Content-Range"] = content_range
            return response
        detail = exc.read().decode("utf-8", errors="ignore")
        logger.warning("Unable to fetch coach Teams %s content: %s %s", artifact_type, exc.code, detail)
        return JsonResponse(
            {"detail": f"Microsoft Graph could not return the {artifact_type} content."},
            status=exc.code,
        )
    except urllib_error.URLError as exc:
        logger.warning("Unable to fetch coach Teams %s content: %s", artifact_type, exc)
        return JsonResponse(
            {"detail": f"Microsoft Graph could not return the {artifact_type} content."},
            status=502,
        )

    content_type = graph_response.headers.get("Content-Type") or (
        "text/vtt" if artifact_type == "transcript" else "video/mp4"
    )
    if artifact_type == "recording":
        response = StreamingHttpResponse(
            iter(lambda: graph_response.read(65536), b""),
            status=graph_response.status,
            content_type=content_type,
        )
        response["Accept-Ranges"] = "bytes"
        content_length = graph_response.headers.get("Content-Length")
        if content_length:
            response["Content-Length"] = content_length
        content_range = graph_response.headers.get("Content-Range")
        if content_range:
            response["Content-Range"] = content_range
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response

    content = graph_response.read()
    graph_response.close()
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@coach_access_required
@require_GET
def coach_timetable_event_artifact_content(request, event_key, artifact_type, artifact_id):
    if event_key.startswith("imported-review:") and is_coach_view_as(request):
        return JsonResponse({"detail": "Admin view-as cannot fetch migrated Teams content.", "code": "coach_view_as_read_only"}, status=403)
    owner_email = authenticated_coach_email(request)
    record = coach_meeting_artifact_record(owner_email, event_key)
    if not record:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)
    if event_key.startswith('imported-review:'):
        definition = _imported_review_definition(owner_email, event_key)
        if not definition:
            return JsonResponse({'detail': 'Imported Review is not available for this coach.'}, status=404)
        family = (definition.get('template') or {}).get('reviewTypeCode')
        if family in {'aptem_mcm', 'aptem_progress_review'}:
            from .migrated_intelligence_views import _association
            from .bound_actual_session import BoundSessionConflict, bound_session

            overlay, linked, _definition = _association(request, event_key)
            if not overlay or not linked or linked.pk != record.pk:
                return JsonResponse({'detail': 'Imported meeting association is unavailable.'}, status=404)
            try:
                bound = bound_session(linked, overlay=overlay)
            except BoundSessionConflict:
                return JsonResponse({'detail': 'The meeting association changed. Reload the Review.',
                                     'code': 'ACTUAL_SESSION_CLAIM_CONFLICT'}, status=409)
            return coach_meeting_artifact_content_response(request, linked, event_key,
                artifact_type, artifact_id, imported_review=True,
                session=bound.context if bound else None,
                transcript_ready=(overlay.meeting_intelligence or {}).get('transcriptStatus') == 'available')
    return coach_meeting_artifact_content_response(request, record, event_key, artifact_type, artifact_id)


@coach_access_required
def coach_timetable_event_summary(request, event_key):
    if request.method not in {"GET", "PATCH"}:
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request)
    record = coach_meeting_artifact_record(owner_email, event_key)
    if not record:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)
    if request.method == "GET":
        return JsonResponse({"meetingSummary": stored_coach_meeting_summary(record)})

    error = coach_meeting_write_error(request, record)
    if error is not None:
        return error

    database = router.db_for_write(CoachCalendarEvent) or "default"
    if not coach_meeting_summaries_table_ready(database):
        return JsonResponse({"detail": "Meeting summary storage is not configured."}, status=503)
    try:
        payload = parse_json_object(request)
    except ValidationError as exc:
        return validation_error_response(exc)
    # Never retag a historical edited cache as the current session.
    if not stored_coach_meeting_summary(record):
        return JsonResponse({'detail': 'No verified summary is available for this session.',
                             'code': 'SUMMARY_SESSION_MISMATCH'}, status=409)
    from .migrated_session_scope import session_provenance
    summary = {**normalize_meeting_summary_payload(payload.get("summary"), record.event_type),
               '_lmsSession': session_provenance(record)}
    now = timezone.now()
    editor = clean_text(getattr(request, "coach_email", "")) or clean_text(owner_email)
    try:
        with transaction.atomic(using=database), connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE {COACH_MEETING_SUMMARIES_RELATION}
                SET current_summary = %s::jsonb,
                    summary_text = %s,
                    status = 'edited',
                    edited_at = %s,
                    edited_by = %s,
                    updated_at = %s
                WHERE event_key = %s
                  AND lower(owner_email) = lower(%s)
                """,
                [
                    coach_meeting_snapshot_json(summary, {}),
                    meeting_summary_plain_text(summary),
                    now,
                    editor,
                    now,
                    record.event_key,
                    owner_email,
                ],
            )
            updated = cursor.rowcount
        if not updated:
            ensure_coach_meeting_summary(record)
            with transaction.atomic(using=database), connections[database].cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {COACH_MEETING_SUMMARIES_RELATION}
                    SET current_summary = %s::jsonb,
                        summary_text = %s,
                        status = 'edited',
                        edited_at = %s,
                        edited_by = %s,
                        updated_at = %s
                    WHERE event_key = %s
                      AND lower(owner_email) = lower(%s)
                    """,
                    [
                        coach_meeting_snapshot_json(summary, {}),
                        meeting_summary_plain_text(summary),
                        now,
                        editor,
                        now,
                        record.event_key,
                        owner_email,
                    ],
                )
                updated = cursor.rowcount
        if not updated:
            return JsonResponse({"detail": "Meeting summary has not been generated yet."}, status=409)
    except Exception:
        logger.exception("Unable to update coach meeting summary for event_key=%s", record.event_key)
        return JsonResponse({"detail": "Unable to save meeting summary."}, status=502)
    return JsonResponse({"meetingSummary": stored_coach_meeting_summary(record)})


def cancel_reserved_calendar_event(record: CoachCalendarEvent) -> tuple[CoachCalendarEvent, str]:
    """Cancel local booking state atomically, then reconcile Graph externally."""
    from . import imported_meeting_ownership as meeting_ownership
    try:
        ownership_active = meeting_ownership.required_for_record(record)
        if ownership_active:
            meeting_ownership.require_activation_ready()
    except meeting_ownership.MeetingOwnershipError:
        raise LearnerCalendarConflict("This imported meeting needs ownership reconciliation before cancellation.") from None
    with transaction.atomic():
        current = CoachCalendarEvent.objects.select_for_update().get(pk=record.pk)
        if ownership_active:
            try:
                if not meeting_ownership.required_for_record(current):
                    raise meeting_ownership.MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
                meeting_ownership.require_cancel_owner(current)
            except meeting_ownership.MeetingOwnershipError:
                raise LearnerCalendarConflict("This imported meeting ownership changed. Reload before cancellation.") from None
        from .reconciliation_links import for_calendar
        if for_calendar(current):
            raise LearnerCalendarConflict(
                "This booking is reconciled to an Aptem Review. Its association must be reviewed before cancellation."
            )
        instance = None
        if clean_text(current.review_instance_id):
            instance = curriculum_review_instances.get_review_instance(
                current.review_instance_id, for_update=True,
            )
            if (
                not instance
                or instance.get("calendar_event_id") != current.pk
                or instance.get("learner_id") != current.learner_id
                or clean_text(instance.get("review_template_id"))
                != clean_text(current.review_template_id)
                or clean_email(instance.get("coach_email"))
                != clean_email(current.owner_email)
            ):
                raise LearnerCalendarConflict(
                    "This booking's review link is inconsistent. Please contact support."
                )
            if instance.get("status") in {
                curriculum_review_instances.STATUS_IN_PROGRESS,
                curriculum_review_instances.STATUS_AWAITING_SIGNATURE,
                curriculum_review_instances.STATUS_COMPLETED,
            }:
                raise LearnerCalendarConflict(
                    "A review that has started, been submitted or completed cannot be cancelled."
                )
            if instance.get("status") not in {
                curriculum_review_instances.STATUS_SCHEDULED,
                curriculum_review_instances.STATUS_NOT_SCHEDULED,
            }:
                raise LearnerCalendarConflict(
                    "This review cannot be cancelled from its current status."
                )
        elif (
            current.event_type in {"mcr", "progress-review", "review"}
            and current.status in {
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
                CoachCalendarEvent.STATUS_COMPLETED,
            }
        ):
            raise LearnerCalendarConflict(
                "LEGACY_REVIEW_RECONCILIATION_REQUIRED: this historical review "
                "cannot be cancelled without an authoritative Review Instance."
            )

        # The additional advanced-state protection is for Review lifecycle
        # rows only.  Catch-up and other non-Review sessions retain their
        # historical cancellation semantics.
        if (
            (instance or current.event_type in {"mcr", "progress-review", "review"})
            and current.status in {
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
                CoachCalendarEvent.STATUS_COMPLETED,
            }
        ):
            raise LearnerCalendarConflict(
                "A review that has started, been submitted or completed cannot be cancelled."
            )

        if instance:
            transitioned = curriculum_review_instances.mark_review_instance_not_scheduled(
                instance["id"],
                calendar_event_id=current.pk,
                learner_id=current.learner_id,
                review_template_id=current.review_template_id,
                coach_email=current.owner_email,
                actor=current.owner_email or "calendar-cancel",
            )
            if not transitioned:
                raise LearnerCalendarConflict(
                    "The review status changed. Reload it before cancelling."
                )

        current.status = CoachCalendarEvent.STATUS_NOT_SCHEDULED
        current.scheduled_date = None
        current.scheduled_time = None
        current.sync_state = CoachCalendarEvent.SYNC_CANCELLED
        current.save(update_fields=[
            "status", "scheduled_date", "scheduled_time", "sync_state", "updated_at",
        ])

    warning = delete_calendar_event_from_graph(current)
    with transaction.atomic():
        current = CoachCalendarEvent.objects.select_for_update().get(pk=record.pk)
        current.last_graph_sync_error = public_graph_sync_warning(warning)
        if warning:
            # Retain the external identifiers so an operator/retry can reconcile
            # the cancellation instead of silently losing the orphan's address.
            current.sync_state = CoachCalendarEvent.SYNC_RECONCILIATION
        else:
            if ownership_active:
                try:
                    meeting_ownership.retire_after_cancel(current)
                except meeting_ownership.MeetingOwnershipError:
                    raise LearnerCalendarConflict("The cancellation needs ownership reconciliation.") from None
            current.meeting_link = ""
            current.graph_web_link = ""
            current.graph_event_id = ""
            current.graph_organizer_email = ""
            current.meeting_provider = ""
            current.sync_state = CoachCalendarEvent.SYNC_CANCELLED
        current.save()
    return current, warning


def build_live_session_calendar_event(
    row: dict,
    session: dict,
    *,
    programme: str,
    cohort: str,
    group: str,
    owner_email: str,
    owner_name: str,
    week_title: str | None = None,
    tracked_occurrence: dict | None = None,
    tracked_series: dict | None = None,
    component_meeting_link: str = "",
) -> dict:
    tracked_occurrence = tracked_occurrence or {}
    tracked_series = tracked_series or {}
    tracked_start = tracked_occurrence.get("scheduled_start")
    event_date = tracked_start.date().isoformat() if isinstance(tracked_start, datetime) else session["date"]
    session_date = date.fromisoformat(event_date)
    module_name = clean_text(row.get("module_name")) or "Live Session"
    session_title = f"{module_name} — {week_title}" if week_title else f"{module_name} — Week {session['sessionNumber']}"

    schedule_value = clean_text(row.get("session_week_day"))
    fallback_start_text, fallback_end_text = schedule_time_parts(schedule_value)
    start_hour, end_hour, time_label, duration_minutes, is_time_estimated = 9, 10, "Time TBC", TIMETABLE_DEFAULT_DURATION_MINUTES, True
    start_time = parse_time_value(row.get("session_start_time") or fallback_start_text)
    end_time = parse_time_value(row.get("session_end_time") or fallback_end_text)
    if start_time:
        start_hour = start_time.hour + start_time.minute / 60
        is_time_estimated = False
        if end_time:
            end_hour = end_time.hour + end_time.minute / 60
            duration_minutes = max(15, round((end_hour - start_hour) * 60))
        else:
            end_hour = start_hour + duration_minutes / 60
        time_label = f'{start_time.strftime("%H:%M")} - {end_time.strftime("%H:%M")}' if end_time else start_time.strftime("%H:%M")

    meeting_link = (
        clean_text(tracked_occurrence.get("join_url"))
        or clean_text(tracked_series.get("join_url"))
        or clean_text(tracked_series.get("web_link"))
        or clean_text(component_meeting_link)
    )

    return {
        "eventKey": f'live-session-{row.get("id")}-{session["sessionNumber"]}',
        "id": f'live-session-{row.get("id")}-{session["sessionNumber"]}',
        # The Teams occurrence this slot is tracked by, when one exists; read by
        # live_session_outcomes to show whether the session was attended.
        "occurrenceId": clean_text(tracked_occurrence.get("id")) or None,
        "ownerEmail": owner_email,
        "ownerName": owner_name,
        "learnerId": "",
        "learner": f'{cohort} · {group}' if cohort and group else (group or cohort or module_name),
        "email": None,
        "programme": programme or "--",
        "cohort": cohort or "--",
        "group": group or "--",
        "module": module_name,
        "tutor": clean_text(row.get("Tutor_name")) or "Unassigned",
        "schedule": schedule_value,
        "source": "live-session",
        "sequence": session["sessionNumber"],
        "title": session_title,
        "type": "live-session",
        "targetDate": event_date,
        "date": event_date,
        "year": session_date.year,
        "month": session_date.month - 1,
        "dayOfMonth": session_date.day,
        "dayOfWeek": session_date.weekday(),
        "startHour": start_hour,
        "endHour": end_hour,
        "durationMinutes": duration_minutes,
        "timeLabel": time_label,
        "isTimeEstimated": is_time_estimated,
        "priority": "normal",
        "status": clean_text(tracked_occurrence.get("status")).lower() or "scheduled",
        "sourceStatus": clean_text(tracked_occurrence.get("status")).title() or "Scheduled",
        "meetingProvider": clean_text(tracked_series.get("provider")) or ("Microsoft Teams" if meeting_link else ""),
        "meetingLink": meeting_link,
        "graphWebLink": clean_text(tracked_series.get("web_link")),
        "platform": "Microsoft Teams" if meeting_link else "LMS",
        "location": "Microsoft Teams" if meeting_link else "--",
        "notes": f'{session_title}.',
        "rawPlanned": event_date,
        "rawStatus": clean_text(tracked_occurrence.get("status")).title() or "Scheduled",
    }


def coach_has_live_session_access(owner_email: str) -> bool:
    """Use the enrolment staff access grant as the coach authority.

    Curriculum staff profiles used to gate live sessions through
    ``assigned_group_ids``. Staff identity and workspace access now live in
    enrolment."Staff_users", so the dashboard admits only the staff row whose
    email matches the requested owner and whose Access grant is ``coach``.
    """
    requested_owner = normalize_email(owner_email)
    if not requested_owner:
        return False
    try:
        return (
            StaffUser.objects.annotate(
                staff_email_key=Lower(Trim("email")),
                staff_access_key=Lower(Trim("access")),
            )
            .filter(
                staff_email_key=requested_owner,
                staff_access_key=ACCESS_COACH,
            )
            .exists()
        )
    except DatabaseError:
        return False


def coach_staff_display_name(owner_email: str) -> str:
    requested_owner = normalize_email(owner_email)
    if not requested_owner:
        return ""
    try:
        row = (
            StaffUser.objects.annotate(
                staff_email_key=Lower(Trim("email")),
                staff_access_key=Lower(Trim("access")),
            )
            .filter(
                staff_email_key=requested_owner,
                staff_access_key=ACCESS_COACH,
            )
            .only("username")
            .first()
        )
    except Exception:
        # Display-name lookup is optional; coach data must remain available when
        # the staff database is offline, unavailable in tests, or still on a
        # legacy schema.
        return ""
    return clean_text(getattr(row, "username", None))


def staff_assignment_match_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_text(value).casefold())


def fetch_official_assigned_groups(owner_email: str, owner_name: str = "") -> list[dict]:
    """Curriculum-owned group assignments for this coach.

    The official source for a coach holding a delivery group is
    ``curriculum.groups.coach_name``. Learner caseload assignment is deliberately
    separate: a group can be assigned before any learners are allocated to it, so
    this dashboard card must read curriculum's group rows rather than infer
    ownership from active learners.
    """
    display_name = clean_text(owner_name) or coach_staff_display_name(owner_email)
    owner_email = normalize_email(owner_email)
    candidate_keys = {
        staff_assignment_match_key(display_name),
        staff_assignment_match_key(owner_email),
    }
    if owner_email and "@" in owner_email:
        candidate_keys.add(staff_assignment_match_key(owner_email.split("@", 1)[0]))
    candidate_keys.discard("")
    if not candidate_keys:
        return []

    groups = []
    for group in group_authoring_detail_rows():
        coach_name = clean_text(group.get("coach"))
        if staff_assignment_match_key(coach_name) not in candidate_keys:
            continue
        groups.append({
            "id": clean_text(group.get("id")),
            "name": clean_text(group.get("name")) or "Unnamed group",
            "programmeId": clean_text(group.get("programmeId")),
            "programme": clean_text(group.get("programme")) or "--",
            "cohortId": clean_text(group.get("cohortId")),
            "cohort": clean_text(group.get("cohort")) or "--",
            "coach": coach_name,
            "status": clean_text(group.get("status")) or "planned",
            "schedule": clean_text(group.get("schedule")) or "--",
            "startDate": clean_text(group.get("startDate")),
            "endDate": clean_text(group.get("endDate")),
        })
    return sorted(
        groups,
        key=lambda item: (
            item.get("programme", "").casefold(),
            item.get("cohort", "").casefold(),
            item.get("name", "").casefold(),
        ),
    )


def live_session_matches_assigned_group(
    *,
    programme: str,
    cohort: str,
    group: str,
    assigned_groups: list[dict],
    programme_id: str = "",
    cohort_id: str = "",
    group_id: str = "",
) -> bool:
    """Return whether a curriculum live-session row belongs on this coach calendar.

    MCM/PR ownership comes from the learner caseload. Live sessions are
    curriculum-owned, so they must be limited to the groups officially assigned
    to the coach in curriculum.groups. Prefer stable ids where present, then
    fall back to normalized programme/cohort/group names for older rows.
    """
    if not assigned_groups:
        return False

    programme_key = staff_assignment_match_key(programme)
    cohort_key = staff_assignment_match_key(cohort)
    group_key = staff_assignment_match_key(group)
    programme_id_key = staff_assignment_match_key(programme_id)
    cohort_id_key = staff_assignment_match_key(cohort_id)
    group_id_key = staff_assignment_match_key(group_id)

    for assigned in assigned_groups:
        assigned_group_id_key = staff_assignment_match_key(assigned.get("id"))
        assigned_cohort_id_key = staff_assignment_match_key(assigned.get("cohortId"))
        assigned_programme_id_key = staff_assignment_match_key(assigned.get("programmeId"))
        assigned_group_key = staff_assignment_match_key(assigned.get("name"))
        assigned_cohort_key = staff_assignment_match_key(assigned.get("cohort"))
        assigned_programme_key = staff_assignment_match_key(assigned.get("programme"))

        if group_id_key and assigned_group_id_key and group_id_key == assigned_group_id_key:
            return True

        group_matches = bool(group_key and assigned_group_key and group_key == assigned_group_key)
        if not group_matches:
            continue

        cohort_matches = (
            bool(cohort_id_key and assigned_cohort_id_key and cohort_id_key == assigned_cohort_id_key)
            or bool(cohort_key and assigned_cohort_key and cohort_key == assigned_cohort_key)
            or not (cohort_id_key or cohort_key or assigned_cohort_id_key or assigned_cohort_key)
        )
        programme_matches = (
            bool(programme_id_key and assigned_programme_id_key and programme_id_key == assigned_programme_id_key)
            or bool(programme_key and assigned_programme_key and programme_key == assigned_programme_key)
            or not (programme_id_key or programme_key or assigned_programme_id_key or assigned_programme_key)
        )
        if cohort_matches and programme_matches:
            return True

    return False


def live_session_matches_curriculum_scope(
    *,
    programme: str,
    cohort: str,
    group: str,
    programme_id: str = "",
    cohort_id: str = "",
    group_id: str = "",
    learner_programme: str = "",
    learner_cohort: str = "",
    learner_group: str = "",
    learner_programme_id: str = "",
    learner_cohort_id: str = "",
    learner_group_id: str = "",
) -> bool:
    """Whether a curriculum live session belongs to one learner's placement.

    Learner-facing live sessions are curriculum-owned: the gate is the
    learner's programme/cohort/group allocation, not the coach assigned to them.
    Prefer stable ids, falling back to names for older rows.
    """
    learner_group_id_key = staff_assignment_match_key(learner_group_id)
    learner_group_key = staff_assignment_match_key(learner_group)
    if not (learner_group_id_key or learner_group_key):
        return False

    group_id_key = staff_assignment_match_key(group_id)
    group_key = staff_assignment_match_key(group)
    if learner_group_id_key and group_id_key:
        group_matches = learner_group_id_key == group_id_key
    else:
        group_matches = bool(learner_group_key and group_key and learner_group_key == group_key)
    if not group_matches:
        return False

    learner_cohort_id_key = staff_assignment_match_key(learner_cohort_id)
    learner_cohort_key = staff_assignment_match_key(learner_cohort)
    cohort_id_key = staff_assignment_match_key(cohort_id)
    cohort_key = staff_assignment_match_key(cohort)
    cohort_matches = (
        bool(learner_cohort_id_key and cohort_id_key and learner_cohort_id_key == cohort_id_key)
        or bool(learner_cohort_key and cohort_key and learner_cohort_key == cohort_key)
        or not (learner_cohort_id_key or learner_cohort_key or cohort_id_key or cohort_key)
    )
    if not cohort_matches:
        return False

    learner_programme_id_key = staff_assignment_match_key(learner_programme_id)
    learner_programme_key = staff_assignment_match_key(learner_programme)
    programme_id_key = staff_assignment_match_key(programme_id)
    programme_key = staff_assignment_match_key(programme)
    return (
        bool(learner_programme_id_key and programme_id_key and learner_programme_id_key == programme_id_key)
        or bool(learner_programme_key and programme_key and learner_programme_key == programme_key)
        or not (learner_programme_id_key or learner_programme_key or programme_id_key or programme_key)
    )


def fetch_cohort_selected_holidays(cohort_id: str) -> list[dict]:
    """The holidays that apply to a cohort: the bank holidays inside its dates.

    Curriculum owns the rule (``cohort_selected_holidays_by_cohort``) and this
    calls it rather than restating it, because the coach's calendar has to land
    on the same dates the cohort's own generator planned. It used to read the
    cohort's ``selected_holidays`` column instead -- a cache of what a ticked
    selection resolved to, against a holiday table curriculum no longer reads.
    """
    return cohort_selected_holidays_by_cohort([cohort_id]).get(clean_text(cohort_id)) or []


def collect_live_session_events(
    owner_email: str,
    owner_name: str,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    require_coach_access: bool = True,
    learner_scope: dict | None = None,
    learner_module_ids: list[str] | None = None,
    include_past: bool = False,
) -> list[dict]:
    if require_coach_access and not coach_has_live_session_access(owner_email):
        return []

    assigned_groups = fetch_official_assigned_groups(owner_email, owner_name) if require_coach_access else []
    if require_coach_access and not assigned_groups:
        return []

    program_configs_by_id = program_config_by_id(get_program_config_rows())
    weeks_by_module: dict[str, list[dict]] = {}
    # Module Builder retains archived/library copies for reuse and audit. They
    # must not participate in the delivery calendar or they shift the mapping
    # from authored weeks to Teams occurrences (for example, Oct 9 can vanish
    # behind an archived copy of an earlier week).
    for week in active_week_rows(
        authoring_fetch_all(AUTHORING_WEEKS_TABLE, ensure_tables=False)
    ):
        module_catalogue_id = clean_text(week.get("module_catalogue_id"))
        if module_catalogue_id:
            weeks_by_module.setdefault(module_catalogue_id, []).append(week)
    component_links_by_week: dict[str, str] = {}
    live_component_week_ids_by_module: dict[str, set[str]] = {}
    learner_live_components = []
    # columns= is load-bearing, not an optimisation: curriculum.components is
    # ~18k rows / 39MB (mostly settings_json) on a remote database -- a plain
    # select * here alone accounts for the "Timetable is taking too long to
    # load" timeout coaches were hitting (~23s vs ~0.5s narrowed).
    for component in active_component_rows(authoring_fetch_all(
        AUTHORING_COMPONENTS_TABLE,
        ensure_tables=False,
        columns=[
            "id", "type", "module_catalogue_id", "week_id", "live_sessions_link",
            "deleted_at", "is_programme_deleted", "library_state",
        ],
    )):
        if clean_text(component.get("type")).lower() != "live_session":
            continue
        if learner_module_ids and component.get("module_catalogue_id") in learner_module_ids:
            learner_live_components.append(component)
        week_id = clean_text(component.get("week_id"))
        module_id = clean_text(component.get("module_catalogue_id"))
        if module_id and week_id:
            live_component_week_ids_by_module.setdefault(module_id, set()).add(week_id)
        meeting_link = clean_text(component.get("live_sessions_link"))
        if week_id and meeting_link:
            component_links_by_week[week_id] = meeting_link
    shared_calendar_slots = {}
    if learner_live_components:
        # Learner assignments may point at a shared module whose Teams records
        # belong to another module. Use the same card-to-occurrence resolver as
        # My Learning; a matching ordinal alone can select the wrong meeting.
        from learner_api.calendar_shared_delivery import read_shared_calendar_slots

        shared_calendar_slots = read_shared_calendar_slots(learner_module_ids, learner_live_components)
    holidays_by_cohort_id: dict[str, list[dict]] = {}
    active_series_by_module: dict[str, dict] = {}
    occurrences_by_series_and_number: dict[tuple[str, int], dict] = {}
    try:
        active_series = authoring_fetch_all(
            LIVE_SESSIONS_TABLE,
            "status = %s",
            ["active"],
            "updated_at desc, created_at desc",
            ensure_tables=False,
        )
        for series in active_series:
            module_id = clean_text(series.get("module_catalogue_id"))
            if module_id and module_id not in active_series_by_module:
                active_series_by_module[module_id] = series
        active_series_ids = {clean_text(series.get("id")) for series in active_series}
        for occurrence in authoring_fetch_all(LIVE_SESSION_OCCURRENCES_TABLE, ensure_tables=False):
            series_id = clean_text(occurrence.get("live_session_id"))
            if series_id in active_series_ids:
                occurrences_by_series_and_number[
                    (series_id, parse_int(occurrence.get("session_number"), 0))
                ] = occurrence
    except Exception:
        # Calendar dates still work when legacy databases do not have the
        # Teams tracking tables yet.
        active_series_by_module = {}
        occurrences_by_series_and_number = {}
    today = date.today()

    events: list[dict] = []
    # The current curriculum source is the normalized authoring schema. Reading
    # it once avoids the previous duplicate full-table pass and skips DDL checks
    # that belong to deployment/migrations, not a dashboard GET request.
    training_rows = authoring_modules_as_training_rows(ensure_tables=False)
    for row in training_rows:
        if not is_operational_training_row(row):
            continue
        if not clean_text(row.get("module_name")):
            continue
        identity = programme_identity(row, program_configs_by_id)
        programme = identity["name"]
        cohort = actual_cohort_identity(row, programme)
        if not cohort:
            continue
        group = actual_group_identity(row, cohort["id"])
        if not group:
            continue
        if learner_module_ids is not None:
            if clean_text(row.get("_meta", {}).get("module_catalogue_id")) not in learner_module_ids:
                continue
        elif learner_scope is not None:
            if not live_session_matches_curriculum_scope(
                programme=programme,
                cohort=cohort["name"],
                group=group["name"],
                programme_id=clean_text(identity.get("sourceId")),
                cohort_id=cohort["id"],
                group_id=group["id"],
                learner_programme=clean_text(learner_scope.get("programme")),
                learner_cohort=clean_text(learner_scope.get("cohort")),
                learner_group=clean_text(learner_scope.get("group")),
                learner_programme_id=clean_text(learner_scope.get("programme_id")),
                learner_cohort_id=clean_text(learner_scope.get("cohort_id")),
                learner_group_id=clean_text(learner_scope.get("group_id")),
            ):
                continue
        else:
            if not live_session_matches_assigned_group(
                programme=programme,
                cohort=cohort["name"],
                group=group["name"],
                assigned_groups=assigned_groups,
                programme_id=clean_text(identity.get("sourceId")),
                cohort_id=cohort["id"],
                group_id=group["id"],
            ):
                continue

        module_start = row.get("start_date")
        if not module_start:
            continue

        module_catalogue_id = clean_text(row.get("_meta", {}).get("module_catalogue_id"))
        module_weeks = weeks_by_module.get(module_catalogue_id) or []
        # The stored session count leads: this builds a SESSION calendar, and a
        # module delivered Mon+Thu runs two sessions per authored week, so
        # counting week rows showed the coach half its live sessions.
        session_count = parse_int(row.get("sessions_number"), 0) or len(module_weeks)
        if session_count <= 0:
            continue

        cohort_id = clean_text(row.get("_meta", {}).get("cohort_id"))
        if cohort_id not in holidays_by_cohort_id:
            holidays_by_cohort_id[cohort_id] = fetch_cohort_selected_holidays(cohort_id)
        cohort_holidays = holidays_by_cohort_id[cohort_id]

        delivery_day_name = clean_text(row.get("session_week_day")) or parse_date(module_start).strftime("%A")
        plan = build_module_session_plan(module_start, session_count, delivery_day_name, cohort_holidays)
        if plan["warnings"]:
            continue

        ordered_weeks = sorted(module_weeks, key=lambda week: parse_int(week.get("week_number"), 0))
        delivery_days = delivery_days_per_week(row)
        tracked_series = active_series_by_module.get(module_catalogue_id) or {}
        tracked_series_id = clean_text(tracked_series.get("id"))
        live_week_ids = live_component_week_ids_by_module.get(module_catalogue_id) or set()
        has_authored_live_components = bool(live_week_ids)
        planned_live_sessions = []
        planned_week_counts = {}
        for session in plan["sessions"]:
            week_index = (session["sessionNumber"] - 1) // delivery_days
            week = ordered_weeks[week_index] if week_index < len(ordered_weeks) else None
            week_id = clean_text(week.get("id")) if week else ""
            # The authored live components are the authority for which weeks
            # actually have a Teams lecture. A content-only holiday slot has no
            # component and consumes no occurrence; an explicitly authored
            # live session is still included even when its date is flagged as a
            # holiday, preserving the LMS rule that holidays warn rather than
            # silently cancelling authored delivery.
            if has_authored_live_components and week_id not in live_week_ids:
                continue
            planned_live_sessions.append((session, week, week_id))
            planned_week_counts[week_id] = planned_week_counts.get(week_id, 0) + 1

        for occurrence_number, (session, week, week_id) in enumerate(planned_live_sessions, start=1):
            session_for_event = {**session, "sessionNumber": occurrence_number}
            tracked_occurrence = occurrences_by_series_and_number.get(
                (tracked_series_id, occurrence_number)
            )
            event = build_live_session_calendar_event(
                row,
                session_for_event,
                programme=programme,
                cohort=cohort["name"],
                group=group["name"],
                owner_email=owner_email or clean_text(tracked_series.get("organizer_email")),
                owner_name=owner_name,
                week_title=clean_text(week.get("title")) if week else None,
                tracked_series=tracked_series,
                tracked_occurrence=tracked_occurrence,
                component_meeting_link=week_id and component_links_by_week.get(week_id, ""),
            )
            shared_slot = shared_calendar_slots.get(week_id)
            if shared_slot and not tracked_series_id and planned_week_counts[week_id] == 1:
                from learner_api.calendar_shared_delivery import apply_shared_calendar_slot

                event = apply_shared_calendar_slot(event, shared_slot)
            if not include_past and date.fromisoformat(event["date"]) < today:
                continue
            events.append(event)

    if start_date or end_date:
        events = [
            event
            for event in events
            if (not start_date or date.fromisoformat(event["date"]) >= start_date)
            and (not end_date or date.fromisoformat(event["date"]) <= end_date)
        ]

    return events


def collect_tracked_live_session_events(
    owner_email: str,
    owner_name: str,
    *,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Read only already-scheduled live occurrences for the dashboard.

    The full timetable planner expands every authored week and component. That
    is useful on the timetable page, but unnecessarily expensive for the small
    dashboard preview.
    """
    if not coach_has_live_session_access(owner_email):
        return []

    assigned_groups = fetch_official_assigned_groups(owner_email, owner_name)
    if not assigned_groups:
        return []

    module_rows = authoring_fetch_all(AUTHORING_MODULES_TABLE, ensure_tables=False)
    modules_by_id = {
        clean_text(row.get("module_catalogue_id")): row
        for row in module_rows
        if clean_text(row.get("module_catalogue_id"))
        and live_session_matches_assigned_group(
            programme=clean_text(row.get("programme_name")),
            cohort=clean_text(row.get("cohort_name")),
            group=clean_text(row.get("group_name")),
            assigned_groups=assigned_groups,
            programme_id=clean_text(row.get("programme_id") or row.get("program_id")),
            cohort_id=clean_text(row.get("cohort_id")),
            group_id=clean_text(row.get("group_id")),
        )
    }
    if not modules_by_id:
        return []

    active_series = authoring_fetch_all(
        LIVE_SESSIONS_TABLE,
        "status = %s",
        ["active"],
        "updated_at desc, created_at desc",
        ensure_tables=False,
    )
    series_by_id = {
        clean_text(series.get("id")): series
        for series in active_series
        if clean_text(series.get("module_catalogue_id")) in modules_by_id
    }
    if not series_by_id:
        return []

    events = []
    for occurrence in authoring_fetch_all(LIVE_SESSION_OCCURRENCES_TABLE, ensure_tables=False):
        series = series_by_id.get(clean_text(occurrence.get("live_session_id")))
        if not series:
            continue
        scheduled_start = parse_date_value(occurrence.get("scheduled_start"))
        if isinstance(scheduled_start, datetime):
            occurrence_date = scheduled_start.date()
        elif isinstance(scheduled_start, date):
            occurrence_date = scheduled_start
        else:
            continue
        if occurrence_date < start_date or occurrence_date > end_date:
            continue

        module = modules_by_id.get(clean_text(series.get("module_catalogue_id"))) or {}
        session_number = max(1, parse_int(occurrence.get("session_number"), 1))
        tracked_occurrence = {**occurrence, "scheduled_start": scheduled_start}
        events.append(build_live_session_calendar_event(
            {
                "id": module.get("module_catalogue_id"),
                "module_name": module.get("title") or series.get("module_title"),
                "Tutor_name": module.get("tutor_name"),
                "session_week_day": module.get("session_week_day"),
                "session_start_time": module.get("session_start_time"),
                "session_end_time": module.get("session_end_time"),
            },
            {"date": occurrence_date.isoformat(), "sessionNumber": session_number},
            programme=clean_text(module.get("programme_name")),
            cohort=clean_text(module.get("cohort_name")),
            group=clean_text(module.get("group_name")),
            owner_email=owner_email,
            owner_name=owner_name,
            tracked_occurrence=tracked_occurrence,
            tracked_series=series,
        ))
    return events


def fetch_aptem_review_events(
    learners: list[LearnerProfile],
    aptem_by_profile: dict[int, int],
    *,
    owner_email: str,
    owner_name: str,
    start_date: date | None = None,
    end_date: date | None = None,
    projection_month: str | None = None,
    projection_review_ids: list | None = None,
    projection_event_keys: list | None = None,
) -> tuple[list[dict], set[int]]:
    """Shape verified Aptem rows as the common coach calendar event contract."""
    from .review_sources import imported_identity, imported_learner_identity, number_imported_events
    if not aptem_by_profile:
        return [], set()

    profiles_by_aptem: dict[str, LearnerProfile] = {}
    duplicate_aptem_ids: set[str] = set()
    learners_by_profile = {int(row.id): row for row in learners}
    for profile_id, aptem_id in aptem_by_profile.items():
        key = str(aptem_id)
        if key in profiles_by_aptem:
            duplicate_aptem_ids.add(key)
            continue
        learner = learners_by_profile.get(profile_id)
        if learner is not None:
            profiles_by_aptem[key] = learner
    for key in duplicate_aptem_ids:
        profiles_by_aptem.pop(key, None)
    if not profiles_by_aptem:
        return [], set()

    profile_ids = sorted(int(row.id) for row in profiles_by_aptem.values())
    source_identities = {row.id: imported_learner_identity(row) for row in profiles_by_aptem.values()}
    query_params: list = [profile_ids]
    # Number the complete imported plan before applying a role's display window.
    query = f"""
        SELECT lr.learner_id, lr.id, lr.aptem_review_id, lr.review_name,
               lr.review_type, lr.reviewer_name, lr.learner_name,
               lr.planned_scheduled_date, lr.completed_date, lr.status,
               jsonb_build_object(
                   'source_metadata', jsonb_build_object(
                       'Planned / Scheduled Date', lr.review_data #>> '{{source_metadata,Planned / Scheduled Date}}',
                       'Completed Date', lr.review_data #>> '{{source_metadata,Completed Date}}',
                       'Reviewer', lr.review_data #>> '{{source_metadata,Reviewer}}',
                       'Status', lr.review_data #>> '{{source_metadata,Status}}'
                   )
               ) AS review_data,
               lr.extraction_status, lr.last_error,
               (
                   EXISTS (
                       SELECT 1
                       FROM "Learner".review_sections section
                       WHERE section.review_id = lr.id
                   )
                   OR EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(
                           CASE
                               WHEN jsonb_typeof(lr.review_data -> 'sections') = 'array'
                               THEN lr.review_data -> 'sections'
                               ELSE '[]'::jsonb
                           END
                       ) embedded
                       WHERE (
                           jsonb_typeof(embedded -> 'fields') = 'array'
                           AND jsonb_array_length(embedded -> 'fields') > 0
                       ) OR (
                           jsonb_typeof(embedded -> 'tables') = 'array'
                           AND jsonb_array_length(embedded -> 'tables') > 0
                       ) OR NULLIF(BTRIM(COALESCE(
                           embedded ->> 'raw_text', embedded ->> 'rawText', ''
                       )), '') IS NOT NULL
                   )
               ) AS has_review_sections,
               lr.review_data ->> 'aptem_learner_id' AS source_learner_id
        FROM "Learner".reviews lr
        WHERE lr.learner_id = ANY(%s)
          AND NULLIF(BTRIM(lr.aptem_review_id), '') IS NOT NULL
          AND NULLIF(BTRIM(lr.review_type), '') IS NOT NULL
        ORDER BY COALESCE(lr.completed_date, lr.planned_scheduled_date), lr.id
    """
    if projection_month:
        # Monthly Focus does not render sequence numbers. Keep legacy raw date
        # formats as candidates for the shared parser; indexed ISO rows and
        # explicitly moved bookings can be bounded before serialization.
        query, ordering = query.rsplit('ORDER BY', 1)
        planned = "coalesce(nullif(lr.planned_scheduled_date::text,''),lr.review_data #>> '{source_metadata,Planned / Scheduled Date}','')"
        completed = "coalesce(nullif(lr.completed_date::text,''),lr.review_data #>> '{source_metadata,Completed Date}','')"
        query += f''' AND (
            left({planned},7)=%s
            OR left({completed},7)=%s
            OR ({planned}<>'' AND {planned} !~ '^[0-9]{{4}}-[0-9]{{2}}-')
            OR ({completed}<>'' AND {completed} !~ '^[0-9]{{4}}-[0-9]{{2}}-')
            OR lr.id=ANY(%s) OR ('imported-review:' || lr.aptem_review_id)=ANY(%s))'''
        query_params.extend([projection_month, projection_month, projection_review_ids or [], projection_event_keys or []])
        query += ' ORDER BY' + ordering
    connection = connections[get_learner_db_alias()]
    with connection.cursor() as cursor:
        cursor.execute(query, query_params)
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    monthly_types = {clean_text(value).casefold() for value in REVIEW_TYPES["monthly-coaching"]}
    events: list[dict] = []
    contributing_profiles: set[int] = set()
    seen_review_ids: set[str] = set()
    for row in rows:
        try:
            profile_id = int(row.get("learner_id"))
        except (TypeError, ValueError):
            continue
        learner = learners_by_profile.get(profile_id)
        aptem_review_id = clean_text(row.get("aptem_review_id"))
        if learner is None or not aptem_review_id or aptem_review_id in seen_review_ids:
            continue
        effective_aptem_id = aptem_by_profile.get(profile_id)
        source_learner_id = clean_text(row.get("source_learner_id"))
        # New reporting rows carry their Aptem learner id and must agree with
        # the stable effective identity. Older imported review kinds predate
        # that reporting projection; their ownership remains the stable
        # Learner.reviews.learner_id -> profile -> effective Aptem bridge.
        if source_learner_id and source_learner_id != str(effective_aptem_id):
            continue
        review = _serialize_review(row, {})
        planned_date = parse_schedule_date(review.get("plannedDate"))
        completed_date = parse_schedule_date(review.get("completedDate"))
        display_date = completed_date or planned_date
        if display_date is None:
            continue
        seen_review_ids.add(aptem_review_id)
        contributing_profiles.add(profile_id)
        review_type = clean_text(review.get("type"))
        event_type = "mcr" if review_type.casefold() in monthly_types else "progress-review"
        status = clean_text(review.get("status"))
        if completed_date is not None:
            status = CoachCalendarEvent.STATUS_COMPLETED
        if status not in {
            CoachCalendarEvent.STATUS_NOT_SCHEDULED,
            CoachCalendarEvent.STATUS_SCHEDULED,
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            CoachCalendarEvent.STATUS_COMPLETED,
            CoachCalendarEvent.STATUS_CANCELLED,
            "confirmed",
            "pending",
        }:
            status = CoachCalendarEvent.STATUS_NOT_SCHEDULED
        event_key = f"imported-review:{aptem_review_id}"
        target_date = planned_date or completed_date
        planned_time = clean_text(review.get("plannedTime"))
        start_hour = 9
        if planned_time and re.match(r"^\d{2}:\d{2}$", planned_time):
            hour, minute = (int(part) for part in planned_time.split(":"))
            start_hour = hour + (minute / 60)
        events.append({
            **imported_identity(review, profile_id),
            "eventKey": event_key,
            "id": event_key,
            "ownerEmail": owner_email,
            "ownerName": owner_name,
            "learnerId": str(profile_id),
            **source_identities[profile_id],
            "learner": clean_text(getattr(learner, "username", None)) or clean_text(row.get("learner_name")) or "Unknown learner",
            "email": clean_text(getattr(learner, "email", None)) or None,
            "programme": clean_text(getattr(learner, "programme", None)) or "--",
            "cohort": clean_text(getattr(learner, "cohort", None)) or "--",
            "group": clean_text(getattr(learner, "group_name", None)) or "--",
            "source": event_type,
            "reviewSource": "aptem",
            "aptemReviewId": aptem_review_id,
            "reviewerName": clean_text(review.get("reviewerName")) or None,
            "reviewCompletedAt": completed_date.isoformat() if completed_date else None,
            "hasReviewForm": _imported_review_has_usable_form(
                review,
                has_normalized_sections=bool(row.get("has_review_sections")),
            ),
            "sequence": 1,
            "title": clean_text(review.get("name")) or review_type or EVENT_TYPE_FALLBACK_TITLES.get(event_type, "Review"),
            "type": "coaching" if event_type == "mcr" else "review",
            "targetDate": target_date.isoformat(),
            "date": display_date.isoformat(),
            "year": display_date.year,
            "month": display_date.month - 1,
            "dayOfMonth": display_date.day,
            "dayOfWeek": display_date.weekday(),
            "startHour": start_hour,
            "endHour": start_hour + 1,
            "durationMinutes": 60,
            "timeLabel": f"{planned_time} - 60 min" if planned_time else "Time TBC",
            "isTimeEstimated": not bool(planned_time),
            "priority": generated_event_priority(status, target_date, display_date),
            "status": status,
            "sourceStatus": review.get("status"),
            "rawPlanned": planned_date.isoformat() if planned_date else None,
            "rawStatus": clean_text(row.get("status")),
            "notes": f"Reviewer: {clean_text(review.get('reviewerName'))}" if clean_text(review.get("reviewerName")) else "",
            "scheduledDate": planned_date.isoformat() if planned_date else None,
            "scheduledTime": planned_time or None,
            "meetingProvider": "",
            "meetingLink": "",
            "graphWebLink": "",
            "platform": "--",
            "location": "--",
        })
    numbered = number_imported_events(events)
    return [event for event in numbered
            if (not start_date or event["date"] >= start_date.isoformat())
            and (not end_date or event["date"] <= end_date.isoformat())], contributing_profiles


def fetch_aptem_mcm_profile_ids(aptem_by_profile: dict[int, int]) -> set[int]:
    """Profiles with any verified Aptem MCM, without loading full review history."""
    if not aptem_by_profile:
        return set()
    monthly_types = sorted({clean_text(value).casefold() for value in REVIEW_TYPES["monthly-coaching"]})
    query = """
        SELECT DISTINCT lr.learner_id,
               lr.review_data ->> 'aptem_learner_id' AS source_learner_id
        FROM "Learner".reviews lr
        WHERE lr.learner_id = ANY(%s)
          AND LOWER(BTRIM(lr.review_type)) = ANY(%s)
          AND NULLIF(BTRIM(lr.aptem_review_id), '') IS NOT NULL
    """
    with connections[get_learner_db_alias()].cursor() as cursor:
        cursor.execute(query, [sorted(aptem_by_profile), monthly_types])
        rows = cursor.fetchall()
    matches = set()
    for profile_id, source_learner_id in rows:
        profile_id = int(profile_id)
        effective_aptem_id = aptem_by_profile.get(profile_id)
        source_id = clean_text(source_learner_id)
        if effective_aptem_id and (not source_id or source_id == str(effective_aptem_id)):
            matches.add(profile_id)
    return matches


def resolve_coach_review_events(
    owner_email: str,
    owner_name: str,
    learners: list[LearnerProfile],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    source_schedule_rows: tuple[dict, dict] | None = None,
) -> dict:
    """An Aptem identity always owns its imported Review source, even if empty."""
    unavailable_profiles: frozenset[int] = frozenset()
    try:
        aptem_by_profile, identity_conflicts = resolve_effective_aptem_ids(learners, strict=True)
    except ReviewEnrolmentIdentityUnavailable as exc:
        # A missing linked row affects only its learners. Keep them out of both
        # source branches; an unknown identity must never become Native fallback.
        # Real query/connection failures still propagate to the caller.
        unavailable_profiles = exc.profile_ids
        learners = [row for row in learners if int(row.id) not in unavailable_profiles]
        aptem_by_profile, identity_conflicts = resolve_effective_aptem_ids(learners, strict=True)
    aptem_reviews_available = True
    try:
        aptem_events, aptem_contributors = fetch_aptem_review_events(
            learners,
            aptem_by_profile,
            owner_email=owner_email,
            owner_name=owner_name,
            # Window filtering follows local booking enrichment in the timetable.
            start_date=None,
            end_date=None,
        )
    except DatabaseError as exc:
        # Imported reviews are one source in a mixed timetable. A slow or
        # temporarily unavailable Aptem mirror must not turn Curriculum reviews,
        # catch-ups and the rest of the coach calendar into a 503.
        logger.warning("Could not load Aptem reviews for coach timetable: %s", exc)
        aptem_reviews_available = False
        aptem_events, aptem_contributors = [], set()
    commercial_rows, enrolment_rows = (
        source_schedule_rows if source_schedule_rows is not None else fetch_source_schedule_rows(learners)
    )
    native_learners = [
        learner for learner in learners
        if int(learner.id) not in aptem_by_profile and int(learner.id) not in identity_conflicts
    ]
    events: list[dict] = list(aptem_events)
    # Imported PRs use the same linked employer as native PR invitations.
    # Resolve once per learner, using the already loaded source identities.
    learners_by_id = {str(learner.id): learner for learner in learners}
    employer_attendees: dict[str, dict[str, str]] = {}
    for index, event in enumerate(events):
        learner_id = str(event.get("learnerId") or "")
        learner = learners_by_id.get(learner_id)
        if learner is None or review_event_category(event) != "progress-review":
            continue
        if learner_id not in employer_attendees:
            source_row = resolve_caseload_source_row(
                learner, commercial_rows=commercial_rows, enrolment_rows=enrolment_rows,
            )
            employer_attendees[learner_id] = learner_employer_attendee(learner, source_row)
        attendee = employer_attendees[learner_id]
        if attendee:
            events[index] = {**event, "employerName": attendee["name"], "employerEmail": attendee["email"]}
    issues: list[dict[str, str]] = [
        {"learnerId": str(profile_id), "code": "aptem_identity_conflict"}
        for profile_id in sorted(identity_conflicts)
    ]
    issues.extend(
        {"learnerId": str(profile_id), "code": "review_identity_unavailable"}
        for profile_id in sorted(unavailable_profiles)
    )
    if not aptem_reviews_available:
        issues.extend(
            {"learnerId": str(profile_id), "code": "aptem_reviews_unavailable"}
            for profile_id in sorted(aptem_by_profile)
        )
    if aptem_reviews_available:
        issues.extend({"learnerId": str(profile_id), "code": "aptem_source_missing"}
                      for profile_id in sorted(set(aptem_by_profile) - aptem_contributors))
    counts = {
        "progressReviewRows": sum(1 for event in aptem_events if review_event_category(event) == "progress-review"),
        "mcrRows": sum(1 for event in aptem_events if event["source"] == "mcr"),
        "reviewRows": sum(1 for event in aptem_events if review_event_category(event) == "review"),
        "learnersWithDates": len(aptem_contributors),
        "reviewAnchorSkipped": 0,
        "reviewAnchorSkipReasons": {},
        "aptemReviewRows": len(aptem_events),
        "curriculumReviewRows": 0,
        "aptemLearners": len(aptem_by_profile),
        "curriculumLearners": len(native_learners),
        "curriculumMcmFallbackLearners": 0,
    }
    review_template_cache: dict[str, list[dict]] = {}

    for learner in native_learners:
        programme_id = resolve_curriculum_programme_id(getattr(learner, "programme_id", None) or getattr(learner, "programme", None))
        template_identifiers = curriculum_review_instances.programme_review_template_identifiers(
            programme_id, template_cache=review_template_cache,
        )
        if not programme_id:
            issues.append({"learnerId": str(learner.id), "code": "missing_curriculum_programme"})
        elif not template_identifiers:
            issues.append({"learnerId": str(learner.id), "code": "no_enabled_review_templates"})

        review_anchor, anchor_reason = resolve_review_anchor_date(learner.id, commercial_rows, enrolment_rows)
        if review_anchor is None:
            log_review_anchor_skip(learner, anchor_reason, programme_id=programme_id, template_cache=review_template_cache)
            counts["reviewAnchorSkipped"] += 1
            counts["reviewAnchorSkipReasons"][anchor_reason] = counts["reviewAnchorSkipReasons"].get(anchor_reason, 0) + 1
            if template_identifiers:
                issue_code = {
                    REVIEW_ANCHOR_MISSING_ROW: "missing_learner_enrolment",
                    REVIEW_ANCHOR_MISSING_START: "missing_learner_start_date",
                    REVIEW_ANCHOR_INVALID_START: "invalid_learner_start_date",
                }.get(anchor_reason, "review_schedule_unavailable")
                issues.append({"learnerId": str(learner.id), "code": issue_code})
            continue

        learner_start_date, learner_end_date = resolve_schedule_window(
            learner.id, commercial_rows, enrolment_rows, learner,
        )
        if not learner_start_date or not learner_end_date or learner_end_date <= learner_start_date:
            issues.append({"learnerId": str(learner.id), "code": "review_schedule_unavailable"})
            continue

        source_row = resolve_caseload_source_row(
            learner, commercial_rows=commercial_rows, enrolment_rows=enrolment_rows,
        )
        employer_attendee = learner_employer_attendee(learner, source_row)
        counts["learnersWithDates"] += 1
        learner_status = clean_text(getattr(learner, "programme_status", None) or getattr(learner, "status", None))
        window_start = start_date or learner_start_date
        window_end = end_date or learner_end_date
        for occurrence in resolve_curriculum_review_occurrences(
            programme_id=programme_id,
            learner_id=learner.id,
            learner_status=learner_status,
            learner_start_date=review_anchor,
            window_start=window_start,
            window_end=window_end,
            template_cache=review_template_cache,
            learner_scope={
                "cohort_id": getattr(learner, "cohort_id", None),
                "cohort": getattr(learner, "cohort", None),
                "group_id": getattr(learner, "group_id", None),
                "group": getattr(learner, "group_name", None),
            },
        ):
            event_type = review_event_type_for_type_code(occurrence.get("reviewTypeCode"))
            event = build_generated_calendar_event(
                learner=learner,
                owner_email=owner_email,
                owner_name=owner_name,
                event_type=event_type,
                sequence=occurrence["occurrenceNumber"] or 1,
                target_date=occurrence["targetDate"],
                source_row=source_row,
                employer_attendee=employer_attendee if event_type == "progress-review" else None,
                review_template_id=occurrence["reviewTemplateId"],
                review_title=occurrence["reviewName"],
                review_type=occurrence,
                occurrence_source=occurrence.get("occurrenceSource", curriculum_review_instances.OCCURRENCE_SOURCE_GENERATED),
                manual_addition_id=occurrence.get("additionId"),
            )
            event["reviewSource"] = "curriculum"
            events.append(event)
            counts["curriculumReviewRows"] += 1
            if event_type == "mcr":
                counts["mcrRows"] += 1
            elif event_type == "progress-review":
                counts["progressReviewRows"] += 1
            else:
                counts["reviewRows"] += 1

    return {
        "events": events,
        "reviewGenerationIssues": issues,
        "sourceCounts": counts,
        "aptemProfileIds": set(aptem_by_profile),
    }


def collect_generated_timetable(
    owner_email: str,
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    include_live_sessions: bool = True,
    include_scheduler_queues: bool = True,
) -> dict:
    active_rows = fetch_owner_active_learner_profiles(owner_email)
    review_rows = fetch_caseload_timetable_profiles(owner_email)
    learner_profile_map = build_learner_profile_map(review_rows)
    staff_owner_name = coach_staff_display_name(owner_email)
    owner_name = staff_owner_name or next(
        (clean_text(row.coach_name) for row in active_rows if clean_text(row.coach_name)),
        "Med Maher",
    )
    live_session_events = []
    if include_live_sessions:
        try:
            live_session_events = collect_live_session_events(
                owner_email,
                owner_name,
                start_date=start_date,
                # When the caller asks for an explicit date window, that window
                # is the intended bound -- the extra "drop anything before
                # today" guard (collect_live_session_events' default) would
                # silently hide in-window sessions already delivered earlier in
                # the requested range (e.g. the all-coaches week grid on a
                # Wednesday losing Monday's live session). The windowless
                # timetable keeps its forward-looking default.
                end_date=end_date,
                include_past=bool(start_date or end_date),
            )
        except Exception as exc:
            logger.warning("Could not collect live session events for %s: %s", owner_email, exc)

    resolved_reviews = resolve_coach_review_events(
        owner_email,
        owner_name,
        review_rows,
        start_date=start_date,
        end_date=end_date,
    )
    generated_events: list[dict] = resolved_reviews["events"]
    resolved_counts = resolved_reviews["sourceCounts"]
    source_counts = {
        **resolved_counts,
        "catchUpRows": 0,
        "liveSessionRows": len(live_session_events),
        "caseloadLearners": len(review_rows),
    }
    review_generation_issues = resolved_reviews["reviewGenerationIssues"]

    # Saved appointments survive changes to programme templates and event keys.
    # Reconcile legacy keys with generated items so a booked event appears once.
    generated_keys = {event["eventKey"] for event in generated_events}
    aptem_profile_ids = resolved_reviews["aptemProfileIds"]
    from .review_sources import load_imported_links, standalone_review_records
    aptem_events = [event for event in generated_events if event.get("reviewSource") == "aptem"]
    aptem_record_map, aptem_overlay_map = load_imported_links(aptem_events)
    generated_keys.update(record.event_key for record in aptem_record_map.values() if record)
    persisted_standalone_records = standalone_review_records(
        fetch_standalone_event_records(owner_email), review_rows, aptem_profile_ids, linked_keys=generated_keys)
    curriculum_events = [event for event in generated_events if event.get("reviewSource") == "curriculum"]
    legacy_keys = [
        build_timetable_event_key(
            int(event["learnerId"]),
            event["source"],
            event["sequence"],
            parse_schedule_date(event["targetDate"]),
        )
        for event in curriculum_events
    ]
    legacy_records = fetch_calendar_event_records(owner_email, legacy_keys) if legacy_keys else {}
    stored_records = {record.event_key: record for record in persisted_standalone_records}
    stored_records.update(legacy_records)
    record_map = curriculum_review_instances.reconcile_review_event_keys(
        curriculum_events,
        list(stored_records.values()),
    )
    persisted_standalone_records = [
        record
        for record in persisted_standalone_records
        if record.event_key not in record_map
        and not (
            getattr(record, "review_template_id", "")
            and record.status == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        )
    ]
    # One resolution pass for the whole list -- a booked Review keeps its
    # template's Review Type, and this is the only place these rows are shaped.
    standalone_review_type_fields = review_type_fields_by_template(
        getattr(record, "review_template_id", "") for record in persisted_standalone_records
    )
    persisted_standalone_events = [
        build_catchup_calendar_event(
            record,
            owner_name=owner_name,
            learner=learner_profile_map.get(record.learner_id),
            review_type_fields=standalone_review_type_fields,
        )
        for record in persisted_standalone_records
    ]
    scheduler_catchups: list[dict] = []
    if include_scheduler_queues:
        persisted_catchup_records = [
            record
            for record in persisted_standalone_records
            if clean_text(record.event_type).lower() == CATCH_UP_EVENT_TYPE
        ]
        scheduler_catchups = build_catchup_scheduler_events(
            owner_email,
            owner_name,
            active_rows,
            learner_profile_map,
            persisted_catchup_records,
        )
    source_counts["catchUpRows"] = sum(1 for event in persisted_standalone_events if event["source"] == CATCH_UP_EVENT_TYPE)

    curriculum_keys = [event["eventKey"] for event in curriculum_events]
    if curriculum_keys:
        record_map.update(fetch_calendar_event_records(owner_email, curriculum_keys))
    events = [overlay_calendar_record(event, record_map.get(event["eventKey"])) for event in curriculum_events]
    events.extend(overlay_calendar_record(event, aptem_record_map.get(event["eventKey"]),
                                         aptem_overlay_map.get(event["eventKey"])) for event in aptem_events)
    events.extend(persisted_standalone_events)
    events.extend(live_session_events)

    if start_date or end_date:
        filtered_events = []
        for event in events:
            event_date = parse_schedule_date(event["date"])
            if not event_date:
                continue
            if start_date and event_date < start_date:
                continue
            if end_date and event_date > end_date:
                continue
            filtered_events.append(event)
        events = filtered_events

    source_needs_scheduling = {
        "mcr": sum(1 for event in events if event["source"] == "mcr" and event["status"] == CoachCalendarEvent.STATUS_NOT_SCHEDULED),
        "progress-review": sum(1 for event in events if review_event_category(event) == "progress-review" and event["status"] == CoachCalendarEvent.STATUS_NOT_SCHEDULED),
        CATCH_UP_EVENT_TYPE: sum(
            1
            for event in events
            if event["source"] == CATCH_UP_EVENT_TYPE and event["status"] == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        ),
        "student-support": sum(
            1
            for event in events
            if event["source"] == "student-support" and event["status"] == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        ),
        LMS_INTRODUCTION_EVENT_TYPE: sum(
            1
            for event in events
            if event["source"] == LMS_INTRODUCTION_EVENT_TYPE and event["status"] == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        ),
    }
    needs_scheduling = sum(source_needs_scheduling.values())
    events = assign_timetable_slots(events)
    events = sorted(events, key=lambda event: (event["date"], event["startHour"], event["learner"]))
    summary = build_timetable_summary(events, needs_scheduling, source_counts, source_needs_scheduling)
    summary["timeAvailability"] = "MCR and progress review dates use Aptem for Aptem-linked learners and the Curriculum review schedule for native learners; catch-up and support sessions can be created by the coach for any learner in their caseload."
    return {
        "owner_name": owner_name,
        "events": events,
        "summary": summary,
        "reviewGenerationIssues": review_generation_issues,
        "schedulerQueues": {
            "catchUp": scheduler_catchups,
        },
    }


def normalize_duration_minutes(value) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ValueError("durationMinutes must be a whole number.")
    if minutes < 15 or minutes > 480:
        raise ValueError("durationMinutes must be between 15 and 480.")
    return minutes


IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,254}$")


def calendar_idempotency_key(request) -> str:
    """Return the caller's stable booking-operation identity.

    The key is scoped to the authenticated Coach by the database constraint.
    Reusing it with different booking inputs is rejected rather than silently
    changing the meaning of an already durable operation.
    """
    value = clean_text(request.headers.get("Idempotency-Key"))
    if not value:
        raise ValueError("Idempotency-Key header is required.")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise ValueError("Idempotency-Key must be 8 to 255 URL-safe characters.")
    return value


def booking_request_matches_record(
    record: CoachCalendarEvent,
    *,
    learner_id: int,
    session_type: str,
    scheduled_date: date,
    scheduled_time: time,
    duration_minutes: int,
    notes: str,
) -> bool:
    return (
        record.learner_id == learner_id
        and clean_text(record.event_type).lower() == session_type
        and record.scheduled_date == scheduled_date
        and record.scheduled_time == scheduled_time
        and record.duration_minutes == duration_minutes
        and clean_text(record.notes) == notes
    )


LEARNER_CALENDAR_LOCK_SCOPE = "__calendar_booking__"
class LearnerCalendarConflict(ValueError):
    def __init__(self, message: str, record: CoachCalendarEvent | None = None):
        super().__init__(message)
        self.record = record


class LearnerSessionAlreadyBooked(LearnerCalendarConflict):
    """The learner already has this session type in the requested week."""


def lock_learner_calendar(learner_id: int) -> None:
    """Serialize all booking types for one learner inside the current transaction."""
    CoachCalendarSequence.objects.select_for_update().get_or_create(
        learner_id=learner_id,
        event_type=LEARNER_CALENDAR_LOCK_SCOPE,
        defaults={"last_sequence": 0},
    )


def ensure_coach_booking_available(owner_email, scheduled_date, scheduled_time, duration_minutes, *, exclude_record_id=None):
    from .booking_availability import lock_coach_calendar, ensure_coach_calendar_available
    lock_coach_calendar(owner_email)
    ensure_coach_calendar_available(owner_email, scheduled_date, scheduled_time, duration_minutes,
                                    exclude_record_id=exclude_record_id)


def find_learner_calendar_conflict(
    *,
    learner_id: int,
    learner_email: str,
    scheduled_date: date,
    scheduled_time: time,
    duration_minutes: int,
    exclude_record_id: int | None = None,
) -> CoachCalendarEvent | None:
    """Return an existing LMS session whose local time overlaps this slot."""
    from .booking_reservations import OCCUPANCY_FIELDS, calendar_record_occupies_slot
    requested_start = datetime.combine(scheduled_date, scheduled_time)
    requested_end = requested_start + timedelta(minutes=duration_minutes)
    identity = Q(learner_id=learner_id)
    normalized_email = normalize_email(learner_email)
    if normalized_email:
        # Email keeps the check effective when an Active_users mirror is
        # recreated and the same learner receives a new numeric id.
        identity |= Q(learner_email__iexact=normalized_email)

    candidates = CoachCalendarEvent.objects.filter(
        identity,
        scheduled_date__range=(scheduled_date - timedelta(days=1), requested_end.date()),
        scheduled_time__isnull=False,
    )
    if exclude_record_id is not None:
        candidates = candidates.exclude(pk=exclude_record_id)

    for existing in candidates.only(
        "id", "sequence", *OCCUPANCY_FIELDS
    ):
        if not calendar_record_occupies_slot(existing):
            continue
        existing_start = datetime.combine(existing.scheduled_date, existing.scheduled_time)
        existing_end = existing_start + timedelta(
            minutes=existing.duration_minutes or TIMETABLE_DEFAULT_DURATION_MINUTES
        )
        if requested_start < existing_end and requested_end > existing_start:
            return existing
    return None


def ensure_learner_calendar_available(
    *,
    learner_id: int,
    learner_email: str,
    scheduled_date: date,
    scheduled_time: time,
    duration_minutes: int,
    exclude_record_id: int | None = None,
) -> None:
    existing = find_learner_calendar_conflict(
        learner_id=learner_id,
        learner_email=learner_email,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
        duration_minutes=duration_minutes,
        exclude_record_id=exclude_record_id,
    )
    if existing is not None:
        raise LearnerCalendarConflict(
            f"That time overlaps {calendar_record_label(existing)}, booked for "
            f"{calendar_record_slot(existing)}. Would you like to reschedule that session instead?",
            existing,
        )


def find_learner_same_session_in_week(
    *,
    learner_id: int,
    learner_email: str,
    session_type: str,
    scheduled_date: date,
    exclude_record_id: int | None = None,
) -> CoachCalendarEvent | None:
    """Return this learner's active booking of the same type, Monday-Sunday."""
    week_start = scheduled_date - timedelta(days=scheduled_date.weekday())
    week_end = week_start + timedelta(days=6)
    identity = Q(learner_id=learner_id)
    normalized_email = normalize_email(learner_email)
    if normalized_email:
        identity |= Q(learner_email__iexact=normalized_email)

    candidates = CoachCalendarEvent.objects.filter(
        identity,
        event_type__iexact=clean_text(session_type),
        scheduled_date__range=(week_start, week_end),
        scheduled_time__isnull=False,
        status__in=(
            CoachCalendarEvent.STATUS_SCHEDULED,
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
        ),
    )
    if exclude_record_id is not None:
        candidates = candidates.exclude(pk=exclude_record_id)
    return candidates.order_by("scheduled_date", "scheduled_time", "pk").first()


def calendar_record_label(record: CoachCalendarEvent) -> str:
    title = BOOKED_EVENT_TITLES.get(clean_text(record.event_type).lower(), "Coaching Session")
    return f'“{title} {record.sequence}”' if record.sequence else f'“{title}”'


def calendar_record_slot(record: CoachCalendarEvent) -> str:
    scheduled_date = record.scheduled_date
    scheduled_time = record.scheduled_time
    if not scheduled_date:
        return "its current date"
    date_label = (
        f"{scheduled_date.strftime('%A')}, {scheduled_date.day} "
        f"{scheduled_date.strftime('%B %Y')}"
    )
    if not scheduled_time:
        return date_label
    return f"{date_label} at {scheduled_time.strftime('%H:%M')}"


def ensure_learner_session_not_booked_in_week(
    *,
    learner_id: int,
    learner_email: str,
    session_type: str,
    scheduled_date: date,
    exclude_record_id: int | None = None,
) -> None:
    existing = find_learner_same_session_in_week(
        learner_id=learner_id,
        learner_email=learner_email,
        session_type=session_type,
        scheduled_date=scheduled_date,
        exclude_record_id=exclude_record_id,
    )
    if existing is None:
        return
    raise LearnerSessionAlreadyBooked(
        f"You already have {calendar_record_label(existing)} booked for "
        f"{calendar_record_slot(existing)}. The same session type can only be booked once "
        f"in the same week. Would you like to reschedule it instead?",
        existing,
    )


def reserve_coach_calendar_booking(
    *,
    owner_email: str,
    owner_name: str,
    learner_id: int,
    learner_name: str,
    learner_email: str,
    session_type: str,
    scheduled_date: date,
    scheduled_time: time,
    duration_minutes: int,
    notes: str,
    idempotency_key: str,
    initial_status: str = CoachCalendarEvent.STATUS_SCHEDULED,
    local_mcm_review_id: int | str | None = None,
) -> tuple[CoachCalendarEvent, bool]:
    """Durably reserve one booking before any external call.

    ``CoachCalendarSequence`` is the stable row locked for the whole allocation.
    Its unique scope and the event constraints make this safe across processes,
    not merely threads in one Python worker.
    """

    owner_email = normalize_email(owner_email)
    session_type = clean_text(session_type).lower()
    non_delivery_reason = england_non_delivery_reason(scheduled_date)
    if non_delivery_reason:
        raise LearnerCalendarConflict(non_delivery_reason)
    if initial_status not in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_NOT_SCHEDULED}:
        raise ValueError("Unsupported initial booking status.")

    def existing_replay() -> CoachCalendarEvent | None:
        return CoachCalendarEvent.objects.filter(
            owner_email=owner_email,
            idempotency_key=idempotency_key,
        ).first()

    existing = existing_replay()
    if existing:
        if not booking_request_matches_record(
            existing,
            learner_id=learner_id,
            session_type=session_type,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=notes,
        ):
            raise ValueError("Idempotency-Key was already used for a different booking.")
        return existing, False

    try:
        with transaction.atomic():
            # One shared row per learner serializes bookings even when two
            # coaches submit different session types at the same instant.
            lock_learner_calendar(learner_id)
            if local_mcm_review_id is not None:
                from .local_mcm_bookings import local_mcm_event_key
                if session_type != "mcr":
                    raise LearnerCalendarConflict("Only an MCM can use this local review identity.")
                review_key = local_mcm_event_key(learner_id, local_mcm_review_id)
                matches = imported_review_calendar_rows(owner_email, review_key, lock=True)
                if len(matches) > 1:
                    raise LearnerCalendarConflict("Multiple bookings are linked to this review. Contact support before booking again.")
                if matches:
                    existing = matches[0]
                    from .reconciliation_links import for_calendar
                    reconciliation = for_calendar(existing)
                    if (normalize_email(existing.owner_email) != owner_email
                            or existing.learner_id != learner_id or existing.event_type != "mcr"
                            or ((existing.review_template_id or existing.review_instance_id) and not reconciliation)
                            or existing.status != CoachCalendarEvent.STATUS_SCHEDULED):
                        raise LearnerCalendarConflict("This review already has an incompatible booking.")
                    if (existing.scheduled_date, existing.scheduled_time, existing.duration_minutes) != (
                            scheduled_date, scheduled_time, duration_minutes):
                        raise LearnerCalendarConflict("This review is already booked at another time. Open the existing booking to reschedule it.")
                    return existing, False
            # Recheck after entering the transaction. The unique constraint is
            # the final authority if another transaction is still uncommitted.
            existing = (
                CoachCalendarEvent.objects.select_for_update()
                .filter(owner_email=owner_email, idempotency_key=idempotency_key)
                .first()
            )
            if existing:
                if not booking_request_matches_record(
                    existing,
                    learner_id=learner_id,
                    session_type=session_type,
                    scheduled_date=scheduled_date,
                    scheduled_time=scheduled_time,
                    duration_minutes=duration_minutes,
                    notes=notes,
                ):
                    raise ValueError("Idempotency-Key was already used for a different booking.")
                return existing, False

            ensure_learner_session_not_booked_in_week(
                learner_id=learner_id,
                learner_email=learner_email,
                session_type=session_type,
                scheduled_date=scheduled_date,
            )
            ensure_learner_calendar_available(
                learner_id=learner_id,
                learner_email=learner_email,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                duration_minutes=duration_minutes,
            )
            ensure_coach_booking_available(owner_email, scheduled_date, scheduled_time, duration_minutes)

            current_max = (
                CoachCalendarEvent.objects.filter(
                    learner_id=learner_id,
                    event_type=session_type,
                ).aggregate(max_seq=Max("sequence"))["max_seq"]
                or 0
            )
            counter, _ = CoachCalendarSequence.objects.select_for_update().get_or_create(
                learner_id=learner_id,
                event_type=session_type,
                defaults={"last_sequence": current_max},
            )
            # Handles a counter introduced before a legacy/manual event import.
            counter.last_sequence = max(counter.last_sequence, current_max) + 1
            counter.save(update_fields=["last_sequence", "updated_at"])
            sequence = counter.last_sequence

            record = CoachCalendarEvent.objects.create(
                event_key=build_timetable_event_key(
                    learner_id, session_type, sequence, scheduled_date
                ),
                idempotency_key=idempotency_key,
                owner_email=owner_email,
                owner_name=owner_name,
                learner_id=learner_id,
                learner_name=learner_name,
                learner_email=learner_email,
                event_type=session_type,
                sequence=sequence,
                target_date=scheduled_date,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                duration_minutes=duration_minutes,
                status=initial_status,
                sync_state=CoachCalendarEvent.SYNC_PENDING,
                notes=notes,
            )
            return record, True
    except IntegrityError:
        # A same-key concurrent transaction may have committed while this one
        # waited on the unique index. The failed transaction has rolled back its
        # counter increment, so returning the winner cannot create a sequence gap.
        existing = existing_replay()
        if existing and booking_request_matches_record(
            existing,
            learner_id=learner_id,
            session_type=session_type,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=notes,
        ):
            return existing, False
        raise


CALENDAR_GRAPH_SYNC_FIELDS = [
    "sync_state", "last_graph_sync_error", "graph_event_id", "graph_organizer_email",
    "meeting_provider", "meeting_link", "graph_web_link", "updated_at",
]


def finalize_calendar_graph_sync(record: CoachCalendarEvent, warning: str) -> None:
    record.sync_state = CoachCalendarEvent.SYNC_SYNCED
    record.last_graph_sync_error = public_graph_sync_warning(warning)
    # Graph I/O runs outside locks. A concurrent submission may have advanced
    # the review; never save stale lifecycle/answers/booking fields over it.
    record.save(update_fields=CALENDAR_GRAPH_SYNC_FIELDS)


class CalendarSyncInProgress(RuntimeError):
    pass


def sync_scheduled_review_instance(record: CoachCalendarEvent) -> CoachCalendarEvent:
    """Validate the booking link and advance only an unstarted review.

    Also used by unchanged reschedule requests, without touching Graph or its
    sync state. Both rows are read under lock, including on retry.
    """
    if not clean_text(record.review_instance_id):
        return record
    with transaction.atomic():
        record = CoachCalendarEvent.objects.select_for_update().get(pk=record.pk)
        from .reconciliation_links import require_schedulable
        if require_schedulable(record):
            return record
        instance = curriculum_review_instances.get_review_instance(record.review_instance_id, for_update=True)
        if (
            not instance
            or instance.get('calendar_event_id') != record.pk
            or instance.get('learner_id') != record.learner_id
            or clean_email(instance.get('coach_email')) != clean_email(record.owner_email)
            or clean_text(instance.get('review_template_id')) != clean_text(record.review_template_id)
        ):
            raise LearnerCalendarConflict("This booking's review link is inconsistent. Please contact support.")
        if (
            record.status != CoachCalendarEvent.STATUS_SCHEDULED
            or instance.get('status') not in {
                curriculum_review_instances.STATUS_NOT_SCHEDULED,
                curriculum_review_instances.STATUS_SCHEDULED,
            }
        ):
            raise LearnerCalendarConflict("Only an unstarted review can be scheduled or rescheduled.")
        if record.scheduled_date is None or record.scheduled_time is None:
            raise LearnerCalendarConflict("A review booking must have a scheduled date and time.")
        if instance['status'] == curriculum_review_instances.STATUS_NOT_SCHEDULED:
            updated = curriculum_review_instances.mark_review_instance_scheduled(
                instance['id'], actor=record.owner_email or 'coach',
            )
            if not updated:
                raise LearnerCalendarConflict("The review status changed. Reload it before scheduling again.")
        return record


def persist_calendar_sync_reservation(
    candidate: CoachCalendarEvent, *, review_event: dict | None = None,
) -> CoachCalendarEvent:
    """Save booking inputs, review linkage and lifecycle before Graph I/O."""
    mutable_fields = (
        "owner_email",
        "owner_name",
        "learner_id",
        "learner_name",
        "learner_email",
        "event_type",
        "sequence",
        "target_date",
        "scheduled_date",
        "scheduled_time",
        "duration_minutes",
        "status",
        "notes",
        # The Curriculum linkage travels with the scheduling inputs. It is set
        # on the candidate by both scheduling paths (coach_timetable_schedule
        # _event and learner_calendar_book) immediately before this call, and
        # leaving it out of the copy silently discarded it: the row reloaded
        # here is a fresh instance, so the assignment never reached the
        # database and ensure_review_instance_for_calendar_record then saw a
        # blank template id and created no review instance at all.
        "review_template_id",
        "occurrence_number",
    )
    if candidate.scheduled_date:
        non_delivery_reason = england_non_delivery_reason(candidate.scheduled_date)
        if non_delivery_reason:
            raise LearnerCalendarConflict(non_delivery_reason, candidate)
    with transaction.atomic():
        lock_learner_calendar(candidate.learner_id)
        record = CoachCalendarEvent.objects.select_for_update().get(pk=candidate.pk)
        from .reconciliation_links import require_schedulable
        reconciliation = require_schedulable(record)
        if record.sync_state in {
            CoachCalendarEvent.SYNC_SYNCING,
            CoachCalendarEvent.SYNC_RECONCILIATION,
        }:
            raise CalendarSyncInProgress("Calendar event synchronization is already in progress.")
        if (record.review_instance_id or candidate.review_template_id) and record.status in {
            CoachCalendarEvent.STATUS_IN_PROGRESS,
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            CoachCalendarEvent.STATUS_COMPLETED,
        }:
            raise LearnerCalendarConflict("Only an unstarted review can be scheduled or rescheduled.")
        if (
            candidate.scheduled_date
            and candidate.scheduled_time
            and candidate.status in {
                CoachCalendarEvent.STATUS_SCHEDULED,
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            }
        ):
            if not clean_text(candidate.review_template_id):
                ensure_learner_session_not_booked_in_week(
                    learner_id=candidate.learner_id,
                    learner_email=candidate.learner_email,
                    session_type=candidate.event_type,
                    scheduled_date=candidate.scheduled_date,
                    exclude_record_id=candidate.pk,
                )
            ensure_learner_calendar_available(
                learner_id=candidate.learner_id,
                learner_email=candidate.learner_email,
                scheduled_date=candidate.scheduled_date,
                scheduled_time=candidate.scheduled_time,
                duration_minutes=candidate.duration_minutes,
                exclude_record_id=candidate.pk,
            )
            ensure_coach_booking_available(candidate.owner_email, candidate.scheduled_date, candidate.scheduled_time,
                                           candidate.duration_minutes, exclude_record_id=candidate.pk)
        for field in mutable_fields:
            if record.review_instance_id and field in {"review_template_id", "occurrence_number"}:
                continue
            if reconciliation and field in {"owner_email", "learner_id", "event_type", "sequence", "target_date"}:
                continue
            setattr(record, field, getattr(candidate, field))
        record.sync_state = CoachCalendarEvent.SYNC_PENDING
        record.save(update_fields=[*mutable_fields, "sync_state", "updated_at"])
        if reconciliation or (review_event is not None and review_event.get("reviewSource") == "aptem"):
            return record
        if review_event is not None and record.review_template_id and not record.review_instance_id:
            ensure_review_instance_for_calendar_record(record, review_event)
        return sync_scheduled_review_instance(record)


def synchronize_reserved_calendar_event(
    record_id: int,
    base_event: dict,
) -> tuple[CoachCalendarEvent, str, bool]:
    """Claim and synchronise a durable operation without holding DB locks on I/O."""
    from . import imported_meeting_ownership as meeting_ownership
    ownership_active = meeting_ownership.enabled_for(base_event)
    if ownership_active:
        # A missing migration or incomplete legacy inventory must fail before
        # POST/PATCH can create an external meeting or send an invitation.
        meeting_ownership.require_activation_ready()
    with transaction.atomic():
        record = CoachCalendarEvent.objects.select_for_update().get(pk=record_id)
        if record.sync_state == CoachCalendarEvent.SYNC_SYNCED:
            if ownership_active:
                meeting_ownership.require_synced_owner(record, base_event)
            return record, "", False
        syncing_is_fresh = (
            record.sync_state == CoachCalendarEvent.SYNC_SYNCING
            and record.last_sync_attempt_at
            and record.last_sync_attempt_at > timezone.now() - timedelta(minutes=5)
        )
        if syncing_is_fresh or record.sync_state in {
            CoachCalendarEvent.SYNC_RECONCILIATION,
            CoachCalendarEvent.SYNC_CANCELLED,
        }:
            return record, public_graph_sync_warning(record.last_graph_sync_error), False
        record.sync_state = CoachCalendarEvent.SYNC_SYNCING
        record.sync_attempt_count += 1
        record.last_sync_attempt_at = timezone.now()
        record.save(update_fields=["sync_state", "sync_attempt_count", "last_sync_attempt_at", "updated_at"])

    logger.info(
        "coach_calendar_graph_sync_started",
        extra={
            "event": "coach_calendar_graph_sync_started",
            "operation_id": str(record.operation_id),
            "graph_status": record.sync_state,
            "attempt_count": record.sync_attempt_count,
        },
    )
    existing_graph_event = bool(clean_text(record.graph_event_id))
    try:
        warning = sync_calendar_event_to_graph(record, base_event)
    except Exception:
        CoachCalendarEvent.objects.filter(pk=record.pk).update(
            sync_state=CoachCalendarEvent.SYNC_FAILED,
            last_graph_sync_error=TEAMS_SYNC_TEMPORARY_MESSAGE,
            updated_at=timezone.now(),
        )
        logger.exception(
            "coach_calendar_graph_sync_exception",
            extra={
                "event": "coach_calendar_graph_sync_exception",
                "operation_id": str(record.operation_id),
                "graph_status": CoachCalendarEvent.SYNC_FAILED,
                "attempt_count": record.sync_attempt_count,
            },
        )
        raise
    warning = public_graph_sync_warning(warning)
    if warning or not calendar_record_has_launch_url(record):
        warning = warning or TEAMS_SYNC_LINK_MISSING_MESSAGE
        record.sync_state = CoachCalendarEvent.SYNC_FAILED
        record.last_graph_sync_error = public_graph_sync_warning(warning)
        record.save(update_fields=CALENDAR_GRAPH_SYNC_FIELDS)
        logger.warning(
            "coach_calendar_graph_sync_failed",
            extra={
                "event": "coach_calendar_graph_sync_failed",
                "operation_id": str(record.operation_id),
                "graph_status": record.sync_state,
                "attempt_count": record.sync_attempt_count,
            },
        )
        return record, warning, True

    try:
        if ownership_active:
            # The strict Graph reads happen after POST/PATCH, outside DB locks.
            # The owner and synced calendar state publish in one transaction.
            target = meeting_ownership.resolve_verified_target(record)
            meeting_ownership.finalize_owned_booking(record, warning, base_event, target)
        else:
            finalize_calendar_graph_sync(record, warning)
    except Exception:
        logger.exception(
            "coach_calendar_graph_finalize_failed",
            extra={"event": "coach_calendar_graph_finalize_failed", "operation_id": str(record.operation_id)},
        )
        compensation_warning = delete_calendar_event_from_graph(record) if not existing_graph_event else ''
        recovery_state = (
            CoachCalendarEvent.SYNC_RECONCILIATION
            if compensation_warning
            else CoachCalendarEvent.SYNC_FAILED
        )
        recovery_error = compensation_warning or "Local finalization failed after Graph synchronization."
        keep_graph_event = existing_graph_event or bool(compensation_warning)
        try:
            CoachCalendarEvent.objects.filter(pk=record.pk).update(
                sync_state=recovery_state,
                last_graph_sync_error=public_graph_sync_warning(recovery_error),
                graph_event_id=record.graph_event_id if keep_graph_event else "",
                graph_organizer_email=record.graph_organizer_email if keep_graph_event else "",
                meeting_provider=record.meeting_provider if keep_graph_event else "",
                meeting_link=record.meeting_link if keep_graph_event else "",
                graph_web_link=record.graph_web_link if keep_graph_event else "",
                updated_at=timezone.now(),
            )
        except Exception:
            logger.exception(
                "coach_calendar_graph_recovery_record_failed",
                extra={"event": "coach_calendar_graph_recovery_record_failed", "operation_id": str(record.operation_id)},
            )
        raise

    logger.info(
        "coach_calendar_graph_sync_completed",
        extra={
            "event": "coach_calendar_graph_sync_completed",
            "operation_id": str(record.operation_id),
            "graph_status": record.sync_state,
            "attempt_count": record.sync_attempt_count,
        },
    )
    return record, warning, True


def find_generated_timetable_event(owner_email: str, event_key: str) -> tuple[dict | None, str]:
    payload = collect_generated_timetable(owner_email)
    event = next((item for item in payload["events"] if event_key in {item.get("eventKey"), item.get("calendarEventKey")}), None)
    return event, payload["owner_name"]


def find_catchup_calendar_record(owner_email: str, event_key: str) -> tuple[CoachCalendarEvent | None, str]:
    record = CoachCalendarEvent.objects.filter(
        owner_email__iexact=owner_email,
        event_key=event_key,
        event_type__in=[CATCH_UP_EVENT_TYPE, "student-support", LMS_INTRODUCTION_EVENT_TYPE],
    ).first()
    owner_name = fetch_owner_name(owner_email, fallback=clean_text(record.owner_name) or "Med Maher") if record else fetch_owner_name(owner_email)
    return record, owner_name


def find_catchup_template_event(owner_email: str, event_key: str) -> tuple[dict | None, str]:
    active_rows = fetch_owner_active_learner_profiles(owner_email)
    owner_name = next(
        (clean_text(row.coach_name) for row in active_rows if clean_text(row.coach_name)),
        "Med Maher",
    )

    for learner in active_rows:
        learner_id = int(getattr(learner, "id", 0) or 0)
        if learner_id <= 0:
            continue
        if build_catchup_template_event_key(owner_email, learner_id) == event_key:
            return build_catchup_template_event(
                learner,
                owner_email=owner_email,
                owner_name=owner_name,
            ), owner_name

    return None, owner_name


@coach_access_required
def coach_timetable_schedule_event(request):
    if request.method not in ("POST", "PATCH"):
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    try:
        payload = parse_json_body(request)
        owner_email = authenticated_coach_email(request)
        validator = ObjectValidator(payload)
        event_key = validator.text("eventKey", required=True, max_length=255)
        scheduled_date = validator.iso_date("scheduledDate", required=True)
        scheduled_time = validator.clock_time("scheduledTime", required=True)
        duration_minutes = validator.integer(
            "durationMinutes", default=TIMETABLE_DEFAULT_DURATION_MINUTES, minimum=15, maximum=480
        )
        timezone_offset_minutes = validator.integer(
            "timezoneOffsetMinutes", default=0, minimum=-840, maximum=840
        )
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    if event_key.startswith("imported-review:"):
        from .reconciliation_links import records_for_key
        mapped_records = records_for_key(event_key)
        if len(mapped_records) != 1 or clean_email(mapped_records[0].owner_email) != clean_email(owner_email):
            return coach_error(request, code="BOOKING_CONFLICT", message="Open the migrated review and use Book Meeting to schedule it.", status=409)
        event_key = mapped_records[0].event_key

    catchup_record, owner_name = find_catchup_calendar_record(owner_email, event_key)
    if catchup_record:
        learner_rows = fetch_owner_active_learner_profiles(owner_email)
        learner_map = build_learner_profile_map(learner_rows)
        learner = learner_map.get(catchup_record.learner_id)
        if learner and coach_learner_personal_calendar_conflicts(
            learner, scheduled_date, scheduled_time, duration_minutes, timezone_offset_minutes
        ):
            return JsonResponse({"detail": "This learner is busy at that time. Choose another time."}, status=409)
        catchup_record.owner_name = owner_name or catchup_record.owner_name
        catchup_record.scheduled_date = scheduled_date
        catchup_record.scheduled_time = scheduled_time
        catchup_record.duration_minutes = duration_minutes
        catchup_record.target_date = catchup_record.target_date or scheduled_date
        catchup_record.status = CoachCalendarEvent.STATUS_SCHEDULED

        try:
            catchup_record = persist_calendar_sync_reservation(catchup_record)
        except LearnerCalendarConflict as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        except CalendarSyncInProgress:
            return coach_error(
                request,
                code="calendar_sync_in_progress",
                message="Calendar event synchronization is already in progress.",
                status=409,
            )
        base_event = build_catchup_calendar_event(
            catchup_record,
            owner_name=owner_name,
            learner=learner_map.get(catchup_record.learner_id),
        )
        catchup_record, warning, _attempted = synchronize_reserved_calendar_event(
            catchup_record.pk, base_event
        )
        if not calendar_record_has_launch_url(catchup_record):
            warning = warning or TEAMS_SYNC_LINK_MISSING_MESSAGE
            catchup_record = save_calendar_sync_warning(catchup_record, reason=warning)
        else:
            catchup_record.last_graph_sync_error = public_graph_sync_warning(warning)
            catchup_record.save()

        updated_event = build_catchup_calendar_event(
            catchup_record,
            owner_name=owner_name,
            learner=learner_map.get(catchup_record.learner_id),
        )
        return JsonResponse({"event": updated_event, "warning": warning})

    catchup_template_event, owner_name = find_catchup_template_event(owner_email, event_key)
    if catchup_template_event:
        learner_rows = fetch_owner_active_learner_profiles(owner_email)
        learner_map = build_learner_profile_map(learner_rows)
        learner_id = int(catchup_template_event["learnerId"])
        learner = learner_map.get(learner_id)
        if learner and coach_learner_personal_calendar_conflicts(
            learner, scheduled_date, scheduled_time, duration_minutes, timezone_offset_minutes
        ):
            return JsonResponse({"detail": "This learner is busy at that time. Choose another time."}, status=409)
        target_date = scheduled_date

        record, _ = CoachCalendarEvent.objects.get_or_create(
            event_key=event_key,
            defaults={
                "owner_email": owner_email,
                "owner_name": owner_name,
                "learner_id": learner_id,
                "learner_name": clean_text(catchup_template_event.get("learner")),
                "learner_email": clean_text(catchup_template_event.get("email")),
                "event_type": CATCH_UP_EVENT_TYPE,
                "sequence": int(catchup_template_event.get("sequence") or 1),
                "target_date": target_date,
            },
        )
        record.owner_email = owner_email
        record.owner_name = owner_name
        record.learner_id = learner_id
        record.learner_name = clean_text(catchup_template_event.get("learner"))
        record.learner_email = clean_text(catchup_template_event.get("email"))
        record.event_type = CATCH_UP_EVENT_TYPE
        record.sequence = int(catchup_template_event.get("sequence") or 1)
        record.target_date = target_date
        record.scheduled_date = scheduled_date
        record.scheduled_time = scheduled_time
        record.duration_minutes = duration_minutes
        record.status = CoachCalendarEvent.STATUS_SCHEDULED

        try:
            record = persist_calendar_sync_reservation(record)
        except LearnerCalendarConflict as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        except CalendarSyncInProgress:
            return coach_error(
                request,
                code="calendar_sync_in_progress",
                message="Calendar event synchronization is already in progress.",
                status=409,
            )
        base_event = build_catchup_calendar_event(
            record,
            owner_name=owner_name,
            learner=learner_map.get(record.learner_id),
        )
        record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, base_event)
        if not calendar_record_has_launch_url(record):
            warning = warning or TEAMS_SYNC_LINK_MISSING_MESSAGE
            record = save_calendar_sync_warning(record, reason=warning)
        else:
            record.last_graph_sync_error = public_graph_sync_warning(warning)
            record.save()

        updated_event = build_catchup_calendar_event(
            record,
            owner_name=owner_name,
            learner=learner_map.get(record.learner_id),
        )
        return JsonResponse({"event": updated_event, "warning": warning})

    # A saved Native appointment remains manageable by its owner after Aptem
    # data arrives, even though it is absent from the primary Review list.
    existing_native = CoachCalendarEvent.objects.filter(
        event_key=event_key, owner_email__iexact=owner_email,
        event_type__in={"mcr", "progress-review", "review"},
        scheduled_date__isnull=False, scheduled_time__isnull=False,
    ).exclude(review_instance_id="").exclude(review_instance_id__isnull=True).first()
    try:
        base_event, owner_name = find_generated_timetable_event(owner_email, event_key)
    except DatabaseError:
        if not existing_native:
            raise
        base_event = None
    if not base_event and existing_native:
        from .reconciliation_links import for_calendar
        if for_calendar(existing_native):
            return JsonResponse({"detail": "The reconciled imported Review is unavailable. Reload before rescheduling."}, status=409)
        base_event = build_booked_calendar_event(existing_native)
        owner_name = existing_native.owner_name
    if not base_event:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)
    if (
        base_event.get("reviewSource") == "aptem"
        and base_event.get("status") == CoachCalendarEvent.STATUS_COMPLETED
    ):
        return JsonResponse({"detail": "This historical imported review is read-only."}, status=409)

    target_date = parse_date_value(base_event.get("targetDate"))
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if not isinstance(target_date, date):
        return JsonResponse({"detail": "Target date is missing for this event."}, status=400)

    review_booking = (base_event.get("reviewSource") == "aptem"
                      or clean_text(base_event.get("source")).lower() in {"mcr", "progress-review", "review"})
    if review_booking:
        if payload.get("durationMinutes") in (None, ""):
            return JsonResponse({"detail": "durationMinutes is required for Review bookings."}, status=400)
        try:
            from .booking_hours import validate_review_time_precision
            validate_review_time_precision(payload.get("scheduledTime"))
        except ValueError as exc:
            return coach_error(request, code="INVALID_REVIEW_TIME", message=str(exc), status=400)

    learner_rows = fetch_owner_active_learner_profiles(owner_email)
    learner_map = build_learner_profile_map(learner_rows)
    base_learner = learner_map.get(int(base_event["learnerId"]))
    current_review_booking = None
    review_slot_is_replay = False
    if review_booking:
        current_review_booking = CoachCalendarEvent.objects.filter(
            event_key=event_key, owner_email__iexact=owner_email,
            learner_id=int(base_event["learnerId"]),
        ).first()
        review_slot_is_replay = bool(current_review_booking
                                     and current_review_booking.status == CoachCalendarEvent.STATUS_SCHEDULED
                                     and (current_review_booking.scheduled_date,
                                          current_review_booking.scheduled_time,
                                          current_review_booking.duration_minutes)
                                     == (scheduled_date, scheduled_time, duration_minutes))
        if not review_slot_is_replay:
            try:
                from .booking_hours import validate_review_interval
                validate_review_interval(scheduled_date, scheduled_time, duration_minutes)
            except ValueError as exc:
                return coach_error(request, code="INVALID_REVIEW_TIME", message=str(exc), status=400)
    if base_event.get("reviewSource") != "aptem" and base_event.get("source") in {"mcr", "progress-review", "review"}:
        existing = CoachCalendarEvent.objects.filter(
            event_key=event_key, owner_email__iexact=owner_email,
            learner_id=int(base_event["learnerId"]),
        ).first()
        if not (existing and existing.review_instance_id and existing.scheduled_date and existing.scheduled_time):
            policy = native_review_creation_policy(base_learner)
            if not policy["canAddReview"]:
                return JsonResponse(policy, status=409)

    if base_learner and not review_slot_is_replay and coach_learner_personal_calendar_conflicts(
        base_learner, scheduled_date, scheduled_time, duration_minutes,
        timezone_offset_minutes, exclude_record=current_review_booking,
        **({"business_time_zone": "Europe/London"} if review_booking else {}),
    ):
        return JsonResponse({"detail": "This learner is busy at that time. Choose another time."}, status=409)
    if review_booking and not review_slot_is_replay:
        try:
            from .booking_availability import ensure_coach_review_slot_available
            ensure_coach_review_slot_available(owner_email, scheduled_date, scheduled_time,
                                               duration_minutes, record=current_review_booking)
        except LearnerCalendarConflict as exc:
            return JsonResponse({"detail": str(exc)}, status=409)
        except Exception as exc:
            from .booking_availability import CoachAvailabilityUnavailable
            if isinstance(exc, CoachAvailabilityUnavailable):
                return coach_error(request, code="AVAILABILITY_UNAVAILABLE", message=str(exc), status=503)
            raise

    record, _ = CoachCalendarEvent.objects.get_or_create(
        event_key=event_key,
        defaults={
            "owner_email": owner_email,
            "owner_name": owner_name,
            "learner_id": int(base_event["learnerId"]),
            "learner_name": clean_text(base_event.get("learner")),
            "learner_email": clean_text(base_event.get("email")),
            "event_type": clean_text(base_event.get("source")),
            "sequence": int(base_event.get("sequence") or 1),
            "target_date": target_date,
        },
    )
    if record.status in {
        CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
        CoachCalendarEvent.STATUS_COMPLETED,
    }:
        return JsonResponse(
            {"detail": "A submitted or completed review cannot be scheduled again."},
            status=409,
        )
    review_template_id = clean_text(base_event.get("reviewTemplateId"))
    # A review-driven event must not become scheduled unless canonical
    # linkage is guaranteed. Rescheduling an already-linked row (record
    # already carries review_instance_id) never re-resolves the template --
    # that instance stays valid even if its template is later archived.
    first_time_linkage = bool(review_template_id) and not record.review_instance_id
    if first_time_linkage:
        try:
            require_review_template_for_first_linkage(review_template_id)
        except (ReviewTemplateUnavailableError, ReviewCreationBlocked) as exc:
            return JsonResponse(getattr(exc, "policy", {"detail": str(exc)}), status=409)

    record.owner_email = owner_email
    record.owner_name = owner_name
    record.learner_id = int(base_event["learnerId"])
    record.learner_name = clean_text(base_event.get("learner"))
    record.learner_email = clean_text(base_event.get("email"))
    record.event_type = clean_text(base_event.get("source"))
    record.sequence = int(base_event.get("sequence") or 1)
    record.target_date = target_date
    record.scheduled_date = scheduled_date
    record.scheduled_time = scheduled_time
    record.duration_minutes = duration_minutes
    record.status = CoachCalendarEvent.STATUS_SCHEDULED

    if review_template_id:
        record.review_template_id = review_template_id
        record.occurrence_number = (
            None if base_event.get("occurrenceSource") == curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL
            else parse_int(base_event.get("occurrenceNumber"), record.sequence)
        )

    try:
        record = persist_calendar_sync_reservation(record, review_event=base_event)
    except (ReviewTemplateUnavailableError, ReviewCreationBlocked) as exc:
        return JsonResponse(getattr(exc, "policy", {"detail": str(exc)}), status=409)
    except LearnerCalendarConflict as exc:
        return JsonResponse({"detail": str(exc)}, status=409)
    except CalendarSyncInProgress:
        return coach_error(
            request,
            code="calendar_sync_in_progress",
            message="Calendar event synchronization is already in progress.",
            status=409,
        )

    record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, base_event)
    if not calendar_record_has_launch_url(record):
        warning = warning or TEAMS_SYNC_LINK_MISSING_MESSAGE
        record = save_calendar_sync_warning(record, reason=warning)
    else:
        record.last_graph_sync_error = public_graph_sync_warning(warning)
        record.save()

    updated_event = overlay_calendar_record(base_event, record)
    return JsonResponse({"event": updated_event, "warning": warning})


@coach_access_required
def coach_timetable_book_event(request):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    try:
        payload = parse_json_body(request)
        owner_email = authenticated_coach_email(request)
        validator = ObjectValidator(payload)
        learner_id = validator.integer("learnerId", required=True, minimum=1)
        # Deliberately narrower than BOOKED_EVENT_TITLES: onboarding reviews
        # are booked by the learner against their case owner, not here.
        session_type = validator.text(
            "sessionType", required=True, lower=True, choices=set(COACH_BOOKABLE_EVENT_TYPES)
        )
        scheduled_date = validator.iso_date("scheduledDate", required=True)
        scheduled_time = validator.clock_time("scheduledTime", required=True)
        duration_minutes = validator.integer(
            "durationMinutes", default=TIMETABLE_DEFAULT_DURATION_MINUTES, minimum=15, maximum=480
        )
        timezone_offset_minutes = validator.integer(
            "timezoneOffsetMinutes", default=0, minimum=-840, maximum=840
        )
        notes = validator.text("notes", max_length=500)
        absence_report_id = validator.integer("absenceReportId", minimum=1) if payload.get("absenceReportId") not in (None, "") else None
        if absence_report_id and session_type != "catch-up":
            validator.error("absenceReportId", "Only catch-up sessions can be linked to a missed lecture.")
        if scheduled_date and scheduled_date < date.today():
            validator.error("scheduledDate", "Choose today or a future date for this session.")
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)
    caseload_rows = fetch_caseload_learner_profiles(owner_email)
    learner = next((row for row in caseload_rows if int(getattr(row, "id", 0) or 0) == learner_id), None)
    if not learner:
        return JsonResponse({"detail": "Learner not found in this coach caseload."}, status=404)

    def linked_report(expected_event_key=""):
        if not absence_report_id:
            return None
        report = CoachAbsenceReport.objects.filter(
            pk=absence_report_id,
            owner_email__iexact=owner_email,
            learner_id=learner_id,
        ).first()
        if not report:
            raise LearnerCalendarConflict("Missed lecture not found in this coach caseload.")
        if report.status == CoachAbsenceReport.STATUS_DECLINED:
            raise LearnerCalendarConflict("A declined absence report cannot have a catch-up meeting.")
        if report.recovery_method and report.recovery_method != "catch-up":
            raise LearnerCalendarConflict("This missed lecture already has a different recovery plan.")
        if report.catchup_event_key and report.catchup_event_key != expected_event_key:
            raise LearnerCalendarConflict("This missed lecture already has a catch-up meeting.")
        if scheduled_date < report.session_date:
            raise LearnerCalendarConflict("Choose a catch-up on or after the missed lecture date.")
        return report

    try:
        linked_report()
    except LearnerCalendarConflict as exc:
        return JsonResponse({"detail": str(exc)}, status=409)

    owner_name = fetch_owner_name(owner_email, fallback=clean_text(getattr(learner, "coach_name", None)) or "Med Maher")
    learner_name = clean_text(getattr(learner, "username", None)) or "Unknown learner"
    learner_email = clean_text(getattr(learner, "email", None))
    try:
        idempotency_key = calendar_idempotency_key(request)
    except ValueError as exc:
        return validation_error_response(ValidationError({"Idempotency-Key": [str(exc)]}))

    replay = CoachCalendarEvent.objects.filter(
        owner_email=normalize_email(owner_email),
        idempotency_key=idempotency_key,
    ).first()
    if replay:
        if not booking_request_matches_record(
            replay,
            learner_id=learner_id,
            session_type=session_type,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=notes,
        ):
            return JsonResponse(
                {"detail": "Idempotency-Key was already used for a different booking."},
                status=409,
            )
        try:
            replay, warning, _attempted = synchronize_reserved_calendar_event(
                replay.pk, build_booked_calendar_event(replay)
            )
        except Exception:
            logger.exception(
                "coach_calendar_booking_replay_failed",
                extra={"event": "coach_calendar_booking_replay_failed", "operation_id": str(replay.operation_id)},
            )
            return coach_error(
                request,
                code="calendar_sync_failed",
                message="The calendar operation could not be completed.",
                status=500,
            )
        if absence_report_id:
            try:
                report = linked_report(replay.event_key)
                if report:
                    from learner_api.session_recovery import _record_linked_catchup
                    with transaction.atomic():
                        report = CoachAbsenceReport.objects.select_for_update().get(pk=report.pk)
                        if report.catchup_event_key and report.catchup_event_key != replay.event_key:
                            raise LearnerCalendarConflict("This missed lecture already has a catch-up meeting.")
                        if not report.catchup_event_key:
                            report.catchup_event_key = replay.event_key
                            report.recovery_method = "catch-up"
                            report.status = CoachAbsenceReport.STATUS_APPROVED
                            report.save(update_fields=["catchup_event_key", "recovery_method", "status", "updated_at"])
                            _record_linked_catchup(learner_id, report.attendance_id, replay.event_key)
            except LearnerCalendarConflict as exc:
                return JsonResponse({"detail": str(exc)}, status=409)
        event = build_catchup_calendar_event(replay, owner_name=owner_name, learner=learner)
        return JsonResponse(
            {
                "event": event,
                "warning": warning,
                "operation": {
                    "id": str(replay.operation_id),
                    "syncState": replay.sync_state,
                    "attemptCount": replay.sync_attempt_count,
                    "replayed": True,
                },
            },
            status=200,
        )

    if coach_learner_personal_calendar_conflicts(
        learner, scheduled_date, scheduled_time, duration_minutes, timezone_offset_minutes
    ):
        return JsonResponse({"detail": "This learner is busy at that time. Choose another time."}, status=409)

    try:
        record, created = reserve_coach_calendar_booking(
            owner_email=owner_email,
            owner_name=owner_name,
            learner_id=learner_id,
            learner_name=learner_name,
            learner_email=learner_email,
            session_type=session_type,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=notes,
            idempotency_key=idempotency_key,
        )
        record, warning, attempted = synchronize_reserved_calendar_event(
            record.pk,
            build_booked_calendar_event(record),
        )
    except LearnerCalendarConflict as exc:
        return coach_error(
            request,
            code="learner_schedule_conflict",
            message=str(exc),
            status=409,
        )
    except ValueError as exc:
        if "already used" in str(exc):
            return coach_error(
                request,
                code="idempotency_conflict",
                message="Idempotency-Key was already used for a different booking.",
                status=409,
            )
        return validation_error_response(ValidationError({"body": ["The booking could not be validated."]}))
    except Exception:  # noqa: BLE001
        logger.exception(
            "coach_calendar_booking_failed coach_account_id=%s learner_id=%s event_type=%s",
            owner_email,
            learner_id,
            session_type,
        )
        return coach_error(
            request,
            code="calendar_sync_failed",
            message="The calendar operation could not be completed.",
            status=500,
        )

    if absence_report_id:
        try:
            from learner_api.session_recovery import _record_linked_catchup
            with transaction.atomic():
                report = CoachAbsenceReport.objects.select_for_update().filter(
                    pk=absence_report_id,
                    owner_email__iexact=owner_email,
                    learner_id=learner_id,
                ).first()
                if not report:
                    raise LearnerCalendarConflict("Missed lecture not found in this coach caseload.")
                if report.status == CoachAbsenceReport.STATUS_DECLINED or report.catchup_event_key:
                    raise LearnerCalendarConflict("This missed lecture is no longer available for a catch-up meeting.")
                if report.recovery_method and report.recovery_method != "catch-up":
                    raise LearnerCalendarConflict("This missed lecture already has a different recovery plan.")
                if scheduled_date < report.session_date:
                    raise LearnerCalendarConflict("Choose a catch-up on or after the missed lecture date.")
                report.catchup_event_key = record.event_key
                report.recovery_method = "catch-up"
                report.status = CoachAbsenceReport.STATUS_APPROVED
                report.save(update_fields=["catchup_event_key", "recovery_method", "status", "updated_at"])
                _record_linked_catchup(learner_id, report.attendance_id, record.event_key)
        except LearnerCalendarConflict as exc:
            try:
                cancel_reserved_calendar_event(record)
            except Exception:
                logger.exception("coach_catchup_link_rollback_failed event_key=%s", record.event_key)
                return JsonResponse({"detail": f"{exc} The booking was kept; please link or cancel it from the calendar."}, status=409)
            return JsonResponse({"detail": f"{exc} The booking was cancelled."}, status=409)
        except Exception:
            logger.exception("coach_catchup_link_failed event_key=%s report_id=%s", record.event_key, absence_report_id)
            try:
                cancel_reserved_calendar_event(record)
            except Exception:
                logger.exception("coach_catchup_link_rollback_failed event_key=%s", record.event_key)
            return JsonResponse({"detail": "The catch-up could not be linked to the missed lecture. The booking was cancelled."}, status=409)

    event = build_catchup_calendar_event(record, owner_name=owner_name, learner=learner)
    return JsonResponse(
        {
            "event": event,
            "warning": warning,
            "operation": {
                "id": str(record.operation_id),
                "syncState": record.sync_state,
                "attemptCount": record.sync_attempt_count,
                "replayed": not created,
            },
        },
        status=201 if created else 200,
    )


@coach_access_required
def coach_timetable_event_action(request):
    if request.method not in ("POST", "PATCH"):
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    try:
        payload = parse_json_body(request)
        validator = ObjectValidator(payload)
        event_key = validator.text("eventKey", required=True, max_length=255)
        action = validator.text(
            "action", required=True, lower=True, choices={"start", "complete", "sign", "cancel"}
        )
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    if event_key.startswith("imported-review:"):
        return coach_error(request, code="BOOKING_CONFLICT", message="Open the migrated review for its meeting actions.", status=409)

    owner_email = authenticated_coach_email(request)

    catchup_record, owner_name = find_catchup_calendar_record(owner_email, event_key)
    if catchup_record:
        warning = ""
        if action == "sign":
            return JsonResponse({"detail": "Only progress reviews can be signed off."}, status=400)
        if action == "start" and not calendar_record_has_launch_url(catchup_record):
            return JsonResponse({"detail": "This event does not have a Teams link yet. Schedule it again first."}, status=409)
        if action == "start" and catchup_record.status != CoachCalendarEvent.STATUS_SCHEDULED:
            return JsonResponse({"detail": "Only a scheduled event can be started."}, status=409)
        if action == "start" and catchup_record.scheduled_date and catchup_record.scheduled_date > date.today():
            return JsonResponse({"detail": "Join Meeting becomes available on the scheduled day."}, status=409)
        if action == "complete" and catchup_record.status != CoachCalendarEvent.STATUS_IN_PROGRESS:
            return JsonResponse({"detail": "Start the event before completing it."}, status=409)
        if action == "start":
            catchup_record.status = CoachCalendarEvent.STATUS_IN_PROGRESS
        elif action == "complete":
            catchup_record.status = CoachCalendarEvent.STATUS_COMPLETED
        elif action == "cancel":
            try:
                catchup_record, warning = cancel_reserved_calendar_event(catchup_record)
            except LearnerCalendarConflict as exc:
                return JsonResponse({"detail": str(exc)}, status=409)

        catchup_record.owner_name = owner_name or catchup_record.owner_name
        catchup_record.last_graph_sync_error = public_graph_sync_warning(warning)
        catchup_record.save()
        if action == "complete":
            from learner_api.session_recovery import refresh_catchup_attendance
            try:
                refresh_catchup_attendance(event_key)
            except Exception:
                logger.exception("Catch-up saved but lecture register update needs retry")
                warning = "Catch-up completed. The lecture attendance register still needs synchronization."

        learner = fetch_owner_active_learner_profiles(owner_email)
        learner_map = build_learner_profile_map(learner)
        updated_event = build_catchup_calendar_event(
            catchup_record,
            owner_name=owner_name,
            learner=learner_map.get(catchup_record.learner_id),
        )
        return JsonResponse({"event": updated_event, "warning": warning})

    base_event, owner_name = find_generated_timetable_event(owner_email, event_key)
    if not base_event:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)
    if (
        base_event.get("reviewSource") == "aptem"
        and base_event.get("status") == CoachCalendarEvent.STATUS_COMPLETED
    ):
        return JsonResponse({"detail": "This historical imported review is read-only."}, status=409)

    record = CoachCalendarEvent.objects.filter(owner_email__iexact=owner_email, event_key=event_key).first()
    if not record:
        return JsonResponse({"detail": "This event has not been scheduled yet."}, status=400)
    from .reconciliation_links import for_calendar
    reconciliation = for_calendar(record)
    if reconciliation:
        return JsonResponse({"detail": "Use the reconciled imported Review form for lifecycle actions.",
                             "code": "REVIEW_RECONCILED", "instanceId": reconciliation.overlay.event_key}, status=409)
    if calendar_record_needs_schedule_repair(record):
        save_calendar_sync_warning(
            record,
            reason=clean_text(record.last_graph_sync_error),
        )
        return JsonResponse({"detail": "This event does not have a Teams link. Retry calendar synchronization before continuing."}, status=409)

    record.owner_name = owner_name
    warning = ""
    review_responses = None
    completion_source = base_event.get("source")
    response_ids = (
        PROGRESS_REVIEW_RESPONSE_IDS
        if completion_source == "progress-review"
        else MONTHLY_COACHING_RESPONSE_IDS
        if completion_source == "mcr"
        else None
    )
    required_response_ids = (
        PROGRESS_REVIEW_REQUIRED_RESPONSE_IDS
        if completion_source == "progress-review"
        else MONTHLY_COACHING_REQUIRED_RESPONSE_IDS
        if completion_source == "mcr"
        else response_ids
    )
    # A row linked to a Curriculum-driven review_instances row must not be
    # completed/signed through this legacy hard-coded payload -- that engine
    # is the canonical one for it (see the "complete"/"sign" branches below).
    # Skipping the legacy validation here, rather than running it and then
    # discarding the result, avoids maintaining two competing definitions of
    # "valid answers" for the same review.
    if action == "complete" and response_ids and not record.review_instance_id:
        submitted_responses = payload.get("reviewResponses")
        if not isinstance(submitted_responses, dict):
            return validation_error_response(ValidationError({
                "reviewResponses": ["Responses are required as a JSON object before completing the session."]
            }))
        invalid_response_text = {
            f"reviewResponses.{key}": ["Must be a string of at most 4000 characters."]
            for key, value in submitted_responses.items()
            if key in response_ids and (not isinstance(value, str) or len(value.strip()) > 4000)
        }
        if invalid_response_text:
            return validation_error_response(ValidationError(invalid_response_text))
        review_responses = {
            key: clean_text(submitted_responses.get(key))
            for key in response_ids
        }
        missing = sorted(
            key
            for key in (required_response_ids or set())
            if not review_responses.get(key)
        )
        if missing:
            return validation_error_response(ValidationError({
                f"reviewResponses.{key}": ["This response is required."] for key in missing
            }))
        if completion_source == "progress-review":
            invalid_ratings = sorted(
                key
                for key in PROGRESS_REVIEW_RATING_RESPONSE_IDS
                if not review_responses.get(key, "").isdigit()
                or not 1 <= int(review_responses[key]) <= 10
            )
            if invalid_ratings:
                return validation_error_response(ValidationError({
                    f"reviewResponses.{key}": ["Must be a whole number between 1 and 10."]
                    for key in invalid_ratings
                }))
            invalid_yes_no = sorted(
                key
                for key in PROGRESS_REVIEW_YES_NO_RESPONSE_IDS
                if review_responses.get(key) not in {"Yes", "No"}
            )
            if invalid_yes_no:
                return validation_error_response(ValidationError({
                    f"reviewResponses.{key}": ["Must be Yes or No."] for key in invalid_yes_no
                }))
            if review_responses.get("key_theme") not in PROGRESS_REVIEW_KEY_THEMES:
                return validation_error_response(ValidationError({
                    "reviewResponses.key_theme": ["Select a valid key theme."]
                }))
            conditional_requirements = {
                ("other_progress_issues", "Yes"): "other_progress_issues_detail",
                ("safeguarding_concerns", "Yes"): "safeguarding_concerns_detail",
                ("key_theme", "Other"): "key_theme_other",
                ("additional_learning_support", "Yes"): "additional_learning_support_detail",
                ("health_adjustments", "Yes"): "health_adjustments_detail",
                ("other_support_circumstances", "Yes"): "other_support_detail",
                ("previous_targets_achieved", "No"): "previous_targets_detail",
            }
            missing_conditional = sorted(
                detail_id
                for (answer_id, trigger_value), detail_id in conditional_requirements.items()
                if review_responses.get(answer_id) == trigger_value
                and not review_responses.get(detail_id)
            )
            if missing_conditional:
                return validation_error_response(ValidationError({
                    f"reviewResponses.{key}": ["This follow-up detail is required."]
                    for key in missing_conditional
                }))
        if completion_source == "mcr":
            invalid_yes_no = sorted(
                key
                for key in MONTHLY_COACHING_YES_NO_RESPONSE_IDS
                if review_responses.get(key) not in {"Yes", "No"}
            )
            if invalid_yes_no:
                return validation_error_response(ValidationError({
                    f"reviewResponses.{key}": ["Must be Yes or No."] for key in invalid_yes_no
                }))
            valid_agreement_values = {"Agree", "Neutral", "Disagree", "Not discussed"}
            invalid_agreements = sorted(
                key
                for key in MONTHLY_COACHING_AGREEMENT_RESPONSE_IDS
                if review_responses.get(key) not in valid_agreement_values
            )
            if invalid_agreements:
                return validation_error_response(ValidationError({
                    f"reviewResponses.{key}": ["Select a valid agreement response."]
                    for key in invalid_agreements
                }))
            if review_responses.get("mcm_previous_meeting") not in {
                "Previous Monthly Coaching Meeting",
                "No previous meeting",
            }:
                return validation_error_response(ValidationError({
                    "reviewResponses.mcm_previous_meeting": ["Select a valid previous meeting."]
                }))
            try:
                date.fromisoformat(review_responses.get("mcm_next_meeting_date", ""))
            except ValueError:
                return validation_error_response(ValidationError({
                    "reviewResponses.mcm_next_meeting_date": ["Enter a valid date in YYYY-MM-DD format."]
                }))
        outcome_key = "rag_status" if completion_source == "progress-review" else "mcm_outcome"
        if review_responses[outcome_key].lower() not in {"green", "amber", "red"}:
            return validation_error_response(ValidationError({
                f"reviewResponses.{outcome_key}": ["Must be Green, Amber or Red."]
            }))

    if action == "start" and not calendar_record_has_launch_url(record):
        return JsonResponse({"detail": "This event does not have a Teams link yet. Schedule it again first."}, status=409)
    if action == "start" and record.status != CoachCalendarEvent.STATUS_SCHEDULED:
        return JsonResponse({"detail": "Only a scheduled event can be started."}, status=409)
    if action == "start" and record.scheduled_date and record.scheduled_date > date.today():
        return JsonResponse({"detail": "Join Meeting becomes available on the scheduled day."}, status=409)
    if action == "complete" and record.status != CoachCalendarEvent.STATUS_IN_PROGRESS:
        return JsonResponse({"detail": "Start the review before completing it."}, status=409)
    if action == "sign":
        if completion_source != "progress-review":
            return JsonResponse({"detail": "Only progress reviews require a manager signature."}, status=400)
        if record.status != CoachCalendarEvent.STATUS_AWAITING_SIGNATURE:
            return JsonResponse({"detail": "This progress review is not awaiting a manager signature."}, status=409)
    if action == "start" and not record.review_instance_id:
        record.status = CoachCalendarEvent.STATUS_IN_PROGRESS
    elif action == "start":
        # Linked row: opening Teams is not evidence that anybody attended.
        # Confirmed attendance or the Review Instance manual override owns the
        # scheduled -> in-progress transition.
        pass
    elif action == "complete" and record.review_instance_id:
        # Linked row: the Curriculum-driven review_instances engine is the
        # only writable source for this review's answers/status now (see the
        # skipped legacy validation above). Delegate to the exact same domain
        # function the canonical /coach/reviews/<id>/complete endpoint calls,
        # rather than re-deriving "complete" from the legacy reviewResponses
        # shape -- there is no safe translation from the hard-coded MCM/PR
        # field ids to this review's own Curriculum-defined fields, and the
        # instance's answers are already the canonical saved state (entered
        # via "Open form" / ReviewInstanceModal, not this payload).
        instance_row = curriculum_review_instances.get_review_instance(record.review_instance_id)
        if not instance_row:
            return JsonResponse({"detail": "This review's canonical record could not be found."}, status=404)
        ok, errors = curriculum_review_instances.complete_review_instance(instance_row, actor=owner_email)
        if not ok:
            detail = (errors or {}).get(
                "status",
                ["This review cannot be completed yet. Open its form to finish any unanswered required questions."],
            )[0]
            return JsonResponse({"detail": detail, "errors": errors}, status=400)
        instance_row = curriculum_review_instances.get_review_instance(instance_row["id"])
        _sync_calendar_record_to_review_instance_status(instance_row)
        record.refresh_from_db()
        record.owner_name = owner_name
    elif action == "complete":
        record.status = (
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE
            if completion_source == "progress-review"
            else CoachCalendarEvent.STATUS_COMPLETED
        )
        if review_responses is not None:
            record.review_responses = review_responses
            record.review_completed_at = timezone.now()
    elif action == "sign" and record.review_instance_id:
        # Linked row: record the signature only against the canonical
        # review_instances engine, which flips the instance to Completed once
        # every signature its Curriculum template requires is present AND
        # projects that onto this calendar row inside the same transaction
        # (see _mirror_linked_calendar_after_signature). No second status write
        # here -- one canonical transition, one mirror. The legacy
        # manager_signed_at/by columns are left untouched so they never
        # disagree with review_instance_signatures for a linked row.
        instance_row = curriculum_review_instances.get_review_instance(record.review_instance_id)
        if not instance_row:
            return JsonResponse({"detail": "This review's canonical record could not be found."}, status=404)
        try:
            curriculum_review_instances.record_review_instance_signature(
                instance_row, "advisor",
                signed_by=owner_email, signed_name=clean_text(payload.get("managerName")) or "Line Manager",
                signature="signed", actor=owner_email,
            )
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        record.refresh_from_db()
        record.owner_name = owner_name
    elif action == "sign":
        record.status = CoachCalendarEvent.STATUS_COMPLETED
        record.manager_signed_at = timezone.now()
        record.manager_signed_by = clean_text(payload.get("managerName")) or "Line Manager"
    elif action == "cancel":
        try:
            record, warning = cancel_reserved_calendar_event(record)
        except LearnerCalendarConflict as exc:
            return JsonResponse({"detail": str(exc)}, status=409)

    record.last_graph_sync_error = public_graph_sync_warning(warning)
    record.save()
    updated_event = overlay_calendar_record(base_event, record)
    return JsonResponse({"event": updated_event, "warning": warning})


def dashboard_attendance_rows(
    rows,
    learners: list[dict],
    *,
    aptem_by_profile: dict[int, int] | None = None,
) -> list[dict]:
    """Dashboard attendance from the same register used by learner pages."""
    if not learners:
        return []
    aptem_by_profile = aptem_by_profile if aptem_by_profile is not None else caseload_aptem_ids(rows)
    summaries = caseload_canonical_attendance(rows)

    payload = []
    for learner in learners:
        learner_id = to_int(learner.get("id"))
        metrics = summaries.get(learner_id)
        # A row with no register behind it carries nothing the client can
        # use -- `mergeAttendanceRates` would read it as "no attendance"
        # either way -- so it is left out rather than padding the payload.
        if metrics is None:
            continue
        # .get, not indexing: this enriches whatever the serializer produced,
        # and must never be the reason the whole dashboard 503s.
        last_session_date = metrics.get("lastSessionDate")
        payload.append({
            "id": learner.get("id"),
            "learner": learner.get("name"),
            "email": learner.get("email"),
            "attendance": metrics.get("rate", metrics.get("attendanceRate")),
            "hasAttendance": True,
            "sessions": metrics["sessions"],
            "present": metrics["present"],
            "absent": metrics["absent"],
            "consecutiveMissed": metrics.get("consecutiveMissed", 0),
            "lastSession": format_date_value(last_session_date),
            "lastSessionDate": last_session_date.isoformat() if hasattr(last_session_date, "isoformat") else last_session_date,
        })
    return payload


def dashboard_review_history(
    rows,
    *,
    aptem_by_profile: dict[int, int] | None = None,
) -> dict[int, dict]:
    """Read imported Aptem reviews for the dashboard caseload in one query.

    The learner workspace uses ``Learner.reviews`` for imported history. Keep
    that same source here, while preserving the dashboard's existing
    Curriculum/timetable data for future scheduled reviews. Learners without a
    valid Aptem id are deliberately omitted.
    """
    aptem_by_profile = aptem_by_profile if aptem_by_profile is not None else caseload_aptem_ids(rows)
    if not aptem_by_profile:
        return {}

    profile_ids = sorted(aptem_by_profile)
    placeholders = ", ".join(["%s"] * len(profile_ids))
    query = f"""
        SELECT learner_id, id, aptem_review_id, review_name, review_type,
               reviewer_name, learner_name, planned_scheduled_date,
               completed_date, status, review_data, extraction_status, last_error
        FROM "Learner".reviews
        WHERE learner_id IN ({placeholders})
          AND NULLIF(BTRIM(review_type), '') IS NOT NULL
        ORDER BY COALESCE(completed_date, planned_scheduled_date) DESC NULLS LAST,
                 id DESC
    """

    try:
        connection = connections[get_learner_db_alias()]
        with connection.cursor() as cursor:
            cursor.execute(query, profile_ids)
            columns = [column[0] for column in cursor.description]
            raw_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        # Review history is an optional enrichment. A missing or unavailable
        # import table must not take the whole coach dashboard down.
        logger.warning("Could not load dashboard review history: %s", exc)
        return {}

    monthly_coaching_types = {
        clean_text(review_type).casefold()
        for review_type in REVIEW_TYPES["monthly-coaching"]
    }
    grouped = {
        profile_id: {
            "id": str(profile_id),
            "aptemId": str(aptem_by_profile[profile_id]),
            "mcm": [],
            "reviews": [],
        }
        for profile_id in profile_ids
    }
    for row in raw_rows:
        try:
            profile_id = int(row.get("learner_id"))
        except (TypeError, ValueError):
            continue
        learner_history = grouped.get(profile_id)
        if learner_history is None:
            continue

        review = _serialize_review(row, {})
        category = (
            "mcm"
            if clean_text(review.get("type")).casefold() in monthly_coaching_types
            else "reviews"
        )
        learner_history[category].append(review)

    return {
        profile_id: history
        for profile_id, history in grouped.items()
        if history["mcm"] or history["reviews"]
    }


def latest_completed_review_dates_from_events(rows, events) -> dict[int, dict[str, str | None]]:
    """Select completed PR/MCM dates by stable profile id from resolved events.

    ``reviewCompletedAt`` is deliberately required. A planned/target/display
    date is never evidence that a Review completed, and confirmed, scheduled
    and in-progress occurrences are never treated as completed.
    """
    profile_ids = {int(row.id) for row in rows or [] if getattr(row, "id", None) is not None}
    latest: dict[int, dict[str, date | None]] = {
        profile_id: {"lastPr": None, "lastMcm": None}
        for profile_id in profile_ids
    }
    for event in events or []:
        if clean_text(event.get("status")).casefold() != CoachCalendarEvent.STATUS_COMPLETED:
            continue
        source = review_event_category(event)
        field = "lastPr" if source == "progress-review" else "lastMcm" if source == "mcr" else None
        if field is None:
            continue
        try:
            profile_id = int(clean_text(event.get("learnerId")))
        except (TypeError, ValueError):
            continue
        target = latest.get(profile_id)
        if target is None:
            continue
        completed_at = parse_date_value(event.get("reviewCompletedAt"))
        if isinstance(completed_at, datetime):
            completed_at = completed_at.date()
        if not isinstance(completed_at, date):
            continue
        if target[field] is None or completed_at > target[field]:
            target[field] = completed_at
    return {
        profile_id: {
            "lastPr": format_date(values["lastPr"]) if values["lastPr"] else None,
            "lastMcm": format_date(values["lastMcm"]) if values["lastMcm"] else None,
        }
        for profile_id, values in latest.items()
    }


def dashboard_imported_completed_review_dates(profile_ids, *, owner_email, owner_name):
    """Repair old Aptem PR snapshot dates with scoped reads, without native planning.

    The date-only dashboard rows do not contain the Aptem identity bridge, so
    load the small set of identity/display fields the imported adapter needs.
    """
    from .review_sources import enrich_imported_events

    profiles = list(LearnerProfile.objects.annotate(
        coach_email_key=Lower(Trim("coach_email")),
    ).filter(coach_email_key=normalize_email(owner_email), pk__in=profile_ids).only(
        "id", "aptem_id", "enrolment_id", "full_name", "email", "learner_type",
        "programme", "cohort", "group_name",
    ))
    aptem_by_profile, _conflicts = resolve_effective_aptem_ids(profiles)
    if not aptem_by_profile:
        return {}
    events, _contributors = fetch_aptem_review_events(
        profiles, aptem_by_profile, owner_email=owner_email, owner_name=owner_name,
    )
    return latest_completed_review_dates_from_events(
        [profile for profile in profiles if profile.id in aptem_by_profile],
        enrich_imported_events(events),
    )


def dashboard_latest_completed_review_dates(
    rows,
    *,
    owner_email: str,
    owner_name: str,
) -> dict[int, dict[str, str | None]]:
    """Resolve unbounded completed history with the shared Aptem/native rule."""
    rows = list(rows or [])
    empty = latest_completed_review_dates_from_events(rows, [])
    if not rows:
        return empty
    try:
        resolved = resolve_coach_review_events(owner_email, owner_name, rows)
        events = list(resolved.get("events", []))
        curriculum_events = [event for event in events if event.get("reviewSource") == "curriculum"]
        if curriculum_events:
            legacy_keys = [
                build_timetable_event_key(
                    int(event["learnerId"]),
                    event["source"],
                    event["sequence"],
                    parse_schedule_date(event["targetDate"]),
                )
                for event in curriculum_events
            ]
            legacy_records = fetch_calendar_event_records(owner_email, legacy_keys)
            record_map = curriculum_review_instances.reconcile_review_event_keys(
                curriculum_events,
                list(legacy_records.values()),
            )
            record_map.update(fetch_calendar_event_records(
                owner_email,
                [event["eventKey"] for event in curriculum_events],
            ))
            overlaid = {
                event["eventKey"]: overlay_calendar_record(event, record_map.get(event["eventKey"]))
                for event in curriculum_events
            }
            events = [overlaid.get(event.get("eventKey"), event) for event in events]
        from .review_sources import enrich_imported_events
        aptem_events = [event for event in events if event.get("reviewSource") == "aptem"]
        enriched = {event["eventKey"]: event for event in enrich_imported_events(aptem_events)}
        events = [enriched.get(event.get("eventKey"), event) for event in events]
        return latest_completed_review_dates_from_events(rows, events)
    except Exception as exc:
        logger.warning("Could not resolve latest completed review dates: %s", exc)
        return empty


def _latest_completed_review_date(reviews: list[dict]) -> str | None:
    """The latest completed imported review date, never a future planned date."""
    dates = [
        clean_text(review.get("completedDate"))
        for review in reviews
        if clean_text(review.get("status")).casefold() == "completed"
        and clean_text(review.get("completedDate"))
    ]
    return max(dates) if dates else None


@coach_access_required
@require_GET
def coach_imported_review_history(request):
    """Imported Aptem reviews and their sections for this coach's caseload."""
    owner_email = authenticated_coach_email(request)
    try:
        rows = fetch_caseload_dashboard_profiles(owner_email)
        history = dashboard_review_history(rows)
    except Exception:
        logger.exception("coach_imported_review_history_failed coach_account_id=%s", owner_email)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load imported review history.",
            status=503,
        )

    learners = []
    for row in rows:
        learner_history = history.get(int(row.id))
        if not learner_history:
            continue
        learners.append({
            **learner_history,
            "name": clean_text(getattr(row, "username", None)) or "Unknown learner",
            "email": clean_text(getattr(row, "email", None)) or None,
            "learnerType": (
                "commercial"
                if clean_text(getattr(row, "learner_type", None)).casefold() == "commercial"
                else "apprenticeship"
            ),
            "enrolmentId": str(row.enrolment_id) if getattr(row, "enrolment_id", None) else None,
        })
    return JsonResponse({"learners": learners})


def serialize_attendance_learner(
    learner: dict,
    attendance_metrics: dict | None,
    catchup_count: int = 0,
) -> dict:
    sessions = attendance_metrics.get("sessions", 0) if attendance_metrics else 0
    present = attendance_metrics.get("present", 0) if attendance_metrics else 0
    absent = attendance_metrics.get("absent", 0) if attendance_metrics else 0
    recorded_sessions = present + absent
    attendance_rate = percentage(present, recorded_sessions) if recorded_sessions else None
    risk = attendance_metrics.get("risk") if attendance_metrics else None
    if risk not in {"green", "amber", "red"}:
        risk = attendance_risk_from_rate(attendance_rate)

    return {
        "id": learner["id"],
        "learner": learner["name"],
        "initials": learner["initials"],
        "learnerType": learner.get("learnerType"),
        "enrolmentId": learner.get("enrolmentId"),
        "currentModule": learner.get("currentModule"),
        "currentWeek": learner.get("currentWeek"),
        "componentsTargetToDate": learner.get("componentsTargetToDate"),
        "email": learner.get("email"),
        "programme": learner.get("programmeName") or learner["cohortName"],
        "programmeId": learner.get("programmeId"),
        "cohort": learner["cohortName"],
        "group": learner["group"],
        "groupName": learner.get("groupName") or learner["group"],
        "groupId": learner.get("groupId"),
        "programStatus": learner["rawProgramStatus"],
        "enrollmentStatus": learner["enrollmentStatus"],
        "isOnBreak": learner["enrollmentStatus"] == "break",
        "includedInAttendanceMetrics": should_include_in_attendance_metrics(learner),
        "attendance": attendance_rate,
        "sessions": sessions if sessions else None,
        "present": present if sessions else None,
        "absent": absent if sessions else None,
        "late": attendance_metrics.get("late", 0) if attendance_metrics else None,
        "catchup": max(catchup_count, attendance_metrics.get("catchup", 0) if attendance_metrics else 0),
        "authorisedAbsent": attendance_metrics.get("authorisedAbsent", 0) if attendance_metrics else None,
        "unauthorisedAbsent": attendance_metrics.get("unauthorisedAbsent", 0) if attendance_metrics else None,
        "absenceReasons": attendance_metrics.get("absenceReasons", {}) if attendance_metrics else {},
        "risk": risk,
        "employer": learner["employer"],
        "overallProgress": learner["overallProgress"],
        "otjhCompleted": learner["otjhCompleted"],
        "otjhTarget": learner["otjhPlanned"] or learner["otjhTarget"],
        "ksbProgress": learner["ksbProgress"],
        "lastSession": attendance_metrics.get("lastSession", "--") if attendance_metrics else "--",
        "lastSessionDate": attendance_metrics.get("lastSessionDate") if attendance_metrics else None,
        "nextSession": "--",
        "consecutiveMissed": attendance_metrics.get("consecutiveMissed", 0) if attendance_metrics else None,
        "hasAttendance": bool(sessions),
    }


def normalize_attendance_detail_status(value) -> str:
    text = clean_text(value).lower()
    if text in {"1", "true", "yes", "y", "present", "attended", "attend", "late"}:
        return "present"
    if text in {"0", "false", "no", "n", "absent", "missed", "did not attend", "non-attendance"}:
        return "absent"
    return text or "--"


def serialize_attendance_register_row(row: dict) -> dict:
    return {
        "learnerId": clean_text(row.get("learner_id")),
        "learnerName": clean_text(row.get("learner_name")) or "Learner",
        "learnerEmail": clean_text(row.get("learner_email")),
        "sessionId": clean_text(row.get("session_id")) or "--",
        "sessionTitle": clean_text(row.get("session_title")) or "Live session",
        "subjectId": clean_text(row.get("module_catalogue_id") or row.get("module_id") or row.get("live_session_id") or row.get("session_id")),
        "subjectTitle": clean_text(row.get("module_title")) or clean_text(row.get("session_title")) or "Live session",
        "sessionDate": format_iso_date_value(row.get("session_date")),
        "sessionDateLabel": format_date_value(row.get("session_date")),
        "status": normalize_attendance_detail_status(row.get("attendance_status")),
    }


def coach_attendance_register_rows(caseload_rows) -> list[dict]:
    """Build the coach register from the canonical learner lecture register."""
    from learner_api.attendance_lectures import lecture_register

    result: list[dict] = []
    for profile in caseload_rows:
        source = getattr(profile, "_caseload_source", None)
        if source is None:
            continue
        try:
            result.extend(serialize_attendance_register_row(item) for item in lecture_register(source))
        except Exception:
            logger.exception("coach_attendance_register_learner_failed learner_id=%s", profile.id)
    return result


def coach_verified_teams_attendance_rows(attendance_data: dict) -> list[dict]:
    """Project verified Teams occurrences into the overview's stable contract."""
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in attendance_data.get("rows") or []:
        serialized = serialize_attendance_register_row(row)
        if serialized["status"] not in {"present", "absent"}:
            continue
        identity = (serialized["learnerId"], serialized["sessionId"])
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        result.append(serialized)
    return result


def coach_last_four_attendance_rows(attendance_data: dict, caseload_rows, aptem_by_profile: dict[int, int]) -> list[dict]:
    """Use KBC for Aptem-linked learners and verified Teams for everyone else."""
    aptem_profile_ids = {str(profile_id) for profile_id in aptem_by_profile}
    result = [
        row for row in coach_verified_teams_attendance_rows(attendance_data)
        if row["learnerId"] not in aptem_profile_ids
    ]
    rows_by_profile = {int(row.id): row for row in caseload_rows or []}
    kbc_learners = []
    for profile_id, aptem_id in aptem_by_profile.items():
        profile = rows_by_profile.get(profile_id)
        if profile is None:
            continue
        kbc_learners.append({
            "aptem_id": aptem_id,
            "learner_id": profile_id,
            "learner_name": clean_text(getattr(profile, "full_name", None)),
            "learner_email": normalize_email(getattr(profile, "email", None)),
        })
    try:
        result.extend(
            serialize_attendance_register_row(row)
            for row in fetch_recent_kbc_attendance_rows(kbc_learners, limit=4)
        )
    except Exception as exc:  # noqa: BLE001
        # KBC is an optional read source for this projection. Do not replace a
        # failed/missing KBC result with another learner identity or Teams data.
        logger.warning("Could not read recent KBC attendance for coach caseload: %s", exc)
    return result


def fetch_attendance_detail_rows(learner: dict) -> list[dict]:
    rows = fetch_verified_teams_attendance_rows(
        [to_int(learner.get("id"))],
        [normalize_email(learner.get("email"))],
        include_reported_participants=True,
    )

    return [
        {
            "learnerId": clean_text(row.get("learner_id")),
            "learnerName": clean_text(row.get("learner_name")) or learner.get("name", ""),
            "learnerEmail": clean_text(row.get("learner_email")) or learner.get("email", ""),
            "sessionId": clean_text(row.get("session_id")) or "--",
            "sessionTitle": clean_text(row.get("session_title")) or "--",
            "sessionType": clean_text(row.get("session_type")) or "--",
            "sessionDate": format_iso_date_value(row.get("session_date")),
            "sessionDateLabel": format_date_value(row.get("session_date")),
            "startTime": format_time_value(row.get("session_start_time")) or "--",
            "endTime": format_time_value(row.get("session_end_time")) or "--",
            "status": normalize_attendance_detail_status(row.get("attendance_status")),
            "reason": clean_text(row.get("absence_reason")) or "--",
            "catchupCompleted": is_truthy_value(row.get("catchup_completed")),
            "attendedSeconds": to_int(row.get("attended_seconds")),
        }
        for row in rows
    ]


def canonical_attendance_detail_rows(source, *, learner_profile_id):
    from learner_api.attendance_lectures import attendance_read_contract
    payload = attendance_read_contract(source, learner_profile_id=learner_profile_id)
    return payload['summary'], payload['history']


def serialize_manual_attendance(row: CoachManualAttendance) -> dict:
    return {
        "learnerId": str(row.learner_id),
        "sessionId": f"manual-{row.id}",
        "manualId": str(row.id),
        "sessionTitle": row.session_title,
        "sessionType": row.module_name,
        "sessionDate": row.session_date.isoformat(),
        "sessionDateLabel": format_date_value(row.session_date),
        "status": row.status,
        "source": "coach-manual",
    }


def _manual_attendance_learner(owner_email: str, learner_id: str):
    rows = fetch_attendance_caseload_rows(owner_email, learner_id=learner_id)
    attach_caseload_source_rows(rows)
    if not rows:
        return None, None
    row = rows[0]
    return row, serialize_attendance_source_learner(row)


@coach_access_required
@require_http_methods(["POST", "PATCH", "DELETE"])
def coach_manual_attendance(request, record_id=None):
    owner_email = authenticated_coach_email(request)
    if is_coach_view_as(request):
        return JsonResponse({"detail": "Manual attendance changes are unavailable in read-only view-as mode."}, status=403)

    record = None
    if record_id is not None:
        record = CoachManualAttendance.objects.filter(id=record_id, owner_email__iexact=owner_email).first()
        if record is None:
            return JsonResponse({"detail": "Manual attendance record not found."}, status=404)
        if request.method == "DELETE":
            record.delete()
            return JsonResponse({}, status=204)

    try:
        payload = parse_json_object(request)
    except ValidationError as exc:
        return validation_error_response(exc)

    learner_id = clean_text(payload.get("learnerId")) if record is None else str(record.learner_id)
    if not learner_id.isdigit():
        return JsonResponse({"detail": "Learner id must be a valid number."}, status=400)
    profile, learner = _manual_attendance_learner(owner_email, learner_id)
    if profile is None or learner is None:
        return JsonResponse({"detail": "Learner not found in this coach caseload."}, status=404)

    try:
        session_date = date.fromisoformat(clean_text(payload.get("date")))
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Date must be a valid ISO date."}, status=400)
    module_name = clean_text(payload.get("module"))
    session_title = clean_text(payload.get("sessionTitle"))
    status = clean_text(payload.get("status")).lower()
    errors = {}
    if not module_name:
        errors["module"] = ["Module is required."]
    if not session_title:
        errors["sessionTitle"] = ["Lecture / Session is required."]
    if status not in {"present", "absent"}:
        errors["status"] = ["Status must be present or absent."]
    if errors:
        return JsonResponse({"detail": "Please correct the highlighted fields.", "errors": errors}, status=400)

    values = {
        "session_date": session_date,
        "module_name": module_name,
        "session_title": session_title,
        "status": status,
    }
    if record is None:
        source = getattr(profile, "_caseload_source", None)
        # Identical retries reuse a record; ambiguous entries remain separate.
        existing = CoachManualAttendance.objects.filter(
            learner_id=int(learner["id"]), **values).order_by("id").first()
        if existing is not None:
            return JsonResponse(serialize_manual_attendance(existing), status=200)
        record = CoachManualAttendance.objects.create(
            owner_email=owner_email,
            learner_id=int(learner["id"]),
            enrolment_id=getattr(source, "id", None),
            learner_name=learner["name"],
            learner_email=learner.get("email") or "",
            created_by=owner_email,
            **values,
        )
        status_code = 201
    else:
        for field, value in values.items():
            setattr(record, field, value)
        record.save(update_fields=[*values.keys(), "updated_at"])
        status_code = 200
    return JsonResponse(serialize_manual_attendance(record), status=status_code)


@coach_access_required
@require_http_methods(["PATCH", "DELETE"])
def coach_source_attendance(request):
    owner_email = authenticated_coach_email(request)
    if is_coach_view_as(request):
        return JsonResponse({"detail": "Attendance changes are unavailable in read-only view-as mode."}, status=403)
    try:
        payload = parse_json_object(request)
    except ValidationError as exc:
        return validation_error_response(exc)

    learner_id = clean_text(payload.get("learnerId"))
    source_name = clean_text(payload.get("source"))
    source_id = clean_text(payload.get("sourceId"))
    if not learner_id.isdigit() or source_name not in {"microsoft-teams", "kbc-attendance"} or not source_id:
        return JsonResponse({"detail": "A valid learner and attendance source are required."}, status=400)
    profile, learner = _manual_attendance_learner(owner_email, learner_id)
    if profile is None or learner is None:
        return JsonResponse({"detail": "Learner not found in this coach caseload."}, status=404)

    from learner_api.attendance_lectures import lecture_register
    enrolment_source = getattr(profile, '_caseload_source', None)
    if enrolment_source is None:
        return JsonResponse({"detail": "Learner attendance source is unavailable."}, status=404)
    register = lecture_register(enrolment_source, learner_profile_id=profile.id, apply_adjustments=False)
    if not any(row.get('source') == source_name and str(row.get('session_id')) == source_id for row in register):
        return JsonResponse({"detail": "Attendance source does not belong to this learner register."}, status=404)

    lookup = {"learner_id": int(learner_id), "source": source_name, "source_id": source_id}
    if request.method == "DELETE":
        CoachAttendanceSourceAdjustment.objects.update_or_create(
            **lookup,
            defaults={"owner_email": owner_email, "updated_by": owner_email, "is_deleted": True},
        )
        return JsonResponse({}, status=204)

    try:
        session_date = date.fromisoformat(clean_text(payload.get("date")))
    except (TypeError, ValueError):
        return JsonResponse({"detail": "Date must be a valid ISO date."}, status=400)
    module_name = clean_text(payload.get("module"))
    session_title = clean_text(payload.get("sessionTitle"))
    status = clean_text(payload.get("status")).lower()
    if not module_name or not session_title or status not in {"present", "absent"}:
        return JsonResponse({"detail": "Date, module, session and a valid status are required."}, status=400)
    CoachAttendanceSourceAdjustment.objects.update_or_create(
        **lookup,
        defaults={
            "owner_email": owner_email,
            "session_date": session_date,
            "module_name": module_name,
            "session_title": session_title,
            "status": status,
            "is_deleted": False,
            "updated_by": owner_email,
        },
    )
    return JsonResponse({"ok": True})


@coach_access_required
@require_GET
def coach_attendance_details(request):
    from .attendance_detail import detail_records
    from .attendance_projection import AttendancePaginationError, pagination_params

    owner_email = authenticated_coach_email(request)
    learner_id = clean_text(request.GET.get("learner_id"))
    learner_email = normalize_email(request.GET.get("learner_email"))
    try:
        pagination_params(request.GET)
    except AttendancePaginationError as error:
        return JsonResponse({"detail": str(error)}, status=400)

    try:
        rows = fetch_attendance_caseload_rows(owner_email, learner_id=learner_id or None, include_plan=False)
        profile = next((row for row in rows if
            (learner_id and str(row.id) == learner_id) or
            (learner_email and normalize_email(row.email) == learner_email)), None)
        if profile is None:
            return JsonResponse({"detail": "Learner not found in this coach caseload."}, status=404)
        commercial, enrolment = fetch_source_schedule_rows([profile], fields=(
            'id', 'username', 'email', 'aptem_id', 'employer_id', 'learner_type',
            'learner_start_date', 'learner_end_date'))
        source = resolve_caseload_source_row(profile, commercial_rows=commercial, enrolment_rows=enrolment)
        if source is None:
            return JsonResponse({"detail": "Learner attendance source is unavailable."}, status=404)
        detail = detail_records(source, profile.id, request.GET)
        learner = {
            "id": str(profile.id), "name": clean_text(profile.username) or "Unknown learner",
            "email": clean_text(profile.email) or None,
            "programme": clean_text(profile.programme) or "--",
            "cohort": clean_text(profile.cohort) or "--",
            "group": clean_text(profile.group_name) or "--",
        }
        # Verified PDF-report fields; omit absent dates instead of extra nulls.
        for field, key in (("learner_start_date", "learnerStartDate"), ("learner_end_date", "learnerEndDate")):
            if value := clean_text(getattr(source, field, None)):
                learner[key] = value
        return JsonResponse({"learner": learner,
            "coach": {"name": clean_text(profile.coach_name) or "Coach", "email": owner_email},
            "tutor": None, **detail})
    except Exception:
        logger.exception("coach_attendance_details_failed coach_account_id=%s learner_id=%s", owner_email, learner_id)
        return coach_error(request, code="database_unavailable",
            message="Unable to load learner attendance details.", status=503)


@coach_access_required
@require_GET
def coach_timetable(request):
    owner_email = authenticated_coach_email(request)
    validator = ObjectValidator(request.GET)
    start_date = validator.iso_date("start")
    end_date = validator.iso_date("end")
    if start_date and end_date and start_date > end_date:
        validator.error("end", "Must be on or after start.")
    try:
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)
    include_live_sessions = clean_text(request.GET.get("include_live_sessions", "1")).casefold() not in {"0", "false", "no", "off"}
    include_scheduler_queues = clean_text(request.GET.get("include_scheduler_queues", "1")).casefold() not in {"0", "false", "no", "off"}
    # Catch-ups only: close this coach's elapsed catch-ups before listing them.
    from learner_api.catchup_outcomes import sync_catchup_outcomes
    sync_catchup_outcomes(owner_email=owner_email)

    try:
        timetable_payload = collect_generated_timetable(
            owner_email,
            start_date=start_date,
            end_date=end_date,
            include_live_sessions=include_live_sessions,
            include_scheduler_queues=include_scheduler_queues,
        )
        # Elapsed meetings show Ended, or Completed when the learner attended.
        from .meeting_outcomes import annotate_meeting_outcomes
        annotate_meeting_outcomes(timetable_payload["events"])
        # Live sessions keep their own rule: Completed when any learner attended.
        from .live_session_outcomes import annotate_live_session_outcomes
        annotate_live_session_outcomes(timetable_payload["events"])
    except Exception:
        logger.exception("coach_timetable_load_failed coach_account_id=%s", owner_email)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load coach timetable data.",
            status=503,
        )

    return JsonResponse(
        {
            "owner": {"name": timetable_payload["owner_name"], "email": owner_email},
            "summary": timetable_payload["summary"],
            "events": timetable_payload["events"],
            "reviewGenerationIssues": timetable_payload.get("reviewGenerationIssues", []),
            "schedulerQueues": timetable_payload.get("schedulerQueues", {}),
        }
    )


def caseload_counts_by_coach(coach_emails: set[str]) -> dict[str, dict[str, int]]:
    """Learner counts per coach email, in one pass over the caseload table.

    Reusing ``fetch_caseload_dashboard_profiles`` per coach would issue a query
    and serialise a full caseload for every card on the picker; each card shows
    two integers.
    """
    counts: dict[str, dict[str, int]] = {}
    if not coach_emails:
        return counts

    # Not ``.iterator()``: the pooled connection this runs over rejects the
    # server-side cursor it opens ("portal does not exist"). Three short columns
    # for the coached learners is a small result set to hold.
    rows = (
        LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
        .filter(coach_email_key__in=sorted(coach_emails))
        .values_list("coach_email_key", "programme_status", "full_name")
    )
    for email_key, programme_status, full_name in rows:
        # The same "has a name" guard the caseload fetches apply — a row without
        # one is a placeholder and is not counted as a learner anywhere else.
        if not clean_text(full_name):
            continue
        bucket = counts.setdefault(email_key, {"total": 0, "active": 0})
        bucket["total"] += 1
        if normalize_program_status(programme_status) == "active":
            bucket["active"] += 1
    return counts


def coach_performance_metrics_by_coach(
    coach_emails: set[str], *, today: date | None = None,
) -> dict[str, dict]:
    """Summarise coach performance from the authoritative learner sources.

    Attendance and OTJH remain compact values in the dashboard snapshot. PR and
    MCM figures come from ``Learner.reviews`` and pending work comes from the
    assignment marking table; neither may be inferred from generated calendar
    events in a snapshot.
    """
    if not coach_emails:
        return {}

    today = today or timezone.localdate()
    pr_start = today - timedelta(weeks=12)
    mcm_start = today - timedelta(weeks=4)
    # Production model names use Django's schema-qualified ``Schema\".\"table``
    # convention and therefore need the same outer quotes Django adds.
    table = f'"{CoachDashboardSnapshot._meta.db_table}"'
    database = router.db_for_read(CoachDashboardSnapshot)

    # Do not select ``payload`` into Python. A single snapshot can contain
    # hundreds of full review-event objects; transferring and decoding all
    # snapshots made this directory request hit the 60-second DB timeout. Let
    # Postgres expand the JSONB and return only the small aggregate row.
    sql = f"""
        WITH selected AS (
            SELECT lower(trim(owner_email)) AS email, payload
              FROM {table}
             WHERE lower(trim(owner_email)) = ANY(%s)
        ), coach_profiles AS (
            SELECT lower(trim(coach_email)) AS email,
                   id AS profile_id,
                   enrolment_id,
                   full_name,
                   programme_status
              FROM "Learner".learners
             WHERE lower(trim(coach_email)) = ANY(%s)
               AND nullif(trim(full_name), '') IS NOT NULL
        ), learner_profiles AS (
            SELECT email, profile_id, enrolment_id
              FROM coach_profiles
             WHERE enrolment_id IS NOT NULL
               AND regexp_replace(
                   lower(coalesce(programme_status, '')), '[^a-z0-9]', '', 'g'
               ) IN ('active', 'delivery')
        ), learner_metrics AS (
            SELECT selected.email,
                   round(avg((learner->>'attendanceRate')::numeric)
                       FILTER (WHERE learner->>'enrollmentStatus' = 'active'
                               AND learner->>'attendanceRateAvailable' = 'true'
                               AND learner->>'attendanceRate' IS NOT NULL))::int AS attendance_rate,
                   count(*) FILTER (WHERE learner->>'enrollmentStatus' = 'active'
                                     AND learner->>'otjhRagStatus' = 'on-track')::int AS on_track,
                   count(*) FILTER (WHERE learner->>'enrollmentStatus' = 'active'
                                     AND learner->>'otjhRagStatus' = 'need-attention')::int AS need_attention,
                   count(*) FILTER (WHERE learner->>'enrollmentStatus' = 'active'
                                     AND learner->>'otjhRagStatus' = 'at-risk')::int AS at_risk
              FROM selected
              CROSS JOIN LATERAL jsonb_array_elements(
                  coalesce(selected.payload->'learners', '[]'::jsonb)
              ) AS learner
             GROUP BY selected.email
        ), review_rows AS (
            SELECT profiles.email,
                   CASE
                     WHEN lower(trim(reviews.review_type)) = ANY(%s) THEN 'progress-review'
                     WHEN lower(trim(reviews.review_type)) = ANY(%s) THEN 'mcm'
                   END AS family,
                   lower(trim(coalesce(reviews.status, ''))) IN (
                       'completed', 'complete', 'finished'
                   ) AS is_completed,
                   coalesce(reviews.completed_date, reviews.planned_scheduled_date)::date AS review_date
              FROM learner_profiles AS profiles
              JOIN "Learner".reviews AS reviews
                ON reviews.learner_id = profiles.profile_id
             WHERE lower(trim(reviews.review_type)) = ANY(%s)
                OR lower(trim(reviews.review_type)) = ANY(%s)
        ), review_metrics AS (
            SELECT email,
                   count(*) FILTER (WHERE is_completed)::int AS completed_reviews,
                   count(*) FILTER (WHERE family = 'progress-review'
                                     AND review_date BETWEEN %s AND %s)::int AS pr_required,
                   count(*) FILTER (WHERE family = 'progress-review' AND is_completed
                                     AND review_date BETWEEN %s AND %s)::int AS pr_completed,
                   count(*) FILTER (WHERE family = 'progress-review' AND NOT is_completed
                                     AND review_date BETWEEN %s AND %s AND review_date < %s)::int AS pr_overdue,
                   count(*) FILTER (WHERE family = 'mcm'
                                     AND review_date BETWEEN %s AND %s)::int AS mcm_required,
                   count(*) FILTER (WHERE family = 'mcm' AND is_completed
                                     AND review_date BETWEEN %s AND %s)::int AS mcm_completed,
                   count(*) FILTER (WHERE family = 'mcm' AND NOT is_completed
                                     AND review_date BETWEEN %s AND %s AND review_date < %s)::int AS mcm_overdue
              FROM review_rows
             GROUP BY email
        ), pending_assignment_metrics AS (
            SELECT profiles.email, count(*)::int AS pending_assignments
              FROM coach_profiles AS profiles
              JOIN "Learner".learning_reflection_submissions AS assignments
                ON assignments.learner_id = profiles.enrolment_id::text
             WHERE assignments.status IN ('submitted_for_tutor_review', 'escalated')
               AND lower(trim(assignments.activity_type)) = ANY(%s)
             GROUP BY profiles.email
        )
        SELECT selected.email,
               learner_metrics.attendance_rate,
               coalesce(pending_assignment_metrics.pending_assignments, 0),
               coalesce(learner_metrics.on_track, 0),
               coalesce(learner_metrics.need_attention, 0),
               coalesce(learner_metrics.at_risk, 0),
               coalesce(review_metrics.completed_reviews, 0),
               coalesce(review_metrics.pr_required, 0),
               coalesce(review_metrics.pr_completed, 0),
               coalesce(review_metrics.pr_overdue, 0),
               coalesce(review_metrics.mcm_required, 0),
               coalesce(review_metrics.mcm_completed, 0),
               coalesce(review_metrics.mcm_overdue, 0)
          FROM selected
          LEFT JOIN learner_metrics USING (email)
          LEFT JOIN review_metrics USING (email)
          LEFT JOIN pending_assignment_metrics USING (email)
    """
    progress_review_types = [value.casefold() for value in REVIEW_TYPES["progress-review"]]
    monthly_review_types = [value.casefold() for value in REVIEW_TYPES["monthly-coaching"]]
    params = [
        sorted(coach_emails),
        sorted(coach_emails),
        progress_review_types, monthly_review_types,
        progress_review_types, monthly_review_types,
        pr_start, today, pr_start, today, pr_start, today, today,
        mcm_start, today, mcm_start, today, mcm_start, today, today,
        list(ASSIGNMENT_ACTIVITY_TYPES),
    ]
    with connections[database].cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        (
            email, attendance_rate, pending_marking,
            on_track, need_attention, at_risk, completed_reviews,
            pr_required, pr_completed, pr_overdue,
            mcm_required, mcm_completed, mcm_overdue,
        ) = row
        progress_reviews = {
            "required": pr_required,
            "completed": pr_completed,
            "overdue": pr_overdue,
        }
        result[email] = {
            "available": True,
            "attendanceRate": attendance_rate,
            "progressReviewRate": (
                round(pr_completed / pr_required * 100) if pr_required else None
            ),
            "pendingMarking": pending_marking,
            "completedReviews": completed_reviews,
            "otjh": {
                "onTrack": on_track,
                "needAttention": need_attention,
                "atRisk": at_risk,
            },
            "progressReviews": progress_reviews,
            "monthlyCoaching": {
                "required": mcm_required,
                "completed": mcm_completed,
                "overdue": mcm_overdue,
            },
        }
    return result


@require_access(ACCESS_SUPER_ADMIN)
@require_GET
def coach_directory_calendar(request):
    """Return one week's calendar events for every coach in one snapshot read.

    The overview used to call the full timetable endpoint once per coach. Each
    call rebuilds live sessions and review schedules from Curriculum, so the
    page waited for the slowest of twenty-plus expensive requests. Dashboard
    snapshots already hold the same read-only meeting projection; filter their
    JSON in PostgreSQL and return only the requested week.
    """
    validator = ObjectValidator(request.GET)
    start_date = validator.iso_date("start")
    end_date = validator.iso_date("end")
    if start_date is None:
        validator.error("start", "Start date is required.")
    if end_date is None:
        validator.error("end", "End date is required.")
    if start_date and end_date:
        if start_date > end_date:
            validator.error("end", "Must be on or after start.")
        elif (end_date - start_date).days > 31:
            validator.error("end", "Calendar range cannot exceed 31 days.")
    try:
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        coach_emails = {
            clean_text(value).casefold()
            for value in StaffUser.objects.annotate(
                staff_access_key=Lower(Trim("access")),
            )
            .filter(staff_access_key=ACCESS_COACH)
            .values_list("email", flat=True)
            if clean_text(value)
        }
        if not coach_emails:
            return JsonResponse({"calendars": [], "missingCoaches": []})

        table = f'"{CoachDashboardSnapshot._meta.db_table}"'
        database = router.db_for_read(CoachDashboardSnapshot)
        sql = f"""
            WITH selected AS (
                SELECT lower(trim(owner_email)) AS email, payload, refreshed_at
                  FROM {table}
                 WHERE lower(trim(owner_email)) = ANY(%s)
            ), event_rows AS (
                SELECT selected.email,
                       selected.refreshed_at,
                       event,
                       CASE
                         WHEN coalesce(
                             event->>'scheduledDate', event->>'date', event->>'targetDate', ''
                         ) ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                         THEN substring(coalesce(
                             event->>'scheduledDate', event->>'date', event->>'targetDate'
                         ) FROM 1 FOR 10)::date
                       END AS event_date
                  FROM selected
                  CROSS JOIN LATERAL jsonb_array_elements(
                      coalesce(selected.payload->'meetings'->'events', '[]'::jsonb)
                  ) AS event
            )
            SELECT email, refreshed_at, event
              FROM event_rows
             WHERE event_date BETWEEN %s AND %s
             ORDER BY email, event_date,
                      coalesce(event->>'scheduledTime', '99:99'),
                      coalesce(event->>'learner', event->>'title', '')
        """
        with connections[database].cursor() as cursor:
            cursor.execute(sql, [sorted(coach_emails), start_date, end_date])
            rows = cursor.fetchall()
    except DatabaseError:
        logger.exception("coach_directory_calendar_failed")
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load the all-coaches calendar.",
            status=503,
        )

    events_by_email: dict[str, list[dict]] = {email: [] for email in coach_emails}
    refreshed_by_email: dict[str, datetime] = {}
    for email, refreshed_at, event in rows:
        if isinstance(event, str):
            try:
                event = json.loads(event)
            except (TypeError, ValueError):
                continue
        if not isinstance(event, dict):
            continue
        events_by_email.setdefault(email, []).append(event)
        refreshed_by_email[email] = refreshed_at

    snapshot_emails = {
        clean_text(value).casefold()
        for value in CoachDashboardSnapshot.objects.annotate(
            owner_email_key=Lower(Trim("owner_email")),
        ).filter(
            owner_email_key__in=sorted(coach_emails),
        ).values_list("owner_email_key", flat=True)
        if clean_text(value)
    }
    return JsonResponse({
        "calendars": [
            {
                "email": email,
                "events": events_by_email[email],
                "refreshedAt": (
                    refreshed_by_email[email].isoformat()
                    if email in refreshed_by_email else None
                ),
            }
            for email in sorted(coach_emails)
            if email in snapshot_emails
        ],
        "missingCoaches": sorted(coach_emails - snapshot_emails),
    })


@require_access(ACCESS_SUPER_ADMIN)
@require_GET
def coach_directory(request):
    """The accounts holding Coach access, for the admin's coach-workspace picker.

    Gated on ``ACCESS_SUPER_ADMIN`` rather than ``ACCESS_COACH`` because this is
    the one coach route where that grant is the requirement and not a bypass: a
    coach has no reason to enumerate their colleagues' caseloads.
    """
    try:
        staff_rows = list(
            StaffUser.objects.annotate(
                staff_email_key=Lower(Trim("email")),
                staff_access_key=Lower(Trim("access")),
            )
            .filter(staff_access_key=ACCESS_COACH)
            .values("id", "username", "email", "staff_email_key")
            .order_by("username", "id")
        )
    except DatabaseError:
        logger.exception("coach_directory_staff_lookup_failed")
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load the coach directory.",
            status=503,
        )

    emails = {row["staff_email_key"] for row in staff_rows if row["staff_email_key"]}
    counts_available = True
    try:
        counts = caseload_counts_by_coach(emails)
    except DatabaseError:
        # The list itself is still worth serving: a caseload database that is
        # down must not hide which accounts hold Coach access.
        logger.exception("coach_directory_caseload_counts_failed")
        counts_available = False
        counts = {}

    try:
        performance = coach_performance_metrics_by_coach(emails)
    except DatabaseError:
        logger.exception("coach_directory_performance_metrics_failed")
        performance = {}

    coaches: list[dict] = []
    seen: set[str] = set()
    for row in staff_rows:
        email = row["staff_email_key"]
        # An address is what every coach-scoped query is keyed by, so a staff row
        # without one cannot open a workspace and is left out rather than shown
        # as a card that loads nothing.
        if not email or email in seen:
            continue
        seen.add(email)
        bucket = counts.get(email, {})
        coaches.append(
            {
                "id": row["id"],
                "name": clean_text(row["username"]) or email,
                "email": email,
                "caseloadCount": bucket.get("total", 0),
                "activeLearnerCount": bucket.get("active", 0),
                "performance": performance.get(email),
            }
        )

    return JsonResponse(
        {
            "coaches": coaches,
            "count": len(coaches),
            "caseloadCountsAvailable": counts_available,
        }
    )


@coach_access_required
@require_GET
def coach_learner_case_file(request, learner_id):
    owner_email = authenticated_coach_email(request)
    try:
        profile, source = _case_file_shell_for_request(request, owner_email, learner_id)
    except DatabaseError:
        logger.exception("coach_case_file_shell_failed learner_id=%s", learner_id)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load learner case file details.",
            status=503,
        )
    if profile is None:
        return JsonResponse({"detail": "Learner not found."}, status=404)
    return JsonResponse(serialize_case_file_shell(profile, source))


def _case_file_learner_not_found():
    # Keep ownership failures indistinguishable from an unknown id.
    return JsonResponse({"detail": "Learner not found."}, status=404)


def _case_file_shell_for_request(request, owner_email, learner_id):
    context = getattr(request, '_case_file_context', None)
    if context is not None and context.coach == owner_email and context.learner_id == learner_id:
        return context.profile, context.source
    return fetch_case_file_shell(owner_email, learner_id)


def _case_file_owner_name(owner_email, profile):
    return coach_staff_display_name(owner_email) or clean_text(getattr(profile, "coach_name", None)) or "Coach"


def _case_file_review_events(owner_email, profile):
    """Resolve the canonical OLD/NEW stream for exactly one authorized learner."""
    owner_name = _case_file_owner_name(owner_email, profile)
    resolved = resolve_coach_review_events(owner_email, owner_name, [profile])
    events = resolved["events"]
    curriculum_events = [event for event in events if event.get("reviewSource") == "curriculum"]
    aptem_events = [event for event in events if event.get("reviewSource") == "aptem"]
    generated_keys = {event["eventKey"] for event in events}
    from .review_sources import standalone_review_records
    standalone_records = standalone_review_records(
        fetch_standalone_event_records(owner_email, learner_id=int(profile.id)), [profile],
        resolved["aptemProfileIds"], linked_keys=generated_keys)
    legacy_keys = [
        build_timetable_event_key(
            int(event["learnerId"]), event["source"], event["sequence"], parse_schedule_date(event["targetDate"]),
        )
        for event in curriculum_events
    ]
    legacy_records = fetch_calendar_event_records(owner_email, legacy_keys) if legacy_keys else {}
    stored_records = {record.event_key: record for record in standalone_records}
    stored_records.update(legacy_records)
    curriculum_records = curriculum_review_instances.reconcile_review_event_keys(curriculum_events, list(stored_records.values()))
    curriculum_keys = [event["eventKey"] for event in curriculum_events]
    if curriculum_keys:
        curriculum_records.update(fetch_calendar_event_records(owner_email, curriculum_keys))
    from .review_sources import enrich_imported_events
    shaped = [overlay_calendar_record(event, curriculum_records.get(event["eventKey"])) for event in curriculum_events]
    shaped.extend(enrich_imported_events(aptem_events))
    review_type_fields = review_type_fields_by_template(getattr(record, "review_template_id", "") for record in standalone_records)
    shaped.extend(
        event for event in (
            build_catchup_calendar_event(record, owner_name=owner_name, learner=profile, review_type_fields=review_type_fields)
            for record in standalone_records
        )
        if clean_text(event.get("source")).lower() in {"mcr", "progress-review", GENERIC_REVIEW_EVENT_TYPE}
    )
    return sorted(shaped, key=lambda event: (event.get("date") or "", event.get("startHour") or 0)), resolved["reviewGenerationIssues"]


def _case_file_next_session(owner_email, profile):
    owner_name = _case_file_owner_name(owner_email, profile)
    learner_scope = {
        "programme": getattr(profile, "programme", None),
        "cohort": getattr(profile, "cohort", None),
        "group": getattr(profile, "group_name", None),
        "programme_id": getattr(profile, "programme_id", None),
        "cohort_id": getattr(profile, "cohort_id", None),
        "group_id": getattr(profile, "group_id", None),
    }
    events = collect_live_session_events(
        owner_email,
        owner_name,
        start_date=timezone.localdate(),
        require_coach_access=False,
        learner_scope=learner_scope,
    )
    valid = [
        {**event, "learnerId": str(profile.id), "enrolmentId": str(profile.enrolment_id) if profile.enrolment_id else None}
        for event in events
        if event.get("status") not in {CoachCalendarEvent.STATUS_CANCELLED, CoachCalendarEvent.STATUS_COMPLETED}
    ]
    valid.sort(key=lambda event: (event.get("date") or "", event.get("startHour") or 0))
    return valid[0] if valid else None


@coach_access_required
@require_GET
def coach_learner_case_file_next_session(request, learner_id):
    owner_email = authenticated_coach_email(request)
    try:
        profile, _source = _case_file_shell_for_request(request, owner_email, learner_id)
        if profile is None:
            return _case_file_learner_not_found()
        event = _case_file_next_session(owner_email, profile)
    except Exception:
        logger.exception("coach_case_file_next_session_failed learner_id=%s", learner_id)
        return coach_error(request, code="database_unavailable", message="Unable to load the learner's next session.", status=503)
    return JsonResponse({"available": True, "event": event, "learnersProcessed": 1})


@coach_access_required
@require_GET
def coach_learner_case_file_reviews(request, learner_id):
    owner_email = authenticated_coach_email(request)
    try:
        profile, _source = _case_file_shell_for_request(request, owner_email, learner_id)
        if profile is None:
            return _case_file_learner_not_found()
        events, issues = _case_file_review_events(owner_email, profile)
    except Exception:
        logger.exception("coach_case_file_reviews_failed learner_id=%s", learner_id)
        return coach_error(request, code="database_unavailable", message="Unable to load learner reviews.", status=503)
    return JsonResponse({"events": events, "reviewGenerationIssues": issues, "learnersProcessed": 1})

@coach_access_required
@require_GET
def coach_monthly_activity(request):
    owner_email = authenticated_coach_email(request)
    try:
        start_date, end_date, month_label, month_key = parse_month_bounds(request.GET.get("month"))
    except ValidationError as exc:
        return validation_error_response(exc)
    refresh_live_snapshots = request_prefers_live_caseload_snapshots(request)

    try:
        rows = fetch_caseload_learner_profiles(owner_email)
        timetable_payload = collect_generated_timetable(
            owner_email,
            # Attendance is loaded from its dedicated projection below, and
            # Monthly Cycle does not render timetable scheduler queues.
            include_live_sessions=False,
            include_scheduler_queues=False,
        )
        events = [
            event
            for event in timetable_payload.get("events", [])
            if monthly_event_is_between(event, start_date, end_date)
        ]
        active_pairs = [
            (row, learner)
            for row in rows
            for learner in [serialize_caseload_learner(row, refresh_live_snapshots=refresh_live_snapshots)]
            if learner.get("enrollmentStatus") == "active"
        ]
        # Curriculum is the attendance source of truth: live-session
        # occurrences and their imported Microsoft Teams attendance reports.
        attendance_rows = fetch_verified_teams_attendance_rows(
            [row.id for row, _learner in active_pairs if getattr(row, "id", None)],
            start_date=start_date,
            end_date=end_date,
        )
        attendance_by_learner_id: dict[int, list[dict]] = defaultdict(list)
        for attendance_row in attendance_rows:
            learner_id = to_int(attendance_row.get("learner_id"))
            if learner_id:
                attendance_by_learner_id[learner_id].append(attendance_row)
        expected_otjh_by_component_id = curriculum_expected_otjh_by_component_id(
            [
                component_id
                for row, _learner in active_pairs
                for component_ids in curriculum_monthly_target_hours_weeks(monthly_target_training_plan(row))
                for component_id in component_ids
            ]
        )
        learners = [
            build_monthly_activity_learner(
                row,
                learner,
                events,
                start_date,
                end_date,
                attendance_rows=attendance_by_learner_id.get(to_int(learner.get("id")), []),
                expected_otjh_by_component_id=expected_otjh_by_component_id,
            )
            for row, learner in active_pairs
        ]
    except Exception:
        logger.exception("coach_monthly_activity_load_failed coach_account_id=%s month=%s", owner_email, month_key)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load monthly activity data.",
            status=503,
        )

    owner_name = coach_staff_display_name(owner_email) or next(
        (clean_text(getattr(row, "coach_name", "")) for row, _learner in active_pairs if clean_text(getattr(row, "coach_name", ""))),
        timetable_payload.get("owner_name") or "Coach",
    )
    on_track = sum(1 for learner in learners if learner["status"] == "on-track")
    need_attention = sum(1 for learner in learners if learner["status"] == "need-attention")
    at_risk = sum(1 for learner in learners if learner["status"] == "at-risk")
    all_ksb_codes = {
        code
        for learner in learners
        for code in learner["ksb"].get("codes", [])
    }

    return JsonResponse(
        {
            "owner": {"name": owner_name, "email": owner_email},
            "month": month_key,
            "monthLabel": month_label,
            "dateRange": {"start": start_date.isoformat(), "end": end_date.isoformat()},
            "summary": {
                "activeLearners": len(learners),
                "timelineItems": sum(len(learner["activities"]) for learner in learners),
                "learningActivities": sum(learner["learning"]["total"] for learner in learners),
                "quizzes": sum(learner["learning"]["quizzes"] for learner in learners),
                "videos": sum(learner["learning"]["videos"] for learner in learners),
                "components": sum(learner["learning"]["components"] for learner in learners),
                "coachingSessions": sum(learner["coaching"]["total"] for learner in learners),
                "bookedSessions": sum(learner["coaching"]["booked"] for learner in learners),
                "needsSchedule": sum(learner["coaching"]["needsSchedule"] for learner in learners),
                "evidence": sum(learner["evidence"]["submitted"] for learner in learners),
                "ksbTouched": len(all_ksb_codes),
                "otjhHours": round(sum(learner["otjh"]["monthlyHours"] for learner in learners), 1),
                "needsAction": sum(1 for learner in learners if learner["needsAction"]),
                "onTrack": on_track,
                "needAttention": need_attention,
                "atRisk": at_risk,
            },
            "learners": learners,
        }
    )


@coach_access_required
@require_GET
def coach_caseload(request):
    endpoint_started = perf_counter()
    owner_email = authenticated_coach_email(request)
    refresh_live_snapshots = request_prefers_live_caseload_snapshots(request)
    request_context = CaseloadRequestContext.from_request(request)
    summary_only = request_context.summary_only
    paginated = request_context.paginated
    validator = ObjectValidator(request.GET)
    page = validator.integer("page", default=1, minimum=1)
    requested_page_size = validator.integer("page_size", default=10, minimum=1)
    search = validator.text("search", max_length=200)
    status = validator.text("status", max_length=100)
    cohort = validator.text("cohort", max_length=255)
    group = validator.text("group", max_length=255)
    sort_key = clean_text(request.GET.get("sort") or "name").casefold()
    direction = clean_text(request.GET.get("direction") or "asc").casefold()
    if sort_key not in {"name", "programme", "cohort", "group", "status"}:
        validator.error("sort", "This calculated field cannot be sorted before learner enrichment.")
    if direction not in {"asc", "desc"}:
        validator.error("direction", "Select asc or desc.")
    try:
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)
    page_size = min(requested_page_size, 100)
    # Caseload enrichment is expensive and the page may request it again on a
    # refresh/remount.  Keep a short per-coach snapshot for read-only GETs;
    # live snapshot requests explicitly bypass this cache.
    caseload_cache_key = coach_caseload_cache_key(
        owner_email,
        summary_only=summary_only,
        query_string=request_context.query_string,
    )
    if not refresh_live_snapshots:
        cached_caseload = get_cached_caseload(caseload_cache_key)
        if cached_caseload is not None:
            return JsonResponse(cached_caseload)
    # A cold page can span several remote databases. React remounts, retries or
    # two tabs must not run that identical work concurrently and exhaust the
    # per-process pools. The winner fills the ordinary response cache; followers
    # wait only for that same scoped coach/page/filter key.
    caseload_lock_key = coach_caseload_lock_key(caseload_cache_key)
    owns_caseload_lock = True
    if paginated and not refresh_live_snapshots:
        owns_caseload_lock = acquire_caseload_lock(caseload_lock_key)
        if not owns_caseload_lock:
            wait_deadline = perf_counter() + 120
            while perf_counter() < wait_deadline:
                _sleep(0.1)
                cached_caseload = get_cached_caseload(caseload_cache_key)
                if cached_caseload is not None:
                    return JsonResponse(cached_caseload)
                if acquire_caseload_lock(caseload_lock_key):
                    owns_caseload_lock = True
                    break

    try:
        pagination = None
        filter_options = None
        if paginated:
            requested_owner = normalize_email(owner_email)
            queryset = LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email"))).filter(
                coach_email_key=requested_owner,
            ).exclude(full_name__regex=r"^\s*$")
            option_queryset = queryset
            if search:
                queryset = queryset.filter(Q(full_name__icontains=search) | Q(email__icontains=search) | Q(programme__icontains=search) | Q(cohort__icontains=search) | Q(group_name__icontains=search))
            if status and status.casefold() != "all":
                queryset = queryset.filter(programme_status__iexact=status)
            if cohort and cohort.casefold() != "all":
                queryset = queryset.filter(Q(cohort_id=cohort) | Q(cohort__iexact=cohort))
            if group and group.casefold() != "all":
                queryset = queryset.filter(Q(group_id=group) | Q(group_name__iexact=group))
            total = queryset.count()
            sort_fields = {"name": "full_name", "programme": "programme", "cohort": "cohort", "group": "group_name", "status": "programme_status"}
            primary_sort = sort_fields[sort_key]
            if direction == "desc":
                primary_sort = f"-{primary_sort}"
            offset = (page - 1) * page_size
            profile_ids = list(queryset.order_by(primary_sort, "full_name", "id").values_list("id", flat=True)[offset:offset + page_size])
            rows = fetch_caseload_learner_profiles_by_ids(owner_email, profile_ids)
            # Two serializer projections used to repeat remote curriculum SQL
            # per learner. They are identical calculations, but their inputs
            # can be collected from this already-selected page: expected OTJH
            # is one component-id query, and authored KSBs are one lookup per
            # distinct programme/assigned-plan shape rather than per learner.
            page_component_ids = [
                clean_text(entry.component_ref)
                for row in rows
                for entry in (
                    row.progress_entries.all()
                    if getattr(row, "progress_entries", None) is not None
                    else []
                )
                if clean_text(entry.component_ref)
            ]
            expected_otjh_by_component = curriculum_expected_otjh_by_component_id(page_component_ids)
            curriculum_ksb_cache: dict[tuple, list[dict]] = {}
            curriculum_ksbs_by_profile: dict[int, list[dict]] = {}
            for row in rows:
                plan = getattr(row, "training_plan", [])
                source = getattr(row, "_caseload_source", None)
                programme = clean_text(getattr(source, "programme", None) or getattr(row, "programme", None))
                module_ids = tuple(
                    clean_text(module.get("moduleId") or module.get("moduleCatalogueId"))
                    for module in plan
                    if isinstance(module, dict)
                    and clean_text(module.get("moduleId") or module.get("moduleCatalogueId"))
                )
                cache_key = (programme.casefold(), module_ids)
                if cache_key not in curriculum_ksb_cache:
                    curriculum_ksb_cache[cache_key] = current_curriculum_ksb_items_for_learner(
                        row, source=source, training_plan=plan,
                    )
                curriculum_ksbs_by_profile[int(row.id)] = curriculum_ksb_cache[cache_key]
            learners = [
                serialize_caseload_learner(
                    row,
                    refresh_live_snapshots=refresh_live_snapshots,
                    expected_otjh_by_component_id=expected_otjh_by_component,
                    curriculum_ksbs=curriculum_ksbs_by_profile[int(row.id)],
                )
                for row in rows
            ]
            total_pages = (total + page_size - 1) // page_size if total else 0
            pagination = {"page": page, "pageSize": page_size, "total": total, "totalPages": total_pages, "hasNext": page < total_pages, "hasPrevious": page > 1}
            filter_options = {
                "cohort": [{"value": key or label, "label": label} for key, label in option_queryset.exclude(cohort="").values_list("cohort_id", "cohort").distinct().order_by("cohort") if label],
                "group": [{"value": key or label, "label": label} for key, label in option_queryset.exclude(group_name="").values_list("group_id", "group_name").distinct().order_by("group_name") if label],
                "programStatus": [{"value": value, "label": value} for value in option_queryset.exclude(programme_status="").values_list("programme_status", flat=True).distinct().order_by("programme_status") if value],
                "employer": [],
            }
        elif summary_only:
            rows = fetch_caseload_dashboard_profiles(owner_email)
            learners = [
                apply_otjh_to_date_metrics(serialize_caseload_dashboard_learner(row))
                for row in rows
            ]
        else:
            rows = fetch_caseload_learner_profiles(owner_email)
            learners = [
                serialize_caseload_learner(row, refresh_live_snapshots=refresh_live_snapshots)
                for row in rows
            ]
        # These enrichments are independent. Running them serially made the
        # first caseload load wait for every audit/metrics/review query in
        # sequence. Bound the pool because canonical metrics can fan out its
        # own read-only workers.
        def run_optional(fn):
            try:
                return fn(rows)
            except Exception:
                logger.warning("coach_caseload_enrichment_failed", exc_info=True)
                return {}
            finally:
                close_old_connections()

        audit_totals = run_optional(caseload_audit_hour_totals)
        ksb_counts = run_optional(caseload_evidenced_ksb_counts)
        if paginated:
            canonical_metrics = run_optional(lambda rows: caseload_canonical_metrics(rows, learner_workspace=True))
        else:
            canonical_metrics = run_optional(caseload_canonical_metrics)
        # Paginated rows use the shared OLD/Aptem vs NEW/Curriculum resolver
        # below. Loading imported Aptem history here as well duplicated review
        # I/O and could not improve the selected source's result.
        review_history = run_optional(dashboard_review_history) if not paginated else {}
        latest_activities = run_optional(caseload_latest_learning_activities) if paginated else {}
        aptem_by_profile = caseload_aptem_ids(rows)
        for row, learner in zip(rows, learners):
            apply_audit_hour_totals(learner, audit_totals.get(int(row.id)))
            apply_evidenced_ksb_count(learner, ksb_counts.get(int(row.id)))
            learner_metrics = canonical_metrics.get(int(row.id))
            apply_canonical_learner_metrics(learner, learner_metrics, learner_workspace=paginated)
            apply_canonical_ksb_evidence(learner, learner_metrics, getattr(getattr(row, "_caseload_source", None), "aptem_id", None))
            apply_aptem_variance_status(learner, aptem_by_profile.get(int(row.id)))
            imported = review_history.get(int(row.id), {})
            last_mcm = _latest_completed_review_date(imported.get("mcm", []))
            last_pr = _latest_completed_review_date([
                review for review in imported.get("reviews", [])
                if imported_review_category(review.get("type")) == "progress-review"
            ])
            if last_mcm:
                learner["lastReview"] = last_mcm
            if imported:
                learner["lastProgressReview"] = last_pr or "--"
            if paginated:
                apply_latest_learning_activity(learner, latest_activities.get(int(row.id)))
        if paginated:
            review_dates = dashboard_latest_completed_review_dates(
                rows, owner_email=owner_email, owner_name="Coach",
            )
            attendance_rows = dashboard_attendance_rows(
                rows, learners, aptem_by_profile=aptem_by_profile,
            )
            attendance_by_id = {
                to_int(entry.get("id")): entry for entry in attendance_rows
                if to_int(entry.get("id")) is not None
            }
            for row, learner in zip(rows, learners):
                dates = review_dates.get(int(row.id), {})
                learner.update(dates)
                if "lastPr" in dates:
                    learner["lastProgressReview"] = dates["lastPr"] or "--"
                if dates.get("lastMcm"):
                    learner["lastReview"] = dates["lastMcm"]
                apply_attendance_summary(learner, attendance_by_id.get(int(row.id)))
        for learner in learners:
            apply_otjh_to_date_metrics(learner)
    except Exception:
        logger.exception("coach_caseload_load_failed coach_account_id=%s", owner_email)
        if paginated and not refresh_live_snapshots and owns_caseload_lock:
            release_caseload_lock(caseload_lock_key)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load coach caseload data.",
            status=503,
        )

    owner_name = coach_staff_display_name(owner_email) or next(
        (clean_text(learner.get("coachName")) for learner in learners if clean_text(learner.get("coachName"))),
        "Coach",
    )
    response_payload = {
            "owner": {"name": owner_name, "email": owner_email},
            "learners": learners,
        }
    if paginated:
        response_payload = {"owner": response_payload["owner"], "results": learners, "pagination": pagination, "filterOptions": filter_options}
    if not refresh_live_snapshots:
        cache_caseload(caseload_cache_key, response_payload, 300 if paginated else 30)
    if paginated and not refresh_live_snapshots and owns_caseload_lock:
        release_caseload_lock(caseload_lock_key)
    _coach_perf("caseload", "total", endpoint_started, learner_count=len(learners))
    return JsonResponse(response_payload)


@coach_access_required
def coach_caseload_coach_rag(request, learner_id):
    if request.method not in ("GET", "PATCH", "PUT"):
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    owner_email = authenticated_coach_email(request)
    learner_queryset = LearnerProfile.objects.annotate(
        coach_email_key=Lower(Trim("coach_email")),
    ).filter(
        id=learner_id,
        coach_email_key=normalize_email(owner_email),
    )

    if request.method == "GET":
        row = learner_queryset.values_list("coach_rag", flat=True).first()
        if row is None and not learner_queryset.exists():
            return JsonResponse({"detail": "Learner not found."}, status=404)
        return JsonResponse({"id": str(learner_id), "coachRag": format_coach_rag_value(row)})

    try:
        payload = parse_json_body(request)
        validator = ObjectValidator(payload)
        raw_rag = validator.text("coachRag", lower=True)
        if raw_rag and raw_rag not in COACH_RAG_LABELS:
            validator.error("coachRag", "Must be one of: green, amber, red.")
        validator.check()
        coach_rag = raw_rag or None
    except ValidationError as exc:
        return validation_error_response(exc)

    try:
        updated = learner_queryset.update(coach_rag=coach_rag) > 0
    except Exception:  # noqa: BLE001
        logger.exception("coach_rag_update_failed coach_account_id=%s learner_id=%s", owner_email, learner_id)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to update coach RAG.",
            status=503,
        )

    if not updated:
        return JsonResponse({"detail": "Learner not found."}, status=404)

    return JsonResponse({"id": str(learner_id), "coachRag": format_coach_rag_value(coach_rag)})


@coach_access_required
@require_GET
def coach_attendance(request):
    endpoint_started = perf_counter()
    owner_email = authenticated_coach_email(request)

    try:
        caseload_rows = fetch_attendance_caseload_rows(owner_email)
        attach_caseload_source_rows(caseload_rows)
        attendance_audit_totals = caseload_audit_hour_totals(caseload_rows)
        attendance_ksb_counts = caseload_evidenced_ksb_counts(caseload_rows)
        attendance_canonical_metrics = caseload_canonical_metrics(caseload_rows)
        caseload_learners = [
            learner
            for learner in [
                apply_canonical_learner_metrics(
                    apply_evidenced_ksb_count(
                        apply_audit_hour_totals(
                            serialize_attendance_source_learner(row),
                            attendance_audit_totals.get(int(row.id)),
                        ),
                        attendance_ksb_counts.get(int(row.id)),
                    ),
                    attendance_canonical_metrics.get(int(row.id)),
                )
                for row in caseload_rows
            ]
            if should_include_in_attendance_page(learner)
        ]
        apply_curriculum_attendance_placements(caseload_learners, caseload_rows)
        active_learners = [
            learner for learner in caseload_learners if should_include_in_attendance_metrics(learner)
        ]
        from learner_api.attendance_lectures import attendance_read_contract
        canonical_by_profile = {}
        canonical_history = []
        attendance_records = []
        for profile in caseload_rows:
            source = getattr(profile, '_caseload_source', None)
            if source is None:
                continue
            contract = attendance_read_contract(source, learner_profile_id=profile.id)
            canonical_by_profile[int(profile.id)] = contract['summary']
            attendance_records.extend(contract['recentAttendance'])
            canonical_history.extend(contract['history'])
        catchup_records = list(
            CoachCalendarEvent.objects.filter(
                owner_email__iexact=owner_email,
                event_type__iexact=CATCH_UP_EVENT_TYPE,
            )
        )
        catchups_by_learner_id: dict[int, int] = {}
        for record in catchup_records:
            if record.status != CoachCalendarEvent.STATUS_COMPLETED:
                continue
            catchups_by_learner_id[record.learner_id] = catchups_by_learner_id.get(record.learner_id, 0) + 1

        attendance_learners = []
        for learner in caseload_learners:
            profile_id = int(learner["id"])
            metrics = canonical_by_profile.get(profile_id)
            attendance_learners.append(
                serialize_attendance_learner(
                    learner,
                    metrics,
                    catchups_by_learner_id.get(profile_id, 0),
                )
            )
    except Exception:
        logger.exception("coach_attendance_load_failed coach_account_id=%s", owner_email)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load coach attendance data.",
            status=503,
        )

    metric_learners = [
        learner
        for learner in attendance_learners
        if learner["includedInAttendanceMetrics"]
    ]
    learners_with_attendance = [learner for learner in metric_learners if learner["hasAttendance"]]
    total_sessions = sum(learner["sessions"] or 0 for learner in learners_with_attendance)
    total_present = sum(learner["present"] or 0 for learner in learners_with_attendance)
    total_absent = sum(learner["absent"] or 0 for learner in learners_with_attendance)
    total_recorded_sessions = total_present + total_absent
    pending_catchups = [
        record
        for record in catchup_records
        if record.status not in {CoachCalendarEvent.STATUS_COMPLETED, CoachCalendarEvent.STATUS_CANCELLED}
    ]
    scheduled_catchups = [
        record
        for record in catchup_records
        if record.status in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS}
    ]
    today = date.today()
    overdue_catchups = [
        record
        for record in pending_catchups
        if (record.scheduled_date or record.target_date) < today
    ]
    stored_catchups = sum(learner["catchup"] or 0 for learner in metric_learners)
    absence_reasons: dict[str, int] = {}
    for learner in metric_learners:
        for reason, count in (learner.get("absenceReasons") or {}).items():
            absence_reasons[reason] = absence_reasons.get(reason, 0) + to_int(count)

    summary = {
        "totalLearners": len(attendance_learners),
        "activeLearners": len(metric_learners),
        "onBreakLearners": sum(1 for learner in attendance_learners if learner["isOnBreak"]),
        "learnersWithAttendance": len(learners_with_attendance),
        "cohortCount": len({learner["cohort"] for learner in attendance_learners if learner["cohort"]}),
        "averageAttendance": percentage(total_present, total_recorded_sessions) if total_recorded_sessions else None,
        "totalSessions": total_sessions,
        "totalPresent": total_present,
        "totalAbsent": total_absent,
        "onTrack": sum(1 for learner in learners_with_attendance if learner["risk"] == "green"),
        "needsAttention": sum(1 for learner in learners_with_attendance if learner["risk"] == "amber"),
        "atRisk": sum(1 for learner in learners_with_attendance if learner["risk"] == "red"),
        "unknown": len(metric_learners) - len(learners_with_attendance),
        "catchupsPending": max(len(pending_catchups), stored_catchups),
        "scheduledCatchups": len(scheduled_catchups),
        "overdueCatchups": len(overdue_catchups),
        "absenceReasons": absence_reasons,
    }

    owner_name = (
        caseload_learners[0]["coachName"]
        if caseload_learners
        else coach_staff_display_name(owner_email) or "Coach"
    )
    active_ids = {str(learner['id']) for learner in active_learners}
    trend_rows = [
        {'learner_id': row['learnerId'], 'session_id': row['sessionId'],
         'session_date': row['sessionDate'], 'attendance_status': row['status']}
        for row in canonical_history if row['counted'] and row['learnerId'] in active_ids
    ]
    active_trends = build_attendance_detail_summary_payload(trend_rows)['trends']
    _coach_perf("attendance", "total", endpoint_started, learner_count=len(caseload_learners))
    return JsonResponse(
        {
            "owner": {"name": owner_name, "email": owner_email},
            "summary": summary,
            "learners": attendance_learners,
            "attendanceRecords": attendance_records,
            "trends": active_trends,
        }
    )


def fetch_absence_report_attendance_rates(learner_ids: list[int], learner_emails: list[str]) -> dict:
    detail_data = fetch_attendance_detail_summary_data(learner_ids, learner_emails)
    by_id: dict[int, int | None] = {}
    by_email: dict[str, int | None] = {}

    for learner_id, metrics in detail_data["metricsById"].items():
        present = to_int(metrics.get("present"))
        absent = to_int(metrics.get("absent"))
        recorded_sessions = present + absent
        by_id[learner_id] = percentage(present, recorded_sessions) if recorded_sessions else None

    for email_key, metrics in detail_data["metrics"].items():
        present = to_int(metrics.get("present"))
        absent = to_int(metrics.get("absent"))
        recorded_sessions = present + absent
        if email_key:
            by_email[email_key] = percentage(present, recorded_sessions) if recorded_sessions else None

    missing_ids = [int(learner_id) for learner_id in learner_ids if learner_id and int(learner_id) not in by_id]
    missing_emails = [
        normalize_email(email)
        for email in learner_emails
        if normalize_email(email) and normalize_email(email) not in by_email
    ]
    connection = connections[router.db_for_read(LearnerAbsence) or "default"]
    legacy_summary_exists = find_existing_relation(connection, ('"Learner"."Absence"',))
    if legacy_summary_exists and (missing_ids or missing_emails):
        query = Q()
        if missing_ids:
            query |= Q(learner_id__in=missing_ids)
        if missing_emails:
            query |= Q(learner_email__in=missing_emails)
        rows = LearnerAbsence.objects.filter(query).values("learner_id", "learner_email", "present", "absent")
        for row in rows:
            present = to_int(row["present"])
            absent = to_int(row["absent"])
            recorded_sessions = present + absent
            attendance_rate = percentage(present, recorded_sessions) if recorded_sessions else None
            learner_id = to_int(row["learner_id"])
            email_key = normalize_email(row["learner_email"])
            if learner_id and learner_id not in by_id:
                by_id[learner_id] = attendance_rate
            if email_key and email_key not in by_email:
                by_email[email_key] = attendance_rate

    return {"by_id": by_id, "by_email": by_email}


def count_previous_absences_from_detail_rows(rows: list[dict], before_date: date | None) -> int:
    seen = set()
    previous_absences = 0
    for row in rows:
        parsed_date = parse_date_value(row.get("session_date"))
        session_date = parsed_date.date() if isinstance(parsed_date, datetime) else parsed_date
        row_key = (
            clean_text(row.get("learner_id")),
            normalize_email(row.get("learner_email")),
            format_iso_date(session_date),
            clean_text(row.get("session_start_time")),
        )
        if row_key in seen:
            continue
        seen.add(row_key)
        if (
            before_date
            and session_date
            and session_date < before_date
            and normalize_attendance_detail_status(row.get("attendance_status")) == "absent"
        ):
            previous_absences += 1
    return previous_absences


def normalize_absence_report_status(value: str | None) -> str:
    status = clean_text(value).lower()
    if status in {
        CoachAbsenceReport.STATUS_PENDING,
        CoachAbsenceReport.STATUS_APPROVED,
        CoachAbsenceReport.STATUS_DECLINED,
    }:
        return status
    return CoachAbsenceReport.STATUS_PENDING


def route_absence_report_evidence(report: CoachAbsenceReport, status: str):
    """Move cloud evidence to the container matching the coach's decision."""
    original_blob_location = parse_blob_url(report.evidence_image_url)
    if not original_blob_location:
        return None
    if not azure_configured():
        raise RuntimeError("Evidence storage is not configured.")
    destination_container = (
        settings.AZURE_APPROVED_CONTAINER
        if status == CoachAbsenceReport.STATUS_APPROVED
        else settings.AZURE_REJECTED_CONTAINER
    )
    source_container, blob_name = original_blob_location
    move_blob(source_container, destination_container, blob_name)
    report.evidence_image_url = blob_url(destination_container, blob_name)
    return source_container, destination_container, blob_name


def serialize_absence_report(
    report: CoachAbsenceReport,
    learner=None,
    attendance_rate_override=None,
    previous_absences_override=None,
) -> dict:
    learner_snapshot = (
        serialize_caseload_learner(learner, refresh_live_snapshots=False)
        if learner
        else {}
    )
    reported_by = clean_text(report.reported_by)
    if reported_by not in {"Learner", "Employer", "Coach"}:
        reported_by = "Learner"
    attendance_rate = attendance_rate_override
    if attendance_rate is None:
        attendance_rate = report.attendance_rate if report.attendance_rate is not None else learner_snapshot.get("attendanceRate") or 0
    status = normalize_absence_report_status(report.status)
    evidence_url = resolve_read_url(
        report.evidence_image_url,
        {
            settings.AZURE_QUARANTINE_CONTAINER,
            settings.AZURE_APPROVED_CONTAINER,
            settings.AZURE_REJECTED_CONTAINER,
        },
    )
    alternative_session = None
    if report.recovery_method == 'alternative':
        from learner_api.alternative_recovery import alternative_target_details
        alternative_session = alternative_target_details(report.catchup_event_key)
    return {
        "id": str(report.id),
        "learnerId": str(report.learner_id),
        "learner": report.learner_name,
        "initials": build_initials(report.learner_name),
        "email": report.learner_email or None,
        "programme": clean_text(getattr(learner, "programme", None)) or "--",
        "cohort": learner_snapshot.get("cohortName") or "--",
        "sessionTitle": report.session_title,
        "sessionDate": report.session_date.isoformat(),
        "sessionTime": report.session_time.strftime("%H:%M") if report.session_time else "--",
        "module": report.session_title,
        "tutor": report.owner_name or "--",
        "reasonCategory": report.reason_category,
        "reason": report.reason,
        "reportedBy": reported_by,
        "reportedDate": report.created_at.date().isoformat(),
        "status": status,
        "evidenceProvided": report.evidence_provided,
        "evidenceKind": report.evidence_kind,
        "evidenceType": "Image" if report.evidence_kind == "image" else "Text" if report.evidence_kind == "text" else None,
        "evidenceText": report.evidence_text or None,
        "recoveryMethod": report.recovery_method,
        "catchupEventKey": report.catchup_event_key,
        "alternativeSession": alternative_session,
        "evidenceImageUrl": evidence_url or None,
        "previousAbsences": previous_absences_override if previous_absences_override is not None else report.previous_absences,
        "attendanceRate": attendance_rate,
        "coachNote": report.coach_note,
        "coachNotes": report.coach_note,
        "decisionDate": report.updated_at.date().isoformat() if status != CoachAbsenceReport.STATUS_PENDING else None,
        "decisionBy": report.owner_name if status != CoachAbsenceReport.STATUS_PENDING else None,
    }


@coach_access_required
def coach_absence_reports(request):
    owner_email = authenticated_coach_email(request)
    active_rows = fetch_owner_active_learner_profiles(owner_email)
    active_ids = {int(row.id) for row in active_rows}
    active_emails = {normalize_email(row.email) for row in active_rows if normalize_email(row.email)}
    active_map = build_learner_profile_map(active_rows)

    if request.method == "PATCH":
        try:
            payload = parse_json_body(request)
            validator = ObjectValidator(payload)
            report_id = validator.integer("id", required=True, minimum=1)
            status = validator.text(
                "status",
                required=True,
                lower=True,
                choices={CoachAbsenceReport.STATUS_APPROVED, CoachAbsenceReport.STATUS_DECLINED},
            )
            coach_note = validator.text("coachNote", max_length=500)
            validator.check()
        except ValidationError as exc:
            return validation_error_response(exc)
        report = CoachAbsenceReport.objects.filter(id=report_id, owner_email__iexact=owner_email).first()
        if not report or (report.learner_id not in active_ids and normalize_email(report.learner_email) not in active_emails):
            return JsonResponse({"detail": "Absence report not found."}, status=404)
        moved_blob = None
        try:
            moved_blob = route_absence_report_evidence(report, status)
        except RuntimeError as exc:
            logger.exception("coach_absence_evidence_route_failed coach_account_id=%s report_id=%s", owner_email, report_id)
            if str(exc) == "Evidence storage is not configured.":
                return coach_error(
                    request,
                    code="storage_unavailable",
                    message="Evidence storage is not configured.",
                    status=503,
                )
            return coach_error(
                request,
                code="storage_unavailable",
                message="Could not move the evidence file in storage.",
                status=502,
            )
        except Exception:
            logger.exception("coach_absence_evidence_route_failed coach_account_id=%s report_id=%s", owner_email, report_id)
            return coach_error(
                request,
                code="storage_unavailable",
                message="Could not move the evidence file in storage.",
                status=502,
            )
        report.status = status
        report.coach_note = coach_note
        update_fields = ["status", "coach_note", "updated_at"]
        if moved_blob:
            update_fields.append("evidence_image_url")
        try:
            report.save(update_fields=update_fields)
        except Exception:
            if moved_blob:
                source_container, current_container, blob_name = moved_blob
                try:
                    move_blob(current_container, source_container, blob_name)
                except Exception:
                    pass
            logger.exception("coach_absence_decision_save_failed coach_account_id=%s report_id=%s", owner_email, report_id)
            return coach_error(
                request,
                code="database_unavailable",
                message="Could not save the absence report decision.",
                status=502,
            )
        recovery_warning = ''
        if status == CoachAbsenceReport.STATUS_APPROVED and report.catchup_event_key:
            try:
                from learner_api.session_recovery import refresh_catchup_attendance
                refresh_catchup_attendance(report.catchup_event_key)
            except Exception:
                recovery_warning = 'Decision saved. Attendance register update needs retry.'
                logger.warning('Catch-up register refresh needs retry for report %s', report.pk)
        attendance_rates = fetch_absence_report_attendance_rates([report.learner_id], [report.learner_email])
        attendance_rate = attendance_rates["by_id"].get(report.learner_id)
        if attendance_rate is None:
            attendance_rate = attendance_rates["by_email"].get(normalize_email(report.learner_email))
        detail_data = fetch_attendance_detail_summary_data([report.learner_id], [report.learner_email])
        detail_rows = detail_data["recordsById"].get(report.learner_id) or detail_data["records"].get(normalize_email(report.learner_email), [])
        previous_absences = count_previous_absences_from_detail_rows(detail_rows, report.session_date)
        return JsonResponse({"item": serialize_absence_report(report, active_map.get(report.learner_id), attendance_rate, previous_absences), "warning": recovery_warning})

    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    records = CoachAbsenceReport.objects.filter(owner_email__iexact=owner_email).order_by("-session_date", "learner_name")
    records = [
        report for report in records
        if report.learner_id in active_ids or normalize_email(report.learner_email) in active_emails
    ]
    attendance_rates = fetch_absence_report_attendance_rates(
        [report.learner_id for report in records],
        [report.learner_email for report in records],
    )
    attendance_detail_data = fetch_attendance_detail_summary_data(
        [report.learner_id for report in records],
        [report.learner_email for report in records],
    )
    items = [
        serialize_absence_report(
            report,
            active_map.get(report.learner_id),
            attendance_rates["by_id"].get(report.learner_id)
            if attendance_rates["by_id"].get(report.learner_id) is not None
            else attendance_rates["by_email"].get(normalize_email(report.learner_email)),
            count_previous_absences_from_detail_rows(
                attendance_detail_data["recordsById"].get(report.learner_id)
                or attendance_detail_data["records"].get(normalize_email(report.learner_email), []),
                report.session_date,
            ),
        )
        for report in records
    ]
    return JsonResponse({
        "owner": {"name": fetch_owner_name(owner_email), "email": owner_email},
        "summary": {
            "total": len(items),
            "pending": sum(1 for item in items if item["status"] == CoachAbsenceReport.STATUS_PENDING),
            "approved": sum(1 for item in items if item["status"] == CoachAbsenceReport.STATUS_APPROVED),
            "declined": sum(1 for item in items if item["status"] == CoachAbsenceReport.STATUS_DECLINED),
        },
        "items": items,
    })


MARKING_QUEUE_COLUMNS = """
    id, learner_kind, learner_id, learner_name, programme_name,
    activity_type, activity_id, activity_title, module_title,
    week_title, planned_otjh, status, learning_reflection,
    ksb_codes, ksb_weights, ksb_explanations, confidence_before, confidence_after,
    application_type, application_text, evidence_files,
    evidence_consent_confirmed, selected_benefits,
    benefit_explanation, actual_time_hours,
    completed_during_paid_hours, date_completed, otjh_confirmed,
    signed_declaration, quality_score, coach_feedback, reviewed_by,
    reviewed_at, submitted_at, full_submission
"""


def serialize_marking_submission(row, *, now=None):
    from learner_api.assignment_attempts import submission_attempts
    now = now or timezone.now()
    submitted_at = row["submitted_at"]
    elapsed_days = max((now - submitted_at).days, 0) if submitted_at else 0
    status = "pending" if row["status"] == "submitted_for_tutor_review" else row["status"]
    learner_name = row["learner_name"] or f"Learner {row['learner_id']}"
    initials = "".join(part[:1].upper() for part in learner_name.split()[:2]) or "L"
    return {
        "id": str(row["id"]),
        "learnerKind": row["learner_kind"],
        "learnerId": row["learner_id"],
        "learner": learner_name,
        "initials": initials,
        "programme": row["programme_name"],
        "activityType": row["activity_type"],
        "activityId": row["activity_id"],
        "activityTitle": row["activity_title"],
        "module": row["module_title"],
        "week": row["week_title"],
        "plannedOtjh": row["planned_otjh"],
        "status": status,
        "learningReflection": row["learning_reflection"],
        "ksbCodes": parse_json_value(row["ksb_codes"], []),
        "ksbWeights": parse_json_value(row["ksb_weights"], {}),
        "ksbExplanations": parse_json_value(row["ksb_explanations"], {}),
        "confidenceBefore": parse_json_value(row["confidence_before"], {}),
        "confidenceAfter": parse_json_value(row["confidence_after"], {}),
        "applicationType": row["application_type"],
        "applicationText": row["application_text"],
        "evidenceFiles": parse_json_value(row["evidence_files"], []),
        "evidenceConsentConfirmed": bool(row["evidence_consent_confirmed"]),
        "selectedBenefits": parse_json_value(row["selected_benefits"], []),
        "benefitExplanation": row["benefit_explanation"],
        "actualTimeHours": row["actual_time_hours"],
        "completedDuringPaidHours": row["completed_during_paid_hours"],
        "dateCompleted": row["date_completed"].isoformat() if row["date_completed"] else None,
        "otjhConfirmed": bool(row["otjh_confirmed"]),
        "signedDeclaration": bool(row["signed_declaration"]),
        "qualityScore": row["quality_score"],
        "coachFeedback": row["coach_feedback"],
        "reviewedBy": row["reviewed_by"],
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "submittedAt": submitted_at.isoformat() if submitted_at else None,
        "submittedDisplay": submitted_at.strftime("%d/%m/%Y %H:%M") if submitted_at else "--",
        "elapsedDays": elapsed_days,
        "isOverdue": status == "pending" and elapsed_days >= MARKING_OVERDUE_DAYS,
        "submissionAttempts": submission_attempts(row.get("full_submission"), row["status"], submitted_at, row["coach_feedback"], row["reviewed_by"], row["reviewed_at"]) if row["activity_type"] in ("assignment", "extra_activity") else [],
    }


def empty_marking_queue_response(owner_email, *, page=1, page_size=25):
    return JsonResponse({
        "owner": {"name": fetch_owner_name(owner_email), "email": owner_email},
        "summary": {
            "totalItems": 0,
            "activeLearners": 0,
            "pendingItems": 0,
            "acceptedItems": 0,
            "referredItems": 0,
            "overdueItems": 0,
            "assignmentItems": 0,
            "reflectionItems": 0,
            "oldestSubmission": "--",
            "overdueThresholdDays": MARKING_OVERDUE_DAYS,
        },
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": 0,
            "totalPages": 0,
            "hasNext": False,
            "hasPrevious": page > 1,
        },
        "items": [],
    })


@coach_access_required
@attributed_write_view
def coach_marking_queue(request, submission_id=None):
    """List and review complete reflections, scoped and paged in PostgreSQL."""
    owner_email = authenticated_coach_email(request)
    requested_owner = normalize_email(owner_email)
    # learning_reflection_submissions.learner_id is populated from
    # enrolment."Created_users".id (see LearnerProfile.enrolment_id), not from
    # LearnerProfile.id -- the two are disjoint pk spaces, so this must filter
    # on enrolment_id or it never matches a real submission.
    allowed_learner_ids = [
        str(learner_id)
        for learner_id in (
            LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
            .filter(coach_email_key=requested_owner)
            .values_list("enrolment_id", flat=True)
        )
        if learner_id is not None
    ]

    if submission_id is not None:
        if request.method == "GET":
            if not allowed_learner_ids:
                return JsonResponse({"detail": "Submission not found."}, status=404)
            try:
                with connections["enrolment"].cursor() as cur:
                    cur.execute(
                        f"""
                        select {MARKING_QUEUE_COLUMNS}
                          from "Learner".learning_reflection_submissions
                         where id = %s and learner_id = any(%s)
                           and {MARKING_QUEUE_EXCLUDE_DRAFTS_SQL}
                        """,
                        [str(submission_id), allowed_learner_ids],
                    )
                    columns = [column[0] for column in cur.description]
                    value = cur.fetchone()
            except DatabaseError:
                logger.exception("Could not load Coach marking submission.")
                return coach_error(
                    request,
                    code="marking_queue_unavailable",
                    message="Could not load the marking submission.",
                    status=502,
                )
            if not value:
                return JsonResponse({"detail": "Submission not found."}, status=404)
            return JsonResponse({"item": serialize_marking_submission(dict(zip(columns, value)))})

        if request.method not in {"PATCH", "POST"}:
            return JsonResponse({"detail": "Method not allowed."}, status=405)
        try:
            payload = parse_json_body(request)
            validator = ObjectValidator(payload)
            decision = validator.text(
                "decision",
                required=True,
                lower=True,
                choices={"accepted", "partial", "referred", "escalated", "rejected"},
            )
            # 4000 was sized for a hand-typed note. An AI-assisted draft is
            # written to the marking policy's own brief -- 1000 to 1200 words,
            # so 6000-7500 characters -- and was rejected with a bare 400 the
            # moment a coach tried to save one. The column itself is unbounded
            # text; this ceiling only exists to stop something absurd being
            # posted, so it is raised to clear a full draft with room to edit.
            feedback = validator.text("feedback", max_length=MARKING_FEEDBACK_MAX_LENGTH)
            reviewed_by = validator.text("reviewedBy", max_length=255, default="Progress Coach") or "Progress Coach"
            # A super-admin marking from inside a coach's workspace is recorded
            # as themselves, not as the coach: "Demo Admin (for Test Coach)".
            # The client sends the coach's name because that is whose workspace
            # is open, so the correction has to happen here, where the real
            # actor is known. Without it the audit trail would show a coach
            # accepting work they never saw.
            if is_coach_view_as(request):
                admin = getattr(request, "coach_view_as_admin", None)
                admin_name = clean_text(
                    getattr(admin, "username", "") or getattr(admin, "email", "")
                ) or "Administrator"
                reviewed_by = f"{admin_name} (for {reviewed_by})"[:255]
            if decision and decision != "accepted" and not feedback:
                validator.error("feedback", "Feedback is required for this decision.")
            validator.check()
        except ValidationError as exc:
            return validation_error_response(exc)

        try:
            with connections["enrolment"].cursor() as cur:
                cur.execute(
                    """
                    update "Learner"."learning_reflection_submissions"
                    set status = %s, coach_feedback = %s, reviewed_by = %s, reviewed_at = %s
                    where id = %s and learner_id = any(%s)
                      and """ + MARKING_QUEUE_EXCLUDE_DRAFTS_SQL + """
                    returning """ + SUBMISSION_AUDIT_SQL + """
                    """,
                    [decision, feedback, reviewed_by, timezone.now(), str(submission_id), allowed_learner_ids],
                )
                updated = cur.fetchone()
        except DatabaseError:
            logger.exception("Could not update Coach marking submission.")
            return coach_error(
                request,
                code="marking_queue_unavailable",
                message="Could not update the marking submission.",
                status=502,
            )
        if not updated:
            return JsonResponse({"detail": "Submission not found."}, status=404)

        # Recorded here rather than by a signal: this is a raw UPDATE on the
        # `enrolment` connection, so no `post_save` fires and the Audit Trail
        # would otherwise show a coach's marking decisions as never having
        # happened. Best-effort by construction -- the helper swallows its own
        # failures -- because a decision that was saved must not be rolled back
        # by a history write that was not.
        record_submission_row(updated)
        row = dict(zip(SUBMISSION_AUDIT_COLUMNS, updated))

        # The learner's accepted/rejected counts are recomputed from the
        # submissions, so they always agree with what a coach has actually
        # decided -- including after a decision is changed. Best-effort: a
        # counter that could not be written must not fail a saved decision.
        from learner_api.marking_tally import refresh_tally_for_submission

        refresh_tally_for_submission(row["learner_kind"], row["learner_id"])

        return JsonResponse({
            "id": str(row["id"]),
            "status": row["status"],
            "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        })

    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    query_validator = ObjectValidator(request.GET)
    page = query_validator.integer("page", default=1, minimum=1)
    requested_page_size = query_validator.integer("page_size", default=25, minimum=1)
    status_filter = clean_text(request.GET.get("status")).lower()
    # Which kind of marking the coach is working through. An assignment is
    # assessed as a work product; everything else is validated through the
    # learner's reflection. Applied to the summary as well as the rows, so the
    # status counts describe the kind on screen rather than the whole queue.
    kind_filter = clean_text(request.GET.get("kind")).lower()
    status_groups = {
        "pending": ["submitted_for_tutor_review", "escalated"],
        "overdue": ["submitted_for_tutor_review"],
        "accepted": ["accepted", "partial"],
        "referred": ["referred", "rejected"],
        "all": [],
    }
    valid_database_statuses = {
        "submitted_for_tutor_review", "accepted", "partial", "referred",
        "escalated", "rejected",
    }
    if status_filter and status_filter not in status_groups and status_filter not in valid_database_statuses:
        query_validator.error("status", "Select a valid marking status.")
    if kind_filter and kind_filter not in MARKING_KINDS:
        query_validator.error("kind", "Select a valid marking kind.")
    date_from = query_validator.iso_date("date_from")
    date_to = query_validator.iso_date("date_to")
    if date_from and date_to and date_from > date_to:
        query_validator.error("date_to", "Must be on or after date_from.")
    learner_filter = query_validator.text("learner", max_length=128)
    search_filter = query_validator.text("search", max_length=200)
    try:
        query_validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)
    page_size = min(requested_page_size, 100)
    if not allowed_learner_ids:
        return empty_marking_queue_response(owner_email, page=page, page_size=page_size)

    base_clauses = ["learner_id = any(%s)", MARKING_QUEUE_EXCLUDE_DRAFTS_SQL]
    base_params = [allowed_learner_ids]
    if learner_filter:
        base_clauses.append("learner_id = %s")
        base_params.append(learner_filter)
    if date_from:
        base_clauses.append("submitted_at >= %s")
        base_params.append(date_from)
    if date_to:
        base_clauses.append("submitted_at < %s")
        base_params.append(date_to + timedelta(days=1))
    if search_filter:
        base_clauses.append(
            "(learner_name ilike %s or activity_title ilike %s or module_title ilike %s or week_title ilike %s)"
        )
        search_pattern = f"%{search_filter}%"
        base_params.extend([search_pattern] * 4)

    # Snapshot before narrowing: the kind tabs each show their own total, so
    # they cannot be counted under a WHERE that has already picked one kind.
    kind_scope_clauses = list(base_clauses)
    kind_scope_params = list(base_params)

    if kind_filter == "assignment":
        base_clauses.append("lower(btrim(activity_type)) = any(%s)")
        base_params.append(list(ASSIGNMENT_ACTIVITY_TYPES))
    elif kind_filter == "reflection":
        base_clauses.append("lower(btrim(activity_type)) <> all(%s)")
        base_params.append(list(ASSIGNMENT_ACTIVITY_TYPES))

    item_clauses = list(base_clauses)
    item_params = list(base_params)
    if status_filter and status_filter != "all":
        statuses = status_groups.get(status_filter, [status_filter])
        item_clauses.append("status = any(%s)")
        item_params.append(statuses)
    overdue_before = timezone.now() - timedelta(days=MARKING_OVERDUE_DAYS)
    if status_filter == "overdue":
        item_clauses.append("submitted_at <= %s")
        item_params.append(overdue_before)

    base_where = " and ".join(base_clauses)
    item_where = " and ".join(item_clauses)
    offset = (page - 1) * page_size
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                f"""
                select count(*) as total_items,
                       count(distinct (learner_kind, learner_id)) as active_learners,
                       count(*) filter (where status in ('submitted_for_tutor_review', 'escalated')) as pending_items,
                       count(*) filter (where status in ('accepted', 'partial')) as accepted_items,
                       count(*) filter (where status in ('referred', 'rejected')) as referred_items,
                       count(*) filter (
                           where status = 'submitted_for_tutor_review' and submitted_at <= %s
                       ) as overdue_items,
                       min(submitted_at) filter (
                           where status in ('submitted_for_tutor_review', 'escalated')
                       ) as oldest_submission
                  from "Learner".learning_reflection_submissions
                 where {base_where}
                """,
                [overdue_before, *base_params],
            )
            summary_row = cur.fetchone()
            # Both kinds, over everything this coach can see -- so switching
            # tabs never changes the number on the tab you are switching from.
            cur.execute(
                f"""
                select count(*) filter (
                           where lower(btrim(activity_type)) = any(%s)
                       ) as assignment_items,
                       count(*) filter (
                           where lower(btrim(activity_type)) <> all(%s)
                       ) as reflection_items
                  from "Learner".learning_reflection_submissions
                 where {" and ".join(kind_scope_clauses)}
                """,
                [list(ASSIGNMENT_ACTIVITY_TYPES), list(ASSIGNMENT_ACTIVITY_TYPES), *kind_scope_params],
            )
            kind_row = cur.fetchone()
            cur.execute(
                f'select count(*) from "Learner".learning_reflection_submissions where {item_where}',
                item_params,
            )
            filtered_total = cur.fetchone()[0]
            cur.execute(
                f"""
                select {MARKING_QUEUE_COLUMNS}
                  from "Learner".learning_reflection_submissions
                 where {item_where}
                 order by case when status = 'submitted_for_tutor_review' then 0 else 1 end,
                          submitted_at asc, id asc
                 limit %s offset %s
                """,
                [*item_params, page_size, offset],
            )
            columns = [column[0] for column in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    except DatabaseError:
        logger.exception("Could not load Coach marking queue.")
        return coach_error(
            request,
            code="marking_queue_unavailable",
            message="Could not load the marking queue.",
            status=502,
        )
    now = timezone.now()
    items = [serialize_marking_submission(row, now=now) for row in rows]
    total_items, active_learners, pending_items, accepted_items, referred_items, overdue_items, oldest = summary_row
    total_pages = (filtered_total + page_size - 1) // page_size if filtered_total else 0
    return JsonResponse({
        "owner": {"name": fetch_owner_name(owner_email), "email": owner_email},
        "summary": {
            "totalItems": total_items,
            "activeLearners": active_learners,
            "pendingItems": pending_items,
            "acceptedItems": accepted_items,
            "referredItems": referred_items,
            "overdueItems": overdue_items,
            # Both kinds, always -- see the kind_row query above.
            "assignmentItems": kind_row[0] if kind_row else 0,
            "reflectionItems": kind_row[1] if kind_row else 0,
            "oldestSubmission": oldest.strftime("%d/%m/%Y %H:%M") if oldest else "--",
            "overdueThresholdDays": MARKING_OVERDUE_DAYS,
        },
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "totalItems": filtered_total,
            "totalPages": total_pages,
            "hasNext": page < total_pages,
            "hasPrevious": page > 1,
        },
        "items": items,
    })


@coach_access_required
@require_GET
def coach_evidence_awaiting_review(request):
    owner_email = authenticated_coach_email(request)

    try:
        items, caseload_learners = fetch_evidence_file_queue(owner_email)
    except Exception:
        logger.exception("coach_evidence_queue_load_failed coach_account_id=%s", owner_email)
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load coach evidence awaiting review data.",
            status=503,
        )

    return JsonResponse(
        {
            "owner": {"email": owner_email},
            "summary": {
                "caseloadLearners": len(caseload_learners),
                "queueLearners": len(items),
                "pendingItems": sum(item["pendingEvidence"] for item in items),
                "totalEvidence": sum(item["totalEvidence"] for item in items),
            },
            "items": items,
        }
    )


# --------------------------------------------------------------------------
# Curriculum-driven Review instances -- opening a scheduled MCM/Progress
# Review, saving its answers, signing it and completing it. The instance's
# question set/signature rules were resolved by Curriculum
# (curriculum_api.review_instances); this only enforces that a coach may
# reach an instance they are themselves the assigned coach for, exactly the
# same ownership boundary every other coach/timetable endpoint enforces.

@coach_access_required
def coach_aptem_review_reconciliation_preview(request, learner_id):
    """Inspect one owned Aptem learner without creating an instance or booking."""
    from .aptem_review_reconciliation import build_aptem_review_reconciliation_preview

    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request)
    learner = next(
        (row for row in fetch_owner_active_learner_profiles(owner_email) if row.id == learner_id),
        None,
    )
    if learner is None:
        return JsonResponse({"detail": "Learner not found in your caseload."}, status=404)
    aptem_ids, conflicts = resolve_effective_aptem_ids([learner])
    if learner_id in conflicts:
        return JsonResponse({"detail": "Aptem learner identity is conflicting."}, status=409)
    if learner_id not in aptem_ids:
        return JsonResponse({"detail": "This learner has no Aptem review identity."}, status=404)
    learner.effective_aptem_id = aptem_ids[learner_id]
    try:
        preview = build_aptem_review_reconciliation_preview(learner, owner_email)
    except (DatabaseError, RuntimeError):
        logger.exception("Aptem review reconciliation preview unavailable for learner %s", learner_id)
        return JsonResponse({"detail": "Review reconciliation preview is unavailable."}, status=503)
    return JsonResponse(preview)


@coach_access_required
def coach_review_learner_addition_templates(request):
    """Enabled Review templates a coach may add a learner-specific Review
    from, scoped to ONE learner's own programme.

    GET /coach/reviews/learner-additions/templates?learnerId=<id>

    Read-only, so the super-admin view-as default (safe for GET) already
    covers it -- no coach_view_as_safe marker needed. Never returns a
    disabled template or one from a different programme: the coach's
    eventual POST is independently re-validated against this exact same
    resolution, never trusting whatever the frontend last rendered.
    """
    from curriculum_api import review_types as curriculum_review_types

    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request)
    learner_id = parse_int(request.GET.get("learnerId"), 0)
    if not learner_id:
        return JsonResponse({"detail": "learnerId is required."}, status=400)

    # Same caseload resolution every other coach endpoint uses (see
    # collect_generated_timetable) -- a learner not in this coach's active
    # caseload is refused exactly like any other coach route would refuse it.
    learner = next(
        (row for row in fetch_owner_active_learner_profiles(owner_email) if row.id == learner_id), None,
    )
    if learner is None:
        return JsonResponse({"detail": "Learner not found in your caseload."}, status=404)

    policy = native_review_creation_policy(learner)
    if not policy["canAddReview"]:
        return JsonResponse({"learnerId": learner_id, "programmeId": None, "templates": [], **policy})

    programme_id = resolve_curriculum_programme_id(learner.programme)
    if not programme_id:
        return JsonResponse({"learnerId": learner_id, "programmeId": None, "templates": [], **policy})

    type_index = curriculum_review_types.review_type_index()
    templates = []
    for template_row in curriculum_review_instances.list_enabled_review_templates(programme_id):
        type_row = type_index.get(clean_text(template_row.get("review_type_id")))
        templates.append({
            "id": template_row.get("id"),
            "name": clean_text(template_row.get("name")),
            "reviewTypeId": clean_text(template_row.get("review_type_id")) or None,
            "reviewTypeCode": clean_text((type_row or {}).get("code")) or None,
            "reviewTypeName": clean_text((type_row or {}).get("name")) or None,
        })
    return JsonResponse({"learnerId": learner_id, "programmeId": programme_id, "templates": templates, **policy})


@coach_access_required
def coach_review_learner_additions_create(request):
    """Create a learner-specific additional Review -- the ONLY way a
    coach-initiated single-learner Review occurrence is created (see
    curriculum_api.review_instances.create_learner_review_addition). Never
    creates a standalone CoachCalendarEvent, never leaves
    review_template_id/review_instance_id blank, never creates a Teams
    meeting -- this endpoint only produces a not-scheduled canonical
    occurrence. Scheduling it (and the Teams meeting) is a separate,
    existing action: POST the returned eventKey to
    coach/timetable/events/schedule, exactly as any other generated Review
    occurrence is scheduled.

    POST /coach/reviews/learner-additions
    {
      "learnerId": 669, "reviewTemplateId": "REV-123",
      "targetDate": "2026-10-30",
      "reasonCode": "additional-coaching", "reason": "..."
    }

    A repeated identical request (same learner + template + targetDate)
    returns the SAME addition/occurrence -- see
    create_learner_review_addition's own idempotent get-or-create.
    """
    from curriculum_api import review_types as curriculum_review_types

    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    # Super-admin view-as is read-only by default (see coach_access_required)
    # -- deliberately NOT overridden here. Creating a Review on a coach's
    # behalf is a write with the same audit-trail concern every other
    # coach_view_as-gated write already has.
    try:
        payload = parse_json_body(request)
        owner_email = authenticated_coach_email(request)
        validator = ObjectValidator(payload)
        learner_id = validator.integer("learnerId", required=True, minimum=1)
        review_template_id = validator.text("reviewTemplateId", required=True, max_length=128)
        target_date = validator.iso_date("targetDate", required=True)
        reason_code = validator.text("reasonCode", required=False, max_length=64, default="")
        reason = validator.text("reason", required=False, max_length=1000, default="")
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    if reason_code and reason_code not in curriculum_review_instances.LEARNER_REVIEW_ADDITION_REASON_CODES:
        return JsonResponse({"detail": "Unrecognised reasonCode."}, status=400)

    learner = next(
        (row for row in fetch_owner_active_learner_profiles(owner_email) if row.id == learner_id), None,
    )
    if learner is None:
        return JsonResponse({"detail": "Learner not found in your caseload."}, status=404)

    policy = native_review_creation_policy(learner)
    if not policy["canAddReview"]:
        return JsonResponse(policy, status=409)

    programme_id = resolve_curriculum_programme_id(learner.programme)
    if not programme_id:
        return JsonResponse({"detail": "This learner has no Curriculum programme mapping."}, status=422)

    # The backend independently re-resolves the template from the learner's
    # OWN programme -- a coach can never submit a review_template_id that
    # belongs to a different programme, regardless of what the frontend sent.
    template_row = None
    for candidate in curriculum_review_instances.list_enabled_review_templates(programme_id):
        if candidate.get("id") == review_template_id:
            template_row = candidate
            break
    if template_row is None:
        return JsonResponse(
            {"detail": "That Review template is not enabled for this learner's programme."}, status=404,
        )

    try:
        addition = curriculum_review_instances.create_learner_review_addition(
            review_template_id=review_template_id,
            programme_id=programme_id,
            learner_id=learner_id,
            target_date=target_date,
            reason_code=reason_code,
            reason=reason,
            actor=owner_email or "coach",
        )
        occurrence_ref = f"{curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL}:{addition['id']}"
        instance = curriculum_review_instances.ensure_review_instance(
            template_row,
            learner_id=learner_id,
            learner_kind="",
            programme_id=programme_id,
            occurrence_number=None,
            target_date=target_date,
            coach_email=owner_email,
            actor=owner_email or "coach",
            occurrence_source=curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL,
            occurrence_ref=occurrence_ref,
        )
    except ReviewCreationBlocked as exc:
        return JsonResponse(exc.policy, status=409)
    event_key = curriculum_review_instances.review_calendar_event_key_manual(
        learner_id, review_template_id, addition["id"],
    )
    type_row = curriculum_review_types.review_type_index().get(clean_text(template_row.get("review_type_id")))
    return JsonResponse({
        "additionId": addition["id"],
        "eventKey": event_key,
        "reviewInstanceId": instance.get("id"),
        "reviewTemplateId": review_template_id,
        "reviewName": clean_text(template_row.get("name")),
        "reviewTypeCode": clean_text((type_row or {}).get("code")) or None,
        "occurrenceRef": occurrence_ref,
        "targetDate": target_date.isoformat(),
        "status": instance.get("status"),
    }, status=201)


def _authorized_review_instance(request, instance_id):
    owner_email = authenticated_coach_email(request)
    instance_row = curriculum_review_instances.get_review_instance(clean_text(instance_id))
    if not instance_row:
        return None, JsonResponse({"detail": "Review not found."}, status=404)
    if clean_email(instance_row.get("coach_email")) != clean_email(owner_email):
        return None, JsonResponse({"detail": "Review not found."}, status=404)
    from .reconciliation_links import for_native
    link = for_native(instance_id)
    if link and request.method != "GET":
        return None, JsonResponse({"detail": "Use the reconciled imported Review form.",
                                   "code": "REVIEW_RECONCILED", "instanceId": link.overlay.event_key}, status=409)
    return instance_row, None


def _review_instance_calendar_record(instance_row):
    """The exact calendar event linked in both directions to this instance."""
    calendar_event_id = instance_row.get("calendar_event_id")
    if not calendar_event_id:
        return None
    return CoachCalendarEvent.objects.filter(
        pk=calendar_event_id,
        review_instance_id=instance_row.get("id"),
        owner_email__iexact=instance_row.get("coach_email") or "",
    ).first()


def _review_instance_meeting_summary_context(instance_row) -> MeetingSummaryContext:
    """Recap prompt metadata for a Review with no linked Teams meeting.

    The linked calendar row normally carries the learner and coach names. A
    meeting held outside Teams has no such row, so the same three facts come
    from the instance itself. Read-only, and it never stands in for the
    calendar row anywhere else -- only the prompt's labels are involved.
    """
    learner = LearnerProfile.objects.filter(pk=instance_row.get("learner_id")).first()
    coach_email = clean_text(instance_row.get("coach_email"))
    return MeetingSummaryContext(
        review_template_id=clean_text(instance_row.get("review_template_id")) or None,
        learner_name=(
            clean_text(getattr(learner, "full_name", ""))
            or clean_text(getattr(learner, "username", ""))
        ),
        owner_name=fetch_owner_name(coach_email, fallback="") if coach_email else "",
    )


def _review_instance_meeting_summary_source(instance_row, definition=None):
    """Stored-only coach suggestion for one mapped MCM/Progress Review field.

    This helper never contacts Graph or OpenAI. The formal answer remains on
    the field inside ``definition['sections']`` and always wins in the client.
    """
    definition = definition or curriculum_review_instances.review_instance_form_definition(instance_row)
    if definition.get("template", {}).get("reviewTypeCode") not in REVIEW_MEETING_SUMMARY_REVIEW_TYPES:
        return None
    field = curriculum_review_instances.meeting_summary_field(definition)
    if not field:
        return None
    source = {
        "fieldId": field.get("id"),
        "status": "unavailable",
        "summaryText": "",
        "generatedAt": None,
        "editedAt": None,
        "message": "No stored AI Meeting Summary is available yet.",
    }
    record = _review_instance_calendar_record(instance_row)
    if not record:
        return source
    stored = stored_coach_meeting_summary(record)
    if not stored:
        return source
    status = clean_text(stored.get("status")) or "ready"
    source.update({
        "status": status,
        "generatedAt": stored.get("generatedAt"),
        "editedAt": stored.get("editedAt"),
        "message": (
            "Meeting Summary generation failed. The formal Review answer was not changed."
            if status == "failed"
            else ""
        ),
    })
    if status in {"ready", "edited"}:
        source["summaryText"] = meeting_summary_plain_text(stored.get("summary") or {})
    return source


def _review_learner_identity(learner):
    """Display identity from the linked learner profile, never route labels."""
    return {
        "learnerName": clean_text(getattr(learner, "full_name", None)) or clean_text(getattr(learner, "username", None)),
        "learnerEmail": clean_text(getattr(learner, "email", None)),
        "programme": clean_text(getattr(learner, "programme", None)),
        "programmeId": clean_text(getattr(learner, "programme_id", None)),
    }


def _coach_review_instance_definition(instance_row):
    definition = curriculum_review_instances.review_instance_form_definition(instance_row)
    learner = LearnerProfile.objects.filter(pk=instance_row.get("learner_id")).only(
        "full_name", "email", "programme", "programme_id",
    ).first()
    definition.update(_review_learner_identity(learner))
    source = _review_instance_meeting_summary_source(instance_row, definition)
    if source is not None:
        definition["meetingSummarySource"] = source
    return definition


def _imported_review_field(field, *, section_id, index, historical=False):
    label = clean_text(field.get("label")) or f"Imported field {index + 1}"
    configuration = {"imported": True}
    description = clean_text(field.get("description"))
    if description:
        configuration["description"] = description
    if historical and field.get("preserveEmpty"):
        configuration["preserveEmpty"] = True
    historical_id = field.get("sourceFieldKey") if historical and field.get("historicalSupplement") else None
    field_index = index
    if historical:
        field_index = f"source:{historical_id}" if historical_id else field.get("historicalIndex", index)
    return {
        "id": f"aptem-field:{section_id}:{field_index}",
        "title": label,
        "fieldType": "title_description" if historical_id and field.get("fieldType") == "title_description" else "text_multiline",
        "required": False,
        "displayOrder": index,
        "configuration": configuration,
        "parentFieldId": None,
        "conditionValue": None,
        "answer": field.get("displayValue", field.get("value")) if historical else field.get("value"),
        "answeredBy": None,
        "answeredAt": None,
        "yesFields": [],
        "noFields": [],
    }


# Intended step order of an imported Aptem Monthly Coaching Meeting, matched
# by title prefix (casefolded). Aptem's exported ``section_order`` puts
# "Previous Meeting Summary" last and, for some records, runs the whole form
# backwards; the LMS MCM form (pages/shared/monthlyCoachingForm.ts and the
# seeded Curriculum template) opens with the previous meeting's summary and
# then walks the agenda. Sections not listed keep their Aptem relative order
# after the known ones.
_IMPORTED_MCM_SECTION_ORDER = (
    "learner information",
    "previous meeting summary",
    "opening the meeting",
    "learner presentation",
    "reflection on knowledge",
    "preparing for next month",
    "learning resources",
    "wellbeing",
    "learner feedback",
    "additional comments",
    "confirm next meeting",
    "meeting details",
    "meeting summary",
)


def _imported_mcm_section_rank(title: str) -> int | None:
    normalized = " ".join(clean_text(title).casefold().split())
    for rank, prefix in enumerate(_IMPORTED_MCM_SECTION_ORDER):
        if normalized.startswith(prefix):
            return rank
    return None


def _imported_review_has_usable_form(review: dict, *, has_normalized_sections: bool = False) -> bool:
    """Return whether an import contains a real form rather than metadata only."""
    if has_normalized_sections:
        return True
    return any(
        bool(section.get("fields") or section.get("tables") or clean_text(section.get("rawText")))
        for section in review.get("sections") or []
        if isinstance(section, dict)
    )


def _imported_progress_metric(*, actual, expected, planned, actual_percent, expected_percent):
    variance = (
        round(actual_percent - expected_percent, 2)
        if actual_percent is not None and expected_percent is not None
        else None
    )
    return {
        "actual": actual,
        "expected": expected,
        "planned": planned,
        "actualPercent": actual_percent,
        "expectedPercent": expected_percent,
        "variancePercent": variance,
        "varianceDirection": "above" if variance is not None and variance >= 0 else "below" if variance is not None else "",
    }


def _imported_progress_date(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\s*:?\s*(\d{{1,2}}\s+[A-Za-z]{{3,9}}\s+\d{{4}})", text, re.IGNORECASE)
    if not match:
        return None
    for date_format in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(match.group(1), date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _imported_progress_snapshot_from_review(review: dict) -> dict | None:
    """Translate Aptem's historical Learning Progress sentence into the
    canonical visual snapshot contract without replacing it with live data.
    """
    progress_sections = [
        section for section in review.get("sections") or []
        if isinstance(section, dict) and clean_text(section.get("name")).casefold() == "learning progress"
    ]
    text = " ".join(clean_text(section.get("rawText")) for section in progress_sections).strip()
    # Aptem exports typographic dashes and non-breaking spaces in otherwise
    # identical labels (for example ``Off–The–Job``).  Normalise presentation
    # characters before matching; the original imported answer remains
    # untouched in the review form.
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()
    activities = re.search(
        r"Learning Plan Activities\s+(\d+)\s+of\s+(\d+)\s+Completed",
        text,
        re.IGNORECASE,
    )
    hours = re.search(
        r"Off[-\s]*The[-\s]*Job Hours Overall Progress\s+(\d+(?:\.\d+)?)%\s*\((\d+(?:\.\d+)?)h\).*?Planned Hours \(ILR\)\s+(\d+(?:\.\d+)?)h",
        text,
        re.IGNORECASE,
    )
    if not activities or not hours:
        return None

    completed_activities = int(activities.group(1))
    planned_activities = int(activities.group(2))
    target_values = re.search(
        r"Submitted Remaining Target\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    target_activities = int(target_values.group(4)) if target_values else None
    programme_actual_percent = round(completed_activities / planned_activities * 100, 2) if planned_activities else None
    programme_expected_percent = (
        round(target_activities / planned_activities * 100, 2)
        if planned_activities and target_activities is not None
        else None
    )
    completed_hours = float(hours.group(2))
    planned_hours = float(hours.group(3))
    hours_actual_percent = float(hours.group(1))
    calculated_from = _imported_progress_date(text, "Timeline Start")
    calculated_at = review.get("completedDate") or review.get("plannedDate")
    return {
        "calculationMethod": "aptem_imported_text",
        "schemaVersion": 1,
        "formulaVersion": "aptem_imported_text_v1",
        "calculatedFrom": calculated_from or calculated_at or "",
        "calculatedAt": calculated_at or calculated_from or "",
        "calculatedBy": "Aptem import",
        "weeksElapsed": None,
        "programmeProgress": _imported_progress_metric(
            actual=completed_activities,
            expected=target_activities,
            planned=planned_activities,
            actual_percent=programme_actual_percent,
            expected_percent=programme_expected_percent,
        ),
        "offTheJobHours": _imported_progress_metric(
            actual=completed_hours,
            expected=None,
            planned=planned_hours,
            actual_percent=hours_actual_percent,
            expected_percent=None,
        ),
        "ksbProgress": {
            "available": False,
            "title": "Apprenticeship Standard progress",
            "reason": "KSB progress was not available in this Aptem import.",
            "actualPercent": None,
            "expectedPercent": None,
        },
    }


def _imported_review_progress_snapshot(learner, review: dict, *, calculated_by: str):
    """Historical Aptem text only; opening any review must never calculate live."""
    return _imported_progress_snapshot_from_review(review)


def _imported_review_definition(owner_email: str, event_key: str, *, preview_only=False, pdf_only=False, learners=None) -> dict | None:
    """Adapt one owned Aptem review to the native form-definition contract.

    Summary-only imports remain read-only. An admin view-as may preview an
    approved template without creating an overlay. Imports with a real form
    can store LMS-local answers without changing the original Aptem data.
    ``event_key`` is the same stable identity used by the timetable row.
    """
    prefix = "imported-review:"
    if not event_key.startswith(prefix):
        return None
    aptem_review_id = clean_text(event_key[len(prefix):])
    if not aptem_review_id:
        return None

    learners = fetch_caseload_dashboard_profiles(owner_email) if learners is None else learners
    aptem_by_profile, conflicts = resolve_effective_aptem_ids(learners)
    eligible_ids = sorted(
        profile_id for profile_id in aptem_by_profile
        if profile_id not in conflicts
    )
    if not eligible_ids:
        return None

    requested_overlay = ImportedReviewInstance.objects.filter(
        owner_email__iexact=owner_email.strip().casefold(), event_key=event_key,
    ).first()
    linked_source_id = getattr(requested_overlay, "source_review_id", None)
    use_internal_id = isinstance(linked_source_id, int) and linked_source_id > 0
    identity_column = "id" if use_internal_id else "aptem_review_id"
    identity_value = linked_source_id if use_internal_id else aptem_review_id
    query = f'''
        SELECT id, learner_id, aptem_review_id, review_name, review_type,
               reviewer_name, learner_name, planned_scheduled_date,
               completed_date, status, review_data, extraction_status, last_error
        FROM "Learner".reviews
        WHERE learner_id = ANY(%s) AND {identity_column} = %s
        ORDER BY id
        LIMIT 2
    '''
    connection = connections[get_learner_db_alias()]
    with connection.cursor() as cursor:
        cursor.execute(query, [eligible_ids, identity_value])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if len(rows) != 1:
            return None
        row = rows[0]
        profile_id = int(row["learner_id"])
        if profile_id not in aptem_by_profile:
            return None
        sections = _sections_by_review(cursor, [row["id"]])

    review = _serialize_review(row, sections, historical_presentation=not pdf_only)
    learner = next((item for item in learners if int(item.id) == profile_id), None)
    if learner is None:
        return None
    source_learner_id = clean_text(review.get("aptemLearnerId"))
    if source_learner_id and source_learner_id != str(aptem_by_profile[profile_id]):
        return None

    normalized_sections = sections.get(row["id"]) or []
    form_available = _imported_review_has_usable_form(
        review,
        has_normalized_sections=bool(normalized_sections),
    )
    historical_completed = migrated_source_is_completed(review)
    canonical_event_key = f"{prefix}{review['aptemReviewId']}"
    canonical_owner = owner_email.strip().casefold()
    saved_instance = requested_overlay
    if not saved_instance:
        saved_instance = ImportedReviewInstance.objects.filter(
            owner_email__iexact=canonical_owner, learner_id=profile_id, source_review_id=row["id"],
        ).first()
        if not saved_instance and ImportedReviewInstance.objects.filter(
                learner_id=profile_id, source_review_id=row["id"]).first() is not None:
            # A blocked handover must not look like a fresh form that the new
            # Coach can initialize over another owner's continuation/history.
            return None
    source_review_id = getattr(saved_instance, "source_review_id", None)
    overlay_learner_id = getattr(saved_instance, "learner_id", None)
    if saved_instance and isinstance(source_review_id, int):
        if source_review_id != row["id"] or overlay_learner_id != profile_id:
            return None
    if saved_instance and isinstance(overlay_learner_id, int) and overlay_learner_id != profile_id:
        return None
    if saved_instance and isinstance(getattr(saved_instance, "event_key", None), str):
        canonical_event_key = saved_instance.event_key
    snapshot = getattr(saved_instance, "template_snapshot", None)
    migrated_form = not historical_completed and isinstance(snapshot, dict) and bool(snapshot.get("sections"))
    if migrated_form and not getattr(saved_instance, "source_review_id", None):
        return None
    if pdf_only:
        # PDF generation needs the same caseload, source identity/completion
        # and overlay association gates above, without the form's mutable
        # template/booking controls, live historical progress or RAG lookups.
        return {
            **_review_learner_identity(learner),
            "migratedForm": migrated_form,
            "instance": {"id": canonical_event_key, "learnerId": profile_id,
                         "targetDate": review.get("plannedDate") or ""},
            "historicalReview": review,
        }
    family = migrated_review_family(review.get("type"))
    psp_initial_status = (clean_text(review.get("status")) if family == "PSP"
                          and review.get("status") in MIGRATED_EDITABLE_STATUSES else None)
    local_programme_key = migrated_programme_key(
        getattr(learner, "programme_id", None), getattr(learner, "programme", None),
    )
    eligible_for_template = bool(
        not saved_instance and not historical_completed and (not form_available or family == "PSP")
        and family and (initial_local_status(review.get("status")) or psp_initial_status)
    )
    approved_template = None
    if eligible_for_template:
        # Programme identity selects an override; Global forms only need a
        # supported family. Aptem programme IDs are not LMS programme keys.
        approved_template = resolve_migrated_template(local_programme_key, family)
    can_initialize = bool(eligible_for_template and approved_template and not preview_only)
    no_approved_template = bool(eligible_for_template and not approved_template)
    preview_sections = []
    preview_warnings = []
    preview_error = False
    if preview_only and approved_template:
        try:
            preview_sections, preview_warnings = render_migrated_sections(
                approved_template.definition_json, {},
            )
        except ValueError:
            # An invalid active definition must never be presented as a usable form.
            preview_error = True
    is_template_preview = bool(preview_sections)
    form_available = form_available or migrated_form or is_template_preview
    summary_only = not form_available
    saved_answers = (
        saved_instance.answers
        if not historical_completed and saved_instance and isinstance(saved_instance.answers, dict)
        else {}
    )
    review_type = clean_text(review.get("type"))
    monthly_types = {clean_text(value).casefold() for value in REVIEW_TYPES["monthly-coaching"]}
    review_type_code = ("aptem_psp" if family == "PSP" else
                        "aptem_mcm" if review_type.casefold() in monthly_types else "aptem_progress_review")
    is_mcm = review_type_code == "aptem_mcm"

    adapted_sections = []
    for section_index, section in enumerate((review.get("sections") or []) if form_available else []):
        section_id = str(section.get("id") or f"{row['id']}:{section_index}")
        fields = [
            _imported_review_field(field, section_id=section_id, index=index, historical=historical_completed)
            for index, field in enumerate(section.get("fields") or [])
            if isinstance(field, dict)
        ]
        display_index = len(fields)
        for table_index, table in enumerate(section.get("tables") or []):
            table_payload = table if isinstance(table, dict) else {"rows": table}
            table_rows = table_payload.get("rows") or table
            fields.append({
                "id": f"aptem-table:{section_id}:{table_index}",
                "title": clean_text(table_payload.get("title")) or "Imported table",
                "fieldType": "title_description",
                "required": False,
                "displayOrder": display_index + table_index,
                "configuration": {
                    "imported": True,
                    "importedTable": table_rows,
                    "description": json.dumps(table_rows, ensure_ascii=False, default=str),
                },
                "parentFieldId": None,
                "conditionValue": None,
                "yesFields": [],
                "noFields": [],
            })
        section_title = clean_text(section.get("name")) or "Review section"
        raw_text = clean_text(section.get("rawText"))
        # An Aptem MCM section's raw text is the verbatim text dump of the same
        # label/value pairs already shown as its questions; repeating it as a
        # display card duplicated every answer. Keep it only when it is the
        # section's sole content, labelled with the section it belongs to.
        if raw_text and not (is_mcm and fields):
            fields.append({
                "id": f"aptem-text:{section_id}",
                "title": section_title,
                "fieldType": "title_description",
                "required": False,
                "displayOrder": len(fields),
                "configuration": {"imported": True, "description": raw_text},
                "parentFieldId": None,
                "conditionValue": None,
                "yesFields": [],
                "noFields": [],
            })
        for field in fields:
            if field["id"] in saved_answers:
                field["answer"] = saved_answers[field["id"]]
        adapted_sections.append({
            "id": f"aptem-section:{section_id}",
            "title": section_title,
            "estimatedMinutes": 0,
            "displayOrder": section.get("order") if section.get("order") is not None else section_index,
            "enabled": True,
            "fields": fields,
            **({"historicalPresentation": True} if historical_completed else {}),
            **({"preserveRawText": True} if historical_completed and section.get("preserveRawText") else {}),
        })
    field_warnings = []
    if migrated_form:
        try:
            from coach_api.migrated_template_sync import active_answers
            adapted_sections, field_warnings = render_migrated_sections(
                snapshot, active_answers(snapshot, saved_answers),
                answer_audit=(getattr(saved_instance, "meeting_intelligence", None) or {}).get("answerEdits", {}),
            )
        except ValueError:
            return None
    elif is_template_preview:
        adapted_sections, field_warnings = preview_sections, preview_warnings

    if is_mcm and adapted_sections and not historical_completed and not migrated_form and not is_template_preview:
        def _mcm_sort_key(item):
            index, section = item
            rank = _imported_mcm_section_rank(section["title"])
            unknown = len(_IMPORTED_MCM_SECTION_ORDER)
            return (unknown if rank is None else rank, section["displayOrder"], index)

        adapted_sections = [
            section for _index, section in sorted(enumerate(adapted_sections), key=_mcm_sort_key)
        ]
        for display_order, section in enumerate(adapted_sections):
            section["displayOrder"] = display_order

    target_date = review.get("plannedDate") or review.get("completedDate") or ""
    progress_snapshot = None
    if review_type_code == "aptem_progress_review":
        progress_snapshot = (
            saved_instance.progress_snapshot if migrated_form
            else _imported_review_progress_snapshot(learner, review, calculated_by=owner_email)
        )
    signatures = {
        role: {"required": False, "signed": False, "signedBy": None, "signedName": None, "signedAt": None, "signature": None}
        for role in curriculum_review_instances.SIGNATURE_ROLES
    }
    if migrated_form and saved_instance:
        from coach_api.migrated_completion import signature_states
        signatures = signature_states(saved_instance)
    instance_status = (
        clean_text(review.get("status")) or CoachCalendarEvent.STATUS_NOT_SCHEDULED
        if summary_only or historical_completed or is_template_preview
        else saved_instance.status if saved_instance else ImportedReviewInstance.STATUS_IN_PROGRESS
    )
    instance_completed_at = (
        review.get("completedDate")
        if summary_only or historical_completed or is_template_preview
        else saved_instance.completed_at.isoformat() if saved_instance and saved_instance.completed_at else None
    )
    definition = {
        **_review_learner_identity(learner),
        **({"programme": review["historicalProgramme"]} if historical_completed and review.get("historicalProgramme") else {}),
        "readOnly": preview_only or summary_only or historical_completed or (family == "PSP" and not migrated_form) or (
            migrated_form and saved_instance.status not in MIGRATED_EDITABLE_STATUSES
        ),
        "source": "aptem",
        "formAvailable": form_available,
        "summaryOnly": summary_only,
        "migratedForm": migrated_form,
        "previewOnly": is_template_preview,
        "noApprovedMigratedTemplate": no_approved_template,
        "migratedPreviewError": preview_error,
        "canInitialize": can_initialize,
        "migratedTemplateResolution": (
            {"resolved_template_id": getattr(saved_instance, "migrated_template_id", None),
             "resolved_scope": (snapshot.get("templateSource") or {}).get("scope", "PROGRAMME"),
             "review_family": family, "uses_snapshot": True}
            if migrated_form else resolution_metadata(approved_template, family)
        ),
        "migratedProgrammeKey": local_programme_key if can_initialize or migrated_form or is_template_preview else None,
        "sourceStatus": clean_text(row.get("status")) or review.get("status"),
        "localStatus": saved_instance.status if saved_instance else None,
        "fieldWarnings": field_warnings,
        "instance": {
            "id": canonical_event_key,
            "reviewTemplateId": "",
            "learnerId": profile_id,
            "programmeId": clean_text(getattr(learner, "programme_id", None)),
            "occurrenceNumber": None if migrated_form or can_initialize or is_template_preview else 1,
            "targetDate": target_date,
            "status": instance_status,
            "startedAt": None,
            "completedAt": instance_completed_at,
        },
        "template": {
            "id": "",
            "name": (clean_text(snapshot.get("name")) if migrated_form else "")
                    or (clean_text(approved_template.name) if is_template_preview else "")
                    or clean_text(review.get("name")) or review_type or "Imported review",
            "reviewTypeCode": review_type_code,
            "signatures": {role: signatures[role]["required"] for role in curriculum_review_instances.SIGNATURE_ROLES},
            "visibleTo": {role: role == "advisor" for role in curriculum_review_instances.SIGNATURE_ROLES},
            "recurrence": {"interval": 0, "unit": "none"},
            "notifications": {},
            "allowEditingPriorDays": 0,
        },
        "sections": adapted_sections,
        "signatures": signatures,
        "manualOverride": None,
        "progressSnapshot": progress_snapshot,
        "ragHistory": (
            curriculum_review_instances.progress_review_rag_history(profile_id)
            if progress_snapshot is not None
            else []
        ),
        "historicalReview": review,
    }
    if migrated_form:
        from coach_api.migrated_summary_binding import answer_version
        from coach_api.migrated_summary_generation import summary_binding_response
        from coach_api.migrated_progress import calculation_available
        definition["progressVersion"] = answer_version(saved_instance)
        definition["canCalculateProgress"] = bool(
            not preview_only and calculation_available(saved_instance, family, local_programme_key)
        )
        definition["summaryBinding"] = summary_binding_response(saved_instance)
        from coach_api.migrated_template_sync import snapshot_state
        definition["templateSync"] = snapshot_state(saved_instance)
        definition["answerVersion"] = answer_version(saved_instance)
        definition["bookingAdoptionVersion"] = _migrated_booking_adoption_version(saved_instance)
        from .migrated_reopening import revision_number, history as migrated_revision_history
        definition["reviewRevision"] = revision_number(saved_instance)
        definition["reopenHistory"] = migrated_revision_history(saved_instance)
        pdf_ready = saved_instance.status == ImportedReviewInstance.STATUS_COMPLETED
        definition["pdf"] = {
            "available": pdf_ready,
            "reason": "" if pdf_ready else "Available after completion.",
            "source": "lms-migrated",
        }
    else:
        definition["pdf"] = {
        "available": True,
        "reason": "",
        "source": "aptem",
        }
    if not historical_completed and (migrated_form or can_initialize or is_template_preview):
        from .review_sources import booking_for_imported_review, ReviewIdentityConflict
        booking_conflict = False
        try:
            calendar = booking_for_imported_review(learner, review, saved_instance)
        except ReviewIdentityConflict:
            calendar = None
            booking_conflict = True
        booking_template = getattr(saved_instance, "migrated_template", None) if saved_instance else None
        association_valid = bool(
            calendar and calendar.owner_email.casefold() == canonical_owner
            and (calendar.event_key != canonical_event_key or (not calendar.review_instance_id and not calendar.review_template_id))
            and calendar.event_type == migrated_booking_event_type(family)
        )
        booked = bool(
            association_valid and calendar.sync_state == CoachCalendarEvent.SYNC_SYNCED
            and calendar.graph_event_id and calendar_record_has_launch_url(calendar)
            and calendar.status in {CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS}
        )
        from .migrated_rescheduling import can_reschedule
        from learner_api.booking_calendar import booking_calendar_payload
        definition["bookingCalendar"] = booking_calendar_payload()
        from .booking_reservations import review_occupies_slot
        definition["booking"] = {
            "reserved": bool(association_valid and review_occupies_slot(calendar)),
            "booked": booked,
            "conflict": booking_conflict or bool(calendar and not association_valid),
            "canReschedule": bool(
                association_valid and not booking_conflict and not preview_only
                and migrated_form and booking_template
                and snapshot_assignment_valid(saved_instance, family, local_programme_key)
                and can_reschedule(saved_instance, calendar)
            ),
            "canAttach": bool(
                booked and migrated_form and saved_instance and booking_template
                and snapshot_assignment_valid(saved_instance, family, local_programme_key)
                and saved_instance.status == ImportedReviewInstance.STATUS_NOT_SCHEDULED
                and not preview_only
            ),
            "canBook": bool(
                migrated_form and saved_instance and booking_template
                and snapshot_assignment_valid(saved_instance, family, local_programme_key)
                and saved_instance.status in {
                    ImportedReviewInstance.STATUS_NOT_SCHEDULED,
                    ImportedReviewInstance.STATUS_SCHEDULED,
                } and not booked and not preview_only and not booking_conflict and (not calendar or association_valid)
            ),
            "eventKey": calendar.event_key if association_valid else None,
            "scheduledDate": calendar.scheduled_date.isoformat() if association_valid and calendar.scheduled_date else None,
            "scheduledTime": format_time_value(calendar.scheduled_time) if association_valid and calendar.scheduled_time else None,
            "durationMinutes": calendar.duration_minutes if association_valid else None,
            "meetingLink": calendar.meeting_link if booked else None,
            "syncState": calendar.sync_state if association_valid else None,
        }
    from .personal_support_booking import is_personal_support_plan, booking_state
    if is_personal_support_plan(review.get("type")):
        from learner_api.booking_calendar import booking_calendar_payload
        definition["bookingCalendar"] = booking_calendar_payload()
        _calendar, definition["booking"] = booking_state(
            canonical_owner, learner, review, saved_instance, preview_only=preview_only,
        )
    return definition


def _owned_migrated_overlay(owner_email, definition, *, lock=False, lock_calendar=False):
    if lock_calendar:
        # Status writers later lock/update the booking. Match reconciliation
        # and rescheduling: learner -> calendar -> overlay, never the reverse.
        if not lock:
            raise ValueError("Calendar locking requires an overlay lock.")
        owned = _owned_migrated_overlay(owner_email, definition)
        if owned is None:
            return None
        lock_learner_calendar(owned.learner_id)
        from .review_sources import booking_for_overlay
        booking_for_overlay(owned, lock=True)
        # Re-read and revalidate below after acquiring the parent locks.
    query = ImportedReviewInstance.objects.filter(
        owner_email__iexact=owner_email.strip().casefold(),
        event_key=definition["instance"]["id"],
    )
    if lock:
        query = query.select_for_update()
    overlay = query.first()
    source = definition.get("historicalReview") or {}
    if not overlay or not isinstance(overlay.template_snapshot, dict) or not overlay.template_snapshot.get("sections"):
        return None
    if overlay.learner_id != definition["instance"]["learnerId"] or overlay.source_review_id != int(source["id"]):
        return None
    return overlay


@coach_access_required
def coach_review_instance_initialize(request, instance_id):
    """Explicitly initialize a working copy; only the assigned coach can sync it."""
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    if not instance_id.startswith("imported-review:"):
        return JsonResponse({"detail": "This action is for imported reviews only."}, status=409)
    owner_email = authenticated_coach_email(request).strip().casefold()
    definition = _imported_review_definition(owner_email, instance_id)
    if not definition:
        return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
    if not definition.get("canInitialize") or definition.get("readOnly") is False:
        return JsonResponse({"detail": "This imported review cannot be initialized."}, status=409)
    from .migrated_initialization import initialize_imported_review
    try:
        initialize_imported_review(owner_email, definition)
    except (ValueError, IntegrityError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else "This source review is already associated with another overlay."
        return JsonResponse({"detail": message}, status=409)
    return JsonResponse(_imported_review_definition(owner_email, instance_id))


@coach_access_required
def coach_review_instance_local_status(request, instance_id):
    """Start an imported review only after its LMS meeting is verified."""
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request).strip().casefold()
    try:
        payload = parse_json_body(request)
    except ValidationError as exc:
        return validation_error_response(exc)
    calendar_key = payload.get('calendarEventKey')
    if calendar_key is not None and (not isinstance(calendar_key, str) or not calendar_key):
        return JsonResponse({'detail': 'A valid calendar event key is required.'}, status=400)
    if any(payload.get(key) is not None and (not isinstance(payload[key], str) or not payload[key])
           for key in ('meetingLink', 'scheduledDate', 'scheduledTime')):
        return JsonResponse({'detail': 'Meeting link and schedule must be valid text values.'}, status=400)
    if not instance_id.startswith("imported-review:"):
        # Calendar keys (including legacy/reconciled bookings) are candidates,
        # never imported source IDs. Resolve through the existing authority.
        from .background_meeting_sync import imported_context
        from .review_sources import ReviewIdentityConflict
        if not calendar_key or calendar_key != instance_id:
            return JsonResponse({"detail": "This action is for imported reviews only."}, status=409)
        record = CoachCalendarEvent.objects.filter(event_key=calendar_key, owner_email__iexact=owner_email).first()
        try:
            context = imported_context(record) if record else None
        except ReviewIdentityConflict:
            context = None
        if not context or not context.get('overlayId'):
            return JsonResponse({'detail': 'No initialized imported review is associated with this booking.'}, status=409)
        instance_id = context['reviewKey']
    definition = _imported_review_definition(owner_email, instance_id)
    if not definition:
        return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
    if not definition.get("migratedForm") or definition.get("readOnly"):
        return JsonResponse({"detail": "This imported review is read-only."}, status=409)
    if payload.get("status") != ImportedReviewInstance.STATUS_IN_PROGRESS:
        return JsonResponse({"detail": "Only the in-progress transition is available in Phase B."}, status=400)
    with transaction.atomic():
        overlay = _owned_migrated_overlay(owner_email, definition, lock=True, lock_calendar=True)
        if not overlay:
            return JsonResponse({"detail": "Imported review association mismatch."}, status=409)
        from .review_sources import booking_for_overlay, ReviewIdentityConflict
        try:
            calendar = booking_for_overlay(overlay, lock=True)
        except ReviewIdentityConflict as exc:
            return coach_error(request, code="BOOKING_CONFLICT", message=str(exc), status=409)
        family = migrated_review_family((definition.get("historicalReview") or {}).get("type"))
        if not calendar or (calendar_key is not None and calendar.event_key != calendar_key):
            return coach_error(request, code="BOOKING_CONFLICT", message="Book and confirm the Teams meeting before starting this review.", status=409)
        if (('meetingLink' in payload and payload['meetingLink'] not in {calendar.meeting_link, calendar.graph_web_link})
                or (payload.get('scheduledDate') and payload['scheduledDate'] != str(calendar.scheduled_date))
                or (payload.get('scheduledTime') and parse_time_value(payload['scheduledTime']) != calendar.scheduled_time)):
            return coach_error(request, code='BOOKING_CONFLICT', message='The meeting booking changed. Refresh the review before starting it.', status=409)
        from .imported_review_start import start_locked
        from .migrated_reviews import source_is_completed
        if not start_locked(overlay, calendar, family,
                            source_completed=source_is_completed(definition.get('historicalReview') or {})):
            return JsonResponse({'detail': 'Only an initialized, scheduled review with a confirmed booking can be started.'}, status=409)
    return JsonResponse(_imported_review_definition(owner_email, instance_id))


def _migrated_booking_adoption_version(overlay):
    """Draft basis that survives a status-only adoption (including a lost reply).

    Keep the ordinary answerVersion unchanged. A retry may advance that version
    only if answers, questions and other saved review work still match.
    """
    state = {name: getattr(overlay, name, None) for name in (
        "answers", "template_snapshot", "migrated_template_id", "progress_snapshot",
        "meeting_intelligence", "signature_requirements",
    )}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@coach_access_required
@require_POST
def coach_review_instance_adopt_booking(request, instance_id):
    """Advance an imported form using its verified booking, without Graph I/O.

    Called automatically by the coach workspace. Reads remain read-only, and
    adopting a past/existing appointment never goes through new-booking rules.
    """
    from .review_sources import booking_for_overlay, ReviewIdentityConflict
    from . import imported_meeting_ownership as meeting_ownership

    owner_email = authenticated_coach_email(request).strip().casefold()
    if not instance_id.startswith("imported-review:"):
        return JsonResponse({"detail": "This action is for imported reviews only."}, status=409)
    definition = _imported_review_definition(owner_email, instance_id)
    if not definition:
        return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
    try:
        payload = parse_json_body(request) if request.body else {}
    except ValidationError as exc:
        return validation_error_response(exc)
    expected_version = payload.get("bookingAdoptionVersion")
    if "bookingAdoptionVersion" in payload and (not isinstance(expected_version, str) or not expected_version):
        return JsonResponse({"detail": "A valid bookingAdoptionVersion is required."}, status=400)
    family = migrated_review_family((definition.get("historicalReview") or {}).get("type"))
    if (family not in {"MCM", "PR", "PR_SKILLS_RADAR"}
            or not definition.get("migratedForm") or definition.get("readOnly")
            or definition.get("previewOnly")
            or (definition.get("templateSync") or {}).get("status") == "conflict"
            or not initial_local_status(definition.get("sourceStatus"))):
        return JsonResponse({"detail": "This review cannot adopt an existing meeting."}, status=409)
    try:
        with transaction.atomic():
            # Same learner -> calendar -> overlay lock order as start/submit.
            overlay = _owned_migrated_overlay(owner_email, definition, lock=True, lock_calendar=True)
            # Recheck the full source/booking resolver under the learner lock;
            # a second legacy appointment must not be hidden by a canonical key.
            current = _imported_review_definition(owner_email, instance_id)
            if (not current or current.get("readOnly") or not current.get("migratedForm")
                    or current.get("previewOnly") or (current.get("booking") or {}).get("conflict")
                    or (current.get("templateSync") or {}).get("status") == "conflict"
                    or migrated_review_family((current.get("historicalReview") or {}).get("type")) != family
                    or not initial_local_status(current.get("sourceStatus"))):
                return JsonResponse({"detail": "The review or meeting association changed. Reload the review.",
                                     "code": "BOOKING_CONFLICT"}, status=409)
            if (not overlay or overlay.completed_at
                    or overlay.learner_id != current["instance"]["learnerId"]
                    or overlay.source_review_id != int(current["historicalReview"]["id"])
                    or not snapshot_assignment_valid(overlay, family, current.get("migratedProgrammeKey"))
                    or overlay.status not in {ImportedReviewInstance.STATUS_NOT_SCHEDULED,
                                              ImportedReviewInstance.STATUS_SCHEDULED,
                                              ImportedReviewInstance.STATUS_IN_PROGRESS}):
                return JsonResponse({"detail": "The review changed. Reload it before linking the meeting."}, status=409)
            if expected_version is not None and expected_version != _migrated_booking_adoption_version(overlay):
                return JsonResponse({
                    "detail": "This review changed in another session. Reopen it and review the latest answers before saving. Your unsaved text has been kept on screen.",
                    "code": "ANSWER_CONFLICT",
                }, status=409)
            calendar = booking_for_overlay(overlay, lock=True)
            if (not calendar
                    or calendar.event_key != (current.get("booking") or {}).get("eventKey")
                    or not imported_booking_state.verified_meeting(
                        calendar, owner_email=owner_email,
                        event_type=migrated_booking_event_type(family))):
                return JsonResponse({"detail": "A verified, synced meeting with a date and time is required.",
                                     "code": "BOOKING_CONFLICT"}, status=409)
            if getattr(settings, 'APTEM_MEETING_OWNERSHIP_ENABLED', False):
                try:
                    meeting_ownership.require_activation_ready()
                    meeting_ownership.bind_owned_overlay(calendar, overlay)
                except meeting_ownership.MeetingOwnershipError:
                    return JsonResponse({"detail": "The Teams meeting ownership needs reconciliation.",
                                         "code": "BOOKING_CONFLICT"}, status=409)
            # Retries never regress an already scheduled/started review or
            # alter answers, signatures, the source record or calendar identity.
            if overlay.status == ImportedReviewInstance.STATUS_NOT_SCHEDULED:
                overlay.status = ImportedReviewInstance.STATUS_SCHEDULED
                overlay.save(update_fields=["status", "updated_at"])
            # Return the checked content and its new answerVersion while the
            # overlay is still locked, so a retry cannot accept a later edit.
            return JsonResponse(_imported_review_definition(owner_email, instance_id))
    except ReviewIdentityConflict as exc:
        return JsonResponse({"detail": str(exc), "code": "BOOKING_CONFLICT"}, status=409)


@coach_access_required
def coach_review_instance_book(request, instance_id):
    """Book one imported review by its Aptem identity, never by a native occurrence."""
    if request.method not in {"POST", "PATCH"}:
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    if not instance_id.startswith("imported-review:"):
        return coach_error(request, code="BOOKING_CONFLICT", message="Only imported reviews can use this booking action.", status=409)
    owner_email = authenticated_coach_email(request).strip().casefold()
    definition = _imported_review_definition(owner_email, instance_id)
    if not definition:
        return coach_error(request, code="UNAUTHORIZED", message="Imported review not found for this coach.", status=404)
    from .personal_support_booking import is_personal_support_plan, book
    if is_personal_support_plan((definition.get("historicalReview") or {}).get("type")):
        return book(request, instance_id, definition)
    if request.method == "PATCH":
        from .migrated_rescheduling import reschedule
        return reschedule(request, instance_id)
    if not initial_local_status(definition.get("sourceStatus")):
        return coach_error(request, code="BOOKING_CONFLICT", message="Historical or unsupported Aptem reviews cannot be booked.", status=409)
    if not definition.get("migratedForm") or definition.get("readOnly"):
        return coach_error(request, code="NO_APPROVED_TEMPLATE", message="An approved migrated form is required before booking.", status=409)
    overlay = _owned_migrated_overlay(owner_email, definition)
    family = migrated_review_family((definition.get("historicalReview") or {}).get("type"))
    # A Global snapshot has no programme assignment. Programme snapshots still
    # require their exact key through snapshot_assignment_valid below.
    if (not overlay or not family or not initial_local_status(definition.get("sourceStatus"))
        or not snapshot_assignment_valid(overlay, family, definition.get("migratedProgrammeKey"))):
        return coach_error(request, code="NO_APPROVED_TEMPLATE", message="An approved migrated form is required before booking.", status=409)
    if overlay.status not in {ImportedReviewInstance.STATUS_NOT_SCHEDULED, ImportedReviewInstance.STATUS_SCHEDULED}:
        return coach_error(request, code="BOOKING_CONFLICT", message="Only an unstarted migrated review can be booked.", status=409)
    if definition.get("booking", {}).get("conflict"):
        return coach_error(request, code="BOOKING_CONFLICT", message="This imported review has an inconsistent calendar link.", status=409)
    try:
        payload = parse_json_body(request)
        validator = ObjectValidator(payload)
        scheduled_date = validator.iso_date("scheduledDate", required=True)
        scheduled_time = validator.clock_time("scheduledTime", required=True)
        duration_minutes = validator.integer("durationMinutes", required=True, minimum=15, maximum=480)
        timezone_offset_minutes = validator.integer("timezoneOffsetMinutes", default=0, minimum=-840, maximum=840)
        if scheduled_date and scheduled_date < date.today():
            validator.error("scheduledDate", "Choose today or a future date.")
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)
    source_review = definition.get("historicalReview") or {}
    occurrence_key = f"imported-review:{source_review['aptemReviewId']}" if source_review.get("aptemReviewId") else instance_id
    base_event, owner_name = find_generated_timetable_event(owner_email, occurrence_key)
    learner_id = definition["instance"]["learnerId"]
    event_type = migrated_booking_event_type(family)
    if not event_type or not base_event or base_event.get("reviewSource") != "aptem" or int(base_event.get("learnerId") or 0) != learner_id or base_event.get("source") != event_type:
        return coach_error(request, code="BOOKING_CONFLICT", message="The imported calendar event could not be verified.", status=409)
    learner = next((row for row in fetch_caseload_dashboard_profiles(owner_email) if int(getattr(row, "id", 0) or 0) == learner_id), None)
    if learner is None:
        return coach_error(request, code="MISSING_LEARNER", message="The learner could not be resolved.", status=409)
    from .booking_hours import validate_review_interval
    current_booking = definition.get("booking") or {}
    same_persisted_slot = bool(
        current_booking.get("booked")
        and (current_booking.get("scheduledDate"), current_booking.get("scheduledTime"),
             current_booking.get("durationMinutes")) == (scheduled_date.isoformat(), scheduled_time.strftime("%H:%M"), duration_minutes)
    )
    if not same_persisted_slot:
        try:
            validate_review_interval(scheduled_date, scheduled_time, duration_minutes)
        except ValueError as exc:
            return coach_error(request, code="INVALID_REVIEW_TIME", message=str(exc), status=400)
    if not same_persisted_slot and coach_learner_personal_calendar_conflicts(
        learner, scheduled_date, scheduled_time, duration_minutes, timezone_offset_minutes,
        exclude_record=(definition.get("booking", {}).get("eventKey") and CoachCalendarEvent.objects.filter(
            event_key=definition["booking"]["eventKey"], owner_email__iexact=owner_email,
            learner_id=learner_id, event_type=event_type,
        ).first()),
        business_time_zone="Europe/London",
    ):
        return coach_error(request, code="BOOKING_CONFLICT", message="This learner is busy at that time.", status=409)
    target_date = parse_schedule_date(definition["instance"]["targetDate"])
    if not target_date:
        return coach_error(request, code="BOOKING_CONFLICT", message="This review has no valid target date.", status=409)
    from .review_sources import booking_for_imported_review, ReviewIdentityConflict
    try:
        record_preview = booking_for_imported_review(learner, source_review)
        placeholder_preview = imported_booking_state.unreserved_placeholder(record_preview)
        if not same_persisted_slot:
            from .booking_availability import ensure_coach_review_slot_available
            ensure_coach_review_slot_available(owner_email, scheduled_date, scheduled_time,
                                               duration_minutes, record=record_preview if placeholder_preview else None)
        with transaction.atomic():
            lock_learner_calendar(learner_id)
            # Re-resolve the durable source link after taking the learner lock;
            # the definition's earlier booking preview may now be stale.
            record = booking_for_imported_review(learner, source_review, lock=True)
            if record and (record.owner_email.casefold() != owner_email
                           or record.event_type != event_type):
                return coach_error(request, code="BOOKING_CONFLICT", message="This imported review has an inconsistent calendar link.", status=409)
            # An abandoned attempt can leave a row that never reserved a slot.
            # Reserving it is this booking, not a second appointment, so it
            # takes the reservation path instead of the "already booked" one.
            placeholder = imported_booking_state.unreserved_placeholder(record)
            if record and not placeholder:
                if record.status != CoachCalendarEvent.STATUS_SCHEDULED:
                    return coach_error(request, code="BOOKING_CONFLICT", message="This booking is no longer in a schedulable state.", status=409)
                if (record.scheduled_date, record.scheduled_time, record.duration_minutes) != (scheduled_date, scheduled_time, duration_minutes):
                    return coach_error(request, code="ALREADY_BOOKED", message="This review already has a different booking request.", status=409)
                if record.sync_state in {CoachCalendarEvent.SYNC_SYNCING, CoachCalendarEvent.SYNC_RECONCILIATION}:
                    return coach_error(request, code="BOOKING_CONFLICT", message="Calendar synchronization is in progress or needs reconciliation.", status=409)
            else:
                if england_non_delivery_reason(scheduled_date):
                    return coach_error(request, code="BOOKING_CONFLICT", message=england_non_delivery_reason(scheduled_date), status=409)
                ensure_coach_booking_available(owner_email, scheduled_date, scheduled_time, duration_minutes,
                                               **({"exclude_record_id": record.pk} if placeholder else {}))
                ensure_learner_calendar_available(
                    learner_id=learner_id, learner_email=clean_text(base_event.get("email")),
                    scheduled_date=scheduled_date, scheduled_time=scheduled_time,
                    duration_minutes=duration_minutes,
                    **({"exclude_record_id": record.pk} if placeholder else {}),
                )
                reserved = dict(
                    owner_name=owner_name,
                    learner_name=clean_text(base_event.get("learner")),
                    learner_email=clean_text(base_event.get("email")),
                    sequence=int(base_event.get("sequence") or 1), target_date=target_date,
                    scheduled_date=scheduled_date, scheduled_time=scheduled_time,
                    duration_minutes=duration_minutes, status=CoachCalendarEvent.STATUS_SCHEDULED,
                    sync_state=CoachCalendarEvent.SYNC_PENDING,
                )
                if placeholder:
                    for field, value in reserved.items():
                        setattr(record, field, value)
                    if not clean_text(record.idempotency_key):
                        record.idempotency_key = record.event_key
                    record.save(update_fields=[*reserved, "idempotency_key", "updated_at"])
                else:
                    record = CoachCalendarEvent.objects.create(
                        event_key=instance_id, idempotency_key=instance_id,
                        owner_email=owner_email, learner_id=learner_id,
                        event_type=event_type, **reserved,
                    )
    except IntegrityError:
        return coach_error(request, code="BOOKING_CONFLICT", message="A concurrent booking won. Reload this review.", status=409)
    except (LearnerCalendarConflict, ReviewIdentityConflict) as exc:
        return coach_error(request, code="BOOKING_CONFLICT", message=str(exc), status=409)
    except Exception as exc:
        from .booking_availability import CoachAvailabilityUnavailable
        if isinstance(exc, CoachAvailabilityUnavailable):
            return coach_error(request, code="AVAILABILITY_UNAVAILABLE", message=str(exc), status=503)
        logger.exception("migrated_review_calendar_reservation_failed")
        return coach_error(request, code="CALENDAR_CREATE_FAILED", message="The calendar operation could not be completed. Reload before retrying.", status=500)
    try:
        record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, base_event)
    except Exception:
        logger.exception("migrated_review_graph_sync_failed")
        return coach_error(request, code="GRAPH_CREATE_FAILED", message="The Teams meeting could not be confirmed. Reload before retrying with the same details.", status=502)
    if warning or record.sync_state != CoachCalendarEvent.SYNC_SYNCED or not record.graph_event_id or not calendar_record_has_launch_url(record):
        return coach_error(request, code="GRAPH_CREATE_FAILED", message="The Teams meeting could not be confirmed. The booking is safe to retry with the same details.", status=502)
    with transaction.atomic():
        saved = _owned_migrated_overlay(owner_email, definition, lock=True)
        if not saved or saved.status not in {ImportedReviewInstance.STATUS_NOT_SCHEDULED, ImportedReviewInstance.STATUS_SCHEDULED}:
            return coach_error(request, code="BOOKING_CONFLICT", message="The review changed during booking. Reload it.", status=409)
        if saved.status != ImportedReviewInstance.STATUS_SCHEDULED:
            saved.status = ImportedReviewInstance.STATUS_SCHEDULED
            saved.save(update_fields=["status", "updated_at"])
    return JsonResponse(_imported_review_definition(owner_email, instance_id))


@coach_access_required
@require_GET
def coach_review_availability(request):
    """Return verified London-time Review slots for a coach and caseload learner."""
    owner_email = authenticated_coach_email(request).strip().casefold()
    try:
        validator = ObjectValidator(request.GET)
        learner_id = validator.integer("learnerId", required=True, minimum=1)
        day = validator.iso_date("date", required=True)
        duration = validator.integer("durationMinutes", required=True, minimum=15, maximum=480)
        validator.check()
        from .booking_hours import validate_review_duration
        duration = validate_review_duration(duration)
    except ValidationError as exc:
        return validation_error_response(exc)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)

    learner = next((row for row in fetch_caseload_dashboard_profiles(owner_email)
                    if int(getattr(row, "id", 0) or 0) == learner_id), None)
    if learner is None:
        return coach_error(request, code="MISSING_LEARNER",
                           message="The learner is no longer assigned to this coach.", status=404)

    exclude_key = clean_text(request.GET.get("excludeEventKey"))
    exclude_record_id = None
    exclude_record = None
    if exclude_key:
        record = CoachCalendarEvent.objects.filter(
            event_key=exclude_key, owner_email__iexact=owner_email, learner_id=learner_id,
            event_type__in={"mcr", "progress-review", "review"},
        ).first()
        if not record:
            return coach_error(request, code="BOOKING_CONFLICT",
                               message="The current Review booking could not be verified. Refresh and retry.",
                               status=409)
        exclude_record_id = record.pk
        exclude_record = record

    try:
        from learner_api.coach_availability import free_slots, uk_offset_minutes
        times = free_slots(owner_email, day, uk_offset_minutes(day), duration=duration,
                           review_hours=True, exclude_event_key=exclude_key,
                           exclude_record_id=exclude_record_id,
                           review_self_record=exclude_record)
    except Exception as exc:
        from learner_api.coach_availability import AvailabilityUnavailable
        if isinstance(exc, AvailabilityUnavailable):
            return coach_error(request, code="AVAILABILITY_UNAVAILABLE", message=str(exc), status=503)
        logger.exception("coach_review_availability_failed")
        return coach_error(request, code="AVAILABILITY_UNAVAILABLE",
                           message="Could not check the coach calendar. Please retry before booking.",
                           status=503)
    response = JsonResponse({"date": day.isoformat(), "durationMinutes": duration,
                             "timeZone": "Europe/London", "times": times})
    response["Cache-Control"] = "private, no-store"
    return response


@coach_access_required
def coach_review_instance_for_event(request):
    """Open the Curriculum Review form for a calendar event.

    A Review occurrence exists on the calendar before anybody schedules it, so
    the coach can open its form from a generated slot too -- the durable
    instance row is created on first open, idempotently and keyed exactly the
    same way scheduling creates it, so the two paths converge on one instance.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    try:
        payload = parse_json_body(request)
        validator = ObjectValidator(payload)
        event_key = validator.text("eventKey", required=True, max_length=255)
        validator.check()
    except ValidationError as exc:
        return validation_error_response(exc)

    owner_email = authenticated_coach_email(request)
    if event_key.startswith("imported-review:"):
        base_event, _owner_name = find_generated_timetable_event(owner_email, event_key)
        if not base_event:
            return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)
        return JsonResponse({"detail": "Imported reviews cannot be opened as native reviews."}, status=409)
    record = CoachCalendarEvent.objects.filter(event_key=event_key, owner_email__iexact=owner_email).first()
    if record and clean_text(record.review_instance_id):
        from .reconciliation_links import for_calendar
        link = for_calendar(record)
        if link:
            return JsonResponse({"instanceId": link.overlay.event_key})
        return JsonResponse({"instanceId": clean_text(record.review_instance_id)})

    base_event, _owner_name = find_generated_timetable_event(owner_email, event_key)
    if not base_event:
        return JsonResponse({"detail": "Calendar event not found for this coach."}, status=404)

    try:
        require_native_review_creation(int(base_event["learnerId"]))
    except ReviewCreationBlocked as exc:
        return JsonResponse(exc.policy, status=409)

    review_template_id = clean_text(base_event.get("reviewTemplateId")) or (record and clean_text(record.review_template_id))
    if not review_template_id:
        return JsonResponse({"detail": "This event is not a Curriculum review."}, status=400)

    template_row = curriculum_reviews.get_review_template_row(review_template_id)
    if not template_row:
        return JsonResponse({"detail": "This review is no longer configured in Curriculum."}, status=404)

    target_date = parse_date_value(base_event.get("targetDate"))
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    is_manual = base_event.get("occurrenceSource") == curriculum_review_instances.OCCURRENCE_SOURCE_MANUAL

    try:
        if record:
            # Advanced unlinked Review rows are historical data.  Refuse to create
            # a fabricated not-scheduled instance; legacy review_responses remain
            # readable through the calendar summary/read path.
            record.review_template_id = review_template_id
            record.occurrence_number = None if is_manual else parse_int(
                base_event.get("occurrenceNumber"), record.sequence,
            )
            if record.status in {
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
                CoachCalendarEvent.STATUS_COMPLETED,
            } and not clean_text(record.review_instance_id):
                try:
                    ensure_review_instance_for_calendar_record(record, base_event)
                except LegacyReviewReconciliationRequiredError as exc:
                    return JsonResponse({
                        "detail": str(exc),
                        "code": exc.code,
                    }, status=409)
            elif record.status == CoachCalendarEvent.STATUS_SCHEDULED:
                try:
                    ensure_review_instance_for_calendar_record(record, base_event)
                except (ReviewTemplateUnavailableError, ReviewCreationBlocked) as exc:
                    return JsonResponse(getattr(exc, "policy", {"detail": str(exc)}), status=409)
            else:
                instance = curriculum_review_instances.ensure_review_instance(
                    template_row,
                    learner_id=int(base_event["learnerId"]),
                    learner_kind='',
                    programme_id=template_row.get("programme_id"),
                    occurrence_number=None if is_manual else parse_int(
                        base_event.get("occurrenceNumber"),
                        int(base_event.get("sequence") or 1),
                    ),
                    target_date=target_date,
                    coach_email=owner_email,
                    actor=owner_email or "coach",
                    occurrence_source=base_event.get(
                        "occurrenceSource",
                        curriculum_review_instances.OCCURRENCE_SOURCE_GENERATED,
                    ),
                    occurrence_ref=base_event.get("occurrenceRef") if is_manual else None,
                )
                if not instance:
                    return JsonResponse({"detail": "This review could not be opened."}, status=400)
                record.review_instance_id = instance.get("id")
                record.save(update_fields=[
                    "review_template_id", "occurrence_number", "review_instance_id", "updated_at",
                ])
                curriculum_review_instances.link_calendar_event(
                    instance.get("id"), record.pk, actor=owner_email or "coach",
                )
            record.refresh_from_db()
            return JsonResponse({"instanceId": clean_text(record.review_instance_id)})

        instance = curriculum_review_instances.ensure_review_instance(
            template_row,
            learner_id=int(base_event["learnerId"]),
            learner_kind='',
            programme_id=template_row.get("programme_id"),
            occurrence_number=None if is_manual else parse_int(
                base_event.get("occurrenceNumber"), int(base_event.get("sequence") or 1),
            ),
            target_date=target_date,
            coach_email=owner_email,
            actor=owner_email or "coach",
            occurrence_source=base_event.get(
                "occurrenceSource",
                curriculum_review_instances.OCCURRENCE_SOURCE_GENERATED,
            ),
            occurrence_ref=base_event.get("occurrenceRef") if is_manual else None,
        )
        if not instance:
            return JsonResponse({"detail": "This review could not be opened."}, status=400)
        return JsonResponse({"instanceId": instance.get("id")})
    except ReviewCreationBlocked as exc:
        return JsonResponse(exc.policy, status=409)


@coach_access_required
def coach_review_instance_detail(request, instance_id):
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    if instance_id.startswith("imported-review:"):
        definition = _imported_review_definition(
            authenticated_coach_email(request), instance_id,
            preview_only=is_coach_view_as(request),
        )
        if not definition:
            return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
        if not is_coach_view_as(request):
            from coach_api.migrated_template_sync import synchronize_on_open
            definition = synchronize_on_open(authenticated_coach_email(request), instance_id, definition)
            if not definition:
                return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
        response = JsonResponse(definition)
        response["Cache-Control"] = "private, no-store"
        return response
    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    from .reconciliation_links import for_native
    link = for_native(instance_id)
    if link:
        definition = _imported_review_definition(authenticated_coach_email(request), link.overlay.event_key,
                                                 preview_only=is_coach_view_as(request))
        if not definition:
            return JsonResponse({"detail": "Imported review not available for this coach."}, status=404)
        return JsonResponse(definition)
    # Opening this form is not evidence the Teams meeting started -- it used
    # to flip status to in-progress here. The real trigger is a confirmed
    # Microsoft Teams attendance signal; see
    # apply_teams_attendance_status_transition.
    return JsonResponse(_coach_review_instance_definition(instance_row))


@coach_access_required
def coach_review_instance_previous(request, instance_id):
    """Return the immediately preceding occurrence for this coach's Review.

    This is deliberately a stored-only, read-only context endpoint.  It uses
    the Curriculum identity (template + learner + occurrence number), so an
    MCM #3 can only resolve to that learner's MCM #2 and a Progress Review #2
    can only resolve to that learner's Progress Review #1.  It never contacts
    Microsoft Graph or an AI provider while loading the form.
    """
    if request.method != "GET":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    if instance_id.startswith("imported-review:"):
        from .migrated_previous import previous_review
        return previous_review(authenticated_coach_email(request), instance_id)

    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error

    current_occurrence = instance_row.get("occurrence_number")
    try:
        current_occurrence = int(current_occurrence)
    except (TypeError, ValueError):
        current_occurrence = None

    unavailable = {
        "available": False,
        "reason": "There is no previous occurrence for this Review.",
        "instance": None,
        "review": None,
        "summaryText": "",
        "transcriptText": "",
        "transcriptAvailable": False,
        "transcriptTruncated": False,
    }
    if current_occurrence is None or current_occurrence <= 1:
        return JsonResponse(unavailable)

    previous = curriculum_review_instances.find_review_instance(
        instance_row.get("review_template_id"),
        instance_row.get("learner_id"),
        current_occurrence - 1,
    )
    if not previous:
        unavailable["reason"] = "The previous occurrence has not been created yet."
        return JsonResponse(unavailable)

    # A coach may read the prior occurrence only when it belongs to the same
    # assigned coach.  The identity lookup above prevents cross-template or
    # cross-learner leakage; this ownership check also protects coach changes
    # and historical rows with a different owner.
    if clean_email(previous.get("coach_email")) != clean_email(instance_row.get("coach_email")):
        unavailable["reason"] = "The previous occurrence is not assigned to this coach."
        return JsonResponse(unavailable)
    if previous.get("status") in {
        curriculum_review_instances.STATUS_NOT_SCHEDULED,
        curriculum_review_instances.STATUS_SCHEDULED,
    }:
        unavailable["reason"] = "The previous occurrence has not started yet."
        return JsonResponse(unavailable)

    previous_definition = curriculum_review_instances.review_instance_form_definition(previous)
    record = _review_instance_calendar_record(previous)

    summary_text = ""
    # The formal mapped Meeting Summary answer is the source of truth.  This
    # lets an MCM show a coach-edited summary rather than only the generated
    # artifact.
    summary_field = curriculum_review_instances.meeting_summary_field(previous_definition)
    if not summary_text and summary_field:
        value = summary_field.get("answer")
        if isinstance(value, str):
            summary_text = value.strip()

    # Older instances may have a stored AI artifact but no formal answer yet;
    # use it as a read-only fallback in that case.
    if not summary_text and record:
        stored_summary = stored_coach_meeting_summary(record)
        if stored_summary and clean_text(stored_summary.get("status")) in {"ready", "edited"}:
            summary_text = meeting_summary_plain_text(stored_summary.get("summary") or {})

    transcript_text = ""
    if record:
        stored_transcript = stored_coach_meeting_transcript_for_summary(record)
        if stored_transcript:
            transcript_text = clean_text(stored_transcript.get("text"))
    transcript_truncated = len(transcript_text) > COACH_MEETING_SUMMARY_TRANSCRIPT_CHARS
    if transcript_truncated:
        transcript_text = transcript_text[:COACH_MEETING_SUMMARY_TRANSCRIPT_CHARS]

    def iso_value(value):
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return clean_text(value) or None

    return JsonResponse({
        "available": True,
        "reason": "",
        "instance": {
            "id": previous.get("id"),
            "occurrenceNumber": previous.get("occurrence_number"),
            "targetDate": iso_value(previous.get("target_date")) or "",
            "completedAt": iso_value(previous.get("completed_at")),
            "status": previous.get("status"),
        },
        "review": {
            "name": previous_definition.get("template", {}).get("name") or "Review",
            "reviewTypeCode": previous_definition.get("template", {}).get("reviewTypeCode"),
            "reviewTemplateId": previous.get("review_template_id"),
        },
        "summaryText": summary_text,
        "transcriptText": transcript_text,
        "transcriptAvailable": bool(transcript_text),
        "transcriptTruncated": transcript_truncated,
    })


@coach_access_required
def coach_review_instance_answers(request, instance_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request)
    if instance_id.startswith("imported-review:"):
        owner_email = owner_email.strip().casefold()
        definition = _imported_review_definition(owner_email, instance_id)
        if not definition:
            return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
        if definition.get("readOnly"):
            return JsonResponse({"detail": "This imported review is read-only."}, status=409)
        try:
            payload = parse_json_body(request)
        except ValidationError as exc:
            return validation_error_response(exc)
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            return JsonResponse({"detail": "answers must be an object keyed by field id."}, status=400)
        if definition.get("migratedForm"):
            from coach_api.migrated_summary_binding import AnswerConflict, check_answer_version, record_answer_edit
            from coach_api.migrated_template_sync import TemplateSyncConflict, preserve_inactive_answers, synchronize_definition_locked
            with transaction.atomic():
                imported = _owned_migrated_overlay(owner_email, definition, lock=True,
                    lock_calendar=definition.get("template", {}).get("reviewTypeCode") == "aptem_psp")
                if not imported or imported.status not in MIGRATED_EDITABLE_STATUSES:
                    return JsonResponse({"detail": "Imported review association or status mismatch."}, status=409)
                try:
                    check_answer_version(imported, payload)
                    if synchronize_definition_locked(imported, definition):
                        return JsonResponse({"detail": "The review template changed. Reopen the review to check its latest questions before saving. Your unsaved answers have been kept on screen.", "code": "template_sync_changed"}, status=409)
                    validate_migrated_answers(imported.template_snapshot, answers)
                except TemplateSyncConflict as exc:
                    return JsonResponse({"detail": str(exc), "templateSync": exc.response()}, status=409)
                except AnswerConflict as exc:
                    return JsonResponse({"detail": str(exc), "code": "ANSWER_CONFLICT"}, status=409)
                except ValueError as exc:
                    return JsonResponse({"detail": str(exc)}, status=400)
                record_answer_edit(imported, answers, actor=owner_email,
                                   edited_fields=payload.get("editedFields") if isinstance(payload.get("editedFields"), list) else ())
                imported.answers = preserve_inactive_answers(imported.template_snapshot, imported.answers or {}, answers)
                imported.save(update_fields=["answers", "meeting_intelligence", "updated_at"])
                from .migrated_completion import complete_personal_support_if_ready
                complete_personal_support_if_ready(imported)
                # Return answers and their version from the same locked state.
                return JsonResponse(_imported_review_definition(owner_email, imported.event_key))
        field_ids = {
            field["id"]
            for section in definition.get("sections", [])
            for field in section.get("fields", [])
        }
        if any(field_id not in field_ids for field_id in answers):
            return JsonResponse({"detail": "answers contains a field that is not part of this review."}, status=400)
        imported, _created = ImportedReviewInstance.objects.get_or_create(
            owner_email=owner_email,
            event_key=definition["instance"]["id"],
            defaults={"learner_id": definition["instance"]["learnerId"]},
        )
        if imported.status == ImportedReviewInstance.STATUS_COMPLETED:
            return JsonResponse({"detail": "This review has already been completed and is read-only."}, status=409)
        imported.answers = answers
        imported.save(update_fields=["answers", "updated_at"])
        return JsonResponse(_imported_review_definition(owner_email, imported.event_key))

    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    try:
        payload = parse_json_body(request)
    except ValidationError as exc:
        return validation_error_response(exc)
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return JsonResponse({"detail": "answers must be an object keyed by field id."}, status=400)
    try:
        curriculum_review_instances.save_review_instance_answers(instance_row, answers, actor=owner_email)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=409)
    return JsonResponse(_coach_review_instance_definition(
        curriculum_review_instances.get_review_instance(instance_row["id"])
    ))


@coach_access_required
def coach_review_instance_meeting_summary(request, instance_id):
    """Explicitly acquire/generate the AI suggestion for one mapped MCM/PR.

    Passive Review reads use ``_review_instance_meeting_summary_source`` and
    never reach Graph/OpenAI. This POST reuses the same snapshot persistence,
    transcript extraction and repaired generation pipeline as Check Teams, or
    accepts one explicit .vtt fallback upload without creating a Teams
    artifact. It never writes the formal Review answer.

    The two routes differ in what they require. Generating from Teams needs a
    linked meeting, because the transcript is fetched from it. An uploaded
    .vtt does not: the coach already has the transcript, and a meeting that
    ran outside Teams is the reason the upload exists. Only the Teams route
    stores its result; an uploaded recap is returned for the coach to review
    and save as the Review answer, and is never recorded as a Teams artifact.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    def summary_error(*, code: str, detail: str, status: int):
        """Return actionable public diagnostics without exposing provider errors."""
        return JsonResponse({
            "detail": detail,
            "code": code,
            "request_id": request_id_for(request),
        }, status=status)

    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    definition = curriculum_review_instances.review_instance_form_definition(instance_row)
    if definition.get("template", {}).get("reviewTypeCode") not in REVIEW_MEETING_SUMMARY_REVIEW_TYPES:
        return JsonResponse({"detail": "Meeting Summary generation is only available on a Monthly Coaching Meeting or Progress Review."}, status=404)
    field = curriculum_review_instances.meeting_summary_field(definition)
    if not field:
        return JsonResponse({
            "detail": "This Review Instance has no explicitly mapped Meeting Summary field. Enter the summary manually.",
        }, status=409)
    if instance_row.get("status") in {
        curriculum_review_instances.STATUS_AWAITING_SIGNATURE,
        curriculum_review_instances.STATUS_COMPLETED,
    }:
        return JsonResponse({"detail": "This Review has already been submitted and its answers are read-only."}, status=409)

    record = _review_instance_calendar_record(instance_row)

    uploaded_transcript = request.FILES.get("transcript")
    if uploaded_transcript is not None:
        # The upload is the documented route for a meeting that did NOT happen
        # in Teams, so it deliberately runs before the linked-meeting check
        # below. Requiring that link made the fallback unreachable in exactly
        # the case it exists for. Nothing here contacts Graph or writes a Teams
        # artifact; a calendar row, when one exists, only labels the prompt.
        context = record or _review_instance_meeting_summary_context(instance_row)
        try:
            transcript_text = uploaded_coach_meeting_transcript_text(uploaded_transcript)
        except ValueError as exc:
            return summary_error(code="invalid_transcript_upload", detail=str(exc), status=400)
        try:
            summary, _model = openai_meeting_summary(context, transcript_text)
        except Exception:  # noqa: BLE001 - never expose provider details
            logger.exception(
                "Unable to generate coach meeting summary from uploaded transcript for review instance %s",
                instance_row.get("id"),
            )
            return summary_error(
                code="meeting_summary_ai_failed",
                detail=(
                    "The transcript was accepted, but the AI Meeting Summary could not be generated. "
                    "Check the OpenAI API key, model access, quota, or server connection, then try again. "
                    "Your current Review answer has not been changed."
                ),
                status=502,
            )
        # Empty unless something qualifies THIS result, matching what
        # _review_instance_meeting_summary_source sends for a clean read. The
        # client supplies its own wording for the ordinary case and shows a
        # message here as a caveat, so a silent partial recap is the one thing
        # that must not happen.
        message = ""
        if meeting_summary_transcript_excerpt(transcript_text)[1]:
            message = (
                "The uploaded transcript was longer than this summary can cover, so only its "
                "earlier part was used. Check the rest of the meeting yourself and add anything "
                "the summary is missing."
            )
        return JsonResponse({
            "meetingSummarySource": {
                "fieldId": field.get("id"),
                "status": "ready",
                "summaryText": meeting_summary_plain_text(summary),
                "generatedAt": timezone.now().isoformat(),
                "editedAt": None,
                "message": message,
            }
        })

    if not record:
        return summary_error(
            code="meeting_summary_meeting_not_linked",
            detail="This Review is not linked to a scheduled Teams meeting. Upload the meeting transcript as a .vtt file, or enter the summary manually.",
            status=409,
        )

    # A valid stored artifact is idempotent. In particular, a coach-edited AI
    # artifact must never be replaced simply because this button was pressed.
    existing = stored_coach_meeting_summary(record)
    if existing and clean_text(existing.get("status")) in {"ready", "edited"}:
        return JsonResponse({"meetingSummarySource": _review_instance_meeting_summary_source(instance_row, definition)})

    snapshot, error_payload, status_code = fetch_coach_meeting_graph_snapshot(record)
    if error_payload:
        return JsonResponse({
            **error_payload,
            "code": error_payload.get("code") or "teams_source_unavailable",
            "request_id": request_id_for(request),
        }, status=status_code)
    persist_coach_meeting_snapshots(
        record,
        artifacts=snapshot["artifacts"],
        attendance_reports=snapshot["attendanceReports"],
        attendance_tracker=snapshot["attendanceTracker"],
    )
    if not stored_coach_meeting_transcript_for_summary(record):
        selection = coach_meeting_transcript_selection(record)
        if selection['code'] == 'TRANSCRIPT_SESSION_AMBIGUOUS':
            return summary_error(code=selection['code'],
                detail='The transcript cannot be matched to one booked session. Your current Review answer has not been changed.', status=409)
        # This is the moment the .vtt upload exists for, so it says so: Teams
        # holds no transcript for a meeting that was moved, not recorded, or
        # still processing, and the coach usually has the file already.
        return summary_error(
            code="teams_transcript_unavailable",
            detail=(
                "The Teams meeting was reached, but no transcript is available yet. "
                "Wait for Teams transcription to finish, upload the .vtt transcript, or enter the summary manually. "
                "Your current Review answer has not been changed."
            ),
            status=409,
        )

    generated = ensure_coach_meeting_summary(record, retry_failed=True)
    if not generated:
        return summary_error(
            code="meeting_summary_storage_unavailable",
            detail=(
                "The Teams transcript was found, but the generated summary could not be saved. "
                "Check the server logs and try again. Your current Review answer has not been changed."
            ),
            status=502,
        )
    if clean_text(generated.get("status")) == "failed":
        return summary_error(
            code="meeting_summary_ai_failed",
            detail=(
                "The Teams transcript was found, but the AI Meeting Summary could not be generated. "
                "Check the OpenAI API key, model access, quota, or server connection, then try again. "
                "Your current Review answer has not been changed."
            ),
            status=502,
        )
    return JsonResponse({"meetingSummarySource": _review_instance_meeting_summary_source(instance_row, definition)})


@coach_access_required
def coach_review_instance_progress(request, instance_id):
    """Calculate (or recalculate) and FREEZE this Progress Review's learner
    progress figures.

    Explicit action only. Nothing recalculates on open, reload, completion or
    PDF download -- a stored snapshot is returned as it was stored, and only
    this endpoint ever replaces it.

    The target cutoff is this server's one timezone-aware clock reading now.
    The learner start remains snapshot metadata; target inclusion itself comes
    from the resolved plan's authoritative curriculum week dates. A timestamp,
    target date, or scheduled date sent by the browser is never used.

    Authorization is the same gate every other review-instance endpoint uses:
    the assigned coach only, via _authorized_review_instance.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error

    definition = curriculum_review_instances.review_instance_form_definition(instance_row)
    if definition["template"].get("reviewTypeCode") != curriculum_review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW:
        # Identified by the Review Type's stable code, never by the template's
        # name -- another Review Type simply has no progress snapshot.
        return JsonResponse({"detail": "Calculating progress is only available on a Progress Review."}, status=404)

    learner_profile = LearnerProfile.objects.filter(pk=instance_row.get("learner_id")).first()
    if learner_profile is None:
        return JsonResponse({"detail": "This review's learner record could not be found."}, status=404)

    commercial_rows, enrolment_rows = fetch_source_schedule_rows([learner_profile])
    learner_start_date, anchor_reason = resolve_review_anchor_date(
        int(learner_profile.id), commercial_rows, enrolment_rows,
    )
    if learner_start_date is None:
        # Never fall back to a cohort/group/programme date: an absent
        # learner-specific start date means this cannot be calculated at all.
        return JsonResponse({
            "detail": "This learner has no individual programme start date, so progress cannot be calculated.",
            "errors": {"learnerStartDate": [anchor_reason or REVIEW_ANCHOR_MISSING_START]},
        }, status=409)

    source = commercial_rows.get(int(learner_profile.id)) or enrolment_rows.get(int(learner_profile.id))
    owner_email = authenticated_coach_email(request)
    try:
        snapshot = build_progress_snapshot(
            source or learner_profile, learner_profile,
            learner_start_date=learner_start_date,
            # The authoritative calculated_at: this server's clock, taken here.
            calculated_at=timezone.now(),
            calculated_by=owner_email,
        )
        curriculum_review_instances.save_review_instance_progress_snapshot(
            instance_row, snapshot, actor=owner_email,
        )
    except UnresolvedTrainingPlanTarget as exc:
        return JsonResponse({
            "detail": str(exc),
            "errors": {"targetSchedule": exc.calculation.get("unresolvedReasons") or []},
        }, status=409)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=409)
    except DatabaseError:
        logger.warning("Progress Review snapshot failed for instance %s", instance_id, exc_info=True)
        return JsonResponse({"detail": "Progress could not be calculated. Please try again."}, status=503)

    return JsonResponse(
        _coach_review_instance_definition(
            curriculum_review_instances.get_review_instance(instance_row["id"]),
        )
    )


@coach_access_required
def coach_review_instance_complete(request, instance_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    owner_email = authenticated_coach_email(request)
    if instance_id.startswith("imported-review:"):
        owner_email = owner_email.strip().casefold()
        definition = _imported_review_definition(owner_email, instance_id)
        if not definition:
            return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
        if definition.get("readOnly"):
            return JsonResponse({"detail": "This imported review is read-only."}, status=409)
        payload = {}
        if request.body:
            try:
                payload = parse_json_body(request)
            except ValidationError as exc:
                return validation_error_response(exc)
        if not isinstance(payload, dict):
            return JsonResponse({"detail": "Request body must be a JSON object."}, status=400)
        answers = payload.get("answers", {})
        if not isinstance(answers, dict):
            return JsonResponse({"detail": "answers must be an object keyed by field id."}, status=400)
        if definition.get("migratedForm"):
            with transaction.atomic():
                imported = _owned_migrated_overlay(owner_email, definition, lock=True, lock_calendar=True)
                if not imported or imported.status != ImportedReviewInstance.STATUS_IN_PROGRESS:
                    return JsonResponse({"detail": "Only an in-progress migrated review can be submitted."}, status=409)
                final_answers = payload.get("answers", imported.answers)
                from coach_api.migrated_summary_binding import AnswerConflict, check_answer_version, record_answer_edit
                try:
                    from coach_api.migrated_completion import submit
                    check_answer_version(imported, payload)
                    record_answer_edit(imported, final_answers, actor=owner_email,
                                       edited_fields=payload.get("editedFields") if isinstance(payload.get("editedFields"), list) else ())
                    submit(imported, final_answers)
                except AnswerConflict as exc:
                    return JsonResponse({"detail": str(exc), "code": "ANSWER_CONFLICT"}, status=409)
                except ValueError as exc:
                    return JsonResponse({"detail": str(exc)}, status=400)
                imported.save(update_fields=["meeting_intelligence"])
                from coach_api.migrated_completion_views import _mirror_status
                _mirror_status(imported)
            return JsonResponse(_imported_review_definition(owner_email, imported.event_key))
        field_ids = {
            field["id"]
            for section in definition.get("sections", [])
            for field in section.get("fields", [])
        }
        if any(field_id not in field_ids for field_id in answers):
            return JsonResponse({"detail": "answers contains a field that is not part of this review."}, status=400)
        imported, _created = ImportedReviewInstance.objects.get_or_create(
            owner_email=owner_email,
            event_key=definition["instance"]["id"],
            defaults={"learner_id": definition["instance"]["learnerId"]},
        )
        if imported.status == ImportedReviewInstance.STATUS_COMPLETED:
            return JsonResponse({"detail": "This review has already been completed."}, status=409)
        imported.answers = answers
        imported.status = ImportedReviewInstance.STATUS_COMPLETED
        imported.completed_at = timezone.now()
        imported.save(update_fields=["answers", "status", "completed_at", "updated_at"])
        return JsonResponse(_imported_review_definition(owner_email, imported.event_key))

    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    payload = {}
    if request.body:
        try:
            payload = parse_json_body(request)
        except ValidationError as exc:
            return validation_error_response(exc)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "Request body must be a JSON object."}, status=400)
    answers = payload.get("answers")
    if answers is not None and not isinstance(answers, dict):
        return JsonResponse({"detail": "answers must be an object keyed by field id."}, status=400)
    ok, errors = curriculum_review_instances.complete_review_instance(
        instance_row, actor=owner_email, answers=answers,
    )
    if not ok:
        # errors is {'status': [...]} for an invalid lifecycle transition (not
        # yet in-progress, or already submitted/completed) or {'fields': [...]}
        # for unanswered required questions -- surface whichever one applies
        # as the headline message so the coach sees why, not a generic string.
        detail = (errors or {}).get("status", ["This review cannot be completed yet."])[0]
        return JsonResponse({"detail": detail, "errors": errors}, status=400)
    instance_row = curriculum_review_instances.get_review_instance(instance_row["id"])
    _sync_calendar_record_to_review_instance_status(instance_row)
    return JsonResponse(_coach_review_instance_definition(instance_row))


@coach_access_required
@attributed_write_view
def coach_review_instance_mark_in_progress_manually(request, instance_id):
    """Authorised fallback: scheduled -> in-progress when Teams attendance
    cannot be detected (Graph outage, delayed report, meeting held outside
    Teams, scheduler failure, etc). See
    curriculum_api.review_instances.mark_review_instance_in_progress_manually
    for the lifecycle/data rules this enforces; this view is only
    authorization + request parsing + actor attribution.

    Reachable by the assigned coach, or by a super-admin who has opened that
    coach's workspace via viewAsCoach -- coach_access_required already
    refuses an unrelated coach or a super-admin with no coach selected, and
    _authorized_review_instance below refuses a coach whose own email does
    not own this instance. @attributed_write_view is what lets the super-
    admin write here at all (view-as is otherwise read-only); the actor
    string below is what keeps that write correctly attributed rather than
    silently recorded as the coach whose workspace happens to be open.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    try:
        payload = parse_json_body(request)
    except ValidationError as exc:
        return validation_error_response(exc)

    owner_email = authenticated_coach_email(request)
    actor = owner_email
    if is_coach_view_as(request):
        admin = getattr(request, "coach_view_as_admin", None)
        admin_identity = clean_text(getattr(admin, "username", "") or getattr(admin, "email", "")) or "Administrator"
        actor = f"{admin_identity} (for {owner_email})"

    reason_code = clean_text(payload.get("reasonCode"))
    note = clean_text(payload.get("note"))
    started_at_raw = clean_text(payload.get("startedAt"))
    started_at = None
    if started_at_raw:
        started_at = parse_graph_datetime(started_at_raw)
        if started_at is None:
            return JsonResponse({"detail": "startedAt must be a valid date/time.", "errors": {"startedAt": ["Invalid date/time."]}}, status=400)
        if started_at.tzinfo is not None:
            started_at = started_at.astimezone(timezone.UTC).replace(tzinfo=None)

    ok, result = curriculum_review_instances.mark_review_instance_in_progress_manually(
        instance_row, reason_code=reason_code, note=note, started_at=started_at, actor=actor,
    )
    if not ok:
        detail = next(iter(result.values()))[0] if result else "This review cannot be marked in progress."
        return JsonResponse({"detail": detail, "errors": result}, status=400)

    updated_instance = curriculum_review_instances.get_review_instance(instance_row["id"])
    _sync_calendar_record_to_review_instance_status(updated_instance)
    return JsonResponse(_coach_review_instance_definition(updated_instance))


@coach_access_required
@attributed_write_view
def coach_review_instance_reopen(request, instance_id):
    """Reopen a completed/awaiting-signature review so the coach can correct it.

    See curriculum_api.review_instances.reopen_review_instance_for_editing for
    the lifecycle/data rules this enforces -- including that every signature
    already collected is cleared, with the pre-reopen answers and signatures
    frozen into curriculum.review_instance_reopens first. This view is only
    authorization + request parsing + actor attribution.

    Same authorization model as coach_review_instance_mark_in_progress_manually
    above: coach_access_required refuses an unrelated coach or a super-admin
    with no coach selected, _authorized_review_instance refuses a coach whose
    own email does not own this instance, and @attributed_write_view is what
    lets a super-admin in view-as mode write here at all while keeping the
    write attributed to them rather than to the coach whose workspace is open.

    Unlike that view this does NOT call
    _sync_calendar_record_to_review_instance_status afterwards: the reopen
    already moved the linked Calendar row inside its own transaction (see
    _mirror_linked_calendar_after_reopen), and that helper only knows how to
    project completed/awaiting-signature forwards, so calling it here would be
    a second, competing writer.
    """
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    try:
        payload = parse_json_body(request)
    except ValidationError as exc:
        return validation_error_response(exc)

    owner_email = authenticated_coach_email(request)
    actor = owner_email
    if is_coach_view_as(request):
        admin = getattr(request, "coach_view_as_admin", None)
        admin_identity = clean_text(getattr(admin, "username", "") or getattr(admin, "email", "")) or "Administrator"
        actor = f"{admin_identity} (for {owner_email})"

    reason_code = clean_text(payload.get("reasonCode"))
    note = clean_text(payload.get("note"))

    try:
        ok, result = curriculum_review_instances.reopen_review_instance_for_editing(
            instance_row, reason_code=reason_code, note=note, actor=actor,
        )
    except ValueError as exc:
        # A Calendar row that no longer matches its instance -- reconciliation,
        # not user input. Same handling as the signature endpoint.
        return JsonResponse({"detail": str(exc)}, status=400)
    if not ok:
        detail = next(iter(result.values()))[0] if result else "This review cannot be reopened."
        return JsonResponse({"detail": detail, "errors": result}, status=400)

    updated_instance = curriculum_review_instances.get_review_instance(instance_row["id"])
    return JsonResponse(_coach_review_instance_definition(updated_instance))


@coach_access_required
def coach_review_instance_signature(request, instance_id):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)
    if instance_id.startswith("imported-review:"):
        definition = _imported_review_definition(authenticated_coach_email(request), instance_id)
        if not definition:
            return JsonResponse({"detail": "Imported review not found for this coach."}, status=404)
        return JsonResponse({"detail": "Imported review signatures cannot be changed here."}, status=409)
    instance_row, error = _authorized_review_instance(request, instance_id)
    if error:
        return error
    try:
        payload = parse_json_body(request)
    except ValidationError as exc:
        return validation_error_response(exc)
    role = clean_text(payload.get("role")).lower()
    if role != "advisor":
        return JsonResponse({"detail": "A coach may only sign as the coach."}, status=403)
    owner_email = authenticated_coach_email(request)
    try:
        result = curriculum_review_instances.record_review_instance_signature(
            instance_row, role,
            signed_by=owner_email, signed_name=clean_text(payload.get("signedName")),
            signature=clean_text(payload.get("signature")), actor=owner_email,
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    # No mirror call here: record_review_instance_signature owns the calendar
    # projection for every signature, in the same transaction as the status
    # change. One canonical transition, one mirror.
    return JsonResponse(_coach_review_instance_definition(
        curriculum_review_instances.get_review_instance(instance_row["id"])
    ))


def _sync_calendar_record_to_review_instance_status(instance_row: dict) -> None:
    """Mirrors a Review instance's status onto its linked CoachCalendarEvent
    row -- the existing calendar/status-filter UI reads CoachCalendarEvent,
    not review_instances, so this is not a second competing status source,
    only a projection of the one this instance already owns."""
    calendar_event_id = instance_row.get("calendar_event_id")
    if not calendar_event_id:
        return
    status = instance_row.get("status")
    if status == curriculum_review_instances.STATUS_COMPLETED:
        updated = CoachCalendarEvent.objects.filter(
            pk=calendar_event_id,
            status__in={
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
                CoachCalendarEvent.STATUS_COMPLETED,
            },
        ).update(
            status=CoachCalendarEvent.STATUS_COMPLETED, review_completed_at=instance_row.get("completed_at") or datetime.utcnow(),
        )
        if not updated:
            logger.warning(
                "review_calendar_status_mirror_rejected",
                extra={"calendar_event_id": calendar_event_id,
                       "review_instance_id": instance_row.get("id"),
                       "requested_status": status,
                       "reason": "incompatible_calendar_status_or_missing_row"},
            )
    elif status == curriculum_review_instances.STATUS_AWAITING_SIGNATURE:
        updated = CoachCalendarEvent.objects.filter(
            pk=calendar_event_id,
            status__in={
                CoachCalendarEvent.STATUS_IN_PROGRESS,
                CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            },
        ).update(status=CoachCalendarEvent.STATUS_AWAITING_SIGNATURE)
        if not updated:
            logger.warning(
                "review_calendar_status_mirror_rejected",
                extra={"calendar_event_id": calendar_event_id,
                       "review_instance_id": instance_row.get("id"),
                       "requested_status": status,
                       "reason": "incompatible_calendar_status_or_missing_row"},
            )
    elif status == curriculum_review_instances.STATUS_IN_PROGRESS:
        # Only ever reached via apply_teams_attendance_status_transition (a
        # real Teams join), never by this instance simply being opened/
        # edited. Guarded to the calendar row's own current status so this
        # mirror is monotonic on that side too -- it can never regress a
        # calendar row that has already moved on (e.g. completed through some
        # other path) back down to in-progress.
        updated = CoachCalendarEvent.objects.filter(
            pk=calendar_event_id, status=CoachCalendarEvent.STATUS_SCHEDULED,
        ).update(status=CoachCalendarEvent.STATUS_IN_PROGRESS)
        if not updated:
            logger.warning(
                "review_calendar_status_mirror_rejected",
                extra={"calendar_event_id": calendar_event_id,
                       "review_instance_id": instance_row.get("id"),
                       "requested_status": status,
                       "reason": "calendar_not_scheduled_or_missing_row"},
            )
