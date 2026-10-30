"""Personal calendar connections for learner availability.

OAuth credentials never reach the browser. They are encrypted at rest and are
only used to answer free/busy requests for the learner that owns the row.
"""
import base64
import hashlib
import json
import os
import re
import ipaddress
import secrets
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from cryptography.fernet import Fernet, InvalidToken
from lxml import etree
from django.core import signing
from django.core.cache import cache
from django.db import connections, transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from login.permissions import _auth_gate_enabled, learner_self_only
from login.sessions import authenticate_request

from .learner_detail import SOURCE_MODELS


PROVIDERS = {"google", "microsoft", "icloud", "caldav", "ics"}
OAUTH_PROVIDERS = {"google", "microsoft"}
STATE_SALT = "learner-personal-calendar-oauth"

#: OAuth ``state`` is signed and short-lived but, on its own, replayable within
#: its 10-minute window. Pairing it with a single-use nonce recorded server-side
#: against the minting learner makes each authorization usable exactly once and
#: lets the callback derive whose credentials to store from the server's record
#: rather than from the (attacker-replayable) state. The nonce lives in the
#: cache (Redis in production) rather than a table, so no migration is needed.
_OAUTH_NONCE_PREFIX = "calendar-oauth-nonce:"
_OAUTH_NONCE_TTL_SECONDS = 600


def _issue_oauth_nonce(kind, learner_id, provider):
    """Mint a single-use nonce for an OAuth flow and record who it belongs to."""
    nonce = secrets.token_urlsafe(32)
    cache.set(
        _OAUTH_NONCE_PREFIX + nonce,
        {"kind": kind, "learner_id": int(learner_id), "provider": provider},
        _OAUTH_NONCE_TTL_SECONDS,
    )
    return nonce


def _consume_oauth_nonce(nonce):
    """Return the record for ``nonce`` and delete it, or None if unknown.

    Single-use: the delete is what stops a state being redeemed twice, so a
    replay of an already-completed authorization finds nothing and is rejected.
    """
    if not nonce:
        return None
    key = _OAUTH_NONCE_PREFIX + nonce
    record = cache.get(key)
    if record is None:
        return None
    cache.delete(key)
    return record


def _own_calendar_error(request, learner_id):
    """learner-self-only, inline, for the GET ``oauth_start``, or None to proceed.

    ``learner_self_only`` cannot be used on ``oauth_start`` because it lets safe
    methods through; this reproduces its non-safe branch. Calendar credentials
    are the learner's own — staff and admin are refused (403), a learner naming
    another's id gets 404 (existence-hiding), no session is 401. Honours
    ``LEARNER_API_REQUIRE_AUTH=0`` like the rest of the login gates.
    """
    if not _auth_gate_enabled():
        authenticate_request(request)
        return None
    account = authenticate_request(request)
    if account is None:
        return _error("Authentication required.", 401)
    if account.role == "learner":
        if account.subject_id != learner_id:
            return JsonResponse({"error": "Not found."}, status=404)
        return None
    return _error("Only the learner may manage their own calendar connection.", 403)


def _microsoft_tenant():
    """Honour the configured audience; use the tenant id when no audience is set."""
    tenant_id = (os.environ.get("MICROSOFT_TENANT_ID") or os.environ.get("TENANTID") or "").strip().strip('"')
    configured = (os.environ.get("MICROSOFT_TENANT") or "").strip().strip('"')
    return configured or tenant_id or "common"


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _db():
    return connections["enrolment"]


def _fernet():
    raw = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").encode()
    if not raw:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not configured.")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _encrypt(value):
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def _decrypt(value):
    try:
        return json.loads(_fernet().decrypt(value.encode()).decode())
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Stored calendar credentials could not be decrypted.") from exc


def _learner(kind, learner_id):
    model = SOURCE_MODELS.get(kind)
    if model is None:
        return None
    return model.all_learners.filter(pk=learner_id).first()


