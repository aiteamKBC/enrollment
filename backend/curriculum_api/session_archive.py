"""Background-only import of Teams files into a private Azure container."""
import json
import logging
import os
import re
import tempfile
from time import sleep
from urllib.parse import quote

import httpx
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ServiceRequestError, ServiceResponseError
from django.db import connections

from .session_results import read, result_rows
from .session_results_policy import archive_prefix, attendance_csv, transcript_text
from .session_media_policy import transcript_timing_ready
from .session_transcripts import parse_vtt_cues
from .session_transfer_progress import TransferProgress, byte_count
from .session_cloud_archive import copy_matched_recording, verify_existing_recording

log = logging.getLogger(__name__)
CONTAINER = os.environ.get('AZURE_SESSION_RECORDINGS_CONTAINER', 'session-recordings')
DOWNLOAD_RANGE_BYTES = 16 * 1024 * 1024


def storage_client():
    from learner_api.evidence_storage import _service_client
    return _service_client()


def save_transcript_timing(artifact_id, raw):
    timeline = {'version': 1, 'cues': parse_vtt_cues(raw.decode('utf-8-sig', errors='replace'))}
    with connections['default'].cursor() as cursor:
        cursor.execute('''UPDATE curriculum.live_session_artifacts
            SET metadata=jsonb_set(coalesce(metadata::jsonb,'{}'::jsonb),'{lmsTranscriptTimeline}',%s::jsonb)
            WHERE id=%s AND artifact_type='transcript' ''', [json.dumps(timeline, ensure_ascii=False), artifact_id])


def provision_container():
    """Explicit operator setup only; never invoked by a read or sync request."""
    try:
        storage_client().create_container(CONTAINER)  # private: no public_access
    except ResourceExistsError:
        pass


def save_archive(artifact, name, *, text=None, state='ready', error=''):
    with connections['default'].cursor() as cursor:
        cursor.execute('''INSERT INTO curriculum.session_result_archive
            (artifact_id,occurrence_id,container,blob_name,status,transcript_text,last_error)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(artifact_id) DO UPDATE SET occurrence_id=EXCLUDED.occurrence_id,
              container=EXCLUDED.container,blob_name=EXCLUDED.blob_name,status=EXCLUDED.status,
              transcript_text=coalesce(EXCLUDED.transcript_text,session_result_archive.transcript_text),
              last_error=EXCLUDED.last_error,updated_at=now()''',
            [artifact['id'], artifact['occurrence_id'], CONTAINER, name, state, text, error])


def saved_copy_of_same_recording(artifact):
    """The ready Azure copy of this same Teams file saved under another LMS row, if any.

    Graph has listed one recording under two ids; each row then fetched and
    uploaded its own byte-identical copy. A row that is the same file (same
    session and type, and the same content correlation id or the same call and
    recording window) points at the copy already saved instead.
    """
    correlation = (artifact.get('content_correlation_id') or '').strip()
    call = (artifact.get('call_id') or '').strip()
    if not correlation and not (call and artifact.get('created_datetime')):
        return None
    rows = read('''SELECT r.blob_name, r.transcript_text FROM curriculum.live_session_artifacts a
        JOIN curriculum.session_result_archive r ON r.artifact_id=a.id
        WHERE a.occurrence_id=%s AND a.artifact_type=%s AND a.id<>%s AND r.status='ready' AND r.container=%s
          AND ((%s<>'' AND a.content_correlation_id=%s AND (a.call_id=%s OR a.call_id='' OR %s=''))
               OR (%s<>'' AND a.call_id=%s AND a.created_datetime=%s AND a.end_datetime IS NOT DISTINCT FROM %s))
        ORDER BY r.updated_at LIMIT 1''',
        [artifact['occurrence_id'], artifact['artifact_type'], artifact['id'], CONTAINER,
         correlation, correlation, call, call, call, call, artifact.get('created_datetime'), artifact.get('end_datetime')])
    return rows[0] if rows else None


