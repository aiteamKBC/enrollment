"""Authorisation decorators for views protected by the login system.

``enrolment_api.auth.enrolment_login_required`` already gates the enrolment
endpoints on "is there a Django session user". These decorators are the
equivalent for the new login system and additionally understand roles, so a
learner's session cannot reach a staff endpoint.

Both attach the resolved account to the request, so a view body can read
``request.login_account`` without repeating the lookup.
"""
from __future__ import annotations

import functools

from django.http import JsonResponse

from .sessions import authenticate_request, session_unreadable


def _unauthenticated(request=None):
    """Refuse a caller with no session.

    ``request`` is optional only for callers predating the distinction. Pass it:
    a request whose session could not be *read* gets ``_unavailable`` instead,
    because 401 is the status the SPA acts on by signing the person out and
    navigating to /login. A database that blinked is not a session that ended,
    and telling the browser it was loses somebody's working session.
    """
    if request is not None and session_unreadable(request):
        return _unavailable()
    return JsonResponse(
        {"error": "Authentication required.", "code": "unauthenticated"},
        status=401,
    )


def _unavailable():
    """Fail closed on an unreadable session without claiming it ended.

    Mirrors ``api_gate._unavailable`` -- same code and status, so the SPA does
    not have to learn a second shape for "ask again shortly" per endpoint.
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


def _forbidden(required):
    return JsonResponse(
        {
            "error": "You do not have permission to perform this action.",
            "code": "forbidden",
            "requiredRole": list(required),
        },
        status=403,
    )


def login_required(view):
    """Reject callers without a live session."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if authenticate_request(request) is None:
            return _unauthenticated(request)
        return view(request, *args, **kwargs)

    return wrapped


def require_role(*roles):
    """Reject callers whose account role is not in ``roles``.

    Role names come from ``models.ROLE_CHOICES``. The check is on the role
    stored on the account, which ``identity.ensure_account`` recomputes from the
    person's enrolment row — so demoting somebody in the staff form takes effect
    on their next request, not only when they next sign in.
    """
    allowed = frozenset(roles)

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)
            if account.role not in allowed:
                return _forbidden(allowed)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


#: Env values that turn the staff gate OFF. Anything else (including unset)
#: keeps it ON. Mirrors ``enrolment_api.auth._DISABLED_VALUES`` so the two gates
#: are configured the same way.
_DISABLED_VALUES = {"0", "false", "no", "off"}

#: Methods that only read. A gate can be applied to writes alone while the
#: read paths are still being migrated onto authenticated fetches.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def staff_only(*, writes_only=False, allow_own_learner=None):
    """Gate a staff-facing endpoint on an authenticated staff or admin session.

    Built for the pre-existing ``learner_api`` views, which are ``@csrf_exempt``
    and had no authentication of any kind: anyone who could reach the URL could
    create, read or edit a learner, an employer or a staff record. That is the
    console's own API, so "authenticated staff" is the boundary that matters.

    ``writes_only=True`` gates POST/PATCH/PUT/DELETE and leaves GET open. It is
    for endpoints whose read path is still consumed by something that has no
    session yet — closing the write path is the urgent half, since that is what
    mutates records and issues invitations.

    ``allow_own_learner="pk"`` additionally lets a **learner** write, but only to
    the record named by that URL kwarg — i.e. their own. The onboarding wizard
    is the reason: a learner at the 'Onboarding' status fills it in themselves,
    so refusing every learner write means the wizard loads and then cannot be
    submitted, which loses their answers at the last step.

    Two things this does **not** grant. A learner naming somebody else's id gets
    **404**, not 403 — a 403 would confirm the id exists, matching how
    ``login.invitations._load_token`` collapses its failure modes. And owning the
    record is not permission to set every field on it: the view is told via
    ``request.learner_self_write`` so it can restrict the payload (see
    ``learner_api.mappers.restrict_to_self_writable``), because the write mapping
    includes ``programmeStatus`` and a learner who could set it would promote
    themselves straight past the enrolment flow.

    Set ``LEARNER_API_REQUIRE_AUTH=0`` to disable the gate entirely. That exists
    for local development and for the migration window while the frontend's
    remaining unauthenticated fetches are moved over; it must never be set in a
    deployment. Read per-request so tests can toggle it.
    """
    allowed = frozenset({"admin", "staff"})

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            import os

            # Default: the view is not serving a learner acting on themselves.
            # Set on every path so a view can read it without a getattr default.
            request.learner_self_write = False

            enabled = os.environ.get(
                "LEARNER_API_REQUIRE_AUTH", "1"
            ).strip().lower() not in _DISABLED_VALUES

            if not enabled:
                # Still resolve the session, so a signed-in caller is attributed
                # correctly in the audit trail even with the gate off.
                authenticate_request(request)
                return view(request, *args, **kwargs)

            if writes_only and request.method in _SAFE_METHODS:
                authenticate_request(request)
                return view(request, *args, **kwargs)

            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)

            if account.role in allowed:
                return view(request, *args, **kwargs)

            if allow_own_learner and account.role == "learner":
                try:
                    target_id = int(kwargs.get(allow_own_learner))
                except (TypeError, ValueError):
                    return _forbidden(allowed)
                # Matched on id alone: ids are unique across the single
                # Created_users table, so the learner kind is not part of it.
                if target_id != account.subject_id:
                    return JsonResponse({"error": "Not found."}, status=404)
                request.learner_self_write = True
                return view(request, *args, **kwargs)

            return _forbidden(allowed)

        return wrapped

    return decorator


