"""Recorded delivery is distinct from a live learner's recording recovery plan.

Read-time credit only: retain Teams evidence, and never rewrite absence history.
"""
from django.utils import timezone

from .attendance_rules import attendance_outcome, INVALID


def saved_delivery_mode(learner_id):
    from django.db import connections
    from .attendance_mode import TABLE, _ready
    with connections['enrolment'].cursor() as cursor:
        if not _ready(cursor):
            return 'live'
        cursor.execute(f'SELECT mode FROM {TABLE} WHERE learner_id=%s', [learner_id])
        row = cursor.fetchone()
    # Requested modes and per-absence recovery plans never grant this credit.
    return 'lazy' if row and row[0] == 'lazy' else 'live'


def apply_recorded_attendance(rows, source, *, mode=None, kind=None, watched=None, now=None):
    now = now or timezone.now()
    candidates = [row for row in rows if row.get('source') == 'microsoft-teams'
                  and attendance_outcome(row, now)[0] not in {'present', 'upcoming', 'in_progress'}
                  and not any(str(row.get(key) or '').strip().lower() in INVALID
                              for key in ('status', 'session_status', 'occurrence_status', 'attendance_status'))]
    if not candidates:
        return rows
    if (mode if mode is not None else saved_delivery_mode(source.id)) != 'lazy':
        return rows
    def evidence_id(row):
        return str(row.get('attendance_source_occurrence_id') or row.get('occurrence_id') or row['session_id'])
    ids = {evidence_id(row) for row in candidates}
    if watched is None:
        from curriculum_api.recording_views import fully_watched_occurrences
        kind = kind or ('commercial' if getattr(source, 'learner_type', '') == 'commercial' else 'apprenticeship')
        watched = fully_watched_occurrences(kind, source.id, ids)
    candidate_keys = {id(row) for row in candidates}
    result = []
    for row in rows:
        if id(row) not in candidate_keys:
            result.append(row)
            continue
        completed = evidence_id(row) in watched
        outcome = 'recorded' if completed else 'awaiting_recording'
        result.append({**row,
            'raw_attendance_status': row.get('raw_attendance_status') or row['attendance_status'],
            'effective_attendance_status': outcome, 'effective_attendance': 1 if completed else None,
            'final_outcome': outcome, 'attendance_basis': outcome})
    return result
