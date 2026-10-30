"""Occurrence-scoped coach writes; source evidence and reports are read-only."""
import hashlib
import json
import logging

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from curriculum_api.models import LiveSession, LiveSessionOccurrence, ModuleAuthoringModule
from .bulk_register import learner_contract as attendance_read_contract, learner_rows as lecture_register
from learner_api.attendance_rules import canonical_status
from .auth import coach_access_required
from .attendance_group import compact_recent
from .bulk_register import saved_module_ids as assigned_module_ids
from .validation import ValidationError
from .models import CoachAttendanceSourceAdjustment

INVALID = {'cancelled', 'canceled', 'deleted', 'failed', 'superseded'}
log = logging.getLogger(__name__)


class BulkError(Exception):
    def __init__(self, detail, status=400):
        self.detail, self.status = detail, status


def context_profiles(owner, programme, group):
    from .views import fetch_attendance_caseload_rows, attach_caseload_source_rows, apply_curriculum_attendance_placements, serialize_attendance_source_learner
    profiles = fetch_attendance_caseload_rows(owner)
    attach_caseload_source_rows(profiles)
    learners = [serialize_attendance_source_learner(row) for row in profiles]
    apply_curriculum_attendance_placements(learners, profiles)
    from .attendance_loading import current_placements
    learners = current_placements(learners)
    allowed = {row['id'] for row in learners if str(row.get('programmeId')) == programme and str(row.get('groupId')) == group}
    return {str(row.id): row for row in profiles if str(row.id) in allowed}


def delivery_occurrences(programme, group, occurrence_id=None, lock=False, *, profiles=None, assigned_modules=None):
    # Use the same physical source as the canonical schedule, never default's
    # possibly separate database. Holding its locks until correction commit
    # serializes bulk writers and prevents cancellation during validation.
    scope = {'group_id': group} if profiles is None else {}
    modules = ModuleAuthoringModule.objects.using('enrolment').filter(
        programme_id=programme, deleted_at__isnull=True, is_programme_deleted=False, **scope)
    if profiles is not None:
        modules = modules.filter(Q(group_id=group) | Q(module_catalogue_id__in=(
            assigned_module_ids(profiles) if assigned_modules is None else assigned_modules)))
    sessions = LiveSession.objects.using('enrolment').filter(module_catalogue_id__in=modules.values('module_catalogue_id'))
    occurrences = LiveSessionOccurrence.objects.using('enrolment').filter(live_session_id__in=sessions.values('id'))
    if occurrence_id:
        occurrences = occurrences.filter(id=occurrence_id)
    if lock:
        modules = modules.select_for_update()
        sessions = sessions.select_for_update()
        occurrences = occurrences.select_for_update()
    modules = {row.module_catalogue_id: row for row in modules}
    sessions = {row.id: row for row in sessions}
    result = []
    for occurrence in occurrences.order_by('scheduled_start', 'id'):
        session = sessions.get(occurrence.live_session_id)
        if not session or str(session.status).strip().lower() in INVALID or str(occurrence.status).strip().lower() in INVALID:
            continue
        module = modules.get(session.module_catalogue_id)
        if module:
            result.append((occurrence, module))
    return result


def current_row(profile, occurrence_id, lock=False):
    source = getattr(profile, '_caseload_source', None)
    if source is None:
        raise BulkError('Learner attendance source is no longer available.', 409)
    rows = lecture_register(source, learner_profile_id=profile.id, apply_adjustments=False,
                            occurrence_id=occurrence_id, lock=lock)
    row = next((row for row in rows if row.get('source') == 'microsoft-teams' and str(row['session_id']) == occurrence_id), None)
    if row is None:
        raise BulkError('No saved attendance record is available for this learner and occurrence.', 409)
    query = CoachAttendanceSourceAdjustment.objects.filter(
        learner_id=profile.id, source='microsoft-teams', source_id=occurrence_id)
    adjustment = (query.select_for_update() if lock else query).first()
    return row, adjustment, attendance_version(profile.id, occurrence_id, row, adjustment)


