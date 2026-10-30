"""The enrolment wizard's Inclusion step: has the learner answered the
inclusion assessment?

    GET /enrolment_api/wizard/<kind>/<learner_id>/inclusion/
        -> 200 {"applicable": true, "completed": bool, "respondedAt": iso | null}
        -> 200 {"applicable": false}   commercial learners: the step is apprenticeship-only
        -> 404 no such learner, 503 the Safeguarding database could not be read

The assessment itself lives on the Safeguarding site
(safeguarding.kentbusinesscollege.net), which the learner opens from the step in
a new tab and signs in to through the LMS (login/safeguarding_sso.py). Its
answers land in public.learner_inclusiveness_quiz_responses in the Safeguarding
database (SafeGuarding_database_url), which the LMS reads but does not own.

Matched on email, never on id: that table's learner_id is the Safeguarding
app's own. The learner's emails are their record's and their sign-in
account's, compared trimmed and lower-cased. Any row for one of them counts as
done. Read-only: nothing here writes to either database.
"""
import logging

import psycopg
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from coach_api.support_tickets import SupportTicketsUnavailable, _connect, _normalize_email
from learner_api.models import CommercialUser, EnrolmentUser
from login.models import SUBJECT_LEARNER, LoginAccount

from .auth import enrolment_login_required

logger = logging.getLogger(__name__)

RESPONSES_TABLE = "public.learner_inclusiveness_quiz_responses"


def learner_emails(learner):
    """Every email the learner may have reached the Safeguarding site with."""
    emails = {_normalize_email(learner.email)}
    accounts = LoginAccount.objects.filter(subject_type=SUBJECT_LEARNER, subject_id=learner.pk).values_list("email", flat=True)
    emails.update(_normalize_email(email) for email in accounts)
    emails.discard("")
    return sorted(emails)


def response_summary(emails):
    """(completed, latest response time) for these emails. Raises SupportTicketsUnavailable or psycopg.Error."""
    if not emails:
        return False, None
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"select count(*) as responses, max(updated_at) as latest from {RESPONSES_TABLE} "
                "where lower(trim(learner_email)) = any(%s)",
                [emails],
            )
            row = cursor.fetchone()
    return bool(row and row["responses"]), row["latest"] if row else None


@enrolment_login_required
@require_GET
def wizard_inclusion(request, kind, learner_id):
    if kind == "commercial":
        if not CommercialUser.objects.filter(pk=learner_id).exists():
            return JsonResponse({"error": "Learner not found."}, status=404)
        return JsonResponse({"applicable": False})
    if kind != "apprenticeship":
        return JsonResponse({"error": "Unknown learner kind."}, status=400)
    try:
        learner = EnrolmentUser.objects.filter(pk=learner_id).first()
        emails = learner_emails(learner) if learner else []
    except DatabaseError:
        return JsonResponse({"error": "The learner record could not be loaded."}, status=502)
    if learner is None:
        return JsonResponse({"error": "Learner not found."}, status=404)
    try:
        completed, latest = response_summary(emails)
    except (SupportTicketsUnavailable, psycopg.Error) as exc:
        logger.warning("Could not read inclusion responses for learner %s: %s", learner_id, exc)
        return JsonResponse({"error": "We could not check the inclusion assessment just now. Please try again in a few minutes."}, status=503)
    response = JsonResponse({
        "applicable": True,
        "completed": completed,
        "respondedAt": latest.isoformat() if latest else None,
    })
    response["Cache-Control"] = "private, no-store"
    return response
