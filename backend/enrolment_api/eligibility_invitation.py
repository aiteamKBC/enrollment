"""Stage an invitation for an eligible application without creating an account.

The learner and login account are created together when the invitation is
accepted. Existing addresses are not re-created by public submissions.
"""
import logging

from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: What happened, as the applicant's page reports it.
INVITED = "invited"
INVITE_PENDING = "invitePending"
EXISTING_ACCOUNT = "existingAccount"
RETRY_LATER = "retryLater"


def has_account(email):
    """Whether this address already belongs to any enrolment person."""
    from learner_api.profile_email import profile_email_in_use

    return profile_email_in_use(email)


def learner_fields(row):
    """The Created_users columns an application fills in, from its stored row."""
    from learner_api.constants import DEFAULT_PROGRAMME_STATUS
    from .eligibility_prefill import learner_values

    name = " ".join(part for part in (row.get("first_name"), row.get("surname")) if part)
    date_of_birth = row.get("date_of_birth")
    stamp = timezone.now().strftime("%d/%m/%Y %H:%M:%S")
    return {
        "learner_type": "apprenticeship",
        "username": name,
        "email": row["work_email"],
        "phone_number": row.get("phone_number"),
        "date_of_birth": (date_of_birth.isoformat() if hasattr(date_of_birth, "isoformat")
                          else str(date_of_birth) if date_of_birth else None),
        "address": row.get("full_address"),
        "current_postcode": row.get("postcode"),
        "employer": row.get("employer_name"),
        "employer_address": row.get("employer_address"),
        "programme_status": DEFAULT_PROGRAMME_STATUS,
        "invite_to_platform": True,
        "enrolled_time_and_user": f"{stamp} by Eligibility form",
        **learner_values(row),
    }


def invite_applicant(row, *, application_id=None, ip=None, user_agent=None):
    """Invite an eligible applicant without creating their learner account yet.

    Returns one of the outcomes above. Never raises: the application itself is
    already saved, so a failure here is reported, not thrown.
    """
    if row.get("assessment_status") is not None and not (
        row.get("assessment_status") == "provisional_eligible" and row.get("eligible") is True
    ):
        return "pendingAssessment" if row.get("assessment_status") == "pending_assessment" else "notEligible"

    try:
        if has_account(row["work_email"]):
            return EXISTING_ACCOUNT
        with transaction.atomic(using="enrolment"):
            from . import eligibility_link
            eligibility_link.add_link(application_id, None)
            result = {'outcome': INVITE_PENDING}
            def deliver():
                result['outcome'] = eligibility_link.dispatch(application_id, ip=ip, user_agent=user_agent)
            transaction.on_commit(deliver, using='enrolment')
        return result['outcome']
    except IntegrityError:
        return EXISTING_ACCOUNT
    except (DatabaseError, ValueError):
        logger.exception("Could not stage an eligibility invitation")
        return RETRY_LATER
