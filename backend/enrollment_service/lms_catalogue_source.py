"""Bounded, explicit reads for Programme Catalogue LMS import and sync.

This module exposes no generic SQL surface. Every operation opens a short-lived
connection through the existing least-privilege LMS reader, confirms READ ONLY,
validates the exact source identity/schema, executes four parameterised data
queries, rolls back, and closes the connection.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, time as time_value
from hashlib import sha256
from urllib.parse import unquote, urlsplit

from psycopg.rows import dict_row

from .lms_readonly import LmsReadUnavailable, READER_ROLE, _connect_safely


SOURCE_SYSTEM = 'lms'
SOURCE_PROJECT_ID = 'green-term-97168878'
SOURCE_BRANCH_ID = 'br-raspy-sky-abvs8lq9'
SOURCE_DATABASE = 'neondb'
MAX_COHORT_ROWS = 2_000
MAX_WEEK_ROWS = 5_000
MAX_KSB_ROWS = 500

_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$')
_REQUIRED_COLUMNS = {
    'programmes': {'programme_id', 'name', 'level', 'description', 'status', 'standard_ref', 'standard_version', 'updated_at'},
    'cohorts': {'cohort_id', 'cohort_name', 'programme_id', 'start_date', 'end_date', 'duration_months', 'epa_months', 'apprenticeship_end_date', 'status', 'updated_at'},
    'groups': {'group_id', 'group_name', 'cohort_id', 'programme_id', 'session_week_day', 'session_start_time', 'session_end_time', 'status', 'updated_at'},
    'modules': {'module_catalogue_id', 'programme_id', 'cohort_id', 'group_id', 'title', 'sessions_number', 'start_date', 'end_date', 'status', 'updated_at'},
    'weeks': {'id', 'module_catalogue_id', 'week_number', 'title', 'display_order', 'status', 'updated_at'},
    'components': {'id', 'week_id', 'module_catalogue_id', 'status'},
    'ksb_component_mappings': {'component_id', 'standard_ksb_id', 'weight', 'weight_class', 'classification', 'status'},
    'ksb_standard_definitions': {'id', 'standard_ref', 'standard_version', 'ksb_code', 'ksb_type', 'ksb_description', 'updated_at'},
}

_GUARD_SQL = """
SELECT current_user AS reader_role,
       pg_catalog.current_setting('transaction_read_only') AS read_only,
       pg_catalog.set_config('statement_timeout', '8000', true),
       pg_catalog.set_config('lock_timeout', '1000', true)
"""
_SCHEMA_SQL = """
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'curriculum'
  AND (table_name || '.' || column_name) = ANY(%s)
ORDER BY table_name, ordinal_position
"""
_PROGRAMME_SQL = """
SELECT p.programme_id, p.name, p.level, p.description, p.status,
       p.standard_ref, p.standard_version, p.updated_at
FROM curriculum.programmes p
WHERE p.programme_id = %s
LIMIT 2
"""
_HIERARCHY_SQL = f"""
SELECT c.cohort_id, c.cohort_name, c.start_date, c.end_date,
       c.duration_months, c.epa_months, c.apprenticeship_end_date,
       c.status AS cohort_status, c.updated_at AS cohort_updated_at,
       g.group_id, g.group_name, g.session_week_day, g.session_start_time,
       g.session_end_time, g.status AS group_status,
       g.updated_at AS group_updated_at,
       m.module_catalogue_id, m.title AS module_title, m.sessions_number,
       m.start_date AS module_start_date, m.end_date AS module_end_date,
       m.status AS module_status, m.updated_at AS module_updated_at
FROM curriculum.cohorts c
LEFT JOIN curriculum.groups g
  ON g.cohort_id = c.cohort_id AND g.programme_id = c.programme_id
LEFT JOIN curriculum.modules m
  ON m.group_id = g.group_id AND m.cohort_id = c.cohort_id
 AND m.programme_id = c.programme_id
WHERE c.programme_id = %s AND c.start_date >= %s
ORDER BY c.start_date, c.cohort_id, g.group_id,
         m.start_date NULLS LAST, m.module_catalogue_id
LIMIT {MAX_COHORT_ROWS + 1}
"""
_WEEKS_SQL = f"""
WITH selected_modules AS (
  SELECT m.module_catalogue_id
  FROM curriculum.modules m
  JOIN curriculum.cohorts c
    ON c.cohort_id = m.cohort_id AND c.programme_id = m.programme_id
  WHERE m.programme_id = %s AND c.start_date >= %s
    AND c.status IN ('active', 'planned') AND m.status = 'active'
)
SELECT w.id, w.module_catalogue_id, w.week_number, w.title,
       w.display_order, w.status, w.updated_at
