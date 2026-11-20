"""Exact-offering activity reads through the approved LMS reader only.

No Django connections, discovery by title, source writes, or startup hooks.
Unavailable permissions/relationships are data in the result, never empty success.
"""
from psycopg.rows import dict_row
from . import lms_readonly as reader

TEXT = {'text', 'varchar'}
INTEGER = {'int4', 'int8', 'int2'}
DELETED = {'deleted_at': {'timestamp', 'timestamptz'}, 'is_programme_deleted': {'bool'},
           'deleted_by': TEXT, 'deleted_via_parent': TEXT}
REQUIRED = {
    'modules': {**{c: TEXT for c in ('module_catalogue_id', 'programme_id', 'cohort_id', 'group_id',
                'title', 'description', 'ksb_profile_source_id')}, **DELETED},
    'weeks': {**{c: TEXT for c in ('id', 'module_catalogue_id', 'title', 'summary')},
              'week_number': INTEGER, 'display_order': INTEGER, 'learning_outcomes': {'jsonb'}, **DELETED},
    'components': {**{c: TEXT for c in ('id', 'module_catalogue_id', 'week_id', 'title', 'type', 'description')},
                   'display_order': INTEGER, 'settings_json': {'jsonb'}, 'expected_otjh': {'numeric'},
                   'ksb_mappings': {'jsonb'}, **DELETED},
    'ksb_mappings': {**{c: TEXT for c in ('id', 'module_catalogue_id', 'week_id', 'component_id',
                    'ksb_code', 'ksb_description', 'classification', 'weight_class')},
                     'weight': {'numeric'}, **DELETED},
    'ksb_profiles': {**{c: TEXT for c in ('id', 'ksb_profile_id')}, 'ksb_items': {'jsonb'}},
    'ksb_profile_versions': {'id': INTEGER, 'source_profile_id': TEXT},
    'ksb_definitions': {'id': INTEGER, 'profile_version_id': INTEGER, 'code': TEXT,
                        'description': TEXT, 'ksb_type': TEXT},
}

METADATA = """SELECT c.relname AS table_name,a.attname AS column_name,t.typname AS data_type,
    has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage,
    has_column_privilege(current_user,c.oid,a.attnum,'SELECT') AS readable
    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
    WHERE n.nspname='curriculum' AND c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped"""


