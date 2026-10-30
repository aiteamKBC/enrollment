"""Staff attendance choices bound to one authored activity/occurrence.

Uses the existing attendance-confirmation event. No Graph call or raw Teams
report write. A source-less authored lecture needs a staff-declared date.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from old_otjh.service import ServiceError

UK = ZoneInfo('Europe/London')
ATTENDED_SECONDS = 9000
EVENT = 'attendance_confirmation'


def metadata(value):
    value = json.loads(value) if isinstance(value, str) else value
    return value if isinstance(value, dict) else {}


def is_attendance(component):
    return str(component.get('type') or component.get('componentType') or '').lower().replace('-', '_') in {'live_session', 'attendance', 'lecture'}


def aware(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    return value.replace(tzinfo=ZoneInfo('UTC')) if value and value.tzinfo is None else value


def context(component, occurrences):
    settings = metadata(component.get('settings_json'))
    legacy = metadata(settings.get('legacySettings'))
    occurrence_id = str(settings.get('teamsOccurrenceId') or legacy.get('teamsOccurrenceId') or '')
    series_id = str(settings.get('teamsLiveSessionId') or '')
    day = str(settings.get('sessionDate') or legacy.get('sessionDate') or '')[:10]
    matches = [row for row in occurrences if
        (occurrence_id and str(row['id']) == occurrence_id) or
        (not occurrence_id and series_id and str(row['live_session_id']) == series_id
         and (not day or aware(row['scheduled_start']).astimezone(UK).date().isoformat() == day))]
    if len(matches) == 1:
        row = matches[0]
        day = aware(row['scheduled_start']).astimezone(UK).date().isoformat()
        return {'date': day, 'lectureId': f"teams:{row['id']}-{day}", 'sourceRef': f"attendance:{row['id']}",
                'occurrenceId': str(row['id']), 'endsAt': aware(row.get('scheduled_end'))}
    if occurrence_id or series_id:
        return {'date': None, 'unresolved': True}
    try:
        if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day: day = ''
    except ValueError:
        day = ''
    return {'date': day or None}


def occurrences(read, module_id, table):
    return read(f'''SELECT o.id,o.live_session_id,o.scheduled_start,o.scheduled_end
        FROM {table('live_session_occurrences','curriculum')} o
        JOIN {table('live_sessions','curriculum')} s ON s.id=o.live_session_id
        WHERE s.module_catalogue_id=%s
        AND lower(coalesce(s.status,'')) NOT IN ('cancelled','canceled','deleted','failed','superseded')
        AND lower(coalesce(o.status,'')) NOT IN ('cancelled','canceled','deleted','failed','superseded')''', [module_id])


def confirmation_rows(read, table, owner, lecture_id, lock=''):
    return read(f'''SELECT * FROM {table('learner_progress_entries')}
        WHERE learner_id=%s AND kind='activity_event' AND feed_kind=%s
        AND time_tracking_session_ref=%s AND deleted_at IS NULL ORDER BY id{lock}''', [owner['id'], EVENT, lecture_id])


def has_source_presence(read, table, owner, lecture):
    occurrence = lecture.get('occurrenceId')
    if not occurrence: return False
    return bool(read(f'''SELECT occurrence_id FROM {table('live_session_learner_attendance','curriculum')}
        WHERE occurrence_id=%s AND learner_profile_id=%s AND attendance_status='present'
        UNION ALL SELECT occurrence_id FROM {table('live_session_attendance','curriculum')}
        WHERE occurrence_id=%s AND lower(trim(email))=%s AND total_attendance_seconds>180''',
        [occurrence, owner['id'], occurrence, str(owner.get('email') or '').strip().lower()]))


def save_confirmation(read, table, owner, component, module, lecture, encode):
    existing = confirmation_rows(read, table, owner, lecture['lectureId'])
    if len(existing) > 1:
        raise ServiceError('Multiple attendance confirmations require review.', 'ambiguous_activity', 409)
    details = {**(metadata(existing[0].get('time_tracking_calculation')) if existing else {}),
        'sourceRef': lecture['sourceRef'], 'date': lecture['date'], 'title': component.get('title') or 'Lecture',
        'module': module.get('title') or '', 'moduleId': module['module_catalogue_id'], 'componentId': component['id'],
        'staffConfirmed': True, 'confirmationSource': 'module_progress'}
    if existing:
        read(f'''UPDATE {table('learner_progress_entries')}
            SET claimed_seconds=%s,time_tracking_calculation=%s,ssot_updated_at=CURRENT_TIMESTAMP
            WHERE id=%s AND learner_id=%s AND kind='activity_event' AND feed_kind=%s''',
            [ATTENDED_SECONDS, encode(details), existing[0]['id'], owner['id'], EVENT])
    else:
        # This event is attendance evidence, not another accepted-hour activity.
        # Accepted hours are stored once on the component's canonical allocation.
        read(f'''INSERT INTO {table('learner_progress_entries')}
            (learner_id,enrolment_id,entry_order,kind,component_type,module_title,component_title,
            feed_kind,feed_action,feed_title,time_tracking_source,time_tracking_session_ref,
            time_tracking_calculation,claimed_seconds,submitted_at,accepted,source_system)
            SELECT %s,%s,coalesce(max(entry_order),0)+1,'activity_event','live_session',%s,%s,
                %s,'attended',%s,%s,%s,%s,%s,CURRENT_TIMESTAMP,false,'staff_confirmation'
            FROM {table('learner_progress_entries')} WHERE learner_id=%s''',
            [owner['id'],owner['enrolment_id'],module.get('title') or '',component.get('title') or '',EVENT,
             component.get('title') or '',EVENT,lecture['lectureId'],encode(details),ATTENDED_SECONDS,owner['id']])
