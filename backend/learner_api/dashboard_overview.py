"""Read-only Overview facts. No legacy dashboard response or evidence expansion."""
from collections import Counter, defaultdict
import re
from types import SimpleNamespace
import unicodedata

from django.db import connections
from django.utils import timezone

from . import canonical_learning as canonical, training_plan_dashboard as schedule
from .dashboard_monthly import day, review_day, display_subjects, display_date, MONTH
from .dashboard_training import display_modules, module_rows, percentage


def saved_attendance_rows(source, profile, *, now):
    """Read both saved registers once, excluding unfinished/unmarked sessions."""
    from coach_api.bulk_register import register_rows
    from old_otjh.service import ServiceError
    from .attendance import kbc_attendance_rows
    from .attendance_rules import attendance_outcome

    if profile is None or profile.enrolment_id != source.pk:
        raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)

    def counted(rows, prefix):
        return [{**row, 'sessionDate': row['session_date'].isoformat(),
                 'recordKey': f"{prefix}:{row['session_id']}"}
                for row in rows if attendance_outcome(row, now)[1]]

    historical = counted(kbc_attendance_rows(source), 'kbc')
    live = register_rows([SimpleNamespace(id=profile.pk, _caseload_source=source)])
    return historical, counted(live[str(profile.pk)], 'live')


def attendance_to_date(source, profile, *, now=None, register=None):
    """Overview counts saved KBC history and the final live learner register."""
    from curriculum_api.bulk_attendance_live_policy import merge_live_over_kbc
    from .attendance import _summarize_attendance

    now = now or timezone.now()
    historical, live = register if register is not None else saved_attendance_rows(source, profile, now=now)
    # Match Bulk Attendance's existing day precedence. Filter unfinished rows
    # first so a future/in-progress session cannot hide recorded KBC history.
    rows = merge_live_over_kbc(historical, live)
    summary = _summarize_attendance(rows, now=now, overview_only=True)
    return {**summary, 'attendanceRate': round(100 * summary['present'] / summary['sessions'])} if summary else None


def _attendance_module_name(title):
    """Match moduleGroups.ts: ignore only the standalone Dr/Dr. title."""
    title = unicodedata.normalize('NFKC', str(title or ''))
    title = re.sub(r'[\u2010-\u2015]', '-', title)
    title = re.sub(r'\s+', ' ', title).strip()
    without_dr = re.sub(r'(^|[\s(:-])dr(?:\.\s*|\s+)(?=[^\W\d_])', r'\1', title, flags=re.I).strip()
    key = re.sub(r'\s*-\s*', '-', without_dr).lower()
    return key, without_dr != title


def module_attendance_to_date(displayed, register, *, now, legacy_subjects=()):
    """Project recorded outcomes onto Overview identities, never authored targets."""
    from curriculum_api.bulk_attendance_live_policy import merge_live_over_kbc
    from .attendance import _summarize_attendance
    from .attendance_lectures import _module_key

    historical, live = register
    old_titles = {f"legacy:{s['id']}": s.get('name') for s in legacy_subjects}
    titles = [title for m in displayed for title in (m['title'], m['detail'].get('title'), old_titles.get(m['id']))]
    titles.extend(row.get('module_title') for row in [*historical, *live])
    alias_keys = {key for title in titles for key, has_dr in [_attendance_module_name(title)] if key and has_dr}
    def alias(title):
        key, _ = _attendance_module_name(title)
        return f'alias:{key}' if key in alias_keys else None

    # Explicit module links still win. Different ids may share attendance only
    # when this learner has a Dr variant and the entire remaining title matches.
    aliases_by_module = {str(m['moduleId']): module_alias for m in displayed
                         if m['moduleId'] and (module_alias := alias(m['title']))}
    identities = {m['id']: aliases_by_module.get(str(m['moduleId'])) or alias(m['title']) or
                  (f"module:{m['moduleId']}" if m['moduleId'] else m['id']) for m in displayed}
    native = {str(m['moduleId']): identities[m['id']] for m in displayed if m['moduleId']}
    def title_key(title, without_level):
        return alias(title) or _module_key(title, without_level=without_level)
    by_title = defaultdict(set)
    for module in displayed:
        for title in (module['title'], module['detail'].get('title'), old_titles.get(module['id'])):
            for without_level in (False, True):
                key = title_key(title, without_level)
                if key:
                    by_title[without_level, key].add(identities[module['id']])

    old_by_module, live_by_module = defaultdict(list), defaultdict(list)
    for row in historical:
        for without_level in (False, True):
            matches = by_title[without_level, title_key(row.get('module_title'), without_level)]
            if matches:
                if len(matches) == 1:
                    old_by_module[next(iter(matches))].append(row)
                break
    for row in live:
        identity = native.get(str(row.get('module_catalogue_id')))
        if identity is not None:
            live_by_module[identity].append(row)

    summaries = {}
    for identity in set(identities.values()):
        # Apply overlap precedence within this module: a different module on
        # the same date cannot erase its historical attendance.
        rows = merge_live_over_kbc(old_by_module[identity], live_by_module[identity])
        summary = _summarize_attendance(rows, now=now, overview_only=True)
        summaries[identity] = {'attended': summary['present'], 'total': summary['sessions']} if summary else None
    return {ref: summaries[identity] for ref, identity in identities.items()}


