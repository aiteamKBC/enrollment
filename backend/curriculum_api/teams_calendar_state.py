"""Read Microsoft cancellations and reconcile eligible local occurrences; never mail."""
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from login.permissions import require_role

from .teams_cancellation_checks import CalendarStateError, cancellation_plan


@contextmanager
def calendar_reader():
    from coach_api.views import get_graph_settings, microsoft_graph_token

    settings = get_graph_settings()
    base = settings['base_url'].rstrip('/')
    if base != 'https://graph.microsoft.com/v1.0':
        raise CalendarStateError('The configured Microsoft calendar endpoint is not supported.')
    token = microsoft_graph_token()
    # Reviews can read a master and its instances (and, for a calendar with
    # several day-specific roots, do that more than once).  Keep the whole
    # read bounded, but allow a transient Graph socket timeout to be retried
    # before the UI turns it into a generic 502.
    deadline = time.monotonic() + 60
    with httpx.Client(base_url=base + '/', headers={'Authorization': f'Bearer {token}'}, follow_redirects=False) as client:
        def read(path, *, prefer=''):
            if prefer not in ('', 'include-unknown-enum-members'):
                raise ValueError('Unsupported Microsoft read preference.')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CalendarStateError('Calendar verification took too long. Retry; no cancellation changes were applied.')
            response = None
            for attempt in range(3):
                try:
                    kwargs = {'headers': {'Prefer': prefer}} if prefer else {}
                    response = client.get(path, timeout=min(15, remaining), **kwargs)
                    break
                except httpx.HTTPError:
                    # This is a read-only request. Retrying it cannot create a
                    # duplicate meeting or notification, and is safe for the
                    # short-lived network failures that otherwise strand the
                    # review before the user can even confirm it.
                    remaining = deadline - time.monotonic()
                    if attempt == 2 or remaining <= 0:
                        raise
                    time.sleep(min(0.25 * (2 ** attempt), remaining))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CalendarStateError('Calendar verification took too long. Retry; no cancellation changes were applied.')
            if response is None:
                raise CalendarStateError('Microsoft did not return a calendar response. Retry the status check.')
            if response.status_code == 404:
                # Only a missing event is cancellation evidence. A missing user,
                # inaccessible mailbox, failed pagination or permission is not.
                if '/events/' in path and '/instances' not in path:
                    code = response.json().get('error', {}).get('code')
                    if code == 'ErrorItemNotFound':
                        return None
            if response.status_code != 200:
                error = CalendarStateError(f'Microsoft calendar status could not be verified (HTTP {response.status_code}).')
                # Carried, not parsed back out of the message: a caller that has
                # to tell "Microsoft refused us" from "Microsoft is busy" can
                # read it without the two ever being told apart by their prose.
                error.status_code = response.status_code
                raise error
            return response.json()
        yield read


def load_calendar_state(live_id, *, lock=False):
    from . import views as v

    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('curriculum.teams_calendar_sync_state')")
        if not cursor.fetchone()[0]:
            raise CalendarStateError('Calendar status sync is not configured. Apply backend/sql/teams_calendar_sync_state.sql.')
        if lock:
            cursor.execute('SELECT id FROM curriculum.live_sessions WHERE id = %s FOR UPDATE', [live_id])
            cursor.execute('SELECT id FROM curriculum.live_session_occurrences WHERE live_session_id = %s FOR UPDATE', [live_id])
        cursor.execute('SELECT snapshot FROM curriculum.teams_calendar_sync_state WHERE live_session_id = %s', [live_id])
        saved = cursor.fetchone()
    series = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], ensure_tables=False)
    if not series:
        raise LookupError('Calendar not found.')
    rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_id],
                                'session_number asc', ensure_tables=False)
    item = dict(series[0])
    item['calendar_series'] = v.parse_json_value(item.get('calendar_series'), []) or []
    snapshot = saved[0] if saved else {}
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    return item, rows, snapshot


def state_signature(series, rows):
    """Avoid overwriting a save or an attendance import made during Graph reads."""
    series_fields = ('id', 'status', 'updated_at', 'graph_event_id', 'calendar_series', 'organizer_email', 'join_url')
    row_fields = ('id', 'updated_at', 'status', 'graph_event_id', 'scheduled_start', 'scheduled_end',
                  'join_url', 'actual_start', 'attendance_report_id', 'participant_count')
    return json.dumps([[series.get(key) for key in series_fields],
                       [[row.get(key) for key in row_fields] for row in rows]], sort_keys=True, default=str)


