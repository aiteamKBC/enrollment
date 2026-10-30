"""Reject callers at the front of the API, before any view runs.

Why this exists
---------------
``permissions.py`` gates endpoints one decorator at a time, and that worked
while the gated set was small. It had not kept up: ``curriculum_api`` (86
routes), ``audit_api`` (59), ``manual_audit_api`` (49), ``engagement_api`` (24)
and ``quiz_api`` (13) carried no authentication of any kind. Every one of them
answered a caller with no cookie -- so the whole curriculum, every cohort roster
and every audit record could be read by anybody who knew a URL, with no account
and no sign-in.

Closing that per-view would mean ~230 decorator edits, each of them a chance to
miss one, and the next route added would start life open again. A prefix gate
inverts that: an app is protected by naming it once here, and a new endpoint
inside a gated app is closed the moment it is routed.

What it answers
---------------
Two questions, in order: is there a live session, and is the caller's *role* one
this prefix serves. Nothing about *which records* -- that is still the job of
``staff_only``, ``require_access``, ``employer_or_staff`` and the enrolment gate,
which run afterwards and are what stop one signed-in account reaching another's
data. Deleting a decorator because this exists would re-open exactly that.

Either identity counts, matching ``enrolment_api.auth``: the platform's own
``kbc_session`` cookie, or a Django ``contrib.auth`` session, so the admin site
and the chat app's Django-session users are not locked out of endpoints they
could already reach. A Django-auth user carries no platform role, so it settles
the session question and is not subject to the role one -- it is an operator
signed into the admin site.

Choosing the role for a prefix
------------------------------
Each rule below was set from what actually calls the prefix in the SPA, not from
what the name suggests. Two would have been wrong on the obvious guess:

* ``curriculum_api`` is not staff-only -- ``pages/learner/video-watch`` renders
  ``SlideDeckViewer``, which reads the presentation endpoint. That one path is
  carved out; the other 85 are staff.
* ``engagement_api`` is not staff-only either. The learner clubs, events and
  rewards pages are built on it, so it stays at "any signed-in account".

``API_REQUIRE_AUTH=0`` turns the gate off, matching ``LEARNER_API_REQUIRE_AUTH``
and ``ENROLMENT_API_REQUIRE_AUTH`` in shape and intent: local development and
tests that run with no session. It must never be set in a deployment.
"""
from __future__ import annotations

import logging
import time
import os

from django.db import DatabaseError
from django.http import JsonResponse, HttpResponseRedirect

from .sessions import (
    authenticate_request,
    mark_session_unreadable,
    session_unreadable,
)

logger = logging.getLogger("login")

#: How often a given failure may log its traceback.
#:
#: This gate runs on every request to a gated prefix, so a failure here is never
#: one failure. An auth backend that has stopped answering produces one per
#: request -- thousands a minute, each with a full traceback -- and the first
#: occurrence, the only one that says what actually broke, is buried under its
#: own repetitions at exactly the moment somebody is reading the log to find it.
#:
#: So the traceback is written at most once per interval per kind of failure,
#: and the ones in between are counted and reported with the next. That is the
#: shape you want at 3am: one stack trace, and a number saying how wide it is.
LOG_INTERVAL_SECONDS = 60

#: ``{key: (monotonic time of last log, failures suppressed since)}``. Races
#: between threads can only miscount the suppressed total, never lose the first
#: traceback or make one log twice, so it needs no lock.
_log_state: dict[str, tuple[float, int]] = {}


def _log_failure(key, message):
    """Log ``message`` with its traceback, at most once per interval per ``key``.

    Call from inside an ``except`` block -- it logs at exception level, so the
    traceback comes from the active exception.
    """
    now = time.monotonic()
    last, suppressed = _log_state.get(key, (None, 0))

    if last is not None and now - last < LOG_INTERVAL_SECONDS:
        _log_state[key] = (last, suppressed + 1)
        return

    if suppressed:
        message = (
            f"{message} (and {suppressed} further occurrence(s) "
            f"in the previous {int(now - last)}s)"
        )
    logger.exception(message)
    _log_state[key] = (now, 0)

#: Env values that turn the gate OFF. Anything else -- including unset -- keeps
#: it ON, so a deployment that never heard of this variable is still protected.
_DISABLED_VALUES = {"0", "false", "no", "off"}

#: Roles that run the staff console. Same set as ``permissions.staff_only``.
STAFF = frozenset({"admin", "staff"})