def archive_series(series_id, *, lease_id=None, artifact_ids=None):
    from coach_api.views import get_graph_settings, microsoft_graph_request, microsoft_graph_token
    from learner_api.evidence_storage import upload_blob
    from .views import teams_online_meeting_owner_id
    series = read('SELECT * FROM curriculum.live_sessions WHERE id=%s', [series_id])[0]
    modules = read('SELECT group_id,group_name FROM curriculum.modules WHERE module_catalogue_id=%s', [series['module_catalogue_id']])
    occurrences = read('SELECT * FROM curriculum.live_session_occurrences WHERE live_session_id=%s', [series_id])
    by_id = {row['id']: row for row in occurrences}
    artifacts = read('''SELECT a.*,r.status AS saved_status,r.blob_name AS saved_name,
        r.occurrence_id AS saved_occurrence_id FROM curriculum.live_session_artifacts a
        JOIN curriculum.live_session_occurrences o ON o.id=a.occurrence_id
        LEFT JOIN curriculum.session_result_archive r ON r.artifact_id=a.id WHERE o.live_session_id=%s''', [series_id])
    if artifact_ids is not None:
        selected = set(artifact_ids)
        artifacts = [artifact for artifact in artifacts if artifact['id'] in selected]
    errors = []
    client = storage_client()
    token = None
    drive_cache = {}
    for artifact in artifacts:
        if artifact['saved_status'] == 'ready':
            # Backfill timing for previously archived transcripts from Azure once.
            # Ready videos are never downloaded or uploaded again.
            if artifact['artifact_type'] == 'transcript' and not transcript_timing_ready(artifact):
                try:
                    raw = client.get_blob_client(CONTAINER, artifact['saved_name']).download_blob(offset=0, length=16 * 1024 * 1024 + 1).readall()
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('Transcript exceeds the supported size.')
                    save_transcript_timing(artifact['id'], raw)
                except Exception as failure:
                    log.warning('Transcript timing %s failed: %s', artifact['id'], type(failure).__name__)
                    errors.append(f"Could not prepare transcript timing for {artifact['id']}. Check worker logs.")
            continue
        occurrence = by_id[artifact['occurrence_id']]
        kind = artifact['artifact_type']
        if kind not in {'recording', 'transcript'}:
            continue
        twin = saved_copy_of_same_recording(artifact)
        if twin:
            # Already in Azure under another row: no download, no second upload.
            save_archive(artifact, twin['blob_name'], text=twin.get('transcript_text') if kind == 'transcript' else None)
            continue
        prefix = archive_prefix(series, occurrence, modules[0] if modules else {})
        saved_name = artifact['saved_name'] if artifact.get('saved_occurrence_id') == artifact['occurrence_id'] else None
        name = saved_name or f"{prefix}/{kind}-{artifact['id']}.{'mp4' if kind == 'recording' else 'vtt'}"
        save_archive(artifact, name, state='pending')
        progress = TransferProgress(series_id, artifact['id'], lease_id)
        progress.update('preparing')
        try:
            blob = client.get_blob_client(CONTAINER, name)
            text = None
            blob_present = blob.exists()
            base = path = None
            if blob_present and kind == 'recording':
                meeting_id = occurrence.get('online_meeting_id') or series['online_meeting_id']
                owner = teams_online_meeting_owner_id(series['organizer_email'], occurrence.get('join_url') or series['join_url'])
                if not meeting_id or not owner:
                    raise ValueError('The stored meeting identity is incomplete.')
                base = get_graph_settings()['base_url'].rstrip('/')
                path = f"users/{quote(owner, safe='')}/onlineMeetings/{quote(meeting_id, safe='')}/recordings/{quote(artifact['graph_artifact_id'], safe='')}/content"
                token = token or microsoft_graph_token()
                graph_url = f'{base}/{path}'
                if not verify_existing_recording(blob, graph_url, token):
                    # Preserve an old partial blob and use a new immutable path.
                    # A worker crash cannot turn an incomplete copy into ready.
                    original_name = name.rsplit('.', 1)[0]
                    for repair_number in range(1, 6):
                        candidate_name = f'{original_name}-repair-{repair_number}.mp4'
                        candidate = client.get_blob_client(CONTAINER, candidate_name)
                        candidate_present = candidate.exists()
                        if candidate_present and not verify_existing_recording(candidate, graph_url, token):
                            continue
                        name, blob, blob_present = candidate_name, candidate, candidate_present
                        save_archive(artifact, name, state='pending')
                        break
                    else:
                        raise ValueError('No unused archive repair path is available.')
            if not blob_present:
                meeting_id = occurrence.get('online_meeting_id') or series['online_meeting_id']
                owner = teams_online_meeting_owner_id(series['organizer_email'], occurrence.get('join_url') or series['join_url'])
                if not meeting_id or not owner:
                    raise ValueError('The stored meeting identity is incomplete.')
                base = base or get_graph_settings()['base_url'].rstrip('/')
                path = path or f"users/{quote(owner, safe='')}/onlineMeetings/{quote(meeting_id, safe='')}/{kind}s/{quote(artifact['graph_artifact_id'], safe='')}/content"
                token = token or microsoft_graph_token()
                headers = {'Authorization': f'Bearer {token}'}
                if kind == 'transcript':
                    headers['Accept'] = 'text/vtt'
                copied = False
                if kind == 'recording':
                    try:
                        copied = copy_matched_recording(
                            artifact, series['organizer_email'], f'{base}/{path}', token, blob,
                            graph_get=microsoft_graph_request, graph_base=base,
                            progress=progress, drive_cache=drive_cache)
                    except Exception as cloud_failure:
                        # An Azure blob left by a failed copy must be checked,
                        # never silently treated as a completed archive.
                        if blob.exists():
                            raise
                        log.warning('OneDrive copy for %s unavailable (%s); using Graph ranges.',
                                    artifact['id'], type(cloud_failure).__name__)
                if copied:
                    progress.update('finalizing')
                    save_archive(artifact, name)
                    continue
                # Disk-backed, bounded memory. No video buffers or transfer in a web request.
                with tempfile.TemporaryFile(dir=os.environ.get('SESSION_RECORDINGS_TEMP_DIR') or None) as stream, httpx.Client(follow_redirects=True, timeout=120) as transport:
                    downloaded = 0
                    total = None
                    while True:
                        offset = downloaded
                        request_headers = dict(headers)
                        if kind == 'recording':
                            # Large full-content responses can time out in Graph.
                            # Each range is independently retried without losing
                            # previously downloaded bytes.
                            request_headers['Range'] = f'bytes={offset}-{offset + DOWNLOAD_RANGE_BYTES - 1}'
                            request_headers['Accept-Encoding'] = 'identity'
                        for attempt in range(3):
                            stream.seek(offset)
                            stream.truncate()
                            if attempt:
                                progress = TransferProgress(series_id, artifact['id'], lease_id)
                            progress.update('downloading', offset, total, force=True)
                            try:
                                with transport.stream('GET', f'{base}/{path}', headers=request_headers) as response:
                                    response.raise_for_status()
                                    ranged = kind == 'recording' and response.status_code == 206
                                    expected = None
                                    if ranged:
                                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                                        if not match:
                                            raise ValueError('Microsoft returned an invalid recording range.')
                                        start, end, size = map(int, match.groups())
                                        if (start != offset or end < start or end >= size
                                                or end - start + 1 > DOWNLOAD_RANGE_BYTES
                                                or (total is not None and total != size)):
                                            raise ValueError('Microsoft returned an inconsistent recording range.')
                                        total = size
                                        expected = end - start + 1
                                    elif offset or response.status_code == 206:
                                        raise ValueError('Microsoft did not return the requested recording range.')
                                    else:
                                        total = None if response.headers.get('Content-Encoding') not in (None, '', 'identity') else byte_count(response.headers.get('Content-Length')) or None
                                    received = 0
                                    progress.update('downloading', offset, total, force=True)
                                    for chunk in response.iter_bytes(1024 * 1024):
                                        stream.write(chunk)
                                        received += len(chunk)
                                        if expected is not None and received > expected:
                                            raise ValueError('Microsoft returned more recording bytes than requested.')
                                        progress.update('downloading', offset + received, total)
                                if expected is not None and received != expected:
                                    raise httpx.ReadError('Microsoft returned an incomplete recording range.')
                                downloaded = offset + received
                                progress.update('downloading', downloaded, total, force=True)
                                break
                            except httpx.HTTPError as failure:
                                status = getattr(getattr(failure, 'response', None), 'status_code', None)
                                transient = isinstance(failure, httpx.RequestError) or status in (408, 429) or (status is not None and status >= 500)
                                if not transient or attempt == 2:
                                    raise
                                sleep(2 ** attempt)
                        if not ranged or downloaded >= total:
                            break
                    if kind == 'recording' and not downloaded:
                        raise ValueError('Microsoft returned an empty recording.')
                    stream.seek(0)
                    if kind == 'transcript':
                        raw = stream.read(16 * 1024 * 1024 + 1)
                        if len(raw) > 16 * 1024 * 1024:
                            raise ValueError('Transcript exceeds the supported size.')
                        text = transcript_text(raw.decode('utf-8-sig', errors='replace'))
                        stream.seek(0)
                    for attempt in range(3):
                        stream.seek(0)
                        if attempt:
                            # A transient Azure response failure should not fetch
                            # the whole recording from Graph again.
                            progress = TransferProgress(series_id, artifact['id'], lease_id)
                        progress.update('uploading', 0, downloaded)
                        try:
                            upload_blob(stream, CONTAINER, name, 'video/mp4' if kind == 'recording' else 'text/vtt', overwrite=False,
                                        upload_block_bytes=1024 * 1024, max_concurrency=8,
                                        retry_total=2, read_timeout=45,
                                        progress_hook=progress.uploaded)
                            break
                        except ResourceExistsError:
                            break  # a completed upload can outlive its response
                        except (ServiceRequestError, ServiceResponseError, HttpResponseError) as failure:
                            status = getattr(failure, 'status_code', None)
                            transient = isinstance(failure, (ServiceRequestError, ServiceResponseError)) or status in (408, 429) or (status is not None and status >= 500)
                            if not transient or attempt == 2:
                                raise
                            sleep(2 ** attempt)
                    if kind == 'recording' and blob.get_blob_properties(logging_enable=False).size != downloaded:
                        raise ValueError('The archived recording size differs from the Teams download.')
            progress.update('finalizing')
            if kind == 'transcript':
                if text is None:
                    raw = blob.download_blob(offset=0, length=16 * 1024 * 1024 + 1).readall()
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('Transcript exceeds the supported size.')
                    text = transcript_text(raw.decode('utf-8-sig', errors='replace'))
                client.get_blob_client(CONTAINER, name.rsplit('.', 1)[0] + '.txt').upload_blob(text.encode('utf-8'), overwrite=True)
                save_transcript_timing(artifact['id'], raw)
            save_archive(artifact, name, text=text)
        except Exception as failure:
            log.warning('Session artifact %s archive failed: %s', artifact['id'], type(failure).__name__)
            # Never persist a token-bearing URL or response body in user-visible errors.
            phase = progress.phase if isinstance(progress.phase, str) else 'preparing'
            status = getattr(failure, 'status_code', None) or getattr(getattr(failure, 'response', None), 'status_code', None)
            status_label = f', HTTP {status}' if isinstance(status, int) and 400 <= status < 600 else ''
            detail = ', temporary disk full' if isinstance(failure, OSError) and getattr(failure, 'errno', None) == 28 else ''
            # Diagnostic facts only, in one fixed shape: session_sync_issues
            # reads it back into a plain category (404, 403, storage, ...)
            # and the staff screen words it, so old and new rows read alike.
            error = f"Could not archive {kind} {artifact['id']} during {phase} ({type(failure).__name__}{status_label}{detail}). Check worker logs and access."
            save_archive(artifact, name, state='failed', error=error)
            errors.append(error)
    if artifact_ids is not None:
        return errors
    for session in result_rows(series):
        if not session['reportReady']:
            continue
        occurrence = by_id[session['id']]
        prefix = archive_prefix(series, occurrence, modules[0] if modules else {})
        try:
            client.get_blob_client(CONTAINER, prefix + '/attendance.csv').upload_blob(attendance_csv(session['attendance']).encode('utf-8'), overwrite=True)
            raw = read('SELECT raw_data FROM curriculum.live_session_attendance WHERE occurrence_id=%s', [session['id']])
            client.get_blob_client(CONTAINER, prefix + '/teams-attendance-original.json').upload_blob(json.dumps(raw, default=str).encode('utf-8'), overwrite=True)
        except Exception:
            errors.append(f"Could not archive attendance for session {session['sessionNumber']}.")
    return errors
