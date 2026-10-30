"""Learner calendar: source-owned Reviews with local coaching bookings.

Native Reviews use Curriculum occurrences and native instances. Aptem Reviews
use Learner.reviews with their existing calendar/form continuations. Stable
enrolment and Aptem identities select the source; missing imported rows never
generate Curriculum replacements. Other sessions retain their booking rules.
"""
import json
import logging
import hashlib
from datetime import date, datetime
from django.conf import settings

from django.db import DatabaseError, connection, connections
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from coach_api.models import CoachCalendarEvent, ImportedReviewInstance
from coach_api.booking_availability import CoachAvailabilityUnavailable
from coach_api.review_sources import (
    review_profile_for_source, source_for_learner, ReviewCreationBlocked, require_native_review_creation,
)

from .learner_detail import SOURCE_MODELS
from .identity import learner_profile_for_source
from .mappers import _s
from .models import EnrolmentReview, LearnerProfile, StaffUser
from .booking_calendar import booking_calendar_payload, booking_date_restriction, mcm_booking_date_restriction
# The first session's own vocabulary: its event type, and the college's today.
# Imported rather than restated so the gate, the booking and the calendar
# cannot disagree about either.
from .aptem_status import programme_status
from .constants import DEFAULT_PROGRAMME_STATUS, DELIVERY_PROGRAMME_STATUS
from .first_session import (
    SESSION_TYPE as FIRST_SESSION_TYPE,
    imported_from_aptem as first_session_imported_from_aptem,
    uk_today as first_session_uk_today,
)
from login.permissions import learner_self_or_staff
from .session_recovery import learner_change
from login.sessions import authenticate_request

logger = logging.getLogger(__name__)


def _calendar_profile(learner, pk):
    if source_for_learner(learner, None).kind == "aptem":
        profile = review_profile_for_source(learner)
        return profile if profile is None or profile.lifecycle_status == "active" else None
    return learner_profile_for_source(learner, pk, active_only=True)

EVENT_TITLES = {
    "mcr": "Monthly Coaching",
    "progress-review": "Progress Review",
    "review": "Review",
    "gateway": "Gateway",
    "other": "Other",
    "catch-up": "Catch-up Session",
    "recorded-recovery": "Watch Recording",
    "student-support": "Student Support",
    "first-session": "First Session",
    # Onboarding reviews (see ONBOARDING_REVIEW_LABELS below).
    "eligibility-review": "Eligibility Review & FS Discussion",
    "workspace": "RPL And Experience",
    "training-plan": "Workplace Health & Safety Declaration",
    "uln-privacy": "ULN Privacy Notice & Learner Acknowledgement",
    # Requested from the account invitation (login.lms_introduction).
    "lms-introduction": "LMS Introduction",
}

# JSON `type` vocabulary shared with the coach timetable frontend.
EVENT_JSON_TYPES = {
    "mcr": "coaching",
    "progress-review": "review",
    "review": "review",
    "gateway": "review",
    "other": "coaching",
    "catch-up": "coaching",
    "recorded-recovery": "coaching",
    "student-support": "welfare",
    "first-session": "coaching",
    "eligibility-review": "review",
    "workspace": "review",
    "training-plan": "review",
    "uln-privacy": "review",
}


def assigned_curriculum_module_ids(learner):
    """Return the learner's explicit curriculum-module assignment ids.

    Calendar visibility follows the same assignment query as My Learning and
    live lecture attendance.  It must not depend on the profile's current
    group label: a learner can remain assigned to a module while a cohort/group
    correction is being made, and hiding that module would hide its Teams link.
    """
    learner_id = getattr(learner, "id", None) or getattr(learner, "pk", None)
    if learner_id in (None, ""):
        return []
    try:
        from .student_activity import CURRENT_SUBJECTS_SQL

        with connections['enrolment'].cursor() as cursor:
            cursor.execute(CURRENT_SUBJECTS_SQL, [learner_id])
            return sorted({str(row[0]).strip() for row in cursor.fetchall() if row and str(row[0]).strip()})
    except DatabaseError:
        logger.warning("learner_calendar: module assignment lookup failed", exc_info=True)
        return []

# What a learner can book for themselves. Monthly coaching and progress reviews
# must be booked against a generated programme-cycle eventKey so the learner and
# coach see the same official calendar row.
BOOKABLE_TYPES = ("catch-up", "student-support", "first-session", "mcr", "progress-review", "review", "gateway", "other")

# Catch-ups use the learner-selected slot immediately. These are the session
# types whose learner-created bookings remain requests for coach placement.
COACH_APPROVAL_TYPES = frozenset(("student-support", "gateway", "other"))

# The Microsoft Graph invite subject uses the same wording as the page — see
# coach_api.BOOKED_EVENT_TITLES, which mirrors EVENT_TITLES above.

# The four onboarding reviews a learner books straight after submitting their
# enrolment. Unlike catch-up/student-support these are bookable *while still
# Onboarding* — they are the meetings that get the learner enrolled, so requiring
# an Active mirror + assigned coach first would be circular. They are booked with
# the learner's case owner (the enrolment officer) instead.
ONBOARDING_REVIEW_TYPES = ("eligibility-review", "workspace", "training-plan", "uln-privacy")
REVIEW_BOOKING_TYPES = frozenset(("mcr", "progress-review", "review", *ONBOARDING_REVIEW_TYPES))

# Same wording the calendar shows for these events.
ONBOARDING_REVIEW_LABELS = {type_: EVENT_TITLES[type_] for type_ in ONBOARDING_REVIEW_TYPES}

# Reviews that have a form to fill in after booking -- all four do; the
# panels each one renders live in review_form.SECTIONS_BY_REVIEW.
REVIEW_FORM_TYPES = ONBOARDING_REVIEW_TYPES

# Only learner-booked sessions can be cancelled by the learner -- the generated
# mcr / progress-review events belong to the coach's schedule.
CANCELLABLE_TYPES = (*BOOKABLE_TYPES, *ONBOARDING_REVIEW_TYPES)


def _error(message, status, *, code=None):
    return JsonResponse({"error": message, **({"code": code} if code else {})}, status=status)


def _friendly_sync_warning(warning):
    """Turn a raw Microsoft Graph failure into something a learner can act on.

    The underlying strings are diagnostics ("getaddrinfo failed",
    "ErrorInvalidUser") and mean nothing to a learner, but the outcome always
    does: the slot is held, yet no invite reached anyone.
    """
    raw = _s(warning)
    if not raw:
        return ""
    lowered = raw.lower()
    if "getaddrinfo" in lowered or "urlopen error" in lowered or "not configured" in lowered:
        detail = "the booking system could not reach Microsoft"
    elif "permissions" in lowered or "accessdenied" in lowered or "access is denied" in lowered:
        detail = "the booking application does not have permission to access the organiser calendar; your Microsoft 365 administrator needs to grant access"
    elif "errorinvaliduser" in lowered or "invalid" in lowered and "user" in lowered:
        detail = "the organiser mailbox is not set up for calendar invites"
    else:
        detail = "Microsoft rejected the calendar invite"
    return (
        f"Your slot is saved, but no calendar invite or email was sent because {detail}. "
        "Please let your programme team know so they can confirm this booking."
    )


def _case_owner_contact(learner):
    """(email, name) of the learner's case owner, for onboarding review bookings.

    Case_owner stores the staff member's *name*, so the email is resolved from
    enrolment."Staff_users". Returns ('', '') when unset or unresolvable — the
    caller turns that into a clear "no case owner assigned yet" message.
    """
    email, name, _staff_id = _case_owner_record(learner)
    return email, name


def _case_owner_record(learner):
    """(email, name, staff_id) of the case owner — staff_id for Enrolment_Reviews."""
    owner_name = _s(getattr(learner, "case_owner", ""))
    if not owner_name:
        return "", "", None
    try:
        staff = (
            StaffUser.objects.filter(username__iexact=owner_name)
            .exclude(email__isnull=True)
            .exclude(email="")
            .first()
        )
    except DatabaseError:
        logger.exception("_case_owner_contact: staff lookup failed")
        return "", owner_name, None
    if staff is None:
        return "", owner_name, None
    return _s(staff.email), _s(staff.username) or owner_name, staff.pk


def _record_enrolment_review(record, *, kind, learner_kind_id, coach_id):
    """Mirror a booked review into enrolment."Enrolment_Reviews".

    Best-effort: the coach_calendar_event row is the operational source of truth
    for the calendar, so a failure here is logged but never fails the booking —
    the learner has a real meeting either way.

    Keyed on Event_key so re-booking the same slot updates rather than duplicates.
    """
    if _s(record.event_type) not in ONBOARDING_REVIEW_TYPES:
        return
    try:
        defaults = {
            "review_type": _s(record.event_type),
            "review_label": ONBOARDING_REVIEW_LABELS.get(_s(record.event_type), ""),
            "learner_kind": kind,
            "learner_id": learner_kind_id,
            "learner_name": _s(record.learner_name),
            "learner_email": _s(record.learner_email),
            "coach_name": _s(record.owner_name),
            "coach_email": _s(record.owner_email),
            "scheduled_date": record.scheduled_date,
            "scheduled_time": record.scheduled_time,
            "duration_minutes": record.duration_minutes or 60,
            "status": EnrolmentReview.STATUS_BOOKED,
            "notes": _s(record.notes),
            "meeting_provider": _s(record.meeting_provider),
            "meeting_link": _s(record.meeting_link) or _s(record.graph_web_link),
            "graph_event_id": _s(record.graph_event_id),
            "invite_sent": bool(_s(record.graph_event_id)),
            "sync_error": _s(record.last_graph_sync_error),
            "booked_at": timezone.now(),
            "cancelled_at": None,
        }
        if coach_id is not None:
            defaults["coach_id"] = coach_id
        EnrolmentReview.objects.update_or_create(
            event_key=record.event_key,
            defaults=defaults,
        )
    except DatabaseError:
        logger.exception(
            "_record_enrolment_review: could not save %s to Enrolment_Reviews", record.event_key
        )


def _cancel_enrolment_review(record):
    """Mark the Enrolment_Reviews row cancelled. Best-effort, as above."""
    if _s(record.event_type) not in ONBOARDING_REVIEW_TYPES:
        return
    try:
        EnrolmentReview.objects.filter(event_key=record.event_key).update(
            status=EnrolmentReview.STATUS_CANCELLED,
            cancelled_at=timezone.now(),
            updated_at=timezone.now(),
            meeting_provider="",
            meeting_link="",
            graph_event_id="",
            invite_sent=False,
        )
    except DatabaseError:
        logger.exception(
            "_cancel_enrolment_review: could not update %s in Enrolment_Reviews", record.event_key
        )


def _belongs_to_current_cycle(record, mirror):
    """Whether a stored row is part of *this* learner's coaching cycle.

    Rows are matched by email as well as by mirror id, because a learner's
    mirror is recreated on occasion and their bookings must survive it. That
    generosity has one bad case: a monthly-coaching or progress-review slot
    generated against a *previous* mirror stays behind when that mirror is
    deleted, and its dates came from a window the learner no longer has. The
    coach never sees those — their timetable only builds keys from live
    caseload profiles — so a learner shown them is reading dates their coach
    cannot see.

    Only the generated cycle is filtered. A catch-up, a student-support session,
    an onboarding review, or a cycle-type meeting explicitly booked by the
    learner is something somebody actually arranged and must survive a mirror
    change. Learner bookings are identifiable by their durable idempotency key;
    unlike coach-generated slots, they currently store the source learner id.
    """
    if mirror is None:
        return True
    if _s(record.event_type) not in ("mcr", "progress-review", "review"):
        return True
    if _s(getattr(record, "idempotency_key", "")).startswith("learner-book:"):
        return True
    return str(record.learner_id or "") in ("", str(mirror.id))


def _generated_cycle_events(learner, mirror, stored_by_key, *, month=None):
    """Generate the same Curriculum cycle the coach timetable exposes.

    Curriculum review_templates are the only source of truth for MCM/Progress
    Review occurrence dates. A learner with no resolvable Curriculum programme
    mapping, or a programme with no configured template, produces zero
    occurrences here -- never a fixed-interval fallback (see
    coach_api.views.collect_generated_timetable, which this mirrors).
    """
    from coach_api.models import CoachCalendarEvent
    from coach_api.views import (
        resolve_curriculum_programme_id,
        resolve_curriculum_review_occurrences,
        resolve_review_anchor_date,
        log_review_anchor_skip,
        resolve_schedule_window,
        review_event_type_for_type_code,
        review_type_event_fields,
    )
    from curriculum_api.review_instances import review_calendar_event_key

    if mirror is None or source_for_learner(learner, mirror).kind != "curriculum":
        return []

    programme_id = resolve_curriculum_programme_id(
        getattr(mirror, 'programme_id', None) or getattr(mirror, 'programme', None),
    )
    if not programme_id:
        return []

    source_rows = {mirror.id: learner} if learner is not None else {}
    anchor, reason = resolve_review_anchor_date(mirror.id, {}, source_rows)
    if anchor is None:
        log_review_anchor_skip(mirror, reason, programme_id=programme_id, template_cache={})
        return []
    start_date, end_date = resolve_schedule_window(mirror.id, {}, source_rows, mirror)
    if not start_date or not end_date or end_date <= start_date:
        return []
    if month:
        from calendar import monthrange
        first = date.fromisoformat(month + '-01')
        last = first.replace(day=monthrange(first.year, first.month)[1])
        start_date, end_date = max(start_date, first), min(end_date, last)
        if end_date < start_date:
            return []
    generated = []
    for occurrence in resolve_curriculum_review_occurrences(
        programme_id=programme_id,
        learner_id=mirror.id,
        learner_status=_s(
            getattr(mirror, 'programme_status', None)
            or getattr(mirror, 'status', None)
            or getattr(learner, 'programme_status', None)
            or getattr(learner, 'status', None)
        ),
        learner_start_date=anchor,
        window_start=start_date,
        window_end=end_date,
        template_cache={},
        learner_scope={
            'cohort_id': getattr(mirror, 'cohort_id', None),
            'cohort': getattr(mirror, 'cohort', None),
            'group_id': getattr(mirror, 'group_id', None),
            'group': getattr(mirror, 'group_name', None),
        },
    ):
        sequence, target_date = occurrence['occurrenceNumber'], occurrence['targetDate']
        event_type = review_event_type_for_type_code(occurrence.get('reviewTypeCode'))
        event_key = review_calendar_event_key(mirror.id, occurrence['reviewTemplateId'], sequence)
        if event_key in stored_by_key:
            continue
        generated.append({
            "id": event_key,
            "eventKey": event_key,
            "learnerId": str(mirror.id),
            "title": occurrence.get('reviewName') or EVENT_TITLES.get(event_type, 'Review'),
            "reviewTemplateId": occurrence['reviewTemplateId'],
            "reviewInstanceId": None,
            "occurrenceNumber": sequence,
            **review_type_event_fields(occurrence),
            "source": event_type,
            "type": EVENT_JSON_TYPES.get(event_type, "coaching"),
            "sequence": sequence,
            "status": CoachCalendarEvent.STATUS_NOT_SCHEDULED,
            "date": target_date.isoformat(),
            "targetDate": target_date.isoformat(),
            "scheduledDate": None,
            "scheduledTime": None,
            "durationMinutes": 60,
            "coachName": _s(mirror.coach_name),
            "coachEmail": _s(mirror.coach_email),
            "meetingProvider": "",
            "meetingLink": "",
            "notes": "",
            "reviewResponses": {},
            "reviewCompletedAt": None,
            "invited": False,
            "syncError": "",
            "isTimeEstimated": True,
            "generated": True,
        })
    return generated


