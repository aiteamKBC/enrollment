"""Review, extend and verify an existing imported series; never create a meeting.

The only Microsoft mutation is an If-Match PATCH containing recurrence. A durable
attempt is committed before that request. Recovery reads Microsoft and never
replays an attempted PATCH. Existing occurrence/history rows are never replaced.
"""
import base64
import copy
import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import httpx
from django.core import signing
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from login.permissions import require_role

from .teams_calendar_checks import event_instant, utc_datetime
from .teams_calendar_state import load_calendar_state
from .teams_calendar_actions import load_module, store_snapshot
from .teams_imported_participants import calendar_collection, join_context, join_context_oid, text
from .teams_imported_calendar_auth import CalendarConnectionError

SALT = 'curriculum.imported-series-extension.v1'
KEY = 'importedExtension'
MASTER_FIELDS = ('id,iCalUId,subject,type,isCancelled,isOrganizer,organizer,attendees,'
                 'onlineMeeting,recurrence,start,end,body,hideAttendees,responseRequested,'
                 'allowNewTimeProposals,isOnlineMeeting,onlineMeetingProvider,location,locations,'
                 'sensitivity,cancelledOccurrences')
INSTANCE_FIELDS = 'id,type,start,end,isCancelled,onlineMeeting,seriesMasterId,originalStart'
ZONES = {'GMT Standard Time': 'Europe/London', 'Egypt Standard Time': 'Africa/Cairo', 'UTC': 'UTC'}
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


class Blocked(RuntimeError):
    def __init__(self, code, message, *, connection_required=False, kind='user'):
        super().__init__(message)
        self.code, self.connection_required, self.kind = code, connection_required, kind


class GraphRefusal(RuntimeError):
    def __init__(self, status):
        self.status_code = status
        super().__init__('Microsoft did not allow this calendar operation.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def iso(value):
    return utc_datetime(value).isoformat().replace('+00:00', 'Z')


def local_clock(day, clock, zone):
    result = datetime.combine(day, clock, zone)
    if result.replace(fold=0).utcoffset() != result.replace(fold=1).utcoffset():
        raise Blocked('ambiguous_local_clock', 'A planned time falls in a daylight-saving clock change. Review that date in the original calendar.')
    return result


def claims(token):
    try:
        segment = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))
    except (ValueError, TypeError, IndexError):
        return {}


def access_for(request, live_id):
    from coach_api.views import microsoft_graph_token
    from .teams_imported_calendar_auth import delegated_calendar_access

    delegated = delegated_calendar_access(request, live_id)
    if delegated:
        # Auth verified the ID-token signature and bound its tenant to this
        # series. Graph access tokens themselves can be opaque to clients.
        return {**delegated, 'mode': 'delegated', 'tenantId': text(delegated.get('tenantId'))}
    token = microsoft_graph_token()
    details = claims(token)
    return {'accessToken': token, 'actorId': text(details.get('appid') or details.get('azp')),
            'scopes': details.get('roles') or [], 'mode': 'application', 'calendarKind': 'user', 'tenantId': text(details.get('tid'))}


class CalendarClient:
    def __init__(self, access):
        self.access = access
        self.client = httpx.Client(base_url='https://graph.microsoft.com/v1.0/',
                                  headers={'Authorization': 'Bearer ' + access['accessToken']},
                                  follow_redirects=False, timeout=20)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def read(self, path):
        response = self.client.get(path)
        if response.status_code != 200:
            raise GraphRefusal(response.status_code)
        data = response.json()
        if not isinstance(data, dict):
            raise Blocked('invalid_microsoft_response', 'Microsoft returned an unreadable calendar. No change was sent.')
        return data

    def extend(self, operation):
        # No event POST, no attendee/body/options PATCH, no alternate link.
        response = self.client.patch(operation['path'], json={'recurrence': operation['afterRecurrence']},
                                     headers={'If-Match': operation['etag']})
        if response.status_code not in (200, 204):
            raise GraphRefusal(response.status_code)


def require_access(access, series=None):
    kind = access.get('calendarKind') or 'user'
    permission = 'Group.ReadWrite.All' if kind == 'group' else 'Calendars.ReadWrite'
    if permission not in (access.get('scopes') or []):
        raise Blocked('calendar_write_permission_required', 'Connect a Microsoft account with permission to update the original calendar.',
                      connection_required=True, kind=kind)
    if series:
        tenant = join_context(series.get('join_url')).get('Tid')
        if not tenant or tenant.casefold() != text(access.get('tenantId')).casefold():
            raise Blocked('calendar_tenant_mismatch', 'Connect an account in the Microsoft tenant that owns the original meeting link.',
                          connection_required=True, kind=kind)


