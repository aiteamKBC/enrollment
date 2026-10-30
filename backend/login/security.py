"""Credential and token primitives: hashing, token minting, lockout, throttling.

Everything here is deliberately independent of Django's request/response layer so
it can be reasoned about (and tested) on its own. The views in ``views.py`` are
thin wrappers over these functions.

Design notes
------------
**Password hashing** uses ``django.contrib.auth.hashers``, which is configured
project-wide (PBKDF2-SHA256 by default; Argon2 if ``argon2-cffi`` is installed —
see ``PASSWORD_HASHERS`` guidance in the README section this feature adds). We do
not hand-roll a KDF.

**Tokens** (session, invitation, reset) are 256 bits from ``secrets.token_urlsafe``.
Only ``sha256`` of the token is persisted. SHA-256 rather than a slow KDF is
correct here and not an oversight: unlike a password, the token already has full
entropy, so there is no dictionary to grind and a slow hash would only add
latency to every authenticated request.

**Comparisons** of hashes use ``hmac.compare_digest``. The database lookup is by
the hash itself (an indexed equality), so the timing signal there is a b-tree
probe, not a byte-by-byte compare of a secret.

**Lockout** is per-account, with an exponential-ish backoff capped at
``LOCKOUT_MAX_MINUTES``. **Throttling** is per-email over a sliding window read
from the audit table, and limits reset emails per address.

The per-IP sign-in throttle is **disabled** -- see ``ip_is_throttled`` for why
(shared office egress made it lock out the whole building) and for what a
replacement would need to do differently.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password as dj_check_password
from django.contrib.auth.hashers import make_password as dj_make_password
from django.utils import timezone


# --- session lifetimes --------------------------------------------------------
# Two distinct clocks govern a session, and conflating them is the mistake this
# split exists to prevent:
#
# *Rolling window* — how long a session survives **without activity**. Every
#   touch pushes it forward, so somebody who is working is never signed out
#   mid-task. On its own it would let a session (or a stolen cookie) live for
#   ever on one request a day.
#
# *Absolute maximum* — how long a session may live **from Created_at**, no
#   matter how active. This is the ceiling that makes the rolling window safe,
#   and it is never moved: re-authentication eventually becomes mandatory.
#
# Renewal is ``min(now + rolling, Created_at + absolute)``. See
# ``sessions.touch_session``.

#: Rolling window without "remember me": how long a device can sit with no
#: request before it is signed out. Five hours so a full working sitting --
#: including a long lunch or a morning of meetings with the console left open
#: and untouched -- is never interrupted, while an unattended machine still
#: signs itself out the same day.
SESSION_TTL = timedelta(hours=5)
#: Rolling window with "remember me" ticked. Deliberately **shorter** than the
#: 30-day absolute TTL it replaces: 30 days was a hard cap, and reusing it as a
#: rolling window would let a stolen cookie survive indefinitely on one request
#: a month. Fourteen days still covers "I use this every working week".
SESSION_TTL_REMEMBER = timedelta(days=14)

#: Ceiling for a normal session. A week of continuous use is generous for a
#: console that reaches learner records; past that, sign in again.
SESSION_MAX_LIFETIME = timedelta(days=7)
#: Ceiling for a "remember me" session. Long enough to be worth ticking, finite
#: enough that a forgotten laptop does not stay signed in for ever.
SESSION_MAX_LIFETIME_REMEMBER = timedelta(days=90)


def session_policy(remember):
    """``(rolling window, absolute maximum)`` for a session of this kind.

    One place decides what "remember me" means, so issuance, renewal and the
    tests cannot drift from one another.
    """
    if remember:
        return SESSION_TTL_REMEMBER, SESSION_MAX_LIFETIME_REMEMBER
    return SESSION_TTL, SESSION_MAX_LIFETIME

#: Invitations are generous: onboarding often waits on the person's first working day.
INVITATION_TTL = timedelta(days=7)
#: Resets are deliberately short — the address may be a shared or stale inbox.
RESET_TTL = timedelta(hours=1)

# --- lockout ------------------------------------------------------------------
#: Failures tolerated before the account starts locking.
LOCKOUT_THRESHOLD = 5
#: Base lock, multiplied by how far past the threshold the account is.
LOCKOUT_BASE_MINUTES = 5
LOCKOUT_MAX_MINUTES = 60

# --- throttling (sliding window over auth."Login_audit") -----------------------
THROTTLE_WINDOW = timedelta(minutes=15)
#: Failed sign-ins from one IP in the window before it is refused outright.
THROTTLE_MAX_FAILURES_PER_IP = 20
#: Reset emails per address in the window. Low: each one sends mail.
THROTTLE_MAX_RESETS_PER_EMAIL = 3


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

#: Minimum length. Length is the property that actually resists guessing, so the
#: policy leans on it rather than on character-class rules, which mostly produce
#: "Password1!" and are no longer recommended (NCSC / NIST SP 800-63B).
MIN_PASSWORD_LENGTH = 8

#: Rejected outright regardless of length — the passwords every spray tries first.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwerty123", "letmein", "welcome1", "admin123",
    "iloveyou", "changeme", "passw0rd", "abc12345", "11111111",
}


class PasswordPolicyError(ValueError):
    """Raised when a proposed password does not meet policy."""


def validate_password_strength(password, *, email=None, display_name=None):
    """Check a proposed password. Raises PasswordPolicyError with a usable message.

    ``email``/``display_name`` are compared against so somebody cannot set their
    own address as their password — the first thing an attacker who knows the
    account would try.
    """
    if not isinstance(password, str) or not password:
        raise PasswordPolicyError("Password is required.")

    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )

    # Bound the work done by the hasher. Django's own limit is the same idea.
    if len(password) > 128:
        raise PasswordPolicyError("Password must be at most 128 characters.")

    lowered = password.lower()

    if lowered in _COMMON_PASSWORDS:
        raise PasswordPolicyError("That password is too common. Choose something less guessable.")

    # The shared first-sign-in password (DEFAULT_LEARNER_PASSWORD, below) is
    # public; keeping it would leave the account open to anyone.
    if lowered == "changeme_password":
        raise PasswordPolicyError("Choose a new password of your own, not the first-sign-in password.")

    # A single repeated character, however long ("aaaaaaaa").
    if len(set(password)) == 1:
        raise PasswordPolicyError("Password cannot be a single repeated character.")

    if email:
        local_part = str(email).split("@", 1)[0].strip().lower()
        if local_part and len(local_part) >= 3 and local_part in lowered:
            raise PasswordPolicyError("Password must not contain your email address.")

    if display_name:
        for part in re.split(r"\s+", str(display_name).strip().lower()):
            if len(part) >= 4 and part in lowered:
                raise PasswordPolicyError("Password must not contain your name.")

    return True


def hash_password(raw_password):
    """Hash a password for storage using the project's configured hasher."""
    return dj_make_password(raw_password)


