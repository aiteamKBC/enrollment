"""LMS-local completion rules for Aptem continuation reviews.

No operation in this module writes Aptem reviews, historical signature caches,
or native curriculum review instances.
"""
import base64
import binascii
import hashlib
import re
from io import BytesIO
from PIL import Image as PillowImage, UnidentifiedImageError

from django.db import transaction
from django.utils import timezone

from .migrated_reviews import EDITABLE_STATUSES, SCHEDULED_WORK_STATUSES, _visible_fields, validate_answers
from .migrated_template_sync import active_answers, preserve_inactive_answers
from .models import ImportedReviewInstance, MigratedReviewDocument, MigratedReviewSignature
from .migrated_signature_rules import ROLES, requirements_for_definition, requirements_for_family


IMAGE_PATTERN = re.compile(r"^data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$", re.I)


def required_roles(overlay):
    rules = overlay.signature_requirements or {}
    if not rules:
        family = (overlay.template_snapshot.get("templateSource") or {}).get("family") or overlay.migrated_template.review_family
        rules = requirements_for_family(family)
    return tuple(role for role in ROLES if rules.get(role) is True)


def is_personal_support_form(overlay):
    family = (overlay.template_snapshot.get("templateSource") or {}).get("family")
    return (family or getattr(getattr(overlay, "migrated_template", None), "review_family", None)) == "PSP"


def complete_personal_support_if_ready(overlay):
    """Called only after an authorized, version-checked save under row locks.

    Partial drafts remain editable. A full save freezes the selected signature
    requirements and completes immediately only when none are required. Neither
    path needs a booking, Graph call or hours allocation or rewrites the source.
    """
    if not is_personal_support_form(overlay) or overlay.status not in EDITABLE_STATUSES:
        return False
    answers = active_answers(overlay.template_snapshot, overlay.answers)
    validate_answers(overlay.template_snapshot, answers)
    if any(field.get("mandatory") and field.get("aptemType") != 11
           and (answers.get(field["key"]) is None or not str(answers.get(field["key"], "")).strip())
           for field in _visible_fields(overlay.template_snapshot, answers)):
        return False
    overlay.signature_requirements = requirements_for_definition(overlay.template_snapshot, "PSP")
    needs_signatures = bool(required_roles(overlay))
    overlay.status = (ImportedReviewInstance.STATUS_AWAITING_SIGNATURE if needs_signatures
                      else ImportedReviewInstance.STATUS_COMPLETED)
    overlay.completed_at = None if needs_signatures else timezone.now()
    overlay.save(update_fields=["signature_requirements", "status", "completed_at", "updated_at"])
    from .migrated_completion_views import _mirror_status
    _mirror_status(overlay)
    return True


def signature_states(overlay):
    rows = {row.role: row for row in overlay.migrated_signatures.all()}
    required = set(required_roles(overlay))
    return {role: {
        "required": role in required,
        "signed": role in rows,
        "signedBy": rows[role].signer_email if role in rows else None,
        "signedName": rows[role].signer_name if role in rows else None,
        "signedAt": rows[role].signed_at.isoformat() if role in rows else None,
        "signature": rows[role].signature if role in rows else None,
    } for role in ROLES}


def submit(overlay, answers):
    if is_personal_support_form(overlay):
        if overlay.status not in EDITABLE_STATUSES:
            raise ValueError("This Personal Support Plan is read-only.")
        validate_answers(overlay.template_snapshot, answers, completing=True)
        overlay.answers = preserve_inactive_answers(overlay.template_snapshot, overlay.answers or {}, answers)
        # Use the same saved signature policy as the normal answer-save endpoint.
        with transaction.atomic():
            if not complete_personal_support_if_ready(overlay):
                raise ValueError("Required review fields are unanswered.")
            overlay.save(update_fields=["answers", "updated_at"])
        return
    if overlay.status not in SCHEDULED_WORK_STATUSES:
        raise ValueError("Only a scheduled or in-progress migrated review can be submitted.")
    validate_answers(overlay.template_snapshot, answers, completing=True)
    family = (overlay.template_snapshot.get("templateSource") or {}).get("family") or overlay.migrated_template.review_family
    overlay.signature_requirements = requirements_for_definition(overlay.template_snapshot, family)
    overlay.answers = preserve_inactive_answers(overlay.template_snapshot, overlay.answers or {}, answers)
    overlay.status = ImportedReviewInstance.STATUS_AWAITING_SIGNATURE
    # Completion and any configured hours allocation must roll back together if
    # submission fails, including endpoints which catch validation errors.
    if required_roles(overlay):
        overlay.save(update_fields=["answers", "signature_requirements", "status", "updated_at"])
    else:
        with transaction.atomic():
            overlay.save(update_fields=["answers", "signature_requirements", "status", "updated_at"])
            complete(overlay)


