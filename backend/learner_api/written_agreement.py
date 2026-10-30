"""The Written Agreement — issue, sign and retrieve.

The commercial agreement between the employer and the main provider: what the
provider will deliver, the End Point Assessment arrangements, the cost breakdown
against the funding band, and the process for queries and complaints.

Signed by the learner, the employer and the provider — the same three parties as
the Training Plan.

Content is assembled from what we already hold:
  * particulars  -> the learner's record, their employer, and their group's dates
  * delivery     -> the components on the learner's learning plan, listed as the
    off-the-job activities the provider will deliver, plus the reviews
  * costs        -> no source in the system, so the line items print with blank
    values for an officer to complete

Those values are SNAPSHOT at issue: once three parties have signed, editing the
learning plan must not rewrite what they agreed.

    GET  /learner_api/written-agreement/<pk>/          particulars + document
    POST /learner_api/written-agreement/<pk>/issue/    create/reissue
    POST /learner_api/written-agreement/<pk>/sign/     one party signs
"""
import json
import logging

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from enrolment_api.auth import employer_signing_party_error, enrolment_login_required

from .apprenticeship_agreement import (
    _employer_address,
    _group_dates,
)
from .learner_progression import advance_learner
from .mappers import _s
from .models import EnrolmentUser, WrittenAgreement
from .training_plan_document import _module_breakdown, _plan_modules

logger = logging.getLogger(__name__)

SIGNING_PARTIES = ("learner", "employer", "provider")
MAX_SIGNATURE_CHARS = 400_000

MAIN_PROVIDER = "Kent Business College"

# The costs table, in the order the form prints it. Values have no source in the
# system, so each line is issued blank for an officer to complete.
COST_ITEMS = (
    "Training that the provider will deliver to the Apprentice Off the Job",
    "Distance, online or blended learning relating to the off-the-job training",
    "English & maths training that the provider will deliver Off the Job (L3+ only)",
    "Assessment that the provider will deliver (including Progress Reviews)",
    "Registration, examination and certification",
    "Materials (non-capital items) used in the delivery of the apprenticeship",
    "Administration costs directly linked to training and assessment, including EPA",
    "Training delivered by the Subcontractor",
    "Assessment undertaken by the Subcontractor",
    "Support that the provider will provide to the Subcontractor",
    "Monitoring that the provider will undertake",
    "End Point Assessment",
    "Apprenticeship Certification",
    "External Quality Assurance",
    "Any other costs not fundable by Levy funds or DfE contribution",
)


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _iso(value):
    return value.isoformat() if value else ""


def derive_content(learner):
    """Everything the Written Agreement states, from the learner's records."""
    start, end, dates_from = _group_dates(learner)
    modules = _plan_modules(learner)
    breakdown = _module_breakdown([_s(m.get("moduleId")) for m in modules])

    # The delivery list: every component the provider will deliver off the job,
    # in the form's "Method: Title (n hrs)" shape.
    activities = []
    for module in modules:
        module_id = _s(module.get("moduleId"))
        for week in breakdown.get(module_id, []):
            for component in week["components"]:
                activities.append({
                    "method": component["method"],
                    "title": component["title"],
                    "hours": component["otjHours"],
                    "module": _s(module.get("moduleTitle")),
                    "week": week["weekTitle"],
                })

    total_hours = round(sum(float(m.get("hours") or 0) for m in modules), 2)

    particulars = {
        "apprenticeName": _s(learner.username),
        "jobTitle": "",
        # We hold no separate standard name/level, so the programme is what we
        # can honestly state; an officer completes the rest.
        "apprenticeshipTitle": _s(learner.programme),
        "apprenticeshipLevel": "",
        "fundingBandValue": "",
        "isStandard": True,
        "startDate": _iso(start),
        "plannedEndDate": _iso(end),
        "managerName": _s(learner.line_manager),
        "managerJobTitle": "",
        "employer": _s(learner.employer),
        "employerPostcode": "",
        "employerAddress": _employer_address(learner),
        "mainProvider": _s(learner.learning_provider) or MAIN_PROVIDER,
        "subcontracted": False,
    }

    delivery = {
        "offTheJobNote": (
            "Training that the provider will deliver to the Apprentice off the job. This must be "
            "a minimum of 20% of the apprentice's total paid working time, not including any "
            "English and maths delivery."
        ),
        "activities": activities,
        "totalOtjHours": total_hours,
        "englishMathsNote": (
            "English and maths requirements up to and including Level 2 are paid directly to the "
            "provider by the DfE. Level 3 or above will have associated costs to the employer."
        ),
    }

    epa = {
        "organisation": "",
        "postcode": "",
        "arrangements": "",
        "paymentArrangements": "",
        "externalQualityAssurance": "",
    }

    costs = {
        "items": [{"item": label, "price": None} for label in COST_ITEMS],
        "total": None,
        "fundingBandMaximum": None,
        "balanceDue": None,
    }

    contacts = {
        "provider": {
            "organisation": f"Provider - {particulars['mainProvider']}",
            "name": _s(learner.case_owner),
            "email": "",
            "phone": "",
        },
        "epao": {"organisation": "End Point Assessment Organisation", "name": "", "email": "", "phone": ""},
        "dfe": {
            "organisation": "Department for Education (DfE)",
            "website": "https://www.gov.uk/education/apprenticeships-traineeships-and-internships",
            "email": "helpdesk@manage-apprenticeships.service.gov.uk",
            "phone": "08000 150 600 - 8am to 8pm, Monday to Friday",
        },
    }

    return {
        "particulars": particulars,
        "delivery": delivery,
        "epa": epa,
        "costs": costs,
        "contacts": contacts,
        "datesFrom": dates_from,
        "moduleCount": len(modules),
    }


