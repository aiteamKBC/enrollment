"""Short-lived, delegated access to an imported meeting's original calendar.

Reuses the registered Microsoft SSO callback, but never signs somebody into the
LMS or changes a meeting. A connection belongs to one existing staff session and
one imported series. Only an encrypted access token is cached, for at most 15
minutes; refresh tokens are neither requested nor retained.
"""
import base64
import hashlib
import html
import json
import os
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from login.permissions import require_role
from login.security import hash_token, tokens_equal
from login.sessions import authenticate_request

from .teams_imported_participants import GUID, join_context, text


STATE_PREFIX = 'teams-calendar:'
STATE_SALT = 'imported-teams-calendar-connect-v1'
NONCE_COOKIE = 'kbc_teams_calendar_nonce'
STATE_TTL = 600
ACCESS_TTL = 900
AUTHORITY = 'https://login.microsoftonline.com'
SCOPES = {'group': 'Group.ReadWrite.All', 'user': 'Calendars.ReadWrite'}


class CalendarConnectionError(RuntimeError):
    def __init__(self, message, code='calendar_connection_unavailable', status=409):
        super().__init__(message)
        self.code, self.status = code, status


def _error(exc):
    return JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)


def _cipher():
    raw = text(os.environ.get('CREDENTIAL_ENCRYPTION_KEY'))
    if not raw:
        raise CalendarConnectionError('Calendar connection encryption is not configured.', status=503)
    key = hashlib.sha256(('imported-teams-calendar-v1\0' + raw).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _seal(value):
    return _cipher().encrypt(json.dumps(value, separators=(',', ':')).encode()).decode()


def _unseal(value):
    try:
        result = json.loads(_cipher().decrypt(value.encode(), ttl=ACCESS_TTL + STATE_TTL).decode())
        return result if isinstance(result, dict) else None
    except (InvalidToken, AttributeError, TypeError, ValueError):
        return None


def _actor_binding(request):
    account = authenticate_request(request)
    session = getattr(request, 'login_session', None)
    if not account or account.role not in {'admin', 'staff'} or not session:
        raise CalendarConnectionError('Sign in to the same staff session before connecting this calendar.',
                                      'calendar_connection_session', 403)
    return {'account': str(account.pk), 'session': str(session.pk)}


def _series(live_id):
    from . import views as v
    rows = v.authoring_fetch_all(v.LIVE_SESSIONS_TABLE, 'id = %s', [live_id], ensure_tables=False)
    if not rows:
        raise CalendarConnectionError('Imported meeting not found.', 'not_found', 404)
    item = rows[0]
    if text(item.get('meeting_type')) != 'linked-external' or text(item.get('status')) != 'active':
        raise CalendarConnectionError('Connect only an active imported Teams series.', 'not_imported')
    return item


def _series_binding(series):
    # The same LMS row must not silently lend its credential to a replacement
    # meeting. Oid is only an identity candidate, never proof of calendar ownership.
    value = [text(series.get('id')), text(series.get('join_url')),
             text(series.get('organizer_email')).casefold()]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _origin(value):
    try:
        url = urlsplit(text(value))
        local = url.hostname in {'localhost', '127.0.0.1', '::1'}
        if (url.username or url.password or not url.hostname or
                (url.scheme != 'https' and not (settings.DEBUG and local and url.scheme == 'http'))):
            return ''
        # Accessing port also rejects invalid/out-of-range ports.
        _ = url.port
        return f'{url.scheme}://{url.netloc}'.lower()
    except ValueError:
        return ''


def _frontend_origin(request):
    from login.invitations import frontend_base_url
    own = _origin(request.build_absolute_uri('/'))
    supplied = text(request.headers.get('Origin'))
    requested = _origin(supplied) if supplied else own
    allowed = {own, _origin(frontend_base_url()), *(_origin(item) for item in settings.CSRF_TRUSTED_ORIGINS)}
    allowed.discard('')
    if not requested or requested not in allowed or (supplied and supplied.rstrip('/').lower() != requested):
        raise CalendarConnectionError('Open the calendar connection from the LMS.', 'calendar_connection_origin', 403)
    return requested


def _configuration(request, series, *, browser_origin=None):
    from coach_api.views import get_graph_settings
    from login.microsoft_sso import config, missing_settings
    value = config()
    if missing_settings():
        raise CalendarConnectionError('Microsoft sign-in must be configured before connecting the original calendar.', status=503)
    _cipher()
    tenant = text(get_graph_settings().get('tenant_id')).lower()
    saved_tenant = join_context(series.get('join_url')).get('Tid', '').lower()
    if not GUID.fullmatch(tenant) or not saved_tenant or tenant != saved_tenant:
        raise CalendarConnectionError('The imported link must belong to the configured Microsoft tenant before it can be connected.')
    callback = urlsplit(value['callback'])
    callback_origin = _origin(value['callback'])
    if (not callback_origin or callback.query or callback.fragment or
            not callback.path.endswith('/login_api/microsoft/callback/')):
        raise CalendarConnectionError('The registered Microsoft sign-in callback is not valid for calendar connection.', status=503)
    # Compare the browser's cookie host, not the upstream HTTP Host. The local
    # Vite proxy rewrites localhost:3000 to 127.0.0.1:8000 on both API routes.
    # Connect supplies an allowlisted Origin; callback/access supply only the
    # origin kept in their encrypted, session-bound record. A disconnected GET
    # has no browser Origin and only reports configuration readiness here.
    if browser_origin is not None:
        origin = _origin(browser_origin)
        if not origin or origin != browser_origin:
            raise CalendarConnectionError('The calendar browser origin could not be verified. Reconnect from the LMS.', 'calendar_connection_origin', 403)
        if callback.hostname != urlsplit(origin).hostname:
            raise CalendarConnectionError('Open the LMS on the Microsoft sign-in callback host to connect this calendar. The current browser host cannot share its staff session with that callback.', status=503)
    return {**value, 'tenant': tenant, 'callbackOrigin': callback_origin}


def _access_key(binding, live_id):
    key = json.dumps([binding['account'], binding['session'], str(live_id)])
    return 'teams-calendar:access:' + hashlib.sha256(key.encode()).hexdigest()


def delegated_calendar_access(request, live_session_id):
    """Return a server-only credential dict, or None; never return it as JSON.

    actorId is the verified Microsoft user object ID. lmsAccountId identifies
    the existing LMS actor. All callers still need to prove original calendar
    ownership and the exact master/link before permitting a calendar write.
    """
    binding = _actor_binding(request)
    encrypted = cache.get(_access_key(binding, live_session_id))
    if not encrypted:
        return None
    record = _unseal(encrypted)
    if (not record or record.get('expires', 0) <= time.time() + 30
            or record.get('binding') != binding or not record.get('origin')):
        return None
    series = _series(live_session_id)
    cfg = _configuration(request, series, browser_origin=record['origin'])
    if (record.get('seriesBinding') != _series_binding(series) or
            record.get('tenant') != cfg['tenant'] or record.get('clientId') != cfg['client_id']):
        return None
    return {
        'accessToken': record['accessToken'], 'actorId': record['actorId'],
        'tenantId': record['tenant'],
        'lmsAccountId': binding['account'], 'scopes': record['scopes'],
        'expiresAt': datetime.fromtimestamp(record['expires'], timezone.utc).isoformat(),
        'calendarKind': record['calendarKind'],
        'accountName': record['accountName'], 'accountEmail': record['accountEmail'],
    }


@never_cache
@require_role('admin', 'staff')
@require_GET
def imported_calendar_connection(request, live_session_id):
    try:
        series = _series(live_session_id)
        cfg = _configuration(request, series)
        access = delegated_calendar_access(request, live_session_id)
        public = {name: access[name] for name in
                  ('expiresAt', 'calendarKind', 'accountName', 'accountEmail')} if access else {}
        return JsonResponse({'configured': True, 'connected': bool(access),
                             'callbackOrigin': cfg['callbackOrigin'], **public,
                             'message': ('Connected for this staff session. Preview verifies original calendar access.' if access else
                                         'Connect the Microsoft account that can manage the original calendar.')})
    except CalendarConnectionError as exc:
        if exc.status in {403, 404}:
            return _error(exc)
        return JsonResponse({'configured': False, 'connected': False, 'message': str(exc)})
    except DatabaseError:
        return JsonResponse({'error': 'The calendar connection could not be read. Retry.'}, status=503)


@never_cache
@require_role('admin', 'staff')
@require_POST
def imported_calendar_connect(request, live_session_id):
    """Begin interactive consent. Django CSRF protection remains enabled."""
    try:
        if len(request.body) > 4096:
            raise ValueError()
        payload = json.loads(request.body or b'{}')
        kind = payload.get('calendarKind', 'group') if isinstance(payload, dict) else ''
        if kind not in SCOPES:
            raise ValueError()
        binding = _actor_binding(request)
        series = _series(live_session_id)
        origin = _frontend_origin(request)
        cfg = _configuration(request, series, browser_origin=origin)
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        nonce_hash = hash_token(nonce)
        scope = f'openid User.Read {SCOPES[kind]}'
        pending = {'binding': binding, 'liveId': str(live_session_id), 'seriesBinding': _series_binding(series),
                   'origin': origin, 'tenant': cfg['tenant'], 'clientId': cfg['client_id'],
                   'callback': cfg['callback'], 'calendarKind': kind, 'scope': scope,
                   'verifier': verifier, 'oidcNonce': secrets.token_urlsafe(32)}
        encrypted = _seal(pending)
        cache.set('teams-calendar:pending:' + nonce_hash, encrypted, STATE_TTL)
        if cache.get('teams-calendar:pending:' + nonce_hash) != encrypted:
            raise CalendarConnectionError('The temporary calendar connection could not be stored. Retry.', status=503)
        state = STATE_PREFIX + signing.dumps({'n': nonce_hash}, salt=STATE_SALT)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        query = urlencode({'client_id': cfg['client_id'], 'redirect_uri': cfg['callback'],
                           'response_type': 'code', 'response_mode': 'query', 'scope': scope,
                           'state': state, 'nonce': pending['oidcNonce'], 'prompt': 'select_account',
                           'code_challenge': challenge, 'code_challenge_method': 'S256'})
        response = JsonResponse({'authorizationUrl': f'{AUTHORITY}/{cfg["tenant"]}/oauth2/v2.0/authorize?{query}',
                                 'callbackOrigin': cfg['callbackOrigin']})
        response.set_cookie(NONCE_COOKIE, nonce, max_age=STATE_TTL, httponly=True, path='/',
                            secure=getattr(settings, 'SESSION_COOKIE_SECURE', not settings.DEBUG), samesite='Lax')
        return response
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Choose a user or group calendar connection.'}, status=400)
    except CalendarConnectionError as exc:
        return _error(exc)
    except DatabaseError:
        return JsonResponse({'error': 'The imported meeting could not be read. Retry.'}, status=503)


def _decode64(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def _verified_id_claims(token, cfg, nonce, client):
    """Verify the tenant-issued OIDC identity; no unverified JWT claim is trusted."""
    try:
        if not isinstance(token, str) or len(token) > 32768:
            raise ValueError()
        header_part, payload_part, signature_part = token.split('.')
        header, claims = json.loads(_decode64(header_part)), json.loads(_decode64(payload_part))
        if not isinstance(header, dict) or not isinstance(claims, dict) or header.get('alg') != 'RS256' or header.get('crit'):
            raise ValueError()
        response = client.get(f'{AUTHORITY}/{cfg["tenant"]}/discovery/v2.0/keys')
        response.raise_for_status()
        keys = response.json().get('keys', [])
        matches = [key for key in keys if isinstance(key, dict) and key.get('kid') == header.get('kid')
                   and key.get('kty') == 'RSA' and key.get('use', 'sig') == 'sig'
                   and key.get('alg', 'RS256') == 'RS256']
        if len(matches) != 1 or not header.get('kid'):
            raise ValueError()
        key = matches[0]
        public = rsa.RSAPublicNumbers(int.from_bytes(_decode64(key['e']), 'big'),
                                     int.from_bytes(_decode64(key['n']), 'big')).public_key()
        public.verify(_decode64(signature_part), f'{header_part}.{payload_part}'.encode(),
                      padding.PKCS1v15(), hashes.SHA256())
        now = time.time()
        if (claims.get('aud') != cfg['client_id'] or
                text(claims.get('tid')).lower() != cfg['tenant'] or
                claims.get('iss') != f'{AUTHORITY}/{cfg["tenant"]}/v2.0' or
                not tokens_equal(text(claims.get('nonce')), nonce) or
                not GUID.fullmatch(text(claims.get('oid'))) or
                float(claims.get('exp', 0)) <= now + 60 or
                float(claims.get('nbf', 0)) > now + 120 or
                float(claims.get('iat', 0)) > now + 120):
            raise ValueError()
        return claims
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise CalendarConnectionError('Microsoft account identity could not be verified. Reconnect the calendar.') from exc


def _exchange(code, cfg, pending):
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        response = client.post(f'{AUTHORITY}/{cfg["tenant"]}/oauth2/v2.0/token', data={
            'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
            'redirect_uri': cfg['callback'], 'code': code, 'grant_type': 'authorization_code',
            'scope': pending['scope'], 'code_verifier': pending['verifier'],
        })
        if response.status_code != 200:
            raise CalendarConnectionError('Microsoft did not authorize the calendar connection. If consent is required, ask your Microsoft administrator to approve the requested delegated permission.')
        tokens = response.json()
        scopes = text(tokens.get('scope')).split()
        required = {'User.Read', SCOPES[pending['calendarKind']]}
        if not required.issubset(scopes) or tokens.get('token_type', '').lower() != 'bearer':
            raise CalendarConnectionError('Microsoft did not grant the requested calendar permission. No calendar was changed.')
        claims = _verified_id_claims(tokens.get('id_token'), cfg, pending['oidcNonce'], client)
        token = text(tokens.get('access_token'))
        if not token:
            raise CalendarConnectionError('Microsoft did not return calendar access.')
        response = client.get('https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName',
                              headers={'Authorization': f'Bearer {token}'})
        response.raise_for_status()
        person = response.json()
        if text(person.get('id')).lower() != text(claims.get('oid')).lower():
            raise CalendarConnectionError('Microsoft returned an unexpected account identity.')
        ttl = min(ACCESS_TTL, int(tokens.get('expires_in', 0)) - 60, int(float(claims['exp']) - time.time()) - 60)
        if ttl <= 60:
            raise CalendarConnectionError('Microsoft calendar access expired. Reconnect the calendar.')
        return {'accessToken': token, 'actorId': text(person['id']), 'scopes': sorted(required),
                'calendarKind': pending['calendarKind'], 'expires': int(time.time()) + ttl,
                'accountName': text(person.get('displayName')),
                'accountEmail': text(person.get('mail') or person.get('userPrincipalName'))}, ttl


def _completion(ok, message, *, origin='', live_id=''):
    payload = {'type': 'lms:teams-calendar-connected', 'liveSessionId': live_id, 'ok': bool(ok)}
    if not ok:
        payload['error'] = message
    script = ''
    script_nonce = secrets.token_urlsafe(24)
    if origin:
        safe_payload = json.dumps(payload).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
        script = (f'<script nonce="{script_nonce}">if(window.opener){{window.opener.postMessage('
                  f'{safe_payload},{json.dumps(origin)});window.close();}}</script>')
    response = HttpResponse('<!doctype html><meta charset="utf-8"><title>Calendar connection</title>'
                            '<p>' + html.escape(message) + '</p><p>You can close this window.</p>' + script)
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Frame-Options'] = 'DENY'
    response['Content-Security-Policy'] = f"default-src 'none'; script-src 'nonce-{script_nonce}'; frame-ancestors 'none'; base-uri 'none'"
    response.delete_cookie(NONCE_COOKIE, path='/')
    return response


@never_cache
@require_GET
def imported_calendar_callback(request):
    """Dispatch target for only our prefixed state at the existing SSO callback."""
    origin, live_id = '', ''
    try:
        state = text(request.GET.get('state'))
        if not state.startswith(STATE_PREFIX):
            raise CalendarConnectionError('The calendar connection could not be verified.')
        signed = signing.loads(state[len(STATE_PREFIX):], salt=STATE_SALT, max_age=STATE_TTL)
        nonce_hash = text(signed.get('n'))
        nonce = text(request.COOKIES.get(NONCE_COOKIE))
        if not nonce or not tokens_equal(hash_token(nonce), nonce_hash):
            raise CalendarConnectionError('Reconnect the calendar from the same browser window.')
        key = 'teams-calendar:pending:' + nonce_hash
        pending = _unseal(cache.get(key))
        if not pending or pending.get('binding') != _actor_binding(request):
            raise CalendarConnectionError('This calendar connection expired or belongs to another staff session.')
        origin, live_id = pending['origin'], pending['liveId']
        series = _series(live_id)
        cfg = _configuration(request, series, browser_origin=origin)
        if (pending['seriesBinding'] != _series_binding(series) or
                pending['tenant'] != cfg['tenant'] or pending['clientId'] != cfg['client_id'] or
                pending['callback'] != cfg['callback']):
            raise CalendarConnectionError('The imported meeting or Microsoft connection changed. Start again.')
        if not cache.add('teams-calendar:used:' + nonce_hash, True, STATE_TTL):
            raise CalendarConnectionError('This calendar connection has already been used. Start again.')
        cache.delete(key)
        if request.GET.get('error'):
            raise CalendarConnectionError('Microsoft calendar connection was cancelled or consent was not granted. No calendar was changed.')
        code = text(request.GET.get('code'))
        if not code:
            raise CalendarConnectionError('Microsoft did not return a calendar authorization code.')
        access, ttl = _exchange(code, cfg, pending)
        access.update(binding=pending['binding'], seriesBinding=pending['seriesBinding'],
                      tenant=cfg['tenant'], clientId=cfg['client_id'], origin=origin)
        encrypted = _seal(access)
        access_key = _access_key(pending['binding'], live_id)
        cache.set(access_key, encrypted, ttl)
        if cache.get(access_key) != encrypted:
            raise CalendarConnectionError('The temporary connection could not be stored. Retry.')
        return _completion(True, 'Microsoft calendar connected for this staff session. No meeting was changed.', origin=origin, live_id=live_id)
    except CalendarConnectionError as exc:
        message = str(exc)
    except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError, httpx.HTTPError, DatabaseError):
        message = 'The Microsoft calendar connection could not be completed. Retry from the LMS; no calendar was changed.'
    return _completion(False, message, origin=origin, live_id=live_id)
