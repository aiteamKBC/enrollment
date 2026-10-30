"""Teams calendar integrity: what each session's meeting is, whether it is sound, and who did it.

Four separate questions are answered for every session of a module calendar,
and they are never folded into one field:

* **Meeting type** -- ``main`` (an occurrence of the module's recurring series),
  ``additional`` (an Additional Week Meeting, separate by design) or
  ``standalone`` (a session that sits on an event of its own, apart from the
  series learners were invited to).
* **Link integrity** -- ``verified``, ``link_conflict``, ``missing``,
  ``verification_required`` or ``verification_failed``.
* **Calendar membership** -- ``in_plan``, ``not_in_plan`` (still on Teams) or
  ``manually_cancelled``.
* **Lifecycle** -- the session's own status, plus any integrity issue with it
  (completed before it started, completed with no evidence).

Why this exists: a recurring occurrence Microsoft had deleted used to be
"restored" by quietly creating a standalone meeting with a new join link, and
learners ended up in two rooms. That creation is gone from every save path. A
missing occurrence is now reported here as *Missing from Teams -- resolution
required*, and only an explicit, previewed and confirmed resolution changes
anything.

Rules this module keeps:

* Reading health never calls Microsoft. It reads the LMS rows, the last saved
  status-check snapshot and the integrity event log; a stale snapshot is
  labelled stale, never presented as a fresh check.
* Nothing here cancels or deletes a Microsoft event, and nothing sends LMS
  email. The one resolution that writes to Microsoft (create a replacement) is
  explicit, previewed, confirmed, idempotent and says plainly that Microsoft
  may email the people on it.
* Historical attribution is never invented. A meeting with no recorded origin
  is shown as ``System -- original trigger unknown``.

The event log lives in ``curriculum.teams_calendar_integrity_events``
(``backend/sql/2026-10-08_teams_calendar_integrity_events.sql``). Until that
table is applied every write here is skipped with a warning and health says
attribution is unavailable; no save is ever failed for want of it.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib import parse as urllib_parse

from django.db import DatabaseError, connection, transaction

logger = logging.getLogger(__name__)

EVENTS_TABLE = 'curriculum.teams_calendar_integrity_events'

# ------------------------------------------------------------------ vocabulary

MEETING_MAIN = 'main'
MEETING_ADDITIONAL = 'additional'
MEETING_STANDALONE = 'standalone'

INTEGRITY_VERIFIED = 'verified'
INTEGRITY_CONFLICT = 'link_conflict'
INTEGRITY_MISSING = 'missing'
INTEGRITY_REQUIRED = 'verification_required'
INTEGRITY_FAILED = 'verification_failed'

MEMBERSHIP_IN_PLAN = 'in_plan'
MEMBERSHIP_NOT_IN_PLAN = 'not_in_plan'
MEMBERSHIP_CANCELLED = 'manually_cancelled'

HEALTH_HEALTHY = 'healthy'
HEALTH_ATTENTION = 'attention_required'
HEALTH_PENDING = 'verification_pending'
HEALTH_FAILED = 'verification_failed'

# Who started something. Kept apart from the executor: the LMS service account
# executes every Microsoft write, and "System" alone says nothing about why.
ORIGIN_MANUAL = 'manual_user'
ORIGIN_USER_BACKGROUND = 'user_initiated_background'
ORIGIN_SCHEDULED = 'scheduled_automation'
ORIGIN_SYSTEM = 'backend_system'
ORIGIN_UNKNOWN = 'unknown_historical'

# Event types written to the log.
EVENT_RECONCILED = 'calendar_reconciled'
EVENT_MISSING = 'occurrence_missing_detected'
EVENT_VERIFIED = 'verification_succeeded'
EVENT_VERIFY_FAILED = 'verification_failed'
EVENT_REPLACEMENT = 'replacement_created'
EVENT_REPLACEMENT_UNCERTAIN = 'replacement_creation_uncertain'
EVENT_REPLACEMENT_FAILED = 'replacement_creation_failed'
EVENT_REASSOCIATED = 'session_reassociated'
EVENT_INTENTIONAL = 'marked_intentional'
EVENT_KEPT = 'kept_existing'
EVENT_COMPLETED = 'lifecycle_completed'
EVENT_PREMATURE_RUN = 'lifecycle_premature_run_ignored'
EVENT_REOPENED = 'lifecycle_reopened'

RESOLUTION_EVENTS = (EVENT_INTENTIONAL, EVENT_KEPT)

#: A linked run that ended this long before the scheduled start is a confirmed lifecycle error.
PREMATURE_CONFIRMED_GAP = timedelta(hours=12)

#: A status check older than this is shown as stale, never as a fresh check.
STALE_AFTER = timedelta(hours=24)

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

FILTERS = ('all', 'main', 'additional', 'standalone', 'link_conflict', 'missing', 'lifecycle', 'audit', 'not_in_plan')


# --------------------------------------------------------------------- helpers

def clean(value):
    return '' if value is None else str(value).strip()


def as_utc(value):
    """Aware UTC from a stored (naive UTC) timestamp, an aware one or ISO text."""
    if value is None or value == '':
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def minute_key(value):
    instant = as_utc(value)
    return instant.replace(second=0, microsecond=0).isoformat(timespec='minutes') if instant else ''


def iso(value):
    instant = as_utc(value)
    return instant.isoformat() if instant else ''


def join_ref(join_url):
    """A short, stable reference to a join link that is not the link itself.

    Join links are the capability to enter a meeting, so the log keeps a digest:
    two references are equal exactly when the links are, and the log can never
    leak a way in.
    """
    url = clean(join_url)
    return hashlib.sha256(url.encode('utf-8')).hexdigest()[:16] if url else ''


def parse_json(value, fallback):
    if value is None or value == '':
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def meeting_reference(event_id='', join_url='', online_meeting_id=''):
    return {'eventId': clean(event_id), 'onlineMeetingId': clean(online_meeting_id), 'joinRef': join_ref(join_url)}


def row_meeting_reference(row):
    return meeting_reference(row.get('graph_event_id'), row.get('join_url'), row.get('online_meeting_id'))


# ---------------------------------------------------------------- attribution

def current_attribution(job_id='', trigger=''):
    """Who started what is being recorded, read from the request -- never guessed.

    * a signed-in person acting directly            -> manual_user
    * the LMS working inside a person's request     -> user_initiated_background
    * a job/worker with no person behind it         -> scheduled_automation
    * anything else                                 -> backend_system
    """
    from . import versioning

    context = versioning.current_context()
    actor = versioning.current_actor()
    triggered = context.get('triggered_by') or {}
    kind = versioning.actor_type()
    initiated_by = {'email': clean(actor.get('email')), 'name': clean(actor.get('name'))}
    if kind == versioning.ACTOR_USER and initiated_by['email']:
        origin = ORIGIN_MANUAL
    elif clean(triggered.get('email')):
        origin = ORIGIN_USER_BACKGROUND
        initiated_by = {'email': clean(triggered.get('email')), 'name': clean(triggered.get('name'))}
    elif job_id or kind == versioning.ACTOR_JOB:
        origin = ORIGIN_SCHEDULED
        initiated_by = {'email': '', 'name': ''}
    else:
        origin = ORIGIN_SYSTEM
        initiated_by = {'email': '', 'name': ''}
    metadata = context.get('metadata') or {}
    return {
        'origin': origin,
        'initiatedBy': initiated_by,
        'trigger': clean(trigger) or clean(metadata.get('page_label')) or clean(metadata.get('request_path')),
        'jobId': clean(job_id),
    }


def new_correlation_id():
    return uuid.uuid4().hex


# --------------------------------------------------------------- the event log

_AVAILABLE = {}


def events_available():
    """Whether the integrity log table exists. Probed once per process, never raises."""
    if 'events' in _AVAILABLE:
        return _AVAILABLE['events']
    available = False
    try:
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('curriculum.teams_calendar_integrity_events')")
                available = bool(cursor.fetchone()[0])
    except Exception:
        # Any failure to probe means "not available": a save never fails for it.
        available = False
    if available:
        _AVAILABLE['events'] = True
    return available


def reset_availability():
    _AVAILABLE.clear()


def build_event(live_session_id, event_type, *, session_number=None, module_catalogue_id='', outcome='success',
                before=None, after=None, detail=None, correlation_id='', job_id='', trigger='', attribution=None):
    """The row one integrity event is stored as. Pure; tested without a database."""
    who = attribution or current_attribution(job_id=job_id, trigger=trigger)
    return {
        'id': f'TCI-{uuid.uuid4().hex.upper()}',
        'live_session_id': clean(live_session_id),
        'module_catalogue_id': clean(module_catalogue_id),
        'session_number': int(session_number) if session_number not in (None, '') else None,
        'event_type': clean(event_type)[:64],
        'origin': who['origin'],
        'initiated_by_email': clean(who['initiatedBy'].get('email'))[:320],
        'initiated_by_name': clean(who['initiatedBy'].get('name'))[:500],
        'executed_by': 'LMS service account',
        'trigger': clean(who.get('trigger'))[:200],
        'job_id': clean(who.get('jobId') or job_id)[:128],
        'correlation_id': clean(correlation_id)[:64],
        'meeting_before': before or {},
        'meeting_after': after or {},
        'outcome': clean(outcome)[:32] or 'success',
        'detail': detail or {},
        'created_at': datetime.now(timezone.utc),
    }


def record_event(live_session_id, event_type, **kwargs):
    """Write one integrity event. Never raises and never fails the caller's save."""
    try:
        event = build_event(live_session_id, event_type, **kwargs)
    except Exception:
        logger.warning('Could not build a Teams integrity event.', exc_info=True)
        return None
    if not events_available():
        logger.warning('Teams integrity log is not provisioned; %s for %s was not recorded. '
                       'Apply backend/sql/2026-10-08_teams_calendar_integrity_events.sql.', event_type, live_session_id)
        return None
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                f'''INSERT INTO {EVENTS_TABLE} (id, live_session_id, module_catalogue_id, session_number, event_type,
                    origin, initiated_by_email, initiated_by_name, executed_by, trigger, job_id, correlation_id,
                    meeting_before, meeting_after, outcome, detail, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s)''',
                [event['id'], event['live_session_id'], event['module_catalogue_id'], event['session_number'],
                 event['event_type'], event['origin'], event['initiated_by_email'], event['initiated_by_name'],
                 event['executed_by'], event['trigger'], event['job_id'], event['correlation_id'],
                 json.dumps(event['meeting_before'], default=str), json.dumps(event['meeting_after'], default=str),
                 event['outcome'], json.dumps(event['detail'], default=str), event['created_at']],
            )
    except Exception:
        logger.warning('Could not record Teams integrity event %s.', event_type, exc_info=True)
        return None
    return event


