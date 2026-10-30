"""The one-to-one LMS introduction a newly invited learner books with their case owner.

The account invitation carries a "Book my LMS introduction" button. It opens a
public page (the learner has no password yet) that lists the times the case
owner is free and books the chosen one straight away: an ``lms-introduction``
row in the coach calendar, then the same Microsoft Graph sync learner catch-up
bookings use, which puts a Teams meeting on the case owner's calendar with the
learner's email as attendee. The case owner is also emailed, and can move or
cancel the meeting from their timetable like any other booking.

Requests saved before bookings were immediate (status ``not-scheduled``) are
booked by the same path when the learner submits again, or approved by the
case owner on the timetable.

**Retries.** One open request per learner, reserved under an idempotency key,
so a double-submitted form replays the same row; Graph sync is claimed through
``sync_state``, so it never creates a second meeting. A booking whose sync
failed stays saved and is retried when the learner submits the same time again.

**The link.** A signed token (``django.core.signing``) naming the login account
and its email, so the page needs no session and no new table. A changed email
or a deactivated account voids the link; it also expires (``LINK_TTL``).

**CSRF.** As in ``views.py``: ``@csrf_exempt`` plus the ``X-Requested-With``
header, which a cross-site form post cannot set.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from datetime import time as clock_time

from django.core import signing
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import email_azure
from .models import LoginAccount

logger = logging.getLogger("login")

EVENT_TYPE = "lms-introduction"
DURATION_MINUTES = 30
LINK_TTL = timedelta(days=30)
_SALT = "login.lms-introduction"

# Office-hours start times offered on the page, in the business time zone (UK),
# the same wall-clock times the coach timetable stores.
START_TIMES = tuple(
    f"{hour:02d}:{minute:02d}" for hour in range(9, 17) for minute in (0, 30)
)
NOTE_MAX_LENGTH = 500

INVALID_LINK = (
    "This booking link is invalid or has expired. Please ask your programme "
    "team to send you a new invitation."
)
ALREADY_BOOKED = (
    "Your LMS introduction is already booked. To change it, reply to the Teams "
    "invitation or contact your case owner."
)
OWNER_BUSY = "Your case owner is not available at that time. Please choose another time."


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


# ---------------------------------------------------------------------------
# Link
# ---------------------------------------------------------------------------


def make_token(account):
    return signing.dumps({"a": account.id, "e": account.email}, salt=_SALT, compress=True)


def account_for_token(token):
    """The active learner account a token was issued for, or None."""
    try:
        data = signing.loads(str(token or ""), salt=_SALT, max_age=LINK_TTL.total_seconds())
        account_id, email = int(data["a"]), str(data["e"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    account = LoginAccount.objects.filter(pk=account_id, is_active=True, subject_type="learner").first()
    if account is None or account.email != email:
        return None
    return account


def booking_link(account, base_url):
    return f"{base_url}/lms-introduction?token={make_token(account)}"


# ---------------------------------------------------------------------------
# Learner, case owner and availability
# ---------------------------------------------------------------------------


def learner_and_owner(account):
    """``(learner, owner_email, owner_name)`` for a learner account.

    The case owner is the name in ``Created_users.Case_owner``, resolved to an
    email through ``Staff_users`` exactly as onboarding review bookings do. The
    email is '' when the learner has no case owner or the name does not resolve.
    """
    from learner_api.calendar import _case_owner_record
    from learner_api.models import EnrolmentUser

    learner = EnrolmentUser.all_learners.filter(pk=account.subject_id).first()
    if learner is None:
        return None, "", ""
    owner_email, owner_name, _staff_id = _case_owner_record(learner)
    return learner, owner_email, owner_name


def open_request(learner_id):
    """The learner's current (not cancelled) introduction, if any."""
    from coach_api.models import CoachCalendarEvent

    return (
        CoachCalendarEvent.objects.filter(learner_id=learner_id, event_type=EVENT_TYPE)
        .exclude(status=CoachCalendarEvent.STATUS_CANCELLED)
        .order_by("-id")
        .first()
    )


def free_times(owner_email, day):
    """The offered start times the case owner is free for on ``day`` (UK time).

    The catch-up picker's own rule: the UK working day, the case owner's Outlook
    free/busy and their other LMS bookings. Raises
    ``learner_api.coach_availability.AvailabilityUnavailable`` when the calendar
    cannot be read -- the caller must not book blind.
    """
    from learner_api.coach_availability import free_slots, uk_offset_minutes

    free = set(free_slots(owner_email, day, uk_offset_minutes(day), duration=DURATION_MINUTES, uk_working_hours=True))
    return [at for at in START_TIMES if at in free]


