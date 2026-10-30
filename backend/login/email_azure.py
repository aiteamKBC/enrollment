"""Transactional email via Microsoft Graph (Azure AD application permissions).

Used for the two mails this feature sends: the platform invitation and the
password reset. Both are security-sensitive, so the transport is explicit about
failure — a mail that did not send is recorded on the token row
(``Send_error``) rather than being swallowed.

Why Graph and not SMTP
----------------------
The tenant is already Microsoft 365 and the project already talks to Graph
(``learner_api/calendar_connections.py``). Graph with an app-only token needs no
mailbox password and can be scoped to a single sender via an application access
policy, which SMTP AUTH cannot.

Configuration
-------------
Set in ``backend/.env``. See ``AZURE_SETUP.md`` for how to obtain them.

    AZURE_MAIL_TENANT_ID       # directory (tenant) id of the app registration
    AZURE_MAIL_CLIENT_ID       # application (client) id
    AZURE_MAIL_CLIENT_SECRET   # client secret VALUE (not the secret id)
    AZURE_MAIL_SENDER          # the mailbox to send as, e.g. noreply@…
    AZURE_MAIL_ENABLED         # "false" to force console fallback

This deployment registered its mail app under different names, so each setting
also accepts the ``AZURE_LOGIN_APP_*`` / ``AZURE_EMAIL`` spelling actually
present in ``.env`` (app ``LMS_Email_login_and_Invitations``):

    AZURE_LOGIN_APP_TENANT_ID      -> AZURE_MAIL_TENANT_ID
    AZURE_LOGIN_APP_CLIENT_ID      -> AZURE_MAIL_CLIENT_ID
    AZURE_LOGIN_APP_CLIENT_SECRET  -> AZURE_MAIL_CLIENT_SECRET
    AZURE_EMAIL                    -> AZURE_MAIL_SENDER

The app registration needs the **application** permission ``Mail.Send``
(not delegated), with admin consent granted. Confirm with a client-credentials
token: its ``roles`` claim must contain ``Mail.Send``. An app registered only for
interactive sign-in (one with a redirect URI) will not have it by default, and
the failure surfaces as a Graph 403 at send time rather than at startup.

There is deliberately **no** fallback to the tenant-wide ``MICROSOFT_*``
credentials. Those belong to the calendar app, which has no ``Mail.Send``; with a
sender configured, falling back to them would flip ``is_configured()`` to True
and turn an honest "not configured" into an opaque 403 on every send.

Falls back to logging when it is not configured, so the whole invitation and
reset flow is exercisable end-to-end before Azure exists. The full link — which
contains a live single-use token — is printed only when ``DEBUG`` is on; with
DEBUG off the fallback logs that a send was skipped and no token. Either way
``send_mail`` reports ``configured=False`` so callers can tell the difference
between "sent" and "printed to a console".
"""
from __future__ import annotations

import logging
import os
import threading
import time
from functools import lru_cache
from html import escape
from pathlib import Path

import httpx

logger = logging.getLogger("login.email")

GRAPH_BASE = os.environ.get("MICROSOFT_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0")
_TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_SCOPE = "https://graph.microsoft.com/.default"

_HTTP_TIMEOUT = 15.0

# Cached app-only token. Tokens last ~1 hour; re-fetching per email would add a
# round-trip and risk throttling. Guarded by a lock because Daphne serves
# requests from several threads.
_token_lock = threading.Lock()
_token_cache = {"value": None, "expires_at": 0.0}
#: Refresh this many seconds before actual expiry.
_TOKEN_SKEW = 120


class EmailNotConfigured(RuntimeError):
    """Raised internally when Graph credentials are absent."""


class EmailSendError(RuntimeError):
    """Raised when Graph rejected the send."""


def _setting(name, default=""):
    return (os.environ.get(name) or default).strip()


#: Each mail setting and the env names it accepts, most-preferred first. The
#: ``AZURE_LOGIN_APP_*`` / ``AZURE_EMAIL`` spellings are what this deployment's
#: .env actually uses; the ``AZURE_MAIL_*`` names stay primary because they are
#: what AZURE_SETUP.md documents and what a fresh deployment would copy.
#:
#: Note what is absent: ``MICROSOFT_*``. See the module docstring — reusing the
#: calendar app's credentials here produces a confident 403, not a working send.
_SETTING_SOURCES = {
    "tenant_id": ("AZURE_MAIL_TENANT_ID", "AZURE_LOGIN_APP_TENANT_ID"),
    "client_id": ("AZURE_MAIL_CLIENT_ID", "AZURE_LOGIN_APP_CLIENT_ID"),
    "client_secret": ("AZURE_MAIL_CLIENT_SECRET", "AZURE_LOGIN_APP_CLIENT_SECRET"),
    "sender": ("AZURE_MAIL_SENDER", "AZURE_EMAIL"),
}