def read_events(live_session_ids, limit=500):
    """Events for these calendars, oldest first. Empty when the log is not provisioned."""
    ids = [clean(value) for value in live_session_ids if clean(value)]
    if not ids or not events_available():
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f'''SELECT id, live_session_id, module_catalogue_id, session_number, event_type, origin,
                    initiated_by_email, initiated_by_name, executed_by, trigger, job_id, correlation_id,
                    meeting_before, meeting_after, outcome, detail, created_at
                    FROM {EVENTS_TABLE} WHERE live_session_id = ANY(%s)
                    ORDER BY created_at DESC LIMIT %s''',
                [ids, int(limit)],
            )
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    except Exception:
        logger.warning('Could not read the Teams integrity log.', exc_info=True)
        return []
    for row in rows:
        for key in ('meeting_before', 'meeting_after', 'detail'):
            row[key] = parse_json(row.get(key), {})
    rows.reverse()
    return rows


# ------------------------------------------------- missing occurrences on save

def missing_entry(target, reason='not_in_recurring_series'):
    """How a planned session absent from the recurring series is reported by a save."""
    return {
        'sessionNumber': int(target['session_number']),
        'startDateTimeUtc': iso(target['start']),
        'endDateTimeUtc': iso(target['end']),
        'reason': reason,
    }


def missing_numbers(missing):
    return {int(item.get('sessionNumber') or 0) for item in (missing or []) if int(item.get('sessionNumber') or 0) > 0}


def without_missing(targets, missing):
    """Targets minus the sessions reported missing; what can still be verified."""
    numbers = missing_numbers(missing)
    return [target for target in targets if int(target['session_number']) not in numbers]


def known_missing_numbers(live_session_id):
    """Sessions the last reconcile found missing, so a later save does not re-read them as drift."""
    for event in reversed(read_events([live_session_id], limit=200)):
        if event.get('event_type') == EVENT_RECONCILED:
            return missing_numbers((event.get('detail') or {}).get('missing'))
    return set()


def record_reconcile(live_session_id, missing, *, module_catalogue_id='', source='', correlation_id=''):
    """Note what a save found: one summary event, plus one per newly missing session."""
    previous = set()
    previous_starts = {}
    for event in reversed(read_events([live_session_id], limit=200)):
        if event.get('event_type') == EVENT_RECONCILED:
            for item in (event.get('detail') or {}).get('missing') or []:
                previous.add(int(item.get('sessionNumber') or 0))
                previous_starts[int(item.get('sessionNumber') or 0)] = minute_key(item.get('startDateTimeUtc'))
            break
    record_event(live_session_id, EVENT_RECONCILED, module_catalogue_id=module_catalogue_id,
                 correlation_id=correlation_id, trigger=source,
                 detail={'missing': list(missing or []), 'source': source},
                 outcome='attention' if missing else 'success')
    for item in missing or []:
        number = int(item.get('sessionNumber') or 0)
        if number in previous and previous_starts.get(number) == minute_key(item.get('startDateTimeUtc')):
            continue
        record_event(live_session_id, EVENT_MISSING, session_number=number, module_catalogue_id=module_catalogue_id,
                     correlation_id=correlation_id, trigger=source, outcome='attention', detail=item)


def missing_warning_sentence(missing):
    numbers = sorted(missing_numbers(missing))
    if not numbers:
        return ''
    label = ', '.join(str(number) for number in numbers)
    return (f'Session {label} is missing from the Teams recurring series. No separate meeting was created and '
            'nothing was cancelled. Open Calendar health to review it.') if len(numbers) == 1 else (
            f'Sessions {label} are missing from the Teams recurring series. No separate meetings were created and '
            'nothing was cancelled. Open Calendar health to review them.')


# ---------------------------------------------------------------- lifecycle

def run_precedes_session(occurrence, run_start, run_end=None):
    """Whether a Teams run finished before this session was due to start.

    Such a run -- somebody opening a freshly created link, a test call, a
    rehearsal -- is not this session's delivery, so it must not complete it or
    give it attendance. A run with no end yet is not judged here.
    """
    scheduled = as_utc((occurrence or {}).get('scheduled_start'))
    finished = as_utc(run_end) or None
    if scheduled is None or finished is None:
        return False
    started = as_utc(run_start)
    return finished <= scheduled and (started is None or started < scheduled)


# A run starting this long after the session's scheduled end is another day's
# use of the same link, not this session's delivery.
LATE_RUN_TOLERANCE = timedelta(hours=12)
# Somebody alone in the room for a few minutes is checking the link, not teaching.
TEST_OPEN_SECONDS = 5 * 60
RUN_BEFORE_SESSION = 'before_session'
RUN_AFTER_SESSION = 'after_session'
RUN_TEST_OPEN = 'test_open'


def run_not_delivery(occurrence, run_start, run_end, participants=None):
    """Why a finished Teams run is not this session's delivery, or '' when it is.

    Three shapes of the same link being opened for something else: the run
    finished before the session started (a fresh link being tried), it started
    long after the session ended (another day's use), or one person was in the
    room for a few minutes (a test open). None of them may complete the
    session, give it attendance or replace the run that delivered it.
    """
    if as_utc(run_end) is None:
        return ''  # an ongoing run is not judged
    if run_precedes_session(occurrence, run_start, run_end):
        return RUN_BEFORE_SESSION
    started, finished = as_utc(run_start), as_utc(run_end)
    scheduled_start = as_utc((occurrence or {}).get('scheduled_start'))
    scheduled_end = as_utc((occurrence or {}).get('scheduled_end')) or (
        scheduled_start + timedelta(hours=2) if scheduled_start else None)
    if started and scheduled_end and started >= scheduled_end + LATE_RUN_TOLERANCE:
        return RUN_AFTER_SESSION
    count = None if participants is None else int(participants or 0)
    if count is not None and count <= 1 and started and (finished - started).total_seconds() < TEST_OPEN_SECONDS:
        return RUN_TEST_OPEN
    return ''


def off_schedule_runs(row, events):
    """Teams calls on this session's link that were not this session, for the screens to say so.

    The attendance sync ignores a call that finished before the session, began
    long after it, or was one person checking the room, and logs it
    (``note_premature_run``). Teams still shows that call as the meeting having
    happened on its own day, so a person reading the LMS needs to see it too:
    when it was, against when the session is, and that it counts for nothing.

    Judged again against the session's CURRENT time -- a session moved onto
    the day of an earlier call would no longer have it ignored -- and one entry
    per call, however many workers logged it. A short test open is kept as it
    was judged, since the participant count is not in the log. Returns a list
    of ``{runStartUtc, runEndUtc, scheduledStartUtc, reason}``, oldest first.
    """
    number = int((row or {}).get('session_number') or 0)
    found, seen = [], set()
    for event in events or ():
        if event.get('event_type') != EVENT_PREMATURE_RUN or int(event.get('session_number') or 0) != number:
            continue
        if clean(event.get('live_session_id')) and clean(row.get('live_session_id')) and \
                clean(event.get('live_session_id')) != clean(row.get('live_session_id')):
            continue
        detail = event.get('detail') or {}
        start, end = as_utc(detail.get('runStart')), as_utc(detail.get('runEnd'))
        if end is None:
            continue
        logged = clean(detail.get('reason')) or RUN_BEFORE_SESSION
        reason = run_not_delivery(row, start, end) or (RUN_TEST_OPEN if logged == RUN_TEST_OPEN else '')
        key = (iso(start), iso(end))
        if not reason or key in seen:
            continue
        seen.add(key)
        found.append({'runStartUtc': iso(start), 'runEndUtc': iso(end),
                      'scheduledStartUtc': iso(row.get('scheduled_start')), 'reason': reason})
    return sorted(found, key=lambda item: item['runStartUtc'])