def reviews_to_date(reviews, today):
    unique = {r.get('eventKey'): r for r in reviews
              if r.get('source') != 'student-support' and r.get('status') != 'cancelled'}
    due = [r for r in unique.values() if isinstance(r.get('targetDate') or r.get('date'), str)
           and re.match(r'^\d{4}-\d{2}-\d{2}', r.get('targetDate') or r.get('date'))
           and (r.get('targetDate') or r.get('date'))[:10] <= today]
    completed = sum(r.get('status') == 'completed' for r in due)
    return {'completed': completed, 'total': len(due),
            'percent': percentage(completed, len(due)) if due else None}


def hour_rows(records, targets, weekly, signed_months, current, *, log_summary=None, actual_rows=None):
    """The existing monthlyHours precedence, without document/history payloads."""
    from .dashboard_tabs import monthly_hours
    summary = log_summary if log_summary is not None else monthly_hours({}, records, targets,
        signed_months=signed_months, current_month=current)
    logs = {row['month']: {'completed': row['actual_hours'], 'submitted': row['not_accepted_hours']}
            for row in summary['months']}
    actual = defaultdict(float)
    for row in actual_rows if actual_rows is not None else schedule._canonical_actual_rows({}, records):
        actual[row['month']] += row['hours']
    result = []
    for month in sorted({*logs, *weekly, *actual}):
        if not isinstance(month, str) or not MONTH.fullmatch(month):
            continue
        native = weekly.get(month) or {}
        log = logs.get(month)
        target = max(0, targets[month]) if log is not None and month in targets else native.get('planned')
        completed = max(0, log['completed']) if log is not None else (
            native.get('actual', 0) if native.get('includesHistorical') else
            actual[month] if month in actual else native.get('actual', 0))
        result.append({'key': month, 'target': target,
                       'submitted': max(0, log['submitted']) if log is not None else native.get('submitted', 0),
                       'completed': completed})
    return result


