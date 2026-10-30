"""What the Extended ILR's Employer Details section can be prefilled with.

    GET /enrolment_api/extended-ilr/<kind>/<id>/employer-details/

Read-only. The wizard fills only the fields the learner has left empty, so
nothing typed or saved is overwritten; every value stays editable.

Sources, in the order agreed for the ILR:

* Organisation name, postcode, address, city — the organisation the learner's
  employer belongs to (enrolment."Organisations", via the employer's
  Employer_group_ids). With no single linked organisation, only the name is
  offered, from the learner's own Orgnization / Employer text; an address is
  never guessed.
* Line manager name, email, phone — the learner's assigned employer person
  (enrolment."Employers", via Created_users."Employer_id").

Every field is "" when the record has nothing for it.
"""
import logging

from django.db import DatabaseError
from django.http import JsonResponse

from learner_api.models import Employer, Organisation

from .auth import enrolment_login_required
from .extended_ilr import KINDS

logger = logging.getLogger(__name__)

FIELDS = (
    "organisationName", "postcode", "address", "city",
    "lineManagerName", "lineManagerEmail", "lineManagerPhone",
)


def _s(value):
    return "" if value is None else str(value).strip()


def _organisation_of(employer):
    """The employer's organisation, or None unless exactly one is linked.

    Employer_group_ids is multi-select; with several there is no telling which
    one the learner works at, and a wrong address is worse than an empty one.
    """
    ids = [i for i in (employer.employer_group_ids or []) if isinstance(i, int) or str(i).isdigit()]
    if len(ids) != 1:
        return None
    return Organisation.objects.filter(pk=int(ids[0])).first()


def employer_details_for(learner):
    details = dict.fromkeys(FIELDS, "")
    employer = Employer.objects.filter(pk=learner.employer_id).first() if learner.employer_id else None
    organisation = _organisation_of(employer) if employer is not None else None

    if organisation is not None:
        details["organisationName"] = _s(organisation.name)
        details["postcode"] = _s(organisation.post_code)
        details["address"] = ", ".join(p for p in (_s(organisation.address_1), _s(organisation.address_2)) if p)
        details["city"] = _s(organisation.city_town)
    if not details["organisationName"]:
        details["organisationName"] = _s(learner.organization) or _s(learner.employer)

    if employer is not None:
        details["lineManagerName"] = employer.full_name
        details["lineManagerEmail"] = _s(employer.email)
        details["lineManagerPhone"] = _s(employer.mobile)
    return details


@enrolment_login_required
def ilr_employer_details(request, kind, learner_id):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    model = KINDS.get(kind)
    if model is None:
        return JsonResponse({"error": f"Unknown learner kind '{kind}'."}, status=400)
    try:
        learner = model.objects.filter(pk=learner_id).first()
        if learner is None:
            return JsonResponse({"error": "Learner not found."}, status=404)
        return JsonResponse(employer_details_for(learner))
    except DatabaseError as exc:
        logger.warning("Could not resolve ILR employer details: %s", exc)
        return JsonResponse({"error": "Could not load employer details."}, status=502)
