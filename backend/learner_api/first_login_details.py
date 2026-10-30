"""The two screens a new apprentice completes on their first sign-in.

Before the enrolment wizard opens, a newly created apprenticeship learner is
asked for the details the enrolment team does not usually hold yet -- their home
address, title, date of birth and mobile -- and for an electronic signature they
agree to use on this platform. Both are saved on their own row in
enrolment."Created_users", and finishing moves them from 'Fresh user' to
'Onboarding', which is what opens the enrolment wizard for them.

Who is asked
------------
An apprenticeship learner whose programme status is still 'Fresh user' (the
status every account is created with -- see DEFAULT_PROGRAMME_STATUS) and who
has not completed these screens before. Commercial learners, and learners
already further along, are never sent here: the status check keeps an existing
learner midway through their programme from being stopped at sign-in.

Where it is stored
------------------
* address, title, DOB, mobile -> the columns the staff create form already
  writes (Title, Country, Current_postcode, Current_address_line_1..4,
  Date_of_birth, Phone_number), so the ILR and every other reader picks them up
  unchanged;
* the signature -> the learner's reusable signature ("Learner_signature",
  "_name", "_saved_at"; see sql/2026-09-07_add_signature_to_created_users.sql),
  which later documents offer back. A signature stored on a signed document is
  never read from here, so saving this rewrites no signed record;
* completion -> "First_login_details_completed_at"
  (sql/2026-09-27_first_login_details_on_created_users.sql).

Those last columns are deliberately not mapped on EnrolmentUser: a mapped column
missing from the database breaks every query on the table, sign-in included
(see the note in models.py). They are probed and used through raw SQL instead,
so a database without them simply never asks anybody.

    GET  /learner_api/first-login-details/<pk>/   whether it is required + prefill
    POST /learner_api/first-login-details/<pk>/   save both screens, then Onboarding
"""
import json
import logging
import re
from datetime import date

from django.db import DatabaseError, connections, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from login.permissions import learner_self_only

from .aptem_status import programme_status
from .constants import DEFAULT_PROGRAMME_STATUS
from .mappers import _s
from .models import EnrolmentUser

logger = logging.getLogger(__name__)

CONN = "enrolment"
COMPLETED_COLUMN = "First_login_details_completed_at"
SIGNATURE_COLUMN = "Learner_signature"
SIGNATURE_NAME_COLUMN = "Learner_signature_name"
SIGNATURE_AT_COLUMN = "Learner_signature_saved_at"

ONBOARDING_STATUS = "Onboarding"

#: Same cap as every other signature column in the schema: a data URL, not a file.
MAX_SIGNATURE_CHARS = 400_000
SIGNATURE_PREFIX = "data:image/png;base64,"

UNITED_KINGDOM = "United Kingdom"
# Outward code, optional space, inward code. Loose on purpose: it catches a
# mistyped postcode without claiming the postcode exists.
UK_POSTCODE = re.compile(r"^([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})$")
PHONE_ALLOWED = re.compile(r"^\+?[0-9 ()\-]+$")

#: payload key -> (model attribute, max length). All plain text columns.
TEXT_FIELDS = {
    "title": ("title", 30),
    "country": ("country", 100),
    "postcode": ("current_postcode", 10),
    "addressLine1": ("address_line_1", 200),
    "addressLine2": ("address_line_2", 200),
    "townCity": ("address_line_3", 100),
    "county": ("address_line_4", 100),
    "phone": ("phone_number", 30),
}


