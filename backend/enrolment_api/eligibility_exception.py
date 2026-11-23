"""Administrator exception from a saved ineligible application to onboarding.

The original screening result is immutable. The exception lives on the existing
application-to-learner link, so the learner's source remains inspectable.
"""
import json
import logging

from django.db import DatabaseError, IntegrityError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from learner_api.models import EnrolmentUser
from login.permissions import require_access
from login.security import client_ip, user_agent

from . import eligibility_form as form
from .eligibility_invitation import has_account
from .eligibility_link import CONN, TABLE, dispatch

logger = logging.getLogger(__name__)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _application(cursor, application_id):
    cursor.execute(f"SELECT a.work_email, a.eligible FROM enrolment.{form.TABLE} a WHERE a.id=%s FOR UPDATE", [application_id])
    row = cursor.fetchone()
    return {"work_email": row[0], "eligible": row[1]} if row else None


def _candidate(email):
    matches = list(EnrolmentUser.all_learners.filter(email__iexact=email).only(
        "id", "email", "username", "learner_type", "programme_status")[:2])
    if len(matches) > 1:
        raise ValueError("More than one learner has this email. Resolve the duplicate before linking.")
    if matches and (matches[0].learner_type or "apprenticeship").casefold() != "apprenticeship":
        raise ValueError("This email belongs to a commercial learner. Resolve the account before linking.")
    return matches[0] if matches else None


@never_cache
@require_access("enrolment")
@csrf_protect
def eligibility_exception(request, application_id):
    """Preview or approve an exception for a saved ineligible application."""
    account = getattr(request, "login_account", None)
    if account is None or account.role != "admin":
        return _error("Only an administrator can approve an eligibility exception.", 403)
    if request.method not in {"GET", "POST"}:
        return _error("Method not allowed.", 405)
    if not 1 <= application_id <= 9_223_372_036_854_775_807:
        return _error("Application not found.", 404)
    try:
        if request.method == "GET":
            with connections[CONN].cursor() as cursor:
                cursor.execute(f"SELECT a.work_email, a.eligible FROM enrolment.{form.TABLE} a WHERE a.id=%s", [application_id])
                result = cursor.fetchone()
                if not result:
                    return _error("Application not found.", 404)
                application = {"work_email": result[0], "eligible": result[1]}
                if application.get("eligible") is not False:
                    return _error("Only a saved ineligible application can receive an exception.", 409)
                candidate = _candidate(str(application.get("work_email") or "").strip())
                cursor.execute(f"SELECT learner_id, override_reason, accepted_at FROM {TABLE} WHERE application_id=%s", [application_id])
                link = cursor.fetchone()
            return JsonResponse({"existingLearner": {
                "id": str(candidate.pk), "name": candidate.username, "email": candidate.email,
                "programmeStatus": candidate.programme_status,
            } if candidate else None, "linkedLearnerId": str(link[0]) if link else None,
                "overrideReason": link[1] if link else None,
                "activationVerified": bool(link and link[2])})

        try:
            payload = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return _error("Invalid JSON.", 400)
        if not isinstance(payload, dict):
            return _error("Invalid request.", 400)
        reason = payload.get("reason")
        confirm_email = payload.get("confirmEmail")
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
            return _error("Give an exception reason of 10 to 1000 characters.", 400)
        if not isinstance(confirm_email, str):
            return _error("Confirm the applicant's email before approving.", 400)

        with transaction.atomic(using=CONN), connections[CONN].cursor() as cursor:
            application = _application(cursor, application_id)
            if application is None:
                return _error("Application not found.", 404)
            if application.get("eligible") is not False:
                return _error("Only a saved ineligible application can receive an exception.", 409)
            email = str(application.get("work_email") or "").strip().casefold()
            if not email or email != confirm_email.strip().casefold():
                return _error("The confirmed email does not match the saved application.", 400)
            # The migration must be present before any learner is created.
            cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='enrolment' "
                           "AND table_name='eligibility_learner_links' AND column_name='override_approved_at'")
            if not cursor.fetchone():
                return _error("Eligibility exception storage is not installed yet.", 503)
            candidate = _candidate(email)
            if candidate or has_account(email):
                return _error("An account already exists for this email. Resolve that account before sending an application invitation.", 409)
            cursor.execute(f"SELECT learner_id, override_reason FROM {TABLE} WHERE application_id=%s", [application_id])
            link = cursor.fetchone()
            if link and link[0] is not None:
                return _error("This application is already linked to a learner.", 409)
            if link is None:
                cursor.execute(f"INSERT INTO {TABLE} (application_id, override_approved_at, "
                               "override_approved_by_account_id, override_reason) VALUES (%s,now(),%s,%s)",
                               [application_id, account.pk, reason.strip()])
            else:
                cursor.execute(f"UPDATE {TABLE} SET override_approved_at=COALESCE(override_approved_at,now()), "
                               "override_approved_by_account_id=%s, override_reason=%s WHERE application_id=%s",
                               [account.pk, reason.strip(), application_id])
        outcome = dispatch(application_id, ip=client_ip(request), user_agent=user_agent(request))
        return JsonResponse({"learnerId": None,
                             "invitation": "sent" if outcome == "invited" else "notSent",
                             "message": "Invitation sent. The account will be created on acceptance." if outcome == "invited" else
                                        "Exception saved. Invitation delivery is pending; retry from this application."})
    except ValueError as exc:
        return _error(str(exc), 409)
    except IntegrityError as exc:
        logger.warning("Eligibility exception could not link application %s: %s", application_id, type(exc).__name__)
        return _error("The learner could not be linked. Check the account and try again.", 409)
    except DatabaseError:
        logger.exception("Eligibility exception failed for application %s", application_id)
        return _error("The eligibility exception could not be saved.", 503)
