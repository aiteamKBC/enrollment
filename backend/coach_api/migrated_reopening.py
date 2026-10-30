"""Versioned, local correction of Aptem continuation reviews."""
from copy import deepcopy
import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection

from .migrated_completion import ensure_document
from .models import MigratedReviewDocument, MigratedReviewRevision

REASONS = {"correction-required", "incorrect-answer", "signature-error", "other"}


def revision_number(overlay):
    return int((getattr(overlay, "meeting_intelligence", None) or {}).get("reviewRevision", 0))


def check_revision(overlay, payload):
    """Old clients remain compatible only until the first reopen."""
    current = revision_number(overlay)
    supplied = payload.get("reviewRevision")
    if supplied is None and current == 0:
        return
    if type(supplied) is not int or supplied != current:
        raise ValueError("This review was reopened. Reload it and read the current version before signing.")


def validate_reason(payload):
    reason, note = payload.get("reasonCode"), payload.get("note", "")
    if not isinstance(reason, str) or reason not in REASONS or not isinstance(note, str) or len(note) > 2000:
        raise ValueError("Choose a valid reopen reason and a note of at most 2000 characters.")
    note = note.strip()
    if reason == "other" and not note:
        raise ValueError('Add details when the reopen reason is "Other".')
    return reason, note


def archive(overlay, *, account, reason, note, pdf_context):
    """Caller locks the overlay. Any failure rolls back before active state changes."""
    if not connection.in_atomic_block:
        raise RuntimeError("Archiving a review requires its reopen transaction.")
    document = None
    if overlay.status == "completed":
        document = ensure_document(overlay, **pdf_context)
        if hashlib.sha256(bytes(document.pdf_bytes)).hexdigest() != document.sha256:
            raise ValueError("The stored PDF checksum could not be verified. The review was not reopened.")
    state = {
        key: deepcopy(getattr(overlay, key))
        for key in (
            "event_key", "owner_email", "learner_id", "source_review_id", "migrated_template_id",
            "template_snapshot", "signature_requirements", "answers", "progress_snapshot",
            "meeting_intelligence", "status", "completed_at", "created_at", "updated_at",
        )
    }
    state["signatures"] = list(overlay.migrated_signatures.values())
    state["documentCreatedAt"] = document.created_at if document else None
    state = json.loads(json.dumps(state, cls=DjangoJSONEncoder))
    return MigratedReviewRevision.objects.create(
        overlay=overlay, number=revision_number(overlay) + 1, snapshot=state,
        pdf_bytes=bytes(document.pdf_bytes) if document else None,
        pdf_sha256=document.sha256 if document else "",
        reason_code=reason, note=note, actor_account_id=account.pk, actor_email=account.email,
    )


def clear_active_signatures(overlay):
    """Only called after archiving, inside the same locked transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError("Clearing active signatures requires the reopen transaction.")
    overlay.migrated_signatures.all().delete()
    MigratedReviewDocument.objects.filter(overlay=overlay).delete()


def history(overlay):
    if not revision_number(overlay):
        return []
    return [{
        "number": row["number"], "reopenedAt": row["created_at"].isoformat(),
        "reasonCode": row["reason_code"], "note": row["note"], "reopenedBy": row["actor_email"],
        "pdfAvailable": bool(row["pdf_sha256"]),
    } for row in overlay.archived_revisions.order_by("-number").values(
        "number", "created_at", "reason_code", "note", "actor_email", "pdf_sha256",
    )]
