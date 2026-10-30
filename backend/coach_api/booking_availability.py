"""Coach-wide reservations and read-only Outlook checks for individual bookings."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from django.db import connections

from .models import CoachCalendarEvent
from .booking_reservations import OCCUPANCY_FIELDS, calendar_record_occupies_slot

BUSY_MESSAGE = "Your coach is busy at that time. Please choose another time."
UNAVAILABLE_MESSAGE = "Could not check the coach calendar. Please retry before booking."


class CoachAvailabilityUnavailable(Exception):
    pass


def lock_coach_calendar(owner_email):
    """All individual booking writers take this after the learner lock.

    A transaction-scoped lock also covers an empty calendar. SQLite is only
    used for isolated unit tests; real concurrency is verified on PostgreSQL.
    """
    connection = connections["default"]
    if not connection.in_atomic_block:
        raise RuntimeError("Coach reservations require a transaction.")
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ["coach-calendar:" + owner_email.strip().casefold()])


def ensure_coach_calendar_available(owner_email, day, start, duration, *, exclude_record_id=None):
    from .views import LearnerCalendarConflict
    begin = datetime.combine(day, start)
    end = begin + timedelta(minutes=duration)
    records = CoachCalendarEvent.objects.filter(
        owner_email__iexact=owner_email.strip(),
        scheduled_date__range=(day - timedelta(days=1), end.date()),
        scheduled_time__isnull=False,
    ).exclude(sync_state=CoachCalendarEvent.SYNC_CANCELLED)
    if exclude_record_id is not None:
        records = records.exclude(pk=exclude_record_id)
    for record in records.only(*OCCUPANCY_FIELDS):
        if not calendar_record_occupies_slot(record):
            continue
        other = datetime.combine(record.scheduled_date, record.scheduled_time)
        if begin < other + timedelta(minutes=record.duration_minutes or 60) and end > other:
            # Never disclose another learner's identity or meeting title.
            raise LearnerCalendarConflict(BUSY_MESSAGE)


def ensure_coach_review_slot_available(owner_email, day, start, duration, *, record=None):
    """Recheck the exact Review slot against Outlook hours/free-busy and LMS bookings.

    This runs before reservation transactions. The reservation lock and overlap
    check still run inside the transaction to close the local concurrency race.
    """
    from .views import LearnerCalendarConflict
    from learner_api.coach_availability import free_slots, uk_offset_minutes

    event_key = (getattr(record, "event_key", "") or "").strip()
    record_id = getattr(record, "pk", None)
    try:
        available = free_slots(
            owner_email,
            day,
            uk_offset_minutes(day),
            duration=duration,
            review_hours=True,
            exclude_event_key=event_key,
            exclude_record_id=record_id,
            review_self_record=record,
        )
    except Exception as exc:
        from learner_api.coach_availability import AvailabilityUnavailable
        if isinstance(exc, AvailabilityUnavailable):
            raise CoachAvailabilityUnavailable(UNAVAILABLE_MESSAGE) from exc
        raise
    if start.strftime("%H:%M") not in available:
        raise LearnerCalendarConflict("That Review time is no longer available. Choose another time and retry.")


def _graph_instant(value):
    from learner_api.coach_availability import working_zone
    instant = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=working_zone(value["timeZone"]))
    return instant.astimezone(timezone.utc)


def coach_outlook_busy_intervals(owner_email, day, start, duration, *, record=None):
    """Check exact busy intervals, excluding only the appointment being moved.

    calendarView expands recurring events and lets us exclude by event identity;
    a free/busy grid cannot distinguish this appointment from another meeting.
    No Graph writes occur here. Unknown availability fails closed.
    """
    from .views import LearnerCalendarConflict, microsoft_graph_request, get_graph_settings
    owner = owner_email.strip().casefold()
    begin = datetime.combine(day, start, ZoneInfo("Europe/London")).astimezone(timezone.utc)
    end = begin + timedelta(minutes=duration)
    event_id = (getattr(record, "graph_event_id", "") or "").strip()
    organizer = (getattr(record, "graph_organizer_email", "") or owner).strip().casefold()
    base_path = f"users/{quote(owner, safe='')}/calendarView"
    path = base_path + "?" + urlencode({
        "startDateTime": begin.isoformat(), "endDateTime": end.isoformat(),
        "$select": "id,iCalUId,start,end,showAs,isCancelled", "$top": 1000,
    })
    try:
        own_uid = None
        if event_id and organizer != owner:
            own = microsoft_graph_request("GET", f"users/{quote(organizer, safe='')}/events/{quote(event_id, safe='')}?$select=iCalUId")
            own_uid = own.get("iCalUId")
            if not own_uid:
                raise ValueError("The original meeting identity could not be checked.")
        busy = []
        for _ in range(50):
            result = microsoft_graph_request("GET", path, extra_headers={"Prefer": 'outlook.timezone="UTC"'})
            if not isinstance(result.get("value"), list):
                raise ValueError("Missing calendar response.")
            for event in result["value"]:
                if event.get("isCancelled") or event.get("showAs") == "free":
                    continue
                if (event_id and organizer == owner and event.get("id") == event_id) or (own_uid and event.get("iCalUId") == own_uid):
                    continue
                busy.append((_graph_instant(event["start"]), _graph_instant(event["end"])))
            next_link = result.get("@odata.nextLink")
            if not next_link:
                return busy
            prefix = get_graph_settings()["base_url"].rstrip("/") + "/"
            if not next_link.startswith(prefix) or next_link[len(prefix):].split("?", 1)[0] != base_path:
                raise ValueError("Unexpected calendar continuation.")
            path = next_link[len(prefix):]
        raise ValueError("Calendar response exceeded the page limit.")
    except LearnerCalendarConflict:
        raise
    except Exception as exc:
        raise CoachAvailabilityUnavailable(UNAVAILABLE_MESSAGE) from exc


def ensure_coach_outlook_available(owner_email, day, start, duration, *, record=None):
    from .views import LearnerCalendarConflict
    begin = datetime.combine(day, start, ZoneInfo("Europe/London")).astimezone(timezone.utc)
    end = begin + timedelta(minutes=duration)
    for other_start, other_end in coach_outlook_busy_intervals(owner_email, day, start, duration, record=record):
        if begin < other_end and end > other_start:
            raise LearnerCalendarConflict(BUSY_MESSAGE)
