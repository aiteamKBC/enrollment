"""One extra Teams meeting on a single week, entirely separate from the module's.

A module's own Teams calendar is one series covering every live session it
delivers: one organiser, one invitation list, one join link per session, written
onto every live-session component by ``attach_teams_meeting_to_module_weeks``.
That is the right shape for teaching the module and the wrong shape for the
one-off -- a guest speaker for week 6, an employer panel, a resit briefing --
that has its own host, its own guests and must not appear on anybody else's
calendar.

So this books a meeting that the module's calendar cannot see and cannot touch,
in both directions:

* Its ``curriculum.live_sessions`` row is stored with ``status =
  'week-meeting'``. Every module-scoped read of that table in this project
  filters ``status = 'active'`` -- the create endpoint's duplicate check, the
  summary, the series read, the delivery metadata, the coach schedule and the
  post-save re-attach. None of them can see this row, so creating one here
  never blocks, supersedes, redirects or reschedules the module's own calendar,
  and the module's calendar is still created and updated exactly as before.

* Its join link is written to the component under its own ``extraTeams*``
  settings keys, never ``liveSessionUrl``/``live_sessions_link``.
  ``attach_teams_meeting_to_module_weeks`` rewrites those two on every
  component, every time the module is saved with a calendar -- storing the
  extra link there would have it silently replaced by the module's. Under its
  own keys it survives, because that function merges over the settings it finds
  rather than replacing them.

The two blocks this endpoint enforces are the author's, not incidental:

1. A week with no live-session component has nowhere to keep the link, so there
   is nothing to attach it to. Add a live session to the week first.
2. A live-session component that already holds an ADDITIONAL meeting is
   refused, so no additional meeting is ever replaced by another.

What is deliberately NOT a block is the module's own calendar link. That link
is written onto every live-session component whenever a module with a calendar
is saved, so treating it as "already taken" would make this feature unusable on
exactly the modules it is for. A week carrying the module's link is free to
take an additional meeting, and that link is left untouched when it does.

Only the people named on this form are invited, by Microsoft, on the one date
chosen. The meeting keeps one ``live_session_occurrences`` row (session 1) of
its own -- see ``save_week_meeting_occurrence`` -- so its recording, transcript
and attendance are synced and saved like any other live session's.
"""
import logging
import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)

#: The status that keeps this row out of every module-scoped read. Never
#: 'active' -- see the module docstring.
WEEK_MEETING_STATUS = 'week-meeting'


def may_send_schedule_email(request):
    """Whether this caller may have the server send the LMS schedule email.

    Exactly the roles `teams_create_guard.with_creation_emails` admits for the
    module calendar, read the same way, so booking an additional meeting never
    widens who can make the LMS mail people.
    """
    from .teams_create_guard import EMAIL_ROLES
    try:
        from login.sessions import authenticate_request
        account = authenticate_request(request)
    except Exception:
        return False
    return account is not None and getattr(account, 'role', None) in EMAIL_ROLES


#: An additional meeting is a single event, so it delivers exactly one session.
WEEK_MEETING_SESSION_NUMBER = 1


def save_week_meeting_occurrence(v, live_session_id, *, start, duration, join_url, event_id, status='scheduled'):
    """Write the one session an additional meeting delivers.

    Recordings, transcripts and attendance are all saved against an occurrence
    row, so without one the sync has nowhere to put them. Re-saving keeps the
    same row (and everything synced onto it); a session already marked
    completed stays completed when only its schedule is edited.
    """
    now = datetime.utcnow()
    existing = v.authoring_fetch_all(
        v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s and session_number = %s',
        [live_session_id, WEEK_MEETING_SESSION_NUMBER],
    )
    values = {
        'live_session_id': live_session_id,
        'session_number': WEEK_MEETING_SESSION_NUMBER,
        'graph_event_id': v.clean_str(event_id),
        'scheduled_start': start,
        'scheduled_end': start + timedelta(minutes=int(duration or 60)),
        'join_url': v.clean_str(join_url),
        'status': status,
        'updated_at': now,
    }
    if existing:
        if status == 'scheduled' and v.clean_str(existing[0].get('status')) == 'completed':
            values['status'] = 'completed'
        v.update_authoring_rows(v.LIVE_SESSION_OCCURRENCES_TABLE, 'id = %s', [existing[0]['id']], values)
    else:
        v.authoring_upsert(v.LIVE_SESSION_OCCURRENCES_TABLE, ['live_session_id', 'session_number'], {
            **values, 'id': f'OCC-{uuid.uuid4().hex.upper()}', 'created_at': now,
        })


def ensure_week_meeting_occurrence(series):
    """Give an additional meeting booked before occurrences were saved its session row.

    Only for a live additional meeting with no occurrence yet; every other
    series, and every meeting that already has its row, is left untouched.
    """
    if not isinstance(series, dict) or str(series.get('status') or '').strip() != WEEK_MEETING_STATUS:
        return
    from . import views as v
    live_id = v.clean_str(series.get('id'))
    if not live_id or v.authoring_fetch_all(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_id],
                                            ensure_tables=False):
        return
    start = series.get('start_datetime')
    if isinstance(start, str):
        start = v.parse_graph_datetime(start)
    if not isinstance(start, datetime):
        return
    save_week_meeting_occurrence(
        v, live_id, start=start, duration=series.get('duration_minutes'),
        join_url=series.get('join_url'), event_id=series.get('graph_event_id'),
    )


def component_extra_meeting_settings(row, v):
    """The extra meeting already stored on a component, if any."""
    settings = v.component_builder_settings(row)
    return settings if isinstance(settings, dict) else {}


def existing_additional_meeting(settings, v):
    """The ADDITIONAL meeting this live-session component already holds, if any.

    Only ``extraTeamsMeetingUrl``. Never ``liveSessionUrl``,
    ``teamsMeetingUrl`` or the ``live_sessions_link`` column: those three are
    the MODULE's calendar, and ``attach_teams_meeting_to_module_weeks`` writes
    them onto every live-session component whenever a module that has a
    calendar is saved. Reading them here would mean that the moment the
    module's own Teams meeting exists -- the normal case -- every live session
    in the module counted as taken and no additional meeting could ever be
    booked. The module's link is inherited context, not an additional meeting;
    it neither blocks one nor is touched by one.
    """
    return v.clean_str(settings.get('extraTeamsMeetingUrl'))