def _first_set(names):
    """First non-empty value among ``names``, else ""."""
    for name in names:
        value = _setting(name)
        if value:
            return value
    return ""


def mail_config():
    """Current mail settings, resolved across the accepted env spellings."""
    config = {key: _first_set(names) for key, names in _SETTING_SOURCES.items()}
    config["enabled"] = (
        _setting("AZURE_MAIL_ENABLED", "true").lower() not in {"0", "false", "no", "off"}
    )
    return config


def is_configured():
    """Whether a real send can be attempted right now."""
    cfg = mail_config()
    return bool(
        cfg["enabled"]
        and cfg["tenant_id"]
        and cfg["client_id"]
        and cfg["client_secret"]
        and cfg["sender"]
    )


def missing_settings():
    """Which required settings are absent — surfaced by the health endpoint.

    Names both accepted spellings, so an operator reading the system-status page
    can set either without having to consult the source to learn the other
    exists. Values are never included: this list is published by an endpoint.
    """
    cfg = mail_config()
    return [
        " or ".join(names)
        for key, names in _SETTING_SOURCES.items()
        if not cfg[key]
    ]


def _access_token(force_refresh=False):
    """Client-credentials token for Graph, cached until shortly before expiry."""
    cfg = mail_config()
    if not is_configured():
        raise EmailNotConfigured("Azure mail is not configured.")

    with _token_lock:
        now = time.time()
        if not force_refresh and _token_cache["value"] and _token_cache["expires_at"] > now:
            return _token_cache["value"]

        response = httpx.post(
            _TOKEN_ENDPOINT.format(tenant=cfg["tenant_id"]),
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "scope": _SCOPE,
                "grant_type": "client_credentials",
            },
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            # The body carries AADSTS codes that make misconfiguration
            # diagnosable (wrong secret, wrong tenant, consent not granted).
            raise EmailSendError(
                f"Azure token request failed ({response.status_code}): {response.text[:400]}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise EmailSendError("Azure token response contained no access_token.")

        _token_cache["value"] = token
        _token_cache["expires_at"] = now + max(
            int(payload.get("expires_in", 3600)) - _TOKEN_SKEW, 60
        )
        return token


def _file_attachment(item):
    import base64
    attachment = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": item["name"],
        "contentType": item.get("content_type") or "application/octet-stream",
        "contentBytes": base64.b64encode(item["content"]).decode("ascii"),
    }
    content_id = str(item.get("content_id") or "").strip()
    if content_id:
        # The HTML names inline parts as ``cid:<content_id>``. Keep the
        # explicit flag supported by RSVP/event mail while defaulting CID
        # attachments, including the brand logo, to inline rendering.
        attachment["contentId"] = content_id
        attachment["isInline"] = bool(item.get("is_inline", True))
    return attachment


#: The crest, carried by any message whose HTML names it. See
#: ``brand_logo_attachment`` for why it is embedded rather than linked.
BRAND_LOGO_CID = "kbc-brand-logo"
_BRAND_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "report_logo_kent.png"


@lru_cache(maxsize=1)
def _brand_logo_bytes():
    """Read the logo once per process and let mail fall back to its alt text."""
    try:
        return _BRAND_LOGO_PATH.read_bytes()
    except OSError:
        logger.warning(
            "Brand logo missing at %s -- mail will fall back to its alt text.",
            _BRAND_LOGO_PATH,
        )
        return b""


def brand_logo_attachment():
    """Return the embedded KBC logo, or None when the asset is unavailable."""
    raw = _brand_logo_bytes()
    if not raw:
        return None
    return {
        "name": "kent-business-college.png",
        "content_type": "image/png",
        "content": raw,
        "content_id": BRAND_LOGO_CID,
    }


