"""Read Microsoft calendar cancellation evidence without changing Microsoft."""
from datetime import timedelta
import re
from urllib.parse import quote, unquote, urlencode, urlparse

from .teams_calendar_checks import event_instant, utc_datetime


class CalendarStateError(RuntimeError):
    pass


FIELDS = 'id,type,isCancelled,iCalUId,occurrenceId,seriesMasterId,originalStart,start,end,onlineMeeting,recurrence,cancelledOccurrences'


def _resource(path):
    # Graph may change /calendars/id to /calendars('id') in nextLink.
    # Compare the same owner and resource after normalizing only key syntax.
    return re.sub(r"/(users|calendars|events)\('((?:[^']|'')*)'\)",
                  lambda match: '/' + match[1] + '/' + match[2].replace("''", "'"), unquote(path))


def _list(read, path):
    """An incomplete/failed page must never become evidence of cancellation."""
    rows, visited = [], set()
    resource = urlparse(path).path
    while path:
        if path in visited or len(visited) >= 30:
            raise CalendarStateError('Microsoft returned an incomplete calendar. Retry the status check.')
        visited.add(path)
        data = read(path)
        if not isinstance(data, dict) or not isinstance(data.get('value'), list):
            raise CalendarStateError('Microsoft did not return a complete calendar.')
        rows.extend(data['value'])
        path = data.get('@odata.nextLink') or ''
        if path:
            parsed = urlparse(path)
            if parsed.scheme != 'https' or parsed.netloc != 'graph.microsoft.com' or _resource(parsed.path) != _resource('/v1.0/' + resource):
                raise CalendarStateError('Microsoft returned an unexpected calendar page.')
            path = parsed.path[len('/v1.0/'):] + ('?' + parsed.query if parsed.query else '')
    return rows


def _join(event):
    return (event.get('onlineMeeting') or {}).get('joinUrl') or ''


