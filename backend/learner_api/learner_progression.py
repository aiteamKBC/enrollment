"""Automatic programme-status progression.

A learner moves through the programme on evidence, not on someone remembering to
change a dropdown:

    Onboarding      --> Delivery         once the 3 onboarding reviews are signed
                                         (see learning_plan.promote_to_delivery_if_ready)
    Delivery        --> Ready to enrol   once all 4 compliance documents are
                                         fully signed by every party they need
    Ready to enrol  --> Active           once the programme start date arrives

Each step only ever moves a learner FORWARD, and only out of the status directly
before it. A withdrawn signature, a reissued document or a late edit can never
drag someone backwards — see the note on demotion in the gap analysis (§9).

Two triggers, deliberately:

  * the signing endpoints call advance_learner() the moment the last signature
    lands, which covers the document-driven step; and
  * the date-driven step has no user action behind it — a start date simply
    arrives — so it is also swept by the `advance_learner_statuses` management
    command (run it daily) and re-checked whenever a learner's record is read.
"""
import logging
from datetime import date, datetime

from django.db import DatabaseError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .constants import DELIVERY_PROGRAMME_STATUS
from .mappers import _s
from .aptem_status import programme_status
from .models import (
    ApprenticeshipAgreement,
    EnrolmentUser,
    IlrDocument,
    TrainingPlanDocument,
    WrittenAgreement,
)

logger = logging.getLogger(__name__)

READY_TO_ENROL_STATUS = "Ready to enrol"
ACTIVE_STATUS = "Active"

# Commercial learners do not use the funded apprenticeship compliance path.
# Preparing data does not activate an account. Commercial learners also wait
# for a successfully sent invitation. Active/terminal states are never demoted.
COMMERCIAL_PRE_START_STATUSES = {
    "",
    "Fresh user",
    "Onboarding",
    DELIVERY_PROGRAMME_STATUS,
    READY_TO_ENROL_STATUS,
}

# The four compliance documents a learner must have fully signed before they are
# ready to enrol. Each is its own table with its own signatories; `Fully_signed`
# is only true once every party that document needs has signed.
COMPLIANCE_DOCUMENT_MODELS = (
    ("apprenticeshipAgreement", ApprenticeshipAgreement),
    ("ilr", IlrDocument),
    ("trainingPlan", TrainingPlanDocument),
    ("writtenAgreement", WrittenAgreement),
)


def _learner_kind(learner):
    return _s(getattr(learner, "learner_type", "")) or "apprenticeship"


def _has_assigned_learning_plan(learner):
    """Whether a learner has a saved plan assigned by the delivery team."""
    return bool(
        getattr(learner, "learning_plan", None)
        or getattr(learner, "training_plan", None)
    )


def _has_platform_invitation(learner):
    """A provisioned account or a failed email is not a sent invitation."""
    from login.models import Invitation

    return Invitation.objects.filter(
        account__subject_type="learner",
        account__subject_id=learner.pk,
        sent_at__isnull=False,
    ).exists()


def compliance_document_state(learner_kind, learner_id):
    """Per-document sign-off state: {name: True|False}.

    False covers both "issued but not fully signed" and "never issued" — from the
    progression's point of view those are the same thing: not done yet.
    """
    state = {}
    for name, model in COMPLIANCE_DOCUMENT_MODELS:
        try:
            document = model.objects.filter(
                learner_kind=learner_kind,
                learner_id=learner_id,
                status=model.STATUS_ACTIVE,
            ).first()
        except DatabaseError:
            logger.exception("compliance_document_state: %s lookup failed", name)
            state[name] = False
            continue
        state[name] = bool(document and document.fully_signed)
    return state


# Where each document keeps the learner's own signature.
LEARNER_SIGNATURE_FIELDS = {
    "apprenticeshipAgreement": "apprentice_signature",
    "ilr": "learner_signature",
    "trainingPlan": "apprentice_signature",
    "writtenAgreement": "learner_signature",
}


def learner_signed_compliance_documents(learner_kind, learner_id):
    """True once the learner has signed all four issued compliance documents.

    Their own signatures only — the employer's and provider's may still be
    outstanding. It is what opens their first learning session booking.
    """
    for name, model in COMPLIANCE_DOCUMENT_MODELS:
        try:
            document = model.objects.filter(
                learner_kind=learner_kind, learner_id=learner_id, status=model.STATUS_ACTIVE,
            ).only(LEARNER_SIGNATURE_FIELDS[name]).first()
        except DatabaseError:
            logger.exception("learner_signed_compliance_documents: %s lookup failed", name)
            return False
        if document is None or not _s(getattr(document, LEARNER_SIGNATURE_FIELDS[name], "")):
            return False
    return True


def compliance_documents_complete(learner_kind, learner_id):
    """True when all four compliance documents are fully signed."""
    return all(compliance_document_state(learner_kind, learner_id).values())


def _programme_start_date(learner):
    """The date the learner's programme starts.

    Keep an individual's recorded date; an assigned cohort supplies a missing
    date. Same resolution the compliance documents print, without saving it.
    """
    try:
        from .apprenticeship_agreement import _group_dates

        start, _end, _source = _group_dates(learner)
        return _as_date(start)
    except DatabaseError:
        logger.exception("_programme_start_date: lookup failed")
        return _as_date(learner.start_date)


