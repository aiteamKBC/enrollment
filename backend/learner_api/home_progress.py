"""Read-only totals for the learner home card, scoped to its enrolment.

The chart covers the whole assigned plan. Period counts use the recorded
programme start through Sunday of the current UK week, not just this week.
"""
import logging
from math import isfinite

import psycopg
from . import journal_sources
from django.db import DatabaseError, connections

from .active_users import completed_hours_value_from_progress
from .apprenticeship_agreement import _group_dates
from .attendance_lectures import lecture_register
from .subject_dates import as_date
from .training_plan_dashboard import plan_planned_hours, rows
from .otjh_totals import completed_otjh
from . import canonical_learning

log = logging.getLogger(__name__)
ACCEPTED = {'accepted', 'partial'}
SUBMITTED = {'submitted_for_tutor_review', 'submitted', 'pending_review'}


def hours(value):
    try:
        value = float(value)
        return value if isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def counts(items, predicate):
    return {'completed': sum(bool(predicate(item)) for item in items), 'total': len(items)}


def apply_canonical_home_metrics(home, metrics):
    """Overlay the learner/coach shared metrics without replacing home-only data."""
    if not metrics:
        return home
    result = {**home, 'otjh': {**home['otjh']}}
    otjh = metrics.get('otjh') or {}
    actual = otjh.get('completed_actual') if 'completed_actual' in otjh else otjh.get('actual')
    planned = otjh.get('planned')
    result['otjh'].update({
        'actual': actual,
        'planned': planned,
        'percent': round(actual / planned * 100, 2) if actual is not None and planned else None,
    })
    programme = metrics.get('programme') or {}
    result['activities'] = ({'completed': programme.get('completed'),
                             'total': programme.get('total')}
                            if programme.get('status') in {'ready', 'empty'} else None)
    return result


def summarise_home(activities, native, progress, submissions, assigned, start, end,
                   lectures, historical_hours=0):
    """Aggregate explicit activity identities; never infer identity from titles."""
    activities = [dict(row) for row in activities]
    by_component = {str(row['id']): row for row in native}
    assignment_ids = {key for key, row in by_component.items() if row.get('type') == 'assignment'}
    assignment_ids.update(str(row['componentId']) for row in progress
                          if row.get('componentId') and row.get('componentType') == 'assignment')
    # Reflections can have a different activity id; component_ref is canonical.
    marking = {}
    for row in submissions:
        component = str(row.get('component_ref') or row.get('activity_id') or '')
        if component in assignment_ids:
            marking[component] = row
    for row in activities:
        component_ids = row.get('component_ids') or ([str(row['id'])] if row.get('id') else [])
        decisions = [marking[key] for key in component_ids if key in marking]
        if decisions:
            # Submission is separate from completion until the tutor accepts it.
            row['completed'] = any(decision['status'] in ACCEPTED for decision in decisions)

    # An exported activity's hours belong to the activity, not each group it
    # appears in. Prefer the currently authored hours over the imported value.
    planned = {}
    for row in activities:
        key = ('legacy', str(row['activity_id'])) if row.get('activity_id') is not None else row['key']
        value = hours(row.get('expected_hours'))
        priority = int(row.get('expected_source') == 'native' or row['key'][0] == 'native')
        previous = planned.get(key)
        if previous is None or value is not None and (previous[0] is None or priority >= previous[1]):
            planned[key] = (value, priority)
    missing_hours = sum(value is None for value, _ in planned.values())
    total_hours = None if missing_hours else round(sum(value for value, _ in planned.values()), 4)

    # A saved assignment also creates a progress entry. Count its declared
    # hours once, in the bucket matching its latest tutor decision.
    # These records are already scoped to this learner. Retain earned time
    # when an activity is archived or replaced in the current curriculum, just
    # as the historical ledger retains previously completed learning.
    actual = completed_otjh(native, progress, submissions, historical_hours)
    submitted, missing_submitted = 0, 0
    for component, row in marking.items():
        if row.get('imported'):
            continue  # Already represented by the accepted historical ledger.
        value = hours(row.get('actual_time_hours'))
        if value is None:
            matching = [item for item in progress if str(item.get('componentId') or '') == component]
            # Use only recorded time here, never authored planned hours as actual.
            matching = [item for item in matching if item.get('reportedTime')
                        or item.get('claimedSeconds') is not None or item.get('verifiedSeconds') is not None]
            matching = [{key: value for key, value in item.items() if key not in {'expectedOtjh', 'expected_otjh'}}
                        for item in matching]
            value = completed_hours_value_from_progress(matching) if matching else None
        if row['status'] in SUBMITTED and component in assignment_ids:
            missing_submitted += value is None
            submitted += value or 0
    submitted = None if missing_submitted else round(submitted, 4)

    undated = sum(not as_date(row.get('date')) or bool(row.get('date_needs_review')) for row in activities)
    period = [row for row in activities if start and not row.get('date_needs_review')
              and (day := as_date(row.get('date'))) and start <= day <= end]
    assignments = [row for row in period if str(row.get('type') or '').lower() == 'assignment']
    attendance = None if lectures is None or not start else counts(
        [row for row in lectures if (day := as_date(row.get('session_date'))) and start <= day <= end],
        lambda row: row.get('attendance_status') in {'present', 'late'},
    )

    # Unite historical and current module identities only through explicit
    # component export links. Include assigned modules that have no activities.
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            key = parents[key]
        return key

    for row in activities:
        subject = root(row['subject'])
        if row.get('module_id'):
            parents[root(f"current:{row['module_id']}")] = subject
    module_rows = {}
    for module_id, _ in assigned:
        module_rows.setdefault(root(f'current:{module_id}'), [])
    for row in activities:
        module_rows.setdefault(root(row['subject']), []).append(row)
    modules = counts(list(module_rows.values()), lambda items: bool(items) and all(row['completed'] for row in items))
    return {
        'period': {'start': start.isoformat() if start else None, 'end': end.isoformat(), 'timezone': 'Europe/London'},
        'otjh': {'actual': actual, 'submitted': submitted, 'planned': total_hours,
                 'percent': round(actual / total_hours * 100, 2) if actual is not None and total_hours else None,
                 'missingPlannedActivities': missing_hours},
        'activities': counts(period, lambda row: row['completed']) if start else None,
        'assignments': counts(assignments, lambda row: row['completed']) if start else None,
        'lectures': attendance, 'modules': modules, 'undatedActivities': undated,
    }


