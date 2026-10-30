"""Sign in with Microsoft — OAuth authorization-code flow against Entra ID.

    GET /login_api/microsoft/start/     -> {"authorizationUrl": ...} (XHR)
    GET /login_api/microsoft/callback/  -> sets the session cookie, redirects

**What this does and does not do.** It authenticates *who the person is* with
Microsoft, then looks that address up in ``auth."Login_accounts"``. An address
Microsoft vouches for that has no active account here is refused. This flow
never mints an account: the login table stays the single register of who may in,
so removing somebody from it removes them from the platform whatever their
tenant still says about them.

Everything after the lookup is the ordinary sign-in path — ``register_success``,
``issue_session``, ``set_session_cookie`` — so an SSO session is revocable,
expires and audits exactly like a password one. The audit row is written with
``reason="microsoft_sso"`` so the two can be told apart after the fact.

**Why the email and not the ID token.** The address comes from a Graph ``/me``
call made with the freshly-issued access token, the same way
``learner_api.calendar_connections._oauth_identity`` reads it. Trusting a token
we just received over TLS from the token endpoint, in exchange for a code we
just minted, needs no JWKS fetch, no signature validation and no clock-skew
handling — three things that are easy to get subtly and silently wrong.

**CSRF on the callback.** The callback is a top-level browser redirect, so the
``X-Requested-With`` header the rest of the login API requires cannot be set on
it. The defence is instead a signed, salted, ten-minute ``state`` minted by
``start``, *bound to the browser that asked for it* by a paired nonce cookie —
so neither a forged callback nor a genuine one lifted from somebody else's
browser can sign anybody in. See the note above ``_nonce_cookie_kwargs``.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlencode

import httpx
from django.conf import settings as django_settings
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET

from . import identity
from .android_download_access import staff_download_allowed
from .invitations import frontend_base_url, record
from .models import EVENT_LOGIN
from .security import (
    client_ip,
    generate_token,
    hash_token,
    is_locked,
    normalize_email,
    register_success,
    tokens_equal,
    user_agent,
)
from .sessions import issue_session, set_session_cookie

logger = logging.getLogger("login.sso")

#: Distinct from the calendar connection flow's salt, so a state minted for one
#: cannot be replayed against the other.
STATE_SALT = "login-microsoft-sso"

#: How long an authorization attempt may sit unfinished. Long enough to type a
#: password and answer an MFA prompt, short enough that a leaked URL is stale.
STATE_MAX_AGE = 600

#: Binds an in-flight sign-in to the browser that began it. See ``_set_nonce``.
NONCE_COOKIE = "kbc_sso_nonce"

#: Delegated scopes. ``User.Read`` is what lets us call /me for the address;
#: ``offline_access`` is deliberately absent — this flow wants an identity once,
#: not a refresh token to store.
SCOPE = "openid profile email User.Read"

AUTHORITY = "https://login.microsoftonline.com"
GRAPH_ME = "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"

#: Shown when Microsoft authenticated somebody the platform has no account for.
#: Deliberately says nothing about which kind of account was or was not found —
#: the caller has proved who they are, but that is no reason to describe the
#: directory to them.
NO_ACCOUNT_ERROR = (
    "That Microsoft account is not registered on this platform. "
    "Ask an administrator to invite you."
)


def _env(*names):
    """First non-empty value among ``names``, stripped of stray quoting."""
    for name in names:
        value = (os.environ.get(name) or "").strip().strip('"')
        if value:
            return value
    return ""


def config():
    """Credentials for the sign-in app registration.

    Reads dedicated ``MICROSOFT_SSO_*`` names first and falls back to the bare
    ``MICROSOFT_*`` pair the calendar integration already uses — the same
    dedicated-name-with-fallback habit as ``MICROSOFT_GRAPH_*``. The fallback
    exists so a deployment that has one delegated app registration does not need
    a second one; if the two are ever separated, only the dedicated names change
    and the calendar keeps its own.

    The callback has no fallback on purpose: ``MICROSOFT_CALLBACK_URI`` points
    at the calendar's own callback, and borrowing it would send every sign-in
    into the calendar-connection handler.

    ``tenant`` falls back to ``MICROSOFT_TENANT_ID`` — the real directory — and
    pointedly **not** to ``MICROSOFT_TENANT``, which this deployment sets to
    ``common`` on purpose so learners can attach a personal Outlook calendar.
    That is the right audience for a calendar and the wrong one for a sign-in:
    it would put personal Microsoft accounts in front of an authorize page they
    can never get past. ``organizations`` is the last resort for the same
    reason. Either way the login-table lookup is the real gate; this only
    decides who is offered the attempt.
    """
    return {
        "client_id": _env("MICROSOFT_SSO_CLIENT_ID", "MICROSOFT_CLIENT_ID"),
        "client_secret": _env("MICROSOFT_SSO_CLIENT_SECRET", "MICROSOFT_CLIENT_SECRET"),
        "tenant": _env("MICROSOFT_SSO_TENANT_ID", "MICROSOFT_TENANT_ID") or "organizations",
        "callback": _env("MICROSOFT_SSO_CALLBACK_URI"),
    }


def missing_settings():
    """Names of the settings that must be set before the button can work.

    ``/login_api/health/`` reports these, so the reason the button is hidden is
    visible without reading logs. Never returns values, only names.
    """
    settings = config()
    missing = []
    if not settings["client_id"]:
        missing.append("MICROSOFT_SSO_CLIENT_ID")
    if not settings["client_secret"]:
        missing.append("MICROSOFT_SSO_CLIENT_SECRET")
    if not settings["callback"]:
        missing.append("MICROSOFT_SSO_CALLBACK_URI")
    return missing


def is_configured():
    return not missing_settings()


def _redirect_with_error(message):
    """Send the browser back to the sign-in page carrying a reason.

    The callback is reached by redirect, so a JSON 4xx would render as a blank
    page of raw JSON. Truncated because the message can quote Microsoft.
    """
    query = urlencode({"sso_error": str(message)[:200]})
    return _clear_nonce(HttpResponseRedirect(f"{frontend_base_url()}/?{query}"))


# ---------------------------------------------------------------------------
# Binding a sign-in to the browser that started it
# ---------------------------------------------------------------------------
#
# A signed ``state`` proves *this server* minted it. It does not prove the
# browser presenting it is the one that asked for it — and those are different
# claims. Without the second, somebody with a platform account can start a
# sign-in, stop the redirect on their own machine (trivial: it is their machine)
# and hand the finished callback URL — valid code, valid unexpired state — to
# another person. That browser completes the flow and is silently signed in as
# *them*. Everything the victim then writes lands in the attacker's account.
#
# So ``start`` also sets a random nonce in an HttpOnly cookie and puts only its
# hash in the state. ``callback`` requires the two to agree. A state lifted from
# somebody else's browser now arrives without the matching cookie and is
# refused. SameSite=Lax is what makes this work: the callback is a top-level GET
# navigation, which Lax permits, while the cross-site POSTs CSRF actually needs
# stay excluded.


def _nonce_cookie_kwargs():
    """Flags for the nonce cookie — mirrors ``sessions._cookie_kwargs``.

    ``Secure`` follows the same setting as the session cookie rather than being
    hard-coded: a cookie marked Secure is not stored over plain http, which
    would break local development and, worse, do so by silently failing every
    sign-in with an expired-link message.
    """
    return {
        "max_age": STATE_MAX_AGE,
        "httponly": True,
        "secure": getattr(django_settings, "SESSION_COOKIE_SECURE", not django_settings.DEBUG),
        "samesite": getattr(django_settings, "SESSION_COOKIE_SAMESITE", "Lax"),
        "path": "/",
    }


def _set_nonce(response, nonce):
    response.set_cookie(NONCE_COOKIE, nonce, **_nonce_cookie_kwargs())
    return response


def _clear_nonce(response):
    """Retire the nonce, so one cookie can complete exactly one sign-in."""
    response.delete_cookie(NONCE_COOKIE, path="/")
    return response


def _nonce_matches(request, expected_hash):
    """Does this browser hold the nonce the state was minted against?

    Compared as hashes and in constant time, the same way session tokens are:
    the state travels through Microsoft and through the browser's history, so
    what it carries should be no more useful to an attacker than a hash.
    """
    presented = request.COOKIES.get(NONCE_COOKIE)
    if not presented or not expected_hash:
        return False
    return tokens_equal(hash_token(presented), expected_hash)


# ---------------------------------------------------------------------------
# Step 1 — hand the SPA a URL to send the browser to
# ---------------------------------------------------------------------------

@require_GET
def start(request):
    """Return the Microsoft authorization URL for the SPA to navigate to.

    Normal sign-in starts at the account's home. Explicit fixed intents have
    fixed destinations; arbitrary return paths are never accepted.
    """
    settings = config()
    if missing_settings():
        return JsonResponse(
            {"error": "Microsoft sign-in is not configured.", "code": "sso_unconfigured"},
            status=503,
        )

    # Only the hash goes into the state; the value itself stays in the cookie.
    nonce = generate_token()
    payload = {"n": hash_token(nonce)}
    if request.GET.get("download") == "android":
        payload["download"] = "android"
    elif request.GET.get("intent") == "lecture-feedback":
        payload["intent"] = "lecture-feedback"
    state = signing.dumps(payload, salt=STATE_SALT, compress=True)
    query = urlencode(
        {
            "client_id": settings["client_id"],
            "redirect_uri": settings["callback"],
            "response_type": "code",
            "response_mode": "query",
            "scope": SCOPE,
            "state": state,
            # Always show the account chooser. Without it a shared machine
            # silently reuses whichever tenant account signed in last, which on
            # a sign-in button is confusing rather than convenient.
            "prompt": "select_account",
        }
    )
    # The SPA fetches this with credentials, so the browser stores the cookie
    # from an XHR response just as it would from a navigation.
    return _set_nonce(
        JsonResponse(
            {
                "authorizationUrl": (
                    f"{AUTHORITY}/{settings['tenant']}/oauth2/v2.0/authorize?{query}"
                )
            }
        ),
        nonce,
    )


# ---------------------------------------------------------------------------
# Step 2 — Microsoft sends the browser back here
# ---------------------------------------------------------------------------

def _exchange_code(code, settings):
    """Swap the authorization code for an access token."""
    response = httpx.post(
        f"{AUTHORITY}/{settings['tenant']}/oauth2/v2.0/token",
        data={
            "code": code,
            "client_id": settings["client_id"],
            "client_secret": settings["client_secret"],
            "redirect_uri": settings["callback"],
            "scope": SCOPE,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _graph_email(access_token):
    """The signed-in person's address, preferring the mailbox over the UPN."""
    response = httpx.get(
        GRAPH_ME, headers={"Authorization": f"Bearer {access_token}"}, timeout=10
    )
    response.raise_for_status()
    data = response.json()
    return normalize_email(data.get("mail") or data.get("userPrincipalName") or "")


