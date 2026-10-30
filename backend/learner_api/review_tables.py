"""Project a review's answers into its per-review-type detail table.

enrolment."Enrolment_Reviews".Form_answers stays the working store: the form saves
one section at a time and the merge logic lives there. These detail tables give
each review type real columns to query and report on, written from that document
on every save.

One-directional on purpose — the document is the source of truth, so a projection
can always be rebuilt (see the backfill_review_details command) and the two can
never disagree about which is authoritative.
"""
import logging

from django.db import DatabaseError

from .mappers import _s
from .models import (
    EligibilityReviewDetail,
    HealthSafetyReviewDetail,
    RplReviewDetail,
    UlnPrivacyReviewDetail,
)

logger = logging.getLogger(__name__)

# (model, {model field: (section, answer key)}) per review type. Fields whose
# value is a collection are handled in _extra() below.
MAPPINGS = {
    "eligibility-review": (
        EligibilityReviewDetail,
        {
            "over16": ("ilr", "over16"),
            "within_contract_time": ("ilr", "withinContractTime"),
            "paye_scheme": ("ilr", "payeScheme"),
            "eligible_residency": ("extendedIlr", "eligibleResidency"),
            "identity_documents_seen": ("extendedIlr", "identityDocumentsSeen"),
            "eligibility_evidence": ("extendedIlr", "eligibilityEvidence"),
            "right_to_work_england": ("extendedIlr", "rightToWorkEngland"),
            "fifty_percent_england": ("extendedIlr", "fiftyPercentEngland"),
            "minimum_wage": ("extendedIlr", "minimumWage"),
            "holds_level2": ("fsJobRoleDiscussion", "holdsLevel2"),
            "level_matches_role": ("fsJobRoleDiscussion", "levelMatchesRole"),
            "productive_purpose": ("fsJobRoleDiscussion", "productivePurpose"),
            "ksb_exposure": ("fsJobRoleDiscussion", "ksbExposure"),
            "release_for_otj": ("fsJobRoleDiscussion", "releaseForOtj"),
            "embed_otj": ("fsJobRoleDiscussion", "embedOtj"),
            "warning_areas": ("fsJobRoleDiscussion", "warningAreas"),
            "comments": ("comments", "text"),
            "programme_status": ("programmeStatus", "status"),
        },
    ),
    "workspace": (
        RplReviewDetail,
        {
            "apprenticeship_appropriate": ("rplExperience", "apprenticeshipAppropriate"),
            "plan_aligns_standard": ("rplExperience", "planAlignsStandard"),
            "prior_education": ("rplExperience", "priorEducation"),
            "prior_work_experience": ("rplExperience", "priorWorkExperience"),
            "plan_needs_adjusting": ("rplExperience", "planNeedsAdjusting"),
            "uln": ("plr", "uln"),
            "reported_attainment": ("plr", "reportedAttainment"),
            "skills_radar_notes": ("skillsRadar", "notes"),
            "comments": ("comments", "text"),
        },
    ),
    "training-plan": (
        HealthSafetyReviewDetail,
        {
            "basic_arrangements": ("healthSafetyVetting", "basicArrangements"),
            "day_one_induction": ("healthSafetyVetting", "dayOneInduction"),
            "fire_safety": ("healthSafetyVetting", "fireSafety"),
            "first_aid": ("healthSafetyVetting", "firstAid"),
            "supervision": ("healthSafetyVetting", "supervision"),
            "ppe": ("healthSafetyVetting", "ppe"),
            "accident_recording": ("healthSafetyVetting", "accidentRecording"),
            "inform_changes": ("healthSafetyVetting", "informChanges"),
            "hs_policy": ("healthSafetyVetting", "hsPolicy"),
            "liability_insurance": ("healthSafetyVetting", "liabilityInsurance"),
        },
    ),
    "uln-privacy": (
        UlnPrivacyReviewDetail,
        {
            "privacy_notice_read": ("ulnPrivacyNotice", "noticeRead"),
            "learner_acknowledged": ("learnerAcknowledgement", "acknowledged"),
        },
    ),
}


def _section(answers, name):
    value = answers.get(name)
    return value if isinstance(value, dict) else {}


def _extra(review_type, answers):
    """Columns that don't come from a flat (section, key) lookup."""
    if review_type == "eligibility-review":
        fs = _section(answers, "functionalSkills")
        exemptions = fs.get("exemptions") if isinstance(fs.get("exemptions"), dict) else {}
        return {
            "initial_assessments": fs.get("initialAssessments") or [],
            "diagnostic_assessments": fs.get("diagnosticAssessments") or [],
            "exemption_english": _s(exemptions.get("English")),
            "exemption_maths": _s(exemptions.get("Maths")),
            "exemption_ict": _s(exemptions.get("ICT")),
            "fs_results": fs.get("results") or {},
        }
    if review_type == "workspace":
        prior = _section(answers, "priorLearning")
        plr = _section(answers, "plr")
        levels = plr.get("subjectLevels") if isinstance(plr.get("subjectLevels"), dict) else {}
        return {
            "prior_learning_items": prior.get("items") or [],
            "attainment_english": _s(levels.get("English")),
            "attainment_maths": _s(levels.get("Maths")),
            "attainment_ict": _s(levels.get("ICT")),
        }
    return {}


def sync_review_detail(review):
    """Write `review`'s answers into its detail table. Best-effort.

    Never raises: Form_answers is the source of truth and has already been saved
    by the caller, so a projection failure must not fail the learner's save. It is
    logged and can be repaired with `manage.py backfill_review_details`.
    """
    mapping = MAPPINGS.get(_s(review.review_type))
    if mapping is None:
        return
    model, fields = mapping
    answers = review.form_answers if isinstance(review.form_answers, dict) else {}

    defaults = {
        "event_key": review.event_key,
        "learner_id": review.learner_id,
        "learner_name": _s(review.learner_name),
        "completed": bool(review.form_completed),
    }
    for column, (section, key) in fields.items():
        defaults[column] = _s(_section(answers, section).get(key))
    defaults.update(_extra(_s(review.review_type), answers))

    try:
        model.objects.update_or_create(review_id=review.pk, defaults=defaults)
    except DatabaseError:
        logger.exception(
            "sync_review_detail: could not project %s into %s",
            review.event_key, model._meta.db_table,
        )
