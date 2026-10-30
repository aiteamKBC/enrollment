"""Keep Teams meeting recordings in Azure Blob instead of only in Graph.

Graph is not a storage guarantee. It goes down, its token requests fail on a
DNS blip, an application access policy can be dropped, and Microsoft's own
retention eventually deletes the recording outright -- any one of those takes
every past recording with it, because Graph is the only copy. A coaching
session that a learner or an auditor may need a year from now cannot live
somewhere this fragile.

So each recording is downloaded once and archived to a private container. The
row in Coach.coach_meeting_artifacts then carries both references: the Graph
artifact id it came from, and the blob it now lives in. Reads prefer the blob
and fall back to Graph while a recording has not been archived yet.

Nothing here is on the request path for booking or joining a meeting. Archiving
is slow (recordings run to hundreds of megabytes) and is driven by the
archive_meeting_recordings management command, not by a user waiting on a page.

Config: AZURE_STORAGE_ACCOUNT / AZURE_STORAGE_KEY / AZURE_SAS_TTL_MINUTES and
AZURE_MEETING_RECORDINGS_CONTAINER, all read from backend/.env via settings.
"""
import hashlib
import logging
import re
from io import BytesIO

from django.conf import settings
from django.db import connections, router

from coach_api.models import CoachCalendarEvent

from learner_api.evidence_storage import (
    azure_configured,
    blob_exists,
    delete_blob,
    download_blob_bytes,
    get_read_sas,
    upload_blob,
)

logger = logging.getLogger(__name__)

ARTIFACTS_RELATION = '"Coach".coach_meeting_artifacts'

# Curriculum live sessions keep their artifacts in their own table, keyed by
# occurrence rather than by calendar event key. Everything else about archiving
# is identical, so the helpers below take the relation and key column as
# arguments instead of being duplicated per table.
LIVE_SESSION_ARTIFACTS_RELATION = '"curriculum"."live_session_artifacts"'
LIVE_SESSION_KEY_COLUMN = "occurrence_id"
COACH_KEY_COLUMN = "event_key"

# Recordings are large; stream them in blocks rather than buffering a whole
# file into one request body.
UPLOAD_BLOCK_BYTES = 8 * 1024 * 1024
UPLOAD_MAX_CONCURRENCY = 4

# A recording Graph reports but refuses to hand over should not be retried
# forever on every pass; the error is recorded and the row skipped.
MAX_UPLOAD_ATTEMPTS = 3

_UNSAFE_BLOB_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def recordings_container(meeting_type: str = "") -> str:
    """The container one meeting type's recordings belong in.

    Separate containers let retention, access review and deletion be set per
    kind of meeting in Azure -- a wellbeing support call should not share a
    governance policy with a taught class. Unknown types fall back to the
    general container rather than failing the archive.
    """
    mapping = getattr(settings, "AZURE_RECORDING_CONTAINERS_BY_TYPE", {}) or {}
    key = str(meeting_type or "").strip().lower()
    return mapping.get(key) or settings.AZURE_MEETING_RECORDINGS_CONTAINER


def meeting_type_from_event_key(event_key: str) -> str:
    """Event keys are '<type>:<learner>:<sequence>:<date>'."""
    return str(event_key or "").split(":", 1)[0].strip().lower()


def archive_configured() -> bool:
    """True when blob archiving can run at all.

    Callers use this to leave Graph as the only source rather than failing: a
    deployment without storage credentials keeps working exactly as before.
    """
    return bool(azure_configured() and recordings_container())


def _slug(value: str, fallback: str) -> str:
    cleaned = _UNSAFE_BLOB_CHARS.sub("-", str(value or "")).strip("-")
    return cleaned[:120] or fallback


def build_blob_name(event_key: str, graph_artifact_id: str, content_type: str = "") -> str:
    """Stable, collision-free path for one recording.

    Keyed by event and Graph artifact id so re-archiving the same recording
    overwrites in place instead of growing a second copy, and so the container
    stays browsable per meeting.
    """
    extension = "mp4"
    if content_type and "/" in content_type:
        candidate = content_type.split("/", 1)[1].split(";", 1)[0].strip().lower()
        if candidate in {"mp4", "webm", "ogg", "mpeg"}:
            extension = "mp4" if candidate == "mpeg" else candidate
    # Graph recording ids run past 200 characters and share a long common
    # prefix (the base64 meeting thread), so truncating them collides: two
    # different recordings of one meeting produced the same name and the
    # second silently overwrote the first. Hash instead -- short, unique, and
    # stable, so re-archiving the same recording still overwrites in place.
    digest = hashlib.sha256(str(graph_artifact_id or "").encode("utf-8")).hexdigest()[:32]
    return f"{_slug(event_key, 'event')}/{digest}.{extension}"


