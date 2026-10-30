"""Plan/apply the exported schema to a verified empty service database."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import psycopg

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
values=dict(os.environ)
for line in (ROOT/'backend/.env').read_text(encoding='utf-8-sig').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key,value=line.split('=',1)
        values.setdefault(key.strip(),value.strip().strip('"').strip("'"))
dsn=values.get('DATABASE_URL','')
if not values.get('ENROLLMENT_DATABASE_HOST') or urlparse(dsn).hostname!=values['ENROLLMENT_DATABASE_HOST']:
    raise SystemExit('Database host does not match the explicit enrollment target.')
relations=json.loads((ROOT/'database/relations.json').read_text())
with psycopg.connect(dsn,connect_timeout=20) as conn:
    with conn.cursor() as cur:
        for item in relations:
            name=psycopg.sql.Identifier(item['schema'],item['table']).as_string(conn)
            cur.execute('SELECT to_regclass(%s)',[name])
            if cur.fetchone()[0] is not None:
                raise SystemExit('Service schema already exists. Bootstrap refused; use a reviewed incremental migration.')
        if args.apply:
            cur.execute((ROOT/'database/schema.sql').read_text(encoding='utf-8'),prepare=False)
            cur.execute((ROOT/'database/reference-schema.sql').read_text(encoding='utf-8'),prepare=False)
            print('Standalone schema created in the configured enrollment database.')
        else:
            print('No service-table conflicts found. Re-run with --apply to create the schema.')
