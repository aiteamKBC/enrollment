"""Adapt persisted Aptem continuation data to the shared PDF presentation.

No Native lifecycle dependency and no template, progress or intelligence lookup.
The caller supplies the existing persisted learner/calendar display context.
"""
from curriculum_api.review_pdf_presentation import (
    EXPORTABLE_REVIEW_TYPES, REVIEW_TYPE_MCM, REVIEW_TYPE_PROGRESS_REVIEW,
    render_review_pdf,
)

from .migrated_reviews import render_sections
from .migrated_template_sync import active_answers


FAMILIES = {"MCM", "PR", "PR_SKILLS_RADAR", "PSP"}
SIGNATURE_ORDER = ("advisor", "employer", "participant", "referrer")
PROVENANCE = "LMS continuation of imported Aptem review"


def pdf_family(overlay, source_family=None):
    """Frozen family first; legacy snapshots may use the persisted source type.

    Pre-family snapshots have only frozen signature rules to select the shell
    when no source type is supplied. Their own questions are always retained.
    Never dereference the mutable migrated_template relation here.
    """
    family = (overlay.template_snapshot.get("templateSource") or {}).get("family")
    if family in FAMILIES:
        return family
    if source_family in FAMILIES:
        return source_family
    return "PR" if (overlay.signature_requirements or {}).get("employer") else "MCM"


def review_pdf_data(overlay, signatures, *, learner_name, learner_email,
                    programme, scheduled_date, coach_name, source_family=None):
    """Normalize display values without mutating the signed review state."""
    snapshot = overlay.template_snapshot
    family = pdf_family(overlay, source_family)
    sections, _warnings = render_sections(snapshot, active_answers(snapshot, overlay.answers))
    # Array order is the frozen form order, including legacy definitions whose
    # numeric order metadata is stale. Native continues using its own ordering.
    for index, section in enumerate(sections):
        section["displayOrder"] = index
    definition = {
        "template": {
            "name": snapshot.get("name") or {
                "MCM": "Monthly Coaching Meeting", "PR": "Progress Review",
                "PR_SKILLS_RADAR": "Progress Review (+ Skills Radar)",
                "PSP": "Personal Support Plan",
            }[family],
            "reviewTypeCode": "aptem_psp" if family == "PSP" else REVIEW_TYPE_MCM if family == "MCM" else REVIEW_TYPE_PROGRESS_REVIEW,
            "reviewFamily": family,
        },
        "instance": {
            "status": overlay.status, "targetDate": scheduled_date,
            "completedAt": overlay.completed_at,
        },
        "sections": sections,
        "progressSnapshot": getattr(overlay, "progress_snapshot", None),
        "signatures": signatures,
    }
    # Existing migrated state does not freeze employer/manager/programme-end
    # metadata. The shared Information block displays Not recorded for those
    # values; PR start date comes from its persisted progress snapshot.
    information = {"name": learner_name or learner_email, "programme": programme}
    return definition, information


def render_migrated_pdf(overlay, signatures, **context):
    definition, information = review_pdf_data(overlay, signatures, **context)
    signature_roles = SIGNATURE_ORDER
    if definition["template"]["reviewFamily"] == "PSP":
        signature_roles = tuple(role for role in SIGNATURE_ORDER
                                if (overlay.signature_requirements or {}).get(role) or signatures.get(role))
    return render_review_pdf(
        definition, information, signature_roles=signature_roles,
        # A migrated bound summary is a normal saved answer at its frozen
        # position. No extra AI block or cross-review RAG history is invented.
        mcm_summary_block=False, rag_history_block=False,
        provenance=PROVENANCE, keep_signature_blocks=bool(signature_roles),
        # Migrated signature validation already permits up to 4000 x 4000.
        # Reusing Native presentation must not reject an existing valid mark.
        signature_image_max_pixels=16_000_000,
    )


def stored_pdf_response(overlay, document):
    """One stored document and filename convention for every authorized viewer."""
    import re
    from django.http import HttpResponse

    code = REVIEW_TYPE_MCM if pdf_family(overlay) == "MCM" else REVIEW_TYPE_PROGRESS_REVIEW
    identifier = re.sub(r"[^A-Za-z0-9_-]", "", str(overlay.pk))
    prefix = "personal-support-plan" if pdf_family(overlay) == "PSP" else EXPORTABLE_REVIEW_TYPES[code]
    response = HttpResponse(bytes(document.pdf_bytes), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{prefix}-{identifier}.pdf"'
    response["Cache-Control"] = "private, no-store"
    return response
