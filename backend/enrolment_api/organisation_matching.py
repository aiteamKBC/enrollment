"""Resolve the employing company from a verified eligibility application.

Call within the same enrolment transaction that creates the learner. The
representative is a company contact, not the learner's line manager.
"""
import re

from django.db import connections

from learner_api.models import Organisation


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value):
    return _clean(value).casefold()


def _postcode(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def resolve_application_organisation(row):
    """Reuse one unambiguous company/office, or create it; never guess a match.

    Company name plus postcode narrows the candidates. Address values must not
    conflict, and multiple compatible candidates require staff resolution.
    The transaction advisory lock serialises two simultaneous applications for
    the same company/office while the first one is being created.
    """
    name = _clean(row.get("employer_name"))
    postcode = _postcode(row.get("employer_postcode"))
    street = _clean(row.get("employer_address_line1"))
    if not name or not postcode or not street:
        raise ValueError("Employer name, street address and postcode are required to link an organisation.")
    submitted = {
        "address_1": street,
        "address_2": _clean(row.get("employer_address_line2")),
        "city_town": _clean(row.get("employer_town_city")),
        "county": _clean(row.get("employer_county")),
    }
    with connections["enrolment"].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", [name.casefold(), postcode])
    candidates = [
        org for org in Organisation.objects.filter(name__iexact=name)
        if _key(org.name) == _key(name) and _postcode(org.post_code) == postcode
    ]
    compatible = [org for org in candidates if all(
        not _key(getattr(org, attr)) or not _key(value) or _key(getattr(org, attr)) == _key(value)
        for attr, value in submitted.items()
    )]
    if len(compatible) > 1 or (candidates and not compatible):
        raise ValueError("Employer organisation match needs staff review; no learner or invitation was created.")
    if compatible:
        org = compatible[0]
        # Existing organisation records are authoritative. In particular, one
        # applicant's representative must not replace a company contact. Fill
        # missing address/contact details only when the profile has none.
        updates = []
        for attr, value in (
            *submitted.items(),
            ("contact_name", _clean(row.get("representative_name"))),
            ("contact_email", _clean(row.get("representative_email"))),
        ):
            if value and not _clean(getattr(org, attr)):
                setattr(org, attr, value)
                updates.append(attr)
        if updates:
            org.save(update_fields=[*updates, "updated_at"])
        return org
    return Organisation.objects.create(
        name=name, post_code=postcode, country="United Kingdom",
        **submitted,
        contact_name=_clean(row.get("representative_name")),
        contact_email=_clean(row.get("representative_email")),
    )