def booked_on_module_calendar(settings, v):
    """Whether this live session already holds a booked occurrence of the module series.

    ``teamsOccurrenceId`` and ``teamsSessionNumber`` are written only by
    ``live_occurrence_component_settings``, which runs when a real Graph
    occurrence was paired to the component -- unlike ``liveSessionUrl`` and
    ``teamsLiveSessionId``, which the series stamps on every live session
    whether Microsoft holds one for it or not.

    A booked session is refused: taking it out of the module series (which is
    what an additional meeting does) would either strand the occurrence
    Microsoft still holds or cancel a meeting people are already invited to.
    Only a live session the module calendar has not booked can take one.
    """
    return v.live_session_booked_on_module_calendar(settings)


def reserved_for_module_calendar(settings, v):
    """Whether the author has already given this live session to the module calendar.

    Not yet booked there -- that is `booked_on_module_calendar` -- but chosen
    for it, which is just as final: the next Update books it, and an additional
    meeting on the same week would then be overwritten by the series' link.
    """
    return (not booked_on_module_calendar(settings, v)
            and v.clean_str(settings.get(v.LIVE_SESSION_MEETING_SCOPE_KEY)).lower() == 'main')


@csrf_exempt
def curriculum_week_teams_meeting(request, module_catalogue_id):
    """Create one additional Teams meeting on a chosen week of a module."""
    from . import views as v
    from coach_api.views import get_graph_settings, has_graph_credentials, microsoft_graph_request

    if request.method != 'POST':
        return v.json_error('Method not allowed.', status=405)
    if not has_graph_credentials():
        return v.json_error('Microsoft Graph credentials are not configured.', status=503)

    payload = v.json_body(request)
    if not isinstance(payload, dict):
        return v.json_error('A valid JSON body is required.')

    v.ensure_module_authoring_tables()
    v.ensure_live_sessions_table()
    requested_id = v.clean_str(module_catalogue_id)
    resolved_id = v.resolve_stored_module_catalogue_id(requested_id) or requested_id
    if not resolved_id or not v.authoring_module_exists(resolved_id):
        return v.json_error('Module authoring structure not found.', status=404)

    week_id = v.clean_str(payload.get('weekId'))
    if not week_id:
        return v.json_error('Choose the week this meeting belongs to.', status=400)
    week_rows = v.active_week_rows(v.authoring_fetch_all(
        v.AUTHORING_WEEKS_TABLE, 'module_catalogue_id = %s and id = %s', [resolved_id, week_id],
    ))
    if not week_rows:
        return v.json_error('That week is not part of this module. Reopen the module and choose again.', status=404)
    week_row = week_rows[0]
    week_label = f"Week {v.parse_int(week_row.get('week_number'), 0) or ''}".strip()

    # Block 1: the week must already have a live-session component. This is the
    # only place the link can live, so a week without one is refused rather than
    # having a component authored for it -- creating components is the module
    # builder's job and the re-attach action's, never a meeting's side effect.
    components = v.active_component_rows(v.authoring_fetch_all(
        v.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s and week_id = %s',
        [resolved_id, week_id], 'display_order, id',
    ))
    live_rows = [row for row in components if v.frontend_component_type(row.get('type')) == 'live-session']
    if not live_rows:
        return v.json_error(
            f'{week_label or "This week"} has no Live Teams Session component, so there is nowhere to keep this '
            'meeting’s link. Add a live session to the week, save the module, then book the meeting.',
            status=400, code='week_has_no_live_session',
        )

    requested_component = v.clean_str(payload.get('componentId'))
    if requested_component:
        component_row = next(
            (row for row in live_rows if v.clean_str(row.get('id')) == requested_component), None,
        )
    else:
        # The first live session of the week that has no additional meeting of
        # its own -- not simply the first, which would refuse a week that still
        # has a free live session beside a booked one.
        def free(row):
            settings = component_extra_meeting_settings(row, v)
            return (not existing_additional_meeting(settings, v) and not booked_on_module_calendar(settings, v)
                    and not reserved_for_module_calendar(settings, v))

        component_row = next((row for row in live_rows if free(row)), live_rows[0])
    if component_row is None:
        return v.json_error('That live session is not part of the chosen week. Reopen the module and choose again.', status=404)
    component_id = v.clean_str(component_row.get('id'))
    settings = component_extra_meeting_settings(component_row, v)

    # Block 2: never replace an additional meeting that already exists. The
    # module calendar's own link on this component is deliberately NOT a block
    # -- see `existing_additional_meeting`.
    held = existing_additional_meeting(settings, v)
    if held:
        return v.json_error(
            f'“{v.clean_str(component_row.get("title")) or "This live session"}” already has an additional meeting. '
            'A live session can hold one, so that no additional meeting is ever replaced. Choose another week, or '
            'add another live session to this one. This module’s own Teams calendar is unaffected either way.',
            status=409, code='live_session_already_has_additional_meeting',
            existingLink=held,
        )

    # Block 3: a live session the module's calendar has already booked cannot be
    # taken out of that series -- see `booked_on_module_calendar`.
    if booked_on_module_calendar(settings, v):
        return v.json_error(
            f'“{v.clean_str(component_row.get("title")) or "This live session"}” is already booked on this module’s '
            'own Teams calendar. An additional meeting replaces the week’s module session rather than running '
            'alongside it, so it can only be added to a live session that has not been sent to Teams yet. Cancel '
            'that session on the Module Teams calendar tab first, or choose another week.',
            status=409, code='live_session_already_booked',
        )

    # Block 4: nor one the author has already given to the module calendar --
    # see `reserved_for_module_calendar`. Each week goes to one calendar only.
    if reserved_for_module_calendar(settings, v):
        return v.json_error(
            f'“{v.clean_str(component_row.get("title")) or "This live session"}” is set to run on this module’s own '
            'Teams calendar. A week is delivered by one calendar only, so it cannot also take an additional meeting. '
            'Move it to “Additional meeting” first, or choose another week.',
            status=409, code='live_session_reserved_for_module_calendar',
        )

    graph_settings = get_graph_settings()
    organizer = v.teams_new_meeting_organizer(payload.get('organizerEmail'))
    if not organizer:
        return v.json_error(
            'Organizer email is required. Add it here or configure MICROSOFT_TEAMS_ORGANIZER_EMAIL.', status=400,
        )

    # One meeting on one date: no recurrence, and therefore no holiday shifting
    # and no occurrence pairing. The business-calendar rule still applies -- a
    # meeting cannot be booked on a weekend or a closure date.
    single = {
        **payload,
        'repeat': 'none',
        'repeatOccurrences': 1,
        'hideAttendees': True,
    }
    # The one date is `startDateTimeUtc`; a sent occurrence list belongs to a
    # series and would silently book more than the author chose.
    single.pop('scheduledOccurrences', None)
    try:
        graph_settings = v.teams_schedule_settings(graph_settings, single)
        event_payload, invited_people, presenters, co_organizers, attendees, utc_start, duration, _repeat, _count = (
            v.teams_event_payload(single, graph_settings)
        )
        targets = v.calendar_targets(single, utc_start, duration, 'none', 1, v.graph_timezone_iana(graph_settings))
        non_delivery = v.teams_non_delivery_reason(targets, v.graph_timezone_iana(graph_settings))
        if non_delivery:
            return v.json_error(non_delivery, status=400, code='non_delivery_date')
    except (TypeError, ValueError, KeyError) as exc:
        return v.json_error(str(exc), status=400)
    if co_organizers and not v.has_column(v.LIVE_SESSIONS_TABLE, 'co_organizers'):
        return v.json_error(
            'Co-organizers cannot be saved until the curriculum.live_sessions co_organizers column is added.',
            status=409, code='teams_co_organizers_schema_required',
        )

    owner_key = v.urllib_parse.quote(organizer, safe='')
    # Booked without recipients, then the invitation list is PUBLISHED onto it
    # below. This is the module calendar's own sequence and it is the reason
    # anybody receives anything: Microsoft puts a meeting on someone's calendar
    # only when a write announces it, so creating the event with the attendees
    # already attached and never touching it again can leave a meeting whose
    # attendee list is right and whose attendees were never told. The publish
    # also reads the event back and refuses to call it done unless Microsoft
    # confirms every address.
    try:
        event = microsoft_graph_request('POST', f'users/{owner_key}/events', payload={**event_payload, 'attendees': []})
    except RuntimeError as exc:
        logger.warning('Unable to create an additional week Teams meeting: %s', exc)
        return v.json_error('Microsoft Teams could not create the meeting.', status=502, detail=str(exc))

    event_id = v.clean_str(event.get('id'))
    if not event_id:
        return v.json_error('Microsoft did not return a calendar identifier. No invitations were sent.', status=502)
    online_meeting = event.get('onlineMeeting') or {}
    join_url = v.clean_str(online_meeting.get('joinUrl'))
    if not join_url:
        try:
            event = microsoft_graph_request(
                'GET', f'users/{owner_key}/events/{v.urllib_parse.quote(event_id, safe="")}',
            )
            join_url = v.clean_str((event.get('onlineMeeting') or {}).get('joinUrl'))
        except RuntimeError:
            pass

    warnings = []
    lobby = v.clean_str(single.get('lobbyBypass')).lower() or 'invited'
    if lobby not in v.TEAMS_LOBBY_VALUES:
        lobby = 'invited'
    recording = v.clean_str(single.get('recording')).lower() or 'none'
    language = v.clean_str(single.get('spokenLanguage')) or 'en-GB'
    settings_applied, graph_meeting, option_warnings = v.apply_teams_meeting_options(
        organizer, join_url, recording=recording, lobby_bypass=lobby, spoken_language=language,
        attendees=invited_people, presenters=presenters, co_organizers=co_organizers,
    )
    for option_warning in option_warnings:
        message = v.clean_str(option_warning.get('message'))
        detail = v.clean_str(option_warning.get('detail'))
        warnings.append(f'{message} ({detail})' if detail else message)
    if not join_url:
        warnings.append('Microsoft Graph created the calendar event but has not returned the Teams join URL yet.')

    meeting_options_url = v.clean_str(graph_meeting.get('meetingOptionsWebUrl'))
    online_meeting_id = v.clean_str(graph_meeting.get('id'))
    live_session_id = f'LIVE-{uuid.uuid4().hex.upper()}'
    now = datetime.utcnow()
    subject = v.clean_str(event.get('subject')) or v.clean_str(event_payload.get('subject'))
    try:
        # Written directly rather than through `persist_live_session_series`:
        # that helper marks the module's existing active calendar superseded,
        # which is exactly what this meeting must never do.
        v.authoring_upsert(v.LIVE_SESSIONS_TABLE, ['id'], {
            'id': live_session_id,
            'module_catalogue_id': resolved_id,
            'module_draft_id': '',
            'module_title': subject,
            'provider': 'Microsoft Teams',
            'graph_event_id': event_id,
            'online_meeting_id': online_meeting_id,
            'join_url': join_url,
            'web_link': v.clean_str(event.get('webLink')),
            'meeting_options_url': meeting_options_url,
            'organizer_email': organizer,
            'attendees': v.json_db_value(attendees),
            'presenters': v.json_db_value(presenters),
            'co_organizers': v.json_db_value(co_organizers),
            'start_datetime': utc_start,
            'timezone': graph_settings.get('timezone') or '',
            'duration_minutes': duration,
            'repeat_pattern': 'none',
            'repeat_occurrences': 1,
            'lobby_bypass': lobby,
            'recording': recording,
            'spoken_language': language,
            'meeting_type': 'week-additional',
            'request_responses': bool(single.get('requestResponses', True)),
            'allow_time_proposals': bool(single.get('allowNewTimeProposals', True)),
            'hide_attendees': True,
            'status': WEEK_MEETING_STATUS,
            'warnings': v.json_db_value(warnings),
            'created_at': now,
            'updated_at': now,
        })
    except Exception:
        logger.exception('The additional week Teams meeting was created but could not be saved.')
        return v.json_error(
            'The Teams meeting was created in Microsoft, but the LMS could not save its record. '
            'Cancel it in the organiser’s calendar before trying again.',
            status=500, meetingCreated=True, eventId=event_id, joinUrl=join_url,
        )
    from .teams_dial_in import remember_teams_dial_in
    remember_teams_dial_in({'id': live_session_id, 'join_url': join_url, 'online_meeting_id': online_meeting_id,
                            'organizer_email': organizer}, graph_meeting)

    # Invite the people named on the form -- the actual send. `always=True`
    # because the event was created empty on purpose: the patch is what makes
    # Microsoft deliver the meeting to each of them. No silent header, so the
    # invitation really is announced. `publish_attendees` reads the event back
    # and raises unless Microsoft confirms every address, so a partial
    # acceptance is reported rather than passing as success.
    invitations_sent = False
    invite_error = ''
    if event_payload['attendees']:
        try:
            invitations_sent = v.publish_attendees(
                microsoft_graph_request, owner_key, event, event_payload['attendees'], always=True,
            )
        except RuntimeError as exc:
            invite_error = str(exc)
            logger.warning('Additional week Teams meeting invitations were not confirmed: %s', exc)
        except Exception as exc:  # noqa: BLE001 - the meeting exists; report, never crash on it
            invite_error = str(exc)
            logger.exception('Additional week Teams meeting invitations could not be sent.')
        if invite_error:
            warnings.append(f'Microsoft did not confirm the invitations: {invite_error}')
            v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_session_id], {
                'warnings': v.json_db_value(warnings), 'updated_at': datetime.utcnow(),
            })

    # `extraTeams*` is where this meeting LIVES: the module calendar never reads
    # or writes those keys, so they survive every re-attach. `liveSessionUrl` and
    # `teamsMeetingUrl` are mirrored from them because this week is now delivered
    # by this meeting and by nothing else -- the module series skips the session
    # entirely -- so every reader of "this live session's Teams link" (the
    # builder, the learner's plan, exports) must find this one rather than the
    # module's. `attach_teams_meeting_to_module_weeks` keeps the mirror pointing
    # here on every later save instead of overwriting it.
    extra_settings = {
        'liveSessionUrl': join_url,
        'teamsMeetingUrl': join_url,
        'extraTeamsMeetingUrl': join_url,
        'extraTeamsLiveSessionId': live_session_id,
        'extraTeamsEventId': event_id,
        'extraTeamsOnlineMeetingId': online_meeting_id,
        'extraTeamsWebLink': v.clean_str(event.get('webLink')),
        'extraTeamsMeetingOptionsUrl': meeting_options_url,
        'extraTeamsOrganizerEmail': organizer,
        'extraTeamsAttendees': attendees,
        'extraTeamsPresenters': presenters,
        'extraTeamsCoOrganizers': co_organizers,
        'extraTeamsStartDateTimeUtc': utc_start.isoformat(),
        'extraTeamsDurationMinutes': duration,
        'extraTeamsSubject': subject,
        # The choice this booking makes, so the week stays out of the module
        # series even if a later read misses the link above.
        v.LIVE_SESSION_MEETING_SCOPE_KEY: 'additional',
    }
    try:
        v.update_authoring_rows(v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [component_id], {
            'settings_json': v.json_db_value({**settings, **extra_settings}),
            'live_sessions_link': join_url,
            'updated_at': now,
        })
        v.invalidate_curriculum_cache()
    except Exception:
        logger.exception('The additional week Teams meeting was saved but could not be linked to its component.')
        return v.json_error(
            'The meeting is booked and saved, but its link could not be written onto the live session. '
            'Reopen the module and add the link by hand.',
            status=502, meetingCreated=True, liveSessionId=live_session_id, joinUrl=join_url,
        )

    # The session its recording and attendance are saved against. Not fatal:
    # the meeting is booked and linked, and the first sync writes this row again.
    try:
        save_week_meeting_occurrence(
            v, live_session_id, start=utc_start, duration=duration, join_url=join_url, event_id=event_id,
        )
    except Exception:
        logger.exception('The additional week Teams meeting session row could not be saved.')
        warnings.append('The meeting is booked, but its recording and attendance tracking will start at the first sync.')

    # Partial success is never reported as success. The meeting exists and its
    # link is attached to the live session -- said here so the author does not
    # book a second one -- but somebody named on the form may not have been
    # invited, and only they can tell who.
    if invite_error:
        return v.json_error(
            'The meeting is booked and attached to the week, but Microsoft did not confirm its invitations, so '
            'some of the people named may not have received one. Open the meeting in the organiser’s calendar and '
            'check who is invited before sending it round.',
            status=502, code='teams_invitations_unconfirmed', detail=invite_error,
            meetingCreated=True, liveSessionId=live_session_id, joinUrl=join_url, warnings=warnings,
        )

    # Our own schedule email, beside Microsoft's invitation -- the same pair the
    # module's calendar sends. Microsoft's is a calendar item; this is the
    # readable schedule with the date, the time and the join button. Sent only
    # once Microsoft has confirmed the meeting and its invitations, and only by
    # a caller who may send mail, which is exactly the rule `with_creation_emails`
    # applies to the module calendar. Never fatal: the meeting is booked and
    # saved by now, so a mail problem is reported beside it, not in place of it.
    schedule_email = None
    if may_send_schedule_email(request):
        from .teams_schedule_delivery import send_week_meeting_emails
        schedule_email = send_week_meeting_emails(live_session_id)

    return JsonResponse({
        'created': True,
        'scheduleEmail': schedule_email,
        'invitationsSent': invitations_sent,
        'invited': [
            v.clean_str((person.get('emailAddress') or {}).get('address'))
            for person in event_payload['attendees']
        ],
        'meeting': {
            'liveSessionId': live_session_id,
            'weekId': week_id,
            'componentId': component_id,
            'eventId': event_id,
            'onlineMeetingId': online_meeting_id,
            'joinUrl': join_url,
            'webLink': v.clean_str(event.get('webLink')),
            'meetingOptionsUrl': meeting_options_url,
            'organizerEmail': organizer,
            'attendees': attendees,
            'presenters': presenters,
            'coOrganizers': co_organizers,
            'startDateTimeUtc': utc_start.isoformat(),
            'durationMinutes': duration,
            'subject': subject,
            'settingsApplied': settings_applied,
        },
        'componentSettings': extra_settings,
        'warnings': warnings,
    }, status=201)


