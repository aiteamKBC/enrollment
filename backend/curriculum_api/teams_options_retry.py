"""Where a meeting stands with Microsoft, and the one action that retries it.

A Teams save is several operations (see ``teams_meeting_options_policy``): the
invitation and the dates are the calendar event; how the meeting runs -- the
lobby, recording, transcription, language (``settings``) and the presenter and
co-organiser roles (``roles``) -- is the onlineMeeting. When Microsoft refuses
the onlineMeeting write the save still keeps the invitation and the dates, and
leaves a ``teams_meeting_options_not_applied`` marker on the series naming the
groups still unapplied.

This module reads that marker into one of three states the page shows:

* ``saved``   -- nothing is waiting on Microsoft;
* ``pending`` -- option groups are waiting, with no refusal on record;
* ``failed``  -- Microsoft refused the last attempt (its HTTP status, code and
  request ID are kept).

and offers **Retry**, which re-sends only the pending groups and nothing else:

* no calendar event is read for a change or written, no date moves;
* nothing is sent to anybody -- the onlineMeeting PATCH carries no invitation,
  and no LMS email is sent;
* nothing is cancelled or deleted;
* a group is reported saved only after Microsoft's read-back of the meeting
  shows the requested values. An accepted write whose read-back fails or
  disagrees stays pending (or failed), never "saved".
"""
from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timezone

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from login.permissions import require_role

from .teams_meeting_options_policy import ALL_GROUPS, NOT_APPLIED, ROLES, SETTINGS, json_list, pending_warning

logger = logging.getLogger(__name__)

#: How an additional week meeting words its unapplied-options warning (it saves
#: its warnings as sentences, not markers).
WEEK_MEETING_MARKERS = ('did not apply its lobby', 'has not applied this meeting')
UNVERIFIED = 'teams_calendar_unverified'

SAVED, PENDING, FAILED = 'saved', 'pending', 'failed'


def _text(value):
    return str(value or '').strip()


def _is_week_marker(item):
    message = item if isinstance(item, str) else (item or {}).get('message') if isinstance(item, dict) else ''
    return any(marker in _text(message) for marker in WEEK_MEETING_MARKERS)


def _is_marker(item):
    return (isinstance(item, dict) and item.get('code') == NOT_APPLIED) or _is_week_marker(item)


def pending_state(warnings):
    """The unapplied option groups saved on a series, and what Microsoft last said.

    Reads both the structured marker and an additional week meeting's sentence.
    A marker that names no groups means every group is unapplied.
    """
    groups, marker, graph_error, requested = set(), None, None, {}
    for item in json_list(warnings) if not isinstance(warnings, list) else warnings:
        if not _is_marker(item):
            continue
        if isinstance(item, dict):
            named = item.get('groups')
            groups |= ({group for group in named if group in ALL_GROUPS}
                       if isinstance(named, list) and named else set(ALL_GROUPS))
            marker = marker or item
            if isinstance(item.get('graphError'), dict) and not graph_error:
                graph_error = item['graphError']
            if isinstance(item.get('requested'), dict) and not requested:
                requested = item['requested']
        else:
            groups |= set(ALL_GROUPS)
            marker = marker or {'message': _text(item)}
    return {'groups': groups, 'marker': marker, 'graphError': graph_error, 'requested': requested}


def microsoft_update_summary(warnings, verified=False):
    """``{state, pendingGroups, lastError, retryable, verification, message}`` for one series' saved warnings.

    ``verification`` keeps three facts apart: ``read_back`` (Microsoft's
    read-back just showed the values -- only Retry establishes this),
    ``accepted`` (Microsoft accepted the last write and nothing is pending, but
    nobody has read it back since) and ``not_applied``.
    """

    items = json_list(warnings) if not isinstance(warnings, list) else warnings
    pending = pending_state(items)
    unverified = next((item for item in items if isinstance(item, dict) and item.get('code') == UNVERIFIED), None)
    error = pending['graphError'] if isinstance(pending['graphError'], dict) else None
    refused = bool(error and error.get('status'))
    if pending['groups']:
        state = FAILED if refused else PENDING
        names = {SETTINGS: 'lobby, recording, transcription and language', ROLES: 'presenter and co-organiser roles'}
        what = ' and '.join(names[group] for group in sorted(pending['groups']))
        message = (f'Microsoft refused this meeting’s {what}. Invitations and dates are saved.' if refused else
                   f'This meeting’s {what} are waiting for Microsoft. Invitations and dates are saved.')
    elif unverified:
        state = FAILED
        message = ('The last save could not be confirmed with Microsoft. Update the Teams calendar again to finish it.')
    elif verified:
        state, message = SAVED, 'Microsoft’s read-back shows the meeting settings saved here.'
    else:
        state, message = SAVED, ('Microsoft accepted the last update of these meeting settings. '
                                 'Nothing is waiting to be retried.')
    return {
        'state': state,
        'pendingGroups': sorted(pending['groups']),
        'lastError': ({key: error.get(key) for key in ('status', 'code', 'message', 'requestId', 'at')}
                      if error else None),
        # Retry only ever re-sends the meeting options. A save that could not
        # be confirmed is finished with the calendar update itself.
        'retryable': bool(pending['groups']),
        'verification': ('not_applied' if pending['groups'] or unverified
                         else 'read_back' if verified else 'accepted'),
        'message': message,
    }


