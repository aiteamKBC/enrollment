"""Explicit, short-lived LMS reads. No Django alias, models, or startup hooks.

Only the public read functions below open connections. Django's existing settings
loader supplies the environment; this module never reads or changes a .env file.
"""
import os
from urllib.parse import parse_qsl, unquote, urlsplit

import certifi
import psycopg
from psycopg.rows import dict_row


READER_ROLE = 'enrolment_modules_reader'


class LmsReadUnavailable(RuntimeError):
    """Safe to report: no server exception, DSN, or credentials are attached."""

    def __init__(self, code):
        self.code = code
        super().__init__(f'LMS schedule reading is unavailable ({code}).')


# Column names/types are documented in database/schema.sql and rechecked live
# before referencing any schedule columns. Unknown/changed schemas fail closed.
_TEXT = frozenset({'character varying', 'text'})
_EXPECTED = {
    'modules': {
        **{name: _TEXT for name in (
            'module_catalogue_id', 'programme_id', 'programme_name', 'title',
            'cohort_id', 'cohort_name', 'group_id', 'group_name', 'deleted_by',
            'deleted_via_parent',
        )},
        'start_date': {'date'}, 'end_date': {'date'},
        'sessions_number': {'integer'}, 'is_programme_deleted': {'boolean'},
        'weeks_number': {'integer'},
        'deleted_at': {'timestamp with time zone', 'timestamp without time zone'},
    },
    'cohorts': {
        **{name: _TEXT for name in (
            'cohort_id', 'cohort_name', 'programme_id', 'programme_name',
            'status', 'source_type', 'deleted_by', 'deleted_via_parent',
        )},
        'start_date': {'date'}, 'end_date': {'date'},
        'is_programme_deleted': {'boolean'},
        'deleted_at': {'timestamp with time zone', 'timestamp without time zone'},
    },
}