def _row(kind, learner_id, provider):
    with _db().cursor() as cursor:
        cursor.execute(
            '''SELECT id, provider, account_email, status, connected_at, last_sync_at,
                      credential_ciphertext, calendar_url
               FROM "Learner"."calendar_connections"
               WHERE learner_kind = %s AND learner_id = %s AND provider = %s''',
            [kind, learner_id, provider],
        )
        result = cursor.fetchone()
    if not result:
        return None
    credentials = _decrypt(result[6])
    return {
        "id": result[0], "provider": result[1], "accountEmail": result[2] or "", "status": result[3],
        "connectedAt": result[4].isoformat() if result[4] else None,
        "lastSyncAt": result[5].isoformat() if result[5] else None,
        "credentials": credentials, "calendarUrl": credentials.get("_calendar_url") or result[7] or "",
    }


def _save(kind, learner_id, provider, credentials, account_email="", calendar_url=""):
    encrypted_payload = dict(credentials)
    if calendar_url:
        encrypted_payload["_calendar_url"] = calendar_url
    with _db().cursor() as cursor:
        cursor.execute(
            '''INSERT INTO "Learner"."calendar_connections"
                 (learner_kind, learner_id, provider, account_email, status,
                  credential_ciphertext, calendar_url, connected_at, updated_at)
               VALUES (%s, %s, %s, %s, 'connected', %s, %s, NOW(), NOW())
               ON CONFLICT (learner_kind, learner_id, provider) DO UPDATE SET
                 account_email = EXCLUDED.account_email,
                 status = 'connected', credential_ciphertext = EXCLUDED.credential_ciphertext,
                 calendar_url = EXCLUDED.calendar_url, connected_at = NOW(), updated_at = NOW()''',
            [kind, learner_id, provider, account_email, _encrypt(encrypted_payload), ""],
        )


def _public(row):
    return {key: row[key] for key in ("provider", "accountEmail", "status", "connectedAt", "lastSyncAt")}