def stored_run_is_delivery(occurrence):
    """Whether the run already saved on a completed session stands as its delivery."""
    if clean((occurrence or {}).get('status')).lower() != 'completed':
        return False
    return not run_not_delivery(occurrence, occurrence.get('actual_start'), occurrence.get('actual_end'),
                                occurrence.get('participant_count'))


def run_replaces_stored(occurrence, report_id, run_start, run_end, participants):
    """Whether a delivery run should become the session's saved run.

    A session can have several qualifying runs (a reconnect, a second room).
    The saved run is the one that best evidences delivery -- most people, then
    longest -- so the order Microsoft lists reports in can never let a smaller
    run overwrite the real lesson. The same report always refreshes itself.
    """
    if clean((occurrence or {}).get('attendance_report_id')) == clean(report_id):
        return True
    if not stored_run_is_delivery(occurrence):
        return True
    stored_start, stored_end = as_utc(occurrence.get('actual_start')), as_utc(occurrence.get('actual_end'))
    stored_length = (stored_end - stored_start).total_seconds() if stored_start and stored_end else 0
    length = (as_utc(run_end) - as_utc(run_start)).total_seconds() if as_utc(run_start) and as_utc(run_end) else 0
    return (int(participants or 0), length) > (int(occurrence.get('participant_count') or 0), stored_length)


def completion_to_reopen(occurrence, now=None):
    """Whether a session saved as completed is in fact still to come.

    Only a session whose scheduled start is in the future and whose saved run
    is not its delivery qualifies. A past session is never reopened here: its
    real run replaces the test open on the next sync, and anything else is for
    Calendar health to show and a person to resolve.
    """
    now = now or datetime.now(timezone.utc)
    scheduled_start = as_utc((occurrence or {}).get('scheduled_start'))
    return (clean((occurrence or {}).get('status')).lower() == 'completed' and scheduled_start is not None
            and scheduled_start > now and not stored_run_is_delivery(occurrence))


_NOTED_PREMATURE = set()


def note_premature_run(series, occurrence, report_id, run_start, run_end, job_id='', reason=RUN_BEFORE_SESSION):
    """Log, once per process, that a Teams run was not used to complete a session."""
    key = (clean(occurrence.get('id')), clean(report_id))
    if key in _NOTED_PREMATURE:
        return
    _NOTED_PREMATURE.add(key)
    record_event(clean(series.get('id')), EVENT_PREMATURE_RUN, session_number=occurrence.get('session_number'),
                 module_catalogue_id=clean(series.get('module_catalogue_id')), job_id=job_id,
                 trigger='Teams attendance sync', outcome='ignored',
                 detail={'scheduledStart': iso(occurrence.get('scheduled_start')), 'runStart': iso(run_start),
                         'runEnd': iso(run_end), 'statusKept': clean(occurrence.get('status')), 'reason': reason})


def note_reopened_by_sync(series, occurrence, job_id=''):
    """Log the sync putting a not-yet-delivered session back to Scheduled, with what it held before."""
    record_event(clean(series.get('id')), EVENT_REOPENED, session_number=occurrence.get('session_number'),
                 module_catalogue_id=clean(series.get('module_catalogue_id')), job_id=job_id,
                 trigger='Teams attendance sync',
                 detail={'statusBefore': clean(occurrence.get('status')), 'statusAfter': 'scheduled',
                         'scheduledStart': iso(occurrence.get('scheduled_start')),
                         'actualStart': iso(occurrence.get('actual_start')), 'actualEnd': iso(occurrence.get('actual_end')),
                         'attendanceReportId': clean(occurrence.get('attendance_report_id')),
                         'participantCount': occurrence.get('participant_count'),
                         'reason': 'The saved run was a test open before this session; its attendance rows are kept.'})


def note_completion(series, occurrence, report_id, run_start, run_end, job_id=''):
    """Log the transition to Completed with the run that proved it and who started the sync."""
    record_event(clean(series.get('id')), EVENT_COMPLETED, session_number=occurrence.get('session_number'),
                 module_catalogue_id=clean(series.get('module_catalogue_id')), job_id=job_id,
                 trigger='Teams attendance sync',
                 detail={'statusBefore': clean(occurrence.get('status')) or 'scheduled', 'statusAfter': 'completed',
                         'scheduledStart': iso(occurrence.get('scheduled_start')), 'runStart': iso(run_start),
                         'runEnd': iso(run_end), 'evidence': 'teams_attendance_report'})


def lifecycle_state(row, now=None):
    status = clean(row.get('status')).lower() or 'scheduled'
    now = now or datetime.now(timezone.utc)
    if status in ('cancelled', 'canceled'):
        return 'cancelled'
    if status == 'superseded':
        return 'not_in_plan'
    if status == 'completed':
        return 'completed'
    end = as_utc(row.get('scheduled_end'))
    if end and end < now:
        return 'scheduled_date_passed'
    return 'scheduled'


def lifecycle_issues(row, now=None):
    """Confirmed errors and insufficient-data notices about a session's status."""
    now = now or datetime.now(timezone.utc)
    issues = []
    if clean(row.get('status')).lower() != 'completed':
        return issues
    start = as_utc(row.get('scheduled_start'))
    actual_start = as_utc(row.get('actual_start'))
    actual_end = as_utc(row.get('actual_end'))
    if start and start > now:
        issues.append({'code': 'future_session_completed', 'severity': 'error', 'confirmed': True,
                       'message': 'This session is marked Completed but its scheduled start is still in the future.'})
    if start and actual_end and actual_end <= start:
        # A run hours or days before the session (a link being tried out) is
        # a confirmed error. A short run the same morning is common on sessions
        # that were then delivered -- the tutor testing the room -- so it is a
        # note to check, not a claim that the session never ran.
        early = start - actual_end > PREMATURE_CONFIRMED_GAP
        issues.append({'code': 'completed_before_start', 'severity': 'error' if early else 'warning', 'confirmed': early,
                       'message': 'This session was marked Completed from a Teams run that ended before its scheduled start.'
                       if early else 'The Teams run linked to this session ended shortly before its scheduled start. '
                                     'Check its attendance to confirm it was delivered.'})
    elif start and actual_start and not actual_end and actual_start < start - timedelta(hours=12):
        issues.append({'code': 'completion_timestamp_inconsistent', 'severity': 'warning', 'confirmed': False,
                       'message': 'The recorded run started well before this session was scheduled.'})
    if not clean(row.get('attendance_report_id')) and not actual_start:
        issues.append({'code': 'completion_evidence_missing', 'severity': 'warning', 'confirmed': False,
                       'message': 'This session is marked Completed but no Teams attendance run is linked to it.'})
    return issues


# ------------------------------------------------------------ classification

def legitimate_links(series):
    links = {clean(series.get('join_url'))}
    events = {clean(series.get('graph_event_id'))}
    for entry in parse_json(series.get('calendar_series'), []) or []:
        if isinstance(entry, dict):
            links.add(clean(entry.get('joinUrl')))
            events.add(clean(entry.get('eventId')))
    links.discard('')
    events.discard('')
    return links, events


def membership(row):
    status = clean(row.get('status')).lower()
    if status in ('cancelled', 'canceled'):
        return MEMBERSHIP_CANCELLED
    if status == 'superseded':
        return MEMBERSHIP_NOT_IN_PLAN
    return MEMBERSHIP_IN_PLAN


def meeting_type(series, row):
    if clean(series.get('status')) == 'week-meeting':
        return MEETING_ADDITIONAL
    links, events = legitimate_links(series)
    join = clean(row.get('join_url'))
    if join and join not in links:
        return MEETING_STANDALONE
    event_id = clean(row.get('graph_event_id'))
    if not join and event_id and event_id not in events and clean(row.get('online_meeting_id')):
        return MEETING_STANDALONE
    return MEETING_MAIN


def latest_reconcile(events):
    for event in reversed(events):
        if event.get('event_type') == EVENT_RECONCILED:
            return event
    return None


def latest_verification(events):
    for event in reversed(events):
        if event.get('event_type') in (EVENT_VERIFIED, EVENT_VERIFY_FAILED):
            return event
    return None


def resolution_for(row, events):
    """The latest decision recorded for this session's *current* meeting, if any."""
    reference = join_ref(row.get('join_url'))
    number = int(row.get('session_number') or 0)
    for event in reversed(events):
        if event.get('event_type') not in RESOLUTION_EVENTS or int(event.get('session_number') or 0) != number:
            continue
        if (event.get('meeting_after') or {}).get('joinRef') == reference:
            return event
        return None
    return None


