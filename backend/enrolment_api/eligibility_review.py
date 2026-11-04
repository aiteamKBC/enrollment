"""Manual decisions, separate from immutable screening/submission evidence.

No account, postcode, invitation or mail service is called here. All mutations
lock the application, compare the viewed version and append one audit entry.
"""
import json
import logging
from uuid import UUID

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from login.permissions import require_access
from . import eligibility_form as form

logger = logging.getLogger(__name__)
TABLE = "eligibility_application_reviews"
STATUSES = ("approved", "rejected", "more_evidence_required")
TRANSITIONS = {None: STATUSES, "more_evidence_required": ("approved", "rejected")}
COLUMN_DEFINITIONS = (
    ("review_status", "text CONSTRAINT eligibility_review_status_check CHECK (review_status IN ('approved', 'rejected', 'more_evidence_required'))"),
    ("review_version", "integer CONSTRAINT eligibility_review_version_check CHECK (review_version >= 1)"),
)
CONSISTENCY = (
    "(review_status IS NULL AND review_version IS NULL) OR "
    "(review_status IS NOT NULL AND review_version IS NOT NULL AND "
    "assessment_status IS NOT DISTINCT FROM 'pending_assessment' AND eligible IS NULL)"
)
PUBLIC_MESSAGES = {
    "approved": "Your assessment has passed. A separate invitation action is still required; no learner account or invitation has been created by this review. This is not final government funding approval.",
    "rejected": "Your application has not passed assessment. Please contact admissions if you have questions about this decision.",
    "more_evidence_required": "More evidence or employer confirmation is required before an assessment decision can be made. Please contact admissions to discuss what is needed.",
}
UNAVAILABLE = "Application review is temporarily unavailable. Please try again."


def overlay_receipt(receipt, status, version):
    """Return current public review state without rewriting the original receipt.

    Staff notes/identity are deliberately excluded. Assessment fields still
    describe original screening; review.status describes the manual decision.
    Older clients retain safe pending/notEligible outcomes and cannot invite.
    """
    if status is None:
        return receipt
    if status not in STATUSES or not isinstance(version, int) or version < 1:
        raise DatabaseError("Invalid manual review state")
    return {**receipt, "eligible": False if status == "rejected" else None,
            "outcome": "notEligible" if status == "rejected" else form.PENDING_OUTCOME,
            "review": {"status": status, "version": version, "message": PUBLIC_MESSAGES[status]}}


def _application(cursor, application_id, lock):
    cursor.execute(
        f"SELECT jsonb_build_object('id', a.id, 'assessment_status', to_jsonb(a)->'assessment_status', "
        "'eligible', to_jsonb(a)->'eligible', 'review_status', to_jsonb(a)->'review_status', "
        "'review_version', to_jsonb(a)->'review_version', 'ready', "
        "(to_jsonb(a) ? 'review_status' AND to_jsonb(a) ? 'review_version')) "
        f"FROM enrolment.{form.TABLE} a WHERE a.id = %s FOR {lock}", [application_id],
    )
    found = cursor.fetchone()
    return _json(found[0]) if found else None


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _payload(cursor, row):
    status, stored_version = row["review_status"], row["review_version"]
    if (status is None and stored_version is not None) or (status is not None and (
            status not in STATUSES or type(stored_version) is not int or stored_version < 1)):
        raise DatabaseError("Invalid manual review state")
    cursor.execute("SELECT to_regclass(%s)", [f"enrolment.{TABLE}"])
    available = bool(cursor.fetchone()[0]) and row["ready"]
    history = []
    if available:
        cursor.execute(
            "SELECT jsonb_build_object('id', id::text, 'applicationId', application_id::text, "
            "'version', version, 'reviewerId', reviewer_id::text, 'reviewerName', reviewer_name, "
            "'previousStatus', previous_status, 'status', new_status, 'notes', reason, 'decidedAt', decided_at) "
            f"FROM enrolment.{TABLE} WHERE application_id = %s ORDER BY version DESC", [row["id"]],
        )
        history = [_json(value[0]) for value in cursor.fetchall()]
    version = row.get("review_version") or 0
    if (history and (history[0]["version"] != version or history[0]["status"] != row["review_status"])) or (version and not history):
        raise DatabaseError("Review history does not match application")
    enabled = available and form.workplace_enabled()
    reviewable = row["assessment_status"] == form.PENDING_ASSESSMENT and row["eligible"] is None
    actions = TRANSITIONS.get(row["review_status"], ()) if enabled and reviewable else ()
    return {"status": row["review_status"], "version": version, "history": history,
            "enabled": enabled, "available": available, "allowedActions": list(actions)}


