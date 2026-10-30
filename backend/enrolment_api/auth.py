"""Authentication gate for the enrolment endpoints (BE-2).

Every enrolment view — the commercial board, the Extended ILR, the wizard
bootstrap, the generated documents, and the four `learner_api` statutory
documents — was reachable with no authentication at all. Anyone who could reach
the URL could read or patch a learner, and issue or sign a compliance document.

`@enrolment_login_required` closes that. Set `ENROLMENT_API_REQUIRE_AUTH=0` (or
false/no) to disable the gate entirely — intended only for local development and
test setups that have no session, never for a deployment.

Which identity counts
---------------------
The gate originally tested `request.user.is_authenticated` — Django's own auth.
Nothing in this platform ever signs a person in that way: people authenticate
through the `login` app, whose middleware resolves the `kbc_session` cookie into
``request.login_account`` and leaves ``request.user`` anonymous. The result was
that every one of these endpoints answered 401 to every real user, staff and
learner alike. Both identities are now accepted, so a Django-admin session keeps
working and a platform session finally does.

Who may reach whose record
--------------------------
Accepting the platform identity is not enough on its own. These endpoints are
addressed by learner id and had no ownership check, so "any authenticated user"
would have meant any learner reading — and *signing* — any other learner's
enrolment record and compliance documents.

So the gate is scoped by role:

* **staff / admin** — any learner. This is the boundary the enrolment console
  needs and the one this module always intended.
* **learner** — their own record only. They need it: a learner at the
  'Onboarding' status fills in this very wizard and signs their own ILR and
  apprenticeship agreement.
* **employer** — refused for now. Employers have their own portal
  (`learner_api.employer_portal`); scoping them here would be speculative.

A learner asking for somebody else's record gets **404, not 403**: a 403 would
confirm that the id exists, and this codebase already collapses failure modes
for that reason (see `login.invitations._load_token`).

An endpoint with no learner id in its URL cannot be ownership-checked, so it is
staff-only unless explicitly listed in ``UNSCOPED_VIEWS``. That way a new
unscoped endpoint fails closed rather than silently becoming readable by every
learner.
"""
import functools
import os

from django.http import JsonResponse

#: Env values that turn the gate OFF. Anything else (including unset) keeps it ON.
_DISABLED_VALUES = {"0", "false", "no", "off"}

#: Roles that may act on any learner.
STAFF_ROLES = frozenset({"admin", "staff"})

#: URL kwargs that carry the learner's id. Both spellings are in use: the
#: enrolment routes name it `learner_id`, the statutory-document and commercial
#: board routes inherited `pk` from their original single-object shape.
LEARNER_ID_KWARGS = ("learner_id", "pk")

#: Views that legitimately have no learner in their URL and are safe for any
#: authenticated caller. `document_types` is a static registry of doc types;
#: `health` reports readiness and no learner data. Anything not listed here and
#: lacking a learner id is treated as staff-only.
# open_policy_document: the college policies every enrolling learner reads —
# the same files for everyone, with nothing learner-specific in them.
# wizard_layout: which questions the enrolment wizard asks — the same for every
# learner, with no answers in it. (Publishing a layout is staff-only.)
UNSCOPED_VIEWS = frozenset({"health", "document_types", "open_policy_document", "wizard_layout"})

#: Views an employer may reach, and only for a learner they employ: signing the
#: compliance documents that carry an employer signature. Each one also refuses
#: an employer signing any party but "employer" (employer_signing_party_error).
EMPLOYER_LEARNER_VIEWS = frozenset({"sign_written_agreement", "sign_training_plan", "sign_agreement"})


def auth_required():
    """Whether the gate is active. Read per-request so tests can toggle it."""
    return os.environ.get("ENROLMENT_API_REQUIRE_AUTH", "1").strip().lower() not in _DISABLED_VALUES


def is_authenticated(request):
    """Whether the request carries either identity.

    ``request.user`` covers a Django-admin session; ``request.login_account`` is
    the platform's own, set by ``login.middleware.LoginSessionMiddleware``.
    """
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return True
    account = getattr(request, "login_account", None)
    return bool(account and account.is_active)