def review_type_rows_by_template(template_ids):
    from curriculum_api import reviews, review_types
    wanted = sorted({_s(value) for value in template_ids if _s(value)})
    if not wanted:
        return {}
    rows = reviews.get_review_template_rows(
        f"id in ({', '.join(['%s'] * len(wanted))})", wanted, include_deleted=True,
    )
    types = review_types.review_type_index()
    return {row['id']: types[row['review_type_id']] for row in rows if row.get('review_type_id') in types}


def _serialize_event(record, *, review_types_by_template=None, templates_by_id=None, compact=False, include_meeting_link=False):
    """Shape one coach_calendar_event row for the learner calendar page.

    Mirrors the field names the coach timetable JSON uses (scheduledDate,
    scheduledTime, durationMinutes, ...) so the two calendars stay in sync.
    """
    event_type = _s(record.event_type) or "mcr"
    from coach_api.views import review_event_type_for_type_code, review_type_event_fields
    from curriculum_api import reviews
    template_id = _s(getattr(record, 'review_template_id', ''))
    if review_types_by_template is None:
        review_types_by_template = review_type_rows_by_template([template_id])
    type_row = review_types_by_template.get(template_id) or {}
    if template_id:
        event_type = review_event_type_for_type_code(type_row.get('code'))
    template = (templates_by_id or {}).get(template_id)
    if template_id and templates_by_id is None:
        template = reviews.get_review_template_row(template_id, include_deleted=True)
    from coach_api.booking_reservations import review_occupies_slot
    display_date = record.scheduled_date or record.target_date
    if compact:
        return {'id': record.event_key, 'eventKey': record.event_key,
                'title': (template or {}).get('name') or EVENT_TITLES.get(event_type, 'Coaching Session'),
                'source': event_type, 'type': EVENT_JSON_TYPES.get(event_type, 'coaching'),
                'status': record.status, 'bookingReserved': review_occupies_slot(record),
                'date': display_date.isoformat() if display_date else None,
                'targetDate': record.target_date.isoformat() if record.target_date else None,
                'scheduledDate': record.scheduled_date.isoformat() if record.scheduled_date else None,
                'scheduledTime': record.scheduled_time.strftime('%H:%M') if record.scheduled_time else None,
                  'durationMinutes': record.duration_minutes or 60,
                  **({'meetingLink': _s(record.meeting_link) or _s(record.graph_web_link)} if include_meeting_link else {})}
    meeting_link = _s(record.meeting_link) or _s(record.graph_web_link)
    from coach_api.views import public_graph_sync_warning
    sync_warning = public_graph_sync_warning(_s(record.last_graph_sync_error))
    booking_parts = _s(getattr(record, "idempotency_key", "")).split(":")
    imported_booking = (
        len(booking_parts) == 6 and booking_parts[0] == "learner-book"
        and booking_parts[1] == ("mcm" if event_type == "mcr" else "progress-review")
        and event_type in {"mcr", "progress-review"}
        and booking_parts[2] in SOURCE_MODELS and booking_parts[5].isdigit()
    )
    review_id = booking_parts[5] if imported_booking else ""
    assignment_month = booking_parts[4] if imported_booking else ""
    if event_type == "mcr" and record.event_key.startswith("imported-review:"):
        from coach_api.local_mcm_bookings import local_mcm_metadata
        local_review = local_mcm_metadata(record)
        if local_review:
            review_id, assignment_month = local_review
    migrated_form = (not compact and record.event_key.startswith("imported-review:")
                     and ImportedReviewInstance.objects.filter(
                         event_key=record.event_key, learner_id=record.learner_id,
                         source_review_id__isnull=False,
                     ).exists())
    return {
        "id": record.event_key,
        "eventKey": record.event_key,
        "title": (template or {}).get('name') or EVENT_TITLES.get(event_type, "Coaching Session"),
        "reviewTemplateId": template_id or None,
        "reviewInstanceId": _s(getattr(record, 'review_instance_id', '')) or None,
        "migratedForm": migrated_form,
        "occurrenceNumber": getattr(record, 'occurrence_number', None) or record.sequence,
        **review_type_event_fields({
            'reviewTypeId': type_row.get('id'), 'reviewTypeCode': type_row.get('code'),
            'reviewTypeName': type_row.get('name'), 'reviewTypeIsSystem': type_row.get('is_system'),
        }),
        "source": event_type,
        "type": EVENT_JSON_TYPES.get(event_type, "coaching"),
        "sequence": record.sequence,
        "status": record.status, "bookingReserved": review_occupies_slot(record),
        "date": display_date.isoformat() if display_date else None,
        "targetDate": record.target_date.isoformat() if record.target_date else None,
        "scheduledDate": record.scheduled_date.isoformat() if record.scheduled_date else None,
        "scheduledTime": record.scheduled_time.strftime("%H:%M") if record.scheduled_time else None,
        "durationMinutes": record.duration_minutes or 60,
        "coachName": _s(record.owner_name),
        "coachEmail": _s(record.owner_email),
        "meetingProvider": _s(record.meeting_provider),
        "meetingLink": meeting_link,
        "notes": _s(record.notes),
        "reviewResponses": record.review_responses if isinstance(record.review_responses, dict) else {},
        "reviewCompletedAt": record.review_completed_at.isoformat() if record.review_completed_at else None,
        "learnerSigned": bool(_s((record.review_responses or {}).get("learner_signature"))),
        "learnerSignedAt": _s((record.review_responses or {}).get("learner_signed_at")) or None,
        # A row can save while the Graph sync fails (no network, non-tenant
        # mailbox, ...). Without this the UI shows a confident "Booked" for a
        # meeting that reached nobody's calendar or inbox.
        "invited": bool(_s(record.graph_event_id)) or event_type == "recorded-recovery",
        "syncError": sync_warning,
        "syncState": getattr(record, "sync_state", ""),
        "syncWarning": _friendly_sync_warning(sync_warning) if sync_warning else "",
        "reviewId": review_id,
        "assignmentMonth": assignment_month,
        # Catch-up only: too close to the start for the learner to move or cancel it.
        "changeClosed": _catchup_change_closed(record),
    }


def _booking_response_event(record, imported_event=None):
    if not imported_event:
        return _serialize_event(record)
    from coach_api.review_sources import load_imported_links
    from coach_api.views import overlay_calendar_record
    _bookings, overlays = load_imported_links([imported_event], records=[record])
    return overlay_calendar_record(imported_event, record, overlays.get(imported_event['eventKey']))


