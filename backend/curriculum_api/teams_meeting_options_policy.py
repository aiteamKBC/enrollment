"""Which onlineMeeting options a Teams save has to send to Microsoft, and why.

A save is several separate operations: who is invited (the calendar event),
the dates (the calendar event), and how the meeting runs (the onlineMeeting).
Only the last is a ``PATCH users/{organizer}/onlineMeetings/{id}``, and it needs
``OnlineMeetings.ReadWrite.All`` plus a Teams application access policy on the
organiser. Sending it on a save that changed none of its fields turned an
organiser-permission gap into a failed invitation save, so it is now sent only
for the option groups that changed, that a previous attempt left unapplied, or
for a meeting Microsoft has just created.

Groups:
- ``settings``: lobby, recording/auto-record/transcription, spoken language.
- ``roles``: presenters and co-organisers. Microsoft requires the full
  attendee list whenever ``participants.attendees`` is sent, and the full
  list with roles whenever ``allowedPresenters`` is ``roleIsPresenter``.

Plain attendee changes are not an onlineMeeting change: the invitation is the
calendar event, and that is written by its own operation.
"""
import json
import re
from datetime import datetime, timezone

SETTINGS = 'settings'
ROLES = 'roles'
ALL_GROUPS = frozenset({SETTINGS, ROLES})
NOT_APPLIED = 'teams_meeting_options_not_applied'


def _text(value):
    return str(value or '').strip()


def _emails(values):
    return {_text(value).casefold() for value in values or () if _text(value)}


def json_list(value):
    """A stored JSON list column, whether the driver returned text or a list."""

    if isinstance(value, str):
        try:
            value = json.loads(value or '[]')
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def stored_email_list(value):
    """Lower-cased addresses of a stored roster column (text or list, strings or {email})."""

    result = []
    for item in value if isinstance(value, (list, tuple)) else json_list(value):
        if isinstance(item, dict):
            item = item.get('email') or item.get('address') or (item.get('emailAddress') or {}).get('address')
        email = _text(item).casefold()
        if '@' in email and email not in result:
            result.append(email)
    return result


def stored_options(series, default_lobby='invited'):
    """The options the LMS last saved for a series, in the shape changed_option_groups compares."""

    return {
        'recording': _text(series.get('recording')).lower() or 'none',
        'lobby_bypass': _text(series.get('lobby_bypass')).lower() or default_lobby,
        'spoken_language': _text(series.get('spoken_language')) or 'en-GB',
        'presenters': stored_email_list(series.get('presenters')),
        'co_organizers': stored_email_list(series.get('co_organizers')),
    }


def changed_option_groups(stored, requested):
    """Groups whose requested value differs from the saved one.

    ``stored`` and ``requested`` are dicts with recording, lobby_bypass,
    spoken_language, presenters and co_organizers.
    """

    groups = set()
    if any(_text(stored.get(key)).casefold() != _text(requested.get(key)).casefold()
           for key in ('recording', 'lobby_bypass', 'spoken_language')):
        groups.add(SETTINGS)
    co_organizers = _emails(requested.get('co_organizers'))
    stored_co_organizers = _emails(stored.get('co_organizers'))
    if (co_organizers != stored_co_organizers
            or _emails(requested.get('presenters')) - co_organizers
            != _emails(stored.get('presenters')) - stored_co_organizers):
        groups.add(ROLES)
    return groups


def pending_option_groups(warnings):
    """Option groups a previous save could not apply, read from the saved warnings."""

    pending = set()
    for warning in warnings or ():
        if isinstance(warning, dict) and warning.get('code') == NOT_APPLIED:
            groups = warning.get('groups')
            pending |= ({group for group in groups if group in ALL_GROUPS}
                        if isinstance(groups, list) and groups else set(ALL_GROUPS))
    return pending


def option_groups_to_apply(stored, requested, pending=(), *, new_meeting=False, retry_pending=True):
    """The onlineMeeting groups this save must send. Empty means no options PATCH.

    ``retry_pending`` is False for a save that only changes who is invited: it
    must not re-attempt settings it did not touch.
    """

    if new_meeting:
        return set(ALL_GROUPS)
    groups = changed_option_groups(stored, requested)
    if retry_pending:
        groups |= set(pending) & ALL_GROUPS
    return groups


def remaining_pending(pending, attempted, failed):
    """Groups still unapplied after a save: old ones not retried, plus new failures."""

    return (set(pending) - set(attempted)) | set(failed)


