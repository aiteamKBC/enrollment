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
saved before the applicant presses Submit (there is no draft). Historical rows
are never reclassified. New submissions are deduplicated and only their
delivery response receipts are updated for safe retries.

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
Employer Postcode must be well formed and found before a submission is saved;
a service failure returns a retryable field error. Supplied postcodes are
looked up (postcode_lookup.py) and saved with country, region and district.
With workplace screening enabled, residential and employer-company countries
do not determine workplace eligibility: the verified effective workplace and
working-hours declaration drive preliminary location screening. Supplied UK
residential postcodes still have format and existence checks. With screening
disabled, a residential postcode outside England retains its legacy
ineligibility reason. An unavailable lookup is never treated as invalid or as
evidence for approval.

Invitation
----------
An applicant who passes the applicable preliminary screening is created as an
apprenticeship learner and emailed the set-password invitation
(eligibility_invitation.py). The response's ``outcome`` tells the page what
happened: "invited", "invitePending" (created, but the email could not be sent;
staff can re-send it), "existingAccount" (the address already has an account:
sign in or use Forgot password), "notEligible", or a technical invitation retry.
New completed screenings have boolean eligibility. Lookup failures are HTTP
errors before saving. Historical pendingAssessment receipts remain readable
without reclassification or invitation replay.
"""
import hashlib
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.conf import settings
from django.core.validators import validate_email
from django.db import DatabaseError, connections, transaction
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

# Built-in answer identifiers are stable.  In particular, livesInUk is retained
# as the storage key for the public UK/EEA-national question so existing rows
# and published layouts remain readable.
RETIRED_PUBLIC_FIELDS = frozenset({"notPreviouslyCompletedProgramme", "employerAddress", "fullAddress", "title"})
RETIRED_PUBLIC_ITEMS = frozenset({"note.representative"})
ALWAYS_VISIBLE_PUBLIC_FIELDS = frozenset({"livesInUk", "nationality", "dateOfBirth"})
NEW_PUBLIC_FIELDS = frozenset({
    "startDateWithEmployer", "jobTitle", "countryOfResidence", "nationality",
    "residentPrev3Years", "yearsInUk", "fullTimeEducationPrior",
})

# Keep the server-side choice validation in step with the public dropdowns.
# The frontend exposes the complete ISO country list; this server-side copy is
# kept in the same order-independent set so arbitrary text is rejected.
COUNTRY_OPTIONS = (
    "United Kingdom", "Ireland", "Afghanistan", "Albania", "Algeria", "Andorra", "Angola",
    "Argentina", "Australia", "Austria", "Bangladesh", "Belgium", "Brazil", "Bulgaria",
    "Canada", "China", "Colombia", "Croatia", "Cyprus", "Czechia", "Denmark", "Egypt",
    "Estonia", "Finland", "France", "Germany", "Ghana", "Greece", "Hungary", "Iceland",
    "India", "Indonesia", "Iran", "Iraq", "Israel", "Italy", "Jamaica", "Japan", "Kenya",
    "Latvia", "Lebanon", "Lithuania", "Luxembourg", "Malaysia", "Malta", "Mexico", "Morocco",
    "Nepal", "Netherlands", "New Zealand", "Nigeria", "Norway", "Pakistan", "Peru", "Poland",
    "Portugal", "Romania", "Russia", "Saudi Arabia", "Serbia", "Singapore", "Slovakia",
    "Slovenia", "South Africa", "South Korea", "Spain", "Sri Lanka", "Sweden", "Switzerland",
    "Thailand", "Turkey", "Ukraine", "United Arab Emirates", "United States", "Vietnam", "Zambia",
)
COUNTRY_OPTIONS = tuple(dict.fromkeys(COUNTRY_OPTIONS + (
    "\u00c5land Islands",
    "Cura\u00e7ao",
    "R\u00e9union",
    "Saint Barth\u00e9lemy",
    "Afghanistan",
    "Albania",
    "Algeria",
    "Andorra",
    "Angola",
    "Antigua and Barbuda",
    "Argentina",
    "Armenia",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahamas",
    "Bahrain",
    "Bangladesh",
    "Barbados",
    "Belarus",
    "Belgium",
    "Belize",
    "Benin",
    "Bhutan",
    "Bolivia",
    "Bosnia and Herzegovina",
    "Botswana",
    "Brazil",
    "Brunei",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cambodia",
    "Cameroon",
    "Canada",
    "Cape Verde",
    "Central African Republic",
    "Chad",
    "Chile",
    "China",
    "Colombia",
    "Comoros",
    "Congo",
    "Congo (Democratic Republic)",
    "Costa Rica",
    "Croatia",
    "Cuba",
    "Cyprus",
    "Czechia",
    "Denmark",
    "Djibouti",
    "Dominica",
    "Dominican Republic",
    "East Timor",
    "Ecuador",
    "Egypt",
    "El Salvador",
    "Equatorial Guinea",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Fiji",
    "Finland",
    "France",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Greece",
    "Grenada",
    "Guatemala",
    "Guinea",
    "Guinea-Bissau",
    "Guyana",
    "Haiti",
    "Honduras",
    "Hong Kong",
    "Hungary",
    "Iceland",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Ireland",
    "Israel",
    "Italy",
    "Ivory Coast",
    "Jamaica",
    "Japan",
    "Jordan",
    "Kazakhstan",
    "Kenya",
    "Kiribati",
    "Kosovo",
    "Kuwait",
    "Kyrgyzstan",
    "Laos",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Macao",
    "Madagascar",
    "Malawi",
    "Malaysia",
    "Maldives",
    "Mali",
    "Malta",
    "Marshall Islands",
    "Mauritania",
    "Mauritius",
    "Mexico",
    "Micronesia",
    "Moldova",
    "Monaco",
    "Mongolia",
    "Montenegro",
    "Morocco",
    "Mozambique",
    "Myanmar (Burma)",
    "Namibia",
    "Nauru",
    "Nepal",
    "Netherlands",
    "New Zealand",
    "Nicaragua",
    "Niger",
    "Nigeria",
    "North Korea",
    "North Macedonia",
    "Norway",
    "Oman",
    "Pakistan",
    "Palau",
    "Palestine",
    "Panama",
    "Papua New Guinea",
    "Paraguay",
    "Peru",
    "Philippines",
    "Poland",
    "Portugal",
    "Qatar",
    "Romania",
    "Russia",
    "Rwanda",
    "Samoa",
    "San Marino",
    "Sao Tome and Principe",
    "Saudi Arabia",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Singapore",
    "Slovakia",
    "Slovenia",
    "Solomon Islands",
    "Somalia",
    "South Africa",
    "South Korea",
    "South Sudan",
    "Spain",
    "Sri Lanka",
    "St Kitts and Nevis",
    "St Lucia",
    "St Vincent and the Grenadines",
    "Sudan",
    "Suriname",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Tajikistan",
    "Tanzania",
    "Thailand",
    "Togo",
    "Tonga",
    "Trinidad and Tobago",
    "Tunisia",
    "Turkey",
    "Turkmenistan",
    "Tuvalu",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United States",
    "Uruguay",
    "Uzbekistan",
    "Vanuatu",
    "Vatican City",
    "Venezuela",
    "Vietnam",
    "Yemen",
    "Zambia",
    "Zimbabwe",
    "American Samoa",
    "Anguilla",
    "Antarctica",
    "Aruba",
    "Bermuda",
    "Bonaire, Sint Eustatius and Saba",
    "Bouvet Island",
    "British Indian Ocean Territory",
    "British Virgin Islands",
    "Cayman Islands",
    "Christmas Island",
    "Cocos (Keeling) Islands",
    "Cook Islands",
    "Falkland Islands",
    "Faroe Islands",
    "French Guiana",
    "French Polynesia",
    "French Southern Territories",
    "Gibraltar",
    "Greenland",
    "Guadeloupe",
    "Guam",
    "Guernsey",
    "Heard Island and McDonald Islands",
    "Isle of Man",
    "Jersey",
    "Martinique",
    "Mayotte",
    "Montserrat",
    "New Caledonia",
    "Niue",
    "Norfolk Island",
    "Northern Mariana Islands",
    "Pitcairn",
    "Puerto Rico",
    "Saint Helena, Ascension and Tristan da Cunha",
    "Saint Martin (French part)",
    "Saint Pierre and Miquelon",
    "Sint Maarten (Dutch part)",
    "South Georgia and the South Sandwich Islands",
    "Svalbard and Jan Mayen",
    "Tokelau",
    "Turks and Caicos Islands",
    "United States Minor Outlying Islands",
    "United States Virgin Islands",
    "Wallis and Futuna",
    "Western Sahara",
)))
NATIONALITY_OPTIONS = COUNTRY_OPTIONS

EMPLOYMENT_STATUSES = (
    "In paid employment",
    "Self employment",
    "Not in paid employment, looking for work and available to start work",
    "Not in paid employment, not looking for work and/or not available to start work",
)
EMPLOYMENT_RESTRICTION_ERROR = "You cannot continue with this application at this stage."
MINIMUM_APPLICATION_AGE = 16
APPLICATION_AGE_REASON = (
    "Under our application-stage screening policy, you must be at least 16 years old "
    "on the date you submit your application. You are currently under 16, so you are "
    "not eligible at this stage. Please contact Admissions at office@kentbusinesscollege.org if you have any questions."
)
WORKING_HOURS_SCREENING_REASON = (
    "You do not expect to spend at least 50% of your working hours in England, "
    "so you do not currently meet the standard initial screening requirement."
)
EMPLOYER_POSTCODE_UNAVAILABLE = "We could not verify your employer's postcode just now. Please try again."
WORKPLACE_POSTCODE_UNAVAILABLE = "We could not verify your main workplace postcode just now. Please try again."
WORKPLACE_ARRANGEMENTS = ("same", "different", "remote_hybrid_multiple")
PROVISIONAL_ELIGIBLE = "provisional_eligible"
PENDING_ASSESSMENT = "pending_assessment"
NOT_ELIGIBLE = "not_eligible"
PENDING_OUTCOME = "pendingAssessment"
# Additive structured answers. Legacy full-address columns remain readable and
# receive a server-composed value for NEW submissions only; old rows are untouched.
ADDRESS_FIELDS = tuple(
    (f"{prefix}{suffix}", f"{column}_{sql_suffix}", "text", required, limit)
    for prefix, column in (("employer", "employer"), ("residential", "residential"))
    for suffix, sql_suffix, required, limit in (
        ("AddressLine1", "address_line1", True, 200),
        ("AddressLine2", "address_line2", False, 200),
        ("AddressLine3", "address_line3", False, 200),
        ("TownCity", "town_city", True, 100),
        ("County", "county", False, 100),
    )
)
ADDRESS_GROUPS = {
    "employerAddress": tuple(key for key, *_rest in ADDRESS_FIELDS if key.startswith("employer")),
    "fullAddress": tuple(key for key, *_rest in ADDRESS_FIELDS if key.startswith("residential")),
}
# Active Lines 3/4 use the established TownCity/County contract. The older
# additional-details field stays optional/readable for historical API clients.
LEGACY_ADDRESS_FIELDS = frozenset({"employerAddressLine3", "residentialAddressLine3"})
ADDRESS_INPUT_GROUPS = {legacy: tuple(key for key in keys if key not in LEGACY_ADDRESS_FIELDS)
                        for legacy, keys in ADDRESS_GROUPS.items()}
ADDRESS_INPUT_FIELDS = frozenset(key for keys in ADDRESS_INPUT_GROUPS.values() for key in keys)
ADDRESS_COLUMNS = tuple(column for _key, column, *_rest in ADDRESS_FIELDS)
REQUIRED_ADDRESS_FIELDS = frozenset(key for key, _column, _kind, required, _limit in ADDRESS_FIELDS if required)

SCREENING_REQUIRED_FIELDS = ("employmentStatus", "employerPostcode", "majorityHoursInEngland")
# These inputs affect the automatic result or the postcode checks. Protection
# from removal is separate from whether an answer is mandatory (postcode is optional).
SCREENING_PROTECTED_FIELDS = frozenset({
    "dateOfBirth", "employmentStatus", "employerPostcode",
    "majorityHoursInEngland", "postcode", "countryOfResidence",
})
SCREENING_PREVIEW_FIELDS = frozenset({"workplaceArrangement", "mainWorkplacePostcode"})
# Keep these built-in questions on the form even when their answers are
# optional or conditional. This does not change required-answer rules.
FORM_PROTECTED_FIELDS = frozenset({
    "employerName", "employerAddressLine1", "employerAddressLine2",
    "employerTownCity", "employerCounty", "startDateWithEmployer",
    "livesInUk", "nationality", "residencyStatus",
    "residentialAddressLine1", "residentialAddressLine2",
    "residentialTownCity", "residentialCounty",
    "attendedFundedTraining", "fundedTrainingCompletedOn",
})


def screening_protected_fields():
    return SCREENING_PROTECTED_FIELDS | (SCREENING_PREVIEW_FIELDS if workplace_enabled() else frozenset())


def non_removable_fields():
    return screening_protected_fields() | FORM_PROTECTED_FIELDS


ASSESSMENT_CONSISTENCY = (
    "assessment_status IS NULL OR "
    "(assessment_status = 'pending_assessment' AND eligible IS NULL AND assessment_reason IS NOT NULL) OR "
    "(assessment_status = 'provisional_eligible' AND eligible IS TRUE AND assessment_reason IS NULL) OR "
    "(assessment_status = 'not_eligible' AND eligible IS FALSE AND assessment_reason IS NOT NULL)"
)


def workplace_enabled():
    """Never activate the incomplete Phase 2 flow in production settings."""
    return settings.DEBUG and getattr(settings, "ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED", False) is True


def application_date():
    """The submission calendar date is UK-local, independent of server timezone."""
    return timezone.localdate(timezone=ZoneInfo("Europe/London"))


def age_on(birth, today):
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

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
    ("fullAddress", "full_address", "text", False, 500),
    ("postcode", "postcode", "text", False, 12),
    ("dateOfBirth", "date_of_birth", "date", True, None),
    # Employment
    ("employmentStatus", "employment_status", "choice", True, EMPLOYMENT_STATUSES),
    ("startDateWithEmployer", "start_date_with_employer", "date", True, None),
    ("jobTitle", "job_title", "text", True, 200),
    ("majorityHoursInEngland", "majority_hours_in_england", "bool", True, None),
    ("employerName", "employer_name", "text", True, 200),
    ("employerAddress", "employer_address", "text", True, 500),
    ("employerPostcode", "employer_postcode", "text", True, 12),
    ("representativeName", "representative_name", "text", True, 200),
    ("representativeEmail", "representative_email", "email", True, 254),
    # Residency
    ("livesInUk", "lives_in_uk", "bool", True, None),
    ("residencyStatus", "residency_status", "choice", True, RESIDENCY_STATUSES),
    ("countryOfResidence", "country_of_residence", "choice", True, COUNTRY_OPTIONS),
    ("nationality", "nationality", "choice", True, NATIONALITY_OPTIONS),
    ("residentPrev3Years", "resident_prev_3_years", "bool", True, None),
    ("yearsInUk", "years_in_uk", "choice", True, ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10 or more")),
    ("residenceNotForEducation", "residence_not_for_education", "bool", True, None),
    ("fullTimeEducationPrior", "full_time_education_prior", "bool", True, None),
    ("expectedLeavingDate", "expected_leaving_date", "date", False, None),
    # Moved from Programme Suitability; the key and conditional completion date are preserved.
    ("attendedFundedTraining", "attended_funded_training", "bool", True, None),
    ("fundedTrainingCompletedOn", "funded_training_completed_on", "date", False, None),
    # Programme selection and the historical, retired confirmation column.
    ("programme", "programme", "choice", True, PROGRAMMES),
    ("notPreviouslyCompletedProgramme", "not_previously_completed_programme", "bool", True, None),
) + ADDRESS_FIELDS

WORKPLACE_FIELDS = (
    ("workplaceArrangement", "workplace_arrangement", "choice", True, WORKPLACE_ARRANGEMENTS),
    ("mainWorkplacePostcode", "main_workplace_postcode", "text", False, 12),
)
WORKPLACE_KEYS = frozenset(key for key, *_rest in WORKPLACE_FIELDS)
WORKPLACE_COLUMN_DEFINITIONS = (
    ("workplace_arrangement", "text CONSTRAINT eligibility_workplace_arrangement_check CHECK (workplace_arrangement IN ('same', 'different', 'remote_hybrid_multiple'))"),
    ("main_workplace_postcode", "text CONSTRAINT eligibility_workplace_postcode_length CHECK (char_length(main_workplace_postcode) <= 12)"),
    ("main_workplace_country", "text CONSTRAINT eligibility_workplace_country_length CHECK (char_length(main_workplace_country) <= 100)"),
    ("main_workplace_region", "text CONSTRAINT eligibility_workplace_region_length CHECK (char_length(main_workplace_region) <= 100)"),
    ("main_workplace_district", "text CONSTRAINT eligibility_workplace_district_length CHECK (char_length(main_workplace_district) <= 100)"),
    ("assessment_status", "text CONSTRAINT eligibility_assessment_status_check CHECK (assessment_status IN ('provisional_eligible', 'pending_assessment', 'not_eligible'))"),
    ("assessment_reason", "text CONSTRAINT eligibility_assessment_reason_length CHECK (char_length(assessment_reason) BETWEEN 1 AND 1000)"),
)
WORKPLACE_COLUMNS = tuple(column for column, _definition in WORKPLACE_COLUMN_DEFINITIONS)
SCREENING_COLUMN_DEFINITIONS = (
    ("submission_fingerprint", "text CONSTRAINT eligibility_submission_fingerprint_check CHECK (submission_fingerprint ~ '^[0-9a-f]{64}$') CONSTRAINT eligibility_submission_fingerprint_unique UNIQUE"),
    ("submission_response", "jsonb CONSTRAINT eligibility_submission_response_check CHECK (jsonb_typeof(submission_response) = 'object')"),
)
SCREENING_COLUMNS = tuple(column for column, _definition in SCREENING_COLUMN_DEFINITIONS)
COLUMNS = tuple(column for _key, column, *_rest in FIELDS)
FIELD_BY_KEY = {key: (column, kind, required, limit) for key, column, kind, required, limit in FIELDS + WORKPLACE_FIELDS}

#: Always asked and required, whatever the layout says: they identify the applicant.
LOCKED = ("firstName", "surname", "workEmail")

#: Built-in questions asked only after a given answer to an earlier one. When
#: asked they are required unless the layout makes them optional.
FOLLOW_UPS = {"fundedTrainingCompletedOn": ("attendedFundedTraining", True)}

# Residency questions whose answers determine whether another built-in field
# is relevant. Hidden answers are cleared instead of being treated as current.
RESIDENCY_CONDITIONAL_FIELDS = {
    "yearsInUk": ("residentPrev3Years", False),
    "residenceNotForEducation": ("residentPrev3Years", True),
    "expectedLeavingDate": ("fullTimeEducationPrior", True),
}

# These answers are relevant only to an applicant who is in paid employment.
# The keys and columns remain in the contract so historical submissions stay
# readable, while clean_application clears them when another status is chosen.
EMPLOYMENT_CONDITIONAL_FIELDS = frozenset(ADDRESS_GROUPS["employerAddress"]) | frozenset({
    "startDateWithEmployer", "jobTitle", "majorityHoursInEngland", "employerName", "employerAddress",
    "employerPostcode", "representativeName", "representativeEmail",
})

#: Each looked-up postcode: (its column, then the columns of its location).
POSTCODES = {
    "postcode": ("postcode", "postcode_country", "postcode_region", "postcode_district"),
    "employerPostcode": (
        "employer_postcode", "employer_postcode_country", "employer_postcode_region", "employer_postcode_district",
    ),
}
LOCATION_COLUMNS = tuple(column for _postcode, *location in POSTCODES.values() for column in location)

#: Columns added since the table was first created (see upgrade_table).
ADDED_COLUMNS = tuple((column, "text") for column in LOCATION_COLUMNS + ADDRESS_COLUMNS) + (
    ("start_date_with_employer", "date"),
    ("job_title", "text"),
    ("country_of_residence", "text"),
    ("nationality", "text"),
    ("resident_prev_3_years", "boolean"),
    ("years_in_uk", "text"),
    ("full_time_education_prior", "boolean"),
    ("expected_leaving_date", "date"),
    ("layout_version", "bigint"),
    ("eligible", "boolean"),
    ("ineligible_reason", "text"),
)

#: Phase 1 columns: the disabled preview must work before its schema is applied.
#: Enabled preview submissions append WORKPLACE_COLUMNS, before custom columns.
# Omit a blank new date from INSERTs so older databases still accept unchanged
# applications until the prepared additive upgrade has been applied.
INSERT_COLUMNS = tuple(column for column in COLUMNS if column != "expected_leaving_date") + LOCATION_COLUMNS + ("layout_version", "eligible", "ineligible_reason")


def leaving_date_columns(row):
    return ("expected_leaving_date",) if row.get("expected_leaving_date") is not None else ()

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
            start_date_with_employer           date,
            job_title                          text,
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
            country_of_residence              text,
            nationality                       text,
            resident_prev_3_years              boolean,
            years_in_uk                        text,
            full_time_education_prior         boolean,
            expected_leaving_date             date,
            programme                         text,
            attended_funded_training          boolean,
            funded_training_completed_on      date,
            not_previously_completed_programme boolean,
            layout_version                    bigint,
            eligible                          boolean,
            ineligible_reason                 text,
            submitted_at                      timestamptz NOT NULL DEFAULT now(),
            {', '.join(f'{column} text' for column in ADDRESS_COLUMNS)},
            {', '.join(f'{column} {definition}' for column, definition in WORKPLACE_COLUMN_DEFINITIONS + SCREENING_COLUMN_DEFINITIONS)},
            CONSTRAINT eligibility_assessment_consistency CHECK ({ASSESSMENT_CONSISTENCY})
        )"""
    )


