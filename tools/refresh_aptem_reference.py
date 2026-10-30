"""Refresh only linked Aptem IDs/statuses; no study histories or source writes."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
import psycopg
from copy_database import source_url, EXPECTED_HOST, ROOT, PRIVATE

parser=argparse.ArgumentParser()
parser.add_argument('--target',type=Path,default=PRIVATE/'target-database.json')
parser.add_argument('--schema-only',action='store_true')
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
target=json.loads(args.target.read_text())
host=(urlparse(target['url']).hostname or '').replace('-pooler','')
if host!=EXPECTED_HOST and not target.get('verified_local_directory'):
    raise SystemExit('Wrong reference-cache target.')
records=[]
if not args.schema_only:
    with psycopg.connect(source_url(),connect_timeout=20) as source:
        source.read_only=True
        with source.cursor() as cur:
            cur.execute('''SELECT DISTINCT a."ID",a."Program-Status" FROM "LMS"."Aptem_users" a WHERE EXISTS (
                SELECT 1 FROM enrolment."Created_users" u WHERE a."ID"::numeric = CASE
                WHEN btrim(u.aptem_id::text) ~ '^[0-9]{1,19}$' THEN btrim(u.aptem_id::text)::numeric ELSE NULL END)''')
            records=cur.fetchall()
with psycopg.connect(target['url'],connect_timeout=20) as conn:
    conn.read_only=not args.apply
    with conn.cursor() as cur:
        if target.get('verified_local_directory'):
            cur.execute('SHOW data_directory')
            if Path(cur.fetchone()[0]).resolve()!=Path(target['verified_local_directory']).resolve():
                raise SystemExit('Wrong local test server.')
            if not args.schema_only:
                raise SystemExit('Do not copy real reference rows into the synthetic test database.')
        if args.apply:
            cur.execute((ROOT/'database/reference-schema.sql').read_text(encoding='utf-8'))
            if records:
                cur.executemany('''INSERT INTO enrollment_reference.aptem_status("ID","Program-Status") VALUES(%s,%s)
                    ON CONFLICT("ID") DO UPDATE SET "Program-Status"=excluded."Program-Status",refreshed_at=now()''',records)
                cur.execute('SELECT count(*) FROM enrollment_reference.aptem_status WHERE "ID"=ANY(%s)',[[row[0] for row in records]])
                if cur.fetchone()[0]!=len(records):raise RuntimeError('Reference verification failed.')
print('Reference schema/refresh', 'applied' if args.apply else 'planned', '; scoped rows:',len(records))
