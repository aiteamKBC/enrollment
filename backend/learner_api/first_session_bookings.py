"""Every learner's first-session booking, for the enrolment workspace.

    GET /learner_api/first-session-bookings/

    -> {count, results: [{id, learnerId, source, learnerName, learnerEmail, programme,
        caseOwner: {name, email}, bookedAt, bookedDate, sessionDate,
        sessionTime, durationMinutes, meetingLink, state, status, syncState}]}

Read only. The source is "Coach".coach_calendar_event (``event_type =
'first-session'``), not the ``First_session_booked*`` columns on
enrolment."Created_users": the learner now books the session from their own
calendar (calendar.learner_calendar_book), and that path never writes those
columns -- only the older enrolment-form booking did. The calendar row exists
whichever route made the booking, and it owns the meeting and its join link.

Every row is returned -- past, cancelled and failed-to-sync bookings included --
so ``state`` says which one it is rather than the list hiding it. A booking
Microsoft never accepted has no join link, and it is reported that way, not as
a working meeting.

Each booking is its own row (reserve_coach_calendar_booking creates a new one
per booking), so ``created_at`` is when it was booked; a reschedule moves the
session on the same row and leaves that time alone.
"""
from __future__ import annotations

import logging

from django.db import DatabaseError
from django.db.models.functions import Lower, Trim
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from coach_api.models import CoachCalendarEvent
from login.permissions import require_access

from .constants import ACCESS_ENROLMENT
from .first_session import SESSION_TYPE, UK
from .models import EnrolmentUser, LearnerProfile

logger = logging.getLogger(__name__)


def _text(value):
    return str(value or "").strip()


def _email(value):
    return _text(value).casefold()


def booking_state(record) -> str:
    """One word for where this booking stands.

    Cancelled wins over everything: a cancelled row can still carry a join link
    and a synced flag from before it was cancelled. A completed session is past
    caring about sync. Otherwise a sync that did not finish is what matters --
    local save is not proof that Microsoft accepted the meeting.
    """
    status = _text(record.status).lower()
    sync = _text(record.sync_state).lower()
    if status == CoachCalendarEvent.STATUS_CANCELLED or sync == CoachCalendarEvent.SYNC_CANCELLED:
        return "cancelled"
    if status == CoachCalendarEvent.STATUS_COMPLETED:
        return "completed"
    if sync == CoachCalendarEvent.SYNC_FAILED:
        return "sync-failed"
    if sync == CoachCalendarEvent.SYNC_RECONCILIATION:
        return "needs-reconciliation"
    if sync in (CoachCalendarEvent.SYNC_PENDING, CoachCalendarEvent.SYNC_SYNCING):
        return "sync-pending"
    if status == CoachCalendarEvent.STATUS_NOT_SCHEDULED:
        # Booked as a request the case owner still has to approve.
        return "requested"
    return status or "scheduled"


def resolve_learners(records):
    """Map each calendar row to its enrolment."Created_users" learner.

    ``learner_id`` on a calendar row is not always an enrolment id: the learner's
    own booking stores the enrolment id, but a coach booking stores the id of
    the "Learner".learners profile. The two id spaces overlap, so an id alone
    could name the wrong person -- the row's learner email decides, the same
    way calendar._learner_calendar_record accepts a row as a learner's.

    Order: enrolment id with a matching email, then profile id with a matching
    email (through the profile's enrolment link), then the email alone. A row
    that matches none is returned unmatched rather than guessed.
    """
    ids = {r.learner_id for r in records if r.learner_id}
    emails = {_email(r.learner_email) for r in records} - {""}

    profiles = {
        p.id: p
        for p in LearnerProfile.objects.filter(id__in=ids).only("id", "email", "enrolment_id")
    }
    enrolment_ids = ids | {p.enrolment_id for p in profiles.values() if p.enrolment_id}
    fields = ("id", "username", "email", "programme", "case_owner", "learner_type")
    by_id = {u.id: u for u in EnrolmentUser.all_learners.filter(id__in=enrolment_ids).only(*fields)}
    by_email = {}
    if emails:
        matches = (
            EnrolmentUser.all_learners.annotate(email_key=Lower(Trim("email")))
            .filter(email_key__in=emails)
            .only(*fields)
            .order_by("id")
        )
        for u in matches:
            by_email.setdefault(_email(u.email), u)

    resolved = {}
    for record in records:
        email = _email(record.learner_email)
        learner = by_id.get(record.learner_id)
        if learner is not None and email and _email(learner.email) != email:
            learner = None
        if learner is None:
            profile = profiles.get(record.learner_id)
            if profile is not None and profile.enrolment_id and (not email or _email(profile.email) == email):
                learner = by_id.get(profile.enrolment_id)
        if learner is None and email:
            learner = by_email.get(email)
        resolved[record.pk] = learner
    return resolved


def serialize_booking(record, learner):
    created = record.created_at
    return {
        "id": record.pk,
        # The enrolment id, so the row can open the learner's board. Null when
        # the calendar row could not be tied to an enrolment record.
        "learnerId": learner.id if learner is not None else None,
        # Which board opens it -- the same rule as the Users directory row.
        "source": (
            ("commercial" if _text(learner.learner_type) == "commercial" else "apprenticeship")
            if learner is not None else None
        ),
        "learnerName": _text(learner.username if learner is not None else "") or _text(record.learner_name),
        "learnerEmail": _text(learner.email if learner is not None else "") or _text(record.learner_email),
        "programme": _text(learner.programme) if learner is not None else "",
        # Who the meeting is with -- the case owner it was booked against. Falls
        # back to the learner's recorded case owner for a row with no name.
        "caseOwner": {
            "name": _text(record.owner_name) or (_text(learner.case_owner) if learner is not None else ""),
            "email": _text(record.owner_email),
        },
        "bookedAt": created.isoformat() if created else None,
        # The UK day it was booked on, so filtering by day does not depend on
        # the browser's time zone.
        "bookedDate": timezone.localtime(created, UK).date().isoformat() if created else None,
        # UK wall clock, as the booking was made.
        "sessionDate": record.scheduled_date.isoformat() if record.scheduled_date else None,
        "sessionTime": record.scheduled_time.strftime("%H:%M") if record.scheduled_time else None,
        "durationMinutes": record.duration_minutes,
        "meetingLink": _text(record.meeting_link),
        "state": booking_state(record),
        "status": _text(record.status),
        "syncState": _text(record.sync_state),
    }


@csrf_exempt
@require_access(ACCESS_ENROLMENT)
def first_session_bookings(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        records = list(
            CoachCalendarEvent.objects.filter(event_type=SESSION_TYPE)
            .order_by("-created_at", "-pk")
        )
        learners = resolve_learners(records)
    except DatabaseError:
        logger.exception("first_session_bookings: lookup failed")
        return JsonResponse({"error": "First-session bookings could not be loaded. Please try again."}, status=502)
    rows = [serialize_booking(record, learners.get(record.pk)) for record in records]
    return JsonResponse({"count": len(rows), "results": rows})
