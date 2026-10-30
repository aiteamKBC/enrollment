"""How much of a live-session recording each learner has actually watched.

The player reports the seconds it played since its last report. The server
caps every report by the wall-clock time since the previous one, so the total
cannot run faster than real time. Counted like completing a normal video
(``learner_self_or_admin``): the learner, or an administrator operating the
learner's workspace. Coaches and other staff viewing the page are refused.

Viewing does not make up a live-mode learner's absence. Recorded delivery uses
the saved watch evidence for read-time credit, independently of recovery plans.
"""
import json
import logging
import uuid

from django.db import connections, DatabaseError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from login.permissions import learner_self_or_admin

from .session_media_policy import artifact_metadata, hidden_artifact_ids
from .session_results import learner_series, read, unavailable

log = logging.getLogger(__name__)

# The player reports about every 30 seconds; allow slack for a slow network.
MAX_REPORT_SECONDS = 90
CLOCK_SLACK_SECONDS = 5
VIEWS_TABLE = 'curriculum.live_session_recording_views'


def _whole_seconds(value, *, limit):
    number = int(value or 0)
    if not 0 <= number <= limit:
        raise ValueError()
    return number


@require_http_methods(['GET', 'POST'])
@ensure_csrf_cookie
@csrf_protect
@learner_self_or_admin(kwarg='learner_id')
def learner_recording_watch(request, kind, learner_id, series_id, artifact_id):
    """GET: viewing so far plus the CSRF token for reports (learners cannot use /coach_api/csrf).
    POST: add the seconds played since the last report."""
    if request.method == 'GET':
        return _watch_state(request, kind, learner_id, series_id, artifact_id)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError()
        seconds = _whole_seconds(payload.get('watchedSeconds'), limit=3600)
        position = _whole_seconds(payload.get('position'), limit=24 * 3600)
        duration = _whole_seconds(payload.get('duration'), limit=24 * 3600)
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Send whole seconds for watchedSeconds, position and duration.'}, status=400)
    try:
        learner, artifact = _learner_recording(kind, learner_id, series_id, artifact_id)
        if not artifact:
            return JsonResponse({'error': 'Recording not found.'}, status=404)
        with transaction.atomic(), connections['default'].cursor() as cursor:
            cursor.execute(f'''INSERT INTO {VIEWS_TABLE} (id, live_session_id, occurrence_id, artifact_id,
                    learner_kind, learner_id, viewer_email, watched_seconds, duration_seconds, last_position_seconds,
                    first_viewed_at, last_heartbeat_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, LEAST(%s, %s), %s, %s,
                    now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC', now() AT TIME ZONE 'UTC')
                ON CONFLICT (artifact_id, learner_kind, learner_id) DO UPDATE SET
                    watched_seconds = {VIEWS_TABLE}.watched_seconds + LEAST(EXCLUDED.watched_seconds,
                        GREATEST(0, EXTRACT(EPOCH FROM (now() AT TIME ZONE 'UTC' - {VIEWS_TABLE}.last_heartbeat_at)))::integer + %s),
                    duration_seconds = GREATEST({VIEWS_TABLE}.duration_seconds, EXCLUDED.duration_seconds),
                    last_position_seconds = EXCLUDED.last_position_seconds,
                    last_heartbeat_at = now() AT TIME ZONE 'UTC', updated_at = now() AT TIME ZONE 'UTC'
                RETURNING watched_seconds, duration_seconds''', [
                'VIEW-' + uuid.uuid4().hex.upper(), series_id, artifact['occurrence_id'], artifact_id,
                kind, learner_id, (learner.email or '').strip().casefold(), seconds, MAX_REPORT_SECONDS,
                duration, position, CLOCK_SLACK_SECONDS,
            ])
            watched, known_duration = cursor.fetchone()
        response = JsonResponse({'watchedSeconds': watched, 'durationSeconds': known_duration})
        response['Cache-Control'] = 'private, no-store'
        return response
    except DatabaseError:
        return unavailable()


def _learner_recording(kind, learner_id, series_id, artifact_id):
    """(learner, artifact) for a recording the learner may play, or (learner, None)."""
    learner, series = learner_series(kind, learner_id, series_id, linked_recordings=True)
    if series and 'linked_session_numbers' in series:
        from .linked_recording_access import linked_artifact_allowed
        if not linked_artifact_allowed(series, artifact_id):
            return learner, None
    if not series:
        return learner, None
    siblings = read('''SELECT a.* FROM curriculum.live_session_artifacts a
        JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
        WHERE o.live_session_id=%s AND a.occurrence_id=(
            SELECT occurrence_id FROM curriculum.live_session_artifacts WHERE id=%s)''', [series_id, artifact_id])
    artifact = next((row for row in siblings if row['id'] == artifact_id), None)
    if not artifact or artifact.get('artifact_type') != 'recording' or artifact_id in hidden_artifact_ids(siblings):
        return learner, None
    return learner, artifact


