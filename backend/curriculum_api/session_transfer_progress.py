"""Bounded transfer telemetry; imports do not connect to Django or storage."""
import json
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)
PROGRESS_INTERVAL_SECONDS = 5


def byte_count(value):
    try:
        return max(0, int(value)) if not isinstance(value, bool) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def save_transfer_progress(series_id, artifact_id, lease_id, progress, *, close_connection=False):
    from django.db import connections

    connection = connections['default']
    try:
        with connection.cursor() as cursor:
            # A replaced worker cannot overwrite the new attempt's telemetry.
            # Update only our JSON key, preserving visibility and transcript cues.
            cursor.execute('''UPDATE curriculum.live_session_artifacts a
                SET metadata=jsonb_set(coalesce(a.metadata::jsonb,'{}'::jsonb),
                    '{lmsArchiveProgress}',%s::jsonb)
                FROM curriculum.live_session_occurrences o, curriculum.session_result_jobs j
                WHERE a.id=%s AND a.occurrence_id=o.id AND o.live_session_id=%s
                  AND j.live_session_id=o.live_session_id AND j.state='running' AND j.lease_id=%s''',
                [json.dumps({**progress, 'leaseId': lease_id}), artifact_id, series_id, lease_id])
            # A long transfer remains owned by this worker. Do not change
            # started_at: requests queued during a transfer are compared to it.
            cursor.execute('''UPDATE curriculum.session_result_jobs
                SET next_attempt_at=now()
                WHERE live_session_id=%s AND state='running' AND lease_id=%s''',
                [series_id, lease_id])
    finally:
        # Azure invokes progress hooks in its upload threads. Do not retain a
        # database connection in those short-lived threads.
        if close_connection:
            connection.close()


class TransferProgress:
    def __init__(self, series_id, artifact_id, lease_id):
        self.series_id, self.artifact_id, self.lease_id = series_id, artifact_id, lease_id
        self.owner = threading.current_thread()
        self.lock = threading.Lock()
        self.phase = None
        self.transferred = 0
        self.last_saved = None

    def update(self, phase, transferred=0, total=None, *, force=False):
        if not self.lease_id:
            return
        with self.lock:
            changed = self.phase != phase
            self.transferred = byte_count(transferred) if changed else max(self.transferred, byte_count(transferred))
            self.phase = phase
            now = time.monotonic()
            if not (force or changed or self.last_saved is None or now - self.last_saved >= PROGRESS_INTERVAL_SECONDS):
                return
            self.last_saved = now
            total = byte_count(total) or None
            # Decoded HTTP bytes may exceed an encoded Content-Length. Never
            # display a bogus percentage if the advertised size is inconsistent.
            if total is not None and self.transferred > total:
                total = None
            try:
                save_transfer_progress(self.series_id, self.artifact_id, self.lease_id, {
                    'phase': phase, 'bytesTransferred': self.transferred, 'totalBytes': total,
                    'updatedAt': datetime.now(timezone.utc).isoformat(),
                }, close_connection=threading.current_thread() is not self.owner)
            except Exception as error:
                # Telemetry failure must not abort a file upload. Surface stale
                # telemetry in the UI; leave a safe diagnostic for operators.
                log.warning('Could not save session transfer progress (%s).', type(error).__name__)

    def uploaded(self, current, total):
        self.update('uploading', current, total)


def summarize_transfers(rows):
    """Public, file-only counters. No Graph metadata, paths, tokens or lease IDs."""
    current = None
    for row in rows:
        value = row.get('transfer')
        if not isinstance(value, dict) or row.get('status') == 'ready':
            continue
        if value.get('phase') not in {'preparing', 'downloading', 'uploading', 'finalizing'}:
            continue
        stamp = value.get('updatedAt')
        try:
            parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        count, total = byte_count(value.get('bytesTransferred')), byte_count(value.get('totalBytes'))
        if current is None or parsed > current[0]:
            current = (parsed, {'phase': value['phase'], 'bytesTransferred': count,
                'totalBytes': total if total and count <= total else None, 'updatedAt': parsed.isoformat(),
                'type': row['artifact_type'], 'sessionNumber': row['session_number']})
    return {'filesReady': sum(row.get('status') == 'ready' for row in rows),
            'totalFiles': len(rows), 'transfer': current[1] if current else None}
