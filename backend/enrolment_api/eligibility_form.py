"""The public apprenticeship eligibility form.

    POST /enrolment_api/public/apprenticeship-application/  -> 201 {"submitted": true}

Filled in at /apprenticeship_application by people who have no LMS account yet,
so neither the page nor this endpoint needs a sign-in. The client sends no
cookies (``credentials: 'omit'``); the view still requires
``X-Requested-With: XMLHttpRequest``, the CSRF defence every other public JSON
endpoint here uses (see login/views.py).

Storage
-------
One row per submitted application in enrolment.eligibility_form. Nothing is
saved before the applicant presses Submit (there is no draft), and nothing here
updates or deletes a row. The form collects answers only: staff assess
eligibility themselves, so no funding rule is applied on the way in.

The table is created and brought up to date by
``manage.py apply_eligibility_form_table``. This view never runs DDL, since
anyone on the internet can call it; if the table is missing it says the form is
unavailable rather than pretending to save.

Choice answers are stored as their labels, the way the enrolment wizard stores
``Employment_status``, so the table reads plainly when queried.

What the form asks
------------------
The questions come from the layout published in the "Edit eligibility form"
builder (eligibility_layout.py): which built-in questions are asked and whether
they are required, the answer lists of the choice questions, and any custom
questions, each with its own ``custom_*`` column. With nothing published the
form asks FIELDS as they stand. An answer the form does not ask is stored as
NULL. First name, surname and work email identify the applicant, so they are
always asked.

The client sends the layout version it was shown (``layoutVersion``). If an
admin has published since, the submission is refused with 409 rather than
judged against questions the applicant never saw; the page reloads the form and
keeps their answers. The version is stored with the row.

Postcodes
---------
Both postcodes are looked up (postcode_lookup.py) and saved as written there,
with their country, region and district alongside. A postcode is never a
reason to refuse the form: the application is always saved. But one that is not
a UK postcode, cannot be found, or is outside England makes the applicant not
eligible -- saved as eligible = false with the reason in ineligible_reason, and
the applicant is told so instead of "Application submitted". If the lookup
service is down, the location is left NULL and eligible stays NULL (not
checked), so "not checked" never looks like "checked".

Invitation
----------
An applicant whose postcodes are all in England is created as an
apprenticeship learner and emailed the set-password invitation
(eligibility_invitation.py). The response's ``outcome`` tells the page what
happened: "invited", "invitePending" (created, but the email could not be sent;
staff can re-send it), "existingAccount" (the address already has an account:
sign in or use Forgot password), "notEligible", or "retryLater" (the postcodes
could not be checked, so nobody was invited).
"""
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from login.security import client_ip, user_agent

from . import eligibility_invitation, postcode_lookup

logger = logging.getLogger(__name__)

CONN = "enrolment"
TABLE = "eligibility_form"

#: The whole form is a few KB; this only stops a runaway payload.
MAX_BODY_BYTES = 64 * 1024

EMPLOYMENT_STATUSES = (
    "In paid employment",
    "Self employment",
    "Not in paid employment, looking for work and available to start work",
    "Not in paid employment, not looking for work and/or not available to start work",
)

RESIDENCY_STATUSES = (
    "UK Citizen",
    "Settled Status",
    "Pre-settled Status",
    "Skilled Worker Visa",
    "Study or Graduate Visa",
    "Other",
)

PROGRAMMES = (
    "Marketing Executive Level 4",
    "Marketing Manager Level 6",
    "Associate Project Manager Level 4",
    "Project Controls Professional Level 6",
)