def _as_date(value):
    """Return a calendar date for the legacy text or date-valued fields."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        parsed = parse_date(value)
        if parsed:
            return parsed
        parsed_datetime = parse_datetime(value)
        return parsed_datetime.date() if parsed_datetime else None
    return None


#: Human names for the four compliance documents, for telling a learner which
#: of them their start is still waiting on.
COMPLIANCE_DOCUMENT_LABELS = {
    "apprenticeshipAgreement": "Apprenticeship Agreement",
    "ilr": "Individual Learner Record",
    "trainingPlan": "Training Plan",
    "writtenAgreement": "Written Agreement",
}


def access_gate(learner):
    """Why this learner cannot start yet — the same conditions advance_learner
    checks, reported instead of applied.

    The waiting page used to assume there was only ever one answer ("your start
    date has not arrived"), so a learner held back by an unassigned plan was told
    to wait for a date that had already passed. Progression knows the real
    reason; this hands it over rather than making the page guess.

    Returns {blocked, reasons, startDate, outstandingDocuments}. `reasons` is
    ordered by what has to happen first, and empty when nothing is holding the
    learner back.
    """
    result = {"blocked": False, "reasons": [], "startDate": "", "outstandingDocuments": []}
    try:
        status = programme_status(learner)
        kind = _learner_kind(learner)
        commercial = kind.casefold() == "commercial"
        waiting_statuses = (
            COMMERCIAL_PRE_START_STATUSES if commercial
            else {"", "Fresh user", "Onboarding", DELIVERY_PROGRAMME_STATUS, READY_TO_ENROL_STATUS}
        )
        if status not in waiting_statuses or status == ACTIVE_STATUS:
            return result

        start = _programme_start_date(learner)
        result["startDate"] = start.isoformat() if start else ""

        # An apprenticeship learner's documents come before their start date:
        # they cannot be enrolled, let alone started, until all four are signed.
        if not commercial and status in {"", "Fresh user", "Onboarding", DELIVERY_PROGRAMME_STATUS}:
            state = compliance_document_state(kind, learner.pk)
            outstanding = [name for name, done in state.items() if not done]
            if outstanding:
                result["reasons"].append("documents")
                result["outstandingDocuments"] = [
                    COMPLIANCE_DOCUMENT_LABELS.get(name, name) for name in outstanding
                ]

        # Commercial delivery waits on the plan the delivery team assigns.
        if commercial and not _has_assigned_learning_plan(learner):
            result["reasons"].append("plan")

        if start is None:
            result["reasons"].append("start-date-missing")
        elif start > timezone.localdate():
            result["reasons"].append("start-date-future")

        if commercial and not _has_platform_invitation(learner):
            result["reasons"].append("invitation")

        result["blocked"] = bool(result["reasons"])
        return result
    except DatabaseError:
        logger.exception("access_gate: failed for learner %s", getattr(learner, "pk", "?"))
        return result


def advance_learner(learner):
    """Move a learner as far forward as their evidence allows.

    Runs the steps in order so one call can carry a learner from Delivery to
    Active when their documents are signed and the start date has already
    passed. Returns the new status when it changed, else None. Never raises: a
    failure here must not fail whatever action triggered it.
    """
    try:
        changed = None
        if programme_status(learner).casefold() == 'withdrawn':
            return None

        # A saved plan and dates prepare delivery; sending the platform
        # invitation is the explicit release step. Existing Active learners
        # remain active even if imported dates or plans are incomplete.
        if _learner_kind(learner).casefold() == "commercial":
            current = _s(learner.programme_status)
            if current in COMMERCIAL_PRE_START_STATUSES:
                start = _programme_start_date(learner)
                ready_to_start = (
                    _has_assigned_learning_plan(learner)
                    and start is not None
                    and start <= timezone.localdate()
                    and _has_platform_invitation(learner)
                )
                target = ACTIVE_STATUS if ready_to_start else DELIVERY_PROGRAMME_STATUS
                if current != target:
                    learner.programme_status = target
                    learner.save(update_fields=["programme_status"])
                    changed = target
                    if target == ACTIVE_STATUS:
                        # Only on the actual transition — becoming Active
                        # creates/refreshes the permanent learner profile.
                        # Re-syncing an already-active learner on every read
                        # (this ran unconditionally before) is the N+1 cost
                        # that made GET /learner_api/enrolment-users/ take
                        # ~2 minutes: a full atomic upsert per commercial
                        # learner, every single list read, whether or not
                        # anything changed.
                        from .active_users import sync_active_user

                        sync_active_user(learner)
                return changed
            return None

        # ---- Delivery -> Ready to enrol ----
        if _s(learner.programme_status) == DELIVERY_PROGRAMME_STATUS:
            if compliance_documents_complete(_learner_kind(learner), learner.pk):
                learner.programme_status = READY_TO_ENROL_STATUS
                learner.save(update_fields=["programme_status"])
                changed = READY_TO_ENROL_STATUS

        # ---- Ready to enrol -> Active ----
        if _s(learner.programme_status) == READY_TO_ENROL_STATUS:
            start = _programme_start_date(learner)
            if start and start <= timezone.localdate():
                learner.programme_status = ACTIVE_STATUS
                learner.save(update_fields=["programme_status"])
                # Becoming Active also creates/refreshes the permanent learner
                # profile used by the learning workspace, coach tools and plan.
                from .active_users import sync_active_user

                sync_active_user(learner)
                changed = ACTIVE_STATUS

        return changed
    except DatabaseError:
        logger.exception("advance_learner: failed for learner %s", getattr(learner, "pk", "?"))
        return None


def advance_learner_by_id(learner_id):
    """advance_learner() for a learner id, for callers that hold only the id."""
    try:
        learner = EnrolmentUser.all_learners.filter(pk=learner_id).first()
    except DatabaseError:
        logger.exception("advance_learner_by_id: lookup failed for %s", learner_id)
        return None
    return advance_learner(learner) if learner is not None else None
