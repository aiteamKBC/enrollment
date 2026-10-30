"""Parameterized SQL only; reads never provision or copy source data."""
import base64
import hashlib
import json
import re
from datetime import date
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from threading import RLock

from django.db import DatabaseError, connections, transaction

from audit_api.db_source import resolve

DB = 'enrolment'
SOURCE_DB = 'audit'
VERSION = 'old-otjh-transition-v1'
CUTOFF = '2026-08'
TRANSITIONS = '"Audit".learner_transitions'
EVENTS = '"Audit".learner_transition_events'
SIGNOFFS = '"Audit".monthly_audit_signoffs'
FINALIZATIONS = 'structured_manual_activities.manual_month_finalization_events'
ROWS = 'structured_manual_activities.manual_learner_activities'
DOCS = 'structured_manual_activities.manual_activity_documents'
PROVISIONAL_ROWS = 'structured_manual_activities.monthly_log_provisional_rows'

_query_alias = ContextVar('old_otjh_query_alias', default=None)
_read_scope = ContextVar('case_file_read_scope', default=None)


@contextmanager
def request_read_scope():
    """Opt-in reuse of identical repository SELECTs for one aggregate request.

    Results are copied because canonical projections enrich their input rows.
    Aliases and the effective journal SQL form part of the key. Never retain
    results between requests or cache a statement containing a write/lock.
    """
    token = _read_scope.set(({}, RLock()))
    try:
        yield
    finally:
        _read_scope.reset(token)


def query(sql, params=()):
    from learner_api.journal_sources import retained_journal_sql
    effective_sql = retained_journal_sql(sql)
    alias = _query_alias.get() or DB
    scope = _read_scope.get()
    read_only = bool(re.match(r'\s*(SELECT|WITH)\b', effective_sql, re.I)) and not re.search(
        r'\b(INSERT|UPDATE|DELETE|MERGE|FOR\s+SHARE|FOR\s+UPDATE|nextval|setval)\b', effective_sql, re.I)
    if scope is not None and read_only:
        cache, lock = scope
        key = (alias, effective_sql, json.dumps(params, default=str, sort_keys=True))
        with lock:
            if key not in cache:
                cache[key] = _query_rows(alias, effective_sql, params)
            return deepcopy(cache[key])
    if scope is not None:
        with scope[1]:
            scope[0].clear()
            return _query_rows(alias, effective_sql, params)
    return _query_rows(alias, effective_sql, params)


def _query_rows(alias, sql, params):
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, params)
        if cursor.description is None:
            return []
        columns = [col[0] for col in cursor.description]
        result = [dict(zip(columns, row)) for row in cursor.fetchall()]
        # The project registers text loaders for json/jsonb in AppConfig.ready.
        for row in result:
            for key in ('required_months', 'metadata'):
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])
        return result


def source_query(sql, params=()):
    """Read the retained journal from the same database as Audit.

    Authentication, transitions and signatures remain on ``enrolment``.  The
    Last_audit/manual-ledger projection deliberately follows the ``audit``
    alias so Learner Monthly Logs cannot silently read a different snapshot.
    """
    token = _query_alias.set(resolve(SOURCE_DB))
    try:
        return query(sql, params)
    finally:
        _query_alias.reset(token)


def source_connection():
    return connections[resolve(SOURCE_DB)]


def student(learner_id):
    rows = query('SELECT id, "Email" AS email, "Username" AS name, '
                 '"Programme" AS programme, aptem_id '
                 'FROM enrolment."Created_users" WHERE id=%s', [learner_id])
    return rows[0] if rows else None


def linked_learners(aptem_id):
    return query('SELECT id FROM enrolment."Created_users" '
                 "WHERE ltrim(btrim(aptem_id), '0')=%s LIMIT 2", [str(aptem_id)])


def historical_learner(aptem_id):
    return source_query('SELECT aptem_id, learner_id, learner_name, learner_email, '
                 'programme_name, programme_status, coach_email, coach_name, planned_hours_monthly '
                 'FROM "Last_audit".learners WHERE aptem_id=%s LIMIT 2', [aptem_id])


def staff(staff_id):
    result = query('SELECT id, "Email" AS email, "Access" AS access '
                   'FROM enrolment."Staff_users" WHERE id=%s', [staff_id])
    return result[0] if result else None


