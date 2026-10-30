"""Small, read-only weekly overview. Never hydrates a learner or fetches content."""
from datetime import datetime, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo

from . import journal_sources
from django.db import DatabaseError, connections
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from login.permissions import learner_self_or_staff
from audit_api.last_audit_ledger_views import _is_completed
from .builder_activity_dates import COMPONENT_ORDER, COMPONENT_SCHEDULE_SETTINGS, read_builder_activity_dates
from .dashboard_metrics import metrics_from_loaded, point_codes, ratio
from .progress_rules import progress_counts_as_achieved
from old_otjh.service import ServiceError
from .learner_detail import SOURCE_MODELS
from .models import LearnerProfile
from . import canonical_learning
from .student_activity import CURRENT_SUBJECTS_SQL, _direct_progress_records, _direct_progress_otjh


# Home needs both assignment interpretations: My Learning treats an explicit
# JSON plan as authoritative, while Attendance intentionally also includes the
# normalised mirror. Returning both scopes from one query avoids reading the
# same learner plan twice without changing either rule.
HOME_SUBJECTS_SQL = '''
    WITH source AS (
        SELECT id, CASE WHEN jsonb_typeof("Training_plan"::jsonb)='array'
            THEN "Training_plan"::jsonb ELSE "Learning_plan"::jsonb END AS plan
        FROM enrolment."Created_users" WHERE id=%s
    ), assigned AS (
        SELECT entry->>'moduleId' AS module_id, true AS current_plan FROM source
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(plan)='array' THEN plan ELSE '[]'::jsonb END
        ) entry
        UNION ALL
        SELECT coalesce(m.curriculum_module_id,nullif(m.module_ref,'')),
               jsonb_typeof(source.plan) IS DISTINCT FROM 'array'
        FROM source
        JOIN "Learner".learners l ON l.enrolment_id=source.id
        JOIN "Learner".learner_training_plan_modules m ON m.learner_id=l.id
    )
    SELECT cm.module_catalogue_id,cm.title,bool_or(assigned.current_plan)
    FROM assigned JOIN curriculum.modules cm ON cm.module_catalogue_id=assigned.module_id
    WHERE (cm.deleted_at IS NULL OR COALESCE(cm.deleted_via_parent, '') <> '')
    GROUP BY cm.module_catalogue_id,cm.title
'''

HOME_SOURCE_FIELDS = (
    'id', 'aptem_id', 'email', 'username',
    # Read by the home card's programme period (_group_dates); deferring them
    # cost one query each.
    'programme', 'cohort', 'start_date', 'end_date',
)
from .student_activity_data import read_curriculum_schedules, apply_curriculum_schedules
from .subject_dates import activity_schedule, as_date
from .subject_content import clean_text
from .training_plan_dashboard import number, rows
from .projection_performance import measure_projection
from curriculum_api.week_visibility import visible_child_sql, visible_week_sql

log = logging.getLogger(__name__)
UK = ZoneInfo('Europe/London')
ASSIGNMENT_PENDING_STATUSES = frozenset({'submitted_for_tutor_review', 'submitted', 'pending_review'})


