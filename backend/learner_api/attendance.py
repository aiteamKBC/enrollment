import hashlib
import logging
import os
from datetime import timezone as datetime_timezone

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone

from login.permissions import learner_self_or_staff

from .learner_detail import SOURCE_MODELS
from .projection_performance import projection_call, projection_operation
from .student_activity_access import student_activity_available
from .teams_attendance import fetch_verified_teams_attendance_rows


DEFAULT_KBC_ATTENDANCE_DATABASE = 'AiTeamKBC'
logger = logging.getLogger(__name__)


def _error(message, status):
    return JsonResponse({'error': message}, status=status)


def _risk_from_rate(rate):
    if rate >= 90:
        return 'green'
    if rate >= 80:
        return 'amber'
    return 'red'


def _kbc_attendance_connection_string():
    """Connection string for the live KBC attendance database.

    ``KBC_ATTENDANCE_DATABASE_URL`` is the explicit form used by Django's
    optional database alias. Older deployments expose the server-wide
    ``KBCDATABASE`` value instead, so retain that supported path and select the
    AiTeamKBC database on it.
    """
    direct = os.environ.get('KBC_ATTENDANCE_DATABASE_URL', '').strip()
    if direct:
        return direct

    server = os.environ.get('KBCDATABASE', '').strip()
    if not server:
        return ''
    conninfo = conninfo_to_dict(server)
    conninfo['dbname'] = os.environ.get(
        'KBC_ATTENDANCE_DATABASE',
        DEFAULT_KBC_ATTENDANCE_DATABASE,
    )
    return make_conninfo(**conninfo)


def _normalize_kbc_attendance_row(row, *, learner_id, learner_name, learner_email):
    attendance_value = row.get('Attendance')
    raw_status = str(row.get('attendance_status') or '').strip().lower()
    status = 'late' if raw_status == 'late' and attendance_value == 1 else (
        'present' if attendance_value == 1 else 'absent'
    )
    session_date = row.get('date')
    source_key = str(row.get('key') or '').strip()
    module = str(row.get('module') or '').strip()
    lecture = str(row.get('lecture_name') or '').strip()
    fallback_key = '|'.join((
        str(learner_id),
        session_date.isoformat() if session_date else '',
        module,
        lecture,
    ))
    fallback_digest = hashlib.sha256(fallback_key.encode('utf-8')).hexdigest()[:20]
    return {
        'learner_id': learner_id,
        'learner_name': learner_name,
        'learner_email': learner_email,
        # The report-absence workflow sends this identifier back later, so the
        # fallback must remain stable across requests (row order is not an ID).
        'session_id': source_key or f'kbc-attendance-{fallback_digest}',
        'session_date': session_date,
        'attendance_status': status,
        'minutes_late': 0,
        'catchup_completed': False,
        'updated_at': row.get('updated_at'),
        'session_title': lecture or module or 'Attendance session',
        'session_type': 'KBC attendance',
        'session_start_time': None,
        'session_end_time': None,
        'module_title': module,
        'coach_name': '',
    }


def fetch_kbc_attendance_rows(*, aptem_id, learner_id, learner_name, learner_email):
    """Read one learner's live register rows from AiTeamKBC.

    Aptem ID is the canonical cross-system identity. Email is only used when an
    enrolment has no Aptem ID; mixing both predicates could accidentally merge
    two people's records when stale source data contains a reused email.
    """
    dsn = _kbc_attendance_connection_string()
    if not dsn:
        raise RuntimeError('KBC attendance database is not configured.')

    aptem_key = str(aptem_id or '').strip()
    email_key = str(learner_email or '').strip().lower()
    if not aptem_key and not email_key:
        return []

    if aptem_key:
        identity_sql = '"ID"::text = %s'
        identity_param = aptem_key
    else:
        identity_sql = 'lower(trim("Email")) = %s'
        identity_param = email_key

    with projection_operation('kbc_total'):
        with projection_operation('kbc_connect'):
            connection = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10)
        with connection as conn, conn.cursor() as cursor:
            with projection_operation('kbc_execute'):
                cursor.execute(
                    f'''
                    SELECT "key", "date", "Attendance", attendance_status,
                           module, lecture_name, created_at AS updated_at
                    FROM public.kbc_attendance
                    WHERE {identity_sql}
                      AND "Attendance" IN (0, 1)
                      AND "date" IS NOT NULL
                    ORDER BY "date" DESC, "key"
                    ''',
                    [identity_param],
                )
            with projection_operation('kbc_fetch'):
                rows = cursor.fetchall()
            with projection_operation('kbc_decode'):
                return [_normalize_kbc_attendance_row(row, learner_id=learner_id,
                    learner_name=learner_name, learner_email=learner_email) for row in rows]