def reconcile_calendar(live_id):
    series, rows, snapshot = load_calendar_state(live_id)
    if series.get('status') != 'active':
        return {'changed': False, 'seriesStatus': series.get('status'), 'cancelledSessions': [], 'errors': []}
    if not rows:
        raise CalendarStateError('No stored sessions are available to verify this calendar.')
    signature = state_signature(series, rows)
    with calendar_reader() as read:
        plan = cancellation_plan(series, rows, snapshot, read)
    cancelled_ids = list(plan.get('cancelledIds') or [])
    cancelled_sessions = sorted(
        row.get('session_number') for row in rows
        if row.get('id') in set(cancelled_ids) and row.get('session_number') is not None
    )
    changed = bool(cancelled_sessions)
    with transaction.atomic():
        current, current_rows, current_snapshot = load_calendar_state(live_id, lock=True)
        if state_signature(current, current_rows) != signature or current_snapshot != snapshot:
            raise CalendarStateError('This calendar changed during verification. Retry the status check.')
        with connection.cursor() as cursor:
            # A confirmed Microsoft cancellation is now applied to the local
            # occurrence when it is still a scheduled, never-started session
            # with no attendance report.  This is the same eligibility boundary
            # used by an explicit LMS cancellation: historical attendance is
            # never rewritten, while a learner cannot be marked absent for a
            # meeting that Microsoft has already cancelled.
            if cancelled_ids:
                cursor.execute("""UPDATE curriculum.live_session_occurrences
                                  SET status = 'cancelled', updated_at = %s
                                  WHERE live_session_id = %s
                                    AND id = ANY(%s)
                                    AND status = 'scheduled'
                                    AND actual_start IS NULL
                                    AND COALESCE(attendance_report_id, '') = ''
                                    AND COALESCE(participant_count, 0) = 0""",
                               [datetime.now(timezone.utc), live_id, cancelled_ids])
            # No Microsoft mutation or email is sent here. The status write is
            # deliberately limited to rows whose cancellation is safe to apply.
            cursor.execute("""INSERT INTO curriculum.teams_calendar_sync_state (live_session_id, snapshot, checked_at)
                              VALUES (%s, %s::jsonb, %s)
                              ON CONFLICT (live_session_id) DO UPDATE
                              SET snapshot = EXCLUDED.snapshot, checked_at = EXCLUDED.checked_at""",
                           [live_id, json.dumps(plan['snapshot']), datetime.now(timezone.utc)])
    if changed:
        from . import views as v
        v.invalidate_curriculum_cache()
    # Read-only on Microsoft; this records the verified result locally.
    from .teams_calendar_integrity import EVENT_VERIFIED, record_event
    record_event(live_id, EVENT_VERIFIED, module_catalogue_id=str(series.get('module_catalogue_id') or ''),
                 trigger='Calendar status check', outcome='attention' if plan['errors'] else 'success',
                 detail={'errors': plan['errors'][:20], 'unmatched': len(plan.get('unmatched') or []),
                         'leftovers': len(plan.get('leftovers') or [])})
    return {'changed': changed, 'seriesStatus': 'active', 'cancelledSessions': cancelled_sessions,
            # Kept for older clients; sessions that were eligible are now
            # recorded locally and therefore do not need a second manual action.
            'cancelledInMicrosoft': [],
            'seriesCancelledInMicrosoft': bool(plan['seriesCancelled']) and not changed,
            'errors': plan['errors'],
            # Slots outside the plan remain report-only and are never changed.
            'leftovers': plan.get('leftovers') or []}


@transaction.non_atomic_requests
@require_role('admin', 'staff')
@require_POST
def sync_calendar_state(request, live_session_id):
    try:
        result = reconcile_calendar(live_session_id)
        return JsonResponse(result, status=207 if result['errors'] else 200)
    except LookupError:
        return JsonResponse({'error': 'Calendar not found.'}, status=404)
    except CalendarStateError as exc:
        _note_failed_check(live_session_id, str(exc))
        return JsonResponse({'error': str(exc)}, status=409)
    except (httpx.HTTPError, ValueError, RuntimeError):
        _note_failed_check(live_session_id, 'Microsoft calendar status could not be verified.')
        return JsonResponse({'error': 'Microsoft calendar status could not be verified. No cancellation was inferred.'}, status=502)


def _note_failed_check(live_session_id, message):
    """Calendar health shows "verification failed" rather than an older success as current."""
    from .teams_calendar_integrity import EVENT_VERIFY_FAILED, record_event
    record_event(live_session_id, EVENT_VERIFY_FAILED, trigger='Calendar status check', outcome='failed',
                 detail={'message': message[:300]})
