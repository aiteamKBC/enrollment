"""Give an existing account a learner record as well.

Somebody already on the platform -- a coach, a tutor, an administrator -- can
also study a programme. This creates the ``enrolment."Created_users"`` row that
makes that true, and links it to the account they already have.

One account, two workspaces
---------------------------
Deliberately NOT a second login account. The unique index on
``login."Login_accounts"`` is ``(Subject_type, lower(Email))``, so a second row
on the same address is permitted by the schema -- but
``identity.account_for_email`` returns ``None`` when an address resolves to more
than one active account, and password sign-in, Microsoft SSO and password reset
all go through it. Creating that second account would lock the person out of
every one of those paths, reported as a plain credential failure with nothing to
explain it.

So the account stays exactly as it is, and the enrolment row points back at it.
The workspace switcher gains a Learner entry the same way a coach who also
tutors gets two: one sign-in, two places to go.

What this does and does not do
------------------------------
Creates the enrolment record, and nothing further. It does **not** create the
``"Learner".learners`` row: that is what finishing enrolment is for
(``learner_api.views.enrolment_user_finish`` -> ``sync_active_user``), and it
happens only once a plan has been assigned. An in-progress enrolment must not
appear as a live learner, which is the same rule every other learner follows.
"""
from django.db import DatabaseError, transaction

from learner_api.constants import DEFAULT_PROGRAMME_STATUS
from learner_api.models import EnrolmentUser, StaffUser

from .identity import SUBJECT_LEARNER, SUBJECT_STAFF, normalize_email

#: What a learner record needs to be a usable enrolment draft. Mirrors the
#: import command's defaults so a person added this way is indistinguishable
#: from one created through the enrolment form.
ENROLMENT_DEFAULTS = {
    "status": "FullUser",
    "type": "User",
    "learner_type": "commercial",
    # Starts as a draft, not Active: a plan has to be built and enrolment
    # finished before this person is a live learner.
    "programme_status": DEFAULT_PROGRAMME_STATUS,
    # Entitled to reach the learner workspace. They already have an account, so
    # no invitation is sent -- that is what makes this "also a learner" rather
    # than "a new person".
    "invite_to_platform": True,
}


class EnrolmentError(ValueError):
    """Raised with a message suitable for the console when this cannot be done."""


def existing_learner_record(email):
    """The learner enrolment row for this address, or None.

    Matched on the address rather than on the account, because the point of the
    lookup is to find a record somebody may already have -- created through the
    enrolment form, or imported -- before making a second one.
    """
    email = normalize_email(email)
    if not email:
        return None
    return EnrolmentUser.all_learners.filter(email__iexact=email).first()


def can_add_learner_record(account):
    """Why this account cannot also be a learner, or '' when it can.

    Returned as a reason rather than a bare boolean so the console can say what
    is wrong instead of disabling a button with no explanation.
    """
    if account is None:
        return "Account not found."
    if account.subject_type == SUBJECT_LEARNER:
        return "This account is already a learner."
    if not normalize_email(account.email):
        return "This account has no email address."
    if existing_learner_record(account.email) is not None:
        return "A learner record already exists for this address."
    return ""


@transaction.atomic(using="enrolment")
def add_learner_record(account, *, programme="", cohort="", group="", learner_type="", created_by=""):
    """Create the enrolment record for an account that already exists.

    Returns the new ``EnrolmentUser``. Raises ``EnrolmentError`` when the
    account cannot have one -- it is already a learner, has no address, or a
    learner record for that address exists already.

    The name is taken from the person's own staff record where there is one, so
    the learner they become is recognisably the same person rather than a row
    labelled with whatever the account happened to cache.
    """
    reason = can_add_learner_record(account)
    if reason:
        raise EnrolmentError(reason)

    email = normalize_email(account.email)
    username = (account.display_name or "").strip()
    phone = ""
    if account.subject_type == SUBJECT_STAFF:
        staff = StaffUser.objects.filter(pk=account.subject_id).first()
        if staff is not None:
            username = (staff.username or "").strip() or username
            phone = (staff.phone_number or "").strip()

    try:
        return EnrolmentUser.all_learners.create(
            email=email,
            username=username or email,
            phone_number=phone,
            programme=(programme or "").strip(),
            cohort=(cohort or "").strip(),
            group=(group or "").strip(),
            enrolled_time_and_user=(created_by or "").strip()[:255],
            **{**ENROLMENT_DEFAULTS,
               **({"learner_type": learner_type.strip()} if (learner_type or "").strip() else {})},
        )
    except DatabaseError as exc:
        raise EnrolmentError(f"Database error: {exc}") from exc
