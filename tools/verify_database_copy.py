"""Independently verify a completed initial import; never copy or modify rows."""
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from copy_database import PRIVATE, ROOT, EXPECTED_HOST

config = json.loads((PRIVATE / 'target-database.json').read_text())
if (urlparse(config['url']).hostname or '').replace('-pooler', '') != EXPECTED_HOST:
    raise SystemExit('Not the selected Enrolment Corner database.')
report = json.loads((ROOT / 'database-copy-plan.json').read_text())
# Recover exactly the original read-only verification statements. No COPY data
# or schema commands are sent to the server by this verification tool.
with (PRIVATE / 'atomic-enrollment-import.sql').open('rb') as stream:
    stream.seek(0, 2)
    stream.seek(max(0, stream.tell() - 128 * 1024))
    tail = stream.read().decode('utf-8')
marker = "\nSET LOCAL TIME ZONE 'UTC';\n"
if marker not in tail:
    raise SystemExit('Original verification statements are missing.')
checks = tail.rsplit(marker, 1)[1].splitlines()
if not checks or any(not line.startswith('DO $verify$ BEGIN IF (SELECT count(*)') or not line.endswith('END $verify$;') for line in checks):
    raise SystemExit('Unexpected import verification format.')
preservation_checks = [line for line in checks if "'Existing target data changed'" in line]
if len(preservation_checks) != 12:
    raise SystemExit('Expected preservation evidence for all 12 existing tables.')
with psycopg.connect(config['url'], connect_timeout=20) as conn:
    conn.read_only = True
    with conn.cursor() as cur:
        cur.execute("SET LOCAL TIME ZONE 'UTC'")
        for line in checks:
            cur.execute(line)
        for row in report['tables']:
            qualified = sql.Identifier(row['schema'], row['table']).as_string(conn)
            cur.execute('SELECT relkind FROM pg_class WHERE oid=to_regclass(%s)', [qualified])
            if cur.fetchone() != (row['kind'],):
                raise RuntimeError('Imported relation kind mismatch.')
report.update(verified=True, existing_tables_verified_unchanged=12,
              verified_at=datetime.now(timezone.utc).isoformat(),
              verification='Independent read-only check after native transaction committed')
(ROOT / 'database-copy-result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print('Verified:', len(report['tables']), 'relations;', sum(row['rows'] or 0 for row in report['tables']),
      'copied rows; all 12 existing target tables unchanged.')