def employer_or_staff(employer_kwarg="employer_id"):
    """Gate an employer-portal endpoint on its owner, or on staff/admin.

    The portal endpoints were written to be opened by an admin from the Users
    directory and had no authentication at all, so the employer id in the URL
    was the only thing naming whose data came back. That is fine while the link
    exists in one staff-only place; it stops being fine the moment employers
    sign in and land there themselves, because the id is a small integer and
    changing it in the address bar would read another employer's learners.

    Staff and admin keep full access — the directory's View action opens the
    same page and must go on working. An employer reaches only their own record:
    ``account.subject_id`` is their ``enrolment."Employers".id``, which is
    exactly what the URL names.

    Naming somebody else's id gets **404**, not 403, matching
    ``staff_only(allow_own_learner=...)`` — a 403 would confirm the id exists.

    Honours ``LEARNER_API_REQUIRE_AUTH=0`` like ``staff_only``, so local
    development toggles every gate in this module the same way.
    """
    allowed = frozenset({"admin", "staff"})

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            import os

            enabled = os.environ.get(
                "LEARNER_API_REQUIRE_AUTH", "1"
            ).strip().lower() not in _DISABLED_VALUES

            if not enabled:
                authenticate_request(request)
                return view(request, *args, **kwargs)

            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)

            if account.role in allowed:
                return view(request, *args, **kwargs)

            if account.role == "employer":
                try:
                    target_id = int(kwargs.get(employer_kwarg))
                except (TypeError, ValueError):
                    return _forbidden(allowed)
                if target_id != account.subject_id:
                    return JsonResponse({"error": "Not found."}, status=404)
                return view(request, *args, **kwargs)

            return _forbidden(allowed)

        return wrapped

    return decorator


