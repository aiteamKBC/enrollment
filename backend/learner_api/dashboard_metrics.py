"""Read-only programme totals using verified legacy results and local progress."""
import json
import logging
from collections import defaultdict
from types import SimpleNamespace

import psycopg
from . import journal_sources
from django.db import DatabaseError, connections
from curriculum_api.week_visibility import visible_child_sql
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .projection_performance import measure_projection

from audit_api.last_audit_ledger_views import _is_completed
from login.permissions import learner_self_or_staff
from .learner_detail import SOURCE_MODELS
from .models import TrainingPlanDocument
from .progress_rules import progress_counts_as_achieved
from .student_activity_access import student_activity_available
from .student_activity import _direct_progress_records, _direct_progress_otjh, load_direct_progress_records_bulk
from .learning_plan import _effective_plan_ids
from .training_plan_contract import selected_contract
from .training_plan_dashboard import TRAINING_PLAN_SOURCE_FIELDS, find_contract, number, plan_planned_hours, rows
from .otjh_totals import completed_otjh, completed_actual_otjh
from old_otjh.service import ServiceError
from . import canonical_learning

log = logging.getLogger(__name__)
_MISSING = object()


def coach_otjh_target_to_date(source, otjh, *, owner=None):
    from coach_api.selectors.otjh import learner_programme_window
    from coach_api.views import apply_otjh_to_date_metrics
    # Projection callers may reuse this source's already-verified SSOT owner.
    profile = canonical_learning.require_profile(source.pk) if owner is None else owner
    start, end = learner_programme_window(SimpleNamespace(**profile), source)
    data = {'otjhCompleted': otjh.get('actual'), 'otjhPlanned': otjh.get('planned')}
    data.update(otjhProgrammeStartDate=start, plannedEndDate=end)
    return apply_otjh_to_date_metrics(data)['otjhTargetAsOfToday']