def week_meeting_row(v, module_catalogue_id, live_session_id):
    """This module's additional meeting, by id. Never the module's own calendar.

    Scoped by ``status`` AND by module: an id belonging to a module series (or
    to another module) must not be reachable through this endpoint, because
    everything below writes to one component rather than to the module's weeks.
    """
    rows = v.authoring_fetch_all(
        v.LIVE_SESSIONS_TABLE,
        'id = %s and status = %s and module_catalogue_id = %s',
        [live_session_id, WEEK_MEETING_STATUS, module_catalogue_id],
    )
    return rows[0] if rows else None


def week_meeting_component(v, module_catalogue_id, live_session_id):
    """The live-session component this additional meeting is attached to."""
    components = v.active_component_rows(v.authoring_fetch_all(
        v.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s', [module_catalogue_id], 'display_order, id',
    ))
    for row in components:
        settings = component_extra_meeting_settings(row, v)
        if v.clean_str(settings.get('extraTeamsLiveSessionId')) == live_session_id:
            return row, settings
    return None, {}


@csrf_exempt
def curriculum_week_teams_meeting_detail(request, module_catalogue_id, live_session_id):
    """Change or cancel one additional week meeting.

    PATCH edits it; DELETE cancels it. Both act on this meeting's own Graph
    event and its own component only: the module's calendar is never read,
    written, re-attached or notified. That is why this is not
    ``curriculum_teams_meeting_schedule`` -- that endpoint finishes by
    re-attaching its series across every live-session component of the module,
    which would put this meeting's link on all of them.

    Emailing keeps Outlook and the LMS email in step:

    * A new date, time or length is always announced. Microsoft sends ONE
      update -- a single write carrying the new time and the full invitation
      list -- to everyone invited, and the LMS "was / now" follows once
      Microsoft confirms the new time. A silent time change would leave
      invitees' Outlook on the old time while the LMS emailed the new one, so
      the request is refused unless ``notifyAttendees`` is set.
    * Otherwise people already invited stay unbothered unless the author asks
      for them to be told. Anybody this save ADDS is reached either way: on the
      announced write when there is one, or by a forward of their own when the
      save is silent -- never both, because Microsoft puts a meeting on
      someone's calendar only when something is sent.
    * Somebody removed is taken off silently first, so no write here ever sends
      a cancellation. Only Cancel cancels.
    * A retry of the same edit after a timeout is never announced twice (see
      teams_update_guard), and its change email reuses one ledger key.
    """
    from . import views as v
    from coach_api.views import get_graph_settings, has_graph_credentials, microsoft_graph_request

    if request.method not in ('PATCH', 'DELETE'):
        return v.json_error('Method not allowed.', status=405)
    if not has_graph_credentials():
        return v.json_error('Microsoft Graph credentials are not configured.', status=503)

    v.ensure_module_authoring_tables()
    v.ensure_live_sessions_table()
    requested_id = v.clean_str(module_catalogue_id)
    resolved_id = v.resolve_stored_module_catalogue_id(requested_id) or requested_id
    live_id = v.clean_str(live_session_id)
    series = week_meeting_row(v, resolved_id, live_id)
    if not series:
        return v.json_error('That additional meeting was not found on this module.', status=404)
    component_row, settings = week_meeting_component(v, resolved_id, live_id)

    # The mailbox that owns the event, never the caller's: Graph cannot move an
    # existing event to another organiser, which is why the form locks it.
    organizer = v.clean_str(series.get('organizer_email'))
    owner_key = v.urllib_parse.quote(organizer, safe='')
    event_id = v.clean_str(series.get('graph_event_id'))
    event_key = v.urllib_parse.quote(event_id, safe='')
    now = datetime.utcnow()

    if request.method == 'DELETE':
        return cancel_week_meeting(
            v, microsoft_graph_request, component_row, settings, owner_key, event_key, live_id, now,
            series=series if may_send_schedule_email(request) else None,
        )

    payload = v.json_body(request)
    if not isinstance(payload, dict):
        return v.json_error('A valid JSON body is required.')
    notify_existing = bool(payload.get('notifyAttendees'))

    graph_settings = get_graph_settings()
    single = {
        **payload,
        'organizerEmail': organizer,
        'repeat': 'none',
        'repeatOccurrences': 1,
        'hideAttendees': True,
    }
    single.pop('scheduledOccurrences', None)
    try:
        graph_settings = v.teams_schedule_settings(graph_settings, single, series=series)
        event_payload, invited_people, presenters, co_organizers, attendees, utc_start, duration, _repeat, _count = (
            v.teams_event_payload(single, graph_settings)
        )
        targets = v.calendar_targets(single, utc_start, duration, 'none', 1, v.graph_timezone_iana(graph_settings))
        non_delivery = v.teams_non_delivery_reason(targets, v.graph_timezone_iana(graph_settings))
        if non_delivery:
            return v.json_error(non_delivery, status=400, code='non_delivery_date')
    except (TypeError, ValueError, KeyError) as exc:
        return v.json_error(str(exc), status=400)

    # Who this save adds, worked out before anything is written. A role change
    # does not make somebody new -- only an address that was not invited at all.
    def lower(values):
        return {v.clean_str(item).lower() for item in (values or []) if v.clean_str(item)}

    before = (lower(v.as_json_value(series.get('attendees'), []))
              | lower(v.as_json_value(series.get('presenters'), []))
              | lower(v.as_json_value(series.get('co_organizers'), [])))
    added = [email for email in invited_people if v.clean_str(email).lower() not in before]

    # Did the time really move, against what the LMS saved (and told people)?
    stored_start = v.parse_graph_datetime(series.get('start_datetime'))
    stored_duration = v.parse_int(series.get('duration_minutes'), 60)
    time_changed = (v.teams_calendar_minute_key(stored_start) != v.teams_calendar_minute_key(utc_start)
                    or stored_duration != duration)
    if time_changed and not notify_existing:
        return v.json_error(
            'A new date, time or length is always sent to the people invited, so their Outlook calendar and the '
            'LMS email show the same time. Choose to notify them, or keep the time unchanged.',
            status=400, code='time_change_needs_notice',
        )

    from .teams_update_guard import (claim_announcement, finish_announcement, graph_failure_outcome,
                                     logical_change_id)

    def one_session(start, minutes):
        start = v.utc_datetime(start)
        return [{'n': 1, 'start': start.isoformat(), 'end': (start + timedelta(minutes=minutes)).isoformat()}] if start else []

    change_id = logical_change_id(live_id, one_session(stored_start, stored_duration), one_session(utc_start, duration),
                                  invited_people, event_payload.get('subject'), extra='week-meeting')
    claim = claim_announcement(live_id, change_id) if notify_existing else ''
    # An earlier attempt at this very edit already asked Microsoft to announce
    # it; that is reported, never repeated. The write below is then silent.
    announce = notify_existing and claim != 'attempted'

    warnings = []
    path = f'users/{owner_key}/events/{event_key}'
    announced_writes = 0
    try:
        event = microsoft_graph_request('GET', path)
        if announce:
            # Whoever this announced write drops goes first, silently, so
            # Exchange sends them no cancellation.
            event, _removed = v.remove_attendees_silently(microsoft_graph_request, owner_key, {**event, 'id': event_id},
                                                          event_payload['attendees'])
        # ONE write: the time, the text and the invitation list together. Two
        # writes -- the event, then the attendees -- was two announcements to
        # everyone invited when the author asked for one.
        announced_writes = 1 if announce else 0
        microsoft_graph_request(
            'PATCH', path,
            payload={
                'subject': event_payload['subject'],
                'body': event_payload['body'],
                'start': event_payload['start'],
                'end': event_payload['end'],
                'responseRequested': event_payload['responseRequested'],
                'allowNewTimeProposals': event_payload['allowNewTimeProposals'],
                'hideAttendees': True,
                'attendees': event_payload['attendees'],
            },
            extra_headers=None if announce else v.GRAPH_SILENT_INVITE_HEADERS,
        )
    except RuntimeError as exc:
        if claim == 'claimed':
            finish_announcement(live_id, change_id, outcome=(
                graph_failure_outcome(exc) if announced_writes else 'failed'))
        return v.json_error('Microsoft Teams could not update the meeting. Nothing was saved here.',
                            status=502, detail=str(exc),
                            verificationRequired=bool(announced_writes and graph_failure_outcome(exc) == 'unknown'))
    if claim == 'claimed':
        finish_announcement(live_id, change_id, outcome='accepted')

    # Microsoft's own read-back: the time it holds and who it invited.
    time_confirmed = False
    try:
        confirmed = v.graph_event_utc(microsoft_graph_request('GET', path))
        time_confirmed = (
            v.teams_calendar_minute_key((confirmed.get('start') or {}).get('dateTime')) == v.teams_calendar_minute_key(utc_start)
            and v.teams_calendar_minute_key((confirmed.get('end') or {}).get('dateTime'))
            == v.teams_calendar_minute_key(v.utc_datetime(utc_start) + timedelta(minutes=duration))
        )
        if not time_confirmed:
            warnings.append('Microsoft did not confirm the new time, so the LMS change email was not sent.')
        missing, extra = v.attendee_differences(event_payload['attendees'], confirmed.get('attendees'),
                                                v.event_organizer_address(confirmed))
        if missing or extra:
            warnings.append('Microsoft did not confirm the invitation list '
                            f'({v.unconfirmed_attendee_detail(missing, extra)}).')
    except RuntimeError as exc:
        warnings.append(f'Microsoft did not confirm the updated meeting: {exc}')

    join_url = v.clean_str(series.get('join_url'))
    lobby = v.clean_str(single.get('lobbyBypass')).lower() or v.clean_str(series.get('lobby_bypass')) or 'invited'
    if lobby not in v.TEAMS_LOBBY_VALUES:
        lobby = 'invited'
    recording = v.clean_str(single.get('recording')).lower() or v.clean_str(series.get('recording')) or 'none'
    language = v.clean_str(single.get('spokenLanguage')) or v.clean_str(series.get('spoken_language')) or 'en-GB'
    # Only the option groups this save changed (see teams_meeting_options_policy).
    # A save that only adds or removes people sends no onlineMeeting PATCH; a
    # time change or a settings change also retries options left unapplied.
    from .teams_meeting_options_policy import (json_list, option_groups_to_apply, pending_warning, remaining_pending,
                                              stored_options as saved_meeting_options)
    stored_options = saved_meeting_options(series)
    saved_warnings = json_list(series.get('warnings'))
    # This meeting saves its warnings as sentences; an unapplied-options one is
    # recognised by its own wording, written below or by apply_teams_meeting_options.
    pending_options = {'settings', 'roles'} if any(
        marker in str((item if isinstance(item, str) else (item or {}).get('message')) or '')
        for item in saved_warnings
        for marker in ('did not apply its lobby', 'has not applied this meeting')
    ) else set()
    option_groups = option_groups_to_apply(
        stored_options,
        {'recording': recording, 'lobby_bypass': lobby, 'spoken_language': language,
         'presenters': presenters, 'co_organizers': co_organizers},
        pending_options, retry_pending=time_changed,
    )
    settings_applied, graph_meeting, option_warnings = True, {}, []
    if option_groups:
        settings_applied, graph_meeting, option_warnings = v.apply_teams_meeting_options(
            organizer, join_url, recording=recording, lobby_bypass=lobby, spoken_language=language,
            attendees=invited_people, presenters=presenters, co_organizers=co_organizers,
            online_meeting_id=v.clean_str(series.get('online_meeting_id')), groups=option_groups,
            live_session_id=live_id,
        )
    for option_warning in option_warnings:
        message = v.clean_str(option_warning.get('message'))
        detail = v.clean_str(option_warning.get('detail'))
        warnings.append(f'{message} ({detail})' if detail else message)
    options_pending = remaining_pending(pending_options, option_groups, set() if settings_applied else option_groups)
    if options_pending and settings_applied:
        # Not retried by this save (people only): keep saying so.
        warnings.append(pending_warning(options_pending)['message'])

    # Everyone this save added, reached on their own so the rest stay quiet --
    # only when nothing was announced. An announced write (this one, or the
    # earlier attempt's, which carried the same list) already invited them.
    forwarded_to = added if not notify_existing else []
    for forwarded in v.forward_teams_invitation(
        microsoft_graph_request, owner_key, [event_id], forwarded_to,
        comment='You have been added to this Teams meeting.',
    ):
        warnings.append(v.clean_str(forwarded.get('message')))

    subject = v.clean_str(event_payload.get('subject'))
    v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {
        'module_title': subject,
        'attendees': v.json_db_value(attendees),
        'presenters': v.json_db_value(presenters),
        'co_organizers': v.json_db_value(co_organizers),
        'start_datetime': utc_start,
        'duration_minutes': duration,
        # A refused settings change is not saved as if Microsoft had taken it.
        **({'lobby_bypass': stored_options['lobby_bypass'], 'recording': stored_options['recording'],
            'spoken_language': stored_options['spoken_language']}
           if not settings_applied and 'settings' in option_groups else
           {'lobby_bypass': lobby, 'recording': recording, 'spoken_language': language}),
        'warnings': v.json_db_value(warnings),
        'updated_at': now,
    })
    save_week_meeting_occurrence(
        v, live_id, start=utc_start, duration=duration, join_url=join_url,
        event_id=series.get('graph_event_id'),
    )

    updated_settings = {
        'extraTeamsOrganizerEmail': organizer,
        'extraTeamsAttendees': attendees,
        'extraTeamsPresenters': presenters,
        'extraTeamsCoOrganizers': co_organizers,
        'extraTeamsStartDateTimeUtc': utc_start.isoformat(),
        'extraTeamsDurationMinutes': duration,
        'extraTeamsSubject': subject,
    }
    if component_row is not None:
        v.update_authoring_rows(v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [component_row.get('id')], {
            'settings_json': v.json_db_value({**settings, **updated_settings}),
            'updated_at': now,
        })
    v.invalidate_curriculum_cache()

    # Our own emails, from the server, the same rule as the module calendar: the
    # people this save added get the full schedule -- under this save's own key,
    # so a guest removed and later added back is sent it again -- and nobody
    # already invited is sent it again. A time that moved is told to everyone
    # else as a "was / now" change, once Microsoft confirmed that same time and
    # announced it; the change is keyed by the logical edit, so a retry of it
    # never emails anyone twice.
    schedule_email = added_email = None
    if may_send_schedule_email(request):
        from .teams_schedule_delivery import send_week_meeting_change_emails, send_week_meeting_emails
        saved = week_meeting_row(v, resolved_id, live_id)
        if added:
            added_email = send_week_meeting_emails(live_id, added=added, key=f'{live_id}+{uuid.uuid4().hex}')
        if saved and time_changed and time_confirmed:
            schedule_email = send_week_meeting_change_emails(series, saved, exclude=added,
                                                             key=f'{live_id}#{change_id}')

    return JsonResponse({
        'updated': True,
        'scheduleEmail': schedule_email,
        'addedEmail': added_email,
        'notifiedExisting': notify_existing,
        # 'sent', 'already_attempted' (an earlier attempt at this same edit
        # asked; not repeated) or 'silent'. A request, never proof of delivery.
        'microsoftUpdate': 'silent' if not notify_existing else 'already_attempted' if claim == 'attempted' else 'sent',
        'timeChanged': time_changed,
        'forwardedTo': forwarded_to,
        'meeting': {
            'liveSessionId': live_id,
            'componentId': v.clean_str((component_row or {}).get('id')),
            'joinUrl': join_url,
            'organizerEmail': organizer,
            'attendees': attendees,
            'presenters': presenters,
            'coOrganizers': co_organizers,
            'startDateTimeUtc': utc_start.isoformat(),
            'durationMinutes': duration,
            'subject': subject,
            'meetingOptionsUrl': (v.clean_str(graph_meeting.get('meetingOptionsWebUrl'))
                                  or v.clean_str(series.get('meeting_options_url'))),
            'settingsApplied': settings_applied,
        },
        'componentSettings': updated_settings,
        'warnings': warnings,
    })


