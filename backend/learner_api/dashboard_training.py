"""Training-only read model. Never builds the legacy dashboard response.

Timeline completion still needs programme-wide catalogue/identity facts: source
lineage and retained quiz credit cannot be replaced by a raw COUNT(progress).
Only the selected module receives description, outcomes, staff and sessions.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from math import floor

from django.db import connections
from django.utils import timezone
from old_otjh.service import ServiceError

from . import canonical_learning as canonical, training_plan_dashboard as schedule
from .dashboard_monthly import day, display_subjects, review_day
from .progress_rules import progress_counts_as_achieved


def percentage(done, total):
    return min(100, floor(done / total * 10000 + .5) / 100) if total else 0


def requested_date(request, key):
    value = request.GET.get(key)
    if not value:
        return ''
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise ServiceError('Programme dates must use YYYY-MM-DD.', 'invalid_date', 400)


def learning_facts(source, owner, profile, records, ids, *, selected=False, subject_id=None):
    """Reuse publication, removed-video and retained-quiz rules without bodies.

    The timeline requests identity-only native rows. Rich native facts, including
    KSB mappings, are restricted to one assigned module on a detail request.
    """
    from .weekly_learning import build_learning_content_summary
    from .student_activity_access import student_activity_available
    from .student_activity_data import summarize_activities
    from .builder_activity_dates import read_builder_activity_dates
    from .student_activity import builder_subject_links

    assigned = [{'moduleId': mid} for mid in ids]
    context = getattr(source, '_training_identity_native', None)
    if selected and context is None:
        all_ids = getattr(source, '_training_module_ids', ids)
        context = build_learning_content_summary(source, source.pk, learning_summary=True,
            assigned_modules=[{'moduleId': mid} for mid in all_ids], strict=True, identities_only=True)
        source._training_identity_native = context
    progress = {'progress': (context.get('quizAttempts', []), context.get('videoProgress', []), context.get('componentProgress', []))} if selected else {}
    native = build_learning_content_summary(source, source.pk, learning_summary=True,
                                  assigned_modules=assigned, strict=True,
                                  identities_only=not selected, **progress,
                                  **({'compact_content': True} if selected else {}))
    if not selected:
        source._training_identity_native = native
    else:
        # Reconcile retained quizzes against the WHOLE assignment identity set.
        # Local uniqueness could otherwise credit an ambiguous historical quiz.
        rich = {c.get('componentId'): c for c in native.get('components', []) if c.get('componentId')}
        native['components'] = [{**c, **rich.get(c.get('componentId'), {})} for c in context.get('components', [])]
        native['retiredQuizComponents'] = context.get('retiredQuizComponents', [])
        native['modules'] = context.get('modules', [])
    scope = ({'course_ids': [subject_id[7:]], 'module_ids': []} if subject_id and subject_id.startswith('legacy:')
             else {'module_ids': ids}) if selected else {}
    activity = canonical.source_subjects(source.pk, summarize_activities,
        owner=owner, records=records, include_totals=False, **scope) \
        if student_activity_available(source.aptem_id) else None
    if selected and activity is not None:
        # Suppress native duplicates for other imported courses using membership
        # identities only. Their catalogue/activity rows remain unqueried.
        memberships = canonical.query('''SELECT DISTINCT c.source_course_ref,c.source_course_title,c.curriculum_module_ref
            FROM "Learner".learner_source_course_memberships m
            JOIN curriculum.source_courses c ON c.id=m.source_course_id AND c.deleted_at IS NULL
            WHERE m.learner_id=%s AND m.deleted_at IS NULL AND c.source_system='old_lms'
              AND c.source_course_ref ~ '^[0-9]+$' ''', [owner['id']])
        present = {str(s['id']) for s in activity.get('subjects', [])}
        activity['subjects'] = [*activity.get('subjects', []), *[{
            'id': int(m['source_course_ref']), 'name': m['source_course_title'], 'module_id': m['curriculum_module_ref']}
            for m in memberships if m['source_course_ref'] not in present]]
    context_ids = getattr(source, '_training_module_ids', ids) if selected else ids
    refs = [f'current:{mid}' for mid in context_ids]
    refs.extend(f"legacy:{s['id']}" for s in (activity or {}).get('subjects', []))
    with connections['enrolment'].cursor() as cur:
        # Timeline, selected-module and plan reads share this request's title cache.
        builder = builder_subject_links(cur, source, refs)
        # These facts are reused when the initial timeline also selects a module.
        # Keep scope exact: a different module population must be read independently.
        cached_dates = getattr(source, '_training_activity_dates', None)
        if cached_dates is not None and cached_dates[0] == frozenset(ids):
            dates = cached_dates[1]
        else:
            dates = read_builder_activity_dates(cur, ids)
            source._training_activity_dates = (frozenset(ids), dates)
    learning = {'activity': activity, 'detail': native, 'metadata': {
        'builder_subjects': builder, 'activity_dates': dates,
        'current_subjects': [{'id': mid, 'title': builder.get(f'current:{mid}', {}).get('title') or ''}
                             for mid in context_ids]}}
    subjects = display_subjects(learning)
    completed = {str(r['componentId']) for bucket in ('videoProgress', 'componentProgress')
                 for r in native.get(bucket, []) if r.get('componentId')
                 and progress_counts_as_achieved(r.get('kind'), r.get('passed'))}
    components = {}
    for index, component in enumerate([*native.get('components', []), *native.get('retiredQuizComponents', [])]):
        ref = component.get('componentId') or f"quiz:{(component.get('quizMeta') or {}).get('quizId') or str(component.get('week')) + ':' + str(index)}"
        components.setdefault(ref, component)
    legacy_done = {(f"legacy:{a['group_id']}", a['activity_id']): bool(a.get('completed'))
                   for a in (activity or {}).get('activities', [])}
    passed_attempts = [a for a in native.get('quizAttempts', []) if a.get('passed')]
    passed_quizzes = {str(a.get('quizId')) for a in passed_attempts}
    passed_components = {a.get('componentId') for a in passed_attempts}
    for subject in subjects:
        for item in subject['activities']:
            component = components.get(item['id']) or {}
            quiz = (component.get('quizMeta') or {}).get('quizId')
            done = ((quiz is not None and str(quiz) in passed_quizzes)
                    or bool(component.get('componentId') and component['componentId'] in passed_components)) \
                if component.get('isQuiz') else str(component.get('componentId')) in completed
            item['completed'] = legacy_done.get((subject['id'], item['id']), False) or done
            item['_native'] = bool(component)
            item['_mappingMissing'] = not component or component.get('ksbMappings') is None
    return subjects, native


def module_rows(cur, ids, *, rich_id=None, identity_metadata=None):
    """One metadata read; transfer descriptions for the focused module only."""
    extra_columns = ',m.cover_image_url' if identity_metadata is not None else ''
    module_visibility = " AND (m.deleted_at IS NULL OR COALESCE(m.deleted_via_parent,'')<>'')" if identity_metadata is not None else ''
    cur.execute(f'''SELECT m.module_catalogue_id AS id,m.title{extra_columns},
        CASE WHEN m.module_catalogue_id=%s THEN m.description ELSE '' END AS description,
        m.start_date,m.end_date,m.tutor_name,
        coalesce(nullif(btrim(g.coach_name),''),m.coach_name) AS coach_name,
        m.programme_name,m.cohort_name,m.group_name,m.total_otjh,m.weeks_number,m.sessions_number,m.cohort_id,
        coalesce(nullif(m.session_week_day,''),g.session_week_day) AS session_week_day,
        coalesce(nullif(m.session_start_time,''),g.session_start_time) AS session_start_time,
        coalesce(nullif(m.session_end_time,''),g.session_end_time) AS session_end_time
        FROM curriculum.modules m LEFT JOIN curriculum.groups g ON g.group_id=m.group_id
        WHERE m.module_catalogue_id=ANY(%s){module_visibility} ORDER BY m.title''', [rich_id, ids])
    found = schedule.rows(cur)
    modules = {r['id']: schedule.plan_module(r) for r in found}
    week_order = ',w.display_order' if identity_metadata is not None else ''
    cur.execute(f'''WITH live_counts AS (
        SELECT c.module_catalogue_id,c.week_id,count(*) AS live_session_count
        FROM curriculum.components c WHERE c.module_catalogue_id=ANY(%s)
          AND lower(replace(coalesce(c.type,''),'-','_'))='live_session'
          AND (c.deleted_at IS NULL OR COALESCE(c.deleted_via_parent,'')<>'')
        GROUP BY c.module_catalogue_id,c.week_id
    ) SELECT w.id,w.module_catalogue_id,w.week_number,w.title{week_order},
        CASE WHEN w.module_catalogue_id=%s THEN w.learning_outcomes ELSE '[]'::jsonb END AS learning_outcomes,
        w.holiday_note_enabled,w.holiday_note,
        coalesce(live_counts.live_session_count,0) AS live_session_count
        FROM curriculum.weeks w LEFT JOIN live_counts
          ON live_counts.module_catalogue_id=w.module_catalogue_id AND live_counts.week_id=w.id
        WHERE w.module_catalogue_id=ANY(%s)
          AND (w.deleted_at IS NULL OR COALESCE(w.deleted_via_parent,'')<>'')
        ORDER BY w.display_order,w.week_number,w.id''', [ids, rich_id, ids])
    counts, weeks = defaultdict(int), {}
    week_rows = schedule.rows(cur)
    if identity_metadata is not None:
        from .student_activity import _builder_cover_url
        identity_metadata.update(module_titles={r['id']: r['title'] for r in found},
            weeks=[(r['id'],r['module_catalogue_id'],r['title'],r['week_number'],r.get('display_order')) for r in week_rows],
            covers={f"current:{r['id']}": _builder_cover_url(r.get('cover_image_url')) for r in found})
    for row in week_rows:
        mid = row['module_catalogue_id']
        counts[mid] += 1
        outcomes = [schedule.clean_text(o) for o in schedule.as_list(row['learning_outcomes']) if isinstance(o, str) and schedule.clean_text(o)]
        module = modules.get(mid)
        if module:
            if schedule.is_educational_live_session_week(row['title']):
                module['educational_session_count'] += max(0, int(row['live_session_count'] or 0))
            module['learning_outcomes'] = list(dict.fromkeys([*module['learning_outcomes'], *outcomes]))
        weeks.setdefault((mid, row['week_number']), {'id': row['id'], 'title': schedule.clean_text(row['title']),
            'learningOutcomes': outcomes, 'holidayNote': schedule.clean_text(row['holiday_note']) if row['holiday_note_enabled'] else ''})
    schedule.attach_curriculum_slots(found, modules, counts, weeks, strict=True)
    return modules


def display_modules(subjects, modules, links, bounds):
    """The Training timeline's buildPlanModules identity/date/count rules."""
    result = []
    for subject in subjects:
        mid = (links.get(subject['id']) or {}).get('id') or subject.get('moduleId') or (subject['id'][8:] if subject['id'].startswith('current:') else None)
        detail = modules.get(mid) or {}
        dates = sorted({a['date'] for a in subject['activities'] if a.get('date') and not a.get('needsReview')}
                       | {day_from_session(schedule.instant(bounds.get(mid, {}).get(k))) for k in ('first_start', 'last_start') if bounds.get(mid, {}).get(k)})
        start = day(detail.get('start_date')) or (dates[0] if dates else '')
        end = day(detail.get('end_date')) or (dates[-1] if dates else start)
        done = sum(bool(a.get('completed')) for a in subject['activities'])
        result.append({'id': subject['id'], 'title': subject['title'], 'moduleId': mid,
            'start': start, 'end': end, 'done': done, 'activityCount': len(subject['activities']),
            'progress': percentage(done, len(subject['activities'])), 'dates': dates,
            'detail': detail, '_subject': subject,
            'weekAnchor': delivery_anchor(start, detail, bounds.get(mid, {}).get('first_start'))})
    return result


