"""Live lecture workspace. Reads source records; never imports or provisions them."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from html import unescape
import logging
import re

from . import journal_sources
from .projection_performance import projection_call
from django.db import DatabaseError, connections
from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.http import require_GET

from audit_api.last_audit_ledger_views import _is_completed
from login.permissions import learner_self_or_staff
from old_otjh.repository import ksb_codes
from .attendance import combined_attendance_rows, _summarize_attendance
from .attendance_rules import attendance_outcome
from .dashboard_metrics import as_json
from .learner_detail import SOURCE_MODELS
from .progress_rules import progress_record_counts_as_achieved as progress_counts_as_achieved
from .student_activity import _direct_progress_records
from .student_activity_access import student_activity_available
from .training_plan_dashboard import rows as dict_rows
from .replaced_teams_attendance import REPLACED_ATTENDANCE_LATERAL_SQL
from .attendance_assignments import assigned_module_ids as effective_assigned_module_ids

log = logging.getLogger(__name__)
ATTENDANCE_SOURCE_FIELDS = ('id', 'username', 'email', 'aptem_id', 'employer_id', 'learner_type',
                            'programme', 'group', 'training_plan', 'learning_plan')

# The effective plan includes group inheritance and respects explicit/empty
# selections, exactly as My Learning. A stale normalized mirror is not a
# second assignment authority. Historical attendance is read separately.
ATTENDANCE_SUBJECTS_SQL = '''
    SELECT DISTINCT cm.module_catalogue_id,cm.title
    FROM curriculum.modules cm WHERE cm.module_catalogue_id=ANY(%s)
      AND (cm.deleted_at IS NULL OR COALESCE(cm.deleted_via_parent, '') <> '')
'''


def _text(value):
    return str(value or '').strip()


def _key(value):
    return re.sub(r'\s+', ' ', _text(value)).casefold()


def _module_key(value, *, without_level=False):
    value = re.sub(r'[\u200b-\u200d\ufeff]', '', unescape(_text(value))).casefold()
    value = re.sub(r'[^\w]+', ' ', value).strip()
    if without_level:
        value = re.sub(r'^level\s+\d+\s+', '', value)
    return value


def _legacy_module_activities(row, meta, activities):
    if meta.get('group_id') is not None:
        return [a for a in activities if str(a['group_id']) == str(meta['group_id'])]
    # Titles vary in punctuation and a leading qualification level between KBC
    # and Last_audit. Resolve only one of this learner's enrolled groups.
    for without_level in (False, True):
        key = _module_key(row.get('module_title'), without_level=without_level)
        if not key:
            return []
        matches = [a for a in activities if _module_key(a['group_name'], without_level=without_level) == key]
        groups = {a['group_id'] for a in matches}
        if groups:
            return matches if len(groups) == 1 else []
    return []


def _activity_ksbs(activities):
    return ksb_codes([code for activity in activities for code in ksb_codes(activity.get('ksbs'))])


def _component_ksbs(component):
    return ksb_codes(component.get('ksb_mappings')) or ksb_codes(component.get('mapped_ksbs'))


def _fill_ksbs(lecture, linked, module_activities, module_codes=None):
    """Keep session mappings first; identify a broader module fallback honestly."""
    for scope, activities in (('activities', linked), ('module', module_activities)):
        codes = module_codes if scope == 'module' and module_codes is not None else _activity_ksbs(activities)
        if codes:
            lecture.update(ksbs=codes, ksbScope=scope)
            return


def _aware(value):
    # Historical native tables use timestamp without time zone, written by
    # Django on a UTC connection. PostgreSQL timestamptz rows are already aware.
    return timezone.make_aware(value, datetime_timezone.utc) if value and timezone.is_naive(value) else value


def session_key(row):
    """Keep existing KBC keys compatible; namespace native occurrences."""
    raw = _text(row.get('session_id'))
    return raw if row.get('source', 'kbc-attendance') in {'kbc-attendance', 'coaching-meeting'} else f'teams:{raw}'


def report_id(row):
    from .absence_reports import _kbc_attendance_report_id
    # KBC source keys already include learner and date; Teams occurrence IDs do not.
    key = session_key(row)
    if row.get('source') == 'microsoft-teams':
        key = f"{row['learner_id']}:{key}"
    return _kbc_attendance_report_id(key)


# An ended Teams lecture stays 'unmarked' until its attendance report syncs; the
# learner can still say they missed it.
REPORTABLE_ABSENCE_STATUSES = frozenset({'absent', 'unmarked', 'upcoming', 'in_progress'})


def can_report_absence_status(status, session_date):
    """One rule for the lecture list's Report Absence and the absence form/submit."""
    status = _text(status).lower()
    if status in REPORTABLE_ABSENCE_STATUSES:
        return True
    day = session_date.isoformat() if hasattr(session_date, 'isoformat') else _text(session_date)
    return status == 'pending' and day == timezone.localdate().isoformat()


NATIVE_OCCURRENCES_SQL = f'''SELECT o.id AS session_id,o.id AS occurrence_id,
            COALESCE(replaced_attendance.occurrence_id,o.id) AS attendance_source_occurrence_id,
            s.id AS live_session_id,s.module_catalogue_id,m.title AS module_title,
            o.scheduled_start,o.scheduled_end,o.updated_at,o.session_number,
            s.status AS live_session_status,o.status AS occurrence_status,
            COALESCE(NULLIF(o.join_url,''),s.join_url) AS join_url,
            CASE WHEN replaced_attendance.occurrence_id IS NOT NULL
                 THEN replaced_attendance.attendance_report_id ELSE o.attendance_report_id END AS attendance_report_id,
            coalesce(nullif(m.tutor_name,''),s.organizer_email) AS tutor_name,
            CASE WHEN coalesce(CASE WHEN replaced_attendance.occurrence_id IS NOT NULL
                                    THEN replaced_attendance.attendance_report_id ELSE o.attendance_report_id END,'')=''
                 THEN false
                 ELSE EXISTS(SELECT 1 FROM curriculum.live_session_attendance a
                     WHERE a.occurrence_id=COALESCE(replaced_attendance.occurrence_id,o.id)
                       AND lower(btrim(a.email))=%s
                       AND a.total_attendance_seconds>180) END AS attended
            FROM curriculum.live_session_occurrences o
            JOIN curriculum.live_sessions s ON s.id=o.live_session_id
            JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
            {REPLACED_ATTENDANCE_LATERAL_SQL}
            WHERE s.module_catalogue_id=ANY(%s)
              AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
              AND (lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
                   AND lower(btrim(o.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
                   OR lower(btrim(o.status)) IN ('cancelled','canceled'))
            ORDER BY o.scheduled_start,o.id'''

# A module duplicated to share another module's Teams meeting deliberately has no
# live_sessions row of its own (curriculum_api.views.teams_module_owner_id); its
# learners attend the source's meeting. Follow the same short chain to its owner.
SHARED_TEAMS_SOURCES_SQL = '''WITH RECURSIVE shared(module_id,source_id,depth) AS (
            SELECT m.module_catalogue_id,btrim(m.teams_shared_source_module_id),1
            FROM curriculum.modules m
            WHERE m.module_catalogue_id=ANY(%s) AND coalesce(btrim(m.teams_shared_source_module_id),'')<>''
            UNION ALL
            SELECT shared.module_id,btrim(m.teams_shared_source_module_id),shared.depth+1
            FROM shared JOIN curriculum.modules m ON m.module_catalogue_id=shared.source_id
            WHERE coalesce(btrim(m.teams_shared_source_module_id),'')<>'' AND shared.depth<5
        )
        SELECT DISTINCT module_id,source_id FROM shared'''


def _shared_native_occurrences_sql():
    shared_select = '\n        SELECT DISTINCT module_id,source_id FROM shared'
    module_filter = 'WHERE s.module_catalogue_id=ANY(%s)'
    assert SHARED_TEAMS_SOURCES_SQL.endswith(shared_select) and NATIVE_OCCURRENCES_SQL.count(module_filter) == 1
    native = NATIVE_OCCURRENCES_SQL.replace(module_filter,
        'WHERE (s.module_catalogue_id=ANY(%s) OR s.module_catalogue_id IN (SELECT source_id FROM shared))')
    return (SHARED_TEAMS_SOURCES_SQL[:-len(shared_select)] + ', native AS (' + native + """),
        targets AS (
            SELECT module_catalogue_id AS module_id,module_catalogue_id AS source_id
            FROM curriculum.modules WHERE module_catalogue_id=ANY(%s)
            UNION SELECT module_id,source_id FROM shared
        )
        SELECT native.*,targets.module_id AS target_module_id,m.title AS target_module_title
        FROM native JOIN targets ON targets.source_id=native.module_catalogue_id
        JOIN curriculum.modules m ON m.module_catalogue_id=targets.module_id
        ORDER BY native.scheduled_start,native.session_id,targets.module_id""")


