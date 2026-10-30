"""Reviewed calendar actions with durable attempts and verification before local saves."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from django.core import signing
from django.db import connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from login.permissions import require_role

from .teams_calendar_state import calendar_reader, load_calendar_state
from .teams_calendar_checks import utc_datetime, event_instant
from .teams_cancellation_checks import CalendarStateError, cancellation_plan
from .teams_cancel import cancellation_confirmed, persist_cancellation, send_cancellation
from .session_overrides import schedule_override, session_overrides, validate_exception_plan

SALT = 'curriculum.teams.calendar-action.v1'
SILENT_UPDATE_HEADERS = {'Prefer': 'outlook.send-invitations="none"'}


class ActionNotSent(CalendarStateError):
    """Definitive preflight failure: it is safe to open a fresh review."""


def saved_operation(snapshot, operation_id):
    operation = snapshot.get('management') or {}
    if operation.get('id') != operation_id:
        raise CalendarStateError('This action was superseded. Check the latest calendar state.')
    return operation


def fingerprint(series, rows, module):
    return hashlib.sha256(json.dumps([series, rows, module], sort_keys=True, default=str).encode()).hexdigest()


def load_module(series, lock=False):
    from . import views as v
    module_id = series.get('module_catalogue_id')
    if not module_id:
        raise CalendarStateError('This calendar has no linked curriculum module.')
    with connection.cursor() as cursor:
        if lock:
            cursor.execute('SELECT module_catalogue_id FROM curriculum.modules WHERE module_catalogue_id = %s FOR UPDATE', [module_id])
    rows = v.authoring_fetch_all(v.AUTHORING_MODULES_TABLE, 'module_catalogue_id = %s', [module_id], ensure_tables=False)
    if not rows:
        raise CalendarStateError('The linked curriculum module was not found.')
    return dict(rows[0])


def store_snapshot(live_id, snapshot):
    with connection.cursor() as cursor:
        cursor.execute('''INSERT INTO curriculum.teams_calendar_sync_state (live_session_id, snapshot, checked_at)
            VALUES (%s, %s::jsonb, %s) ON CONFLICT (live_session_id) DO UPDATE
            SET snapshot = EXCLUDED.snapshot, checked_at = EXCLUDED.checked_at''',
            [live_id, json.dumps(snapshot), datetime.now(timezone.utc)])


def eligible(row):
    return (row.get('status') == 'scheduled' and not row.get('actual_start')
            and not row.get('attendance_report_id') and not row.get('participant_count'))


def require_managed_calendar(series):
    """Imported main meetings stay with their original Teams calendar owner."""
    from . import views as v
    if v.module_has_external_teams_calendar(series.get('module_catalogue_id'), series):
        raise CalendarStateError(
            'This module uses an imported Teams meeting. Manage dates and cancellations in the original Teams calendar; '
            'the LMS will preserve its existing link.'
        )


def action_preview(live_id, payload, actor):
    from . import views as v
    from .teams_schedule_notice import schedule_snapshot
    series, rows, saved = load_calendar_state(live_id)
    require_managed_calendar(series)
    module = load_module(series)
    previous = saved.get('management') or {}
    if previous.get('status') in ('processing', 'uncertain'):
        # A browser can lose the response after Microsoft accepted the
        # mutation. Reconcile an identifiable uncertain operation while
        # opening this review; this is read-only and never calls Graph's
        # mutation endpoint again. A live processing lease remains protected
        # so a concurrent writer cannot be disturbed.
        if previous.get('status') == 'uncertain' and previous.get('id') and isinstance(previous.get('commands'), list):
            recovered = continue_action(live_id, previous['id'], send=False)
            if recovered.get('status') not in ('processing', 'uncertain'):
                series, rows, saved = load_calendar_state(live_id)
                previous = saved.get('management') or {}
                if recovered.get('status') == 'done':
                    recovered = with_change_emails(live_id, with_change_notice(live_id, recovered))
            if recovered.get('status') in ('processing', 'uncertain'):
                raise CalendarStateError('A previous action needs a status check before another change can be made.')
        else:
            raise CalendarStateError('A previous action needs a status check before another change can be made.')
    if series.get('status') != 'active':
        raise CalendarStateError('This calendar is no longer active.')
    action, scope = payload.get('action'), payload.get('scope')
    if scope == 'leftover':
        return leftover_preview(live_id, payload, actor, series, rows, saved)
    if action not in ('cancel', 'reschedule') or scope not in ('series', 'occurrence'):
        raise ValueError('Choose a calendar action and its scope.')
    number = payload.get('sessionNumber')
    if scope == 'occurrence' and (not isinstance(number, int) or isinstance(number, bool) or number < 1):
        raise ValueError('Choose a valid session number.')
    affected = rows if scope == 'series' else [row for row in rows if row['session_number'] == number]
    if not affected:
        raise ValueError('Choose a session belonging to this calendar.')
    if scope == 'occurrence' and not eligible(affected[0]):
        raise ValueError('A cancelled session or a session with attendance history cannot be changed.')
    captured = {}
    with calendar_reader() as read:
        def capture(path):
            result = read(path)
            if isinstance(result, dict):
                for event in ([result] if result.get('id') else result.get('value', [])):
                    if event.get('id'):
                        captured[event['id']] = event
                for event in result.get('exceptionOccurrences') or []:
                    captured[event['id']] = event
            return result
        plan = cancellation_plan(series, rows, saved, capture)
    if plan['seriesCancelled']:
        raise CalendarStateError('Microsoft has already cancelled this calendar. Sync calendar status first.')
    bindings = plan['snapshot']['occurrences']
    commands = []
    changes = payload.get('changes') or []
    zone = ZoneInfo(v.graph_timezone_iana({'timezone': series.get('timezone') or 'GMT Standard Time'}))
    candidate = {**module, 'session_overrides': dict(session_overrides(module))}
    if action == 'cancel' and scope == 'series':
        for event_id in plan['snapshot']['roots']:
            event = captured.get(event_id)
            if not event or event_id in plan['cancelledRootIds']:
                continue
            if not event.get('isCancelled'):
                commands.append({'eventId': event_id, 'action': action, 'joinUrl': (event.get('onlineMeeting') or {}).get('joinUrl') or '',
                                 'etag': event.get('@odata.etag') or '', 'state': 'pending'})
    else:
        if action == 'reschedule':
            if 'session_overrides' not in module:
                raise CalendarStateError('Session date editing is not configured on this server.')
            if not isinstance(changes, list) or not changes or len(changes) > 52:
                raise ValueError('Supply between 1 and 52 session changes.')
            numbers = [item.get('sessionNumber') for item in changes if isinstance(item, dict)]
            if (len(numbers) != len(changes) or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in numbers)
                    or len(set(numbers)) != len(numbers)):
                raise ValueError('Each session can be edited only once.')
            allowed = {row['session_number'] for row in affected if eligible(row)}
            if not set(numbers) <= allowed:
                raise ValueError('A change names a session outside this action or one with attendance history.')
            affected = [row for row in affected if row['session_number'] in numbers]
        for row in affected:
            binding = bindings.get(row['id'])
            if not binding or row['id'] in plan['cancelledIds']:
                raise CalendarStateError(f"Session {row['session_number']} could not be verified. Sync calendar status first.")
            event = captured.get(binding['eventId'])
            if not event or event.get('isCancelled'):
                raise CalendarStateError('The selected Microsoft session could not be verified.')
            command = {'eventId': binding['eventId'], 'rootId': binding['rootId'], 'occurrenceId': row['id'],
                       # Microsoft's own name for this occurrence within its
                       # series, which is how a cancelled one is recognised on
                       # the master once it has left the instance list.
                       'occurrenceGraphId': binding.get('occurrenceId') or '',
                       'sessionNumber': row['session_number'], 'action': action, 'state': 'pending',
                       'joinUrl': row.get('join_url') or series.get('join_url') or '', 'etag': event.get('@odata.etag') or ''}
            if action == 'reschedule':
                change = next(item for item in changes if item['sessionNumber'] == row['session_number'])
                start = utc_datetime(change.get('startDateTimeUtc'))
                duration = change.get('durationMinutes')
                if not isinstance(duration, int) or isinstance(duration, bool) or not 15 <= duration <= 1440:
                    raise ValueError('Choose a duration between 15 and 1440 minutes.')
                if start <= datetime.now(timezone.utc) or utc_datetime(row['scheduled_start']) <= datetime.now(timezone.utc):
                    raise ValueError('Only future sessions can be rescheduled.')
                if start == utc_datetime(row['scheduled_start']) and start + timedelta(minutes=duration) == utc_datetime(row['scheduled_end']):
                    continue
                exception = schedule_override(start, duration, zone)
                candidate['session_overrides'][str(row['session_number'])] = exception
                command.update(start=start.isoformat(), end=(start + timedelta(minutes=duration)).isoformat(), exception=exception)
            commands.append(command)
    if not commands:
        raise ValueError('There are no calendar changes to apply.')
    if action == 'reschedule':
        holidays = [*v.module_cohort_selected_holidays(module),
                    *(v.parse_json_value(module.get('session_holidays'), []) or [])]
        closed = v.holiday_date_set(holidays)
        if any(datetime.fromisoformat(c['exception']['date']).date() in closed for c in commands):
            raise ValueError('A new date falls on a selected holiday or module closure. Choose an open date.')
        proposed = v.module_session_plan_for_count(candidate, v.module_stored_session_count(candidate))
        validate_exception_plan(proposed)
        if v.find_tutor_schedule_conflicts(v.module_schedule_view(candidate)):
            raise ValueError('The new time conflicts with another module assigned to this tutor.')
    comment = str(payload.get('comment') or '').strip()
    if len(comment) > 1000:
        raise ValueError('Keep the cancellation message within 1,000 characters.')
    operation = {'id': uuid.uuid4().hex, 'actor': actor, 'liveId': live_id, 'action': action, 'scope': scope,
                 'version': fingerprint(series, rows, module), 'commands': commands, 'comment': comment,
                 'snapshot': plan['snapshot'], 'status': 'reviewed',
                 # What the calendar held when this was reviewed, so the change
                 # can be told as "was ... now ..." once Microsoft confirms it.
                 'before': schedule_snapshot(rows)}
    reviewed_sessions = []
    for row in affected:
        event = captured.get((bindings.get(row['id']) or {}).get('eventId')) or {}
        calendar_start, calendar_end = event_instant(event, 'start'), event_instant(event, 'end')
        reviewed_sessions.append({
            'sessionNumber': row['session_number'],
            'startDateTimeUtc': (calendar_start or utc_datetime(row['scheduled_start'])).isoformat(),
            'endDateTimeUtc': (calendar_end or utc_datetime(row['scheduled_end'])).isoformat(),
            'calendarVerified': bool(calendar_start and calendar_end),
            'joinUrl': row.get('join_url') or series.get('join_url'),
            'newStartDateTimeUtc': next((c.get('start') for c in commands if c.get('occurrenceId') == row['id']), None),
            'newEndDateTimeUtc': next((c.get('end') for c in commands if c.get('occurrenceId') == row['id']), None),
        })
    return {'reviewToken': signing.dumps(operation, salt=SALT, compress=True), 'action': action, 'scope': scope,
            'title': series.get('module_title') or 'Teams calendar', 'organizer': series.get('organizer_email'),
            'timeZone': str(zone), 'notificationRequired': action == 'cancel', 'calendarRequests': len(commands),
            'sessions': reviewed_sessions, 'warnings': plan['errors']}


def leftover_preview(live_id, payload, actor, series, rows, saved):
    """Review cancelling one slot that is on Teams but is not a module session.

    The only path that removes such a slot. Nothing automatic does -- a save, a
    sync or a background job leaves it where it is and only reports it -- so a
    cancellation Exchange emails to everyone invited is always this click and
    its confirmation. The slot is re-proved against Microsoft here: it must still
    be live, and still not be any scheduled LMS session's own occurrence.
    """
    from . import views as v
    if payload.get('action') != 'cancel':
        raise ValueError('A slot that is not a module session can only be cancelled.')
    event_id = str(payload.get('eventId') or '')
    if not event_id:
        raise ValueError('Choose the Teams slot to cancel.')
    module = load_module(series)
    captured = {}
    with calendar_reader() as read:
        def capture(path):
            result = read(path)
            if isinstance(result, dict):
                for event in ([result] if result.get('id') else result.get('value', [])):
                    if event.get('id'):
                        captured[event['id']] = event
                for event in result.get('exceptionOccurrences') or []:
                    captured[event['id']] = event
            return result
        plan = cancellation_plan(series, rows, saved, capture)
    slot = next((item for item in plan.get('leftovers') or [] if item.get('eventId') == event_id), None)
    if not slot:
        raise CalendarStateError('That Teams slot is no longer listed as outside the module plan. Sync calendar status first.')
    event = captured.get(event_id)
    if not event or event.get('isCancelled'):
        raise CalendarStateError('That Teams slot could not be verified. Sync calendar status first.')
    join_url = (event.get('onlineMeeting') or {}).get('joinUrl') or ''
    command = {'eventId': event_id, 'rootId': slot.get('rootId') or event_id, 'occurrenceId': '',
               'occurrenceGraphId': slot.get('occurrenceId') or '', 'sessionNumber': 0,
               'action': 'cancel', 'state': 'pending', 'leftover': True,
               'joinUrl': join_url, 'etag': event.get('@odata.etag') or ''}
    comment = str(payload.get('comment') or '').strip()
    if len(comment) > 1000:
        raise ValueError('Keep the cancellation message within 1,000 characters.')
    operation = {'id': uuid.uuid4().hex, 'actor': actor, 'liveId': live_id, 'action': 'cancel', 'scope': 'leftover',
                 'version': fingerprint(series, rows, module), 'commands': [command], 'comment': comment,
                 'snapshot': plan['snapshot'], 'status': 'reviewed'}
    zone = ZoneInfo(v.graph_timezone_iana({'timezone': series.get('timezone') or 'GMT Standard Time'}))
    start = slot.get('startDateTimeUtc') or ''
    return {'reviewToken': signing.dumps(operation, salt=SALT, compress=True), 'action': 'cancel', 'scope': 'leftover',
            'title': series.get('module_title') or 'Teams calendar', 'organizer': series.get('organizer_email'),
            'timeZone': str(zone), 'notificationRequired': True, 'calendarRequests': 1,
            'sessions': [{'sessionNumber': 0, 'startDateTimeUtc': start, 'endDateTimeUtc': slot.get('endDateTimeUtc') or '',
                          'calendarVerified': True, 'joinUrl': join_url, 'kind': slot.get('kind'), 'day': slot.get('day') or ''}],
            'warnings': plan['errors']}


def graph_action(series, command, comment, notify_attendees=True):
    """Exactly one attempted mutation. Transport errors are never auto-retried."""
    from coach_api.views import get_graph_settings, microsoft_graph_token
    try:
        settings = get_graph_settings()
        token = microsoft_graph_token()
    except (RuntimeError, httpx.HTTPError) as exc:
        raise ActionNotSent('Microsoft sign-in could not be verified. No calendar change was sent.') from exc
    if settings['base_url'].rstrip('/') != 'https://graph.microsoft.com/v1.0':
        raise CalendarStateError('The Microsoft calendar endpoint is not supported.')
    path = f"users/{quote(series['organizer_email'], safe='')}/events/{quote(command['eventId'], safe='')}"
    with httpx.Client(base_url='https://graph.microsoft.com/v1.0/', follow_redirects=False,
                      headers={'Authorization': 'Bearer ' + token}, timeout=20) as client:
        try:
            current = client.get(path)
        except httpx.HTTPError as exc:
            raise ActionNotSent('The meeting could not be checked. No change was sent; review it again.') from exc
        if current.status_code != 200:
            raise ActionNotSent('The meeting changed or could not be read. Review it again.')
        try:
            event = current.json()
        except ValueError as exc:
            raise ActionNotSent('The Microsoft meeting response could not be read. No change was sent.') from exc
        if not isinstance(event, dict):
            raise ActionNotSent('The Microsoft meeting response could not be read. No change was sent.')
        if event.get('isCancelled') or (command.get('etag') and event.get('@odata.etag') != command['etag']):
            raise ActionNotSent('The Microsoft meeting changed after review. Review it again.')
        if (event.get('onlineMeeting') or {}).get('joinUrl', '') != command['joinUrl']:
            raise ActionNotSent('The meeting link changed after review.')
        if command['action'] == 'cancel':
            return send_cancellation(client, path, comment, not_sent=ActionNotSent,
                                     occurrence=bool(command.get('rootId') and command['rootId'] != command['eventId']))
        response = client.patch(
            path,
            json={'start': {'dateTime': command['start'], 'timeZone': 'UTC'},
                  'end': {'dateTime': command['end'], 'timeZone': 'UTC'}, 'hideAttendees': True},
            headers=None if notify_attendees else SILENT_UPDATE_HEADERS,
        )
        if 400 <= response.status_code < 500 and response.status_code not in (408, 409):
            try:
                code = (response.json().get('error') or {}).get('code')
            except (ValueError, AttributeError):
                code = None
            if code == 'ErrorOccurrenceCrossingBoundary':
                raise ActionNotSent('Microsoft requires this session to stay between its neighbouring recurrence dates. Choose a date between them.')
            raise ActionNotSent('Microsoft rejected the new time. The local plan was preserved.')
        return response.status_code == 200


def verified_move(series, command):
    path = f"users/{quote(series['organizer_email'], safe='')}/events/{quote(command['eventId'], safe='')}"
    with calendar_reader() as read:
        event = read(path)
    return bool(event and not event.get('isCancelled')
                and (event.get('onlineMeeting') or {}).get('joinUrl') == command['joinUrl']
                and event_instant(event, 'start') == utc_datetime(command['start'])
                and event_instant(event, 'end') == utc_datetime(command['end']))


def persist_move(series, rows, module, command, snapshot):
    """Called under the series/module locks after Microsoft confirms the move."""
    from . import views as v
    now = datetime.now(timezone.utc)
    exception = command['exception']
    overrides = dict(session_overrides(module))
    overrides[str(command['sessionNumber'])] = exception
    candidate = {**module, 'session_overrides': overrides}
    plan = v.module_session_plan_for_count(candidate, v.module_stored_session_count(candidate))
    with connection.cursor() as cursor:
        cursor.execute('''UPDATE curriculum.modules SET session_overrides = %s::jsonb, end_date = %s, updated_at = %s
                          WHERE module_catalogue_id = %s''',
                       [json.dumps(overrides), plan['finalEndDate'], now, module['module_catalogue_id']])
        cursor.execute('''UPDATE curriculum.live_session_occurrences SET scheduled_start = %s, scheduled_end = %s,
                          graph_event_id = %s, updated_at = %s WHERE id = %s AND live_session_id = %s''',
                       [command['start'], command['end'], command['eventId'], now, command['occurrenceId'], series['id']])
    # Components already carrying this stable occurrence/session identity
    # feed learner activities and training plans. Never update other sessions.
    settings = {'sessionDate': exception['date'], 'sessionDay': datetime.fromisoformat(exception['date']).strftime('%A'),
                'sessionTime': exception['startTime'], 'sessionDateTimeUtc': command['start'],
                'teamsStartDateTimeUtc': command['start'], 'durationMinutes': exception['durationMinutes'],
                'teamsDurationMinutes': exception['durationMinutes'], 'teamsEventId': command['eventId']}
    # Through the authoring write helper rather than a raw UPDATE, so a move is
    # recorded like every other change to a component. The statement this
    # replaced wrote the same values but never reached `versioning.record_rows`,
    # so a session moving to a different day -- `sessionDate`, `sessionTime`,
    # `durationMinutes`, the confirmed instant -- left nothing in the trail.
    # What the log keeps is still the log's decision: a write that only restamps
    # Teams identifiers diffs to nothing and records nothing, exactly as before.
    #
    # Read first and merge per row, because the statement this replaced merged
    # into `settings_json` (`||`) and the WHERE can match more than one
    # component. One assignment for all of them would flatten each row to the
    # same settings and drop whatever else it held.
    matched = v.authoring_fetch_all(
        v.AUTHORING_COMPONENTS_TABLE,
        '''module_catalogue_id = %s AND deleted_at IS NULL
           AND type IN ('live-session', 'live_session') AND
           (settings_json->>'teamsOccurrenceId' = %s OR
           (settings_json->>'teamsLiveSessionId' = %s AND settings_json->>'teamsSessionNumber' = %s))''',
        [module['module_catalogue_id'], command['occurrenceId'], series['id'], str(command['sessionNumber'])],
    )
    for row in matched:
        stored = v.as_json_value(row.get('settings_json'), {})
        v.update_authoring_rows(
            v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [row.get('id')],
            {'settings_json': v.json_db_value({**(stored if isinstance(stored, dict) else {}), **settings}),
             'updated_at': now},
        )
    binding = snapshot.get('occurrences', {}).get(command['occurrenceId'])
    if binding:
        binding.update(eventId=command['eventId'], dates=[command['start'], command['end']])
    v.invalidate_curriculum_cache()


def confirm_action(live_id, payload, actor):
    operation = signing.loads(payload.get('reviewToken') or '', salt=SALT, max_age=600)
    if operation.get('liveId') != live_id or operation.get('actor') != actor:
        raise ValueError('This review belongs to another calendar or account.')
    # Calendar actions always notify Microsoft and the LMS. The acknowledgement
    # confirms that the author reviewed the affected sessions; it is not an
    # optional email switch.
    notify_attendees = True
    if payload.get('acknowledgeNotifications') is not True:
        raise ValueError('Confirm the Microsoft calendar notification before continuing.')
    operation['notifyAttendees'] = notify_attendees
    with transaction.atomic():
        series, rows, snapshot = load_calendar_state(live_id, lock=True)
        module = load_module(series, lock=True)
        require_managed_calendar(series)
        previous = snapshot.get('management') or {}
        if previous.get('id') == operation['id']:
            return action_result(previous)
        if previous.get('status') in ('processing', 'uncertain'):
            raise CalendarStateError('Check the previous action before sending another request.')
        if fingerprint(series, rows, module) != operation['version']:
            raise CalendarStateError('This calendar changed after review. Review its latest dates first.')
        snapshot = operation.pop('snapshot')
        operation['status'] = 'processing'
        operation['leaseUntil'] = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
        snapshot['management'] = operation
        store_snapshot(live_id, snapshot)
    return continue_action(live_id, operation['id'], send=True)


def with_change_notice(live_id, result):
    """Attach a finished action's signed before/after record for email delivery.

    Only once every request is confirmed: a partial result is not a change
    anyone should be told about as done. The id is the operation's own, so a
    repeated status check hands out the same change, never a second one.
    """
    from .teams_schedule_notice import issue_change_notice, schedule_snapshot
    if result.get('status') != 'done':
        return result
    series, rows, saved = load_calendar_state(live_id)
    operation = saved.get('management') or {}
    # A slot outside the plan changes no module session, so there is no LMS
    # schedule change to tell anyone about; Microsoft's own cancellation is it.
    if operation.get('status') != 'done' or 'before' not in operation or operation.get('scope') == 'leftover':
        return result
    after = schedule_snapshot(rows) if series.get('status') == 'active' else []
    return {**result, 'changeNotice': issue_change_notice(live_id, operation['before'], after, notice_id=operation['id'])}


def with_change_emails(live_id, result):
    """Send the change or cancellation notice from the server once Microsoft is confirmed.

    Every confirmed action with a notice -- a session moved as much as one
    cancelled -- so closing the browser cannot lose the LMS email. The notice id
    is the operation's own, so the browser's retry reaches nobody twice.
    """
    if result.get('status') != 'done' or not result.get('changeNotice'):
        return result
    from .teams_schedule_delivery import send_change_emails
    return {**result, 'scheduleEmail': send_change_emails(live_id, result['changeNotice'])}


def action_result(operation):
    return {'status': operation.get('status'), 'action': operation.get('action'),
            'completed': sum(c['state'] == 'done' for c in operation.get('commands', [])),
            'total': len(operation.get('commands', [])),
            'message': operation.get('message') or ('Calendar change completed.' if operation.get('status') == 'done'
                       else 'The change needs a status check. Unconfirmed requests will not be sent again.')}


def continue_action(live_id, operation_id, send=False):
    """Status recovery only reads Microsoft; it never repeats an attempted write."""
    with transaction.atomic():
        series, rows, snapshot = load_calendar_state(live_id, lock=True)
        if send:
            require_managed_calendar(series)
        operation = saved_operation(snapshot, operation_id)
        if operation.get('status') in ('done', 'failed', 'incomplete'):
            return action_result(operation)
        if not send:
            if operation.get('status') == 'processing' and utc_datetime(operation.get('leaseUntil')) > datetime.now(timezone.utc):
                return action_result(operation)
            operation.update(status='processing', leaseUntil=(datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat())
            store_snapshot(live_id, snapshot)
    for index, original in enumerate(operation['commands']):
        if original['state'] == 'done':
            continue
        try:
            if original['state'] == 'pending' and send:
                with transaction.atomic():
                    series, rows, snapshot = load_calendar_state(live_id, lock=True)
                    module = load_module(series, lock=True)
                    require_managed_calendar(series)
                    operation = saved_operation(snapshot, operation_id)
                    if fingerprint(series, rows, module) != operation['version']:
                        raise CalendarStateError('The local schedule changed. Completed changes remain saved; review the remaining changes.')
                    command = operation['commands'][index]
                    if command['state'] != 'pending':
                        raise CalendarStateError('This request is already being processed.')
                    command['state'] = 'attempted'
                    operation['leaseUntil'] = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
                    store_snapshot(live_id, snapshot)
                accepted = graph_action(
                    series, command, operation['comment'],
                    operation.get('notifyAttendees', True),
                )
                if not accepted:
                    raise CalendarStateError('Microsoft did not confirm the request. Check calendar status before any further action.')
            elif original['state'] == 'pending':
                break
            command = operation['commands'][index]
            if command['action'] == 'reschedule':
                if not verified_move(series, command):
                    raise CalendarStateError('Microsoft has not confirmed the new time yet. Check status again.')
                with transaction.atomic():
                    series, rows, snapshot = load_calendar_state(live_id, lock=True)
                    module = load_module(series, lock=True)
                    operation = saved_operation(snapshot, operation_id)
                    if fingerprint(series, rows, module) != operation['version']:
                        raise CalendarStateError('Microsoft may have accepted the move, but the local plan changed. Manual reconciliation is required.')
                    persist_move(series, rows, module, command, snapshot)
                    current, current_rows, _ = load_calendar_state(live_id)
                    operation['version'] = fingerprint(current, current_rows, load_module(current))
                    operation['commands'][index]['state'] = 'done'
                    store_snapshot(live_id, snapshot)
            else:
                # Ask Microsoft about this one meeting, rather than inferring a
                # cancellation from a whole-calendar sweep that is built to
                # refuse to infer one. See teams_cancel for what counts as proof.
                with calendar_reader() as read:
                    cancelled = cancellation_confirmed(series, command, read)
                if not cancelled:
                    raise CalendarStateError('Microsoft accepted the cancellation but has not confirmed it yet. Use Check action status again in a moment.')
                with transaction.atomic():
                    current, current_rows, snapshot = load_calendar_state(live_id, lock=True)
                    operation = saved_operation(snapshot, operation_id)
                    operation['commands'][index]['state'] = 'done'
                    # The series row and the sessions still to run go only once
                    # every meeting of a series cancellation is confirmed; one
                    # session's cancellation touches its own row and no other.
                    persist_cancellation(live_id, operation['commands'][index], operation.get('scope'),
                                         complete=all(c['state'] == 'done' for c in operation['commands']))
                    if operation.get('scope') == 'leftover':
                        gone = operation['commands'][index]['eventId']
                        snapshot['leftovers'] = [item for item in snapshot.get('leftovers') or []
                                                 if item.get('eventId') != gone]
                    current, current_rows, _ = load_calendar_state(live_id)
                    operation['version'] = fingerprint(current, current_rows, load_module(current, lock=True))
                    store_snapshot(live_id, snapshot)
        except ActionNotSent as exc:
            with transaction.atomic():
                _, _, snapshot = load_calendar_state(live_id, lock=True)
                operation = saved_operation(snapshot, operation_id)
                operation['commands'][index]['state'] = 'failed'
                operation.update(status='failed', message=str(exc))
                store_snapshot(live_id, snapshot)
            return action_result(operation)
        except (CalendarStateError, httpx.HTTPError, RuntimeError, ValueError):
            with transaction.atomic():
                _, _, snapshot = load_calendar_state(live_id, lock=True)
                operation = saved_operation(snapshot, operation_id)
                operation.update(status='uncertain', message='Some changes are not confirmed. Use Check action status; this will not resend requests.')
                store_snapshot(live_id, snapshot)
            return action_result(operation)
    with transaction.atomic():
        _, _, snapshot = load_calendar_state(live_id, lock=True)
        operation = saved_operation(snapshot, operation_id)
        operation['status'] = 'done' if all(c['state'] == 'done' for c in operation['commands']) else 'incomplete'
        if operation['status'] == 'done':
            operation.pop('message', None)
        else:
            operation['message'] = 'Confirmed changes are saved. Close this dialog and review the remaining changes before sending them.'
        store_snapshot(live_id, snapshot)
    return action_result(operation)


@transaction.non_atomic_requests
@require_role('admin', 'staff')
@require_POST
def calendar_action(request, live_session_id):
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError('A JSON action is required.')
        actor = str(request.login_account.pk)
        if payload.get('stage') == 'review':
            result = action_preview(live_session_id, payload, actor)
        elif payload.get('stage') == 'confirm':
            result = with_change_emails(live_session_id, with_change_notice(live_session_id, confirm_action(live_session_id, payload, actor)))
        elif payload.get('stage') == 'status':
            _, _, saved = load_calendar_state(live_session_id)
            operation = saved.get('management') or {}
            result = with_change_emails(live_session_id, with_change_notice(live_session_id, continue_action(live_session_id, operation.get('id'), send=False))) if operation else {'status': 'none', 'message': 'No calendar action is awaiting confirmation.'}
        else:
            raise ValueError('Choose review, confirm or status.')
        return JsonResponse(result)
    except signing.BadSignature:
        return JsonResponse({'error': 'This review has expired or changed. Open the review again.'}, status=400)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    except (LookupError, CalendarStateError) as exc:
        return JsonResponse({'error': str(exc)}, status=409)
    except (httpx.HTTPError, RuntimeError):
        return JsonResponse({'error': 'Microsoft could not be reached. Check action status before trying again.'}, status=502)