def as_json(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def ratio(done, total):
    return {'completed': done, 'total': total,
            'percent': round(done / total * 100, 2) if total else None,
            'status': 'ready' if total else 'empty'}


def unavailable(reason):
    return {'completed': None, 'total': None, 'percent': None,
            'status': 'unavailable', 'reason': reason}


def point_codes(value):
    """A point belongs to a component; the same code in another one counts again."""
    entries = as_json(value, [])
    if not isinstance(entries, list):
        return None
    codes = set()
    for entry in entries:
        code = (entry.get('code') or entry.get('ksbCode') or entry.get('ksb_code')) if isinstance(entry, dict) else entry
        if not isinstance(code, str) or not code.strip():
            return None
        codes.add(code.strip().upper())
    return codes


def programme_totals(historical, native, progress, attempts, links):
    """Union placements by explicit export identity and preserve any completion."""
    completed_ids = {str(item.get('componentId')) for item in progress
                     if item.get('componentId') and progress_counts_as_achieved(item.get('kind'), item.get('passed'))}
    passed_quizzes = {str(item.get('quizId')) for item in progress
                      if item.get('quizId') and progress_counts_as_achieved(item.get('kind'), item.get('passed'))}
    activities = {}
    for item in historical:
        key = (str(item['group_id']), str(item['activity_id']))
        activities[key] = activities.get(key, False) or _is_completed(item) or key in attempts
    old_complete = sum(_is_completed(item) for item in {
        (str(row['group_id']), str(row['activity_id'])): row for row in historical
    }.values())
    for item in native:
        component = str(item['id'])
        key = links.get(component, ('native', component))
        done = component in completed_ids or bool(item.get('quiz_id') and str(item['quiz_id']) in passed_quizzes)
        activities[key] = activities.get(key, False) or done
    return {**ratio(sum(activities.values()), len(activities)), 'historicalCompleted': old_complete}


def read_accepted_ksb_rows(cursor, source, kind):
    """Read accepted monthly activities using explicit learner/activity identity."""
    cursor.execute(f'''SELECT r.id, r.group_id, r.activity_id, r.source_ref,
            coalesce(p.component_ref, s.component_ref) AS component_ref,
            coalesce(nullif(r.title, ''), nullif(a.title, '')) AS activity_title,
            coalesce(j.ksbs, CASE WHEN lk.source_preference='learner' THEN lk.ksbs ELSE ak.ksbs END,
                     a.raw #> '{{live_lms_component,ksbs}}') AS ksb_mappings
        FROM {journal_sources.table('manual_learner_activities')} r
        LEFT JOIN {journal_sources.table('learner_journal_row_ksbs')} j
          ON j.row_id=r.id AND j.aptem_id=r.aptem_id
        LEFT JOIN {journal_sources.table('learner_activity_ksbs')} lk
          ON lk.activity_id=r.activity_id AND lk.aptem_id=r.aptem_id
        LEFT JOIN {journal_sources.table('activity_ksbs')} ak ON ak.activity_id=r.activity_id
        LEFT JOIN "Last_audit".activities a ON a.activity_id=r.activity_id
        LEFT JOIN "Learner".learners l ON l.enrolment_id=%s
        LEFT JOIN "Learner".learner_progress_entries p
          ON p.learner_id=l.id AND r.source_ref='progress:' || p.id::text
        LEFT JOIN "Learner".learning_reflection_submissions s
          ON s.learner_id=%s AND s.learner_kind=%s AND r.source_ref='reflection:' || s.id::text
        WHERE r.aptem_id=%s AND r.accepted=true AND r.deleted_at IS NULL''',
        [source.pk, str(source.pk), kind, int(str(source.aptem_id).strip())])
    return rows(cursor)


def ksb_totals(native, progress, historical=None, attempts=None, links=None, ledger=None):
    completed_ids = {str(item.get('componentId')) for item in progress
                     if item.get('componentId') and progress_counts_as_achieved(item.get('kind'), item.get('passed'))}
    passed_quizzes = {str(item.get('quizId')) for item in progress
                      if item.get('quizId') and progress_counts_as_achieved(item.get('kind'), item.get('passed'))}
    points = {}
    old_points = {}
    missing = set()
    mapped = set()
    attempts, links = attempts or set(), links or {}
    # Journal evidence fills missing catalogue mappings. Never match by title.
    ledger_codes = {}
    native_ids = {str(item['id']) for item in native}
    for item in ledger or []:
        component = str(item.get('component_ref') or '')
        source_parts = str(item.get('source_ref') or '').split(':')
        if not component and len(source_parts) > 1 and source_parts[0] == 'asg' and source_parts[1] in native_ids:
            component = source_parts[1]
        if component:
            key = links.get(component, ('native', component))
        elif item.get('group_id') is not None and item.get('activity_id') is not None:
            key = (str(item['group_id']), str(item['activity_id']))
        else:
            key = ('ledger', str(item.get('source_ref') or item['id']))
        codes = point_codes(item.get('ksb_mappings')) if item.get('ksb_mappings') is not None else None
        if codes is None:
            missing.add(key)
            continue
        ledger_codes.setdefault(key, set()).update(codes)
        mapped.add(key)
        for code in codes:
            old_points[(*key, code)] = True
            points[(*key, code)] = True
    for item in historical or []:
        key = (str(item['group_id']), str(item['activity_id']))
        codes = point_codes(item.get('ksb_mappings')) if item.get('ksb_mappings') is not None else None
        if key in ledger_codes:
            codes = (codes or set()) | ledger_codes[key]
        if codes is None:
            missing.add(key)
            continue
        mapped.add(key)
        done = _is_completed(item) or key in attempts
        for code in codes:
            old_points[(*key, code)] = old_points.get((*key, code), False) or _is_completed(item)
            points[(*key, code)] = points.get((*key, code), False) or done
    historical_done = sum(old_points.values())
    for item in native:
        key = links.get(str(item['id']), ('native', str(item['id'])))
        codes = point_codes(item.get('ksb_mappings'))
        if codes is None:
            missing.add(key)
            continue
        mapped.add(key)
        done = str(item['id']) in completed_ids or key in attempts or bool(item.get('quiz_id') and str(item['quiz_id']) in passed_quizzes)
        for code in codes:
            points[(*key, code)] = points.get((*key, code), False) or done
    missing -= mapped
    if missing:
        return {**unavailable('activity_points_missing'), 'unmappedActivities': len(missing),
                'historicalCompleted': historical_done,
                'mappedCompleted': sum(points.values()), 'mappedTotal': len(points)}
    by_code = {}
    for (*_, code), done in points.items():
        counts = by_code.setdefault(code, [0, 0])
        counts[0] += bool(done)
        counts[1] += 1
    return {**ratio(sum(points.values()), len(points)), 'historicalCompleted': historical_done,
            'codes': [{'code': code, **ratio(*counts)} for code, counts in sorted(by_code.items())]}


def read_planned_hours(source, kind, cursor, preloaded_document=_MISSING, preloaded_contract=_MISSING):
    if preloaded_document is _MISSING:
        document = (TrainingPlanDocument.objects.using('enrolment')
                    .filter(learner_id=source.pk, learner_kind=kind, status=TrainingPlanDocument.STATUS_ACTIVE)
                    .order_by('-created_at', '-id').values('otjh').first())
    else:
        document = preloaded_document
    if document is not None:
        snapshot = as_json(document['otjh'], {})
        planned = number(snapshot.get('plannedTotal')) if isinstance(snapshot, dict) else None
        if planned is not None:
            return planned
    if student_activity_available(source.aptem_id):
        contract = (find_contract(cursor, int(str(source.aptem_id).strip()))
                    if preloaded_contract is _MISSING else preloaded_contract)
        return number(contract.get('training_plan_planned_hours')) if contract else None
    return None


def activity_planned_hours(historical, native, links):
    """Sum the assigned activity plan when a document total is unavailable."""
    planned = {}
    for item in historical:
        key = (str(item['group_id']), str(item['activity_id']))
        hours = number(item.get('expected_hours'))
        if hours is not None or key not in planned:
            planned[key] = hours
    for item in native:
        component = str(item['id'])
        key = links.get(component, ('native', component))
        hours = number(item.get('expected_hours'))
        # The current authored value wins for explicitly linked activities;
        # a missing native value can still use the exact historical mapping.
        if hours is not None or key not in planned:
            planned[key] = hours
    # A partial mapped sum must not masquerade as the whole programme target.
    if not planned or any(value is None for value in planned.values()):
        return None
    return round(sum(planned.values()), 4)


def read_aptem_planned_total(cursor, aptem_id):
    """Read the retained programme total using the learner's Aptem identity."""
    cursor.execute('SELECT planned_hours_total FROM "Last_audit".learners WHERE aptem_id=%s', [aptem_id])
    records = cursor.fetchall()
    return number(records[0][0]) if len(records) == 1 else None


def metrics_from_loaded(source, kind, *, migrated, native, progress,
                        direct_progress, historical, attempts, links, history_ready,
                        manual_hours=_MISSING, preloaded=None):
    """Finish dashboard metrics from the activity snapshot already in memory.

    ``overview-week?section=dashboard`` and the standalone metrics endpoint use
    this same calculation.  Keeping the expensive subject, component, legacy
    activity and export-link reads outside this function lets the Dashboard
    load them once without changing the metric definitions used elsewhere.
    """
    if canonical_learning.enabled(source.pk):
        return canonical_learning.metrics(source.pk)
    with connections['enrolment'].cursor() as cursor:
        planned_document = (preloaded or {}).get('planned_hours_document', _MISSING)
        planned_contract = (preloaded or {}).get('planned_hours_contract', _MISSING)
        planned = read_planned_hours(source, kind, cursor, planned_document, planned_contract)
        aptem_planned_total = (
            (preloaded or {}).get('aptem_planned_total', _MISSING)
            if migrated else None
        )
        if aptem_planned_total is _MISSING:
            aptem_planned_total = read_aptem_planned_total(cursor, int(str(source.aptem_id).strip()))
        old_hours = 0 if not migrated else None
        historical_refs = []
        if migrated:
            if manual_hours is not _MISSING:
                old_hours = manual_hours
            else:
                cursor.execute(f'''SELECT SUM(actual_hours), array_agg(source_ref)
                    FROM {journal_sources.table('manual_learner_activities')}
                    WHERE aptem_id=%s AND accepted=true AND deleted_at IS NULL''',
                    [int(str(source.aptem_id).strip())])
                retained = cursor.fetchone()
                old_hours = number(retained[0])
                historical_refs = retained[1] or []
    submissions_available = True
    try:
        if preloaded is not None and 'reflection_submissions' in preloaded:
            submissions_available = preloaded['reflection_submissions'] is not None
            submissions = preloaded['reflection_submissions'] or []
        else:
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('''SELECT id, progress_entry_id, submitted_at, activity_id, component_ref, status, actual_time_hours,
                        full_submission->>'submissionOrigin' = 'imported_legacy' AS imported
                    FROM "Learner".learning_reflection_submissions
                    WHERE learner_kind=%s AND learner_id=%s AND activity_type='assignment'
                    ORDER BY submitted_at NULLS FIRST,id''', [kind, str(source.pk)])
                submissions = rows(cursor)
    except (DatabaseError, psycopg.Error, StopIteration):
        # Preserve the existing response while exposing unknown completed time.
        submissions = []
        submissions_available = False
    if planned is None and history_ready:
        planned = activity_planned_hours(historical, native, links)
    actual_hours = completed_otjh(native, direct_progress, submissions, old_hours)
    ledger = []
    if migrated and history_ready:
        if preloaded is not None and 'accepted_ksb_rows' in preloaded:
            ledger = preloaded['accepted_ksb_rows']
        else:
            with connections['enrolment'].cursor() as cursor:
                ledger = read_accepted_ksb_rows(cursor, source, kind)
        historical_refs = list(set(historical_refs) | {row['source_ref'] for row in ledger if row.get('source_ref')})
    new_hours = (round(actual_hours - old_hours, 4)
                 if actual_hours is not None and old_hours is not None else round(_direct_progress_otjh(direct_progress), 4))
    # Private evidence rows are consumed by the coach case-file serializer to
    # enrich the existing KSB browser payload. They deliberately remain
    # separate from the canonical percentage calculation below.
    ksb_evidence_sources = []
    if migrated:
        for index, item in enumerate([*(historical or []), *(ledger or [])]):
            if not isinstance(item, dict):
                continue
            if item in (historical or []) and not _is_completed(item):
                continue
            codes = point_codes(item.get('ksb_mappings'))
            if not codes:
                continue
            source_id = str(item.get('source_ref') or f"audit:{item.get('group_id')}:{item.get('activity_id')}:{index}")
            ksb_evidence_sources.append({
                'id': source_id,
                'title': str(item.get('activity_title') or item.get('title') or 'Historical Activity'),
                'typeLabel': 'Historical Activity',
                'source': 'Aptem',
                'activityId': str(item.get('activity_id') or '') or None,
                'completedAt': item.get('completed_at') or None,
                'activityDate': item.get('activity_date') or None,
                'status': item.get('status') or None,
                'module': item.get('module_title') or None,
                'componentId': str(item.get('component_ref') or '') or None,
                'codes': sorted(codes),
            })
    return {
        'migrated': migrated,
        'aptem_planned_total': aptem_planned_total,
        'programme': programme_totals(historical, native, progress, attempts, links) if history_ready
                     else unavailable('historical_activities_missing'),
        'otjh': {'historical': old_hours, 'new': new_hours,
                 'completed_actual': completed_actual_otjh(native, direct_progress,
                     submissions if submissions_available else None, old_hours, historical_refs),
                 'actual': actual_hours, 'planned': planned},
        'ksb': ksb_totals(native, progress, historical, attempts, links, ledger) if history_ready
               else unavailable('historical_activities_missing'),
        '_ksb_evidence_sources': ksb_evidence_sources,
    }


def _read_legacy_metrics(source, kind, preloaded=None):
    """Retained only for isolated migration comparisons; never used by live endpoints."""
    migrated = student_activity_available(source.aptem_id)
    direct_progress = (preloaded or {}).get('direct_progress') if preloaded is not None else None
    if direct_progress is None:
        direct_progress = _direct_progress_records(source.pk)
    historical, attempts, links = [], set(), {}
    history_ready = not migrated
    with connections['enrolment'].cursor() as cursor:
        module_ids = ((preloaded or {}).get('effective_plan_ids', _MISSING)
                      if preloaded is not None else _MISSING)
        if module_ids is _MISSING:
            module_ids = _effective_plan_ids(source, {})
        if preloaded is not None and 'native_components' in preloaded:
            native = preloaded['native_components']
        else:
            cursor.execute('''SELECT c.id,c.type,c.expected_otjh AS expected_hours,coalesce(nullif(c.ksb_mappings,'[]'::jsonb),
                (SELECT jsonb_agg(jsonb_build_object('code',k.ksb_code))
                 FROM curriculum.ksb_mappings k WHERE k.component_id=c.id
                   AND ''' + visible_child_sql('k') + '''), '[]'::jsonb) AS ksb_mappings,
                coalesce((SELECT q.quiz_id::text FROM curriculum.quiz_component_links q
                          WHERE q.component_id=c.id ORDER BY q.id LIMIT 1),
                         c.settings_json->>'linkedQuizId') AS quiz_id
                FROM curriculum.components c
                WHERE c.module_catalogue_id=ANY(%s)
                  AND ''' + visible_child_sql('c'), [module_ids])
            native = rows(cursor)
        if preloaded is not None and 'native_progress' in preloaded:
            progress = preloaded['native_progress']
        else:
            cursor.execute('''SELECT p.component_ref AS "componentId",p.quiz_ref AS "quizId",p.kind,p.passed
                FROM "Learner".learners l JOIN "Learner".learner_progress_entries p ON p.learner_id=l.id
                WHERE l.enrolment_id=%s AND p.kind<>'activity_event' ''', [source.pk])
            # Imported completions of explicitly assigned native components remain
            # achievements too. Only OTJ hours above exclude the imported mirror.
            progress = rows(cursor)
        if migrated:
            aptem_id = int(str(source.aptem_id).strip())
            audit_input = (preloaded or {}).get('audit_inputs') if preloaded is not None else None
            if audit_input is not None:
                identity = audit_input.get('identity')
            else:
                cursor.execute('SELECT learner_email FROM "Last_audit".learners WHERE aptem_id=%s', [aptem_id])
                identity = cursor.fetchone()
            if identity and identity[0] and source.email and identity[0].strip().casefold() != source.email.strip().casefold():
                raise ValueError('The previous learning identity could not be verified.')
            history_ready = identity is not None
            if history_ready:
                if audit_input is not None:
                    historical = audit_input.get('historical', [])
                else:
                    cursor.execute(f'''SELECT gl.group_id,ga.activity_id,
                    r.status,r.video_completed,
                    r.reading_viewed,r.quiz_passed,a.quiz_id,a.reading_type,ph.planned_hours AS expected_hours,
                    CASE WHEN nullif(a.reading_iframe_url,'') IS NOT NULL THEN 'present' ELSE '' END AS reading_iframe_url,
                    CASE WHEN jsonb_typeof(a.quiz_questions)='array' AND a.quiz_questions<>'[]'::jsonb
                         THEN '[{{}}]'::jsonb ELSE '[]'::jsonb END AS quiz_questions,
                    jsonb_path_query_array(CASE WHEN lk.source_preference='learner' THEN lk.ksbs ELSE ak.ksbs END,
                                           '$[*].code') AS ksb_mappings
                    FROM "Last_audit".learners l
                    JOIN "Last_audit".group_learners gl ON gl.learner_id=l.learner_id
                    JOIN "Last_audit".group_activities ga ON ga.group_id=gl.group_id
                    JOIN "Last_audit".activities a ON a.activity_id=ga.activity_id
                    LEFT JOIN {journal_sources.table('learner_activity_ksbs')} lk
                        ON lk.aptem_id=l.aptem_id AND lk.activity_id=ga.activity_id
                    LEFT JOIN {journal_sources.table('activity_ksbs')} ak ON ak.activity_id=ga.activity_id
                    LEFT JOIN "Last_audit".activity_results r ON r.learner_id=l.learner_id
                        AND r.group_id=gl.group_id AND r.activity_id=ga.activity_id
                    LEFT JOIN "Last_audit".activity_planned_hours ph ON ph.learner_id=l.learner_id AND ph.aptem_id=l.aptem_id
                        AND ph.ref=ga.activity_id::text AND ph.kind=CASE lower(coalesce(a.activity_type,r.activity_type))
                            WHEN 'video' THEN 'video' WHEN 'audio' THEN 'audio' WHEN 'reading+quiz' THEN 'reading_quiz' END
                        WHERE l.aptem_id=%s''', [aptem_id])
                # Dashboard totals use the verified audit snapshot as their
                # stable baseline. The external LMS inventory is a separate
                # view and can contain newly published, unmapped activities;
                # mixing it here changes both the denominator and KSB status.
                    historical = rows(cursor)
                # Human-readable evidence labels are optional.  Keep this query
                # separate from the core historical inputs above so a retired or
                # partially migrated metadata column cannot take metrics down.
                if history_ready and historical and not (
                    preloaded is not None and preloaded.get('historical_metadata_loaded')
                ):
                    try:
                        cursor.execute('''SELECT ga.group_id,ga.activity_id,g.group_name,
                                a.title,a.activity_date
                            FROM "Last_audit".learners l
                            JOIN "Last_audit".group_learners gl ON gl.learner_id=l.learner_id
                            JOIN "Last_audit".groups g ON g.group_id=gl.group_id
                            JOIN "Last_audit".group_activities ga ON ga.group_id=gl.group_id
                            JOIN "Last_audit".activities a ON a.activity_id=ga.activity_id
                            WHERE l.aptem_id=%s''', [aptem_id])
                        metadata = {
                            (str(group_id), str(activity_id)): {
                                'module_title': module_title,
                                'activity_title': title,
                                'activity_date': activity_date,
                            }
                            for group_id, activity_id, module_title, title, activity_date in cursor.fetchall()
                        }
                        for item in historical:
                            item.update(metadata.get((str(item.get('group_id')), str(item.get('activity_id'))), {}))
                    except (DatabaseError, psycopg.Error, StopIteration):
                        log.warning('Optional historical evidence metadata unavailable for %s', aptem_id, exc_info=True)
                if preloaded is not None and 'subject_attempts' in preloaded:
                    attempts = preloaded['subject_attempts']
                else:
                    cursor.execute('''SELECT DISTINCT group_id,activity_id FROM "Learner".subject_activity_attempts
                        WHERE enrolment_id=%s AND aptem_id=%s AND completed=true
                          AND submitted_at IS NOT NULL
                          AND coalesce(definition->>'catalogue_kind','material')='material' ''', [source.pk, aptem_id])
                    attempts = {(str(group), str(activity)) for group, activity in cursor.fetchall()}
                if preloaded is not None and 'export_links' in preloaded:
                    export_rows = preloaded['export_links']
                else:
                    cursor.execute('''WITH exports AS (
                    SELECT course_id,CASE WHEN jsonb_typeof(curriculum)='string'
                        THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
                    FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
                    SELECT course_id,material->>'source_component_id',material->>'component_id'
                    FROM exports CROSS JOIN LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(payload->'sections')='array' THEN payload->'sections' ELSE '[]'::jsonb END) section
                    CROSS JOIN LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(section->'materials')='array' THEN section->'materials' ELSE '[]'::jsonb END) material''',
                                   [sorted({int(item['group_id']) for item in historical})])
                    export_rows = cursor.fetchall()
                candidates = {}
                for group, activity, component in export_rows:
                    if component and activity:
                        candidates.setdefault(str(component), set()).add((str(group), str(activity)))
                links = {component: next(iter(keys)) for component, keys in candidates.items() if len(keys) == 1}
    result = metrics_from_loaded(source, kind, migrated=migrated, native=native,
        progress=progress, direct_progress=direct_progress, historical=historical,
        attempts=attempts, links=links, history_ready=history_ready,
        manual_hours=(preloaded.get('manual_hours', _MISSING) if preloaded is not None else _MISSING),
        preloaded=preloaded)
    return result


def read_metrics(source, kind, preloaded=None):
    """Return programme totals exclusively from the consolidated SSOT."""
    return canonical_learning.metrics(source.pk)


@require_GET
@learner_self_or_staff(kwarg='pk')
@journal_sources.learner_journal_view
def learner_metrics(request, kind, pk):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return JsonResponse({'error': 'Unknown learner kind.'}, status=404)
    try:
        with measure_projection('metrics', kind=kind, learner_id=pk) as measurement:
            source = model.all_learners.only('id', 'aptem_id', 'email').get(pk=pk)
            with measurement.stage('metrics'):
                if request.GET.get('view') == 'coach-ksb-breakdown':
                    from .aptem_ksb_breakdown import read_learner_breakdown
                    payload = read_learner_breakdown(connections['enrolment'], source.pk)
                elif request.GET.get('view') == 'learner-overview':
                    payload = canonical_learning.metrics_bulk(
                        [source.pk], learner_workspace=True, include_ksb_points=True,
                    ).get(source.pk)
                    if payload is None:
                        raise ValueError('Learner identity is unavailable.')
                    if payload['otjh'].get('planned') is None:
                        # No SSOT programme plan yet: the target follows the
                        # learning-plan total, as the home progress card does.
                        plan_source = model.all_learners.only(*TRAINING_PLAN_SOURCE_FIELDS).get(pk=source.pk)
                        payload['otjh']['planned'] = plan_planned_hours(
                            plan_source, canonical_learning.profile(source.pk))
                    payload['otjh']['targetToDate'] = coach_otjh_target_to_date(source, payload['otjh'])
                else:
                    payload = read_metrics(source, kind)
    except model.DoesNotExist:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    except ServiceError as error:
        return JsonResponse({'error': str(error)}, status=error.status)
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=409)
    except (DatabaseError, psycopg.Error) as error:
        log.warning(
            '[learner_metrics] learner=%s stage=read_metrics failed exception=%s message=%s',
            pk, type(error).__name__, str(error), exc_info=True,
        )
        log.warning('Learner metrics unavailable for %s', pk, exc_info=True)
        return JsonResponse({'error': 'Could not load programme totals. Please try again.'}, status=503)
    payload.pop('_ksb_evidence_sources', None)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'private, no-store'
    return response