# Learner material tables are read-only and intentionally exposed to learners,
# while still excluding employer accounts. The views additionally restrict a
# learner to the programme assigned to their inspection-demo account.
LEARNER_AND_STAFF = frozenset({"admin", "staff", "learner"})

#: A rule of ``ANY`` requires a session and nothing more.
ANY = None

#: ``(url prefix, roles allowed)``, applied longest-prefix-first -- so a specific
#: path can open up or lock down a corner of an app whose general rule differs.
#:
#: Every prefix ends in "/" so "/audit_api_public/" could not be matched by
#: "/audit_api". A path matching no rule is not gated at all; the three
#: deliberate omissions are:
#:
#: ``/login_api/``
#:     Owns signing in, password reset, invitation acceptance and the health
#:     check. All of it is reached *before* there is a session; gating it would
#:     make the door require the key it hands out. It runs its own throttling
#:     and lockout, and its token endpoints validate a single-use signed token.
#: ``/api/chat/``
#:     Already gated properly per-view (``IsAuthenticated`` plus participant
#:     checks on every conversation and message route). Its two ``AllowAny``
#:     routes are the session bootstrap itself -- the same reasoning as above.
#: ``/api/calendar/``
#:     The Google and Microsoft calendar OAuth callbacks, and nothing else --
#:     the learner's actual calendar data is under ``/learner_api/``, which is
#:     gated. A callback arrives as a top-level redirect *from the provider* and
#:     authenticates on the signed 10-minute ``state`` it carries, never on the
#:     session; ``microsoft_sso.py``'s callback is ungated for the same reason.
#:     Gating it would make connecting a calendar depend on the session cookie
#:     surviving a cross-site navigation, and fail as raw JSON where a redirect
#:     belongs.
#:
#: ``/django_admin/`` and ``/media/`` are likewise absent: the admin has its own
#: login, and media is served by the reverse proxy rather than routed here.
RULES = (
    # Browser performance samples are accepted only for server-allowlisted
    # accounts by system_audit.performance. The transport must nevertheless be
    # reachable by every signed-in role so Learner, Employer and Tutor pages can
    # be measured without opening the rest of curriculum_api to those roles.
    ("/curriculum_api/performance/record/", ANY),
    # Page activity is recorded for every workspace. Only the recorder is
    # shared; reading the audit trail remains staff-only. The view derives
    # the actor from the authenticated session, never from the posted body.
    ("/curriculum_api/activity/record/", ANY),
    # Learner pages subscribe to curriculum changes too. This endpoint returns
    # only the shared counter for learners, never staff change paths.
    ("/curriculum_api/curriculum/cache-epoch/", LEARNER_AND_STAFF),
    # Authored learner activities reference PDFs, decks, audio and other files
    # through this stable upload URL. Learners need the file itself after the
    # activity page has authorised and linked it; employers still do not.
    ("/curriculum_api/curriculum/uploads/", LEARNER_AND_STAFF),
    # Slide decks are rendered inside the learner's video and component pages,
    # so this one curriculum path serves every signed-in account. It reads a
    # presentation the learner's own week already links to.
    ("/curriculum_api/curriculum/presentations/", ANY),
    # The rest of the curriculum console: programmes, cohorts, groups, modules,
    # rosters, KSB coverage, Teams meetings. Nothing a learner or employer has
    # any reason to read, and it is the whole provision behind one API.
    ("/curriculum_api/", STAFF),
    # Learner-facing: clubs, events and their bookings, rewards, voucher claims,
    # recognitions and points. pages/learner/{clubs,clubs/events,rewards} are
    # built on these, so this cannot be narrowed to staff.
    ("/engagement_api/", ANY),
    ("/coach_api/", STAFF),
    ("/quiz_api/", STAFF),
    ("/audit_api/", STAFF),
    ("/hours_test_api/", STAFF),
    ("/manual_audit_api/", STAFF),
    # These two carry their own role logic, including the per-record ownership
    # checks that let a learner reach their own record and nobody else's. The
    # rule here is only the floor beneath that.
    ("/learner_api/", ANY),
    ("/enrolment_api/", ANY),
    # The batch transport fans one request out to several of the above. It is
    # ANY here because the fan-out is what needs checking, not the envelope --
    # and it is checked: config/batch.py runs every sub-request through
    # `refusal_for` with the parent's account. It has to, because it dispatches
    # by calling the view function directly, which no middleware ever sees.
    ("/api/batch/", ANY),
    ("/coach_api/_batch/", ANY),
)