def _mark_imported_review_scheduled(review_id, learner_profile_id, scheduled_date, scheduled_time):
    """Validate legacy callers without rewriting the imported source evidence.

    Effective booking state now comes from the linked calendar row. The Aptem
    planned date/status must survive local booking and rescheduling unchanged.
    """
    if not review_id:
        return
    try:
        review_pk = int(review_id)
    except (TypeError, ValueError):
        raise DatabaseError(f"Invalid imported review id {review_id!r}.")
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT id FROM "Learner".reviews
             WHERE id = %s
               AND learner_id = %s
               AND LOWER(BTRIM(review_type)) IN (%s, %s, %s, %s, %s)
            ''',
            [review_pk, learner_profile_id,
             "monthly coaching meeting", "monthly coaching", "mcm",
             "progress review", "progress review (+ skills radar)"],
        )
        if cursor.fetchone() is None:
            raise DatabaseError(
                f"Imported review {review_pk} was not found for learner profile {learner_profile_id} "
                "or is not a schedulable review."
            )


def _assignment_imported_mcm_month(profile_id, review_id):
    """Validate the Aptem MCM an assignment booking names; return its month.

    An Aptem-linked learner with imported Aptem MCMs gets no Curriculum MCMs
    on the coach timetable (coach_api.views.resolve_coach_review_events), so
    the imported row is the only MCM identity the coach can see. Only this
    learner's own, still-open Aptem MCM row may be booked.

    Returns (month or None, None) on success, or (None, (message, status)).
    """
    from .review_history import REVIEW_TYPES, _serialize_review

    try:
        review_pk = int(review_id)
    except (TypeError, ValueError):
        return None, ("reviewId must identify an imported Monthly Coaching Meeting.", 400)
    with connection.cursor() as cursor:
        cursor.execute(
            '''
            SELECT id, aptem_review_id, review_name, review_type, reviewer_name,
                   learner_name, planned_scheduled_date, completed_date, status,
                   review_data, extraction_status, last_error
              FROM "Learner".reviews
             WHERE id = %s
               AND learner_id = %s
               AND NULLIF(BTRIM(aptem_review_id), '') IS NOT NULL
               AND LOWER(BTRIM(review_type)) = ANY(%s)
            ''',
            [review_pk, profile_id, [value.casefold() for value in REVIEW_TYPES["monthly-coaching"]]],
        )
        columns = [column[0] for column in cursor.description]
        row = cursor.fetchone()
    if row is None:
        return None, ("This Monthly Coaching Meeting is not available for your assignment.", 404)
    review = _serialize_review(dict(zip(columns, row)), {})
    if review["completedDate"] or review["status"] in {
        CoachCalendarEvent.STATUS_COMPLETED,
        CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
        CoachCalendarEvent.STATUS_CANCELLED,
    }:
        return None, ("A submitted or completed review cannot be scheduled again.", 409)
    return (review["plannedDate"] or "")[:7] or None, None


def coaching_events_for_learner(learner, mirror, *, month=None, only_sources=None, dates_only=False, header_only=False):
    """One source for the calendar and Training Plan coaching dates/statuses.

    ``only_sources`` is for a caller that keeps only those event sources. It
    skips work whose events can never carry one of them; the caller still
    filters, and every returned event is built exactly as without it.
    ``dates_only`` (with ``only_sources``) is for a caller that reads only
    each event's identity, title, status and dates: imported Aptem reviews
    then omit their review history fields.
    """
    if source_for_learner(learner, mirror).kind != "curriculum":
        mirror = review_profile_for_source(learner, mirror)
    review_source = source_for_learner(learner, mirror)
    emails = {_s(getattr(learner, 'email', '')).strip().casefold(),
              _s(getattr(mirror, 'email', '')).strip().casefold()} - {''}
    match = Q()
    for email in emails:
        match |= Q(learner_email__iexact=email)
    if mirror is not None:
        match |= Q(learner_id=mirror.id) | Q(learner_id=learner.pk)
    if not match:
        return []
    records = []
    query = CoachCalendarEvent.objects.filter(match)
    if header_only:
        query = query.defer('review_responses', 'notes')
    if month:
        from calendar import monthrange
        first = date.fromisoformat(month + '-01')
        last = first.replace(day=monthrange(first.year, first.month)[1])
        # Include bookings moved away from a generated target so their original
        # placeholder is still suppressed by reconciliation.
        query = query.filter(Q(scheduled_date__range=(first, last)) | Q(target_date__range=(first, last)))
        query = query.filter(event_type__in=('mcr', 'progress-review', 'review'))
        query = query.defer('review_responses', 'notes')
    for record in query.order_by('target_date', 'event_type', 'sequence'):
        email = _s(getattr(record, 'learner_email', '')).strip().casefold()
        # Created_users and learner profiles have different ID sequences.
        # A matching number must never override another learner's email.
        expected_id = learner.pk if _s(getattr(record, 'idempotency_key', '')).startswith('learner-book:') else getattr(mirror, 'id', None)
        imported_owned = (review_source.kind == "aptem" and mirror is not None
                          and record.event_type in {"mcr", "progress-review"}
                          and str(record.learner_id) in {str(mirror.id), str(learner.pk)})
        belongs = imported_owned or (email in emails if email else expected_id is not None and str(record.learner_id) == str(expected_id))
        if belongs and _belongs_to_current_cycle(record, mirror):
            records.append(record)
    if review_source.kind == "aptem":
        from .imported_review_calendar import imported_events_for_learner
        if month:
            imported = imported_events_for_learner(learner, mirror, review_source.aptem_id, records, month=month)
        elif only_sources is not None:
            # Section text only feeds ``importedReview``; the narrowed caller drops it.
            options = {'compact_bookings': True} if header_only else {}
            imported = imported_events_for_learner(learner, mirror, review_source.aptem_id, records,
                include_sections=False, dates_only=dates_only or header_only, **options)
        else:
            imported = imported_events_for_learner(learner, mirror, review_source.aptem_id, records)

        def may_match(record):
            # A stored row's source is its event type, unless a review template
            # names the type (_serialize_event); keep those rows to be safe.
            return (only_sources is None or _s(getattr(record, 'review_template_id', ''))
                    or (_s(record.event_type) or "mcr") in only_sources)

        # Missing imported reviews never authorize Curriculum replacements.
        # Persisted native rows must not become a second occurrence or fallback.
        other = [] if month else [
            _serialize_event(record, compact=header_only, include_meeting_link=header_only)
            for record in records
            if record.event_type not in {"mcr", "progress-review", "review"} and may_match(record)
        ]
        return [*imported, *other]
    options = {'compact': True} if header_only else {}
    return _curriculum_cycle_events(learner, mirror, records, month=month, **options)


def _curriculum_cycle_events(learner, mirror, records, *, month=None, compact=False):
    """Generated Curriculum occurrences reconciled with their saved bookings."""
    from curriculum_api import reviews, review_instances
    generated = (_generated_cycle_events(learner, mirror, set(), month=month)
                 if month else _generated_cycle_events(learner, mirror, set()))
    matched = review_instances.reconcile_review_event_keys(generated, records)
    # Generated occurrences already carry the current template title and Review
    # Type metadata. Reuse it for stored rows from the same templates instead
    # of reading review_templates and review_types again.
    templates = {
        _s(event.get('reviewTemplateId')): {
            'id': _s(event.get('reviewTemplateId')),
            'name': event.get('title') or '',
        }
        for event in generated if _s(event.get('reviewTemplateId'))
    }
    types = {
        _s(event.get('reviewTemplateId')): {
            'id': event.get('reviewTypeId'),
            'code': event.get('reviewTypeCode'),
            'name': event.get('reviewTypeName'),
            'is_system': event.get('reviewTypeIsSystem'),
        }
        for event in generated if _s(event.get('reviewTemplateId'))
    }
    template_ids = sorted({_s(getattr(record, 'review_template_id', '')) for record in records} - {''})
    unresolved_template_ids = [template_id for template_id in template_ids if template_id not in templates]
    if unresolved_template_ids:
        templates.update({row['id']: row for row in reviews.get_review_template_rows(
            f"id in ({', '.join(['%s'] * len(unresolved_template_ids))})",
            unresolved_template_ids, include_deleted=True,
        )})
        types.update(review_type_rows_by_template(unresolved_template_ids))
    events = []
    for event in generated:
        record = matched.get(event['eventKey'])
        if record:
            stored = _serialize_event(record, review_types_by_template=types, templates_by_id=templates, compact=bool(month) or compact, include_meeting_link=compact)
            for field in ('title', 'source', 'type', 'reviewTemplateId', 'occurrenceNumber', 'reviewTypeId', 'reviewTypeCode', 'reviewTypeName', 'reviewTypeIsSystem'):
                # Legacy cycles have no Curriculum review metadata. Preserve
                # the serialized booking fields when the generator omits them.
                if field in event:
                    stored[field] = event[field]
            events.append(stored)
        else:
            events.append(event)
    for record in records:
        if record.event_key in matched:
            continue
        is_review_placeholder = bool(getattr(record, 'review_template_id', '')) or (
            record.event_type in {'mcr', 'progress-review', 'review'}
            and not _s(getattr(record, 'idempotency_key', '')).startswith('learner-book:')
        )
        if is_review_placeholder and record.status == CoachCalendarEvent.STATUS_NOT_SCHEDULED:
            continue
        events.append(_serialize_event(record, review_types_by_template=types, templates_by_id=templates, compact=bool(month) or compact, include_meeting_link=compact))
    return events


def alternative_recovery_events_for_learner(learner_id):
    """Expose approved cross-group recovery occurrences on the learner calendar."""
    from coach_api.models import CoachAbsenceReport
    from .alternative_recovery import ALTERNATIVE_METHOD, alternative_target_details

    events = []
    reports = CoachAbsenceReport.objects.filter(
        learner_id=learner_id,
        status=CoachAbsenceReport.STATUS_APPROVED,
        recovery_method=ALTERNATIVE_METHOD,
    )
    for report in reports:
        target = alternative_target_details(report.catchup_event_key, include_join_url=True)
        if not target:
            continue
        try:
            start = datetime.fromisoformat(f"{target['dateIso']}T{target['startTime']}")
            end = datetime.fromisoformat(f"{target['dateIso']}T{target['endTime']}") if target.get('endTime') else None
            duration = max(1, int((end - start).total_seconds() // 60)) if end else 60
        except (KeyError, TypeError, ValueError):
            continue
        key = f"absence-alternative:{report.id}"
        events.append({
            "id": key,
            "eventKey": key,
            "title": target.get("title") or report.session_title,
            "source": "live-session",
            "type": "live-session",
            "sequence": 1,
            "status": CoachCalendarEvent.STATUS_SCHEDULED,
            "date": target["dateIso"],
            "targetDate": target["dateIso"],
            "scheduledDate": target["dateIso"],
            "scheduledTime": target["startTime"],
            "durationMinutes": duration,
            "coachName": "",
            "coachEmail": report.owner_email,
            "meetingProvider": "Microsoft Teams" if target.get("joinUrl") else "",
            "meetingLink": target.get("joinUrl") or "",
            "notes": "Alternative group session for an approved absence recovery plan.",
            "invited": True,
            "syncError": "",
            "syncState": CoachCalendarEvent.SYNC_SYNCED,
            "module": target.get("module") or report.session_title,
            "cohort": target.get("cohort") or "",
            "group": target.get("group") or "",
        })
    return events


def _resolve_direct_cycle_event_key(learner, mirror, session_type):
    """Resolve the single official, not-yet-booked Curriculum occurrence for a
    generic MCM/PR request that arrived without an eventKey (the learner used
    the general booking picker instead of an official calendar card).

    Reuses coaching_events_for_learner -- the same per-learner occurrence
    resolver the official calendar cards are built from -- instead of
    re-deriving Curriculum recurrence here, so this can never disagree with
    what the learner's calendar actually shows.

    Returns (event_key, None) on exactly one unambiguous match, or
    (None, (message, status)) otherwise. The caller must turn that into an
    error response rather than falling back to an unlinked booking: a review
    session must never reach reserve_coach_calendar_booking, which has no
    concept of Curriculum linkage at all.
    """
    unresolved = [
        event for event in coaching_events_for_learner(learner, mirror)
        if event.get('source') == session_type
        and event.get('status') == CoachCalendarEvent.STATUS_NOT_SCHEDULED
        and _s(event.get('reviewTemplateId'))
    ]
    if not unresolved:
        label = 'Monthly Coaching Meeting' if session_type == 'mcr' else 'Progress Review'
        return None, (
            f"No official {label} occurrence is currently due for scheduling. "
            "Contact your coach if you believe this is incorrect.",
            404,
        )
    # A Review's recurrence is generated many occurrences ahead, so a learner
    # can have several future not-yet-scheduled slots for the SAME template --
    # only the soonest of those is actually due. Ambiguity only arises when
    # more than one *template* (e.g. two enabled MCM Reviews on the same
    # programme) each have their own occurrence due at once.
    earliest_per_template: dict[str, dict] = {}
    for event in sorted(unresolved, key=lambda e: _s(e.get('targetDate'))):
        template_id = _s(event.get('reviewTemplateId'))
        earliest_per_template.setdefault(template_id, event)
    candidates = list(earliest_per_template.values())
    if len(candidates) > 1:
        return None, (
            "More than one official review occurrence is available to schedule. "
            "Open the specific occurrence from your calendar to schedule it.",
            409,
        )
    return candidates[0]['eventKey'], None


def _resolve_assignment_month_mcm_occurrence(learner, mirror):
    """The canonical Curriculum MCM occurrence an assignment_month booking
    should attach to.

    assignment_month is only an eligibility/date-window gate -- the caller
    separately checks the chosen scheduled_date against
    monthly_assignment.coaching_booking_bounds(assignment_month). It must
    never be allowed to invent the Review's own identity: Curriculum's
    review_templates/review_instances are the only source of truth for which
    MCM this is and when it is due (see the module docstring's architecture
    diagram). Before this function existed, an assignment_month request with
    no eventKey fell through to reserve_coach_calendar_booking exactly like
    the (now-guarded) generic direct-cycle-request path used to, creating a
    standalone event_type='mcr' row with review_template_id/review_instance_id
    left blank.

    Returns (event_key, None) on an unambiguous resolution, or
    (None, (message, status)) otherwise. The caller must turn that into an
    error response rather than falling back to an unlinked booking.
    """
    from coach_api.views import resolve_curriculum_programme_id, resolve_review_anchor_date
    from curriculum_api import review_types as curriculum_review_types
    from curriculum_api.review_instances import list_enabled_review_templates

    if mirror is None:
        return None, ("Only Active learners can book programme-cycle sessions.", 400)

    programme_id = resolve_curriculum_programme_id(
        getattr(mirror, 'programme_id', None) or getattr(mirror, 'programme', None),
    )
    if not programme_id:
        return None, ("No Monthly Coaching Meeting is configured for this learner's programme.", 422)

    mcm_type = curriculum_review_types.get_review_type_by_code(curriculum_review_types.REVIEW_TYPE_CODE_MCM)
    mcm_type_id = _s((mcm_type or {}).get('id'))
    mcm_templates = [
        template for template in list_enabled_review_templates(programme_id)
        if mcm_type_id and _s(template.get('review_type_id')) == mcm_type_id
    ]
    if not mcm_templates:
        return None, ("No Monthly Coaching Meeting is configured for this learner's programme.", 422)
    if len(mcm_templates) > 1:
        return None, (
            "More than one Monthly Coaching Meeting is configured for this learner's programme. "
            "Ask your programme team to resolve this before booking.",
            409,
        )

    # STRICT anchor: enrolment."Created_users"."Learner_start_date" only,
    # never Start_date, the profile mirror, or any other fallback -- same
    # rule resolve_review_anchor_date enforces everywhere else.
    source_rows = {mirror.id: learner} if learner is not None else {}
    anchor, _reason = resolve_review_anchor_date(mirror.id, {}, source_rows)
    if anchor is None:
        return None, ("Cannot schedule this review because the learner start date is missing.", 422)

    # Template configuration and the anchor are both confirmed -- the
    # remaining question ("which occurrence, of possibly several generated
    # ahead of time, is actually due") is exactly what the generic MCM
    # request already answers. Reuse it rather than re-deriving the same
    # "soonest not-yet-scheduled occurrence" rule a second time.
    return _resolve_direct_cycle_event_key(learner, mirror, "mcr")


def _resolve_assignment_month_progress_review_occurrence(learner, mirror):
    """The canonical Curriculum Progress Review occurrence an assignment_month
    booking should attach to (see _resolve_assignment_month_mcm_occurrence,
    which this mirrors).

    Unlike MCM, assignment_month carries no eligibility/date-window rule for
    Progress Review -- it is only bookkeeping context copied from the
    imported Aptem review (which month it came from), never a booking-window
    gate and never the Review's own identity. That identity, and which
    occurrence is actually due, comes from Curriculum's review_templates /
    review_instances exactly as it does for every other Progress Review
    request -- see _resolve_direct_cycle_event_key.

    Returns (event_key, None) on an unambiguous resolution, or
    (None, (message, status)) otherwise. The caller must turn that into an
    error response rather than falling back to an unlinked booking.
    """
    from coach_api.views import resolve_curriculum_programme_id, resolve_review_anchor_date
    from curriculum_api import review_types as curriculum_review_types
    from curriculum_api.review_instances import list_enabled_review_templates

    if mirror is None:
        return None, ("Only Active learners can book programme-cycle sessions.", 400)

    programme_id = resolve_curriculum_programme_id(
        getattr(mirror, 'programme_id', None) or getattr(mirror, 'programme', None),
    )
    if not programme_id:
        return None, ("No Progress Review is configured for this learner's programme.", 422)

    pr_type = curriculum_review_types.get_review_type_by_code(curriculum_review_types.REVIEW_TYPE_CODE_PROGRESS_REVIEW)
    pr_type_id = _s((pr_type or {}).get('id'))
    pr_templates = [
        template for template in list_enabled_review_templates(programme_id)
        if pr_type_id and _s(template.get('review_type_id')) == pr_type_id
    ]
    if not pr_templates:
        return None, ("No Progress Review is configured for this learner's programme.", 422)
    if len(pr_templates) > 1:
        return None, (
            "More than one Progress Review is configured for this learner's programme. "
            "Ask your programme team to resolve this before booking.",
            409,
        )

    # STRICT anchor: enrolment."Created_users"."Learner_start_date" only,
    # never Start_date, the profile mirror, or any other fallback -- same
    # rule resolve_review_anchor_date enforces everywhere else.
    source_rows = {mirror.id: learner} if learner is not None else {}
    anchor, _reason = resolve_review_anchor_date(mirror.id, {}, source_rows)
    if anchor is None:
        return None, ("Cannot schedule this review because the learner start date is missing.", 422)

    # Template configuration and the anchor are both confirmed -- reuse the
    # generic Progress Review request's own "soonest not-yet-scheduled
    # occurrence" rule rather than re-deriving it, and never use
    # assignment_month itself to pick or manufacture an occurrence.
    return _resolve_direct_cycle_event_key(learner, mirror, "progress-review")


def _record_owner_ids(record, *, created_user_id, profile_id):
    """The learner ids this learner may be stored under on ``record``.

    Created_users.id and the Learner profile id are independent sequences, so
    a number only means something in the id space its row was written in:

    * Review rows -- linked to a Review Instance, or a coach-generated
      programme-cycle slot -- carry the profile id, the same id as
      curriculum.review_instances.learner_id. Only that id is compared.
    * Onboarding reviews are always written with Created_users.id
      (calendar_learner_id in learner_calendar_book), so only that id is
      compared.
    * Rows written by the learner's own booking flow (``learner-book:`` keys)
      and first-session rows have no single id space: bookings stored
      Created_users.id before 2026-09-08 and the profile id since, and
      first_session.py still stores Created_users.id. They keep both ids until
      the stored rows are checked.
    """
    event_type = _s(record.event_type)
    booked_by_learner = _s(getattr(record, "idempotency_key", "")).startswith("learner-book:")
    if _s(getattr(record, "review_instance_id", "")) or (
        event_type in ("mcr", "progress-review", "review") and not booked_by_learner
    ):
        ids = {profile_id}
    elif event_type in ONBOARDING_REVIEW_TYPES:
        ids = {created_user_id}
    else:
        ids = {created_user_id, profile_id}
    return {str(value) for value in ids if value is not None}


def _learner_calendar_record(kind, pk, event_key):
    """Resolve an event only when it belongs to the requested learner."""
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return None
    learner = model.all_learners.filter(pk=pk).first()
    if learner is None:
        return None
    mirror = _calendar_profile(learner, pk)
    existing = CoachCalendarEvent.objects.filter(event_key=event_key).first()
    if existing and mirror and str(existing.learner_id) == str(mirror.id):
        from coach_api.reconciliation_links import for_calendar
        link = for_calendar(existing)
        if link:
            occurrences = coaching_events_for_learner(learner, mirror)
            imported = next((item for item in occurrences if item.get("reviewSource") == "aptem"
                             and str(item.get("reviewId")) == str(link.source_review_id)
                             and str(item.get("aptemReviewId")) == link.aptem_review_id), None)
            if imported is None:
                return None
            existing._imported_review_event = imported
            return existing
    if (existing and mirror and getattr(existing, "review_instance_id", None)
            and getattr(existing, "review_template_id", None)
            and not event_key.startswith("imported-review:")
            and str(existing.learner_id) == str(mirror.id)
            and existing.event_type in {"mcr", "progress-review", "review"}):
        return existing
    if source_for_learner(learner, mirror).kind != "curriculum":
        mirror = review_profile_for_source(learner, mirror)
        occurrences = coaching_events_for_learner(learner, mirror)
        imported = [item for item in occurrences if item.get("reviewSource") == "aptem"
                    and event_key in {item["eventKey"], item.get("calendarEventKey")}]
        if imported:
            key = imported[0].get("calendarEventKey")
            record = CoachCalendarEvent.objects.filter(event_key=key).first() if key else None
            if record:
                record._imported_review_event = imported[0]
            return record
        if event_key.startswith(("imported-review:", "review:", "mcr:", "progress-review:")):
            return None
    emails = {_s(learner.email).strip().casefold()}
    if mirror:
        emails.add(_s(mirror.email).strip().casefold())
    emails.discard("")
    record = CoachCalendarEvent.objects.filter(event_key=event_key).first()
    if not record or (source_for_learner(learner, mirror).kind == "aptem"
                      and record.event_type in {"mcr", "progress-review", "review"}):
        return None
    owner_ids = _record_owner_ids(record, created_user_id=pk, profile_id=mirror.id if mirror else None)
    return record if (str(record.learner_id or "") in owner_ids or _s(record.learner_email).strip().casefold() in emails) else None


def _follow_first_session_start_date(kind, pk, record, scheduled_date):
    """Move the programme start date with the first session, if that is what moved.

    Booking the first session writes its date to ``learner_start_date``
    (first_session._stamp) because the programme starts at that session. Moving
    the session therefore has to move the start date too: leaving it behind
    would make the record disagree with the meeting, and that column is the
    strict anchor review scheduling reads -- so the learner's whole review
    timeline would stay pinned to a day nothing happens on.

    Only ``first-session``. Every other bookable type is a session *within* a
    programme that has already started, and moving one must not shift the
    learner's start date.

    Failure is logged, not raised: the meeting has already moved in Graph and
    everybody has been re-invited by this point, so refusing the whole request
    would report a failure for something that did happen.
    """
    if _s(getattr(record, "event_type", "")).lower() != "first-session":
        return
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return
    try:
        from .learner_dates import save_enrolment_fields

        learner = model.all_learners.filter(pk=pk).first()
        if learner is None:
            return
        learner.learner_start_date = scheduled_date.isoformat()
        save_enrolment_fields(learner, ["learner_start_date"])
    except DatabaseError:
        logger.exception(
            "learner_calendar_reschedule: could not move the start date for learner %s", pk,
        )


def _learner_booking_record(kind, pk, event_key):
    """Resolve a learner-owned booking row that the learner is allowed to move."""
    record = _learner_calendar_record(kind, pk, event_key)
    if not record or _s(record.event_type) not in CANCELLABLE_TYPES:
        return None
    if getattr(settings, 'ENROLLMENT_ONLY', False) and record.event_type not in (*ONBOARDING_REVIEW_TYPES, FIRST_SESSION_TYPE):
        return None
    return record


@csrf_exempt
@learner_self_or_staff(kwarg="pk")
def learner_calendar_event_review(request, kind, pk, event_key):
    """Return the Curriculum form for a learner calendar event, or (POST)
    save the learner's own answers to whichever fields the Review's
    Curriculum template opted the Learner into answering.

    The endpoint never creates a review instance -- GET on an unscheduled
    occurrence exposes a read-only template preview; POST requires a booked
    instance to already exist (the coach's meeting is what creates it).
    """
    if request.method not in ("GET", "POST"):
        return _error("Method not allowed.", 405)
    if request.method == "POST":
        return _save_learner_review_answers(request, kind, pk, event_key)
    record = _learner_calendar_record(kind, pk, event_key)
    from curriculum_api import review_instances, reviews
    if record and isinstance(getattr(record, '_imported_review_event', None), dict):
        return _error("Open this imported Review through its migrated form.", 409)
    instance_id = _s(getattr(record, "review_instance_id", ""))
    if not instance_id:
        template_id = _s(getattr(record, 'review_template_id', ''))
        occurrence_number = getattr(record, 'occurrence_number', None) or getattr(record, 'sequence', None)
        if not record:
            model = SOURCE_MODELS.get(kind)
            learner = model.all_learners.filter(pk=pk).first() if model else None
            if learner is None:
                return _error('Calendar event not found for this learner.', 404)
            mirror = _calendar_profile(learner, pk)
            event = next((event for event in _generated_cycle_events(learner, mirror, set()) if event['eventKey'] == event_key), None)
            if event is None:
                return _error('Calendar event not found for this learner.', 404)
            template_id = event['reviewTemplateId']
            occurrence_number = event['occurrenceNumber']
        if not template_id:
            return JsonResponse({'instance': None})
        # A coach can open a generated Curriculum occurrence before a
        # CoachCalendarEvent booking row exists. In that case opening the
        # occurrence creates the durable instance, but the learner calendar
        # still reaches this endpoint through the generated event key. Resolve
        # that same instance by its stable identity instead of returning a
        # blank template preview. review_instances.learner_id is the learner's
        # profile id (see _record_owner_ids), so only that id is looked up --
        # Created_users.id is a different sequence and may equal someone
        # else's profile id.
        model = SOURCE_MODELS.get(kind)
        learner = model.all_learners.filter(pk=pk).first() if model else None
        mirror = _calendar_profile(learner, pk) if learner is not None else None
        if mirror is not None:
            existing = review_instances.find_review_instance(template_id, str(mirror.pk), occurrence_number)
            if existing:
                instance_id = _s(existing.get('id'))
        if instance_id:
            instance = review_instances.get_review_instance(instance_id)
            if not instance:
                return _error("Review instance not found.", 404)
            definition = review_instances.review_instance_form_definition(instance)
            if not definition['template']['visibleTo'].get('participant', True):
                return _error('This review is not visible to the learner.', 403)
            return JsonResponse(_learner_visible_review_definition(definition, pk))
        template = reviews.get_review_template_row(template_id, include_deleted=bool(record))
        if template is None:
            return _error('Review template not found.', 404)
        snapshot = review_instances.build_definition_snapshot(template)
        if not snapshot.get('visibleTo', {}).get('participant', True):
            return _error('This review is not visible to the learner.', 403)
        return JsonResponse(_learner_visible_review_definition({
            'instance': None, 'occurrenceNumber': occurrence_number,
            'template': snapshot, 'sections': snapshot['sections'],
            'signatures': {role: {'required': bool(snapshot['signatures'].get(role)), 'signed': False}
                           for role in review_instances.SIGNATURE_ROLES},
        }, pk))
    instance = review_instances.get_review_instance(instance_id)
    if not instance:
        return _error("Review instance not found.", 404)
    definition = review_instances.review_instance_form_definition(instance)
    if not definition['template']['visibleTo'].get('participant', True):
        return _error('This review is not visible to the learner.', 403)
    return JsonResponse(_learner_visible_review_definition(definition, pk))


def _save_learner_review_answers(request, kind, pk, event_key):
    """POST /learner_api/calendar/<kind>/<pk>/events/<event_key>/review/

    Saves only the fields the Review's Curriculum template opted the
    Learner ('participant') into answering -- every other field stays the
    coach's/Curriculum's, exactly as before this endpoint accepted writes.
    """
    from curriculum_api import review_instances

    record = _learner_calendar_record(kind, pk, event_key)
    if record and isinstance(getattr(record, '_imported_review_event', None), dict):
        return _error("Open this imported Review through its migrated form.", 409)
    instance_id = _s(getattr(record, "review_instance_id", ""))
    if not instance_id:
        return _error("This review has not been booked yet.", 404)
    instance = review_instances.get_review_instance(instance_id)
    if not instance:
        return _error("Review instance not found.", 404)
    definition = review_instances.review_instance_form_definition(instance)
    if not definition['template']['visibleTo'].get('participant', True):
        return _error('This review is not visible to the learner.', 403)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return _error("answers must be an object keyed by field id.", 400)

    account = authenticate_request(request)
    actor = _s(getattr(account, "email", "")) or f"learner:{pk}"
    try:
        updated = review_instances.save_review_instance_answers_for_role(
            instance, answers, "participant", actor=actor,
        )
    except PermissionError as exc:
        return _error(str(exc), 403)
    except ValueError as exc:
        return _error(str(exc), 409)
    return JsonResponse(_learner_visible_review_definition(updated, pk))


def _saved_learner_signature(learner_id):
    """Read the learner's reusable signature without making it mandatory.

    The signature columns were added after the original learner calendar
    endpoint. A deployment that has not applied that small migration should
    still be able to open and sign reviews, so a missing table/column is
    treated as an empty reusable signature.
    """
    try:
        with connections["enrolment"].cursor() as cursor:
            cursor.execute(
                '''SELECT "Learner_signature", "Learner_signature_name"
                     FROM enrolment."Created_users"
                    WHERE id=%s LIMIT 1''',
                [str(learner_id)],
            )
            row = cursor.fetchone()
    except DatabaseError:
        return {}
    signature = _s(row[0]) if row else ""
    if not signature.startswith("data:image/"):
        return {}
    return {
        "savedSignature": signature,
        "savedSignatureName": _s(row[1]) if row else "",
    }


def _learner_visible_review_definition(definition, learner_id=None):
    """Hide the formal MCM summary until the coach submits the Review."""
    definition = dict(definition)
    is_mcm = (definition.get('template') or {}).get('reviewTypeCode') == 'mcm'
    if (
        is_mcm
        and (definition.get('instance') or {}).get('status') not in {'awaiting-signature', 'completed'}
    ):
        from curriculum_api.review_instances import meeting_summary_field
        field = meeting_summary_field(definition)
        if field:
            field['answer'] = None
            field['answeredBy'] = None
            field['answeredAt'] = None
    if learner_id is not None and is_mcm:
        definition.update(_saved_learner_signature(learner_id))
    return definition


@learner_self_or_staff(kwarg="pk")
def learner_calendar_event_review_pdf(request, kind, pk, event_key):
    """Download a PDF for a learner-visible current or imported review."""
    if event_key.startswith('imported-review:'):
        from curriculum_api.review_pdf import historical_pdf_response, learner_information
        from .aptem_review_pdf import imported_review_for_source, original_review_pdf

        model = SOURCE_MODELS.get(kind)
        source = model.all_learners.filter(pk=pk).first() if model else None
        review = imported_review_for_source(source, event_key.split(':', 1)[-1], kind=kind)
        if not review:
            return _error("Imported review not found for this learner.", 404)
        information = learner_information(source, name=review.get('learnerName', ''), programme='')
        return historical_pdf_response(
            review,
            information,
            identifier=event_key.split(':', 1)[-1],
            original_content=original_review_pdf(review),
        )

    # The review view has its own learner_self_or_staff decorator, which reads
    # ``pk`` from keyword arguments. Preserve that contract when delegating so
    # the nested authorization gate can identify the learner as well.
    response = learner_calendar_event_review(request, kind=kind, pk=pk, event_key=event_key)
    if getattr(response, "status_code", 500) != 200:
        return response
    try:
        definition = json.loads(response.content.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError):
        return _error("Review definition could not be read.", 502)

    from curriculum_api.review_pdf import learner_information, mcm_pdf_response, pdf_availability

    if not (pdf_availability(definition) or {}).get("available"):
        return mcm_pdf_response(definition, {})

    model = SOURCE_MODELS.get(kind)
    learner = model.all_learners.filter(pk=pk).first() if model else None
    record = _learner_calendar_record(kind, pk, event_key)
    information = learner_information(
        learner,
        name=getattr(record, "learner_name", "") if record else "",
        programme=getattr(record, "programme", "") if record else "",
    )
    return mcm_pdf_response(definition, information)


@learner_self_or_staff(kwarg="pk")
def learner_calendar_event_artifacts(request, kind, pk, event_key):
    from coach_api.review_attendance_links import attendance_for_display
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    record = _learner_calendar_record(kind, pk, event_key)
    if not record:
        return _error("Calendar event not found for this learner.", 404)
    from .imported_meeting_resources import is_imported_meeting, stored_resources
    if is_imported_meeting(record):
        return stored_resources(record, include_attendance=True)
    from coach_api.views import (
        apply_teams_attendance_status_transition,
        fetch_coach_meeting_graph_snapshot, persist_coach_meeting_snapshots,
    )
    snapshot, error_payload, status_code = fetch_coach_meeting_graph_snapshot(record)
    if error_payload:
        return JsonResponse(error_payload, status=status_code)
    # Opening this panel is not itself what moves a linked review to
    # in-progress -- reuses the attendance data the Graph fetch above already
    # returned for the panel's own reasons; see
    # coach_api.views.apply_teams_attendance_status_transition.
    apply_teams_attendance_status_transition(record, (snapshot.get("attendance") or {}).get("records") or [])
    storage = persist_coach_meeting_snapshots(
        record,
        artifacts=snapshot["artifacts"],
        attendance_reports=snapshot["attendanceReports"],
        attendance_tracker=snapshot["attendanceTracker"],
    )
    # Learners may watch the formal meeting recording, but transcripts remain
    # staff-only because they can contain sensitive discussion notes.
    learner_artifacts = [
        artifact for artifact in snapshot["artifacts"]
        if _s(artifact.get("artifact_type")).lower() == "recording"
    ]
    return JsonResponse({
        "artifacts": learner_artifacts,
        "attendance": attendance_for_display(record, snapshot["attendance"]),
        # Mutable AI drafts never cross the learner boundary. The lifecycle-
        # gated formal answer is returned by learner_calendar_event_review.
        "meetingSummary": None,
        "errors": snapshot["errors"],
        "partial": snapshot["partial"],
        "storage": storage,
    }, status=status_code)


@learner_self_or_staff(kwarg="pk")
def learner_calendar_event_artifact_content(request, kind, pk, event_key, artifact_type, artifact_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    record = _learner_calendar_record(kind, pk, event_key)
    if not record:
        return _error("Calendar event not found for this learner.", 404)
    allowed_types = {"recording"}
    if _s(artifact_type).lower() not in allowed_types:
        return _error("Only meeting recordings are available to learners.", 403)
    from .imported_meeting_resources import is_imported_meeting, stored_recording
    if is_imported_meeting(record):
        return stored_recording(request, record, artifact_id)
    from coach_api.views import coach_meeting_artifact_content_response
    return coach_meeting_artifact_content_response(request, record, event_key, artifact_type, artifact_id)


@csrf_exempt
@learner_self_or_staff(kwarg="pk")
def learner_progress_review_sign(request, kind, pk, event_key):
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    record = _learner_calendar_record(kind, pk, event_key)
    if not record or record.event_type not in {"mcr", "progress-review", "review"}:
        return _error("Review not found for this learner.", 404)
    if isinstance(getattr(record, '_imported_review_event', None), dict):
        return _error("Sign this imported Review through its migrated form.", 409)
    if record.event_type == 'mcr':
        from .mcm_signoff import mcm_signoff_response
        return mcm_signoff_response(request, record, pk)
    if request.method != 'POST':
        return _error("Method not allowed.", 405)
    # Curriculum Review instances are the canonical form/signature record.
    # Keep the old calendar blob only for rows created before instances existed.
    if _s(getattr(record, 'review_instance_id', '')):
        from curriculum_api import review_instances
        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return _error("Invalid JSON body.", 400)
        signature = _s(payload.get("signature"))
        if not signature.startswith("data:image/"):
            return _error("A valid learner signature is required.", 400)
        if record.status not in {CoachCalendarEvent.STATUS_AWAITING_SIGNATURE, CoachCalendarEvent.STATUS_COMPLETED}:
            return _error("The coach must submit the review before the learner can sign it.", 409)
        instance = review_instances.get_review_instance(record.review_instance_id)
        if not instance:
            return _error("Review instance not found.", 404)
        try:
            definition = review_instances.record_review_instance_signature(
                instance, "participant", signed_by=_s(record.learner_email),
                signed_name=_s(payload.get("name")) or _s(record.learner_name),
                signature=signature, actor=_s(record.learner_email) or "learner",
            )
        except ValueError as exc:
            return _error(str(exc), 409)
        # record_review_instance_signature already projected the instance's new
        # status onto this Calendar row, transactionally and with link-identity
        # checks (curriculum_api.review_instances
        # ._mirror_linked_calendar_after_signature). Writing it again here would
        # be a second, unguarded lifecycle decision -- re-read instead.
        record.refresh_from_db()
        return JsonResponse({"event": _serialize_event(record), "review": definition})
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return _error("Invalid JSON body.", 400)
    signature = _s(payload.get("signature"))
    if not signature.startswith("data:image/"):
        return _error("A valid learner signature is required.", 400)
    if record.status not in {CoachCalendarEvent.STATUS_AWAITING_SIGNATURE, CoachCalendarEvent.STATUS_COMPLETED}:
        return _error("The coach must submit the review before the learner can sign it.", 409)
    responses = dict(record.review_responses or {})
    responses.update({
        "learner_signature": signature,
        "learner_signed_by": _s(payload.get("name")) or record.learner_name,
        "learner_signed_at": timezone.now().isoformat(),
    })
    record.review_responses = responses
    record.save(update_fields=["review_responses", "updated_at"])
    return JsonResponse({"event": _serialize_event(record)})


def _serialize_live_session_event(event):
    """Convert the coach curriculum event shape to the learner calendar shape."""
    scheduled_time = None
    start_hour = event.get("startHour")
    if isinstance(start_hour, (int, float)):
        hour = int(start_hour)
        minute = round((start_hour - hour) * 60)
        scheduled_time = f"{hour:02d}:{minute:02d}"
    return {
        "id": event.get("id"),
        "eventKey": event.get("eventKey") or event.get("id"),
        "occurrenceId": event.get("occurrenceId"),
        "title": event.get("title") or "Live Session",
        "source": "live-session",
        "type": "live-session",
        "sequence": event.get("sequence"),
        "status": event.get("status") or "scheduled",
        "date": event.get("date"),
        "targetDate": event.get("targetDate") or event.get("date"),
        "scheduledDate": event.get("date"),
        "scheduledTime": scheduled_time,
        "durationMinutes": event.get("durationMinutes") or 60,
        "coachName": event.get("tutor") or event.get("ownerName") or "",
        "coachEmail": event.get("ownerEmail") or "",
        "meetingProvider": event.get("meetingProvider") or "",
        "meetingLink": event.get("meetingLink") or event.get("graphWebLink") or "",
        "notes": event.get("notes") or "",
        "programme": event.get("programme") or "",
        "cohort": event.get("cohort") or "",
        "group": event.get("group") or "",
        "module": event.get("module") or "",
    }


def _catchup_link_precheck(learner_id, report_id, scheduled_date, *, enforce_change_cutoff=False):
    """Refuse, before booking, a catch-up the absence report could not take."""
    from .absence_reports import RecoveryPlanError
    from .session_recovery import ReportNotFound, check_report_can_take_catchup
    try:
        report = check_report_can_take_catchup(learner_id, report_id, enforce_change_cutoff=enforce_change_cutoff)
    except ReportNotFound:
        return _error("Absence report not found.", 404)
    except RecoveryPlanError as exc:
        return _error(str(exc), 409)
    if report.session_date and scheduled_date < report.session_date:
        return _error("Choose a catch-up on or after the lecture date.", 400)
    return None


def _link_booked_catchup(record, learner, mirror, learner_id, report_id, *, enforce_change_cutoff=False):
    """Link the new booking to the absence; if that fails, cancel the booking again."""
    from .absence_reports import RecoveryPlanError
    from .session_recovery import ReportNotFound, link_report_to_catchup
    try:
        link_report_to_catchup(learner, mirror, learner_id, report_id, record.event_key,
                               enforce_change_cutoff=enforce_change_cutoff)
        return None
    except (ReportNotFound, RecoveryPlanError, DatabaseError) as exc:
        message = str(exc) if isinstance(exc, RecoveryPlanError) else "The catch-up could not be linked to your absence."
        logger.warning("learner_calendar_book: linking %s to report %s failed: %s", record.event_key, report_id, exc)
    try:
        from coach_api.views import cancel_reserved_calendar_event
        cancel_reserved_calendar_event(record)
    except Exception:
        logger.exception("learner_calendar_book: could not cancel unlinked catch-up %s", record.event_key)
        return _error(f"{message} The booking was kept; please link or cancel it from your calendar.", 409)
    return _error(f"{message} The booking was cancelled. Please choose another time.", 409)


def _annotate_linked_catchups(events):
    """``linkedReportId``: the absence report a catch-up already makes up (one lecture per catch-up)."""
    from coach_api.models import CoachAbsenceReport
    keys = [event.get("eventKey") for event in events if event.get("source") == "catch-up" and event.get("eventKey")]
    if not keys:
        return
    linked = dict(
        CoachAbsenceReport.objects.filter(catchup_event_key__in=keys)
        .exclude(status=CoachAbsenceReport.STATUS_DECLINED)
        .values_list("catchup_event_key", "id")
    )
    for event in events:
        if event.get("source") == "catch-up":
            event["linkedReportId"] = linked.get(event.get("eventKey"))


def _catchup_change_closed(record):
    """A catch-up starting within the cut-off can no longer be moved or cancelled by the learner."""
    from .coach_availability import catchup_change_closed
    return (_s(record.event_type).lower() == "catch-up"
            and catchup_change_closed(record.scheduled_date, record.scheduled_time))


def _learner_catchup_change_error(request, record):
    """Refuse a learner's late change to their catch-up; staff may still change it."""
    from .coach_availability import CATCHUP_CHANGE_CLOSED_MESSAGE
    role = getattr(getattr(request, "account", None), "role", "")
    if role in {"admin", "staff"} or not _catchup_change_closed(record):
        return None
    return _error(CATCHUP_CHANGE_CLOSED_MESSAGE, 409)


def _release_cancelled_catchup(record):
    """The absence this catch-up was making up goes back to needing a recovery."""
    if _s(record.event_type).lower() != "catch-up":
        return
    from curriculum_api.live_session_absences import release_cancelled_catchup
    from curriculum_api.models import LiveSessionAbsence
    from django.db import router
    try:
        release_cancelled_catchup(
            database=router.db_for_write(LiveSessionAbsence) or "default", event_key=record.event_key,
        )
    except DatabaseError:
        logger.exception("learner_calendar_cancel: could not release absence for %s", record.event_key)


def _catchup_time_error(owner_email, scheduled_date, scheduled_time, duration_minutes, *, exclude_event_key=""):
    """Refuse a catch-up outside the coach's free working time (the picker's own rule)."""
    from .coach_availability import AvailabilityUnavailable, catchup_slot_is_free
    try:
        if catchup_slot_is_free(owner_email, scheduled_date, scheduled_time, duration_minutes,
                                exclude_event_key=exclude_event_key):
            return None
    except AvailabilityUnavailable as exc:
        return _error(str(exc), 503)
    return _error("Your coach is not available at that time. Choose one of the available times.", 409)


@learner_self_or_staff(kwarg="pk")
def learner_calendar(request, kind, pk):
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        # all_learners: the default manager is scoped to apprenticeship rows.
        learner = model.all_learners.filter(pk=pk).first()
    except DatabaseError as exc:
        logger.exception("learner_calendar: learner lookup failed")
        return _error(f"Database error: {exc}", 502)
    if learner is None:
        return _error("Learner not found.", 404)

    try:
        # The Active_users mirror carries the source row's id (see
        # active_users.sync_active_user), and coach events store that mirror's
        # id + email — so the mirror email is the authoritative one here.
        mirror = _calendar_profile(learner, pk)
    except DatabaseError as exc:
        logger.exception("learner_calendar: mirror lookup failed")
        return _error(f"Database error: {exc}", 502)

    mirror_email = _s(mirror.email) if mirror else ""
    source_email = _s(learner.email)
    email = mirror_email or source_email

    match = Q()
    if mirror is not None:
        match |= Q(learner_id=pk)
        match |= Q(learner_id=mirror.id)
    for candidate in {mirror_email, source_email} - {""}:
        match |= Q(learner_email__iexact=candidate)
    if not match:
        return JsonResponse({
            "learner": {"kind": kind, "id": pk},
            "events": [],
            "bookingCalendar": booking_calendar_payload(),
        })

    try:
        if getattr(settings, 'ENROLLMENT_ONLY', False):
            records = CoachCalendarEvent.objects.filter(match, event_type__in=(*ONBOARDING_REVIEW_TYPES, FIRST_SESSION_TYPE))
            events = [_serialize_event(record) for record in records.order_by('target_date', 'sequence')]
            return JsonResponse({
                'learner': {'kind': kind, 'id': pk, 'email': email},
                'events': events,
                'bookingCalendar': booking_calendar_payload(),
            })
        events = coaching_events_for_learner(learner, mirror)
        events.extend(alternative_recovery_events_for_learner(pk))

        # Live curriculum sessions belong to the learner's explicit module
        # assignment, not to a possibly stale profile group label. Fall back to
        # placement scope only for legacy learners with no module assignment.
        module_ids = assigned_curriculum_module_ids(learner)
        if module_ids:
            from coach_api.views import collect_live_session_events

            live_events = collect_live_session_events(
                "",
                "",
                require_coach_access=False,
                include_past=True,
                learner_module_ids=module_ids,
            )
            for event in live_events:
                events.append(_serialize_live_session_event(event))
        elif mirror is not None and (_s(getattr(mirror, "group_id", "")) or _s(mirror.group_name)):
            from coach_api.views import collect_live_session_events

            live_events = collect_live_session_events(
                "",
                "",
                require_coach_access=False,
                include_past=True,
                learner_scope={
                    "programme": _s(mirror.programme),
                    "programme_id": _s(getattr(mirror, "programme_id", "")),
                    "cohort": _s(mirror.cohort),
                    "cohort_id": _s(getattr(mirror, "cohort_id", "")),
                    "group": _s(mirror.group_name),
                    "group_id": _s(getattr(mirror, "group_id", "")),
                },
            )
            for event in live_events:
                events.append(_serialize_live_session_event(event))

        # A curriculum event can be reachable through more than one legacy
        # source. Keep one stable calendar item per event key.
        events = list({
            event.get("eventKey") or event.get("id"): event
            for event in events
            if event.get("eventKey") or event.get("id")
        }.values())
        # Generated slots interleave with stored ones, so the page is handed a
        # calendar in date order rather than stored-then-generated.
        events.sort(key=lambda event: (
            event.get("date") or event.get("targetDate") or "",
            _s(event.get("source")),
            event.get("sequence") or 0,
        ))
        # Elapsed meetings show Ended, or Completed when the learner attended.
        from coach_api.meeting_outcomes import annotate_meeting_outcomes
        annotate_meeting_outcomes(events)
        _annotate_linked_catchups(events)
        # Live sessions keep their own rule: Completed when this learner attended.
        from coach_api.live_session_outcomes import annotate_live_session_outcomes
        annotate_live_session_outcomes(
            events, learner_profile_id=getattr(mirror, "id", None), learner_email=email,
            recording_viewer=(kind, pk),
        )
    except DatabaseError as exc:
        logger.exception("learner_calendar: event lookup failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse(
        {
            "learner": {"kind": kind, "id": pk, "email": email},
            "events": events,
            "bookingCalendar": booking_calendar_payload(),
        }
    )


def onboarding_review_idempotency_parts(kind, pk, session_type, cancelled_count):
    """Logical identity of one onboarding review booking, before hashing.

    The slot is left out on purpose: each review type is booked once, so a
    retried POST replays that booking. What does make a new booking is a
    cancellation -- the cancelled row keeps its key, so without the count a
    rebooking would match it and be refused as a different booking. A count of 0 gives the same parts as before, so a key
    already stored for a first booking still matches its retries.
    """
    parts = [kind, str(pk), session_type]
    if cancelled_count:
        parts.append(f"after-cancelled:{cancelled_count}")
    return parts


@csrf_exempt
# The one write a staff viewer keeps on a learner's page: arranging a coaching
# session is administration, not a claim about the learner's own work.
@learner_self_or_staff(kwarg="pk")
def learner_calendar_book(request, kind, pk):
    """Book a session with the learner's assigned coach.

        POST /learner_api/calendar/<kind>/<pk>/book/
        {"sessionType": "catch-up" | "student-support",
         "scheduledDate": "YYYY-MM-DD", "scheduledTime": "HH:MM",
         "durationMinutes": 60, "notes": "..."}

    Mirrors coach_api.views.coach_timetable_schedule_event: same
    coach_calendar_event row shape, same event-key format, same Microsoft
    Graph Teams sync (meeting created on the coach's calendar, learner as
    attendee). The coach comes from the Active_users mirror's coach_email.
    """
    # Imported here (not module level) to keep the heavyweight coach views
    # module out of learner_api's import path until a booking actually happens.
    from coach_api.views import (
        build_booked_calendar_event,
        booking_request_matches_record,
        calendar_idempotency_key,
        CalendarSyncInProgress,
        find_generated_timetable_event,
        LearnerCalendarConflict,
        normalize_duration_minutes,
        parse_date_value,
        parse_time_value,
        persist_calendar_sync_reservation,
        reserve_coach_calendar_booking,
        synchronize_reserved_calendar_event,
    )
    from datetime import datetime

    if request.method != "POST":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        # all_learners: the default manager is scoped to apprenticeship rows.
        learner = model.all_learners.filter(pk=pk).first()
        mirror = _calendar_profile(learner, pk)
    except DatabaseError as exc:
        logger.exception("learner_calendar_book: learner lookup failed")
        return _error(f"Database error: {exc}", 502)
    if learner is None:
        return _error("Learner not found.", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)

    session_type = _s(payload.get("sessionType"))
    if getattr(settings, 'ENROLLMENT_ONLY', False) and session_type not in (*ONBOARDING_REVIEW_TYPES, FIRST_SESSION_TYPE):
        return _error('Study meetings are booked in the LMS.', 400, code='LMS_MEETING_REQUIRED')
    is_onboarding_review = session_type in ONBOARDING_REVIEW_TYPES
    is_first_session = session_type == FIRST_SESSION_TYPE
    if session_type not in BOOKABLE_TYPES and not is_onboarding_review:
        allowed = "', '".join((*BOOKABLE_TYPES, *ONBOARDING_REVIEW_TYPES))
        return _error(f"sessionType must be one of '{allowed}'.", 400)

    identity = source_for_learner(learner, mirror)
    if session_type in {"mcr", "progress-review", "review"} and identity.kind == "conflict":
        return _error("Conflicting Aptem learner identities.", 409, code="REVIEW_IDENTITY_CONFLICT")
    if session_type == "review" and identity.kind == "aptem":
        return _error("Choose an imported Aptem Review for this learner.", 409, code="APTEM_REVIEW_SOURCE_ONLY")

    owner_staff_id = None
    if is_onboarding_review or is_first_session:
        # Booked before the learner is Active and before a coach exists, so
        # these go to the case owner (enrolment officer) rather than a coach.
        #
        # The first session is the clearest case: it is the meeting that starts
        # the programme, so requiring an Active mirror to book it would be
        # circular -- the learner cannot become Active until it has happened.
        owner_email, owner_name, owner_staff_id = _case_owner_record(learner)
        if not owner_email:
            return _error(
                "No case owner has been assigned to you yet. Please contact your programme team.", 400
            )
        if is_first_session and _still_enrolling(kind, learner) and not _may_book_first_session_early(kind, learner):
            # The gate stops offering it, but the calendar still lists the type:
            # the order is kept here, where a stale tab cannot get round it.
            return _error(
                "Your first learning session is booked once your enrolment is complete: "
                "your enrolment form, your onboarding reviews and your signature on "
                "each of your compliance documents.", 400
            )
        if is_first_session:
            # There is only ever one first session. Without this a learner
            # could book a second from a stale tab and end up with two
            # meetings, two invitations and a start date that follows whichever
            # was written last -- moving it is what reschedule is for.
            try:
                existing = (
                    CoachCalendarEvent.objects.filter(
                        learner_id=pk, event_type=FIRST_SESSION_TYPE
                    )
                    .exclude(status=CoachCalendarEvent.STATUS_CANCELLED)
                    .exists()
                )
            except DatabaseError as exc:
                logger.exception("learner_calendar_book: first-session check failed")
                return _error(f"Database error: {exc}", 502)
            if existing:
                return _error(
                    "Your first learning session is already booked. "
                    "Contact your case owner if you need to change it.", 409
                )
    else:
        if mirror is None:
            return _error(
                "Only Active learners can book coach sessions. "
                "Book your first learning session first.", 400
            )
        # The source learner carries the current assignment. A mirror can lag
        # after reassignment, and an explicitly cleared source assignment must
        # not silently send a new invite to the former coach.
        owner_email = _s(
            getattr(learner, "coach_email", mirror.coach_email)
            if hasattr(learner, "coach_email") else mirror.coach_email
        )
        owner_name = _s(
            getattr(learner, "coach_name", mirror.coach_name)
            if hasattr(learner, "coach_name") else mirror.coach_name
        ) or "Coach"
        if not owner_email:
            return _error("No coach has been assigned to you yet. Please contact your programme team.", 400)

    try:
        if session_type in REVIEW_BOOKING_TYPES:
            from coach_api.booking_hours import validate_review_time_precision
            validate_review_time_precision(payload.get("scheduledTime"))
        scheduled_date = parse_date_value(payload.get("scheduledDate"))
        scheduled_time = parse_time_value(payload.get("scheduledTime"))
        duration_value = payload.get("durationMinutes")
        if session_type in REVIEW_BOOKING_TYPES and duration_value in (None, ""):
            raise ValueError("durationMinutes is required for Review bookings.")
        if session_type in REVIEW_BOOKING_TYPES:
            from coach_api.booking_hours import validate_review_duration
            duration_minutes = validate_review_duration(duration_value)
        else:
            duration_minutes = normalize_duration_minutes(duration_value or 60)
        timezone_offset_minutes = int(payload.get("timezoneOffsetMinutes") or 0)
        if not -840 <= timezone_offset_minutes <= 840:
            raise ValueError("timezoneOffsetMinutes is outside the supported range.")
    except ValueError as exc:
        return _error(str(exc), 400)
    if isinstance(scheduled_date, datetime):
        scheduled_date = scheduled_date.date()
    if not scheduled_date:
        return _error("scheduledDate is required.", 400)
    if not scheduled_time:
        return _error("scheduledTime is required.", 400)
    date_restriction = booking_date_restriction(scheduled_date)
    if date_restriction is None and session_type == "mcr":
        date_restriction = mcm_booking_date_restriction(scheduled_date)
    if date_restriction is not None:
        return _error(date_restriction.message, 400)
    if session_type in REVIEW_BOOKING_TYPES:
        try:
            from coach_api.booking_hours import validate_review_interval
            validate_review_interval(scheduled_date, scheduled_time, duration_minutes)
        except ValueError as exc:
            return _error(str(exc), 400)
    if session_type == "catch-up":
        busy_error = _catchup_time_error(owner_email, scheduled_date, scheduled_time, duration_minutes)
        if busy_error:
            return busy_error
    # Booking a catch-up for a reported absence links it in the same request, and
    # the link is checked before anything is booked, so a refused link never
    # leaves a stray booking (and its invitation) behind.
    link_report_id = None
    if session_type == "catch-up" and payload.get("absenceReportId") not in (None, ""):
        try:
            link_report_id = int(payload.get("absenceReportId"))
        except (TypeError, ValueError):
            return _error("absenceReportId must be a number.", 400)
        link_error = _catchup_link_precheck(pk, link_report_id, scheduled_date, enforce_change_cutoff=learner_change(request))
        if link_error:
            return link_error

    notes = _s(payload.get("notes"))[:500]
    # An onboarding learner has no mirror row yet, so fall back to the source.
    # (LearnerProfile's name column is full_name, not username.)
    learner_name = _s(getattr(mirror, "full_name", "")) or _s(learner.username)
    learner_email = _s(getattr(mirror, "email", "")) or _s(learner.email)
    assignment_month = _s(payload.get("assignmentMonth")) if session_type in {"mcr", "progress-review"} else ""
    imported_review_id = _s(payload.get("reviewId")) if assignment_month else ""
    if payload.get("reviewId") and source_for_learner(learner, mirror).kind == "curriculum":
        return _error("This learner uses Curriculum Review occurrences.", 409)
    # The assignment screen and imported reviews both send assignmentMonth.
    # An explicit context keeps their eligibility rules and identities separate.
    assignment_booking = _s(payload.get("bookingContext")) == "monthly-assignment"
    if assignment_booking and (
        session_type != "mcr" or not assignment_month
        or (imported_review_id and _s(payload.get("eventKey")))
    ):
        return _error(
            "Monthly assignment bookings require an MCM, an assignment month, and either "
            "an official slot or an imported Aptem MCM.", 400,
        )
    # The imported review's own month keys its booking, as on the Calendar page,
    # so the same Aptem MCM keeps one meeting whichever screen booked it.
    idempotency_month = assignment_month
    if assignment_booking and imported_review_id:
        if mirror is None:
            return _error("Only Active learners can book programme-cycle sessions.", 400)
        try:
            review_month, review_error = _assignment_imported_mcm_month(mirror.id, imported_review_id)
        except DatabaseError as exc:
            logger.exception("learner_calendar_book: imported MCM lookup failed")
            return _error(f"Database error: {exc}", 502)
        if review_error:
            message, status_code = review_error
            return _error(message, status_code)
        idempotency_month = review_month or assignment_month
    aptem_target = None
    aptem_review_booking = session_type in {"mcr", "progress-review"} and source_for_learner(learner, mirror).kind != "curriculum"
    if aptem_review_booking:
        try:
            mirror = review_profile_for_source(learner, mirror)
            candidates = [item for item in coaching_events_for_learner(learner, mirror)
                          if item.get("reviewSource") == "aptem" and item["source"] == session_type
                          and ((payload.get("reviewId") and item["reviewId"] == str(payload["reviewId"]))
                               or (payload.get("eventKey") and payload["eventKey"] in {
                                   item["eventKey"], item.get("calendarEventKey")}))]
        except DatabaseError:
            return _error("The imported Review identity could not be resolved.", 409)
        if len(candidates) != 1:
            return _error("Choose an existing imported Review for this learner. If none are available, refresh the Aptem source data.", 409, code="APTEM_REVIEW_SOURCE_ONLY")
        aptem_target = candidates[0]
        if getattr(mirror, "lifecycle_status", "") != "active":
            return _error("Only Active learners can schedule programme-cycle sessions.", 400)
        if aptem_target["status"] not in {"not-scheduled", "scheduled"}:
            return _error("A submitted or started imported Review cannot be scheduled again.", 409)
        imported_review_id = aptem_target["reviewId"]
        payload = {**payload, "reviewId": imported_review_id,
                   "eventKey": aptem_target.get("calendarEventKey") or aptem_target["eventKey"]}
        if aptem_target.get("calendarEventKey"):
            owner_email = aptem_target["coachEmail"] or owner_email
            existing = CoachCalendarEvent.objects.filter(event_key=aptem_target["calendarEventKey"]).first()
            if existing and existing.sync_state == CoachCalendarEvent.SYNC_SYNCED:
                if (existing.scheduled_date, existing.scheduled_time, existing.duration_minutes) != (scheduled_date, scheduled_time, duration_minutes):
                    return _error("This imported Review already has an appointment. Use reschedule to move it.", 409)
                from coach_api import imported_meeting_ownership as meeting_ownership
                if meeting_ownership.enabled_for(aptem_target):
                    try:
                        meeting_ownership.require_activation_ready()
                        meeting_ownership.require_synced_owner(existing, aptem_target)
                    except meeting_ownership.MeetingOwnershipError:
                        return _error("This imported meeting needs ownership reconciliation.", 409)
                # The reservation branch below ends with this same transition.
                # Answering a replay from the appointment that already exists
                # must not leave the form unscheduled instead. Lifecycle only:
                # the row, its schedule, its Teams meeting and its sync state
                # are untouched, and no Graph request is made. The resolved
                # event was shaped before the transition, so a promotion
                # reshapes it through the same serializer the booking path uses.
                from .imported_booking import schedule_overlay_for_booking
                scheduled_now = schedule_overlay_for_booking(existing.pk, aptem_target)
                return JsonResponse({
                    "event": _booking_response_event(existing, aptem_target) if scheduled_now else aptem_target,
                    "warning": _friendly_sync_warning(existing.last_graph_sync_error),
                })
    # Generic MCM/PR requests still resolve to an official Curriculum
    # occurrence before they use the direct scheduling flow below.
    direct_cycle_request = session_type in {"mcr", "progress-review"} and not _s(payload.get("eventKey")) and not assignment_month
    if direct_cycle_request:
        # A generic MCM/PR request must still resolve to an official Curriculum
        # occurrence -- it must never fall through to the generic
        # reserve_coach_calendar_booking path below, which has no concept of
        # review_template_id/review_instance_id and would create an unlinked
        # calendar row (see coach_timetable_schedule_event's own linkage guard
        # for the matching coach-side rule).
        if mirror is None:
            return _error("Only Active learners can book programme-cycle sessions.", 400)
        resolved_event_key, resolution_error = _resolve_direct_cycle_event_key(learner, mirror, session_type)
        if resolution_error:
            message, status_code = resolution_error
            return _error(message, status_code)
        payload = {**payload, "eventKey": resolved_event_key}
    requires_coach_approval = session_type in COACH_APPROVAL_TYPES and not is_onboarding_review
    calendar_learner_id = int(mirror.id) if mirror is not None and not is_onboarding_review else pk

    if assignment_month:
        if not imported_review_id and not assignment_booking:
            return _error("reviewId is required when scheduling an imported monthly coaching review.", 400)
        if session_type == "mcr":
            from .monthly_assignment import coaching_booking_bounds, month_bounds
            # Assignment bookings use the recurring MCM window checked above;
            # their submission month does not change the Review's identity.
            if assignment_booking:
                if not month_bounds(assignment_month)[0]:
                    return _error("Choose a valid assignment month for the MCM.", 400)
            else:
                start, end = coaching_booking_bounds(assignment_month)
                if not (start and start <= scheduled_date <= end):
                    return _error("Book an MCM from the 20th of the submission month through the 5th of the following month.", 400)
            # A validated imported Aptem MCM is already the Review's identity
            # (see _assignment_imported_mcm_month) and books on the imported path below.
            if not _s(payload.get("eventKey")) and not (assignment_booking and imported_review_id):
                # assignment_month is an eligibility gate (the window check
                # above), never the Review's identity -- resolve the actual
                # canonical Curriculum occurrence instead of falling through
                # to the generic reserve_coach_calendar_booking path below,
                # which has no concept of review_template_id/review_instance_id
                # and would create an unlinked standalone 'mcr' row (see
                # _resolve_assignment_month_mcm_occurrence).
                resolved_event_key, resolution_error = _resolve_assignment_month_mcm_occurrence(learner, mirror)
                if resolution_error:
                    message, status_code = resolution_error
                    return _error(message, status_code)
                payload = {**payload, "eventKey": resolved_event_key}
        elif session_type == "progress-review":
            # No MCM-style booking-window check here: assignment_month for a
            # Progress Review is bookkeeping context copied from the imported
            # Aptem review (which month it came from), never an eligibility
            # window and never the Review's identity -- see
            # _resolve_assignment_month_progress_review_occurrence.
            if not _s(payload.get("eventKey")):
                resolved_event_key, resolution_error = _resolve_assignment_month_progress_review_occurrence(learner, mirror)
                if resolution_error:
                    message, status_code = resolution_error
                    return _error(message, status_code)
                payload = {**payload, "eventKey": resolved_event_key}

    # A supplied event key means the learner opened an official generated
    # programme slot. Without one, MCM/PR requests from the general picker use
    # the normal coach-approval path (including imported-review bookings).
    if session_type == 'review' and not _s(payload.get('eventKey')):
        return _error('Open a configured review from your calendar to schedule it.', 400)
    if session_type in {"mcr", "progress-review", "review"} and _s(payload.get("eventKey")):
        event_key = _s(payload.get("eventKey"))
        if not event_key:
            return _error(
                "Open the Monthly Coaching or Progress Review slot from your calendar and schedule that session.",
                400,
            )
        if len(event_key) > 255:
            return _error("eventKey is too long.", 400)
        if mirror is None:
            return _error("Only Active learners can schedule programme-cycle sessions.", 400)

        if aptem_target is not None:
            base_event, owner_name = aptem_target, aptem_target.get("coachName") or owner_name
        else:
            base_event, owner_name = find_generated_timetable_event(owner_email, event_key)
        if (
            not base_event
            or _s(base_event.get("source")) != session_type
            or str(base_event.get("learnerId") or "") != str(mirror.id)
        ):
            return _error("Programme-cycle calendar event not found for this learner.", 404)

        if _s(base_event.get("status")) in {
            CoachCalendarEvent.STATUS_AWAITING_SIGNATURE,
            CoachCalendarEvent.STATUS_COMPLETED,
        }:
            return _error("A submitted or completed review cannot be scheduled again.", 409)

        if aptem_target is None:
            try:
                require_native_review_creation(mirror.id)
            except ReviewCreationBlocked as exc:
                return _error(str(exc), 409, code=exc.code)

        target_date = parse_date_value(base_event.get("targetDate"))
        if isinstance(target_date, datetime):
            target_date = target_date.date()
        if not target_date:
            return _error("Target date is missing for this event.", 400)

        from coach_api.views import (
            require_review_template_for_first_linkage,
            ReviewTemplateUnavailableError,
        )

        try:
            from .calendar_connections import booking_conflicts
            review_booking = session_type in REVIEW_BOOKING_TYPES
            existing_booking = (CoachCalendarEvent.objects.filter(
                event_key=event_key, owner_email__iexact=owner_email,
                learner_id=int(getattr(mirror, "id", pk) or pk),
            ).first() if review_booking else None)
            unchanged_review_replay = bool(
                existing_booking and existing_booking.status == CoachCalendarEvent.STATUS_SCHEDULED
                and (existing_booking.scheduled_date, existing_booking.scheduled_time,
                     existing_booking.duration_minutes) == (scheduled_date, scheduled_time, duration_minutes)
            )
            personal_exclusion = ({
                "exclude_scheduled_date": existing_booking.scheduled_date,
                "exclude_scheduled_time": existing_booking.scheduled_time,
                "exclude_duration_minutes": existing_booking.duration_minutes,
            } if existing_booking else {})
            if not unchanged_review_replay and booking_conflicts(
                kind,
                pk,
                scheduled_date,
                scheduled_time,
                duration_minutes,
                timezone_offset_minutes,
                **({"business_time_zone": "Europe/London"} if review_booking else {}),
                **personal_exclusion,
            ):
                return _error(
                    "That time overlaps an event in your connected personal calendar. Please choose another time.",
                    409,
                )

            if review_booking and not unchanged_review_replay:
                from coach_api.booking_availability import ensure_coach_review_slot_available
                ensure_coach_review_slot_available(owner_email, scheduled_date, scheduled_time,
                                                   duration_minutes, record=existing_booking)

            if aptem_target is not None:
                # An Aptem row is created inside the reservation's own
                # transaction and learner lock, so an attempt refused there
                # leaves no reservation-less placeholder for the next booking
                # to trip over. Everything below still fills this candidate in.
                from .imported_booking import (booking_slot, reserve_imported_booking,
                                               schedule_overlay_for_booking)
                created = existing_booking is None
                record = existing_booking or CoachCalendarEvent(event_key=event_key)
                imported_expected_slot = booking_slot(record)
            else:
                record, created = CoachCalendarEvent.objects.get_or_create(
                    event_key=event_key,
                    defaults={
                        "owner_email": owner_email,
                        "owner_name": owner_name or _s(mirror.coach_name) or "Coach",
                        "learner_id": int(mirror.id),
                        "learner_name": _s(base_event.get("learner")) or learner_name,
                        "learner_email": _s(base_event.get("email")) or learner_email,
                        "event_type": session_type,
                        "sequence": int(base_event.get("sequence") or 1),
                        "target_date": target_date,
                    },
                )
            if aptem_target is not None and not created:
                from coach_api.review_sources import linked_booking
                if linked_booking(aptem_target, [record]) is None or record.owner_email.casefold() != owner_email.casefold():
                    return _error("The imported Review booking identity is inconsistent.", 409)
                if record.scheduled_date and record.scheduled_time:
                    if (record.scheduled_date, record.scheduled_time, record.duration_minutes) != (scheduled_date, scheduled_time, duration_minutes):
                        return _error("This imported Review already has a different booking request. Use reschedule to move it.", 409)
                    # Replay the existing operation. An unreserved placeholder
                    # left by a busy slot may instead retry at another time.
                    record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, base_event)
                    schedule_overlay_for_booking(record.pk, aptem_target)
                    return JsonResponse({"event": _booking_response_event(record, aptem_target),
                                         "warning": _friendly_sync_warning(warning)})
            review_template_id = _s(base_event.get('reviewTemplateId'))
            # A review-driven event must not become scheduled unless canonical
            # linkage is guaranteed. Rescheduling an already-linked row never
            # re-resolves the template -- that instance stays valid even if
            # its template is later archived (see coach_timetable_schedule_event
            # for the matching coach-side rule).
            first_time_linkage = bool(review_template_id) and not record.review_instance_id
            if first_time_linkage:
                try:
                    require_review_template_for_first_linkage(review_template_id)
                except (ReviewTemplateUnavailableError, ReviewCreationBlocked) as exc:
                    return _error(str(exc), 409, code=getattr(exc, "code", None))

            record.owner_email = owner_email
            record.owner_name = owner_name or _s(mirror.coach_name) or record.owner_name or "Coach"
            if aptem_target is None or created:
                record.learner_id = int(mirror.id)
            record.learner_name = _s(base_event.get("learner")) or learner_name
            record.learner_email = _s(base_event.get("email")) or learner_email
            record.event_type = session_type
            record.sequence = int(base_event.get("sequence") or 1)
            record.target_date = target_date
            record.scheduled_date = scheduled_date
            record.scheduled_time = scheduled_time
            record.duration_minutes = duration_minutes
            record.status = CoachCalendarEvent.STATUS_SCHEDULED
            record.notes = notes
            if aptem_target is None or created:
                record.review_template_id = review_template_id
                record.occurrence_number = (
                    None if aptem_target is not None or base_event.get('occurrenceSource') == 'manual'
                    else int(base_event.get('occurrenceNumber') or base_event.get('sequence') or 1)
                )

            if aptem_target is not None:
                record, needs_sync = reserve_imported_booking(
                    record, base_event, imported_expected_slot, notes=notes, expected_notes="",
                )
            else:
                record = persist_calendar_sync_reservation(record, review_event=base_event)
                needs_sync = True
            warning = ""
            if needs_sync:
                record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, base_event)
            if aptem_target is not None:
                # The Coach booking endpoint ends with the same transition.
                # Without it the learner's own booking would leave the form
                # not-scheduled and hide Start/Submit behind an extra step.
                schedule_overlay_for_booking(record.pk, aptem_target)
        except (ReviewTemplateUnavailableError, ReviewCreationBlocked) as exc:
            return _error(str(exc), 409, code=getattr(exc, "code", None))
        except LearnerCalendarConflict as exc:
            return _error(str(exc), 409)
        except CoachAvailabilityUnavailable as exc:
            return _error(str(exc), 503)
        except CalendarSyncInProgress:
            return _error("Calendar event synchronization is already in progress.", 409)
        except DatabaseError as exc:
            logger.exception("learner_calendar_book: generated cycle booking failed")
            return _error(f"Database error: {exc}", 502)

        _mark_imported_review_scheduled(
            payload.get("reviewId"), mirror.id, scheduled_date, scheduled_time,
        )
        if warning:
            logger.error(
                "learner_calendar_book: Graph sync failed for generated %s (owner=%s): %s",
                record.event_key, owner_email, warning,
            )
        return JsonResponse(
            {"event": _booking_response_event(record, aptem_target), "warning": _friendly_sync_warning(warning)},
            status=201 if created else 200,
        )

    try:
        if is_onboarding_review:
            # Each onboarding review is booked once. Without this, a double-click
            # or a retried POST creates a second calendar event and sends the case
            # owner a second invite for the same meeting. Returning the existing
            # booking makes the request idempotent instead.
            existing = (
                CoachCalendarEvent.objects.filter(learner_id=pk, event_type=session_type)
                .exclude(status=CoachCalendarEvent.STATUS_CANCELLED)
                .order_by("-sequence")
                .first()
            )
            if existing is not None:
                return JsonResponse(
                    {
                        "event": _serialize_event(existing),
                        "warning": _friendly_sync_warning(existing.last_graph_sync_error),
                        "alreadyBooked": True,
                    },
                    status=200,
                )

        supplied_key = _s(request.headers.get("Idempotency-Key"))
        if assignment_month:
            booking_kind = "mcm" if session_type == "mcr" else session_type
            idempotency_key = f"learner-book:{booking_kind}:{kind}:{pk}:{idempotency_month}:{imported_review_id or 'legacy'}"
            if assignment_booking and imported_review_id:
                # Booking moves the imported row's planned date, so an earlier
                # booking of this review can carry another month. Reuse it
                # rather than inviting the coach to a second meeting.
                earlier = (
                    CoachCalendarEvent.objects.filter(
                        owner_email=owner_email.strip().lower(),
                        idempotency_key__startswith=f"learner-book:{booking_kind}:{kind}:{pk}:",
                        idempotency_key__endswith=f":{imported_review_id}",
                    )
                    .order_by("-pk")
                    .values_list("idempotency_key", flat=True)
                    .first()
                )
                idempotency_key = earlier or idempotency_key
        elif supplied_key:
            idempotency_key = calendar_idempotency_key(request)
        else:
            # Backward-compatible deterministic identity for existing learner
            # clients. Onboarding review identity intentionally ignores the
            # slot because each review type is a one-time logical operation.
            if is_onboarding_review:
                cancelled_count = CoachCalendarEvent.objects.filter(
                    learner_id=pk,
                    event_type=session_type,
                    status=CoachCalendarEvent.STATUS_CANCELLED,
                ).count()
                logical_parts = onboarding_review_idempotency_parts(kind, pk, session_type, cancelled_count)
            else:
                logical_parts = [kind, str(pk), session_type]
                logical_parts.extend(
                    [
                        scheduled_date.isoformat(),
                        scheduled_time.isoformat(),
                        str(duration_minutes),
                        notes,
                    ]
                )
            digest = hashlib.sha256("\x1f".join(logical_parts).encode("utf-8")).hexdigest()
            idempotency_key = (
                f"learner-book:request:{session_type}:{digest}"
                if session_type in {"mcr", "progress-review"}
                else f"learner-book:{digest}"
            )

        replay = CoachCalendarEvent.objects.filter(
            owner_email=owner_email.strip().lower(),
            idempotency_key=idempotency_key,
        ).first()
        if replay is None and assignment_month and imported_review_id:
            # Reuse bookings created by the previous month-only key format.
            replay = CoachCalendarEvent.objects.filter(
                owner_email=owner_email.strip().lower(),
                idempotency_key=f"learner-book:{booking_kind}:{kind}:{pk}:{idempotency_month}",
            ).first()
        if replay is not None:
            if assignment_month:
                replay.scheduled_date = scheduled_date
                replay.scheduled_time = scheduled_time
                replay.duration_minutes = duration_minutes
                replay.status = CoachCalendarEvent.STATUS_SCHEDULED
                replay = persist_calendar_sync_reservation(replay)
                replay, warning, _attempted = synchronize_reserved_calendar_event(
                    replay.pk, build_booked_calendar_event(replay)
                )
                _mark_imported_review_scheduled(
                    imported_review_id, replay.learner_id, scheduled_date, scheduled_time,
                )
                return JsonResponse(
                    {"event": _serialize_event(replay), "warning": _friendly_sync_warning(warning)},
                    status=200,
                )
            if not booking_request_matches_record(
                replay,
                learner_id=calendar_learner_id,
                session_type=session_type,
                scheduled_date=scheduled_date,
                scheduled_time=scheduled_time,
                duration_minutes=duration_minutes,
                notes=notes,
            ):
                return _error("Idempotency-Key was already used for a different booking.", 409)
            if requires_coach_approval:
                return JsonResponse(
                    {
                        "event": _serialize_event(replay),
                        "warning": "",
                        "approvalRequired": True,
                    },
                    status=200,
                )
            replay, warning, _attempted = synchronize_reserved_calendar_event(
                replay.pk, build_booked_calendar_event(replay)
            )
            if link_report_id is not None:
                link_error = _link_booked_catchup(replay, learner, mirror, pk, link_report_id,
                                                  enforce_change_cutoff=learner_change(request))
                if link_error:
                    return link_error
            return JsonResponse(
                {"event": _serialize_event(replay), "warning": _friendly_sync_warning(warning),
                 **({"linkedReportId": link_report_id} if link_report_id is not None else {})},
                status=200,
            )

        from .calendar_connections import booking_conflicts
        if booking_conflicts(
            kind,
            pk,
            scheduled_date,
            scheduled_time,
            duration_minutes,
            timezone_offset_minutes,
        ):
            return _error(
                "That time overlaps an event in your connected personal calendar. Please choose another time.",
                409,
            )

        record, created = reserve_coach_calendar_booking(
            owner_email=owner_email,
            owner_name=owner_name,
            learner_id=calendar_learner_id,
            learner_name=learner_name,
            learner_email=learner_email,
            session_type=session_type,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            duration_minutes=duration_minutes,
            notes=notes,
            idempotency_key=idempotency_key,
            initial_status=CoachCalendarEvent.STATUS_NOT_SCHEDULED if requires_coach_approval else CoachCalendarEvent.STATUS_SCHEDULED,
            **({"local_mcm_review_id": imported_review_id} if assignment_booking and imported_review_id else {}),
        )
        if requires_coach_approval:
            return JsonResponse(
                {
                    "event": _serialize_event(record),
                    "warning": "",
                    "approvalRequired": True,
                },
                status=201 if created else 200,
            )
        record, warning, _attempted = synchronize_reserved_calendar_event(
            record.pk, build_booked_calendar_event(record)
        )
        if assignment_month and imported_review_id:
            # Imported Aptem rows are not generated calendar events. Persist the
            # booking on the exact Reviews row so a fresh page load reads the
            # same status and date from the database.
            _mark_imported_review_scheduled(
                imported_review_id, mirror.id, scheduled_date, scheduled_time,
            )
    except CalendarSyncInProgress:
        return _error("Calendar event synchronization is already in progress.", 409)
    except LearnerCalendarConflict as exc:
        return _error(str(exc), 409)
    except ValueError as exc:
        return _error(str(exc), 409 if "already used" in str(exc) else 400)
    except DatabaseError as exc:
        logger.exception("learner_calendar_book: booking failed")
        return _error(f"Database error: {exc}", 502)

    if warning:
        # Loud in the log: the learner thinks they have a meeting, and nobody
        # else has been told about it.
        logger.error(
            "learner_calendar_book: Graph sync failed for %s (owner=%s): %s",
            record.event_key, owner_email, warning,
        )

    _record_enrolment_review(record, kind=kind, learner_kind_id=pk, coach_id=owner_staff_id)
    # The programme starts at the first session, so booking one sets the
    # learner's start date. This used to be done by first_session._stamp when
    # the enrolment form did the booking; the learner books it themselves now,
    # and the date has to follow either way -- it is the anchor review
    # scheduling reads, and the enrolment header states it.
    _follow_first_session_start_date(kind, pk, record, scheduled_date)

    if link_report_id is not None:
        link_error = _link_booked_catchup(record, learner, mirror, pk, link_report_id,
                                          enforce_change_cutoff=learner_change(request))
        if link_error:
            return link_error
    return JsonResponse(
        {"event": _serialize_event(record), "warning": _friendly_sync_warning(warning),
         **({"linkedReportId": link_report_id} if link_report_id is not None else {})},
        status=201 if created else 200,
    )


@csrf_exempt
@learner_self_or_staff(kwarg="pk")
def learner_calendar_reschedule(request, kind, pk):
    """Move one of this learner's booked sessions and update its Graph event."""
    from coach_api.views import (
        build_booked_calendar_event,
        CalendarSyncInProgress,
        LearnerCalendarConflict,
        normalize_duration_minutes,
        parse_date_value,
        parse_time_value,
        persist_calendar_sync_reservation,
        sync_scheduled_review_instance,
        synchronize_reserved_calendar_event,
    )

    if request.method not in {"POST", "PATCH"}:
        return _error("Method not allowed.", 405)
    if SOURCE_MODELS.get(kind) is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)
    if not isinstance(payload, dict):
        return _error("Expected a JSON object.", 400)

    event_key = _s(payload.get("eventKey"))
    if not event_key:
        return _error("eventKey is required.", 400)
    try:
        scheduled_date = parse_date_value(payload.get("scheduledDate"))
        scheduled_time = parse_time_value(payload.get("scheduledTime"))
        duration_value = payload.get("durationMinutes")
        duration_minutes = normalize_duration_minutes(duration_value or 60)
        timezone_offset_minutes = int(payload.get("timezoneOffsetMinutes") or 0)
        if not -840 <= timezone_offset_minutes <= 840:
            raise ValueError("timezoneOffsetMinutes is outside the supported range.")
    except ValueError as exc:
        return _error(str(exc), 400)
    if isinstance(scheduled_date, datetime):
        scheduled_date = scheduled_date.date()
    if not scheduled_date:
        return _error("scheduledDate is required.", 400)
    if not scheduled_time:
        return _error("scheduledTime is required.", 400)
    desired_slot = (scheduled_date, scheduled_time, duration_minutes)

    try:
        record = _learner_booking_record(kind, pk, event_key)
        if record is None:
            return _error("Booking not found.", 404)
        if record.status != CoachCalendarEvent.STATUS_SCHEDULED:
            return _error("Only an upcoming scheduled session can be rescheduled.", 409)
        imported_event = getattr(record, '_imported_review_event', None)
        review_reschedule = _s(record.event_type).lower() in REVIEW_BOOKING_TYPES
        if review_reschedule:
            try:
                from coach_api.booking_hours import (
                    validate_review_duration, validate_review_interval, validate_review_time_precision,
                )
                validate_review_time_precision(payload.get("scheduledTime"))
                duration_minutes = validate_review_duration(duration_value)
                if (record.scheduled_date, record.scheduled_time, record.duration_minutes) != (
                    scheduled_date, scheduled_time, duration_minutes
                ):
                    validate_review_interval(scheduled_date, scheduled_time, duration_minutes)
            except ValueError as exc:
                return _error(str(exc), 400)
        if imported_event:
            from .imported_booking import booking_slot, expected_booking, reserve_imported_booking
            from coach_api.validation import ValidationError
            try:
                expected_slot = expected_booking(payload)
            except (ValueError, ValidationError):
                return _error("Refresh this booking before rescheduling; its original date, time and duration are required.", 400)
            if "notes" in payload and payload["notes"] is not None and not isinstance(payload["notes"], str):
                return _error("Booking notes must be text.", 400)
            if "notes" in payload and not isinstance(payload["expectedBooking"].get("notes"), str):
                return _error("Refresh this booking before editing its notes.", 400)
            desired_slot = (scheduled_date, scheduled_time, duration_minutes)
            if booking_slot(record) not in (expected_slot, desired_slot):
                return _error("This booking was changed by another request. Refresh before rescheduling.", 409)
        late_change = _learner_catchup_change_error(request, record)
        if late_change:
            return late_change
        # Checked once the row is known: a first session may move to a Sunday,
        # every other session type keeps the weekday-only calendar.
        date_restriction = booking_date_restriction(
            scheduled_date,
            allow_sunday=_s(record.event_type).lower() == "first-session",
        )
        if date_restriction is None and _s(record.event_type).lower() == "mcr":
            date_restriction = mcm_booking_date_restriction(scheduled_date)
        if date_restriction is not None:
            return _error(date_restriction.message, 400)
        if _s(record.event_type).lower() == "catch-up":
            busy_error = _catchup_time_error(record.owner_email, scheduled_date, scheduled_time,
                                             duration_minutes, exclude_event_key=record.event_key)
            if busy_error:
                return busy_error
        if (
            not imported_event
            and record.scheduled_date == scheduled_date
            and record.scheduled_time == scheduled_time
            and record.duration_minutes == duration_minutes
            and record.sync_state == CoachCalendarEvent.SYNC_SYNCED
        ):
            imported_event = getattr(record, '_imported_review_event', None)
            if getattr(record, 'review_instance_id', '') and not imported_event:
                record = sync_scheduled_review_instance(record)
            _mark_imported_review_scheduled(
                imported_event.get("reviewId") if imported_event else payload.get("reviewId"),
                int(imported_event["learnerId"]) if imported_event else record.learner_id, scheduled_date, scheduled_time,
            )
            return JsonResponse({"event": _booking_response_event(record, imported_event), "warning": ""})

        from .calendar_connections import booking_conflicts
        imported_event = getattr(record, '_imported_review_event', None)
        unchanged_review_slot = bool(review_reschedule and (
            record.scheduled_date, record.scheduled_time, record.duration_minutes
        ) == desired_slot)
        if review_reschedule and not unchanged_review_slot:
            from coach_api.booking_availability import ensure_coach_review_slot_available
            ensure_coach_review_slot_available(record.owner_email, scheduled_date, scheduled_time,
                                               duration_minutes, record=record)
        if not unchanged_review_slot and booking_conflicts(
            kind,
            pk,
            scheduled_date,
            scheduled_time,
            duration_minutes,
            timezone_offset_minutes,
            exclude_scheduled_date=record.scheduled_date,
            exclude_scheduled_time=record.scheduled_time,
            exclude_duration_minutes=record.duration_minutes,
            **({"business_time_zone": "Europe/London"} if review_reschedule else {}),
        ):
            return _error(
                "That time overlaps an event in your connected personal calendar. Please choose another time.",
                409,
            )

        record.scheduled_date = scheduled_date
        record.scheduled_time = scheduled_time
        record.duration_minutes = duration_minutes
        imported_event = getattr(record, '_imported_review_event', None)
        if imported_event:
            record, needs_sync = reserve_imported_booking(
                record, imported_event, expected_slot,
                notes=_s(payload.get("notes"))[:500] if "notes" in payload else None,
                expected_notes=payload["expectedBooking"].get("notes"),
            )
        else:
            record, needs_sync = persist_calendar_sync_reservation(record), True
        warning = ""
        if needs_sync:
            record, warning, _attempted = synchronize_reserved_calendar_event(
                record.pk, imported_event or build_booked_calendar_event(record)
            )
        _mark_imported_review_scheduled(
            imported_event.get("reviewId") if imported_event else payload.get("reviewId"),
            int(imported_event["learnerId"]) if imported_event else record.learner_id, scheduled_date, scheduled_time,
        )
        _follow_first_session_start_date(kind, pk, record, scheduled_date)
    except LearnerCalendarConflict as exc:
        return _error(str(exc), 409)
    except CoachAvailabilityUnavailable as exc:
        return _error(str(exc), 503)
    except CalendarSyncInProgress:
        return _error("Calendar event synchronization is already in progress.", 409)
    except DatabaseError as exc:
        logger.exception("learner_calendar_reschedule: update failed")
        return _error(f"Database error: {exc}", 502)

    _record_enrolment_review(record, kind=kind, learner_kind_id=pk, coach_id=None)
    return JsonResponse(
        {"event": _booking_response_event(record, imported_event), "warning": _friendly_sync_warning(warning)}
    )


def cancel_booking(record):
    """Delete the booking's Outlook/Teams event (Graph emails the cancellation) and keep the row as cancelled."""
    from coach_api.views import delete_calendar_event_from_graph
    warning = delete_calendar_event_from_graph(record)
    record.status = CoachCalendarEvent.STATUS_CANCELLED
    record.scheduled_date = None
    record.scheduled_time = None
    record.meeting_provider = ""
    record.meeting_link = ""
    record.graph_web_link = ""
    record.graph_event_id = ""
    record.last_graph_sync_error = warning
    record.save()
    return warning


@csrf_exempt
# Same reasoning as booking: staff who arranged a session can call it off.
@learner_self_or_staff(kwarg="pk")
def learner_calendar_cancel(request, kind, pk):
    """Cancel a session the learner booked.

        POST /learner_api/calendar/<kind>/<pk>/cancel/
        {"eventKey": "eligibility-review:31:1:2026-08-03"}

    Uses the same Microsoft Graph path as the coach's cancel action
    (coach_api.views.delete_calendar_event_from_graph): deleting the organizer's
    event makes Graph email a cancellation to the attendees -- the learner and,
    for onboarding reviews, the case owner.

    The row is kept and marked cancelled rather than deleted, so the booking
    history survives and learner_onboarding_reviews frees the slot for rebooking.
    """
    from coach_api.views import (
        cancel_reserved_calendar_event,
        LearnerCalendarConflict,
    )

    if request.method != "POST":
        return _error("Method not allowed.", 405)

    if SOURCE_MODELS.get(kind) is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Invalid JSON body.", 400)

    event_key = _s(payload.get("eventKey"))
    if not event_key:
        return _error("eventKey is required.", 400)

    try:
        # Scoped to this learner's own bookings so one learner cannot cancel
        # another's session by guessing an event key.
        record = _learner_booking_record(kind, pk, event_key)
        if record is None:
            return _error("Booking not found.", 404)
        if record.status == CoachCalendarEvent.STATUS_CANCELLED:
            return JsonResponse({"event": _serialize_event(record), "warning": ""})
        if _s(record.event_type).lower() == "catch-up":
            if record.status != CoachCalendarEvent.STATUS_SCHEDULED:
                return _error("Only an upcoming scheduled catch-up can be cancelled.", 409)
            late_change = _learner_catchup_change_error(request, record)
            if late_change:
                return late_change

        is_review_booking = (
            record.event_type in {'mcr', 'progress-review', 'review'}
            or _s(getattr(record, 'review_template_id', ''))
            or _s(getattr(record, 'review_instance_id', ''))
        )
        if is_review_booking:
            if record.status in {CoachCalendarEvent.STATUS_COMPLETED, CoachCalendarEvent.STATUS_AWAITING_SIGNATURE}:
                return _error('A submitted or completed review cannot be cancelled.', 409)
            record, warning = cancel_reserved_calendar_event(record)
            return JsonResponse({'event': _serialize_event(record), 'warning': _friendly_sync_warning(warning)})

        warning = cancel_booking(record)
    except LearnerCalendarConflict as exc:
        return _error(str(exc), 409)
    except DatabaseError as exc:
        logger.exception("learner_calendar_cancel: cancel failed")
        return _error(f"Database error: {exc}", 502)

    _cancel_enrolment_review(record)
    _release_cancelled_catchup(record)

    return JsonResponse(
        {"event": _serialize_event(record), "warning": _friendly_sync_warning(warning)}
    )


@learner_self_or_staff(kwarg="pk")
def learner_onboarding_reviews(request, kind, pk):
    """The three onboarding reviews and whether each is booked.

        GET /learner_api/calendar/<kind>/<id>/onboarding-reviews/

    -> {caseOwner: {name, email} | null,
        reviews: [{type, label, booked, event}], allBooked: bool}

    Drives the learner's Reviews tab after they submit their enrolment: all
    three must be booked before enrolment can be finished.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        learner = model.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)
        booked = {
            record.event_type: record
            for record in CoachCalendarEvent.objects.filter(
                learner_id=pk, event_type__in=ONBOARDING_REVIEW_TYPES
            )
            .exclude(status=CoachCalendarEvent.STATUS_CANCELLED)
            .order_by("scheduled_date")
        }
    except DatabaseError as exc:
        logger.exception("learner_onboarding_reviews: lookup failed")
        return _error(f"Database error: {exc}", 502)

    # Form progress, so a booked review can offer Start / Continue / Completed
    # rather than just "Booked".
    try:
        form_state = {
            row.event_key: row
            for row in EnrolmentReview.objects.filter(
                learner_id=pk, review_type__in=ONBOARDING_REVIEW_TYPES
            )
        }
    except DatabaseError:
        logger.exception("learner_onboarding_reviews: form state lookup failed")
        form_state = {}

    owner_email, owner_name = _case_owner_contact(learner)
    reviews = []
    for type_ in ONBOARDING_REVIEW_TYPES:
        record = booked.get(type_)
        form_row = form_state.get(record.event_key) if record is not None else None
        reviews.append({
            "type": type_,
            "label": ONBOARDING_REVIEW_LABELS[type_],
            "booked": type_ in booked,
            "event": _serialize_event(record) if record is not None else None,
            # Only the eligibility review has a form built so far; the other two
            # stay "Booked" until theirs exist.
            "hasForm": type_ in REVIEW_FORM_TYPES,
            "formStarted": bool(form_row and form_row.started_at),
            "formCompleted": bool(form_row and form_row.form_completed),
            # Drives the Sign button next to View review.
            "learnerSigned": bool(form_row and _s(form_row.learner_signature)),
            "adminSigned": bool(form_row and _s(form_row.admin_signature)),
        })
    return JsonResponse({
        "caseOwner": {"name": owner_name, "email": owner_email} if owner_email else None,
        "reviews": reviews,
        "allBooked": all(r["booked"] for r in reviews),
    })


def _already_started(learner):
    """Whether this learner's programme is already running.

    Active is the whole test. Being Active *is* the statement that the
    programme is under way, so there is no first session left to arrange --
    whether or not a start date was ever written, and whether or not the
    session was booked through this flow at all.

    A start date is deliberately not also required. Active learners who have
    none are real (a learner activated before their session was arranged, or
    one carried in from an older route), and requiring the date sent exactly
    those learners to the booking screen, which is the lockout this branch
    exists to prevent.

    The one thing this must not swallow is the waiting state: a learner with a
    session booked for a future day is *not* Active yet, so they still get the
    holding screen and the day still has to arrive.
    """
    return programme_status(learner).casefold() == "active"


#: An apprentice's enrolment stages, before the one that books the first
#: session. The wizard and the three onboarding reviews happen at Onboarding,
#: the four compliance documents at Delivery; signing the last of those moves
#: them to Ready to enrol (learner_progression), and only then is the first
#: session theirs to book. Blank is the backend's own "Fresh user" default.
APPRENTICE_ENROLMENT_STATUSES = frozenset(
    status.casefold()
    for status in ("", DEFAULT_PROGRAMME_STATUS, "Onboarding", DELIVERY_PROGRAMME_STATUS)
)


def _still_enrolling(kind, learner):
    """Whether this is an apprentice who has not finished enrolling yet.

    The first session comes after every enrolment step for an apprentice, so
    until then there is nothing to book and no programme to hold them out of --
    their own enrolment is the page they need. Commercial learners have no such
    steps and book from the start, as before.

    The record's own learner type wins over the URL's ``kind``: both kinds live
    in one table, and the row is what says which one this learner is.
    """
    learner_type = _s(getattr(learner, "learner_type", "")) or _s(kind)
    if learner_type.casefold() != "apprenticeship":
        return False
    return programme_status(learner).strip().casefold() in APPRENTICE_ENROLMENT_STATUSES


def _may_book_first_session_early(kind, learner):
    """Whether an apprentice in Delivery may already book their first session.

    Delivery is the last enrolment stage: the reviews are done and the four
    compliance documents are being signed. Once the learner's own signatures are
    on all four, the booking opens for them — the employer's and provider's may
    still be outstanding.
    """
    from .learner_progression import learner_signed_compliance_documents

    if programme_status(learner).strip().casefold() != DELIVERY_PROGRAMME_STATUS.casefold():
        return False
    learner_type = _s(getattr(learner, "learner_type", "")) or _s(kind)
    return learner_signed_compliance_documents(learner_type, learner.pk)


@learner_self_or_staff(kwarg="pk")
def learner_first_session(request, kind, pk):
    """Whether this learner has booked their first session, and when.

        GET /learner_api/calendar/<kind>/<id>/first-session/

    -> {caseOwner: {name, email} | null, booked: bool, event: {...} | null,
        startsOn: "YYYY-MM-DD" | null,
        access: "enrolling" | "book" | "waiting" | "open"}

    The first session used to be arranged by whoever enrolled the learner. It is
    now the learner's own first task: they sign in, book it with their case
    owner, and wait until the day.

    ``access`` is the whole gate in one word, decided here rather than in the
    browser -- a learner must not be able to reach their programme early by
    changing a date on their own machine:

    * ``enrolling`` -- an apprentice still working through enrolment
      (``_still_enrolling``). The first session comes after it, so nothing is
      held and nothing is offered yet.
    * ``book``    -- nothing booked yet; the learner books it.
    * ``waiting`` -- booked, but the day has not arrived.
    * ``open``    -- the session day has come (or passed), so the programme runs
      normally from here. Also any Active learner, whose programme is running
      whether or not a session was ever booked through here
      (``_already_started``).

    Computed on every request rather than stored, so nothing has to run
    overnight to let a learner in, and moving the session takes effect at once.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error(f"Unknown kind: {kind!r}. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        learner = model.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)
        record = (
            CoachCalendarEvent.objects.filter(learner_id=pk, event_type=FIRST_SESSION_TYPE)
            .exclude(status=CoachCalendarEvent.STATUS_CANCELLED)
            .order_by("-sequence")
            .first()
        )
    except DatabaseError as exc:
        logger.exception("learner_first_session: lookup failed")
        return _error(f"Database error: {exc}", 502)

    owner_email, owner_name = _case_owner_contact(learner)
    starts_on = record.scheduled_date if record is not None else None

    if first_session_imported_from_aptem(learner):
        # An Aptem learner arrives with a start date and a history behind them,
        # and book_first_session skips them entirely -- so they will never have
        # one of these. Holding them out until a session that is never going to
        # be booked would lock them out of their own programme for good.
        access = "open"
    elif _already_started(learner):
        # An Active learner's programme is already running, so there is no
        # first session left to arrange. Asking them to book one would hold a
        # learner out of a programme they are part-way through -- the same
        # permanent lockout the Aptem case above avoids, reached by a different
        # route (activated outside this booking flow, or before it existed).
        access = "open"
    elif _still_enrolling(kind, learner):
        # Checked before the booking itself: an apprentice who booked under the
        # old order must not be held on the waiting screen, away from the
        # enrolment steps still left. The booking is kept, and takes over again
        # once they reach Ready to enrol.
        access = "enrolling"
    elif starts_on is None:
        access = "book"
    else:
        # The college's own day. A learner abroad must not reach their
        # programme a day early, nor be held out a day late, because of where
        # they happen to be -- the session happens in Kent either way.
        access = "waiting" if starts_on > first_session_uk_today() else "open"

    response = JsonResponse({
        "caseOwner": {"name": owner_name, "email": owner_email} if owner_email else None,
        "booked": record is not None,
        "event": _serialize_event(record) if record is not None else None,
        "startsOn": starts_on.isoformat() if starts_on else None,
        "access": access,
        # Whether the learner may book (or has booked) it from their First
        # Learning Session tab. ``access`` stays "enrolling" for an apprentice
        # in Delivery, so the full-page gate never takes over mid-enrolment.
        "canBook": access != "enrolling" or _may_book_first_session_early(kind, learner),
    })
    # Never cached: ``access`` turns over at midnight UK time, so a stored copy
    # would hold a learner out on the morning of their own session, or let them
    # in the day before.
    response["Cache-Control"] = "private, no-store"
    return response