def fetch_kbc_attendance_rows_bulk(learners, *, recent_dates=None, correction_keys=()):
    """Bulk equivalent of ``fetch_kbc_attendance_rows`` for Aptem learners."""
    grouped = {}
    for item in learners or []:
        key = str(item.get('aptem_id') or '').strip()
        if key:
            grouped.setdefault(key, []).append(item)
    identities = {key: values[0] for key, values in grouped.items() if len(values) == 1}
    if not identities:
        return []
    dsn = _kbc_attendance_connection_string()
    if not dsn:
        raise RuntimeError('KBC attendance database is not configured.')
    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            sql = '''
                SELECT "ID"::text AS aptem_id,"key","date","Attendance",attendance_status,
                       module,lecture_name,created_at AS updated_at
                FROM public.kbc_attendance
                WHERE "ID"::text=ANY(%s) AND "Attendance" IN (0,1) AND "date" IS NOT NULL
                ORDER BY "ID"::text,"date" DESC,"key"
            '''
            params = [list(identities)]
            if recent_dates is not None:
                # Preserve complete same-day mirror sets and explicitly edited
                # older identities; never transfer an entire register for chips.
                sql = '''WITH ranked AS (
                    SELECT "ID"::text AS aptem_id,"key","date","Attendance",attendance_status,
                        module,lecture_name,created_at AS updated_at,
                        dense_rank() OVER (PARTITION BY "ID"::text ORDER BY "date" DESC) AS day_rank
                    FROM public.kbc_attendance
                    WHERE "ID"::text=ANY(%s) AND "Attendance" IN (0,1)
                      AND "date" IS NOT NULL AND ("date"<=%s OR btrim("key"::text)=ANY(%s)
                        OR (cardinality(%s::text[])>0 AND coalesce(btrim("key"::text),'')=''))
                ) SELECT * FROM ranked WHERE day_rank<=%s OR btrim("key"::text)=ANY(%s)
                    OR (cardinality(%s::text[])>0 AND coalesce(btrim("key"::text),'')='')
                  ORDER BY aptem_id,"date" DESC,"key"'''
                params += [timezone.localdate(), list(correction_keys), list(correction_keys),
                           recent_dates + 1, list(correction_keys), list(correction_keys)]
            cursor.execute(sql, params)
            rows = cursor.fetchall()
    result = []
    for row in rows:
        identity = identities.get(row['aptem_id'])
        if identity:
            normalized = _normalize_kbc_attendance_row(
                row, learner_id=identity['learner_id'], learner_name=identity['learner_name'],
                learner_email=identity['learner_email'],
            )
            if recent_dates is not None:
                normalized['_day_rank'] = row['day_rank']
            result.append(normalized)
    return result