def blob_columns_ready(database: str) -> bool:
    """Whether the archive columns exist yet.

    Mirrors coach_meeting_artifact_transcript_columns_ready: a deployment that
    has not run the column migration keeps serving recordings from Graph rather
    than erroring on a missing column.
    """
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                """
                select count(*)
                from information_schema.columns
                where table_schema = 'Coach'
                  and table_name = 'coach_meeting_artifacts'
                  and column_name in (
                    'blob_container',
                    'blob_name',
                    'blob_size_bytes',
                    'blob_content_type',
                    'blob_uploaded_at',
                    'blob_upload_error'
                  )
                """
            )
            row = cursor.fetchone()
        return bool(row and row[0] == 6)
    except Exception:
        return False


def stored_recording_blob(event_key: str, graph_artifact_id: str, database: str | None = None) -> dict | None:
    """The archived blob for one recording, or None if it is not archived yet."""
    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    if not blob_columns_ready(database):
        return None
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                select blob_container, blob_name, blob_size_bytes, blob_content_type
                from {ARTIFACTS_RELATION}
                where event_key = %s
                  and artifact_type = 'recording'
                  and graph_artifact_id = %s
                  and blob_name is not null
                  and blob_name <> ''
                limit 1
                """,
                [event_key, graph_artifact_id],
            )
            row = cursor.fetchone()
    except Exception:
        logger.warning("Could not read archived recording for %s", event_key, exc_info=True)
        return None
    if not row:
        return None
    container, blob_name, size_bytes, content_type = row
    return {
        "container": container,
        "blob_name": blob_name,
        "size_bytes": size_bytes,
        "content_type": content_type,
    }


def archived_recording_url(event_key: str, graph_artifact_id: str, database: str | None = None) -> str:
    """Short-lived read URL for an archived recording, or '' to fall back to Graph.

    The container is private: this SAS is the only way to read a recording, and
    it expires with AZURE_SAS_TTL_MINUTES.
    """
    if not archive_configured():
        return ""
    stored = stored_recording_blob(event_key, graph_artifact_id, database)
    if not stored:
        return ""
    try:
        return get_read_sas(stored["container"], stored["blob_name"])
    except Exception:
        logger.warning("Could not sign archived recording %s", stored.get("blob_name"), exc_info=True)
        return ""


def _record_upload(database, event_key, graph_artifact_id, *, container="", blob_name="",
                   size_bytes=None, content_type="", error="", only_unarchived=False):
    """Persist the outcome of one archive attempt onto the artifact row."""
    from django.utils import timezone

    with connections[database].cursor() as cursor:
        cursor.execute(
            f"""
            update {ARTIFACTS_RELATION}
               set blob_container = %s,
                   blob_name = %s,
                   blob_size_bytes = %s,
                   blob_content_type = %s,
                   blob_uploaded_at = %s,
                   blob_upload_error = %s,
                   updated_at = %s
             where event_key = %s
               and artifact_type = 'recording'
               and graph_artifact_id = %s
               {"and coalesce(blob_name, '') = ''" if only_unarchived else ""}
            """,
            [
                container or None,
                blob_name or None,
                size_bytes,
                content_type or None,
                timezone.now() if blob_name else None,
                error or "",
                timezone.now(),
                event_key,
                graph_artifact_id,
            ],
        )
        return cursor.rowcount


def archive_recording(event_key: str, graph_artifact_id: str, content_bytes: bytes,
                      content_type: str = "", database: str | None = None, *, imported_attempt=None) -> dict:
    """Upload one already-downloaded recording and record where it landed.

    Returns ``{"archived": bool, "blob_name": str, "error": str}`` and never
    raises: a failure here must not lose the Graph copy the caller still has,
    and the next pass retries the row.
    """
    database = database or router.db_for_write(CoachCalendarEvent) or "default"
    if not archive_configured():
        return {"archived": False, "blob_name": "", "error": "Azure storage is not configured."}
    if not content_bytes:
        return {"archived": False, "blob_name": "", "error": "Recording content was empty."}
    if not blob_columns_ready(database):
        return {"archived": False, "blob_name": "", "error": "Archive columns are not present yet."}

    if imported_attempt is not None:
        if (event_key, graph_artifact_id) != (imported_attempt.event_key, imported_attempt.artifact_id):
            return {"archived": False, "blob_name": "", "error": "Imported recording identity mismatch."}
        from .imported_recording_archive import archive_imported_recording
        return archive_imported_recording(imported_attempt, content_bytes, content_type, database)

    container = recordings_container(meeting_type_from_event_key(event_key))
    blob_name = build_blob_name(event_key, graph_artifact_id, content_type)
    try:
        upload_blob(
            BytesIO(content_bytes),
            container,
            blob_name,
            content_type or "video/mp4",
            upload_block_bytes=UPLOAD_BLOCK_BYTES,
            max_concurrency=UPLOAD_MAX_CONCURRENCY,
        )
    except Exception as exc:
        logger.warning("Recording upload failed for %s: %s", event_key, exc)
        try:
            _record_upload(database, event_key, graph_artifact_id, error=str(exc)[:500])
        except Exception:
            logger.warning("Could not record the failed upload for %s", event_key, exc_info=True)
        return {"archived": False, "blob_name": "", "error": str(exc)}

    try:
        _record_upload(
            database, event_key, graph_artifact_id,
            container=container, blob_name=blob_name,
            size_bytes=len(content_bytes), content_type=content_type or "video/mp4",
        )
    except Exception as exc:
        # The bytes are in the container but nothing points at them. Drop the
        # orphan rather than leave a recording nobody can find or delete.
        logger.warning("Could not persist the archived recording for %s: %s", event_key, exc)
        try:
            delete_blob(container, blob_name)
        except Exception:
            logger.warning("Orphaned recording blob left behind: %s/%s", container, blob_name)
        return {"archived": False, "blob_name": "", "error": str(exc)}

    return {"archived": True, "blob_name": blob_name, "error": ""}


def download_recording_from_graph(base: str, artifact_id: str) -> tuple[bytes, str, str]:
    """Pull one recording's bytes out of Graph.

    Returns ``(content, content_type, error)``. Mirrors the transcript fetcher
    in views.py, with a longer timeout: recordings are large and Graph streams
    them slowly. Never raises -- an unreachable Graph is the whole reason this
    archive exists, so it is reported, not thrown.
    """
    import urllib.error as urllib_error
    import urllib.parse as urllib_parse
    import urllib.request as urllib_request

    from coach_api.views import clean_text, get_graph_settings, microsoft_graph_token

    graph_settings = get_graph_settings()
    path = f"{base}/recordings/{urllib_parse.quote(clean_text(artifact_id), safe='')}/content"
    url = f'{graph_settings["base_url"].rstrip("/")}/{path}'
    try:
        request = urllib_request.Request(
            url,
            headers={
                "Authorization": f"Bearer {microsoft_graph_token()}",
                "Accept": "video/mp4",
            },
            method="GET",
        )
        with urllib_request.urlopen(request, timeout=300) as response:
            content_type = response.headers.get("Content-Type") or "video/mp4"
            return response.read(), content_type, ""
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        logger.warning("Graph refused recording %s: %s %s", artifact_id, exc.code, detail)
        return b"", "", f"Graph returned {exc.code} for the recording content."
    except Exception as exc:
        logger.warning("Could not download recording %s: %s", artifact_id, exc)
        return b"", "", str(exc)[:300]


def pending_recordings(database: str | None = None, limit: int = 50, *, event_key: str = '') -> list[dict]:
    """Recordings Graph has handed us that are not archived yet.

    Preserve the existing fresh-candidate selection. Spare capacity can retry
    failed APTEM uploads, after source and stored session qualification. There
    is no persisted attempt counter; an error alone is not retry exhaustion.
    """
    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    if not blob_columns_ready(database):
        return []
    with connections[database].cursor() as cursor:
        cursor.execute(
            f"""
            select event_key, graph_artifact_id, owner_email, graph_organizer_email, graph_event_id
            from {ARTIFACTS_RELATION}
            where artifact_type = 'recording'
              and event_key not like 'coach-calendar:%%'
              and (blob_name is null or blob_name = '')
              and coalesce(blob_upload_error, '') = ''
              {"and event_key = %s" if event_key else ""}
            order by created_datetime desc nulls last
            limit %s
            """,
            [event_key, limit] if event_key else [limit],
        )
        columns = [column[0] for column in cursor.description]
        pending = [dict(zip(columns, row)) for row in cursor.fetchall()]
        remaining = limit - len(pending)
        if remaining <= 0:
            return pending
        cursor.execute(
            f"""
            select event_key, graph_artifact_id, owner_email, graph_organizer_email, graph_event_id
            from {ARTIFACTS_RELATION}
            where artifact_type = 'recording'
              and (blob_name is null or blob_name = '')
              and coalesce(blob_upload_error, '') <> ''
              {"and event_key = %s" if event_key else ""}
            order by updated_at asc nulls first, created_datetime asc nulls first
            limit %s
            """,
            [event_key, limit] if event_key else [limit],
        )
        failed = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for row in failed:
        if _imported_recording_retry_allowed(row, database):
            pending.append(row)
            if len(pending) == limit:
                break
    return pending


def _imported_recording_retry_allowed(row, database):
    """Reuse APTEM identity/session guards; never initialize or start a Review."""
    from .background_meeting_sync import imported_context
    from .migrated_session_scope import select_artifacts
    from .views import stored_coach_meeting_snapshot

    try:
        record = CoachCalendarEvent.objects.using(database).filter(event_key=row['event_key']).first()
        context = imported_context(record) if record is not None else None
        if record is None or not context:
            return False
        for field in ('owner_email', 'graph_organizer_email', 'graph_event_id'):
            saved, current = str(row.get(field) or '').strip(), str(getattr(record, field, '') or '').strip()
            if field != 'graph_event_id':
                saved, current = saved.casefold(), current.casefold()
            if not saved or saved != current:
                return False
        snapshot = stored_coach_meeting_snapshot(record, database=database, safe_diagnostics=True)
        if snapshot.get('partial') or not (snapshot.get('storage') or {}).get('stored'):
            return False
        from .migrated_session_scope import has_verified_meeting_evidence
        meeting_scoped = has_verified_meeting_evidence(snapshot)
        from .bound_actual_session import bound_session
        from .models import ImportedReviewInstance
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
        return (not error or meeting_scoped) and any(
            artifact.get('artifact_type') == 'recording'
            and artifact.get('graph_artifact_id') == row['graph_artifact_id']
            for artifact in selected
        )
    except Exception:
        # Do not print provider paths, identities or stored metadata on failure.
        logger.warning('Imported recording retry qualification unavailable; candidate skipped.')
        return False


def transcripts_container() -> str:
    return settings.AZURE_TRANSCRIPTS_CONTAINER


def build_transcript_blob_name(event_key: str, graph_artifact_id: str) -> str:
    """Same hashed scheme as recordings -- Graph ids are too long to truncate."""
    digest = hashlib.sha256(str(graph_artifact_id or "").encode("utf-8")).hexdigest()[:32]
    return f"{_slug(event_key, 'event')}/{digest}.vtt"


def transcript_blob_columns_ready(database: str) -> bool:
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                """
                select count(*)
                from information_schema.columns
                where table_schema = 'Coach'
                  and table_name = 'coach_meeting_artifacts'
                  and column_name in (
                    'transcript_blob_container',
                    'transcript_blob_name',
                    'transcript_blob_size_bytes',
                    'transcript_blob_uploaded_at',
                    'transcript_blob_error'
                  )
                """
            )
            row = cursor.fetchone()
        return bool(row and row[0] == 5)
    except Exception:
        return False


def archive_transcript_vtt(event_key: str, graph_artifact_id: str, vtt: str,
                           database: str | None = None) -> dict:
    """Move one transcript's WebVTT body into blob storage.

    The readable transcript_text column is deliberately left in place: it is
    what SQL search runs against. Only the cue-heavy VTT is offloaded, and the
    column is cleared afterwards so the row stops carrying it.
    """
    database = database or router.db_for_write(CoachCalendarEvent) or "default"
    if not archive_configured():
        return {"archived": False, "error": "Azure storage is not configured."}
    if not vtt:
        return {"archived": False, "error": "Transcript was empty."}
    if not transcript_blob_columns_ready(database):
        return {"archived": False, "error": "Transcript archive columns are not present yet."}

    from django.utils import timezone

    container = transcripts_container()
    blob_name = build_transcript_blob_name(event_key, graph_artifact_id)
    payload = vtt.encode("utf-8")
    try:
        upload_blob(BytesIO(payload), container, blob_name, "text/vtt")
    except Exception as exc:
        logger.warning("Transcript upload failed for %s: %s", event_key, exc)
        return {"archived": False, "error": str(exc)[:300]}

    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                update {ARTIFACTS_RELATION}
                   set transcript_blob_container = %s,
                       transcript_blob_name = %s,
                       transcript_blob_size_bytes = %s,
                       transcript_blob_uploaded_at = %s,
                       transcript_blob_error = '',
                       transcript_vtt = '',
                       updated_at = %s
                 where event_key = %s
                   and artifact_type = 'transcript'
                   and graph_artifact_id = %s
                """,
                [container, blob_name, len(payload), timezone.now(), timezone.now(),
                 event_key, graph_artifact_id],
            )
    except Exception as exc:
        # Nothing points at the blob, and the column still holds the only copy.
        # Drop the orphan rather than leave an unreferenced transcript behind.
        logger.warning("Could not persist archived transcript for %s: %s", event_key, exc)
        try:
            delete_blob(container, blob_name)
        except Exception:
            logger.warning("Orphaned transcript blob: %s/%s", container, blob_name)
        return {"archived": False, "error": str(exc)[:300]}

    return {"archived": True, "blob_name": blob_name, "error": ""}