def week_bounds(now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(UK).date()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def progress_day(value):
    try:
        value = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return value.replace(tzinfo=timezone.utc).astimezone(UK).date() if value.tzinfo is None else value.astimezone(UK).date()
    except (ValueError, TypeError):
        return None


def completion_day(row):
    """The day a progress row's hours count on.

    The declared working instant when the learner's Finish click fell outside
    the working rules and had to be corrected, otherwise the click itself. This
    is the same order ``coach_api.entry_activity_date`` uses, so the learner's
    own week and month totals bucket exactly where the coach's do; the real
    click stays readable at ``submittedAt`` for audit.
    """
    row = row if isinstance(row, dict) else {}
    return progress_day(row.get('declaredCompletedAt') or row.get('submittedAt'))


def merged_activities(historical, native, progress, attempts, links):
    """Merge only explicit identities, using the same completion rule as the cards."""
    achieved = [row for row in progress if progress_counts_as_achieved(row.get('kind'), row.get('passed'))]
    component_ids = {str(row['componentId']) for row in achieved if row.get('componentId')}
    quiz_ids = {str(row['quizId']) for row in achieved if row.get('quizId')}
    activities = {}
    for row in historical:
        key = (str(row['group_id']), str(row['activity_id']))
        done = _is_completed(row) or key in attempts
        previous = activities.get(key)
        activities[key] = {**row, 'key': key, 'subject': f'legacy:{row["group_id"]}',
                           'completed': done or bool(previous and previous['completed'])}
    for row in native:
        key = links.get(str(row['id']), ('native', str(row['id'])))
        previous = activities.get(key)
        done = str(row['id']) in component_ids or bool(row.get('quiz_id') and str(row['quiz_id']) in quiz_ids)
        if previous:
            previous['completed'] |= done
            previous.setdefault('component_ids', []).append(str(row['id']))
            # A mapped Builder activity can be rescheduled after the export.
            if row.get('date') and not row.get('date_needs_review'):
                for field in ('date', 'week_start', 'week_end', 'section_title', 'date_needs_review'):
                    previous[field] = row.get(field)
            old_codes, new_codes = point_codes(previous.get('ksb_mappings')), point_codes(row.get('ksb_mappings'))
            if old_codes is not None and new_codes is not None:
                previous['ksb_mappings'] = sorted(old_codes | new_codes)
            previous['type'] = row['type']
            previous['due_timing'] = row.get('due_timing')
            previous['module_id'] = row['module_id']
            if number(row.get('expected_hours')) is not None:
                previous['expected_hours'] = row['expected_hours']
                previous['expected_source'] = 'native'
        else:
            activities[key] = {**row, 'key': key, 'subject': f'current:{row["module_id"]}', 'completed': done}
    return activities.values()


def direct_hours_by_subject(native, progress, links):
    """Attribute recorded time by component/quiz identity, never by a module title."""
    components, quizzes = {}, {}
    for row in native:
        placement = links.get(str(row['id']))
        subject = f'legacy:{placement[0]}' if placement else f'current:{row["module_id"]}'
        components[str(row['id'])] = subject
        if row.get('quiz_id'):
            quizzes.setdefault(str(row['quiz_id']), set()).add(subject)
    grouped = {}
    for entry in progress:
        subject = components.get(str(entry.get('componentId')))
        candidates = quizzes.get(str(entry.get('quizId')), set())
        if not subject and len(candidates) == 1:
            subject = next(iter(candidates))
        if subject:
            grouped.setdefault(subject, []).append(entry)
    return {subject: round(_direct_progress_otjh(entries), 4) for subject, entries in grouped.items()}


def monthly_otjh_summary(activities, progress):
    """Return planned, submitted and achieved current-platform hours by UK month.

    Planned time follows the authored activity delivery date and uses the same
    explicit old/new identity and source-priority rules as the weekly card.
    Assignment submissions use their coach-marking status. Other activity types,
    including quizzes, keep the existing progress-row semantics.
    """
    planned, priorities = {}, {}
    for row in activities:
        day = as_date(row.get('date'))
        if not day or row.get('date_needs_review'):
            continue
        month = day.strftime('%Y-%m')
        hour_key = ('legacy', str(row['activity_id'])) if row.get('activity_id') is not None else row['key']
        value = number(row.get('expected_hours'))
        priority = int(row.get('expected_source') == 'native' or row['key'][0] == 'native')
        key = (month, hour_key)
        if key not in planned or value is not None and (planned[key] is None or priority >= priorities[key]):
            planned[key] = value
            priorities[key] = priority

    progress_by_month = {}
    for row in progress:
        day = completion_day(row)
        if day:
            progress_by_month.setdefault(day.strftime('%Y-%m'), []).append(row)

    result = {}
    months = {month for month, _ in planned} | set(progress_by_month)
    for month in sorted(months):
        values = [value for (key_month, _), value in planned.items() if key_month == month]
        missing = sum(value is None for value in values)
        submitted_rows = progress_by_month.get(month, [])
        assignment_rows = [row for row in submitted_rows
                           if str(row.get('componentType') or '').strip().casefold() == 'assignment']
        pending_assignments = [row for row in assignment_rows
                               if str(row.get('markingStatus') or '').strip().casefold() in ASSIGNMENT_PENDING_STATUSES]
        accepted_assignments = [row for row in assignment_rows
                                if str(row.get('markingStatus') or '').strip().casefold() in {'accepted', 'partial'}]
        other_rows = [row for row in submitted_rows if row not in assignment_rows]
        achieved_rows = [row for row in other_rows + accepted_assignments
                         if progress_counts_as_achieved(row.get('kind'), row.get('passed'))]
        result[month] = {
            'planned': round(sum(value for value in values if value is not None), 4) if values and not missing else None,
            # Only assignments have a separate pending marking state. Quiz and
            # other activity totals retain their established semantics.
            'submitted': round(_direct_progress_otjh(pending_assignments + other_rows), 4),
            'actual': round(_direct_progress_otjh(achieved_rows), 4),
            'missingPlannedActivities': missing,
        }
    return result


def summarise_plan(activities, assigned, direct_hours=None):
    """Plan cards need counts and compact monthly rows, never lesson bodies or attempts."""
    subjects = {}
    for row in activities:
        subject = subjects.setdefault(row['subject'], {
            'id': row['subject'], 'title': clean_text(row.get('module_title')) or 'Learning activities',
            'source': 'legacy' if row['subject'].startswith('legacy:') else 'current',
            'total': 0, 'completed': 0, 'dates': set(), 'moduleIds': set(), 'sessionTitles': [],
            'activityCounts': {}, 'ksbCodes': set(), 'ksbMappingMissing': False,
            'ksbProgress': {'completed': 0, 'total': 0},
            'monthlyActivities': [], 'ksbCodesByMonth': {},
        })
        subject['total'] += 1
        subject['completed'] += bool(row['completed'])
        category = clean_text(row.get('type') or row.get('category')) or 'activity'
        monthly_type = category.strip().casefold().replace('-', '_').replace(' ', '_')
        subject['activityCounts'][category] = subject['activityCounts'].get(category, 0) + 1
        codes = point_codes(row.get('ksb_mappings')) if row.get('ksb_mappings') is not None else None
        subject['ksbCodes'].update(codes or [])
        subject['ksbMappingMissing'] |= codes is None
        subject['ksbProgress']['total'] += len(codes or [])
        subject['ksbProgress']['completed'] += len(codes or []) if row['completed'] else 0
        if row.get('module_id'):
            subject['moduleIds'].add(row['module_id'])
        day = as_date(row.get('date'))
        if day and not row.get('date_needs_review'):
            subject['dates'].add(day.isoformat())
            month_codes = subject['ksbCodesByMonth'].setdefault(day.strftime('%Y-%m'), set())
            month_codes.update(codes or [])
            if monthly_type == 'live_session':
                subject['sessionTitles'].append({'date': day.isoformat(), 'title': clean_text(row.get('title'))})
            if monthly_type in ('assignment', 'live_session'):
                native_ids = row.get('component_ids') or []
                component_id = (native_ids[0] if native_ids else row.get('id')) if row.get('module_id') else None
                subject['monthlyActivities'].append({
                    'id': ':'.join(str(value) for value in row['key']),
                    'componentId': str(component_id) if component_id else None,
                    'title': clean_text(row.get('title')) or ('Assignment' if monthly_type == 'assignment' else 'Live session'),
                    'type': monthly_type,
                    'date': day.isoformat(),
                    'weekTitle': clean_text(row.get('section_title')) or None,
                    'expectedHours': number(row.get('expected_hours')),
                    'completed': bool(row['completed']),
                    'ksbCodes': sorted(codes or []),
                })
    represented = {module_id for subject in subjects.values() for module_id in subject['moduleIds']}
    for module_id, title in assigned:
        if module_id not in represented:
            subjects[f'current:{module_id}'] = {
                'id': f'current:{module_id}', 'title': clean_text(title), 'source': 'current',
                'total': 0, 'completed': 0, 'dates': set(), 'moduleIds': {module_id}, 'sessionTitles': [],
                'activityCounts': {}, 'ksbCodes': set(), 'ksbMappingMissing': False,
                'ksbProgress': {'completed': 0, 'total': 0},
                'monthlyActivities': [], 'ksbCodesByMonth': {},
            }
    return [{**subject, 'dates': sorted(subject['dates']), 'moduleIds': sorted(subject['moduleIds']),
             'ksbCodes': sorted(subject['ksbCodes']),
             'ksbCodesByMonth': {month: sorted(codes) for month, codes in subject['ksbCodesByMonth'].items()},
             'ksbProgress': None if subject['ksbMappingMissing'] else subject['ksbProgress'],
             'directHours': (direct_hours or {}).get(subject['id'], 0) if direct_hours is not None else None}
            for subject in sorted(subjects.values(), key=lambda item: (item['title'].casefold(), item['id']))]


def due_date(row):
    """A dated learning activity is not automatically a submission deadline.

    Only an authored due rule can turn its week into a due date.
    """
    timing = str(row.get('due_timing') or '').strip().casefold()
    return as_date(row.get('week_end')) if timing == 'end of week' else None


# Student Home's assignment deadlines: the assignment components of the
# learner's current plan modules, read in one round trip with the plan itself
# and the learner's direct progress rows naming each component or its quiz.
UPCOMING_ASSIGNMENTS_SQL = f'''SELECT c.id,c.module_catalogue_id AS module_id,c.title,c.type,
    w.title AS section_title,c.settings_json->>'dueTiming' AS due_timing,quiz.quiz_id,
    (SELECT coalesce(jsonb_agg(jsonb_build_object('kind',p.kind,'passed',p.passed,
                                                  'componentId',p.component_ref,'quizId',p.quiz_ref)),'[]'::jsonb)
     FROM "Learner".learner_progress_entries p
     WHERE p.learner_id=%s AND p.component_link_source IN ('direct','quiz_ref') AND p.kind<>'activity_event'
       AND (p.component_ref=c.id::text OR p.quiz_ref=quiz.quiz_id)) AS progress
    FROM curriculum.components c JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
    LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
    CROSS JOIN LATERAL (SELECT coalesce((SELECT q.quiz_id::text FROM curriculum.quiz_component_links q
              WHERE q.component_id=c.id ORDER BY q.id LIMIT 1),c.settings_json->>'linkedQuizId') AS quiz_id) quiz
    WHERE c.module_catalogue_id IN (SELECT home.module_id FROM ({HOME_SUBJECTS_SQL}) home(module_id,title,current_plan)
                                    WHERE home.current_plan)
      AND c.type='assignment' AND {visible_child_sql('c')}
      AND (w.id IS NULL OR {visible_week_sql('w')})'''


# Header supplies the scheduling rows it already reads to the canonical Builder
# planner. Empty weeks still consume slots; unrelated component bodies/metadata
# do not influence an assignment's week date.
HEADER_ASSIGNMENTS_SQL = UPCOMING_ASSIGNMENTS_SQL.replace('w.title AS section_title',
    f'c.week_id,{COMPONENT_SCHEDULE_SETTINGS} AS schedule_settings,w.title AS section_title', 1) + '''
      AND lower(regexp_replace(coalesce(c.settings_json->>'dueTiming',''),
          '^[[:space:]]+|[[:space:]]+$','','g'))='end of week'
    ORDER BY ''' + COMPONENT_ORDER


def upcoming_assignment_deadlines(source, profile, today, until=None, *, header_only=False):
    """Home's assignment deadlines due from ``today`` to ``until`` (UK dates, inclusive), earliest first.

    The same rows and rule as the ``home-summary`` week deadlines: current plan
    modules, Builder delivery dates, an authored "end of week" due rule and the
    same completion rule, which reads only the progress rows naming these
    assignments. Builder dates are read only for modules holding an
    "end of week" assignment.

    ``profile`` is the enrolment's ``LearnerProfile`` (None without one, so
    there is no progress), as ``_direct_progress_records`` would resolve it.
    """
    # Every schedule's ``week_end`` is the Sunday of its Monday-based week
    # (``activity_schedule``), so a range holding no Sunday has no deadlines.
    if until is not None and not any(
            (today + timedelta(days=offset)).weekday() == 6 for offset in range((until - today).days + 1)):
        return []
    with connections['enrolment'].cursor() as cur:
        cur.execute(HEADER_ASSIGNMENTS_SQL if header_only else UPCOMING_ASSIGNMENTS_SQL,
                    [profile.id if profile is not None else None, source.pk])
        candidates = [row for row in rows(cur)
                      if str(row.get('due_timing') or '').strip().casefold() == 'end of week']
        if not candidates:
            return []
        options = {'components': [(row['id'], row['module_id'], row['week_id'], row['title'],
                                   row['type'], row['schedule_settings']) for row in candidates]} if header_only else {}
        dates = read_builder_activity_dates(cur, sorted({row['module_id'] for row in candidates}), **options)
    due, progress = [], []
    for row in candidates:
        row.update(dates.get(str(row['id'])) or activity_schedule(row['title'], section_title=row['section_title']))
        day = due_date(row)
        # An undated or unreviewed placement is not on the learner's week at all.
        if (as_date(row.get('date')) and not row.get('date_needs_review') and day
                and today <= day and (until is None or day <= until)):
            due.append(row)
            progress.extend(json.loads(row['progress']) if isinstance(row['progress'], str) else row['progress'] or [])
    return sorted(({'id': ':'.join(row['key']), 'title': clean_text(row.get('title')) or 'Assignment',
                    'date': due_date(row).isoformat()}
                   for row in merged_activities([], due, progress, set(), {}) if not row['completed']),
                  key=lambda row: (row['date'], row['id']))


def summarise_week(historical, native, progress, attempts, links, start, end, *, activities=None):
    if activities is None:
        activities = merged_activities(historical, native, progress, attempts, links)
    modules, deadlines, expected, expected_priority = {}, [], {}, {}
    undated = 0
    for row in activities:
        day = as_date(row.get('date'))
        if not day or row.get('date_needs_review'):
            undated += 1
            continue
        if start <= day <= end:
            # The same historical activity may have multiple group placements.
            # Its expected time is recorded per learner/activity, not per group.
            hour_key = ('legacy', str(row['activity_id'])) if row.get('activity_id') is not None else row['key']
            value = number(row.get('expected_hours'))
            priority = int(row.get('expected_source') == 'native' or row['key'][0] == 'native')
            if hour_key not in expected or value is not None and (expected[hour_key] is None or priority >= expected_priority[hour_key]):
                expected[hour_key] = value
                expected_priority[hour_key] = priority
            module = modules.setdefault(row['subject'], {'id': row['subject'], 'title': clean_text(row.get('module_title')) or 'Learning activities',
                'weekLabels': set(), 'moduleIds': set(), 'completed': 0, 'total': 0, 'ksbCodes': set(), 'ksbMappingMissing': False})
            if row.get('module_id'):
                module['moduleIds'].add(row['module_id'])
            module['total'] += 1
            module['completed'] += row['completed']
            if row.get('section_title'):
                module['weekLabels'].add(clean_text(row['section_title']))
            codes = point_codes(row.get('ksb_mappings')) if row.get('ksb_mappings') is not None else None
            module['ksbMappingMissing'] |= codes is None
            module['ksbCodes'].update(codes or [])
        if row.get('type') in ('assignment', 'checkpoint') and not row['completed']:
            due = due_date(row)
            if due and due >= start:
                deadlines.append({'id': ':'.join(row['key']), 'title': clean_text(row.get('title')) or 'Assignment',
                                  'type': row['type'], 'date': due.isoformat(), 'subjectId': row['subject']})
    result = []
    for module in modules.values():
        result.append({**module, 'weekLabels': sorted(module['weekLabels']), 'moduleIds': sorted(module['moduleIds']), 'ksbCodes': sorted(module['ksbCodes']),
                       'percent': ratio(module['completed'], module['total'])['percent']})
    return {'modules': sorted(result, key=lambda row: (row['title'].casefold(), row['id'])),
            'deadlines': sorted(deadlines, key=lambda row: (row['date'], row['id'])), 'undatedActivities': undated,
            'expectedHours': round(sum(expected.values()), 4) if expected and all(value is not None for value in expected.values()) else None,
            'missingExpectedHours': sum(value is None for value in expected.values())}


def read_overview_subjects(source, profile):
    """Overview only needs native activity counts, not dates, hours or KSBs."""
    progress = [{'kind': row['kind'], 'passed': row['passed'],
                 'componentId': row['component_ref'], 'quizId': row['quiz_ref']}
                for row in profile.progress_entries.using('enrolment')
                .filter(component_link_source__in=('direct', 'quiz_ref'), deleted_at__isnull=True)
                .exclude(kind='activity_event')
                .values('kind', 'passed', 'component_ref', 'quiz_ref')]
    with connections['enrolment'].cursor() as cur:
        cur.execute(CURRENT_SUBJECTS_SQL, [source.pk])
        assigned = cur.fetchall()
        cur.execute('''SELECT c.id,c.module_catalogue_id AS module_id,m.title AS module_title,
            coalesce((SELECT q.quiz_id::text FROM curriculum.quiz_component_links q
                      WHERE q.component_id=c.id ORDER BY q.id LIMIT 1),
                     c.settings_json->>'linkedQuizId') AS quiz_id
            FROM curriculum.components c
            JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
            LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            WHERE c.module_catalogue_id=ANY(%s)
              AND ''' + visible_child_sql('c') + '''
              AND (w.id IS NULL OR ''' + visible_week_sql('w') + ''')''',
                    [[module_id for module_id, _title in assigned]])
        native = rows(cur)
    subjects = {}
    for row in merged_activities([], native, progress, set(), {}):
        subject = subjects.setdefault(row['subject'], {'id': row['subject'],
            'title': clean_text(row.get('module_title')) or 'Learning activities',
            'total': 0, 'completed': 0})
        subject['total'] += 1
        subject['completed'] += bool(row['completed'])
    for module_id, title in assigned:
        subjects.setdefault(f'current:{module_id}', {'id': f'current:{module_id}',
            'title': clean_text(title), 'total': 0, 'completed': 0})
    return sorted(subjects.values(), key=lambda row: (row['title'].casefold(), row['id']))


def component_sql(ksb_mappings, *, builder_columns=False):
    """The plan's undeleted components (in undeleted or no weeks).

    ``builder_columns`` also selects what ``read_builder_activity_dates`` reads
    from the same rows, so it needs no second pass over the components'
    settings. Components it would skip (deleted or missing weeks, deleted
    modules) it still skips.
    """
    extra = (f''',c.week_id AS builder_week_id,{COMPONENT_SCHEDULE_SETTINGS} AS builder_settings,
            row_number() OVER (ORDER BY {COMPONENT_ORDER}) AS builder_order''' if builder_columns else '')
    return f'''SELECT c.id,c.module_catalogue_id AS module_id,m.title AS module_title,c.title,c.type,c.expected_otjh AS expected_hours,
            w.title AS section_title,c.settings_json->>'dueTiming' AS due_timing,
            {ksb_mappings} AS ksb_mappings,
            coalesce((SELECT q.quiz_id::text FROM curriculum.quiz_component_links q
                      WHERE q.component_id=c.id ORDER BY q.id LIMIT 1),c.settings_json->>'linkedQuizId') AS quiz_id{extra}
            FROM curriculum.components c JOIN curriculum.modules m ON m.module_catalogue_id=c.module_catalogue_id
            LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            WHERE c.module_catalogue_id=ANY(%s) AND {visible_child_sql('c')}
              AND (w.id IS NULL OR {visible_week_sql('w')})'''


def builder_components(native):
    """Take the builder columns off ``component_sql`` rows, in Module Builder order."""
    found = sorted((row.pop('builder_order'), (row['id'], row['module_id'], row.pop('builder_week_id'),
                                                row['title'], row['type'], row.pop('builder_settings')))
                   for row in native)
    return [component for _, component in found]


def read_week(source, now=None, *, home_kind=None, dashboard_kind=None, home_summary=False, plan_only=False, monthly_hours_only=False):
    """``home_summary`` is Student Home's compact projection of the ``home`` section.

    It reads the same sources with the same rules, but fails fast on missing SSOT
    identity and skips plan subjects, monthly OTJH and component KSB lookups.
    """
    start, end = week_bounds(now)
    # Every active learner route is SSOT-only. Missing SSOT identity fails when
    # the canonical metrics/home projection is read below.
    historical, attempts, links = [], set(), {}
    old_hours, undated_hours = 0, 0
    early_metrics = None
    owned_profile = getattr(source, '_case_file_profile', None)
    if home_summary:
        # One verified SSOT identity serves the metrics, progress and
        # attendance reads below instead of each re-reading "Learner".learners.
        owner = canonical_learning.require_profile(source.pk)
        source._canonical_profile = owner
        early_metrics = canonical_learning.metrics(source.pk, owner=owner)
        if owned_profile is None:
            # require_profile matched exactly one row for this enrolment, so it
            # is the profile _direct_progress_records would otherwise look up.
            owned_profile = LearnerProfile(id=owner['id'], enrolment_id=source.pk)
    progress = (_direct_progress_records(source.pk, profile=owned_profile) if owned_profile is not None
                else _direct_progress_records(source.pk))
    attendance_module_ids = None
    with connections['enrolment'].cursor() as cur:
        cur.execute(HOME_SUBJECTS_SQL if home_kind else CURRENT_SUBJECTS_SQL, [source.pk])
        subject_rows = cur.fetchall()
        if home_kind:
            assigned = [(module_id, title) for module_id, title, current in subject_rows if current]
            attendance_module_ids = [module_id for module_id, _title, _current in subject_rows]
        else:
            assigned = subject_rows
        module_ids = [row[0] for row in assigned]
        # The compact home projection returns no KSB codes, so skip the per-component lookup.
        ksb_mappings = "'[]'::jsonb" if home_summary or monthly_hours_only else '''coalesce(nullif(c.ksb_mappings,'[]'::jsonb),
                (SELECT jsonb_agg(jsonb_build_object('code',k.ksb_code)) FROM curriculum.ksb_mappings k
                 WHERE k.component_id=c.id AND ''' + visible_child_sql('k') + '''), '[]'::jsonb)'''
        cur.execute(component_sql(ksb_mappings, builder_columns=home_summary), [module_ids])
        native = rows(cur)
        # Match My Learning's delivery calendar, including undated lesson titles,
        # empty teaching weeks and cohort holidays.
        cached_dates = getattr(source, '_dashboard_activity_dates', None) \
            if (plan_only or monthly_hours_only) and not home_summary else None
        if cached_dates is not None and cached_dates[0] == set(module_ids):
            # The Dashboard's learning projection already read this module set.
            dates = cached_dates[1]
        else:
            dates = read_builder_activity_dates(
                cur, module_ids, **({'components': builder_components(native)} if home_summary else {}))
            if plan_only or monthly_hours_only:
                source._dashboard_activity_dates = (set(module_ids), dates)
        for row in native:
            row.update(dates.get(str(row['id'])) or activity_schedule(row['title'], section_title=row['section_title']))
    # merged_activities copies its rows and nothing below mutates them, so one
    # merge serves the week, plan, monthly and home summaries alike.
    plan_activities = list(merged_activities(historical, native, progress, attempts, links))
    if home_summary:
        from .home_progress import read_home_progress
        week = summarise_week(historical, native, progress, attempts, links, start, end,
                              activities=plan_activities)
        progress_card = read_home_progress(
            source, home_kind, plan_activities, native, progress, assigned, end,
            attendance_module_ids=attendance_module_ids, canonical_metrics=early_metrics)
        # Week dates are always UK calendar dates; the card reads no time-zone label.
        progress_card['period'] = {key: progress_card['period'][key] for key in ('start', 'end')}
        return {'weekStart': start.isoformat(), 'weekEnd': end.isoformat(),
                # Assignment deadlines are Upcoming's (training-plan-dashboard section=upcoming).
                'modules': [{key: module[key] for key in ('id', 'title', 'weekLabels')} for module in week['modules']],
                'homeProgress': progress_card}
    if monthly_hours_only:
        return monthly_otjh_summary(plan_activities, progress)
    if plan_only:
        return {'planSubjects': summarise_plan(plan_activities, assigned,
                    direct_hours_by_subject(native, progress, links)),
                'monthlyOtjh': monthly_otjh_summary(plan_activities, progress)}
    weekly_progress = [row for row in progress if (day := completion_day(row)) and start <= day <= end]
    new_hours = _direct_progress_otjh(weekly_progress)
    result = {'weekStart': start.isoformat(), 'weekEnd': end.isoformat(), 'timezone': 'Europe/London',
            **summarise_week(historical, native, progress, attempts, links, start, end,
                             activities=plan_activities),
            'planSubjects': summarise_plan(plan_activities, assigned,
                                          direct_hours_by_subject(native, progress, links)),
            'monthlyOtjh': monthly_otjh_summary(plan_activities, progress),
            'otjh': {'actual': round(old_hours + new_hours, 4) if old_hours is not None and not undated_hours else None,
                     'historical': old_hours, 'new': round(new_hours, 4), 'undatedHistoricalRows': undated_hours}}
    canonical_metrics = canonical_learning.metrics(source.pk) if (dashboard_kind or home_kind) else None
    if canonical_metrics is not None:
        canonical_otjh = canonical_metrics['otjh']
        result['otjh'] = {
            'actual': canonical_otjh.get('completed_actual', canonical_otjh.get('actual')),
            'historical': canonical_otjh.get(
                'historical', canonical_otjh.get('completed_actual', canonical_otjh.get('actual')),
            ),
            'new': canonical_otjh.get('new', 0),
            'planned': canonical_otjh.get('planned'),
            'undatedHistoricalRows': 0,
        }
    if dashboard_kind:
        # The dashboard and dedicated Metrics endpoint expose the same SSOT
        # total; there is no alternate historical/manual fallback.
        result['metrics'] = canonical_metrics
    if home_kind:
        from .home_progress import read_home_progress
        result['homeProgress'] = read_home_progress(source, home_kind,
            plan_activities, native, progress, assigned, end,
            attendance_module_ids=attendance_module_ids, canonical_metrics=canonical_metrics)
    return result


@require_GET
@learner_self_or_staff(kwarg='pk')
@journal_sources.learner_journal_view
def overview_week(request, kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    section = request.GET.get('section')
    read_model_status = None
    try:
        with measure_projection('overview-week', kind=kind, learner_id=pk, section=section) as measurement:
            if section not in (None, 'home', 'dashboard', 'home-summary'):
                return JsonResponse({'error': 'Invalid overview section.'}, status=400)
            home = section == 'home'
            home_summary = section == 'home-summary'
            shared = None
            if home and getattr(settings, 'LEARNER_HOME_SHARED_READ_MODEL_ENABLED', False):
                from .read_model import enqueue_learner_home_refresh, get_learner_home
                shared = get_learner_home(kind, pk)
                if shared is not None:
                    payload = dict(shared.payload)
                    read_model_status = 'STALE' if shared.stale else 'HIT'
                    if shared.stale:
                        enqueue_learner_home_refresh(kind, pk, reason='stale-read')
            if shared is None:
                fields = HOME_SOURCE_FIELDS if home or home_summary else ('id', 'aptem_id', 'email')
                from .case_file_sources import source_for_case_file
                source = source_for_case_file(request, kind, pk)
                if source is None:
                    source = model.all_learners.only(*fields).get(pk=pk)
                with measurement.stage(section or 'overview'):
                    if home:
                        payload = read_week(source, home_kind=kind)
                    elif home_summary:
                        payload = read_week(source, home_kind=kind, home_summary=True)
                    else:
                        payload = read_week(source, dashboard_kind=kind if section == 'dashboard' else None)
                if home and getattr(settings, 'LEARNER_HOME_SHARED_READ_MODEL_ENABLED', False):
                    from .read_model import enqueue_learner_home_refresh
                    enqueue_learner_home_refresh(kind, pk, reason='missing-read-model')
                    read_model_status = 'MISS'
    except model.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except ServiceError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except LookupError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except DatabaseError:
        log.warning('Weekly overview unavailable for %s', pk, exc_info=True)
        return JsonResponse({'error': 'Could not load this week. Please try again.'}, status=503)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    if read_model_status:
        response['X-LMS-Cache'] = read_model_status
    return response
