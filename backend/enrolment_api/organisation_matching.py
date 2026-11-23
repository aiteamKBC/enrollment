"""Resolve the employing company from a verified eligibility application.

Call within the same enrolment transaction that creates the learner. The
representative is a company contact, not the learner's line manager.
"""
import re
from difflib import SequenceMatcher

from django.db import connections

from learner_api.models import Organisation


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _key(value):
    return re.sub(r"[^0-9a-z]+", " ", _clean(value).lower()).strip()


def _postcode(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def _postcode_area(value):
    """Outward UK postcode: same area is a review hint, never an auto-link."""
    compact = _postcode(value)
    return compact[:-3] if len(compact) > 3 else compact


def resolve_application_organisation(row):
    """Reuse one unambiguous company/office, or create it; never guess a match.

    Exact normalised name plus full postcode is the only automatic match.
    Similar names in the same postcode area create a separate profile flagged
    for staff review. Street and contact details never decide identity.
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
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", [_key(name), postcode])
    active = list(Organisation.objects.filter(merged_into_organisation_id__isnull=True))
    exact = [org for org in active if _key(org.name) == _key(name)
             and _postcode(org.post_code) == postcode]
    if len(exact) > 1:
        raise ValueError("Multiple exact organisation matches need staff review.")
    if exact:
        return exact[0]
    possible = [org.pk for org in active
                if _postcode_area(org.post_code) == _postcode_area(postcode)
                and SequenceMatcher(None, _key(org.name), _key(name)).ratio() >= 0.8]
    return Organisation.objects.create(
        name=name, post_code=postcode, country="United Kingdom",
        **submitted,
        contact_name=_clean(row.get("representative_name")),
        contact_email=_clean(row.get("representative_email")),
        potential_match_ids=possible,
    )