def cancel_week_meeting(v, graph_request, component_row, settings, owner_key, event_key, live_id, now, series=None):
    """Cancel one additional meeting and leave its week for the author to assign again.

    Microsoft cancels the event and tells the people invited -- that is what a
    cancellation IS, so it is never sent silently. The LMS cancellation email
    follows to everyone the meeting invited, as the module calendar's does;
    ``series`` is the row as it stood, passed only for a caller who may send
    mail. Afterwards the component's
    ``extraTeams*`` keys and its main/additional choice are cleared. On a module
    whose calendar exists that makes the live session 'pending' (see
    ``live_session_meeting_scope``): it is not handed back to the module series
    until someone chooses it for that calendar, so a cancel never books a
    session nobody asked for.
    """
    warnings = []
    try:
        graph_request('DELETE', f'users/{owner_key}/events/{event_key}')
    except RuntimeError as exc:
        # A meeting already gone from the calendar is the state being asked for.
        # Any other refusal leaves it standing, so nothing here is cleared --
        # clearing would lose the only record of a meeting that still exists.
        if '404' not in str(exc):
            return v.json_error(
                'Microsoft could not cancel the meeting, so it is still in the organiser’s calendar. '
                'Nothing was changed here.', status=502, detail=str(exc),
            )
        warnings.append('The meeting was already gone from the organiser’s calendar.')

    v.update_authoring_rows(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], {
        'status': 'cancelled', 'warnings': v.json_db_value(warnings), 'updated_at': now,
    })
    # Its session is cancelled with it; anything already synced onto it stays.
    v.update_authoring_rows(v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [live_id], {
        'status': 'cancelled', 'updated_at': now,
    })
    if component_row is not None:
        cleared = {key: value for key, value in settings.items()
                   if key not in v.ADDITIONAL_MEETING_SETTING_KEYS
                   and key != v.LIVE_SESSION_MEETING_SCOPE_KEY}
        # The mirrors go too: this live session has no meeting of its own now,
        # and leaving them would point the week at a cancelled event until the
        # module's own calendar happened to overwrite them.
        cleared['liveSessionUrl'] = ''
        cleared['teamsMeetingUrl'] = ''
        v.update_authoring_rows(v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [component_row.get('id')], {
            'settings_json': v.json_db_value(cleared),
            'live_sessions_link': '',
            'updated_at': now,
        })
    v.invalidate_curriculum_cache()
    schedule_email = None
    if series is not None:
        from .teams_schedule_delivery import send_week_meeting_change_emails
        schedule_email = send_week_meeting_change_emails(series)
    return JsonResponse({
        'cancelled': True,
        'liveSessionId': live_id,
        'componentId': v.clean_str((component_row or {}).get('id')),
        'scheduleEmail': schedule_email,
        'warnings': warnings,
    })