def _watch_state(request, kind, learner_id, series_id, artifact_id):
    try:
        _learner, artifact = _learner_recording(kind, learner_id, series_id, artifact_id)
        if not artifact:
            return JsonResponse({'error': 'Recording not found.'}, status=404)
        rows = read(f'''SELECT watched_seconds, duration_seconds FROM {VIEWS_TABLE}
            WHERE artifact_id=%s AND learner_kind=%s AND learner_id=%s''', [artifact_id, kind, learner_id])
    except DatabaseError:
        return unavailable()
    row = rows[0] if rows else {}
    response = JsonResponse({'watchedSeconds': int(row.get('watched_seconds') or 0),
                             'durationSeconds': int(row.get('duration_seconds') or 0),
                             'csrfToken': get_token(request)})
    response['Cache-Control'] = 'private, no-store'
    return response


def watched_by_occurrence(kind, learner_id, occurrence_ids):
    """{occurrence id: (watched seconds, recording seconds)} for one learner's recordings."""
    ids = sorted({str(value) for value in occurrence_ids if value})
    if not ids:
        return {}
    rows = read(f'''SELECT occurrence_id, SUM(watched_seconds) AS watched, MAX(duration_seconds) AS duration
        FROM {VIEWS_TABLE} WHERE learner_kind=%s AND learner_id=%s AND occurrence_id = ANY(%s)
        GROUP BY occurrence_id''', [kind, learner_id, ids])
    return {row['occurrence_id']: (int(row['watched'] or 0), int(row['duration'] or 0)) for row in rows}


# A few unseen seconds still count as the whole recording: the last 10 seconds,
# or 2% of a longer recording (about two and a half minutes of a two-hour lecture).
FULL_WATCH_SLACK_SECONDS = 10
FULL_WATCH_SLACK_RATIO = 0.02


def fully_watched_occurrences(kind, learner_id, occurrence_ids):
    """Occurrences whose every recording the learner can play was watched to the end."""
    ids = sorted({str(value) for value in occurrence_ids if value})
    if not ids:
        return set()
    artifacts = read('''SELECT a.*, r.status AS archive_status FROM curriculum.live_session_artifacts a
        LEFT JOIN curriculum.session_result_archive r ON r.artifact_id=a.id
        WHERE a.occurrence_id = ANY(%s)''', [ids])
    by_occurrence = {}
    for row in artifacts:
        by_occurrence.setdefault(row['occurrence_id'], []).append(row)
    views = {row['artifact_id']: row for row in read(f'''SELECT artifact_id, watched_seconds, duration_seconds
        FROM {VIEWS_TABLE} WHERE learner_kind=%s AND learner_id=%s AND occurrence_id = ANY(%s)''',
        [kind, learner_id, ids])}

    def watched_to_end(artifact_id):
        view = views.get(artifact_id)
        duration = int((view or {}).get('duration_seconds') or 0)
        if duration <= 0:
            return False
        slack = max(FULL_WATCH_SLACK_SECONDS, duration * FULL_WATCH_SLACK_RATIO)
        return int(view['watched_seconds'] or 0) + slack >= duration

    complete = set()
    for occurrence_id, rows in by_occurrence.items():
        hidden = hidden_artifact_ids(rows)
        # Only recordings the learner can actually play (archived and not hidden) are required.
        copies = {}
        for row in rows:
            if row['artifact_type'] == 'recording' and row.get('archive_status') == 'ready' and row['id'] not in hidden:
                copies.setdefault(_recording_identity(row), []).append(row['id'])
        # Teams can return one recording twice under different ids: watching either copy counts.
        if copies and all(any(watched_to_end(artifact_id) for artifact_id in ids) for ids in copies.values()):
            complete.add(occurrence_id)
    return complete


def _recording_identity(row):
    """The same Teams call and recording window identify one recording across duplicate ids."""
    meta = artifact_metadata(row)
    key = (meta.get('callId'), meta.get('createdDateTime'), meta.get('endDateTime'))
    return key if all(key) else row['id']