def creation_event_for(row, events):
    reference = join_ref(row.get('join_url'))
    number = int(row.get('session_number') or 0)
    for event in reversed(events):
        if (event.get('event_type') in (EVENT_REPLACEMENT, EVENT_REASSOCIATED)
                and int(event.get('session_number') or 0) == number
                and (event.get('meeting_after') or {}).get('joinRef') == reference):
            return event
    return None


def describe_attribution(event):
    if not event:
        return {'known': False, 'label': 'System — original trigger unknown', 'origin': ORIGIN_UNKNOWN,
                'initiatedBy': '', 'trigger': '', 'jobId': '', 'at': ''}
    who = clean(event.get('initiated_by_name')) or clean(event.get('initiated_by_email'))
    origin = clean(event.get('origin'))
    labels = {
        ORIGIN_MANUAL: f'{who or "A signed-in person"}',
        ORIGIN_USER_BACKGROUND: f'System, started by {who or "a signed-in person"}',
        ORIGIN_SCHEDULED: 'Scheduled automation' + (f' (job {clean(event.get("job_id"))})' if clean(event.get('job_id')) else ''),
        ORIGIN_SYSTEM: 'System',
    }
    return {'known': origin in (ORIGIN_MANUAL, ORIGIN_USER_BACKGROUND, ORIGIN_SCHEDULED),
            'label': labels.get(origin, 'System — original trigger unknown'),
            'origin': origin or ORIGIN_UNKNOWN, 'initiatedBy': who, 'trigger': clean(event.get('trigger')),
            'jobId': clean(event.get('job_id')), 'at': iso(event.get('created_at'))}


def classify_session(series, row, *, events, snapshot, checked_at, now):
    kind = meeting_type(series, row)
    member = membership(row)
    number = int(row.get('session_number') or 0)
    integrity = None
    integrity_basis = ''
    resolution = None
    if member != MEMBERSHIP_CANCELLED:
        if kind == MEETING_ADDITIONAL:
            # Separate by design. The status check does not read additional
            # meetings, so this is never shown as verified by Microsoft.
            own_link = clean(series.get('join_url'))
            integrity = INTEGRITY_REQUIRED if not own_link or clean(row.get('join_url')) in ('', own_link) else INTEGRITY_CONFLICT
            integrity_basis = 'lms'
        elif kind == MEETING_STANDALONE:
            resolution = resolution_for(row, events)
            if resolution and resolution.get('event_type') == EVENT_INTENTIONAL:
                integrity, integrity_basis = INTEGRITY_VERIFIED, 'microsoft_at_resolution'
            else:
                integrity, integrity_basis = INTEGRITY_CONFLICT, 'lms'
        else:
            reconcile = latest_reconcile(events)
            missing_starts = {
                int(item.get('sessionNumber') or 0): minute_key(item.get('startDateTimeUtc'))
                for item in ((reconcile or {}).get('detail') or {}).get('missing') or []
            }
            saved = (snapshot.get('occurrences') or {}).get(clean(row.get('id'))) or {}
            dates = [iso(row.get('scheduled_start')), iso(row.get('scheduled_end'))]
            saved_dates = [iso(value) for value in (saved.get('dates') or [])]
            snapshot_matches = bool(saved) and saved_dates == dates
            unmatched = clean(row.get('id')) in set(((snapshot.get('integrity') or {}).get('unmatched')) or [])
            reconcile_at = as_utc((reconcile or {}).get('created_at'))
            missing_now = number in missing_starts and missing_starts[number] == minute_key(row.get('scheduled_start'))
            if missing_now and not (snapshot_matches and checked_at and reconcile_at and checked_at > reconcile_at):
                integrity, integrity_basis = INTEGRITY_MISSING, 'lms_save'
                resolution = resolution_for(row, events)
            elif unmatched and member == MEMBERSHIP_IN_PLAN:
                integrity, integrity_basis = INTEGRITY_FAILED, 'microsoft'
            elif snapshot_matches and checked_at:
                integrity, integrity_basis = INTEGRITY_VERIFIED, 'microsoft'
            else:
                integrity, integrity_basis = INTEGRITY_REQUIRED, ''
    issues = lifecycle_issues(row, now)
    creation = creation_event_for(row, events) if kind == MEETING_STANDALONE else None
    completion = next((event for event in reversed(events)
                       if event.get('event_type') == EVENT_COMPLETED
                       and int(event.get('session_number') or 0) == number
                       and clean(event.get('live_session_id')) == clean(series.get('id'))), None)
    audit_issues = []
    if kind == MEETING_STANDALONE and not describe_attribution(creation)['known']:
        audit_issues.append({'code': 'unknown_meeting_origin', 'severity': 'notice',
                             'message': 'The original user or trigger that created this separate meeting could not be identified.'})
    if any(issue['confirmed'] for issue in issues) and not describe_attribution(completion)['known']:
        audit_issues.append({'code': 'unknown_completion_origin', 'severity': 'notice',
                             'message': 'The user or job that marked this session Completed could not be identified.'})
    stale = bool(checked_at and now - checked_at > STALE_AFTER)
    return {
        'occurrenceId': clean(row.get('id')),
        'liveSessionId': clean(series.get('id')),
        'sessionNumber': number,
        'startDateTimeUtc': iso(row.get('scheduled_start')),
        'endDateTimeUtc': iso(row.get('scheduled_end')),
        'meetingType': kind,
        'integrity': integrity,
        'integrityBasis': integrity_basis,
        'verificationStale': stale if integrity == INTEGRITY_VERIFIED and integrity_basis == 'microsoft' else False,
        'membership': member,
        'lifecycle': lifecycle_state(row, now),
        'lifecycleIssues': issues,
        'auditIssues': audit_issues,
        # Calls on this link that were not this session (a test, another day's use).
        'offScheduleRuns': off_schedule_runs(row, events),
        'attribution': describe_attribution(creation) if kind == MEETING_STANDALONE else None,
        'resolution': {'type': resolution.get('event_type'), 'at': iso(resolution.get('created_at')),
                       'by': describe_attribution(resolution)['label']} if resolution else None,
        'meeting': {'eventId': clean(row.get('graph_event_id')), 'onlineMeetingId': clean(row.get('online_meeting_id')),
                    'joinUrl': clean(row.get('join_url')) or clean(series.get('join_url')),
                    'joinRef': join_ref(clean(row.get('join_url')) or clean(series.get('join_url')))},
    }


def has_confirmed_issue(session):
    return any(issue.get('confirmed') for issue in session['lifecycleIssues'])


def needs_attention(session):
    if session['membership'] == MEMBERSHIP_CANCELLED:
        return False
    open_link = session['integrity'] in (INTEGRITY_CONFLICT, INTEGRITY_MISSING) and not (
        session['resolution'] and session['resolution']['type'] == EVENT_KEPT)
    return open_link or any(issue['confirmed'] for issue in session['lifecycleIssues'])


def matches_filter(session, name):
    name = clean(name) or 'all'
    if name == 'all':
        return True
    if name in (MEETING_MAIN, MEETING_ADDITIONAL, MEETING_STANDALONE):
        return session['meetingType'] == name
    if name in (INTEGRITY_CONFLICT, INTEGRITY_MISSING):
        return session['integrity'] == name
    if name == 'lifecycle':
        return has_confirmed_issue(session)
    if name == 'audit':
        return bool(session['auditIssues'])
    if name == 'not_in_plan':
        return session['membership'] == MEMBERSHIP_NOT_IN_PLAN
    return True


def summarize(sessions, *, checked_at, last_verification, now, log_available):
    main_rows = [item for item in sessions if item['meetingType'] != MEETING_ADDITIONAL]
    in_plan = [item for item in main_rows if item['membership'] == MEMBERSHIP_IN_PLAN]
    counts = {
        'plannedSessions': len(in_plan),
        'mainSeries': sum(item['meetingType'] == MEETING_MAIN for item in in_plan),
        'additionalMeetings': sum(item['meetingType'] == MEETING_ADDITIONAL and item['membership'] != MEMBERSHIP_CANCELLED
                                  for item in sessions),
        'standaloneReplacements': sum(item['meetingType'] == MEETING_STANDALONE and item['membership'] != MEMBERSHIP_CANCELLED
                                      for item in main_rows),
        'missingOccurrences': sum(item['integrity'] == INTEGRITY_MISSING for item in in_plan),
        'linkConflicts': sum(item['integrity'] == INTEGRITY_CONFLICT for item in sessions
                             if item['membership'] != MEMBERSHIP_CANCELLED),
        'lifecycleIssues': sum(has_confirmed_issue(item) for item in sessions),
        'auditIssues': sum(bool(item['auditIssues']) for item in sessions),
        'notInPlan': sum(item['membership'] == MEMBERSHIP_NOT_IN_PLAN for item in main_rows),
    }
    failed_since = (last_verification and last_verification.get('event_type') == EVENT_VERIFY_FAILED
                    and (not checked_at or as_utc(last_verification.get('created_at')) > checked_at))
    stale = not checked_at or now - checked_at > STALE_AFTER
    unverified = any(item['integrity'] in (INTEGRITY_REQUIRED, INTEGRITY_FAILED) for item in in_plan)
    if any(needs_attention(item) for item in sessions):
        status = HEALTH_ATTENTION
    elif failed_since or any(item['integrity'] == INTEGRITY_FAILED for item in in_plan):
        status = HEALTH_FAILED
    elif stale or unverified:
        status = HEALTH_PENDING
    else:
        status = HEALTH_HEALTHY
    return {
        'status': status,
        'counts': counts,
        'lastVerifiedAt': iso(checked_at),
        'verificationStale': bool(checked_at) and stale,
        'neverVerified': not checked_at,
        'lastVerificationFailure': {
            'at': iso(last_verification.get('created_at')),
            'message': clean((last_verification.get('detail') or {}).get('message')),
        } if failed_since else None,
        'auditLogAvailable': log_available,
    }