# ---------------------------------------------------------------------------
# Serialising
# ---------------------------------------------------------------------------
def _party_json(signature, name, position, at):
    return {
        "signed": bool(_s(signature)),
        "name": _s(name),
        "position": _s(position),
        "signedAt": at.isoformat() if at else None,
    }


def _document_json(document):
    if document is None:
        return None
    return {
        "id": str(document.id),
        "status": _s(document.status),
        "fullySigned": bool(document.fully_signed),
        "createdAt": document.created_at.isoformat() if document.created_at else None,
        "updatedAt": document.updated_at.isoformat() if document.updated_at else None,
        "particulars": document.particulars or {},
        "delivery": document.delivery or {},
        "epa": document.epa or {},
        "costs": document.costs or {},
        "contacts": document.contacts or {},
        "signatures": {
            "learner": _party_json(
                document.learner_signature,
                document.learner_signed_name,
                document.learner_position,
                document.learner_signed_at,
            ),
            "employer": _party_json(
                document.employer_signature,
                document.employer_signed_name,
                document.employer_position,
                document.employer_signed_at,
            ),
            "provider": _party_json(
                document.provider_signature,
                document.provider_signed_name,
                document.provider_position,
                document.provider_signed_at,
            ),
        },
        "marks": {
            "learner": _s(document.learner_signature),
            "employer": _s(document.employer_signature),
            "provider": _s(document.provider_signature),
        },
    }


def _active_document(learner_kind, learner_id, lock=False):
    """The learner's active written agreement. `lock=True` takes a row lock for
    the caller's transaction — only valid inside
    `transaction.atomic(using="enrolment")`."""
    qs = WrittenAgreement.objects.filter(
        learner_kind=learner_kind,
        learner_id=learner_id,
        status=WrittenAgreement.STATUS_ACTIVE,
    )
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def _learner_kind(learner):
    return _s(getattr(learner, "learner_type", "")) or "apprenticeship"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@enrolment_login_required
@csrf_exempt
def written_agreement(request, pk):
    """The learner's Written Agreement, plus the content a new one would carry."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        derived = derive_content(learner)
        document = _active_document(_learner_kind(learner), learner.pk)
    except DatabaseError as exc:
        logger.exception("written_agreement: build failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "learner": {
            "id": str(learner.id),
            "name": _s(learner.username),
            "programmeStatus": _s(learner.programme_status),
        },
        "particulars": derived["particulars"],
        "delivery": derived["delivery"],
        "epa": derived["epa"],
        "costs": derived["costs"],
        "contacts": derived["contacts"],
        "meta": {
            "datesFrom": derived["datesFrom"],
            "moduleCount": derived["moduleCount"],
            "activityCount": len(derived["delivery"]["activities"]),
        },
        "document": _document_json(document),
    })


@enrolment_login_required
@csrf_exempt
def issue_written_agreement(request, pk):
    """Issue the agreement, snapshotting the current content onto a new row."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        kind = _learner_kind(learner)
        derived = derive_content(learner)

        # `using="enrolment"` is required — a bare atomic() opens on `default`
        # and leaves this supersede+create unprotected. See BE-1.
        with transaction.atomic(using="enrolment"):
            existing = _active_document(kind, learner.pk, lock=True)
            if existing is not None:
                existing.status = WrittenAgreement.STATUS_SUPERSEDED
                existing.save(update_fields=["status", "updated_at"])

            document = WrittenAgreement.objects.create(
                learner_kind=kind,
                learner_id=learner.pk,
                particulars=derived["particulars"],
                delivery=derived["delivery"],
                epa=derived["epa"],
                costs=derived["costs"],
                contacts=derived["contacts"],
            )
    except DatabaseError as exc:
        logger.exception("issue_written_agreement: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({"document": _document_json(document)}, status=201)


@enrolment_login_required
@csrf_exempt
def sign_written_agreement(request, pk):
    """Record one party's signature. Empty signature withdraws that sign-off."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    party = _s(payload.get("party")).lower()
    if party not in SIGNING_PARTIES:
        allowed = "', '".join(SIGNING_PARTIES)
        return _error(f"party must be one of '{allowed}'.", 400)
    party_error = employer_signing_party_error(request, party)
    if party_error is not None:
        return party_error

    signature = _s(payload.get("signature"))
    name = _s(payload.get("name"))
    position = _s(payload.get("position"))
    if signature:
        if len(signature) > MAX_SIGNATURE_CHARS:
            return _error("That signature image is too large.", 400)
        if not signature.startswith("data:image/"):
            return _error("signature must be a PNG data URL.", 400)
        if not name:
            return _error("name is required when signing.", 400)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        # Lock the row for the read-modify-write so concurrent signers (learner,
        # employer, provider) can't overwrite each other's signature. See BE-4.
        with transaction.atomic(using="enrolment"):
            document = _active_document(_learner_kind(learner), learner.pk, lock=True)
            if document is None:
                return _error("No written agreement has been issued for this learner yet.", 404)

            now = timezone.now() if signature else None
            setattr(document, f"{party}_signature", signature)
            setattr(document, f"{party}_signed_name", name if signature else "")
            setattr(document, f"{party}_position", position if signature else "")
            setattr(document, f"{party}_signed_at", now)

            document.recalculate_signed()
            document.save()
        promoted = advance_learner(learner)
    except DatabaseError as exc:
        logger.exception("sign_written_agreement: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "document": _document_json(document),
        "programmeStatus": _s(learner.programme_status),
        "programmeStatusChangedTo": promoted,
    })