def original_master(series, client):
    """An attendee event is never promoted to the original organizer event."""
    subject, join_url = text(series.get('module_title')), text(series.get('join_url'))
    if not subject or not join_url:
        raise Blocked('missing_series_identity', 'The imported meeting has no complete saved title and join link.')
    kind = client.access.get('calendarKind') or 'user'
    prefixes, inaccessible, attendee_copy = [], False, False
    if kind == 'group':
        mailbox = text(series.get('organizer_email'))
        query = urlencode({'$filter': "mail eq '" + mailbox.replace("'", "''") + "'", '$select': 'id,mail,displayName', '$top': 100})
        try:
            groups = calendar_collection(client.read, 'groups?' + query)
        except GraphRefusal as exc:
            raise Blocked('group_calendar_access_denied', 'Microsoft did not allow access to the original group calendar. Connect an account allowed to manage that group.',
                          connection_required=True, kind='group') from exc
        exact = [group for group in groups if text(group.get('mail')).casefold() == mailbox.casefold() and text(group.get('id'))]
        if len(exact) != 1:
            raise Blocked('group_calendar_not_identified', 'The original group calendar could not be identified uniquely from the saved calendar address.',
                          connection_required=True, kind='group')
        prefixes = ['groups/' + quote(exact[0]['id'], safe='')]
    else:
        mailboxes = list(dict.fromkeys(value for value in (text(series.get('organizer_email')), join_context_oid(join_url)) if value))
        prefixes = ['users/' + quote(value, safe='') for value in mailboxes]
    query = urlencode({'$filter': "subject eq '" + subject.replace("'", "''") + "'",
                       '$select': 'id,iCalUId,type,subject,isCancelled,isOrganizer,onlineMeeting', '$top': 100})
    found = {}
    for prefix in prefixes:
        try:
            events = calendar_collection(client.read, prefix + '/events?' + query)
        except GraphRefusal:
            inaccessible = True
            continue
        for event in events:
            meeting = event.get('onlineMeeting') if isinstance(event.get('onlineMeeting'), dict) else {}
            if (text(event.get('subject')) != subject or text(meeting.get('joinUrl')) != join_url
                    or event.get('type') != 'seriesMaster' or event.get('isCancelled') or not text(event.get('id'))):
                continue
            if event.get('isOrganizer') is not True:
                attendee_copy = True
                continue
            key = (text(event.get('iCalUId')), text(event['id']))
            found[key] = (prefix + '/events/' + quote(event['id'], safe=''), event)
    if len(found) != 1:
        if len(found) > 1:
            raise Blocked('ambiguous_original_series', 'More than one original series matches this link. An administrator must identify the correct series.')
        requested_kind = 'group' if kind == 'group' or attendee_copy else 'user'
        raise Blocked('attendee_copy_only' if attendee_copy else 'organizer_calendar_unavailable',
                      'Only an attendee calendar copy is accessible. Connect access to the original calendar; attendee copies cannot send updates to invitees.' if attendee_copy else
                      'The original organiser calendar is not accessible. Connect an account permitted to manage that calendar.',
                      connection_required=inaccessible or attendee_copy or kind == 'group', kind=requested_kind)
    path, _event = next(iter(found.values()))
    master = client.read(path + '?' + urlencode({'$select': MASTER_FIELDS}))
    verify_master(master, join_url)
    if not text(master.get('@odata.etag')):
        raise Blocked('missing_concurrency_identity', 'Microsoft did not return the original calendar version required for a safe update.')
    return path, master


def verify_master(master, join_url):
    if (master.get('isOrganizer') is not True or master.get('isCancelled') or master.get('type') != 'seriesMaster'
            or not isinstance(master.get('onlineMeeting'), dict) or master['onlineMeeting'].get('joinUrl') != join_url):
        raise Blocked('original_series_changed', 'The original organiser series or its join link could not be verified. No replacement meeting will be created.')


