"""Authentication endpoints.

    POST   /login_api/login/              email + password -> session cookie
    POST   /login_api/logout/             revoke the current session
    GET    /login_api/me/                 the signed-in identity, or 401
    POST   /login_api/change-password/    signed-in password change
    GET    /login_api/invitation/?token=  validate an invite (no side effects)
    POST   /login_api/accept-invitation/  redeem an invite, set first password
    POST   /login_api/forgot-password/    request a reset email
    GET    /login_api/reset/?token=       validate a reset token
    POST   /login_api/reset-password/     redeem a reset, set new password
    POST   /login_api/accounts/invite/    (admin) invite an existing person
    GET    /login_api/microsoft/start/    begin sign-in with Microsoft
    GET    /login_api/microsoft/callback/ finish it (see microsoft_sso.py)
    GET    /login_api/health/             config/readiness, no secrets

Two conventions run through the file:

**Uniform failure.** Sign-in and reset-request answer the same way whether or not
the address exists. An endpoint that says "no such user" is an account
enumeration oracle, and this deployment's addresses are staff and learner names
at a known domain.

**CSRF.** These views are ``@csrf_exempt`` and instead require a custom header
(see ``_reject_cross_site``). A browser will not attach a custom header to a
cross-origin form post without a successful preflight, so requiring one is a
sufficient CSRF defence for a JSON API and avoids the chicken-and-egg of needing
a CSRF cookie before the user has any session. It matches how the rest of this
project's JSON endpoints are written (``@csrf_exempt`` throughout learner_api).
"""
from __future__ import annotations

import json

from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import email_azure, identity, microsoft_sso
from .invitations import (
    TokenError,
    accept_invitation,
    complete_reset,
    peek_invitation,
    peek_reset,
    record,
)
from .models import EVENT_LOGIN, EVENT_LOGOUT, EVENT_PASSWORD_CHANGED, LoginAccount
from .permissions import login_required, require_role
from .services import default_password_setup, invite_subject, issue_invitation_link, request_password_link
from .security import (
    PasswordPolicyError,
    client_ip,
    hash_password,
    ip_is_throttled,
    is_default_learner_password,
    is_locked,
    looks_like_email,
    normalize_email,
    register_failure,
    register_success,
    reset_requests_exhausted,
    user_agent,
    validate_password_strength,
    verify_password,
)
from .sessions import (
    authenticate_request,
    clear_session_cookie,
    issue_session,
    revoke_all_for_account,
    revoke_session,
    set_session_cookie,
)

#: Returned for every bad sign-in, regardless of the underlying cause.
GENERIC_LOGIN_ERROR = "Incorrect email or password."


def _error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def _body(request):
    """Parse a JSON request body, tolerating an empty one."""
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


def _reject_cross_site(request):
    """CSRF defence for a cookie-authenticated JSON API.

    Requires ``X-Requested-With: XMLHttpRequest``. A cross-origin ``<form>`` post
    — the shape CSRF actually takes — cannot set a custom header at all, and an
    XHR/fetch that tries must first pass a CORS preflight this origin does not
    grant. Returns an error response, or None when the request is acceptable.
    """
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _error(
            "Missing X-Requested-With header.",
            403,
            code="csrf",
        )
    return None


