"""Short-lived selected group projection; attendance edit state is never cached."""
from collections import OrderedDict
from concurrent.futures import Future
from hashlib import sha256
from threading import Lock
from time import monotonic

from django.utils import timezone

from .bulk_register import recent_attendance, saved_modules_by_source as assigned_modules_by_source
from .attendance_timing import AttendanceTiming

TTL = 60
_lock = Lock()
_cache = OrderedDict()
_pending = {}
_generation = 0


def context_key(request):
    from .views import authenticated_coach_email
    from .bulk_attendance import BulkError
    programme, group = request.GET.get('programmeId', ''), request.GET.get('groupId', '')
    if not programme or not group:
        raise BulkError('Select a programme and group.')
    admin = getattr(request, 'coach_view_as_admin', None)
    return sha256(repr((authenticated_coach_email(request).strip().lower(),
        bool(getattr(request, 'coach_view_as', False)), getattr(admin, 'id', None), programme, group,
        timezone.get_current_timezone_name())).encode()).hexdigest()


def clear_context_cache():
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _pending.clear()


def load_context(request):
    key = context_key(request)
    with _lock:
        generation = _generation
        entry = _cache.get(key)
        if entry and entry[0] > monotonic():
            _cache.move_to_end(key)
            return entry[1]
        existing = _pending.get(key)
        if existing is None:
            existing = Future()
            _pending[key] = existing
            owns_read = True
        else:
            owns_read = False
    if not owns_read:
        return existing.result()
    try:
        with AttendanceTiming().request() as timing:
            payload = build_context(request, timing)
        with _lock:
            if generation == _generation:
                _cache[key] = monotonic() + TTL, payload
            while len(_cache) > 128:
                _cache.popitem(last=False)
        existing.set_result(payload)
        return payload
    except BaseException as exc:
        existing.set_exception(exc)
        raise
    finally:
        with _lock:
            if _pending.get(key) is existing:
                _pending.pop(key, None)


def build_context(request, timing):
    from .attendance_loading import selected_context
    from .bulk_attendance import delivery_occurrences
    from learner_api.attendance_lectures import _aware
    with timing.stage('ownership_programme_group_identity'):
        profiles, placements, programme, group = selected_context(request)
        selected = [profiles[row['id']] for row in placements]
    with timing.stage('saved_plan_and_normalized_module_resolution'):
        assignments = assigned_modules_by_source(selected)
    with timing.stage('eligible_session_occurrences'):
        occurrences = delivery_occurrences(programme['id'], group['id'], profiles=selected,
            assigned_modules=sorted({module for modules in assignments.values() for module in modules}))
        sessions = []
        for occurrence, module in occurrences:
            start = timezone.localtime(_aware(occurrence.scheduled_start))
            sessions.append({'id': str(occurrence.id), 'date': start.date().isoformat(),
                'title': f'{module.title} — Session {occurrence.session_number}', 'time': start.strftime('%H:%M')})
    with timing.stage('bounded_recent_canonical_outcomes'):
        recent = recent_attendance(selected, assignments)
    learners = [{'id': row['id'], 'name': row['name'], 'email': row['email'],
        'status': 'on-break' if row['enrollmentStatus'] == 'break' else 'active',
        'recent': recent.get(row['id'], [])} for row in placements]
    return {'programme': programme, 'group': group, 'learners': learners, 'sessions': sessions}