def session_title(session):
    return f"Week {session['sessionNumber']}" if session['sessionNumber'] > 0 else 'A former session'


def category_warnings(sessions, *, module_title, checked_at):
    """Three independent warning categories. Clearing one never clears another."""
    warnings = []
    link = [item for item in sessions if item['membership'] != MEMBERSHIP_CANCELLED
            and item['integrity'] in (INTEGRITY_CONFLICT, INTEGRITY_MISSING)]
    if link:
        unresolved = [item for item in link if not item['resolution']]
        warnings.append({
            'category': 'link', 'severity': 'error' if unresolved else 'warning',
            'title': 'Meeting Link Conflict — Review Required',
            'module': module_title,
            'sessions': [item['sessionNumber'] for item in link],
            'explanation': ' '.join(
                f"{session_title(item)} uses a separate Teams meeting from the module's main recurring series."
                if item['integrity'] == INTEGRITY_CONFLICT else
                f"{session_title(item)} is in the plan, but its occurrence is missing from the Teams recurring series."
                for item in link),
            'evidence': [{'sessionNumber': item['sessionNumber'], 'eventId': item['meeting']['eventId'],
                          'joinRef': item['meeting']['joinRef']} for item in link],
            'nextAction': 'Open each session, compare the meetings and choose a resolution. Nothing changes until you confirm.',
            'lastVerifiedAt': iso(checked_at),
        })
    lifecycle = [item for item in sessions if has_confirmed_issue(item)]
    if lifecycle:
        warnings.append({
            'category': 'lifecycle',
            'severity': 'error',
            'title': 'Session Lifecycle Issue — Review Required',
            'module': module_title,
            'sessions': [item['sessionNumber'] for item in lifecycle],
            'explanation': ' '.join(f"{session_title(item)}: {next(i for i in item['lifecycleIssues'] if i['confirmed'])['message']}"
                                    for item in lifecycle),
            'evidence': [{'sessionNumber': item['sessionNumber'], 'codes': [issue['code'] for issue in item['lifecycleIssues']]}
                         for item in lifecycle],
            'nextAction': 'Open the session and review its status history before correcting it.',
            'lastVerifiedAt': '',
        })
    audit = [item for item in sessions if item['auditIssues']]
    if audit:
        warnings.append({
            'category': 'audit', 'severity': 'notice',
            'title': 'Audit Attribution Incomplete',
            'module': module_title,
            'sessions': [item['sessionNumber'] for item in audit],
            'explanation': 'The original user or trigger for an action on '
                           + ', '.join(session_title(item) for item in audit)
                           + ' could not be identified. It is shown as unknown and is not inferred.',
            'evidence': [{'sessionNumber': item['sessionNumber'], 'codes': [issue['code'] for issue in item['auditIssues']]}
                         for item in audit],
            'nextAction': 'No action can recover historical attribution. Changes from now on record who started them.',
            'lastVerifiedAt': '',
        })
    return warnings


# ------------------------------------------------------------ loading health

