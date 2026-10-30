"""Coach bulk attendance reads the persisted learner register, never raw reports."""
from collections import defaultdict
from types import SimpleNamespace

from django.db import connections
from django.utils import timezone

from learner_api import attendance_lectures as canonical
from learner_api.attendance_rules import attendance_outcome
from .models import CoachAbsenceReport, CoachAttendanceSourceAdjustment


REGISTER_SQL = """
    SELECT r.learner_profile_id,r.occurrence_id,r.live_session_id,
           r.module_catalogue_id,r.attendance_status,r.updated_at,
           r.attended_seconds,r.attendance_report_id,
           o.scheduled_start,o.scheduled_end,o.session_number,m.title AS module_title
    FROM curriculum.live_session_learner_attendance r
    JOIN curriculum.live_session_occurrences o
      ON o.id=r.occurrence_id AND o.live_session_id=r.live_session_id
    JOIN curriculum.live_sessions s
      ON s.id=r.live_session_id AND s.module_catalogue_id=r.module_catalogue_id
    JOIN curriculum.modules m ON m.module_catalogue_id=r.module_catalogue_id
    WHERE r.learner_profile_id=ANY(%s)
      AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
      AND lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
      AND lower(btrim(o.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
"""


def saved_modules_by_source(profiles):
    """Resolve delivery membership from the same saved register using profile IDs."""
    sources = {int(profile.id): profile._caseload_source.id for profile in profiles
               if getattr(profile, '_caseload_source', None) is not None}
    if not sources:
        return {}
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(
            '''SELECT DISTINCT learner_profile_id,module_catalogue_id
               FROM curriculum.live_session_learner_attendance
               WHERE learner_profile_id=ANY(%s)''', [sorted(sources)])
        saved = cursor.fetchall()
    result = defaultdict(list)
    for profile_id, module_id in saved:
        if profile_id in sources and module_id:
            result[sources[profile_id]].append(str(module_id))
    return dict(result)


def saved_module_ids(profiles):
    return sorted({module for modules in saved_modules_by_source(profiles).values() for module in modules})


def register_rows(profiles, *, occurrence_id=None, lock=False):
    """One scoped read; curriculum joins supply dates/titles, never attendance."""
    available = {int(profile.id): profile for profile in profiles
                 if getattr(profile, '_caseload_source', None) is not None}
    result = {str(key): [] for key in available}
    if not available:
        return result
    sql, params = REGISTER_SQL, [sorted(available)]
    if occurrence_id is not None:
        sql += ' AND r.occurrence_id=%s'
        params.append(occurrence_id)
    sql += ' ORDER BY o.scheduled_start DESC,o.id,r.learner_profile_id'
    if lock:
        sql += ' FOR UPDATE OF r'
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(sql, params)
        records = canonical.dict_rows(cursor)
    for record in records:
        profile = available.get(record['learner_profile_id'])
        if profile is None:
            continue
        source = profile._caseload_source
        start, end = canonical._aware(record['scheduled_start']), canonical._aware(record['scheduled_end'])
        local_start = timezone.localtime(start)
        local_end = timezone.localtime(end) if end else None
        title = record['module_title'] or 'Live session'
        result[str(profile.id)].append({
            **record, 'source': 'microsoft-teams', 'session_id': record['occurrence_id'],
            'learner_id': source.id, 'learner_profile_id': profile.id,
            'session_type': 'live_session', 'module_title': title,
            'session_title': f"{title} — Session {record['session_number']}",
            'scheduled_start': start, 'scheduled_end': end,
            'session_date': local_start.date(), 'session_start_time': local_start.time().replace(tzinfo=None),
            'session_end_time': local_end.time().replace(tzinfo=None) if local_end else None,
            'minutes_late': 0, 'catchup_completed': False,
        })
    return result


def correction_rows(profile_ids, *, occurrence_id=None):
    query = CoachAttendanceSourceAdjustment.objects.filter(
        learner_id__in=profile_ids, source='microsoft-teams')
    if occurrence_id is not None:
        query = query.filter(source_id=occurrence_id)
    result = defaultdict(dict)
    for row in query:
        result[str(row.learner_id)][(row.source, row.source_id)] = row
    return result


def learner_rows(source, *, learner_profile_id, apply_adjustments=True, occurrence_id=None, lock=False):
    profile = SimpleNamespace(id=learner_profile_id, _caseload_source=source)
    rows = register_rows([profile], occurrence_id=occurrence_id, lock=lock)[str(learner_profile_id)]
    if apply_adjustments:
        adjustments = correction_rows([learner_profile_id], occurrence_id=occurrence_id)
        rows = canonical._apply_coach_source_adjustments(
            rows, learner_profile_id, adjustments=adjustments[str(learner_profile_id)])
    return rows


def learner_contract(source, *, learner_profile_id):
    return canonical.attendance_read_contract(
        source, learner_profile_id=learner_profile_id,
        register=learner_rows(source, learner_profile_id=learner_profile_id),
        manual_rows=[], compact=True,
    )


def group_contracts(profiles):
    rows = register_rows(profiles)
    if not rows:
        return {}
    adjustments = correction_rows([int(key) for key in rows])
    result = {}
    for profile in profiles:
        key = str(profile.id)
        if key not in rows:
            continue
        corrected = canonical._apply_coach_source_adjustments(
            rows[key], profile.id, adjustments=adjustments[key])
        result[key] = canonical.attendance_read_contract(
            profile._caseload_source, learner_profile_id=profile.id,
            register=corrected, manual_rows=[], compact=True,
        )
    return result


def selected_rows(profiles, occurrence_id, timing):
    from .bulk_attendance import attendance_version
    with timing.stage('attendance_state'):
        rows = register_rows(profiles, occurrence_id=occurrence_id)
        if not rows:
            return {}
        adjustments = correction_rows([int(key) for key in rows], occurrence_id=occurrence_id)
    report_ids = {
        profile._caseload_source.id: str(canonical.report_id({
            'learner_id': profile._caseload_source.id, 'source': 'microsoft-teams',
            'session_id': occurrence_id,
        }))
        for profile in profiles if str(profile.id) in rows
    }
    reports = {}
    with timing.stage('absence_reports'):
        for report in CoachAbsenceReport.objects.filter(
                learner_id__in=list(report_ids), attendance_id__in=list(report_ids.values())
        ).order_by('created_at', 'id'):
            if str(report.attendance_id) == report_ids.get(report.learner_id):
                reports[report.learner_id] = report
    result = {}
    for profile in profiles:
        key = str(profile.id)
        if not rows.get(key):
            continue
        row = rows[key][0]
        adjustment = adjustments[key].get(('microsoft-teams', occurrence_id))
        corrected = canonical._apply_coach_source_adjustments(
            [row], profile.id, adjustments=adjustments[key])
        status = attendance_outcome(corrected[0])[0] if corrected else None
        report = reports.get(profile._caseload_source.id) if corrected else None
        result[key] = {
            'learnerId': key, 'status': status if status in {'present', 'absent'} else None,
            'absenceReport': {'id': str(report.id), 'status': report.status,
                              'url': getattr(report, 'evidence_image_url', '') or None} if report else None,
            'version': attendance_version(profile.id, occurrence_id, row, adjustment),
            'manualStatus': adjustment.status if adjustment and not adjustment.is_deleted and adjustment.status else None,
        }
    return result


def recent_attendance(profiles, assignments):
    """Keep the context response shape; membership is already scoped by callers."""
    from .attendance_group import compact_recent
    return {key: compact_recent(contract) for key, contract in group_contracts(profiles).items()}