def load_subject_attempts_bulk(keys):
    """Load completed submitted attempts for all stable enrolment/Aptem pairs."""
    pairs = list(dict.fromkeys((int(enrolment), int(aptem)) for enrolment, aptem in (keys or []) if enrolment is not None and aptem is not None))
    result = {pair: set() for pair in pairs}
    if not pairs:
        return result
    placeholders = ','.join(['(%s,%s)'] * len(pairs))
    params = [value for pair in pairs for value in pair]
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''SELECT DISTINCT enrolment_id, aptem_id, group_id, activity_id
            FROM "Learner".subject_activity_attempts
            WHERE (enrolment_id, aptem_id) IN ({placeholders})
              AND completed=true AND submitted_at IS NOT NULL
              AND coalesce(definition->>'catalogue_kind','material')='material' ''', params)
        for enrolment_id, aptem_id, group_id, activity_id in cursor.fetchall():
            result.setdefault((int(enrolment_id), int(aptem_id)), set()).add((str(group_id), str(activity_id)))
    return result


def load_manual_hours_bulk(aptem_ids):
    """Sum accepted, non-deleted manual hours by stable Aptem id."""
    ids = list(dict.fromkeys(int(value) for value in (aptem_ids or []) if value not in (None, '')))
    if not ids:
        return {}
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''SELECT aptem_id, SUM(actual_hours)
            FROM {journal_sources.table('manual_learner_activities')}
            WHERE aptem_id=ANY(%s) AND accepted=true AND deleted_at IS NULL
            GROUP BY aptem_id''', [ids])
        return {int(aptem_id): number(total) for aptem_id, total in cursor.fetchall()}


