"""Read-only Coach learner detail: historical canonical counts, paged display facts."""
from collections import defaultdict
from datetime import datetime

from django.db import connections
from django.utils import timezone

from learner_api import attendance_lectures as canonical
from learner_api import recorded_attendance
from learner_api.attendance import kbc_attendance_rows
from learner_api.attendance_rules import INVALID, attendance_outcome
from learner_api.teams_attendance import fetch_verified_teams_attendance_rows
from .attendance_projection import pagination_params
from .bulk_register import learner_rows as saved_attendance_rows
from .models import CoachAbsenceReport, CoachManualAttendance


def historical_schedule(source, now, legacy_rows=()):
    if not source.email:
        return []
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(canonical.ATTENDANCE_SUBJECTS_SQL, [canonical.effective_assigned_module_ids(source)])
        modules = [row[0] for row in cursor.fetchall()]
        if not modules:
            return []
        sql = canonical.NATIVE_OCCURRENCES_SQL.replace(
            'WHERE s.module_catalogue_id=ANY(%s)',
            'WHERE s.module_catalogue_id=ANY(%s) AND o.scheduled_end<=%s')
        cursor.execute(sql, [canonical._key(source.email), modules, now])
        rows = canonical.dict_rows(cursor)
        # A future/active native identity can suppress a legacy mirror before
        # corrections are applied. Read only matching dates, with no evidence,
        # join URLs or full schedule enrichment; it will never enter records.
        days = sorted({row['session_date'] for row in legacy_rows
                       if row.get('source') == 'kbc-attendance' and row.get('session_date')})
        if days:
            cursor.execute('''SELECT o.id AS session_id,s.module_catalogue_id,m.title AS module_title,
                o.scheduled_start,o.scheduled_end,o.session_number,NULL AS updated_at
                FROM curriculum.live_session_occurrences o
                JOIN curriculum.live_sessions s ON s.id=o.live_session_id
                JOIN curriculum.modules m ON m.module_catalogue_id=s.module_catalogue_id
                WHERE s.module_catalogue_id=ANY(%s) AND o.scheduled_end>%s
                  AND (o.scheduled_start AT TIME ZONE %s)::date=ANY(%s)
                  AND m.deleted_at IS NULL AND NOT coalesce(m.is_programme_deleted,false)
                  AND lower(btrim(s.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
                  AND lower(btrim(o.status)) NOT IN ('cancelled','canceled','deleted','failed','superseded')
                ORDER BY o.scheduled_start,o.id''', [modules, now, timezone.get_current_timezone_name(), days])
            rows.extend(canonical.dict_rows(cursor))
        return canonical.normalize_native_occurrences(source, rows)


def page_components(rows):
    """Only title matching for this page; no progress, KSB or activity bundles."""
    modules = sorted({row['module_catalogue_id'] for row in rows
                      if row.get('source') == 'microsoft-teams' and row.get('module_catalogue_id')})
    if not modules:
        return {}
    with connections['enrolment'].cursor() as cursor:
        cursor.execute('''SELECT c.id,c.module_catalogue_id,c.type,c.title,
            jsonb_build_object('teamsOccurrenceId',c.settings_json->'teamsOccurrenceId',
                'teamsLiveSessionId',c.settings_json->'teamsLiveSessionId',
                'sessionDate',c.settings_json->'sessionDate',
                'legacySettings',c.settings_json->'legacySettings') AS settings_json
            FROM curriculum.components c
            LEFT JOIN curriculum.weeks w ON w.id=c.week_id AND w.module_catalogue_id=c.module_catalogue_id
            WHERE c.module_catalogue_id=ANY(%s) AND c.type='live_session' AND c.deleted_at IS NULL
              AND NOT c.is_programme_deleted AND (w.id IS NULL OR
                (w.deleted_at IS NULL AND NOT w.is_programme_deleted))
            ORDER BY c.display_order,c.id''', [modules])
        components = canonical.dict_rows(cursor)
    result = defaultdict(list)
    for component in components:
        settings = canonical.as_json(component['settings_json'], {})
        component['settings'] = settings if isinstance(settings, dict) else {}
        result[component['module_catalogue_id']].append(component)
    return result


def historical_evidence(source, scheduled, legacy_rows=None):
    """Keep source identity/dedup rules; scope persisted Teams reads to history.

    Approved alternative occurrences must remain available as recovery evidence
    even when they are not assigned lectures in this learner's schedule.
    """
    rows = kbc_attendance_rows(source) if legacy_rows is None else legacy_rows
    targets = canonical._approved_alternative_targets(source.id) if source.email else {}
    occurrences = sorted({str(row['session_id']) for row in scheduled} |
                         {str(row['attendance_source_occurrence_id']) for row in scheduled
                          if row.get('attendance_source_occurrence_id')} |
                         {target for target in targets.values() if target})
    teams = fetch_verified_teams_attendance_rows(learner_emails=[source.email], occurrence_ids=occurrences) if source.email and occurrences else []
    for row in teams:
        if str(row.get('enrolment_id')) == str(source.id):
            rows.append({**row, 'learner_id': source.id, 'source': 'microsoft-teams'})
    unique = {}
    for row in rows:
        key = (row['learner_id'], row['source'], str(row.get('occurrence_id') or row.get('session_id')))
        if key not in unique or row['attendance_status'] in {'present', 'late'}:
            unique[key] = row
    return list(unique.values())