# ---------------------------------------------------------------------------
# Sign in / out
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def login(request):
    """Exchange credentials for a session cookie."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    email = normalize_email(payload.get("email"))
    password = payload.get("password") or ""
    remember = bool(payload.get("remember"))

    ip = client_ip(request)
    ua = user_agent(request)

    if not email or not password:
        return _error("Email and password are required.", 400)

    # Per-IP throttle first: it is the control that survives an attacker
    # spreading attempts across many accounts to stay under per-account lockout.
    if ip_is_throttled(ip):
        record(EVENT_LOGIN, email=email, succeeded=False, reason="ip_throttled", ip=ip, user_agent=ua)
        return _error(
            "Too many failed attempts from this location. Try again later.",
            429,
            code="throttled",
        )

    try:
        account = identity.account_for_email(email)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if is_default_learner_password(password) and (account is None or not account.has_password):
        # A learner who has never set a password, using the shared first-sign-in
        # password: no session, only their own set-password link (see
        # security.DEFAULT_LEARNER_PASSWORD). Anyone else falls through to the
        # ordinary failure below, indistinguishable from a wrong password.
        try:
            setup_path = default_password_setup(email, ip=ip, user_agent=ua)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        if setup_path:
            record(EVENT_LOGIN, email=email, succeeded=False, reason="default_password_setup", ip=ip, user_agent=ua)
            response = JsonResponse({"passwordSetupRequired": True, "setPasswordPath": setup_path})
            response["Cache-Control"] = "no-store"
            return response

    if account is None:
        # No account, inactive, or ambiguous across subject types. All are
        # reported identically — see the module docstring.
        record(EVENT_LOGIN, email=email, succeeded=False, reason="unknown_account", ip=ip, user_agent=ua)
        return _error(GENERIC_LOGIN_ERROR, 401)

    if is_locked(account):
        record(
            EVENT_LOGIN, email=email, account_id=account.id,
            succeeded=False, reason="locked", ip=ip, user_agent=ua,
        )
        # Being explicit here is a deliberate exception to uniform failure: the
        # person is far more likely to be the legitimate owner locked out by
        # their own typos than an attacker, and silently rejecting a correct
        # password with "wrong password" is actively misleading.
        return _error(
            "This account is temporarily locked after too many failed attempts. "
            "Try again later or reset your password.",
            423,
            code="locked",
            lockedUntil=account.locked_until.isoformat() if account.locked_until else None,
        )

    if not account.has_password:
        # Invited but never onboarded. Reported as a normal credential failure:
        # confirming "this address was invited" to an anonymous caller leaks the
        # existence of the account.
        record(
            EVENT_LOGIN, email=email, account_id=account.id,
            succeeded=False, reason="no_password", ip=ip, user_agent=ua,
        )
        return _error(GENERIC_LOGIN_ERROR, 401)

    if not verify_password(password, account.password_hash):
        locked_minutes = register_failure(account)
        record(
            EVENT_LOGIN, email=email, account_id=account.id,
            succeeded=False, reason="bad_password", ip=ip, user_agent=ua,
        )
        if locked_minutes:
            return _error(
                f"Too many failed attempts. This account is locked for "
                f"{locked_minutes} minute(s).",
                423,
                code="locked",
            )
        return _error(GENERIC_LOGIN_ERROR, 401)

    # --- authenticated ---
    register_success(account, ip=ip)
    token, session, ttl = issue_session(account, remember=remember, ip=ip, user_agent=ua)
    record(EVENT_LOGIN, email=email, account_id=account.id, succeeded=True, ip=ip, user_agent=ua)

    response = JsonResponse({"user": identity.account_payload(account)})
    return set_session_cookie(response, token, ttl, remember=remember)


@csrf_exempt
@require_POST
def logout(request):
    """Revoke the current session. Idempotent — always answers 200."""
    account = authenticate_request(request)
    session = getattr(request, "login_session", None)

    if session is not None:
        revoke_session(session)
        record(
            EVENT_LOGOUT,
            email=account.email if account else None,
            account_id=account.id if account else None,
            succeeded=True,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )

    return clear_session_cookie(JsonResponse({"ok": True}))


@require_GET
def me(request):
    """The signed-in identity, or 401. The SPA calls this on every page load."""
    account = authenticate_request(request)
    if account is None:
        return _error("Not authenticated.", 401, code="unauthenticated")
    try:
        return JsonResponse({"user": identity.account_payload(account)})
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)


@require_GET
def learner_action_access(request):
    """May the signed-in account act for ``?learnerId=``? Read-only.

    The learner workspace asks this to decide between its working and read-only
    views. It answers with the rule ``permissions.learner_self_or_admin``
    enforces on every write: the learner, an administrator, or the learner's
    own coach, or a staff account studying its own linked enrolment.
    """
    from .permissions import coach_owns_learner, staff_owns_learner

    account = authenticate_request(request)
    if account is None:
        return _error("Not authenticated.", 401, code="unauthenticated")
    try:
        learner_id = int(request.GET.get("learnerId", ""))
    except (TypeError, ValueError):
        return _error("learnerId is required.", 400)
    can_act = learner_id > 0 and (
        account.role == "admin"
        or (account.role == "learner" and account.subject_id == learner_id)
        or coach_owns_learner(account, learner_id)
        or staff_owns_learner(account, learner_id)
    )
    response = JsonResponse({"canAct": bool(can_act)})
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
@login_required
def change_password(request):
    """Change your own password. Requires the current one."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    account = request.login_account
    current = payload.get("currentPassword") or ""
    new_password = payload.get("newPassword") or ""

    # Requiring the current password is what stops a hijacked session from
    # being escalated into permanent ownership of the account.
    if not verify_password(current, account.password_hash):
        record(
            EVENT_PASSWORD_CHANGED, email=account.email, account_id=account.id,
            succeeded=False, reason="bad_current_password",
            ip=client_ip(request), user_agent=user_agent(request),
        )
        return _error("Your current password is incorrect.", 403)

    try:
        validate_password_strength(
            new_password, email=account.email, display_name=account.display_name
        )
    except PasswordPolicyError as exc:
        return _error(str(exc), 400, code="weak_password")

    account.password_hash = hash_password(new_password)
    account.password_set_at = timezone.now()
    account.save(update_fields=["password_hash", "password_set_at", "updated_at"])

    # Keep the caller signed in, drop every other session.
    session = getattr(request, "login_session", None)
    revoke_all_for_account(account, except_session_id=session.id if session else None)

    record(
        EVENT_PASSWORD_CHANGED, email=account.email, account_id=account.id,
        succeeded=True, ip=client_ip(request), user_agent=user_agent(request),
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def forgot_password(request):
    """Request a reset email -- or, for an enrolled learner who has never set a
    password, the set-password invitation (``services.request_password_link``).

    Always answers 200 with the same body. Whether the address exists, is
    inactive, is a learner, or has already used its allowance is never disclosed.
    """
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    email = normalize_email(payload.get("email"))
    ip = client_ip(request)
    ua = user_agent(request)

    uniform = JsonResponse({
        "ok": True,
        "message": "If that address is registered, we have emailed it a link to reset or set your password.",
    })

    if not looks_like_email(email):
        # Malformed input is the one case worth reporting: it is a user error,
        # not an enumeration signal.
        return _error("Enter a valid email address.", 400)

    try:
        if reset_requests_exhausted(email):
            record(EVENT_LOGIN, email=email, succeeded=False, reason="reset_throttled", ip=ip, user_agent=ua)
            return uniform

        request_password_link(email, ip=ip, user_agent=ua)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    return uniform


@require_GET
def reset_info(request):
    """Validate a reset token so the page can render (or fail early)."""
    token = (request.GET.get("token") or "").strip()
    if not token:
        return _error("A reset token is required.", 400)
    try:
        return JsonResponse(peek_reset(token))
    except TokenError as exc:
        return _error(str(exc), 400, code="invalid_token")
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)


