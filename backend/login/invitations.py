"""Issue and redeem the two emailed tokens: invitations and password resets.

Both follow the same shape, and the shape is the security-relevant part:

1. Mint a token, store only its SHA-256, email the plaintext.
2. Supersede any earlier unused token for the same account, so a re-send
   invalidates the previous link rather than leaving several live at once.
3. On redemption: look up by hash, check unused and unexpired, set the password,
   mark used, and revoke every existing session for that account.

Step 3's session revocation is what makes a reset meaningful — otherwise
somebody who had already signed in with the old password would stay signed in.
"""
from __future__ import annotations

import os

from django.db import transaction
from django.utils import timezone

from . import email_azure
from .models import (
    EVENT_INVITE_ACCEPTED,
    EVENT_INVITE_SENT,
    EVENT_RESET_COMPLETED,
    EVENT_RESET_REQUESTED,
    Invitation,
    LoginAudit,
    PasswordReset,
)
from .security import (
    INVITATION_TTL,
    RESET_TTL,
    generate_token,
    hash_password,
    hash_token,
    validate_password_strength,
)
from .sessions import revoke_all_for_account


def frontend_base_url():
    """Where the emailed links point.

    ``FRONTEND_URL`` is already used by this project for the same purpose. The
    trailing slash is stripped so joining a path cannot produce a double slash,
    which some mail clients mangle.
    """
    return (os.environ.get("FRONTEND_URL") or "http://localhost:5173").rstrip("/")


def invitation_link(token):
    return f"{frontend_base_url()}/set-password?token={token}"


def reset_link(token):
    return f"{frontend_base_url()}/reset-password?token={token}"


def one_to_one_booking(account):
    """``(case owner name, booking link)`` for a learner's invitation, or None.

    The link opens the LMS introduction request page (``lms_introduction``),
    where the learner picks a preferred time for a one-to-one with their case
    owner. Best effort: the invitation is the point of the email, so a learner
    with no case owner, a case owner who does not resolve to a staff email, or a
    lookup failure leaves the option out instead of stopping the invitation.
    """
    if account.subject_type != "learner":
        return None
    try:
        from . import lms_introduction

        learner, owner_email, owner_name = lms_introduction.learner_and_owner(account)
    except Exception:  # noqa: BLE001 - an optional extra must not block the invitation
        import logging

        logging.getLogger("login").exception(
            "Could not look up the case owner for account %s's LMS introduction", account.id
        )
        return None
    if learner is None or not owner_email:
        return None
    return owner_name, lms_introduction.booking_link(account, frontend_base_url())


def record(event, *, email=None, account_id=None, succeeded=False, reason=None,
           ip=None, user_agent=None):
    """Write one audit row. Never raises — auditing must not break the flow."""
    try:
        LoginAudit.objects.create(
            event=event,
            email=(email or "").strip().lower() or None,
            account_id=account_id,
            succeeded=succeeded,
            reason=reason,
            ip_address=ip,
            user_agent=user_agent,
        )
    except Exception:  # noqa: BLE001 - audit is best-effort by design
        import logging

        logging.getLogger("login").exception("Failed to write login audit row")


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

@transaction.atomic(using="enrolment")
def create_invitation(account, *, invited_by=None, ip=None):
    """Mint an invitation for an account, superseding any earlier unused one.

    Returns ``(invitation, plaintext_token)``. The plaintext is only ever handed
    to the mail sender.
    """
    Invitation.objects.filter(account=account, used_at__isnull=True).update(
        used_at=timezone.now()
    )

    token = generate_token()
    invitation = Invitation.objects.create(
        account=account,
        token_hash=hash_token(token),
        email=account.email,
        expires_at=timezone.now() + INVITATION_TTL,
        invited_by=invited_by,
        created_ip=ip,
    )
    return invitation, token


def send_invitation(account, *, invited_by=None, ip=None, user_agent=None):
    """Create and email an invitation. Returns ``(invitation, sent, detail)``.

    A send failure is recorded on the row and returned, not raised: the account
    and its invitation are still valid, and an admin can re-send. Callers that
    are creating a person (the three creation forms) must not have the whole
    creation fail because a mail server was briefly unavailable.
    """
    invitation, token = create_invitation(account, invited_by=invited_by, ip=ip)

    booking = one_to_one_booking(account)
    subject, html, text = email_azure.invitation_message(
        display_name=account.display_name,
        link=invitation_link(token),
        expires_days=INVITATION_TTL.days,
        booking_owner=booking[0] if booking else None,
        booking_link=booking[1] if booking else None,
    )
    sent, detail = email_azure.send_mail(
        to=account.email, subject=subject, html_body=html, text_body=text,
        # Kept in the sender's Sent Items so staff can see which invitations
        # went out and when. Password resets do the same (see send_reset).
        save_to_sent=True,
    )

    invitation.sent_at = timezone.now() if sent else None
    invitation.send_error = None if sent else detail
    invitation.save(update_fields=["sent_at", "send_error"])

    if sent and account.subject_type == "learner":
        from learner_api.learner_progression import advance_learner_by_id

        advance_learner_by_id(account.subject_id)

    record(
        EVENT_INVITE_SENT,
        email=account.email,
        account_id=account.id,
        succeeded=sent,
        reason=None if sent else (detail or "send-failed")[:200],
        ip=ip,
        user_agent=user_agent,
    )
    return invitation, sent, detail


