"""Bulk evidence loading for the compact Students table; no writes or Graph calls."""
from collections import defaultdict

from django.db import connections

from learner_api import attendance_lectures as canonical
from learner_api.attendance import fetch_kbc_attendance_rows_bulk, fetch_verified_teams_attendance_rows
from learner_api.student_activity_access import student_activity_available
from learner_api.attendance_confirmation import EVENT
from learner_api.alternative_recovery import ALTERNATIVE_METHOD, alternative_occurrence_id
from curriculum_api.models import LiveSessionAbsence
from .models import CoachAttendanceSourceAdjustment, CoachAbsenceReport, CoachManualAttendance


def compact_recent(contract):
    # The canonical contract already filters eligibility and orders by instant,
    # including deterministic same-day occurrence ordering.
    return [{'date': row['sessionDate'], 'status': row['status']}
            for row in contract['recentAttendance'][:4]]


def group_contracts(profiles):
    sources = {profile._caseload_source.id: profile._caseload_source for profile in profiles
               if getattr(profile, '_caseload_source', None) is not None}
    if not sources:
        return {}
    source_ids = list(sources)
    profile_ids = [profile.id for profile in profiles]
    records, scheduled, confirmations, targets, completed, manuals, adjustments = (
        defaultdict(list), defaultdict(list), defaultdict(dict), defaultdict(dict),
        defaultdict(set), defaultdict(list), defaultdict(dict))

    # One KBC read, including duplicate Aptem identities without dropping rows.
    aptem_sources = defaultdict(list)
    for source in sources.values():
        if student_activity_available(getattr(source, 'aptem_id', None)):
            aptem_sources[str(source.aptem_id).strip()].append(source)
    identities = [{'aptem_id': key, 'learner_id': rows[0].id,
                   'learner_name': rows[0].username or '', 'learner_email': rows[0].email or ''}
                  for key, rows in aptem_sources.items()]
    by_representative = {rows[0].id: rows for rows in aptem_sources.values()}
    for row in fetch_kbc_attendance_rows_bulk(identities):
        for source in by_representative[row['learner_id']]:
            records[source.id].append({**row, 'learner_id': source.id,
                'learner_name': source.username or '', 'learner_email': source.email or '',
                'source': 'kbc-attendance'})
    emails = [source.email for source in sources.values() if source.email]
    for row in (fetch_verified_teams_attendance_rows(learner_emails=emails) if emails else []):
        source_id = row.get('enrolment_id')
        if source_id in sources:
            records[source_id].append({**row, 'learner_id': source_id, 'source': 'microsoft-teams'})

    # Same assignment union and native query as the individual register, expanded
    # across source IDs. The attendance predicate remains scoped to each email.
    assignment_sql = '''WITH source AS (
        SELECT id, lower(btrim("Email")) AS email,
            CASE WHEN jsonb_typeof("Training_plan"::jsonb)='array'
            THEN "Training_plan"::jsonb ELSE "Learning_plan"::jsonb END AS plan
        FROM enrolment."Created_users" WHERE id=ANY(%s)
    ), assigned AS (
        SELECT id, email, entry->>'moduleId' AS module_id FROM source
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(plan)='array' THEN plan ELSE '[]'::jsonb END) entry
        UNION
        SELECT source.id, source.email, coalesce(m.curriculum_module_id,nullif(m.module_ref,''))
        FROM source JOIN "Learner".learners l ON l.enrolment_id=source.id
        JOIN "Learner".learner_training_plan_modules m ON m.learner_id=l.id
    ), eligible AS (
        SELECT DISTINCT assigned.id, assigned.email, cm.module_catalogue_id
        FROM assigned JOIN curriculum.modules cm ON cm.module_catalogue_id=assigned.module_id
        WHERE (cm.deleted_at IS NULL OR COALESCE(cm.deleted_via_parent, '') <> '')
          AND coalesce(assigned.email,'')<>''
    ) '''
    native_sql = canonical.NATIVE_OCCURRENCES_SQL.replace(
        'SELECT o.id AS session_id', 'SELECT eligible.id AS enrolment_id,o.id AS session_id').replace(
        'lower(btrim(a.email))=%s', 'lower(btrim(a.email))=eligible.email').replace(
        'WHERE s.module_catalogue_id=ANY(%s)',
        'JOIN eligible ON eligible.module_catalogue_id=s.module_catalogue_id WHERE true')
    with connections['enrolment'].cursor() as cur:
        cur.execute(assignment_sql + native_sql, [source_ids])
        for row in canonical.dict_rows(cur):
            scheduled[row['enrolment_id']].append(row)
        cur.execute('''SELECT l.enrolment_id,p.time_tracking_session_ref AS lecture_id,
            p.claimed_seconds AS seconds,p.submitted_at,p.time_tracking_calculation AS details
            FROM "Learner".learner_progress_entries p
            JOIN "Learner".learners l ON l.id=p.learner_id
            WHERE l.enrolment_id=ANY(%s) AND p.kind='activity_event' AND p.feed_kind=%s
            ORDER BY p.id''', [source_ids, EVENT])
        for row in canonical.dict_rows(cur):
            confirmations[row['enrolment_id']][row['lecture_id']] = row
    for report in CoachAbsenceReport.objects.filter(
            learner_id__in=source_ids, status=CoachAbsenceReport.STATUS_APPROVED,
            recovery_method=ALTERNATIVE_METHOD):
        targets[report.learner_id][str(report.attendance_id)] = alternative_occurrence_id(report.catchup_event_key)
    for learner_id, occurrence_id in LiveSessionAbsence.objects.filter(
            source_learner_id__in=source_ids, recovery_method='catch-up',
            recovery_status=LiveSessionAbsence.RECOVERY_COMPLETED).values_list('source_learner_id', 'occurrence_id'):
        completed[learner_id].add(occurrence_id)
    for row in CoachManualAttendance.objects.filter(learner_id__in=profile_ids):
        manuals[row.learner_id].append(row)
    for row in CoachAttendanceSourceAdjustment.objects.filter(learner_id__in=profile_ids):
        adjustments[row.learner_id][(row.source, row.source_id)] = row

    result = {}
    for profile in profiles:
        source = getattr(profile, '_caseload_source', None)
        if source is None:
            continue
        unique = {}
        for row in records[source.id]:
            key = (row['source'], str(row.get('occurrence_id') or row.get('session_id')))
            if key not in unique or row['attendance_status'] in {'present', 'late'}:
                unique[key] = row
        register = canonical.lecture_register(source, learner_profile_id=profile.id,
            records=list(unique.values()), preloaded={
                'scheduled': canonical.normalize_native_occurrences(source, scheduled[source.id]),
                'confirmations': confirmations[source.id], 'targets': targets[source.id],
                'completed': completed[source.id], 'adjustments': adjustments[profile.id]})
        result[str(profile.id)] = canonical.attendance_read_contract(source,
            learner_profile_id=profile.id, register=register, manual_rows=manuals[profile.id], compact=True)
    return result
