"""Private WSGI sidecar beside LMS; signed one-time handoffs, no public UI.

Uses only LMS_HANDOFF_DATABASE_URL, LMS_HANDOFF_DATABASE_HOST and
LMS_HANDOFF_SECRET from its own environment. Never imports Django/.env.
"""
import hashlib
import json
import os
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from .handoff_contract import Conflict, DOCUMENTS, PLAN_TABLES, MAX_BODY, authenticated, validate

PROFILE_STUDY_FIELDS = {
    'completed_hours', 'progress_hours', 'progress_variance', 'otjh_status', 'coach_rag',
    'accepted_assignments', 'rejected_assignments', 'accepted_reflections', 'rejected_reflections',
    'gateway_review_date', 'alert_notify_for_epa', 'enter_epa',
}


def import_payload(conn, payload, digest):
    user = validate(payload)
    rows, learner_uuid, uid = payload['rows'], user['uuid'], user['id']
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', [learner_uuid])
        if payload.get('existingStaff'):
            staff = payload['existingStaff']
            cur.execute('''SELECT a.id FROM login."Login_accounts" a JOIN enrolment."Staff_users" s
                ON a."Subject_type"='staff' AND a."Subject_id"=s.id
                WHERE s.uuid=%s AND lower(a."Email")=lower(%s) AND lower(s."Email")=lower(%s)
                  AND a."Role" IN ('staff','admin') AND a."Is_active"=true''', [staff['uuid'],staff['email'],staff['email']])
            if cur.fetchone() is None:
                raise Conflict('The linked staff login must already exist in LMS; staff grants are never transferred.')
        cur.execute('SELECT enrolment_id FROM enrollment_integration.handoff_receipts WHERE learner_uuid=%s', [learner_uuid])
        receipt = cur.fetchone()
        if receipt:
            if receipt[0] != uid:
                raise Conflict('A learner identity was already mapped to another ID.')
            return 'already-accepted'
        cur.execute('SELECT row_to_json(t) FROM enrolment."Created_users" t WHERE id=%s OR uuid=%s', [uid, learner_uuid])
        current = [row[0] for row in cur.fetchall()]
        if current and (len(current) != 1 or current[0]['id'] != uid or current[0]['uuid'] != learner_uuid):
            raise Conflict('The LMS learner ID is already in use.')
        if current and str(current[0].get('Programme_status', '')).strip().lower() == 'active':
            # An existing study identity stays owned by LMS; never replay enrolment over progress.
            if str(current[0].get('Email', '')).lower() != str(user.get('Email', '')).lower():
                raise Conflict('The existing study identity differs.')
        else:
            def upsert(table, row, owner, preserve_existing=False):
                schema, name = table.split('.')
                identifier = sql.Identifier(schema, name)
                qualified = identifier.as_string(conn)
                cur.execute("""SELECT a.attname,a.attgenerated FROM pg_attribute a
                    WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", [qualified])
                columns = {column: generated for column, generated in cur.fetchall()}
                if not columns or set(row) - columns.keys():
                    raise Conflict('LMS schema differs from the enrolment contract.')
                cur.execute("""SELECT a.attname FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
                    JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                    WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.ord""", [qualified])
                keys = [item[0] for item in cur.fetchall()]
                if not keys or any(key not in row for key in keys):
                    raise Conflict('A primary identity is missing.')
                condition = sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key in keys)
                cur.execute(sql.SQL('SELECT row_to_json(t) FROM {} t WHERE {} FOR UPDATE').format(identifier, condition), [row[key] for key in keys])
                old = cur.fetchone()
                if old:
                    if any(old[0].get(key) != row.get(key) for key in owner):
                        raise Conflict('A dependent LMS record belongs to another identity.')
                    if preserve_existing:
                        return
                writable = [key for key in columns if key in row and not columns[key]]
                if old and table == 'Learner.learners':
                    writable = [key for key in writable if key not in PROFILE_STUDY_FIELDS]
                names = sql.SQL(',').join(sql.Identifier(key) for key in writable)
                updates = [key for key in writable if key not in keys]
                conflict = sql.SQL(' DO UPDATE SET ') + sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(key), sql.Identifier(key)) for key in updates)
                cur.execute(sql.SQL('INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE SELECT {} FROM jsonb_populate_record(NULL::{},%s) ON CONFLICT ({})').format(
                    identifier, names, names, identifier, sql.SQL(',').join(sql.Identifier(key) for key in keys)) + conflict, [Jsonb(row)])
                # Explicit legacy identities must not leave the destination sequence behind.
                if 'id' in keys:
                    cur.execute('SELECT pg_get_serial_sequence(%s,%s)', [qualified, 'id'])
                    sequence = cur.fetchone()[0]
                    if sequence:
                        cur.execute(sql.SQL('SELECT last_value FROM {}').format(sql.SQL(sequence)))
                        last = cur.fetchone()[0]
                        cur.execute(sql.SQL('SELECT max(id) FROM {}').format(identifier))
                        maximum = cur.fetchone()[0]
                        cur.execute('SELECT setval(%s::regclass,%s,true)', [sequence, max(last, maximum)])

            for employer in rows.get('enrolment.Employers', []):
                upsert('enrolment.Employers', employer, ['uuid'], preserve_existing=True)
            initial_user = {**user, 'learner_id': None}
            upsert('enrolment.Created_users', initial_user, ['uuid'])
            for profile in rows.get('Learner.learners', []):
                upsert('Learner.learners', profile, ['uuid', 'enrolment_id'])
            cur.execute('UPDATE enrolment."Created_users" SET learner_id=%s WHERE id=%s', [user.get('learner_id'), uid])
            for table, owner in ((PLAN_TABLES[0], 'learner_id'), (PLAN_TABLES[1], 'plan_module_id'), (PLAN_TABLES[2], 'plan_week_id'),
                                 ('learner_free_courses', 'learner_id'), ('learner_ksb_assignments', 'learner_id')):
                for row in rows.get('Learner.' + table, []):
                    upsert('Learner.' + table, row, [owner])
            for table in DOCUMENTS:
                for row in rows.get('enrolment.' + table, []):
                    upsert('enrolment.' + table, row, ['Learner_id'])
            for event in rows.get('Learner.coach_calendar_event', []):
                upsert('Learner.coach_calendar_event', event, ['learner_id', 'event_key'])
            account = rows['login.Login_accounts'][0].copy() if not payload.get('existingStaff') else None
            # Login IDs are local to each service; subject identity stays stable.
            if account:
                cur.execute('SELECT id,"Subject_type","Subject_id" FROM login."Login_accounts" WHERE lower("Email")=lower(%s)', [account['Email']])
                existing_account = cur.fetchone()
                if existing_account and existing_account[1:] != ('learner', uid):
                    raise Conflict('An LMS login belongs to another identity.')
                if existing_account:
                    account['id'] = existing_account[0]
                else:
                    cur.execute('SELECT nextval(pg_get_serial_sequence(%s,%s))', ['login."Login_accounts"', 'id'])
                    account['id'] = cur.fetchone()[0]
                upsert('login.Login_accounts', account, ['Subject_type', 'Subject_id', 'Email'])
        cur.execute('INSERT INTO enrollment_integration.handoff_receipts(learner_uuid,enrolment_id,payload_hash) VALUES(%s,%s,%s)', [learner_uuid, uid, digest])
    return 'accepted'


def application(environ, start_response):
    def respond(status, message):
        body = json.dumps({'status': message}).encode()
        start_response(status, [('Content-Type', 'application/json'), ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
        return [body]
    if environ.get('PATH_INFO') != '/handoff' or environ.get('REQUEST_METHOD') != 'POST':
        return respond('404 Not Found', 'not-found')
    try:
        length = int(environ.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return respond('400 Bad Request', 'invalid-length')
    if not 0 < length <= MAX_BODY:
        return respond('413 Content Too Large', 'invalid-size')
    body = environ['wsgi.input'].read(length)
    if not authenticated(os.environ.get('LMS_HANDOFF_SECRET', ''), environ.get('HTTP_X_ENROLLMENT_TIME'), environ.get('HTTP_X_ENROLLMENT_SIGNATURE'), body):
        return respond('401 Unauthorized', 'unauthorized')
    dsn = os.environ.get('LMS_HANDOFF_DATABASE_URL', '')
    expected = os.environ.get('LMS_HANDOFF_DATABASE_HOST', '')
    if not expected or urlparse(dsn).hostname != expected:
        return respond('503 Service Unavailable', 'database-not-configured')
    try:
        payload = json.loads(body)
        validate(payload)
        with psycopg.connect(dsn, connect_timeout=10) as conn:
            status = import_payload(conn, payload, hashlib.sha256(body).hexdigest())
        return respond('200 OK', status)
    except (Conflict, psycopg.IntegrityError):
        return respond('409 Conflict', 'identity-or-dependency-conflict')
    except (ValueError, TypeError, KeyError):
        return respond('400 Bad Request', 'invalid-payload')
    except psycopg.Error:
        return respond('503 Service Unavailable', 'database-unavailable')