_GUARD = """
SELECT current_user AS reader_role,
       pg_catalog.current_setting('transaction_read_only') AS read_only,
       pg_catalog.set_config('statement_timeout', '5000', true),
       pg_catalog.set_config('lock_timeout', '1000', true)
"""
_SCHEMA = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'curriculum' AND table_name IN ('modules', 'cohorts', 'groups')
"""
_GROUP_EXPECTED = {
    **{name: _TEXT for name in ('group_id', 'group_name', 'cohort_id', 'programme_id', 'deleted_by', 'deleted_via_parent')},
    'is_programme_deleted': {'boolean'},
    'deleted_at': {'timestamp with time zone', 'timestamp without time zone'},
}
_QUERIES = {
    'cohort_apprenticeship_end': """
        SELECT cohort_id, programme_id, apprenticeship_end_date
        FROM curriculum.cohorts
        WHERE programme_id = %s AND cohort_id = %s
          AND deleted_at IS NULL AND is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(deleted_via_parent), '') IS NULL
        LIMIT 2
    """,
    'saved_module_references': """
        SELECT module_catalogue_id, programme_id, cohort_id, group_id,
               deleted_at, is_programme_deleted, deleted_by, deleted_via_parent
        FROM curriculum.modules WHERE module_catalogue_id = ANY(%s)
        ORDER BY module_catalogue_id LIMIT 201
    """,
    'cohort_groups': """
        SELECT g.group_id, g.group_name, g.cohort_id, g.programme_id
        FROM curriculum.groups g
        INNER JOIN curriculum.cohorts c
          ON c.cohort_id = g.cohort_id AND c.programme_id = g.programme_id
        WHERE g.programme_id = %s AND g.cohort_id = %s
          AND NULLIF(btrim(g.group_id), '') IS NOT NULL
          AND NULLIF(btrim(g.group_name), '') IS NOT NULL
          AND g.deleted_at IS NULL AND g.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(g.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(g.deleted_via_parent), '') IS NULL
          AND c.deleted_at IS NULL AND c.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(c.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(c.deleted_via_parent), '') IS NULL
        ORDER BY g.group_name, g.group_id LIMIT 2001
    """,
    'programme_cohorts': """
        SELECT cohort_id, cohort_name, programme_id, status, source_type,
               start_date, end_date
        FROM curriculum.cohorts
        WHERE programme_id = %s
          AND deleted_at IS NULL AND is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(deleted_via_parent), '') IS NULL
        ORDER BY cohort_id
        LIMIT 2001
    """,
    'assigned_cohort': """
        SELECT c.cohort_id, c.cohort_name, c.programme_id,
               m.module_catalogue_id, m.title, m.group_id, m.group_name,
               m.start_date, m.end_date, m.sessions_number, m.weeks_number
        FROM curriculum.cohorts c
        LEFT JOIN curriculum.modules m
          ON m.cohort_id = c.cohort_id AND m.programme_id = c.programme_id
          AND (%s::text IS NULL OR m.group_id = %s)
          AND m.deleted_at IS NULL AND m.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(m.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(m.deleted_via_parent), '') IS NULL
        WHERE c.programme_id = %s AND c.cohort_id = %s
          AND c.deleted_at IS NULL AND c.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(c.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(c.deleted_via_parent), '') IS NULL
        ORDER BY m.start_date, m.module_catalogue_id
        LIMIT 2001
    """,
    'smoke': """
        SELECT
          (SELECT count(*) FROM (SELECT 1 FROM curriculum.modules LIMIT 3) m)
            AS modules_sample_count,
          (SELECT count(*) FROM (SELECT 1 FROM curriculum.cohorts LIMIT 3) c)
            AS cohorts_sample_count
    """,
    'offerings': """
        SELECT m.module_catalogue_id, m.programme_id, m.programme_name, m.title,
               m.cohort_id, c.cohort_name, m.group_id, m.group_name,
               m.start_date, m.end_date, m.sessions_number, m.weeks_number,
               NULL AS total_otjh,
               c.start_date AS cohort_start_date, c.end_date AS cohort_end_date,
               c.status AS cohort_status, c.source_type AS cohort_source_type
        FROM curriculum.modules m
        INNER JOIN curriculum.cohorts c
          ON c.cohort_id = m.cohort_id AND c.programme_id = m.programme_id
        WHERE m.programme_id = %s
          AND (%s::text IS NULL OR m.cohort_id = %s)
          AND NULLIF(btrim(m.programme_id), '') IS NOT NULL
          AND NULLIF(btrim(m.programme_name), '') IS NOT NULL
          AND m.deleted_at IS NULL AND m.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(m.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(m.deleted_via_parent), '') IS NULL
          AND c.deleted_at IS NULL AND c.is_programme_deleted IS NOT TRUE
          AND lower(btrim(coalesce(c.deleted_by, ''))) <> 'module-delete'
          AND NULLIF(btrim(c.deleted_via_parent), '') IS NULL
          AND lower(btrim(c.status)) IN ('active', 'planned')
          AND m.start_date IS NOT NULL AND m.end_date IS NOT NULL
          AND isfinite(m.start_date) AND isfinite(m.end_date)
          AND m.end_date >= m.start_date
          AND (c.start_date IS NULL OR isfinite(c.start_date))
          AND (c.end_date IS NULL OR isfinite(c.end_date))
          AND (c.start_date IS NULL OR c.end_date IS NULL
               OR c.end_date >= c.start_date)
        ORDER BY m.start_date, m.end_date, m.cohort_id, m.group_id,
                 m.module_catalogue_id
        LIMIT %s OFFSET %s
    """,
}

# Same validated live-offering query, restricted to one saved home group.
_QUERIES['replacement_offerings'] = _QUERIES['offerings'].replace(
    'AND (%s::text IS NULL OR m.cohort_id = %s)',
    'AND m.cohort_id = %s AND m.group_id = %s')


def _connection_failure_code(error):
    """Classify locally; never return the driver's message or connection info."""
    message = str(error).lower()
    if 'service' in message and ('not found' in message or 'definition' in message):
        return 'client_service_configuration_failed'
    if 'certificate' in message or 'root.crt' in message:
        return 'tls_verification_failed'
    if 'channel binding' in message:
        return 'channel_binding_failed'
    if 'password authentication failed' in message:
        return 'authentication_failed'
    if 'timeout' in message or 'timed out' in message:
        return 'connection_timeout'
    return 'connection_failed'


