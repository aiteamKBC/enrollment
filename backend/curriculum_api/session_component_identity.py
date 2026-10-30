"""Conservative links between authored live-session cards and Teams occurrences."""

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from django.db import connections, transaction


TIMEZONE_ALIASES = {
    'GMT Standard Time': 'Europe/London',
    'UTC': 'UTC',
    'W. Europe Standard Time': 'Europe/Berlin',
    'Romance Standard Time': 'Europe/Paris',
    'Egypt Standard Time': 'Africa/Cairo',
    'Arabian Standard Time': 'Asia/Dubai',
}


def _text(value):
    return '' if value is None else str(value).strip()


def occurrence_fallbacks(rows):
    """Return only unique (module, local date) occurrence identities.

    This is also used by the learner projection. Never infer a meeting from
    session order: moved and cancelled lectures can leave gaps in numbering.
    """
    candidates, ambiguous = {}, set()
    for module_id, series_id, windows_zone, occurrence_id, session_number, scheduled_start in rows:
        try:
            start = scheduled_start
            if not isinstance(start, datetime):
                start = datetime.fromisoformat(_text(start).replace('Z', '+00:00'))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            zone_name = TIMEZONE_ALIASES.get(_text(windows_zone), _text(windows_zone) or 'Europe/London')
            local_date = start.astimezone(ZoneInfo(zone_name)).date().isoformat()
            number = int(session_number or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not (_text(module_id) and _text(series_id) and _text(occurrence_id) and number > 0):
            continue
        key = (_text(module_id), local_date)
        value = {
            'teamsLiveSessionId': _text(series_id),
            'teamsSessionNumber': number,
            'teamsOccurrenceId': _text(occurrence_id),
        }
        if key in candidates and candidates[key] != value:
            ambiguous.add(key)
        else:
            candidates[key] = value
    return {key: value for key, value in candidates.items() if key not in ambiguous}


def _settings(value):
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or '{}')
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _instant(value):
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(_text(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _eligible(settings, live_link, identity, meeting):
    if str(settings.get('teamsMeetingScope') or '') not in ('', 'primary'):
        return False
    if any(str(settings.get(key) or '').strip() for key in
           ('extraTeamsLiveSessionId', 'extraTeamsMeetingUrl', 'liveSessionLinkOverride')):
        return False
    for key in ('teamsLiveSessionId', 'teamsOccurrenceId'):
        if str(settings.get(key) or '').strip() not in ('', str(identity[key])):
            return False
    try:
        stored_number = int(settings.get('teamsSessionNumber') or 0)
    except (TypeError, ValueError):
        return False
    if stored_number not in (0, identity['teamsSessionNumber']):
        return False
    for key, valid in (('teamsOnlineMeetingId', meeting['online_ids']),
                       ('teamsEventId', meeting['event_ids'])):
        stored = str(settings.get(key) or '').strip()
        if stored and stored not in valid:
            return False
    for key in ('sessionDateTimeUtc', 'teamsStartDateTimeUtc'):
        if settings.get(key) and _instant(settings[key]) != _instant(meeting['scheduled_start']):
            return False
    links = [str(value).strip() for value in
             (live_link, settings.get('liveSessionUrl'), settings.get('teamsMeetingUrl')) if value]
    if not links or not meeting['join_urls'] or any(link not in meeting['join_urls'] for link in links):
        return False
    return any(not settings.get(key) for key in identity)


def reconcile_component_links(series_id):
    """Fill missing identity fields on unambiguous, already booked components.

    Runs after Graph discovery. It never creates a component, changes a meeting
    URL or moves a session. Row locks prevent an authoring save racing this pass.
    """
    with transaction.atomic():
        with connections['default'].cursor() as cursor:
            cursor.execute('''SELECT module_catalogue_id FROM curriculum.live_sessions
                              WHERE id=%s AND status NOT IN ('deleted','superseded','failed')''', [series_id])
            series = cursor.fetchone()
            if not series:
                return 0
            module_id = series[0]
            cursor.execute('''SELECT s.module_catalogue_id,s.id,s.timezone,o.id,o.session_number,
                                     o.scheduled_start,o.join_url,s.join_url,
                                     o.online_meeting_id,s.online_meeting_id,o.graph_event_id,s.graph_event_id
                              FROM curriculum.live_sessions s
                              JOIN curriculum.live_session_occurrences o ON o.live_session_id=s.id
                              JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
                              LEFT JOIN curriculum.programmes p ON p.programme_id=m.programme_id
                              WHERE s.module_catalogue_id=%s AND s.status NOT IN ('deleted','superseded','failed')
                                AND o.status NOT IN ('cancelled','deleted','superseded')
                                AND m.deleted_at IS NULL AND NOT COALESCE(m.is_programme_deleted,false)
                                AND LOWER(COALESCE(p.status,'')) NOT IN ('archived','deleted')''', [module_id])
            occurrence_rows = cursor.fetchall()
            identities = occurrence_fallbacks(row[:6] for row in occurrence_rows)
            meetings = {}
            for row in occurrence_rows:
                meetings[str(row[3])] = {
                    'scheduled_start': row[5],
                    'join_urls': {str(value).strip() for value in row[6:8] if value},
                    'online_ids': {str(value).strip() for value in row[8:10] if value},
                    'event_ids': {str(value).strip() for value in row[10:12] if value},
                }
            cursor.execute('''SELECT c.id,c.settings_json,c.live_sessions_link
                              FROM curriculum.components c
                              JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
                              WHERE c.module_catalogue_id=%s AND c.type IN ('live-session','live_session')
                                AND c.deleted_at IS NULL AND NOT COALESCE(c.is_programme_deleted,false)
                                AND COALESCE(c.library_state,'')<>'library'
                                AND w.deleted_at IS NULL AND NOT COALESCE(w.is_programme_deleted,false)
                                AND COALESCE(w.library_state,'')<>'library'
                              FOR UPDATE OF c''', [module_id])
            components = [(component_id, _settings(settings), link)
                          for component_id, settings, link in cursor.fetchall()]
            dates = Counter(str(settings.get('sessionDate') or '')[:10]
                            for _, settings, _ in components)
            changed = 0
            for component_id, settings, link in components:
                local_date = str(settings.get('sessionDate') or '')[:10]
                identity = identities.get((str(module_id), local_date))
                if dates[local_date] != 1 or not identity or identity['teamsLiveSessionId'] != str(series_id):
                    continue
                meeting = meetings.get(identity['teamsOccurrenceId'])
                if not meeting or not _eligible(settings, link, identity, meeting):
                    continue
                cursor.execute('''UPDATE curriculum.components SET
                                    settings_json=COALESCE(settings_json,'{}'::jsonb) || %s::jsonb,
                                    updated_at=now()
                                  WHERE id=%s AND module_catalogue_id=%s''',
                               [json.dumps(identity), component_id, module_id])
                changed += cursor.rowcount
    if changed:
        from curriculum_api.views import invalidate_curriculum_cache
        invalidate_curriculum_cache()
        logging.getLogger(__name__).info('Linked %s live-session components to saved Teams occurrences', changed)
    return changed
