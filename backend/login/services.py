"""The seam the three creation forms call into.

``learner_api`` must not import login views or reach into token internals; it
needs exactly one function: "this person was just created and the form said to
invite them — do the right thing."

``invite_subject`` is that function. It is deliberately forgiving about
*infrastructure* failure: a creation form's job is to create a person, and it
must not fail because the mail tenant is down. Problems come back in the return
value so the API response can surface them, and they land in the audit table
either way.

It is **not** forgiving about authorisation. Issuing an invitation mints a
credential, so ``invite_subject`` re-checks the caller itself rather than
trusting that whatever called it was gated — see ``_authorise``.
"""
from __future__ import annotations

import logging

from django.db import DatabaseError

from .identity import AccountError, describe_subject, ensure_account, fetch_subject
from .invitations import send_invitation
from .models import ROLE_ADMIN, ROLE_STAFF

logger = logging.getLogger("login")

#: Roles permitted to invite anybody at all.
_MAY_INVITE = frozenset({ROLE_ADMIN, ROLE_STAFF})


class InvitePermissionError(PermissionError):
    """The caller is not allowed to issue this invitation."""


def _authorise(inviter, target_role):
    """Check that ``inviter`` may issue an invitation conferring ``target_role``.

    Enforced here, at the point the credential is created, rather than only on
    the view. The three creation endpoints in ``learner_api`` are the reason:
    they are ``@csrf_exempt`` with no auth decorator of their own, so a check
    that lived only on the caller could be reintroduced-by-omission the next
    time an endpoint learns to invite. A privilege boundary should not depend on
    every call site remembering it exists.

    ``inviter`` is a ``LoginAccount`` or None (anonymous).
    """
    if inviter is None:
        raise InvitePermissionError(
            "You must be signed in to invite someone to the platform."
        )
    if not inviter.is_active:
        raise InvitePermissionError("Your account is not active.")
    if inviter.role not in _MAY_INVITE:
        raise InvitePermissionError(
            "Only staff and administrators can invite people to the platform."
        )
    # Only an admin can create another admin. Otherwise any staff member could
    # promote themselves by creating an "Admin"-position colleague at an address
    # they control — the role is derived from the Position field on a form they
    # are already allowed to submit.
    if target_role == ROLE_ADMIN and inviter.role != ROLE_ADMIN:
        raise InvitePermissionError(
            "Only an administrator can invite another administrator."
        )
    return True


def invite_subject(subject_type, subject_id, *, subject=None, inviter=None,
                   invited_by=None, ip=None, user_agent=None, send_email=True):
    """Ensure an account exists for a person, and optionally email them.

    ``send_email=False`` provisions the account and stops there: the person
    appears on the Accounts page with no password set, and an administrator
    sends the invitation when they choose to with the button there. That is what
    the creation forms now do -- enrolling somebody should not put a live
    set-password link in their inbox before anybody has checked the record.

    The account is still created either way, because the Accounts page lists
    accounts: skipping it would leave the new person invisible on the very page
    the invitation button lives on.

    ``inviter`` is the ``LoginAccount`` of the signed-in caller, or None for an
    anonymous request. It is **required** in practice: an anonymous caller is
    refused, because issuing an invitation creates a credential.

    Returns a small dict describing what happened; never raises. Shape:

        {"invited": bool, "emailSent": bool, "accountCreated": bool,
         "error": str | None, "expiresAt": str | None, "forbidden": bool}

    ``invited`` is True when an invitation row was written, whether or not the
    email itself went out — the link is real and can be re-sent. ``forbidden``
    is True when the caller was not allowed to invite, so the view can answer
    403 rather than reporting a generic failure.
    """
    result = {
        "invited": False,
        "emailSent": False,
        "accountCreated": False,
        "error": None,
        "expiresAt": None,
        "forbidden": False,
        # True when an account was provisioned but deliberately not emailed, so
        # the console can say "account created - send the invitation from
        # Accounts" rather than implying a mail failure.
        "awaitingInvitation": False,
        # True when the address already belongs to somebody who has signed in,
        # so their existing account was reused and no invitation applies.
        "alreadyOnboarded": False,
    }

    # Resolve the role this invitation would confer *before* creating anything,
    # so a refused invitation leaves no account behind.
    try:
        target = subject if subject is not None else fetch_subject(subject_type, subject_id)
        _, _, target_role = describe_subject(subject_type, target)
    except (ValueError, DatabaseError) as exc:
        result["error"] = str(exc)
        return result

    try:
        _authorise(inviter, target_role)
    except InvitePermissionError as exc:
        logger.warning(
            "Refused invitation for %s:%s (role=%s) from %s",
            subject_type, subject_id, target_role,
            inviter.email if inviter else "anonymous",
        )
        result["error"] = str(exc)
        result["forbidden"] = True
        return result

    try:
        account, created = ensure_account(subject_type, subject_id, subject=subject)
    except AccountError as exc:
        # The commonest case by far: the form was submitted with the invite
        # toggle on but no email address filled in.
        result["error"] = str(exc)
        return result
    except DatabaseError as exc:
        logger.exception("Could not create login account for %s:%s", subject_type, subject_id)
        result["error"] = f"Database error: {exc}"
        return result

    result["accountCreated"] = created

    # The address already belongs to somebody who has signed in -- a staff
    # member or admin now also being enrolled as a learner. `ensure_account`
    # deliberately returned their existing account rather than minting a second
    # one, so there is nothing to invite: they already have a password, and an
    # invitation is a *set-password* link. Mailing one here would put a live
    # credential for an admin account in an inbox on the say-so of an enrolment
    # form, which is precisely the thing send_email=False exists to prevent.
    if not created and account.has_password:
        result["alreadyOnboarded"] = True
        return result

    if not send_email:
        # Provisioned, not invited. No invitation row is written either: a token
        # nobody has been sent is a live credential with no audit trail behind
        # it, and the Accounts button mints a fresh one when it is actually
        # wanted.
        result["awaitingInvitation"] = True
        return result

    try:
        invitation, sent, detail = send_invitation(
            account, invited_by=invited_by, ip=ip, user_agent=user_agent
        )
    except DatabaseError as exc:
        logger.exception("Could not create invitation for %s", account.email)
        result["error"] = f"Database error: {exc}"
        return result

    result["invited"] = True
    result["emailSent"] = sent
    result["expiresAt"] = invitation.expires_at.isoformat()
    if not sent:
        result["error"] = detail
    return result