def pending_warning(groups, previous=None, requested=None):
    """The saved marker for unapplied options, keeping Microsoft's last reason.

    ``requested`` is the settings the author asked for (recording,
    lobby_bypass, spoken_language) when Microsoft refused them: the series
    columns keep the last accepted values, so Retry needs these to send the
    change itself. Omitted, the previous marker's are kept.
    """

    previous = previous if isinstance(previous, dict) else {}
    if requested is None:
        requested = previous.get('requested')
    requested = ({key: _text(requested.get(key)) for key in ('recording', 'lobby_bypass', 'spoken_language')
                  if _text(requested.get(key))} if isinstance(requested, dict) and SETTINGS in groups else {})
    names = {SETTINGS: 'lobby, recording, transcription and language', ROLES: 'presenter and co-organiser roles'}
    return {
        'code': NOT_APPLIED,
        'groups': sorted(groups),
        'message': ('Microsoft has not applied this meeting’s '
                    + ' or '.join(names[group] for group in sorted(groups))
                    + '. Invitations and dates are not affected. Saving these settings again retries them.'),
        'detail': _text(previous.get('detail')),
        'graphError': previous.get('graphError') or graph_error_details(previous.get('detail')),
        'requested': requested,
    }


_GRAPH_ERROR = re.compile(
    r'Microsoft Graph (?P<method>[A-Z]+) (?P<path>\S+) failed: HTTP (?P<status>\d{3})'
    r'(?:; code=(?P<code>[^;]*))?(?:; message=(?P<message>.*?))?(?:; request-id=(?P<request_id>[0-9a-fA-F-]+))?\s*$',
    re.S,
)


def graph_error_details(text, *, at=None):
    """Split the transport's error sentence into the fields support asks for."""

    text = _text(text)
    match = _GRAPH_ERROR.search(text)
    if not match:
        return {'status': None, 'code': '', 'message': text, 'requestId': '', 'method': '', 'path': '',
                'at': at or ''}
    return {
        'status': int(match.group('status')), 'code': _text(match.group('code')),
        'message': _text(match.group('message')), 'requestId': _text(match.group('request_id')),
        'method': match.group('method'), 'path': match.group('path'),
        'at': at or datetime.now(timezone.utc).isoformat(),
    }


def meeting_options_patch(groups, *, recording, lobby_scope, spoken_language, attendees, presenters, co_organizers):
    """The onlineMeeting PATCH body for these groups only (empty when none).

    ``lobby_scope`` is already Graph's value (e.g. ``invited``,
    ``organizationExcludingGuests``). Roles keep Microsoft's mandatory pairing:
    the complete attendee list, each with a role, and ``roleIsPresenter`` only
    when somebody actually presents (naming nobody would mute the tutor too).
    """

    patch = {}
    recording = _text(recording).lower() or 'none'
    if SETTINGS in groups:
        patch.update({
            'lobbyBypassSettings': {'scope': lobby_scope, 'isDialInBypassEnabled': False},
            # Recording, automatic recording and transcription move together:
            # transcription without recording is not a state the LMS offers.
            'allowRecording': recording != 'none',
            'recordAutomatically': recording != 'none',
            'allowTranscription': recording == 'record-transcribe',
            'meetingSpokenLanguageTag': _text(spoken_language) or 'en-GB',
        })
    if ROLES in groups:
        co_list = list(dict.fromkeys(_text(email).casefold() for email in co_organizers or () if _text(email)))
        co_set = set(co_list)
        presenter_list = [email for email in dict.fromkeys(_text(email).casefold() for email in presenters or ())
                          if email and email not in co_set]
        presenter_set = set(presenter_list)
        attendee_list = [email for email in dict.fromkeys(_text(email).casefold() for email in attendees or ())
                         if email and email not in co_set and email not in presenter_set]
        roster = [*co_list, *presenter_list, *attendee_list]
        if roster:
            patch['participants'] = {'attendees': [
                {'upn': email, 'role': 'coorganizer' if email in co_set else 'presenter' if email in presenter_set else 'attendee'}
                for email in roster
            ]}
        if presenter_list or co_list:
            patch['allowedPresenters'] = 'roleIsPresenter'
    return patch


def reported_failure(item):
    """An options refusal, worded for a save whose invitations and dates went ahead."""

    if not isinstance(item, dict):
        return item
    names = {SETTINGS: 'lobby, recording, transcription and language settings', ROLES: 'presenter and co-organiser roles'}
    groups = [group for group in sorted(item.get('groups') or ALL_GROUPS) if group in names]
    error = item.get('graphError') if isinstance(item.get('graphError'), dict) else graph_error_details(item.get('detail'))
    said = ' '.join(part for part in (f"HTTP {error['status']}" if error.get('status') else '', _text(error.get('code')),
                                      _text(error.get('message'))) if part)
    return {**item, 'graphError': error, 'message': (
        'Invitations and dates were saved, but Microsoft did not apply this meeting’s '
        + ' or '.join(names[group] for group in groups)
        + (f' (Microsoft said: {said}).' if said else '.')
        + ' Use “Check Microsoft permissions” on this meeting to see why.'
    )}