def overview_subjects(learning):
    from .progress_rules import progress_counts_as_achieved
    native = learning['detail']
    subjects = display_subjects(learning, deduplicate=False)
    completed = {str(r['componentId']) for bucket in ('videoProgress', 'componentProgress')
                 for r in native.get(bucket, []) if r.get('componentId')
                 and progress_counts_as_achieved(r.get('kind'), r.get('passed'))}
    components = defaultdict(list)
    for i, component in enumerate([*native.get('components', []), *native.get('retiredQuizComponents', [])]):
        ref = component.get('componentId') or f"quiz:{(component.get('quizMeta') or {}).get('quizId') or str(component.get('week')) + ':' + str(i)}"
        components[(ref, component.get('moduleId'))].append(component)
    legacy_done = {}
    for activity in (learning['activity'] or {}).get('activities', []):
        legacy_done.setdefault((f"legacy:{activity['group_id']}", activity['activity_id']), bool(activity.get('completed')))
    passed_attempts = [a for a in native.get('quizAttempts', []) if a.get('passed') is True]
    passed_quizzes = {str(a.get('quizId')) for a in passed_attempts}
    passed_components = {a.get('componentId') for a in passed_attempts}
    def native_done(component):
        quiz = (component.get('quizMeta') or {}).get('quizId')
        return ((quiz is not None and str(quiz) in passed_quizzes)
                or bool(component.get('componentId') and component['componentId'] in passed_components)) \
            if component.get('isQuiz') else str(component.get('componentId')) in completed
    for subject in subjects:
        module_id = subject.get('moduleId') or (learning['metadata']['builder_subjects'].get(subject['id']) or {}).get('id') \
            or (subject['id'][8:] if subject['id'].startswith('current:') else None)
        for activity in subject['activities']:
            activity['date'] = display_date(activity.get('date'))
            candidates = [c for ref in activity.get('nativeRefs', [activity['id']]) for c in components.get((ref, module_id), [])
                          if module_id is not None or subject['id'] == f"unlinked:{c.get('module')}"]
            done = any(native_done(c) for c in candidates) if activity.get('nativeRefs') else native_done(candidates[0] if candidates else {})
            activity['completed'] = legacy_done.get((subject['id'], activity['id']), False) or done
    return subjects


