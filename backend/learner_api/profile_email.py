"""Reserve one email address for one enrolment person, regardless of role.

Call ``assert_available_for_new_profile`` inside an enrolment transaction before
inserting a learner, staff member, or employer. The transaction-scoped advisory
lock serialises concurrent creations across those three tables; each table's
own unique index cannot enforce uniqueness across the other tables.
"""

from django.db import connections
from django.db.models.functions import Lower, Trim

from login.models import LoginAccount
from login.security import normalize_email

from .models import Employer, EnrolmentUser, StaffUser


class ProfileEmailInUse(ValueError):
    pass


def profile_email_in_use(email):
    """Include profiles without a login account and inactive login accounts."""
    address = normalize_email(email)
    if not address:
        return False
    for manager in (LoginAccount.objects, EnrolmentUser.all_learners,
                    StaffUser.objects, Employer.objects):
        if (manager.annotate(_email_key=Lower(Trim("email")))
                .filter(_email_key=address).exists()):
            return True
    return False


def assert_available_for_new_profile(email):
    """Lock this email until the enclosing transaction inserts its new profile."""
    address = normalize_email(email)
    if not address:
        return
    connection = connections["enrolment"]
    if not connection.in_atomic_block:
        raise RuntimeError("Email uniqueness check requires an enrolment transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [address])
    if profile_email_in_use(address):
        raise ProfileEmailInUse(
            "This email already belongs to a learner, administrator, staff member, or employer."
        )


def assert_available_for_email_change(current_email, new_email):
    """Permit edits to a profile's existing address, including legacy overlaps."""
    if normalize_email(current_email) != normalize_email(new_email):
        assert_available_for_new_profile(new_email)
