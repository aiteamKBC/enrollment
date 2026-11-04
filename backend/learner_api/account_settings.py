"""Owner-scoped, read-only About You learner profile.

The canonical personal/contact/address values remain on
``enrolment.\"Created_users\"``. Pronouns remain on the existing ILR learner
details row. All fields are read-only here; authorised corrections before
finalisation belong in the ILR, identity changes in the verified staff workflow.
"""
from __future__ import annotations

import json
import logging
import re

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from enrolment_api.eligibility_form import COUNTRY_OPTIONS, TITLE_OPTIONS
from enrolment_api.models import WizardIlrLearnerDetails
from enrolment_api.postcode_lookup import formatted, is_valid
from login.permissions import learner_self_only

from .models import EnrolmentUser

logger = logging.getLogger(__name__)
PHONE_ALLOWED = re.compile(r"^\+?[0-9 ()\-]+$")


def _text(value, limit):
    if not isinstance(value, str):
        raise ValueError("Enter text.")
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValueError(f"Keep this to {limit} characters or fewer.")
    return cleaned


def _kind(learner):
    return "commercial" if (learner.learner_type or "").strip().casefold() == "commercial" else "apprenticeship"


def _pronouns(learner):
    row = WizardIlrLearnerDetails.objects.filter(
        learner_kind=_kind(learner), learner_id=learner.pk
    ).only("pronouns").first()
    return (row.pronouns or "") if row else ""


def _payload(request, learner):
    name = (learner.username or "").strip()
    return {
        "learnerId": learner.pk,
        "learnerKind": _kind(learner),
        "profile": {
            "title": learner.title or "",
            "fullName": name,
            "pronouns": _pronouns(learner),
            # No canonical post-nominals column exists in the learner record.
            # Expose an honest empty read-only value instead of duplicating it.
            "postNominals": "",
            "dateOfBirth": str(learner.date_of_birth or ""),
        },
        "contact": {
            "emailAddress": learner.email or "",
            "mobileNumber": learner.phone_number or "",
            # Created_users has one telephone field. Do not invent a second
            # landline store or copy an employer telephone into this profile.
            "landlineNumber": "",
        },
        "address": {
            "addressLine1": learner.address_line_1 or learner.address or "",
            "addressLine2": learner.address_line_2 or "",
            "addressLine3": learner.address_line_3 or "",
            "addressLine4": learner.address_line_4 or "",
            "residentialPostcode": learner.current_postcode or "",
            "country": learner.country or "",
        },
        "editable": {
            "profile": [], "contact": [], "address": [],
        },
        "csrfToken": get_token(request),
    }


def _profile_values(values):
    unknown = set(values) - {"title", "pronouns"}
    if unknown:
        raise ValueError("That profile field cannot be changed here.")
    title = _text(values.get("title", ""), 30)
    if title not in TITLE_OPTIONS:
        raise ValueError("Choose a listed title.")
    pronouns = _text(values.get("pronouns", ""), 80)
    return title, pronouns


def _contact_values(values):
    if set(values) - {"mobileNumber"}:
        raise ValueError("That contact field cannot be changed here.")
    phone = _text(values.get("mobileNumber", ""), 40)
    digits = re.sub(r"\D", "", phone)
    if phone and (not PHONE_ALLOWED.fullmatch(phone) or not 7 <= len(digits) <= 15):
        raise ValueError("Enter a valid mobile number.")
    return phone


def _address_values(values):
    allowed = {
        "addressLine1": ("address_line_1", 200),
        "addressLine2": ("address_line_2", 200),
        "addressLine3": ("address_line_3", 100),
        "addressLine4": ("address_line_4", 100),
        "residentialPostcode": ("current_postcode", 12),
        "country": ("country", 100),
    }
    if set(values) - set(allowed):
        raise ValueError("That address field cannot be changed here.")
    cleaned = {attribute: _text(values.get(key, ""), limit) for key, (attribute, limit) in allowed.items()}
    if not cleaned["address_line_1"]:
        raise ValueError("Enter the first line of your residential address.")
    if not cleaned["country"]:
        raise ValueError("Choose your country.")
    if cleaned["country"] not in COUNTRY_OPTIONS:
        raise ValueError("Choose a listed country.")
    postcode = cleaned["current_postcode"]
    if not postcode:
        raise ValueError("Enter your residential postcode or postal code.")
    if cleaned["country"] == "United Kingdom":
        if not is_valid(postcode):
            raise ValueError("Enter a valid UK postcode.")
        cleaned["current_postcode"] = formatted(postcode)
    return cleaned


@csrf_protect
@require_http_methods(["GET", "PUT"])
@learner_self_only(kwarg="pk")
def learner_account_settings(request, pk):
    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return JsonResponse({"error": "Not found."}, status=404)
        if request.method == "GET":
            response = JsonResponse(_payload(request, learner))
            response["Cache-Control"] = "private, no-store"
            return response

        # About You is a view of the record, never a learner editing channel.
        # Corrections before Sign + Next belong in the ILR wizard itself.
        return JsonResponse({"error": "About You is read-only. Correct permitted details in your ILR before signing."}, status=403)

    except ValueError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except DatabaseError:
        logger.exception("Could not read or update account settings for learner %s", pk)
        action = "loaded" if request.method == "GET" else "saved"
        return JsonResponse({"error": f"Your account settings could not be {action}. Please try again."}, status=503)
    except Exception:
        logger.exception("Unexpected account settings error for learner %s", pk)
        return JsonResponse({"error": "Your account settings are temporarily unavailable. Please try again."}, status=500)