def fetch_kbc_attendance_rates(aptem_ids):
    """Attendance rates for many learners in one query, keyed by Aptem id.

    The per-learner reader above opens a connection and runs a query for each
    learner, which a coach caseload cannot afford. The counting rules are kept
    identical to ``_normalize_kbc_attendance_row`` + ``_summarize_attendance``:
    ``Attendance`` is the signal (1 attended, 0 absent) and ``attendance_status``
    only ever distinguishes 'late', which counts as attended in both the
    numerator and the denominator. So a coach and their learner never read
    different percentages for the same register.

    Returns {aptem_id (str): {'sessions', 'present', 'absent', 'rate', 'lastSessionDate'}} with an
    entry only for learners that actually have counted rows.
    """
    keys = sorted({str(value).strip() for value in (aptem_ids or []) if str(value or '').strip()})
    if not keys:
        return {}
    dsn = _kbc_attendance_connection_string()
    if not dsn:
        raise RuntimeError('KBC attendance database is not configured.')

    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                SELECT "ID"::text AS aptem_id,
                       "Attendance" AS attended,
                       count(*) AS row_count,
                       max("date") AS last_session_date
                FROM public.kbc_attendance
                WHERE "ID"::text = ANY(%s)
                  AND "Attendance" IN (0, 1)
                  AND "date" IS NOT NULL
                  AND "date" <= CURRENT_DATE
                GROUP BY 1, 2
                ''',
                [keys],
            )
            rows = cursor.fetchall()

    totals = {}
    for row in rows:
        bucket = totals.setdefault(row['aptem_id'], {'sessions': 0, 'present': 0, 'absent': 0, 'lastSessionDate': None})
        count = int(row['row_count'] or 0)
        bucket['sessions'] += count
        last_date = row['last_session_date']
        if last_date and (bucket['lastSessionDate'] is None or last_date > bucket['lastSessionDate']):
            bucket['lastSessionDate'] = last_date
        if row['attended'] == 1:
            bucket['present'] += count
        else:
            bucket['absent'] += count
    for bucket in totals.values():
        bucket['rate'] = round((bucket['present'] / bucket['sessions']) * 100) if bucket['sessions'] else 0
    return totals


def fetch_recent_kbc_attendance_rows(learners, *, limit=4):
    """Read recent dated KBC register rows for Aptem-linked learners in one query.

    ``learners`` contains the LMS learner identity alongside its canonical
    Aptem ID. The result stays keyed to the LMS learner ID so callers never
    need to match attendance by name or email.
    """
    entries = [
        {
            'aptem_id': str(item.get('aptem_id') or '').strip(),
            'learner_id': item.get('learner_id'),
            'learner_name': item.get('learner_name') or '',
            'learner_email': item.get('learner_email') or '',
        }
        for item in (learners or [])
        if str(item.get('aptem_id') or '').strip() and item.get('learner_id') is not None
    ]
    if not entries:
        return []
    row_limit = max(int(limit), 1)
    dsn = _kbc_attendance_connection_string()
    if not dsn:
        raise RuntimeError('KBC attendance database is not configured.')

    entries_by_aptem_id = {}
    for entry in entries:
        entries_by_aptem_id.setdefault(entry['aptem_id'], []).append(entry)

    with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                SELECT aptem_id, "key", "date", "Attendance",
                       attendance_status, module, lecture_name, updated_at
                FROM (
                    SELECT "ID"::text AS aptem_id, "key", "date", "Attendance",
                           attendance_status, module, lecture_name,
                           created_at AS updated_at,
                           row_number() OVER (
                               PARTITION BY "ID"::text
                               ORDER BY "date" DESC, "key" DESC
                           ) AS recent_rank
                    FROM public.kbc_attendance
                    WHERE "ID"::text = ANY(%s)
                      AND "Attendance" IN (0, 1)
                      AND "date" IS NOT NULL
                      AND "date" <= CURRENT_DATE
                ) recent
                WHERE recent_rank <= %s
                ORDER BY aptem_id, "date" DESC, "key" DESC
                ''',
                [list(entries_by_aptem_id), row_limit],
            )
            source_rows = cursor.fetchall()

    result = []
    for row in source_rows:
        for entry in entries_by_aptem_id.get(row['aptem_id'], []):
            result.append(_normalize_kbc_attendance_row(
                row,
                learner_id=entry['learner_id'],
                learner_name=entry['learner_name'],
                learner_email=entry['learner_email'],
            ))
    return result