def delivery_anchor(start, detail, first_session):
    weekdays = {'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'tues': 1,
        'wednesday': 2, 'wed': 2, 'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
        'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5, 'sunday': 6, 'sun': 6}
    days = [weekdays[d.strip().lower()] for d in (detail.get('session_week_day') or '').split(',') if d.strip().lower() in weekdays]
    if start and days:
        initial = date.fromisoformat(start)
        return (initial + timedelta(days=min((d - initial.weekday()) % 7 for d in days))).isoformat()
    slots = sorted(day(s.get('date')) for s in detail.get('curriculumSlots') or [] if s.get('date'))
    return (day_from_session(schedule.instant(first_session)) if first_session else '') or (slots[0] if slots else '') or start


def timeline_module(module):
    detail = module['detail']
    slots = detail.get('curriculumSlots') or []
    return {key: module[key] for key in ('id', 'title', 'moduleId', 'start', 'end', 'done', 'activityCount', 'progress')} | {
        'weekAnchor': module.get('weekAnchor', ''),
        'detail': {key: detail[key] for key in ('effectiveEndDate', 'session_week_day') if key in detail} | {
            'curriculumSlots': [{key: slot[key] for key in ('slotNumber', 'type', 'date', 'sessionNumber', 'weekTitle', 'holidays', 'holidayNote') if key in slot}
                for index, slot in enumerate(slots) if index == 0 or slot.get('holidayNote')]}}


def selected_detail(source, owner, profile, records, selected, modules, links, *, facts=None, metadata=None):
    mid = selected['moduleId']
    ids = [mid] if mid else []
    subjects, native = facts if facts is not None else learning_facts(source, owner, profile, records, ids, selected=True, subject_id=selected['id'])
    subject = next((s for s in subjects if s['id'] == selected['id']), selected['_subject'])
    with connections['enrolment'].cursor() as cur:
        detail = metadata if metadata is not None else module_rows(cur, ids, rich_id=mid).get(mid) or {}
        sessions = schedule.plan_sessions(cur, str(source.email or '').strip().casefold(), ids) if ids else []
    entry = schedule._canonical_module_progress(records, list(modules.values())).get(str(mid))
    activities = subject['activities']
    done = sum(bool(a.get('completed')) for a in activities)
    activity_counts = dict(Counter(a.get('category') or 'activity' for a in activities))
    codes = sorted({k for a in activities for k in a.get('ksbs', [])})
    mapping_missing = any(a.get('_mappingMissing', not a.get('_native')) for a in activities)
    live_activities = defaultdict(list)
    for activity in activities:
        if activity.get('category') == 'live_session':
            live_activities[activity.get('date')].append(activity)
    for session in sessions:
        matches = live_activities.get(day_from_session(session['start']), [])
        if len(matches) == 1:
            session['title'] = matches[0]['title']
    all_dates = {*selected['dates'], *(day_from_session(s['start']) for s in sessions)}
    weeks = {date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday()) for d in all_dates if d}
    actual = entry['hours']['actual'] if entry else None
    if entry is None and selected['id'].startswith('legacy:'):
        group_id = selected['id'][7:]
        historical = schedule._canonical_actual_rows(owner, [r for r in records if str(r.get('module_ref') or '').strip() == group_id])
        if historical:
            actual = sum(r['hours'] for r in historical)
    return {key: selected[key] for key in ('id', 'title', 'moduleId', 'start', 'end')} | {
        'weekAnchor': selected.get('weekAnchor', ''),
        'detail': detail, 'done': done,
        'activityCount': len(activities),
        'progress': percentage(done, len(activities)),
        'activityCounts': activity_counts, 'ksbCodes': codes,
        'achievedKsbCodes': entry['ksb']['achievedCodes'] if entry else None,
        'ksbMappingMissing': mapping_missing, 'weeks': len(weeks),
        'actual': actual,
        'sessions': sessions, 'activities': [{key: a[key] for key in ('id', 'title', 'completed')} for a in activities[:5]]}