def load_local(live_id, *, lock=False):
    from . import views as v

    series, rows, snapshot = load_calendar_state(live_id, lock=lock)
    if text(series.get('meeting_type')) != 'linked-external' or series.get('status') != 'active':
        raise Blocked('not_active_imported_series', 'This operation requires an active imported Teams series.')
    module = load_module(series, lock=lock)
    if lock:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM curriculum.weeks WHERE module_catalogue_id=%s FOR UPDATE', [module['module_catalogue_id']])
            cursor.execute("SELECT id FROM curriculum.components WHERE module_catalogue_id=%s AND type IN ('live-session','live_session') FOR UPDATE", [module['module_catalogue_id']])
    weeks = v.active_week_rows(v.authoring_fetch_all(v.AUTHORING_WEEKS_TABLE, 'module_catalogue_id=%s',
        [module['module_catalogue_id']], 'display_order, week_number, id', ensure_tables=False))
    components = v.active_component_rows(v.authoring_fetch_all(v.AUTHORING_COMPONENTS_TABLE, "module_catalogue_id=%s AND type IN ('live-session','live_session')",
        [module['module_catalogue_id']], 'display_order, id', ensure_tables=False))
    groups = v.authoring_fetch_all(v.GROUPS_TABLE, 'group_id=%s', [module.get('group_id')], ensure_tables=False) if module.get('group_id') else []
    group = groups[0] if groups else {}
    slots, _plan = v.module_week_delivery_slots(module, group, len(weeks))
    zone_name = v.GRAPH_WINDOWS_TO_IANA.get(text(series.get('timezone')), text(series.get('timezone')) or 'Europe/London')
    zone = ZoneInfo(zone_name)
    targets = []
    for week_index, week in enumerate(weeks):
        live = [component for component in components if component.get('week_id') == week['id']
                and v.frontend_component_type(component.get('type')) == 'live-session']
        for index, component in enumerate(live):
            settings = v.component_builder_settings(component)
            if text(settings.get('extraTeamsMeetingUrl')) or v.live_session_meeting_scope(settings, True) != 'main':
                continue
            link = (text(settings.get('liveSessionLinkOverride')) or text(settings.get('liveSessionUrl'))
                    or text(settings.get('teamsMeetingUrl')) or text(component.get('live_sessions_link')) or text(series.get('join_url')))
            if link != text(series.get('join_url')):
                raise Blocked('different_main_links', 'A main-calendar session uses a different join link. Resolve its calendar choice before updating this series.')
            if week_index >= len(slots) or index >= len(slots[week_index]):
                raise Blocked('missing_main_session_date', 'A main-calendar session has no dated delivery slot. Save its schedule before reviewing Teams.')
            planned = slots[week_index][index]
            clock, _end, duration = v.module_session_clock(module, group, planned.get('date'))
            start = datetime.fromisoformat(text(planned.get('date'))[:10] + 'T' + (text(planned.get('startTime')) or clock))
            start = local_clock(start.date(), start.time(), zone)
            end = start.astimezone(timezone.utc) + timedelta(minutes=int(planned.get('durationMinutes') or duration))
            targets.append({'componentId': text(component['id']), 'start': iso(start), 'end': iso(end)})
    targets.sort(key=lambda item: item['start'])
    if not targets or len(targets) > 52 or len({target['start'] for target in targets}) != len(targets):
        raise Blocked('invalid_main_session_plan', 'Review between one and 52 distinct dated main-calendar sessions.')
    version = digest({'series': {key: series.get(key) for key in ('id', 'status', 'meeting_type', 'join_url', 'online_meeting_id')},
                      'moduleId': module['module_catalogue_id'], 'targets': targets,
                      'rows': [{key: row.get(key) for key in ('id', 'session_number', 'status', 'scheduled_start', 'scheduled_end', 'join_url', 'online_meeting_id')} for row in rows]})
    return series, rows, snapshot, module, targets, version, zone_name