@csrf_exempt
@require_POST
def reset_password(request):
    """Redeem a reset token and set the new password."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    token = (payload.get("token") or "").strip()
    new_password = payload.get("password") or ""
    if not token:
        return _error("A reset token is required.", 400)

    try:
        complete_reset(
            token, new_password,
            ip=client_ip(request), user_agent=user_agent(request),
        )
    except TokenError as exc:
        return _error(str(exc), 400, code="invalid_token")
    except PasswordPolicyError as exc:
        return _error(str(exc), 400, code="weak_password")
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    # Deliberately does not sign the person in. Making them use the password
    # they just chose confirms they know it, and keeps a stolen reset link one
    # step further from a live session.
    return JsonResponse({"ok": True, "message": "Your password has been set. You can now sign in."})


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

@require_GET
def invitation_info(request):
    """Validate an invitation token so the set-password page can render."""
    token = (request.GET.get("token") or "").strip()
    if not token:
        return _error("An invitation token is required.", 400)
    try:
        return JsonResponse(peek_invitation(token))
    except TokenError as exc:
        return _error(str(exc), 400, code="invalid_token")
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)


@csrf_exempt
@require_POST
def accept_invitation_view(request):
    """Redeem an invitation and set the first password."""
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    token = (payload.get("token") or "").strip()
    new_password = payload.get("password") or ""
    if not token:
        return _error("An invitation token is required.", 400)

    try:
        accept_invitation(
            token, new_password,
            ip=client_ip(request), user_agent=user_agent(request),
        )
    except TokenError as exc:
        return _error(str(exc), 400, code="invalid_token")
    except PasswordPolicyError as exc:
        return _error(str(exc), 400, code="weak_password")
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "ok": True,
        "message": "Your password has been set. You can now sign in.",
    })


@csrf_exempt
@require_POST
@require_role("admin", "staff")
def invite_account(request):
    """(Staff) Invite, or re-invite, an existing person.

    Body: ``{"subjectType": "learner"|"employer"|"staff", "subjectId": 123}``.
    Used by the account-management screen and by the "resend invitation" action;
    the creation forms call the same code path directly via ``services.py``.
    """
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked

    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)

    subject_type = (payload.get("subjectType") or "").strip().lower()
    subject_id = payload.get("subjectId")

    try:
        subject_id = int(subject_id)
    except (TypeError, ValueError):
        return _error("subjectId must be a whole number.", 400)

    if subject_type not in identity.SUBJECT_CHOICES:
        return _error(f"Unknown subjectType: {subject_type!r}", 400)

    # Goes through invite_subject rather than calling send_invitation directly,
    # so this route gets the same authorisation as the creation forms — notably
    # "only an admin may invite another admin", which @require_role("admin",
    # "staff") on its own would not enforce.
    outcome = invite_subject(
        subject_type,
        subject_id,
        inviter=request.login_account,
        invited_by=request.login_account.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )

    if outcome["forbidden"]:
        return _error(outcome["error"], 403, code="forbidden")
    if not outcome["invited"]:
        return _error(outcome["error"] or "Could not issue the invitation.", 400)

    account = identity.account_for_subject(subject_type, subject_id)

    return JsonResponse({
        "ok": True,
        "accountCreated": outcome["accountCreated"],
        "emailSent": outcome["emailSent"],
        # Surfaced to staff (not to anonymous callers) so a misconfigured mail
        # tenant is visible in the UI rather than silently dropping invitations.
        "emailError": outcome["error"],
        "expiresAt": outcome["expiresAt"],
        "account": identity.account_payload(account) if account else None,
    }, status=201 if outcome["accountCreated"] else 200)


@csrf_exempt
@require_POST
@require_role("admin", "staff")
def invitation_link_view(request):
    """(Staff) A learner's set-password link to copy and send another way.

    Body: ``{"subjectType": "learner", "subjectId": 123}``. For learners whose
    employer blocks our email: the link is returned instead of emailed. It is
    never cached and replaces any earlier invitation link.
    """
    blocked = _reject_cross_site(request)
    if blocked:
        return blocked
    try:
        payload = _body(request)
    except ValueError as exc:
        return _error(str(exc), 400)
    try:
        subject_id = int(payload.get("subjectId"))
    except (TypeError, ValueError):
        return _error("subjectId must be a whole number.", 400)
    subject_type = (payload.get("subjectType") or "").strip().lower()
    try:
        outcome = issue_invitation_link(
            subject_type, subject_id, inviter=request.login_account,
            ip=client_ip(request), user_agent=user_agent(request),
        )
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if outcome["forbidden"]:
        return _error(outcome["error"], 403, code="forbidden")
    if not outcome["link"]:
        return _error(outcome["error"] or "Could not create the link.", 400)
    response = JsonResponse({"ok": True, "link": outcome["link"], "expiresAt": outcome["expiresAt"],
                             "accountCreated": outcome["accountCreated"]})
    # A live credential: never stored by a browser or proxy cache.
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@require_GET
def health(request):
    """Readiness of the auth subsystem. Reports names of missing settings only."""
    try:
        accounts = LoginAccount.objects.count()
        with_password = LoginAccount.objects.exclude(password_hash="").count()
        db_ok, db_error = True, None
    except DatabaseError as exc:
        accounts = with_password = None
        db_ok, db_error = False, str(exc)

    return JsonResponse({
        "ok": db_ok,
        "database": {"ok": db_ok, "error": db_error,
                     "accounts": accounts, "accountsWithPassword": with_password},
        "email": {
            "configured": email_azure.is_configured(),
            # Names, never values.
            "missing": email_azure.missing_settings(),
        },
        # The sign-in page reads this to decide whether to offer the Microsoft
        # button at all — an SSO button that cannot work is worse than none.
        "microsoftSso": {
            "configured": microsoft_sso.is_configured(),
            "missing": microsoft_sso.missing_settings(),
        },
    })