@require_GET
def callback(request):
    """Complete the flow: verify state, resolve the address, issue a session."""
    # Calendar consent reuses this registered redirect URI, with its own salt,
    # nonce, active staff-session check and expiry. It never issues an LMS login.
    # Ordinary SSO states continue through the unchanged sign-in path below.
    if str(request.GET.get("state", "")).startswith("teams-calendar:"):
        from curriculum_api.teams_imported_calendar_auth import imported_calendar_callback
        return imported_calendar_callback(request)
    ip = client_ip(request)
    ua = user_agent(request)
    settings = config()

    if missing_settings():
        return _redirect_with_error("Microsoft sign-in is not configured.")

    # Microsoft reports a refused or cancelled consent here rather than by not
    # calling back at all, so this is a normal outcome, not an exception.
    if request.GET.get("error"):
        return _redirect_with_error(
            request.GET.get("error_description") or request.GET["error"]
        )

    try:
        payload = signing.loads(
            request.GET.get("state", ""), salt=STATE_SALT, max_age=STATE_MAX_AGE
        )
    except signing.BadSignature:
        record(EVENT_LOGIN, succeeded=False, reason="sso_bad_state", ip=ip, user_agent=ua)
        return _redirect_with_error("That sign-in link has expired. Please try again.")

    # The state is ours, but is this the browser that asked for it? See the
    # note above _nonce_cookie_kwargs. Worded like an expiry rather than an
    # accusation: the overwhelmingly common cause is a second tab overwriting
    # the cookie, or one that sat too long, not an attack.
    if not _nonce_matches(request, payload.get("n")):
        record(EVENT_LOGIN, succeeded=False, reason="sso_state_not_bound", ip=ip, user_agent=ua)
        return _redirect_with_error(
            "That sign-in could not be verified. Please try again from this browser."
        )

    code = request.GET.get("code")
    if not code:
        return _redirect_with_error("Microsoft did not return a sign-in code.")

    try:
        email = _graph_email(_exchange_code(code, settings))
    except Exception:  # noqa: BLE001 - any failure here is a failed sign-in
        # Logged in full because it is a configuration or transport fault worth
        # diagnosing; the browser is only told that it did not work.
        logger.exception("Microsoft sign-in failed during token exchange")
        record(EVENT_LOGIN, succeeded=False, reason="sso_exchange_failed", ip=ip, user_agent=ua)
        return _redirect_with_error("Could not complete sign-in with Microsoft.")

    if not email:
        record(EVENT_LOGIN, succeeded=False, reason="sso_no_email", ip=ip, user_agent=ua)
        return _redirect_with_error("That Microsoft account has no email address.")

    # The gate. ``account_for_email`` returns only active accounts, and returns
    # None when one address exists under two subject types — the same ambiguity
    # the password path refuses, for the same reason: there is no way to tell
    # which of them was meant.
    account = identity.account_for_email(email)
    if account is None:
        record(
            EVENT_LOGIN, email=email, succeeded=False,
            reason="sso_unknown_account", ip=ip, user_agent=ua,
        )
        return _redirect_with_error(NO_ACCOUNT_ERROR)

    # Honoured so a lockout means the same thing whichever door is tried;
    # otherwise the lockout on the password form is bypassable by anybody whose
    # tenant account still works.
    if is_locked(account):
        record(
            EVENT_LOGIN, email=email, account_id=account.id,
            succeeded=False, reason="sso_locked", ip=ip, user_agent=ua,
        )
        return _redirect_with_error(
            "This account is temporarily locked after too many failed attempts. "
            "Try again later."
        )

    # Note what is *not* checked: ``has_password``. Somebody invited but never
    # onboarded can sign in this way and never set one — their tenant account is
    # the credential. The password form still refuses them.
    register_success(account, ip=ip)
    token, _session, ttl = issue_session(account, ip=ip, user_agent=ua)
    record(
        EVENT_LOGIN, email=email, account_id=account.id,
        succeeded=True, reason="microsoft_sso", ip=ip, user_agent=ua,
    )

    # Student Home verifies the previous-record signature before showing the
    # landing page. The SPA's signed-in root resolves every other account's home
    # from its current access grant. Ignore `next` in older in-flight states too,
    # so all sign-in methods have the same landing behaviour.
    destination = "/learner/home" if account.role == "learner" else "/"
    if payload.get("download") == "android" and (account.role == "learner" or staff_download_allowed(account)):
        destination = "/login?download=android"
    elif payload.get("intent") == "lecture-feedback" and account.role == "learner":
        destination = "/login?lectureFeedback=1"
    response = HttpResponseRedirect(f"{frontend_base_url()}{destination}")
    # Deliberately not remembered. There is no checkbox on this route to read a
    # preference from, and inventing one in the person's favour is the wrong
    # default for a console holding learner records: signing in again costs a
    # redirect through a tenant that has usually already authenticated them.
    set_session_cookie(response, token, ttl, remember=False)
    return _clear_nonce(response)
