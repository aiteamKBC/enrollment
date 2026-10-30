"""Optional, recoverable monthly-log sign-off for Aptem learner MCMs.

The MCM signature commits first. The reporting database is a separate write:
persist the learner's consent and exact reporting month before attempting it.
Retries use the immutable MCM signature, never a new image or client identity.
"""
from datetime import date
import logging

from django.db import transaction
from django.utils import timezone

from .migrated_reviews import review_family
from .models import ImportedReviewInstance

logger = logging.getLogger(__name__)
STATE_KEY = "monthlyLogSignature"
FINISHED = {"synced", "already-synced", "skipped"}
FAILURE_MESSAGE = "Your MCM signature is saved, but the Monthly Log could not be signed. Retry the Monthly Log below."


def saved_state(overlay):
    return (overlay.meeting_intelligence or {}).get(STATE_KEY) or {}


def public_result(state):
    return {key: state[key] for key in ("status", "month", "reason", "message") if key in state}


def signing_option(overlay, account, definition):
    """Source planned date owns the month, even after a cross-month booking."""
    if account.role != "learner":
        return None
    state = saved_state(overlay)
    if state:
        if state.get("enrolmentId") != account.subject_id or state.get("signerAccountId") != account.pk:
            return None
        return {"month": state["month"], "sync": public_result(state)}
    if review_family((definition.get("historicalReview") or {}).get("type")) != "MCM":
        return None
    value = (definition.get("instance") or {}).get("targetDate")
    try:
        month = date.fromisoformat(str(value)[:10]).strftime("%Y-%m")
    except (ValueError, TypeError):
        return None
    return {"month": month}


def record_request(overlay, account, month):
    """Called under the signing transaction, only after a successful signature."""
    signature = overlay.migrated_signatures.get(role="participant", signer_account_id=account.pk)
    state = {
        "status": "pending", "month": month, "enrolmentId": account.subject_id,
        "signerAccountId": account.pk, "signatureId": signature.pk,
        "requestedAt": timezone.now().isoformat(),
        "reviewRevision": (overlay.meeting_intelligence or {}).get("reviewRevision", 0),
    }
    overlay.meeting_intelligence = {**(overlay.meeting_intelligence or {}), STATE_KEY: state}
    overlay.save(update_fields=["meeting_intelligence"])


def saved_signature(overlay, state):
    """A reopened MCM retains consent to copy its original, archived mark."""
    signature = overlay.migrated_signatures.filter(
        pk=state.get("signatureId"), role="participant", signer_account_id=state.get("signerAccountId"),
    ).first()
    if signature:
        return signature
    from types import SimpleNamespace
    from .migrated_reopening import revision_number
    if not revision_number(overlay):
        return None
    archived = overlay.archived_revisions.filter(number=state.get("reviewRevision", 0) + 1).first()
    for row in (archived.snapshot.get("signatures", []) if archived else []):
        if (row.get("id") == state.get("signatureId") and row.get("role") == "participant"
                and row.get("signer_account_id") == state.get("signerAccountId")):
            return SimpleNamespace(**row)
    return None


def validate_retry(overlay, account, payload):
    from .migrated_reopening import revision_number
    state = saved_state(overlay)
    if (account.role != "learner" or state.get("enrolmentId") != account.subject_id
            or state.get("signerAccountId") != account.pk
            or (overlay.status not in {"awaiting-signature", "completed"}
                and not (overlay.status == "in-progress" and revision_number(overlay)))):
        raise ValueError("There is no Monthly Log signature request to retry for this account.")
    signature = saved_signature(overlay, state)
    if not signature or ("signature" in payload and payload["signature"] != signature.signature):
        raise ValueError("The saved MCM signature cannot be replaced when retrying the Monthly Log.")


def sync_request(overlay):
    """No review lock is held while writing the separate reporting database."""
    from learner_api import monthly_logs
    from old_otjh import service as old

    overlay_id = overlay.pk
    state = saved_state(overlay)
    if state.get("status") in FINISHED:
        return public_result(state)
    result = {"status": "failed", "month": state["month"], "message": FAILURE_MESSAGE}
    try:
        signature = saved_signature(overlay, state)
        if signature is None:
            raise ValueError("The saved MCM signature is unavailable.")
        learner = old.resolve_record(state["enrolmentId"])
        if str(learner["id"]) != str(state["enrolmentId"]):
            raise ValueError("Monthly Log learner association mismatch.")
        copied = monthly_logs.mirror_mcm_learner_signature(
            learner, state["month"], signature.signature, signature.signer_name,
        )
        if copied.get("status") in FINISHED:
            result = {**public_result(copied), "month": state["month"]}
    except Exception:
        # Avoid logging signature images, learner records or SQL parameters.
        logger.warning("Aptem MCM monthly-log signature copy failed; saved request can be retried.")
    try:
        with transaction.atomic():
            current = ImportedReviewInstance.objects.select_for_update().get(pk=overlay_id)
            latest = saved_state(current)
            # Concurrent retries may finish in either order. A late failure must
            # never hide an already-confirmed successful copy.
            if latest.get("status") in FINISHED:
                return public_result(latest)
            current.meeting_intelligence = {
                **(current.meeting_intelligence or {}),
                STATE_KEY: {**{key: value for key, value in latest.items() if key not in {"reason", "message"}}, **result},
            }
            current.save(update_fields=["meeting_intelligence"])
    except Exception:
        logger.warning("Aptem MCM monthly-log result could not be recorded; original request remains retryable.")
        return {"status": "failed", "month": state["month"], "message": FAILURE_MESSAGE}
    return result