def load_reflection_submissions_bulk(keys):
    """Load assignment reflection submissions keyed by (learner kind, profile id)."""
    pairs = list(dict.fromkeys((str(kind), str(profile_id)) for kind, profile_id in (keys or []) if profile_id is not None))
    result = {pair: [] for pair in pairs}
    if not pairs:
        return result
    clauses = ' OR '.join(['(learner_kind=%s AND learner_id=%s)'] * len(pairs))
    params = [value for pair in pairs for value in pair]
    try:
        with connections['enrolment'].cursor() as cursor:
            cursor.execute(f'''SELECT learner_kind, learner_id, id, progress_entry_id, submitted_at, activity_id, component_ref, status, actual_time_hours,
                    full_submission->>'submissionOrigin' = 'imported_legacy' AS imported
                FROM "Learner".learning_reflection_submissions
                WHERE activity_type='assignment' AND ({clauses})
                ORDER BY submitted_at NULLS FIRST,id''', params)
            for learner_kind, learner_id, entry_id, progress_entry_id, submitted_at, activity_id, component_ref, status, actual_time_hours, imported in cursor.fetchall():
                result.setdefault((str(learner_kind), str(learner_id)), []).append({
                    'id': entry_id, 'progress_entry_id': progress_entry_id, 'submitted_at': submitted_at,
                    'activity_id': activity_id, 'component_ref': component_ref, 'status': status,
                    'actual_time_hours': actual_time_hours, 'imported': imported,
                })
    except (DatabaseError, psycopg.Error, StopIteration):
        return {pair: None for pair in pairs}
    return result