def read_overview(source, kind, owner, profile, request):
    from .overview_week import read_week
    from .module_progress import aggregate_module_progress
    from .dashboard_metrics import coach_otjh_target_to_date
    from .dashboard_tabs import learning_projection, monthly_hours

    records = canonical.entries_for(owner, training_summary=True)
    targets = canonical.targets_for(owner)
    with connections['enrolment'].cursor() as cur:
        ids, links = schedule.plan_module_ids(cur, source, owner)
        modules = module_rows(cur, ids)
        bounds = schedule._plan_session_rows(cur, str(source.email or '').strip().casefold(), ids,
            rail_only=True, navigation_only=True) if ids else []
    # Reuse narrow catalogue/native facts; no component descriptions or evidence.
    # Populate the existing request-scoped Builder dates cache before learning
    # membership reads it, preserving the old read order without planSubjects.
    weekly = read_week(source, monthly_hours_only=True)
    learning = learning_projection(source, owner, records, compact_content=True)
    native = learning['detail']
    subjects = overview_subjects(learning)
    bounds_by_module = {r['module_id']: r for r in bounds}
    displayed = display_modules(subjects, modules, links, bounds_by_module)
    attendance_now = timezone.now()
    register = saved_attendance_rows(source, profile, now=attendance_now)
    attendance_by_module = module_attendance_to_date(displayed, register, now=attendance_now,
        legacy_subjects=(learning['activity'] or {}).get('subjects', []))
    # The duplicate-date fallback historically used the first row for a module,
    # whereas display_modules uses the last. Preserve both rules explicitly.
    first_bounds_by_module = {}
    for bound in bounds:
        first_bounds_by_module.setdefault(bound['module_id'], bound)
    duplicates = Counter(a.get('dedupe') for s in subjects for a in s['activities'] if a.get('dedupe'))
    ledger = schedule._canonical_module_progress(records, list(modules.values()))
    actual_rows = schedule._canonical_actual_rows(owner, records)
    historical_hours = defaultdict(list)
    for row in actual_rows:
        historical_hours[row['groupId']].append(row['hours'])
    chart_modules = []
    for module in displayed:
        entry = ledger.get(str(module['moduleId']))
        historical = historical_hours.get(module['id'][7:], []) if module['id'].startswith('legacy:') else []
        actual = entry['hours']['actual'] if entry else sum(historical) if historical else None
        chart_modules.append({'id': module['id'], 'title': module['title'], 'start': module['start'], 'end': module['end'],
            'activities': {'completed': module['done'], 'total': module['activityCount']},
            'attendance': attendance_by_module[module['id']],
            'hours': {'actual': actual, 'planned': module['detail'].get('total_otjh')},
            'ksb': {key: entry['ksb'][key] for key in ('completed', 'total')} if entry else None})
        duplicate_rows = [a for a in module['_subject']['activities'] if duplicates.get(a.get('dedupe'), 0) > 1]
        if duplicate_rows:
            # Only cross-subject duplicate identities leave the reader. Browser
            # locale ordering owns their winner, exactly as the existing UI did.
            bound = first_bounds_by_module.get(module['moduleId'], {})
            unique_dates = [a['date'] for a in module['_subject']['activities'] if a.get('date') and not a.get('needsReview')
                            and duplicates.get(a.get('dedupe'), 0) <= 1]
            from .dashboard_training import day_from_session
            unique_dates.extend(day_from_session(schedule.instant(bound[k])) for k in ('first_start', 'last_start') if bound.get(k))
            chart_modules[-1]['duplicates'] = [{'key': a['dedupe'], 'completed': bool(a['completed']),
                'date': (a.get('date') or '') if not a.get('needsReview') else ''} for a in duplicate_rows]
            chart_modules[-1]['dateBounds'] = {'startFixed': bool(day(module['detail'].get('start_date'))),
                'endFixed': bool(day(module['detail'].get('end_date'))),
                'first': min(unique_dates, default=''), 'last': max(unique_dates, default='')}
        if day(module['detail'].get('effectiveEndDate')) > module['end']:
            chart_modules[-1]['deliveryEnd'] = day(module['detail']['effectiveEndDate'])
    # Keep the canonical activity population used by programme module progress.
    # Reuse already-read native completion facts instead of a second ORM read.
    progress = [{**r, 'component_ref': r.get('componentId'),
                 'submitted_at': True if r.get('kind') == 'quiz_reading' else None}
                for bucket in ('videoProgress', 'componentProgress') for r in native.get(bucket, [])]
    metadata = learning['metadata']
    programme = aggregate_module_progress(learning['activity'], native, metadata['current_subjects'],
                                         progress, metadata['builder_subjects'])
    signed = [r['report_month'] for r in canonical.query('''SELECT DISTINCT report_month
        FROM "Learner".learner_monthly_signatures WHERE learner_id=%s AND review_confirmed IS TRUE''', [owner['id']])]
    today = timezone.localdate(timezone=schedule.UK).isoformat()
    log_summary = monthly_hours(owner, records, targets, signed_months=signed, current_month=today[:7])
    reviews = schedule.plan_reviews(source, profile, ('mcr', 'progress-review', 'student-support'), dates_only=True)
    # This authoritative reader intentionally stays separate from the display
    # ledger: its completion population is the established learner metrics contract.
    metrics = canonical.metrics_bulk([source.pk], learner_workspace=True).get(source.pk)
    if metrics is None:
        raise LookupError('Learner identity is unavailable.')
    if metrics['otjh'].get('planned') is None:
        # The plan's module ids were resolved above; do not re-read them.
        metrics['otjh']['planned'] = schedule.plan_planned_hours(source, owner, ids=ids)
    metrics['otjh']['targetToDate'] = coach_otjh_target_to_date(source, metrics['otjh'], owner=owner)
    attendance = attendance_to_date(source, profile, now=attendance_now, register=register)
    payload_months = [*(m for m in targets if m <= today[:7]), *(r['month'] for r in actual_rows), *(review_day(r)[:7] for r in reviews)]
    end = max([day(owner.get('end_date')),
               *(m + '-28' for m in payload_months if MONTH.fullmatch(m))], default='')[:7]
    return {'metrics': {key: {field: metrics[key].get(field) for field in fields} for key, fields in {
                'ksb': ('completed', 'total', 'percent'),
                'otjh': ('actual', 'targetToDate')}.items()},
            'attendance': attendance, 'reviews': reviews_to_date(reviews, today),
            'modules': chart_modules, 'programme': [{key: r[key] for key in ('id', 'title', 'percent')} for r in programme],
            'hours': {'months': hour_rows(records, targets, weekly, signed, today[:7], log_summary=log_summary,
                                         actual_rows=actual_rows),
                      'planned': log_summary['training_plan_totals']['planned_hours'],
                      'end': end},
            'programmeStartDate': day(owner.get('start_date')) or None,
            'programmeEndDate': day(owner.get('end_date')) or None}