def _serialize(record):
    if record is None:
        return None
    booked = record.status != "not-scheduled"
    return {
        "date": record.scheduled_date.isoformat() if record.scheduled_date else None,
        "time": record.scheduled_time.strftime("%H:%M") if record.scheduled_time else None,
        "note": record.notes or "",
        # "requested": saved before bookings were immediate, awaiting the case
        # owner. "scheduled": booked; inviteSent says whether Teams accepted it.
        "status": "scheduled" if booked else "requested",
        "inviteSent": booked and record.sync_state == "synced",
        "meetingLink": (record.meeting_link or "") if booked else "",
    }


def when_label(day, at):
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')} at {at.strftime('%H:%M')} (UK time)"


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def _parse_day(value):
    from learner_api.booking_calendar import booking_date_restriction

    try:
        day = date.fromisoformat(str(value or ""))
    except ValueError:
        raise ValueError("Choose a date for your introduction.") from None
    restriction = booking_date_restriction(day)
    if restriction is not None:
        raise ValueError(restriction.message)
    return day


def _parse_request(payload):
    """``(date, time, note)`` from the page, or raise ValueError with a message."""
    day = _parse_day(payload.get("date"))
    at = str(payload.get("time") or "")
    if at not in START_TIMES:
        raise ValueError("Choose a start time between 09:00 and 16:30.")
    note = str(payload.get("note") or "").strip()[:NOTE_MAX_LENGTH]
    return day, clock_time.fromisoformat(at), note


def _save(account, learner, owner_email, owner_name, day, at, note):
    """Reserve the booking (or book a legacy request). Returns (record, created).

    Durable before any Graph call, like every other coach booking: the row is
    the operation, and ``_sync`` then puts it in Teams.
    """
    from coach_api.models import CoachCalendarEvent
    from coach_api.views import (
        normalize_email,
        persist_calendar_sync_reservation,
        reserve_coach_calendar_booking,
    )

    learner_id = int(learner.pk)
    learner_name = str(learner.username or account.display_name or "").strip()
    existing = open_request(learner_id)
    if existing is not None:
        if existing.status != CoachCalendarEvent.STATUS_NOT_SCHEDULED:
            return existing, False
        # A request saved before bookings were immediate: book it now.
        existing.owner_email = normalize_email(owner_email)
        existing.owner_name = owner_name
        existing.learner_email = account.email
        existing.scheduled_date = existing.target_date = day
        existing.scheduled_time = at
        existing.duration_minutes = DURATION_MINUTES
        existing.notes = note
        existing.status = CoachCalendarEvent.STATUS_SCHEDULED
        return persist_calendar_sync_reservation(existing), False
    # Numbered so a learner whose earlier booking was cancelled can book again,
    # while a double-submitted form replays the same row.
    attempt = CoachCalendarEvent.objects.filter(learner_id=learner_id, event_type=EVENT_TYPE).count() + 1
    return reserve_coach_calendar_booking(
        owner_email=owner_email,
        owner_name=owner_name,
        learner_id=learner_id,
        learner_name=learner_name,
        learner_email=account.email,
        session_type=EVENT_TYPE,
        scheduled_date=day,
        scheduled_time=at,
        duration_minutes=DURATION_MINUTES,
        notes=note,
        idempotency_key=f"{EVENT_TYPE}:{learner_id}:{attempt}",
        initial_status=CoachCalendarEvent.STATUS_SCHEDULED,
    )


def _sync(record):
    """Put the booking in Teams. Returns (record, learner-facing warning or '')."""
    from coach_api.models import CoachCalendarEvent
    from coach_api.views import CalendarSyncInProgress, build_booked_calendar_event, synchronize_reserved_calendar_event
    from learner_api.calendar import _friendly_sync_warning

    try:
        record, warning, _attempted = synchronize_reserved_calendar_event(record.pk, build_booked_calendar_event(record))
    except CalendarSyncInProgress:
        warning = "being sent"
    except Exception:  # noqa: BLE001 - recorded as failed by the sync; the booking stays saved
        logger.exception("LMS introduction %s: Teams sync failed", record.event_key)
        warning = "Microsoft rejected the calendar invite"
    record = CoachCalendarEvent.objects.get(pk=record.pk)
    if record.sync_state == CoachCalendarEvent.SYNC_SYNCED:
        return record, ""
    if warning == "being sent":
        return record, "Your booking is saved and the Teams invitation is being sent. Refresh in a minute to check."
    logger.error("LMS introduction %s: Teams invitation not sent: %s", record.event_key, warning)
    return record, _friendly_sync_warning(warning or "Microsoft rejected the calendar invite")


