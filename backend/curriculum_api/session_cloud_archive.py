"""Copy a verified Teams recording from the organizer's OneDrive into private Azure.

OneDrive is an optional fast path. A missing drive, permission, or unique match
leaves the caller's Graph byte-range transfer available as the fallback.
"""

from datetime import datetime, timezone
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
import re

import httpx
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings

from .session_graph import collection


EDGE_BYTES = 64 * 1024
MAX_TIME_DIFFERENCE_SECONDS = 5 * 60


def _instant(value):
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value or '').replace('Z', '+00:00'))
        except ValueError:
            return None
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _safe_source_url(value):
    try:
        parsed = urlsplit(str(value or ''))
        return bool(parsed.scheme == 'https' and parsed.hostname and
                    parsed.hostname.endswith('.sharepoint.com') and
                    parsed.username is None and parsed.password is None and
                    parsed.port in (None, 443) and not parsed.fragment)
    except ValueError:
        return False


def _range_bytes(transport, url, start, end, *, token=None):
    headers = {'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if not token:
        # urllib does not emit its URL at INFO level. The preauthenticated
        # SharePoint URL contains a secret and must never enter HTTP logs.
        with urlopen(Request(url, headers=headers), timeout=90) as response:
            if response.status != 206 or not _safe_source_url(response.geturl()):
                raise ValueError('The recording source did not honor a safe byte range.')
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match:
                raise ValueError('The recording source returned an invalid byte range.')
            actual_start, actual_end, total = map(int, match.groups())
            if actual_start != start or actual_end != end or total <= end:
                raise ValueError('The recording source returned an inconsistent byte range.')
            content = response.read(end - start + 2)
            if len(content) != end - start + 1:
                raise ValueError('The recording source returned an incomplete byte range.')
            return total, content
    with transport.stream('GET', url, headers=headers) as response:
        response.raise_for_status()
        if response.status_code != 206:
            raise ValueError('The recording source did not honor a byte range.')
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
        if not match:
            raise ValueError('The recording source returned an invalid byte range.')
        actual_start, actual_end, total = map(int, match.groups())
        if actual_start != start or actual_end != end or total <= end:
            raise ValueError('The recording source returned an inconsistent byte range.')
        content = b''
        for part in response.iter_bytes(EDGE_BYTES):
            content += part
            if len(content) > end - start + 1:
                raise ValueError('The recording source exceeded its byte range.')
        if len(content) != end - start + 1:
            raise ValueError('The recording source returned an incomplete byte range.')
        return total, content


def _edges(transport, url, *, token=None):
    size, first = _range_bytes(transport, url, 0, EDGE_BYTES - 1, token=token)
    if size < EDGE_BYTES:
        raise ValueError('The recording is smaller than the verification range.')
    last_size, last = _range_bytes(transport, url, size - EDGE_BYTES, size - 1, token=token)
    if last_size != size:
        raise ValueError('The recording changed during verification.')
    return size, first, last


def verify_existing_recording(blob, graph_url, token):
    """Reuse an orphaned Azure MP4 only when its size and edge bytes match Teams."""
    with httpx.Client(follow_redirects=True, timeout=90) as transport:
        size, _ = _range_bytes(transport, graph_url, 0, 0, token=token)
        edge = min(EDGE_BYTES, size)
        _, first = _range_bytes(transport, graph_url, 0, edge - 1, token=token)
        if blob.get_blob_properties(logging_enable=False).size != size:
            return False
        if blob.download_blob(offset=0, length=edge, logging_enable=False).readall() != first:
            return False
        if size > edge:
            _, last = _range_bytes(transport, graph_url, size - edge, size - 1, token=token)
            if blob.download_blob(offset=size - edge, length=edge, logging_enable=False).readall() != last:
                return False
        return True


def copy_matched_recording(artifact, organizer, graph_url, token, blob, *, graph_get,
                           graph_base, progress=None, drive_cache=None):
    """Return True only after a unique source and the Azure bytes are verified.

    The caller catches discovery/copy errors and uses its Graph transfer. The
    short-lived OneDrive URL is never persisted or included in an error.
    """
    created = _instant(artifact.get('created_datetime'))
    if not created or not organizer:
        return False
    owner = quote(organizer, safe='')
    if drive_cache is not None and owner in drive_cache:
        items = drive_cache[owner]
    else:
        path = (f"users/{owner}/drive/root:/Recordings:/children?"
                '$select=id,name,size,createdDateTime,file&$top=200')
        items = collection(graph_get, path, graph_base)
        if drive_cache is not None:
            drive_cache[owner] = items
    candidates = []
    for item in items:
        timestamp = _instant(item.get('createdDateTime'))
        if (isinstance(item.get('file'), dict) and
                str(item.get('name') or '').lower().endswith('.mp4') and
                item.get('id') and isinstance(item.get('size'), int) and
                timestamp and abs((timestamp - created).total_seconds()) <= MAX_TIME_DIFFERENCE_SECONDS):
            candidates.append(item)
    if not candidates:
        return False
    with httpx.Client(follow_redirects=True, timeout=90) as transport:
        graph_size, graph_first, graph_last = _edges(transport, graph_url, token=token)
        candidates = [item for item in candidates if item['size'] == graph_size]
        if len(candidates) != 1:
            return False
        candidate = candidates[0]
        item_path = f"users/{owner}/drive/items/{quote(candidate['id'], safe='')}"
        detail = graph_get('GET', item_path)
        if (not isinstance(detail, dict) or detail.get('id') != candidate['id'] or
                detail.get('size') != graph_size):
            return False
        source_url = detail.get('@microsoft.graph.downloadUrl') if isinstance(detail, dict) else None
        if not _safe_source_url(source_url):
            return False
        source_size, source_first, source_last = _edges(transport, source_url)
        if (source_size, source_first, source_last) != (graph_size, graph_first, graph_last):
            return False
        if progress:
            progress.update('uploading', 0, graph_size, force=True)
        if not blob.exists():
            try:
                blob.upload_blob_from_url(source_url, overwrite=False,
                                          content_settings=ContentSettings(content_type='video/mp4'),
                                          connection_timeout=30, read_timeout=600, timeout=600,
                                          retry_total=0, logging_enable=False)
            except ResourceExistsError:
                pass  # another worker may have finished the same immutable blob
        if blob.get_blob_properties().size != graph_size:
            raise ValueError('The archived recording size differs from Microsoft Teams.')
        if (blob.download_blob(offset=0, length=EDGE_BYTES).readall() != graph_first or
                blob.download_blob(offset=graph_size - EDGE_BYTES, length=EDGE_BYTES).readall() != graph_last):
            raise ValueError('The archived recording bytes differ from Microsoft Teams.')
        if progress:
            progress.update('uploading', graph_size, graph_size, force=True)
        return True