def load_audit_inputs_bulk(aptem_ids):
    """Load audit identity and historical activity rows by stable Aptem id."""
    ids = list(dict.fromkeys(int(value) for value in (aptem_ids or []) if value not in (None, '')))
    result = {
        aptem_id: {'identity': None, 'historical': [], 'aptem_planned_total': None}
        for aptem_id in ids
    }
    if not ids:
        return result
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('SELECT aptem_id, learner_email, planned_hours_total FROM "Last_audit".learners WHERE aptem_id=ANY(%s)', [ids])
        identity_rows = defaultdict(list)
        for aptem_id, email, planned_total in cursor.fetchall():
            identity_rows[int(aptem_id)].append((email, planned_total))
        for aptem_id, matches in identity_rows.items():
            audit = result.setdefault(
                aptem_id,
                {'identity': None, 'historical': [], 'aptem_planned_total': None},
            )
            audit['identity'] = (matches[0][0],)
            audit['aptem_planned_total'] = number(matches[0][1]) if len(matches) == 1 else None
        cursor.execute(f'''SELECT l.aptem_id,gl.group_id,ga.activity_id,r.status,r.video_completed,
            r.reading_viewed,r.quiz_passed,a.quiz_id,a.reading_type,ph.planned_hours AS expected_hours,
            CASE WHEN nullif(a.reading_iframe_url,'') IS NOT NULL THEN 'present' ELSE '' END AS reading_iframe_url,
            CASE WHEN jsonb_typeof(a.quiz_questions)='array' AND a.quiz_questions<>'[]'::jsonb
                 THEN '[{{}}]'::jsonb ELSE '[]'::jsonb END AS quiz_questions,
            jsonb_path_query_array(CASE WHEN lk.source_preference='learner' THEN lk.ksbs ELSE ak.ksbs END,
                                   '$[*].code') AS ksb_mappings,
            g.group_name AS module_title,a.title AS activity_title,a.activity_date
            FROM "Last_audit".learners l
            JOIN "Last_audit".group_learners gl ON gl.learner_id=l.learner_id
            JOIN "Last_audit".group_activities ga ON ga.group_id=gl.group_id
            JOIN "Last_audit".activities a ON a.activity_id=ga.activity_id
            LEFT JOIN "Last_audit".groups g ON g.group_id=gl.group_id
            LEFT JOIN {journal_sources.table('learner_activity_ksbs')} lk
                ON lk.aptem_id=l.aptem_id AND lk.activity_id=ga.activity_id
            LEFT JOIN {journal_sources.table('activity_ksbs')} ak ON ak.activity_id=ga.activity_id
            LEFT JOIN "Last_audit".activity_results r ON r.learner_id=l.learner_id
                AND r.group_id=gl.group_id AND r.activity_id=ga.activity_id
            LEFT JOIN "Last_audit".activity_planned_hours ph ON ph.learner_id=l.learner_id AND ph.aptem_id=l.aptem_id
                AND ph.ref=ga.activity_id::text AND ph.kind=CASE lower(coalesce(a.activity_type,r.activity_type))
                    WHEN 'video' THEN 'video' WHEN 'audio' THEN 'audio' WHEN 'reading+quiz' THEN 'reading_quiz' END
            WHERE l.aptem_id=ANY(%s)''', [ids])
        for item in rows(cursor):
            aptem_id = int(item.pop('aptem_id'))
            result.setdefault(
                aptem_id,
                {'identity': None, 'historical': [], 'aptem_planned_total': None},
            )['historical'].append(item)
    return result