def require_access(*accesses):
    """Gate an endpoint on the caller's staff **access** grant.

    Roles are too coarse for this. Every account the console creates carries
    ``Position = 'Admin'``, and an account whose access is ``super-admin`` gets
    ``role='admin'`` while the other three get ``role='staff'`` — so
    ``staff_only`` cannot tell an enrolment officer from a curriculum designer.
    The access grant is what distinguishes them, and this is where it is enforced.

    ``super-admin`` always passes: it means "everything" by definition, so it
    never has to be listed at a call site.

    Learners and employers are refused outright — these are staff areas, and
    their own surfaces are gated by ``require_role``/``enrolment_api.auth``.

    An account with **no** access recorded is refused. That is the whole point:
    "unset" means nobody has decided yet, and defaulting an undecided account
    into an area would hand out exactly the access this mechanism exists to
    control. The SPA sends such an account to ``/access-required``, where it can
    ask an administrator for one.

    Refusals are 403 with the required list, matching ``require_role``: unlike the
    per-learner scoping in ``enrolment_api.auth``, no record id is being probed
    here, so there is nothing to leak by being explicit.
    """
    required = frozenset(accesses)

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            from learner_api.constants import ACCESS_SUPER_ADMIN

            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)
            if account.role not in {"admin", "staff"}:
                return _forbidden(required)

            # "Holds any of", not "is": an account may carry several grants,
            # and needing only one of the required ones is the whole point.
            accesses = _accesses_of(account)
            if ACCESS_SUPER_ADMIN in accesses or (accesses & required):
                return view(request, *args, **kwargs)

            return _forbidden(required)

        return wrapped

    return decorator


def _accesses_of(account):
    """Every access grant on a staff account, or an empty set for anyone else.

    Read from the staff row rather than cached on the account, for the same
    reason ``role`` is recomputed per request: an access changed in the console
    must take effect on the account's next request, not their next sign-in.
    """
    if account.subject_type != "staff":
        return frozenset()
    from django.db import DatabaseError

    from learner_api.models import StaffUser
    from .identity import accesses_for_staff

    try:
        row = (
            StaffUser.objects.filter(pk=account.subject_id)
            .only("access", "access_extra")
            .first()
        )
    except DatabaseError:
        # Fail closed: an unreadable grant is not a grant.
        return frozenset()
    return accesses_for_staff(row) if row else frozenset()


def require_permission(*permissions):
    """Reject callers lacking every one of the named permissions.

    Thin wrapper over ``identity.permissions_for``; useful where a capability
    spans several roles and naming the roles at the call site would duplicate
    that mapping.
    """
    needed = frozenset(permissions)

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            from .identity import permissions_for

            account = authenticate_request(request)
            if account is None:
                return _unauthenticated(request)
            if not needed.issubset(set(permissions_for(account.role))):
                return _forbidden(needed)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def _auth_gate_enabled():
    """Whether the ``learner_api`` auth gates are switched on for this request.

    Used by the learner-progress gates below, and deliberately the same env var
    and the same ``_DISABLED_VALUES`` as the inline check in ``staff_only`` and
    ``employer_or_staff``, so ``LEARNER_API_REQUIRE_AUTH=0`` turns every gate in
    this module off together rather than leaving a half-open API.

    Read per-request so tests can toggle it.
    """
    import os

    return os.environ.get(
        "LEARNER_API_REQUIRE_AUTH", "1"
    ).strip().lower() not in _DISABLED_VALUES


def auth_gate_enabled() -> bool:
    """Public alias for :func:`_auth_gate_enabled`.

    For a view outside this module that calls ``authenticate_request`` itself
    (rather than going through ``staff_only``/``employer_or_staff``/one of the
    learner-progress gates) but still wants ``LEARNER_API_REQUIRE_AUTH=0`` to
    disable its own auth requirement the same way every gated endpoint in this
    module already does, instead of re-deriving the env-var check locally.
    """
    return _auth_gate_enabled()


def _read_only_learner_view():
    """403 for somebody reading a learner's plan who may not write to it.

    Carries its own code rather than reusing ``_forbidden``'s: the caller is
    *correctly* signed in and *is* allowed to read this learner, so the UI has to
    tell them "you are viewing this learner, not working as them" rather than
    "you lack a role". No id is being probed — the caller already had read
    access to reach the page — so being explicit leaks nothing.
    """
    return JsonResponse(
        {
            "error": (
                "Only the learner can record progress on their own training plan. "
                "You are viewing this learner's workspace read-only."
            ),
            "code": "read_only_learner_view",
        },
        status=403,
    )