def coach_learners(email, is_admin, page, page_size=25, search=''):
    """One page of a coach's previous-learning records, newest name order.

    ``search`` filters on the learner's name or email, and is applied to the
    count as well as the rows -- filtering only the rows would page through a
    total that no longer matched what is on screen.

    Ranked by how well the match starts rather than purely alphabetically: a
    coach typing "moham" wants Mohamed at the top, not the first name
    alphabetically among the matches. An exact prefix on the name sorts first,
    then a prefix on any word in it, then anything else containing the term.
    """
    where = "btrim(c.aptem_id) ~ '^[0-9]+$' AND l.aptem_id > 0"
    params = []
    if not is_admin:
        where += ' AND lower(btrim(l.coach_email))=%s'
        params.append(email)

    search = str(search or '').strip()
    if search:
        where += ' AND (l.learner_name ILIKE %s OR l.learner_email ILIKE %s)'
        params.extend([f'%{search}%', f'%{search}%'])

    base = ('FROM enrolment."Created_users" c JOIN "Last_audit".learners l '
            "ON ltrim(btrim(c.aptem_id), '0')=l.aptem_id::text WHERE " + where)
    total = query('SELECT count(*) AS n ' + base, params)[0]['n']

    if search:
        # 0 = the name starts with the term, 1 = a word inside it does,
        # 2 = it appears anywhere (including the email).
        rank = ('CASE WHEN l.learner_name ILIKE %s THEN 0 '
                "WHEN l.learner_name ILIKE %s THEN 1 ELSE 2 END")
        order = f'ORDER BY {rank}, lower(l.learner_name), c.id'
        rank_params = [f'{search}%', f'% {search}%']
    else:
        order = 'ORDER BY lower(l.learner_name), c.id'
        rank_params = []

    rows = query('SELECT l.aptem_id AS id, l.learner_name AS name, l.programme_name AS programme '
                 + base + ' ' + order + ' LIMIT %s OFFSET %s',
                 [*params, *rank_params, page_size, (page - 1) * page_size])
    return {'learners': rows, 'total': total, 'page': page, 'page_size': page_size}


def source_months(aptem_id):
    return source_query(f'''SELECT month, count(*) AS row_count,
        coalesce(sum(planned_hours),0) AS planned_hours,
        coalesce(sum(actual_hours) FILTER (WHERE accepted),0) AS actual_hours,
        coalesce(sum(actual_hours) FILTER (WHERE NOT accepted),0) AS not_accepted_hours
        , coalesce(sum(actual_hours),0) AS total_actual_hours
        FROM {ROWS} WHERE aptem_id=%s AND deleted_at IS NULL
        AND month ~ '^[0-9]{{4}}-(0[1-9]|1[0-2])$' AND month<=%s
        GROUP BY month ORDER BY month''', [aptem_id, CUTOFF])


def transition(learner_id, lock=False):
    rows = query(f'SELECT * FROM {TRANSITIONS} WHERE learner_id=%s'
                 + (' FOR UPDATE' if lock else ''), [learner_id])
    return rows[0] if rows else None


def create_transition(learner, months):
    query(f'''INSERT INTO {TRANSITIONS} (learner_id, aptem_id, required_months)
        VALUES (%s,%s,%s::jsonb) ON CONFLICT (learner_id) DO NOTHING''',
          [learner['id'], learner['aptem_id'], json.dumps(months)])
    return transition(learner['id'], lock=True)


@contextmanager
def atomic():
    with transaction.atomic(using=DB):
        yield
        # The event is written before this transaction commits, so the worker
        # can never observe a refresh request for a write that later rolls back.
        # Raw source imports outside this workflow remain covered by the 30s
        # stale-read refresh and bounded reconciliation.
        from .read_model import enqueue_record_monitor_refresh
        enqueue_record_monitor_refresh(reason='previous-record-write', using=DB)


def programme_key(learner):
    return f"otjh-transition:{learner['id']}"


def signatures(learner):
    return query(f'''SELECT id, report_month, signer_role, signer_name,
        signed_at, snapshot_hash, signature_source_document_id AS file_id
        FROM {SIGNOFFS} WHERE learner_id=%s AND programme_key=%s
        AND audit_version=%s AND review_confirmed IS TRUE
        AND coalesce(signature_data,'')<>'' ''',
        [str(learner['aptem_id']), programme_key(learner), VERSION])


def finalizations(aptem_id):
    return query(f'''SELECT DISTINCT ON (report_month)
        id, report_month, event_type, snapshot_hash, row_count, created_at
        FROM {FINALIZATIONS} WHERE aptem_id=%s AND report_month<=%s
        ORDER BY report_month, id DESC''', [aptem_id, CUTOFF])


def pending_revisions(aptem_id):
    return query('''SELECT selected_month AS month, count(*) AS count
        FROM structured_manual_activities.manual_activity_hours_revision
        WHERE aptem_id=%s AND status='pending' GROUP BY selected_month''', [aptem_id])