def load_contracts_bulk(aptem_ids):
    """Select the same training-plan contract as ``find_contract``, once per Aptem id."""
    ids = list(dict.fromkeys(int(value) for value in (aptem_ids or []) if value not in (None, '')))
    result = {aptem_id: None for aptem_id in ids}
    if not ids:
        return result
    candidates = defaultdict(list)
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT c.learner_id AS aptem_id,c.id,c.azure_path,c.training_plan_planned_hours,
                c.document_name AS original_name,
                coalesce(nullif(a.display_name,''),c.document_name) AS document_name,
                c.date,c.fetched_at,c.fully_signed_date,c.raw AS extraction_metadata,
                c.program_start_date,c.planned_end_date
            FROM fetching_evidence.aptem_cv_contracts_probe c
            LEFT JOIN "Audit".contract_document_archive a ON a.contract_id=c.id
            WHERE c.learner_id=ANY(%s)
              AND lower(coalesce(nullif(a.display_name,''),c.document_name)) ~ 'training[[:space:]_-]*plan'
              AND a.archived_at IS NULL AND a.deleted_at IS NULL
            ORDER BY c.learner_id,coalesce(c.fully_signed_date,c.date) DESC NULLS LAST,c.id DESC''', [ids])
        for item in rows(cursor):
            candidates[int(item.pop('aptem_id'))].append(item)
    for aptem_id in ids:
        result[aptem_id] = selected_contract(candidates.get(aptem_id, []))
    return result


def load_accepted_ksb_rows_bulk(keys):
    """Load accepted KSB ledger rows by stable enrolment/Aptem/kind identity."""
    triples = list(dict.fromkeys(
        (int(enrolment_id), int(aptem_id), str(kind))
        for enrolment_id, aptem_id, kind in (keys or [])
        if enrolment_id is not None and aptem_id not in (None, '')
    ))
    result = {enrolment_id: [] for enrolment_id, _aptem_id, _kind in triples}
    if not triples:
        return result
    placeholders = ','.join(['(%s,%s,%s)'] * len(triples))
    params = [value for triple in triples for value in triple]
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''WITH requested(enrolment_id,aptem_id,learner_kind) AS (VALUES {placeholders}f)
            SELECT requested.enrolment_id,r.id,r.group_id,r.activity_id,r.source_ref,
                coalesce(p.component_ref,s.component_ref) AS component_ref,
                coalesce(nullif(r.title, ''),nullif(a.title, '')) AS activity_title,
                coalesce(j.ksbs,CASE WHEN lk.source_preference='learner' THEN lk.ksbs ELSE ak.ksbs END,
                         a.raw #> '{{{{live_lms_component,ksbs}}}}') AS ksb_mappings
            FROM requested
            JOIN {journal_sources.table('manual_learner_activities')} r
              ON r.aptem_id=requested.aptem_id
            LEFT JOIN {journal_sources.table('learner_journal_row_ksbs')} j
              ON j.row_id=r.id AND j.aptem_id=r.aptem_id
            LEFT JOIN {journal_sources.table('learner_activity_ksbs')} lk
              ON lk.activity_id=r.activity_id AND lk.aptem_id=r.aptem_id
            LEFT JOIN {journal_sources.table('activity_ksbs')} ak ON ak.activity_id=r.activity_id
            LEFT JOIN "Last_audit".activities a ON a.activity_id=r.activity_id
            LEFT JOIN "Learner".learners l ON l.enrolment_id=requested.enrolment_id
            LEFT JOIN "Learner".learner_progress_entries p
              ON p.learner_id=l.id AND r.source_ref='progress:' || p.id::text
            LEFT JOIN "Learner".learning_reflection_submissions s
              ON s.learner_id=requested.enrolment_id::text
             AND s.learner_kind=requested.learner_kind
             AND r.source_ref='reflection:' || s.id::text
            WHERE r.accepted=true AND r.deleted_at IS NULL''', params)
        for item in rows(cursor):
            enrolment_id = int(item.pop('enrolment_id'))
            result.setdefault(enrolment_id, []).append(item)
    return result