def _target_learner_id(request, kwargs, *, kwarg, query_param, body_field):
    """The learner id a progress write names, or None if it cannot be read.

    The five progress endpoints disagree about where the id lives — a URL kwarg
    (evidence upload), a query parameter (quiz/video/component completion) or a
    JSON body field (reflection submissions) — so the source is named at each
    call site instead of assumed.

    Reading ``request.body`` here is safe for the body case: Django caches it on
    the request, so the view's own ``json.loads(request.body)`` still sees it.
    That is why the body source is only ever used for JSON endpoints; touching
    ``body`` on a multipart upload would break the view's ``request.FILES``.
    """
    raw = None
    if kwarg:
        raw = kwargs.get(kwarg)
    elif query_param:
        raw = request.GET.get(query_param)
    elif body_field:
        import json

        if "multipart" in (request.content_type or ""):
            return None
        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        raw = payload.get(body_field) if isinstance(payload, dict) else None

    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def staff_owns_learner(account, learner_id):
    """Match a staff account to the same enrolment exposed by its /me payload.

    The account's authenticated email is authoritative, never a learner id or
    email supplied by the client. Keep the staff identity and grants intact.
    """
    if (getattr(account, "role", None) != "staff" or getattr(account, "subject_type", None) != "staff"
            or not learner_id or learner_id <= 0 or not getattr(account, "email", None)):
        return False
    from django.db import DatabaseError
    from .learner_enrolment import existing_learner_record

    try:
        learner = existing_learner_record(account.email)
        return learner is not None and learner.pk == learner_id
    except DatabaseError:
        return False


def coach_owns_learner(account, learner_id):
    """Is this a Coach account and the learner on their own caseload?

    The caseload rule is the Coach Dashboard's: the learner profile's coach
    email is the coach's staff email. Fails closed on anything unreadable.
    """
    if (getattr(account, "role", None) != "staff" or getattr(account, "subject_type", None) != "staff"
            or not learner_id or learner_id <= 0):
        return False
    from django.db import DatabaseError
    from django.db.models.functions import Lower, Trim

    from learner_api.constants import ACCESS_COACH
    from learner_api.models import LearnerProfile, StaffUser
    from .identity import accesses_for_staff
    from .security import normalize_email

    try:
        staff = (StaffUser.objects.filter(pk=account.subject_id)
                 .only("email", "access", "access_extra").first())
        email = normalize_email(getattr(staff, "email", ""))
        if not staff or not email or ACCESS_COACH not in accesses_for_staff(staff):
            return False
        return (LearnerProfile.objects.annotate(coach_email_key=Lower(Trim("coach_email")))
                .filter(coach_email_key=email, enrolment_id=learner_id).exists())
    except DatabaseError:
        return False


def audit_admin_learner_action(request, learner_id, response):
    """Keep the real administrator or coach on the audit trail for learner-side writes."""
    account = getattr(request, "login_account", None)
    if request.method in _SAFE_METHODS:
        return
    if getattr(account, "role", None) == "admin":
        event = "admin_learner_action"
    elif getattr(request, "coach_learner_action", False):
        event = "coach_learner_action"
    else:
        return
    import json
    from .invitations import record
    from .security import client_ip, user_agent

    record(
        event,
        email=account.email,
        account_id=account.id,
        succeeded=200 <= response.status_code < 400,
        reason=json.dumps({"learnerId": learner_id, "method": request.method,
                           "path": request.path, "status": response.status_code}),
        ip=client_ip(request), user_agent=user_agent(request),
    )


