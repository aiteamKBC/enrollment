"""Several Graph series behind one module calendar, with an explicit link per session."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo
import uuid

from django.http import JsonResponse
from .teams_calendar_checks import (CalendarMismatch, calendar_targets, dropped_sessions_sentence, publish_attendees,
                                    sessions_a_rewrite_would_drop, verify_calendar)
from .teams_schedule_notice import same_schedule, schedule_snapshot
from .teams_update_guard import (claim_announcement, finish_announcement, graph_failure_outcome, logical_change_id,
                                 targets_snapshot)


class DroppedSessions(RuntimeError):
    """A weekday rewrite would take a future session off Teams; nothing for that day was sent."""


def graph_event_utc(event):
    """Graph DateTimeTimeZone values are instants, including non-UTC responses."""
    from . import views as v
    event = dict(event)
    for field in ('start', 'end'):
        value = event.get(field) or {}
        instant = v.parse_graph_datetime(value.get('dateTime'))
        if not instant:
            continue
        if instant.tzinfo is None:
            name = value.get('timeZone') or 'UTC'
            zone = v.GRAPH_WINDOWS_TO_IANA.get(name, name)
            instant = instant.replace(tzinfo=ZoneInfo(zone))
        event[field] = {'dateTime': instant.astimezone(timezone.utc).isoformat(), 'timeZone': 'UTC'}
    return event


def calendar_groups(payload, graph_settings):
    from . import views as v
    mode = payload.get('seriesMode') or 'auto'
    if mode not in ('auto', 'shared', 'per_day'):
        raise ValueError('Choose a shared series or a separate series for each day.')
    zone = ZoneInfo(v.graph_timezone_iana(graph_settings))
    items = payload.get('scheduledOccurrences') or []
    if not items:
        return []
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError('Scheduled occurrences must be a list of sessions.')
    groups, clocks, numbers = {}, set(), set()
    for item in items:
        start = v.parse_graph_datetime(item.get('startDateTimeUtc'))
        number = int(item.get('sessionNumber') or 0)
        duration = int(item.get('durationMinutes') or payload.get('durationMinutes') or 60)
        if not start or number < 1 or number in numbers or not 15 <= duration <= 1440:
            raise ValueError('Each scheduled session needs a unique number, valid start and duration.')
        numbers.add(number)
        local = start.replace(tzinfo=start.tzinfo or timezone.utc).astimezone(zone)
        day = local.strftime('%A')
        clocks.add((local.strftime('%H:%M'), duration))
        groups.setdefault(day, []).append({**item, 'sessionNumber': number, 'durationMinutes': duration,
                                          'startDateTimeUtc': local.astimezone(timezone.utc).isoformat()})
    if mode == 'shared' and len(clocks) > 1:
        raise ValueError('A shared Teams series requires the same start time and duration on every day. Choose separate series per day.')
    if mode != 'per_day' and len(clocks) <= 1:
        return []
    return [(day, sorted(items, key=lambda item: item['startDateTimeUtc'])) for day, items in groups.items()]


def stored_calendar_series(series):
    from . import views as v
    return v.parse_json_value((series or {}).get('calendar_series'), []) or []


def save_weekday_calendar(payload, graph_settings, series=None):
    """Create/update each weekday master, then read Graph before reporting tracked times.

    The manifest is saved after each Graph write. A partial failure can be retried
    through the existing update endpoint without creating the successful days again.
    """
    from coach_api.views import microsoft_graph_request
    from . import views as v
    series = series or {}
    if not v.has_column(v.LIVE_SESSIONS_TABLE, 'calendar_series'):
        return v.json_error('Apply curriculum migration 0063 before creating separate Teams series.', status=409)
    existing = stored_calendar_series(series)
    combined = {
        'organizerEmail': series.get('organizer_email'),
        'attendees': v.teams_series_email_list(series.get('attendees')),
        'presenters': v.teams_series_email_list(series.get('presenters')),
        'coOrganizers': v.teams_series_email_list(series.get('co_organizers')),
        'recording': series.get('recording'), 'lobbyBypass': series.get('lobby_bypass'),
        'spokenLanguage': series.get('spoken_language'), 'meetingType': series.get('meeting_type'),
        'moduleCatalogueId': series.get('module_catalogue_id'),
        **payload,
        'hideAttendees': True,
    }
    # A change to the meeting's dates is announced to everyone already invited
    # when the author asked for it (``notifyAttendees``), exactly as on a shared
    # series: the per-day date writes are silent, and the announcement is the
    # attendee write that follows on each day whose dates actually changed. A
    # save that only corrects who is invited, or how the meeting runs, is never
    # announced: it goes to Microsoft silently and the people it adds are
    # forwarded the meeting on their own, so nobody already on the calendar is
    # mailed about a learner joining it. The LMS schedule email to those added
    # is separate. A new calendar is always announced: that is its invitation.
    creating = not v.clean_str(series.get('id'))
    people_only = bool(combined.get('peopleOnly'))
    invitations_only = people_only and bool(combined.get('invitationsOnly'))
    notify_attendees = True if creating else (False if people_only else bool(combined.get('notifyAttendees')))
    newly_invited = v.teams_newly_invited(
        [
            *v.teams_series_email_list(series.get('attendees')),
            *v.teams_series_email_list(series.get('presenters')),
            *v.teams_series_email_list(series.get('co_organizers')),
            v.clean_str(series.get('organizer_email')),
        ],
        [
            *v.teams_series_email_list(combined.get('coOrganizers')),
            *v.teams_series_email_list(combined.get('presenters')),
            *v.teams_series_email_list(combined.get('attendees')),
        ],
    ) if existing else []
    # An existing calendar always belongs to its stored organizer's mailbox.
    organizer = v.clean_str(series.get('organizer_email')) or v.teams_new_meeting_organizer(combined.get('organizerEmail'))
    if not organizer:
        return v.json_error('Organizer email is required.')
    combined['organizerEmail'] = organizer
    if existing:
        # Keep the established day links even if their clocks now happen to match.
        combined['seriesMode'] = 'per_day'
    try:
        groups = calendar_groups(combined, graph_settings)
        if not groups:
            return v.json_error('No weekday sessions were supplied.')
        requested_targets = [item for _day, items in groups for item in items]
        if not combined.get('peopleOnly'):
            non_delivery_reason = v.teams_non_delivery_reason(
                requested_targets,
                v.graph_timezone_iana(graph_settings),
            )
            if non_delivery_reason:
                return v.json_error(non_delivery_reason, status=400, code='non_delivery_date')
        prepared = []
        zone = ZoneInfo(v.graph_timezone_iana(graph_settings))
        for day, items in groups:
            first = v.parse_graph_datetime(items[0]['startDateTimeUtc'])
            local = first.replace(tzinfo=first.tzinfo or timezone.utc).astimezone(zone).replace(tzinfo=None)
            day_payload = {
                **combined, 'scheduledOccurrences': items,
                'startDateTimeUtc': items[0]['startDateTimeUtc'],
                'localStartDateTime': local.isoformat(),
                'durationMinutes': items[0]['durationMinutes'],
                'repeat': 'weekly' if len(items) > 1 else 'none', 'repeatOccurrences': len(items),
                'transactionId': f"{combined.get('transactionId') or 'TEAMS-' + str(combined.get('moduleCatalogueId') or series.get('id'))}-{day}",
            }
            event_body, invited_people, presenters, co_organizers, stored_attendees, start, duration, repeat, count = v.teams_event_payload(day_payload, graph_settings)
            event_body.setdefault('recurrence', None)
            prepared.append((day, day_payload, event_body, invited_people, stored_attendees, presenters, co_organizers))
        if co_organizers and not v.has_column(v.LIVE_SESSIONS_TABLE, 'co_organizers'):
            return v.json_error('The co-organizers schema must be applied before creating this meeting.', status=409)
    except (TypeError, ValueError, KeyError) as exc:
        return v.json_error(str(exc), status=400)

    owner = quote(organizer, safe='')
    live_id = v.clean_str(series.get('id'))
    manifest = list(existing)
    warnings, settings_applied = [], True
    # The onlineMeeting options are their own operation (see
    # teams_meeting_options_policy): sent per day only for the groups that
    # changed, were left unapplied, or for a day Microsoft just created; a
    # refusal is reported and never blocks the invitations or the dates.
    from .teams_meeting_options_policy import (NOT_APPLIED, json_list, option_groups_to_apply, pending_option_groups,
                                              pending_warning, remaining_pending, reported_failure,
                                              stored_options as saved_meeting_options)
    stored_options = saved_meeting_options(series, 'invited')
    saved_warnings = json_list(series.get('warnings'))
    pending_options = pending_option_groups(saved_warnings)
    option_failures, attempted_groups = [], set()
    first_event = None
    tracked_numbers = set()
    publish_queue = []
    leftover_slots = []
    requested_numbers = {item['sessionNumber'] for _, items in groups for item in items}
    stored_rows = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_id]) if live_id else []
    # The logical change, named by its content (see teams_update_guard): the
    # dates the LMS saved last, the dates asked for, and who is invited.
    held_before = schedule_snapshot(stored_rows)
    requested_after = []
    for _day, items in groups:
        for item in items:
            begins = v.parse_graph_datetime(item['startDateTimeUtc'])
            begins = begins.replace(tzinfo=begins.tzinfo or timezone.utc)
            requested_after.append({'n': item['sessionNumber'], 'start': begins.isoformat(),
                                    'end': (begins + timedelta(minutes=item['durationMinutes'])).isoformat()})
    everyone = [person for entry in prepared for person in entry[3]]
    change_id = logical_change_id(live_id, held_before, requested_after, everyone,
                                  v.teams_calendar_subject(combined, series))
    if people_only:
        # A people-only save never touches a date, so every day must already be
        # exactly where the LMS holds it -- checked for all of them before
        # anything is written. Drift is reported, never repaired here: the
        # repair moves or re-creates sessions, which is a schedule update.
        for day, day_payload, *_rest in prepared:
            previous = next((item for item in manifest if item.get('day') == day), {})
            event_id = v.clean_str(previous.get('eventId'))
            try:
                targets = calendar_targets(day_payload, day_payload['startDateTimeUtc'], day_payload['durationMinutes'],
                                           day_payload['repeat'], len(day_payload['scheduledOccurrences']),
                                           v.graph_timezone_iana(graph_settings))
                if not event_id:
                    raise CalendarMismatch(f'{day} has no Teams series saved for its sessions.')
                verify_calendar(microsoft_graph_request, owner, event_id, targets, previous.get('joinUrl') or '',
                                day_payload['repeat'] != 'none')
            except CalendarMismatch as mismatch:
                return v.json_error(
                    'The Teams calendar dates no longer match the dates saved here, so the invitations were not '
                    'saved. Nothing was changed in Teams. Update the Teams calendar dates first, then save the '
                    'invitations again.',
                    status=409, code='teams_schedule_drift', detail=str(mismatch), liveSessionId=live_id,
                )
            except RuntimeError as exc:
                return v.json_error('Microsoft Teams could not read the calendar, so nothing was changed.',
                                    status=502, detail=str(exc), liveSessionId=live_id)
    claims = {}
    announced_writes = {'count': 0}
    pending_rows = []
    try:
        for index, (day, day_payload, body, invited_people, stored_attendees, presenters, co_organizers) in enumerate(prepared):
            previous = next((item for item in manifest if item.get('day') == day), {})
            # When splitting a legacy calendar, keep its first link for the first day.
            if not previous and not existing and series and index == 0:
                previous = {'eventId': series.get('graph_event_id'), 'joinUrl': series.get('join_url'), 'onlineMeetingId': series.get('online_meeting_id')}
            event_id = v.clean_str(previous.get('eventId'))
            targets = calendar_targets(day_payload, day_payload['startDateTimeUtc'], day_payload['durationMinutes'],
                                       day_payload['repeat'], len(day_payload['scheduledOccurrences']), v.graph_timezone_iana(graph_settings))
            dates_already_verified = False
            if event_id:
                current = microsoft_graph_request('GET', f'users/{owner}/events/{quote(event_id, safe="")}')
                current_link = (current.get('onlineMeeting') or {}).get('joinUrl') or ''
                if current.get('isCancelled') or (previous.get('joinUrl') and previous['joinUrl'] != current_link):
                    raise RuntimeError('The saved weekday calendar identity differs from Microsoft. Review it before continuing.')
                # Preserve the existing Teams meeting body; Graph stores its
                # meeting information there. Recipients are applied after verification.
                patch = {key: value for key, value in body.items() if key not in ('transactionId', 'isOnlineMeeting', 'onlineMeetingProvider', 'body', 'attendees')}
                if combined.get('peopleOnly'):
                    patch = {'hideAttendees': True}
                try:
                    event = verify_calendar(microsoft_graph_request, owner, event_id, targets, previous.get('joinUrl') or '', day_payload['repeat'] != 'none')
                    dates_already_verified = True
                    patch = {'subject': body['subject']} if event.get('subject') != body['subject'] else {}
                except CalendarMismatch:
                    pass
                if 'recurrence' in patch:
                    # Rewriting this day's recurrence regenerates it from the
                    # pattern. A future session of this day that is no longer
                    # planned anywhere, and falls outside the new range, would
                    # simply stop existing -- a cancellation nobody pressed.
                    own = set(previous.get('sessionNumbers') or [])
                    candidates = [row for row in stored_rows
                                  if row.get('session_number') in own and row.get('session_number') not in requested_numbers]
                    dropped = sessions_a_rewrite_would_drop(targets, day_payload['repeat'] != 'none', candidates,
                                                            v.graph_timezone_iana(graph_settings))
                    if dropped:
                        raise DroppedSessions(dropped_sessions_sentence(dropped, v.graph_timezone_iana(graph_settings)))
                if patch:
                    event = microsoft_graph_request(
                        'PATCH', f'users/{owner}/events/{quote(event_id, safe="")}', payload=patch,
                        extra_headers=v.GRAPH_SILENT_INVITE_HEADERS,
                    ) or {}
                event = {**event, 'id': event_id}
            else:
                event = microsoft_graph_request('POST', f'users/{owner}/events', payload={**body, 'attendees': []})
                event_id = v.clean_str(event.get('id'))
            if not event_id:
                raise RuntimeError(f'Microsoft Graph did not return the {day} event identifier.')
            if not (event.get('onlineMeeting') or {}).get('joinUrl'):
                event = microsoft_graph_request('GET', f'users/{owner}/events/{quote(event_id, safe="")}')
            join_url = v.clean_str((event.get('onlineMeeting') or {}).get('joinUrl')) or v.clean_str(previous.get('joinUrl'))
            entry = {**previous, 'day': day, 'eventId': event_id, 'joinUrl': join_url, 'verified': False, 'unplanned': False,
                     'sessionNumbers': [item['sessionNumber'] for item in day_payload['scheduledOccurrences']]}
            manifest = [item for item in manifest if item.get('day') != day] + [entry]
            if not live_id:
                # Do not manufacture tracked occurrences before Graph confirms them.
                live_id, _ = v.persist_live_session_series(
                    {**combined, 'scheduledOccurrences': [], 'repeat': 'none'}, event, [], graph_settings,
                    organizer, stored_attendees, presenters, co_organizers=co_organizers, persist_occurrences=False,
                )
            v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {'calendar_series': v.json_db_value(manifest)})
            if first_event is None:
                first_event = event
            fresh_meeting = (not previous.get('onlineMeetingId') or not previous.get('joinUrl')
                             or str(previous.get('joinUrl') or '').strip() != join_url)
            day_groups = option_groups_to_apply(
                stored_options,
                {'recording': combined.get('recording') or 'none',
                 'lobby_bypass': combined.get('lobbyBypass') or v.DEFAULT_TEAMS_LOBBY_BYPASS,
                 'spoken_language': combined.get('spokenLanguage') or 'en-GB',
                 'presenters': presenters, 'co_organizers': co_organizers},
                pending_options,
                new_meeting=fresh_meeting and not people_only,
                retry_pending=not people_only,
            )
            attempted_groups |= day_groups
            applied, meeting, option_warnings = True, {}, []
            if day_groups:
                applied, meeting, option_warnings = v.apply_teams_meeting_options(
                    organizer, join_url, recording=combined.get('recording') or 'none',
                    lobby_bypass=combined.get('lobbyBypass') or v.DEFAULT_TEAMS_LOBBY_BYPASS, spoken_language=combined.get('spokenLanguage') or 'en-GB',
                    attendees=invited_people, presenters=presenters, co_organizers=co_organizers,
                    online_meeting_id=previous.get('onlineMeetingId'), groups=day_groups, live_session_id=live_id,
                )
                if not applied and not option_warnings:
                    option_warnings = [{'code': NOT_APPLIED, 'groups': sorted(day_groups),
                                        'message': 'Microsoft did not apply this meeting\u2019s options.', 'detail': ''}]
            settings_applied = settings_applied and applied
            entry['onlineMeetingId'] = v.clean_str(meeting.get('id')) or v.clean_str(previous.get('onlineMeetingId'))
            entry['meetingOptionsUrl'] = (v.clean_str(meeting.get('meetingOptionsWebUrl'))
                                          or v.clean_str(previous.get('meetingOptionsUrl')))
            if fresh_meeting and option_warnings:
                # A meeting this save just created: nobody is invited to it
                # while its lobby and roles are still the tenant defaults.
                warnings.extend(option_warnings)
            else:
                option_failures.extend(option_warnings)
            if day_payload['repeat'] != 'none':
                if not combined.get('peopleOnly') and not dates_already_verified:
                    # The third value is the occurrences the shift PROVED removable:
                    # here, with no stored rows handed in, only the recurrence's own
                    # weekly filler. A week this calendar actually owns is never in
                    # it -- see `vacated_occurrence_keys` / `tracked_occurrence_keys`.
                    shift_warnings, _, stale = v.apply_teams_occurrence_shifts(owner, quote(event_id, safe=''), body['subject'], targets, invited_people)
                    # Reported, never deleted: see flag_teams_occurrence_leftovers.
                    leftover_slots.extend(v.flag_teams_occurrence_leftovers(
                        stale,
                        live_session_id=v.clean_str(body.get('liveSessionId')),
                        module_catalogue_id=v.clean_str(combined.get('moduleCatalogueId')),
                        source='weekday_series_cleanup',
                    ))
                    warnings.extend(shift_warnings)
                query = urlencode({'startDateTime': (min(item['start'] for item in targets) - timedelta(days=7)).isoformat(),
                                   'endDateTime': (max(item['end'] for item in targets) + timedelta(days=7)).isoformat(), '$top': 200})
                instances = (microsoft_graph_request('GET', f'users/{owner}/events/{quote(event_id, safe="")}/instances?{query}') or {}).get('value') or []
            else:
                instances = [event]
            available = [graph_event_utc(item) for item in instances]
            verified = bool(join_url)
            for target in targets:
                match = next((item for item in available if v.teams_calendar_minute_key((item.get('start') or {}).get('dateTime')) == v.teams_calendar_minute_key(target['start'])), None)
                if not match:
                    verified = False
                    warnings.append({'code': 'teams_weekday_session_unverified', 'message': f"{day} session {target['session_number']} could not be verified on Teams. Update the calendar to retry."})
                    continue
                available.remove(match)
                number = target['session_number']
                tracked_numbers.add(number)
                # Saved only once every day is verified and published (below). Saved
                # here, a failed attempt left the LMS holding dates nobody had been
                # told about, and its retry -- reading those as "already saved" --
                # never announced them.
                pending_rows.append({
                    'live_session_id': live_id, 'session_number': number,
                    'graph_event_id': match.get('id') or event_id,
                    'scheduled_start': v.parse_graph_datetime((match.get('start') or {}).get('dateTime')),
                    'scheduled_end': v.parse_graph_datetime((match.get('end') or {}).get('dateTime')),
                    'join_url': join_url, 'online_meeting_id': entry['onlineMeetingId'],
                })
                if v.teams_calendar_minute_key((match.get('end') or {}).get('dateTime')) != v.teams_calendar_minute_key(target['end']):
                    verified = False
                    warnings.append({'code': 'teams_weekday_duration_mismatch', 'message': f"{day} session {number} has a different end time on Teams. Update the calendar to retry."})
            entry['verified'] = verified
            v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {'calendar_series': v.json_db_value(manifest)})
            checked = verify_calendar(microsoft_graph_request, owner, event_id, targets,
                                      previous.get('joinUrl') or join_url, day_payload['repeat'] != 'none')
            # Did this day's sessions really move? Against both what Microsoft
            # held before this save and what the LMS last saved for this day.
            own_numbers = {target['session_number'] for target in targets} | set(previous.get('sessionNumbers') or [])
            day_before = [item for item in held_before if item['n'] in own_numbers]
            day_changed = not dates_already_verified or not same_schedule(day_before, targets_snapshot(targets))
            publish_queue.append((checked, body['attendees'], targets, day_payload['repeat'] != 'none', day, day_changed))

        if warnings:
            raise RuntimeError('Microsoft did not accept every reviewed session. New invitations remain pending.')
        # Every weekday must pass before any new invitation list is published.
        # The list itself is always published: who is on a meeting is not the
        # author's email choice to make. Only whether Microsoft announces it is.
        #
        # A day is announced when its dates changed and the author asked for
        # the notice -- even when its invitation list is unchanged, because the
        # date write itself is silent and this attendee write is the only thing
        # that puts the new dates in invitees' calendars. A day whose dates did
        # not change is written silently (and skipped when nothing differs), so
        # nobody is told about a date that did not move.
        def announcing_request(method, path, *, payload=None, extra_headers=None):
            announced = method == 'PATCH' and not extra_headers
            try:
                result = microsoft_graph_request(method, path, payload=payload, extra_headers=extra_headers)
            except RuntimeError as exc:
                if announced and graph_failure_outcome(exc) == 'unknown':
                    announced_writes['count'] += 1
                raise
            if announced:
                announced_writes['count'] += 1
            return result

        silent_days = []
        for checked, recipients, targets, recurring, day, day_changed in publish_queue:
            announce_day = notify_attendees and (creating or day_changed)
            if announce_day and not creating:
                # Claimed before Microsoft is asked; an earlier attempt at this
                # same change that already asked is not repeated.
                claims[day] = claim_announcement(live_id, change_id, f'day-{day}')
                if claims[day] == 'attempted':
                    announce_day = False
            if not announce_day and claims.get(day) != 'attempted':
                silent_days.append(v.clean_str(checked.get('id')))
            announced_writes['count'] = 0
            try:
                written = publish_attendees(
                    announcing_request if announce_day else microsoft_graph_request, owner, checked, recipients,
                    extra_headers=None if announce_day else v.GRAPH_SILENT_INVITE_HEADERS,
                    always=announce_day,
                )
                if written:
                    verify_calendar(microsoft_graph_request, owner, checked['id'], targets,
                                    (checked.get('onlineMeeting') or {}).get('joinUrl'), recurring)
            except RuntimeError:
                if claims.get(day) == 'claimed':
                    finish_announcement(live_id, change_id, f'day-{day}',
                                        'unknown' if announced_writes['count'] else 'failed')
                    claims[day] = 'unknown'
                raise
            if claims.get(day) == 'claimed':
                finish_announcement(live_id, change_id, f'day-{day}', 'accepted')
                claims[day] = 'accepted'

        # "Save without notifying" sends nothing, not even to the people it adds.
        # Days that were announced already carried the added people on that
        # write (an earlier attempt's included), so only silent days forward.
        if not creating and newly_invited and silent_days and not invitations_only:
            # Every silent day's series, and only after all of them are verified:
            # the first thing someone added sees should be the finished calendar.
            warnings.extend(v.forward_teams_invitation(
                microsoft_graph_request, owner, silent_days, newly_invited,
                comment=f"You have been added to {v.teams_calendar_subject(combined, series)}.",
            ))

        for values in pending_rows:
            prior = (v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s and session_number = %s',
                                           [live_id, values['session_number']]) or [{}])[0]
            v.authoring_upsert(v.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
                'id': prior.get('id') or f'OCC-{uuid.uuid4().hex.upper()}', **values,
                'status': prior.get('status') if prior.get('status') == 'completed' else 'scheduled',
                'updated_at': datetime.utcnow(),
            })

        failed_groups = {group for item in option_failures for group in (item.get('groups') or attempted_groups)}
        still_pending = remaining_pending(pending_options, attempted_groups, failed_groups)
        previous_pending = next((item for item in saved_warnings
                                 if isinstance(item, dict) and item.get('code') == NOT_APPLIED), None)
        requested_settings = ({'recording': combined.get('recording') or 'none',
                               'lobby_bypass': combined.get('lobbyBypass') or v.DEFAULT_TEAMS_LOBBY_BYPASS,
                               'spoken_language': combined.get('spokenLanguage') or 'en-GB'}
                              if 'settings' in failed_groups else (previous_pending or {}).get('requested'))
        stored_option_warnings = ([pending_warning(still_pending, (option_failures or [previous_pending])[0],
                                                   requested_settings)]
                                  if still_pending else [])
        days = {day for day, _ in groups}
        for old in list(manifest):
            if old['day'] not in days:
                # A weekday the plan no longer uses keeps its Teams series. It
                # is not deleted -- that is a cancellation Exchange emails to
                # everyone invited -- but kept in the manifest, holding no
                # sessions, and flagged so a person can cancel it by hand.
                index = manifest.index(old)
                manifest[index] = {**old, 'sessionNumbers': [], 'unplanned': True}
                leftover_slots.append({'eventId': old.get('eventId') or '', 'day': old['day'], 'kind': 'series',
                                       'reason': 'weekday_no_longer_planned'})
                v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {'calendar_series': v.json_db_value(manifest)})
        for row in v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_id]):
            # Not in the plan any more, but nobody pressed Cancel and its Teams
            # slot is still there: "not in plan", never 'cancelled'.
            if row['session_number'] not in requested_numbers and str(row.get('status') or '') not in ('cancelled', 'canceled'):
                v.update_authoring_rows(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s', [row['id']], {'status': 'superseded'})
        main = next(item for item in manifest if item['day'] == groups[0][0])
        v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {
            'calendar_series': v.json_db_value(manifest), 'graph_event_id': main['eventId'],
            'join_url': main['joinUrl'], 'online_meeting_id': main.get('onlineMeetingId') or '',
            'meeting_options_url': main.get('meetingOptionsUrl') or '',
            'start_datetime': v.parse_graph_datetime(combined.get('startDateTimeUtc')),
            'duration_minutes': combined.get('durationMinutes') or 60, 'repeat_pattern': 'weekly',
            'repeat_occurrences': len(requested_numbers), 'module_title': v.teams_calendar_subject(combined, series),
            'attendees': v.json_db_value(stored_attendees), 'presenters': v.json_db_value(presenters), 'co_organizers': v.json_db_value(co_organizers),
            # A refused settings change is not saved as if Microsoft had taken it.
            **({'recording': stored_options['recording'], 'lobby_bypass': stored_options['lobby_bypass'],
                'spoken_language': stored_options['spoken_language']} if 'settings' in failed_groups and series else {
                'recording': combined.get('recording') or 'none',
                'lobby_bypass': combined.get('lobbyBypass') or v.DEFAULT_TEAMS_LOBBY_BYPASS,
                'spoken_language': combined.get('spokenLanguage') or 'en-GB'}),
            'warnings': v.json_db_value([*warnings, *stored_option_warnings]), 'updated_at': datetime.utcnow(),
            'hide_attendees': True,
        })
        # The meeting ID and passcode under the main join link; a read, skipped
        # once captured for this link.
        from .teams_dial_in import remember_teams_dial_in
        remember_teams_dial_in({'id': live_id, 'join_url': main['joinUrl'], 'online_meeting_id': main.get('onlineMeetingId'),
                                'organizer_email': organizer, 'dial_in_join_url': series.get('dial_in_join_url'),
                                'join_meeting_code': series.get('join_meeting_code')})
        module_id = combined.get('moduleCatalogueId')
        if module_id and v.authoring_module_exists(module_id):
            saved = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id])[0]
            occurrences = v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, "live_session_id = %s and status not in ('cancelled', 'superseded')", [live_id], 'session_number')
            # Creating or updating a calendar only attaches it to live-session
            # components the author already placed. New components belong to
            # the explicit Restore/Re-attach action, never to Send itself.
            v.attach_teams_meeting_to_module_weeks(module_id, saved, v.live_session_row_to_component_settings(saved), occurrences,
                                                   create_missing=False)
    except DroppedSessions as exc:
        return v.json_error(str(exc), status=409, code='teams_update_would_drop_sessions', liveSessionId=live_id,
                            calendarSeries=manifest)
    except RuntimeError as exc:
        for day, state in claims.items():
            if state == 'claimed':
                # Claimed but never reached: a retry may announce that day.
                finish_announcement(live_id, change_id, f'day-{day}', 'failed')
        if live_id:
            # This failed save wrote no options: keep the unapplied-options marker.
            unapplied = set(pending_options) | {group for item in option_failures
                                                for group in (item.get('groups') or attempted_groups)}
            v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {
                'warnings': v.json_db_value([*warnings, {'code': 'teams_calendar_unverified', 'message': str(exc)},
                                             *([pending_warning(unapplied, (option_failures or [{}])[0])] if unapplied else [])]),
                'updated_at': datetime.utcnow(),
            })
        return v.json_error('Teams could not finish every weekday series. Update this calendar to retry the remaining work.',
                            status=502, detail=str(exc), liveSessionId=live_id, partial=bool(live_id), calendarSeries=manifest)

    meeting_result = {
        'liveSessionId': live_id, 'eventId': main['eventId'], 'joinUrl': main['joinUrl'],
        'onlineMeetingId': main.get('onlineMeetingId') or '', 'meetingOptionsUrl': main.get('meetingOptionsUrl') or '',
        'webLink': (first_event or {}).get('webLink') or '', 'organizerEmail': organizer,
        'attendees': stored_attendees, 'presenters': presenters, 'coOrganizers': co_organizers,
        'startDateTimeUtc': combined.get('startDateTimeUtc'), 'durationMinutes': combined.get('durationMinutes'),
        'repeat': 'weekly', 'repeatOccurrences': len(requested_numbers), 'trackedOccurrences': len(tracked_numbers),
        'settingsApplied': settings_applied, 'trackingReady': all(item.get('onlineMeetingId') for item in manifest),
        'provider': 'Microsoft Teams', 'calendarSeries': manifest,
    }
    for checked, *_rest in publish_queue:
        leftover_slots.extend(checked.get('unplannedInstances') or [])
    # One entry per Microsoft event, however many checks noticed it.
    leftover_slots = list({slot.get('eventId') or str(index): slot for index, slot in enumerate(leftover_slots)}.values())
    # The onlineMeeting options, reported apart from the invitations and dates.
    option_result = {'optionGroupsSent': sorted(attempted_groups), 'optionsApplied': not option_failures,
                     'optionsPending': sorted(still_pending), 'partial': bool(option_failures)}
    if not series:
        return JsonResponse({'created': True, 'meeting': meeting_result, **option_result,
                             'warnings': [item.get('message') or str(item) for item in [*warnings, *option_failures]],
                             'leftoverSlots': leftover_slots}, status=201)
    changed_days = [entry[4] for entry in publish_queue if entry[5]]
    attempted = [day for day, state in claims.items() if state == 'attempted']
    return JsonResponse({
        'updated': True, 'meeting': meeting_result, 'warnings': [*warnings, *[reported_failure(item) for item in option_failures]],
        **option_result,
        'leftoverSlots': leftover_slots,
        # The same result fields as a shared series: whether a date change was
        # announced, and what Microsoft was asked ('sent', 'already_attempted',
        # 'silent', 'not_needed'). The LMS change email follows `announced`.
        'announced': notify_attendees and bool(changed_days),
        'microsoftUpdate': ('not_needed' if not changed_days else 'silent' if not notify_attendees
                            else 'already_attempted' if len(attempted) == len(changed_days) else 'sent'),
        'retryGuard': 'unavailable' if 'unguarded' in claims.values() else 'on',
        'changeId': change_id,
    })