def send_mail(*, to, subject, html_body, text_body=None, sender_name=None, save_to_sent=False, attachments=None):
    """Send one message. Returns ``(sent, detail)``.

    ``save_to_sent`` keeps a copy in the sender mailbox's Sent Items. Off by
    default so routine notifications do not pile up in the shared mailbox;
    invitations and password resets opt in so staff can see what was sent.

    ``attachments`` is an optional list of ``{"name", "content_type", "content"}``
    (content as bytes), sent as Graph file attachments -- e.g. a calendar invite.
    An item may also carry ``content_id`` and an optional ``is_inline`` flag.
    The brand logo is appended automatically when the HTML references its CID.

    ``sent`` is True only when Graph accepted it. When Azure is not configured
    this returns ``(False, "not-configured: …")`` after logging the message —
    the caller records that on the token row and, importantly, still reports
    success to the end user, because whether our mail transport is set up is not
    something an anonymous caller should be able to probe.
    """
    if not is_configured():
        missing = ", ".join(missing_settings()) or "AZURE_MAIL_ENABLED=false"

        # The body carries a live single-use invitation/reset token. Print it
        # only when DEBUG is on, where the developer needs the link to walk
        # through the flow and the log is their own console. With DEBUG off,
        # log that a send was skipped and nothing more: aggregated production
        # logs are read by more people than should be able to seize an account.
        from django.conf import settings

        if settings.DEBUG:
            logger.warning(
                "Azure mail not configured (%s). Message NOT sent.\n"
                "  To:      %s\n  Subject: %s\n  Body:\n%s",
                missing, to, subject, text_body or html_body,
            )
        else:
            logger.error(
                "Azure mail not configured (%s). Message to %s NOT sent "
                "(subject: %s). See backend/AZURE_SETUP.md.",
                missing, to, subject,
            )
        return False, f"not-configured: {missing}"

    # A template shows the logo by naming its cid; carrying it is this
    # function's job, so no call site has to know the picture exists.
    if html_body and f"cid:{BRAND_LOGO_CID}" in html_body:
        logo = brand_logo_attachment()
        if logo:
            attachments = [*(attachments or []), logo]

    cfg = mail_config()
    try:
        token = _access_token()
        response = httpx.post(
            f"{GRAPH_BASE}/users/{cfg['sender']}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html_body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                    **({"attachments": [_file_attachment(item) for item in attachments]} if attachments else {}),
                    # Keep the configured mailbox as the authenticated sender,
                    # while showing the staff member who initiated the message
                    # in clients that honour the Graph display name.
                    **({"from": {"emailAddress": {"address": cfg["sender"], "name": sender_name.strip()}}}
                       if isinstance(sender_name, str) and sender_name.strip() else {}),
                },
                "saveToSentItems": bool(save_to_sent),
            },
            timeout=_HTTP_TIMEOUT,
        )
    except EmailNotConfigured as exc:
        return False, str(exc)
    except (EmailSendError, httpx.HTTPError) as exc:
        logger.error("Azure mail send failed for %s: %s", to, exc)
        return False, str(exc)[:500]

    # 202 Accepted is the documented success for sendMail.
    if response.status_code in (200, 202):
        logger.info("Invitation/reset mail sent to %s", to)
        return True, None

    detail = f"graph {response.status_code}: {response.text[:400]}"
    logger.error("Azure mail send failed for %s: %s", to, detail)
    return False, detail


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------
# Plain inline-styled HTML on purpose: mail clients strip <style> blocks and do
# not load external CSS, so a stylesheet would render as unstyled text.

_BRAND = "Kent Business College"


def _shell(heading, intro, button_label, link, footer, extra=""):
    return f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#f4f5f7;font-family:Segoe UI,Arial,sans-serif;color:#1f2933;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e4e7eb;">
      <tr>
        <td style="background:#0b3d6b;padding:20px 28px;color:#ffffff;font-size:18px;font-weight:600;">{_BRAND}</td>
      </tr>
      <tr>
        <td style="padding:28px;">
          <h1 style="margin:0 0 12px;font-size:20px;color:#0b3d6b;">{heading}</h1>
          <p style="margin:0 0 20px;font-size:15px;line-height:1.55;">{intro}</p>
          <p style="margin:0 0 24px;">
            <a href="{link}" style="display:inline-block;background:#0b3d6b;color:#ffffff;text-decoration:none;padding:12px 22px;border-radius:6px;font-size:15px;font-weight:600;">{button_label}</a>
          </p>
          <p style="margin:0 0 8px;font-size:13px;color:#616e7c;">If the button does not work, copy this link into your browser:</p>
          <p style="margin:0 0 24px;font-size:12px;word-break:break-all;color:#0b3d6b;">{link}</p>
          <p style="margin:0;font-size:13px;color:#616e7c;line-height:1.5;">{footer}</p>{extra}
        </td>
      </tr>
      <tr>
        <td style="padding:16px 28px;background:#f9fafb;font-size:12px;color:#9aa5b1;border-top:1px solid #e4e7eb;">
          This is an automated message from the {_BRAND} learning platform. Please do not reply.
        </td>
      </tr>
    </table>
  </body>