def validate_signature_image(signature):
    if not isinstance(signature, str) or len(signature) > 500_000:
        raise ValueError("A PNG, JPEG or WebP signature image is required.")
    match = IMAGE_PATTERN.fullmatch(signature)
    if not match:
        raise ValueError("A PNG, JPEG or WebP signature image is required.")
    try:
        decoded = base64.b64decode(match.group(1), validate=True)
    except binascii.Error:
        raise ValueError("The signature image is invalid.") from None
    if not decoded or len(decoded) > 350_000:
        raise ValueError("The signature image is empty or too large.")
    try:
        with PillowImage.open(BytesIO(decoded)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or image.width > 4000 or image.height > 4000:
                raise ValueError("The signature image format or dimensions are invalid.")
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise ValueError("The signature image is invalid.") from None
    return signature


def sign(overlay, role, *, account, signature):
    if overlay.status != ImportedReviewInstance.STATUS_AWAITING_SIGNATURE:
        raise ValueError("Only a submitted migrated review can be signed.")
    if role not in required_roles(overlay):
        raise ValueError("This signature role is not required for this review.")
    if overlay.migrated_signatures.filter(role=role).exists():
        raise ValueError("This role has already signed the review.")
    signature = validate_signature_image(signature)
    name = str(account.display_name or "").strip()
    if not name:
        raise ValueError("The signed-in account needs a display name before signing.")
    return MigratedReviewSignature.objects.create(
        overlay=overlay, role=role, signer_account_id=account.pk,
        signer_name=name, signer_email=account.email or "",
        signature=signature, signed_at=timezone.now(),
    )


def complete(overlay):
    if overlay.status != ImportedReviewInstance.STATUS_AWAITING_SIGNATURE:
        raise ValueError("Only a review awaiting signatures can be completed.")
    validate_answers(overlay.template_snapshot, active_answers(overlay.template_snapshot, overlay.answers), completing=True)
    signed = set(overlay.migrated_signatures.values_list("role", flat=True))
    if set(required_roles(overlay)) - signed:
        raise ValueError("All required signatures must be saved before completion.")
    from .migrated_otjh import hours_for
    # Reopening corrects the review document, not its original hours allocation.
    # A previously completed zero-credit review must not be backfilled either.
    previously_completed = (getattr(overlay, "meeting_intelligence", None) or {}).get("reopenCompletedAt")
    if not is_personal_support_form(overlay) and not previously_completed and hours_for(overlay.template_snapshot) > 0:
        return _complete_with_hours(overlay)
    overlay.status = ImportedReviewInstance.STATUS_COMPLETED
    overlay.completed_at = timezone.now()
    overlay.save(update_fields=["status", "completed_at", "updated_at"])


@transaction.atomic
def _complete_with_hours(overlay):
    overlay.status = ImportedReviewInstance.STATUS_COMPLETED
    overlay.completed_at = timezone.now()
    from .migrated_otjh import record_completion
    record_completion(overlay)
    overlay.save(update_fields=["status", "completed_at", "meeting_intelligence", "updated_at"])


def build_pdf(overlay, *, learner_name, learner_email, programme, scheduled_date,
              coach_name, source_family=None):
    """Validate migrated completion, then render saved state using Native styles."""
    from .migrated_review_pdf import pdf_family, render_migrated_pdf

    if overlay.status != ImportedReviewInstance.STATUS_COMPLETED or not overlay.completed_at:
        raise ValueError("The migrated review must be completed before PDF generation.")
    validate_answers(overlay.template_snapshot, active_answers(overlay.template_snapshot, overlay.answers), completing=True)
    signatures = {row.role: row for row in overlay.migrated_signatures.all()}
    rules = overlay.signature_requirements or requirements_for_family(pdf_family(overlay, source_family))
    required = {role for role in ROLES if rules.get(role) is True}
    if required - set(signatures):
        raise ValueError("Required signatures are missing.")
    states = {role: {
        "required": role in required, "signed": True,
        "signedName": row.signer_name, "signedAt": row.signed_at,
        "signature": row.signature,
    } for role, row in signatures.items() if role in ROLES}
    return render_migrated_pdf(
        overlay, states, learner_name=learner_name, learner_email=learner_email,
        programme=programme, scheduled_date=scheduled_date, coach_name=coach_name,
        source_family=source_family,
    )


def ensure_document(overlay, **context):
    """Caller holds the overlay row lock; failed generation leaves no document."""
    existing = MigratedReviewDocument.objects.filter(overlay=overlay).first()
    if existing:
        return existing
    content = build_pdf(overlay, **context)
    with transaction.atomic():
        return MigratedReviewDocument.objects.create(
            overlay=overlay, pdf_bytes=content, sha256=hashlib.sha256(content).hexdigest(),
        )