def upgrade_table(cursor):
    """Bring a table created before the builder up to date.

    Adds the columns introduced since, and lets every answer the builder can
    remove be NULL. Idempotent. Never drops a column or touches a row.
    """
    for column, sql_type in ADDED_COLUMNS:
        cursor.execute(f"ALTER TABLE enrolment.{TABLE} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
    if workplace_enabled():
        for column, definition in WORKPLACE_COLUMN_DEFINITIONS + SCREENING_COLUMN_DEFINITIONS:
            cursor.execute(f"ALTER TABLE enrolment.{TABLE} ADD COLUMN IF NOT EXISTS {column} {definition}")
        upgrade_assessment_constraints(cursor)
    for key, column, *_rest in FIELDS:
        if key not in LOCKED:
            cursor.execute(f"ALTER TABLE enrolment.{TABLE} ALTER COLUMN {column} DROP NOT NULL")


def upgrade_assessment_constraints(cursor):
    """Replace Phase 2A's reserved-NULL checks without changing historical rows."""
    for name in ("eligibility_assessment_status_reserved", "eligibility_assessment_reason_reserved"):
        cursor.execute(f"ALTER TABLE enrolment.{TABLE} DROP CONSTRAINT IF EXISTS {name}")
    for name, expression in (
        ("eligibility_assessment_status_check", "assessment_status IN ('provisional_eligible', 'pending_assessment', 'not_eligible')"),
        ("eligibility_assessment_reason_length", "char_length(assessment_reason) BETWEEN 1 AND 1000"),
        ("eligibility_assessment_consistency", ASSESSMENT_CONSISTENCY),
    ):
        cursor.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'enrolment.{TABLE}'::regclass AND conname = '{name}') THEN
                ALTER TABLE enrolment.{TABLE} ADD CONSTRAINT {name} CHECK ({expression});
            END IF;
        END $$""")


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
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
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
        if key in FOLLOW_UPS or key in REQUIRED_ADDRESS_FIELDS or key in {"dateOfBirth", "startDateWithEmployer", "jobTitle"}:
            required = True
        if key in ADDRESS_INPUT_FIELDS:
            required = key in REQUIRED_ADDRESS_FIELDS
        elif isinstance(override, bool) and key not in REQUIRED_ADDRESS_FIELDS and key not in LEGACY_ADDRESS_FIELDS and key not in {"dateOfBirth", "startDateWithEmployer", "jobTitle"}:
            required = override
        return {"required": required, "options": limit if kind == "choice" else None}

    if not layout:
        return {key: builtin_entry(key) for key in FIELD_BY_KEY
                if key not in RETIRED_PUBLIC_FIELDS and (workplace_enabled() or key not in WORKPLACE_KEYS)}, []

    from .eligibility_layout import structured_address_layout
    layout = structured_address_layout(layout)
    builtin, custom = {}, []
    for section in layout.get("sections") or []:
        if not isinstance(section, dict) or section.get("hidden"):
            continue
        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if item.get("hidden") and not (item.get("builtin") and key in ALWAYS_VISIBLE_PUBLIC_FIELDS):
                continue
            if item.get("builtin"):
                if key in RETIRED_PUBLIC_ITEMS or key in RETIRED_PUBLIC_FIELDS:
                    continue
                if key in FIELD_BY_KEY:
                    entry = builtin_entry(key, item.get("required"))
                    if entry["options"] is not None and item.get("options"):
                        entry["options"] = tuple(item["options"])
                    builtin[key] = entry
            elif item.get("type") in CUSTOM_SQL_TYPES and CUSTOM_COLUMN_RE.match(str(item.get("column") or "")):
                custom.append(item)
    for key in LOCKED:
        builtin[key] = builtin_entry(key, True)
    # Neither an old layout nor a direct client may omit the age evidence.
    builtin["dateOfBirth"] = builtin_entry("dateOfBirth", True)
    for prefix in ("employer", "residential"):
        if f"{prefix}AddressLine1" in builtin:
            # Accept optional old-client details without ever restoring an input
            # or allowing these details to replace the required town (Line 3).
            builtin[f"{prefix}AddressLine3"] = builtin_entry(f"{prefix}AddressLine3", False)
    if not workplace_enabled():
        for key in WORKPLACE_KEYS:
            builtin.pop(key, None)
    else:
        # Layout edits cannot remove the evidence needed for an automatic pass.
        for key in SCREENING_REQUIRED_FIELDS:
            builtin[key] = builtin_entry(key, True)
    return builtin, custom


def _answer_label(value):
    """An answer as a condition names it: Yes/No for a yes/no question."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value if isinstance(value, str) else None