</html>"""


# The invitation uses the learner workspace palette (frontend index.css --kbc-*),
# so the first thing a new learner sees matches the platform they are joining.
_LEARNER_DEEP = "#4B168C"
_LEARNER_PRIMARY = "#5B21B6"
_LEARNER_ACCENT = "#8B5CF6"
_LEARNER_SOFT = "#F3EEFF"
_LEARNER_WASH = "#F7F5FF"
_LEARNER_BORDER = "#E5DDF1"
_LEARNER_TEXT = "#241638"
_LEARNER_MUTED = "#72657F"

# Invitation copy, as approved for the move to the new LMS. Kept as data so the
# HTML and plain-text bodies cannot drift apart.
_INVITE_READY = "Your account is ready on the new Kent Business College learning platform."
_INVITE_START_WITH_BOOKING = (
    "Getting started is simple: set your password, log in, and book a short "
    "introduction with your case owner."
)
# Without a reachable case owner there is no booking step to mention.
_INVITE_START = "Getting started is simple: set your password and log in."
_INVITE_PURPOSE = "The new platform is designed to make your learning easier and reduce unnecessary admin."
_PLATFORM_BENEFITS_TITLE = "Why you’ll love the new LMS"
_PLATFORM_BENEFITS = (
    ("No more double work.",
     "You won’t need to complete your learning on the LMS and then upload the same "
     "progress separately to Aptem. With the new system, you complete your work "
     "once, in one place."),
    ("Everything in one place.",
     "Your learning activities, live sessions and progress are all available "
     "directly inside the new LMS, so it’s much easier to know what you need to do "
     "and where to find it."),
    ("Simple to get started.",
     "We know moving to a new system can feel like a big change, so we’ve made the "
     "process as straightforward as possible. Your case owner will also be there to "
     "help you get comfortable with the platform."),
)
_OPTIONAL_MOVE_TITLE = "Give it a try — there’s no pressure."
_OPTIONAL_MOVE = (
    "Moving to the new LMS is optional. You can continue using your current LMS and "
    "Aptem while you get familiar with the new platform, then switch when you feel ready."
)


def _email_button(label, link, *, primary=True):
    """A table-built button: Outlook ignores padding on a bare <a>."""
    background, colour = (_LEARNER_PRIMARY, "#ffffff") if primary else ("#ffffff", _LEARNER_PRIMARY)
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:separate;">
                    <tr>
                      <td bgcolor="{background}" style="background:{background};border:2px solid {_LEARNER_PRIMARY};border-radius:10px;">
                        <a href="{link}" style="display:inline-block;padding:12px 24px;font-size:15px;font-weight:600;color:{colour};text-decoration:none;border-radius:10px;">{label}</a>
                      </td>
                    </tr>
                  </table>"""


def _invitation_step(number, title, body, button):
    """One numbered card; ``number=None`` for a lone step, which needs no numbering."""
    badge = f"""
                  <td width="44" valign="top" style="padding:22px 0 22px 22px;">
                    <div style="width:32px;height:32px;line-height:32px;border-radius:16px;background:{_LEARNER_SOFT};color:{_LEARNER_PRIMARY};font-size:15px;font-weight:700;text-align:center;">{number}</div>
                  </td>""" if number else ""
    return f"""
          <tr>
            <td style="padding:0 32px 16px;">
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border:1px solid {_LEARNER_BORDER};border-radius:14px;background:#ffffff;">
                <tr>{badge}
                  <td valign="top" style="padding:22px 22px 22px {"14px" if number else "22px"};">
                    <p style="margin:2px 0 6px;font-size:17px;font-weight:700;color:{_LEARNER_TEXT};">{title}</p>
                    {body}
                    {button}
                  </td>
                </tr>
              </table>
            </td>
          </tr>"""


def _initials(name):
    parts = [part for part in str(name).split() if part[:1].isalpha()]
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def _benefits_box():
    items = "".join(
        f'<tr><td valign="top" style="padding:8px 10px 8px 0;font-size:15px;font-weight:700;color:{_LEARNER_ACCENT};">&#10003;</td>'
        f'<td style="padding:8px 0;font-size:14px;line-height:1.55;color:{_LEARNER_TEXT};">'
        f'<strong style="display:block;margin-bottom:2px;color:{_LEARNER_DEEP};">{escape(title)}</strong>{escape(detail)}</td></tr>'
        for title, detail in _PLATFORM_BENEFITS
    )
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:16px 0 0;">
                      <tr>
                        <td style="padding:14px 16px;background:{_LEARNER_WASH};border-left:3px solid {_LEARNER_ACCENT};border-radius:8px;">
                          <p style="margin:0 0 4px;font-size:13px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:{_LEARNER_PRIMARY};">{escape(_PLATFORM_BENEFITS_TITLE)}</p>
                          <table role="presentation" cellpadding="0" cellspacing="0">{items}</table>
                        </td>
                      </tr>
                    </table>"""


def _optional_move_box():
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="margin:18px 0 0;">
                      <tr>
                        <td style="padding:14px 16px;border:1px solid {_LEARNER_BORDER};border-radius:8px;background:#ffffff;">
                          <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:{_LEARNER_DEEP};">{escape(_OPTIONAL_MOVE_TITLE)}</p>
                          <p style="margin:0;font-size:14px;line-height:1.55;color:{_LEARNER_TEXT};">{escape(_OPTIONAL_MOVE)}</p>
                        </td>
                      </tr>
                    </table>"""


