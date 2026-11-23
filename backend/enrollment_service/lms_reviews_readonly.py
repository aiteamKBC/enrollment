"""Programme review templates/skip decisions through the approved reader only."""
from psycopg.rows import dict_row
from . import lms_readonly as reader
from .lms_activity_readonly import METADATA, blocker

TEXT = {'text', 'varchar'}
REQUIRED = {
    'review_templates': {**{c: TEXT for c in ('id', 'programme_id', 'name', 'recurrence_unit')},
        'enabled': {'bool'}, 'recurrence_interval': {'int4', 'int8', 'int2'},
        'occurrence_count': {'int4', 'int8', 'int2'}, 'applicable_statuses': {'jsonb', 'json'},
        'applicability': {'jsonb', 'json'}, 'expected_otjh': {'numeric'}, 'counts_towards_otjh': {'bool'},
        'deleted_at': {'timestamp', 'timestamptz'}},
    'review_occurrence_overrides': {**{c: TEXT for c in ('review_id', 'programme_id', 'action')},
        'occurrence_date': {'date'}, 'learner_id': {'int4', 'int8'}, 'deleted_at': {'timestamp', 'timestamptz'}},
}
QUERIES = {
    'review_templates': '''SELECT id,programme_id,name,enabled,recurrence_interval,recurrence_unit,
        occurrence_count,applicable_statuses,applicability,expected_otjh,counts_towards_otjh,deleted_at
        FROM curriculum.review_templates WHERE programme_id=%s AND enabled IS TRUE AND deleted_at IS NULL
        ORDER BY id LIMIT 501''',
    'review_occurrence_overrides': '''SELECT review_id,programme_id,occurrence_date,learner_id,action,deleted_at
        FROM curriculum.review_occurrence_overrides WHERE programme_id=%s AND deleted_at IS NULL
        AND (learner_id IS NULL OR learner_id=%s) AND occurrence_date BETWEEN %s AND %s
        ORDER BY review_id,occurrence_date LIMIT 10001''',
}


def read_programme_reviews(programme_id, learner_profile_id, start, end):
    if not isinstance(programme_id, str) or not programme_id or len(programme_id) > 255:
        raise ValueError('Provide a verified programme ID.')
    if type(learner_profile_id) is not int or learner_profile_id <= 0 or not start or not end or start > end:
        raise ValueError('Provide a verified learner and programme window.')
    result = {'review_templates': None, 'review_occurrence_overrides': None, 'blockers': []}
    conn, failure = reader._connect_safely()
    if failure:
        result['blockers'].append(blocker(failure, 'connection'))
        return result
    current = 'connection'
    try:
        conn.read_only = True
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(reader._GUARD)
            guard = cursor.fetchone()
            if guard['reader_role'] != reader.READER_ROLE or guard['read_only'] != 'on':
                result['blockers'].append(blocker('read_only_not_confirmed', 'connection'))
                return result
            cursor.execute(METADATA, (list(REQUIRED),))
            metadata = cursor.fetchall()
            for table, parameters, limit in (
                ('review_templates', (programme_id,), 500),
                ('review_occurrence_overrides', (programme_id, learner_profile_id, start, end), 10000),
            ):
                current = table
                columns = {r['column_name']: r for r in metadata if r['table_name'] == table}
                if not columns:
                    result['blockers'].append(blocker('table_unavailable', table, REQUIRED[table]))
                    continue
                errors = []
                for code, names in (
                    ('column_missing', [c for c in REQUIRED[table] if c not in columns]),
                    ('select_denied', [c for c in REQUIRED[table] if c in columns and not columns[c]['readable']]),
                    ('column_type_mismatch', [c for c, types in REQUIRED[table].items()
                        if c in columns and columns[c]['data_type'] not in types]),
                ):
                    if names:
                        errors.append(blocker(code, table, names))
                if not all(r['schema_usage'] for r in columns.values()):
                    errors.append(blocker('schema_usage_denied', table))
                if errors:
                    result['blockers'].extend(errors)
                    continue
                cursor.execute(QUERIES[table], parameters)
                rows = cursor.fetchall()
                if len(rows) > limit:
                    result['blockers'].append(blocker('result_incomplete', table))
                else:
                    result[table] = rows
    except Exception:
        result['blockers'].append(blocker('read_failed', current))
    finally:
        try:
            conn.rollback()
        except Exception:
            result['blockers'].append(blocker('rollback_failed', 'connection'))
        try:
            conn.close()
        except Exception:
            result['blockers'].append(blocker('close_failed', 'connection'))
    return result