def screen_custom_rules(row, layout, reasons):
    """Apply published choice rules to validated, server-stored answers only."""
    _builtin, custom = asked_questions(layout)
    passed = True
    for item in custom:
        rule = item.get("screeningRule")
        if not rule:
            continue
        answer = _answer_label(row.get(item["column"]))
        if answer not in rule["eligibleAnswers"]:
            passed = False
            reasons.append(f"Your answer to '{item['label']}' does not meet the additional eligibility requirement.")
    return passed


def _employment_condition_shown(key, row):
    """Whether a paid-employment answer is current for this submission."""
    if key not in EMPLOYMENT_CONDITIONAL_FIELDS:
        return True
    return row.get(FIELD_BY_KEY["employmentStatus"][0]) == "In paid employment"


def _residency_condition_shown(key, row):
    condition = RESIDENCY_CONDITIONAL_FIELDS.get(key)
    if not condition:
        return True
    trigger, expected = condition
    return row.get(FIELD_BY_KEY[trigger][0]) is expected


def clean_application(payload, today=None, layout=None):
    """Validate a submission. Returns (row, errors) keyed by column / payload key.

    Keys outside the form are ignored, so the client cannot choose a column.
    Custom answers arrive under payload["custom"], keyed by question key.
    """
    today = today or application_date()
    asked, custom = asked_questions(layout)
    row, errors = {}, {}
    for key, column, kind, _required, limit in FIELDS:
        question = asked.get(key)
        if question is not None and not _employment_condition_shown(key, row):
            question = None
        if question is not None and not _residency_condition_shown(key, row):
            question = None
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
            value = _clean_field(kind, payload.get(key), limit, date.max if key in {"expectedLeavingDate", "startDateWithEmployer"} else today)
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        if value is None and question["required"]:
            errors[key] = REQUIRED
        row[column] = value

    # Data chronology only: employment start is not apprenticeship start, and
    # these checks do not establish age, immigration or funding eligibility.
    birth = row.get("date_of_birth")
    if birth is not None:
        for key in ("startDateWithEmployer", "expectedLeavingDate", "fundedTrainingCompletedOn"):
            value = row.get(FIELD_BY_KEY[key][0])
            if value is not None and value <= birth:
                errors[key] = "Enter a date after your date of birth."
        age = age_on(birth, today)
        if row.get("resident_prev_3_years") is True and age < 3:
            errors["residentPrev3Years"] = "This answer conflicts with your date of birth. Please check your date of birth and residency history."
        years = row.get("years_in_uk")
        if isinstance(years, str) and re.fullmatch(r"[0-9]+(?: or more)?", years) and int(years.split()[0]) > age:
            errors["yearsInUk"] = "The number of full years in the UK cannot exceed your age. Please check your answers."

    # Do not split, trust or rewrite a client's legacy free-text answer. Compose
    # compatibility values solely from the structured fields validated above.
    for legacy_key, keys in ADDRESS_GROUPS.items():
        values = [row.get(FIELD_BY_KEY[key][0]) for key in keys]
        row[FIELD_BY_KEY[legacy_key][0]] = ", ".join(value for value in values if value) or None

    # The public form is collect-only, but applicants who are not in paid
    # employment must not be able to bypass its Section 2 gate by posting JSON
    # directly. Existing stored applications are untouched; this only applies
    # to new submissions.
    if row.get(FIELD_BY_KEY["employmentStatus"][0]) in EMPLOYMENT_STATUSES[1:]:
        errors["employmentStatus"] = EMPLOYMENT_RESTRICTION_ERROR

    if workplace_enabled():
        # The server owns choices and conditional requiredness, regardless of
        # layout edits. Metadata and the reserved assessment columns are never
        # read from the payload.
        row.update(dict.fromkeys(WORKPLACE_COLUMNS))
        if row.get("employment_status") == "In paid employment":
            try:
                arrangement = _clean_field("choice", payload.get("workplaceArrangement"), WORKPLACE_ARRANGEMENTS, today)
                row["workplace_arrangement"] = arrangement
                if arrangement is None:
                    errors["workplaceArrangement"] = REQUIRED
            except ValueError as exc:
                errors["workplaceArrangement"] = str(exc)
                arrangement = None
            if arrangement in ("different", "remote_hybrid_multiple"):
                try:
                    row["main_workplace_postcode"] = _clean_text(payload.get("mainWorkplacePostcode"), 12)
                    if row["main_workplace_postcode"] is None:
                        errors["mainWorkplacePostcode"] = REQUIRED
                except ValueError as exc:
                    errors["mainWorkplacePostcode"] = str(exc)

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

    Invalid/unknown employer postcodes raise
    ValidationError and an unavailable employer lookup propagates to the caller.
    Each postcode found is saved as written, with its location. A supplied UK
    residential postcode that is malformed or cannot be found is a field
    error. Country-based rejection applies only to legacy
    screening; enabled screening assesses effective workplace country later.
    Returns None for any unchecked lookup, otherwise False when there is a
    reason, or True for a preliminary postcode screening pass.
    This does not establish residency, immigration or overall funding eligibility.
    """
    reasons, unchecked = [], False
    for key, (column, country, region, district) in POSTCODES.items():
        row[country] = row[region] = row[district] = None
        value = row.get(column)
        if not value:
            continue
        # A postcode is a UK location check only for a UK country of residence.
        # Overseas applicants may provide a local postal code without being
        # rejected or looked up as though it were a UK postcode.
        if key == "postcode" and row.get("country_of_residence") not in (None, "", "United Kingdom"):
            continue
        name = POSTCODE_NAMES[key]
        if not postcode_lookup.is_valid(value):
            raise ValidationError({key: postcode_lookup.INVALID_POSTCODE})
        try:
            found = postcode_lookup.lookup(value)
        except postcode_lookup.PostcodeLookupUnavailable as exc:
            logger.warning("Could not check a postcode on an eligibility application: %s", exc)
            if key == "employerPostcode":
                raise
            unchecked = True
            continue
        if found is None:
            raise ValidationError({key: postcode_lookup.UNKNOWN_POSTCODE})
        row[column] = found["postcode"]
        row[country] = found["country"] or None
        row[region] = found["region"] or None
        row[district] = found["district"] or None
        # Residential address is not the funding workplace, and an employer's
        # company address need not be the actual workplace. Preserve both
        # verified locations, but screen the effective workplace separately.
        if found["country"] != "England" and not workplace_enabled():
            where = f" ({found['country']})" if found["country"] else ""
            reasons.append(f"{name} ({found['postcode']}) is outside England{where}.")
    if unchecked:
        return None, reasons
    if reasons:
        return False, reasons
    return True, []


def locate_workplace(row):
    """Collect verified effective-workplace geography, independent of screening."""
    if row.get("workplace_arrangement") == "same":
        row["main_workplace_postcode"] = row.get("employer_postcode")
        for key in ("country", "region", "district"):
            row[f"main_workplace_{key}"] = row.get(f"employer_postcode_{key}")
    else:
        value = row.get("main_workplace_postcode")
        if not value:
            raise ValidationError(REQUIRED)
        if not postcode_lookup.is_valid(value):
            raise ValidationError(postcode_lookup.INVALID_POSTCODE)
        found = postcode_lookup.lookup(value)
        if found is None:
            raise ValidationError(postcode_lookup.UNKNOWN_POSTCODE)
        row["main_workplace_postcode"] = found["postcode"]
        for key in ("country", "region", "district"):
            row[f"main_workplace_{key}"] = found[key] or None
    if row.get("main_workplace_country") not in {"England", "Wales", "Scotland", "Northern Ireland"}:
        raise postcode_lookup.PostcodeLookupUnavailable("The effective workplace country could not be verified.")


def screen_workplace(row, other_eligible, reasons):
    """Two initial-screening results; this is not a final funding decision."""
    if row.get("majority_hours_in_england") is False:
        reasons.append(WORKING_HOURS_SCREENING_REASON)
    if row.get("main_workplace_country") != "England":
        reasons.append(f"Your verified usual workplace ({row.get('main_workplace_postcode')}) is outside England ({row.get('main_workplace_country')}), so it does not currently meet the standard initial screening requirement.")
    if reasons or other_eligible is False:
        row["assessment_status"] = NOT_ELIGIBLE
        row["assessment_reason"] = " ".join(reasons)
        return False
    row["assessment_status"] = PROVISIONAL_ELIGIBLE
    row["assessment_reason"] = None
    return True


def submission_fingerprint(row, version, today=None):
    """Hash canonical answers, not client flags or changing lookup metadata."""
    answers = {column: row.get(column) for column in COLUMNS + ("workplace_arrangement", "main_workplace_postcode")}
    # Preserve pre-existing fingerprints when the new optional answer is blank.
    for column in ADDRESS_COLUMNS:
        if answers.get(column) is None:
            answers.pop(column, None)
    if answers.get("expected_leaving_date") is None:
        answers.pop("expected_leaving_date", None)
    answers.update({key: value for key, value in row.items() if CUSTOM_COLUMN_RE.match(key)})
    for key in ("postcode", "employer_postcode", "main_workplace_postcode"):
        if answers.get(key):
            answers[key] = postcode_lookup.compact(answers[key])
    answers["work_email"] = str(answers.get("work_email") or "").casefold()
    body = {"screeningVersion": "2b-1", "layoutVersion": version, "answers": answers}
    # Isolate new under-16 decisions from pre-policy receipts without altering
    # historical hashes/rows or adult retry identities. Turning 16 permits a
    # new screening rather than replaying the earlier under-age result.
    birth = row.get("date_of_birth")
    if birth is not None and age_on(birth, today or application_date()) < MINIMUM_APPLICATION_AGE:
        body["applicationAgePolicy"] = "under16-v1"
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def screening_response(row, reasons, request, application_id=None):
    # Historical pending receipts return earlier, unchanged. New responses must
    # never turn missing evidence into a saved third screening result.
    if type(row["eligible"]) is not bool or (workplace_enabled() and row.get("assessment_status") not in {PROVISIONAL_ELIGIBLE, NOT_ELIGIBLE}):
        raise DatabaseError("Incomplete initial eligibility screening")
    if row["eligible"] is True and (not workplace_enabled() or row.get("assessment_status") == PROVISIONAL_ELIGIBLE):
        outcome = eligibility_invitation.invite_applicant(row, application_id=application_id, ip=client_ip(request), user_agent=user_agent(request))
    else:
        outcome = "notEligible"
    response = {"submitted": True, "eligible": row["eligible"], "reasons": reasons, "outcome": outcome}
    if workplace_enabled():
        response.update(assessmentStatus=row['assessment_status'], assessmentReason=row['assessment_reason'])
    return response


def save_screened_application(row, reasons, request):
    """A unique fingerprint and one transaction serialize identical submissions.

    Only a new row's response receipt is updated; historical applications never
    acquire a fingerprint. Invitation failures may retry against the same row.
    """
    custom = [column for column in row if CUSTOM_COLUMN_RE.match(column)]
    columns = list(INSERT_COLUMNS + (WORKPLACE_COLUMNS if workplace_enabled() else ()) + SCREENING_COLUMNS + leaving_date_columns(row)) + custom
    names = [f'"{column}"' for column in columns]
    with transaction.atomic(using=CONN):
        with connections[CONN].cursor() as cursor:
            cursor.execute(
                f"INSERT INTO enrolment.{TABLE} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) "
                "ON CONFLICT (submission_fingerprint) DO NOTHING RETURNING id",
                [row.get(column) for column in columns],
            )
            inserted = cursor.fetchone()
            if inserted:
                application_id = inserted[0]
            else:
                cursor.execute(f"SELECT id, eligible, assessment_status, submission_response FROM enrolment.{TABLE} a WHERE submission_fingerprint = %s FOR UPDATE",
                               [row["submission_fingerprint"]])
                stored = cursor.fetchone()
                receipt = stored[3] if stored else None
                if isinstance(receipt, str):
                    try:
                        receipt = json.loads(receipt)
                    except ValueError:
                        receipt = None
                if not isinstance(receipt, dict):
                    raise DatabaseError("Submission receipt is unavailable")
                if receipt.get('outcome') == eligibility_invitation.INVITE_PENDING:
                    from .eligibility_link import dispatch, refresh_response
                    def retry_delivery():
                        dispatch(stored[0], ip=client_ip(request), user_agent=user_agent(request))
                        receipt.update(refresh_response(stored[0], receipt))
                    transaction.on_commit(retry_delivery, using=CONN)
                if not (receipt.get("outcome") == eligibility_invitation.RETRY_LATER
                        and stored[1] is True and stored[2] == row.get('assessment_status')
                        and row['eligible'] is True):
                    return receipt
                application_id = stored[0]
            response = screening_response(row, reasons, request, application_id)
            cursor.execute(f"UPDATE enrolment.{TABLE} SET submission_response = %s::jsonb WHERE id = %s",
                           [json.dumps(response), application_id])
    if response.get('outcome') == eligibility_invitation.INVITE_PENDING:
        from .eligibility_link import refresh_response
        return refresh_response(application_id, response)
    return response


def _published_layout():
    """(version, layout) in force, or (None, None) while nothing is published."""
    # Imported here: the layout module builds on this one.
    from .eligibility_layout import read_current

    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
    if not current:
        return None, None
    # Apply the same legacy-layout relocation as the public layout endpoint;
    # this keeps validation aligned with what the applicant actually saw.
    from .eligibility_layout import public_layout
    return current[0], public_layout(current[1], include_screening_rules=True)


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

    today = application_date()
    row, errors = clean_application(payload, today=today, layout=layout)
    if errors:
        return _error("Please correct the highlighted answers.", 400, fields=errors)
    fingerprint = submission_fingerprint(row, version, today=today)
    postcode_field = "employerPostcode"
    try:
        eligible, reasons = locate_postcodes(row)
        if workplace_enabled():
            postcode_field = "employerPostcode" if row.get("workplace_arrangement") == "same" else "mainWorkplacePostcode"
            locate_workplace(row)
    except ValidationError as exc:
        return _error("Please correct the highlighted answers.", 400,
                      fields={key: messages[0] for key, messages in exc.message_dict.items()}
                      if hasattr(exc, "message_dict") else {postcode_field: exc.messages[0]})
    except postcode_lookup.PostcodeLookupUnavailable:
        message = WORKPLACE_POSTCODE_UNAVAILABLE if postcode_field == "mainWorkplacePostcode" else EMPLOYER_POSTCODE_UNAVAILABLE
        return _error(message, 503, fields={postcode_field: message})
    if eligible is None:
        return _error("We could not complete the postcode checks just now. Please try again.", 503)
    # Approved business screening policy, not an official funding-age rule.
    # DOB is validated above; client age/eligible metadata is never consulted.
    if age_on(row["date_of_birth"], today) < MINIMUM_APPLICATION_AGE:
        eligible = False
        reasons.append(APPLICATION_AGE_REASON)
    if workplace_enabled():
        eligible = screen_workplace(row, eligible, reasons)
    elif row.get("majority_hours_in_england") is False:
        eligible = False
        reasons.append(WORKING_HOURS_SCREENING_REASON)
    if not screen_custom_rules(row, layout, reasons):
        eligible = False
    row["layout_version"] = version
    row["eligible"] = eligible
    row["ineligible_reason"] = " ".join(reasons) if eligible is False else None
    row["submission_fingerprint"] = fingerprint
    row["submission_response"] = None
    try:
        return JsonResponse(save_screened_application(row, reasons, request), status=201)
    except DatabaseError:
        logger.exception("Could not save a screened apprenticeship application")
        return _error(UNAVAILABLE, 503)