def load_snapshot(live_session_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('curriculum.teams_calendar_sync_state')")
            if not cursor.fetchone()[0]:
                return {}, None
            cursor.execute('SELECT snapshot, checked_at FROM curriculum.teams_calendar_sync_state WHERE live_session_id = %s',
                           [live_session_id])
            row = cursor.fetchone()
    except DatabaseError:
        return {}, None
    if not row:
        return {}, None
    return parse_json(row[0], {}), as_utc(row[1])


def load_calendar(live_session_id):
    from . import views as v

    series_rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], ensure_tables=False)
    if not series_rows:
        raise LookupError('Calendar not found.')
    series = dict(series_rows[0])
    rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_session_id],
                                 'session_number asc', ensure_tables=False)
    additional = []
    module_id = clean(series.get('module_catalogue_id'))
    if module_id and clean(series.get('status')) != 'week-meeting':
        for extra in v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'module_catalogue_id = %s and status = %s',
                                           [module_id, 'week-meeting'], ensure_tables=False):
            extra_rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s',
                                               [extra['id']], 'session_number asc', ensure_tables=False)
            additional.append((dict(extra), extra_rows))
    module = {}
    if module_id:
        found = v.authoring_fetch_all(v.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', [module_id], ensure_tables=False)
        module = dict(found[0]) if found else {}
    return series, rows, additional, module


def build_health(series, rows, additional, module, *, events, snapshot, checked_at, now=None, log_available=True):
    """The whole health picture for one calendar. Pure: no database and no Microsoft."""
    now = now or datetime.now(timezone.utc)
    sessions = [classify_session(series, row, events=events, snapshot=snapshot, checked_at=checked_at, now=now)
                for row in rows if int(row.get('session_number') or 0) > 0 or membership(row) != MEMBERSHIP_IN_PLAN]
    for extra, extra_rows in additional:
        extra_events = [event for event in events if clean(event.get('live_session_id')) == clean(extra.get('id'))]
        for row in extra_rows:
            item = classify_session(extra, row, events=extra_events, snapshot={}, checked_at=None, now=now)
            item['title'] = clean(extra.get('module_title'))
            sessions.append(item)
    sessions.sort(key=lambda item: (item['startDateTimeUtc'] or '9999', item['meetingType'] == MEETING_ADDITIONAL))
    series_events = [event for event in events if clean(event.get('live_session_id')) == clean(series.get('id'))]
    summary = summarize(sessions, checked_at=checked_at, last_verification=latest_verification(series_events),
                        now=now, log_available=log_available)
    title = clean(module.get('title')) or clean(series.get('module_title'))
    return {
        'liveSessionId': clean(series.get('id')),
        'module': {
            'moduleCatalogueId': clean(series.get('module_catalogue_id')),
            'title': title,
            'programme': clean(module.get('programme_name')),
            'cohort': clean(module.get('cohort_name')),
            'group': clean(module.get('group_name')),
        },
        'mainMeeting': {
            'eventId': clean(series.get('graph_event_id')),
            'onlineMeetingId': clean(series.get('online_meeting_id')),
            'organizer': clean(series.get('organizer_email')),
            'joinUrl': clean(series.get('join_url')),
            'joinRef': join_ref(series.get('join_url')),
        },
        'summary': summary,
        'warnings': category_warnings(sessions, module_title=title, checked_at=checked_at),
        'sessions': sessions,
    }


def page_of(sessions, filter_name='all', page=1, page_size=DEFAULT_PAGE_SIZE):
    chosen = [item for item in sessions if matches_filter(item, filter_name)]
    size = max(1, min(MAX_PAGE_SIZE, int(page_size or DEFAULT_PAGE_SIZE)))
    pages = max(1, -(-len(chosen) // size))
    page = max(1, min(pages, int(page or 1)))
    return {'items': chosen[(page - 1) * size: page * size], 'page': page, 'pageSize': size,
            'pages': pages, 'total': len(chosen), 'filter': filter_name if filter_name in FILTERS else 'all'}


def health_for(live_session_id, now=None):
    series, rows, additional, module = load_calendar(live_session_id)
    snapshot, checked_at = load_snapshot(live_session_id)
    ids = [clean(series.get('id')), *[clean(extra.get('id')) for extra, _rows in additional]]
    available = events_available()
    events = read_events(ids, limit=1000)
    return build_health(series, rows, additional, module, events=events, snapshot=snapshot,
                        checked_at=checked_at, now=now, log_available=available), (series, rows, events)


# ---------------------------------------------------------- session detail

def evidence_for(occurrence_id):
    from . import views as v

    if not occurrence_id:
        return {'attendanceRecords': 0, 'recordings': 0, 'transcripts': 0}
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT count(*) FROM {v.authoring_table_name(v.LIVE_SESSION_ATTENDANCE_TABLE)} WHERE occurrence_id = %s',
                           [occurrence_id])
            attendance = cursor.fetchone()[0]
            cursor.execute(f'''SELECT artifact_type, count(*) FROM {v.authoring_table_name(v.LIVE_SESSION_ARTIFACTS_TABLE)}
                               WHERE occurrence_id = %s GROUP BY artifact_type''', [occurrence_id])
            artifacts = dict(cursor.fetchall())
    except DatabaseError:
        return {'attendanceRecords': None, 'recordings': None, 'transcripts': None}
    return {'attendanceRecords': int(attendance or 0), 'recordings': int(artifacts.get('recording') or 0),
            'transcripts': int(artifacts.get('transcript') or 0)}


def timeline_entry(event):
    who = describe_attribution(event)
    return {
        'id': clean(event.get('id')),
        'type': clean(event.get('event_type')),
        'at': iso(event.get('created_at')),
        'sessionNumber': event.get('session_number'),
        'origin': who['origin'],
        'by': who['label'],
        'executedBy': clean(event.get('executed_by')),
        'trigger': clean(event.get('trigger')),
        'jobId': clean(event.get('job_id')),
        'correlationId': clean(event.get('correlation_id')),
        'before': event.get('meeting_before') or {},
        'after': event.get('meeting_after') or {},
        'outcome': clean(event.get('outcome')),
        'detail': event.get('detail') or {},
    }


LIFECYCLE_TYPES = (EVENT_COMPLETED, EVENT_PREMATURE_RUN, EVENT_REOPENED)


def session_detail(health, row, events, series):
    number = int(row.get('session_number') or 0)
    session = next((item for item in health['sessions']
                    if item['occurrenceId'] == clean(row.get('id'))), None)
    if session is None:
        raise LookupError('Session not found.')
    own = [event for event in events if clean(event.get('live_session_id')) == clean(series.get('id'))
           and (event.get('session_number') in (None, number))]
    main = health['mainMeeting']
    separate = session['meeting']['joinRef'] != main['joinRef']
    return {
        **session,
        'module': health['module'],
        'mainMeeting': main,
        'actualStartUtc': iso(row.get('actual_start')),
        'actualEndUtc': iso(row.get('actual_end')),
        'attendanceReportLinked': bool(clean(row.get('attendance_report_id'))),
        'participantCount': int(row.get('participant_count') or 0),
        'createdAt': iso(row.get('created_at')),
        'updatedAt': iso(row.get('updated_at')),
        'evidence': evidence_for(clean(row.get('id'))),
        'separateFromMain': separate,
        'compare': {
            'main': {**main, 'membership': 'Recurring series master'},
            'session': {**session['meeting'],
                        'membership': {MEETING_MAIN: 'Occurrence of the recurring series',
                                       MEETING_STANDALONE: 'A separate event, not in the recurring series',
                                       MEETING_ADDITIONAL: 'Additional Week Meeting (separate by design)'}[session['meetingType']],
                        'startDateTimeUtc': session['startDateTimeUtc'], 'endDateTimeUtc': session['endDateTimeUtc']},
            'implications': ([
                'Learners using a previously saved link, an Outlook invitation or the Teams chat for the main series '
                'may enter a different meeting room from those using this session’s link.',
                'Changing the LMS join link does not update Outlook invitations people already hold.',
            ] if separate and session['meetingType'] != MEETING_ADDITIONAL else []),
        },
        'auditHistory': [timeline_entry(event) for event in own if event.get('event_type') not in LIFECYCLE_TYPES],
        'statusHistory': [timeline_entry(event) for event in own if event.get('event_type') in LIFECYCLE_TYPES],
        'resolutions': resolution_options(session),
    }


# --------------------------------------------------------------- resolutions

ACTION_KEEP = 'keep_existing'
ACTION_INTENTIONAL = 'mark_intentional'
ACTION_REASSOCIATE = 'reassociate_main'
ACTION_REPLACE = 'create_replacement'
ACTION_REOPEN = 'reopen_completion'
ACTION_RECHECK = 'recheck_occurrence'
ACTIONS = (ACTION_KEEP, ACTION_INTENTIONAL, ACTION_REASSOCIATE, ACTION_REPLACE, ACTION_REOPEN, ACTION_RECHECK)

ACTION_LABELS = {
    ACTION_KEEP: 'Keep existing arrangement',
    ACTION_INTENTIONAL: 'Mark as intentional separate meeting',
    ACTION_REASSOCIATE: 'Reassociate with the main recurring meeting',
    ACTION_REPLACE: 'Create a replacement meeting',
    ACTION_REOPEN: 'Return session to Scheduled',
    ACTION_RECHECK: 'Re-check Microsoft (read-only)',
}


def resolution_options(session, evidence=None):
    """Which actions apply to this session, each with whether it may run now and why not."""
    options = []
    if session['membership'] == MEMBERSHIP_CANCELLED or session['meetingType'] == MEETING_ADDITIONAL:
        return options
    if session['integrity'] == INTEGRITY_CONFLICT:
        options += [ACTION_KEEP, ACTION_INTENTIONAL, ACTION_REASSOCIATE]
    elif session['integrity'] == INTEGRITY_MISSING:
        options += [ACTION_RECHECK, ACTION_KEEP, ACTION_REPLACE]
    if any(issue['code'] in ('completed_before_start', 'future_session_completed') for issue in session['lifecycleIssues']):
        options.append(ACTION_REOPEN)
    return [{'action': action, 'label': ACTION_LABELS[action], 'readOnly': action == ACTION_RECHECK} for action in options]


def preview_token(row, action):
    """Binds a confirmation to the exact state it was previewed against."""
    material = json.dumps([clean(row.get('id')), clean(row.get('status')), clean(row.get('graph_event_id')),
                           join_ref(row.get('join_url')), clean(row.get('online_meeting_id')),
                           iso(row.get('scheduled_start')), iso(row.get('scheduled_end')), action])
    return hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]


def replacement_transaction_id(live_session_id, row):
    """Deterministic, so Microsoft de-duplicates a retried create of the same replacement."""
    seed = f'lms-replacement:{clean(live_session_id)}:{int(row.get("session_number") or 0)}:{minute_key(row.get("scheduled_start"))}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


def build_preview(action, session, row, series, evidence):
    """What an action will and will not do. Pure; every claim here is tested."""
    has_evidence = bool(evidence.get('attendanceRecords') or evidence.get('recordings') or evidence.get('transcripts')
                        or clean(row.get('attendance_report_id')))
    blocked = ''
    changes, unchanged, follow_up = [], [], []
    microsoft = {'creates': False, 'updates': False, 'cancels': False, 'reads': False, 'mayEmail': False}
    lms_email = False
    allowed = {item['action'] for item in resolution_options(session)}
    if action not in allowed:
        blocked = 'This action does not apply to this session in its current state.'
    if action == ACTION_KEEP:
        changes.append('Records that this arrangement was reviewed and kept.')
        unchanged += ['Both meeting links', 'Every Microsoft event and invitation', 'Attendance, recordings and transcripts']
    elif action == ACTION_INTENTIONAL:
        microsoft['reads'] = True
        changes.append('Reads the separate meeting from Microsoft to confirm it exists and is not cancelled, then records '
                       'it as an intentional separate meeting for this session.')
        unchanged += ['Both meeting links', 'Every Microsoft event and invitation', 'Attendance, recordings and transcripts']
    elif action == ACTION_REASSOCIATE:
        microsoft['reads'] = True
        changes.append("Points this LMS session at the module's main recurring meeting, once Microsoft confirms the "
                       'recurring series has an occurrence on this date.')
        unchanged += ['The separate meeting stays on Teams: nothing is cancelled', 'Outlook invitations people hold']
        follow_up += ['People who saved the separate meeting’s link may still use it.',
                      'If the separate meeting should go, cancel it in Teams yourself; Microsoft will email its invitees.']
        if has_evidence:
            blocked = ('Attendance, a recording or a transcript is already linked to this session’s current meeting. '
                       'Reassociating would separate that evidence from future syncs, so it is blocked.')
    elif action == ACTION_REPLACE:
        microsoft.update(reads=True, creates=True, mayEmail=True)
        changes += ['Checks Microsoft that the occurrence is still missing and that no separate meeting already exists.',
                    'Creates ONE separate Teams meeting for this session with its own, different join link, '
                    'with the module’s invited people and meeting options.',
                    'Points this LMS session at the new meeting.']
        unchanged += ['The recurring series and every other session', 'No meeting is cancelled']
        follow_up += ['Microsoft may send an invitation for the new meeting to everyone on it. The LMS cannot '
                      'guarantee it stays silent.',
                      'People who use the main series link on this date will not reach the new meeting. Tell learners '
                      'which link to use.']
        if not clean(series.get('organizer_email')) or not clean(series.get('graph_event_id')):
            blocked = 'This calendar is missing its organiser or event identity.'
    elif action == ACTION_REOPEN:
        changes.append('Sets this session back to Scheduled.')
        unchanged += ['Attendance records, recordings and transcripts stay where they are',
                      'Every Microsoft event and invitation']
        follow_up.append('A later sync will not complete it again from a Teams run that ended before its start.')
    elif action == ACTION_RECHECK:
        microsoft['reads'] = True
        changes.append('Reads the recurring series from Microsoft and reports whether this occurrence is there now.')
        unchanged.append('Everything: this check only reads.')
    return {
        'action': action,
        'label': ACTION_LABELS.get(action, action),
        'sessionNumber': int(row.get('session_number') or 0),
        'changes': changes,
        'unchanged': unchanged,
        'followUp': follow_up,
        'microsoft': microsoft,
        'lmsEmail': lms_email,
        'blocked': blocked,
        'requiresConfirmation': action != ACTION_RECHECK,
        'previewToken': preview_token(row, action),
    }


# ----------------------------------------------- Microsoft reads for resolutions

def graph():
    from coach_api.views import microsoft_graph_request
    return microsoft_graph_request


def owner_key(series):
    from . import views as v
    organizer = clean(series.get('organizer_email')) or v.teams_meeting_default_organizer()
    return urllib_parse.quote(organizer, safe='')


def graph_instant(item, field):
    value = (item or {}).get(field) or {}
    instant = as_utc(clean(value.get('dateTime')))
    if instant and clean(value.get('timeZone')) not in ('', 'UTC'):
        from .teams_calendar_checks import event_instant
        try:
            instant = event_instant(item, field)
        except Exception:
            return None
    return instant


def series_instance_on(series, row, request=None):
    """The recurring series' instance on this session's start, read-only. None when absent."""
    request = request or graph()
    start = as_utc(row.get('scheduled_start'))
    end = as_utc(row.get('scheduled_end'))
    query = urllib_parse.urlencode({'startDateTime': (start - timedelta(hours=2)).isoformat(),
                                    'endDateTime': (end + timedelta(hours=2)).isoformat(), '$top': 50})
    path = f'users/{owner_key(series)}/events/{urllib_parse.quote(clean(series.get("graph_event_id")), safe="")}/instances?{query}'
    response = request('GET', path) or {}
    for item in response.get('value') or []:
        if minute_key(graph_instant(item, 'start')) == minute_key(start) and item.get('isCancelled') is not True:
            return item
    return None


def separate_events_at(series, row, request=None, transaction_id=''):
    """Single events on the organiser's calendar at this session's time: possible existing replacements."""
    request = request or graph()
    start = as_utc(row.get('scheduled_start'))
    end = as_utc(row.get('scheduled_end'))
    query = urllib_parse.urlencode({'startDateTime': (start - timedelta(hours=1)).isoformat(),
                                    'endDateTime': (end + timedelta(hours=1)).isoformat(), '$top': 50})
    response = request('GET', f'users/{owner_key(series)}/calendarView?{query}') or {}
    found = []
    for item in response.get('value') or []:
        if item.get('isCancelled') is True or clean(item.get('type')) not in ('singleInstance', ''):
            continue
        same_transaction = transaction_id and clean(item.get('transactionId')) == transaction_id
        same_slot = (minute_key(graph_instant(item, 'start')) == minute_key(start)
                     and clean((item.get('onlineMeeting') or {}).get('joinUrl')))
        if same_transaction or same_slot:
            found.append({'eventId': clean(item.get('id')), 'subject': clean(item.get('subject')),
                          'joinRef': join_ref((item.get('onlineMeeting') or {}).get('joinUrl')),
                          'joinUrl': clean((item.get('onlineMeeting') or {}).get('joinUrl')),
                          'startDateTimeUtc': iso(graph_instant(item, 'start')),
                          'createdByThisLms': bool(same_transaction)})
    return found


# ------------------------------------------------------ executing resolutions

class ResolutionRefused(Exception):
    def __init__(self, message, status=409, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def lock_row(live_session_id, occurrence_id):
    from . import views as v
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT id FROM {v.authoring_table_name(v.LIVE_SESSION_OCCURRENCES_TABLE)} WHERE id = %s FOR UPDATE',
                           [occurrence_id])
    rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s and live_session_id = %s',
                                 [occurrence_id, live_session_id], ensure_tables=False)
    if not rows:
        raise ResolutionRefused('Session not found.', status=404)
    return dict(rows[0])


