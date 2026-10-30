"""Explicitly migrated storage; no DDL or implicit schema repair on reads."""
import json
from contextlib import contextmanager

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction

REQUESTS = '"Learner".progress_transfer_requests'
ITEMS = '"Learner".progress_transfer_items'


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def dumps(value):
    return json.dumps(value, cls=DjangoJSONEncoder)


@contextmanager
def lock_learner(enrolment_id):
    # Shared by confirmation and legacy attempt writes: a started request cannot
    # race a completion through after the snapshot is captured.
    with transaction.atomic(using='enrolment'):
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('SELECT pg_advisory_xact_lock(%s,%s)', [73142, int(enrolment_id)])
            yield cursor


def read_state(enrolment_id, *, include_records=False):
    with connections['enrolment'].cursor() as cursor:
        cursor.execute(f'''SELECT r.id,r.status,r.requested_at,r.approved_at,i.snapshot
            FROM {REQUESTS} r JOIN {ITEMS} i ON i.request_id=r.id AND i.enrolment_id=r.enrolment_id
            WHERE r.enrolment_id=%s ORDER BY r.requested_at,i.course_ref''', [enrolment_id])
        items = [dict(decode(row[4]), requestId=str(row[0]), status=row[1],
                      requestedAt=row[2].isoformat(), approvedAt=row[3].isoformat() if row[3] else None)
                 for row in cursor.fetchall()]
        records = []
        if include_records and items:
            cursor.execute(f'SELECT records FROM {REQUESTS} WHERE enrolment_id=%s ORDER BY requested_at', [enrolment_id])
            records = [record for row in cursor.fetchall() for record in decode(row[0])]
    return {'items': items, 'records': records}


def assert_legacy_writable(cursor, enrolment_id, course_id):
    from old_otjh.service import ServiceError
    cursor.execute(f'SELECT 1 FROM {ITEMS} WHERE enrolment_id=%s AND course_ref=%s',
                   [enrolment_id, f'legacy:{course_id}'])
    if cursor.fetchone():
        raise ServiceError('Progress on this old LMS module stopped when you confirmed Transfer Progress.',
                           'progress_transfer_locked', 409)