# (payload key, column, kind, required, limit-or-choices). Order is the form's.
FIELDS = (
    # Personal Details
    ("firstName", "first_name", "text", True, 100),
    ("surname", "surname", "text", True, 100),
    ("workEmail", "work_email", "email", True, 254),
    ("phoneNumber", "phone_number", "text", False, 40),
    ("fullAddress", "full_address", "text", True, 500),
    ("postcode", "postcode", "text", True, 12),
    ("dateOfBirth", "date_of_birth", "date", True, None),
    # Employment
    ("employmentStatus", "employment_status", "choice", True, EMPLOYMENT_STATUSES),
    ("majorityHoursInEngland", "majority_hours_in_england", "bool", True, None),
    ("employerName", "employer_name", "text", True, 200),
    ("employerAddress", "employer_address", "text", True, 500),
    ("employerPostcode", "employer_postcode", "text", True, 12),
    ("representativeName", "representative_name", "text", True, 200),
    ("representativeEmail", "representative_email", "email", True, 254),
    # Residency
    ("livesInUk", "lives_in_uk", "bool", True, None),
    ("residenceNotForEducation", "residence_not_for_education", "bool", True, None),
    ("residencyStatus", "residency_status", "choice", True, RESIDENCY_STATUSES),
    # Programme Suitability
    ("programme", "programme", "choice", True, PROGRAMMES),
    ("attendedFundedTraining", "attended_funded_training", "bool", True, None),
    # Asked only after a Yes to attendedFundedTraining (see FOLLOW_UPS).
    ("fundedTrainingCompletedOn", "funded_training_completed_on", "date", False, None),
    ("notPreviouslyCompletedProgramme", "not_previously_completed_programme", "bool", True, None),
)

COLUMNS = tuple(column for _key, column, *_rest in FIELDS)
FIELD_BY_KEY = {key: (column, kind, required, limit) for key, column, kind, required, limit in FIELDS}

#: Always asked and required, whatever the layout says: they identify the applicant.
LOCKED = ("firstName", "surname", "workEmail")

#: Built-in questions asked only after a given answer to an earlier one. When
#: asked they are required unless the layout makes them optional.
FOLLOW_UPS = {"fundedTrainingCompletedOn": ("attendedFundedTraining", True)}

#: Each looked-up postcode: (its column, then the columns of its location).
POSTCODES = {
    "postcode": ("postcode", "postcode_country", "postcode_region", "postcode_district"),
    "employerPostcode": (
        "employer_postcode", "employer_postcode_country", "employer_postcode_region", "employer_postcode_district",
    ),
}
LOCATION_COLUMNS = tuple(column for _postcode, *location in POSTCODES.values() for column in location)

#: Columns added since the table was first created (see upgrade_table).
ADDED_COLUMNS = tuple((column, "text") for column in LOCATION_COLUMNS) + (
    ("layout_version", "bigint"),
    ("eligible", "boolean"),
    ("ineligible_reason", "text"),
)

#: What every insert writes, before the custom questions' columns.
INSERT_COLUMNS = COLUMNS + LOCATION_COLUMNS + ("layout_version", "eligible", "ineligible_reason")

#: How each looked-up postcode is named in an ineligibility reason.
POSTCODE_NAMES = {"postcode": "Your postcode", "employerPostcode": "Your employer's postcode"}

#: Custom questions added in the builder: their SQL type, by question type.
CUSTOM_SQL_TYPES = {"text": "text", "number": "numeric", "date": "date", "yesno": "boolean", "dropdown": "text"}
CUSTOM_COLUMN_RE = re.compile(r"^custom_[a-z0-9_]{1,60}$")
MAX_CUSTOM_TEXT = 500

REQUIRED = "This field is required."
FORM_CHANGED = (
    "This form was updated while you were filling it in. Your answers have been kept: "
    "please check each section and submit again."
)
UNAVAILABLE = "We could not save your application just now. Please try again in a few minutes."
EARLIEST_DATE = date(1900, 1, 1)