def execute_resolution(live_session_id, occurrence_id, action, token, *, request=None):
    """Run one confirmed resolution. Re-checks state under a row lock first."""
    from . import views as v

    correlation = new_correlation_id()
    request = request or graph()
    # Written after the transaction: an uncertain Microsoft outcome must stay on
    # record even though the refusal that reports it rolls the LMS write back.
    deferred = []

    def note(event_type, **kwargs):
        deferred.append((event_type, kwargs))

    try:
        return _execute(live_session_id, occurrence_id, action, token, request, correlation, note)
    finally:
        for event_type, kwargs in deferred:
            record_event(live_session_id, event_type, **kwargs)


def _execute(live_session_id, occurrence_id, action, token, request, correlation, note):
    from . import views as v

    health, (series, _rows, events) = health_for(live_session_id)
    session = next((item for item in health['sessions'] if item['occurrenceId'] == occurrence_id
                    and item['liveSessionId'] == clean(series.get('id'))), None)
    if session is None:
        raise ResolutionRefused('Session not found.', status=404)
    with transaction.atomic():
        row = lock_row(live_session_id, occurrence_id)
        if preview_token(row, action) != token:
            raise ResolutionRefused('This session changed since the review was opened. Review it again before confirming.',
                                    code='resolution_stale')
        preview = build_preview(action, session, row, series, evidence_for(occurrence_id))
        if preview['blocked']:
            raise ResolutionRefused(preview['blocked'], code='resolution_blocked')
        before = row_meeting_reference(row)
        common = {'session_number': row.get('session_number'), 'module_catalogue_id': clean(series.get('module_catalogue_id')),
                  'correlation_id': correlation, 'trigger': 'Calendar health resolution'}
        if action == ACTION_KEEP:
            note(EVENT_KEPT, before=before, after=before, detail={'integrity': session['integrity']}, **common)
            return {'resolved': True, 'action': action}
        if action == ACTION_INTENTIONAL:
            event_id = clean(row.get('graph_event_id'))
            event = request('GET', f'users/{owner_key(series)}/events/{urllib_parse.quote(event_id, safe="")}')
            link = clean(((event or {}).get('onlineMeeting') or {}).get('joinUrl'))
            if not event or event.get('isCancelled') or link != clean(row.get('join_url')):
                raise ResolutionRefused('Microsoft did not confirm this separate meeting with the saved link, so it was '
                                        'not marked intentional. Nothing was changed.', code='resolution_unverified')
            note(EVENT_INTENTIONAL, before=before, after=before,
                         detail={'verifiedAt': iso(datetime.now(timezone.utc))}, **common)
            return {'resolved': True, 'action': action}
        if action == ACTION_REASSOCIATE:
            instance = series_instance_on(series, row, request)
            if instance is None:
                raise ResolutionRefused('Microsoft has no occurrence of the recurring series on this date, so there is '
                                        'nothing to reassociate with. Nothing was changed.', code='resolution_no_occurrence')
            link = clean((instance.get('onlineMeeting') or {}).get('joinUrl'))
            if link and link != clean(series.get('join_url')):
                raise ResolutionRefused('The series occurrence has a different join link from the saved series.',
                                        code='resolution_unverified')
            update = {'graph_event_id': clean(series.get('graph_event_id')), 'join_url': clean(series.get('join_url')),
                      'online_meeting_id': '', 'updated_at': datetime.utcnow()}
            v.update_authoring_rows(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s', [occurrence_id], update)
            after = meeting_reference(update['graph_event_id'], update['join_url'], '')
            note(EVENT_REASSOCIATED, before=before, after=after,
                         detail={'instanceId': clean(instance.get('id')), 'separateMeetingLeftOnTeams': before['eventId']},
                         **common)
            return {'resolved': True, 'action': action}
        if action == ACTION_REOPEN:
            v.update_authoring_rows(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s', [occurrence_id],
                                    {'status': 'scheduled', 'updated_at': datetime.utcnow()})
            note(EVENT_REOPENED, before=before, after=before,
                         detail={'statusBefore': clean(row.get('status')), 'statusAfter': 'scheduled',
                                 'actualStart': iso(row.get('actual_start')), 'actualEnd': iso(row.get('actual_end')),
                                 'issues': [issue['code'] for issue in session['lifecycleIssues']]}, **common)
            return {'resolved': True, 'action': action}
        if action == ACTION_REPLACE:
            return create_replacement(series, row, before, common, request, note)
    raise ResolutionRefused('Unknown action.', status=400)


def create_replacement(series, row, before, common, request, note):
    """The only path that creates a separate meeting for a missing occurrence -- explicit and confirmed.

    Idempotent three ways: the row lock above serialises concurrent confirms;
    the deterministic ``transactionId`` lets Microsoft de-duplicate a retried
    POST; and before every POST the organiser's calendar is read for a meeting
    already at this slot (including one an earlier, timed-out attempt created),
    which is reported instead of creating another.
    """
    from . import views as v
    from .teams_update_guard import graph_failure_outcome

    live_session_id = clean(series.get('id'))
    if series_instance_on(series, row, request) is not None:
        raise ResolutionRefused('Microsoft now shows this occurrence in the recurring series, so it is no longer '
                                'missing. Re-check instead; nothing was created.', code='resolution_not_missing')
    transaction_id = replacement_transaction_id(live_session_id, row)
    existing = separate_events_at(series, row, request, transaction_id)
    if existing:
        raise ResolutionRefused('A separate Teams meeting already exists at this time, so another was not created. '
                                'Review it before deciding.', code='resolution_existing_meeting', existing=existing)
    invited = list(dict.fromkeys([
        *v.teams_series_email_list(series.get('co_organizers')),
        *v.teams_series_email_list(series.get('presenters')),
        *v.teams_series_email_list(series.get('attendees')),
    ]))
    target = {'session_number': int(row.get('session_number') or 0),
              'start': as_utc(row.get('scheduled_start')), 'end': as_utc(row.get('scheduled_end'))}
    title = clean(series.get('module_title')) or 'Live session'
    try:
        created = request('POST', f'users/{owner_key(series)}/events',
                          payload=v.teams_single_occurrence_payload(title, target, invited, transaction_id),
                          extra_headers=v.GRAPH_SILENT_INVITE_HEADERS)
    except RuntimeError as exc:
        outcome = graph_failure_outcome(exc)
        note(EVENT_REPLACEMENT_UNCERTAIN if outcome == 'unknown' else EVENT_REPLACEMENT_FAILED,
             before=before, outcome='unknown' if outcome == 'unknown' else 'failed',
                     detail={'transactionId': transaction_id, 'error': clean(exc)[:500]}, **common)
        raise ResolutionRefused(
            ('Microsoft did not confirm whether the meeting was created. Nothing more was sent. Review the session '
             'again: the review reads Microsoft first and will show the meeting if it exists.')
            if outcome == 'unknown' else 'Microsoft refused to create the meeting. Nothing was changed.',
            status=502, code='resolution_uncertain' if outcome == 'unknown' else 'resolution_failed')
    options = {
        'organizer': clean(series.get('organizer_email')),
        'recording': clean(series.get('recording')).lower() or 'none',
        'lobby_bypass': clean(series.get('lobby_bypass')).lower() or 'invited',
        'spoken_language': clean(series.get('spoken_language')) or 'en-GB',
        'presenters': v.teams_series_email_list(series.get('presenters')),
        'co_organizers': v.teams_series_email_list(series.get('co_organizers')),
    }
    detail = v.teams_standalone_occurrence_meeting(owner_key(series), created, target, invited, options)
    option_warnings = detail.pop('warnings', [])
    v.persist_recreated_occurrence_details(live_session_id, [detail])
    after = meeting_reference(detail.get('graph_event_id'), detail.get('join_url'), detail.get('online_meeting_id'))
    note(EVENT_REPLACEMENT, before=before, after=after,
                 detail={'transactionId': transaction_id, 'optionWarnings': len(option_warnings)}, **common)
    return {'resolved': True, 'action': ACTION_REPLACE, 'meeting': after,
            'warnings': [clean(item.get('message')) for item in option_warnings if isinstance(item, dict)]}


def recheck_occurrence(live_session_id, occurrence_id, request=None):
    """Read-only: is the missing occurrence in the series now? Records the answer; changes nothing in Microsoft."""
    from . import views as v

    series_rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], ensure_tables=False)
    rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s and live_session_id = %s',
                                 [occurrence_id, live_session_id], ensure_tables=False)
    if not series_rows or not rows:
        raise ResolutionRefused('Session not found.', status=404)
    series, row = dict(series_rows[0]), dict(rows[0])
    instance = series_instance_on(series, row, request)
    number = int(row.get('session_number') or 0)
    reconcile = latest_reconcile(read_events([live_session_id], limit=200))
    missing = [item for item in ((reconcile or {}).get('detail') or {}).get('missing') or []]
    if instance is not None:
        missing = [item for item in missing if int(item.get('sessionNumber') or 0) != number]
    record_event(live_session_id, EVENT_RECONCILED, session_number=number,
                 module_catalogue_id=clean(series.get('module_catalogue_id')), trigger='Calendar health re-check',
                 detail={'missing': missing, 'source': 'recheck'}, outcome='attention' if missing else 'success')
    return {'present': instance is not None, 'sessionNumber': number}