def occurrence_order(row):
    # Keep the full canonical contract's instant ordering and tie breaker.
    start = canonical._aware(row.get('scheduled_start'))
    if start is None:
        start = timezone.make_aware(datetime.combine(
            row['session_date'], row.get('session_start_time') or datetime.min.time()))
    identity = f"manual-{row['session_id']}" if row.get('_manual') else canonical.session_key(row)
    return start.timestamp(), identity


def counted_history(source, learner_profile_id):
    """All visible history, before pagination or display/report enrichment."""
    now = timezone.now()
    # Counts require all historical canonical evidence (including corrections,
    # confirmations and recovery). Future schedules and full-history DTOs are
    # unnecessary. Only page rows get title/report enrichment and serialization.
    legacy_rows = kbc_attendance_rows(source)
    saved = saved_attendance_rows(source, learner_profile_id=learner_profile_id, apply_adjustments=False)
    scheduled = historical_schedule(source, now, legacy_rows)
    scheduled_ids = {str(row['session_id']) for row in scheduled}
    # A saved historical result survives a later change to the current plan.
    # The same scoped reader as Bulk excludes cancelled/deleted occurrences.
    scheduled.extend(dict(row) for row in saved if str(row['session_id']) not in scheduled_ids
                     and row.get('scheduled_end') and row['scheduled_end'] <= now)
    evidence = historical_evidence(source, scheduled, legacy_rows)
    # The canonical merger gives saved learner outcomes precedence over raw
    # Teams evidence, then applies recovery, confirmations and manual corrections.
    evidence.extend({**row, 'source': 'live-register', 'learner_email': source.email} for row in saved)
    rows = canonical.lecture_register(source, learner_profile_id=learner_profile_id,
        scheduled=scheduled, records=evidence)
    manual = CoachManualAttendance.objects.filter(learner_id=learner_profile_id).only(
        'id', 'status', 'session_date', 'module_name', 'session_title')
    rows.extend({'source': 'coach-manual', 'session_id': str(row.id),
        'session_date': row.session_date, 'module_title': row.module_name,
        'session_title': row.session_title, 'attendance_status': row.status,
        '_manual': True} for row in manual)
    history = []
    present = counted_total = awaiting_total = 0
    for row in rows:
        status, eligible = attendance_outcome(row, now)
        awaiting_recording = status == 'awaiting_recording' and row.get('session_date') and not any(
            str(row.get(key) or '').strip().lower() in INVALID
            for key in ('status', 'session_status', 'occurrence_status', 'attendance_status'))
        # Coach View/PDF counts unfinished recordings as absences. Keep the
        # shared learner rule and persisted attendance evidence unchanged.
        if eligible or awaiting_recording:
            counted_total += 1
            present += status == 'present'
            awaiting_total += bool(awaiting_recording)
            history.append((row, status))
    history.sort(key=lambda item: occurrence_order(item[0]), reverse=True)
    return history, {
        'present': present, 'absent': counted_total - present, 'sessions': counted_total,
        'attendanceRate': round(100 * present / counted_total) if counted_total else None,
    }, awaiting_total


def detail_records(source, learner_profile_id, params):
    page, size = pagination_params(params)
    history, summary, awaiting_total = counted_history(source, learner_profile_id)
    total = len(history)
    start = (page - 1) * size
    selected = history[start:start + size]
    components = page_components([row for row, _ in selected])
    report_ids = [str(canonical.report_id(row)) for row, _ in selected if not row.get('_manual')]
    reports = {str(report.attendance_id): report for report in
        (CoachAbsenceReport.objects.filter(learner_id=source.id, attendance_id__in=report_ids)
         .only('id', 'attendance_id', 'status', 'evidence_image_url').order_by('created_at', 'id') if report_ids else [])}
    records = []
    for row, status in selected:
        component = canonical._component_for(row, components.get(row.get('module_catalogue_id'), []))
        report = reports.get(str(canonical.report_id(row))) if not row.get('_manual') else None
        record = {
            'id': f"{row.get('source', 'kbc-attendance')}:{row['session_id']}",
            'date': row['session_date'].isoformat(), 'module': row.get('module_title') or '',
            'title': (row.get('session_title') if row.get('title_adjusted') else (component or {}).get('title')) or row.get('session_title') or '',
            'status': status,
            'absenceReport': {'id': str(report.id), 'status': report.status,
                'url': report.evidence_image_url or None} if report else None,
        }
        # This warning has a verified visible consumer; omit it on normal rows.
        if row.get('legacy_ambiguity'):
            record['legacyAmbiguity'] = row['legacy_ambiguity']
        records.append(record)
    return {'attendanceMode': recorded_attendance.saved_delivery_mode(source.id),
            'awaitingRecording': awaiting_total,
            'summary': summary,
            'records': records,
            'pagination': {'page': page, 'pageSize': size, 'total': total, 'hasMore': start + size < total}}