FROM curriculum.weeks w
JOIN selected_modules sm ON sm.module_catalogue_id = w.module_catalogue_id
WHERE w.status = 'active'
ORDER BY w.module_catalogue_id, w.display_order, w.week_number, w.id
LIMIT {MAX_WEEK_ROWS + 1}
"""
_KSBS_SQL = f"""
WITH selected_modules AS (
  SELECT DISTINCT m.module_catalogue_id
  FROM curriculum.modules m
  JOIN curriculum.cohorts c
    ON c.cohort_id = m.cohort_id AND c.programme_id = m.programme_id
  JOIN curriculum.groups g
    ON g.group_id = m.group_id AND g.cohort_id = m.cohort_id
   AND g.programme_id = m.programme_id AND g.status = 'active'
  WHERE m.programme_id = %s AND c.start_date >= %s
    AND c.status IN ('active', 'planned') AND m.status = 'active'
), coverage AS (
  SELECT c.module_catalogue_id, km.standard_ksb_id,
         COUNT(*) AS mapping_count,
         COUNT(*) FILTER (WHERE km.weight IS NULL) AS missing_weight_count,
         SUM(km.weight)::text AS total_weight,
         COUNT(*) FILTER (WHERE km.weight_class = 'hard') AS hard_count,
         COUNT(*) FILTER (WHERE km.weight_class = 'soft') AS soft_count,
         COUNT(*) FILTER (WHERE km.weight_class = 'possible') AS possible_count,
         COALESCE(SUM(km.weight) FILTER (WHERE km.weight_class = 'hard'), 0)::text AS hard_weight,
         COALESCE(SUM(km.weight) FILTER (WHERE km.weight_class = 'soft'), 0)::text AS soft_weight,
         COALESCE(SUM(km.weight) FILTER (WHERE km.weight_class = 'possible'), 0)::text AS possible_weight,
         COUNT(*) FILTER (WHERE km.classification = 'main') AS main_count,
         COUNT(*) FILTER (WHERE km.classification = 'secondary') AS secondary_count,
         COUNT(*) FILTER (WHERE km.classification = 'possible') AS possible_classification_count,
         COALESCE(SUM(km.weight) FILTER (WHERE km.classification = 'main'), 0)::text AS main_weight,
         COALESCE(SUM(km.weight) FILTER (WHERE km.classification = 'secondary'), 0)::text AS secondary_weight,
         COALESCE(SUM(km.weight) FILTER (WHERE km.classification = 'possible'), 0)::text AS possible_classification_weight
  FROM curriculum.components c
  JOIN selected_modules sm ON sm.module_catalogue_id = c.module_catalogue_id
  JOIN curriculum.weeks w
    ON w.id = c.week_id AND w.module_catalogue_id = c.module_catalogue_id
   AND w.status = 'active'
  JOIN curriculum.ksb_component_mappings km
    ON km.component_id = c.id AND km.status = 'active'
  WHERE c.status = 'active'
  GROUP BY c.module_catalogue_id, km.standard_ksb_id
)
SELECT d.id, d.ksb_code, d.ksb_type, d.ksb_description,
       d.standard_ref, d.standard_version, d.updated_at,
       COALESCE(array_agg(DISTINCT cv.module_catalogue_id)
         FILTER (WHERE cv.module_catalogue_id IS NOT NULL),
         ARRAY[]::varchar[]) AS module_ids,
       COALESCE(jsonb_agg(jsonb_build_object(
         'module_id', cv.module_catalogue_id,
         'total_weight', cv.total_weight,
         'mapping_count', cv.mapping_count,
         'missing_weight_count', cv.missing_weight_count,
         'hard_count', cv.hard_count, 'soft_count', cv.soft_count,
         'possible_count', cv.possible_count,
         'hard_weight', cv.hard_weight, 'soft_weight', cv.soft_weight,
         'possible_weight', cv.possible_weight,
         'main_count', cv.main_count, 'secondary_count', cv.secondary_count,
         'possible_classification_count', cv.possible_classification_count,
         'main_weight', cv.main_weight, 'secondary_weight', cv.secondary_weight,
         'possible_classification_weight', cv.possible_classification_weight
       ) ORDER BY cv.module_catalogue_id)
         FILTER (WHERE cv.module_catalogue_id IS NOT NULL), '[]'::jsonb) AS module_weights
FROM curriculum.ksb_standard_definitions d
LEFT JOIN coverage cv ON cv.standard_ksb_id = d.id
WHERE d.standard_ref = %s AND d.standard_version = %s
GROUP BY d.id, d.ksb_code, d.ksb_type, d.ksb_description,
         d.standard_ref, d.standard_version, d.updated_at