def expected_options(groups, settings, roles, lobby_values):
    """What Microsoft's read-back must show once these groups are applied."""

    expected = {}
    if SETTINGS in groups:
        recording = _text(settings.get('recording')).lower() or 'none'
        lobby = _text(settings.get('lobby_bypass')).lower() or 'invited'
        expected.update({
            'allowRecording': recording != 'none',
            'recordAutomatically': recording != 'none',
            'allowTranscription': recording == 'record-transcribe',
            'lobbyScope': lobby_values.get(lobby, lobby_values.get('invited', 'invited')),
            'spokenLanguage': _text(settings.get('spoken_language')) or 'en-GB',
        })
    if ROLES in groups:
        co = [_text(email).casefold() for email in roles.get('co_organizers') or () if _text(email)]
        presenters = [_text(email).casefold() for email in roles.get('presenters') or ()
                      if _text(email) and _text(email).casefold() not in co]
        expected['roles'] = {**{email: 'presenter' for email in presenters}, **{email: 'coorganizer' for email in co}}
    return expected


def confirm_options(meeting, expected):
    """Compare Microsoft's read-back with what was asked for.

    Returns ``(confirmed_groups, problems)``. A value Microsoft does not return
    is unconfirmed, never assumed. The spoken language is compared only when
    Microsoft returns it (not every tenant exposes it on a read).
    """
    meeting = meeting if isinstance(meeting, dict) else {}
    problems, confirmed = [], set()
    if 'allowRecording' in expected:
        before = len(problems)
        for key in ('allowRecording', 'recordAutomatically', 'allowTranscription'):
            if key not in meeting:
                problems.append(f'Microsoft did not return {key}.')
            elif bool(meeting.get(key)) != expected[key]:
                problems.append(f'Microsoft holds {key}={str(bool(meeting.get(key))).lower()}.')
        scope = _text((meeting.get('lobbyBypassSettings') or {}).get('scope'))
        if not scope:
            problems.append('Microsoft did not return the lobby setting.')
        elif scope.casefold() != _text(expected['lobbyScope']).casefold():
            problems.append(f'Microsoft holds lobby={scope}.')
        language = _text(meeting.get('meetingSpokenLanguageTag'))
        if language and language.casefold() != _text(expected['spokenLanguage']).casefold():
            problems.append(f'Microsoft holds language={language}.')
        if len(problems) == before:
            confirmed.add(SETTINGS)
    if 'roles' in expected:
        attendees = (meeting.get('participants') or {}).get('attendees')
        if not isinstance(attendees, list):
            problems.append('Microsoft did not return the meeting’s participants.')
        else:
            held = {_text(item.get('upn') or ((item.get('identity') or {}).get('user') or {}).get('userPrincipalName')
                          ).casefold(): _text(item.get('role')).casefold()
                    for item in attendees if isinstance(item, dict)}
            wrong = [email for email, role in expected['roles'].items() if held.get(email) != role]
            if wrong:
                problems.append(f'Microsoft does not hold the requested role for {len(wrong)} '
                                f'{"person" if len(wrong) == 1 else "people"}.')
            else:
                confirmed.add(ROLES)
    return confirmed, problems


def meetings_of(series, manifest, occurrences):
    """Every Teams meeting this series owns: the series meeting, each weekday's, each standalone session's.

    Keyed by join link, so one meeting is never written twice.
    """
    seen, meetings = set(), []

    def add(join_url, online_meeting_id, label):
        join_url, online_meeting_id = _text(join_url), _text(online_meeting_id)
        key = join_url or online_meeting_id
        if key and key not in seen:
            seen.add(key)
            meetings.append({'joinUrl': join_url, 'onlineMeetingId': online_meeting_id, 'label': label})

    add(series.get('join_url'), series.get('online_meeting_id'), 'series')
    for entry in manifest or ():
        if isinstance(entry, dict):
            add(entry.get('joinUrl'), entry.get('onlineMeetingId'), f"day-{_text(entry.get('day'))}")
    series_event = _text(series.get('graph_event_id'))
    for row in occurrences or ():
        event_id = _text(row.get('graph_event_id'))
        if event_id and event_id != series_event and _text(row.get('status')).lower() not in {'cancelled', 'superseded'}:
            add(row.get('join_url'), row.get('online_meeting_id'), f"session-{_text(row.get('session_number'))}")
    return meetings


