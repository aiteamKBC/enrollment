"""Staff-owned ILR employment-status history stored in the existing draft JSON.

The wizard's learner-owned employment questions remain in
Wizard_Ilr_Learner_Details.  Official AgreemId/DateEmpStatApp values are a
separate, append-only staff record: changing one creates a new dated item and
never rewrites the item that applied to an earlier document.
"""
from datetime import date
import re

from learner_api.mappers import _s
from learner_api.models import Organisation


HISTORY_KEY = "ilrEmploymentStatusHistory"
STAFF_DETAIL_KEYS = frozenset({"agreementIdentifier", "dateStatusApplies"})
AGREEMENT_RE = re.compile(r"[A-Za-z0-9]{1,7}")
EMPLOYER_RE = re.compile(r"[0-9]{9}")


def strip_staff_fields(details):
    """Return learner details without values a learner is not allowed to own."""
    return {key: value for key, value in (details or {}).items() if key not in STAFF_DETAIL_KEYS}


def organisation_for(learner):
    organisation_id = getattr(learner, "organisation_id", None)
    return Organisation.objects.filter(pk=organisation_id).first() if organisation_id else None


def validate_staff_record(details, learner):
    """Build a status snapshot, or None when staff supplied no managed values."""
    status = _s((details or {}).get("employmentStatus"))
    agreement = _s((details or {}).get("agreementIdentifier"))
    applies = _s((details or {}).get("dateStatusApplies"))
    if not agreement and not applies:
        return None
    if not applies:
        raise ValueError("Date Status Applies is required for a staff-managed employment status record.")
    try:
        date.fromisoformat(applies)
    except ValueError:
        raise ValueError("Date Status Applies must be a valid date in YYYY-MM-DD format.")
    organisation = organisation_for(learner)
    paid = status.casefold() == "in paid employment"
    if paid and organisation is None:
        raise ValueError("Link the learner to the correct organisation before saving an employed status.")
    if paid and not agreement:
        agreement = _s(getattr(organisation, "apprenticeship_agreement_id", ""))
    if agreement and not AGREEMENT_RE.fullmatch(agreement):
        raise ValueError("Agreement Identifier must be 1-7 alphanumeric characters.")
    if paid and not agreement:
        raise ValueError("Agreement Identifier is required for an employed apprenticeship status.")

    employer_identifier = _s(getattr(organisation, "edrs_ern_number", ""))
    # 999999999 is accepted only when staff have deliberately stored it on the
    # organisation. We never manufacture the fallback for a missing employer.
    if employer_identifier and not EMPLOYER_RE.fullmatch(employer_identifier):
        raise ValueError("The learner's organisation EDRS/ERN must contain exactly 9 digits.")
    if paid and not employer_identifier:
        raise ValueError("The learner's organisation needs an EDRS/ERN before saving an employed status.")
    return {
        "employmentStatus": status,
        "dateStatusApplies": applies,
        "agreementIdentifier": agreement,
        "organisationId": getattr(organisation, "pk", None),
        "employerIdentifier": employer_identifier,
    }


def append_record(history, record):
    """Append idempotently; an existing effective date is immutable."""
    clean = [item for item in (history or []) if isinstance(item, dict)]
    if record is None:
        return clean
    same_date = [item for item in clean if _s(item.get("dateStatusApplies")) == record["dateStatusApplies"]]
    if same_date:
        if same_date[-1] == record:
            return clean
        raise ValueError(
            "An employment status record already exists for this date. Use a new effective date to preserve history."
        )
    return [*clean, record]


def staff_record_for_write(details, learner, history):
    """Reuse an unchanged historical snapshot without re-resolving its employer."""
    applies = _s((details or {}).get("dateStatusApplies"))
    agreement = _s((details or {}).get("agreementIdentifier"))
    status = _s((details or {}).get("employmentStatus"))
    for item in history or []:
        if (isinstance(item, dict)
                and _s(item.get("dateStatusApplies")) == applies
                and _s(item.get("agreementIdentifier")) == agreement
                and _s(item.get("employmentStatus")) == status):
            return item
    return validate_staff_record(details, learner)


def effective_record(history, on_date=None):
    """Latest record applying on a date; future records do not leak early."""
    target = on_date or date.today()
    candidates = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        try:
            applies = date.fromisoformat(_s(item.get("dateStatusApplies")))
        except ValueError:
            continue
        if applies <= target:
            candidates.append((applies, item))
    return max(candidates, key=lambda pair: pair[0])[1] if candidates else {}


def apply_effective(details, history, on_date=None):
    current = effective_record(history, on_date)
    if not current:
        return details
    return {
        **(details or {}),
        "employmentStatus": _s(current.get("employmentStatus")) or _s((details or {}).get("employmentStatus")),
        "dateStatusApplies": _s(current.get("dateStatusApplies")),
        "agreementIdentifier": _s(current.get("agreementIdentifier")),
        "edrsErn": _s(current.get("employerIdentifier")),
    }