def attendance_version(profile_id, occurrence_id, row, adjustment):
    """Shared read/write token; the inputs and digest format remain unchanged."""
    # The register includes read-time calculated_at and display metadata. Neither
    # is an attendance revision. Hash only this learner/occurrence's state, after
    # canonical deduplication, plus its persisted correction state. Correction
    # timestamps also change for edits to titles/dates, outside this save scope.
    def state(value):
        status = canonical_status(value)
        return status if status in {'present', 'absent'} else 'unmarked'

    raw_status = row.get('raw_attendance_status') or row.get('attendance_status')
    version_state = {
        'learner_profile_id': str(profile_id), 'session_occurrence_id': str(occurrence_id),
        'raw_status': state(raw_status),
        'effective_status': state(row.get('effective_attendance_status') or raw_status),
        'effective_attendance': row.get('effective_attendance'),
        'correction': {
            'status': adjustment.status, 'is_deleted': adjustment.is_deleted,
        } if adjustment else None,
    }
    token = hashlib.sha256(json.dumps(version_state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return token


def write_records(profiles, occurrence_id, records, owner):
    """Validate the whole request before any write. Caller owns both transactions."""
    pending, seen = [], set()
    for record in records:
        if not isinstance(record, dict):
            raise BulkError('Each attendance record must be an object.')
        learner_id = str(record.get('learnerId', ''))
        status = record.get('status')
        if learner_id in seen or status not in {'present', 'absent', 'reset'}:
            raise BulkError('Use distinct learners and present, absent or reset actions.')
        seen.add(learner_id)
        profile = profiles.get(learner_id)
        if profile is None:
            raise BulkError('Learner is outside your caseload or selected programme/group.', 403)
        row, adjustment, version = current_row(profile, occurrence_id, lock=True)
        desired = '' if status == 'reset' else status
        same = ((adjustment is None and status == 'reset') or
                (adjustment is not None and not adjustment.is_deleted and adjustment.status == desired))
        # Retries after a lost response succeed; different stale edits conflict.
        if record.get('version') != version and not same:
            raise BulkError('Attendance changed since loading. Reload the session before applying your changes.', 409)
        pending.append((learner_id, status, adjustment, same))
    results = []
    for learner_id, status, adjustment, same in pending:
        action = 'unchanged'
        if not same:
            CoachAttendanceSourceAdjustment.objects.update_or_create(
                learner_id=int(learner_id), source='microsoft-teams', source_id=occurrence_id,
                defaults={'status': '' if status == 'reset' else status, 'is_deleted': False, 'owner_email': owner, 'updated_by': owner})
            action = 'updated' if adjustment else 'created'
        results.append({'learnerId': learner_id, 'status': status, 'action': action})
    return results


@coach_access_required
@require_http_methods(['GET', 'POST'])
def coach_bulk_attendance(request):
    from .views import authenticated_coach_email, is_coach_view_as, parse_json_object
    owner = authenticated_coach_email(request)
    try:
        if request.method == 'POST' and is_coach_view_as(request):
            raise BulkError('Attendance changes are unavailable in read-only view-as mode.', 403)
        payload = request.GET if request.method == 'GET' else parse_json_object(request)
        programme, group = str(payload.get('programmeId') or ''), str(payload.get('groupId') or '')
        occurrence_id = str(payload.get('sessionOccurrenceId') or '')
        if not programme or not group:
            raise BulkError('Select a programme and group.')
        if request.method == 'GET':
            profiles = context_profiles(owner, programme, group)
            if not profiles:
                if occurrence_id:
                    raise BulkError('Learner context is outside your caseload or selected programme/group.', 403)
                return JsonResponse({'sessions': [], 'learners': []})
            occurrences = delivery_occurrences(programme, group, occurrence_id or None, profiles=list(profiles.values()))
            if occurrence_id and not occurrences:
                raise BulkError('Occurrence is invalid, cancelled or outside the selected group.', 409)
            if not occurrence_id:
                return JsonResponse({'sessions': [{
                    'id': row.id, 'occurrenceStart': row.scheduled_start.isoformat(),
                    'module': module.title, 'sessionTitle': f'{module.title} — Session {row.session_number}',
                } for row, module in occurrences]})
            learners, warnings = [], []
            for profile in profiles.values():
                source = getattr(profile, '_caseload_source', None)
                if source is None:
                    log.warning(
                        'bulk_attendance_learner_source_unavailable learner_profile_id=%s enrolment_id=%s programme_id=%s group_id=%s occurrence_id=%s',
                        profile.id, getattr(profile, 'enrolment_id', None), programme, group, occurrence_id,
                        extra={'learner_profile_id': profile.id, 'enrolment_id': getattr(profile, 'enrolment_id', None),
                               'programme_id': programme, 'group_id': group, 'occurrence_id': occurrence_id})
                    warnings.append({'learnerProfileId': str(profile.id), 'code': 'learner_source_unavailable',
                                     'message': 'Attendance source unavailable for this learner.'})
                    continue
                # Only assigned learners are eligible, even within this group.
                raw = lecture_register(source, learner_profile_id=profile.id, apply_adjustments=False)
                if not any(row.get('source') == 'microsoft-teams' and str(row['session_id']) == occurrence_id for row in raw):
                    continue
                _, adjustment, version = current_row(profile, occurrence_id)
                contract = attendance_read_contract(source, learner_profile_id=profile.id)
                session = next((row for row in contract['history'] if row['source'] == 'microsoft-teams' and row['sourceId'] == occurrence_id), None)
                learners.append({'learnerId': str(profile.id), 'status': session['status'] if session else 'unmarked', 'version': version,
                                 'manualStatus': adjustment.status if adjustment and not adjustment.is_deleted and adjustment.status else None})
            return JsonResponse({'learners': learners, 'warnings': warnings})
        records = payload.get('records')
        if not occurrence_id or not isinstance(records, list) or not records or len(records) > 1000:
            raise BulkError('Select a session and supply between 1 and 1000 attendance records.')
        with transaction.atomic(using='enrolment'):
            profiles = context_profiles(owner, programme, group)
            if not delivery_occurrences(programme, group, occurrence_id, lock=True, profiles=list(profiles.values())):
                raise BulkError('Occurrence is invalid, cancelled or outside the selected group.', 409)
            with transaction.atomic():
                results = write_records(profiles, occurrence_id, records, owner)
                for result in results:
                    profile = profiles[result['learnerId']]
                    contract = attendance_read_contract(profile._caseload_source, learner_profile_id=profile.id)
                    session = next(row for row in contract['history']
                                   if row['source'] == 'microsoft-teams' and row['sourceId'] == occurrence_id)
                    _, adjustment, version = current_row(profile, occurrence_id)
                    result.update(status=session['status'], version=version,
                                  sessionOccurrenceId=occurrence_id, attendanceRecord=session,
                                  recent=compact_recent(contract),
                                  manualStatus=adjustment.status if adjustment and not adjustment.is_deleted and adjustment.status else None)
        from .attendance_context import clear_context_cache
        clear_context_cache()
        return JsonResponse({'ok': True, 'results': results})
    except BulkError as exc:
        return JsonResponse({'detail': exc.detail}, status=exc.status)
    except ValidationError:
        return JsonResponse({'detail': 'Supply a valid JSON object.'}, status=400)
    except DatabaseError:
        log.exception('bulk_attendance_database_failure')
        return JsonResponse({'detail': 'Attendance service is unavailable. Your changes have not been confirmed; retry or reload.'}, status=503)
