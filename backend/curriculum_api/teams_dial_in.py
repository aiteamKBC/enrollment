"""The meeting ID and passcode Teams prints under a join link.

Microsoft keeps them on the onlineMeeting (``joinMeetingIdSettings``), not on
the calendar event, and they are not derivable from the link: the ``p=`` of a
``/meet/`` link is a different token from the passcode. So they are read once,
when the LMS already holds the meeting, and kept on the live-session row beside
the join link they were read for.

Reading them never changes the meeting: it is a Graph GET at most, and a
failure is logged and skipped -- the join link still works without them, so a
save or a sync must never fail for want of a passcode.

``dial_in_join_url`` is the link the pair belongs to. A screen shows the pair
only under that same link, so a series that moved to a new meeting, or a
component given its own link, shows nothing rather than another meeting's code.
Provisioned by ``backend/sql/live_session_dial_in.sql``.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


def _clean(value):
    return str(value or '').strip()


def dial_in_from_meeting(meeting):
    """(meeting ID, passcode) from a Graph onlineMeeting; blanks when absent."""
    settings = (meeting or {}).get('joinMeetingIdSettings') if isinstance(meeting, dict) else None
    settings = settings if isinstance(settings, dict) else {}
    return _clean(settings.get('joinMeetingId')), _clean(settings.get('passcode'))


def read_teams_dial_in(organizer, meeting_id, join_url):
    """(meeting ID, passcode) from Microsoft for one join link. A Graph GET only.

    With no online meeting ID saved, the meeting is found by its join link --
    the same read-only lookup the options and sync paths use. Raises
    RuntimeError when Graph refuses.
    """
    from . import views as v
    from coach_api.views import microsoft_graph_request

    organizer, meeting_id, join_url = _clean(organizer), _clean(meeting_id), _clean(join_url)
    if not organizer or not join_url:
        return '', ''
    if meeting_id:
        return dial_in_from_meeting(
            microsoft_graph_request('GET', v.teams_meeting_base_path(organizer, meeting_id, join_url)))
    return dial_in_from_meeting(v.teams_online_meeting_from_join_url(organizer, join_url))


def store_teams_dial_in(live_session_id, join_url, code, passcode):
    """Keep the pair beside the link it was read for. False when there is nowhere to keep it."""
    from . import views as v

    if not code or 'join_meeting_code' not in v.column_names(v.LIVE_SESSIONS_TABLE):
        return False
    # A savepoint, so a failed write leaves the caller's transaction usable.
    # ``updated_at`` is left alone: it picks the module's active calendar, and
    # this is not a calendar change.
    with transaction.atomic():
        v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], {
            'join_meeting_code': code, 'join_passcode': passcode, 'dial_in_join_url': join_url,
        })
    return True


def remember_teams_dial_in(series, graph_meeting=None):
    """Keep the meeting ID and passcode of ``series``' join link. Never raises.

    ``series`` needs ``id``, ``join_url``, ``online_meeting_id`` and
    ``organizer_email``; its stored ``dial_in_join_url``/``join_meeting_code``,
    when given, skip the Graph read for a link already captured. A
    ``graph_meeting`` the caller already holds is used before asking Graph.
    """
    from . import views as v

    series = series or {}
    live_session_id = _clean(series.get('id'))
    join_url = _clean(series.get('join_url'))
    meeting_id = _clean(series.get('online_meeting_id')) or _clean((graph_meeting or {}).get('id'))
    if not live_session_id or not join_url:
        return False
    if _clean(series.get('dial_in_join_url')) == join_url and _clean(series.get('join_meeting_code')):
        return False
    try:
        # Nowhere to keep them until the SQL file is applied: ask Graph nothing.
        if 'join_meeting_code' not in v.column_names(v.LIVE_SESSIONS_TABLE):
            return False
        code, passcode = dial_in_from_meeting(graph_meeting)
        if not code:
            if not meeting_id:
                return False
            code, passcode = read_teams_dial_in(series.get('organizer_email'), meeting_id, join_url)
        return store_teams_dial_in(live_session_id, join_url, code, passcode)
    except Exception as exc:  # noqa: BLE001 - optional detail; the link works without it
        logger.warning('Teams meeting ID and passcode not stored for %s: %s', live_session_id, exc)
        return False


def _dial_in_response(join_url='', code='', passcode=''):
    response = JsonResponse({
        'joinUrl': join_url if code else '',
        'meetingId': code,
        'passcode': passcode if code else '',
    })
    response['Cache-Control'] = 'no-store, private'
    return response


@require_GET
def teams_meeting_dial_in(request, live_session_id):
    """The meeting ID and passcode of the link a component shows (``?link=``).

    Answered from the stored pair when it was read for that link. Otherwise
    Microsoft is asked once -- a GET, nothing written there -- and the series'
    own link is stored, so a meeting nobody has synced since still shows them.
    Only a link that belongs to this series (its own, or one of its sessions')
    is looked up; any other link gets blanks.

    Staff-only through ``teams_staff_required`` at the URL, like the other Teams
    routes, so this module imports nothing from ``login``.
    """
    from . import views as v
    from coach_api.views import has_graph_credentials

    rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])
    if not rows:
        return JsonResponse({'error': 'Live session series not found.', 'code': 'not_found'}, status=404)
    row = rows[0]
    series_link = _clean(row.get('join_url'))
    link = _clean(request.GET.get('link')) or series_link
    stored_link = _clean(row.get('dial_in_join_url'))
    if stored_link and stored_link == link and _clean(row.get('join_meeting_code')):
        return _dial_in_response(stored_link, _clean(row.get('join_meeting_code')), _clean(row.get('join_passcode')))
    if not link:
        return _dial_in_response()
    if link == series_link:
        meeting_id = _clean(row.get('online_meeting_id'))
    else:
        # A session moved onto its own event has its own meeting and passcode.
        occurrences = v.authoring_fetch_all(
            v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s and join_url = %s', [live_session_id, link])
        if not occurrences:
            return _dial_in_response()
        meeting_id = next((_clean(o.get('online_meeting_id')) for o in occurrences if _clean(o.get('online_meeting_id'))), '')
    if not has_graph_credentials():
        return _dial_in_response()
    try:
        code, passcode = read_teams_dial_in(row.get('organizer_email'), meeting_id, link)
    except Exception as exc:  # noqa: BLE001 - optional detail; the link works without it
        logger.warning('Teams meeting ID and passcode could not be read for %s: %s', live_session_id, exc)
        return _dial_in_response()
    if code and link == series_link:
        try:
            store_teams_dial_in(live_session_id, link, code, passcode)
        except Exception as exc:  # noqa: BLE001 - shown now, stored next time
            logger.warning('Teams meeting ID and passcode not stored for %s: %s', live_session_id, exc)
    return _dial_in_response(link, code, passcode)