def load_native_progress_bulk(enrolment_ids):
    """Load native learner progress keyed by stable enrolment ID."""
    ids = list(dict.fromkeys(int(value) for value in (enrolment_ids or []) if value is not None))
    result = {enrolment_id: [] for enrolment_id in ids}
    if not ids:
        return result
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT l.enrolment_id,p.component_ref AS "componentId",p.quiz_ref AS "quizId",p.kind,p.passed
            FROM "Learner".learners l
            JOIN "Learner".learner_progress_entries p ON p.learner_id=l.id
            WHERE l.enrolment_id=ANY(%s) AND p.kind<>'activity_event' ''', [ids])
        for enrolment_id, component_id, quiz_id, kind, passed in cursor.fetchall():
            result.setdefault(int(enrolment_id), []).append({
                'componentId': component_id, 'quizId': quiz_id, 'kind': kind, 'passed': passed,
            })
    return result


def load_native_components_bulk(module_ids):
    """Load native components/KSB mappings keyed by module catalogue ID."""
    ids = list(dict.fromkeys(str(value) for value in (module_ids or []) if value not in (None, '')))
    result = {module_id: [] for module_id in ids}
    if not ids:
        return result
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT c.module_catalogue_id,c.id,c.type,c.expected_otjh AS expected_hours,
                coalesce(nullif(c.ksb_mappings,'[]'::jsonb),
                (SELECT jsonb_agg(jsonb_build_object('code',k.ksb_code))
                 FROM curriculum.ksb_mappings k WHERE k.component_id=c.id
                   AND ''' + visible_child_sql('k') + '''), '[]'::jsonb) AS ksb_mappings,
                coalesce((SELECT q.quiz_id::text FROM curriculum.quiz_component_links q
                          WHERE q.component_id=c.id ORDER BY q.id LIMIT 1),
                         c.settings_json->>'linkedQuizId') AS quiz_id
            FROM curriculum.components c
            WHERE c.module_catalogue_id=ANY(%s)
              AND ''' + visible_child_sql('c'), [ids])
        for item in rows(cursor):
            result.setdefault(str(item.pop('module_catalogue_id')), []).append(item)
    return result