def _demo_lms_rows(rows, learner, month, *, formula_note='FORMULA PREVIEW ONLY — calculated value',
                   fill_attendance_missing=False, fill_all_missing_actual=False):
    """Overlay deterministic presentation values without touching source data.

    This is deliberately opt-in (``?demo=1``) and presentation-only.  It is
    useful when reviewing the completed-row layout before the real hours/KSB
    reconciliation has been approved.  Existing non-empty values always win;
    only missing values are filled with the documented deterministic formula.
    """
    pool = ('K1', 'K2', 'S1', 'S2', 'B1', 'B2')
    result = []
    for row in rows:
        source_ref = row.get('source_ref') or f"row:{row.get('id')}"
        digest = hashlib.sha256(f"{learner.get('aptem_id')}|{month}|{source_ref}".encode()).digest()
        offset = (-15, -10, -5, 0, 5, 10, 15)[digest[0] % 7]
        category = str(row.get('category') or '').strip().lower()
        try:
            minutes = float(row.get('duration_minutes') or 29)
        except (TypeError, ValueError):
            minutes = 29
        timestamp_label = str(row.get('timestamp_label') or '').strip()
        if re.fullmatch(r'\d{2}:\d{2}:\d{2}-\d{2}:\d{2}:\d{2}', timestamp_label):
            from datetime import datetime
            start, end = timestamp_label.split('-', 1)
            try:
                elapsed = (datetime.strptime(end, '%H:%M:%S') -
                           datetime.strptime(start, '%H:%M:%S')).total_seconds()
                minutes = max(1, round(elapsed / 60))
            except ValueError:
                minutes = max(1, round(minutes + offset))
        elif category in ('reading+quiz', 'reading_quiz', 'reading'):
            # 29-minute Reading/Quiz reference on the permitted 5-minute grid.
            minutes = max(5, min(40, round((29 + offset) / 5) * 5))
        else:
            minutes = max(1, round(minutes + offset))
        formula_hours = round(minutes / 60, 4)
        pending = bool(row.get('actual_pending')) or row.get('actual_hours') is None
        projected = str(row.get('source_ref') or '').startswith('la:')
        planned_missing = row.get('planned_hours') is None or (projected and not row.get('planned_hours'))
        actual_missing = (pending or
                          (row.get('actual_hours') in (0, 0.0, '0', '0.0') and
                           (fill_all_missing_actual or row.get('accepted', True)) and
                           (fill_attendance_missing or category != 'attendance')))
        ksb_codes_value = row.get('ksb_codes') or ksb_codes(row.get('component_ksbs'))
        if not ksb_codes_value:
            start = digest[1] % len(pool)
            ksb_codes_value = [pool[(start + index) % len(pool)] for index in range(6)]
        # Preview values are visibly synthetic and always positive; they never
        # overwrite a real source value or participate in signing/finalisation.
        row = {**row, 'demo_only': True, 'ksb_codes': ksb_codes_value}
        if planned_missing:
            row['planned_hours'] = formula_hours
        if actual_missing:
            row['actual_hours'] = formula_hours
            row['actual_pending'] = False
        if actual_missing or planned_missing or not row.get('completion_note'):
            row['completion_note'] = f'{formula_note} ({offset:+d} min).'
        result.append(row)
    return result


def provisional_rows(learner, month):
    """Read the separately stored formula reconstruction for one month.

    This table is intentionally independent from the accepted journal rows.
    Older deployments may not have it yet, so a missing table behaves as an
    empty provisional layer rather than making the canonical report fail.
    """
    try:
        table_info = source_query('SELECT to_regclass(%s) AS table_name', [PROVISIONAL_ROWS])
        if not table_info or 'table_name' not in table_info[0] or not table_info[0].get('table_name'):
            return []
        return source_query(f'''SELECT source_ref, payload, provisional_fields,
                                       formula_rule, created_at
                                  FROM {PROVISIONAL_ROWS}
                                 WHERE aptem_id=%s AND month=%s AND status='provisional'
                                 ORDER BY source_ref''', [learner.get('aptem_id'), month])
    except DatabaseError:
        return []


def _has_hours(value):
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _apply_provisional_rows(rows, learner, month):
    records = provisional_rows(learner, month)
    if not records:
        return rows
    by_ref = {str(item.get('source_ref')): item for item in records if item.get('source_ref')}
    merged = []
    consumed = set()
    for original in rows:
        row = dict(original)
        source_ref = str(row.get('source_ref') or f"row:{row.get('id')}")
        record = by_ref.get(source_ref)
        payload = record.get('payload') if record else None
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = None
        raw_fields = record.get('provisional_fields') if record else []
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (TypeError, ValueError):
                raw_fields = [raw_fields]
        fields = set(raw_fields or [])
        applied = []
        if isinstance(payload, dict):
            if 'planned' in fields and (row.get('planned_hours') is None or not _has_hours(row.get('planned_hours'))):
                row['planned_hours'] = payload.get('planned_hours')
                applied.append('planned')
            if 'actual' in fields and (row.get('actual_pending') or not _has_hours(row.get('actual_hours'))):
                row['actual_hours'] = payload.get('actual_hours')
                row['actual_pending'] = False
                applied.append('actual')
            if 'ksb' in fields and not row.get('ksb_codes'):
                row['ksb_codes'] = payload.get('ksb_codes') or []
                applied.append('ksb')
        if applied:
            row['provisional'] = True
            row['provisional_fields'] = sorted(applied)
            row['provisional_source'] = 'formula_reconstruction'
            row['provisional_formula_rule'] = record.get('formula_rule')
            row['provisional_created_at'] = record.get('created_at')
            consumed.add(source_ref)
        merged.append(row)
    # Keep reconstruction rows whose source activity is no longer present in
    # the live mirror visible for review, without mutating the canonical rows.
    for record in records:
        source_ref = str(record.get('source_ref') or '')
        if source_ref in consumed or any(str(row.get('source_ref') or f"row:{row.get('id')}") == source_ref for row in merged):
            continue
        payload = record.get('payload')
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = None
        if not isinstance(payload, dict):
            continue
        row = dict(payload)
        row['provisional'] = True
        raw_fields = record.get('provisional_fields') or []
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (TypeError, ValueError):
                raw_fields = [raw_fields]
        row['provisional_fields'] = sorted(set(raw_fields or []))
        row['provisional_source'] = 'formula_reconstruction'
        row['provisional_formula_rule'] = record.get('formula_rule')
        row['provisional_created_at'] = record.get('created_at')
        merged.append(row)
    return merged