def invitation_message(*, display_name, link, expires_days, booking_owner=None, booking_link=None):
    """The account invitation. ``booking_owner``/``booking_link`` add the
    one-to-one LMS introduction as a second step; both are needed, so a
    half-known case owner simply leaves it out.

    The booking link is separate from the password link and reusable (see
    ``login.lms_introduction``), so the once-only expiry note sits with step 1.
    """
    greeting = f"Hello {display_name}," if display_name else "Hello,"
    subject = f"Your {_BRAND} account — set your password"
    booking = bool(booking_owner and booking_link)
    start = _INVITE_START_WITH_BOOKING if booking else _INVITE_START
    paragraph = f'<p style="margin:0 0 10px;font-size:15px;line-height:1.6;color:{_LEARNER_TEXT};">'
    safe_link = escape(link, quote=True)
    steps = _invitation_step(
        1 if booking else None, "Set your password",
        f'<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:{_LEARNER_MUTED};">Activate your account '
        f"and sign in. This link works once and expires in {expires_days} days.</p>",
        _email_button("Set your password", safe_link),
    )
    fallback_links = (
        f'<p style="margin:0 0 4px;font-size:12px;color:{_LEARNER_MUTED};">Set your password:</p>'
        f'<p style="margin:0 0 12px;font-size:12px;word-break:break-all;"><a href="{safe_link}" style="color:{_LEARNER_PRIMARY};">{safe_link}</a></p>'
    )
    if booking:
        owner, safe_booking = escape(booking_owner), escape(booking_link, quote=True)
        steps += _invitation_step(
            2, "Book your LMS introduction",
            f"""<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 14px;">
                      <tr>
                        <td valign="middle" style="width:40px;height:40px;border-radius:20px;background:{_LEARNER_PRIMARY};color:#ffffff;font-size:14px;font-weight:700;text-align:center;">{escape(_initials(booking_owner))}</td>
                        <td valign="middle" style="padding-left:12px;">
                          <p style="margin:0;font-size:14px;font-weight:700;color:{_LEARNER_TEXT};">{owner}</p>
                          <p style="margin:2px 0 0;font-size:12px;color:{_LEARNER_MUTED};">Your case owner</p>
                        </td>
                      </tr>
                    </table>
                    <p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:{_LEARNER_MUTED};">A short one-to-one on Microsoft Teams where {owner} shows you around the platform. Pick a time that suits you and you will get a Teams invitation by email.</p>""",
            _email_button("Book my LMS introduction", safe_booking, primary=False),
        )
        fallback_links += (
            f'<p style="margin:0 0 4px;font-size:12px;color:{_LEARNER_MUTED};">Book your one-to-one LMS introduction:</p>'
            f'<p style="margin:0;font-size:12px;word-break:break-all;"><a href="{safe_booking}" style="color:{_LEARNER_PRIMARY};">{safe_booking}</a></p>'
        )
    preheader = (
        "Set your password and book your one-to-one LMS introduction." if booking
        else "Set your password to activate your learning account."
    )
    html = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:{_LEARNER_WASH};font-family:Segoe UI,Arial,sans-serif;color:{_LEARNER_TEXT};">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{_LEARNER_WASH};">{preheader}</div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:{_LEARNER_WASH};">
      <tr>
        <td align="center" style="padding:28px 12px;">
          <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:600px;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid {_LEARNER_BORDER};">
            <tr>
              <td bgcolor="{_LEARNER_DEEP}" style="background:{_LEARNER_DEEP};background-image:linear-gradient(108deg,{_LEARNER_DEEP} 0%,{_LEARNER_PRIMARY} 60%,{_LEARNER_ACCENT} 100%);padding:30px 32px;">
                <p style="margin:0 0 18px;font-size:13px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#E2C8FF;">{_BRAND}</p>
                <h1 style="margin:0;font-size:26px;line-height:1.25;color:#ffffff;">Welcome to the new LMS</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 32px 20px;">
                {paragraph}{escape(greeting)}</p>
                {paragraph}{escape(_INVITE_READY)}</p>
                {paragraph}{escape(start)}</p>
                {paragraph}{escape(_INVITE_PURPOSE)}</p>
                {_optional_move_box()}
                {_benefits_box()}
              </td>
            </tr>{steps}
            <tr>
              <td style="padding:8px 32px 28px;">
                <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-top:1px solid {_LEARNER_BORDER};">
                  <tr>
                    <td style="padding-top:18px;">
                      <p style="margin:0 0 10px;font-size:13px;font-weight:700;color:{_LEARNER_MUTED};">Button not working? Copy a link into your browser.</p>
                      {fallback_links}
                      <p style="margin:16px 0 0;font-size:12px;line-height:1.6;color:{_LEARNER_MUTED};">If you were not expecting this email, you can ignore it — no account is active until a password is set.</p>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 32px;background:{_LEARNER_SOFT};font-size:12px;color:{_LEARNER_MUTED};">
                This is an automated message from the {_BRAND} learning platform. Please do not reply.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""
    text = (
        f"{greeting}\n{_INVITE_READY}\n{start}\n{_INVITE_PURPOSE}\n"
        f"{_OPTIONAL_MOVE_TITLE}\n{_OPTIONAL_MOVE}\n"
        f"{_PLATFORM_BENEFITS_TITLE.upper()}\n"
        + "".join(f"✓ {title}\n{detail}\n" for title, detail in _PLATFORM_BENEFITS)
        + f"\n{'Step 1 - ' if booking else ''}Set your password:\n{link}\n"
        f"This link can be used once and expires in {expires_days} days.\n"
    )
    if booking:
        text += (
            f"\nStep 2 - Book a one-to-one LMS introduction with your case owner, {booking_owner}:\n"
            f"{booking_link}\n"
        )
    return subject, html, text