def archived_transcript_vtt(event_key: str, graph_artifact_id: str,
                            database: str | None = None) -> str:
    """Read an offloaded transcript back, or '' when it is not archived."""
    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    if not transcript_blob_columns_ready(database):
        return ""
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                select transcript_blob_container, transcript_blob_name
                from {ARTIFACTS_RELATION}
                where event_key = %s
                  and artifact_type = 'transcript'
                  and graph_artifact_id = %s
                  and coalesce(transcript_blob_name, '') <> ''
                limit 1
                """,
                [event_key, graph_artifact_id],
            )
            row = cursor.fetchone()
        if not row:
            return ""
        return download_blob_bytes(row[0], row[1]).decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Could not read archived transcript for %s", event_key, exc_info=True)
        return ""


def recording_is_archived(event_key: str, graph_artifact_id: str, database: str | None = None) -> bool:
    """True when the blob is both recorded in the database and present in the container."""
    stored = stored_recording_blob(event_key, graph_artifact_id, database)
    if not stored:
        return False
    try:
        return bool(blob_exists(stored["container"], stored["blob_name"]))
    except Exception:
        # Assume present: a storage blip should not trigger a re-upload of
        # hundreds of megabytes.
        return True


# ---------------------------------------------------------------------------
# Curriculum live sessions
#
# Same archive, different table: live_session_artifacts is keyed by
# occurrence_id and every live session is a taught class, so they all share one
# container rather than being split by meeting type.
# ---------------------------------------------------------------------------

LIVE_SESSION_TYPE = "live-session"


def live_session_blob_columns_ready(database: str) -> bool:
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                """
                select count(*)
                from information_schema.columns
                where table_schema = 'curriculum'
                  and table_name = 'live_session_artifacts'
                  and column_name in (
                    'blob_container', 'blob_name', 'blob_size_bytes',
                    'blob_content_type', 'blob_uploaded_at', 'blob_upload_error',
                    'transcript_text', 'transcript_blob_container',
                    'transcript_blob_name', 'transcript_blob_size_bytes',
                    'transcript_blob_uploaded_at', 'transcript_blob_error'
                  )
                """
            )
            row = cursor.fetchone()
        return bool(row and row[0] == 12)
    except Exception:
        return False


def pending_live_session_recordings(database: str, limit: int = 50) -> list[dict]:
    """Live-session recordings Graph has exposed that are not archived yet."""
    if not live_session_blob_columns_ready(database):
        return []
    with connections[database].cursor() as cursor:
        cursor.execute(
            f"""
            select occurrence_id, graph_artifact_id
            from {LIVE_SESSION_ARTIFACTS_RELATION}
            where artifact_type = 'recording'
              and coalesce(blob_name, '') = ''
              and coalesce(blob_upload_error, '') = ''
            order by created_datetime desc nulls last
            limit %s
            """,
            [limit],
        )
        return [{"occurrence_id": row[0], "graph_artifact_id": row[1]} for row in cursor.fetchall()]


def archive_live_session_recording(occurrence_id: str, graph_artifact_id: str,
                                   content_bytes: bytes, content_type: str = "",
                                   database: str | None = None) -> dict:
    """Upload one live-session recording and record where it landed."""
    from django.utils import timezone

    database = database or router.db_for_write(CoachCalendarEvent) or "default"
    if not archive_configured():
        return {"archived": False, "blob_name": "", "error": "Azure storage is not configured."}
    if not content_bytes:
        return {"archived": False, "blob_name": "", "error": "Recording content was empty."}
    if not live_session_blob_columns_ready(database):
        return {"archived": False, "blob_name": "", "error": "Archive columns are not present yet."}

    container = recordings_container(LIVE_SESSION_TYPE)
    blob_name = build_blob_name(occurrence_id, graph_artifact_id, content_type)
    try:
        upload_blob(
            BytesIO(content_bytes), container, blob_name, content_type or "video/mp4",
            upload_block_bytes=UPLOAD_BLOCK_BYTES, max_concurrency=UPLOAD_MAX_CONCURRENCY,
        )
    except Exception as exc:
        logger.warning("Live session recording upload failed for %s: %s", occurrence_id, exc)
        return {"archived": False, "blob_name": "", "error": str(exc)[:300]}

    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                update {LIVE_SESSION_ARTIFACTS_RELATION}
                   set blob_container = %s, blob_name = %s, blob_size_bytes = %s,
                       blob_content_type = %s, blob_uploaded_at = %s,
                       blob_upload_error = '', updated_at = %s
                 where {LIVE_SESSION_KEY_COLUMN} = %s and artifact_type = 'recording'
                   and graph_artifact_id = %s
                """,
                [container, blob_name, len(content_bytes), content_type or "video/mp4",
                 timezone.now(), timezone.now(), occurrence_id, graph_artifact_id],
            )
    except Exception as exc:
        logger.warning("Could not persist live session recording for %s: %s", occurrence_id, exc)
        try:
            delete_blob(container, blob_name)
        except Exception:
            logger.warning("Orphaned live session blob: %s/%s", container, blob_name)
        return {"archived": False, "blob_name": "", "error": str(exc)[:300]}

    return {"archived": True, "blob_name": blob_name, "error": ""}