def day_from_session(value):
    from .dashboard_monthly import session_day
    return session_day(value)


def read_module_request(request, kind, pk, module_id):
    """Authenticate with precisely the same learner/staff gates as tab reads."""
    from .learner_detail import SOURCE_MODELS
    from .identity import learner_profile_for_source
    from .case_file_sources import source_for_case_file
    from django.db import DatabaseError
    from django.http import JsonResponse
    from .projection_performance import measure_projection
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Dashboard not found.'}, status=404)
    try:
        with measure_projection('dashboard-training-module', kind=kind, learner_id=pk):
            source = source_for_case_file(request, kind, pk)
            if source is None:
                source = model.all_learners.only(*schedule.TRAINING_PLAN_SOURCE_FIELDS).get(pk=pk)
            owner = canonical.require_profile(source.pk)
            source._canonical_profile = owner
            profile = learner_profile_for_source(source, source.pk, heal=False,
                                                 fields=schedule.TRAINING_PLAN_PROFILE_FIELDS)
            if profile is None or profile.pk != owner['id'] or profile.enrolment_id != source.pk:
                raise ServiceError('The consolidated learner identity needs review.', 'identity_review_required', 409)
            source._case_file_profile = profile
            from .dashboard_tabs import install_request_caches
            install_request_caches(source)
            payload = read_training(source, kind, owner, profile, request, module_id)
    except model.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except ServiceError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except LookupError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except DatabaseError:
        return JsonResponse({'error': 'Could not load your module. Please retry.'}, status=503)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response