def load_planned_hours_documents_bulk(keys):
    """Select the latest active training-plan document per (learner, kind)."""
    pairs = list(dict.fromkeys((int(learner_id), str(kind)) for learner_id, kind in (keys or [])))
    result = {pair: None for pair in pairs}
    if not pairs:
        return result
    from django.db.models import Q
    query = Q()
    for learner_id, kind in pairs:
        query |= Q(learner_id=learner_id, learner_kind=kind)
    documents = (TrainingPlanDocument.objects.using('enrolment')
                 .filter(query, status=TrainingPlanDocument.STATUS_ACTIVE)
                 .order_by('learner_id', 'learner_kind', '-created_at', '-id')
                 .values('learner_id', 'learner_kind', 'otjh'))
    for document in documents:
        pair = (int(document['learner_id']), str(document['learner_kind']))
        if pair in result and result[pair] is None:
            result[pair] = {'otjh': document['otjh']}
    return result


def load_export_links_bulk(group_ids):
    """Load raw course-curriculum export rows keyed by audit group id."""
    ids = list(dict.fromkeys(int(value) for value in (group_ids or []) if value is not None))
    result = {group_id: [] for group_id in ids}
    if not ids:
        return result
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''WITH exports AS (
                SELECT course_id,CASE WHEN jsonb_typeof(curriculum)='string'
                    THEN (curriculum #>> '{}')::jsonb ELSE curriculum END AS payload
                FROM "MBA".course_curriculum WHERE course_id=ANY(%s))
            SELECT course_id,material->>'source_component_id',material->>'component_id'
            FROM exports CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(payload->'sections')='array' THEN payload->'sections' ELSE '[]'::jsonb END) section
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(section->'materials')='array' THEN section->'materials' ELSE '[]'::jsonb END) material''', [ids])
        for group_id, activity, component in cursor.fetchall():
            result.setdefault(int(group_id), []).append((group_id, activity, component))
    return result
