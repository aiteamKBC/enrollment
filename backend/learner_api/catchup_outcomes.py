"""Close elapsed coach catch-ups and credit the lectures they recover.

Catch-up only: Monthly Coaching, Progress Reviews, Student Support and live
sessions keep their own lifecycles. A catch-up completes once its booked time
has passed; the missed lecture counts as made up only when the stored Teams
attendance shows the learner in the catch-up for more than three minutes.
"""
from datetime import datetime, timedelta
import logging

from django.db import connections, router
from django.utils import timezone

log = logging.getLogger(__name__)

CATCHUP_EVENT_TYPE = 'catch-up'
# Same presence rule as live-session attendance (more than three minutes).
LEARNER_ATTENDED_SECONDS = 180


def learner_attended_catchup(event_key):
    """True when the stored Teams attendance shows the learner present long enough."""
    from coach_api.models import CoachCalendarEvent
    database = router.db_for_read(CoachCalendarEvent) or 'default'
    with connections[database].cursor() as cursor:
        cursor.execute('''SELECT 1 FROM "Coach".coach_meeting_attendance
            WHERE event_key=%s AND role='learner' AND is_current = true
              AND total_attendance_seconds > %s LIMIT 1''', [event_key, LEARNER_ATTENDED_SECONDS])
        return cursor.fetchone() is not None


def _ended(event, now_local):
    start = datetime.combine(event.scheduled_date, event.scheduled_time)
    return start + timedelta(minutes=event.duration_minutes or 30) <= now_local


def complete_elapsed_catchups(*, owner_email=None, learner_email=None, now=None):
    """Mark scheduled or in-progress catch-ups completed once their time has passed."""
    from coach_api.models import CoachCalendarEvent
    now_local = timezone.localtime(now).replace(tzinfo=None)
    open_statuses = [CoachCalendarEvent.STATUS_SCHEDULED, CoachCalendarEvent.STATUS_IN_PROGRESS]
    events = CoachCalendarEvent.objects.filter(
        event_type__iexact=CATCHUP_EVENT_TYPE, status__in=open_statuses,
        scheduled_date__lte=now_local.date(), scheduled_time__isnull=False,
    )
    if owner_email:
        events = events.filter(owner_email__iexact=owner_email)
    if learner_email:
        events = events.filter(learner_email__iexact=learner_email)
    completed = []
    for event in events.only('pk', 'event_key', 'scheduled_date', 'scheduled_time', 'duration_minutes', 'status'):
        if not _ended(event, now_local):
            continue
        # Conditional update: a coach action saved meanwhile is never overwritten.
        if CoachCalendarEvent.objects.filter(pk=event.pk, status=event.status).update(
                status=CoachCalendarEvent.STATUS_COMPLETED, updated_at=timezone.now()):
            completed.append(event.event_key)
    return completed


def credit_attended_catchups(*, owner_email=None, learner_email=None):
    """Credit booked catch-up recoveries whose completed catch-up the learner attended."""
    from coach_api.models import CoachCalendarEvent
    from curriculum_api.models import LiveSessionAbsence
    from .session_recovery import refresh_catchup_attendance
    booked = LiveSessionAbsence.objects.filter(
        recovery_method=CATCHUP_EVENT_TYPE, recovery_status=LiveSessionAbsence.RECOVERY_CATCHUP_BOOKED,
    )
    if learner_email:
        booked = booked.filter(learner_email__iexact=learner_email)
    keys = set(booked.values_list('recovery_reference', flat=True))
    if not keys:
        return []
    events = CoachCalendarEvent.objects.filter(
        event_key__in=keys, event_type__iexact=CATCHUP_EVENT_TYPE, status=CoachCalendarEvent.STATUS_COMPLETED,
    )
    if owner_email:
        events = events.filter(owner_email__iexact=owner_email)
    credited = []
    for event_key in events.values_list('event_key', flat=True):
        if learner_attended_catchup(event_key):
            refresh_catchup_attendance(event_key)
            credited.append(event_key)
    return credited


def sync_catchup_outcomes(*, owner_email=None, learner_email=None):
    """Complete elapsed catch-ups, then credit attended ones. Never breaks the caller's page."""
    try:
        completed = complete_elapsed_catchups(owner_email=owner_email, learner_email=learner_email)
        credited = credit_attended_catchups(owner_email=owner_email, learner_email=learner_email)
        return completed, credited
    except Exception:
        log.exception('Catch-up outcome sync failed')
        return [], []
