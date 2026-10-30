"""The learner-record values the ILR Learner Details step shows read-only.

The step asks only what the record does not already hold (ethnicity, prior
address, employment…). Everything the record does hold — name, date of birth,
current postcode and address lines, phone, email — is displayed from
enrolment."Created_users" rather than retyped, so the ILR can never disagree
with the record the enrolment team maintains.

    GET /enrolment_api/ilr-learner-record/<kind>/<learner_id>/

Read-only. Same gate as the rest of the enrolment API: the learner themselves
or enrolment staff (see enrolment_api.auth).
"""
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from learner_api.models import CommercialUser, EnrolmentUser

from .auth import enrolment_login_required

KINDS = {"apprenticeship": EnrolmentUser, "commercial": CommercialUser}


def _s(value):
    return str(value or "").strip()


def learner_record(learner):
    """The record fields, as the step displays them."""
    parts = _s(learner.username).split()
    # The record holds one display name. Family name is its last word, matching
    # how the rest of the wizard splits it (WizardContext.makeInitialDraft).
    given, family = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (_s(learner.username), "")
    return {
        "familyName": family,
        "givenNames": given,
        "dateOfBirth": _s(learner.date_of_birth),
        "currentPostcode": _s(learner.current_postcode),
        "addressLine1": _s(learner.address_line_1) or _s(learner.address),
        "addressLine2": _s(learner.address_line_2),
        "addressLine3": _s(learner.address_line_3),
        "addressLine4": _s(learner.address_line_4),
        "telephone": _s(learner.phone_number),
        "email": _s(learner.email),
        "nationalInsuranceNumber": _s(learner.national_insurance_number),
        "legalSex": _s(learner.legal_sex),
    }


@enrolment_login_required
@require_GET
def ilr_learner_record(request, kind, learner_id):
    model = KINDS.get(kind)
    if model is None:
        return JsonResponse({"error": "Unknown learner kind."}, status=400)
    try:
        learner = model.objects.filter(pk=learner_id).first()
    except DatabaseError:
        return JsonResponse({"error": "The learner record could not be loaded."}, status=502)
    if learner is None:
        return JsonResponse({"error": "Learner not found."}, status=404)
    return JsonResponse(learner_record(learner))