def _error(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


def _learner(pk):
    return EnrolmentUser.all_learners.filter(pk=pk).first()


def _is_apprenticeship(learner):
    return (_s(getattr(learner, "learner_type", "")) or "apprenticeship").casefold() == "apprenticeship"


def _is_fresh(learner):
    # An unset status *is* 'Fresh user' -- the learner summary reports it that
    # way, and the create form never stamps anything else.
    status = programme_status(learner) or DEFAULT_PROGRAMME_STATUS
    return status.strip().casefold() == DEFAULT_PROGRAMME_STATUS.casefold()


def _completion(cur, pk):
    """(column available, completed at) for this learner.

    A missing column reads as "not available": the SQL script may not have been
    applied yet, and without somewhere to record completion the learner would be
    asked again at every sign-in. ``transaction.atomic`` supplies a savepoint,
    so a failed probe cannot abort a surrounding transaction.
    """
    try:
        with transaction.atomic(using=CONN):
            cur.execute(
                f'select "{COMPLETED_COLUMN}" from enrolment."Created_users" where id = %s',
                [pk],
            )
            row = cur.fetchone()
    except DatabaseError:
        logger.warning("First-login details column is not available yet.")
        return False, None
    return True, (row[0] if row else None)


def _has_saved_signature(cur, pk):
    try:
        with transaction.atomic(using=CONN):
            cur.execute(
                f'select coalesce("{SIGNATURE_COLUMN}", \'\') <> \'\' '
                f'from enrolment."Created_users" where id = %s',
                [pk],
            )
            row = cur.fetchone()
    except DatabaseError:
        logger.warning("Saved learner signature columns are not available yet.")
        return False
    return bool(row and row[0])


def _details(learner):
    return {
        "title": _s(learner.title),
        "dateOfBirth": _s(learner.date_of_birth),
        "phone": _s(learner.phone_number),
        "country": _s(learner.country) or UNITED_KINGDOM,
        "postcode": _s(learner.current_postcode),
        "addressLine1": _s(learner.address_line_1),
        "addressLine2": _s(learner.address_line_2),
        "townCity": _s(learner.address_line_3),
        "county": _s(learner.address_line_4),
    }


def _state(request, learner, cur):
    available, completed_at = _completion(cur, learner.pk)
    required = (
        available
        and completed_at is None
        and _is_apprenticeship(learner)
        and _is_fresh(learner)
    )
    return {
        "required": required,
        "completedAt": completed_at.isoformat() if completed_at else None,
        "signatoryName": _s(learner.username),
        "details": _details(learner),
        "hasSavedSignature": _has_saved_signature(cur, learner.pk),
        "programmeStatus": programme_status(learner) or DEFAULT_PROGRAMME_STATUS,
        "csrfToken": get_token(request),
    }


def _clean(payload):
    """Validated column values from the POST body, or (None, field errors)."""
    errors = {}
    values = {}
    for key, (attr, limit) in TEXT_FIELDS.items():
        raw = payload.get(key)
        if raw is not None and not isinstance(raw, str):
            errors[key] = "Enter text."
            continue
        text = " ".join((raw or "").split())
        if len(text) > limit:
            errors[key] = f"Keep this to {limit} characters or fewer."
        values[attr] = text

    if not values["title"]:
        errors["title"] = "Choose your title."
    if not values["country"]:
        errors["country"] = "Choose your country."
    if not values["address_line_1"]:
        errors["addressLine1"] = "Enter the first line of your address."
    if not values["address_line_3"]:
        errors["townCity"] = "Enter your town or city."

    postcode = values["current_postcode"].upper()
    if values["country"] == UNITED_KINGDOM:
        match = UK_POSTCODE.match(postcode)
        if not match:
            errors["postcode"] = "Enter a valid UK postcode, for example CT1 1AA."
        else:
            postcode = f"{match.group(1)} {match.group(2)}"
    values["current_postcode"] = postcode

    phone = values["phone_number"]
    if not phone:
        errors["phone"] = "Enter your mobile number."
    elif not PHONE_ALLOWED.match(phone) or not 7 <= sum(c.isdigit() for c in phone) <= 15:
        errors["phone"] = "Enter a valid mobile number."

    raw_dob = payload.get("dateOfBirth")
    dob = None
    try:
        dob = date.fromisoformat(raw_dob) if isinstance(raw_dob, str) and raw_dob else None
    except ValueError:
        dob = None
    if dob is None:
        errors["dateOfBirth"] = "Enter your date of birth."
    elif not date(1900, 1, 1) <= dob < timezone.localdate():
        errors["dateOfBirth"] = "Enter a date of birth in the past."
    else:
        values["date_of_birth"] = dob.isoformat()

    signature = payload.get("signature")
    if (
        not isinstance(signature, str)
        or not signature.startswith(SIGNATURE_PREFIX)
        or len(signature) <= len(SIGNATURE_PREFIX)
    ):
        errors["signature"] = "Your signature is required."
    elif len(signature) > MAX_SIGNATURE_CHARS:
        errors["signature"] = "That signature is too large. Please clear it and sign again."

    if errors:
        return None, None, errors
    return values, signature, {}


@csrf_protect
@require_http_methods(["GET", "POST"])
@learner_self_only(kwarg="pk")
def first_login_details(request, pk):
    try:
        learner = _learner(pk)
        if learner is None:
            return _error("Not found.", 404)
        cur = connections[CONN].cursor()
        if request.method == "GET":
            return JsonResponse(_state(request, learner, cur))
    except DatabaseError:
        logger.exception("Could not read first-login details for learner %s.", pk)
        return _error("Your details could not be loaded. Please try again.", 503)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _error("Send your details as JSON.")
    if not isinstance(payload, dict):
        return _error("Send your details as JSON.")

    values, signature, errors = _clean(payload)
    if errors:
        return _error("Please check the highlighted answers.", 400, fields=errors)

    try:
        with transaction.atomic(using=CONN):
            # Locked, and every precondition re-read under the lock, so a double
            # submit or a staff status change mid-request cannot apply twice or
            # promote a learner who is no longer at 'Fresh user'.
            learner = EnrolmentUser.all_learners.select_for_update().filter(pk=pk).first()
            if learner is None:
                return _error("Not found.", 404)
            available, completed_at = _completion(cur, pk)
            if not available:
                return _error("This step is not available yet. Please try again later.", 503)
            if completed_at is not None:
                # Already done (a retried submit): report success, rewrite nothing.
                return JsonResponse({**_state(request, learner, cur), "alreadyCompleted": True})
            if not _is_apprenticeship(learner) or not _is_fresh(learner):
                return _error("These details are not needed for your account.", 409, code="not_required")

            for attr, value in values.items():
                setattr(learner, attr, value)
            learner.programme_status = ONBOARDING_STATUS
            learner.save(update_fields=[*values.keys(), "programme_status"])

            cur.execute(
                f"""
                update enrolment."Created_users"
                   set "{SIGNATURE_COLUMN}" = %s,
                       "{SIGNATURE_NAME_COLUMN}" = %s,
                       "{SIGNATURE_AT_COLUMN}" = now(),
                       "{COMPLETED_COLUMN}" = now()
                 where id = %s
                """,
                [signature, _s(learner.username), pk],
            )
            return JsonResponse(_state(request, learner, cur))
    except DatabaseError:
        logger.exception("Could not save first-login details for learner %s.", pk)
        return _error("Your details could not be saved. Please try again.", 503)