def read_home_progress(
    source, kind, activities, native, progress, assigned, end, *,
    attendance_module_ids=None, canonical_metrics=None,
):
    start, _, _ = _group_dates(source)
    with connections['enrolment'].cursor() as cur:
        cur.execute('''SELECT activity_id, component_ref, status, actual_time_hours,
            full_submission->>'submissionOrigin' = 'imported_legacy' AS imported
            FROM "Learner".learning_reflection_submissions
            WHERE learner_kind=%s AND learner_id=%s AND activity_type='assignment'
            ORDER BY submitted_at NULLS FIRST,id''', [kind, str(source.pk)])
        submissions = rows(cur)
        historical_hours = 0
    owner = getattr(source, '_canonical_profile', None)
    try:
        # A verified SSOT owner is the enrolment's single learner profile.
        lectures = lecture_register(source, module_ids=attendance_module_ids,
                                    **({'learner_profile_id': owner['id']} if owner else {}))
    except (DatabaseError, psycopg.Error):
        log.warning('Home attendance unavailable for enrolment %s', source.pk, exc_info=True)
        lectures = None
    home = summarise_home(
        activities, native, progress, submissions, assigned, start, end, lectures, historical_hours,
    )
    # Actual and Planned are one programme-wide SSOT pair. Missing consolidated
    # identity fails closed instead of switching to another data source.
    metrics = canonical_metrics if canonical_metrics is not None else canonical_learning.metrics(source.pk)
    result = apply_canonical_home_metrics(home, metrics)
    # The activity denominator is the complete assigned catalogue, exactly as
    # in My Learning, not the number of saved progress entries. Reuse its final
    # calculation (including retained quizzes and live attendance).
    if metrics and (metrics.get('programme') or {}).get('status') in {'ready', 'empty'}:
        from .models import LearnerProfile
        from .module_progress import canonical_module_progress
        owner = owner or canonical_learning.require_profile(source.pk)
        profile = LearnerProfile(id=owner['id'], enrolment_id=source.pk)
        modules = canonical_module_progress(source, profile)
        result['activities'] = {'completed': sum(module['completed'] for module in modules),
                                'total': sum(module['total'] for module in modules)}
        result['modules'] = counts(modules, lambda module: module['total'] > 0
                                   and module['completed'] == module['total'])
    # Use the same verified live credit as My Learning. The register is already
    # scoped to this learner; reuse it rather than fetching attendance twice.
    # Top up only time absent from accepted records, preserving staff overrides.
    from .live_session_progress import read_live_credits, supplement_module_hours
    credits = read_live_credits(source, module_ids=[module for module, _ in assigned],
                               register=lectures) if lectures is not None else []
    if credits and result['otjh']['actual'] is not None:
        owner = owner or canonical_learning.require_profile(source.pk)
        records = canonical_learning.entries_for(owner, lineage_only=True)
        extra = supplement_module_hours(
            {credit['moduleId']: {'hours': {'actual': 0}} for credit in credits}, records, credits)
        actual = round(result['otjh']['actual'] + sum(item['hours']['actual'] for item in extra.values()), 4)
        planned_hours = result['otjh']['planned']
        result['otjh'].update(actual=actual, percent=round(actual / planned_hours * 100, 2)
                              if planned_hours else None)
    planned = (metrics.get('otjh') or {}).get('planned') if metrics else None
    if result['otjh'].get('planned') is None:
        # No SSOT programme plan yet (e.g. a new cohort awaiting the target
        # sync): use the learning-plan total the Overview tab also uses.
        planned = plan_planned_hours(source, getattr(source, '_canonical_profile', None)
                                     or canonical_learning.profile(source.pk))
        actual = result['otjh']['actual']
        result['otjh'].update(planned=planned, percent=round(actual / planned * 100, 2)
                              if actual is not None and planned else None)
    result['otjh']['missingPlannedActivities'] = 0 if planned is not None else None
    return result
