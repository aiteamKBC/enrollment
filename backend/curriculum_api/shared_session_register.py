"""Persist shared-module attendance from saved, reviewed Teams evidence.

No Graph calls, invitations, rewards or meeting creation. Meeting IDs remain
owned by the source; the destination module owns its separate learner register.
"""
from collections import defaultdict

from django.db import connections
from django.utils import timezone

from .session_results_policy import instant


SHARED_SERIES_SQL = '''WITH RECURSIVE shared AS (
    SELECT m.module_catalogue_id AS module_id,m.teams_shared_source_module_id AS source_id,
           ARRAY[m.module_catalogue_id]::text[] AS path,1 AS depth
    FROM curriculum.modules m
    WHERE m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
      AND coalesce(btrim(m.teams_shared_source_module_id),'')<>''
    UNION ALL
    SELECT shared.module_id,m.teams_shared_source_module_id,
           shared.path || m.module_catalogue_id,shared.depth+1
    FROM shared JOIN curriculum.modules m ON m.module_catalogue_id=shared.source_id
    WHERE shared.depth<5 AND NOT m.module_catalogue_id=ANY(shared.path)
      AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
      AND coalesce(btrim(m.teams_shared_source_module_id),'')<>''
)
SELECT DISTINCT shared.module_id,s.id,s.module_catalogue_id
FROM shared JOIN curriculum.live_sessions s ON s.module_catalogue_id=shared.source_id
JOIN curriculum.modules owner ON owner.module_catalogue_id=s.module_catalogue_id
WHERE owner.deleted_at IS NULL AND NOT coalesce(owner.is_programme_deleted,false)
  AND lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
'''


def project_shared_attendance(module_id, series_id, sessions, reports, corrections, calculated_at):
    """One row per eligible profile and completed occurrence, in this delivery."""
    rows = []
    for session in sessions:
        occurrence_id = str(session['id'])
        if not session.get('reportReady') or not reports.get(occurrence_id):
            continue
        candidates = defaultdict(set)
        for person in session.get('attendanceCandidates', []):
            if person.get('learnerProfileId'):
                candidates[str(person.get('email') or '').strip().casefold()].add(person['learnerProfileId'])
        for person in session.get('attendance', []):
            email = str(person.get('email') or '').strip().casefold()
            profiles = candidates.get(email, set())
            if len(profiles) != 1:
                continue
            profile_id = next(iter(profiles))
            raw_status = person.get('rawStatus') or person.get('status')
            status = corrections.get((occurrence_id, profile_id)) or raw_status
            # An unfinished report or unresolved identity never becomes absence.
            if status not in {'present', 'absent'}:
                continue
            visits = [(instant(v.get('joinedAt')), instant(v.get('leftAt')))
                      for v in person.get('intervals', [])]
            visits = [(start, end) for start, end in visits if start and end and end >= start]
            recovery = person.get('recoveryStatus') or 'none'
            rows.append({
                'occurrence_id': occurrence_id, 'live_session_id': series_id,
                'module_catalogue_id': module_id, 'learner_profile_id': profile_id,
                'learner_email': email, 'learner_name': person.get('name') or '',
                'attendance_status': status,
                'recovery_status': recovery if recovery in {'none', 'requested', 'catchup_booked', 'completed'} else 'none',
                'attended_seconds': max(0, int(person.get('seconds') or 0)),
                'attendance_report_id': reports[occurrence_id],
                'first_join_at': min((start for start, _ in visits), default=None),
                'last_leave_at': max((end for _, end in visits), default=None),
                'calculated_at': calculated_at,
            })
    return rows


def shared_register_rows(*, database, module_refs=None, learner_ids=None, learner_emails=None,
                         all_learners=False, exact_modules=False):
    from coach_api.models import CoachAttendanceSourceAdjustment
    from .session_results import result_rows, apply_recovery

    modules = sorted({str(value).strip() for value in (module_refs or []) if str(value).strip()})
    ids = {int(value) for value in (learner_ids or []) if value}
    emails = {str(value).strip().casefold() for value in (learner_emails or []) if value}
    if not (modules or ids or emails or all_learners):
        return []
    sql, params = SHARED_SERIES_SQL, []
    if modules:
        sql += ' AND shared.module_id=ANY(%s)' if exact_modules else ' AND (shared.module_id=ANY(%s) OR shared.source_id=ANY(%s))'
        params = [modules] if exact_modules else [modules, modules]
    with connections[database].cursor() as cursor:
        cursor.execute(sql + ' ORDER BY shared.module_id,s.id', params)
        deliveries = cursor.fetchall()
    rows, calculated_at = [], timezone.now()
    for module_id, series_id, owner_id in deliveries:
        with connections[database].cursor() as cursor:
            cursor.execute('''SELECT id,attendance_report_id FROM curriculum.live_session_occurrences
                WHERE live_session_id=%s AND actual_end IS NOT NULL
                  AND coalesce(attendance_report_id,'')<>''
                  AND lower(btrim(status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')''', [series_id])
            reports = dict(cursor.fetchall())
        if not reports:
            continue
        sessions = result_rows({'id': series_id, 'module_catalogue_id': owner_id},
                               module_catalogue_id=module_id, occurrence_ids=list(reports),
                               attendance_only=True, use_saved_outcomes=False)
        apply_recovery(sessions)
        correction_ids = {f'{occurrence}:{module_id}': occurrence for occurrence in reports}
        corrections = {(correction_ids[row['source_id']], row['learner_id']): row['status']
                       for row in CoachAttendanceSourceAdjustment.objects.using(database).filter(
                           source='microsoft-teams', source_id__in=list(correction_ids), is_deleted=False,
                           status__in=['present', 'absent']).values('source_id', 'learner_id', 'status')}
        projected = project_shared_attendance(module_id, series_id, sessions, reports, corrections, calculated_at)
        rows.extend(row for row in projected if not (ids or emails)
                    or row['learner_profile_id'] in ids or row['learner_email'] in emails)
    return rows