ORDER BY d.ksb_code
LIMIT {MAX_KSB_ROWS + 1}
"""


def _valid_identifier(value):
    return isinstance(value, str) and bool(_IDENTIFIER.fullmatch(value))


def _source_identity_is_exact():
    values = {
        'project': os.environ.get('LMS_READONLY_PROJECT_ID', '').strip(),
        'branch': os.environ.get('LMS_READONLY_BRANCH_ID', '').strip(),
        'database': os.environ.get('LMS_READONLY_DATABASE_NAME', '').strip(),
    }
    if values != {'project': SOURCE_PROJECT_ID, 'branch': SOURCE_BRANCH_ID, 'database': SOURCE_DATABASE}:
        return False
    try:
        parsed = urlsplit(os.environ.get('LMS_READONLY_DATABASE_URL', '').strip())
    except ValueError:
        return False
    return unquote(parsed.path.lstrip('/')) == SOURCE_DATABASE and unquote(parsed.username or '') == READER_ROLE


def _json_value(value):
    if isinstance(value, (date, datetime, time_value)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def canonical_fingerprint(value):
    payload = json.dumps(_json_value(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return sha256(payload.encode('utf-8')).hexdigest()


def catalogue_query_sql():
    """Expose immutable SQL templates for plan verification tests only."""
    return (_PROGRAMME_SQL, _HIERARCHY_SQL, _WEEKS_SQL, _KSBS_SQL)


def read_catalogue_snapshot(programme_id, cohort_start_date, standard_ref, standard_version):
    if not all(_valid_identifier(item) for item in (programme_id, standard_ref, standard_version)):
        raise ValueError('Provide valid LMS catalogue identifiers.')
    if not isinstance(cohort_start_date, date):
        raise ValueError('Provide a cohort start date filter.')
    if not _source_identity_is_exact():
        raise LmsReadUnavailable('catalogue_source_identity_mismatch')

    connection, failure = _connect_safely()
    if failure:
        raise LmsReadUnavailable(failure)
    started = time.perf_counter()
    query_metrics = []
    result = None
    try:
        connection.read_only = True
        with connection.cursor(row_factory=dict_row) as cursor:
            def execute(name, sql, params=()):
                before = time.perf_counter()
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                query_metrics.append({
                    'name': name, 'rows': len(rows),
                    'execution_ms': round((time.perf_counter() - before) * 1000, 2),
                })
                return rows

            guard_rows = execute('read_only_guard', _GUARD_SQL)
            guard = guard_rows[0] if guard_rows else {}
            if guard.get('reader_role') != READER_ROLE or guard.get('read_only') != 'on':
                raise LmsReadUnavailable('read_only_not_confirmed')
            required_names = [f'{table}.{column}' for table, columns in _REQUIRED_COLUMNS.items() for column in columns]
            schema_rows = execute('schema_guard', _SCHEMA_SQL, (required_names,))
            actual = {}
            for row in schema_rows:
                actual.setdefault(row['table_name'], set()).add(row['column_name'])
            if any(not columns.issubset(actual.get(table, set())) for table, columns in _REQUIRED_COLUMNS.items()):
                raise LmsReadUnavailable('catalogue_schema_mismatch')

            programmes = execute('programme', _PROGRAMME_SQL, (programme_id,))
            if len(programmes) != 1:
                raise LmsReadUnavailable('catalogue_programme_not_found')
            programme = programmes[0]
            if programme['standard_ref'] != standard_ref or programme['standard_version'] != standard_version:
                raise LmsReadUnavailable('catalogue_standard_mismatch')
            hierarchy = execute('hierarchy', _HIERARCHY_SQL, (programme_id, cohort_start_date))
            weeks = execute('weekly_topics', _WEEKS_SQL, (programme_id, cohort_start_date))
            ksbs = execute('module_ksb_coverage', _KSBS_SQL, (
                programme_id, cohort_start_date, standard_ref, standard_version,
            ))
            for row in ksbs:
                if isinstance(row['module_weights'], str):
                    row['module_weights'] = json.loads(row['module_weights'])
                if not isinstance(row['module_weights'], list):
                    raise LmsReadUnavailable('catalogue_weight_payload_invalid')
            if len(hierarchy) > MAX_COHORT_ROWS or len(weeks) > MAX_WEEK_ROWS or len(ksbs) > MAX_KSB_ROWS:
                raise LmsReadUnavailable('catalogue_result_incomplete')
            result = _json_value({
                'programme': programme, 'hierarchy': hierarchy,
                'weeks': weeks, 'ksbs': ksbs,
            })
    except LmsReadUnavailable:
        raise
    except Exception:
        failure = 'catalogue_read_failed'
    finally:
        try:
            connection.rollback()
        except Exception:
            failure = failure or 'catalogue_read_failed'
        try:
            connection.close()
        except Exception:
            failure = failure or 'catalogue_read_failed'
    if failure:
        raise LmsReadUnavailable(failure)

    payload_bytes = len(json.dumps(result, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    metrics = {
        'query_count': len(query_metrics),
        'data_query_count': 4,
        'rows_returned': sum(item['rows'] for item in query_metrics),
        'payload_bytes': payload_bytes,
        'payload_kb': round(payload_bytes / 1024, 2),
        'execution_ms': round((time.perf_counter() - started) * 1000, 2),
        'queries': query_metrics,
    }
    warnings = []
    if metrics['query_count'] != 6:
        warnings.append('Unexpected LMS SQL query count.')
    if metrics['payload_bytes'] > 512 * 1024:
        warnings.append('The LMS preview payload is larger than 512 KB.')
    if metrics['execution_ms'] > 5000:
        warnings.append('The LMS preview read took longer than 5 seconds.')
    return {**result, 'fingerprint': canonical_fingerprint(result), 'metrics': metrics, 'performance_warnings': warnings}