def month_rows(learner, month, *, demo=False):
    import mimetypes
    from audit_api.last_audit_ledger_views import _duration_min_sql
    rows = source_query(f'''SELECT r.id, r.month, r.category, r.source_ref, r.group_id, r.activity_id, r.title,
        r.activity_date, r.activity_time, r.planned_hours, r.actual_hours, r.timestamp_label,
        r.completion_note, r.accepted, r.updated_at, g.group_name,
        {_duration_min_sql('a')} AS duration_minutes,
        a.raw #> '{{live_lms_component,ksbs}}' AS component_ksbs,
        jk.ksbs AS journal_ksbs, lk.ksbs AS learner_ksbs,
        lk.source_preference AS ksb_source_preference, mk.ksbs AS material_ksbs
        FROM {ROWS} r
        LEFT JOIN "Last_audit".groups g ON g.group_id=r.group_id
        LEFT JOIN "Last_audit".activities a ON a.activity_id=r.activity_id
        LEFT JOIN structured_manual_activities.learner_journal_row_ksbs jk
          ON jk.row_id=r.id AND jk.aptem_id=r.aptem_id
        LEFT JOIN structured_manual_activities.learner_activity_ksbs lk
          ON lk.activity_id=r.activity_id AND lk.aptem_id=r.aptem_id
        LEFT JOIN structured_manual_activities.activity_ksbs mk ON mk.activity_id=r.activity_id
        WHERE r.aptem_id=%s AND r.month=%s AND r.deleted_at IS NULL
        ORDER BY r.activity_date NULLS LAST, r.id''', [learner['aptem_id'], month])
    docs = source_query(f'''SELECT d.id, d.manual_activity_id, d.display_name,
        d.content_type, d.size_bytes, d.uploaded_at, d.blob_name FROM {DOCS} d JOIN {ROWS} r
        ON r.id=d.manual_activity_id AND r.aptem_id=d.aptem_id
        WHERE d.aptem_id=%s AND r.month=%s AND d.deleted_at IS NULL
        AND r.deleted_at IS NULL ORDER BY d.id''', [learner['aptem_id'], month])
    for doc in docs:
        blob_name = doc.pop('blob_name', '')
        if not doc.get('content_type') or doc['content_type'] == 'application/octet-stream':
            doc['content_type'] = mimetypes.guess_type(blob_name)[0] or mimetypes.guess_type(doc['display_name'])[0]
    activity_ids = list({r['activity_id'] for r in rows if r['activity_id'] is not None})
    results = source_query('''SELECT activity_id, group_id, status, video_started,
        video_completed, reading_viewed, quiz_attempted, quiz_passed, quiz_score,
        quiz_maximum_score, quiz_attempt_number, mapped_hours, updated_at
        FROM "Last_audit".activity_results
        WHERE learner_id=%s AND activity_id=ANY(%s)''',
        [learner.get('lms_id'), activity_ids]) if activity_ids and learner.get('lms_id') else []
    evidence_ids = [int(r['source_ref'][3:]) for r in rows
                    if re.fullmatch(r'ev:[0-9]+', r.get('source_ref') or '')]
    evidence = source_query('''SELECT evidence_id, component_name, ksb_codes
        FROM fetching_evidence.evidence_items
        WHERE learner_id=%s AND evidence_id=ANY(%s)''',
        [learner['aptem_id'], evidence_ids]) if evidence_ids else []
    evidence = {str(e['evidence_id']): e for e in evidence}
    for row in rows:
        item = evidence.get((row.get('source_ref') or '')[3:], {}) if (row.get('source_ref') or '').startswith('ev:') else {}
        row['component_name'] = item.get('component_name')
        # The journal's saved row mapping wins, followed by its selected
        # learner/material mapping. An explicitly cleared mapping stays empty.
        journal_ksbs = row.pop('journal_ksbs', None)
        learner_ksbs = row.pop('learner_ksbs', None)
        material_ksbs = row.pop('material_ksbs', None)
        preference = row.pop('ksb_source_preference', None)
        component_ksbs = row.pop('component_ksbs', None)
        selected_ksbs = learner_ksbs if preference == 'learner' else material_ksbs
        if journal_ksbs is not None:
            selected_ksbs = journal_ksbs
        if selected_ksbs is None:
            selected_ksbs = item.get('ksb_codes') or component_ksbs
        row['ksb_codes'] = ksb_codes(selected_ksbs)
        row['documents'] = [d for d in docs if d['manual_activity_id'] == row['id']]
        row['results'] = [r for r in results if r['activity_id'] == row['activity_id']
                          and (row['group_id'] is None or r['group_id'] == row['group_id'])]
    rows.extend(lms_activity_rows(learner, month, rows))
    rows = _apply_provisional_rows(rows, learner, month)
    if demo:
        rows = _demo_lms_rows(rows, learner, month)
    # Read the original mirrored evidence for previously unlinked assignments.
    # This is a projection, never the legacy INSERT-based attachment backfill.
    missing_docs = [r for r in rows if r['category'] == 'assignment' and not r['documents']]
    source_docs = source_documents(learner, [r['id'] for r in missing_docs])
    for row in missing_docs:
        row['documents'] = [{k: v for k, v in doc.items() if k not in {'container', 'blob_name', 'body', 'row_id'}}
                            for doc in source_docs if doc['row_id'] == row['id']]
    return rows


