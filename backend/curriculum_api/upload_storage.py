"""Where curriculum component uploads live.

Slide decks, reading PDFs, podcast audio and assignment briefs used to be written
to MEDIA_ROOT on whichever machine served the upload. The database only ever held
the path, so a file uploaded on one host was a 404 on every other host sharing
that database — which is exactly what happened to an authored deck whose row was
in the database while its bytes were on nobody's disk.

So the bytes belong in the kbcdocs storage account, in AZURE_CURRICULUM_CONTAINER,
and the database keeps holding a path. The stored path does not change: an upload
is still recorded as ``/curriculum_api/curriculum/uploads/<relative>``, which the
serving view resolves through this module. That keeps every settings_json
reference already in the database valid, whether its bytes are on local disk
(uploaded before this change, or a render cache) or in Azure.

Local disk stays the fallback in both directions: reads check it first, and when
Azure is not configured — a developer machine with no credentials — uploads go
there exactly as before.
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
import tempfile
from threading import Lock
from pathlib import Path

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.files.storage import default_storage

from learner_api import evidence_storage

logger = logging.getLogger(__name__)

# The current storage connection times out on one multi-megabyte Put Blob
# request. Curriculum authoring files are capped at 300 MB, so 256 KB blocks keep
# each socket write short while still allowing two blocks to upload together —
# and a large file is streamed as many short blocks rather than one long PUT.
CURRICULUM_UPLOAD_BLOCK_BYTES = 256 * 1024
CURRICULUM_UPLOAD_ATTEMPTS = 3
CURRICULUM_AZURE_TIMEOUT_SECONDS = 30

_ready_containers = set()
_ready_containers_lock = Lock()

#: Prefix of the URL an upload is recorded under, unchanged by where bytes live.
UPLOAD_URL_PREFIX = '/curriculum_api/curriculum/uploads/'


def azure_enabled() -> bool:
    """True when uploads should go to Azure rather than to local disk."""
    return bool(evidence_storage.azure_configured() and container_name())


def container_name() -> str:
    return getattr(settings, 'AZURE_CURRICULUM_CONTAINER', '') or ''


def ensure_container() -> None:
    """Create the container if it is not there yet. Safe to call repeatedly."""
    if not azure_enabled():
        return
    key = (settings.AZURE_STORAGE_ACCOUNT, container_name())
    if key in _ready_containers:
        return
    with _ready_containers_lock:
        if key in _ready_containers:
            return
        client = evidence_storage._service_client(retry_total=1).get_container_client(container_name())
        request_options = {
            'connection_timeout': CURRICULUM_AZURE_TIMEOUT_SECONDS,
            'read_timeout': CURRICULUM_AZURE_TIMEOUT_SECONDS,
        }
        if not client.exists(**request_options):
            try:
                client.create_container(**request_options)
                logger.info('Created Azure container %s for curriculum uploads', container_name())
            except Exception:
                # A second process may have created it between exists() and
                # create_container(). Confirm that before treating it as a
                # storage failure.
                if not client.exists(**request_options):
                    raise
        _ready_containers.add(key)


def blob_name_for(relative_path) -> str:
    """The blob a storage-relative upload path maps to.

    ``COMPONENT_UPLOAD_ROOT`` is dropped: it exists to keep uploads apart from
    other media inside MEDIA_ROOT, and the container already does that.
    """
    from curriculum_api.views import COMPONENT_UPLOAD_ROOT
    text = str(relative_path or '').strip('/')
    prefix = f'{COMPONENT_UPLOAD_ROOT}/'
    return text[len(prefix):] if text.startswith(prefix) else text


def local_path(relative_path) -> Path | None:
    """The on-disk path for an upload, or None when it escapes MEDIA_ROOT.

    A traversal path resolves to None rather than raising: callers use this to
    decide where to read from, so a bad path has to be a miss, not a 500.
    """
    try:
        resolved = Path(default_storage.path(relative_path)).resolve()
    except (NotImplementedError, ValueError, SuspiciousFileOperation):
        return None
    media_root = Path(settings.MEDIA_ROOT).resolve()
    return resolved if str(resolved).startswith(str(media_root)) else None


def store(file_obj, relative_path, content_type='') -> str:
    """Save an upload and return the storage-relative path it was saved as.

    With Azure configured the bytes go to the container and nothing is written to
    local disk; otherwise this is ``default_storage.save``, so a machine without
    credentials behaves exactly as it did before.
    """
    if not azure_enabled():
        return default_storage.save(relative_path, file_obj)
    last_error = None
    for attempt in range(1, CURRICULUM_UPLOAD_ATTEMPTS + 1):
        try:
            ensure_container()
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)
            evidence_storage.upload_blob(
                file_obj,
                container_name(),
                blob_name_for(relative_path),
                content_type,
                # The name is unique to this request. Overwrite makes a retry
                # safe when Azure stored the bytes but its response was lost.
                overwrite=True,
                upload_block_bytes=CURRICULUM_UPLOAD_BLOCK_BYTES,
                max_concurrency=1,
                retry_total=1,
            )
            return relative_path
        except Exception as error:  # noqa: BLE001 - retried, then reported
            last_error = error
            logger.warning(
                'Curriculum upload attempt %s/%s failed for %s: %s',
                attempt, CURRICULUM_UPLOAD_ATTEMPTS, relative_path, error,
            )
    raise last_error


def exists(relative_path) -> bool:
    path = local_path(relative_path)
    if path and path.is_file():
        return True
    if not azure_enabled():
        return False
    return evidence_storage.blob_exists(container_name(), blob_name_for(relative_path))


STREAM_CHUNK_BYTES = 262144
VIDEO_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def _bounded_file_stream(path, offset, length):
    """Yield at most `length` bytes of `path`, starting at `offset`."""
    with path.open('rb') as handle:
        handle.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk


def open_stream(relative_path, offset=0, length=None):
    """``(stream, total_size, content_type)`` for an upload, or None if it is gone.

    `offset`/`length` serve one byte range, which is what an <audio> element
    needs to seek and what a PDF viewer uses to jump to a page; `total_size` is
    always the size of the whole file, not of the slice. Local disk is checked
    first so a render cache — or anything uploaded before the move to Azure — is
    still served without a round trip.
    """
    path = local_path(relative_path)
    if path and path.is_file():
        total = path.stat().st_size
        span = total - offset if length is None else length
        return _bounded_file_stream(path, offset, max(span, 0)), total, ''
    if not azure_enabled():
        return None
    media_type = mimetypes.guess_type(str(relative_path))[0] or ''
    # download_blob() reads its initial range before returning a downloader.
    # The default 32 MiB delays video headers and metadata even with chunks().
    options = {'download_chunk_bytes': VIDEO_DOWNLOAD_CHUNK_BYTES} if media_type.startswith('video/') else {}
    client = evidence_storage._service_client(**options).get_blob_client(
        container_name(), blob_name_for(relative_path),
    )
    try:
        properties = client.get_blob_properties()
    except Exception:
        return None
    downloader = client.download_blob(offset=offset or 0, length=length)
    return downloader.chunks(), properties.size, (properties.content_settings.content_type or '')


def content_stamp(relative_path):
    """A string that changes when an upload's bytes change, or None if it is gone.

    The slide renderer keys its cache on this. A temporary copy downloaded from
    Azure has a fresh mtime every time, so stamping the copy would defeat the
    cache and re-parse the deck on every request; the blob's own etag and size
    identify the bytes regardless of where they are read from.
    """
    path = local_path(relative_path)
    if path and path.is_file():
        stat = path.stat()
        return f'local:{stat.st_size}:{stat.st_mtime_ns}'
    if not azure_enabled():
        return None
    client = evidence_storage._service_client().get_blob_client(
        container_name(), blob_name_for(relative_path),
    )
    try:
        properties = client.get_blob_properties()
    except Exception:
        return None
    return f'azure:{properties.size}:{properties.etag}'


def local_copy(relative_path):
    """A real file on this machine for code that can only read from disk.

    The slide renderer parses OOXML with python-pptx, which needs a filesystem
    path. Returns ``(path, cleanup)``; ``cleanup()`` removes the copy only when
    one had to be made, so an already-local file is never deleted.
    """
    path = local_path(relative_path)
    if path and path.is_file():
        return path, lambda: None
    if not azure_enabled():
        return None, lambda: None
    client = evidence_storage._service_client().get_blob_client(
        container_name(), blob_name_for(relative_path),
    )
    suffix = Path(str(relative_path)).suffix
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with handle:
            client.download_blob().readinto(handle)
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        return None, lambda: None
    temporary = Path(handle.name)
    return temporary, lambda: temporary.unlink(missing_ok=True)


def delete(relative_path) -> None:
    """Remove an upload from wherever it is."""
    path = local_path(relative_path)
    if path and path.is_file():
        path.unlink(missing_ok=True)
    if azure_enabled():
        try:
            evidence_storage.delete_blob(container_name(), blob_name_for(relative_path))
        except Exception:
            logger.warning('Could not delete blob for %s', relative_path)


def upload_url(relative_path) -> str:
    """The URL an upload is recorded under, whatever holds its bytes."""
    return UPLOAD_URL_PREFIX + blob_name_for(relative_path)


def signed_read_url(relative_path) -> str:
    """A short-lived direct Azure URL for a stored upload, or ``''``.

    Most callers should keep using :func:`upload_url`, which streams the bytes
    through the LMS and never exposes storage credentials.  Office Online is
    the exception: its servers must fetch a DOCX/XLSX/PPTX themselves, so the
    iframe preview page hands Microsoft a read-only SAS URL that expires using
    the normal evidence-storage TTL.
    """
    if not azure_enabled():
        return ''
    blob_name = blob_name_for(relative_path)
    if not evidence_storage.blob_exists(container_name(), blob_name):
        return ''
    return evidence_storage.get_read_sas(container_name(), blob_name)


#: Bulk uploads are big and the link is shared with other transfers, so they get
#: a longer socket timeout than the SDK's default and are retried.
BULK_UPLOAD_TIMEOUT_SECONDS = 600
BULK_UPLOAD_ATTEMPTS = 3


def copy_local_to_azure(relative_path, content_type='', overwrite=False) -> int:
    """Push one local file to Azure and return the size Azure reports.

    Used by the migration command. The size comes back from Azure rather than
    from the local file so the caller can only delete a local copy once the
    remote one is verifiably complete.

    Uploads are chunked, concurrent and retried: a single-shot upload of a
    several-megabyte deck times out when the connection is busy, and a timeout
    that reaches the caller means a file needlessly keeps its only copy on one
    machine's disk.
    """
    from azure.storage.blob import ContentSettings

    path = local_path(relative_path)
    if not path or not path.is_file():
        raise FileNotFoundError(relative_path)
    ensure_container()
    blob = blob_name_for(relative_path)
    client = evidence_storage._service_client().get_blob_client(container_name(), blob)
    if not overwrite and client.exists():
        return client.get_blob_properties().size

    last_error = None
    for attempt in range(1, BULK_UPLOAD_ATTEMPTS + 1):
        try:
            with path.open('rb') as handle:
                client.upload_blob(
                    handle,
                    overwrite=True,
                    content_settings=ContentSettings(
                        content_type=content_type or 'application/octet-stream',
                    ),
                    max_concurrency=4,
                    connection_timeout=BULK_UPLOAD_TIMEOUT_SECONDS,
                )
            return client.get_blob_properties().size
        except Exception as error:  # noqa: BLE001 - retried, then reported
            last_error = error
            logger.warning('Upload attempt %s/%s failed for %s: %s',
                           attempt, BULK_UPLOAD_ATTEMPTS, blob, error)
    raise last_error


def free_disk_bytes() -> int:
    return shutil.disk_usage(Path(settings.MEDIA_ROOT).anchor or '/').free