def _body(request):
    if len(request.body) > 12 * 1024:
        raise ValueError("The review request is too large.")
    try:
        body = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Request body must be valid JSON.") from None
    if not isinstance(body, dict) or set(body) != {"action", "notes", "expectedVersion", "requestId"}:
        raise ValueError("Provide only action, notes, expectedVersion and requestId.")
    if not isinstance(body["action"], str) or body["action"] not in STATUSES:
        raise ValueError("Choose a listed review decision.")
    if not isinstance(body["notes"], str) or "\x00" in body["notes"] or not 1 <= len(body["notes"].strip()) <= 2000:
        raise ValueError("Decision notes are required and must be 2000 characters or fewer.")
    if type(body["expectedVersion"]) is not int or not 0 <= body["expectedVersion"] < 2_147_483_647:
        raise ValueError("A valid expected review version is required.")
    try:
        if not isinstance(body["requestId"], str):
            raise ValueError
        body["requestId"] = str(UUID(body["requestId"]))
    except ValueError:
        raise ValueError("A valid review request ID is required.") from None
    body["notes"] = body["notes"].strip()
    return body


@never_cache
@require_access("enrolment")
@csrf_exempt
def application_reviews(request, application_id):
    if request.method not in ("GET", "POST"):
        return JsonResponse({"error": "Method not allowed."}, status=405, headers={"Allow": "GET, POST"})
    if not 1 <= application_id <= 9_223_372_036_854_775_807:
        return JsonResponse({"error": "Application not found."}, status=404)
    if request.method == "POST":
        if not form.workplace_enabled():
            return JsonResponse({"error": "Review decisions are disabled in this environment."}, status=403)
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"error": "Missing X-Requested-With header."}, status=403)
        try:
            body = _body(request)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        actor = getattr(request, "login_account", None)
        if not actor or not getattr(actor, "id", None):
            return JsonResponse({"error": UNAVAILABLE}, status=503)
    try:
        with transaction.atomic(using=form.CONN):
            with connections[form.CONN].cursor() as cursor:
                row = _application(cursor, application_id, "UPDATE" if request.method == "POST" else "SHARE")
                if row is None:
                    return JsonResponse({"error": "Application not found."}, status=404)
                if request.method == "GET":
                    return JsonResponse(_payload(cursor, row))
                if not row["ready"]:
                    raise DatabaseError("Review storage is not prepared")
                cursor.execute(
                    f"SELECT reviewer_id, new_status, reason, version FROM enrolment.{TABLE} "
                    "WHERE application_id = %s AND request_id = %s::uuid", [application_id, body["requestId"]],
                )
                previous_request = cursor.fetchone()
                if previous_request:
                    if previous_request != (actor.id, body["action"], body["notes"], body["expectedVersion"] + 1):
                        return JsonResponse({"error": "This request ID has already been used for a different decision."}, status=409)
                    return JsonResponse(_payload(cursor, row))
                if (row["review_version"] or 0) != body["expectedVersion"]:
                    return JsonResponse({"error": "Another reviewer has changed this application. Reload its review history before deciding."}, status=409)
                allowed = TRANSITIONS.get(row["review_status"], ())
                if row["assessment_status"] != form.PENDING_ASSESSMENT or row["eligible"] is not None or body["action"] not in allowed:
                    return JsonResponse({"error": "This application cannot make that review transition."}, status=409)
                # Confirm history agrees before appending; fail closed on corruption.
                _payload(cursor, row)
                version = body["expectedVersion"] + 1
                cursor.execute(
                    f"INSERT INTO enrolment.{TABLE} (application_id, reviewer_id, reviewer_name, previous_status, "
                    "new_status, reason, version, request_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::uuid)",
                    [application_id, actor.id, ((actor.display_name or "").strip() or actor.email)[:200], row["review_status"] or form.PENDING_ASSESSMENT,
                     body["action"], body["notes"], version, body["requestId"]],
                )
                cursor.execute(
                    f"UPDATE enrolment.{form.TABLE} SET review_status = %s, review_version = %s "
                    "WHERE id = %s AND COALESCE(review_version, 0) = %s RETURNING id",
                    [body["action"], version, application_id, body["expectedVersion"]],
                )
                if cursor.fetchone() is None:
                    raise DatabaseError("Review version changed during decision")
                row.update(review_status=body["action"], review_version=version)
                return JsonResponse(_payload(cursor, row), status=201)
    except DatabaseError:
        logger.exception("Could not read or record an eligibility review")
        return JsonResponse({"error": UNAVAILABLE}, status=503)
