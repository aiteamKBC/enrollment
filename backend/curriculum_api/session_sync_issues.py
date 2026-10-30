"""Plain categories for the errors a Teams files sync stores.

The worker stores short, safe strings (never a URL, token or response body) in
``session_result_jobs.last_error`` and ``session_result_archive.last_error``.
Rows saved before this module existed use the same strings, so reading them
back here classifies old and new failures alike. Staff-facing wording lives in
the frontend (``SessionSyncIssues.tsx``), keyed by these categories.
"""
import re

CATEGORIES = ('not_found', 'access_denied', 'authentication', 'rate_limited',
              'service_unavailable', 'network', 'storage', 'link_repair', 'unknown')

# httpx/builtin transport failures: the connection dropped or timed out.
NETWORK_ERRORS = {
    'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout', 'TimeoutException',
    'ConnectError', 'ReadError', 'WriteError', 'NetworkError', 'TransportError',
    'RemoteProtocolError', 'LocalProtocolError', 'ProtocolError', 'ProxyError',
    'TimeoutError', 'ConnectionError', 'ConnectionResetError', 'ConnectionAbortedError',
}
# Azure SDK failures: the LMS's own storage, not Microsoft Teams.
STORAGE_ERRORS = {
    'HttpResponseError', 'ServiceRequestError', 'ServiceResponseError', 'ResourceNotFoundError',
    'ResourceExistsError', 'ClientAuthenticationError', 'AzureError',
}
STORAGE_STAGES = {'uploading', 'finalizing'}

_ARCHIVE = re.compile(
    r'Could not archive (?P<type>recording|transcript) (?P<id>\S+) during (?P<stage>\w+) '
    r'\((?P<error>[A-Za-z_][\w.]*)(?:, HTTP (?P<status>\d{3}))?(?P<disk>, temporary disk full)?\)')
# Interim wording used briefly for a recording 404 before categories existed.
_MISSING = re.compile(r'Teams could not find (?P<type>recording|transcript) (?P<id>\S+) \(HTTP (?P<status>\d{3})\)')
_TIMING = re.compile(r'Could not prepare transcript timing for (?P<id>\S+?)\.')
_ATTENDANCE_ARCHIVE = re.compile(r'Could not archive attendance for session (?P<number>\d+)')
_GRAPH = re.compile(r'(?P<label>Attendance reporting|Attendance|Recording|Transcript) sync failed(?: \(HTTP (?P<status>\d{3})\))?')


def category_for(status=None, error_type='', stage='', disk_full=False):
    """One category from the facts a stored error keeps."""
    if disk_full or stage in STORAGE_STAGES or error_type in STORAGE_ERRORS:
        return 'storage'
    if status == 404:
        return 'not_found'
    if status == 403:
        return 'access_denied'
    if status == 401:
        return 'authentication'
    if status == 429:
        return 'rate_limited'
    if status == 408 or error_type in NETWORK_ERRORS:
        return 'network'
    if status is not None and status >= 500:
        return 'service_unavailable'
    return 'unknown'


def _issue(category, *, file_type=None, artifact_id=None, session_number=None, status=None,
           stage=None, error_type=None, detail=''):
    return {'category': category, 'fileType': file_type, 'artifactId': artifact_id,
            'sessionNumber': session_number, 'httpStatus': status, 'stage': stage,
            'errorType': error_type, 'occurredAt': None, 'detail': detail}


def parse_sync_error(text):
    """A stored error string as an issue dict, or None for a blank string."""
    text = str(text or '').strip()
    if not text:
        return None
    if match := _ARCHIVE.search(text):
        status = int(match['status']) if match['status'] else None
        disk = bool(match['disk'])
        return _issue(category_for(status, match['error'], match['stage'], disk),
                      file_type=match['type'], artifact_id=match['id'], status=status,
                      stage=match['stage'], error_type=match['error'], detail=text)
    if match := _MISSING.search(text):
        status = int(match['status'])
        return _issue(category_for(status), file_type=match['type'], artifact_id=match['id'],
                      status=status, stage='downloading', detail=text)
    if match := _TIMING.search(text):
        return _issue('storage', file_type='transcript', artifact_id=match['id'], stage='finalizing', detail=text)
    if match := _ATTENDANCE_ARCHIVE.search(text):
        return _issue('storage', file_type='attendance', session_number=int(match['number']),
                      stage='uploading', detail=text)
    if match := _GRAPH.search(text):
        status = int(match['status']) if match['status'] else None
        file_type = 'attendance' if match['label'].startswith('Attendance') else match['label'].lower()
        return _issue(category_for(status), file_type=file_type, status=status, stage='discovering', detail=text)
    if 'Saved recording is missing or empty in Azure' in text:
        return _issue('storage', file_type='recording', stage='finalizing', detail=text)
    if 'recording link repair failed' in text:
        return _issue('link_repair', stage='linking', detail=text)
    return _issue('unknown', detail=text)


def sync_issues(job, rows):
    """Outstanding problems for one failed job; empty once a later sync succeeds.

    ``rows`` are this series' recording/transcript rows with ``id``,
    ``session_number``, ``artifact_type``, ``status``, ``last_error`` and
    ``updated_at`` (already an ISO string). Failed archive rows are the record of
    which file failed; the job's own error adds the problems no file row holds.
    """
    if job.get('state') != 'failed':
        return []
    by_id = {row.get('id'): row for row in rows if row.get('id')}
    issues, seen = [], set()
    for row in rows:
        if row.get('status') != 'failed':
            continue
        issue = parse_sync_error(row.get('last_error')) or _issue('unknown')
        issue.update(fileType=row.get('artifact_type') or issue['fileType'], artifactId=row['id'],
                     sessionNumber=row.get('session_number'), occurredAt=row.get('updated_at'))
        issues.append(issue)
        seen.add(('artifact', row['id']))
    stored = str(job.get('last_error') or '')
    parts = [part.strip() for part in stored.split('; ') if part.strip()]
    if len(stored) >= 1500 and parts:
        parts.pop()  # the worker cut this one off mid-sentence
    for part in parts:
        issue = parse_sync_error(part)
        artifact = issue['artifactId']
        if artifact:
            row = by_id.get(artifact)
            # The file row is the truth: a file saved since is no longer a problem.
            if ('artifact', artifact) in seen or (row and row.get('status') != 'failed'):
                continue
            if row:
                issue['sessionNumber'] = row.get('session_number')
            key = ('artifact', artifact)
        else:
            key = ('job', part)
        if key in seen:
            continue
        seen.add(key)
        issue['occurredAt'] = issue['occurredAt'] or job.get('finished_at')
        issues.append(issue)
    issues.sort(key=lambda item: (item['sessionNumber'] is None, item['sessionNumber'] or 0))
    return issues
