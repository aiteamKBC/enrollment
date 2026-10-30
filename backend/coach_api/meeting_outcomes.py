"""Display outcome for coach calendar meetings whose booked time has passed.

Read-only: the stored status and every per-type lifecycle (review instances,
signatures, catch-up recovery) are left untouched. A meeting still open after
its booked end shows as ``ended`` unless the stored Teams attendance shows the
learner in it for more than three minutes, which shows it as ``completed``.
"""
from datetime import datetime, timedelta

from django.db import connections, router
from django.utils import timezone

# Same presence rule as live-session attendance (more than three minutes).
LEARNER_ATTENDED_SECONDS = 180
MEETING_TYPES = frozenset({
    'catch-up', 'student-support', 'mcr', 'progress-review', 'review', 'first-session', 'gateway', 'other',
})
OPEN_STATUSES = frozenset({'scheduled', 'in-progress'})


def _parse_time(value):
    for pattern in ('%H:%M', '%H:%M:%S'):
        try:
            return datetime.strptime(str(value), pattern).time()
        except (TypeError, ValueError):
            continue
    return None


def _ended_at(event):
    try:
        day = datetime.strptime(str(event.get('scheduledDate') or ''), '%Y-%m-%d').date()
    except ValueError:
        return None
    start = _parse_time(event.get('scheduledTime'))
    if start is None:
        return None
    return datetime.combine(day, start) + timedelta(minutes=int(event.get('durationMinutes') or 30))


def _applies(event):
    if event.get('reviewSource') == 'aptem' and not event.get('calendarEventKey'):
        return False
    status = str(event.get('status') or '').lower()
    source = str(event.get('source') or '').lower()
    if source not in MEETING_TYPES:
        return False
    # Elapsed catch-ups are closed automatically, so their attendance decides the label.
    return status in OPEN_STATUSES or (status == 'completed' and source == 'catch-up')


def learner_attended_event_keys(event_keys):
    """Event keys whose stored Teams attendance shows the learner present long enough."""
    keys = sorted({str(key) for key in event_keys if key})
    if not keys:
        return set()
    from .models import CoachCalendarEvent
    database = router.db_for_read(CoachCalendarEvent) or 'default'
    with connections[database].cursor() as cursor:
        cursor.execute('''SELECT DISTINCT event_key FROM "Coach".coach_meeting_attendance
            WHERE event_key = ANY(%s) AND role='learner' AND is_current = true
              AND total_attendance_seconds > %s''', [keys, LEARNER_ATTENDED_SECONDS])
        return {row[0] for row in cursor.fetchall()}


def annotate_meeting_outcomes(events, *, now=None):
    """Add ``meetingOutcome`` ('ended' | 'completed' | None) to serialized calendar events."""
    now_local = timezone.localtime(now).replace(tzinfo=None)
    elapsed = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event['meetingOutcome'] = None
        if not _applies(event):
            continue
        ended_at = _ended_at(event)
        if ended_at is not None and ended_at <= now_local:
            elapsed.append(event)
    if not elapsed:
        return events
    attended = learner_attended_event_keys(event.get('calendarEventKey') or event.get('eventKey') for event in elapsed)
    for event in elapsed:
        event['meetingOutcome'] = 'completed' if (event.get('calendarEventKey') or event.get('eventKey')) in attended else 'ended'
    return events
