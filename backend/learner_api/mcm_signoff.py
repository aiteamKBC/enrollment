"""Learner sign-off for Monthly Coaching Meetings.

MCMs use the Curriculum review-instance signature as their source of truth.
The monthly log is a separate reporting record, so after the instance is
signed we copy the same learner signature to the MCM's scheduled month.  The
copy is deliberately best-effort and idempotent: a failed log write must not
undo a signature that has already been accepted by the review engine.
"""

import json
import logging

from django.db import DatabaseError
from django.http import JsonResponse
from django.utils import timezone

from old_otjh import repository as old_repo, service as old

logger = logging.getLogger(__name__)


def _month_for(record):
    # The MCM occurrence owns the curriculum target month.  A later booking
    # date is the appointment date and must not move the monthly-log sign-off
    # into a different reporting month.
    value = getattr(record, "target_date", None) or getattr(record, "scheduled_date", None)
    return value.strftime("%Y-%m") if value is not None else ""


def _learner_context(request):
    """Only a learner context may create the learner monthly-log sign-off."""
    account = getattr(request, "login_account", None)
    role = str(getattr(account, "role", "") or "").lower()
    if role == "learner":
        return True
    # Staff/admin may use the explicit learner preview, but a normal coach
    # view must never silently sign a learner's monthly log.
    return request.GET.get("perspective") == "learner" if hasattr(request, "GET") else False


def _save_reusable_signature(learner, signature, name):
    """Keep the learner's latest confirmed signature for the next form."""
    try:
        old_repo.query(
            '''UPDATE enrolment."Created_users"
               SET "Learner_signature"=%s,
                   "Learner_signature_name"=%s,
                   "Learner_signature_saved_at"=now()
             WHERE id=%s''',
            [signature, name, learner["id"]],
        )
    except DatabaseError:
        # The signature on the MCM/monthly log is still valid when an older
        # deployment has not applied the reusable-signature columns yet.
        logger.warning("Reusable learner signature could not be saved.", exc_info=True)


def _sync_monthly_log(request, learner_id, record, signature, signed_name, *, apply_monthly_log=True):
    """Mirror the exact signature to the MCM month when the learner opted in."""
    month = _month_for(record)
    if not month:
        return {"status": "failed", "message": "The MCM has no scheduled month."}
    if not apply_monthly_log:
        return {"status": "skipped", "reason": "learner_choice", "month": month}
    if not _learner_context(request):
        return None

    try:
        learner = old.resolve_record(learner_id)
        from . import monthly_logs

        result = monthly_logs.mirror_mcm_learner_signature(learner, month, signature, signed_name)
        return {**(result or {}), "month": month}
    except Exception:
        logger.exception("MCM learner signature could not be copied to the monthly log.")
        return {
            "status": "failed",
            "month": month,
            "message": "The MCM was signed, but the monthly log could not be updated. Please try again.",
        }


def _legacy_sign(request, record, learner_id, signature, signed_name, *, apply_monthly_log=True):
    if record.status not in {record.STATUS_AWAITING_SIGNATURE, record.STATUS_COMPLETED}:
        return None, JsonResponse(
            {"error": "The coach must submit the review before the learner can sign it."},
            status=409,
        )
    responses = dict(record.review_responses or {})
    responses.update({
        "learner_signature": signature,
        "learner_signed_by": signed_name,
        "learner_signed_at": timezone.now().isoformat(),
    })
    record.review_responses = responses
    record.save(update_fields=["review_responses", "updated_at"])
    sync = _sync_monthly_log(request, learner_id, record, signature, signed_name, apply_monthly_log=apply_monthly_log)
    if _learner_context(request):
        try:
            learner = old.resolve_record(learner_id)
            _save_reusable_signature(learner, signature, signed_name)
        except Exception:
            logger.warning("Could not load learner for reusable MCM signature.", exc_info=True)
    return sync, None


def mcm_signoff_response(request, record, learner_id):
    """Handle POST /sign/ for an MCM calendar event."""
    from .calendar import _error, _serialize_event

    if request.method != "POST":
        return _error("Method not allowed.", 405)
    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return _error("Invalid JSON body.", 400)

    signature = str(payload.get("signature") or "").strip()
    if not signature.startswith("data:image/"):
        return _error("A valid learner signature is required.", 400)
    signed_name = str(payload.get("name") or getattr(record, "learner_name", "") or "Learner").strip()
    # Older clients did not send this choice, so preserve the original
    # mirroring behavior unless the new option is explicitly unchecked.
    apply_monthly_log = payload.get("applyMonthlyLogSignature", True) is not False
    if record.status not in {record.STATUS_AWAITING_SIGNATURE, record.STATUS_COMPLETED}:
        return _error("The coach must submit the review before the learner can sign it.", 409)

    if str(getattr(record, "review_instance_id", "") or "").strip():
        from curriculum_api import review_instances

        instance = review_instances.get_review_instance(record.review_instance_id)
        if not instance:
            return _error("Review instance not found.", 404)
        try:
            definition = review_instances.record_review_instance_signature(
                instance,
                "participant",
                signed_by=str(getattr(record, "learner_email", "") or ""),
                signed_name=signed_name,
                signature=signature,
                actor=str(getattr(record, "learner_email", "") or "") or "learner",
            )
        except ValueError as exc:
            return _error(str(exc), 409)
        record.refresh_from_db()
        sync = _sync_monthly_log(request, learner_id, record, signature, signed_name, apply_monthly_log=apply_monthly_log)
        if _learner_context(request):
            try:
                learner = old.resolve_record(learner_id)
                _save_reusable_signature(learner, signature, signed_name)
            except Exception:
                logger.warning("Could not load learner for reusable MCM signature.", exc_info=True)
        response = {"event": _serialize_event(record), "review": definition}
        if sync is not None:
            response["monthlyLogSync"] = sync
        return JsonResponse(response)

    sync, error = _legacy_sign(request, record, learner_id, signature, signed_name, apply_monthly_log=apply_monthly_log)
    if error is not None:
        return error
    response = {"event": _serialize_event(record)}
    if sync is not None:
        response["monthlyLogSync"] = sync
    return JsonResponse(response)