def retry_options(v, graph_request, live_session_id, *, record_failure=None, now=None):
    """Re-send a series' pending option groups and confirm them. Returns ``(body, status)``.

    ``v`` is the curriculum views module (injected so this can be tested
    without Django or Microsoft).
    """
    if record_failure is None:
        from .teams_graph_failure_log import record_graph_failure as record_failure
    from .teams_graph_failure_log import OPTIONS_CONFIRM, OPTIONS_PATCH, OPTIONS_RESOLVE

    now = now or datetime.now(timezone.utc)
    rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])
    if not rows:
        return {'error': 'Live session series not found.', 'code': 'not_found'}, 404
    series = rows[0]
    if _text(series.get('status')).lower() == 'cancelled':
        return {'error': 'This Teams calendar is cancelled. Nothing was sent.', 'code': 'cancelled'}, 409
    warnings = json_list(series.get('warnings'))
    pending = pending_state(warnings)
    groups = set(pending['groups'])
    if not groups:
        return {'retried': [], 'microsoftUpdate': microsoft_update_summary(warnings),
                'message': 'Nothing is waiting for Microsoft on this meeting. Nothing was sent.'}, 200

    organizer = _text(series.get('organizer_email')) or v.teams_meeting_default_organizer()
    stored = {'recording': _text(series.get('recording')).lower() or 'none',
              'lobby_bypass': _text(series.get('lobby_bypass')).lower() or 'invited',
              'spoken_language': _text(series.get('spoken_language')) or 'en-GB'}
    # The settings the author asked for when Microsoft refused them. Without
    # them only the settings saved here can be re-sent -- said plainly below.
    requested = {**stored, **{key: _text(value) for key, value in (pending['requested'] or {}).items()
                              if key in stored and _text(value)}}
    requested_unknown = SETTINGS in groups and not pending['requested']
    presenters = v.teams_series_email_list(series.get('presenters'))
    co_organizers = v.teams_series_email_list(series.get('co_organizers'))
    attendees = v.teams_series_email_list(series.get('attendees'))
    invited = list(dict.fromkeys([*co_organizers, *presenters, *attendees]))
    expected = expected_options(groups, requested, {'presenters': presenters, 'co_organizers': co_organizers},
                                v.TEAMS_LOBBY_VALUES)

    occurrences = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_session_id])
    stored_calendar_series = getattr(v, 'stored_calendar_series', None)
    if stored_calendar_series is None:
        from .teams_weekly_calendar import stored_calendar_series
    meetings = meetings_of(series, stored_calendar_series(series), occurrences)
    if not meetings:
        return {'error': 'This series has no Teams meeting to update yet. Nothing was sent.',
                'code': 'no_meeting'}, 409

    confirmed_everywhere = set(groups)
    results, last_error = [], None
    # Name the series in the failure log when the options PATCH records it itself.
    log_context = ({'live_session_id': live_session_id}
                   if 'live_session_id' in inspect.signature(v.apply_teams_meeting_options).parameters else {})
    for meeting in meetings:
        owner_id = v.teams_online_meeting_owner_id(organizer, meeting['joinUrl'])
        context = {'live_session_id': live_session_id, 'organizer': organizer, 'organizer_object_id': owner_id,
                   'groups': sorted(groups)}
        applied, resolved, option_warnings = v.apply_teams_meeting_options(
            organizer, meeting['joinUrl'], recording=requested['recording'], lobby_bypass=requested['lobby_bypass'],
            spoken_language=requested['spoken_language'], attendees=invited, presenters=presenters,
            co_organizers=co_organizers, online_meeting_id=meeting['onlineMeetingId'], groups=groups, **log_context,
        )
        meeting_id = meeting['onlineMeetingId'] or _text((resolved or {}).get('id'))
        result = {'meeting': meeting['label'], 'accepted': bool(applied), 'confirmed': [], 'problems': []}
        if not applied:
            for item in option_warnings:
                detail = _text(item.get('detail'))
                if not item.get('failureLogged'):
                    record_failure(OPTIONS_PATCH if '/onlineMeetings/' in detail and ' PATCH ' in f' {detail}'
                                   else OPTIONS_RESOLVE, detail or _text(item.get('message')),
                                   online_meeting_id=meeting_id, **context)
                last_error = item
                result['problems'].append(_text(item.get('message')))
            confirmed_everywhere = set()
            results.append(result)
            continue
        # Accepted is not confirmed: read the meeting back.
        try:
            if not meeting_id:
                raise RuntimeError('Microsoft has not exposed this meeting’s ID, so it cannot be read back.')
            held = graph_request('GET', v.teams_meeting_base_path(organizer, meeting_id, meeting['joinUrl']))
            confirmed, problems = confirm_options(held, expected)
        except RuntimeError as exc:
            record_failure(OPTIONS_CONFIRM, str(exc), online_meeting_id=meeting_id, **context)
            confirmed, problems = set(), [f'Microsoft accepted the update, but it could not be read back: {exc}']
        if problems and not last_error:
            last_error = {'detail': '', 'graphError': None, 'message': ' '.join(problems)}
        if problems:
            record_failure(OPTIONS_CONFIRM, ' '.join(problems), online_meeting_id=meeting_id, **context)
        result.update(confirmed=sorted(confirmed), problems=problems)
        confirmed_everywhere &= confirmed
        results.append(result)

    remaining = groups - confirmed_everywhere
    # Written only if nobody saved this meeting while Microsoft was answering:
    # a newer save owns the marker and the settings, and this retry must not
    # put back what it replaced.
    fresh_rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id])
    fresh_warnings = json_list((fresh_rows[0] if fresh_rows else {}).get('warnings'))
    superseded = (json.dumps(pending['marker'], sort_keys=True, default=str)
                  != json.dumps(pending_state(fresh_warnings)['marker'], sort_keys=True, default=str))
    if not superseded:
        kept = [item for item in fresh_warnings if not _is_marker(item)]
        update = {'updated_at': now.replace(tzinfo=None)}
        if remaining:
            marker = pending_warning(remaining, last_error or pending['marker'] or {})
            previous = pending['marker'] if isinstance(pending['marker'], dict) else {}
            marker.update({'requested': pending['requested'] or {}, 'attempts': int(previous.get('attempts') or 1) + 1,
                           'lastAttemptAt': now.isoformat()})
            kept.append(marker)
        if SETTINGS in confirmed_everywhere and pending['requested']:
            # Microsoft now holds the requested settings: so does the LMS.
            update.update({'recording': requested['recording'], 'lobby_bypass': requested['lobby_bypass'],
                           'spoken_language': requested['spoken_language']})
        update['warnings'] = v.json_db_value(kept)
        v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], update)
        v.invalidate_curriculum_cache()
        summary = microsoft_update_summary(kept, verified=not remaining)
    else:
        summary = microsoft_update_summary(fresh_warnings)

    if superseded:
        message = 'This meeting was saved again while Microsoft was answering. Its latest state is shown.'
    elif not remaining:
        message = ('Microsoft confirmed the meeting settings.' if not requested_unknown else
                   'Microsoft now holds the settings saved here. The settings change Microsoft refused earlier was '
                   'not kept; choose it again in the meeting settings.')
    else:
        message = 'Microsoft did not confirm every setting. Invitations and dates are unchanged.'
    return {
        'retried': sorted(groups), 'confirmed': sorted(confirmed_everywhere), 'remaining': sorted(remaining),
        'meetings': results, 'superseded': superseded, 'requestedChangeUnknown': requested_unknown,
        'microsoftUpdate': summary, 'message': message,
    }, 200


@require_role('admin', 'staff')
@require_POST
def teams_meeting_options_retry(request, live_session_id):
    """Staff only: re-send this meeting's pending settings/roles. No email, no dates, no cancellation."""
    from coach_api.views import has_graph_credentials, microsoft_graph_request
    from . import views as v

    if not has_graph_credentials():
        return JsonResponse({'error': 'Microsoft Graph credentials are not configured.',
                             'code': 'graph_not_configured'}, status=503)
    try:
        body, status = retry_options(v, microsoft_graph_request, _text(live_session_id))
    except Exception:
        logger.exception('Teams meeting options retry failed for %s', live_session_id)
        return JsonResponse({'error': 'The retry could not finish. Invitations and dates are unchanged.',
                             'code': 'retry_failed'}, status=502)
    response = JsonResponse(body, status=status)
    response['Cache-Control'] = 'no-store, private'
    return response