def invite_eligible_applicant(learner, *, ip=None, user_agent=None):
    """Invite a learner just created from the public eligibility form.

    The one invitation issued with no signed-in inviter: the applicant passed
    the form's postcode check, and the project owner asked for the set-password
    email to follow automatically. It is kept narrow so it cannot become a way
    round ``_authorise``:

    * the subject is always a learner row, so the role is always learner --
      never staff, admin or employer;
    * only a brand-new account with no password is invited. An address that
      already signs in (``ensure_account`` hands back its existing account)
      gets nothing, so this never mails a set-password link for a live login.

    Returns ``(sent, detail)``: ``sent`` is False when no invitation went out,
    with ``detail`` saying why. Never raises for a mail failure; the invitation
    row records it and staff can re-send from Accounts.
    """
    from .models import SUBJECT_LEARNER

    account, created = ensure_account(SUBJECT_LEARNER, learner.id, subject=learner)
    if not created or account.has_password or account.subject_type != SUBJECT_LEARNER:
        return False, "existing-account"
    _invitation, sent, detail = send_invitation(
        account, invited_by="eligibility form", ip=ip, user_agent=user_agent
    )
    return sent, detail


def sync_account(subject_type, subject_id, *, subject=None):
    """Keep an existing login account's email/name/role in step with an edit.

    Called from the update paths of the creation forms. Does nothing when the
    person has no account yet — editing somebody who was never invited should
    not silently create a sign-in identity for them.
    """
    from .models import LoginAccount

    try:
        if not LoginAccount.objects.filter(
            subject_type=subject_type, subject_id=subject_id
        ).exists():
            return None
        account, _ = ensure_account(subject_type, subject_id, subject=subject)
        return account
    except (AccountError, DatabaseError):
        logger.exception("Could not sync login account for %s:%s", subject_type, subject_id)
        return None


def request_password_link(email, *, ip=None, user_agent=None):
    """Answer a "forgot password" request for ``email``. Never raises for a miss.

    Somebody who already has a password gets the usual reset email. An enrolled
    learner who has never set one -- invited but not activated, or in
    enrolment."Created_users" with no login account yet -- gets the set-password
    invitation instead (provisioning the account if needed), because "reset" is
    the wrong word for a password that does not exist yet.

    Only an unambiguous match is acted on: one active account, or exactly one
    learner record and no login account of any kind for the address. A
    deactivated account is never revived and a staff account is never minted
    from here. The caller answers every address identically, so none of this is
    visible to whoever typed it.
    """
    from learner_api.models import EnrolmentUser

    from .identity import account_for_email
    from .invitations import record, send_reset
    from .models import EVENT_RESET_REQUESTED, SUBJECT_LEARNER, LoginAccount

    def invite(account):
        _invitation, sent, detail = send_invitation(account, ip=ip, user_agent=user_agent)
        # Counted by reset_requests_exhausted like a reset email, so the form
        # cannot be used to flood a learner's inbox with invitations.
        record(
            EVENT_RESET_REQUESTED, email=account.email, account_id=account.id, succeeded=sent,
            reason="set-password-invitation" if sent else (detail or "send-failed")[:200],
            ip=ip, user_agent=user_agent,
        )

    account = account_for_email(email)
    if account is not None:
        if account.subject_type == SUBJECT_LEARNER and not account.has_password:
            invite(account)
        else:
            send_reset(account, ip=ip, user_agent=user_agent)
        return
    if LoginAccount.objects.filter(email=email).exists():
        # Two active accounts share the address, or its account is deactivated.
        return
    learner_ids = list(EnrolmentUser.all_learners.filter(email__iexact=email).values_list("pk", flat=True)[:2])
    if len(learner_ids) != 1:
        return
    try:
        account, _created = ensure_account(SUBJECT_LEARNER, learner_ids[0])
    except AccountError:
        return
    if account.is_active and not account.has_password and account.subject_type == SUBJECT_LEARNER:
        invite(account)