def lms_introduction_request_message(*, owner_name, learner_name, learner_email,
                                     when_label, note, timetable_link, updated=False,
                                     invite_sent=True):
    """Tell a case owner a learner booked their one-to-one LMS introduction.

    The booking already sits on the case owner's timetable and, when
    ``invite_sent``, in their Teams calendar. No token, nothing secret: the
    button only opens the timetable, where the meeting can be moved or resent.
    """
    learner = learner_name or learner_email
    subject = f"LMS introduction booked — {learner}"
    verb = "moved" if updated else "booked"
    where = (
        "It is in your Teams calendar, and they have been sent the Teams invitation."
        if invite_sent else
        "Microsoft did not accept the Teams invitation, so it is not in your Teams "
        "calendar yet. Open it on your timetable to send it again."
    )
    html = _shell(
        heading="LMS introduction booked",
        intro=(
            f"<strong>{escape(learner)}</strong> has {verb} a one-to-one LMS "
            f"introduction with you. {where}"
            f'<br><br>{_detail_table([("Learner", learner_name), ("Email", learner_email), ("Time", when_label), ("Note", note)])}'
        ),
        button_label="Open my timetable",
        link=escape(timetable_link, quote=True),
        footer="You can reschedule or cancel it from your timetable like any other booking.",
    )
    text = (
        f"{learner} has {verb} a one-to-one LMS introduction with you. {where}\n\n"
        f"Learner: {learner_name}\nEmail: {learner_email}\nTime: {when_label}\n"
        + (f"Note: {note}\n" if note else "")
        + f"\nOpen your timetable:\n{timetable_link}\n"
    )
    return subject, html, text


def reset_message(*, display_name, link, expires_hours):
    greeting = f"Hello {display_name}," if display_name else "Hello,"
    subject = f"Reset your {_BRAND} password"
    html = _shell(
        heading="Reset your password",
        intro=(
            f"{greeting}<br><br>We received a request to reset the password for "
            "this account. Use the button below to choose a new one."
        ),
        button_label="Reset password",
        link=link,
        footer=(
            f"This link can be used once and expires in {expires_hours} hour(s). "
            "If you did not request a reset, you can ignore this email — your "
            "current password remains unchanged."
        ),
    )
    text = (
        f"{greeting}\n\nWe received a request to reset your password.\n\n"
        f"Reset it here:\n{link}\n\n"
        f"This link can be used once and expires in {expires_hours} hour(s).\n"
        "If you did not request this, ignore this email.\n"
    )
    return subject, html, text


def access_request_message(*, requester_name, requester_email, console_url):
    """Mail an administrator that somebody is waiting for an access grant.

    Sent by the person themselves from the /access-required page, so the body
    carries only what the administrator needs to act: who is asking, and a link
    to the Accounts screen where the grant is made. No token, nothing secret —
    unlike the invitation and reset mails, this one is safe in a shared inbox.
    """
    who = requester_name or requester_email
    subject = f"Access request — {who}"
    html = _shell(
        heading="Someone is waiting for access",
        intro=(
            f"<strong>{who}</strong> ({requester_email}) has signed in to the "
            f"{_BRAND} platform but has no access level yet, so there is nothing "
            "they can open. Grant them one from the Accounts screen."
        ),
        button_label="Open Accounts",
        link=console_url,
        footer=(
            "Click their name on that screen to choose an access level. They will "
            "be able to use the platform on their next request — they do not need "
            "to sign in again."
        ),
    )
    text = (
        f"{who} ({requester_email}) has signed in but has no access level yet.\n\n"
        f"Grant one here:\n{console_url}\n"
    )
    return subject, html, text