#: Longest prefix first, so "/curriculum_api/curriculum/presentations/" is tested
#: before "/curriculum_api/". Sorted once at import rather than per request.
_RULES_BY_SPECIFICITY = tuple(sorted(RULES, key=lambda rule: -len(rule[0])))

#: CORS preflight carries no cookies by design, so refusing it would break the
#: real request that follows rather than protecting anything. The browser sends
#: the credentialed request only after the preflight succeeds, and that request
#: is gated normally.
_EXEMPT_METHODS = frozenset({"OPTIONS"})


def _enabled():
    """Read per-request so tests and local runs can toggle it."""
    return os.environ.get("API_REQUIRE_AUTH", "1").strip().lower() not in _DISABLED_VALUES


def rule_for(path):
    """The ``(prefix, roles)`` governing ``path``, or None if it is not gated."""
    # Email approval authenticates with an expiring, single-use signed request.
    # Exact path only: the learner's attendance APIs still require a session.
    if path == '/learner_api/attendance-mode/review/':
        return None
    # A post-event attendee arrives from an emailed, high-entropy bearer link.
    # The view validates only a hash and exposes forms for that token's event;
    # keeping this narrow prefix public lets guests respond without opening any
    # other Engagement endpoint.
    if path.startswith('/engagement_api/feedback/public-event/'):
        return None
    if path.startswith('/engagement_api/feedback/public-rsvp/'):
        return None
    if path.startswith('/engagement_api/event-check-in/'):
        return None
    if path.startswith(('/learner_api/certificates/verify/', '/learner_api/personal-learning/verify/')):
        return None
    # The public apprenticeship application (/apprenticeship_application) is
    # filled in by people with no account yet. Exact path only: the view accepts
    # a POST that inserts one new row and returns nothing that is stored.
    # The same page reads the questions it asks (the published layout, which
    # holds no answers and no names) and checks postcodes as they are typed.
    if path in (
        '/enrolment_api/public/apprenticeship-application/',
        '/enrolment_api/public/eligibility-form-layout/',
        '/enrolment_api/public/postcode-lookup/',
    ):
        return None
    # Fetched by Microsoft's Office Online servers, which carry no session. The
    # view authenticates on a signed, 15-minute token naming one upload.
    if path.startswith('/curriculum_api/office-preview/'):
        return None
    from old_otjh.gate import is_transition_path
    if is_transition_path(path):
        return '/audit_api/', LEARNER_AND_STAFF
    for prefix, roles in _RULES_BY_SPECIFICITY:
        if path.startswith(prefix):
            return prefix, roles
    return None


def is_gated(path):
    return rule_for(path) is not None


def _unauthenticated():
    # Same body and code as ``permissions._unauthenticated`` so the SPA does not
    # have to learn a second shape for "signed out" per endpoint.
    return JsonResponse(
        {"error": "Authentication required.", "code": "unauthenticated"},
        status=401,
    )


def _forbidden(roles):
    return JsonResponse(
        {
            "error": "You do not have permission to perform this action.",
            "code": "forbidden",
            "requiredRole": sorted(roles),
        },
        status=403,
    )


def _unavailable():
    """The session could not be read. Fail closed, but do not say "signed out".

    ``503`` rather than ``401`` because the two are different facts and only one
    of them is the caller's. A 401 tells the SPA the session has ended and it
    signs the person out and navigates to /login -- so answering an unreadable
    auth database with 401 converts a few seconds of database trouble into a
    lost session and a re-typed password, for somebody whose session row is
    alive and hours from expiring.

    Nothing is admitted either way: this is still a refusal. ``Retry-After``
    says the condition is expected to pass, which is the honest shape of it.
    """
    response = JsonResponse(
        {
            "error": "The sign-in service is temporarily unavailable.",
            "code": "session_unavailable",
        },
        status=503,
    )
    response["Retry-After"] = "5"
    return response