def _summarize_attendance(rows, *, now=None, overview_only=False):
    """Summarize recorded attendance for sessions that have happened so far."""
    now = now or timezone.now()

    def status(row):
        return (row['attendance_status'] or '').strip().lower()

    # Late is a display distinction only: it counts as attended in both the
    # numerator and denominator. Non-attendance workflow states (for example a
    # legacy ``catchup`` row) do not silently dilute the attendance rate.
    from .attendance_rules import attendance_outcome
    counted_rows = [row for row in rows if attendance_outcome(row, now)[1]]
    if not counted_rows:
        return None

    # A missed session recovered by a completed catch-up or an attended
    # alternative session counts as attended; the raw Teams status is kept.
    def missed(row):
        return attendance_outcome(row, now)[0] == 'absent'

    sessions = len(counted_rows)
    absent = sum(missed(row) for row in counted_rows)
    present = sessions - absent
    if overview_only:
        return {'present': present, 'sessions': sessions}
    late = sum(
        status(row) == 'late' or (row['minutes_late'] or 0) > 0
        for row in counted_rows
    )
    catchup = sum(bool(row['catchup_completed']) for row in counted_rows)
    attendance_rate = round((present / sessions) * 100) if sessions else 0

    latest_first = sorted(counted_rows, key=lambda row: row['session_date'], reverse=True)
    consecutive_missed = 0
    for row in latest_first:
        if not missed(row):
            break
        consecutive_missed += 1

    latest = latest_first[0]
    updated_values = [row['updated_at'] for row in counted_rows if row['updated_at']]
    updated_at = max(updated_values) if updated_values else None

    session_history = [
        {
            'id': f"{row.get('session_id', '')}-{row['session_date'].isoformat()}",
            'source': row.get('source', 'kbc-attendance'),
            'sourceId': str(row.get('session_id') or ''),
            'date': row['session_date'].isoformat(),
            'title': row.get('session_title', '') or '',
            'sessionType': row.get('session_type', '') or '',
            'status': attendance_outcome(row, now)[0],
            'rawStatus': row.get('raw_attendance_status') or row['attendance_status'],
            'effectiveStatus': row.get('effective_attendance_status') or row['attendance_status'],
            'startTime': row['session_start_time'].strftime('%H:%M') if row.get('session_start_time') else '',
            'endTime': row['session_end_time'].strftime('%H:%M') if row.get('session_end_time') else '',
            'module': row.get('module_title', '') or '',
            'coach': row.get('coach_name', '') or '',
        }
        for row in latest_first
    ]

    return {
        'learnerEmail': latest['learner_email'],
        'learnerId': latest['learner_id'],
        'learnerName': latest['learner_name'],
        'sessions': sessions,
        'present': present,
        'absent': absent,
        'late': late,
        'catchup': catchup,
        'risk': _risk_from_rate(attendance_rate),
        'lastSessionDate': latest['session_date'].isoformat(),
        'consecutiveMissed': consecutive_missed,
        'updatedAt': updated_at.isoformat() if updated_at else None,
        'attendanceRate': attendance_rate,
        'source': 'combined' if len({row.get('source', 'kbc-attendance') for row in counted_rows}) > 1
                  else counted_rows[0].get('source', 'kbc-attendance'),
        'sessionHistory': session_history,
    }