def cancellation_plan(series, occurrences, previous, read):
    """Return exact local IDs to cancel plus durable occurrence identities.

    `read` returns None only for a confirmed Graph event 404; all other failures
    raise. A missing root additionally requires a complete scan of the owner's
    calendars, so moving an event to a different calendar cannot cancel it here.
    Single cancellations require Microsoft's cancelled occurrence ID, never an
    absent date or the position of a neighbouring session.
    """
    owner = quote(str(series.get('organizer_email') or ''), safe='')
    if not owner or not series.get('graph_event_id'):
        raise CalendarStateError('The saved calendar identity is incomplete.')
    prefix = f'users/{owner}'
    cache, snapshot, cancelled, errors = {}, {'roots': {}, 'occurrences': {}}, set(), []
    # Planned sessions Microsoft could not be matched to this run. Reported to
    # Calendar health as "verification failed"; never read as a cancellation.
    unmatched = []
    previous = previous or {}
    if previous.get('management'):
        snapshot['management'] = previous['management']
    if previous.get('importedExtension'):
        snapshot['importedExtension'] = previous['importedExtension']

    def get(event_id, expand=False):
        key = (event_id, expand)
        if key not in cache:
            query = {'$select': FIELDS}
            if expand:
                query['$select'] += ',exceptionOccurrences'
                query['$expand'] = 'exceptionOccurrences'
            cache[key] = read(f'{prefix}/events/{quote(event_id, safe="")}?{urlencode(query)}')
        return cache[key]

    # Assign the actual master for each day; standalone replacement events are
    # separate roots. The series row's first link is not every weekday's link.
    manifest = series.get('calendar_series') or []
    groups = {}
    for row in occurrences:
        day = next((entry for entry in manifest if row['session_number'] in entry.get('sessionNumbers', [])), None)
        root_id = (day or {}).get('eventId') or series['graph_event_id']
        root_link = (day or {}).get('joinUrl') or series.get('join_url') or ''
        own_id = row.get('graph_event_id') or root_id
        if own_id != root_id:
            own = get(own_id)
            if own and own.get('type') in ('occurrence', 'exception'):
                if own.get('seriesMasterId') != root_id:
                    raise CalendarStateError('A session belongs to a different Microsoft calendar.')
            elif own and own.get('type') == 'singleInstance':
                root_id, root_link = own_id, row.get('join_url') or root_link
            elif own is None:
                saved = previous.get('occurrences', {}).get(row['id'], {})
                if saved.get('eventId') != own_id or saved.get('rootId') != root_id:
                    root_id, root_link = own_id, row.get('join_url') or root_link
        group = groups.setdefault(root_id, {'link': root_link, 'rows': []})
        group['rows'].append(row)

    all_calendar_events = None

    def confirmed_missing(root_id, link):
        nonlocal all_calendar_events
        if all_calendar_events is None:
            calendars = _list(read, prefix + '/calendars?' + urlencode({'$select': 'id', '$top': 100}))
            if not calendars:
                raise CalendarStateError('The organizer calendars could not be verified.')
            all_calendar_events = []
            for calendar in calendars:
                calendar_id = calendar.get('id')
                if not calendar_id:
                    raise CalendarStateError('Microsoft returned an invalid calendar identity.')
                all_calendar_events.extend(_list(read, f'{prefix}/calendars/{quote(calendar_id, safe="")}/events?' +
                    urlencode({'$select': 'id,iCalUId,isCancelled,onlineMeeting', '$top': 100})))
        uid = previous.get('roots', {}).get(root_id, {}).get('iCalUId')
        if not uid and not link:
            raise CalendarStateError('The missing calendar has no saved identity to verify.')
        for event in all_calendar_events:
            same = event.get('id') == root_id or (uid and event.get('iCalUId') == uid) or (link and _join(event) == link)
            if same and event.get('isCancelled') is not True:
                raise CalendarStateError('This meeting may have moved to another calendar. Its LMS status was preserved.')
        return True

    cancelled_roots = set()
    # Live on Teams, but no scheduled LMS session is held there: weekly filler
    # in a gap, a week handed to its own additional meeting, a session the plan
    # dropped, a weekday the plan stopped using. Never cancelled by this or any
    # automatic path -- only reported, for the explicit Cancel on that slot.
    leftovers = []
    for root_id, group in groups.items():
        event = get(root_id, expand=True)
        if event is None:
            confirmed_missing(root_id, group['link'])
            cancelled_roots.add(root_id)
            cancelled.update(row['id'] for row in group['rows'])
            continue
        if event.get('id') != root_id or (_join(event) and _join(event) != group['link']):
            raise CalendarStateError('The Microsoft meeting no longer matches its saved identity.')
        snapshot['roots'][root_id] = {'iCalUId': event.get('iCalUId') or ''}
        if event.get('isCancelled') is True:
            cancelled_roots.add(root_id)
            cancelled.update(row['id'] for row in group['rows'])
            continue
        if event.get('type') == 'singleInstance':
            available = [event]
        elif event.get('type') == 'seriesMaster':
            starts = [utc_datetime(row['scheduled_start']) for row in group['rows']]
            ends = [utc_datetime(row['scheduled_end']) for row in group['rows']]
            query = {'startDateTime': (min(starts) - timedelta(days=7)).isoformat(),
                     'endDateTime': (max(ends) + timedelta(days=7)).isoformat(), '$select': FIELDS, '$top': 100}
            available = _list(read, f'{prefix}/events/{quote(root_id, safe="")}/instances?{urlencode(query)}')
            # A rescheduled exception can be outside the original date window.
            exceptions = event.get('exceptionOccurrences') or []
            if event.get('exceptionOccurrences@odata.nextLink'):
                raise CalendarStateError('Microsoft returned incomplete session exceptions. Retry the status check.')
            available = list({item['id']: item for item in [*available, *exceptions]}.values())
        else:
            raise CalendarStateError('Microsoft returned an unexpected meeting type.')
        removed = set(event.get('cancelledOccurrences') or [])
        claimed = set()
        held = set()
        errors_before = len(errors)
        for row in group['rows']:
            dates = [utc_datetime(row[key]).isoformat() for key in ('scheduled_start', 'scheduled_end')]
            saved = previous.get('occurrences', {}).get(row['id'], {})
            if saved.get('dates') != dates or saved.get('rootId') != root_id:
                saved = {}
            if saved.get('occurrenceId') and saved['occurrenceId'] in removed:
                cancelled.add(row['id'])
                snapshot['occurrences'][row['id']] = saved
                continue
            identity_ids = {value for value in (saved.get('eventId'), row.get('graph_event_id')) if value and value != root_id}
            matches = [item for item in available if item.get('id') in identity_ids or
                       (saved.get('occurrenceId') and item.get('occurrenceId') == saved['occurrenceId'])]
            if not matches:
                matches = [item for item in available if event_instant(item, 'start') == utc_datetime(row['scheduled_start'])
                           and event_instant(item, 'end') == utc_datetime(row['scheduled_end'])]
            if len(matches) != 1 or matches[0]['id'] in claimed:
                if row.get('status') not in ('cancelled', 'superseded'):
                    errors.append(f"Session {row['session_number']} could not be matched to Microsoft; its status was preserved.")
                    unmatched.append(row['id'])
                if saved:
                    snapshot['occurrences'][row['id']] = saved
                continue
            match = matches[0]
            if match.get('type') in ('occurrence', 'exception') and match.get('seriesMasterId') != root_id:
                raise CalendarStateError('Microsoft returned a session from another series.')
            if _join(match) and _join(match) != (row.get('join_url') or group['link']):
                raise CalendarStateError('A session has a different Microsoft join link.')
            claimed.add(match['id'])
            if row.get('status') not in ('cancelled', 'superseded'):
                held.add(match['id'])
            snapshot['occurrences'][row['id']] = {'rootId': root_id, 'eventId': match['id'],
                'occurrenceId': match.get('occurrenceId') or '', 'dates': dates}
            if match.get('isCancelled') is True:
                cancelled.add(row['id'])
        # A session that could not be matched may be any of these, so a root
        # with an unmatched session reports nothing rather than guess.
        if len(errors) == errors_before:
            for item in available:
                if item.get('id') and item['id'] not in held and item.get('isCancelled') is not True:
                    leftovers.append({'kind': 'occurrence' if item['id'] != root_id else 'event', 'rootId': root_id,
                                      'eventId': item['id'], 'occurrenceId': item.get('occurrenceId') or '',
                                      'startDateTimeUtc': event_instant(item, 'start').isoformat(),
                                      'endDateTimeUtc': event_instant(item, 'end').isoformat()})
    # A weekday series the plan no longer uses holds no sessions, so no group
    # above reads it. It is still on Teams until somebody cancels it.
    for entry in manifest:
        event_id = entry.get('eventId') or ''
        if entry.get('sessionNumbers') or not event_id or event_id in groups:
            continue
        event = get(event_id)
        if event and event.get('isCancelled') is not True:
            leftovers.append({'kind': 'series', 'rootId': event_id, 'eventId': event_id, 'occurrenceId': '',
                              'day': entry.get('day') or '', 'startDateTimeUtc': '', 'endDateTimeUtc': ''})
    snapshot['leftovers'] = leftovers
    snapshot['integrity'] = {'unmatched': unmatched}
    # Preserve completed attendance and all historical evidence. This operation
    # updates calendar availability, never recategorizes a session that ran.
    eligible = {row['id'] for row in occurrences if row.get('status') == 'scheduled'
                and not row.get('actual_start') and not row.get('attendance_report_id')
                and not row.get('participant_count')}
    return {'cancelledIds': sorted(cancelled & eligible), 'seriesCancelled': bool(groups) and len(cancelled_roots) == len(groups),
            'snapshot': snapshot, 'errors': errors, 'cancelledRootIds': sorted(cancelled_roots),
            'leftovers': leftovers, 'unmatched': unmatched}
