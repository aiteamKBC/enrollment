"""Migrated-only authorization and persistence around the shared calculator."""
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from learner_api.models import LearnerProfile
from learner_api.review_progress_snapshot import build_progress_snapshot

from .migrated_reviews import EDITABLE_STATUSES, review_family
from .migrated_summary_binding import answer_version
from .migrated_templates import snapshot_assignment_valid
from .models import ImportedReviewInstance


PROGRESS_FAMILIES = frozenset({"PR", "PR_SKILLS_RADAR"})
CONFLICT_MESSAGE = "This review changed. Reopen it before calculating progress again. Your unsaved answers have been kept on screen."


class ProgressError(ValueError):
    def __init__(self, message, *, status=409, errors=None, code=None):
        super().__init__(message)
        self.status, self.errors, self.code = status, errors, code


def calculation_available(overlay, family, programme_key):
    return bool(
        overlay and family in PROGRESS_FAMILIES
        and overlay.status in EDITABLE_STATUSES
        and snapshot_assignment_valid(overlay, family, programme_key)
    )


@dataclass(frozen=True)
class ProgressContext:
    overlay_id: int
    learner_id: int
    source_review_id: int
    template_id: int
    family: str
    version: str


def resolve_context(owner, review_id):
    # Reuse the caseload, effective Aptem identity, source-completion and
    # association gates. This reader never calculates or initializes a review.
    from .views import _imported_review_definition, _owned_migrated_overlay

    definition = _imported_review_definition(owner, review_id)
    if not definition:
        raise ProgressError("Migrated review not found for this coach.", status=404)
    if not definition.get("migratedForm"):
        raise ProgressError("Initialize an eligible migrated review before calculating progress.")
    overlay = _owned_migrated_overlay(owner, definition)
    if not overlay:
        raise ProgressError("Migrated review association mismatch.")
    family = review_family((definition.get("historicalReview") or {}).get("type"))
    if family not in PROGRESS_FAMILIES:
        raise ProgressError("Progress calculation is only available for PR and PR_SKILLS_RADAR.")
    if not calculation_available(overlay, family, definition.get("migratedProgrammeKey", "")):
        raise ProgressError("Progress requires an initialized, editable migrated Progress Review with a valid template assignment.")
    return ProgressContext(
        overlay.pk, overlay.learner_id, overlay.source_review_id,
        overlay.migrated_template_id, family, answer_version(overlay),
    )


def require_version(expected, actual):
    if not isinstance(expected, str) or not expected or expected != actual:
        raise ProgressError(CONFLICT_MESSAGE, code="progress_conflict")


def calculate_snapshot(context, owner):
    from .views import fetch_source_schedule_rows, resolve_review_anchor_date, REVIEW_ANCHOR_MISSING_START

    learner = LearnerProfile.objects.filter(pk=context.learner_id).first()
    if learner is None:
        raise ProgressError("This review's learner record could not be found.", status=404)
    commercial, enrolment = fetch_source_schedule_rows([learner])
    start, reason = resolve_review_anchor_date(context.learner_id, commercial, enrolment)
    if start is None:
        raise ProgressError(
            "This learner has no individual programme start date, so progress cannot be calculated.",
            errors={"learnerStartDate": [reason or REVIEW_ANCHOR_MISSING_START]},
        )
    source = commercial.get(context.learner_id) or enrolment.get(context.learner_id) or learner
    return build_progress_snapshot(
        source, learner, learner_start_date=start,
        calculated_at=timezone.now(), calculated_by=owner,
    )


def persist_snapshot(owner, review_id, context, snapshot):
    with transaction.atomic():
        # Lock before repeating the source/family/ownership gates. Submit,
        # signatures and summary writes already lock this same overlay row.
        locked = ImportedReviewInstance.objects.select_for_update().filter(pk=context.overlay_id).first()
        if not locked:
            raise ProgressError(CONFLICT_MESSAGE, code="progress_conflict")
        require_version(context.version, answer_version(locked))
        current = resolve_context(owner, review_id)
        if current != context:
            raise ProgressError(CONFLICT_MESSAGE, code="progress_conflict")
        locked.progress_snapshot = snapshot
        # Advance the existing overlay/answer version; never save a stale
        # model's answers, template, intelligence, signatures or documents.
        locked.save(update_fields=["progress_snapshot", "updated_at"])
        from .views import _imported_review_definition
        definition = _imported_review_definition(owner, review_id)
        if not definition:
            raise ProgressError("Migrated review is no longer available for this coach.", status=404)
        return definition

