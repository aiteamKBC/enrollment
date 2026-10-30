"""Lazy coach attendance reads. Bulk correction writes remain in bulk_attendance."""
import logging

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .auth import coach_access_required
from .bulk_register import selected_rows, group_contracts
from .attendance_timing import AttendanceTiming
from .attendance_group import compact_recent
from .bulk_attendance import BulkError, delivery_occurrences
from learner_api.attendance_lectures import _aware

log = logging.getLogger(__name__)


@coach_access_required
@require_GET
def coach_attendance_context(request):
    from .attendance_context import load_context
    try:
        return JsonResponse(load_context(request))
    except BulkError as exc:
        return JsonResponse({'detail': exc.detail}, status=exc.status)
    except Exception:
        log.exception('coach_attendance_context_failed')
        return JsonResponse({'detail': 'Unable to load group attendance context.'}, status=503)


def attendance_placements(owner):
    from .views import (fetch_attendance_caseload_rows, fetch_source_schedule_rows, resolve_caseload_source_row,
                        apply_curriculum_attendance_placements, clean_text,
                        get_lms_row_program_status, normalize_program_status,
                        should_include_in_attendance_page, authoring_fetch_all, GROUPS_TABLE)
    profiles = fetch_attendance_caseload_rows(owner, include_plan=False)
    # Do not hydrate saved plans and unrelated profile JSON for the whole
    # caseload. Canonical assignments are resolved separately in one SQL read.
    commercial, enrolment = fetch_source_schedule_rows(profiles, fields=(
        'id', 'username', 'email', 'aptem_id', 'learner_type', 'employer_id'))
    for profile in profiles:
        profile._caseload_source = resolve_caseload_source_row(
            profile, commercial_rows=commercial, enrolment_rows=enrolment)
    learners = []
    for profile in profiles:
        learner = {
            'id': str(profile.id), 'name': clean_text(profile.username) or 'Unknown learner',
            'email': clean_text(profile.email) or None,
            'programmeId': clean_text(profile.programme_id), 'programmeName': clean_text(profile.programme),
            'groupId': clean_text(profile.group_id), 'groupName': clean_text(profile.group_name),
            'cohortName': clean_text(profile.cohort),
            'enrollmentStatus': normalize_program_status(get_lms_row_program_status(profile)),
        }
        if should_include_in_attendance_page(learner):
            learners.append(learner)
    groups = authoring_fetch_all(GROUPS_TABLE, ensure_tables=False)
    apply_curriculum_attendance_placements(learners, profiles, groups=groups)
    return {str(row.id): row for row in profiles}, current_placements(learners, groups=groups)


def current_placements(learners, *, groups=None):
    from .views import clean_text, authoring_fetch_all, GROUPS_TABLE
    # Current curriculum membership wins over stale profile placements. Only
    # existing coach learners are enriched; global groups are never appended.
    groups = {clean_text(row.get('group_id')): row
              for row in (authoring_fetch_all(GROUPS_TABLE, ensure_tables=False) if groups is None else groups)}
    current = []
    for learner in learners:
        group = groups.get(learner.get('groupId'))
        if group:
            if clean_text(group.get('status')).casefold() in {'archived', 'deleted'}:
                continue
            learner.update(programmeId=clean_text(group.get('programme_id')),
                           programmeName=clean_text(group.get('programme_name')),
                           groupName=clean_text(group.get('group_name')),
                           cohortName=clean_text(group.get('cohort_name')))
        current.append(learner)
    return current


def placement_options(learners):
    programmes = {}
    for learner in learners:
        programme_id, group_id = learner.get('programmeId'), learner.get('groupId')
        if not programme_id:
            continue
        programme = programmes.setdefault(programme_id, {
            'id': programme_id, 'name': learner.get('programmeName') or '--', 'groups': {}})
        # Never collapse names across cohorts. The placement resolver supplies a
        # canonical curriculum ID only for unambiguous legacy placements.
        if group_id:
            programme['groups'].setdefault(group_id, {
                'id': group_id, 'name': learner.get('groupName') or '--',
                'cohort': learner.get('cohortName') or '--'})
    return {'programmes': [
        {**programme, 'groups': sorted(programme['groups'].values(), key=lambda row: (row['name'], row['id']))}
        for programme in sorted(programmes.values(), key=lambda row: (row['name'], row['id']))]}


def selected_context(request):
    from .views import authenticated_coach_email
    programme_id, group_id = request.GET.get('programmeId', ''), request.GET.get('groupId', '')
    if not programme_id or not group_id:
        raise BulkError('Select a programme and group.')
    profiles, placements = attendance_placements(authenticated_coach_email(request))
    programmes = placement_options(placements)['programmes']
    programme = next((row for row in programmes if row['id'] == programme_id), None)
    group = next((row for row in programme['groups'] if row['id'] == group_id), None) if programme else None
    if not group:
        raise BulkError('Learner context is outside your caseload or selected programme/group.', 403)
    learners = [row for row in placements if row['programmeId'] == programme_id and row['groupId'] == group_id]
    return profiles, learners, {'id': programme['id'], 'name': programme['name']}, group


