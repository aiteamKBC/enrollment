"""Enrolment console endpoints.

The console is a single section whose user directory lists both apprenticeship and
commercial learners. Both kinds live in one table, enrolment."Created_users",
distinguished by its "Learner_type" column.

This module projects a commercial learner onto the EnrolmentBoard shape the
details wizard expects. Commercial rows leave the ILR/skills/notes columns null,
so those sections come back empty rather than 404, and wizard-only sections that
have nowhere to persist are rejected explicitly instead of being silently dropped.
"""
import json
import logging

from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from learner_api.constants import DEFAULT_PROGRAMME_STATUS
from learner_api.mappers import restrict_to_self_writable
from learner_api.models import CommercialUser

from .auth import enrolment_login_required

logger = logging.getLogger(__name__)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _s(value):
    return "" if value is None else str(value).strip()


def _as_list(value):
    return value if isinstance(value, list) else []


def _iso(value):
    """DateField -> 'YYYY-MM-DD' ('' when unset)."""
    return "" if value is None else str(value)


# Fields the commercial table can actually store, keyed by wizard payload name.
WRITABLE_FIELDS = {
    "username": "username",
    "name": "username",
    "email": "email",
    "phone": "phone_number",
    "employer": "employer",
    "lineManager": "line_manager",
    "organization": "organization",
    "reference": "organization",
    "programmeStatus": "programme_status",
    "programme": "programme",
    "cohort": "cohort",
    "group": "group",
}

_EMPTY_FS_BLOCK = {"assessments": [], "exempt": False, "evidence": []}


def commercial_to_board(u):
    """Project a CommercialUser onto the EnrolmentBoard shape.

    Sections with no commercial-side column are returned empty so the wizard
    renders its steps blank rather than erroring.
    """
    return {
        "user": {
            "id": str(u.id),
            "name": _s(u.username),
            "reference": _s(u.organization),
            # Case owner (set on the create form) is the learner's owner/coach;
            # line manager is the fallback for rows created before that field.
            "owner": _s(u.case_owner) or _s(u.line_manager),
        },
        "contact": {
            "email": _s(u.email),
            "phone": _s(u.phone_number),
            "dob": "",
            "groupMembership": _s(u.group),
            "signatureUrl": None,
            "hasMandate": False,
        },
        "activity": {
            "aptemUsage": "00:00",
            "daysTillNextReporting": 0,
            "lastLoggedIn": None,
            "logins": 0,
            "tasksAddedByUser": 0,
            "uncompletedTasks": 0,
            "adviceItemsAccessed": 0,
            "adviceLastAccessed": None,
            "actionPlans": "No plans created",
        },
        "programme": {
            "type": "Commercial",
            "name": _s(u.programme),
            "cohort": _s(u.cohort),
            "status": _s(u.programme_status) or DEFAULT_PROGRAMME_STATUS,
            "startDate": _iso(u.start_date),
            "endDate": _iso(u.end_date),
            "enrolledAt": "",
            "enrolledBy": "",
            "onboardingStatus": "Not started",
            "onboardingCompletedAt": None,
        },
        # No commercial-side columns exist for any of the following.
        "subProgrammes": [],
        "aims": [],
        "previousProgrammes": [],
        "functionalSkills": {
            "english": dict(_EMPTY_FS_BLOCK),
            "maths": dict(_EMPTY_FS_BLOCK),
            "ict": dict(_EMPTY_FS_BLOCK),
        },
        "managedJobs": [],
        "tracker": [],
        "milestones": [],
        "notes": [],
        "courseProgress": [],
        "contacts": [],
        "activities": [],
        "complianceDocuments": [],
        "reviewDocuments": [],
        "documents": [],
        "competencies": [],
        "subscription": {"startDate": "", "endDate": "", "status": ""},
        "auditTrail": [],
        "trainingPlan": _as_list(u.training_plan),
    }


def _parse_body(request):
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body)
    except ValueError:
        raise ValueError("Request body must be valid JSON.")
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


@enrolment_login_required
@csrf_exempt
def commercial_board(request, pk):
    """GET/PATCH the wizard board for a single commercial learner."""
    try:
        user = CommercialUser.objects.get(pk=pk)
    except CommercialUser.DoesNotExist:
        return _error("Commercial learner not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if request.method == "GET":
        return JsonResponse(commercial_to_board(user))

    if request.method in ("PATCH", "PUT"):
        try:
            payload = _parse_body(request)
        except ValueError as exc:
            return _error(str(exc), 400)

        # A learner reaches this endpoint only for their own board (the gate in
        # enrolment_api.auth scopes them by id), but owning the row is not
        # permission to set every field on it — WRITABLE_FIELDS includes
        # programmeStatus, which would let them promote themselves past
        # enrolment. Same allowlist the apprenticeship wizard's PATCH uses.
        account = getattr(request, "login_account", None)
        if account is not None and account.role == "learner":
            payload, rejected = restrict_to_self_writable(payload)
            if rejected:
                logger.warning(
                    "Learner %s attempted to write board fields they do not own: %s",
                    pk, ", ".join(rejected),
                )

        fields = {}
        for key, value in payload.items():
            attr = WRITABLE_FIELDS.get(key)
            if attr is not None:
                fields[attr] = None if value is None else str(value).strip()

        if payload.get("trainingPlan") is not None:
            plan = payload["trainingPlan"]
            if not isinstance(plan, list):
                return _error("trainingPlan must be a list.", 400)
            fields["training_plan"] = plan

        # Be explicit rather than silently discarding wizard-only data: the
        # commercial table has no columns for these sections.
        unsupported = sorted(
            k for k in payload
            if k not in WRITABLE_FIELDS and k != "trainingPlan"
        )
        if unsupported and not fields:
            return _error(
                "Commercial learners cannot store: " + ", ".join(unsupported),
                400,
            )

        if fields:
            for attr, value in fields.items():
                setattr(user, attr, value)
            try:
                user.save(update_fields=list(fields.keys()))
            except DatabaseError as exc:
                return _error(f"Database error: {exc}", 502)

        return JsonResponse(commercial_to_board(user))

    return _error("Method not allowed.", 405)


@require_GET
def health(request):
    """Liveness probe — confirms the app is installed and routed."""
    return JsonResponse({"status": "ok", "app": "enrolment_api"})