class TokenError(ValueError):
    """Raised when a token is unknown, already used, or expired."""


def _load_token(model, token):
    row = model.objects.select_related("account").filter(token_hash=hash_token(token)).first()
    # One message for all three failure modes: distinguishing "expired" from
    # "never existed" tells an attacker holding a guessed token whether it was
    # ever real.
    if row is None or row.used_at is not None or row.expires_at <= timezone.now():
        raise TokenError("This link is invalid or has expired. Please request a new one.")
    return row


def peek_invitation(token):
    """Validate an invitation without consuming it — for rendering the form.

    The set-password page calls this on load so it can greet the person and
    fail early on a dead link, rather than only discovering it on submit.
    """
    invitation = _load_token(Invitation, token)
    return {
        "email": invitation.account.email,
        "displayName": invitation.account.display_name,
        "role": invitation.account.role,
        "expiresAt": invitation.expires_at.isoformat(),
    }


@transaction.atomic(using="enrolment")
def accept_invitation(token, new_password, *, ip=None, user_agent=None):
    """Redeem an invitation: set the first password and activate the account."""
    invitation = _load_token(Invitation, token)
    account = invitation.account

    validate_password_strength(
        new_password, email=account.email, display_name=account.display_name
    )

    account.password_hash = hash_password(new_password)
    account.password_set_at = timezone.now()
    account.is_active = True
    account.failed_attempts = 0
    account.locked_until = None
    account.save(
        update_fields=[
            "password_hash", "password_set_at", "is_active",
            "failed_attempts", "locked_until", "updated_at",
        ]
    )

    invitation.used_at = timezone.now()
    invitation.save(update_fields=["used_at"])

    # An invitation being redeemed a second time (or a stale session from before
    # onboarding) should not survive setting the first password.
    revoke_all_for_account(account)

    record(
        EVENT_INVITE_ACCEPTED,
        email=account.email,
        account_id=account.id,
        succeeded=True,
        ip=ip,
        user_agent=user_agent,
    )
    return account


# ---------------------------------------------------------------------------
# Password resets
# ---------------------------------------------------------------------------

@transaction.atomic(using="enrolment")
def create_reset(account, *, ip=None):
    PasswordReset.objects.filter(account=account, used_at__isnull=True).update(
        used_at=timezone.now()
    )

    token = generate_token()
    reset = PasswordReset.objects.create(
        account=account,
        token_hash=hash_token(token),
        email=account.email,
        expires_at=timezone.now() + RESET_TTL,
        created_ip=ip,
    )
    return reset, token


def send_reset(account, *, ip=None, user_agent=None):
    """Create and email a password reset. Returns ``(reset, sent, detail)``."""
    reset, token = create_reset(account, ip=ip)

    subject, html, text = email_azure.reset_message(
        display_name=account.display_name,
        link=reset_link(token),
        expires_hours=int(RESET_TTL.total_seconds() // 3600) or 1,
    )
    sent, detail = email_azure.send_mail(
        to=account.email, subject=subject, html_body=html, text_body=text,
        save_to_sent=True,
    )

    reset.sent_at = timezone.now() if sent else None
    reset.send_error = None if sent else detail
    reset.save(update_fields=["sent_at", "send_error"])

    record(
        EVENT_RESET_REQUESTED,
        email=account.email,
        account_id=account.id,
        # Recorded as succeeded when the mail went out; the throttle counts these
        # rows regardless, so a failing transport cannot be used to bypass it.
        succeeded=sent,
        reason=None if sent else (detail or "send-failed")[:200],
        ip=ip,
        user_agent=user_agent,
    )
    return reset, sent, detail


def peek_reset(token):
    reset = _load_token(PasswordReset, token)
    return {
        "email": reset.account.email,
        "displayName": reset.account.display_name,
        "expiresAt": reset.expires_at.isoformat(),
    }


@transaction.atomic(using="enrolment")
def complete_reset(token, new_password, *, ip=None, user_agent=None):
    """Redeem a reset token: set the new password and sign the account out everywhere."""
    reset = _load_token(PasswordReset, token)
    account = reset.account

    validate_password_strength(
        new_password, email=account.email, display_name=account.display_name
    )

    account.password_hash = hash_password(new_password)
    account.password_set_at = timezone.now()
    # A successful reset is the documented way out of a lockout — the person has
    # proven control of the mailbox, which is a stronger signal than the failed
    # password attempts that locked it.
    account.failed_attempts = 0
    account.locked_until = None
    account.save(
        update_fields=[
            "password_hash", "password_set_at",
            "failed_attempts", "locked_until", "updated_at",
        ]
    )

    reset.used_at = timezone.now()
    reset.save(update_fields=["used_at"])

    revoke_all_for_account(account)

    record(
        EVENT_RESET_COMPLETED,
        email=account.email,
        account_id=account.id,
        succeeded=True,
        ip=ip,
        user_agent=user_agent,
    )
    return account
