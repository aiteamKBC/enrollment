"""Server-side session issue / resolve / revoke, and the request authenticator.

The cookie holds a random token; the database holds its SHA-256 and the state
that makes it revocable. See ``models.LoginSession`` for why this is not a JWT.

Cookie flags are set from one place (``_cookie_kwargs``) so a future change to
SameSite or the cookie name cannot drift between login, logout and renewal.
That includes *persistence*: "remember me" decides whether the browser keeps the
cookie after it closes, and issuance and renewal both read that from the session
row so the two can never disagree.

Sessions roll: activity pushes ``Expires_at`` forward so nobody is signed out
mid-task, bounded by an absolute ceiling anchored on ``Created_at`` so no session
lives for ever. ``touch_session`` renews on the way in and
``refresh_session_cookie`` extends the cookie on the way out; the middleware
carries the signal between them.

Every step of that emits a structured event on the ``login.sessions`` logger --
issued, renewed, at_ceiling, rejected, revoked -- carrying ids, a boolean and a
duration, never a token or an email. They exist so "why was I signed out?" is
answerable from the log instead of guessed at, and so the two ways a session can
end can be told apart in aggregate: see ``expiry_reason``.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from .models import LoginSession
from .security import (
    generate_token,
    hash_token,
    session_policy,
)

#: Distinct from Django's own ``sessionid`` so the two auth systems cannot be
#: confused for one another while the legacy admin session still exists.
COOKIE_NAME = getattr(settings, "ENROLLMENT_SESSION_COOKIE_NAME", "kbc_session")

#: Its own logger, so the volume can be tuned without touching the rest of the
#: login app -- and so these events can be routed somewhere that counts them.
event_log = logging.getLogger("login.sessions")


def _event(name, session=None, **fields):
    """Emit one session lifecycle event.

    Fields are restricted to what ``config.observability.SAFE_LOG_FIELDS``
    allows through the JSON formatter, which is the point: there is no path from
    here to a token, a token hash, an email or an IP ending up in the log
    stream, however this is called later.
    """
    if session is not None:
        fields.setdefault("session_id", str(session.pk))
        fields.setdefault("account_id", str(session.account_id))
        fields.setdefault("remember", bool(session.remember))
    event_log.info(name, extra={"event": name, **fields})


def _cookie_kwargs(max_age):
    """Flags for the session cookie.

    ``HttpOnly`` always: no frontend code has any reason to read this value, and
    it is the single control that keeps an XSS bug from becoming account theft.

    ``Secure`` follows ``SESSION_COOKIE_SECURE``, which the settings module ties
    to DEBUG — a cookie marked Secure is simply not stored over plain http, so
    hard-coding it True would break local development.

    ``SameSite=Lax`` lets the SPA's same-origin XHR through (the Vite dev server
    proxies the API, and production serves both from one host) while refusing the
    cross-site POSTs that CSRF depends on.

    ``max_age`` of **None** produces a browser-session cookie: no ``Max-Age`` and
    no ``Expires``, so the browser normally drops it when it closes. That is what
    leaving "remember me" unticked means. It is a UX boundary, not a security
    one -- browsers that restore a session ("continue where you left off", crash
    recovery, a mobile browser that never really exits) keep it anyway. What
    actually ends a session is ``Expires_at`` and ``Revoked_at``, checked
    server-side on every request in ``resolve_session``.
    """
    kwargs = {
        "httponly": True,
        "secure": getattr(settings, "SESSION_COOKIE_SECURE", not settings.DEBUG),
        "samesite": getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax"),
        "path": "/",
    }
    if max_age is not None:
        kwargs["max_age"] = int(max_age.total_seconds())
    return kwargs


def issue_session(account, *, remember=False, ip=None, user_agent=None):
    """Create a session row and return ``(plaintext_token, session, ttl)``.

    The plaintext is returned to the caller and never stored; only the caller
    (``views.login``) ever sees it, and it goes straight into a cookie.
    """
    remember = bool(remember)
    ttl, _absolute = session_policy(remember)
    token = generate_token()
    now = timezone.now()

    session = LoginSession.objects.create(
        account=account,
        token_hash=hash_token(token),
        expires_at=now + ttl,
        ip_address=ip,
        user_agent=user_agent,
        last_seen_at=now,
        # Persisted, not just used here: ``touch_session`` renews this session
        # hours or weeks from now and has to know which policy governs it.
        remember=remember,
    )
    _event("session.issued", session, ttl_seconds=int(ttl.total_seconds()))
    return token, session, ttl


def set_session_cookie(response, token, ttl, *, remember):
    """Send the cookie for a newly issued session.

    ``remember`` decides persistence only -- how long the session lives is
    ``Expires_at``'s business either way. Required rather than defaulted so a new
    sign-in path has to make the choice, instead of inheriting the quieter half
    of it by accident.
    """
    response.set_cookie(
        COOKIE_NAME, token, **_cookie_kwargs(ttl if remember else None)
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


def resolve_session(token):
    """Return the live ``LoginSession`` for a token, or None.

    "Live" means: exists, not revoked, not expired, and its account is still
    active. The account is fetched in the same query so the common case — an
    authenticated request — costs one round-trip.
    """
    if not token:
        return None

    session = (
        LoginSession.objects.select_related("account")
        .filter(token_hash=hash_token(token), revoked_at__isnull=True)
        .first()
    )
    if session is None:
        # DEBUG, not INFO, and deliberately so: this is the one rejection whose
        # volume a stranger controls. A revoked cookie and a fabricated one look
        # identical here, and anybody can send the latter as fast as they like.
        # The rejections below are about real sessions of real people, which is
        # the signal worth keeping on.
        event_log.debug(
            "session.rejected", extra={"event": "session.rejected", "reason": "unknown"}
        )
        return None
    if session.expires_at <= timezone.now():
        _event("session.rejected", session, reason=expiry_reason(session))
        return None
    if not session.account.is_active:
        _event("session.rejected", session, reason="account_inactive")
        return None
    return session


#: Only refresh ``Last_seen_at`` if it is this stale, so a busy console does not
#: issue a write on every single request. Rolling renewal rides on the same
#: throttle: a dashboard opening twelve panels writes once, not twelve times.
_LAST_SEEN_REFRESH_SECONDS = 300

#: Attribute ``authenticate_request`` leaves on the request when a renewal really
#: happened, carrying the new ``Expires_at``. The response middleware reads it to
#: decide whether the cookie needs extending. Named rather than inlined so the
#: two ends cannot drift.
RENEWED_UNTIL_ATTR = "login_session_renewed_until"

#: Attribute marking a request whose session could not be *read* -- the auth
#: database refused the query. That is not the same fact as "this caller has no
#: session", and conflating the two is what turns a momentary Neon hiccup into a
#: sign-out: the gate answers 401, the SPA reads 401 as "your session has ended"
#: and bounces to /login, while the session row itself is alive and unexpired.
#:
#: Both gates still fail closed -- an unreadable session admits nobody -- but
#: they answer **503** when this is set. A 503 is not in the SPA's sign-out path
#: (see ``lib/sessionExpiry.ts``), so the request fails, the panel can retry, and
#: nobody loses a session over an infrastructure blip.
SESSION_UNREADABLE_ATTR = "login_session_unreadable"


def mark_session_unreadable(request):
    """Record that the session lookup itself failed on this request."""
    setattr(request, SESSION_UNREADABLE_ATTR, True)


def session_unreadable(request):
    """Whether the session lookup *failed*, as opposed to finding no session."""
    return bool(getattr(request, SESSION_UNREADABLE_ATTR, False))


def renewal_target(session, now=None):
    """The furthest ``Expires_at`` this session may hold at ``now``.

    ``min(now + rolling window, Created_at + absolute maximum)`` — the rolling
    window keeps a working person signed in, and the ceiling anchored on the
    immutable ``Created_at`` is what stops "signed in for ever". ``Created_at``
    is read, never written: moving it would dissolve the ceiling.
    """
    now = now or timezone.now()
    rolling, absolute = session_policy(session.remember)
    return min(now + rolling, session.created_at + absolute)


def expiry_reason(session):
    """Which of the two clocks ended this session: ``idle`` or ``ceiling``.

    The distinction is the only one that matters when reading these numbers
    back. An **idle** expiry is the system working: somebody stopped using it
    for twelve hours (or a fortnight) and was signed out. A **ceiling** expiry
    interrupts somebody who was working -- they hit the absolute maximum, and no
    amount of activity could have prevented it.

    They are told apart by where ``Expires_at`` landed. Renewal clamps the
    target at ``Created_at + absolute`` exactly, so a session that reached its
    ceiling carries an expiry equal to it, and one that idled out carries an
    earlier one.

    Sessions predating the ``Remember`` column can read as ``ceiling`` on a
    technicality: a legacy 30-day session backfilled as normal holds an expiry
    beyond the 7-day ceiling it is now measured against. They age out within a
    month of the column landing, and "it outlived its ceiling" is not the wrong
    answer for them anyway.
    """
    _rolling, absolute = session_policy(session.remember)
    at_or_past_ceiling = session.expires_at >= session.created_at + absolute
    return "expired_ceiling" if at_or_past_ceiling else "expired_idle"


def touch_session(session):
    """Record activity and, if it has moved, extend the expiry.

    Returns the new ``Expires_at`` when this request actually renewed the
    session, otherwise None. The caller turns that into the cookie refresh; None
    means the response must not touch the cookie at all.

    The write is one conditional ``UPDATE`` rather than a read-then-save, which
    is what makes concurrent tabs safe. Its ``WHERE`` clause carries every rule:

    ``revoked_at IS NULL``
        A revoked session is never renewed, even though it resolved a moment
        ago — a logout in another tab may have landed in between.
    ``expires_at > now``
        An expired session is never revived. Renewal can only extend something
        that is still alive.
    ``expires_at < target``
        Expiry only moves *forward*. Two near-simultaneous requests compute
        near-identical targets; the later one wins and the earlier one no-ops,
        so neither can shorten what the other set.

    The same clause is why the ceiling cannot be jumped: ``target`` is already
    clamped, so there is no value this statement could write that exceeds it.

    It is also what makes the change safe for sessions predating the
    ``Remember`` column. Those backfill as normal sessions, so a legacy 30-day
    "remember me" session computes a target earlier than the expiry it already
    holds — ``expires_at < target`` fails, nothing is written, and it retires on
    its original schedule instead of being cut short.
    """
    now = timezone.now()
    last = session.last_seen_at
    if last is not None and (now - last).total_seconds() < _LAST_SEEN_REFRESH_SECONDS:
        return None

    target = renewal_target(session, now)
    live = LoginSession.objects.filter(
        pk=session.pk, revoked_at__isnull=True, expires_at__gt=now
    )

    if live.filter(expires_at__lt=target).update(expires_at=target, last_seen_at=now):
        session.expires_at = target
        session.last_seen_at = now
        _event(
            "session.renewed",
            session,
            ttl_seconds=int((target - now).total_seconds()),
        )
        return target

    # Still due a heartbeat even when the expiry had nowhere to move — the
    # ceiling has been reached, or another tab renewed a moment ago.
    live.update(last_seen_at=now)
    session.last_seen_at = now

    # Only one of those two is worth an event. A concurrent renewal is normal
    # and says nothing; a session pinned at its ceiling is somebody who is
    # working now and will be signed out mid-task regardless, and this is the
    # only advance warning of it there is.
    _rolling, absolute = session_policy(session.remember)
    if target >= session.created_at + absolute:
        _event(
            "session.at_ceiling",
            session,
            ttl_seconds=int((session.expires_at - now).total_seconds()),
        )
    return None


def refresh_session_cookie(request, response):
    """Extend the cookie lifetime iff *this* request renewed the session.

    Four guards, each closing a way the browser could end up holding a cookie
    that contradicts the database:

    1. **A renewal actually happened.** No ``RENEWED_UNTIL_ATTR``, no
       ``Set-Cookie`` — so the touch throttle governs cookie writes too, and an
       ordinary request leaves the header alone.
    2. **The response has not already spoken for the cookie.** ``set_cookie``
       and ``delete_cookie`` both leave a morsel in ``response.cookies``, so
       this single check covers sign-in, sign-out and the SSO callback without
       any of them having to know this function exists. It is what stops logout
       being followed by a middleware that helpfully puts ``kbc_session`` back.
    3. **The session was not revoked during the request.** Belt to (2) braces:
       views revoke through ``request.login_session``, so the in-memory row
       carries the revocation even before the response is built.
    4. **There is a token to re-send.** The value written back is the one the
       browser already holds — renewal extends a session, it never rotates or
       reissues one.

    Persistence is re-read from the session row, never inferred from the fact
    that a renewal happened. A renewal fires within five minutes of ordinary
    use, so a version of this that always sent ``Max-Age`` would promote every
    unremembered session to a persistent one almost immediately -- the choice
    undone by the very activity it was meant to survive.

    For a remembered session ``Max-Age`` is the time left until the *renewed*
    expiry, not a fresh full window, so a cookie clamped by the absolute ceiling
    dies with the session rather than outliving it. Every other flag comes from
    ``_cookie_kwargs``, the helper login itself uses, so none of them can drift
    here.
    """
    renewed_until = getattr(request, RENEWED_UNTIL_ATTR, None)
    if renewed_until is None:
        return response

    if COOKIE_NAME in response.cookies:
        return response

    session = getattr(request, "login_session", None)
    if session is None or session.revoked_at is not None:
        return response

    token = request.COOKIES.get(COOKIE_NAME)
    if not token:
        return response

    remaining = renewed_until - timezone.now()
    if remaining.total_seconds() <= 0:
        return response

    response.set_cookie(
        COOKIE_NAME,
        token,
        **_cookie_kwargs(remaining if session.remember else None),
    )
    return response


def revoke_session(session):
    if session.revoked_at is None:
        session.revoked_at = timezone.now()
        session.save(update_fields=["revoked_at"])
        _event("session.revoked", session, reason="signed_out")


def revoke_all_for_account(account, *, except_session_id=None):
    """Sign the account out everywhere.

    Called on password change and on deactivation: a credential that has been
    replaced must not leave live sessions behind that were established with it.
    """
    qs = LoginSession.objects.filter(account=account, revoked_at__isnull=True)
    if except_session_id:
        qs = qs.exclude(pk=except_session_id)
    revoked = qs.update(revoked_at=timezone.now())
    if revoked:
        # Worth an event even at zero-ish volume: a spike here is either a
        # password reset campaign or an account being cleaned up after a
        # compromise, and both are things somebody wants to notice.
        _event(
            "session.revoked_bulk",
            account_id=str(account.id),
            row_count=revoked,
            reason="credential_changed",
        )
    return revoked


def authenticate_request(request):
    """Attach ``request.login_session`` / ``request.login_account``; return the account.

    Idempotent and cheap to call more than once per request — the middleware
    calls it, and the permission decorators re-read the cached attribute.
    """
    if hasattr(request, "login_account"):
        return request.login_account

    token = request.COOKIES.get(COOKIE_NAME)
    session = resolve_session(token)

    request.login_session = session
    request.login_account = session.account if session else None
    setattr(request, RENEWED_UNTIL_ATTR, None)

    if session is not None:
        # The renewal, if any, has to reach the response: the cookie lifetime is
        # the other half of an expiry, and a row that says "another fortnight" is
        # worthless if the browser drops the cookie tonight.
        setattr(request, RENEWED_UNTIL_ATTR, touch_session(session))
        _refresh_staff_role(request.login_account)

    return request.login_account


def _refresh_staff_role(account):
    """Re-derive a staff account's role from its current access grant.

    ``require_role`` has always documented that a demotion "takes effect on their
    next request, not only when they next sign in" — but nothing actually
    recomputed it, so the ``Role`` column stayed at whatever it was when the
    account was minted. That was harmless while Position decided the role and
    Position rarely changed. It stopped being harmless once access became the
    grant: an account created as Position='Admin' kept ``role='admin'`` in the
    database even after the derivation stopped awarding it, so the super-admin
    console still let it in.

    Only staff accounts are affected — a learner's or employer's role comes from
    which table they live in and cannot drift. Writes only when the value really
    changed, so this costs one indexed read per authenticated staff request and
    no write on the overwhelming majority of them.

    Never raises: a failure here must not sign somebody out. The API gate
    reports unreadable staff grants as temporary unavailability instead of
    letting a restricted monitoring account use a broader stale role.
    """
    if account is None or account.subject_type != "staff":
        return
    try:
        from django.db import DatabaseError

        from learner_api.models import StaffUser

        from .identity import accesses_for_staff, role_for_staff

        row = (
            StaffUser.objects.filter(pk=account.subject_id)
            .only("position", "access", "access_extra")
            .first()
        )
        if row is None:
            account._staff_access_unavailable = True
            return
        account._staff_access = (row.access or '').strip().lower()
        derived = role_for_staff(row.position, row.access)
        if derived != account.role:
            account.role = derived
            account.save(update_fields=["role", "updated_at"])
    except DatabaseError:
        account._staff_access_unavailable = True
    except Exception:  # noqa: BLE001 - never break authentication over this
        account._staff_access_unavailable = True
        import logging

        logging.getLogger("login").exception("Could not refresh staff role")
