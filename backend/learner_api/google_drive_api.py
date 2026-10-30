"""Stream course videos from a private Google Drive through the Drive API.

The media proxy fetches Drive files anonymously, which works only for files
shared "Anyone with the link". A file shared only inside the organisation
answers that request with 404, and the learner falls back to Drive's own
preview (when they happen to be signed in to Google) or sees nothing.

With a Google account's consent stored as an OAuth refresh token, the proxy can
read those files as that account. It does so only for a file that a live
curriculum component links to: the endpoint is open to every signed-in user,
and without that check anyone could pull any other private file in that
Drive by its id.

Configured by three settings (``backend/.env``):

    KBC_LMS_GOOGLE_CLIENT_ID / KBC_LMS_GOOGLE_CLIENT_SECRET   the OAuth client
    KBC_LMS_GOOGLE_DRIVE_REFRESH_TOKEN                        from the one-time
        ``python manage.py authorize_google_drive`` sign-in

Without all three, nothing here is used and the proxy behaves as before.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from django.db import DatabaseError
from django.db.models import Q

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FILE_URL = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&supportsAllDrives=true"
CLIENT_ID_ENV = "KBC_LMS_GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV = "KBC_LMS_GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN_ENV = "KBC_LMS_GOOGLE_DRIVE_REFRESH_TOKEN"

_token_lock = threading.Lock()
_token = {"value": "", "expires_at": 0.0}


def _credentials():
    values = tuple(os.environ.get(name, "").strip() for name in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REFRESH_TOKEN_ENV))
    return values if all(values) else None


def is_configured():
    return _credentials() is not None


def _access_token(force_refresh=False):
    """A current access token, refreshed a minute before Google expires it."""
    credentials = _credentials()
    if credentials is None:
        return ""
    with _token_lock:
        if not force_refresh and _token["value"] and time.time() < _token["expires_at"]:
            return _token["value"]
        client_id, client_secret, refresh_token = credentials
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        }).encode()
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        _token["value"] = payload["access_token"]
        _token["expires_at"] = time.time() + max(int(payload.get("expires_in", 3600)) - 60, 60)
        return _token["value"]


def is_course_linked(file_id):
    """Does a live curriculum component play this Drive file?

    The same fields the learner API takes a component's video from
    (``learner_detail._video_url_from_settings``). Fails closed.

    ``__regex``, not ``__contains``: on a JSON key ``contains`` means JSON
    containment. Callers pass an id already matched against GOOGLE_DRIVE_ID_RE,
    whose characters are all literal in a regex.
    """
    from curriculum_api.models import ModuleAuthoringComponent

    try:
        return (ModuleAuthoringComponent.objects
                .filter(deleted_at__isnull=True)
                .filter(Q(settings_json__videoUrl__regex=file_id) | Q(settings_json__embedCode__regex=file_id))
                .exists())
    except DatabaseError:
        logger.exception("Could not check whether Drive file %s is course content", file_id)
        return False


def open_file(file_id, range_header=None):
    """Open the file's bytes as the authorised account; raises HTTPError/URLError."""
    for attempt in range(2):
        headers = {"Authorization": f"Bearer {_access_token(force_refresh=bool(attempt))}"}
        if range_header:
            headers["Range"] = range_header
        try:
            return urllib.request.urlopen(
                urllib.request.Request(FILE_URL.format(file_id=urllib.parse.quote(file_id)), headers=headers),
                timeout=30,
            )
        except urllib.error.HTTPError as exc:
            # One retry with a fresh token, for a token revoked or expired early.
            if exc.code != 401 or attempt:
                raise
            exc.close()
    raise urllib.error.URLError("Google Drive API request failed")
