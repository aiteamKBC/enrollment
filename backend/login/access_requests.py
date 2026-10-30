"""Let a signed-in account with no access grant ask for one.

Why this exists
---------------
Position no longer grants anything (see ``identity.role_for_staff``), so a newly
created staff account arrives with no access and can open nothing. That is the
correct default — the alternative was every created account silently becoming a
full platform administrator — but it leaves the person staring at a wall. This is
the door out of it: the SPA lands them on ``/access-required`` and they press one
button, which mails an administrator.

The administrator address comes from ``ACCESS_REQUEST_RECIPIENT`` so it is not
hard-coded to one person, falling back to the address the platform owner gave.

Two deliberate choices:

* **Server-side send, not a ``mailto:`` link.** A mailto depends on the person
  having a mail client configured, and silently does nothing when they do not.
  The platform already has a working Graph sender.
* **Throttled per account.** The button is the only thing on the page, so it will
  be pressed repeatedly. One request per account per window keeps an impatient
  new starter from filling the administrator's inbox, while still reporting
  success to them — the mail they are waiting for has already been sent.
"""
from __future__ import annotations

import os

from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import email_azure
from .models import LoginAudit
from .permissions import login_required

#: Who receives access requests. Overridable so this does not depend on one
#: person's mailbox continuing to exist.
DEFAULT_RECIPIENT = "Mahmoud.Fouda@kentbusinesscollege.com"

#: Audit event name, so requests are visible in the access log like everything
#: else the auth layer does.
EVENT_ACCESS_REQUESTED = "access_requested"

#: One request per account per this many minutes.
THROTTLE_MINUTES = 30


def recipient():
    return (os.environ.get("ACCESS_REQUEST_RECIPIENT") or DEFAULT_RECIPIENT).strip()


def _console_url():
    """Deep link to the Accounts screen, where the grant is actually made."""
    base = (os.environ.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")
    return f"{base}/admin/users"


def _recently_requested(account):
    """Whether this account already asked inside the throttle window."""
    since = timezone.now() - timezone.timedelta(minutes=THROTTLE_MINUTES)
    try:
        return LoginAudit.objects.filter(
            account_id=account.id,
            event=EVENT_ACCESS_REQUESTED,
            succeeded=True,
            created_at__gt=since,
        ).exists()
    except DatabaseError:
        # Cannot prove they asked recently — let it through rather than blocking
        # the only action available to them.
        return False


@csrf_exempt
@require_POST
@login_required
def request_access(request):
    """Email an administrator that this account is waiting for an access grant."""
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return JsonResponse(
            {"error": "Missing X-Requested-With header.", "code": "csrf"}, status=403
        )

    account = request.login_account

    if _recently_requested(account):
        # Reported as success: from where they are sitting, the request they want
        # made has been made. Saying "too many requests" would read as a failure.
        return JsonResponse({
            "ok": True,
            "alreadySent": True,
            "sentTo": recipient(),
            "message": "Your request has already been sent. An administrator will be in touch.",
        })

    subject, html, text = email_azure.access_request_message(
        requester_name=account.display_name,
        requester_email=account.email,
        console_url=_console_url(),
    )
    sent, detail = email_azure.send_mail(
        to=recipient(), subject=subject, html_body=html, text_body=text
    )

    try:
        LoginAudit.objects.create(
            event=EVENT_ACCESS_REQUESTED,
            email=account.email,
            account_id=account.id,
            succeeded=sent,
            reason=None if sent else (detail or "send-failed")[:200],
        )
    except DatabaseError:
        pass

    if not sent:
        # Told plainly, because the alternative is waiting for a mail that will
        # never arrive. The recipient address is shown so they can email directly.
        return JsonResponse({
            "ok": False,
            "sentTo": recipient(),
            "error": "We could not send the request automatically. Please email the address below directly.",
        }, status=502)

    return JsonResponse({
        "ok": True,
        "alreadySent": False,
        "sentTo": recipient(),
        "message": "Your request has been sent. An administrator will grant your access shortly.",
    })