def session_option(occurrence, module):
    return {'id': str(occurrence.id), 'occurrenceStart': occurrence.scheduled_start.isoformat(),
            'module': module.title, 'sessionTitle': f'{module.title} — Session {occurrence.session_number}'}


@coach_access_required
@require_GET
def coach_attendance_options(request):
    from .views import authenticated_coach_email
    try:
        profiles, learners = attendance_placements(authenticated_coach_email(request))
        payload = placement_options(learners)
        if request.GET.get('includeModules') == '1':
            from .attendance_modules import with_module_options
            programme_id = request.GET.get('programmeId', '')
            programme = next((row for row in payload['programmes'] if row['id'] == programme_id), None)
            if programme is None:
                return JsonResponse({'detail': 'Programme is outside your caseload.'}, status=403)
            payload = {'programmes': [with_module_options(programme, profiles, learners)]}
        return JsonResponse(payload)
    except Exception:
        log.exception('coach_attendance_options_failed')
        return JsonResponse({'detail': 'Unable to load attendance options.'}, status=503)


@coach_access_required
@require_GET
def coach_attendance_group(request):
    try:
        profiles, placements, programme, group = selected_context(request)
        learners = []
        contracts = group_contracts([profiles[row['id']] for row in placements])
        for placement in placements:
            profile = profiles[placement['id']]
            contract = contracts.get(str(profile.id))
            summary = contract['summary'] if contract else {}
            learners.append({
                'id': placement['id'], 'name': placement['name'], 'email': placement['email'],
                'status': 'on-break' if placement['enrollmentStatus'] == 'break' else 'active',
                'attendance': {'rate': summary.get('attendanceRate'), 'present': summary.get('present', 0),
                               'absent': summary.get('absent', 0), 'sessions': summary.get('sessions', 0)},
                'recent': compact_recent(contract) if contract else []})
        return JsonResponse({'programme': programme, 'group': group, 'learners': learners})
    except BulkError as exc:
        return JsonResponse({'detail': exc.detail}, status=exc.status)
    except Exception:
        log.exception('coach_attendance_group_failed')
        return JsonResponse({'detail': 'Unable to load group attendance.'}, status=503)


@coach_access_required
@require_GET
def coach_attendance_sessions(request):
    from django.utils import timezone
    try:
        profiles, placements, programme, group = selected_context(request)
        occurrences = delivery_occurrences(programme['id'], group['id'],
            profiles=[profiles[row['id']] for row in placements])
        now = timezone.now()
        sessions = []
        for occurrence, module in occurrences:
            start = timezone.localtime(_aware(occurrence.scheduled_start))
            sessions.append({'id': str(occurrence.id), 'date': start.date().isoformat(),
                'title': f'{module.title} — Session {occurrence.session_number}',
                'time': start.strftime('%H:%M'),
                'status': 'completed' if _aware(occurrence.scheduled_end) <= now else 'scheduled'})
        return JsonResponse({'sessions': sessions})
    except BulkError as exc:
        return JsonResponse({'detail': exc.detail}, status=exc.status)
    except Exception:
        log.exception('coach_attendance_sessions_failed')
        return JsonResponse({'detail': 'Unable to load attendance sessions.'}, status=503)


@coach_access_required
@require_GET
def coach_attendance_session(request):
    with AttendanceTiming().request() as timing:
        return _selected_session_response(request, timing)


def _selected_session_response(request, timing):
    try:
        with timing.stage('learner_identity'):
            profiles, placements, programme, group = selected_context(request)
        session_id = request.GET.get('sessionId', '')
        if not session_id:
            raise BulkError('Select a session.')
        with timing.stage('occurrence_session'):
            occurrences = delivery_occurrences(programme['id'], group['id'], session_id,
                profiles=[profiles[row['id']] for row in placements])
        if not occurrences:
            raise BulkError('Occurrence is invalid, cancelled or outside the selected group.', 409)
        occurrence, module = occurrences[0]
        rows = selected_rows([profiles[row['id']] for row in placements], session_id, timing)
        learners, warnings = [], []
        for placement in placements:
            profile = profiles[placement['id']]
            if getattr(profile, '_caseload_source', None) is None:
                warnings.append({'learnerProfileId': placement['id'], 'code': 'learner_source_unavailable',
                                 'message': 'Attendance source unavailable for this learner.'})
            elif placement['id'] in rows:
                learners.append({**rows[placement['id']], 'name': placement['name']})
        from django.utils import timezone
        start = timezone.localtime(_aware(occurrence.scheduled_start))
        return JsonResponse({'session': {'id': session_id, 'title': session_option(occurrence, module)['sessionTitle'],
                                         'date': start.date().isoformat(), 'startTime': start.strftime('%H:%M')},
                             'learners': learners, 'warnings': warnings})
    except BulkError as exc:
        return JsonResponse({'detail': exc.detail}, status=exc.status)
    except Exception:
        log.exception('coach_attendance_session_failed')
        return JsonResponse({'detail': 'Unable to load session attendance.'}, status=503)