from django.views.decorators.http import require_GET
from login.permissions import learner_self_or_staff
from .journal_sources import learner_journal_view

training_module = require_GET(learner_self_or_staff(kwarg='pk')(learner_journal_view(read_module_request)))


def read_training(source, kind, owner, profile, request, module_id=None):
    from .dashboard_monthly import validate_month
    requested_month = validate_month(request.GET.get('month'))
    programme_start = requested_date(request, 'programmeStartDate')
    programme_end = requested_date(request, 'programmeEndDate')
    records = canonical.entries_for(owner, training_summary=True)
    with connections['enrolment'].cursor() as cur:
        ids, links = schedule.plan_module_ids(cur, source, owner)
        source._training_module_ids = ids
        if module_id is not None:
            return read_selected(source, owner, profile, records, ids, links, module_id, cur)
        modules = module_rows(cur, ids)
        bound_rows = schedule._plan_session_rows(cur, str(source.email or '').strip().casefold(), ids,
            rail_only=True, navigation_only=True) if ids else []
    bounds = {r['module_id']: r for r in bound_rows}
    subjects, _ = learning_facts(source, owner, profile, records, ids)
    displayed = display_modules(subjects, modules, links, bounds)
    requested = module_id if module_id is not None else request.GET.get('subject')
    reviews = schedule.plan_reviews(source, profile, ('mcr', 'progress-review', 'student-support'), header_only=True)
    targets = canonical.targets_for(owner)
    payload_months = [*targets, *(a.get('reporting_month') or '' for r in records if canonical.counts_as_actual(r) for a in canonical.allocations(r)), *(review_day(r)[:7] for r in reviews)]
    minimum = programme_start or day(owner.get('start_date')) or min((m['start'] for m in displayed if m['start']), default='')
    maximum = max([programme_end or day(owner.get('end_date')), *(max(m['end'], day(m['detail'].get('effectiveEndDate'))) for m in displayed),
                   *(m + '-28' for m in payload_months if len(m) == 7)], default='')
    current = timezone.localdate(timezone=schedule.UK).strftime('%Y-%m')
    month = requested_month or min(max(current, minimum[:7] or current), maximum[:7] or current)
    selected = next((m for m in displayed if m['id'] == requested), None) or next((m for m in displayed if m['start'] and m['start'][:7] <= month <= m['end'][:7]), None)
    coach = schedule.current_coach(source, profile, None)
    coach_name = coach['coach_name']
    if not coach_name and not coach['coach_email'] and schedule.source_coach(source) is None:
        coach_name = schedule.assigned_group_coach(source, list(modules.values()))
    return {'timeline': {'start': minimum[:7], 'end': maximum[:7], 'programmeStartDate': minimum,
        'monthKeys': list(targets), 'modules': [timeline_module(m) for m in displayed],
        'reviews': reviews, 'coachName': coach_name},
        'selectedModuleId': selected['id'] if selected else None,
        'selectedModule': selected_detail(source, owner, profile, records, selected, modules, links) if selected else None}


