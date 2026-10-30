"""A durable record of every Microsoft Graph write the Teams paths saw refused.

The intermittent ``HTTP 403`` on ``PATCH users/{organiser}/onlineMeetings/{id}``
has no confirmed cause: the app holds ``OnlineMeetings.ReadWrite.All``, the
application access policy covers the organiser, every meeting is readable, and
Microsoft accepts the same write on the organiser's other meetings. What is
left is evidence -- which meeting, which organiser, which operation, when, and
Microsoft's own request ID for its support team -- and a log line on one of
nine worker processes is gone by the time anybody asks.

Each failure is written twice:

* a structured ``logger.warning`` line, always (the process log);
* a row in ``curriculum.teams_graph_failures`` once the owner has applied
  ``backend/sql/2026-10-08_teams_graph_failures.sql``. Until then the insert is
  skipped and says so in the log line; a save is never failed for want of it.

What is never stored: the access token, the client secret, request bodies
(attendee lists) or any learner data. Microsoft's error text is kept, cut to a
fixed length, with anything shaped like a credential redacted.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from django.db import connection

from .teams_meeting_options_policy import graph_error_details

logger = logging.getLogger(__name__)
TABLE = 'curriculum.teams_graph_failures'
SQL_FILE = 'backend/sql/2026-10-08_teams_graph_failures.sql'

#: Operation names written to the log, so a query can group by them.
OPTIONS_PATCH = 'online_meeting_options_patch'
OPTIONS_RESOLVE = 'online_meeting_resolve'
OPTIONS_CONFIRM = 'online_meeting_options_confirm'
EVENT_SAVE = 'calendar_event_save'

MAX_MESSAGE = 1000
_SECRETS = re.compile(
    r'(?i)(bearer\s+[A-Za-z0-9._~+/=-]+|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*'
    r'|(?:client_secret|access_token|refresh_token|password|sig)=[^&\s;]+)'
)
_available = None


def _text(value, limit=None):
    text = str(value or '').strip()
    return text[:limit] if limit else text


def redact(text):
    """Microsoft's error text without anything that looks like a credential."""

    return _SECRETS.sub('[redacted]', _text(text))[:MAX_MESSAGE]


def table_available():
    """Whether the owner has applied the SQL file. Remembered per process once true."""

    global _available
    if _available:
        return True
    try:
        if connection.vendor != 'postgresql':
            return False
        with connection.cursor() as cursor:
            cursor.execute('SELECT to_regclass(%s)', [TABLE])
            _available = bool(cursor.fetchone()[0])
    except Exception:  # noqa: BLE001 -- recording must never fail a save
        return False
    return bool(_available)


def reset_availability():
    global _available
    _available = None


def lms_request_id():
    """The LMS's own correlation ID for the request in progress, when there is one."""

    try:
        from config.observability import request_id_context
        return _text(request_id_context.get(), 64)
    except (ImportError, LookupError):
        return ''


def failure_record(operation, error_text, *, live_session_id='', online_meeting_id='', organizer='',
                   organizer_object_id='', groups=(), at=None):
    """The fields kept for one refused Graph call, Microsoft's error split out."""

    at = at or datetime.now(timezone.utc)
    graph = graph_error_details(error_text, at=at.isoformat())
    return {
        'occurredAt': at.isoformat(),
        'operation': _text(operation, 64),
        'liveSessionId': _text(live_session_id, 128),
        'onlineMeetingId': _text(online_meeting_id, 512),
        'organizerEmail': _text(organizer, 254),
        'organizerObjectId': _text(organizer_object_id, 64),
        'groups': sorted({_text(group) for group in groups or () if _text(group)}),
        'httpStatus': graph.get('status'),
        'graphCode': _text(graph.get('code'), 128),
        'graphMessage': redact(graph.get('message')),
        'graphRequestId': _text(graph.get('requestId'), 64),
        'graphMethod': _text(graph.get('method'), 10),
        'graphPath': _text(graph.get('path'), 512),
        'lmsRequestId': lms_request_id(),
    }


def record_graph_failure(operation, error_text, **context):
    """Log one refused Graph call and keep it. Never raises; returns the record."""

    record = failure_record(operation, error_text, **context)
    stored = False
    if table_available():
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'INSERT INTO {TABLE} (occurred_at, operation, live_session_id, online_meeting_id, '
                    'organizer_email, organizer_object_id, option_groups, http_status, graph_code, graph_message, '
                    'graph_request_id, graph_method, graph_path, lms_request_id) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)',
                    [record['occurredAt'], record['operation'], record['liveSessionId'], record['onlineMeetingId'],
                     record['organizerEmail'], record['organizerObjectId'], json.dumps(record['groups']),
                     record['httpStatus'], record['graphCode'], record['graphMessage'], record['graphRequestId'],
                     record['graphMethod'], record['graphPath'], record['lmsRequestId']],
                )
            stored = True
        except Exception:  # noqa: BLE001 -- recording must never fail a save
            logger.warning('Could not store a Teams Graph failure in %s.', TABLE, exc_info=True)
    logger.warning(
        'Teams Graph write refused: operation=%s live_session_id=%s online_meeting_id=%s organizer=%s '
        'organizer_object_id=%s groups=%s http_status=%s graph_code=%s graph_request_id=%s graph_path=%s '
        'lms_request_id=%s stored=%s message=%s',
        record['operation'], record['liveSessionId'], record['onlineMeetingId'], record['organizerEmail'],
        record['organizerObjectId'], ','.join(record['groups']), record['httpStatus'], record['graphCode'],
        record['graphRequestId'], record['graphPath'], record['lmsRequestId'],
        'yes' if stored else f'no (apply {SQL_FILE})', record['graphMessage'],
    )
    return {**record, 'stored': stored}


def recent_failures(live_session_id, limit=20):
    """The newest refusals kept for one series, newest first. Empty until the table exists."""

    if not table_available():
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT occurred_at, operation, online_meeting_id, organizer_object_id, option_groups, http_status, '
                f'graph_code, graph_message, graph_request_id FROM {TABLE} WHERE live_session_id = %s '
                'ORDER BY occurred_at DESC LIMIT %s',
                [_text(live_session_id, 128), max(1, min(int(limit), 100))],
            )
            columns = ['occurredAt', 'operation', 'onlineMeetingId', 'organizerObjectId', 'groups', 'httpStatus',
                       'graphCode', 'graphMessage', 'graphRequestId']
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception:  # noqa: BLE001
        logger.warning('Could not read Teams Graph failures.', exc_info=True)
        return []
    for row in rows:
        if isinstance(row['occurredAt'], datetime):
            row['occurredAt'] = row['occurredAt'].isoformat()
        if isinstance(row['groups'], str):
            try:
                row['groups'] = json.loads(row['groups'])
            except ValueError:
                row['groups'] = []
    return rows