# ---------------------------------------------------------------------------
# Staffing notifications
# ---------------------------------------------------------------------------
# Unlike the invitation/reset mails above, these carry no token and no secret —
# only the delivery facts a tutor needs in order to know what they are teaching,
# to whom, and when. That is why the body is a details table rather than a bare
# call-to-action: the mail has to be useful when read on a phone without ever
# opening the platform.


def _detail_table(pairs, label_color="#616e7c", value_color="#1f2933", row_padding="4px"):
    """Two-column label/value rows. Pairs with an empty value are dropped.

    Dropping blanks rather than printing "—" matters here: a module authored
    before its group has a schedule would otherwise mail a table half full of
    placeholders, which reads as broken data instead of detail-not-set-yet.

    The colours are arguments because two different mails use this: the defaults
    are the neutral pair the access-request mail has always had, and the module
    card passes the brand's own.
    """
    rows = []
    for label, value in pairs:
        text = str(value if value is not None else "").strip()
        if not text:
            continue
        rows.append(
            '<tr>'
            f'<td style="padding:{row_padding} 14px {row_padding} 0;font-size:13px;color:{label_color};'
            'white-space:nowrap;vertical-align:top;">' + escape(str(label)) + '</td>'
            f'<td style="padding:{row_padding} 0;font-size:13px;color:{value_color};'
            'font-weight:600;vertical-align:top;">' + escape(text) + '</td>'
            '</tr>'
        )
    if not rows:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="width:100%;border-collapse:collapse;">' + "".join(rows) + '</table>'
    )


def _module_card(module):
    """One assigned module, rendered as a titled details block."""
    heading = escape(str(module.get("name") or "Untitled module"))
    code = str(module.get("code") or "").strip()
    code_html = (
        f'<div style="margin:3px 0 12px;font-size:11px;color:{_LEARNER_MUTED};'
        f'letter-spacing:0.5px;font-family:Consolas,Menlo,monospace;">{escape(code)}</div>'
        if code else '<div style="height:12px;font-size:0;line-height:0;">&nbsp;</div>'
    )
    details = _detail_table([
        ("Programme", module.get("programme")),
        ("Cohort", module.get("cohort")),
        ("Group", module.get("group")),
        ("Schedule", module.get("schedule")),
        ("Runs", module.get("dates")),
        ("Sessions", module.get("sessions")),
        ("Off-the-job hours", module.get("otjh")),
        ("Group coach", module.get("coach")),
    ], label_color=_LEARNER_MUTED, value_color=_LEARNER_TEXT, row_padding="5px")
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
        f'style="margin:0 0 14px;border:1px solid {_LEARNER_BORDER};border-radius:10px;'
        f'border-collapse:separate;background:{_LEARNER_WASH};">'
        # A hairline of the brand accent along the top, so a run of several
        # modules reads as a stack of cards rather than one long table.
        f'<tr><td style="height:3px;background:{_LEARNER_ACCENT};font-size:0;'
        'line-height:0;">&nbsp;</td></tr>'
        '<tr><td style="padding:16px 18px 18px;">'
        f'<div style="font-size:17px;font-weight:700;color:{_LEARNER_DEEP};'
        'line-height:1.3;">' + heading + '</div>'
        f'{code_html}'
        f'<div style="height:1px;background:{_LEARNER_BORDER};font-size:0;line-height:0;'
        'margin:0 0 12px;">&nbsp;</div>'
        f'{details}'
        '</td></tr></table>'
    )


def _module_text(module):
    lines = [f"- {module.get('name') or 'Untitled module'}"]
    if str(module.get("code") or "").strip():
        lines.append(f"    {'Module ID:':<14}{module['code']}")
    for label, key in (
        ("Programme", "programme"),
        ("Cohort", "cohort"),
        ("Group", "group"),
        ("Schedule", "schedule"),
        ("Runs", "dates"),
        ("Sessions", "sessions"),
        ("OTJ hours", "otjh"),
        ("Group coach", "coach"),
    ):
        value = str(module.get(key) or "").strip()
        if value:
            lines.append(f"    {label + ':':<14}{value}")
    return "\n".join(lines)


