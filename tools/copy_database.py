"""Copy enrolment and its declared dependencies; never delete/replace target objects.

The source transaction is read-only and repeatable-read. --plan is the default.
An apply refuses overlapping relations, takes a private target backup, imports
inside one transaction, and checks row counts before commit. No learner values
or connection strings are logged. Existing Enrolment Corner tables are untouched.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import traceback
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'private'
EXPECTED_HOST = 'ep-delicate-lab-aqejxa89.c-8.us-east-1.aws.neon.tech'
ONBOARDING_EVENTS = ('first-session', 'eligibility-review', 'workspace', 'training-plan', 'uln-privacy')
SUPPORT = {
    'login': ['Login_accounts', 'Invitations', 'Login_audit', 'Login_sessions', 'Password_resets', 'coach_directory'],
    'Learner': ['learners', 'coach_calendar_event', 'learner_training_plan_modules',
                'learner_training_plan_weeks', 'learner_training_plan_components',
                'learner_ksbs', 'learner_ksb_assignments', 'learner_free_courses',
                'calendar_busy_slots'],
    'Coach': ['coach_calendar_event', 'coach_calendar_sequence', 'coach_calendar_color_preference',
              'coach_imported_meeting_ownership', 'coach_imported_review_instance', 'coach_migrated_review_template'],
    'curriculum': ['programmes', 'cohorts', 'groups', 'coaches', 'tutors', 'modules', 'module_details',
                   'weeks', 'components', 'ksb_definitions', 'ksb_mappings', 'ksb_profile_versions',
                   'ksb_profiles', 'standard_ksbs', 'free_courses', 'free_course_weeks',
                   'free_programme_modules', 'free_programme_components', 'holidays', 'england_holidays',
                   'activity_events', 'record_versions', 'record_revisions'],
}
SCHEMA_ONLY = {
    ('login', 'Login_sessions'), ('login', 'Password_resets'),
    ('Coach', 'coach_imported_meeting_ownership'), ('Coach', 'coach_imported_review_instance'),
    ('Coach', 'coach_migrated_review_template'), ('curriculum', 'activity_events'),
    ('curriculum', 'record_versions'), ('curriculum', 'record_revisions'),
}


def source_url():
    values = {}
    for line in (ROOT.parent / 'backend/.env').read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return next(values[key] for key in ('ENROLMENT_DATABASE_URL', 'Database_url', 'DATABASEURL', 'DATABASE_URL') if values.get(key))


def pg_env(dsn):
    parsed = urlparse(dsn)
    return {**os.environ, 'PGHOST': parsed.hostname.replace('-pooler', ''), 'PGPORT': str(parsed.port or 5432),
            'PGDATABASE': parsed.path.lstrip('/'), 'PGUSER': unquote(parsed.username or ''),
            'PGPASSWORD': unquote(parsed.password or ''), 'PGSSLMODE': 'require' if parsed.hostname not in ('127.0.0.1', 'localhost') else 'disable',
            'PGCONNECT_TIMEOUT': '20', 'PGAPPNAME': 'enrollment-scoped-copy'}


def relations(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT n.nspname,c.relname,c.relkind,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S')")
        return {(s, t): {'kind': k, 'oid': oid} for s, t, k, oid in cur.fetchall()}


def build_plan(source, target, structure_only=False):
    available = relations(source)
    selected = {key for key in available if key[0] == 'enrolment' and available[key]['kind'] in ('r', 'p', 'v')}
    selected |= {(schema, table) for schema, tables in SUPPORT.items() for table in tables if (schema, table) in available}
    oid_keys = {info['oid']: key for key, info in available.items()}
    with source.cursor() as cur:
        cur.execute("SELECT conrelid,confrelid FROM pg_constraint WHERE contype='f'")
        dependencies = cur.fetchall()
        cur.execute("SELECT r.ev_class,d.refobjid FROM pg_rewrite r JOIN pg_depend d ON d.objid=r.oid AND d.classid='pg_rewrite'::regclass WHERE d.refclassid='pg_class'::regclass AND r.ev_class<>d.refobjid")
        dependencies += cur.fetchall()
    while True:
        before = set(selected)
        ids = {available[key]['oid'] for key in selected}
        for owner, reference in dependencies:
            if owner in ids and reference in oid_keys:
                selected.add(oid_keys[reference])
        if before == selected:
            break
    conflicts = selected & relations(target).keys()
    if conflicts:
        raise RuntimeError('Target already contains source relations; nothing will be overwritten: ' + ', '.join('.'.join(k) for k in sorted(conflicts)))
    # Any dependency beyond the explicitly approved catalogue is schema-only.
    declared = {key for key in selected if key[0] == 'enrolment'} | {(s,t) for s,tables in SUPPORT.items() for t in tables}
    schema_only = set(selected) if structure_only else SCHEMA_ONLY | (selected - declared)
    rows = []
    with source.cursor() as cur:
        for key in sorted(selected):
            info = available[key]
            count = None
            where, params = data_filter(key, schema_only)
            if info['kind'] in ('r', 'p'):
                cur.execute(sql.SQL('SELECT count(*) FROM {} {}').format(sql.Identifier(*key), where), params)
                count = cur.fetchone()[0]
            rows.append({'schema': key[0], 'table': key[1], 'kind': info['kind'], 'rows': count,
                         'schema_only': key in schema_only})
    return rows, available, schema_only


def data_filter(key, schema_only):
    if key in schema_only:
        return sql.SQL('WHERE false'), ()
    if key == ('Learner', 'coach_calendar_event'):
        return sql.SQL('WHERE event_type = ANY(%s)'), (list(ONBOARDING_EVENTS),)
    if key == ('Learner', 'calendar_busy_slots'):
        # Availability is retrieved from Graph. Do not copy OAuth tokens/busy histories.
        return sql.SQL('WHERE false'), ()
    return sql.SQL(''), ()


def run_pg(name, args, dsn, outfile=None):
    exe = PRIVATE / 'pgsql/bin' / (name + ('.exe' if os.name == 'nt' else ''))
    if not exe.exists():
        raise RuntimeError('PostgreSQL tools are required; run download_postgres.py or configure the native tools first.')
    with (outfile.open('wb') if outfile else open(os.devnull, 'wb')) as out:
        result = subprocess.run([str(exe), '--no-password', *args], env=pg_env(dsn), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE, timeout=1200)
    if result.returncode:
        # Native tool errors may name data values: retain them only in the private folder.
        (PRIVATE / (name + '-error.log')).write_bytes(result.stderr)
        raise RuntimeError(name + ' failed; inspect the private diagnostic file (not for Git).')


def apply(source, target, source_dsn, target_dsn, plan, available, schema_only):
    existing = {key: info for key, info in relations(target).items() if info['kind'] in ('r', 'p')}
    def fingerprints():
        result = {}
        with target.cursor() as cursor:
            cursor.execute("SET LOCAL TIME ZONE 'UTC'")
            for key in sorted(existing):
                cursor.execute(sql.SQL("SELECT count(*),md5(coalesce(string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),'')) FROM {} t").format(sql.Identifier(*key)))
                result['.'.join(key)] = cursor.fetchone()
        return result
    before = fingerprints()
    (PRIVATE / 'target-preservation-baseline.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    for item in plan:
        if item['schema'] not in {'enrolment','login','Learner','Coach','curriculum'}:
            raise RuntimeError('A dependency outside the agreed schemas requires review: ' + item['schema'])
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    target_name = urlparse(target_dsn).path.lstrip('/')
    run_pg('pg_dump', ['--format=custom', '--no-owner', '--no-acl', '--lock-wait-timeout=15s'], target_dsn, PRIVATE / f'{target_name}-before-import-{stamp}.dump')
    with source.cursor() as cur:
        cur.execute('SELECT pg_export_snapshot()')
        snapshot = cur.fetchone()[0]
    args = ['--schema-only', '--format=custom', '--no-owner', '--no-acl', '--lock-wait-timeout=15s', '--snapshot=' + snapshot]
    for item in plan:
        args += ['--table=' + sql.Identifier(item['schema'], item['table']).as_string(source)]
    archive = PRIVATE / 'enrollment-schema.dump'
    run_pg('pg_dump', args, source_dsn, archive)
    for section in ('pre-data', 'post-data'):
        run_pg('pg_restore', ['--file=-', '--no-owner', '--no-acl', '--section=' + section, str(archive)], target_dsn, PRIVATE / (section + '.sql'))
    oids = [available[(item['schema'], item['table'])]['oid'] for item in plan]
    with source.cursor() as cur:
        cur.execute("""WITH RECURSIVE required(oid) AS (
            SELECT tgfoid FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = ANY(%s::oid[])
            UNION
            SELECT d.refobjid FROM pg_depend d JOIN (
                SELECT 'pg_constraint'::regclass AS kind,oid FROM pg_constraint WHERE conrelid=ANY(%s::oid[])
                UNION ALL SELECT 'pg_attrdef'::regclass,oid FROM pg_attrdef WHERE adrelid=ANY(%s::oid[])
            ) owners ON d.classid=owners.kind AND d.objid=owners.oid WHERE d.refclassid='pg_proc'::regclass
        ), expanded(oid) AS (
            SELECT oid FROM required UNION
            SELECT d.refobjid FROM pg_depend d JOIN expanded e ON d.objid=e.oid
            WHERE d.classid='pg_proc'::regclass AND d.refclassid='pg_proc'::regclass
        ) SELECT DISTINCT p.oid,pg_get_functiondef(p.oid) FROM expanded e JOIN pg_proc p ON p.oid=e.oid
          JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema')""", (oids, oids, oids))
        functions = cur.fetchall()
    def native_sql(section):
        # psql's restriction meta-command is client-side, not SQL.
        return '\n'.join(line for line in (PRIVATE / (section + '.sql')).read_text(encoding='utf-8').splitlines() if not line.startswith('\\'))
    pre, post = native_sql('pre-data'), native_sql('post-data')
    # This source trigger propagates study-review lifecycle state. That table
    # remains in LMS, so its trigger must not run in the enrolment database.
    post = '\n'.join(line for line in post.splitlines() if not line.startswith('CREATE TRIGGER trg_sync_review_lifecycle_status '))
    # Keep a reproducible schema bootstrap, without any learner rows or secrets.
    schema_sql = '\n'.join('CREATE SCHEMA IF NOT EXISTS ' + sql.Identifier(s).as_string(target) + ';' for s in sorted({item['schema'] for item in plan}))
    schema_sql += '\nSET LOCAL check_function_bodies = false;\n'
    schema_sql += '\n'.join(definition.rstrip(';') + ';' for _, definition in functions) + '\n' + pre + '\n' + post
    (ROOT / 'database').mkdir(exist_ok=True)
    (ROOT / 'database/schema.sql').write_text(schema_sql, encoding='utf-8')
    (ROOT / 'database/relations.json').write_text(json.dumps([{'schema': row['schema'], 'table': row['table'], 'kind': row['kind']} for row in plan],indent=2)+'\n',encoding='utf-8')
    filtered = {('Learner','coach_calendar_event'), ('Learner','calendar_busy_slots')}
    native_tables = [row for row in plan if row['kind'] in ('r','p') and (row['schema'],row['table']) not in schema_only | filtered]
    data_file = PRIVATE / 'scoped-source-data.sql'
    if native_tables:
        args = ['--data-only','--no-owner','--no-acl','--snapshot='+snapshot,'--lock-wait-timeout=15s']
        args += ['--table='+sql.Identifier(row['schema'],row['table']).as_string(source) for row in native_tables]
        print('Exporting scoped data with native PostgreSQL tools.', flush=True)
        run_pg('pg_dump', args, source_dsn, data_file)
    else:
        data_file.write_bytes(b'')
    restore_file = PRIVATE / 'atomic-enrollment-import.sql'
    with restore_file.open('wb') as output:
        prefix = schema_sql[:schema_sql.index(pre)] + pre
        output.write(("SET LOCAL statement_timeout = '20min';\nSET LOCAL idle_in_transaction_session_timeout = '20min';\n" + prefix + '\n').encode())
        # pg_dump's COPY payload stays byte-for-byte intact, including \\. terminators.
        with data_file.open('rb') as stream:
            shutil.copyfileobj(stream, output)
        for key in sorted(filtered - schema_only):
            if key not in available:
                continue
            with source.cursor() as cur:
                cur.execute("SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum>0 AND NOT attisdropped AND attgenerated='' ORDER BY attnum", [available[key]['oid']])
                columns = sql.SQL(',').join(sql.Identifier(row[0]) for row in cur.fetchall())
                where, params = data_filter(key,schema_only)
                output.write((sql.SQL('COPY {} ({}) FROM stdin;\n').format(sql.Identifier(*key),columns).as_string(source)).encode())
                with cur.copy(sql.SQL('COPY (SELECT {} FROM {} {}) TO STDOUT').format(columns,sql.Identifier(*key),where),params) as reader:
                    for block in reader:
                        output.write(block)
                output.write(b'\\.\n')
                qualified = sql.Identifier(*key).as_string(source)
                cur.execute("SELECT attname,pg_get_serial_sequence(%s,attname) FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped", [qualified,qualified])
                for column, sequence in cur.fetchall():
                    if sequence:
                        cur.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.SQL(sequence)))
                        last, called = cur.fetchone()
                        output.write((sql.SQL('SELECT pg_catalog.setval({}, {}, {});\n').format(sql.Literal(sequence),sql.Literal(last),sql.Literal(called)).as_string(source)).encode())
        output.write(('\n'+post+'\nSET LOCAL TIME ZONE \'UTC\';\n').encode())
        for row in plan:
            if row['kind'] in ('r','p'):
                check = sql.SQL("DO $verify$ BEGIN IF (SELECT count(*) FROM {}) <> {} THEN RAISE EXCEPTION 'Imported row count mismatch'; END IF; END $verify$;\n").format(sql.Identifier(row['schema'],row['table']),sql.Literal(row['rows']))
                output.write(check.as_string(target).encode())
        for key in sorted(existing):
            count, digest = before['.'.join(key)]
            check = sql.SQL("DO $verify$ BEGIN IF (SELECT count(*) <> {} OR md5(coalesce(string_agg(md5(row_to_json(t)::text),'' ORDER BY md5(row_to_json(t)::text)),'')) <> {} FROM {} t) THEN RAISE EXCEPTION 'Existing target data changed'; END IF; END $verify$;\n").format(sql.Literal(count),sql.Literal(digest),sql.Identifier(*key))
            output.write(check.as_string(target).encode())
    # The native client's one transaction includes all constraints and verification.
    # ON_ERROR_STOP prevents a failed statement from ever reaching COMMIT.
    print('Applying native import with constraints and preservation checks in one transaction.', flush=True)
    # The native restore owns its transaction; release the planning sessions
    # before a potentially long upload so their idle timeouts cannot mask success.
    source.commit()
    target.commit()
    run_pg('psql', ['--no-psqlrc','--single-transaction','--set=ON_ERROR_STOP=1','--file='+str(restore_file)], target_dsn, PRIVATE / 'native-import.log')
    return len(existing)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--schema-only', action='store_true', help='Rehearse DDL without copying learner data.')
    parser.add_argument('--target', type=Path, default=PRIVATE / 'target-database.json')
    args = parser.parse_args()
    def direct(dsn):
        host = urlparse(dsn).hostname or ''
        return dsn.replace(host, host.replace('-pooler', ''), 1)
    source_dsn = direct(source_url())
    target_config = json.loads(args.target.read_text(encoding='utf-8'))
    target_dsn = direct(target_config['url'])
    target_host = (urlparse(target_dsn).hostname or '').replace('-pooler','')
    if target_host != EXPECTED_HOST and target_config.get('verified_local_directory') is None:
        raise SystemExit('Target identity is not the selected Enrolment Corner project.')
    if target_host == (urlparse(source_dsn).hostname or '').replace('-pooler',''):
        raise SystemExit('Source and target must be different databases.')
    with psycopg.connect(source_dsn, connect_timeout=20) as source, psycopg.connect(target_dsn, connect_timeout=20) as target:
        source.read_only = True
        source.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        target.read_only = not args.apply
        # Bulk COPY must not inherit the request-serving statement timeout.
        source.execute("SET LOCAL statement_timeout = '20min'")
        target.execute("SET LOCAL statement_timeout = '20min'")
        source.execute("SET LOCAL idle_in_transaction_session_timeout = '20min'")
        target.execute("SET LOCAL idle_in_transaction_session_timeout = '20min'")
        if target_config.get('verified_local_directory'):
            with target.cursor() as cur:
                cur.execute('SHOW data_directory')
                if Path(cur.fetchone()[0]).resolve() != Path(target_config['verified_local_directory']).resolve():
                    raise SystemExit('Local test PostgreSQL directory verification failed.')
        plan, available, schema_only = build_plan(source,target,args.schema_only)
        report = {'source_read_only': True, 'target_project': target_config.get('project_id', 'verified-local'),
                  'target_database': urlparse(target_dsn).path.lstrip('/'), 'tables': plan}
        report_name = 'schema-rehearsal' if args.schema_only else 'database-copy'
        (ROOT / (report_name + '-plan.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print('Conflict check passed:', len(plan), 'relations;', sum(row['rows'] or 0 for row in plan), 'rows scoped for copy.', flush=True)
        if args.apply:
            preserved = apply(source,target,source_dsn,target_dsn,plan,available,schema_only)
            target.commit()
            report['verified'] = True
            report['existing_tables_verified_unchanged'] = preserved
            (ROOT / (report_name + '-result.json')).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            print('Copy committed; imported row counts and constraints verified. Existing target relations preserved.')
        else:
            print('Plan only. No schema or data was changed.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (PRIVATE / 'copy-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise SystemExit('Copy did not finish reporting. Verify the target before retrying; native import may already have committed. Inspect private/copy-error.log locally; it may contain sensitive diagnostics.')