def get_document_week_dates(module_id, programme_id, cohort_id, group_id, weeks):
    """Read calendar inputs, then reuse the existing pure Builder session rule.

    Missing holiday/group access is explicit; never treat an unreadable holiday
    calendar as an empty calendar. No Django cursor or bootstrap is invoked.
    """
    if any(not isinstance(v, str) or not v or len(v) > 255 for v in (module_id, programme_id, cohort_id, group_id)):
        raise ValueError('Provide verified module placement IDs.')
    result = {'weeks': {}, 'blockers': []}
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
            required = {
                'modules': ['module_catalogue_id', 'programme_id', 'cohort_id', 'group_id', 'start_date',
                    'session_week_day', 'session_start_time', 'session_end_time', 'session_overrides', *DELETED],
                'groups': ['group_id', 'cohort_id', 'programme_id', 'session_week_day', 'session_start_time', 'session_end_time', *DELETED],
                'cohorts': ['cohort_id', 'programme_id', 'start_date', 'excluded_holiday_ids', *DELETED],
                'holidays': ['id', 'label', 'start_date', 'end_date', 'notes', 'is_archived'],
                'england_holidays': ['id', 'title', 'holiday_date'],
            }
            optional = {'modules': ['weekly_schedule', 'session_holidays'], 'groups': ['schedule'], 'holidays': ['status']}
            cursor.execute(METADATA, (list(required),))
            metadata = {(r['table_name'], r['column_name']): r for r in cursor.fetchall()}
            columns = {}
            for table, names in required.items():
                columns[table] = names + [c for c in optional.get(table, []) if (table, c) in metadata]
                missing = [c for c in columns[table] if (table, c) not in metadata]
                denied = [c for c in columns[table] if (table, c) in metadata and not
                    (metadata[table, c]['readable'] and metadata[table, c]['schema_usage'])]
                if missing:
                    result['blockers'].append(blocker('column_missing', table, missing))
                if denied:
                    result['blockers'].append(blocker('select_denied', table, denied))
            if result['blockers']:
                return result

            def select(table, where, args):
                cursor.execute('SELECT ' + ','.join(columns[table]) + ' FROM curriculum.' + table +
                    ' t WHERE ' + where + ' LIMIT 10001', args)
                rows = cursor.fetchall()
                if len(rows) > 10000:
                    raise ValueError('Incomplete calendar source')
                return rows

            modules = select('modules', 'module_catalogue_id=%s AND programme_id=%s AND cohort_id=%s AND group_id=%s AND ' + _live('t'),
                (module_id, programme_id, cohort_id, group_id))
            groups = select('groups', 'group_id=%s AND programme_id=%s AND cohort_id=%s AND ' + _live('t'), (group_id, programme_id, cohort_id))
            cohorts = select('cohorts', 'cohort_id=%s AND programme_id=%s AND ' + _live('t'), (cohort_id, programme_id))
            if any(len(rows) != 1 for rows in (modules, groups, cohorts)):
                result['blockers'].append(blocker('calendar_placement_unverified', 'modules'))
                return result
            from curriculum_api import views
            from learner_api.builder_activity_dates import _dated_schedule
            holidays = select('england_holidays', 'TRUE', ())
            holidays += [r for r in select('holidays', 'TRUE', ()) if not views.holiday_row_archived(r)]
            cohort = cohorts[0]
            selected = views.scheduling_holidays_from(holidays, cohort['start_date'],
                views.parse_json_value(cohort['excluded_holiday_ids'], []))
            slots, _ = views.module_week_delivery_slots(modules[0], groups[0], len(weeks), holidays=selected)
            for week, planned in zip(weeks, slots):
                dates = {p.get('date') for p in planned if p.get('date')}
                windows = [_dated_schedule(value, 'builder_week') for value in dates]
                bounds = {(w['week_start'], w['week_end']) for w in windows if w.get('week_start') and not w['date_needs_review']}
                if len(bounds) == 1:
                    start, end = bounds.pop()
                    result['weeks'][week['sourceId']] = {'start': start, 'end': end, 'source': 'existing Builder delivery/holiday rule'}
    except Exception:
        result['blockers'].append(blocker('calendar_read_failed', 'modules'))
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    return result


def blocker(code, table, columns=()):
    return {'database': 'LMS', 'schema': 'curriculum', 'table': table,
            'code': code, 'columns': list(columns),
            'message': f"LMS curriculum.{table}: {code.replace('_', ' ')}."
                       + (f" Columns: {', '.join(columns)}." if columns else '')}


def schema_blockers(table, metadata):
    rows = {r['column_name']: r for r in metadata if r['table_name'] == table}
    if not rows:
        return [blocker('table_unavailable', table, REQUIRED[table])]
    problems = []
    for code, columns in (
        ('column_missing', [c for c in REQUIRED[table] if c not in rows]),
        ('select_denied', [c for c in REQUIRED[table] if c in rows and not rows[c]['readable']]),
        ('column_type_mismatch', [c for c, types in REQUIRED[table].items()
                                  if c in rows and rows[c]['data_type'] not in types]),
    ):
        if columns:
            problems.append(blocker(code, table, columns))
    if not all(r['schema_usage'] for r in rows.values()):
        problems.append(blocker('schema_usage_denied', table))
    return problems


def _live(alias):
    # Match existing schedule reader's soft-deletion exclusions.
    return (f"{alias}.deleted_at IS NULL AND {alias}.is_programme_deleted IS NOT TRUE "
            f"AND lower(btrim(coalesce({alias}.deleted_by,''))) <> 'module-delete' "
            f"AND NULLIF(btrim({alias}.deleted_via_parent),'') IS NULL")