@csrf_exempt
def curriculum_live_session_meeting_scope(request, module_catalogue_id):
    """Choose which calendar delivers one live session: the module's own, or an additional meeting.

    POST ``{componentId, scope}`` with ``scope`` 'main' or 'additional'. This
    records the choice and inherits the main link. Imported calendar occurrences
    are matched to the current plan; nothing is sent to Microsoft. This keeps the
    two calendars apart before either has booked the week: one chosen for the
    module calendar is refused an additional meeting, and one chosen for an
    additional meeting is left out of every date the module calendar sends.

    A booking is final and cannot be re-chosen here: a live session holding an
    additional meeting is cancelled from that meeting first, and one the module
    series has booked is cancelled on the module calendar first.
    """
    from . import views as v

    if request.method != 'POST':
        return v.json_error('Method not allowed.', status=405)
    payload = v.json_body(request)
    if not isinstance(payload, dict):
        return v.json_error('A valid JSON body is required.')
    scope = v.clean_str(payload.get('scope')).lower()
    if scope not in v.LIVE_SESSION_MEETING_SCOPES:
        return v.json_error('Choose the module calendar or an additional meeting.', status=400)
    component_id = v.clean_str(payload.get('componentId'))
    if not component_id:
        return v.json_error('Choose the live session this is for.', status=400)

    v.ensure_module_authoring_tables()
    requested_id = v.clean_str(module_catalogue_id)
    resolved_id = v.resolve_stored_module_catalogue_id(requested_id) or requested_id
    if not resolved_id or not v.authoring_module_exists(resolved_id):
        return v.json_error('Module authoring structure not found.', status=404)
    rows = v.active_component_rows(v.authoring_fetch_all(
        v.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s and id = %s', [resolved_id, component_id],
    ))
    component_row = next((row for row in rows if v.frontend_component_type(row.get('type')) == 'live-session'), None)
    if component_row is None:
        return v.json_error('That live session is not part of this module. Reopen the module and choose again.', status=404)

    settings = component_extra_meeting_settings(component_row, v)
    title = v.clean_str(component_row.get('title')) or 'This live session'
    if existing_additional_meeting(settings, v) and scope != 'additional':
        return v.json_error(
            f'“{title}” already has an additional meeting. Cancel that meeting first; it will not be overwritten.',
            status=409, code='live_session_already_has_additional_meeting',
        )
    if booked_on_module_calendar(settings, v) and scope != 'main':
        return v.json_error(
            f'“{title}” is already booked on this module’s own Teams calendar. Cancel that session on the Module '
            'Teams calendar tab first, or choose another week.',
            status=409, code='live_session_already_booked',
        )

    from .teams_external_calendar import inherited_main_link
    try:
        with transaction.atomic():
            v.lock_module_structure_row(resolved_id)
            current = v.active_component_rows(v.authoring_fetch_all(
                v.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s and id = %s', [resolved_id, component_id],
            ))
            if not current or v.frontend_component_type(current[0].get('type')) != 'live-session':
                return v.json_error('The live session changed. Reload the module and choose again.', status=409)
            component_row = current[0]
            settings = component_extra_meeting_settings(component_row, v)
            if ((existing_additional_meeting(settings, v) and scope != 'additional')
                    or (booked_on_module_calendar(settings, v) and scope != 'main')):
                return v.json_error('This session was booked while you were choosing. Reload the module.', status=409)
            changed = {**settings, v.LIVE_SESSION_MEETING_SCOPE_KEY: scope}
            status = ''
            owner_id = v.teams_module_owner_id(resolved_id)
            series_rows = v.authoring_fetch_all(
                v.LIVE_SESSIONS_TABLE, "module_catalogue_id = %s and status = 'active'", [owner_id],
                'updated_at desc, created_at desc',
            ) if scope == 'main' else []
            series = series_rows[0] if series_rows else None
            if scope == 'main' and not booked_on_module_calendar(settings, v):
                peers = v.active_component_rows(v.authoring_fetch_all(
                    v.AUTHORING_COMPONENTS_TABLE, 'module_catalogue_id = %s', [resolved_id],
                ))
                link, overridden = inherited_main_link([
                    component_extra_meeting_settings(row, v) for row in peers
                    if v.frontend_component_type(row.get('type')) == 'live-session' and row.get('id') != component_id
                ], series)
                if link:
                    changed.update(liveSessionUrl=link, teamsMeetingUrl=link, teamsMainLinkInherited=True)
                    if overridden:
                        changed['liveSessionLinkOverride'] = link
                    else:
                        changed.pop('liveSessionLinkOverride', None)
                changed['teamsCalendarBinding'] = 'missing'
                status = 'pending'
            elif scope == 'additional' and settings.get('teamsMainLinkInherited'):
                for key in ('liveSessionUrl', 'teamsMeetingUrl', 'liveSessionLinkOverride',
                            'teamsCalendarBinding'):
                    changed.pop(key, None)
                # Retain a server-owned tombstone so an older builder tab
                # cannot restore the inherited main link on its next save.
                changed['teamsMainLinkInherited'] = False
            if changed != settings:
                v.update_authoring_rows(v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [component_id], {
                    'settings_json': v.json_db_value(changed),
                    'live_sessions_link': v.clean_str(changed.get('liveSessionUrl') or changed.get('teamsMeetingUrl')),
                    'updated_at': datetime.utcnow(),
                })
                v.invalidate_curriculum_cache()
            if (scope == 'main' and series and owner_id == resolved_id
                    and v.clean_str(series.get('meeting_type')) == 'linked-external'):
                occurrences = v.authoring_fetch_all(
                    v.LIVE_SESSION_OCCURRENCES_TABLE, 'live_session_id = %s', [series['id']], 'session_number',
                )
                v.attach_teams_meeting_to_module_weeks(
                    resolved_id, series, v.live_session_row_to_component_settings(series), occurrences,
                )
                saved = v.authoring_fetch_all(v.AUTHORING_COMPONENTS_TABLE, 'id = %s', [component_id])[0]
                status = v.clean_str(component_extra_meeting_settings(saved, v).get('teamsCalendarBinding')) or 'pending'
    except ValueError as exc:
        return v.json_error(str(exc), status=409)
    message = ''
    if scope == 'main':
        message = ('Linked to the existing calendar session on this week\'s date. No invitations were changed.'
                   if status == 'matched' else
                   'Module calendar selected. The meeting link is inherited where available, but this date still needs calendar confirmation. '
                   'For an imported meeting, the organizer must update the original Teams calendar; '
                   'new dates need to be imported before they can be confirmed in the LMS.')
    return JsonResponse({'componentId': component_id, 'scope': scope, 'calendarStatus': status, 'message': message})