def issue_invitation_link(subject_type, subject_id, *, inviter, ip=None, user_agent=None):
    """Mint a learner's set-password link for staff to pass on themselves.

    For learners whose employer's mail filter blocks our domain: staff copy the
    link from the Users screen and send it over Teams, WhatsApp or SMS. It is
    the ordinary single-use invitation (7-day expiry, supersedes any earlier
    one, so a previously emailed link stops working), recorded as delivered --
    the learner has been invited, by another channel -- so the progression
    rules that wait on "invitation sent" treat it exactly like an email.

    Same authorisation as ``invite_subject``. Returns a dict with ``link`` and
    ``expiresAt`` on success, or ``error`` (and ``forbidden``) otherwise.
    """
    from .invitations import invitation_link
    from .models import SUBJECT_LEARNER

    result = {"link": None, "expiresAt": None, "accountCreated": False, "error": None, "forbidden": False}
    if subject_type != SUBJECT_LEARNER:
        result["error"] = "Set-password links can only be copied for learners."
        return result
    try:
        target = fetch_subject(subject_type, subject_id)
        if target is None:
            raise AccountError("That record no longer exists.")
        _, _, target_role = describe_subject(subject_type, target)
        _authorise(inviter, target_role)
        account, created = ensure_account(subject_type, subject_id, subject=target)
    except InvitePermissionError as exc:
        result["error"], result["forbidden"] = str(exc), True
        return result
    except (AccountError, ValueError) as exc:
        result["error"] = str(exc)
        return result
    result["accountCreated"] = created
    if account.subject_type != SUBJECT_LEARNER or account.has_password:
        # Already signs in (or the address belongs to a staff account): a
        # set-password link would be a live credential for an existing login.
        result["error"] = "This person has already set a password. Send a password reset instead."
        return result
    if not account.is_active:
        result["error"] = "This account is suspended. Restore it from Accounts first."
        return result

    invitation, token = _delivered_invitation(
        account, invited_by=inviter.email, reason=f"link issued to {inviter.email} to deliver (not emailed)",
        ip=ip, user_agent=user_agent,
    )
    result["link"] = invitation_link(token)
    result["expiresAt"] = invitation.expires_at.isoformat()
    return result


def _delivered_invitation(account, *, invited_by, reason, ip=None, user_agent=None):
    """An invitation handed over without email, recorded as delivered.

    The learner has been invited -- by another channel -- so the progression
    rules that wait on "invitation sent" treat it exactly like an email.
    """
    from django.utils import timezone

    from learner_api.learner_progression import advance_learner_by_id

    from .invitations import create_invitation, record
    from .models import EVENT_INVITE_SENT

    invitation, token = create_invitation(account, invited_by=invited_by, ip=ip)
    invitation.sent_at = timezone.now()
    invitation.send_error = None
    invitation.save(update_fields=["sent_at", "send_error"])
    advance_learner_by_id(account.subject_id)
    record(
        EVENT_INVITE_SENT, email=account.email, account_id=account.id, succeeded=True,
        reason=reason[:200], ip=ip, user_agent=user_agent,
    )
    return invitation, token


def default_password_setup(email, *, ip=None, user_agent=None):
    """The set-password page path for a learner signing in with the default
    password (``security.DEFAULT_LEARNER_PASSWORD``), or None.

    Only a learner who has never set a password qualifies: an active account
    with no password, or one learner record in enrolment."Created_users" and no
    login account of any kind for the address (provisioned here). A deactivated,
    locked or ambiguous account, staff or employer, or anyone who already has a
    password gets None -- the caller then answers like any failed sign-in.
    No session is issued: the learner sets their own password first.
    """
    from learner_api.models import EnrolmentUser

    from .identity import account_for_email
    from .models import SUBJECT_LEARNER, LoginAccount
    from .security import is_locked

    account = account_for_email(email)
    if account is None:
        if LoginAccount.objects.filter(email=email).exists():
            return None
        learner_ids = list(EnrolmentUser.all_learners.filter(email__iexact=email).values_list("pk", flat=True)[:2])
        if len(learner_ids) != 1:
            return None
        try:
            account, _created = ensure_account(SUBJECT_LEARNER, learner_ids[0])
        except AccountError:
            return None
    if (account.subject_type != SUBJECT_LEARNER or account.has_password
            or not account.is_active or is_locked(account)):
        return None
    _invitation, token = _delivered_invitation(
        account, invited_by="default-password sign-in",
        reason="set-password link issued at sign-in with the default password",
        ip=ip, user_agent=user_agent,
    )
    return f"/set-password?token={token}"