def combined_attendance_rows(source, *, credit_projection=False, register_schedule=None):
    """Refresh source attendance and apply the learner live-register outcome.

    ``curriculum.live_session_learner_attendance`` is the editable learner
    register used by Bulk Attendance.  Teams evidence remains the source for
    join intervals, but an explicit register correction must be visible in the
    learner workspace immediately after it is saved.
    """
    rows = kbc_attendance_rows(source)
    live_register = _live_register_rows(source)
    # The shared Teams reader groups reconnects by occurrence and email and
    # excludes sessions without a finished attendance report. IDs here refer
    # to LearnerProfile, so use the verified email and check enrolment linkage.
    options = {}
    if credit_projection:
        profile = getattr(source, '_case_file_profile', None)
        owner = getattr(source, '_canonical_profile', None)
        if (profile is not None and owner is not None
                and str(owner['id']) == str(profile.pk)
                and str(profile.enrolment_id) == str(source.id)):
            options['verified_profile'] = profile
        options['include_group_metadata'] = False
        if register_schedule is not None:
            options['register_schedule'] = register_schedule
    teams = projection_call('teams_reports', fetch_verified_teams_attendance_rows, learner_emails=[source.email], **options) if source.email else []
    overridden_occurrences = {
        str(row['occurrence_id']) for row in live_register if row.get('occurrence_id')
    }
    rows.extend(live_register)
    for row in teams:
        if str(row.get('enrolment_id')) != str(source.id):
            continue
        if str(row.get('occurrence_id') or '') in overridden_occurrences:
            continue
        rows.append({**row, 'learner_id': source.id, 'source': 'microsoft-teams'})
    unique = {}
    for row in rows:
        key = (row['learner_id'], row['source'], str(row.get('occurrence_id') or row.get('session_id')))
        previous = unique.get(key)
        if previous is None or row['attendance_status'] in {'present', 'late'}:
            unique[key] = row
    return list(unique.values())


def _live_register_rows(source, *, occurrence_ids=None):
    """Return editable live-register outcomes in learner-register shape.

    The live register is keyed by ``LearnerProfile`` while learner pages use
    the enrolment source.  Resolve exactly one profile and keep this reader
    best-effort so an unavailable optional register cannot hide KBC/Teams
    attendance.
    """
    # Offline callers and pure projection tests pass lightweight source
    # objects; only real learner models have the identity needed for this
    # optional database read.
    if not getattr(source, '_meta', None):
        return []

    from django.db import router

    from curriculum_api.models import (
        LiveSession,
        LiveSessionLearnerAttendance,
        LiveSessionOccurrence,
    )
    from .models import LearnerProfile

    try:
        profile_database = router.db_for_read(LearnerProfile) or 'default'
        profiles = list(
            LearnerProfile.objects.using(profile_database)
            .filter(enrolment_id=source.id)
            .only('id')
        )
        if len(profiles) != 1:
            return []
        profile_id = profiles[0].id
        register_database = router.db_for_read(LiveSessionLearnerAttendance) or 'default'
        register_query = LiveSessionLearnerAttendance.objects.using(register_database).filter(
            learner_profile_id=profile_id,
        )
        if occurrence_ids is not None:
            register_query = register_query.filter(occurrence_id__in=list(occurrence_ids))
        register_rows = list(
            register_query.values(
                'occurrence_id', 'live_session_id', 'module_catalogue_id',
                'attendance_status', 'attended_seconds', 'attendance_report_id',
                'updated_at',
            )
        )
        if not register_rows:
            return []
        occurrence_by_id = {
            row['id']: row
            for row in LiveSessionOccurrence.objects.using(register_database)
            .filter(id__in={row['occurrence_id'] for row in register_rows})
            .exclude(status__in=['cancelled', 'deleted'])
            .values('id', 'scheduled_start', 'scheduled_end', 'session_number')
        }
        session_by_id = {
            row['id']: row
            for row in LiveSession.objects.using(register_database)
            .filter(id__in={row['live_session_id'] for row in register_rows})
            .values('id', 'module_title', 'module_catalogue_id')
        }
    except DatabaseError:
        logger.warning('Learner live attendance register unavailable.', exc_info=True)
        return []

    result = []
    for row in register_rows:
        status = str(row.get('attendance_status') or '').strip().lower()
        occurrence = occurrence_by_id.get(row['occurrence_id'])
        session = session_by_id.get(row['live_session_id'])
        if status not in {'present', 'absent'} or occurrence is None or session is None:
            continue
        start = occurrence.get('scheduled_start')
        end = occurrence.get('scheduled_end')
        if start is None:
            continue
        if timezone.is_naive(start):
            start = timezone.make_aware(start, datetime_timezone.utc)
        if end is not None and timezone.is_naive(end):
            end = timezone.make_aware(end, datetime_timezone.utc)
        local_start = timezone.localtime(start)
        local_end = timezone.localtime(end) if end is not None else None
        module_title = str(session.get('module_title') or '').strip() or 'Live session'
        session_number = occurrence.get('session_number')
        lecture_title = (
            f'{module_title} — Session {session_number}'
            if session_number is not None else module_title
        )
        shared_delivery = row['module_catalogue_id'] != session.get('module_catalogue_id', row['module_catalogue_id'])
        register_id = f"{row['occurrence_id']}:{row['module_catalogue_id']}" if shared_delivery else row['occurrence_id']
        result.append({
            'learner_id': source.id,
            'learner_name': getattr(source, 'username', '') or '',
            'learner_email': getattr(source, 'email', '') or '',
            'session_id': register_id,
            'occurrence_id': register_id,
            'live_session_id': row['live_session_id'],
            'module_catalogue_id': row['module_catalogue_id'],
            'session_date': local_start.date(),
            'session_start_time': local_start.time().replace(tzinfo=None),
            'session_end_time': local_end.time().replace(tzinfo=None) if local_end else None,
            'session_title': lecture_title,
            'module_title': module_title,
            'session_type': 'live_session',
            'attendance_status': status,
            'minutes_late': 0,
            'catchup_completed': False,
            'attended_seconds': max(int(row.get('attended_seconds') or 0), 0),
            'attendance_report_id': str(row.get('attendance_report_id') or '').strip(),
            'updated_at': row.get('updated_at'),
            'source': 'live-register',
        })
    return result


