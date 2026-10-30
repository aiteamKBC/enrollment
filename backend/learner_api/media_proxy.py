"""Small media proxy for learner playback and inline material previews.

Google Drive public files can return a valid MP4 to the backend while a browser
`<video>` pointed directly at Drive stays black or refuses to stream reliably.
This endpoint keeps playback same-origin and preserves Range requests, which
native video/audio controls need for loading and seeking.
"""
import asyncio
import html
import http.cookiejar
import json
import logging
import mimetypes
import re
import urllib.parse
import urllib.error
import urllib.request

from django.db import DatabaseError, connections
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from curriculum_api import upload_storage
from config.video_streaming import video_chunks


logger = logging.getLogger(__name__)

GOOGLE_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")
LEGACY_ATTACHMENT_ID_RE = re.compile(r"^[0-9]{1,20}$")
CHUNK_SIZE = 1024 * 256

PROGRAMME_AUDIT_MATERIAL_TABLES = (
    "ai_in_marketing",
    "commercial_intelligence",
    "customer_journey",
    "earned_value_management_portfolio_management",
    "impact_planning",
    "managing_successful_programmes_scheduling_professional",
    "marketing_technology",
    "project_management_professional",
    "project_planning_control_project_management_office",
    "risk_management",
    "social_media",
    "strategy_planning",
)
PROGRAMME_AUDIT_DATABASE_ALIASES = ("audit", "enrolment", "default")


def _programme_audit_database_aliases():
    """Return configured databases that may carry imported material snapshots.

    Production keeps ``programme_audit`` on its dedicated audit database, while
    older/local environments may still have it on the enrolment/default
    connection.  Keep the latter two as compatibility fallbacks.
    """
    configured = connections.databases
    return tuple(
        alias for alias in PROGRAMME_AUDIT_DATABASE_ALIASES
        if alias in configured
    )


