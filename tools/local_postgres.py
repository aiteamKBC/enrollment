"""Provision a private local PostgreSQL cluster for migration rehearsal/tests only."""
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import quote
import psycopg

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'private'
DATA = PRIVATE / 'postgres-data'
BIN = PRIVATE / 'pgsql/bin'
PORT = 55439
CONFIG = PRIVATE / 'local-postgres.json'
if CONFIG.exists():
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    password = config['password']
else:
    if DATA.exists():
        raise SystemExit('An unrecognised cluster directory exists; refusing to initialise it.')
    password = secrets.token_urlsafe(32)
    CONFIG.write_text(json.dumps({'password': password}), encoding='utf-8')
    password_file = PRIVATE / 'local-postgres-password'
    password_file.write_text(password, encoding='utf-8')
    result = subprocess.run([str(BIN / 'initdb.exe'), '-D', str(DATA), '-U', 'enrollment_test_owner',
                             '-A', 'scram-sha-256', '--pwfile', str(password_file), '--encoding=UTF8', '--locale=C'],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    (PRIVATE / 'initdb.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit('Local initdb failed; see private/initdb.log.')
result = subprocess.run([str(BIN / 'pg_ctl.exe'), '-D', str(DATA), 'status'], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
if result.returncode:
    with (PRIVATE / 'postgres-start-error.log').open('wb') as output:
        result = subprocess.run([str(BIN / 'pg_ctl.exe'), '-D', str(DATA), '-l', str(PRIVATE / 'postgres-server.log'),
                                 '-o', f'-h 127.0.0.1 -p {PORT}', '-w', '-t', '30', 'start'],
                                stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise SystemExit('Local PostgreSQL did not start; see private diagnostics.')
base = f'postgresql://enrollment_test_owner:{quote(password)}@127.0.0.1:{PORT}'
with psycopg.connect(base + '/postgres', autocommit=True) as conn:
    with conn.cursor() as cursor:
        cursor.execute('SHOW data_directory')
        if Path(cursor.fetchone()[0]).resolve() != DATA.resolve():
            raise SystemExit('A different PostgreSQL server is listening on the test port.')
        for name in ('enrollment_rehearsal', 'enrollment_regression'):
            cursor.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,))
            if not cursor.fetchone():
                cursor.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
            (PRIVATE / (name + '.json')).write_text(json.dumps({
                'url': base + '/' + name, 'verified_local_directory': str(DATA.resolve()),
            }), encoding='utf-8')
print('Local PostgreSQL verified by server data_directory; isolated rehearsal and regression databases are available.')