def kbc_attendance_rows(source):
    """Read only the KBC register for a learner with a valid Aptem identity.

    This deliberately does not fall back to email and does not add Teams rows.
    It is used where the UI must mirror ``public.kbc_attendance`` exactly.
    """
    if not student_activity_available(getattr(source, 'aptem_id', None)):
        return []
    return [{**row, 'source': 'kbc-attendance'} for row in fetch_kbc_attendance_rows(
        aptem_id=source.aptem_id,
        learner_id=source.id,
        learner_name=getattr(source, 'username', '') or '',
        learner_email=getattr(source, 'email', '') or '',
    )]


@learner_self_or_staff(kwarg="learner_id")
def learner_attendance(request, kind, learner_id):
    if request.method != 'GET':
        return _error('Method not allowed.', 405)

    model = SOURCE_MODELS.get(kind)
    if model is None:
        return _error("Unknown learner kind. Expected 'commercial' or 'apprenticeship'.", 404)

    try:
        # all_learners: the default manager is scoped to apprenticeship rows.
        from .case_file_sources import source_for_case_file
        source = source_for_case_file(request, kind, learner_id)
        if source is None:
            source = model.all_learners.only('id', 'username', 'email', 'aptem_id').get(pk=learner_id)
    except model.DoesNotExist:
        return _error('Learner not found.', 404)
    except DatabaseError:
        logger.warning('Learner attendance identity lookup failed.', exc_info=True)
        return _error('Unable to load attendance. Please try again.', 502)

    try:
        # The coach Case File requests this focused view. Its source is only
        # the KBC database table and Aptem ID, never an email or Teams merge.
        rows = kbc_attendance_rows(source) if request.GET.get('source') == 'kbc' else None
        if rows is None:
            if getattr(source, 'email', '') and getattr(request, '_case_file_context', None) is None:
                # Case File projects stored attendance without settling outcomes.
                # Student reads retain the existing catch-up settlement path.
                from .catchup_outcomes import sync_catchup_outcomes
                sync_catchup_outcomes(learner_email=getattr(source, 'email', ''))
            from .attendance_lectures import lecture_register
            rows = lecture_register(source)
    except Exception:
        return _error('Unable to load attendance. Please try again.', 502)

    # Only the internal Overview projection omits history formatting. The
    # register projection above remains the same read path.
    context = getattr(request, '_case_file_context', None)
    overview_only = context is not None and context.request.path.rstrip('/').endswith('/overview')
    return JsonResponse({'attendance': _summarize_attendance(rows, overview_only=overview_only)})