def verify_password(raw_password, stored_hash):
    """Constant-time-ish verification of a password against a stored hash.

    An empty ``stored_hash`` means "invited but never set a password". Django's
    ``check_password`` already treats an unusable hash as a failure, but we short
    -circuit explicitly so the intent is not accidentally refactored away.
    """
    if not stored_hash:
        return False
    return dj_check_password(raw_password, stored_hash)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

#: 32 bytes of entropy, URL-safe (~43 characters). Safe to place in a link.
TOKEN_BYTES = 32


def generate_token():
    """Return a fresh high-entropy token. This is the only place it exists in plaintext."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token):
    """SHA-256 hex digest of a token — the only form ever written to the database."""
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def tokens_equal(a, b):
    """Constant-time comparison of two token hashes."""
    return hmac.compare_digest(str(a or ""), str(b or ""))


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_email(email):
    """Lowercase and trim. Matches the ``lower(btrim(...))`` unique indexes.

    Note it does *not* strip dots or ``+tags``: those are provider-specific
    conventions, and treating ``a.b@`` and ``ab@`` as one identity would be wrong
    for most corporate mail systems, which is what this deployment uses.
    """
    return str(email or "").strip().lower()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(email):
    return bool(_EMAIL_RE.match(normalize_email(email)))


def client_ip(request):
    """Best-effort client IP.

    Trusts ``X-Forwarded-For`` only when ``TRUST_PROXY_IP_HEADER`` is enabled,
    because a forwarded header is attacker-controlled unless a proxy is known to
    overwrite it — and this value feeds throttling decisions.
    """
    from django.conf import settings

    if getattr(settings, "TRUST_PROXY_IP_HEADER", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            candidate = forwarded.split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass

    return request.META.get("REMOTE_ADDR") or None


def user_agent(request):
    """Truncated UA string — recorded for audit, never trusted for a decision."""
    return (request.META.get("HTTP_USER_AGENT") or "")[:400] or None


# ---------------------------------------------------------------------------
# Lockout
# ---------------------------------------------------------------------------

def is_locked(account):
    """Whether the account is currently refusing passwords."""
    return bool(account.locked_until and account.locked_until > timezone.now())


def lock_duration_for(failed_attempts):
    """Backoff for a given failure count, in minutes (0 = no lock yet)."""
    if failed_attempts < LOCKOUT_THRESHOLD:
        return 0
    over = failed_attempts - LOCKOUT_THRESHOLD + 1
    return min(LOCKOUT_BASE_MINUTES * over, LOCKOUT_MAX_MINUTES)


def register_failure(account):
    """Record one failed password attempt and lock the account if it is due.

    Saves only the affected columns so it cannot clobber a concurrent write to
    the rest of the row.
    """
    account.failed_attempts = (account.failed_attempts or 0) + 1
    minutes = lock_duration_for(account.failed_attempts)
    if minutes:
        account.locked_until = timezone.now() + timedelta(minutes=minutes)
    account.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    return minutes


def register_success(account, ip=None):
    """Clear lockout state after a successful sign-in."""
    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = timezone.now()
    account.last_login_ip = ip
    account.save(
        update_fields=[
            "failed_attempts", "locked_until",
            "last_login_at", "last_login_ip", "updated_at",
        ]
    )


# ---------------------------------------------------------------------------
# Throttling — sliding window over the audit table
# ---------------------------------------------------------------------------

def recent_failure_count(*, ip=None, email=None, event=None, window=THROTTLE_WINDOW,
                         only_failures=True):
    """Count recent audit rows matching the given filters.

    Reading the audit table rather than a cache keeps the limit correct across
    processes and restarts, which a LocMem cache would not be (the deployment
    runs multiple workers and only optionally has Redis).
    """
    from .models import LoginAudit

    qs = LoginAudit.objects.filter(created_at__gte=timezone.now() - window)
    if only_failures:
        qs = qs.filter(succeeded=False)
    if ip:
        qs = qs.filter(ip_address=ip)
    if email:
        qs = qs.filter(email=normalize_email(email))
    if event:
        qs = qs.filter(event=event)
    return qs.count()


def ip_is_throttled(ip):
    """Whether this source has failed too often recently to be allowed another try.

    Disabled: always False. The per-IP window counted failures by *source
    address*, and the college reaches the platform through shared egress -- one
    office, one NAT, one address for everybody. Twenty failed attempts anywhere
    in the building refused sign-in for every colleague behind it for fifteen
    minutes, including people typing their password correctly, which is how it
    was found.

    Per-ACCOUNT lockout is untouched and is the control that actually matters
    here: `register_failure`/`is_locked` still lock an individual account after
    LOCKOUT_THRESHOLD failures with escalating backoff, so guessing one person's
    password is no easier than before. What is lost is the defence against an
    attacker spreading attempts thinly across MANY accounts from one source --
    worth restoring as a per-IP limit that counts distinct accounts tried, or
    keyed on something narrower than a shared office address, rather than as a
    flat failure count.
    """
    return False


def reset_requests_exhausted(email):
    """Whether this address has already been sent its allowance of reset emails."""
    from .models import EVENT_RESET_REQUESTED

    return recent_failure_count(
        email=email,
        event=EVENT_RESET_REQUESTED,
        only_failures=False,
    ) >= THROTTLE_MAX_RESETS_PER_EMAIL


#: The first-sign-in password every learner can use until they set their own --
#: for learners whose employer's mail filter blocks the invitation email.
#:
#: It is never stored on an account and never opens a session. Signing in with
#: it, as a learner who has never set a password, only hands back that
#: learner's single-use set-password link (see
#: ``services.default_password_setup``); nothing in the account is reachable
#: until they choose a password of their own, and from then on it no longer
#: works for them. Known risk, accepted by the project owner: until a learner
#: sets a password, anyone who knows their email address can set it first.
DEFAULT_LEARNER_PASSWORD = "changeme_password"


def is_default_learner_password(password):
    import hmac

    return hmac.compare_digest(str(password or "").encode("utf-8"), DEFAULT_LEARNER_PASSWORD.encode("utf-8"))