# Shared source traversal, occurrence evidence and assigned-module titles in one read.
SHARED_NATIVE_OCCURRENCES_SQL = _shared_native_occurrences_sql()


def shared_teams_sources(module_ids, *, cursor=None):
    """{assigned shared duplicate: the modules whose Teams meeting it attends}."""
    module_ids = sorted({_text(value) for value in module_ids} - {''})
    if not module_ids:
        return {}
    if cursor is None:
        with connections['enrolment'].cursor() as cursor:
            return shared_teams_sources(module_ids, cursor=cursor)
    try:
        cursor.execute(SHARED_TEAMS_SOURCES_SQL, [module_ids])
        rows = cursor.fetchall()
    except DatabaseError:
        # The learner's own modules still load; shared meetings return once readable.
        log.warning('Could not read shared Teams modules for %s.', module_ids, exc_info=True)
        return {}
    result = {}
    for module_id, source_id in rows:
        result.setdefault(module_id, set()).add(source_id)
    return result


def read_native_occurrences(source, *, module_ids=None):
    """Current assigned-module occurrences expected for, or attended by, the learner.

    Current module assignment is authoritative for the learner's schedule.  A
    saved Teams invite list may be stale after assignment changes, so it must
    not hide a module session.  Completed attendance evidence is merged below.
    """
    if not source.email:
        return []
    with connections['enrolment'].cursor() as cur:
        if module_ids is None:
            # An old Teams invitation is not a current curriculum assignment.
            # Use the same effective plan as My Learning and Training Plan, then exclude
            # deleted delivery modules and replaced meeting series below.
            cur.execute(ATTENDANCE_SUBJECTS_SQL, [effective_assigned_module_ids(source)])
            module_ids = [row[0] for row in cur.fetchall()]
        else:
            module_ids = list(dict.fromkeys(str(value or '').strip() for value in module_ids))
            module_ids = [value for value in module_ids if value]
        if not module_ids:
            return []
        assigned_module_ids = list(module_ids)
        try:
            cur.execute(SHARED_NATIVE_OCCURRENCES_SQL, [
                sorted({_text(value) for value in module_ids} - {''}), _key(source.email),
                module_ids, assigned_module_ids])
            source_rows = dict_rows(cur)
            projections = [(row, row.get('target_module_id') or row.get('module_catalogue_id'),
                            row.get('target_module_title') or row.get('module_title')) for row in source_rows]
        except DatabaseError:
            # Preserve the separate-read fallback when the combined lookup is unavailable.
            shared = shared_teams_sources(assigned_module_ids, cursor=cur)
            source_module_ids = sorted(set().union(*shared.values())) if shared else []
            query_module_ids = list(dict.fromkeys([*assigned_module_ids, *source_module_ids]))
            cur.execute(NATIVE_OCCURRENCES_SQL, [_key(source.email), query_module_ids])
            source_rows = dict_rows(cur)
            cur.execute('''SELECT module_catalogue_id,title FROM curriculum.modules
                WHERE module_catalogue_id=ANY(%s)''', [assigned_module_ids])
            module_titles = {row[0]: row[1] for row in cur.fetchall()}
            aliases_by_source = {}
            for alias_id, source_ids in shared.items():
                for source_id in source_ids:
                    aliases_by_source.setdefault(source_id, []).append(alias_id)
            projections = []
            for row in source_rows:
                source_id = row.get('module_catalogue_id')
                targets = ([source_id] if source_id in assigned_module_ids else [])
                targets += aliases_by_source.get(source_id, [])
                projections.extend((row, target, module_titles.get(target) or row.get('module_title'))
                                   for target in dict.fromkeys(targets))
    result = []
    for row, target_module_id, title in projections:
        projected = {key: value for key, value in row.items()
                     if key not in {'target_module_id', 'target_module_title'}}
        projected.update(module_catalogue_id=target_module_id, module_title=title)
        if target_module_id != row.get('module_catalogue_id'):
            projected['attendance_source_module_id'] = row.get('module_catalogue_id')
            evidence_id = str(row.get('attendance_source_occurrence_id') or row.get('session_id') or '')
            projected['attendance_source_occurrence_id'] = evidence_id
            projected['session_id'] = f'{evidence_id}:{target_module_id}'
            projected['occurrence_id'] = projected['session_id']
        result.append(projected)
    return normalize_native_occurrences(source, result)


def read_cancelled_native_occurrences(source, *, module_ids=None):
    """Read cancellation tombstones used to suppress stale legacy mirrors.

    Cancelled occurrences are intentionally absent from ``read_native_occurrences``
    so they do not appear in the learner schedule.  A legacy KBC row can still
    survive that omission, though, and would otherwise be counted as an
    independent absence.  This narrow read supplies only the identity facts
    needed to reconcile that mirror; it never exposes cancelled sessions.
    """
    if not getattr(source, 'email', None):
        return []
    with connections['enrolment'].cursor() as cur:
        if module_ids is None:
            cur.execute(ATTENDANCE_SUBJECTS_SQL, [effective_assigned_module_ids(source)])
            module_ids = [row[0] for row in cur.fetchall()]
        module_ids = [str(value or '').strip() for value in module_ids if str(value or '').strip()]
        if not module_ids:
            return []
        cur.execute('''
            SELECT o.id AS occurrence_id, s.module_catalogue_id,
                   m.title AS module_title, o.scheduled_start,
                   o.session_number
            FROM curriculum.live_session_occurrences o
            JOIN curriculum.live_sessions s ON s.id=o.live_session_id
            JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
            WHERE s.module_catalogue_id=ANY(%s)
              AND m.deleted_at IS NULL
              AND (lower(btrim(coalesce(s.status,''))) IN
                       ('cancelled','canceled','deleted','failed','superseded')
                   OR lower(btrim(coalesce(o.status,''))) IN
                       ('cancelled','canceled','deleted','failed','superseded'))
            ORDER BY o.scheduled_start,o.id
        ''', [module_ids])
        rows = dict_rows(cur)
    result = []
    for row in rows:
        start = _aware(row.get('scheduled_start'))
        if not start:
            continue
        result.append({
            'occurrence_id': str(row.get('occurrence_id') or ''),
            'module_catalogue_id': row.get('module_catalogue_id'),
            'module_title': row.get('module_title') or '',
            'session_date': timezone.localtime(start).date(),
            'session_number': row.get('session_number'),
        })
    return result


def _kbc_session_number(value):
    match = re.search(r'\b(?:lecture|lec|session|l)\s*#?\s*(\d+)\b', _text(value), re.I)
    return int(match.group(1)) if match else None


def _exclude_cancelled_kbc_mirrors(records, scheduled, cancelled):
    """Drop only an unambiguous KBC mirror of a cancelled live occurrence."""
    if not cancelled:
        return records
    active = [row for row in scheduled if row.get('source') == 'microsoft-teams'
              and str(row.get('attendance_status') or '').strip().lower() not in {'cancelled', 'canceled'}
              and str(row.get('occurrence_status') or '').strip().lower() not in {'cancelled', 'canceled'}
              and str(row.get('live_session_status') or '').strip().lower() not in {'cancelled', 'canceled'}]
    cancelled_by_day = {}
    for row in cancelled:
        cancelled_by_day.setdefault((_module_key(row.get('module_title')), row.get('session_date')), []).append(row)
    active_by_day = {}
    for row in active:
        active_by_day.setdefault((_module_key(row.get('module_title')), row.get('session_date')), []).append(row)
    excluded = []
    for row in records:
        if row.get('source') != 'kbc-attendance':
            excluded.append(row)
            continue
        key = (_module_key(row.get('module_title')), row.get('session_date'))
        candidates = cancelled_by_day.get(key, [])
        if not candidates or active_by_day.get(key):
            excluded.append(row)
            continue
        number = _kbc_session_number(row.get('session_title'))
        if number is not None:
            candidates = [item for item in candidates if item.get('session_number') == number]
        if len(candidates) != 1:
            excluded.append(row)
    return excluded