def tutor_assignment_message(*, tutor_name, modules, workspace_url):
    """Tell a tutor which modules they have just been put on.

    ``modules`` is a list of already-formatted dicts (name, code, programme,
    cohort, group, schedule, dates, sessions, otjh, coach) — this function does
    no database work and no date formatting, so it stays testable on its own and
    the curriculum layer keeps ownership of what a "schedule" reads like.

    One mail covers every module in the batch. A wizard save that puts a tutor on
    six modules at once should land as one message, not six.
    """
    modules = list(modules or [])
    greeting = f"Hello {tutor_name}," if tutor_name else "Hello,"
    count = len(modules)

    if count == 1:
        subject = f"You have been assigned to {modules[0].get('name') or 'a module'}"
        lead = "You have been assigned as the tutor for the following module."
    else:
        subject = f"You have been assigned to {count} modules"
        lead = f"You have been assigned as the tutor for the following {count} modules."
    subject = f"{subject} — {_BRAND}"

    cards = "".join(_module_card(module) for module in modules)
    # The line the inbox shows beside the subject. Without one, Gmail pulls the
    # greeting ("Hello Osama Kord,"), which tells the reader nothing the subject
    # has not already told them.
    first = modules[0] if modules else {}
    preheader = escape(" · ".join(part for part in [
        str(first.get("name") or "").strip(),
        str(first.get("schedule") or "").strip(),
        str(first.get("dates") or "").strip(),
    ] if part) or lead)
    heading_line = (
        "You are now teaching this module" if count == 1
        else f"You are now teaching {count} modules"
    )
    html = f"""\
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta name="color-scheme" content="light">
    <title>{escape(subject)}</title>
  </head>
  <body style="margin:0;padding:0;background:{_LEARNER_SOFT};font-family:Segoe UI,Helvetica,Arial,sans-serif;color:{_LEARNER_TEXT};-webkit-font-smoothing:antialiased;">
    <div style="display:none;max-height:0;overflow:hidden;font-size:0;line-height:0;color:{_LEARNER_SOFT};opacity:0;">{preheader}</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{_LEARNER_SOFT};">
      <tr>
        <td align="center" style="padding:28px 12px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:620px;background:#ffffff;border-radius:14px;overflow:hidden;border:1px solid {_LEARNER_BORDER};">
            <tr>
              <td align="center" style="padding:30px 28px 22px;background:#ffffff;">
                <img src="cid:{BRAND_LOGO_CID}" width="136" alt="{_BRAND}"
                     style="display:block;width:136px;max-width:136px;height:auto;border:0;outline:none;text-decoration:none;">
              </td>
            </tr>
            <tr>
              <td style="height:4px;background:{_LEARNER_PRIMARY};font-size:0;line-height:0;">&nbsp;</td>
            </tr>
            <tr>
              <td style="padding:30px 28px 8px;">
                <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;color:{_LEARNER_ACCENT};">Teaching assignment</p>
                <h1 style="margin:0 0 18px;font-size:23px;line-height:1.25;color:{_LEARNER_DEEP};font-weight:700;">{escape(heading_line)}</h1>
                <p style="margin:0 0 6px;font-size:15px;line-height:1.6;color:{_LEARNER_TEXT};">{escape(greeting)}</p>
                <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:{_LEARNER_TEXT};">{escape(lead)}</p>
                {cards}
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:26px 0 4px;">
                  <tr>
                    <td align="center" bgcolor="{_LEARNER_PRIMARY}" style="border-radius:9px;">
                      <a href="{escape(workspace_url)}" style="display:inline-block;padding:14px 28px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:9px;">Open your tutor workspace &rarr;</a>
                    </td>
                  </tr>
                </table>
                <p style="margin:10px 0 0;font-size:12px;color:{_LEARNER_MUTED};word-break:break-all;">{escape(workspace_url)}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:22px 28px 26px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{_LEARNER_SOFT};border-radius:10px;">
                  <tr>
                    <td style="padding:14px 16px;font-size:13px;line-height:1.6;color:{_LEARNER_MUTED};">
                      Session content, learners and KSB mappings for each module are in the workspace.
                      If any of these details look wrong, reply to your curriculum lead rather than to this address.
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 28px;background:#ffffff;font-size:12px;color:{_LEARNER_MUTED};border-top:1px solid {_LEARNER_BORDER};">
                This is an automated message from the {_BRAND} learning platform. Please do not reply.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

    text = "\n".join([
        greeting,
        "",
        lead,
        "",
        "\n\n".join(_module_text(module) for module in modules),
        "",
        f"Open your tutor workspace:\n{workspace_url}",
        "",
        "This is an automated message. Please do not reply.",
    ])
    return subject, html, text