def _notify_owner(record, *, updated):
    from .invitations import frontend_base_url

    subject, html, text = email_azure.lms_introduction_request_message(
        owner_name=record.owner_name,
        learner_name=record.learner_name,
        learner_email=record.learner_email,
        when_label=when_label(record.scheduled_date, record.scheduled_time),
        note=record.notes,
        timetable_link=f"{frontend_base_url()}/coach/timetable",
        updated=updated,
        invite_sent=record.sync_state == "synced",
    )
    sent, detail = email_azure.send_mail(to=record.owner_email, subject=subject, html_body=html, text_body=text)
    if not sent:
        # The booking stands either way; say so loudly because the case owner
        # has not been told by email.
        logger.error("LMS introduction %s: case owner email not sent: %s", record.event_key, detail)
    return sent


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


@csrf_exempt
def public_request(request):
    """The public booking page's API.

        GET  /login_api/public/lms-introduction/?token=...            page state
        GET  /login_api/public/lms-introduction/?token=...&date=YYYY-MM-DD
             -> {"date": ..., "times": [free start times]}
        POST /login_api/public/lms-introduction/
             {"token": "...", "date": "YYYY-MM-DD", "time": "HH:MM", "note": "..."}
    """
    if request.method not in ("GET", "POST"):
        return _error("Method not allowed.", 405)
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _error("Missing X-Requested-With header.", 403)
    payload = {}
    if request.method == "POST":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            return _error("Invalid JSON body.", 400)
    token = payload.get("token") if request.method == "POST" else request.GET.get("token")

    from coach_api.views import CalendarSyncInProgress, LearnerCalendarConflict
    from learner_api.coach_availability import AvailabilityUnavailable

    try:
        account = account_for_token(token)
        if account is None:
            return _error(INVALID_LINK, 404)
        learner, owner_email, owner_name = learner_and_owner(account)
        if learner is None:
            return _error(INVALID_LINK, 404)
        existing = open_request(learner.pk)
        if not owner_email and existing is None:
            return _error(
                "No case owner has been assigned to you yet, so the introduction cannot be "
                "booked online. Please contact your programme team.", 409,
            )
        state = {
            "learnerName": account.display_name or "",
            "caseOwner": owner_name or (existing.owner_name if existing else ""),
            "durationMinutes": DURATION_MINUTES,
            "times": list(START_TIMES),
        }
        if request.method == "GET":
            if request.GET.get("date") is None:
                return JsonResponse({**state, "request": _serialize(existing)})
            if not owner_email:
                return _error("Your case owner cannot be reached online. Please contact your programme team.", 409)
            try:
                day = _parse_day(request.GET.get("date"))
            except ValueError as exc:
                return _error(str(exc), 400)
            return JsonResponse({"date": day.isoformat(), "times": free_times(owner_email, day)})

        if existing is not None and existing.status != "not-scheduled":
            if existing.sync_state == "synced":
                return JsonResponse({**state, "request": _serialize(existing), "error": ALREADY_BOOKED}, status=409)
            # Booked but Teams did not accept it: submitting again retries the
            # same booking (never a second one), whatever time was picked.
            record, warning = _sync(existing)
            return JsonResponse({**state, "caseOwner": record.owner_name, "request": _serialize(record), "warning": warning})
        if not owner_email:
            return _error("Your case owner cannot be reached online. Please contact your programme team.", 409)
        try:
            day, at, note = _parse_request(payload)
        except ValueError as exc:
            return _error(str(exc), 400)
        exclude = existing.event_key if existing is not None else ""
        from learner_api.coach_availability import catchup_slot_is_free

        if not catchup_slot_is_free(owner_email, day, at, DURATION_MINUTES, exclude_event_key=exclude):
            return _error(OWNER_BUSY, 409)
        record, created = _save(account, learner, owner_email, owner_name, day, at, note)
    except AvailabilityUnavailable:
        return _error("We could not check your case owner's calendar just now. Please try again in a few minutes.", 503)
    except CalendarSyncInProgress:
        return _error("Your booking is already being sent to Teams. Refresh the page in a minute.", 409)
    except LearnerCalendarConflict as exc:
        return _error(str(exc), 409)
    except ValueError:
        # reserve_coach_calendar_booking: the same attempt was saved with other
        # details a moment ago (two tabs). The page reloads and shows that one.
        return _error("Your booking was just changed in another window. Reload the page to see it.", 409)
    except DatabaseError:
        logger.exception("LMS introduction booking failed")
        return _error("Your booking could not be saved. Please try again in a few minutes.", 502)

    if record.status == "not-scheduled" or (not created and record.sync_state == "synced"):
        # Replayed an existing booking: nothing new to send or tell anyone.
        return JsonResponse({**state, "caseOwner": record.owner_name, "request": _serialize(record)})
    record, warning = _sync(record)
    _notify_owner(record, updated=not created)
    return JsonResponse(
        {**state, "caseOwner": record.owner_name, "request": _serialize(record), "warning": warning},
        status=201 if created else 200,
    )