def refusal_for(path, account, *, django_user_is_authenticated=False, method='GET'):
    """The response refusing this caller, or None to let the request through.

    Split out from the middleware so ``config.batch`` can apply the same rules to
    the sub-requests it dispatches. It calls view functions directly, so no
    middleware -- this one included -- ever sees them; without this, the batch
    endpoint would be a way to read any gated prefix the caller's own role is
    refused at the front door.

    ``account`` is a ``LoginAccount`` or None. ``django_user_is_authenticated``
    covers the admin-site operator, who has a session but no platform role.
    """
    if account is not None and getattr(account, '_staff_access_unavailable', False):
        return _unavailable()
    if account is not None and getattr(account, '_staff_access', None) == 'advanced-admin':
        # This grant has a learner allowlist of its own. The coarse ``staff``
        # role would otherwise admit it to older staff prefixes whose views
        # predate access grants. Keep the exception independent of the regular
        # role gate so batch requests and ungated prefixes cannot bypass it.
        account_paths = {
            '/login_api/me/', '/login_api/logout/', '/login_api/change-password/',
            '/login_api/forgot-password/', '/login_api/reset/',
            '/login_api/reset-password/', '/login_api/login/',
            '/login_api/health/',
        }
        if not path.startswith('/login_api/advanced-admin/') and path not in account_paths:
            return _forbidden(['advanced-admin'])
    if account is not None and getattr(account, '_staff_access', None) == 'record-monitor':
        from old_otjh.gate import is_transition_path
        # Applies to batch children too. Record endpoints enforce read-only
        # methods and learner scope; authentication keeps its own handlers.
        if not is_transition_path(path) and path not in {
            '/login_api/me/', '/login_api/logout/', '/login_api/change-password/',
            '/login_api/login/', '/login_api/health/',
        }:
            return _forbidden(['previous_records.view'])

    if not _enabled():
        return None

    rule = rule_for(path)
    if rule is None:
        return None

    if account is None:
        return None if django_user_is_authenticated else _unauthenticated()

    _, roles = rule
    if roles is ANY or account.role in roles:
        from old_otjh.gate import refusal
        transition_refusal = refusal(path, account)
        if transition_refusal is not None:
            return transition_refusal
        from learner_api.programme_access import refusal as programme_refusal
        return programme_refusal(path, account, method)

    return _forbidden(roles)


class ApiSessionGateMiddleware:
    """Refuse any request to a gated prefix that the caller's role cannot make."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in _EXEMPT_METHODS:
            account = self._account(request)

            # Checked before ``refusal_for``, which only knows "account or no
            # account" and would answer 401 -- the status that ends the session
            # in the browser. Either this middleware failed the lookup just now
            # or ``LoginSessionMiddleware`` did upstream; the flag carries both.
            if account is None and session_unreadable(request):
                return _unavailable()

            refusal = refusal_for(
                request.path_info,
                account,
                django_user_is_authenticated=self._django_user(request),
                method=request.method,
            )
            if refusal is not None:
                # Only the human-facing APK link gets a sign-in journey. API
                # clients retain their 401 contract; no protected view is run.
                if (refusal.status_code == 401
                        and request.method == "GET"
                        and request.path_info == "/learner_api/mobile/download/android/"
                        and "text/html" in request.headers.get("Accept", "")
                        and request.headers.get("X-Requested-With") != "XMLHttpRequest"):
                    response = HttpResponseRedirect("/login?download=android")
                    response["Cache-Control"] = "private, no-store"
                    response["Vary"] = "Accept, Cookie, X-Requested-With"
                    return response
                return refusal

        return self.get_response(request)

    @staticmethod
    def _account(request):
        # LoginSessionMiddleware has normally resolved this already; the call is
        # idempotent and cached on the request, so this costs nothing when it has.
        try:
            return authenticate_request(request)
        except DatabaseError:
            # Fail closed. An unreadable session is not a session, and the view
            # behind this could not have served the request anyway.
            _log_failure(
                "session", "Could not resolve login session at the API gate"
            )
            mark_session_unreadable(request)
            return None

    @staticmethod
    def _django_user(request):
        """Whether a Django ``contrib.auth`` session is signed in.

        ``request.user`` is a lazy object: the attribute exists long before
        anything is read, and the read is what loads the session row and the
        user model. So this is the point where an unrelated failure -- the
        session table unreachable, or ``django.contrib.auth`` not resolvable in
        the app registry -- surfaces, and an unguarded read turns it into a 500
        on every gated endpoint at once.

        A failure here answers the question rather than escaping it: there is no
        Django-auth identity to be found. That is the closed direction, since
        this identity is one of two ways past the gate and the caller still has
        to satisfy the platform session instead. Same posture as ``_account``.
        """
        user = getattr(request, "user", None)
        if user is None:
            return False
        try:
            return bool(user.is_authenticated)
        except Exception:  # noqa: BLE001 - see the docstring; never 500 on this
            _log_failure(
                "django-user",
                "Could not resolve the Django auth user at the API gate",
            )
            return False
