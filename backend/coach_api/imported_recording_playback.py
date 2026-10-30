"""Protected, on-demand playback of a verified imported Review recording."""

import re
from urllib import error as urllib_error, parse as urllib_parse, request as urllib_request

from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse


class _SafeGraphRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urllib_parse.urlsplit(new_url).scheme.lower() != 'https':
            raise urllib_error.HTTPError(new_url, 502, 'Insecure recording redirect.', headers, response)
        redirected = super().redirect_request(request, response, code, message, headers, new_url)
        changed_host = (urllib_parse.urlsplit(new_url).netloc.lower()
                        != urllib_parse.urlsplit(request.full_url).netloc.lower())
        if redirected and changed_host:
            for key in list(redirected.headers):
                if key.lower() == 'authorization':
                    del redirected.headers[key]
        return redirected


def _open_graph(request):
    return urllib_request.build_opener(_SafeGraphRedirect()).open(request, timeout=45)


def graph_playback_candidate(record, snapshot, artifact):
    """A saved recording can be *attempted* at Graph; this does not prove bytes exist."""
    from .views import has_graph_credentials

    metadata = artifact.get('metadata') or {}
    meeting_id = metadata.get('_lms_verified_online_meeting_id') if isinstance(metadata, dict) else None
    return bool(
        has_graph_credentials()
        and (snapshot.get('storage') or {}).get('stored')
        and not snapshot.get('partial')
        and 'selectionError' in snapshot and not snapshot['selectionError']
        and artifact.get('artifact_type') == 'recording'
        and artifact.get('graph_artifact_id')
        and record.graph_event_id
        and metadata.get('_lms_graph_event_id') == record.graph_event_id
        and meeting_id
        and metadata.get('meetingId') in (None, meeting_id)
    )


def stream_imported_recording(request, record, snapshot, artifact):
    """Recheck the provider meeting before requesting bytes for one saved ID."""
    from .actual_session_resolution import review_meeting_target
    from .views import get_graph_settings, microsoft_graph_token

    if not graph_playback_candidate(record, snapshot, artifact):
        return _unavailable()
    identity = {}
    try:
        base, error = review_meeting_target(record, identity_out=identity)
    except (RuntimeError, ValueError, TypeError, DatabaseError):
        return _unavailable('The linked Teams meeting could not be verified.', 409)
    target = identity.get('target')
    metadata = artifact['metadata']
    if (error or not target or not base
            or target.calendar_id != record.pk
            or target.graph_event_id != record.graph_event_id
            or target.online_meeting_id != metadata['_lms_verified_online_meeting_id']):
        return _unavailable('The linked Teams meeting could not be verified.', 409)

    range_header = request.META.get('HTTP_RANGE', '').strip()
    if range_header and (len(range_header) > 128 or not re.fullmatch(r'bytes=\d*-\d*', range_header)):
        return _unavailable('Invalid video byte range.', 400)
    path = f"{base}/recordings/{urllib_parse.quote(artifact['graph_artifact_id'], safe='')}/content"
    try:
        url = f"{get_graph_settings()['base_url'].rstrip('/')}/{path}"
        headers = {'Authorization': f'Bearer {microsoft_graph_token()}', 'Accept': 'video/mp4'}
    except (RuntimeError, KeyError, ValueError):
        return _unavailable('Teams playback is temporarily unavailable.', 503)
    if range_header:
        headers['Range'] = range_header
    try:
        upstream = _open_graph(urllib_request.Request(url, headers=headers, method='GET'))
    except urllib_error.HTTPError as exc:
        if exc.code == 416:
            response = HttpResponse(status=416)
            if exc.headers and exc.headers.get('Content-Range'):
                response['Content-Range'] = exc.headers['Content-Range']
            response['Cache-Control'] = 'private, no-store'
            return response
        return _unavailable('Teams could not return this recording.', 404 if exc.code == 404 else 502)
    except (urllib_error.URLError, TimeoutError):
        return _unavailable('Teams could not return this recording.', 502)

    content_type = upstream.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if upstream.status not in (200, 206) or content_type not in ('video/mp4', 'application/octet-stream'):
        upstream.close()
        return _unavailable('Teams did not return playable video.', 502)

    def chunks():
        try:
            while chunk := upstream.read(65536):
                yield chunk
        finally:
            upstream.close()

    response = StreamingHttpResponse(chunks(), status=upstream.status, content_type='video/mp4')
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'private, no-store'
    for header in ('Content-Length', 'Content-Range'):
        value = upstream.headers.get(header)
        if value:
            response[header] = value
    disposition = 'inline' if request.GET.get('preview', '').lower() in ('1', 'true', 'yes') else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="review-recording.mp4"'
    return response


def _unavailable(detail='This recording is not available for direct playback.', status=404):
    response = JsonResponse({'detail': detail, 'code': 'RECORDING_PLAYBACK_UNAVAILABLE'}, status=status)
    response['Cache-Control'] = 'private, no-store'
    return response