def _connect_safely():
    """Keep private configuration and raw driver exceptions off caller stacks."""
    value = os.environ.get('LMS_READONLY_DATABASE_URL', '').strip()
    if not value:
        return None, 'not_configured'
    if os.environ.get('PGSERVICE'):
        # A libpq service can supply options outside this reader's allowlist.
        return None, 'invalid_configuration'
    try:
        parsed = urlsplit(value)
        query = parse_qsl(parsed.query, strict_parsing=True)
        if (parsed.scheme not in {'postgres', 'postgresql'}
                or not parsed.hostname or not parsed.hostname.endswith('.neon.tech')
                or unquote(parsed.username or '') != READER_ROLE
                or not parsed.password or not parsed.path.lstrip('/')
                or parsed.fragment
                or any(key not in {'sslmode', 'channel_binding', 'connect_timeout', 'sslrootcert'}
                       for key, _ in query)
                or any(key == 'sslrootcert' and val != 'system' for key, val in query)):
            return None, 'invalid_configuration'
        # Reconstruct from allowlisted URL fields, never pass arbitrary libpq
        # URL options. Enforce TLS identity verification, regardless of URL mode.
        connection = psycopg.connect(
            host=parsed.hostname, port=parsed.port or 5432,
            dbname=unquote(parsed.path.lstrip('/')), user=READER_ROLE,
            password=unquote(parsed.password), connect_timeout=5,
            sslmode='verify-full', sslrootcert=certifi.where(), channel_binding='require',
            options='',
            application_name='enrolment-modules-reader',
            autocommit=False, prepare_threshold=None,
        )
        return connection, None
    except (ValueError, TypeError):
        return None, 'invalid_configuration'
    except Exception as error:
        # Never propagate libpq errors: they can contain hosts and credentials.
        return None, _connection_failure_code(error)


def _read(query_name, parameters=()):
    if query_name not in _QUERIES:
        raise LmsReadUnavailable('query_not_allowed')
    connection, failure = _connect_safely()
    if failure:
        raise LmsReadUnavailable(failure)
    rows = None
    try:
        # Psycopg emits BEGIN READ ONLY before the first SELECT. No session or
        # cursor escapes this function; no autocommit or write path is exposed.
        connection.read_only = True
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(_GUARD)
            guard = cursor.fetchone()
            if guard['reader_role'] != READER_ROLE or guard['read_only'] != 'on':
                failure = 'read_only_not_confirmed'
            else:
                cursor.execute(_SCHEMA)
                actual = {(r['table_name'], r['column_name']): r['data_type']
                          for r in cursor.fetchall()}
                if query_name == 'cohort_apprenticeship_end' and actual.get(('cohorts', 'apprenticeship_end_date')) != 'date':
                    failure = 'cohort_apprenticeship_end_access_unavailable'
                elif query_name == 'cohort_groups' and not any(table == 'groups' for table, _ in actual):
                    failure = 'groups_access_unavailable'
                elif query_name == 'cohort_groups' and any(actual.get(('groups', column)) not in types for column, types in _GROUP_EXPECTED.items()):
                    failure = 'schema_mismatch'
                elif any(actual.get((table, column)) not in types
                       for table, columns in _EXPECTED.items()
                       for column, types in columns.items()):
                    failure = 'schema_mismatch'
                else:
                    sql = _QUERIES[query_name]
                    # information_schema exposes this optional column only if
                    # the reader already has access. Never require new grants
                    # or break schedule reads when module hours are unavailable.
                    if query_name in {'offerings', 'replacement_offerings'} and actual.get(('modules', 'total_otjh')) == 'numeric':
                        sql = sql.replace('NULL AS total_otjh', 'm.total_otjh AS total_otjh')
                    cursor.execute(sql, parameters)
                    rows = cursor.fetchall()
    except Exception:
        failure = 'read_failed'
    finally:
        # No commit, even for reads. Always discard transaction-local settings.
        try:
            connection.rollback()
        except Exception:
            failure = failure or 'read_failed'
        try:
            connection.close()
        except Exception:
            failure = failure or 'read_failed'
    # Raise outside the except block so driver errors are not chained into logs.
    if failure:
        raise LmsReadUnavailable(failure)
    return rows


