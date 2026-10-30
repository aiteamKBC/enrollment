"""Scoped display overrides and explicitly declared Activity Log time.

No attempt, answer, marking, KSB or raw Teams report write belongs here.
The audit keeps the entire before row for recovery. GETs never provision schema.
"""
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from login.permissions import require_access, _accesses_of
from old_otjh.service import ServiceError
from learner_api.canonical_learning import counts_as_completed, input_activity_date
from learner_api.current_learning import source_payload_metadata
from . import schema_gate
from . import learner_activity_attendance as attendance

UK = ZoneInfo('Europe/London')
TABLES = ('learner_activity_display_overrides', 'learner_activity_display_audit')


def table(name, schema='Learner'):
    return f'"{schema}"."{name}"' if connection.vendor == 'postgresql' else f'"{name}"'


def query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        if cursor.description is None:
            return []
        keys = [col[0] for col in cursor.description]
        return [dict(zip(keys, row)) for row in cursor.fetchall()]


def encode(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'))


def digest(rows):
    return hashlib.sha256(encode(rows).encode()).hexdigest()


def lock():
    return ' FOR UPDATE' if connection.vendor == 'postgresql' else ''


def component_reference(row):
    return row.get('curriculum_component_ref') or row.get('component_ref')


def allowed(account, owner, module, grants=None):
    grants = _accesses_of(account) if grants is None else grants
    if grants & {'super-admin', 'curriculum'}:
        return True
    email = str(account.email or '').strip().casefold()
    return bool(email and (
        'coach' in grants and email == str(owner.get('coach_email') or '').strip().casefold()
        or 'tutor' in grants and email == str(module.get('tutor_email') or '').strip().casefold()
    ))


def recorded_hour_rows(records):
    """Read stored allocations or verified native time from the same ledger.

    Stored seconds, including zero, take priority. A native retry cannot add
    hours to a retained allocation or staff correction for the same activity.
    Keep the original rows untouched for optimistic locking and audit snapshots.
    """
    def activity_key(row):
        return (row.get('learner_id'), component_reference(row)
                or (f"quiz:{row['quiz_ref']}" if row.get('quiz_ref') else f"entry:{row['id']}"))

    allocated = {activity_key(row) for row in records if row.get('actual_seconds') is not None
                 and (row.get('accepted') is True or row.get('actual_basis') == 'staff:module-progress')}
    retained = {(row.get('learner_id'), source_payload_metadata(row.get('source_payload')).get('original_source_ref'))
                for row in records if row.get('source_system')}
    result, pending = [], {}
    for row in records:
        if (row.get('actual_seconds') is not None or row.get('source_system')
                or row.get('component_link_source') not in {'direct', 'quiz_ref'}
                or row.get('accepted') is not True or row.get('kind') == 'activity_event'
                or not row.get('submitted_at')):
            result.append(row)
            continue
        key = activity_key(row)
        if key in allocated or (row.get('learner_id'), f"progress:{row['id']}") in retained:
            continue
        projected = dict(row)
        try:
            seconds = float(row.get('verified_seconds'))
        except (TypeError, ValueError, OverflowError):
            seconds = None
        if seconds is not None and isfinite(seconds) and seconds >= 0:
            projected.update(actual_seconds=seconds, _hours_source='learner_progress_entries.verified_seconds')
        previous = pending.get(key)
        if previous is None or (projected.get('actual_seconds') is not None, projected.get('actual_seconds') or 0) > (
                previous.get('actual_seconds') is not None, previous.get('actual_seconds') or 0):
            pending[key] = projected
    return result + list(pending.values())


def facts(records, override=None, *, hour_rows=None):
    # Hours come only from persisted fields in learner_progress_entries.
    # Multiple durable allocations still require manual review before editing.
    current = recorded_hour_rows(records) if hour_rows is None else hour_rows
    original_by_id = {row['id']: row for row in records}
    eligible = [row for row in current if row.get('accepted') is True]
    selected = max(eligible or current, key=lambda row: (
        row.get('actual_seconds') is not None, row.get('actual_seconds') or 0,
        str(row.get('submitted_at') or ''), row['id'],
    ), default=None)
    override = override or {}
    original = original_by_id.get(selected['id'], {}) if selected else {}
    input_date = input_activity_date(original)
    declaration = source_payload_metadata(original.get('source_payload')).get('staff_time_entry') or {}
    mode = 'input' if input_date or original.get('reporting_timestamp_label') == 'Input' else 'timestamp'
    recorded_start = original.get('reporting_started_at')
    if isinstance(recorded_start, str):
        recorded_start = datetime.fromisoformat(recorded_start.replace('Z', '+00:00'))
    activity_date = input_date or (recorded_start.astimezone(UK).date().isoformat() if recorded_start else None)
    return {
        'revision': override.get('revision', 0), 'snapshotHash': digest(records),
        'displayStatus': override.get('display_status'),
        'statusSource': 'manual' if override else 'calculated',
        'actualOtjh': selected['actual_seconds'] / 3600 if selected and selected.get('actual_seconds') is not None else (0 if not records else None),
        # Missing allocations do not erase hours already persisted in the ledger.
        # Keep their count separate; never substitute claimed or planned time.
        'acceptedOtjh': sum((row.get('actual_seconds') or 0) / 3600 for row in eligible),
        'unallocatedAcceptedRecords': sum(row.get('actual_seconds') is None for row in eligible),
        'hoursSource': selected.get('_hours_source', 'learner_progress_entries.actual_seconds') if selected else 'learner_progress_entries.actual_seconds',
        'startedAt': original.get('reporting_started_at') if mode == 'timestamp' else None,
        'endedAt': original.get('reporting_ended_at') if mode == 'timestamp' else None,
        'timeEntryMode': mode, 'activityDate': activity_date,
        'attendanceMode': declaration.get('attendance_mode'),
        'lectureDate': declaration.get('lecture_date'),
        'progressId': selected['id'] if selected else None,
        'accepted': selected.get('accepted') if selected else None,
        'multipleRecordedAllocations': sum(row.get('actual_seconds') is not None for row in records) > 1,
        'calculatedStatus': 'completed' if any(counts_as_completed(row) for row in records)
        else 'in_progress' if records else 'not_started',
    }


def load_records(learner_ids, component_ids, module_id=None):
    # With module_id the same read also returns rows tagged with the module but
    # linked to no current component (an earlier version of its activities).
    if not learner_ids or not (component_ids or module_id):
        return []
    learner_slots = ','.join(['%s'] * len(learner_ids))
    component_slots = ','.join(['%s'] * len(component_ids))
    matches = [f'coalesce(curriculum_component_ref,component_ref) IN ({component_slots})'] if component_ids else []
    if module_id:
        matches.append('module_ref=%s')
    return query(f'''SELECT * FROM {table('learner_progress_entries')}
        WHERE learner_id IN ({learner_slots}) AND ({' OR '.join(matches)})
          AND deleted_at IS NULL ORDER BY id''', [*learner_ids, *component_ids, *([module_id] if module_id else [])])


def earlier_progress(rows, linked_keys, *, hour_rows=None):
    """Module-tagged rows for components the module no longer has.

    A row whose canonical activity is already recorded against a current
    component is the same work and is not counted twice.
    """
    rows = [row for row in rows if not (row.get('canonical_activity_key') and row['canonical_activity_key'] in linked_keys)]
    kept_ids = {row['id'] for row in rows}
    projected = recorded_hour_rows(rows) if hour_rows is None else hour_rows
    accepted = [row for row in projected if row['id'] in kept_ids and row.get('accepted') is True]
    return {
        'earlierRecords': len(rows),
        'earlierCompleted': sum(1 for row in rows if counts_as_completed(row)),
        'earlierOtjh': sum((row.get('actual_seconds') or 0) / 3600 for row in accepted),
        'unallocatedAcceptedRecords': sum(row.get('actual_seconds') is None for row in accepted),
    }


def enrich(payload, account):
    """Bounded bulk reads; no per-activity identity/permission queries."""
    schema_gate.require_tables(*TABLES)
    plan = payload['learnerActivityPlan']
    otjh_learners = payload.get('otjhAchievement', {}).get('learners', [])
    learner_ids = sorted({int(row['learnerId']) for row in plan}
                         | {int(row['learnerId']) for row in otjh_learners if str(row.get('learnerId')).isdigit()})
    component_ids = sorted({row['componentId'] for row in plan})
    current_components = set(component_ids)
    records = defaultdict(list)
    earlier_rows = defaultdict(list)
    loaded_records = load_records(learner_ids, component_ids, payload['identifier'])
    for row in loaded_records:
        if component_reference(row) in current_components:
            records[(str(row['learner_id']), component_reference(row))].append(row)
        else:
            earlier_rows[str(row['learner_id'])].append(row)
    hour_rows = defaultdict(list)
    earlier_hour_rows = defaultdict(list)
    # Project once across the module so retained references can suppress a
    # native original even when its imported copy has another component ID.
    for row in recorded_hour_rows(loaded_records):
        if component_reference(row) in current_components:
            hour_rows[(str(row['learner_id']), component_reference(row))].append(row)
        else:
            earlier_hour_rows[str(row['learner_id'])].append(row)
    overrides = {}
    if learner_ids:
        slots = ','.join(['%s'] * len(learner_ids))
        overrides = {(str(row['learner_id']), row['component_id']): row for row in query(f'''
            SELECT * FROM {table(TABLES[0], 'curriculum')}
            WHERE learner_id IN ({slots}) AND module_id=%s''', [*learner_ids, payload['identifier']])}
    # The existing assignment roster is the membership authority. Ownership is
    # verified again on POST; this capability is only a UI hint.
    owners = {str(row['id']): row for row in query(f'''SELECT id,coach_email FROM {table('learners')}
        WHERE id IN ({','.join(['%s'] * len(learner_ids))})''', learner_ids)} if learner_ids else {}
    from . import views
    modules = query(f'SELECT * FROM {table("modules", "curriculum")} WHERE module_catalogue_id=%s', [payload['identifier']])
    module = modules[0] if modules else {}
    attendance_components = {}
    occurrence_rows = []
    if any(attendance.is_attendance(row) for row in plan):
        attendance_components = {row['id']: row for row in query(f'''SELECT id,type,settings_json
            FROM {table('components', 'curriculum')} WHERE module_catalogue_id=%s
            AND type IN ('live_session','live-session','attendance','lecture')''', [payload['identifier']])}
        occurrence_rows = attendance.occurrences(query, payload['identifier'], table)
    grants = _accesses_of(account) if account else frozenset()
    for row in plan:
        key = (str(row['learnerId']), row['componentId'])
        state = facts(records[key], overrides.get(key), hour_rows=hour_rows[key])
        state['calculatedStatus'] = row.get('calculatedStatus') or state['calculatedStatus']
        row['logState'] = state
        row['calculatedStatus'] = state['calculatedStatus']
        row['displayStatus'] = state['displayStatus'] or state['calculatedStatus']
        row['statusSource'] = state['statusSource']
        row['canChangeStatus'] = bool(account and allowed(account, owners.get(key[0], {}), module, grants))
        if attendance.is_attendance(row):
            lecture = attendance.context(attendance_components.get(row['componentId'], {}), occurrence_rows)
            row['lectureDate'] = lecture.get('date') or state.get('lectureDate')
            if lecture.get('unresolved'):
                row['canChangeStatus'] = False
    payload['moduleProgressHoursSource'] = 'Activity Log · accepted learner_progress_entries.actual_seconds, otherwise verified_seconds for native saves (current components and module_ref)'
    totals = defaultdict(float)
    unallocated = defaultdict(int)
    completion = defaultdict(lambda: {'totalActivities': 0, 'completedActivities': 0, 'inProgressActivities': 0})
    linked_keys = defaultdict(set)
    for (learner, _component), rows in hour_rows.items():
        linked_keys[learner].update(row['canonical_activity_key'] for row in rows
                                   if row.get('canonical_activity_key') and row.get('accepted') is True
                                   and row.get('actual_seconds') is not None)
    for row in plan:
        learner = str(row['learnerId'])
        totals[learner] += row['logState']['acceptedOtjh']
        unallocated[learner] += row['logState']['unallocatedAcceptedRecords']
        counts = completion[learner]
        counts['totalActivities'] += 1
        if row['displayStatus'] == 'completed':
            counts['completedActivities'] += 1
        elif row['displayStatus'] == 'in_progress':
            counts['inProgressActivities'] += 1
    progress_rows = []
    for learner_id in learner_ids:
        learner = str(learner_id)
        earlier = earlier_progress(earlier_rows.get(learner, []), linked_keys[learner], hour_rows=earlier_hour_rows[learner])
        totals[learner] += earlier['earlierOtjh']
        unallocated[learner] += earlier['unallocatedAcceptedRecords']
        progress_rows.append({'learnerId': learner_id, **completion[learner], **earlier})
    payload['activityLogProgress'] = {
        'learners': progress_rows,
        'completedActivities': sum(row['completedActivities'] for row in progress_rows),
        'totalActivities': sum(row['totalActivities'] for row in progress_rows),
        'earlierRecords': sum(row['earlierRecords'] for row in progress_rows),
        'source': 'learner_progress_entries',
    }
    hours_rows = [{
        'learnerId': row['learnerId'], 'plannedOtjh': row['plannedOtjh'],
        'achievedOtjh': totals[str(row['learnerId'])],
        'unallocatedAcceptedRecords': unallocated[str(row['learnerId'])],
    } for row in payload.get('otjhAchievement', {}).get('learners', [])]
    payload['activityLogOtjh'] = {
        'learners': hours_rows,
        'achievedTotal': sum(row['achievedOtjh'] for row in hours_rows),
        'unallocatedAcceptedRecords': sum(row['unallocatedAcceptedRecords'] for row in hours_rows),
        'plannedTotal': sum(row['plannedOtjh'] for row in hours_rows),
        'source': payload['moduleProgressHoursSource'],
    }


def resolve(account, module_id, learner_id, component_id):
    from . import views
    learners = views.assigned_learners_for_scope('module', module_id, '')
    if not any(str(row['id']) == str(learner_id) for row in learners):
        raise ServiceError('Learner is not assigned to this module.', 'not_found', 404)
    owners = query(f'SELECT * FROM {table("learners")} WHERE id=%s', [learner_id])
    modules = query(f'SELECT * FROM {table("modules", "curriculum")} WHERE module_catalogue_id=%s', [module_id])
    components = query(f'SELECT * FROM {table("components", "curriculum")} WHERE id=%s AND module_catalogue_id=%s', [component_id, module_id])
    if not owners or not modules or not components or any(views.curriculum_row_effectively_deleted(row) for row in [modules[0], components[0]]):
        raise ServiceError('Activity not found.', 'not_found', 404)
    owner = owners[0]
    if not owner.get('enrolment_id'):
        raise ServiceError('Learner identity needs review.', 'identity_review_required', 409)
    from learner_api.canonical_learning import require_profile
    if require_profile(owner['enrolment_id'])['id'] != owner['id']:
        raise ServiceError('Learner identity needs review.', 'identity_review_required', 409)
    if not allowed(account, owner, modules[0]):
        raise ServiceError('You do not own this learner or module.', 'forbidden', 403)
    return owner, modules[0], components[0]


def interval(payload, owner):
    seconds = payload.get('durationSeconds')
    if type(seconds) is not int or not 0 < seconds <= 28800:
        raise ServiceError('Enter an OTH duration greater than zero and at most 8 hours.')
    mode = payload.get('timeEntryMode', 'timestamp')
    if mode not in {'input', 'timestamp'}:
        raise ServiceError('Choose Input or Timestamp.')
    if mode == 'input':
        if 'startedAt' in payload:
            raise ServiceError('Input accepts a date and duration only.')
        try:
            activity_date = date.fromisoformat(payload['activityDate'])
            if activity_date.isoformat() != payload['activityDate']:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ServiceError('A valid activity date is required.')
        validate_date(activity_date, owner)
        return seconds, None, None, activity_date.strftime('%Y-%m')
    try:
        start = datetime.fromisoformat(payload['startedAt'].replace('Z', '+00:00'))
        if start.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ServiceError('A valid start date and time with timezone is required.')
    end = start + timedelta(seconds=seconds)
    local, finish = start.astimezone(UK), end.astimezone(UK)
    if local.date() != finish.date() or local.weekday() > 4 or local.hour < 9 or finish.time().replace(tzinfo=None) > datetime.strptime('17:00', '%H:%M').time():
        raise ServiceError('Enter a weekday interval within 09:00–17:00 UK time.')
    validate_date(local.date(), owner)
    if end > datetime.now(timezone.utc):
        raise ServiceError('Completed activity time cannot be in the future.')
    return seconds, start, end, local.strftime('%Y-%m')


def validate_date(activity_date, owner):
    if activity_date.weekday() > 4:
        raise ServiceError('Enter a weekday activity date.')
    for field, too_early in (('start_date', True), ('end_date', False)):
        if owner.get(field):
            boundary = str(owner[field])[:10]
            if (too_early and activity_date.isoformat() < boundary) or (not too_early and activity_date.isoformat() > boundary):
                raise ServiceError('The activity must be within the learner programme dates.')
    if activity_date > datetime.now(UK).date():
        raise ServiceError('Completed activity date cannot be in the future.')


def require_open_month(owner, month):
    # Fail closed on missing signoff schema. Never invalidate a signature or
    # reopen a finalized month from this screen.
    if query(f'''SELECT report_month FROM {table('learner_monthly_locks')}
        WHERE learner_id=%s AND report_month=%s AND locked_at IS NOT NULL AND unlocked_at IS NULL''', [owner['id'], month]):
        raise ServiceError('This Activity Log month is locked.', 'month_locked', 409)
    if query(f'''SELECT report_month FROM {table('learner_monthly_signatures')}
        WHERE learner_id=%s AND report_month=%s AND review_confirmed IS TRUE''', [owner['id'], month]):
        raise ServiceError('This Activity Log month is signed.', 'month_signed', 409)
    if owner.get('aptem_id'):
        events = query(f'''SELECT event_type FROM {table('manual_month_finalization_events')}
            WHERE aptem_id=%s AND report_month=%s ORDER BY id DESC LIMIT 1''', [owner['aptem_id'], month])
        if events and events[0]['event_type'] == 'finalized':
            raise ServiceError('This Activity Log month is finalized.', 'month_finalized', 409)
        if query(f'''SELECT report_month FROM {table('monthly_audit_signoffs')}
            WHERE learner_id=%s AND report_month=%s AND review_confirmed IS TRUE''', [str(owner['aptem_id']), month]):
            raise ServiceError('This Activity Log month has an audit signature.', 'month_signed', 409)


def save(account, module_id, learner_id, component_id, payload):
    schema_gate.require_tables(*TABLES)
    status = payload.get('displayStatus')
    if status not in {'completed', 'not_started'}:
        raise ServiceError('Choose Completed or Not Started.')
    if type(payload.get('expectedRevision')) is not int or not isinstance(payload.get('snapshotHash'), str):
        raise ServiceError('Reload the activity before changing its status.', 'record_changed', 409)
    mode = payload.get('timeEntryMode', 'timestamp')
    reason = f'Staff set displayed status to Completed with {mode} time entry.' if status == 'completed' else 'Staff set displayed status to Not Started; activity hours and progress retained.'
    with transaction.atomic():
        owner, module, component = resolve(account, module_id, learner_id, component_id)
        # Shared with Monthly Log signing; protects an empty destination month.
        query(f'SELECT id FROM {table("Created_users", "enrolment")} WHERE id=%s{lock()}', [owner['enrolment_id']])
        query(f'SELECT id FROM {table("learners")} WHERE id=%s{lock()}', [learner_id])
        # Reread membership/ownership after the learner lock.
        owner, module, component = resolve(account, module_id, learner_id, component_id)
        is_attendance = attendance.is_attendance(component)
        if not is_attendance and 'attendanceMode' in payload:
            raise ServiceError('Attendance choices apply only to a lecture activity.')
        records = query(f'''SELECT * FROM {table('learner_progress_entries')}
            WHERE learner_id=%s AND coalesce(curriculum_component_ref,component_ref)=%s AND deleted_at IS NULL ORDER BY id{lock()}''', [learner_id, component_id])
        rows = query(f'''SELECT * FROM {table(TABLES[0], 'curriculum')}
            WHERE learner_id=%s AND module_id=%s AND component_id=%s{lock()}''', [learner_id, module_id, component_id])
        override = rows[0] if rows else None
        state = facts(records, override)
        if state['revision'] != payload['expectedRevision'] or state['snapshotHash'] != payload['snapshotHash']:
            raise ServiceError('Activity changed. Reload before saving.', 'record_changed', 409)
        before = {'override': override, 'progress': records}
        lecture = None
        if status == 'completed':
            if is_attendance:
                choice = payload.get('attendanceMode')
                if choice not in {'attended', 'recording'}:
                    raise ServiceError('Choose Attended or Watched recording.')
                lecture = attendance.context(component, attendance.occurrences(query, module_id, table))
                if lecture.get('unresolved'):
                    raise ServiceError('The lecture occurrence needs review.', 'source_review_required', 409)
                if not lecture.get('date'):
                    previous_dates = {source_payload_metadata(row.get('source_payload')).get('staff_time_entry', {}).get('lecture_date') for row in records}
                    previous_dates.discard(None)
                    if len(previous_dates) > 1:
                        raise ServiceError('The original lecture date needs review.', 'ambiguous_activity', 409)
                    lecture['date'] = next(iter(previous_dates), None)
                if lecture.get('endsAt') and lecture['endsAt'] > datetime.now(timezone.utc):
                    raise ServiceError('Attendance or recording can be confirmed only after the lecture has ended.')
                if choice == 'attended':
                    lecture_date = lecture.get('date') or payload.get('activityDate')
                    if lecture.get('date') and payload.get('activityDate') != lecture['date']:
                        raise ServiceError('Attendance must use the original lecture date.')
                    payload = {**payload, 'durationSeconds': attendance.ATTENDED_SECONDS,
                               'timeEntryMode': 'input', 'activityDate': lecture_date}
                    payload.pop('startedAt', None)
                    mode = 'input'
                    reason = 'Staff confirmed Attended; 2.5 lecture hours credited.'
                else:
                    reason = f'Staff confirmed Watched recording with {mode} time entry.'
            seconds, start, end, month = interval(payload, owner)
            activity_date = start.astimezone(UK).date().isoformat() if start else payload['activityDate']
            timestamp_label = 'attended' if is_attendance and choice == 'attended' else 'Input' if mode == 'input' else 'Scheduled'
            declaration = {'mode': mode, 'activity_date': activity_date}
            if is_attendance:
                lecture['date'] = lecture.get('date') or activity_date
                lecture.setdefault('lectureId', f'component:{component_id}:{lecture["date"]}')
                lecture.setdefault('sourceRef', f'staff-component:{component_id}:{lecture["date"]}')
                confirmations = attendance.confirmation_rows(query, table, owner, lecture['lectureId'], lock())
                before['attendanceConfirmations'] = confirmations
                if len(confirmations) > 1:
                    raise ServiceError('Multiple attendance confirmations require review.', 'ambiguous_activity', 409)
                if choice == 'recording' and (confirmations or attendance.has_source_presence(query, table, owner, lecture)
                        or any(source_payload_metadata(row.get('source_payload')).get('staff_time_entry', {}).get('attendance_mode') == 'attended' for row in records)):
                    raise ServiceError('Attendance is already credited. Recording hours would count the lecture twice.', 'already_attended', 409)
                if choice == 'recording' and activity_date < lecture['date']:
                    raise ServiceError('Recording learning cannot precede the original lecture date.')
                # A second component or imported allocation must not claim the
                # same occurrence. Staff confirmations are evidence only.
                other_records = query(f'''SELECT id,source_payload,time_tracking_session_ref FROM {table('learner_progress_entries')}
                    WHERE learner_id=%s AND deleted_at IS NULL AND actual_seconds>0 AND accepted IS TRUE
                    AND coalesce(curriculum_component_ref,component_ref) IS DISTINCT FROM %s''', [learner_id, component_id])
                if any(row.get('time_tracking_session_ref') == lecture['lectureId'] or
                       source_payload_metadata(row.get('source_payload')).get('original_source_ref') == lecture['sourceRef'] or
                       source_payload_metadata(row.get('source_payload')).get('staff_time_entry', {}).get('lecture_id') == lecture['lectureId']
                       for row in other_records):
                    raise ServiceError('This lecture has another Activity Log allocation; review it before crediting hours.', 'duplicate_attendance', 409)
                declaration.update(attendance_mode=choice, lecture_id=lecture['lectureId'], lecture_date=lecture['date'])
            require_open_month(owner, month)
            from learner_api.working_hours_holidays import component_holiday_ranges
            if any(row['start'] <= activity_date <= row['end'] for row in component_holiday_ranges(component_id)):
                raise ServiceError('The selected date is a holiday.')
            selected = next((row for row in records if row['id'] == state['progressId']), None)
            if any(row.get('component_ref') and row.get('curriculum_component_ref') and row['component_ref'] != row['curriculum_component_ref'] for row in records):
                raise ServiceError('The component identity needs an Activity Log review.', 'ambiguous_activity', 409)
            if selected and selected.get('reporting_month') and selected['reporting_month'] != month:
                raise ServiceError('Changing the Activity Log month requires a separate review.', 'month_change', 409)
            durable = [row for row in records if row.get('actual_seconds') is not None and row.get('accepted') is True]
            if len(durable) > 1:
                raise ServiceError('Multiple recorded allocations require a separate Activity Log review.', 'ambiguous_activity', 409)
            overlap = query(f'''SELECT id FROM {table('learner_progress_entries')}
                WHERE learner_id=%s AND deleted_at IS NULL AND coalesce(curriculum_component_ref,component_ref) IS DISTINCT FROM %s
                AND reporting_started_at < %s AND reporting_ended_at > %s LIMIT 1''', [learner_id, component_id, end, start]) if start else []
            if overlap:
                raise ServiceError('This time overlaps another recorded activity.', 'time_overlap', 409)
            if selected:
                original_payload = selected.get('source_payload')
                if isinstance(original_payload, str):
                    original_payload = json.loads(original_payload)
                if original_payload is not None and not isinstance(original_payload, dict):
                    raise ServiceError('The source metadata needs an Activity Log review.', 'source_review_required', 409)
                source_payload = {**(original_payload or {}), 'staff_time_entry': declaration}
                query(f'''UPDATE {table('learner_progress_entries')}
                    SET actual_seconds=%s,actual_basis='staff:module-progress',
                        reporting_started_at=%s,reporting_ended_at=%s,reporting_month=%s,
                        reporting_timestamp_label=%s,source_payload=%s,accepted=CASE WHEN %s THEN true ELSE accepted END,
                        ssot_updated_at=CURRENT_TIMESTAMP
                    WHERE id=%s AND learner_id=%s AND coalesce(curriculum_component_ref,component_ref)=%s AND deleted_at IS NULL''',
                    [seconds, start, end, month, timestamp_label, encode(source_payload), is_attendance, selected['id'], learner_id, component_id])
            else:
                # Staff-declared hours are labelled, not a quiz attempt/mark or
                # manufactured evidence. No KSB links or reward hooks are fired.
                query(f'''INSERT INTO {table('learner_progress_entries')}
                    (learner_id,enrolment_id,entry_order,kind,module_ref,module_title,week_ref,
                    component_ref,component_title,component_type,expected_otjh,actual_seconds,
                    actual_basis,reporting_started_at,reporting_ended_at,reporting_month,
                    reporting_timestamp_label,source_system,canonical_activity_key,component_link_source,
                    component_link_status,accepted,source_payload)
                    SELECT %s,%s,coalesce(max(entry_order),0)+1,'staff_hours',%s,%s,%s,%s,%s,%s,%s,%s,
                        'staff:module-progress',%s,%s,%s,%s,'staff',%s,'direct','resolved_to_current_component',true,%s
                    FROM {table('learner_progress_entries')} WHERE learner_id=%s''',
                    [learner_id, owner['enrolment_id'], module_id, module.get('title') or '', component.get('week_id'), component_id,
                     component.get('title') or '', component['type'], component.get('expected_otjh'), seconds, start, end, month,
                     timestamp_label, f'staff:{learner_id}:{module_id}:{component_id}', encode({'staff_time_entry': declaration}), learner_id])
            if is_attendance and choice == 'attended':
                attendance.save_confirmation(query, table, owner, component, module, lecture, encode)
        elif any(key in payload for key in ('durationSeconds', 'startedAt', 'activityDate', 'timeEntryMode', 'attendanceMode')):
            raise ServiceError('Not Started changes display status only.')
        revision = state['revision'] + 1
        query(f'''INSERT INTO {table(TABLES[0], 'curriculum')}
            (learner_id,enrolment_id,module_id,component_id,display_status,revision,actor_id,reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (learner_id,module_id,component_id) DO UPDATE SET
                display_status=excluded.display_status,revision=excluded.revision,actor_id=excluded.actor_id,
                reason=excluded.reason,updated_at=CURRENT_TIMESTAMP''',
            [learner_id, owner['enrolment_id'], module_id, component_id, status, revision, account.id, reason])
        after_records = load_records([learner_id], [component_id])
        after_override = query(f'''SELECT * FROM {table(TABLES[0], 'curriculum')}
            WHERE learner_id=%s AND module_id=%s AND component_id=%s''', [learner_id, module_id, component_id])[0]
        after = {'override': after_override, 'progress': after_records}
        if lecture:
            after['attendanceConfirmations'] = attendance.confirmation_rows(query, table, owner, lecture['lectureId'])
        query(f'''INSERT INTO {table(TABLES[1], 'curriculum')}
            (learner_id,enrolment_id,module_id,component_id,revision,actor_id,reason,before_snapshot,after_snapshot)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            [learner_id, owner['enrolment_id'], module_id, component_id, revision, account.id, reason,
             encode(before), encode(after)])
        result = facts(after_records, after_override)
        from . import views
        views.invalidate_curriculum_cache()
    return result


@require_access('curriculum', 'coach', 'tutor')
@require_http_methods(['GET', 'POST'])
def activity_status(request, module_id, learner_id, component_id):
    try:
        if request.method == 'GET':
            schema_gate.require_tables(*TABLES)
            resolve(request.login_account, module_id, learner_id, component_id)
            changes = query(f'''SELECT revision,actor_id,reason,created_at,after_snapshot
                FROM {table(TABLES[1], 'curriculum')}
                WHERE learner_id=%s AND module_id=%s AND component_id=%s
                ORDER BY revision DESC LIMIT 50''', [learner_id, module_id, component_id])
            history = []
            for change in changes:
                snapshot = change.pop('after_snapshot')
                if isinstance(snapshot, str): snapshot = json.loads(snapshot)
                history.append({**change, 'displayStatus': snapshot['override']['display_status']})
            return JsonResponse({'history': history})
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError()
        return JsonResponse({'state': save(request.login_account, module_id, learner_id, component_id, payload)})
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid request.'}, status=400)
    except ServiceError as exc:
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)
    except schema_gate.SchemaNotProvisioned as exc:
        return JsonResponse({'error': str(exc), 'code': 'schema_not_provisioned'}, status=503)
    except DatabaseError:
        return JsonResponse({'error': 'Activity storage is unavailable. No changes were saved.', 'code': 'storage_unavailable'}, status=503)
