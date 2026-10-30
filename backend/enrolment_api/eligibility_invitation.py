"""Turn an eligible apprenticeship application into a learner with an invitation.

Called by the public eligibility form (eligibility_form.submit_application)
once the postcode check finds the applicant eligible. It creates the
apprenticeship learner in enrolment."Created_users" from the answers, provisions
their login account and emails the set-password invitation
(login.services.invite_eligible_applicant).

An address that already has a learner record or a login account is never
re-created or re-invited: the applicant is told to sign in, or to use Forgot
password, which sends a learner who never set a password their invitation
(login.services.request_password_link). That also means the form sends any one
address at most one invitation.

The chosen standard is kept in Target_programme, not Programme: Programme and
Cohort place a learner in delivery, and staff do that once they have reviewed
the application.
"""
import logging

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.functions import Lower, Trim
from django.utils import timezone

logger = logging.getLogger(__name__)

#: What happened, as the applicant's page reports it.
INVITED = "invited"
INVITE_PENDING = "invitePending"
EXISTING_ACCOUNT = "existingAccount"
RETRY_LATER = "retryLater"


def has_account(email):
    """Whether `email` already belongs to a learner record or a login account."""
    from learner_api.models import EnrolmentUser
    from login.models import LoginAccount
    from login.security import normalize_email

    address = normalize_email(email)
    if LoginAccount.objects.filter(email=address).exists():
        return True
    return (
        EnrolmentUser.all_learners
        .annotate(_normalised_email=Lower(Trim("email")))
        .filter(_normalised_email=address)
        .exists()
    )


def learner_fields(row):
    """The Created_users columns an application fills in, from its stored row."""
    from learner_api.constants import DEFAULT_PROGRAMME_STATUS

    name = " ".join(part for part in (row.get("first_name"), row.get("surname")) if part)
    date_of_birth = row.get("date_of_birth")
    stamp = timezone.now().strftime("%d/%m/%Y %H:%M:%S")
    return {
        "learner_type": "apprenticeship",
        "username": name,
        "email": row["work_email"],
        "phone_number": row.get("phone_number"),
        "date_of_birth": date_of_birth.isoformat() if date_of_birth else None,
        "address": row.get("full_address"),
        "current_postcode": row.get("postcode"),
        "employer": row.get("employer_name"),
        "employer_address": row.get("employer_address"),
        "target_programme": row.get("programme"),
        "programme_status": DEFAULT_PROGRAMME_STATUS,
        "invite_to_platform": True,
        "enrolled_time_and_user": f"{stamp} by Eligibility form",
    }


def invite_applicant(row, *, ip=None, user_agent=None):
    """Create the learner for an eligible application and invite them.

    Returns one of the outcomes above. Never raises: the application itself is
    already saved, so a failure here is reported, not thrown.
    """
    from learner_api.learner_progression import advance_learner
    from learner_api.models import EnrolmentUser
    from login.identity import AccountError
    from login.services import invite_eligible_applicant

    try:
        if has_account(row["work_email"]):
            return EXISTING_ACCOUNT
        with transaction.atomic(using="enrolment"):
            learner = EnrolmentUser.all_learners.create(**learner_fields(row))
    except IntegrityError:
        # The unique email index: the same address was submitted twice at once.
        return EXISTING_ACCOUNT
    except DatabaseError:
        logger.exception("Could not create the learner for an eligible application")
        return RETRY_LATER

    advance_learner(learner)
    try:
        sent, detail = invite_eligible_applicant(learner, ip=ip, user_agent=user_agent)
    except (AccountError, DatabaseError):
        logger.exception("Could not invite learner %s from the eligibility form", learner.id)
        return INVITE_PENDING
    if detail == "existing-account":
        return EXISTING_ACCOUNT
    if not sent:
        logger.warning("Invitation for learner %s from the eligibility form was not sent: %s", learner.id, detail)
        return INVITE_PENDING
    logger.info("Learner %s created and invited from the eligibility form", learner.id)
    return INVITED