def weekly_dates(recurrence, through):
    pattern, span = recurrence.get('pattern') or {}, recurrence.get('range') or {}
    if pattern.get('type') != 'weekly' or not isinstance(pattern.get('daysOfWeek'), list):
        raise Blocked('unsupported_recurrence', 'This update currently supports extending an existing weekly series. Its recurrence pattern will not be changed.')
    interval = pattern.get('interval')
    names = pattern.get('daysOfWeek')
    if not isinstance(interval, int) or not 1 <= interval <= 52 or not names or any(name not in DAYS for name in names):
        raise Blocked('unsupported_recurrence', 'Microsoft returned an unsupported weekly recurrence.')
    first = date.fromisoformat(span['startDate'])
    if through < first or (through - first).days > 730:
        raise Blocked('unsupported_recurrence_span', 'The original series exceeds the supported two-year review window.')
    first_weekday = pattern.get('firstDayOfWeek') or 'monday'
    if first_weekday not in DAYS:
        raise Blocked('unsupported_recurrence', 'Microsoft returned an unsupported recurrence week boundary.')
    anchor = first - timedelta(days=(first.weekday() - DAYS.index(first_weekday)) % 7)
    return [first + timedelta(days=offset) for offset in range((through - first).days + 1)
            if DAYS[(first + timedelta(days=offset)).weekday()] in names
            and ((first + timedelta(days=offset) - anchor).days // 7) % interval == 0]


def recurrence_end(recurrence, last_planned):
    span = recurrence.get('range') or {}
    first = date.fromisoformat(span['startDate'])
    if span.get('type') == 'endDate':
        end = date.fromisoformat(span['endDate'])
        dates = weekly_dates(recurrence, end)
        return dates[-1] if dates else first
    if span.get('type') == 'numbered':
        count = span.get('numberOfOccurrences')
        if not isinstance(count, int) or not 1 <= count <= 104:
            raise Blocked('unsupported_recurrence_span', 'This series has too many original occurrences for this extension review.')
        dates = weekly_dates(recurrence, first + timedelta(days=730))
        if len(dates) < count:
            raise Blocked('unsupported_recurrence_span', 'The original series exceeds the supported review window.')
        return dates[count - 1]
    if span.get('type') == 'noEnd':
        return last_planned
    raise Blocked('unsupported_recurrence', 'The series has no supported recurrence range.')


def instances(client, path, master, through):
    first = date.fromisoformat(master['recurrence']['range']['startDate'])
    if (through - first).days > 730:
        raise Blocked('unsupported_recurrence_span', 'The original series exceeds the supported review window.')
    query = urlencode({'startDateTime': (first - timedelta(days=1)).isoformat() + 'T00:00:00Z',
                       'endDateTime': (through + timedelta(days=2)).isoformat() + 'T00:00:00Z',
                       '$select': INSTANCE_FIELDS, '$top': 100})
    result = calendar_collection(client.read, path + '/instances?' + query)
    if len(result) > 104:
        raise Blocked('too_many_calendar_instances', 'The original calendar has too many instances for this extension review.')
    if len({text(event.get('id')) for event in result}) != len(result):
        raise Blocked('duplicate_occurrence_identity', 'Microsoft returned duplicate occurrence identities. No calendar change can be reviewed.')
    expected_join = master['onlineMeeting']['joinUrl']
    for event in result:
        if text(event.get('seriesMasterId')) != text(master.get('id')) or not text(event.get('id')):
            raise Blocked('invalid_occurrence_identity', 'Microsoft returned an occurrence outside the verified original series.')
        if not event.get('isCancelled') and (event.get('onlineMeeting') or {}).get('joinUrl') != expected_join:
            raise Blocked('different_occurrence_link', 'An original calendar occurrence uses a different join link. Review it before extending the series.')
    return result


def event_slot(event):
    return (iso(event_instant(event, 'start')), iso(event_instant(event, 'end')))


def instance_snapshot(events):
    return [{'id': text(event['id']), 'start': event_slot(event)[0], 'end': event_slot(event)[1],
             'cancelled': bool(event.get('isCancelled')), 'originalStart': text(event.get('originalStart'))}
            for event in events]


def protected_master(master):
    protected = {key: master.get(key) for key in ('id', 'iCalUId', 'subject', 'organizer', 'onlineMeeting',
        'start', 'end', 'body', 'hideAttendees', 'responseRequested', 'allowNewTimeProposals', 'isOnlineMeeting',
        'onlineMeetingProvider', 'location', 'locations', 'sensitivity', 'cancelledOccurrences')}
    # Microsoft may reset replies while sending a legitimate series update.
    # Preserve who is invited and each invitation type, not changing RSVP times.
    protected['attendees'] = sorted([(text((item.get('emailAddress') or {}).get('address')).casefold(), text(item.get('type')))
                                     for item in master.get('attendees') or []])
    return digest(protected)


def extension_plan(master, targets, events, zone_name):
    recurrence = master.get('recurrence')
    if not isinstance(recurrence, dict):
        raise Blocked('not_recurring', 'The original meeting is not a recurring series.')
    zone = ZoneInfo(zone_name)
    requested_last = max(utc_datetime(target['start']).astimezone(zone).date() for target in targets)
    last = recurrence_end(recurrence, requested_last)
    held = [event_slot(event) for event in events if not event.get('isCancelled')]
    missing = [target for target in targets if held.count((target['start'], target['end'])) != 1]
    for target in missing:
        if utc_datetime(target['start']).astimezone(zone).date() <= last:
            raise Blocked('missing_inside_original_range', 'A planned date is missing or differs inside the original series. Extending its end cannot safely restore a cancelled or moved occurrence.')
    after = copy.deepcopy(recurrence)
    if missing:
        all_dates = weekly_dates(recurrence, requested_last)
        generated = [day for day in all_dates if day > last]
        master_start, master_end = event_instant(master, 'start'), event_instant(master, 'end')
        clock = master_start.astimezone(zone).timetz().replace(tzinfo=None)
        duration = master_end - master_start
        expected = [(iso(local_clock(day, clock, zone)), iso(local_clock(day, clock, zone).astimezone(timezone.utc) + duration)) for day in generated]
        wanted = [(target['start'], target['end']) for target in missing]
        if expected != wanted:
            raise Blocked('extension_would_add_unplanned_dates', 'Extending the original pattern would add different times or extra dates. Review the main-calendar plan; additional meetings are not added to this series.')
        if after['range']['type'] == 'numbered':
            after['range']['numberOfOccurrences'] = len(all_dates)
        elif after['range']['type'] == 'endDate':
            after['range']['endDate'] = requested_last.isoformat()
        else:
            raise Blocked('unbounded_series_missing_date', 'The series already has no end date. A missing occurrence needs review in the original calendar.')
    return after, missing, last, requested_last


def local_missing(rows, targets, join_url):
    missing = []
    for target in targets:
        matches = [row for row in rows if iso(row['scheduled_start']) == target['start'] and iso(row['scheduled_end']) == target['end']]
        if len(matches) > 1 or any(text(row.get('join_url')) != join_url or text(row.get('status')).lower() in
                                {'cancelled', 'canceled', 'superseded', 'deleted', 'removed', 'declined', 'failed'} for row in matches):
            raise Blocked('stored_occurrence_conflict', 'Saved occurrence identities conflict with a planned date. They will not be replaced or resurrected.')
        if not matches:
            missing.append(target)
    return missing


def pending_result(operation):
    return {'status': 'verification_pending', 'operationId': operation.get('id'),
            'code': operation.get('code'), 'connectionRequired': bool(operation.get('connectionRequired')),
            'connectionKind': operation.get('connectionKind') or operation.get('calendarKind') or 'user',
            'message': operation.get('message') or 'The previous update needs a status check. Checking will not send the calendar update again.'}


def review(live_id, actor, access):
    series, rows, snapshot, module, targets, version, zone_name = load_local(live_id)
    previous = snapshot.get(KEY) or {}
    if previous.get('status') in {'processing', 'uncertain'}:
        return pending_result(previous)
    if (snapshot.get('management') or {}).get('status') in {'processing', 'uncertain'}:
        raise Blocked('other_calendar_action_pending', 'Another calendar action needs verification before extending this series.')
    require_access(access, series)
    with CalendarClient(access) as client:
        path, master = original_master(series, client)
        zone_name = ZONES.get(text((master.get('recurrence') or {}).get('range', {}).get('recurrenceTimeZone')),
                              text((master.get('recurrence') or {}).get('range', {}).get('recurrenceTimeZone')) or zone_name)
        zone = ZoneInfo(zone_name)
        last_requested = max(utc_datetime(target['start']).astimezone(zone).date() for target in targets)
        last = recurrence_end(master.get('recurrence') or {}, last_requested)
        through = max(last, last_requested)
        events = instances(client, path, master, through)
        after, added, last, requested = extension_plan(master, targets, events, zone_name)
    missing = local_missing(rows, targets, text(series.get('join_url')))
    attendee_list = master.get('attendees')
    if not isinstance(attendee_list, list):
        raise Blocked('invitation_list_unavailable', 'Microsoft did not return the original invitation list, so the recipients of this update cannot be reviewed.')
    owner = (master.get('organizer') or {}).get('emailAddress') or {}
    display = {'currentEndDate': last.isoformat(), 'proposedEndDate': max(last, requested).isoformat(), 'timeZone': zone_name,
               'addedSessions': [{'start': target['start'], 'end': target['end']} for target in (added or missing)],
               'existingInviteeCount': len(attendee_list), 'organizer': {'name': text(owner.get('name')), 'email': text(owner.get('address'))},
               'joinUrl': series['join_url'], 'calendarChangeRequired': bool(added)}
    if not added and not missing:
        return {**display, 'status': 'up_to_date', 'message': 'The original Teams series and saved LMS sessions already cover the main-calendar plan.'}
    operation = {'id': uuid.uuid4().hex, 'liveId': live_id, 'actor': actor, 'version': version,
                 'accessMode': access['mode'], 'accessActor': text(access.get('actorId')), 'calendarKind': access['calendarKind'], 'tenantId': access['tenantId'],
                 'path': path, 'etag': master['@odata.etag'], 'joinUrl': series['join_url'],
                 'beforeRecurrence': master['recurrence'], 'afterRecurrence': after,
                 'protected': protected_master(master), 'beforeInstances': instance_snapshot(events),
                 'targets': targets, 'through': through.isoformat(), 'zoneName': zone_name,
                 'calendarChangeRequired': bool(added), 'addedCount': len(added), 'status': 'reviewed'}
    return {**display, 'status': 'ready', 'previewToken': signing.dumps(operation, salt=SALT, compress=True),
            'message': 'Review extending the original Teams series. Its existing join link and invitation list are preserved.' if added else
                       'These dates already exist in the original Teams series. Confirm to import the missing dates into the LMS without sending calendar updates.',
            'notificationScope': 'Microsoft will send a series update to the existing calendar invitees. Edited exceptions can produce additional update messages; channel membership and previous attendance are not additional recipients.' if added else
                                 'No Microsoft calendar change or notification is needed; this only imports verified existing dates.'}


def operation_result(operation):
    if operation.get('status') == 'done':
        return {'status': 'confirmed', 'operationId': operation['id'], 'addedSessions': operation.get('addedCount', 0),
                'importedSessions': operation.get('importedCount', 0), 'message': operation.get('message') or 'The original Teams series is verified and its new dates are linked in the LMS.'}
    if operation.get('status') == 'failed':
        return {'status': 'blocked', 'operationId': operation['id'], 'code': operation.get('code'), 'message': operation['message'],
                'connectionRequired': bool(operation.get('connectionRequired')),
                'connectionKind': operation.get('connectionKind') or operation.get('calendarKind') or 'user'}
    return pending_result(operation)


def same_access(operation, access):
    if (operation.get('accessMode') != access.get('mode') or operation.get('accessActor') != text(access.get('actorId'))
            or operation.get('calendarKind') != access.get('calendarKind') or operation.get('tenantId') != access.get('tenantId')):
        raise Blocked('calendar_connection_changed', 'Reconnect the same Microsoft account used for this review, then check status.',
                      connection_required=True, kind=operation.get('calendarKind') or 'user')


def confirm(live_id, payload, actor, access):
    operation = signing.loads(payload.get('previewToken') or '', salt=SALT, max_age=600)
    if operation.get('liveId') != live_id or operation.get('actor') != actor:
        raise Blocked('wrong_review_owner', 'This calendar review belongs to another account or series.')
    same_access(operation, access)
    require_access(access)
    if operation['calendarChangeRequired'] and payload.get('acknowledgeNotifications') is not True:
        raise Blocked('notification_confirmation_required', 'Confirm that Microsoft will send the existing invitees the series update.')
    with transaction.atomic():
        series, rows, snapshot, module, targets, version, zone = load_local(live_id, lock=True)
        require_access(access, series)
        previous = snapshot.get(KEY) or {}
        if previous.get('id') == operation['id']:
            return operation_result(previous)
        if previous.get('status') in {'processing', 'uncertain'} or (snapshot.get('management') or {}).get('status') in {'processing', 'uncertain'}:
            raise Blocked('calendar_update_pending', 'Check the previous calendar update before sending another request.')
        if version != operation['version']:
            raise Blocked('plan_changed', 'The module plan changed after review. Review its latest dates before confirming.')
        operation.update(status='processing', leaseUntil=(datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(), attempted=False)
        snapshot[KEY] = operation
        store_snapshot(live_id, snapshot)
    return execute(live_id, operation['id'], actor, access, send=True)


def set_state(live_id, operation_id, **updates):
    with transaction.atomic():
        _series, _rows, snapshot = load_calendar_state(live_id, lock=True)
        operation = snapshot.get(KEY) or {}
        if operation.get('id') != operation_id:
            raise Blocked('operation_changed', 'This operation was superseded. Review the latest calendar state.')
        if operation.get('status') in {'done', 'failed'}:
            return operation
        operation.update(updates)
        snapshot[KEY] = operation
        store_snapshot(live_id, snapshot)
    return operation


def finish_unattempted(live_id, operation_id):
    """Do not declare an attempt absent from an unlocked, stale status read."""
    with transaction.atomic():
        _series, _rows, snapshot = load_calendar_state(live_id, lock=True)
        operation = snapshot.get(KEY) or {}
        if operation.get('id') != operation_id:
            raise Blocked('operation_changed', 'The calendar operation changed. Check the latest status.')
        if (operation.get('status') == 'processing' and not operation.get('attempted')
                and utc_datetime(operation['leaseUntil']) <= datetime.now(timezone.utc)):
            operation.update(status='failed', code='not_sent',
                             message='The previous attempt ended before sending a Microsoft update. Open a new review.')
            snapshot[KEY] = operation
            store_snapshot(live_id, snapshot)
        return operation_result(operation)


def verify_readback(client, operation):
    master = client.read(operation['path'] + '?' + urlencode({'$select': MASTER_FIELDS}))
    verify_master(master, operation['joinUrl'])
    if master.get('recurrence') != operation['afterRecurrence'] or protected_master(master) != operation['protected']:
        raise Blocked('calendar_verification_pending', 'The series range or protected meeting details do not match the reviewed change. No request will be resent automatically.')
    events = instances(client, operation['path'], master, date.fromisoformat(operation['through']))
    after = {item['id']: item for item in instance_snapshot(events)}
    if any(after.get(item['id']) != item for item in operation['beforeInstances']):
        raise Blocked('original_occurrences_changed', 'An existing Teams occurrence changed during verification. Attendance and recording rows were preserved; an administrator must review the calendar.')
    original_ids = {item['id'] for item in operation['beforeInstances']}
    original_slots = {(item['start'], item['end']) for item in operation['beforeInstances'] if not item['cancelled']}
    expected_new = {(target['start'], target['end']) for target in operation['targets']} - original_slots
    actual_new = [item for item in after.values() if item['id'] not in original_ids]
    if (len(actual_new) != len(expected_new) or any(item['cancelled'] for item in actual_new)
            or {(item['start'], item['end']) for item in actual_new} != expected_new):
        raise Blocked('unexpected_new_occurrences', 'Microsoft returned unexpected new dates. No additional request will be sent; review the original calendar.')
    live = [event for event in events if not event.get('isCancelled')]
    for target in operation['targets']:
        if sum(event_slot(event) == (target['start'], target['end']) for event in live) != 1:
            raise Blocked('calendar_verification_pending', 'Microsoft has not confirmed every planned date yet. Check status again; this will not resend the update.')
    return live


def persist_verified(live_id, operation_id, events):
    from . import views as v

    with transaction.atomic():
        series, rows, snapshot, module, targets, version, zone = load_local(live_id, lock=True)
        operation = snapshot.get(KEY) or {}
        if operation.get('id') != operation_id:
            raise Blocked('operation_changed', 'The calendar operation changed during verification.')
        if operation.get('status') in {'done', 'failed'}:
            return operation_result(operation)
        if version != operation['version']:
            raise Blocked('plan_changed_after_update', 'Microsoft was checked, but the LMS plan changed meanwhile. No attendance or existing occurrences were overwritten; review is required.')
        missing = local_missing(rows, targets, operation['joinUrl'])
        next_number = max((int(row.get('session_number') or 0) for row in rows), default=0)
        for target in missing:
            matching = [event for event in events if event_slot(event) == (target['start'], target['end'])]
            if len(matching) != 1:
                raise Blocked('calendar_verification_pending', 'The new occurrence could not be uniquely verified.')
            next_number += 1
            occurrence_id = 'OCC-' + uuid.uuid5(uuid.NAMESPACE_URL, live_id + '|' + target['start'] + '|' + target['end']).hex.upper()
            with connection.cursor() as cursor:
                cursor.execute('''INSERT INTO curriculum.live_session_occurrences
                    (id,live_session_id,session_number,graph_event_id,scheduled_start,scheduled_end,join_url,online_meeting_id,status,created_at,updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'scheduled',%s,%s)''',
                    [occurrence_id, live_id, next_number, matching[0]['id'], utc_datetime(target['start']).replace(tzinfo=None),
                     utc_datetime(target['end']).replace(tzinfo=None), operation['joinUrl'], series.get('online_meeting_id') or '',
                     datetime.now(timezone.utc), datetime.now(timezone.utc)])
        all_rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id=%s', [live_id], 'session_number asc', ensure_tables=False)
        from .teams_external_calendar import external_calendar_settings
        claimed = set()
        for target in targets:
            component_rows = v.authoring_fetch_all(v.AUTHORING_COMPONENTS_TABLE,
                'id=%s AND module_catalogue_id=%s', [target['componentId'], module['module_catalogue_id']], ensure_tables=False)
            if len(component_rows) != 1:
                raise Blocked('main_component_changed', 'A reviewed main session changed before its verified date could be attached.')
            component = component_rows[0]
            settings = v.component_builder_settings(component)
            local = utc_datetime(target['start']).astimezone(ZoneInfo(operation['zoneName']))
            attached = external_calendar_settings(settings, series, {'date': local.date().isoformat(), 'startTime': local.strftime('%H:%M')},
                                                  all_rows, claimed, operation['zoneName'])
            if not attached or attached.get('teamsCalendarBinding') != 'matched':
                raise Blocked('main_component_unmatched', 'The verified Teams date could not be attached uniquely. Existing records were preserved.')
            merged = {**settings, **attached, 'teamsMeetingScope': 'main'}
            if merged != settings or text(component.get('live_sessions_link')) != operation['joinUrl']:
                # Avoid authoring helpers that provision schema during a save.
                with connection.cursor() as cursor:
                    cursor.execute('''UPDATE curriculum.components SET settings_json=%s,live_sessions_link=%s,updated_at=%s
                        WHERE id=%s AND module_catalogue_id=%s RETURNING *''',
                        [v.json_db_value(merged), operation['joinUrl'], datetime.now(timezone.utc), component['id'], module['module_catalogue_id']])
                    changed = v.rows_as_dicts(cursor)
                v.versioning.record_rows(v.AUTHORING_COMPONENTS_TABLE, changed, before_rows=[component], reason='Verified imported Teams date')
        if missing:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE curriculum.live_sessions SET repeat_occurrences=%s,updated_at=%s WHERE id=%s RETURNING *',
                               [len(all_rows), datetime.now(timezone.utc), live_id])
                changed = v.rows_as_dicts(cursor)
            v.versioning.record_rows(v.LIVE_SESSIONS_TABLE, changed, before_rows=[series], reason='Verified original Teams series extension')
        operation.update(status='done', importedCount=len(missing), message=(
            'The original Teams series was extended and verified; its added dates are now linked in the LMS. Microsoft was asked to update the existing invitees.' if operation['calendarChangeRequired'] else
            'The existing Microsoft dates were verified and imported into the LMS. No calendar update or invitation was sent.'))
        snapshot[KEY] = operation
        store_snapshot(live_id, snapshot)
        v.invalidate_curriculum_cache()
    return operation_result(operation)


def execute(live_id, operation_id, actor, access, *, send=False):
    _series, _rows, snapshot = load_calendar_state(live_id)
    operation = snapshot.get(KEY) or {}
    if operation.get('id') != operation_id or operation.get('actor') != actor:
        raise Blocked('wrong_operation_owner', 'No pending calendar operation belongs to this account and series.')
    same_access(operation, access)
    if operation.get('status') in {'done', 'failed'}:
        return operation_result(operation)
    if not send and operation.get('status') == 'processing' and utc_datetime(operation['leaseUntil']) > datetime.now(timezone.utc):
        return pending_result(operation)
    if not send and operation.get('calendarChangeRequired') and not operation.get('attempted'):
        return finish_unattempted(live_id, operation_id)
    patch_accepted = False
    phase = 'preflight'
    try:
        with CalendarClient(access) as client:
            if send and operation['calendarChangeRequired']:
                master = client.read(operation['path'] + '?' + urlencode({'$select': MASTER_FIELDS}))
                verify_master(master, operation['joinUrl'])
                if master.get('@odata.etag') != operation['etag'] or master.get('recurrence') != operation['beforeRecurrence']:
                    raise Blocked('calendar_changed_after_review', 'The original calendar changed after review. No update was sent; review it again.')
                with transaction.atomic():
                    _s, _r, current, _m, _t, version, _z = load_local(live_id, lock=True)
                    saved = current.get(KEY) or {}
                    if (saved.get('id') != operation_id or saved.get('status') != 'processing'
                            or saved.get('attempted') or version != operation['version']):
                        raise Blocked('plan_changed_before_send', 'The calendar or plan changed before sending. No update was sent.')
                    saved['attempted'] = True
                    current[KEY] = saved
                    store_snapshot(live_id, current)
                operation['attempted'] = True
                phase = 'patch'
                client.extend(operation)
                patch_accepted = True
            phase = 'verification'
            events = verify_readback(client, operation)
        return persist_verified(live_id, operation_id, events)
    except GraphRefusal as exc:
        definitive = send and not patch_accepted and phase in {'preflight', 'patch'} and 400 <= exc.status_code < 500 and exc.status_code not in (408, 429)
        state = set_state(live_id, operation_id, status='failed' if definitive else 'uncertain',
                          code='microsoft_calendar_refused' if definitive else 'calendar_verification_pending',
                          connectionRequired=exc.status_code in (401, 403), connectionKind=operation.get('calendarKind') or 'user',
                          message='Microsoft refused access or the original calendar version changed. No replacement meeting was created; reconnect or review the latest calendar.' if definitive else
                                  'Microsoft has not confirmed the calendar update. Check status; the request will not be resent automatically.')
        return operation_result(state)
    except Blocked as exc:
        failed = send and not operation.get('attempted')
        state = set_state(live_id, operation_id, status='failed' if failed else 'uncertain', code=exc.code, message=str(exc))
        return operation_result(state)
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, AttributeError, DatabaseError):
        not_sent = send and operation.get('calendarChangeRequired') and not operation.get('attempted')
        state = set_state(live_id, operation_id, status='failed' if not_sent else 'uncertain',
                          code='not_sent' if not_sent else 'calendar_verification_pending',
                          message='The request ended before a Microsoft update was sent. Open a fresh review.' if not_sent else
                                  'The update or LMS import is not yet confirmed. Check status; this will only read Microsoft and will not resend the update.')
        return operation_result(state)


@transaction.non_atomic_requests
@never_cache
@require_role('admin', 'staff')
@require_POST
def imported_calendar_update(request, live_session_id, stage):
    try:
        payload = json.loads(request.body or b'{}')
        if not isinstance(payload, dict):
            raise ValueError()
        actor = str(request.login_account.pk)
        if stage == 'status':
            _series, _rows, snapshot = load_calendar_state(live_session_id)
            if not (snapshot.get(KEY) or {}).get('id'):
                return JsonResponse({'status': 'blocked', 'code': 'no_operation', 'message': 'No calendar update was committed. Open a new review.'})
        access = access_for(request, live_session_id)
        if stage == 'preview':
            result = review(live_session_id, actor, access)
        elif stage == 'confirm':
            result = confirm(live_session_id, payload, actor, access)
        else:
            _series, _rows, snapshot = load_calendar_state(live_session_id)
            operation = snapshot.get(KEY) or {}
            wanted = text(payload.get('operationId')) or text(operation.get('id'))
            result = execute(live_session_id, wanted, actor, access, send=False)
        return JsonResponse(result)
    except signing.BadSignature:
        return JsonResponse({'status': 'blocked', 'code': 'review_expired', 'message': 'This review expired or changed. Open a fresh review.'}, status=400)
    except CalendarConnectionError as exc:
        return JsonResponse({'status': 'blocked', 'code': exc.code, 'message': str(exc), 'connectionRequired': True,
                             'connectionKind': 'group'}, status=exc.status if exc.status in (401, 403) else 200)
    except GraphRefusal as exc:
        denied = exc.status_code in (401, 403)
        return JsonResponse({'status': 'blocked', 'code': 'calendar_access_denied' if denied else 'calendar_review_unavailable',
                             'connectionRequired': denied, 'connectionKind': (locals().get('access') or {}).get('calendarKind') or 'user',
                             'message': 'Microsoft did not allow reading the original calendar. Reconnect an account allowed to manage it.' if denied else
                                        'Microsoft could not return the original calendar for review. No calendar update was sent.'})
    except Blocked as exc:
        return JsonResponse({'status': 'blocked', 'code': exc.code, 'message': str(exc), 'addedSessions': [],
                             'connectionRequired': exc.connection_required, 'connectionKind': exc.kind})
    except (LookupError, DatabaseError):
        return JsonResponse({'status': 'blocked', 'code': 'saved_calendar_unavailable', 'message': 'The saved calendar or operation ledger could not be read. No new calendar request was sent.'}, status=409)
    except (RuntimeError, httpx.HTTPError, ValueError, KeyError, TypeError):
        return JsonResponse({'status': 'blocked', 'code': 'calendar_review_unavailable', 'message': 'The original calendar could not be verified. No replacement meeting was created. Check a pending operation before trying again.'}, status=502)