def read_document_review_definitions(programme_id, review_ids):
    """Optional document metadata; never changes the Meetings recurrence query.

    Select only permitted columns. Classification and signature access are
    independent so a missing signature grant cannot hide a verified type.
    """
    if not isinstance(programme_id, str) or not programme_id or len(programme_id) > 255:
        raise ValueError('Provide a verified programme ID.')
    ids = list(dict.fromkeys(review_ids))
    if len(ids) > 500 or any(not isinstance(i, str) or not i or len(i) > 255 for i in ids):
        raise ValueError('Provide verified review IDs.')
    result = {'definitions': {}, 'blockers': []}
    if not ids:
        return result
    conn, failure = reader._connect_safely()
    if failure:
        result['blockers'].append(blocker(failure, 'connection'))
        return result
    try:
        conn.read_only = True
        with conn.cursor(row_factory=dict_row) as cursor:
            cursor.execute(reader._GUARD)
            guard = cursor.fetchone()
            if guard['reader_role'] != reader.READER_ROLE or guard['read_only'] != 'on':
                result['blockers'].append(blocker('read_only_not_confirmed', 'connection'))
                return result
            cursor.execute(METADATA, (['review_templates', 'review_types'],))
            meta = {(r['table_name'], r['column_name']): r for r in cursor.fetchall()}

            def allowed(table, columns):
                missing = [c for c in columns if (table, c) not in meta]
                denied = [c for c in columns if (table, c) in meta and
                          not (meta[table, c]['readable'] and meta[table, c]['schema_usage'])]
                if missing:
                    result['blockers'].append(blocker('column_missing', table, missing))
                if denied:
                    result['blockers'].append(blocker('select_denied', table, denied))
                return not missing and not denied

            base = ['id', 'programme_id', 'enabled', 'deleted_at']
            if not allowed('review_templates', base):
                return result
            columns = list(base)
            if allowed('review_templates', ['review_type_id']):
                columns.append('review_type_id')
            signatures = ['signature_' + role for role in ('advisor', 'employer', 'participant', 'referrer')]
            if allowed('review_templates', signatures):
                columns.extend(signatures)
            cursor.execute('SELECT ' + ','.join(columns) + ''' FROM curriculum.review_templates
                WHERE programme_id=%s AND id=ANY(%s) AND enabled IS TRUE AND deleted_at IS NULL
                ORDER BY id LIMIT 501''', (programme_id, ids))
            rows = cursor.fetchall()
            if ({r['id'] for r in rows} != set(ids) or len(rows) != len(ids) or
                    any(r.get('programme_id') != programme_id or not r.get('enabled') or r.get('deleted_at') for r in rows)):
                result['blockers'].append(blocker('review_identity_unverified', 'review_templates', ['id', 'programme_id']))
                return result
            # System type IDs are the existing immutable Review engine constants,
            # never an inference from the editable template name.
            codes = {}
            if 'review_type_id' in columns:
                from curriculum_api.review_types import SYSTEM_REVIEW_TYPES
                codes = {r['id']: r['code'] for r in SYSTEM_REVIEW_TYPES}
            custom = list({r.get('review_type_id') for r in rows} - set(codes) - {None, ''})
            if custom and allowed('review_types', ['id', 'code']):
                cursor.execute('SELECT id,code FROM curriculum.review_types WHERE id=ANY(%s) LIMIT 501', (custom,))
                codes.update({r['id']: r['code'] for r in cursor.fetchall()})
            for row in rows:
                result['definitions'][row['id']] = {
                    'typeCode': codes.get(row.get('review_type_id')),
                    'signatures': {role: row['signature_' + role] for role in
                        ('advisor', 'employer', 'participant', 'referrer')} if all(c in row for c in signatures) else None,
                }
    except Exception:
        result['blockers'].append(blocker('read_failed', 'review_templates'))
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    return result