def _lms_row_id(learner_id, group_id, activity_id):
    """Stable id for a read-only LMS projection row.

    These rows are deliberately not inserted into the manual journal.  The
    id only needs to remain stable between reads so the activity expansion and
    signature digest do not jump when another source row is added.
    """
    ref = f'lms-journal:{learner_id}:{group_id}:{activity_id}'
    return int(hashlib.sha256(ref.encode()).hexdigest()[:13], 16)


def _lms_marked_where(alias='r'):
    """A learner has marked an activity when LMS progress is present."""
    prefix = f'{alias}.'
    return f"""(
        lower(coalesce({prefix}status, '')) NOT IN ('', 'not_started')
        OR coalesce({prefix}video_started, false)
        OR coalesce({prefix}video_completed, false)
        OR coalesce({prefix}reading_viewed, false)
        OR coalesce({prefix}quiz_attempted, false)
        OR coalesce({prefix}quiz_passed, false)
    )"""


def lms_activity_rows(learner, month, existing_rows=None):
    """Project marked LMS activities missing from the historical journal.

    The migration is intentionally read-only.  Manual rows remain the source
    of truth when an activity is already present; only the missing LMS result
    is projected.  An approved ``activity_actual_hours`` value wins, followed
    by the LMS ``mapped_hours`` value.  Missing mappings are exposed as
    ``actual_pending`` instead of deriving a duration from a configured length.
    """
    if not learner.get('lms_id'):
        return []
    existing_rows = existing_rows or []
    existing = {(row.get('activity_id'), row.get('group_id'))
                for row in existing_rows if row.get('activity_id') is not None}
    from audit_api.last_audit_ledger_views import _duration_min_sql
    activity_rows = source_query(f'''SELECT r.group_id, r.activity_id, r.status,
            r.video_started, r.video_completed, r.reading_viewed,
            r.quiz_attempted, r.quiz_passed, r.quiz_score,
            r.quiz_maximum_score, r.quiz_attempt_number, r.mapped_hours,
            ph.planned_hours AS mapped_planned_hours,
            ah.actual_hours AS mapped_actual_hours,
            ah.reporting_method, ah.timestamp_label, ah.start_time, ah.end_time,
            a.activity_type, a.title, a.activity_date,
            a.raw #> '{{live_lms_component,ksbs}}' AS component_ksbs,
            g.group_name, {_duration_min_sql('a')} AS duration_minutes
        FROM "Last_audit".activity_results r
        JOIN "Last_audit".activities a ON a.activity_id=r.activity_id
        LEFT JOIN "Last_audit".groups g ON g.group_id=r.group_id
        LEFT JOIN "Last_audit".activity_planned_hours ph
          ON ph.learner_id=r.learner_id AND ph.ref=r.activity_id::text
        LEFT JOIN "Last_audit".activity_actual_hours ah
          ON ah.learner_id=r.learner_id AND ah.ref=r.activity_id::text
        WHERE r.learner_id=%s
          AND to_char(a.activity_date, 'YYYY-MM')=%s
          AND {_lms_marked_where('r')}
        ORDER BY a.activity_date, r.group_id, r.activity_id''',
        [learner['lms_id'], month])
    result = []
    for source in activity_rows:
        key = (source.get('activity_id'), source.get('group_id'))
        if key in existing:
            continue
        actual = source.get('mapped_actual_hours')
        if actual is None:
            actual = source.get('mapped_hours')
        actual_pending = actual is None
        category = str(source.get('activity_type') or 'activity').strip().lower()
        source_ref = f"la:{source.get('group_id')}:{source.get('activity_id')}"
        result.append({
            'id': _lms_row_id(learner['lms_id'], source.get('group_id'), source.get('activity_id')),
            'month': month,
            'category': category,
            'source_ref': source_ref,
            'group_id': source.get('group_id'),
            'activity_id': source.get('activity_id'),
            'title': source.get('title') or f"LMS activity {source.get('activity_id')}",
            'activity_date': source.get('activity_date'),
            'activity_time': None,
            'planned_hours': float(source.get('mapped_planned_hours') or 0),
            'actual_hours': float(actual or 0),
            'actual_pending': actual_pending,
            'timestamp_label': source.get('timestamp_label'),
            'completion_note': ('Recorded on LMS. Actual hours pending mapping.'
                                if actual_pending else 'Recorded on LMS.'),
            'accepted': True,
            'updated_at': None,
            'group_name': source.get('group_name'),
            'duration_minutes': source.get('duration_minutes'),
            'component_name': None,
            # Keep an explicit source mapping when one exists.  The demo
            # overlay may fill an empty mapping for presentation only.
            'component_ksbs': source.get('component_ksbs'),
            'ksb_codes': ksb_codes(source.get('component_ksbs')),
            'documents': [],
            'results': [{
                'activity_id': source.get('activity_id'),
                'group_id': source.get('group_id'),
                'status': source.get('status'),
                'video_started': source.get('video_started'),
                'video_completed': source.get('video_completed'),
                'reading_viewed': source.get('reading_viewed'),
                'quiz_attempted': source.get('quiz_attempted'),
                'quiz_passed': source.get('quiz_passed'),
                'quiz_score': source.get('quiz_score'),
                'quiz_maximum_score': source.get('quiz_maximum_score'),
                'quiz_attempt_number': source.get('quiz_attempt_number'),
                'mapped_hours': actual,
                'updated_at': None,
            }],
        })
    return result