def _accesses_of(account):
    """Every staff access grant for this account, or an empty set for anyone else.

    Read live from the staff row rather than from the session, so an access
    changed in the console takes effect on the next request instead of the next
    sign-in. Fails closed — an unreadable grant is not a grant.

    A set, not a single value: an account may hold several grants, and asking
    ``== "enrolment"`` of somebody who holds enrolment AND coach would refuse
    them the enrolment workspace they were deliberately given.
    """
    if account.subject_type != "staff":
        return frozenset()
    from django.db import DatabaseError

    from learner_api.models import StaffUser
    from login.identity import accesses_for_staff

    try:
        row = (
            StaffUser.objects.filter(pk=account.subject_id)
            .only("access", "access_extra")
            .first()
        )
    except DatabaseError:
        return frozenset()
    return accesses_for_staff(row) if row else frozenset()


def _requested_learner_id(kwargs):
    """The learner id this request addresses, or None if the URL names none."""
    for name in LEARNER_ID_KWARGS:
        if name in kwargs:
            try:
                return int(kwargs[name])
            except (TypeError, ValueError):
                return None
    return None


def _may_access(request, view_name, kwargs):
    """Whether the caller may act on the learner this URL addresses.

    Returns True, or False when the answer should be "no such learner".
    """
    account = getattr(request, "login_account", None)

    # A Django-admin session has no login_account and no role to scope by. It is
    # already a superuser-grade identity, so it keeps the access it had.
    if account is None:
        return True

    if account.role in STAFF_ROLES:
        # Staff are additionally narrowed by their access grant. Enrolment is the
        # one the user asked to be exclusive: only it and super-admin may touch
        # enrolment records, so a curriculum designer or coach holding a staff
        # account cannot read or change a learner's enrolment through here.
        #
        # An account with no access recorded is refused: Position no longer
        # grants anything, so "unset" means undecided, not trusted.
        from learner_api.constants import ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN

        return bool(_accesses_of(account) & {ACCESS_ENROLMENT, ACCESS_SUPER_ADMIN})

    if account.role == "learner":
        learner_id = _requested_learner_id(kwargs)
        # No learner in the URL: nothing to scope against, so only the views
        # explicitly declared safe are reachable.
        if learner_id is None:
            return view_name in UNSCOPED_VIEWS
        # Matched on id alone, not (kind, id): ids are unique across the single
        # Created_users table and `kind` is cosmetic in these routes, so a
        # learner whose session pins one spelling must still reach their record.
        return learner_id == account.subject_id

    if account.role == "employer" and view_name in EMPLOYER_LEARNER_VIEWS:
        return _employs(account, _requested_learner_id(kwargs))

    # Employers (outside the views above) and any future role: not scoped yet.
    return view_name in UNSCOPED_VIEWS


def _employs(account, learner_id):
    """Whether the learner's employer record is this employer account's own."""
    if learner_id is None:
        return False
    from learner_api.models import EnrolmentUser

    employer_id = (
        EnrolmentUser.all_learners.filter(pk=learner_id).values_list("employer_id", flat=True).first()
    )
    return employer_id is not None and employer_id == account.subject_id


def employer_signing_party_error(request, party):
    """403 when an employer tries to sign a compliance document as another party.

    The document sign views take the party from the request body, so the gate in
    enrolment_login_required cannot see it; each of them calls this instead.
    """
    account = getattr(request, "login_account", None)
    if auth_required() and account is not None and account.role == "employer" and party != "employer":
        return JsonResponse({"error": "An employer can only sign as the employer."}, status=403)
    return None


def enrolment_login_required(view):
    """Reject unauthenticated callers with 401, and out-of-scope ones with 404."""

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        # The toggle bypasses the whole gate, scoping included: it exists for
        # sessionless dev and test runs, where there is no account to scope by.
        if not auth_required():
            return view(request, *args, **kwargs)

        if not is_authenticated(request):
            return JsonResponse(
                {"error": "Authentication required."},
                status=401,
            )

        if not _may_access(request, view.__name__, kwargs):
            return JsonResponse(
                {"error": "Not found."},
                status=404,
            )

        return view(request, *args, **kwargs)

    return wrapped
