"""Read participants of an imported Teams meeting without adopting its calendar.

Calendar invitations, online-meeting roles and channel membership are different
things. This report reads the first two separately and never infers the third.
An attendee mailbox discovered from a join link is only a read source: neither
its event IDs nor the returned people are saved as LMS-managed meeting data.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

import httpx
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from login.permissions import require_role


GUID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')
EVENT_FIELDS = ('id,iCalUId,subject,type,isCancelled,isOrganizer,organizer,attendees,'
                'hideAttendees,onlineMeeting,recurrence')
BASE = 'https://graph.microsoft.com/v1.0/'


def text(value):
    return str(value or '').strip()


def unavailable(status, message):
    return {'status': status, 'message': message}


class IncompleteRead(RuntimeError):
    pass


def join_context(join_url):
    """Context identities are discovery candidates, never proof of ownership."""
    try:
        parsed = urlsplit(text(join_url))
        if (parsed.scheme != 'https' or parsed.hostname not in
                {'teams.microsoft.com', 'teams.cloud.microsoft'} or parsed.username or parsed.password):
            return {}
        values = parse_qs(parsed.query).get('context') or []
        if len(values) != 1:
            return {}
        context = json.loads(values[0])
        if not isinstance(context, dict):
            return {}
        result = {}
        for key in ('Oid', 'Tid'):
            value = text(context.get(key) or context.get(key.lower()))
            if GUID.fullmatch(value):
                result[key] = value
        return result
    except (TypeError, ValueError):
        return {}


def join_context_oid(join_url):
    return join_context(join_url).get('Oid', '')


def calendar_collection(read, path):
    """Complete exact-subject discovery, bounded to three pages / 300 events."""
    expected_resource = unquote(urlsplit(path).path)
    seen, rows = set(), []
    for _ in range(3):
        if path in seen:
            raise IncompleteRead()
        seen.add(path)
        try:
            data = read(path)
        except (RuntimeError, httpx.HTTPError, ValueError, TypeError):
            if len(seen) > 1:
                raise IncompleteRead() from None
            raise
        if not isinstance(data, dict) or not isinstance(data.get('value'), list):
            raise IncompleteRead()
        if any(not isinstance(item, dict) for item in data['value']):
            raise IncompleteRead()
        rows.extend(data['value'])
        if len(rows) > 300:
            raise IncompleteRead()
        next_link = data.get('@odata.nextLink')
        if not next_link:
            return rows
        target = urlsplit(text(next_link))
        if (target.scheme != 'https' or target.netloc != 'graph.microsoft.com'
                or target.fragment or not target.path.startswith('/v1.0/')
                or unquote(target.path[len('/v1.0/'):]) != expected_resource):
            raise IncompleteRead()
        path = target.path[len('/v1.0/'):] + ('?' + target.query if target.query else '')
    raise IncompleteRead()


def person(address, name=''):
    return {'email': text(address), 'name': text(name)}


def calendar_person(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('emailAddress'), dict):
        return None
    identity = raw['emailAddress']
    result = person(identity.get('address'), identity.get('name'))
    return result if result['email'] or result['name'] else None


def unique_people(people):
    result, seen = [], set()
    for item in people:
        key = (item['email'].casefold(), item['name'] if not item['email'] else '')
        if key not in seen:
            result.append(item)
            seen.add(key)
    return result


def read_failure(exc, what):
    if getattr(exc, 'status_code', 0) in (401, 403):
        return unavailable('access_denied', f'Microsoft did not allow reading {what}. No changes were made.')
    return unavailable('unavailable', f'{what.capitalize()} could not be verified from Microsoft. Retry the check.')


def calendar_report(series, read):
    link, subject = text(series.get('join_url')), text(series.get('module_title'))
    candidates = list(dict.fromkeys(value for value in
        (text(series.get('organizer_email')), join_context_oid(link)) if value))
    if not link or not subject or not candidates:
        return unavailable('not_connected', 'The saved link has insufficient calendar identity to read invitations.')
    matches, failures = [], []
    query = urlencode({'$filter': "subject eq '" + subject.replace("'", "''") + "'",
                       '$select': EVENT_FIELDS, '$top': 100})
    for candidate in candidates:
        try:
            events = calendar_collection(read, f'users/{quote(candidate, safe="")}/events?{query}')
        except IncompleteRead:
            # An incomplete mailbox cannot prove that its matching series is unique.
            return unavailable('unavailable', 'Microsoft returned an incomplete calendar search. No invitation list was inferred.')
        except (RuntimeError, httpx.HTTPError, ValueError, TypeError) as exc:
            failures.append(read_failure(exc, 'calendar invitations'))
            continue
        found = [event for event in events
                 if text(event.get('subject')) == subject and event.get('type') == 'seriesMaster'
                 and text(event.get('id')) and not event.get('isCancelled')
                 and isinstance(event.get('recurrence'), dict)
                 and isinstance(event.get('onlineMeeting'), dict)
                 and text(event['onlineMeeting'].get('joinUrl')) == link]
        if len(found) > 1:
            return unavailable('ambiguous', 'More than one recurring calendar event uses this link and title. An administrator must identify the original series.')
        if found:
            matches.append(found[0])
    if not matches:
        if failures:
            return next((item for item in failures if item['status'] == 'access_denied'), failures[0])
        return unavailable('not_connected', 'No unique recurring calendar event matching the saved link was found.')
    if len(matches) > 1 and (not text(matches[0].get('iCalUId')) or
                            len({text(item.get('iCalUId')) for item in matches}) != 1):
        return unavailable('ambiguous', 'The accessible calendars returned different or unverified series identities. No invitation list was inferred.')
    event = next((item for item in matches if item.get('isOrganizer') is True), matches[0])
    owned = event.get('isOrganizer') is True
    result = {'status': 'available', 'source': 'organizer_calendar' if owned else 'attendee_calendar',
              'message': ('Calendar invitations read from Microsoft. This is not channel membership.' if owned else
                          'Calendar invitations read from an attendee copy. This does not grant calendar editing access or list channel membership.')}
    organizer = calendar_person(event.get('organizer'))
    if organizer:
        result['organizer'] = organizer
    if isinstance(event.get('hideAttendees'), bool):
        result['hideAttendees'] = event['hideAttendees']
    raw = event.get('attendees')
    if isinstance(raw, list):
        people = [calendar_person(item) for item in raw]
        result['attendees'] = unique_people([item for item in people if item])
        if any(item is None for item in people):
            result.update(status='partial', message='Microsoft returned some invitations without readable identities. The displayed list is incomplete.')
    else:
        result.update(status='partial', message='Microsoft did not return an invitation list. Missing data does not mean nobody was invited.')
    if not owned and event.get('hideAttendees') is not False:
        result.update(status='partial', message='This attendee copy may hide other invitees. Only the invitations Microsoft exposed are shown; the full list is not confirmed.')
    return result


def meeting_person(raw):
    if not isinstance(raw, dict):
        return None
    identity = raw.get('identity') if isinstance(raw.get('identity'), dict) else {}
    user = identity.get('user') if isinstance(identity.get('user'), dict) else {}
    result = person(raw.get('upn'), user.get('displayName'))
    return result if result['email'] or result['name'] else None


def roles_report(series, read):
    link, meeting_id = text(series.get('join_url')), text(series.get('online_meeting_id'))
    owner = join_context_oid(link)
    if not owner or not meeting_id:
        return unavailable('not_connected', 'A verified online-meeting identity is needed to read presenter roles.')
    try:
        meeting = read(f'users/{quote(owner, safe="")}/onlineMeetings/{quote(meeting_id, safe="")}',
                       prefer='include-unknown-enum-members')
    except (RuntimeError, httpx.HTTPError, ValueError, TypeError) as exc:
        return read_failure(exc, 'meeting roles')
    if not isinstance(meeting, dict):
        return unavailable('unavailable', 'Microsoft did not return the online meeting.')
    participants = meeting.get('participants') if isinstance(meeting.get('participants'), dict) else {}
    organizer = participants.get('organizer') if isinstance(participants.get('organizer'), dict) else {}
    identity = organizer.get('identity') if isinstance(organizer.get('identity'), dict) else {}
    user = identity.get('user') if isinstance(identity.get('user'), dict) else {}
    if (text(meeting.get('id')) != meeting_id or text(meeting.get('joinWebUrl')) != link
            or text(user.get('id')).casefold() != owner.casefold()):
        return unavailable('unavailable', 'The returned online-meeting identity did not match the saved link. Roles were not inferred.')
    result = {'status': 'available', 'message': 'Explicit meeting roles read from Microsoft; these are separate from calendar invitations and channel membership.'}
    named_organizer = meeting_person(organizer)
    if named_organizer:
        result['organizer'] = named_organizer
    if isinstance(meeting.get('allowedPresenters'), str):
        result['allowedPresenters'] = meeting['allowedPresenters']
    raw = participants.get('attendees')
    if not isinstance(raw, list):
        result.update(status='partial', message='Microsoft did not return explicit participant roles. Missing data does not mean no presenters or co-organisers.')
        return result
    presenters, co_organizers, incomplete = [], [], False
    for entry in raw:
        if not isinstance(entry, dict) or text(entry.get('role')) not in {'attendee', 'presenter', 'coorganizer'}:
            incomplete = True
            continue
        if entry['role'] == 'attendee':
            continue
        named = meeting_person(entry)
        if named is None:
            incomplete = True
            continue
        (presenters if entry['role'] == 'presenter' else co_organizers).append(named)
    if presenters or not incomplete:
        result['presenters'] = unique_people(presenters)
    if co_organizers or not incomplete:
        result['coOrganizers'] = unique_people(co_organizers)
    if incomplete:
        result.update(status='partial', message='Some Microsoft participant identities or roles could not be read. Displayed role assignments may be incomplete.')
    return result


def organizer_report(series, roles, checked_at, saved_evidence=None):
    """Keep the actual meeting owner separate from its calendar/channel mailbox."""
    context = join_context(series.get('join_url'))
    if roles.get('status') in {'available', 'partial'}:
        # These statuses are returned only after the online-meeting ID, link
        # and organiser object ID all pass roles_report's identity check.
        named = roles.get('organizer')
        result = {'status': 'available' if named else 'partial', 'source': 'online_meeting',
                  'objectId': context.get('Oid', ''), 'verifiedAt': checked_at,
                  'message': 'Meeting organiser identity verified from the online meeting in Microsoft.'}
        if named:
            result['person'] = named
        return result
    if saved_evidence is not None:
        from .teams_imported_organizer import saved_organizer_report
        try:
            saved = saved_organizer_report(series, context, saved_evidence(series, context))
            if saved.get('source') or saved.get('status') == 'ambiguous':
                return saved
        except (DatabaseError, RuntimeError, ValueError, TypeError):
            return {'status': 'unavailable', 'message': 'A fresh Microsoft organiser lookup was unavailable, and saved organiser evidence could not be read. Retry; no person identity was inferred.'}
    return {'status': roles.get('status') if roles.get('status') in {'access_denied', 'not_connected'} else 'unavailable',
            'message': 'The actual meeting organiser could not be verified. The calendar/channel mailbox is shown separately and is not used as a person identity.'}


def imported_participant_report(series, reader, saved_evidence=None):
    checked_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        with reader() as read:
            calendar = calendar_report(series, read)
            roles = roles_report(series, read)
    except (RuntimeError, httpx.HTTPError, ValueError, TypeError) as exc:
        calendar = read_failure(exc, 'calendar invitations')
        roles = read_failure(exc, 'meeting roles')
    organizer = organizer_report(series, roles, checked_at, saved_evidence)
    return {'checkedAt': checked_at, 'calendar': calendar, 'roles': roles, 'organizer': organizer}


@never_cache
@require_role('admin', 'staff')
@require_GET
def imported_meeting_participants(request, live_session_id):
    from . import views as v
    from .teams_calendar_state import calendar_reader
    from .teams_imported_organizer import load_saved_organizer_evidence

    try:
        rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], ensure_tables=False)
    except DatabaseError:
        return JsonResponse({'error': 'The saved meeting could not be read. Retry.', 'code': 'temporarily_unavailable'}, status=503)
    if not rows:
        return JsonResponse({'error': 'Live session series not found.', 'code': 'not_found'}, status=404)
    if text(rows[0].get('meeting_type')) != 'linked-external':
        return JsonResponse({'error': 'This read-only report is for an imported Teams series.', 'code': 'not_imported'}, status=409)
    return JsonResponse(imported_participant_report(rows[0], calendar_reader, load_saved_organizer_evidence))