def lms_activity_month_totals(learner):
    """Return counts and mapped actuals for projected LMS rows by month."""
    if not learner.get('lms_id'):
        return {}
    rows = source_query(f'''SELECT to_char(a.activity_date, 'YYYY-MM') AS month,
            count(*) AS row_count,
            coalesce(sum(coalesce(ah.actual_hours, r.mapped_hours))
                     FILTER (WHERE coalesce(ah.actual_hours, r.mapped_hours) IS NOT NULL), 0) AS actual_hours
        FROM "Last_audit".activity_results r
        JOIN "Last_audit".activities a ON a.activity_id=r.activity_id
        LEFT JOIN "Last_audit".activity_actual_hours ah
          ON ah.learner_id=r.learner_id AND ah.ref=r.activity_id::text
        WHERE r.learner_id=%s AND a.activity_date IS NOT NULL
          AND a.activity_date <= %s::date
          AND {_lms_marked_where('r')}
          AND NOT EXISTS (
            SELECT 1 FROM {ROWS} existing
            WHERE existing.aptem_id=%s AND existing.month=to_char(a.activity_date, 'YYYY-MM')
              AND existing.activity_id=r.activity_id
              AND coalesce(existing.group_id, -1)=coalesce(r.group_id, -1)
              AND existing.deleted_at IS NULL
          )
        GROUP BY to_char(a.activity_date, 'YYYY-MM')''',
        [learner['lms_id'], f'{CUTOFF}-31', learner['aptem_id']])
    return {row['month']: {'row_count': int(row['row_count'] or 0),
                           'actual_hours': float(row['actual_hours'] or 0)} for row in rows}


def provisional_month_totals(learner):
    """Return only formula-derived deltas, avoiding double-counting journal rows."""
    try:
        table_info = source_query('SELECT to_regclass(%s) AS table_name', [PROVISIONAL_ROWS])
        if not table_info or 'table_name' not in table_info[0] or not table_info[0].get('table_name'):
            return {}
        rows = source_query(f'''SELECT month,
                coalesce(sum(NULLIF(payload->>'actual_hours','')::numeric)
                         FILTER (WHERE provisional_fields @> '["actual"]'::jsonb), 0) AS actual_hours,
                coalesce(sum(NULLIF(payload->>'planned_hours','')::numeric)
                         FILTER (WHERE provisional_fields @> '["planned"]'::jsonb), 0) AS planned_hours
            FROM {PROVISIONAL_ROWS}
           WHERE aptem_id=%s AND status='provisional'
           GROUP BY month''', [learner.get('aptem_id')])
    except DatabaseError:
        return {}
    return {row['month']: {'actual_hours': float(row['actual_hours'] or 0),
                           'planned_hours': float(row['planned_hours'] or 0)} for row in rows}