def ensure_table(cursor):
    """Create enrolment.eligibility_form if it does not exist. Never alters it."""
    cursor.execute("CREATE SCHEMA IF NOT EXISTS enrolment")
    # Only the answers that identify the applicant are NOT NULL: the builder
    # can remove every other question from the form.
    cursor.execute(
        f"""CREATE TABLE IF NOT EXISTS enrolment.{TABLE} (
            id                                bigserial PRIMARY KEY,
            first_name                        text NOT NULL,
            surname                           text NOT NULL,
            work_email                        text NOT NULL,
            phone_number                      text,
            full_address                      text,
            postcode                          text,
            postcode_country                  text,
            postcode_region                   text,
            postcode_district                 text,
            date_of_birth                     date,
            employment_status                 text,
            majority_hours_in_england         boolean,
            employer_name                     text,
            employer_address                  text,
            employer_postcode                 text,
            employer_postcode_country         text,
            employer_postcode_region          text,
            employer_postcode_district        text,
            representative_name               text,
            representative_email              text,
            lives_in_uk                       boolean,
            residence_not_for_education       boolean,
            residency_status                  text,
            programme                         text,
            attended_funded_training          boolean,
            funded_training_completed_on      date,
            not_previously_completed_programme boolean,
            layout_version                    bigint,
            eligible                          boolean,
            ineligible_reason                 text,
            submitted_at                      timestamptz NOT NULL DEFAULT now()
        )"""
    )


