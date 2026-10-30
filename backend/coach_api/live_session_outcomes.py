"""Display outcome for live sessions whose scheduled time has passed.

Kept apart from coach meetings (see meeting_outcomes): live sessions have
their own Teams attendance, calculated per occurrence into
curriculum.live_session_learner_attendance. This module only reads that
result; it never changes the occurrence, its meeting or its attendance.

A learner's calendar shows ``completed`` when that learner was present, or
watched the whole recording afterwards (``watchedRecording``), and ``ended``
otherwise. Watching does not change the attendance record. A coach's calendar shows ``completed`` when any learner was
present and ``ended`` otherwise.
"""
from datetime import datetime, timedelta

from django.utils import timezone

LIVE_SESSION_SOURCE = 'live-session'
CLOSED_STATUSES = frozenset({'cancelled', 'canceled', 'superseded'})


def _start(event):
    try:
        day = datetime.strptime(str(event.get('scheduledDate') or event.get('date') or ''), '%Y-%m-%d').date()
    except ValueError:
        return None
    text = event.get('scheduledTime')
    if text:
        try:
            return datetime.combine(day, datetime.strptime(str(text), '%H:%M').time())
        except ValueError:
            return None
    hour = event.get('startHour')
    if isinstance(hour, (int, float)):
        return datetime.combine(day, datetime.min.time()) + timedelta(hours=float(hour))
    return None


def _present_occurrences(occurrence_ids, *, learner_profile_id=None, learner_email=''):
    from django.db.models import Q
    from curriculum_api.models import LiveSessionLearnerAttendance
    rows = LiveSessionLearnerAttendance.objects.filter(
        occurrence_id__in=list(occurrence_ids),
        attendance_status=LiveSessionLearnerAttendance.STATUS_PRESENT,
    )
    if learner_profile_id is not None or learner_email:
        learner = Q()
        if learner_profile_id is not None:
            learner |= Q(learner_profile_id=learner_profile_id)
        if learner_email:
            learner |= Q(learner_email__iexact=learner_email.strip())
        rows = rows.filter(learner)
    return set(rows.values_list('occurrence_id', flat=True).distinct())


def annotate_live_session_outcomes(events, *, learner_profile_id=None, learner_email='', now=None,
                                   recording_viewer=None):
    """Add ``meetingOutcome`` ('ended' | 'completed' | None) to elapsed live-session events.

    Pass the learner to judge that learner's attendance; leave it out for a coach view.
    ``recording_viewer`` is (learner kind, source learner id): a fully watched recording
    also completes the session on that learner's calendar.
    """
    now_local = timezone.localtime(now).replace(tzinfo=None)
    elapsed = []
    for event in events:
        if not isinstance(event, dict) or event.get('source') != LIVE_SESSION_SOURCE:
            continue
        event['meetingOutcome'] = None
        if str(event.get('status') or '').lower() in CLOSED_STATUSES:
            continue
        start = _start(event)
        if start and start + timedelta(minutes=int(event.get('durationMinutes') or 60)) <= now_local:
            elapsed.append(event)
    # A slot with no Teams occurrence had no meeting to attend, so it can only have ended.
    tracked = {event['occurrenceId'] for event in elapsed if event.get('occurrenceId')}
    present = _present_occurrences(
        tracked, learner_profile_id=learner_profile_id, learner_email=learner_email,
    ) if tracked else set()
    for event in elapsed:
        event['meetingOutcome'] = 'completed' if event.get('occurrenceId') in present else 'ended'
    missed = {event['occurrenceId'] for event in elapsed if event['meetingOutcome'] == 'ended' and event.get('occurrenceId')}
    if recording_viewer and missed:
        from curriculum_api.recording_views import fully_watched_occurrences
        watched = fully_watched_occurrences(*recording_viewer, missed)
        for event in elapsed:
            if event['meetingOutcome'] == 'ended' and event.get('occurrenceId') in watched:
                event['meetingOutcome'] = 'completed'
                event['watchedRecording'] = True
    return events
