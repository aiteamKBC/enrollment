"""APTEM archive publication guards around the existing storage transport.

Provider work is outside row locks. Each upload owns its path, while the first
committed reference wins. Uncertain/unpublished uploads are deliberately kept:
cleanup cannot prove a failed commit did not publish that path.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from .migrated_session_scope import has_verified_meeting_evidence, refresh_basis, select_artifacts
from .models import CoachCalendarEvent, ImportedReviewInstance
from .review_sources import ReviewIdentityConflict


@dataclass(frozen=True)
class ArchiveAttempt:
    calendar_id: int
    event_key: str
    artifact_id: str
    context: dict
    calendar_basis: dict
    artifact_basis: str
    provenance: dict
    verified_meeting_id: str = ''


def _artifact_row(event_key, artifact_id, database):
    from . import recording_archive as storage
    with storage.connections[database].cursor() as cursor:
        cursor.execute(f"""SELECT owner_email, graph_organizer_email, graph_event_id,
                blob_container, blob_name, blob_size_bytes, blob_content_type
            FROM {storage.ARTIFACTS_RELATION}
            WHERE event_key = %s AND artifact_type = 'recording' AND graph_artifact_id = %s""",
            [event_key, artifact_id])
        row = cursor.fetchone()
    if not row:
        raise ReviewIdentityConflict('Stored recording is unavailable.')
    return dict(zip(('owner_email', 'graph_organizer_email', 'graph_event_id',
                     'blob_container', 'blob_name', 'blob_size_bytes', 'blob_content_type'), row))


def prepare_archive(record, candidate, database):
    """None is verified generic/Native; an uncertain imported scope raises."""
    from .background_meeting_sync import imported_context
    from .views import stored_coach_meeting_snapshot
    context = imported_context(record)
    if context is None:
        return None
    if record.event_key != candidate.get('event_key'):
        raise ReviewIdentityConflict('Recording calendar changed.')
    artifact_id = candidate['graph_artifact_id']
    stored = _artifact_row(record.event_key, artifact_id, database)
    provenance = {}
    for key in ('owner_email', 'graph_organizer_email', 'graph_event_id'):
        values = [str(value or '').strip() for value in
                  (stored[key], candidate.get(key), getattr(record, key, None))]
        if key != 'graph_event_id':
            values = [value.casefold() for value in values]
        if not values[0] or len(set(values)) != 1:
            raise ReviewIdentityConflict('Stored recording target changed.')
        provenance[key] = stored[key]
    snapshot = stored_coach_meeting_snapshot(record, database=database, safe_diagnostics=True)
    if snapshot.get('partial') or not (snapshot.get('storage') or {}).get('stored'):
        raise ReviewIdentityConflict('Stored recording evidence is unavailable.')
    meeting_scoped = has_verified_meeting_evidence(snapshot)
    from .bound_actual_session import bound_session
    overlay = (ImportedReviewInstance.objects.using(database).filter(pk=context['overlayId']).first()
               if context.get('overlayId') else None)
    bound = bound_session(record, overlay=overlay) if not meeting_scoped and overlay and context['family'] in {
        'MCM', 'PR', 'PR_SKILLS_RADAR'} else None
    if meeting_scoped:
        from .migrated_session_scope import scope_snapshot
        scoped = scope_snapshot(record, snapshot, meeting_scoped=True)
        selected, error = scoped['artifacts'], scoped['selectionError']
    else:
        selected, error = select_artifacts(record, snapshot, session=bound.context if bound else None)
    artifacts = [row for row in selected if row.get('artifact_type') == 'recording'
                 and row.get('graph_artifact_id') == artifact_id]
    if (error and not meeting_scoped) or len(artifacts) != 1:
        raise ReviewIdentityConflict('Recording does not resolve to the booked call.')
    artifact = artifacts[0]
    verified_meeting_id = ((artifact.get('metadata') or {}).get('_lms_verified_online_meeting_id')
                           if meeting_scoped else bound.context.online_meeting_id if bound else '') or ''
    basis = {key: artifact.get(key) for key in
             ('graph_artifact_id', 'call_id', 'created_datetime', 'end_datetime', 'metadata')}
    if bound:
        context = {**context, 'boundCallId': bound.context.call_id,
                   'boundSelectionVersion': bound.context.selection_version}
    return ArchiveAttempt(record.pk, record.event_key, artifact_id, deepcopy(context),
        deepcopy(refresh_basis(record)),
        hashlib.sha256(json.dumps(basis, cls=DjangoJSONEncoder, sort_keys=True).encode()).hexdigest(),
        provenance, verified_meeting_id)


def _locked_result(attempt, database, *, result=None, error=''):
    from . import recording_archive as storage
    with transaction.atomic(using=database):
        record = CoachCalendarEvent.objects.using(database).select_for_update().get(pk=attempt.calendar_id)
        if attempt.context.get('overlayId'):
            ImportedReviewInstance.objects.using(database).select_for_update().get(pk=attempt.context['overlayId'])
        candidate = {'event_key': attempt.event_key, 'graph_artifact_id': attempt.artifact_id, **attempt.provenance}
        if prepare_archive(record, candidate, database) != attempt:
            raise ReviewIdentityConflict('Imported recording context changed.')
        stored = _artifact_row(attempt.event_key, attempt.artifact_id, database)
        if stored['blob_name']:
            return {'archived': True, 'blob_name': stored['blob_name'], 'error': ''}
        if result is None and not error:
            return None  # Pre-upload validation only.
        updated = storage._record_upload(database, attempt.event_key, attempt.artifact_id,
            **(result or {}), error=error, only_unarchived=True)
        if updated != 1:
            raise ReviewIdentityConflict('Recording publication was superseded.')
    return {'archived': bool(result), 'blob_name': (result or {}).get('blob_name', ''), 'error': error}


def published_reference(attempt, database):
    """Revalidate a selected candidate and retain a concurrent winner before I/O."""
    return _locked_result(attempt, database)


def archive_imported_recording(attempt, content_bytes, content_type, database):
    from io import BytesIO
    from . import recording_archive as storage
    failed = lambda detail: {'archived': False, 'blob_name': '', 'error': detail}
    try:
        existing = _locked_result(attempt, database)
        if existing:
            return existing
    except Exception:
        return failed('Imported recording context could not be revalidated; archive skipped.')
    container = storage.recordings_container(storage.meeting_type_from_event_key(attempt.event_key))
    stable = storage.build_blob_name(attempt.event_key, attempt.artifact_id, content_type)
    stem, extension = stable.rsplit('.', 1)
    blob_name = f'{stem}-{uuid4().hex}.{extension}'
    try:
        storage.upload_blob(BytesIO(content_bytes), container, blob_name, content_type or 'video/mp4',
            upload_block_bytes=storage.UPLOAD_BLOCK_BYTES, max_concurrency=storage.UPLOAD_MAX_CONCURRENCY)
    except Exception:
        try:
            return _locked_result(attempt, database, error='Imported recording upload failed; retry pending.')
        except Exception:
            return failed('Imported recording upload failed; stored state could not be updated.')
    try:
        return _locked_result(attempt, database, result={
            'container': container, 'blob_name': blob_name, 'size_bytes': len(content_bytes),
            'content_type': content_type or 'video/mp4'})
    except Exception:
        # No delete: rollback/connection failure can leave the commit uncertain.
        # A future authorized orphan review can compare references safely.
        return failed('Recording upload completed but publication could not be confirmed; bytes retained.')