def _stream_response(upstream):
    try:
        while True:
            chunk = upstream.read(CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        upstream.close()


async def _async_stream_response(upstream):
    """Stream a blocking urllib response without making ASGI buffer it all.

    Django's ASGI adapter has to consume a synchronous response iterator before
    it can send it asynchronously.  That turned a 250 MB Drive video into a
    long blank player.  Reading one chunk in a worker thread keeps the response
    genuinely incremental and lets the browser issue normal Range requests.
    """
    try:
        while True:
            chunk = await asyncio.to_thread(upstream.read, CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(upstream.close)


def _google_drive_warning_download_url(page_html):
    """Extract Drive's "Download anyway" URL from the virus-scan warning page."""
    form = re.search(
        r'<form[^>]+id=["\']download-form["\'][^>]*action=["\']([^"\']+)["\'][^>]*>(.*?)</form>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not form:
        return ""
    action = html.unescape(form.group(1))
    inputs = re.findall(
        r'<input[^>]+name=["\']([^"\']+)["\'][^>]+value=["\']([^"\']*)["\']',
        form.group(2),
        flags=re.IGNORECASE,
    )
    if not inputs:
        return ""
    query = urllib.parse.urlencode({
        html.unescape(name): html.unescape(value)
        for name, value in inputs
    })
    return f"{action}?{query}"


def _open_google_drive_file_once(file_id, range_header=None):
    """Open one stream, rejecting HTML even after the download confirmation."""
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    headers = {"User-Agent": "Mozilla/5.0"}
    if range_header:
        headers["Range"] = range_header

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    upstream = opener.open(urllib.request.Request(url, headers=headers), timeout=30)
    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    if not any(kind in content_type.lower() for kind in ("text/html", "application/xhtml+xml")):
        return upstream

    try:
        page = upstream.read().decode("utf-8", "replace")
    finally:
        upstream.close()
    download_url = _google_drive_warning_download_url(page)
    if not download_url:
        return None
    upstream = opener.open(urllib.request.Request(download_url, headers=headers), timeout=30)
    content_type = (upstream.headers.get("Content-Type") or "").lower()
    if "text/html" in content_type or "application/xhtml+xml" in content_type:
        upstream.close()
        return None
    return upstream


def _open_google_drive_file(file_id, range_header=None):
    """Retry a transient Drive failure once; never retry denied/missing files."""
    for attempt in range(2):
        try:
            upstream = _open_google_drive_file_once(file_id, range_header)
            if upstream is not None:
                return upstream
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in (429, 500, 502, 503, 504):
                raise
            exc.close()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt:
                raise urllib.error.URLError("Google Drive media request failed") from exc
    return None


def _office_viewer_source(url):
    parsed = urllib.parse.urlparse(url or "")
    if parsed.netloc.lower() != "view.officeapps.live.com":
        return url
    params = urllib.parse.parse_qs(parsed.query)
    source = (params.get("src") or [""])[0]
    return source or url


def _raw_http_urls(value):
    """Yield trusted original KBC URLs retained inside an imported audit row."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            yield from _raw_http_urls(parsed)
            return
        for match in re.findall(r'https?://[^\s"\'<>\\]+', html.unescape(value)):
            url = match.rstrip('.,);]')
            hostname = (urllib.parse.urlparse(url).hostname or '').lower()
            if hostname == 'kentbusinesscollege.org' or hostname.endswith('.kentbusinesscollege.org'):
                yield url
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from _raw_http_urls(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _raw_http_urls(nested)


def _legacy_attachment_upload_path(attachment_id):
    """Resolve an attachment id to its stable Azure/local path via material tables."""
    like_legacy = f"%/_legacy_files/{attachment_id}/%"
    marker = '/curriculum_api/curriculum/uploads/'
    for alias in _programme_audit_database_aliases():
        try:
            with connections[alias].cursor() as cur:
                for table in PROGRAMME_AUDIT_MATERIAL_TABLES:
                    cur.execute(
                        f'''SELECT source_url, embed_url
                            FROM programme_audit."{table}"
                            WHERE source_url LIKE %s OR embed_url LIKE %s
                            ORDER BY updated_at DESC NULLS LAST,
                                     imported_at DESC NULLS LAST
                            LIMIT 1''',
                        [like_legacy, like_legacy],
                    )
                    row = cur.fetchone()
                    if not row:
                        continue
                    for candidate in row:
                        path = urllib.parse.urlparse(candidate or '').path
                        if marker in path:
                            # Decoded like the hint path: stored names keep
                            # their spaces, the audit URL carries %20.
                            relative = urllib.parse.unquote(path.split(marker, 1)[1]).lstrip('/')
                            if relative.startswith(f'_legacy_files/{attachment_id}/'):
                                return relative
        except (DatabaseError, KeyError):
            continue
    return ''


def _legacy_upload_path_from_hint(attachment_id, value):
    """Accept only the existing upload URL for this exact legacy attachment.

    Learner component payloads already contain the stable Azure-backed upload
    path. Passing it to the PDF endpoints avoids making preview availability
    depend on which database alias carries the imported audit tables.
    """
    path = urllib.parse.urlparse(value or '').path
    marker = '/curriculum_api/curriculum/uploads/'
    if marker not in path:
        return ''
    relative = urllib.parse.unquote(path.split(marker, 1)[1]).lstrip('/')
    expected_prefix = f'_legacy_files/{attachment_id}/'
    if not relative.startswith(expected_prefix) or '\\' in relative:
        return ''
    if any(part in ('', '.', '..') for part in relative.split('/')):
        return ''
    return relative


def _legacy_attachment_source(attachment_id):
    """Find the original imported source for a legacy attachment id.

    Some Azure/local legacy blobs are zero-byte placeholders. The audit import
    still stores the original WordPress material endpoint with the same
    attachment_id, so learner previews can recover the real file through this
    same-origin proxy.
    """
    like_attachment = f"%attachment_id={attachment_id}%"
    like_encoded_attachment = f"%attachment_id%3D{attachment_id}%"
    like_legacy = f"%/_legacy_files/{attachment_id}/%"
    for alias in _programme_audit_database_aliases():
        try:
            with connections[alias].cursor() as cur:
                for table in PROGRAMME_AUDIT_MATERIAL_TABLES:
                    cur.execute(
                        f'''SELECT source_url, embed_url, raw_component
                            FROM programme_audit."{table}"
                            WHERE source_url LIKE %s OR embed_url LIKE %s
                               OR source_url LIKE %s OR embed_url LIKE %s
                               OR source_url LIKE %s OR embed_url LIKE %s
                            ORDER BY updated_at DESC NULLS LAST,
                                     imported_at DESC NULLS LAST
                            LIMIT 1''',
                        [
                            like_attachment,
                            like_attachment,
                            like_encoded_attachment,
                            like_encoded_attachment,
                            like_legacy,
                            like_legacy,
                        ],
                    )
                    row = cur.fetchone()
                    if not row:
                        continue
                    direct = [
                        _office_viewer_source(candidate or "")
                        for candidate in row[:2]
                    ]
                    retained = list(_raw_http_urls(row[2]))
                    candidates = [*direct, *retained]
                    candidates.sort(key=lambda url: (
                        0 if f'attachment_id={attachment_id}' in url else
                        1 if '/wp-content/uploads/' in url else 2
                    ))
                    for source in candidates:
                        if source.startswith("http://") or source.startswith("https://"):
                            return source
        except (DatabaseError, KeyError):
            continue
    return ""


def _embedded_kbc_media_url(page_html, attachment_id):
    """Extract the real audio/video URL from a trusted KBC embed wrapper."""
    decoded = html.unescape(page_html or "")
    matches = re.findall(
        r'<(?:source|audio|video)\b[^>]*\bsrc=["\']([^"\']+)["\']',
        decoded,
        flags=re.IGNORECASE,
    )
    for candidate in matches:
        url = html.unescape(candidate).strip()
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()
        query_attachment = (urllib.parse.parse_qs(parsed.query).get("attachment_id") or [""])[0]
        if (
            (hostname == "kentbusinesscollege.org" or hostname.endswith(".kentbusinesscollege.org"))
            and str(query_attachment) == str(attachment_id)
        ):
            return url
    return ""


def _open_legacy_attachment(attachment_id, range_header=None):
    """Open the attachment bytes, following an LMS audio/video embed once."""
    url = _legacy_attachment_source(attachment_id)
    if not url:
        return None
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "video/*,audio/*,application/pdf,application/octet-stream,*/*",
    }
    if range_header:
        headers["Range"] = range_header
    upstream = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=45)
    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    if "text/html" not in content_type.lower():
        return upstream

    page = upstream.read(1024 * 1024).decode("utf-8", "replace")
    upstream.close()
    media_url = _embedded_kbc_media_url(page, attachment_id)
    if not media_url:
        return None
    return urllib.request.urlopen(urllib.request.Request(media_url, headers=headers), timeout=45)


def _load_legacy_attachment_bytes(attachment_id, upload_hint=''):
    hinted_path = _legacy_upload_path_from_hint(attachment_id, upload_hint)
    discovered_path = _legacy_attachment_upload_path(attachment_id)
    upload_paths = tuple(dict.fromkeys(path for path in (hinted_path, discovered_path) if path))
    for upload_path in upload_paths:
        opened = upload_storage.open_stream(upload_path)
        if opened is not None:
            stream, total, _content_type = opened
            data = b''.join(stream)
            # A zero-byte placeholder is not the file; fall through to the
            # original source below instead of handing back an empty document.
            if total and data:
                return data, None

    try:
        upstream = _open_legacy_attachment(attachment_id)
    except urllib.error.HTTPError as exc:
        return None, HttpResponse(exc.reason or "Could not load legacy attachment.", status=exc.code)
    except urllib.error.URLError:
        return None, HttpResponse("Could not load legacy attachment.", status=502)
    if upstream is None:
        return None, HttpResponse("Legacy attachment source not found.", status=404)

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    if "text/html" in content_type.lower():
        return None, HttpResponse("Legacy attachment returned an HTML page, not a file.", status=502)
    return upstream.read(), None


def _open_private_course_file(file_id, range_header=None):
    """Course video from a private Drive, via the authorised account, or None.

    None also when the Drive API is not configured, the file is not course
    content, or the API refuses: the anonymous path then answers as before.
    """
    from . import google_drive_api

    if not google_drive_api.is_configured() or not google_drive_api.is_course_linked(file_id):
        return None
    try:
        return google_drive_api.open_file(file_id, range_header)
    except urllib.error.HTTPError as exc:
        logger.warning("Drive API refused course file %s: HTTP %s", file_id, exc.code)
        exc.close()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        logger.exception("Drive API request failed for course file %s", file_id)
    return None


@require_GET
def google_drive_media(request, file_id):
    if not GOOGLE_DRIVE_ID_RE.match(file_id or ""):
        return HttpResponse("Invalid file id.", status=400)

    range_header = request.headers.get("Range")

    try:
        upstream = _open_private_course_file(file_id, range_header) or _open_google_drive_file(file_id, range_header)
        if upstream is None:
            return HttpResponse("Google Drive returned a page instead of a media file.", status=502)
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.reason or "Could not load media.", status=exc.code)
    except urllib.error.URLError:
        return HttpResponse("Could not load media.", status=502)

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    response = StreamingHttpResponse(
        # Chosen by server interface, not by content type: Drive and blob storage
        # often label a video application/octet-stream, and an async iterator
        # under WSGI is read to the end before the first byte is sent.
        video_chunks(request, _stream_response(upstream)),
        status=getattr(upstream, "status", 200),
        content_type=content_type,
    )
    for header in ("Content-Length", "Content-Range", "Accept-Ranges"):
        value = upstream.headers.get(header)
        if value:
            response[header] = value
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
# Same reason as curriculum uploads: the site-wide DENY blanks a PDF the learner
# page frames itself. SAMEORIGIN still refuses every other site.
@xframe_options_sameorigin
def legacy_attachment_media(request, attachment_id):
    if not LEGACY_ATTACHMENT_ID_RE.match(attachment_id or ""):
        return HttpResponse("Invalid attachment id.", status=400)

    upload_path = _legacy_attachment_upload_path(attachment_id)
    if upload_path:
        probe = upload_storage.open_stream(upload_path, offset=0, length=1)
        # Zero-byte placeholders skip to the original source further down.
        if probe is not None and probe[1]:
            _probe_stream, total_size, content_type = probe
            start, end = 0, max(total_size - 1, 0)
            range_header = request.headers.get("Range") or ''
            match = re.match(r'^bytes=(\d+)-(\d*)$', range_header.strip())
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else end
                if start >= total_size or end < start:
                    response = HttpResponse(status=416)
                    response['Content-Range'] = f'bytes */{total_size}'
                    return response
                end = min(end, total_size - 1)
            length = max(0, end - start + 1)
            opened = upload_storage.open_stream(upload_path, offset=start, length=length)
            if opened is not None:
                stream, _total, opened_type = opened
                file_type = mimetypes.guess_type(upload_path)[0] or ''
                served_type = opened_type or content_type or (
                    file_type if file_type.startswith('video/') else 'application/octet-stream')
                response = StreamingHttpResponse(
                    video_chunks(request, stream),
                    status=206 if match else 200,
                    content_type=served_type,
                )
                response['Accept-Ranges'] = 'bytes'
                response['Content-Length'] = str(length)
                if match:
                    response['Content-Range'] = f'bytes {start}-{end}/{total_size}'
                response['Cache-Control'] = 'private, max-age=300'
                response['X-Content-Type-Options'] = 'nosniff'
                return response

    range_header = request.headers.get("Range")
    try:
        upstream = _open_legacy_attachment(attachment_id, range_header)
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.reason or "Could not load legacy attachment.", status=exc.code)
    except urllib.error.URLError:
        return HttpResponse("Could not load legacy attachment.", status=502)
    if upstream is None:
        return HttpResponse("Legacy attachment source not found.", status=404)

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    if "text/html" in content_type.lower():
        return HttpResponse("Legacy attachment returned an HTML page, not a file.", status=502)

    response = StreamingHttpResponse(
        # Chosen by server interface, not by content type: Drive and blob storage
        # often label a video application/octet-stream, and an async iterator
        # under WSGI is read to the end before the first byte is sent.
        video_chunks(request, _stream_response(upstream)),
        status=getattr(upstream, "status", 200),
        content_type=content_type,
    )
    for header in ("Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition"):
        value = upstream.headers.get(header)
        if value:
            response[header] = value
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=300"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def legacy_attachment_pdf_info(request, attachment_id):
    if not LEGACY_ATTACHMENT_ID_RE.match(attachment_id or ""):
        return HttpResponse("Invalid attachment id.", status=400)

    data, error = _load_legacy_attachment_bytes(attachment_id, request.GET.get('path', ''))
    if error:
        return error

    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            return JsonResponse({"pages": document.page_count})
        finally:
            document.close()
    except Exception as exc:
        return HttpResponse(f"Could not inspect PDF: {exc}", status=502)


@require_GET
def legacy_attachment_pdf_page(request, attachment_id, page_number):
    if not LEGACY_ATTACHMENT_ID_RE.match(attachment_id or ""):
        return HttpResponse("Invalid attachment id.", status=400)

    try:
        page_index = max(0, int(page_number) - 1)
    except (TypeError, ValueError):
        return HttpResponse("Invalid page number.", status=400)

    data, error = _load_legacy_attachment_bytes(attachment_id, request.GET.get('path', ''))
    if error:
        return error

    try:
        import fitz

        document = fitz.open(stream=data, filetype="pdf")
        try:
            if page_index >= document.page_count:
                return HttpResponse("Page not found.", status=404)
            page = document.load_page(page_index)
            zoom = 1.7
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            response = HttpResponse(pixmap.tobytes("png"), content_type="image/png")
            response["Cache-Control"] = "private, max-age=300"
            response["X-Page-Count"] = str(document.page_count)
            response["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            document.close()
    except Exception as exc:
        return HttpResponse(f"Could not render PDF page: {exc}", status=502)