def source_documents(learner, row_ids):
    import mimetypes
    if not row_ids:
        return []
    evidence = source_query(f'''SELECT r.id AS row_id, r.month, e.evidence_id, e.evidence_name,
        e.file_blob, e.report_blob, e.note_content, e.updated_at AS source_updated_at FROM {ROWS} r
        JOIN fetching_evidence.evidence_items e ON e.learner_id=r.aptem_id AND (
          r.source_ref='ev:' || e.evidence_id::text OR
          r.source_ref='asg:' || e.component_id::text || ':evidence:' || e.evidence_id::text OR
          r.source_ref='asg:' || e.component_id::text)
        WHERE r.aptem_id=%s AND r.id=ANY(%s) AND r.category='assignment' AND r.deleted_at IS NULL
        AND NOT EXISTS(SELECT 1 FROM "Audit".learner_evidence_overrides o
          WHERE o.learner_id=r.aptem_id AND NOT o.is_uploaded AND o.source_evidence_id::text=e.evidence_id::text
          AND (o.deleted_at IS NOT NULL OR o.archived_at IS NOT NULL))
        ORDER BY r.id,e.evidence_id''', [learner['aptem_id'], row_ids])
    # Respect deliberate document deletion from the other system.
    deleted = source_query(f'''SELECT manual_activity_id, blob_name FROM {DOCS}
        WHERE aptem_id=%s AND manual_activity_id=ANY(%s) AND deleted_at IS NOT NULL''',
        [learner['aptem_id'], row_ids])
    tombstones = {(d['manual_activity_id'], d['blob_name']) for d in deleted}
    result = []
    for item in evidence:
        name = item['evidence_name'] or f"Evidence {item['evidence_id']}"
        for index, kind in enumerate(('file', 'report', 'note')):
            blob = item.get(f'{kind}_blob')
            body = item.get('note_content') if kind == 'note' else None
            if not blob and not str(body or '').strip():
                continue
            if (item['row_id'], blob) in tombstones:
                continue
            extension = re.search(r'\.[A-Za-z0-9]{1,6}$', blob or '')
            stem = re.sub(r'\.[A-Za-z0-9]{1,6}$', '', name)
            label = f"Assessment report - {stem}{extension.group() if extension else '.pdf'}" if kind == 'report' else name
            if kind == 'note':
                label = f'{name} - reflection.txt'
            result.append({'id': -(item['evidence_id'] * 3 + index), 'row_id': item['row_id'], 'month': item['month'],
                'source_evidence_id': item['evidence_id'], 'source_kind': kind, 'display_name': label,
                'source_updated_at': item.get('source_updated_at'),
                'content_type': 'text/plain' if kind == 'note' else mimetypes.guess_type(blob or label)[0],
                'container': 'fetch-aptem-evidences', 'blob_name': blob, 'body': body})
    return result