def normalize_native_occurrences(source, result):
    now = timezone.now()
    for row in result:
        row['scheduled_start'] = _aware(row['scheduled_start'])
        row['scheduled_end'] = _aware(row['scheduled_end'])
        row['updated_at'] = _aware(row['updated_at'])
        start = timezone.localtime(row['scheduled_start'])
        end = timezone.localtime(row['scheduled_end'])
        cancelled = str(row.get('live_session_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
                    str(row.get('occurrence_status') or '').strip().lower() in {'cancelled', 'canceled'}
        row.update(
            learner_id=source.id, learner_name=source.username or '', learner_email=source.email,
            source='microsoft-teams', session_type='live_session',
            session_title=f"{row['module_title'] or 'Live session'} — Session {row['session_number']}",
            session_date=start.date(), session_start_time=start.time(), session_end_time=end.time(),
            attendance_status='cancelled' if cancelled else
                              'present' if row.get('attended') else
                              'upcoming' if row['scheduled_start'] > now else
                              'in_progress' if row['scheduled_end'] > now else
                              'absent' if row.get('attendance_report_id') else 'pending',
            minutes_late=0, catchup_completed=False,
        )
    return result


def lecture_register(source, *, module_ids=None, records=None, learner_profile_id=None, apply_adjustments=True, preloaded=None, scheduled=None, credit_projection=False):
    # Bulk consumers can preload source rows; all schedule, recovery and saved
    # confirmation rules still run through this same learner register.
    loaded_schedule = scheduled is None
    if credit_projection and records is None:
        if scheduled is None:
            scheduled = projection_call('attendance_schedule', read_native_occurrences, source, module_ids=module_ids) if preloaded is None else preloaded['scheduled']
        records = projection_call('attendance_sources', combined_attendance_rows, source, credit_projection=True, register_schedule=scheduled)
    else:
        records = combined_attendance_rows(source) if records is None else records
    if scheduled is None:
        scheduled = projection_call('attendance_schedule', read_native_occurrences, source, module_ids=module_ids) if preloaded is None else preloaded['scheduled']
    if loaded_schedule and preloaded is None and any(row.get('source') == 'kbc-attendance' for row in records):
        cancelled = projection_call('attendance_schedule', read_cancelled_native_occurrences, source, module_ids=module_ids)
        records = _exclude_cancelled_kbc_mirrors(records, scheduled, cancelled)
    by_occurrence = {str(row['session_id']): row for row in scheduled}
    by_attendance_source = {}
    for row in scheduled:
        evidence_id = str(row.get('attendance_source_occurrence_id') or row['session_id'])
        if evidence_id != str(row['session_id']):
            by_attendance_source.setdefault(evidence_id, []).append(str(row['session_id']))
    now = timezone.now()
    result = []
    attended_alternatives = set()
    # A saved Bulk Attendance result is an explicit learner-register outcome.
    # Process it before raw Teams evidence so it wins for the same occurrence.
    records = sorted(records, key=lambda row: 0 if row.get('source') == 'live-register' else 1)
    for row in records:
        if row.get('source') in {'microsoft-teams', 'live-register'}:
            occurrence_id = str(row.get('occurrence_id') or row['session_id'])
            target_ids = ([occurrence_id] if occurrence_id in by_occurrence else []) + by_attendance_source.get(occurrence_id, [])
            target_ids = list(dict.fromkeys(target_ids))
            if row.get('source') == 'live-register':
                # A saved outcome belongs to its delivery. Raw evidence may
                # feed several modules, but one module's correction may not.
                target_ids = [target for target in target_ids
                              if by_occurrence.get(target, {}).get('module_catalogue_id')
                              == row.get('module_catalogue_id')]
            if not target_ids:
                # Another group's occurrence joined as an approved alternative
                # recovers the original absence; it is not a lecture of its own.
                if row.get('eligibility_reason') == 'approved_recovery_guest' and row['attendance_status'] == 'present':
                    attended_alternatives.add(str(row['session_id']))
                # Evidence for an occurrence outside the current schedule does
                # not create a second lecture row.
                continue
            for target_id in target_ids:
                schedule = by_occurrence.pop(target_id, None)
                if schedule is None:
                    continue
                if row.get('source') == 'live-register':
                    # Keep Teams/report metadata from the scheduled row while
                    # replacing only the learner outcome saved by Bulk.
                    projected = {
                        **schedule,
                        'attendance_status': row['attendance_status'],
                        'source': 'microsoft-teams',
                        'updated_at': _aware(row.get('updated_at')) or schedule.get('updated_at'),
                    }
                    if not projected.get('attendance_report_id') and row.get('attendance_report_id'):
                        projected['attendance_report_id'] = row['attendance_report_id']
                elif schedule['scheduled_end'] > now:
                    projected = schedule
                else:
                    projected = {**schedule, **row}
                    for key in ('session_id', 'occurrence_id', 'live_session_id', 'module_catalogue_id',
                                'module_title', 'session_number', 'join_url', 'scheduled_start', 'scheduled_end',
                                'session_date', 'session_start_time', 'session_end_time'):
                        if key in schedule:
                            projected[key] = schedule[key]
                result.append({**projected, 'updated_at': _aware(projected.get('updated_at'))})
            continue
        elif row['session_date'] > timezone.localdate() or (
                row['session_date'] == timezone.localdate() and row['attendance_status'] == 'absent'):
            row = {**row, 'attendance_status': 'upcoming'}
        result.append({**row, 'updated_at': _aware(row.get('updated_at'))})
    result.extend(by_occurrence.values())
    from .shared_teams_attendance import apply_shared_attendance
    result = projection_call('shared_series', apply_shared_attendance, result, source)
    # Ended sessions remain unmarked until evidence or a saved correction exists.
    result = [_ended_unmarked_status(row, now) for row in result]
    if attended_alternatives:
        result = projection_call('attendance_recovery', _apply_attended_alternatives, result, source.id, attended_alternatives, **({} if preloaded is None else {'targets': preloaded['targets']}))
    from .parallel_attendance import apply_parallel_attendance
    result = projection_call('attendance_parallel', apply_parallel_attendance, result, source, records=records)
    result = projection_call('attendance_catchups', _apply_completed_catchups, result, source.id, **({} if preloaded is None else {'completed': preloaded['completed']}))
    from .attendance_confirmation import apply_confirmations, read_confirmations
    merged = _merge_register_duplicates(apply_confirmations(result, (projection_call('attendance_confirmations', read_confirmations, source.id) if preloaded is None else preloaded['confirmations'])))
    from .recorded_attendance import apply_recorded_attendance
    merged = projection_call('attendance_recorded', apply_recorded_attendance, merged, source)
    if not apply_adjustments:
        return merged
    if learner_profile_id is None and credit_projection:
        profile = getattr(source, '_case_file_profile', None)
        owner = getattr(source, '_canonical_profile', None)
        if (profile is not None and owner is not None
                and str(owner['id']) == str(profile.pk)
                and str(profile.enrolment_id) == str(source.id)):
            learner_profile_id = profile.pk
    if learner_profile_id is None:
        from .models import LearnerProfile
        profile_ids = list(LearnerProfile.objects.filter(enrolment_id=source.id).values_list('id', flat=True))
        if len(profile_ids) > 1:
            raise ValueError('Attendance requires an unambiguous learner profile.')
        learner_profile_id = profile_ids[0] if profile_ids else None
    return projection_call('attendance_corrections', _apply_coach_source_adjustments, merged, learner_profile_id, **({} if preloaded is None else {'adjustments': preloaded['adjustments']})) if learner_profile_id is not None else merged


def _ended_unmarked_status(row, now):
    """Use the shared backend rule without losing the source status."""
    if str(row.get('attendance_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
            str(row.get('occurrence_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
            str(row.get('live_session_status') or '').strip().lower() in {'cancelled', 'canceled'}:
        return {**row, 'attendance_status': 'cancelled'}
    status, _ = attendance_outcome(row, now)
    if status == 'unmarked':
        return {**row, 'raw_attendance_status': row.get('raw_attendance_status') or row.get('attendance_status'),
                'attendance_status': 'unmarked'}
    return row


def attendance_read_contract(source, *, learner_profile_id, register=None, manual_rows=None, compact=False):
    """Authoritative coach read; profile identity never substitutes for enrolment."""
    from coach_api.models import CoachManualAttendance, CoachAbsenceReport
    rows = lecture_register(source, learner_profile_id=learner_profile_id) if register is None else register
    native_ids = {row['module_catalogue_id'] for row in rows
                  if row.get('source') == 'microsoft-teams' and row.get('module_catalogue_id')}
    components, _ = read_native_components(source, native_ids) if native_ids and not compact else ([], [])
    by_module = {}
    for component in components:
        by_module.setdefault(component['module_catalogue_id'], []).append(component)
    reports = {str(report.attendance_id): report for report in
               (CoachAbsenceReport.objects.filter(learner_id=source.id).order_by('created_at', 'id') if not compact else [])}
    history = []
    now = timezone.now()
    for row in rows:
        status = row.get('effective_attendance_status') or row['attendance_status']
        cancelled = str(row.get('attendance_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
                    str(row.get('occurrence_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
                    str(row.get('live_session_status') or '').strip().lower() in {'cancelled', 'canceled'}
        outcome, counted = ('cancelled', False) if cancelled else attendance_outcome(row, now)
        if cancelled:
            status = 'cancelled'
        start = _aware(row.get('scheduled_start'))
        report = reports.get(str(report_id(row)))
        component = _component_for(row, by_module.get(row.get('module_catalogue_id'), []))
        history.append({
            'learnerId': str(learner_profile_id), 'learner_profile_id': learner_profile_id,
            'enrolment_id': source.id, 'sessionId': session_key(row),
            'sourceId': str(row['session_id']), 'source': row.get('source', 'kbc-attendance'),
            'module': row.get('module_title') or '', 'sessionTitle': (row.get('session_title') if row.get('title_adjusted') else (component or {}).get('title')) or row.get('session_title') or '',
            'sessionType': row.get('session_type') or '', 'sessionDate': row['session_date'].isoformat(),
            'sessionDateLabel': row['session_date'].strftime('%d %b %Y'),
            'occurrenceStart': start.isoformat() if start else None,
            'startTime': row['session_start_time'].strftime('%H:%M') if row.get('session_start_time') else '',
            'rawStatus': 'cancelled' if cancelled else (row.get('raw_attendance_status') or row['attendance_status']),
            'effectiveStatus': status, 'status': outcome, 'counted': counted,
            'legacyAmbiguity': row.get('legacy_ambiguity', []),
            'absenceReport': {'id': str(report.id), 'status': report.status,
                              'url': getattr(report, 'evidence_image_url', '') or None} if report else None,
        })
    for row in (CoachManualAttendance.objects.filter(learner_id=learner_profile_id) if manual_rows is None else manual_rows):
        outcome, counted = attendance_outcome({'attendance_status': row.status, 'session_date': row.session_date}, now)
        history.append({
            'learnerId': str(learner_profile_id), 'learner_profile_id': learner_profile_id,
            'enrolment_id': source.id, 'sessionId': f'manual-{row.id}', 'manualId': str(row.id),
            'source': 'coach-manual', 'module': row.module_name, 'sessionType': 'manual',
            'sessionTitle': row.session_title, 'sessionDate': row.session_date.isoformat(),
            'sessionDateLabel': row.session_date.strftime('%d %b %Y'), 'occurrenceStart': None,
            'rawStatus': row.status, 'effectiveStatus': row.status, 'status': outcome,
            'counted': counted,
            'absenceReport': None,
        })
    def occurrence_order(row):
        start = row.get('occurrenceStart')
        instant = datetime.fromisoformat(start) if start else timezone.make_aware(
            datetime.fromisoformat(f"{row['sessionDate']}T{row.get('startTime') or '00:00'}"))
        return instant.timestamp(), row['sessionId']
    history.sort(key=occurrence_order, reverse=True)
    counted = [row for row in history if row['counted']]
    present = sum(row['status'] == 'present' for row in counted)
    absent = len(counted) - present
    summary = {'present': present, 'absent': absent, 'total': len(counted),
               'totalCounted': len(counted), 'sessions': len(counted), 'unknown': 0,
               'attendanceRate': round(100 * present / len(counted)) if counted else None}
    return {'learner_profile_id': learner_profile_id, 'enrolment_id': source.id,
            'summary': summary, 'history': history, 'recentAttendance': counted[:4]}


def _apply_coach_source_adjustments(rows, learner_profile_id, *, adjustments=None):
    """Apply persisted per-learner coach edits without changing the meeting."""
    from coach_api.models import CoachAttendanceSourceAdjustment

    try:
        adjustments = adjustments if adjustments is not None else {
            (item.source, item.source_id): item
            for item in CoachAttendanceSourceAdjustment.objects.filter(learner_id=learner_profile_id)
        }
    except DatabaseError:
        log.warning('Could not read coach attendance adjustments for profile %s.', learner_profile_id, exc_info=True)
        return rows

    result = []
    for row in rows:
        key = (str(row.get('source') or ''), str(row.get('session_id') or ''))
        adjustment = adjustments.get(key)
        if adjustment is None:
            result.append(row)
            continue
        if adjustment.is_deleted:
            continue
        updated = dict(row)
        updated['raw_attendance_status'] = row.get('raw_attendance_status') or row['attendance_status']
        if adjustment.session_date:
            updated['session_date'] = adjustment.session_date
        if adjustment.module_name:
            updated['module_title'] = adjustment.module_name
        if adjustment.session_title:
            updated['session_title'] = adjustment.session_title
            updated['title_adjusted'] = True
        if adjustment.status:
            updated.pop('attendance_basis', None)
            updated['attendance_status'] = adjustment.status
            updated['effective_attendance'] = 1 if adjustment.status == 'present' else 0
            updated['effective_attendance_status'] = adjustment.status
            updated['final_outcome'] = adjustment.status
        updated['updated_at'] = adjustment.updated_at
        result.append(updated)
    return result


def _approved_alternative_targets(learner_id):
    """Approved alternative-session reports as {absence report id: target occurrence}."""
    from coach_api.models import CoachAbsenceReport
    from .alternative_recovery import ALTERNATIVE_METHOD, alternative_occurrence_id
    reports = CoachAbsenceReport.objects.filter(
        learner_id=learner_id, status=CoachAbsenceReport.STATUS_APPROVED, recovery_method=ALTERNATIVE_METHOD,
    ).values_list('attendance_id', 'catchup_event_key')
    return {str(attendance_id): alternative_occurrence_id(key) for attendance_id, key in reports}


def _apply_attended_alternatives(rows, learner_id, attended_occurrences, *, targets=None):
    """Mark a missed Teams lecture made up once its approved alternative was attended.

    Attendance at the alternative comes from the verified Teams report (more
    than three minutes). The original absence keeps its raw Teams status.
    """
    targets = _approved_alternative_targets(learner_id) if targets is None else targets
    made_up = {'catchup_completed': True, 'excused': True, 'effective_attendance': 1,
               'effective_attendance_status': 'made_up', 'final_outcome': 'made_up'}
    return [
        {**row, **made_up}
        if row.get('source') == 'microsoft-teams' and row.get('attendance_status') in {'absent', 'unmarked'}
        and targets.get(str(report_id(row))) in attended_occurrences
        else row
        for row in rows
    ]


def _completed_catchup_occurrences(learner_id, occurrence_ids):
    """Occurrences whose absence ledger records an attended (completed) catch-up."""
    from curriculum_api.models import LiveSessionAbsence
    return set(LiveSessionAbsence.objects.filter(
        source_learner_id=learner_id, occurrence_id__in=list(occurrence_ids),
        recovery_method='catch-up', recovery_status=LiveSessionAbsence.RECOVERY_COMPLETED,
    ).values_list('occurrence_id', flat=True))


def _apply_completed_catchups(rows, learner_id, *, completed=None):
    """Credit a missed Teams lecture that has no attendance report of its own.

    Unmarked lectures can also have independently verified recovery evidence. The absence ledger still records
    a catch-up the learner attended (recovery_status completed).
    """
    unreported = {
        str(row.get('session_id')) for row in rows
        if row.get('source') == 'microsoft-teams' and row.get('attendance_status') in {'absent', 'unmarked'}
        and row.get('effective_attendance') is None
    }
    if not unreported:
        return rows
    try:
        completed = _completed_catchup_occurrences(learner_id, unreported) if completed is None else completed
    except DatabaseError:
        # The register still loads; these lectures show as missed until the ledger is readable.
        log.warning('Could not read completed catch-ups for learner %s.', learner_id, exc_info=True)
        return rows
    made_up = {'catchup_completed': True, 'excused': True, 'effective_attendance': 1,
               'effective_attendance_status': 'made_up', 'final_outcome': 'made_up'}
    return [
        {**row, **made_up} if str(row.get('session_id')) in completed and str(row.get('session_id')) in unreported
        else row
        for row in rows
    ]


def _merge_register_duplicates(rows):
    """Suppress only a deterministic legacy mirror; occurrences remain authoritative."""
    # Source evidence may contain stale dates or multiple representations of
    # one occurrence. Collapse explicit identities before legacy matching and
    # before coach corrections; an adjustment must override the single row.
    unique = {}
    for row in rows:
        occurrence = row.get('occurrence_id')
        source = row.get('source') or 'kbc-attendance'
        key = (row.get('learner_id'),
               'microsoft-teams' if occurrence else source,
               str(occurrence or row['session_id']))
        previous = unique.get(key)
        if previous is None or (source == 'microsoft-teams' and previous.get('source') != 'microsoft-teams'):
            unique[key] = row
    rows = list(unique.values())
    teams = {}
    for row in rows:
        if row.get('source') == 'microsoft-teams':
            teams.setdefault((row['session_date'], _module_key(row.get('module_title'))), []).append(row)
    candidates = {}
    for index, row in enumerate(rows):
        if row.get('source') != 'kbc-attendance':
            continue
        key = _module_key(row.get('module_title'))
        matches = teams.get((row['session_date'], key), []) if key else []
        if row.get('session_start_time'):
            matches = [target for target in matches if target.get('session_start_time') == row['session_start_time']]
        candidates[index] = matches
    merged = []
    for index, original in enumerate(rows):
        row = dict(original)
        matches = candidates.get(index, [])
        if len(matches) == 1:
            target_id = session_key(matches[0])
            competing = sum(any(session_key(target) == target_id for target in options) for options in candidates.values())
            if competing == 1:
                continue
        if matches:
            row['legacy_ambiguity'] = [session_key(target) for target in matches]
        merged.append(row)
    return merged


def read_legacy_metadata(source, register):
    if not student_activity_available(source.aptem_id):
        return {}, []
    aptem = int(str(source.aptem_id).strip())
    keys = [row['session_id'] for row in register if row.get('source') == 'kbc-attendance']
    if not keys:
        return {}, []
    with connections['enrolment'].cursor() as cur:
        # The register remains authoritative for attendance/date/title. Last_audit
        # and the audit editor supply details only, joined by the exact source key.
        cur.execute(f'''SELECT la.source_key,la.activity_hours,
            j.ksbs,r.activity_time,r.completion_note,r.group_id,r.month AS log_month,
            greatest(la.synced_at,r.updated_at,j.updated_at) AS updated_at
            FROM "Last_audit".learner_attendance la
            LEFT JOIN {journal_sources.table('manual_learner_activities')} r
              ON r.aptem_id=la.aptem_id AND r.source_ref='att:' || la.source_key
             AND r.category='attendance' AND r.deleted_at IS NULL
            LEFT JOIN {journal_sources.table('learner_journal_row_ksbs')} j
              ON j.aptem_id=r.aptem_id AND j.row_id=r.id
            WHERE la.aptem_id=%s AND la.source_key=ANY(%s)''', [aptem, keys])
        metadata_rows = dict_rows(cur)
        metadata = {}
        for row in metadata_rows:
            # Ambiguous audit links cannot choose another row's KSBs arbitrarily.
            key = row['source_key']
            metadata[key] = row if key not in metadata else None
        # Resolve against every enrolled non-empty group first, preserving the
        # ambiguity rule, then fetch materials only for this register's modules.
        cur.execute('''SELECT DISTINCT gl.group_id,g.group_name
            FROM "Last_audit".learners l
            JOIN "Last_audit".group_learners gl ON gl.learner_id=l.learner_id
            JOIN "Last_audit".groups g ON g.group_id=gl.group_id
            JOIN "Last_audit".group_activities ga ON ga.group_id=gl.group_id
            JOIN "Last_audit".activities a ON a.activity_id=ga.activity_id
            WHERE l.aptem_id=%s''', [aptem])
        groups = dict_rows(cur)
        group_ids = sorted({group['group_id'] for row in register
                            if row.get('source') == 'kbc-attendance'
                            for group in _legacy_module_activities(row, metadata.get(row['session_id']) or {}, groups)})
        if not group_ids:
            return metadata, []
        cur.execute(f'''SELECT gl.group_id,g.group_name,a.activity_id,a.title,a.activity_date,
            a.activity_type,a.configured_duration_min,a.quiz_id,a.reading_type,
            CASE WHEN nullif(a.reading_iframe_url,'') IS NOT NULL THEN 'present' ELSE '' END AS reading_iframe_url,
            CASE WHEN jsonb_typeof(a.quiz_questions)='array' AND a.quiz_questions<>'[]'::jsonb
                 THEN '[{{}}]'::jsonb ELSE '[]'::jsonb END AS quiz_questions,
            r.status,r.video_completed,
            r.reading_viewed,r.quiz_passed,r.updated_at,
            coalesce(CASE WHEN lk.source_preference='learner' THEN lk.ksbs ELSE ak.ksbs END,
                     a.raw #> '{{live_lms_component,ksbs}}') AS ksbs,
            EXISTS (SELECT 1 FROM "Learner".subject_activity_attempts p
              WHERE p.enrolment_id=%s AND p.aptem_id=l.aptem_id AND p.group_id=gl.group_id
                AND p.activity_id=a.activity_id AND p.completed=true
                AND coalesce(p.definition->>'catalogue_kind','material')='material') AS new_completed
            FROM "Last_audit".learners l
            JOIN "Last_audit".group_learners gl ON gl.learner_id=l.learner_id
            JOIN "Last_audit".groups g ON g.group_id=gl.group_id
            JOIN "Last_audit".group_activities ga ON ga.group_id=gl.group_id
            JOIN "Last_audit".activities a ON a.activity_id=ga.activity_id
            LEFT JOIN "Last_audit".activity_results r ON r.learner_id=l.learner_id
              AND r.group_id=gl.group_id AND r.activity_id=a.activity_id
            LEFT JOIN {journal_sources.table('learner_activity_ksbs')} lk
              ON lk.aptem_id=l.aptem_id AND lk.activity_id=a.activity_id
            LEFT JOIN {journal_sources.table('activity_ksbs')} ak ON ak.activity_id=a.activity_id
            WHERE l.aptem_id=%s AND gl.group_id=ANY(%s)''', [source.id, aptem, group_ids])
        activities = dict_rows(cur)
    return metadata, activities


def read_native_components(source, module_refs=None, *, monthly=False):
    # Only modules represented by a Teams lecture need activity bundles. Keep
    # direct progress for Recent Activity even on a historical-only register.
    if module_refs is not None and not module_refs:
        return [], [] if monthly else _direct_progress_records(source.id)
    with connections['enrolment'].cursor() as cur:
        cur.execute(ATTENDANCE_SUBJECTS_SQL, [effective_assigned_module_ids(source)])
        modules = [row[0] for row in cur.fetchall() if module_refs is None or row[0] in module_refs]
        description = "''::text" if monthly else 'c.description'
        cur.execute(f'''SELECT c.id,c.module_catalogue_id,c.week_id,c.type,c.title,{description} AS description,
            jsonb_build_object(
                'teamsOccurrenceId',c.settings_json->'teamsOccurrenceId',
                'teamsLiveSessionId',c.settings_json->'teamsLiveSessionId',
                'sessionDate',c.settings_json->'sessionDate',
                'sessionPurpose',c.settings_json->'sessionPurpose',
                'legacySettings',CASE WHEN jsonb_typeof(c.settings_json->'legacySettings')='object'
                    THEN jsonb_build_object('teamsOccurrenceId',c.settings_json #> '{{legacySettings,teamsOccurrenceId}}')
                    ELSE c.settings_json->'legacySettings' END
            ) AS settings_json,c.expected_otjh,{"'[]'::jsonb" if monthly else 'c.ksb_mappings'} AS ksb_mappings,
            {"NULL::jsonb" if monthly else '(SELECT jsonb_agg(k.ksb_code ORDER BY k.ksb_code) FROM curriculum.ksb_mappings k WHERE k.component_id=c.id AND k.deleted_at IS NULL)'} AS mapped_ksbs
            FROM curriculum.components c
            LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            WHERE c.module_catalogue_id=ANY(%s) AND c.deleted_at IS NULL
              AND NOT c.is_programme_deleted AND (w.id IS NULL OR
                (w.deleted_at IS NULL AND NOT w.is_programme_deleted))
            ORDER BY c.display_order,c.id''', [modules])
        components = dict_rows(cur)
    progress = [] if monthly else _direct_progress_records(source.id)
    completed = {str(row.get('componentId')) for row in progress if progress_counts_as_achieved(row)}
    for component in components:
        settings = as_json(component['settings_json'], {})
        component['settings'] = settings if isinstance(settings, dict) else {}
        component['completed'] = str(component['id']) in completed
    return components, progress


def read_assigned_modules(source):
    """Return the learner's current assigned catalogue modules.

    Attendance rows are historical/source data and may not exist yet for every
    module on the learner's training plan. The module selector still needs to
    expose those assignments so the learner can select an empty module and see
    its zero/upcoming state.
    """
    with connections['enrolment'].cursor() as cur:
        cur.execute(ATTENDANCE_SUBJECTS_SQL, [effective_assigned_module_ids(source)])
        return [{'id': row[0], 'title': row[1]} for row in cur.fetchall()]


def _component_for(row, components):
    candidates = []
    for component in components:
        if component['type'] != 'live_session' or component['module_catalogue_id'] != row.get('module_catalogue_id'):
            continue
        settings = component['settings']
        legacy = as_json(settings.get('legacySettings'), {})
        legacy = legacy if isinstance(legacy, dict) else {}
        occurrence = settings.get('teamsOccurrenceId') or legacy.get('teamsOccurrenceId')
        if occurrence:
            if occurrence == row['session_id']:
                candidates.append(component)
        elif settings.get('teamsLiveSessionId') == row.get('live_session_id'):
            # Recurring meetings may have one component per occurrence. Require
            # the authored date as well, so completing week one cannot cover ten.
            if settings.get('sessionDate') == row['session_date'].isoformat():
                candidates.append(component)
    return candidates[0] if len(candidates) == 1 else None


# Every live session counts as a two-hour lecture, the same rule the audit uses
# (audit_api.views.LIVE_SESSION_HOURS), whatever its schedule or source.
LIVE_SESSION_MINUTES = 120


def build_lectures(register, legacy_meta, legacy_activities, components, kind, learner_id):
    lectures = []
    # Resolve a module and its KSB fallback once per request, instead of
    # rescanning thousands of activities for every lecture in that module.
    legacy_modules = {}
    native_modules = {}
    module_ksbs = {}
    for component in components:
        native_modules.setdefault(component['module_catalogue_id'], []).append(
            {**component, 'ksbs': _component_ksbs(component)})
    for row in register:
        source = row.get('source', 'kbc-attendance')
        # Monthly Logs groups Teams attendance by the stored scheduled timestamp.
        # Its UTC month can differ from the local date displayed in Attendance.
        log_date = (row.get('scheduled_start') or row['session_date']) if source == 'microsoft-teams' else row['session_date']
        module_id = f"native:{row['module_catalogue_id']}" if source == 'microsoft-teams' else f"legacy:{_key(row.get('module_title'))}"
        cancelled = str(row.get('attendance_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
                    str(row.get('occurrence_status') or '').strip().lower() in {'cancelled', 'canceled'} or \
                    str(row.get('live_session_status') or '').strip().lower() in {'cancelled', 'canceled'}
        outcome, counted = ('cancelled', False) if cancelled else attendance_outcome(row)
        lecture = {
            'id': f"{session_key(row)}-{row['session_date'].isoformat()}",
            'sessionId': session_key(row), 'reportId': str(report_id(row)),
            'monthlyLog': {'month': log_date.isoformat()[:7],
                           'sourceRef': f"attendance:{row['session_id']}" if source == 'microsoft-teams' else f"att:{row['session_id']}"},
            'date': row['session_date'].isoformat(), 'title': row.get('session_title') or 'Lecture',
            'moduleId': module_id, 'module': row.get('module_title') or 'Other lectures',
            'source': source, 'startTime': row['session_start_time'].strftime('%H:%M') if row.get('session_start_time') else '',
            'endTime': row['session_end_time'].strftime('%H:%M') if row.get('session_end_time') else '',
            'durationMinutes': LIVE_SESSION_MINUTES, 'contentSummary': '', 'ksbs': [], 'ksbScope': None, 'activities': [],
            'startsAt': _aware(row['scheduled_start']).isoformat() if row.get('scheduled_start') else None,
            'endsAt': _aware(row['scheduled_end']).isoformat() if row.get('scheduled_end') else None,
            'joinUrl': (f"/learner_api/session-results/{kind}/{learner_id}/{row['live_session_id']}/sessions/{row['session_number']}/join/" if source == 'microsoft-teams' and row.get('live_session_id') and row.get('session_number') else _text(row.get('join_url'))),
            'tutor': _text(row.get('tutor_name')), 'coach': _text(row.get('coach_name')),
            'attendanceConfirmed': bool(row.get('attendance_confirmed')),
            'creditedMinutes': row.get('credited_minutes'),
            'status': outcome, 'counted': counted,
            'rawStatus': 'cancelled' if cancelled else (row.get('raw_attendance_status') or row['attendance_status']),
            'effectiveStatus': 'cancelled' if cancelled else (row.get('effective_attendance_status') or row['attendance_status']),
            'rawAttendanceStatus': 'cancelled' if cancelled else (row.get('raw_attendance_status') or row['attendance_status']),
            'effectiveAttendanceStatus': 'cancelled' if cancelled else (row.get('effective_attendance_status') or row['attendance_status']),
            'effectiveAttendance': row.get('effective_attendance') if 'effective_attendance' in row else
                                   (1 if row['attendance_status'] in {'present', 'late'} else
                                    0 if row['attendance_status'] == 'absent' else None),
            'finalOutcome': 'cancelled' if cancelled else (row.get('final_outcome') or row['attendance_status']),
            'attendanceBasis': row.get('attendance_basis'),
            'excused': bool(row.get('excused')), 'catchupStatus': None if cancelled else ('completed' if row.get('catchup_completed') else None), 'updatedAt': row['updated_at'].isoformat() if row.get('updated_at') else None,
        }
        if source == 'kbc-attendance':
            meta = legacy_meta.get(row['session_id']) or {}
            if meta.get('log_month'):
                lecture['monthlyLog']['month'] = str(meta['log_month'])[:7]
            lecture['ksbs'] = ksb_codes(meta.get('ksbs'))
            if lecture['ksbs']:
                lecture['ksbScope'] = 'lecture'
            if meta.get('activity_time'):
                lecture['startTime'] = meta['activity_time'].strftime('%H:%M')
            key = ('legacy', meta.get('group_id'), row.get('module_title'))
            if key not in legacy_modules:
                legacy_modules[key] = _legacy_module_activities(row, meta, legacy_activities)
            module_activities = legacy_modules[key]
            linked = [a for a in module_activities if a.get('activity_date') == row['session_date'] or
                      _key(a['title']) == _key(row.get('session_title'))]
            # An explicitly cleared journal mapping remains cleared. Only an
            # absent mapping falls back to the materials' current saved KSBs.
            if meta.get('ksbs') is None and legacy_meta.get(row['session_id'], {}) is not None:
                if key not in module_ksbs:
                    module_ksbs[key] = _activity_ksbs(module_activities)
                _fill_ksbs(lecture, linked, module_activities, module_ksbs[key])
            for activity in linked:
                lecture['activities'].append({
                    'id': f"legacy:{activity['group_id']}:{activity['activity_id']}",
                    'title': activity['title'] or 'Activity', 'type': activity['activity_type'],
                    'completed': bool(activity.get('new_completed') or _is_completed(activity)),
                    'groupId': activity['group_id'], 'activityId': activity['activity_id'],
                })
            lecture['contentSummary'] = '; '.join(dict.fromkeys(a['title'] for a in lecture['activities']))
        else:
            module_activities = native_modules.get(row.get('module_catalogue_id'), [])
            component = _component_for(row, module_activities)
            linked = []
            if component:
                lecture['title'] = component['title'] or lecture['title']
                lecture['contentSummary'] = component['description'] or component['settings'].get('sessionPurpose') or ''
                lecture['componentHref'] = f"/learner/component/{kind}/{learner_id}/{component['id']}"
                lecture['ksbs'] = _component_ksbs(component)
                if lecture['ksbs']:
                    lecture['ksbScope'] = 'lecture'
                # The authored week is this lecture's activity bundle.
                linked = [c for c in module_activities if c['id'] == component['id'] or
                          (component['week_id'] is not None and c['week_id'] == component['week_id']
                           and c['type'] != 'live_session')]
                for activity in linked:
                    lecture['activities'].append({
                        'id': activity['id'], 'title': activity['title'] or 'Activity',
                        'type': activity['type'], 'completed': activity['completed'],
                        'href': f"/learner/component/{kind}/{learner_id}/{activity['id']}",
                    })
            if not lecture['ksbs']:
                key = ('native', row.get('module_catalogue_id'))
                if key not in module_ksbs:
                    module_ksbs[key] = _activity_ksbs(module_activities)
                _fill_ksbs(lecture, linked, module_activities, module_ksbs[key])
        if lecture['attendanceConfirmed']:
            lecture['durationMinutes'] = lecture['creditedMinutes']
            if source == 'kbc-attendance':
                lecture['monthlyLog']['month'] = lecture['date'][:7]
        if (row['attendance_status'] in {'absent', 'unmarked'} and row.get('catchup_completed')) or lecture['status'] == 'absent':
            activities = lecture['activities']
            lecture['catchupStatus'] = ('completed' if row.get('catchup_completed') else 'pending') if source == 'microsoft-teams' else (
                'completed' if activities and all(a['completed'] for a in activities) else 'pending')
        lectures.append(lecture)
    return sorted(lectures, key=lambda r: (r['date'], r['startTime'], r['id']))


def lecture_totals(lectures):
    from .attendance_rules import canonical_status
    attended = sum(canonical_status(row['status']) == 'present' and row.get('counted') is not False for row in lectures)
    absent = sum(row['status'] == 'absent' and row.get('counted') is not False for row in lectures)
    return {'total': len(lectures), 'attended': attended, 'absent': absent,
            'covered': sum(row['catchupStatus'] == 'completed' for row in lectures),
            'upcoming': sum(row['status'] == 'upcoming' for row in lectures),
            'attendanceRate': round(100 * attended / (attended + absent)) if attended + absent else None}


def _attach_recovery(lectures, reported, now=None):
    """Tell the learner how each reported absence is being made up, when, and where to go."""
    keys = {_plan_event_key(report) for report in reported.values()} - {None}
    events = {}
    if keys:
        from coach_api.models import CoachCalendarEvent
        events = {row['event_key']: row for row in CoachCalendarEvent.objects.filter(event_key__in=list(keys)).values(
            'event_key', 'scheduled_date', 'scheduled_time', 'duration_minutes', 'meeting_link', 'status')}
    for lecture in lectures:
        report = reported.get(lecture['reportId'])
        method = (report or {}).get('recovery_method') or ''
        if not method:
            lecture['recovery'] = None
        elif method == 'alternative':
            lecture['recovery'] = _alternative_recovery(report, now)
        else:
            key = _plan_event_key(report)
            lecture['recovery'] = _booked_recovery(method, key, events.get(key), now)


RECOVERY_JOIN_EARLY_MINUTES = 10


def _occurrence_id(lecture):
    """A Teams lecture's occurrence id (session_key namespaces it as 'teams:<id>')."""
    return str(lecture['sessionId']).removeprefix('teams:')


def _attach_recording_views(lectures, kind, learner_id):
    """Show how much of the lecture recording the learner watched, for a recording plan."""
    planned = [lecture for lecture in lectures if (lecture.get('recovery') or {}).get('method') == 'recorded'
               and lecture.get('source') == 'microsoft-teams']
    if not planned:
        return
    from curriculum_api.recording_views import watched_by_occurrence
    try:
        watched = watched_by_occurrence(kind, learner_id, [_occurrence_id(lecture) for lecture in planned])
    except DatabaseError:
        log.warning('Could not read recording views for learner %s.', learner_id, exc_info=True)
        return
    for lecture in planned:
        seconds, duration = watched.get(_occurrence_id(lecture), (0, 0))
        lecture['recovery'].update({'watchedSeconds': seconds, 'recordingSeconds': duration})


def _plan_event_key(report):
    """Calendar event holding a catch-up booking or a planned recording watch."""
    method = report.get('recovery_method')
    if method == 'catch-up':
        return report.get('catchup_event_key') or None
    if method == 'recorded':
        # Created with the report (absence_reports._recording_recovery_event).
        return f"absence-recording:{report['id']}"
    return None


def _timing(recovery, day, start_time, end_time, join_url, now):
    """Add ended/joinUrl: joinable from shortly before the start until the end, in business (London) time."""
    try:
        start = datetime.fromisoformat(f"{day}T{start_time}")
        end = datetime.fromisoformat(f"{day}T{end_time}") if end_time else start
    except (TypeError, ValueError):
        return recovery
    now_local = timezone.localtime(now).replace(tzinfo=None)
    recovery['ended'] = now_local > end
    if join_url and start - timedelta(minutes=RECOVERY_JOIN_EARLY_MINUTES) <= now_local <= end:
        recovery['joinUrl'] = join_url
    return recovery


def _booked_recovery(method, key, event, now=None):
    """A catch-up booking or planned recording watch, and the calendar entry to open."""
    recovery = {'method': method, 'date': None}
    if not event or not event.get('scheduled_date'):
        return recovery
    day = event['scheduled_date'].isoformat()
    recovery.update({'date': day, 'calendarKey': key})
    if not event.get('scheduled_time'):
        return recovery
    start = datetime.combine(event['scheduled_date'], event['scheduled_time'])
    end = start + timedelta(minutes=event.get('duration_minutes') or 30)
    recovery.update({'startTime': start.strftime('%H:%M'), 'endTime': end.strftime('%H:%M')})
    # Only a live catch-up meeting has a Teams link to join.
    join_url = event.get('meeting_link') if method == 'catch-up' and event.get('status') != 'cancelled' else ''
    return _timing(recovery, day, recovery['startTime'], recovery['endTime'], join_url, now)


def _alternative_recovery(report, now=None):
    """Where and when the learner makes up this lecture in another group's session."""
    from .alternative_recovery import alternative_target_details
    recovery = {'method': 'alternative', 'date': None,
                # The learner calendar lists the alternative under this key (alternative_recovery_events_for_learner).
                'calendarKey': f"absence-alternative:{report['id']}"}
    target = alternative_target_details(report.get('catchup_event_key'), include_join_url=True)
    if not target:
        return recovery
    recovery.update({'date': target['dateIso'], 'startTime': target['startTime'],
                     'endTime': target.get('endTime') or '', 'group': target.get('group') or '',
                     'title': target.get('title') or ''})
    return _timing(recovery, target['dateIso'], target['startTime'], target.get('endTime'), target.get('joinUrl'), now)


def _mark_missed_catchups(lectures, reported):
    """Show a missed Teams lecture's catch-up as missed once it ended without the learner."""
    pending = {}
    for lecture in lectures:
        report = reported.get(lecture['reportId'])
        if (lecture['source'] == 'microsoft-teams' and lecture['status'] == 'absent'
                and lecture['catchupStatus'] == 'pending' and report
                and report.get('recovery_method') == 'catch-up' and report.get('catchup_event_key')):
            pending.setdefault(report['catchup_event_key'], []).append(lecture)
    if not pending:
        return
    from coach_api.models import CoachCalendarEvent
    from .catchup_outcomes import learner_attended_catchup
    completed = CoachCalendarEvent.objects.filter(
        event_key__in=list(pending), event_type__iexact='catch-up', status=CoachCalendarEvent.STATUS_COMPLETED,
    ).values_list('event_key', flat=True)
    for event_key in completed:
        if not learner_attended_catchup(event_key):
            for lecture in pending[event_key]:
                lecture['catchupStatus'] = 'missed'


def read_workspace(source, kind, *, monthly=None, monthly_occurrences=None):
    from .attendance_mode import read_mode
    register = lecture_register(source)
    if monthly is not None:
        register = [row for row in register if row.get('source') == 'microsoft-teams' and row['session_date'].isoformat().startswith(monthly)]
        if monthly_occurrences is not None:
            # Clip only after canonical register normalization. Avoid content and
            # absence/recovery queries for cards the Monthly UI does not render.
            register = [row for row in register if (session_key(row), row['session_date'].isoformat()) in monthly_occurrences]
    metadata, activities = ({}, []) if monthly is not None else read_legacy_metadata(source, register)
    native_modules = {row['module_catalogue_id'] for row in register if row.get('source') == 'microsoft-teams'}
    components, progress = read_native_components(source, native_modules, monthly=True) if monthly is not None else read_native_components(source, native_modules)
    lectures = build_lectures(register, metadata, activities, components, kind, source.id)
    if monthly is not None:
        # Register normalization precedes clipping: replacement/catch-up outcomes
        # can depend on occurrences outside the displayed month.
        lectures = [lecture for lecture in lectures if lecture['date'].startswith(monthly)]
        with connections['enrolment'].cursor() as cur:
            ids = list(dict.fromkeys(lecture['reportId'] for lecture in lectures))
            if ids:
                cur.execute('''SELECT id,attendance_id,status,recovery_method,catchup_event_key
                    FROM "Coach".coach_absence_report
                    WHERE learner_id=%s AND attendance_id=ANY(%s) ORDER BY created_at DESC''', [source.id, ids])
                reports = dict_rows(cur)
            else:
                reports = []
        reported = {str(report['attendance_id']): report for report in reversed(reports)}
        for lecture in lectures:
            report = reported.get(lecture['reportId'])
            lecture['absenceReport'] = {'id': report['id'], 'status': report['status']} if report else None
            lecture['canReportAbsence'] = can_report_absence_status(lecture['status'], lecture['date']) and not report
        _attach_recovery(lectures, reported)
        _mark_missed_catchups(lectures, reported)
        return {'lectures': lectures}
    # Include every current assignment, even when it has no attendance row yet.
    # Historical/source-only modules remain available through the lecture list.
    assigned_modules = read_assigned_modules(source)
    mode = read_mode(source, kind)
    with connections['enrolment'].cursor() as cur:
        cur.execute('''SELECT coach_name,coach_email FROM "Learner".learners
            WHERE enrolment_id=%s''', [source.id])
        profiles = dict_rows(cur)
        coach = (profiles[0].get('coach_name') or profiles[0].get('coach_email') or '') if len(profiles) == 1 else ''
        cur.execute('''SELECT id,attendance_id,status,session_title,created_at,updated_at,
            recovery_method,catchup_event_key
            FROM "Coach".coach_absence_report WHERE learner_id=%s ORDER BY created_at DESC''', [source.id])
        reports = dict_rows(cur)
        cur.execute('''SELECT id,event_type,status,updated_at FROM "Coach".coach_calendar_event
            WHERE learner_id=%s AND lower(btrim(learner_email))=%s
              AND event_type IN ('student-support','catch-up') AND scheduled_date IS NOT NULL
            ORDER BY updated_at DESC LIMIT 15''', [source.id, _key(source.email)])
        bookings = dict_rows(cur)
        cur.execute('''SELECT p.id,coalesce(nullif(p.feed_title,''),p.component_title) AS title,
            p.feed_action,p.feed_occurred_at
            FROM "Learner".learner_progress_entries p
            JOIN "Learner".learners l ON l.id=p.learner_id
            WHERE l.enrolment_id=%s AND p.kind='activity_event' AND p.feed_occurred_at IS NOT NULL
              AND coalesce(p.feed_kind,'')<>'attendance_confirmation'
            ORDER BY p.feed_occurred_at DESC,p.id DESC LIMIT 20''', [source.id])
        events = dict_rows(cur)
    reported = {str(report['attendance_id']): report for report in reversed(reports)}
    recent = []
    for lecture in lectures:
        lecture['coach'] = coach or lecture['coach']
        report = reported.get(lecture['reportId'])
        lecture['absenceReport'] = {'id': report['id'], 'status': report['status']} if report else None
        lecture['canReportAbsence'] = can_report_absence_status(lecture['status'], lecture['date']) and not report
        if lecture['status'] == 'present' and lecture['updatedAt']:
            recent.append({'id': lecture['id'], 'title': f"{lecture['title']} attended", 'at': lecture['updatedAt'], 'type': 'attendance'})
    _attach_recovery(lectures, reported)
    _attach_recording_views(lectures, kind, source.id)
    _mark_missed_catchups(lectures, reported)
    for report in reports:
        recent.append({'id': f"report:{report['id']}", 'title': f"Absence report: {report['session_title']} ({report['status']})",
                       'at': _aware(report['updated_at']).isoformat(), 'type': 'absence'})
    for booking in bookings:
        recent.append({'id': f"booking:{booking['id']}",
                       'title': f"{'Support session' if booking['event_type'] == 'student-support' else 'Catch-up session'}: {booking['status']}",
                       'at': _aware(booking['updated_at']).isoformat(), 'type': 'booking'})
    for event in events:
        recent.append({'id': f"event:{event['id']}",
                       'title': f"{event['title'] or 'Activity'} {event['feed_action'] or ''}".strip(),
                       'at': _aware(event['feed_occurred_at']).isoformat(), 'type': 'activity'})
    for entry in progress:
        at = entry.get('submittedAt')
        if at:
            recent.append({'id': f"progress:{entry.get('componentId')}:{at}",
                           'title': f"{entry.get('componentTitle') or 'Activity'} completed" if progress_counts_as_achieved(entry) else
                                    f"{entry.get('componentTitle') or 'Activity'} attempted",
                           'at': at.isoformat() if isinstance(at, datetime) else str(at), 'type': 'activity'})
    if mode.get('updatedAt'):
        recent.append({'id': 'mode', 'title': f"Attendance mode: {mode['requestedMode'] or mode['mode']}",
                       'at': mode['updatedAt'], 'type': 'mode'})
    planned = sum((r['durationMinutes'] or 0) / 60 for r in lectures if r['status'] == 'upcoming')
    mode['unmappedPlannedLectures'] = sum(r['durationMinutes'] is None for r in lectures if r['status'] == 'upcoming')
    mode['plannedLiveHours'] = round(planned, 2) if mode['mode'] == 'live' else 0
    mode['plannedRecordedHours'] = round(planned, 2) if mode['mode'] == 'lazy' else 0
    module_options = {(r['moduleId'], r['module']) for r in lectures}
    module_options.update(
        (f"native:{row['id']}", row['title'])
        for row in assigned_modules if row.get('id') and row.get('title')
    )
    return {'lectures': lectures, 'totals': lecture_totals(lectures),
            'summary': _summarize_attendance(register), 'mode': mode, 'timeZone': settings.TIME_ZONE,
            'modules': [{'id': key, 'title': title} for key, title in sorted(module_options, key=lambda v: v[1].casefold())],
            'recentActivity': sorted(recent, key=lambda r: r['at'], reverse=True)[:30]}


@require_GET
@learner_self_or_staff(kwarg='learner_id')
@journal_sources.learner_journal_view
def attendance_lectures(request, kind, learner_id):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Unknown learner kind.'}, status=404)
    try:
        source = model.all_learners.filter(pk=learner_id).only(*ATTENDANCE_SOURCE_FIELDS).first()
        if source is None:
            return JsonResponse({'error': 'Learner not found.'}, status=404)
        if getattr(source, 'email', ''):
            # Catch-ups only: settle this learner's elapsed catch-ups before reading recoveries.
            from .catchup_outcomes import sync_catchup_outcomes
            sync_catchup_outcomes(learner_email=getattr(source, 'email', ''))
        payload = read_workspace(source, kind)
        payload['csrfToken'] = get_token(request)
    except Exception:
        log.exception('Could not read lecture workspace for learner %s', learner_id)
        return JsonResponse({'error': 'Could not refresh lectures. Please try again.'}, status=502)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response
