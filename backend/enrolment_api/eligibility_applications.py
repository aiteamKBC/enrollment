"""Staff-only, read-only access to saved eligibility screening applications.

No runtime schema upgrades, re-screening, postcode calls or invitation actions.
Explicit JSON projections tolerate missing additive columns on legacy tables;
internal submission fingerprints/receipts and unknown custom columns stay private.
"""
import json
import logging

from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from login.permissions import require_access
from . import eligibility_form as form

logger = logging.getLogger(__name__)

LIST_COLUMNS = (
    "id", "first_name", "surname", "work_email", "submitted_at", "employer_name",
    "employer_postcode", "main_workplace_postcode", "workplace_arrangement",
    "assessment_status", "assessment_reason", "review_status", "review_version",
)
DETAIL_COLUMNS = tuple(dict.fromkeys(
    LIST_COLUMNS + form.COLUMNS + form.LOCATION_COLUMNS + form.WORKPLACE_COLUMNS
    + ("layout_version", "eligible", "ineligible_reason")
))
STATUSES = (form.PENDING_ASSESSMENT, form.PROVISIONAL_ELIGIBLE, form.NOT_ELIGIBLE, "legacy", "all", "approved", "rejected", "more_evidence_required")
STATUS_SQL = "to_jsonb(a)->>'assessment_status'"
UNAVAILABLE = "Eligibility applications could not be loaded. Please try again."


def _projection(columns, *, outcome=False):
    # Only server-owned identifiers reach SQL. Never SELECT * into an API.
    pairs = [f"'{key}', to_jsonb(a)->'{key}'" for key in columns]
    if outcome:
        pairs.append("'invitation_outcome', to_jsonb(a)->'submission_response'->'outcome'")
    # PostgreSQL limits a function call to 100 arguments (two per key/value).
    chunks = [f"jsonb_build_object({', '.join(pairs[start:start + 40])})" for start in range(0, len(pairs), 40)]
    return "(" + " || ".join(chunks) + ")"


def _record(value):
    # Raw PostgreSQL JSONB may be returned as text rather than a decoded object.
    result = json.loads(value) if isinstance(value, str) else value
    result["id"] = str(result["id"])
    return result


def _positive_int(request, key, default, maximum):
    value = request.GET.get(key, str(default))
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= maximum:
        raise ValueError(f"'{key}' must be a whole number between 1 and {maximum}.")
    return int(value)


@never_cache
@require_access("enrolment")
def eligibility_applications(request):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405, headers={"Allow": "GET"})
    try:
        page = _positive_int(request, "page", 1, 1_000_000)
        page_size = _positive_int(request, "pageSize", 25, 100)
        status = request.GET.get("status", form.PENDING_ASSESSMENT)
        search = request.GET.get("search", "").strip()
        if status not in STATUSES:
            raise ValueError("Choose a listed assessment status.")
        if len(search) > 200:
            raise ValueError("Search must be 200 characters or fewer.")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    clauses, params = [], []
    if status == "legacy":
        clauses.append(f"{STATUS_SQL} IS NULL")
    elif status in ("approved", "rejected", "more_evidence_required"):
        clauses.append("to_jsonb(a)->>'review_status' = %s")
        params.append(status)
    elif status != "all":
        clauses.append(f"{STATUS_SQL} = %s")
        params.append(status)
        if status == form.PENDING_ASSESSMENT:
            clauses.append("COALESCE(to_jsonb(a)->>'review_status', '') NOT IN ('approved', 'rejected')")
    if search:
        # Literal, case-insensitive substring search: % and _ are not wildcards.
        clauses.append("strpos(lower(concat_ws(' ', a.first_name, a.surname, a.work_email, "
                       "a.employer_name, a.employer_postcode, to_jsonb(a)->>'main_workplace_postcode')), lower(%s)) > 0")
        params.append(search)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        with connections[form.CONN].cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM enrolment.{form.TABLE} a{where}", params)
            total = cursor.fetchone()[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            cursor.execute(
                f"SELECT {_projection(LIST_COLUMNS)} FROM enrolment.{form.TABLE} a{where} "
                "ORDER BY a.submitted_at DESC NULLS LAST, a.id DESC LIMIT %s OFFSET %s",
                params + [page_size, (page - 1) * page_size],
            )
            applications = [_record(row[0]) for row in cursor.fetchall()]
    except DatabaseError:
        logger.exception("Could not read the eligibility applications queue")
        return JsonResponse({"error": UNAVAILABLE}, status=503)
    return JsonResponse({"applications": applications, "total": total, "page": page, "pageSize": page_size})


@never_cache
@require_access("enrolment")
def eligibility_application_detail(request, application_id):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405, headers={"Allow": "GET"})
    if not 1 <= application_id <= 9_223_372_036_854_775_807:
        return JsonResponse({"error": "Application not found."}, status=404)
    try:
        with connections[form.CONN].cursor() as cursor:
            cursor.execute(
                f"SELECT {_projection(DETAIL_COLUMNS, outcome=True)} FROM enrolment.{form.TABLE} a WHERE a.id = %s",
                [application_id],
            )
            row = cursor.fetchone()
    except DatabaseError:
        logger.exception("Could not read an eligibility application")
        return JsonResponse({"error": UNAVAILABLE}, status=503)
    if row is None:
        return JsonResponse({"error": "Application not found."}, status=404)
    return JsonResponse({"application": _record(row[0])})