def archived_live_session_recording_url(occurrence_id: str, graph_artifact_id: str,
                                        database: str | None = None) -> str:
    """Short-lived read URL for an archived live-session recording, or ''."""
    if not archive_configured():
        return ""
    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    if not live_session_blob_columns_ready(database):
        return ""
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                select blob_container, blob_name
                from {LIVE_SESSION_ARTIFACTS_RELATION}
                where {LIVE_SESSION_KEY_COLUMN} = %s and artifact_type = 'recording'
                  and graph_artifact_id = %s and coalesce(blob_name, '') <> ''
                limit 1
                """,
                [occurrence_id, graph_artifact_id],
            )
            row = cursor.fetchone()
        if not row:
            return ""
        return get_read_sas(row[0], row[1])
    except Exception:
        logger.warning("Could not sign live session recording for %s", occurrence_id, exc_info=True)
        return ""


def pending_live_session_transcripts(database: str, limit: int = 50) -> list[dict]:
    """Live-session transcripts Graph has exposed that are not archived yet."""
    if not live_session_blob_columns_ready(database):
        return []
    with connections[database].cursor() as cursor:
        cursor.execute(
            f"""
            select occurrence_id, graph_artifact_id
            from {LIVE_SESSION_ARTIFACTS_RELATION}
            where artifact_type = 'transcript'
              and coalesce(transcript_blob_name, '') = ''
              and coalesce(transcript_blob_error, '') = ''
            order by created_datetime desc nulls last
            limit %s
            """,
            [limit],
        )
        return [{"occurrence_id": row[0], "graph_artifact_id": row[1]} for row in cursor.fetchall()]


def download_transcript_from_graph(base: str, artifact_id: str) -> tuple[str, str]:
    """Pull one transcript's WebVTT body out of Graph.

    Returns ``(vtt, error)``. Never raises, for the same reason as the recording
    fetcher: Graph being unreachable is the condition this archive exists for.
    """
    import urllib.error as urllib_error
    import urllib.parse as urllib_parse
    import urllib.request as urllib_request

    from coach_api.views import clean_text, get_graph_settings, microsoft_graph_token

    graph_settings = get_graph_settings()
    path = f"{base}/transcripts/{urllib_parse.quote(clean_text(artifact_id), safe='')}/content"
    url = f'{graph_settings["base_url"].rstrip("/")}/{path}'
    try:
        request = urllib_request.Request(
            url,
            headers={"Authorization": f"Bearer {microsoft_graph_token()}", "Accept": "text/vtt"},
            method="GET",
        )
        with urllib_request.urlopen(request, timeout=90) as response:
            return response.read().decode("utf-8", errors="replace"), ""
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        logger.warning("Graph refused transcript %s: %s %s", artifact_id, exc.code, detail)
        return "", f"Graph returned {exc.code} for the transcript content."
    except Exception as exc:
        logger.warning("Could not download transcript %s: %s", artifact_id, exc)
        return "", str(exc)[:300]


def archive_live_session_transcript(occurrence_id: str, graph_artifact_id: str, vtt: str,
                                    database: str | None = None) -> dict:
    """Store one live-session transcript: VTT to blob, readable text to the row.

    Same split as coach transcripts -- the cue file is what grows the table, the
    plain text is what SQL search needs.
    """
    from django.utils import timezone

    from coach_api.views import coach_meeting_transcript_text

    database = database or router.db_for_write(CoachCalendarEvent) or "default"
    if not archive_configured():
        return {"archived": False, "error": "Azure storage is not configured."}
    if not vtt:
        return {"archived": False, "error": "Transcript was empty."}
    if not live_session_blob_columns_ready(database):
        return {"archived": False, "error": "Archive columns are not present yet."}

    container = transcripts_container()
    blob_name = build_transcript_blob_name(occurrence_id, graph_artifact_id)
    payload = vtt.encode("utf-8")
    try:
        upload_blob(BytesIO(payload), container, blob_name, "text/vtt")
    except Exception as exc:
        logger.warning("Live session transcript upload failed for %s: %s", occurrence_id, exc)
        return {"archived": False, "error": str(exc)[:300]}

    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                update {LIVE_SESSION_ARTIFACTS_RELATION}
                   set transcript_blob_container = %s,
                       transcript_blob_name = %s,
                       transcript_blob_size_bytes = %s,
                       transcript_blob_uploaded_at = %s,
                       transcript_blob_error = '',
                       transcript_text = %s,
                       updated_at = %s
                 where {LIVE_SESSION_KEY_COLUMN} = %s and artifact_type = 'transcript'
                   and graph_artifact_id = %s
                """,
                [container, blob_name, len(payload), timezone.now(),
                 coach_meeting_transcript_text(vtt), timezone.now(),
                 occurrence_id, graph_artifact_id],
            )
    except Exception as exc:
        logger.warning("Could not persist live session transcript for %s: %s", occurrence_id, exc)
        try:
            delete_blob(container, blob_name)
        except Exception:
            logger.warning("Orphaned live session transcript blob: %s/%s", container, blob_name)
        return {"archived": False, "error": str(exc)[:300]}

    return {"archived": True, "blob_name": blob_name, "error": ""}


def archived_live_session_transcript(occurrence_id: str, graph_artifact_id: str,
                                     database: str | None = None) -> str:
    """Read an archived live-session transcript back, or '' when not archived."""
    database = database or router.db_for_read(CoachCalendarEvent) or "default"
    if not live_session_blob_columns_ready(database):
        return ""
    try:
        with connections[database].cursor() as cursor:
            cursor.execute(
                f"""
                select transcript_blob_container, transcript_blob_name
                from {LIVE_SESSION_ARTIFACTS_RELATION}
                where {LIVE_SESSION_KEY_COLUMN} = %s and artifact_type = 'transcript'
                  and graph_artifact_id = %s
                  and coalesce(transcript_blob_name, '') <> ''
                limit 1
                """,
                [occurrence_id, graph_artifact_id],
            )
            row = cursor.fetchone()
        if not row:
            return ""
        return download_blob_bytes(row[0], row[1]).decode("utf-8", errors="replace")
    except Exception:
        logger.warning("Could not read archived live transcript for %s", occurrence_id, exc_info=True)
        return ""