QUERIES = {
    'modules': f"""SELECT module_catalogue_id,programme_id,cohort_id,group_id,title,description,ksb_profile_source_id
        FROM curriculum.modules m WHERE module_catalogue_id=%s AND programme_id=%s
        AND cohort_id=%s AND group_id=%s AND {_live('m')} LIMIT 2""",
    'weeks': f"""SELECT id,module_catalogue_id,week_number,title,summary,learning_outcomes,display_order
        FROM curriculum.weeks w WHERE module_catalogue_id=%s AND {_live('w')}
        ORDER BY display_order,week_number,id LIMIT 2001""",
    # Do not join away orphan weeks: report them explicitly in the projection.
    'components': f"""SELECT id,module_catalogue_id,week_id,type,title,description,expected_otjh,
        display_order,settings_json,ksb_mappings FROM curriculum.components c
        WHERE module_catalogue_id=%s AND {_live('c')} ORDER BY display_order,id LIMIT 5001""",
    'ksb_mappings': f"""SELECT id,module_catalogue_id,week_id,component_id,ksb_code,ksb_description,
        classification,weight,weight_class FROM curriculum.ksb_mappings k
        WHERE module_catalogue_id=%s AND {_live('k')} ORDER BY ksb_code,id LIMIT 5001""",
    'ksb_profiles': """SELECT id,ksb_profile_id,
        (SELECT jsonb_agg(item) FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(ksb_items)='array' THEN ksb_items ELSE '[]'::jsonb END) item
          WHERE item->>'code'=ANY(%s)) AS ksb_items FROM curriculum.ksb_profiles
        WHERE id=%s OR ksb_profile_id=%s ORDER BY id LIMIT 3""",
    'ksb_profile_versions': """SELECT id,source_profile_id FROM curriculum.ksb_profile_versions
        WHERE source_profile_id=%s ORDER BY id LIMIT 3""",
    'ksb_definitions': """SELECT id,profile_version_id,code,description,ksb_type FROM curriculum.ksb_definitions
        WHERE profile_version_id=%s AND code=ANY(%s) ORDER BY code,id LIMIT 5001""",
}


def get_module_activities(module_id, programme_id, cohort_id, group_id):
    """Caller supplies four verified IDs from ONE learner's confirmed plan.

    An exact, live module placement is verified before reading any children.
    Definition enrichment uses one explicit profile version, never latest-by-name.
    """
    for value in (module_id, programme_id, cohort_id, group_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 255 or '\x00' in value:
            raise ValueError('Provide verified module, programme, home cohort and group identifiers.')
    result = {key: None for key in REQUIRED}
    result['blockers'] = []
    conn, failure = reader._connect_safely()
    if failure:
        result['blockers'].append(blocker(failure, 'connection'))
        return result
    current_table = 'connection'
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

            def read(table, parameters, limit):
                nonlocal current_table
                current_table = table
                errors = schema_blockers(table, metadata)
                if errors:
                    result['blockers'].extend(errors)
                    return None
                cursor.execute(QUERIES[table], parameters)
                rows = cursor.fetchall()
                if len(rows) > limit:
                    result['blockers'].append(blocker('result_incomplete', table))
                    return None
                result[table] = rows
                return rows

            modules = read('modules', (module_id, programme_id, cohort_id, group_id), 1)
            if modules is None:
                return result
            if not modules:
                result['blockers'].append(blocker('module_placement_not_found', 'modules'))
                return result
            for table, limit in (('weeks', 2000), ('components', 5000), ('ksb_mappings', 5000)):
                read(table, (module_id,), limit)
            profile_id = modules[0]['ksb_profile_source_id']
            codes = {r['ksb_code'] for r in result['ksb_mappings'] or [] if r['ksb_code']}
            for component in result['components'] or []:
                for item in component.get('ksb_mappings') or []:
                    if isinstance(item, dict) and (item.get('code') or item.get('ksb_code')):
                        codes.add(item.get('code') or item['ksb_code'])
            if not profile_id:
                result['blockers'].append(blocker('module_ksb_profile_missing', 'modules', ['ksb_profile_source_id']))
            elif codes:
                read('ksb_profiles', (sorted(codes), profile_id, profile_id), 2)
                versions = read('ksb_profile_versions', (profile_id,), 2)
                if versions is not None and len(versions) == 1:
                    read('ksb_definitions', (versions[0]['id'], sorted(codes)), 5000)
                elif versions is not None:
                    result['blockers'].append(blocker('ksb_profile_version_unresolved', 'ksb_profile_versions'))
            # Report required definition access even when mapping access blocked.
            for table in ('ksb_profiles', 'ksb_profile_versions', 'ksb_definitions'):
                if result[table] is None:
                    for error in schema_blockers(table, metadata):
                        if error not in result['blockers']:
                            result['blockers'].append(error)
    except Exception as error:
        code = 'select_denied' if getattr(error, 'sqlstate', None) == '42501' else 'read_failed'
        result['blockers'].append(blocker(code, current_table))
    finally:
        for cleanup in (conn.rollback, conn.close):
            try:
                cleanup()
            except Exception:
                result['blockers'].append(blocker('connection_cleanup_failed', 'connection'))
    return result