def ksb_codes(value):
    """Only explicit codes in the source mapping; never infer from titles."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [value]
    if isinstance(value, dict):
        value = [item for key in ('K', 'S', 'B')
                 if isinstance(value.get(key), list) for item in value[key]]
    if not isinstance(value, list):
        return []
    codes = []
    for item in value:
        code = str(item.get('code', '') if isinstance(item, dict) else item).strip().upper()
        if re.fullmatch(r'[KSB][0-9]+(?:\.[0-9]+)?', code) and code not in codes:
            codes.append(code)
    return codes


def activity_row(learner, month, row_id):
    rows = source_query(f'''SELECT id, month, title, category, source_ref, group_id, activity_id
        FROM {ROWS} WHERE id=%s AND aptem_id=%s AND month=%s
        AND deleted_at IS NULL''', [row_id, learner['aptem_id'], month])
    return rows[0] if rows else None


def activity_parts(learner, row):
    from .content import resolve, validate_parts
    return validate_parts(resolve(learner, [row], companions=True)[row['id']]['parts'])


def content_review(learner, rows):
    from .content import review
    return review(learner, rows)


def programme_dates(learner):
    # Only the authenticated Aptem identity and an exact programme match; no
    # name-based cross-person matching and no arbitrary choice between dates.
    contracts = source_query('''SELECT DISTINCT program_start_date AS start_date, planned_end_date
        FROM fetching_evidence.aptem_cv_contracts_probe WHERE learner_id=%s
        AND (lower(btrim(current_programme))=lower(btrim(%s))
             OR lower(btrim(program_name))=lower(btrim(%s)))
        AND program_start_date IS NOT NULL LIMIT 2''',
        [learner['aptem_id'], learner['programme'], learner['programme']])
    return dict(contracts[0]) if len(contracts) == 1 else {'start_date': None, 'planned_end_date': None}


def report_profile(learner):
    profile = programme_dates(learner)
    profile['first_evidence_date'] = None
    if not profile['start_date']:
        return profile
    # Same first-evidence definition as the audit report: after programme start,
    # excluding welcome material, deleted/archived records, including overrides.
    candidates = source_query('''SELECT coalesce(o.evidence_date::text, item->>'created_date') AS evidence_date
        FROM fetching_evidence.learner_evidence e
        CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(e.evidence)='array'
            THEN e.evidence ELSE '[]'::jsonb END) item
        LEFT JOIN "Audit".learner_evidence_overrides o
            ON o.learner_id=e.learner_id AND o.is_uploaded=false
            AND o.source_evidence_id::text=item->>'id'
        WHERE e.learner_id=%s AND o.deleted_at IS NULL AND o.archived_at IS NULL
          AND ltrim(lower(coalesce(item->>'name',''))) NOT LIKE 'welcome%%'
          AND ltrim(lower(coalesce(item->>'component_name',''))) NOT LIKE 'welcome%%'
        UNION ALL SELECT evidence_date::text FROM "Audit".learner_evidence_overrides
        WHERE learner_id=%s AND is_uploaded=true AND deleted_at IS NULL AND archived_at IS NULL
          AND ltrim(lower(coalesce(document_name,''))) NOT LIKE 'welcome%%'
          AND ltrim(lower(coalesce(component_name,''))) NOT LIKE 'welcome%%' ''',
        [learner['aptem_id'], learner['aptem_id']])
    dates = []
    for item in candidates:
        try:
            value = date.fromisoformat(str(item['evidence_date'])[:10])
            if value >= date.fromisoformat(str(profile['start_date'])[:10]):
                dates.append(value)
        except (ValueError, TypeError):
            continue
    profile['first_evidence_date'] = min(dates) if dates else None
    return profile


def document(learner, doc_id):
    result = source_query(f'''SELECT d.id, d.container, d.blob_name, d.display_name,
        d.content_type, r.month FROM {DOCS} d JOIN {ROWS} r
        ON r.id=d.manual_activity_id AND r.aptem_id=d.aptem_id
        WHERE d.id=%s AND d.aptem_id=%s AND d.deleted_at IS NULL
        AND r.deleted_at IS NULL''', [doc_id, learner['aptem_id']])
    return result[0] if result else None


def save_signature(learner, month, role, name, file_id, digest):
    url = f'/audit_api/old-otjh/signatures/{file_id}/'
    query(f'''INSERT INTO {SIGNOFFS} (learner_id, programme_key, report_month,
        signer_role, signer_name, review_confirmed, signature_data, signed_at,
        snapshot_hash, audit_version, signature_source_type,
        signature_source_document_id, updated_at)
        VALUES (%s,%s,%s,%s,%s,true,%s,now(),%s,%s,'transition',%s,now())
        ON CONFLICT (learner_id,programme_key,report_month,signer_role)
        DO UPDATE SET signer_name=excluded.signer_name, review_confirmed=true,
        signature_data=excluded.signature_data, signed_at=excluded.signed_at,
        snapshot_hash=excluded.snapshot_hash, audit_version=excluded.audit_version,
        signature_source_type=excluded.signature_source_type,
        signature_source_document_id=excluded.signature_source_document_id,
        updated_at=now()''', [str(learner['aptem_id']), programme_key(learner), month,
                             role, name, url, digest, VERSION, file_id])


def save_learner_signature(learner, name, image_bytes):
    # Store only the learner's newly confirmed, server-sanitized PNG capture.
    # Monthly signoffs retain their own immutable image and audit history.
    image = 'data:image/png;base64,' + base64.b64encode(image_bytes).decode('ascii')
    saved = query('''UPDATE enrolment."Created_users"
        SET "Learner_signature"=%s, "Learner_signature_name"=%s,
            "Learner_signature_saved_at"=now()
        WHERE id=%s AND ltrim(btrim(aptem_id), '0')=%s RETURNING id''',
        [image, name, learner['id'], str(learner['aptem_id'])])
    if len(saved) != 1:
        from .service import identity_error
        identity_error()


def signature_owner(file_id):
    rows = query(f'''SELECT t.learner_id, s.report_month, s.signer_role,
        s.signature_source_document_id AS file_id FROM {SIGNOFFS} s
        JOIN {TRANSITIONS} t ON s.learner_id=t.aptem_id::text
        AND (s.programme_key=('otjh-transition:' || t.learner_id::text)
             OR starts_with(s.programme_key,
                            'otjh-transition:' || t.learner_id::text || ':resign-archive:'))
        WHERE s.audit_version=%s AND s.signature_source_document_id=%s
        AND s.review_confirmed IS TRUE AND coalesce(s.signature_data,'')<>'' ''',
        [VERSION, file_id])
    # A bulk capture can belong to several months, but never several learners
    # or signing roles. The file endpoint still authorizes the owner and month.
    owners = {(row['learner_id'], row['signer_role']) for row in rows}
    return rows[0] if rows and len(owners) == 1 else None


def event(transition_id, month, kind, account, actor_role, metadata=None):
    query(f'''INSERT INTO {EVENTS} (transition_id, report_month, event_type,
        actor_id, actor_role, metadata) VALUES (%s,%s,%s,%s,%s,%s::jsonb)''',
        [transition_id, month, kind, account.id, actor_role, json.dumps(metadata or {}, default=str)])


def finalize(learner, month, digest, row_count, account, reason=None, metadata=None):
    # The existing table requires snapshot for finalized events. Only a digest
    # and scope metadata are stored: never copies of the source records.
    snapshot = {'schema': VERSION, 'aptem_id': learner['aptem_id'], 'month': month,
                'digest': digest, **(metadata or {})}
    query(f'''INSERT INTO {FINALIZATIONS} (aptem_id,report_month,event_type,
        snapshot_hash,snapshot,row_count,actor,reason)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s)''',
        [learner['aptem_id'], month, 'reopened' if reason else 'finalized', digest,
         json.dumps(snapshot), row_count, f'login:{account.id}', reason])


def set_completed(transition_id, complete):
    rows = query(f'''UPDATE {TRANSITIONS} SET completed_at=
        CASE WHEN %s THEN coalesce(completed_at,now()) ELSE NULL END,
        updated_at=now() WHERE id=%s RETURNING completed_at''', [complete, transition_id])
    return rows[0]['completed_at'] if rows else None