def read_selected(source, owner, profile, records, ids, links, subject_id, cur):
    """Resolve membership before fetching one module's content facts."""
    mid = subject_id[8:] if subject_id.startswith('current:') else None
    if subject_id.startswith('legacy:'):
        cur.execute('''SELECT c.curriculum_module_ref FROM "Learner".learner_source_course_memberships m
            JOIN curriculum.source_courses c ON c.id=m.source_course_id AND c.deleted_at IS NULL
            WHERE m.learner_id=%s AND m.deleted_at IS NULL AND c.source_system='old_lms'
              AND c.source_course_ref=%s''', [owner['id'], subject_id[7:]])
        memberships = cur.fetchall()
        if not memberships:
            raise ServiceError('Module not found on this learner training plan.', 'module_not_found', 404)
        mid = memberships[0][0]
    elif not mid or mid not in ids:
        raise ServiceError('Module not found on this learner training plan.', 'module_not_found', 404)
    selected_ids = [mid] if mid else []
    metadata = module_rows(cur, selected_ids, rich_id=mid)
    facts = learning_facts(source, owner, profile, records, selected_ids, selected=True, subject_id=subject_id)
    bounds = schedule._plan_session_rows(cur, str(source.email or '').strip().casefold(), selected_ids,
        rail_only=True, navigation_only=True) if selected_ids else []
    displayed = display_modules(facts[0], metadata, links, {r['module_id']: r for r in bounds})
    selected = next((m for m in displayed if m['id'] == subject_id), None)
    if selected is None:
        raise ServiceError('Module not found on this learner training plan.', 'module_not_found', 404)
    # Title fallback in canonical hours must remain unambiguous across the
    # learner's whole assignment set, even while detail reads are scoped.
    cur.execute('SELECT module_catalogue_id AS id,title FROM curriculum.modules WHERE module_catalogue_id=ANY(%s)', [ids])
    all_titles = {r['id']: r for r in schedule.rows(cur)}
    all_titles.update(metadata)
    return selected_detail(source, owner, profile, records, selected, all_titles, links,
                           facts=facts, metadata=metadata.get(mid) or {})
