"""Narrow rejection handling and non-personal writer diagnostics for lecture feedback."""
import hashlib
import json
import logging
import os
from pathlib import Path
import socket


CONSTRAINT_NAME = 'feedback_lecture_creation_guard'
logger = logging.getLogger(__name__)
# Capture at import, not on each write: an already-running process must not
# identify itself as new code merely because its source files were replaced.
_CODE_VERSION = hashlib.sha256(
    Path(__file__).read_bytes().replace(b'\r\n', b'\n')
    + Path(__file__).with_name('feedback_delivery.py').read_bytes().replace(b'\r\n', b'\n')
).hexdigest()[:20]


def writer_context():
    # PID is evaluated after fork (e.g. a preloaded Gunicorn application).
    return {'host': socket.gethostname(), 'pid': os.getpid(), 'code': _CODE_VERSION}


def tag_transaction(connection):
    """Transaction-local context; never change a pooled connection's defaults."""
    if getattr(connection, 'vendor', None) != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config('feedback.writer_context', %s, true)",
                       [json.dumps(writer_context(), separators=(',', ':'))])


def is_creation_guard_rejection(error):
    cause = error.__cause__
    return (
        getattr(cause, 'sqlstate', getattr(cause, 'pgcode', None)) == '23514'
        and getattr(getattr(cause, 'diag', None), 'constraint_name', None) == CONSTRAINT_NAME
    )


def rejected_form_id(error):
    """Read only the numeric form ID, never log arbitrary driver error text."""
    try:
        detail = json.loads(error.__cause__.diag.message_detail)
        value = detail.get('form_id')
        return value if type(value) is int else None
    except (AttributeError, TypeError, ValueError):
        return None


def audit(action, *, occurrence_key, form=None, form_id=None, starts_at=None,
          delivery_id=None, reason=None):
    payload = {
        **writer_context(), 'action': action,
        'form_id': getattr(form, 'id', form_id),
        'form_version': getattr(form, 'version', None),
        # The same fingerprint is emitted by the database trigger. Keep arbitrary
        # attendance references, names, emails, answers and error text out of logs.
        'occurrence_fingerprint': hashlib.md5(str(occurrence_key).encode('utf-8'), usedforsecurity=False).hexdigest(),
        'starts_at': starts_at.isoformat() if starts_at is not None else None,
        'form_created_at': getattr(form, 'created_at', None),
        'delivery_id': delivery_id, 'reason': reason,
    }
    level = logging.WARNING if action == 'database_rejected' else logging.INFO
    logger.log(level, 'feedback_delivery_audit %s', json.dumps(payload, default=str, separators=(',', ':')))