def upgrade_table(cursor):
    """Bring a table created before the builder up to date.

    Adds the columns introduced since, and lets every answer the builder can
    remove be NULL. Idempotent. Never drops a column or touches a row.
    """
    for column, sql_type in ADDED_COLUMNS:
        cursor.execute(f"ALTER TABLE enrolment.{TABLE} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
    for key, column, *_rest in FIELDS:
        if key not in LOCKED:
            cursor.execute(f"ALTER TABLE enrolment.{TABLE} ALTER COLUMN {column} DROP NOT NULL")


def add_custom_columns(cursor, columns):
    """Add the columns of custom questions, {column: sql_type}. Idempotent; never drops."""
    for column, sql_type in columns.items():
        if not CUSTOM_COLUMN_RE.match(column) or sql_type not in CUSTOM_SQL_TYPES.values():
            raise ValueError(f"Refusing custom column {column!r} {sql_type!r}.")
        cursor.execute(f'ALTER TABLE enrolment.{TABLE} ADD COLUMN IF NOT EXISTS "{column}" {sql_type}')


def _clean_text(value, limit):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Enter text.")
    # Single-line inputs: collapse pasted line breaks and runs of spaces.
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValueError(f"Use {limit} characters or fewer.")
    return cleaned or None


def _clean_date(value, today):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("Enter a valid date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("Enter a valid date.") from None
    if parsed > today:
        raise ValueError("The date cannot be in the future.")
    if parsed < EARLIEST_DATE:
        raise ValueError("Enter a valid date.")
    return parsed


def _clean_number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Enter a number.")
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError("Enter a number.") from None
    if len(text) > 30 or not number.is_finite():
        raise ValueError("Enter a number.")
    return number


def _clean_field(kind, value, limit, today):
    if kind == "bool":
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError("Choose Yes or No.")
        return value
    if kind == "date":
        return _clean_date(value, today)
    if kind == "choice":
        if value in (None, ""):
            return None
        if value not in limit:
            raise ValueError("Choose one of the listed options.")
        return value
    cleaned = _clean_text(value, limit)
    if kind == "email" and cleaned is not None:
        try:
            validate_email(cleaned)
        except ValidationError:
            raise ValueError("Enter a valid email address.") from None
    return cleaned


def _clean_custom(item, value):
    kind = item.get("type")
    if kind == "yesno":
        return _clean_field("bool", value, None, None)
    if kind == "date":
        # A custom date may be a future one, such as a planned start.
        return _clean_date(value, date.max)
    if kind == "number":
        return _clean_number(value)
    if kind == "dropdown":
        return _clean_field("choice", value, tuple(item.get("options") or ()), None)
    return _clean_text(value, MAX_CUSTOM_TEXT)


def asked_questions(layout):
    """The questions the form asks under `layout` (None: the standard form).

    Returns (builtin, custom). `builtin` maps each built-in key the form asks to
    {"required": bool, "options": tuple or None}; `custom` lists the custom
    questions it asks, in form order, before their conditions are applied. A
    question removed from the form, or on a hidden section, is not asked.
    """
    def builtin_entry(key, override=None):
        _column, kind, required, limit = FIELD_BY_KEY[key]
        if key in FOLLOW_UPS:
            required = True
        if isinstance(override, bool):
            required = override
        return {"required": required, "options": limit if kind == "choice" else None}

    if not layout:
        return {key: builtin_entry(key) for key in FIELD_BY_KEY}, []

    builtin, custom = {}, []
    for section in layout.get("sections") or []:
        if not isinstance(section, dict) or section.get("hidden"):
            continue
        for item in section.get("items") or []:
            if not isinstance(item, dict) or item.get("hidden"):
                continue
            key = item.get("key")
            if item.get("builtin"):
                if key in FIELD_BY_KEY:
                    entry = builtin_entry(key, item.get("required"))
                    if entry["options"] is not None and item.get("options"):
                        entry["options"] = tuple(item["options"])
                    builtin[key] = entry
            elif item.get("type") in CUSTOM_SQL_TYPES and CUSTOM_COLUMN_RE.match(str(item.get("column") or "")):
                custom.append(item)
    for key in LOCKED:
        builtin[key] = builtin_entry(key, True)
    return builtin, custom


def _answer_label(value):
    """An answer as a condition names it: Yes/No for a yes/no question."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value if isinstance(value, str) else None


def clean_application(payload, today=None, layout=None):
    """Validate a submission. Returns (row, errors) keyed by column / payload key.

    Keys outside the form are ignored, so the client cannot choose a column.
    Custom answers arrive under payload["custom"], keyed by question key.
    """
    today = today or timezone.localdate()
    asked, custom = asked_questions(layout)
    row, errors = {}, {}
    for key, column, kind, _required, limit in FIELDS:
        question = asked.get(key)
        follow_up = FOLLOW_UPS.get(key)
        if follow_up:
            trigger, answer = follow_up
            if trigger not in asked or row.get(FIELD_BY_KEY[trigger][0]) is not answer:
                question = None
        if question is None:
            # Not asked: a stale answer from a changed choice is not stored either.
            row[column] = None
            continue
        if question["options"] is not None:
            limit = question["options"]
        try:
            value = _clean_field(kind, payload.get(key), limit, today)
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        if value is None and question["required"]:
            errors[key] = REQUIRED
        row[column] = value

    answers = payload.get("custom")
    answers = answers if isinstance(answers, dict) else {}
    values, problems = {}, {}
    for item in custom:
        try:
            values[item["key"]] = _clean_custom(item, answers.get(item["key"]))
        except ValueError as exc:
            problems[item["key"]] = str(exc)
            values[item["key"]] = None

    by_key = {item["key"]: item for item in custom}

    def shown(item, depth=0):
        condition = item.get("condition")
        if not condition:
            return True
        field = condition.get("field")
        if depth > 10:
            return False
        if field in by_key:
            if not shown(by_key[field], depth + 1):
                return False
            value = values.get(field)
        elif field in asked:
            value = row.get(FIELD_BY_KEY[field][0])
        else:
            return False
        return _answer_label(value) in (condition.get("values") or [])

    for item in custom:
        key, column = item["key"], item["column"]
        if not shown(item):
            row[column] = None
        elif key in problems:
            errors[key] = problems[key]
        elif values[key] is None and item.get("required"):
            errors[key] = REQUIRED
        else:
            row[column] = values[key]
    return row, errors


def locate_postcodes(row):
    """Look up the postcodes in `row`; returns (eligible, reasons).

    Each postcode found is saved as written, with its location. A postcode that
    is not a UK postcode, cannot be found or is outside England is a reason the
    applicant is not eligible. eligible is True when every postcode given was
    found in England, False when there is a reason, and None when one could not
    be checked (and nothing else ruled the applicant out).
    """
    reasons, unchecked = [], False
    for key, (column, country, region, district) in POSTCODES.items():
        row[country] = row[region] = row[district] = None
        value = row.get(column)
        if not value:
            continue
        name = POSTCODE_NAMES[key]
        if not postcode_lookup.is_valid(value):
            reasons.append(f"{name} ({value}) is not a valid UK postcode.")
            continue
        try:
            found = postcode_lookup.lookup(value)
        except postcode_lookup.PostcodeLookupUnavailable as exc:
            logger.warning("Could not check a postcode on an eligibility application: %s", exc)
            unchecked = True
            continue
        if found is None:
            reasons.append(f"{name} ({postcode_lookup.formatted(value)}) could not be found.")
            continue
        row[column] = found["postcode"]
        row[country] = found["country"] or None
        row[region] = found["region"] or None
        row[district] = found["district"] or None
        if found["country"] != "England":
            where = f" ({found['country']})" if found["country"] else ""
            reasons.append(f"{name} ({found['postcode']}) is outside England{where}.")
    if reasons:
        return False, reasons
    return (None if unchecked else True), []


def _published_layout():
    """(version, layout) in force, or (None, None) while nothing is published."""
    # Imported here: the layout module builds on this one.
    from .eligibility_layout import read_current

    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
    return (current[0], current[1]) if current else (None, None)


def _error(message, status, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


@csrf_exempt
def submit_application(request):
    if request.method != "POST":
        return _error("Method not allowed.", 405)
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _error("Missing X-Requested-With header.", 403)
    if len(request.body or b"") > MAX_BODY_BYTES:
        return _error("The application is too large.", 413)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)
    shown_version = payload.get("layoutVersion")
    if shown_version is not None and (isinstance(shown_version, bool) or not isinstance(shown_version, int)):
        return _error("'layoutVersion' must be a number or null.", 400)

    try:
        version, layout = _published_layout()
    except DatabaseError:
        logger.exception("Could not read the eligibility form layout")
        return _error(UNAVAILABLE, 503)
    # A page loaded before the builder existed sends no version; it was shown
    # the standard form, which is what is in force until the first publish.
    if "layoutVersion" in payload and shown_version != version:
        return _error(FORM_CHANGED, 409, formChanged=True)

    row, errors = clean_application(payload, layout=layout)
    if errors:
        return _error("Please correct the highlighted answers.", 400, fields=errors)
    eligible, reasons = locate_postcodes(row)
    row["layout_version"] = version
    row["eligible"] = eligible
    row["ineligible_reason"] = " ".join(reasons) or None

    custom_columns = [column for column in row if CUSTOM_COLUMN_RE.match(column)]
    names = list(INSERT_COLUMNS) + [f'"{column}"' for column in custom_columns]
    params = [row[column] for column in INSERT_COLUMNS] + [row[column] for column in custom_columns]
    try:
        with connections[CONN].cursor() as cursor:
            cursor.execute(
                f"INSERT INTO enrolment.{TABLE} ({', '.join(names)}) "
                f"VALUES ({', '.join(['%s'] * len(names))}) RETURNING id",
                params,
            )
            application_id = cursor.fetchone()[0]
    except DatabaseError:
        # Never echo the database error to an anonymous caller.
        logger.exception("Could not save an apprenticeship eligibility application")
        return _error(UNAVAILABLE, 503)

    logger.info("Apprenticeship eligibility application %s received", application_id)
    # Saved either way; the applicant is told whether the postcodes ruled them
    # out, and an eligible one is invited to set their password.
    if eligible is True:
        outcome = eligibility_invitation.invite_applicant(
            row, ip=client_ip(request), user_agent=user_agent(request),
        )
    elif eligible is False:
        outcome = "notEligible"
    else:
        outcome = eligibility_invitation.RETRY_LATER
    return JsonResponse(
        {"submitted": True, "eligible": eligible, "reasons": reasons, "outcome": outcome}, status=201,
    )