def _learner_progress_gate(view, *, kwarg, query_param, body_field, allow_staff, allow_admin=False):
    """Enforce ownership and each learner endpoint's allowed privileged roles."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _auth_gate_enabled():
            authenticate_request(request)
            return view(request, *args, **kwargs)

        # OPTIONS is a CORS/preflight probe: it carries no identity and returns no
        # data, and ChatCorsMiddleware answers real cross-origin preflights before
        # the view is reached. Leave it to the view. GET and HEAD are now
        # ownership-scoped below, exactly like writes: reading another learner's
        # record by changing the id in the URL is the A5 read-path IDOR
        # (SECURITY_AUDIT.md A5), so a safe method is no longer a free pass. Staff
        # keep read access wherever the endpoint uses ``learner_self_or_staff``
        # (``allow_staff`` below); ``learner_self_only`` reads are the owner's alone.
        if request.method == "OPTIONS":
            authenticate_request(request)
            return view(request, *args, **kwargs)

        account = authenticate_request(request)
        if account is None:
            return _unauthenticated(request)

        if allow_admin and account.role == "admin":
            target_id = _target_learner_id(
                request, kwargs, kwarg=kwarg, query_param=query_param, body_field=body_field
            )
            if target_id is None or target_id <= 0:
                return _error_bad_target()
            # Preserve the administrator's session; never impersonate the target
            # by replacing login_account or changing its role/subject_id.
            request.login_account = account
            response = view(request, *args, **kwargs)
            audit_admin_learner_action(request, target_id, response)
            return response

        # A coach acts like an administrator, but only for their own caseload.
        # Other staff may act only on their own linked enrolment.
        if allow_admin and account.role == "staff":
            target_id = _target_learner_id(
                request, kwargs, kwarg=kwarg, query_param=query_param, body_field=body_field
            )
            if target_id is not None and coach_owns_learner(account, target_id):
                request.login_account = account
                request.coach_learner_action = True
                response = view(request, *args, **kwargs)
                audit_admin_learner_action(request, target_id, response)
                return response
            if target_id is not None and staff_owns_learner(account, target_id):
                request.login_account = account
                return view(request, *args, **kwargs)

        if account.role == "learner":
            target_id = _target_learner_id(
                request, kwargs, kwarg=kwarg, query_param=query_param, body_field=body_field
            )
            if target_id is None:
                return _error_bad_target()
            # Matched on id alone, like ``staff_only(allow_own_learner=...)``:
            # ids are unique across the single Created_users table, so the
            # learner kind is not part of the comparison.
            if target_id != account.subject_id:
                # 404, not 403 — a learner poking at another learner's id must
                # not have its existence confirmed.
                return JsonResponse({"error": "Not found."}, status=404)
            return view(request, *args, **kwargs)

        if allow_staff and account.role in {"admin", "staff"}:
            return view(request, *args, **kwargs)

        return _read_only_learner_view()

    return wrapped


def _error_bad_target():
    return JsonResponse(
        {"error": "The learner this request applies to could not be determined."},
        status=400,
    )


def learner_self_only(*, kwarg=None, query_param=None, body_field=None):
    """Owner-only access to private learner resources such as calendar connections.

    Learning actions use ``learner_self_or_admin`` so administrators can operate
    the selected learner workspace. This stricter gate keeps private connections
    restricted to their owner for both reads and writes. Other learners get 404;
    staff/admin get 403, and unauthenticated callers get 401.
    """

    def decorator(view):
        return _learner_progress_gate(
            view, kwarg=kwarg, query_param=query_param, body_field=body_field, allow_staff=False
        )

    return decorator


def learner_self_or_staff(*, kwarg=None, query_param=None, body_field=None):
    """The learner themselves, or staff/admin acting on their behalf.

    For coaching-session booking and cancellation, which staff legitimately do
    *for* a learner — arranging a catch-up is administration, not a claim about
    the learner's own work, so it is the one thing a staff viewer keeps on a
    learner's page.

    Still a real gate: before this, the learner id in the URL was the only thing
    naming whose calendar was written, so any signed-out caller could book
    against any learner and any learner could book against another's coach.
    """

    def decorator(view):
        return _learner_progress_gate(
            view, kwarg=kwarg, query_param=query_param, body_field=body_field, allow_staff=True
        )

    return decorator


def learner_self_or_admin(*, kwarg=None, query_param=None, body_field=None):
    """Learner actions available to their owner or an authenticated administrator.

    Staff may study their own linked enrolment; other staff previews remain
    read-only. Administrators may submit, edit and complete
    activities for the selected learner; their own account is retained and each
    write is audited. Private account connections continue to use self_only.
    """
    def decorator(view):
        return _learner_progress_gate(
            view, kwarg=kwarg, query_param=query_param, body_field=body_field,
            allow_staff=False, allow_admin=True,
        )

    return decorator