def verify_connection():
    """Explicit opt-in smoke read; return counts capped at 3, never LMS records."""
    return {'read_only': True, **_read('smoke')[0]}


def get_assigned_cohort(programme_id, cohort_id, group_id=None):
    """Exact saved placement only, including undated modules and empty cohorts.

    Group labels come from the permitted modules table, not another database
    role or an inferred first group. With no assigned group, callers may show
    the cohort's offerings with their individual group labels, not as a booking.
    """
    if programme_id is None or cohort_id is None:
        raise ValueError('Provide a verified LMS identifier.')
    for value in (programme_id, cohort_id, group_id):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide a verified LMS identifier.')
    if not programme_id or not cohort_id:
        raise ValueError('Provide a verified LMS identifier.')
    rows = _read('assigned_cohort', (group_id, group_id, programme_id, cohort_id))
    if len(rows) > 2000:
        raise LmsReadUnavailable('assignment_result_incomplete')
    return rows


def get_programme_cohorts(programme_id):
    """Bounded catalogue read for one verified programme, including empty cohorts."""
    if not isinstance(programme_id, str) or not programme_id.strip() or len(programme_id) > 255 or '\x00' in programme_id:
        raise ValueError('Provide a verified LMS identifier.')
    rows = _read('programme_cohorts', (programme_id,))
    if len(rows) > 2000:
        raise LmsReadUnavailable('cohort_result_incomplete')
    return rows


def get_cohort_apprenticeship_end(programme_id, cohort_id):
    """Exact assigned cohort only; never calculate EPA or use another intake."""
    for value in (programme_id, cohort_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide verified saved placement identifiers.')
    rows = _read('cohort_apprenticeship_end', (programme_id, cohort_id))
    if len(rows) > 1 or any(r['programme_id'] != programme_id or r['cohort_id'] != cohort_id for r in rows):
        raise LmsReadUnavailable('cohort_identity_mismatch')
    return rows[0]['apprenticeship_end_date'] if rows else None


def get_cohort_groups(programme_id, cohort_id):
    """Read actual group records, including groups with no modules yet."""
    for value in (programme_id, cohort_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide a verified LMS identifier.')
    rows = _read('cohort_groups', (programme_id, cohort_id))
    if len(rows) > 2000:
        raise LmsReadUnavailable('group_result_incomplete')
    return rows


def get_module_offerings(programme_id, *, cohort_id=None, limit=100, offset=0):
    """Read a page for a caller-verified LMS identifier, not an ME/MM/APM/PCP map.

    Retains individual module/cohort/group identities, including G1/G2 offerings.
    No programme discovery, name-based matching, caching, or assignment updates.
    """
    if programme_id is None:
        raise ValueError('Provide a verified LMS identifier.')
    for value in (programme_id, cohort_id):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide a verified LMS identifier.')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Limit must be between 1 and 200.')
    if type(offset) is not int or not 0 <= offset <= 100_000:
        raise ValueError('Offset must be between 0 and 100000.')
    return _read('offerings', (programme_id, cohort_id, cohort_id, limit, offset))


def get_saved_module_references(module_ids):
    """Deletion/move proof for exact saved IDs; no broad catalogue discovery."""
    if not isinstance(module_ids, (list, tuple)) or not 1 <= len(module_ids) <= 200:
        raise ValueError('Provide between 1 and 200 saved module identifiers.')
    for value in module_ids:
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide a saved module identifier.')
    rows = _read('saved_module_references', (list(dict.fromkeys(module_ids)),))
    if len(rows) > 200:
        raise LmsReadUnavailable('module_reference_result_incomplete')
    return rows


def get_replacement_offerings(programme_id, cohort_id, group_id, *, limit=200, offset=0):
    """Live candidates in exactly the existing programme/cohort/group."""
    for value in (programme_id, cohort_id, group_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide verified saved placement identifiers.')
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('Invalid replacement-offering page.')
    return _read('replacement_offerings', (programme_id, cohort_id, group_id, limit, offset))