# --------------------------------------------------------------------- views

def _json(result, status=200):
    from django.http import JsonResponse
    return JsonResponse(result, status=status)


def staff_view(method):
    """Admin/staff only, one HTTP method. Imported lazily so the save paths that
    import this module never pull the login stack in with them."""
    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            from django.views.decorators.http import require_http_methods
            from login.permissions import require_role
            return require_role('admin', 'staff')(require_http_methods([method])(view))(request, *args, **kwargs)
        return wrapped
    return decorator


@staff_view('GET')
def calendar_health(request, live_session_id):
    """Read-only. Reads LMS rows, the saved status check and the integrity log; never Microsoft."""
    try:
        health, _context = health_for(live_session_id)
    except LookupError:
        return _json({'error': 'Calendar not found.'}, 404)
    listing = page_of(health['sessions'], request.GET.get('filter') or 'all',
                      request.GET.get('page') or 1, request.GET.get('pageSize') or DEFAULT_PAGE_SIZE)
    return _json({**{key: value for key, value in health.items() if key != 'sessions'}, 'sessions': listing,
                  'readOnly': True})


@staff_view('GET')
def calendar_health_session(request, live_session_id, occurrence_id):
    """Read-only drawer data for one session: meeting identity, evidence, comparison, history."""
    try:
        health, (series, rows, events) = health_for(live_session_id)
    except LookupError:
        return _json({'error': 'Calendar not found.'}, 404)
    row = next((row for row in rows if clean(row.get('id')) == clean(occurrence_id)), None)
    if row is None:
        # An additional meeting's session is read under its own calendar id.
        from . import views as v
        found = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s', [occurrence_id], ensure_tables=False)
        row = dict(found[0]) if found else None
        if row is not None:
            owners = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [row.get('live_session_id')], ensure_tables=False)
            if not owners or clean(owners[0].get('module_catalogue_id')) != health['module']['moduleCatalogueId']:
                row = None
            else:
                series = dict(owners[0])
    if row is None:
        return _json({'error': 'Session not found.'}, 404)
    try:
        detail = session_detail(health, row, events, series)
    except LookupError:
        return _json({'error': 'Session not found.'}, 404)
    detail['resolutions'] = resolution_options(detail)
    return _json({'session': detail, 'readOnly': True})


@staff_view('POST')
def calendar_health_resolution(request, live_session_id):
    """Preview (default) or confirm one resolution.

    ``{"occurrenceId", "action"}`` previews: it reads nothing from Microsoft and
    changes nothing. ``{"confirm": true, "previewToken": ...}`` runs it, and only
    against the exact state the preview was made from.
    """
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return _json({'error': 'A valid JSON body is required.'}, 400)
    if not isinstance(body, dict):
        return _json({'error': 'A valid JSON body is required.'}, 400)
    occurrence_id = clean(body.get('occurrenceId'))
    action = clean(body.get('action'))
    if action not in ACTIONS or not occurrence_id:
        return _json({'error': 'Choose a session and a supported action.'}, 400)
    try:
        if action == ACTION_RECHECK:
            return _json(recheck_occurrence(live_session_id, occurrence_id))
        health, (series, rows, _events) = health_for(live_session_id)
        row = next((row for row in rows if clean(row.get('id')) == occurrence_id), None)
        session = next((item for item in health['sessions'] if item['occurrenceId'] == occurrence_id
                        and item['liveSessionId'] == clean(series.get('id'))), None)
        if row is None or session is None:
            return _json({'error': 'Session not found.'}, 404)
        if not body.get('confirm'):
            return _json({'preview': build_preview(action, session, row, series, evidence_for(occurrence_id))})
        from coach_api.views import has_graph_credentials
        if action in (ACTION_INTENTIONAL, ACTION_REASSOCIATE, ACTION_REPLACE) and not has_graph_credentials():
            return _json({'error': 'Microsoft Graph credentials are not configured.'}, 503)
        result = execute_resolution(live_session_id, occurrence_id, action, clean(body.get('previewToken')))
        from . import views as v
        try:
            v.invalidate_curriculum_cache()
        except Exception:
            pass
        return _json(result)
    except LookupError:
        return _json({'error': 'Calendar not found.'}, 404)
    except ResolutionRefused as refusal:
        return _json({'error': str(refusal), **refusal.extra}, refusal.status)
    except RuntimeError as exc:
        logger.warning('Calendar health resolution could not read Microsoft: %s', exc)
        return _json({'error': 'Microsoft could not be read, so nothing was changed.', 'detail': clean(exc)[:300]}, 502)