# Private calendar credentials/connections — the owner's alone, not staff (A5).
@learner_self_only(kwarg="learner_id")
def connection_list(request, kind, learner_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    if not _learner(kind, learner_id):
        return _error("Learner not found.", 404)
    try:
        with _db().cursor() as cursor:
            cursor.execute(
                '''SELECT provider, account_email, status, connected_at, last_sync_at
                   FROM "Learner"."calendar_connections"
                   WHERE learner_kind = %s AND learner_id = %s ORDER BY connected_at DESC''',
                [kind, learner_id],
            )
            rows = cursor.fetchall()
    except Exception as exc:
        return _error(f"Calendar connection store is unavailable: {exc}", 503)
    return JsonResponse({"connections": [
        {"provider": row[0], "accountEmail": row[1] or "", "status": row[2],
         "connectedAt": row[3].isoformat() if row[3] else None,
         "lastSyncAt": row[4].isoformat() if row[4] else None}
        for row in rows
    ]})


def oauth_start(request, kind, learner_id, provider):
    if request.method != "GET" or provider not in OAUTH_PROVIDERS:
        return _error("Unsupported calendar provider.", 400)
    # A GET, so learner_self_only (which lets safe methods through) cannot gate
    # it; enforce the same learner-self-only rule inline before a state naming
    # this learner is minted. Fail closed; same status shapes as the decorator.
    ownership_error = _own_calendar_error(request, learner_id)
    if ownership_error is not None:
        return ownership_error
    if not _learner(kind, learner_id):
        return _error("Learner not found.", 404)
    nonce = _issue_oauth_nonce(kind, learner_id, provider)
    state = signing.dumps(
        {"kind": kind, "learnerId": learner_id, "provider": provider, "nonce": nonce},
        salt=STATE_SALT, compress=True,
    )
    if provider == "google":
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        callback = os.environ.get("GOOGLE_CALLBACK_URI", "")
        if not client_id or not callback:
            return _error("Google Calendar OAuth is not configured.", 503)
        query = urlencode({
            "client_id": client_id, "redirect_uri": callback, "response_type": "code",
            "scope": "openid email https://www.googleapis.com/auth/calendar.freebusy",
            "access_type": "offline", "prompt": "consent", "state": state,
        })
        url = f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    else:
        client_id = os.environ.get("MICROSOFT_CLIENT_ID", "")
        callback = os.environ.get("MICROSOFT_CALLBACK_URI", "")
        tenant = _microsoft_tenant()
        if not client_id or not callback:
            return _error("Microsoft Calendar OAuth is not configured.", 503)
        query = urlencode({
            "client_id": client_id, "redirect_uri": callback, "response_type": "code",
            "response_mode": "query", "scope": "offline_access User.Read Calendars.ReadBasic",
            "state": state,
        })
        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{query}"
    return JsonResponse({"authorizationUrl": url})


def _oauth_identity(provider, token):
    if provider == "google":
        response = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        response.raise_for_status()
        return response.json().get("email", "")
    response = httpx.get("https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName", headers={"Authorization": f"Bearer {token}"}, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get("mail") or data.get("userPrincipalName") or ""


def oauth_callback(request, provider):
    app_url = os.environ.get("APP_URL", "http://localhost:3000").rstrip("/")
    try:
        payload = signing.loads(request.GET.get("state", ""), salt=STATE_SALT, max_age=600)
        if payload.get("provider") != provider:
            raise ValueError("Provider mismatch")

        # Single-use: consume the nonce before doing anything with the flow. A
        # replayed or already-completed authorization finds no record and stops
        # here, before any token exchange or write.
        record = _consume_oauth_nonce(payload.get("nonce"))
        if record is None:
            raise ValueError("This calendar authorization has expired or was already used.")
        # The server's record — not the (replayable) state — decides whose
        # credentials are stored. A state whose learner id disagrees with the
        # record it was issued against is rejected, not quietly resolved to one.
        learner_id = record["learner_id"]
        if int(payload.get("learnerId")) != learner_id:
            raise ValueError("Calendar authorization mismatch.")
        # Opportunistic identity binding: where the session cookie reaches this
        # callback host, a caller who is not this learner is rejected outright,
        # closing the in-flight-state replay race. Where no session is present
        # (the cookie may not survive to the callback host in every deployment),
        # the single-use nonce above remains the gate. Never the sole check.
        account = authenticate_request(request)
        if account is not None and account.subject_id != learner_id:
            raise ValueError("Calendar authorization does not match the signed-in learner.")
        code = request.GET.get("code")
        if not code:
            raise ValueError(request.GET.get("error_description") or "OAuth was cancelled")
        if provider == "google":
            token_url = "https://oauth2.googleapis.com/token"
            body = {"code": code, "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                    "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                    "redirect_uri": os.environ.get("GOOGLE_CALLBACK_URI"), "grant_type": "authorization_code"}
        else:
            tenant = _microsoft_tenant()
            token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            body = {"code": code, "client_id": os.environ.get("MICROSOFT_CLIENT_ID"),
                    "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET"),
                    "redirect_uri": os.environ.get("MICROSOFT_CALLBACK_URI"),
                    "scope": "offline_access User.Read Calendars.ReadBasic", "grant_type": "authorization_code"}
        response = httpx.post(token_url, data=body, timeout=10)
        response.raise_for_status()
        tokens = response.json()
        email = _oauth_identity(provider, tokens["access_token"])
        _save(record["kind"], learner_id, provider, tokens, email)
        return HttpResponseRedirect(f"{app_url}/learner/calendar?calendar_connected={provider}")
    except Exception as exc:
        message = urlencode({"calendar_error": str(exc)[:180]})
        return HttpResponseRedirect(f"{app_url}/learner/calendar?{message}")


def _safe_url(url):
    parsed = urlparse(url)
    allowed_scheme = parsed.scheme == "https" or (os.environ.get("DJANGO_DEBUG", "false").lower() == "true" and parsed.scheme == "http")
    if not allowed_scheme or not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return False
    except ValueError:
        pass
    return True


def _caldav_xml(response):
    return etree.fromstring(response.content, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _discover_caldav(url, credentials, timeout):
    """Resolve a CalDAV service root to the user's first calendar collection."""
    auth = (credentials["username"], credentials["password"])
    headers = {"Depth": "0", "Content-Type": "application/xml; charset=utf-8"}
    principal_body = '''<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'''
    response = httpx.request("PROPFIND", url, content=principal_body, auth=auth, headers=headers, follow_redirects=True, timeout=timeout)
    if response.status_code not in (200, 207):
        raise RuntimeError("Calendar credentials or server URL were rejected.")
    root = _caldav_xml(response)
    principal_hrefs = root.xpath("//*[local-name()='current-user-principal']/*[local-name()='href']/text()")
    principal_url = urljoin(str(response.url), principal_hrefs[0]) if principal_hrefs else str(response.url)

    home_body = '''<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-home-set/></d:prop></d:propfind>'''
    response = httpx.request("PROPFIND", principal_url, content=home_body, auth=auth, headers=headers, follow_redirects=True, timeout=timeout)
    if response.status_code not in (200, 207):
        raise RuntimeError("Could not discover the calendar home collection.")
    root = _caldav_xml(response)
    home_hrefs = root.xpath("//*[local-name()='calendar-home-set']/*[local-name()='href']/text()")
    home_url = urljoin(str(response.url), home_hrefs[0]) if home_hrefs else principal_url

    list_body = '''<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>'''
    response = httpx.request("PROPFIND", home_url, content=list_body, auth=auth,
                             headers={**headers, "Depth": "1"}, follow_redirects=True, timeout=timeout)
    if response.status_code not in (200, 207):
        raise RuntimeError("Could not list calendars from this account.")
    root = _caldav_xml(response)
    for item in root.xpath("//*[local-name()='response']"):
        if item.xpath(".//*[local-name()='resourcetype']/*[local-name()='calendar']"):
            hrefs = item.xpath("./*[local-name()='href']/text()")
            if hrefs:
                discovered = urljoin(str(response.url), hrefs[0])
                if _safe_url(discovered):
                    return discovered
    raise RuntimeError("No readable calendar collection was found for this account.")


@csrf_exempt
# Calendar credentials are the learner's own; only they may attach them. Staff
# and admin are refused (403), not given access as they are on most endpoints.
@learner_self_only(kwarg="learner_id")
def credential_connect(request, kind, learner_id, provider):
    if request.method != "POST" or provider not in {"icloud", "caldav", "ics"}:
        return _error("Unsupported calendar provider.", 400)
    if not _learner(kind, learner_id):
        return _error("Learner not found.", 404)
    try:
        data = json.loads(request.body or b"{}")
        url = str(data.get("url") or "").strip()
        if provider == "icloud":
            url = url or os.environ.get("APPLE_CALDAV_SERVER", "https://caldav.icloud.com")
        if not _safe_url(url):
            return _error("A secure HTTPS calendar URL is required.")
        timeout = max(1, int(os.environ.get("CALENDAR_REQUEST_TIMEOUT_MS", "10000"))) / 1000
        credentials = {"username": str(data.get("username") or "").strip(), "password": str(data.get("password") or "")}
        if provider == "ics":
            response = httpx.get(url, follow_redirects=True, timeout=timeout)
            response.raise_for_status()
            max_size = int(os.environ.get("ICS_MAX_FILE_SIZE_BYTES", "5000000"))
            if len(response.content) > max_size or "BEGIN:VCALENDAR" not in response.text:
                return _error("The URL did not return a valid ICS calendar.")
            credentials = {}
        else:
            if not credentials["username"] or not credentials["password"]:
                return _error("Calendar username and app-specific password are required.")
            url = _discover_caldav(url, credentials, timeout)
        _save(kind, learner_id, provider, credentials, credentials.get("username", ""), url)
        return JsonResponse({"connection": _public(_row(kind, learner_id, provider))}, status=201)
    except (json.JSONDecodeError, httpx.HTTPError, RuntimeError) as exc:
        return _error(str(exc), 400)


@csrf_exempt
# Only the learner may remove their own calendar connection; staff/admin 403.
@learner_self_only(kwarg="learner_id")
def disconnect(request, kind, learner_id, provider):
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    with _db().cursor() as cursor:
        cursor.execute('''DELETE FROM "Learner"."calendar_connections"
                          WHERE learner_kind = %s AND learner_id = %s AND provider = %s''',
                       [kind, learner_id, provider])
    return JsonResponse({"disconnected": True})


def _parse_dt(value, timezone_name=None):
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
    clean = value.replace("Z", "+00:00")
    for fmt in (None, "%Y%m%dT%H%M%S%z", "%Y%m%dT%H%M%S"):
        try:
            parsed = datetime.fromisoformat(clean) if fmt is None else datetime.strptime(clean, fmt)
            if parsed.tzinfo:
                return parsed
            try:
                tz = ZoneInfo(timezone_name) if timezone_name else timezone.utc
            except ZoneInfoNotFoundError:
                tz = timezone.utc
            return parsed.replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def _ics_busy(text):
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    busy = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S):
        start_match = re.search(r"^DTSTART([^:]*):(.+)$", block, re.M)
        end_match = re.search(r"^DTEND([^:]*):(.+)$", block, re.M)
        if start_match and end_match:
            start_tz = re.search(r"TZID=([^;:]+)", start_match.group(1))
            end_tz = re.search(r"TZID=([^;:]+)", end_match.group(1))
            start = _parse_dt(start_match.group(2), start_tz.group(1) if start_tz else None)
            end = _parse_dt(end_match.group(2), end_tz.group(1) if end_tz else None)
            if start and end:
                busy.append({"start": start.isoformat(), "end": end.isoformat()})
    return busy


def _refresh_token(provider, row):
    tokens = row["credentials"]
    refresh = tokens.get("refresh_token")
    if not refresh:
        return tokens.get("access_token", "")
    if provider == "google":
        url = "https://oauth2.googleapis.com/token"
        body = {"client_id": os.environ.get("GOOGLE_CLIENT_ID"), "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "refresh_token": refresh, "grant_type": "refresh_token"}
    else:
        tenant = _microsoft_tenant()
        url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        body = {"client_id": os.environ.get("MICROSOFT_CLIENT_ID"), "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET"),
                "refresh_token": refresh, "scope": "offline_access User.Read Calendars.ReadBasic", "grant_type": "refresh_token"}
    response = httpx.post(url, data=body, timeout=10)
    response.raise_for_status()
    fresh = response.json()
    fresh["refresh_token"] = fresh.get("refresh_token") or refresh
    _save(row["kind"], row["learnerId"], provider, fresh, row["accountEmail"], row["calendarUrl"])
    return fresh["access_token"]


def _provider_busy(row, start, end):
    provider = row["provider"]
    timeout = max(1, int(os.environ.get("CALENDAR_REQUEST_TIMEOUT_MS", "10000"))) / 1000
    if provider in OAUTH_PROVIDERS:
        token = _refresh_token(provider, row)
        headers = {"Authorization": f"Bearer {token}"}
        if provider == "google":
            response = httpx.post("https://www.googleapis.com/calendar/v3/freeBusy", headers=headers,
                                  json={"timeMin": start, "timeMax": end, "items": [{"id": "primary"}]}, timeout=timeout)
            response.raise_for_status()
            return response.json().get("calendars", {}).get("primary", {}).get("busy", [])
        response = httpx.get("https://graph.microsoft.com/v1.0/me/calendarView",
                             params={"startDateTime": start, "endDateTime": end, "$select": "start,end,showAs"},
                             headers={**headers, "Prefer": 'outlook.timezone="UTC"'}, timeout=timeout)
        response.raise_for_status()
        return [{"start": item["start"]["dateTime"] + "Z", "end": item["end"]["dateTime"] + "Z"}
                for item in response.json().get("value", []) if item.get("showAs") != "free"]
    if provider == "ics":
        response = httpx.get(row["calendarUrl"], follow_redirects=True, timeout=timeout)
    else:
        creds = row["credentials"]
        body = f'''<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{start.replace('-', '').replace(':', '')[:15]}Z" end="{end.replace('-', '').replace(':', '')[:15]}Z"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'''
        response = httpx.request("REPORT", row["calendarUrl"], content=body, auth=(creds["username"], creds["password"]),
                                 headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"}, timeout=timeout)
    response.raise_for_status()
    if provider == "ics":
        return _ics_busy(response.text)
    try:
        root = etree.fromstring(response.content, parser=etree.XMLParser(resolve_entities=False, no_network=True))
        calendar_text = "\n".join(node.text or "" for node in root.xpath("//*[local-name()='calendar-data']"))
    except etree.XMLSyntaxError:
        calendar_text = response.text
    return _ics_busy(calendar_text)


def _as_utc(value):
    """Parse a provider datetime and normalise it for safe database comparisons."""
    parsed = _parse_dt(str(value or ""))
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _replace_busy_slots(connection_id, provider, range_start, range_end, busy_slots):
    """Replace the cached free/busy result for one connection and time window."""
    start_dt, end_dt = _as_utc(range_start), _as_utc(range_end)
    if not start_dt or not end_dt or end_dt <= start_dt:
        raise ValueError("Invalid busy-slot sync range.")

    rows = []
    for slot in busy_slots:
        slot_start = _as_utc(slot.get("start"))
        slot_end = _as_utc(slot.get("end"))
        if not slot_start or not slot_end or slot_end <= slot_start:
            continue
        if slot_start >= end_dt or slot_end <= start_dt:
            continue
        source_hash = hashlib.sha256(
            f"{provider}|{slot_start.isoformat()}|{slot_end.isoformat()}".encode()
        ).hexdigest()
        rows.append((connection_id, slot_start, slot_end, source_hash))

    with transaction.atomic(using="enrolment"):
        with _db().cursor() as cursor:
            cursor.execute(
                '''DELETE FROM "Learner"."calendar_busy_slots"
                   WHERE connection_id = %s AND starts_at < %s AND ends_at > %s''',
                [connection_id, end_dt, start_dt],
            )
            if rows:
                cursor.executemany(
                    '''INSERT INTO "Learner"."calendar_busy_slots"
                         (connection_id, starts_at, ends_at, busy_status, source_hash, synced_at)
                       VALUES (%s, %s, %s, 'busy', %s, NOW())
                       ON CONFLICT (connection_id, source_hash) DO UPDATE SET
                         starts_at = EXCLUDED.starts_at,
                         ends_at = EXCLUDED.ends_at,
                         busy_status = 'busy',
                         synced_at = NOW()''',
                    rows,
                )


def _sync_connection_busy(row, start, end):
    slots = _provider_busy(row, start, end)
    _replace_busy_slots(row["id"], row["provider"], start, end, slots)
    with _db().cursor() as cursor:
        cursor.execute(
            '''UPDATE "Learner"."calendar_connections"
               SET last_sync_at = NOW(), updated_at = NOW() WHERE id = %s''',
            [row["id"]],
        )
    return slots


def sync_learner_busy_slots(kind, learner_id, start, end):
    """Refresh every connected provider for a learner and return privacy-safe slots."""
    with _db().cursor() as cursor:
        cursor.execute(
            '''SELECT provider FROM "Learner"."calendar_connections"
               WHERE learner_kind = %s AND learner_id = %s AND status = 'connected' ''',
            [kind, learner_id],
        )
        providers = [item[0] for item in cursor.fetchall()]

    busy, errors = [], []
    for provider in providers:
        try:
            row = _row(kind, learner_id, provider)
            row.update({"kind": kind, "learnerId": learner_id})
            slots = _sync_connection_busy(row, start, end)
            busy.extend({**slot, "provider": provider} for slot in slots)
        except Exception as exc:
            errors.append({"provider": provider, "message": str(exc)[:160]})
    return busy, errors, providers


def cached_learner_busy_slots(kind, learner_id, start, end):
    """Read cached busy intervals without exposing private event metadata."""
    start_dt, end_dt = _as_utc(start), _as_utc(end)
    if not start_dt or not end_dt or end_dt <= start_dt:
        raise ValueError("Invalid busy-slot range.")
    with _db().cursor() as cursor:
        cursor.execute(
            '''SELECT slot.id, slot.starts_at, slot.ends_at, slot.busy_status,
                      connection.provider, slot.synced_at
               FROM "Learner"."calendar_busy_slots" slot
               JOIN "Learner"."calendar_connections" connection
                 ON connection.id = slot.connection_id
               WHERE connection.learner_kind = %s
                 AND connection.learner_id = %s
                 AND connection.status = 'connected'
                 AND slot.starts_at < %s AND slot.ends_at > %s
               ORDER BY slot.starts_at, slot.ends_at''',
            [kind, learner_id, end_dt, start_dt],
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "start": row[1].isoformat(),
            "end": row[2].isoformat(),
            "status": row[3],
            "provider": row[4],
            "syncedAt": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]


def _cached_booking_conflicts(
    kind, learner_id, start_dt, end_dt, provider=None, exclude_start_dt=None, exclude_end_dt=None
):
    """Return True when cached privacy-safe busy slots overlap a proposed booking."""
    try:
        provider_clause = ""
        exclude_clause = ""
        params = [kind, learner_id, end_dt, start_dt]
        if exclude_start_dt is not None and exclude_end_dt is not None:
            exclude_clause = "AND NOT (slot.starts_at = %s AND slot.ends_at = %s)"
            params.extend([exclude_start_dt, exclude_end_dt])
        if provider:
            provider_clause = "AND connection.provider = %s"
            params.append(provider)
        with _db().cursor() as cursor:
            cursor.execute(
                f'''SELECT 1
                   FROM "Learner"."calendar_busy_slots" slot
                   JOIN "Learner"."calendar_connections" connection
                     ON connection.id = slot.connection_id
                   WHERE connection.learner_kind = %s
                     AND connection.learner_id = %s
                      AND connection.status = 'connected'
                      AND slot.starts_at < %s AND slot.ends_at > %s
                      {exclude_clause}
                      {provider_clause}
                   LIMIT 1''',
                params,
            )
            return cursor.fetchone() is not None
    except Exception:
        return False


# Reads (and refreshes) the owner's private free/busy — owner only, not staff (A5).
@learner_self_only(kwarg="learner_id")
def availability(request, kind, learner_id):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    start, end = request.GET.get("start", ""), request.GET.get("end", "")
    try:
        start_dt, end_dt = datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return _error("start and end must be ISO-8601 datetimes.")
    max_days = int(os.environ.get("MAX_AVAILABILITY_RANGE_DAYS", "31"))
    if end_dt <= start_dt or (end_dt - start_dt).days > max_days:
        return _error(f"Availability range must be between 0 and {max_days} days.")
    busy, errors, providers = sync_learner_busy_slots(kind, learner_id, start, end)
    return JsonResponse({"busy": busy, "errors": errors, "connectedProviders": providers})


def booking_conflicts(
    kind,
    learner_id,
    scheduled_date,
    scheduled_time,
    duration_minutes,
    timezone_offset_minutes=0,
    *,
    exclude_scheduled_date=None,
    exclude_scheduled_time=None,
    exclude_duration_minutes=None,
    business_time_zone=None,
):
    """Return True when a proposed learner booking overlaps a connected calendar."""
    def instant(day, clock):
        if business_time_zone:
            return datetime.combine(day, clock, ZoneInfo(business_time_zone)).astimezone(timezone.utc)
        return datetime.combine(day, clock, tzinfo=timezone.utc) + timedelta(minutes=timezone_offset_minutes)

    start_dt = instant(scheduled_date, scheduled_time)
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    exclude_start_dt = exclude_end_dt = None
    if exclude_scheduled_date and exclude_scheduled_time and exclude_duration_minutes:
        exclude_start_dt = instant(exclude_scheduled_date, exclude_scheduled_time)
        exclude_end_dt = exclude_start_dt + timedelta(minutes=exclude_duration_minutes)
    start, end = start_dt.isoformat().replace("+00:00", "Z"), end_dt.isoformat().replace("+00:00", "Z")
    try:
        with _db().cursor() as cursor:
            cursor.execute('''SELECT provider FROM "Learner"."calendar_connections"
                              WHERE learner_kind = %s AND learner_id = %s AND status = 'connected' ''', [kind, learner_id])
            providers = [item[0] for item in cursor.fetchall()]
    except Exception:
        return _cached_booking_conflicts(
            kind, learner_id, start_dt, end_dt,
            exclude_start_dt=exclude_start_dt, exclude_end_dt=exclude_end_dt,
        )
    for provider in providers:
        try:
            row = _row(kind, learner_id, provider)
            row.update({"kind": kind, "learnerId": learner_id})
            for slot in _sync_connection_busy(row, start, end):
                slot_start, slot_end = _parse_dt(slot.get("start", "")), _parse_dt(slot.get("end", ""))
                if slot_start == exclude_start_dt and slot_end == exclude_end_dt:
                    continue
                if slot_start and slot_end and start_dt < slot_end and end_dt > slot_start:
                    return True
        except Exception:
            # A transient provider failure should not make the whole LMS unusable;
            # cached busy slots still block known conflicts without exposing details.
            if _cached_booking_conflicts(
                kind, learner_id, start_dt, end_dt, provider,
                exclude_start_dt=exclude_start_dt, exclude_end_dt=exclude_end_dt,
            ):
                return True
            continue
    return False
