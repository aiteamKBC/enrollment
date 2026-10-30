"""Map the flat/JSON EnrolmentUser row to the shapes the frontend expects.

Two output shapes:
  * to_list_row  -> UserListRow (Users List screen)
  * to_board     -> EnrolmentBoard (read-only details page)

And two inbound helpers:
  * write_fields -> validates + returns kwargs for create/update (flat columns)
  * validate_choices -> enforces the canonical option lists
"""
from datetime import date
import re

from .student_activity_access import student_activity_available
from .aptem_status import programme_status
from .constants import (
    ACCESS_CHOICES,
    ACCESS_SUPER_ADMIN,
    STATUS_CHOICES,
    TYPE_CHOICES,
    PROGRAMME_STATUS_CHOICES,
    DEFAULT_PROGRAMME_STATUS,
    POSITION_CHOICES,
    LEARNER_TYPE_CHOICES,
    ATTENDANCE_TYPE_CHOICES,
    ORGANISATION_STATUS_CHOICES,
    ORGANISATION_GROUP_TYPE_CHOICES,
    LEVY_PAYER_CHOICES,
    HEALTH_SAFETY_CHOICES,
)


# --------------------------------------------------------------------------- #
# small defensive helpers                                                      #
# --------------------------------------------------------------------------- #
def _s(value):
    """Coerce to a trimmed string ('' for None)."""
    return "" if value is None else str(value).strip()


def _as_list(value):
    return value if isinstance(value, list) else []


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _pick(d, *keys, default=""):
    """First present key from a dict, tolerant of camel/snake variants."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _maybe_json(value):
    """A TEXT column that might hold JSON. Returns parsed value or None."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


# --------------------------------------------------------------------------- #
# outbound: list row                                                           #
# --------------------------------------------------------------------------- #
def to_list_row(u):
    status = _s(u.status)
    utype = _s(u.type) or "User"
    has_plan = getattr(u, "_directory_has_learning_plan", None)
    if has_plan is None:
        # Create/update responses still pass a fully loaded learner. Directory
        # reads supply the boolean without fetching either deferred JSON field.
        has_plan = bool(u.learning_plan) or bool(u.training_plan)
    return {
        "id": str(u.id),
        # Permanent identifier, stable across the enrolment/active phases.
        # "id" stays the integer the existing routes are built on.
        "uuid": str(u.uuid) if getattr(u, "uuid", None) else None,
        "name": _s(u.username),
        "type": utype,
        "email": _s(u.email),
        "group": _s(u.group),
        "subscriptionStatus": status,
        "subscriptionVerified": status.lower() == "fulluser",
        # True for every row this mapper produces, which are all learner rows.
        # It used to read `utype == "User"`, but Type is an editable label on the
        # row (User, Referrer, Caseowner...) — so relabelling a learner took away
        # their learning plan. What the row *is* comes from which table it came
        # from, not from a column somebody can type over.
        "learningPlan": True,
        # Whether a plan has actually been saved, so the users table can offer
        # "Add" vs "Edit" without fetching every learner's plan.
        "hasLearningPlan": has_plan,
        "programmeStatus": programme_status(u),
        # The programme itself, not just its status — the directory shows both.
        # Staff and employer rows have no programme, so their mappers leave it
        # absent and the table renders a dash.
        "programme": _s(u.programme),
        # The cohort too, so the directory can offer a programme -> cohort ->
        # group filter cascade. Absent on staff/employer rows, like programme.
        "cohort": _s(u.cohort),
        # Who owns this learner's case. The directory's Case owner filter builds
        # its list from the values actually present here rather than a fixed
        # roster, so an owner who leaves or joins needs no code change.
        "caseOwner": _s(getattr(u, "case_owner", "")),
        "notesCount": 0,
        # Same reasoning as learningPlan above.
        "hasTasks": True,
        "reference": _s(u.organization),
        # Which kind of learner this is. Rows predating the Commercial_users merge
        # have no value and are apprenticeship.
        "learnerType": _s(getattr(u, "learner_type", "")) or "apprenticeship",
        # Kept so the directory's existing source-based row routing keeps working
        # while both kinds share one table.
        "source": "commercial" if _s(getattr(u, "learner_type", "")) == "commercial" else "apprenticeship",
    }


def to_edit_fields(u):
    """The flat, editable columns of a learner row — the edit form's prefill.

    Deliberately not part of ``to_board``: the board GET is not staff-gated
    (``@staff_only(writes_only=True)``), and these columns include a national
    insurance number and a home address. This is served by its own staff-only
    endpoint instead, so widening the form never widens who can read it.

    The keys are ``WRITABLE_FIELDS`` keys, so whatever comes out of here can be
    handed straight back to PATCH without a translation layer in between.
    """
    return {
        "id": str(u.id),
        "username": _s(u.username),
        "email": _s(u.email),
        "phone": _s(u.phone_number),
        "dob": _s(u.date_of_birth),
        "type": _s(u.type) or "User",
        "status": _s(u.status),
        "programmeStatus": programme_status(u),
        "programme": _s(u.programme),
        "cohort": _s(u.cohort),
        "group": _s(u.group),
        "employer": _s(u.employer),
        # The record id alongside the name: the form's employer select is keyed
        # on it, because two employers can share a name.
        "employerId": u.employer_id,
        "organization": _s(u.organization),
        "lineManager": _s(u.line_manager),
        "learnerType": _s(getattr(u, "learner_type", "")) or "apprenticeship",
        **{key: _s(getattr(u, attr)) for key, attr in APTEM_TEXT_FIELDS.items()},
        **{key: getattr(u, attr) for key, attr in APTEM_BOOL_FIELDS.items()},
    }


# --------------------------------------------------------------------------- #
# outbound: full board                                                         #
# --------------------------------------------------------------------------- #
def _fs_block(assessments, exemption):
    exempt = False
    evidence = []
    ex = _as_dict(exemption)
    if ex:
        exempt = bool(ex.get("exempt"))
        evidence = _as_list(ex.get("evidence"))
    elif isinstance(exemption, bool):
        exempt = exemption
    return {"assessments": _as_list(assessments), "exempt": exempt, "evidence": evidence}


def _split_enrolled(value):
    """'31/10/2025 18:24:17 by Ayman Badewi' -> ('31/10/2025 18:24:17', 'Ayman Badewi')."""
    text = _s(value)
    if " by " in text:
        at, _, by = text.partition(" by ")
        return at.strip(), by.strip()
    return text, ""


def _review_groups(u):
    """Reviews / Review_documents -> [{programme, reviews:[...]}]."""
    raw = u.review_documents if u.review_documents is not None else u.reviews
    items = _as_list(raw)
    if not items:
        return []
    # Already grouped? (each item has a 'reviews' array)
    if all(isinstance(i, dict) and "reviews" in i for i in items):
        return [
            {"programme": _s(_pick(g, "programme")), "reviews": _as_list(g.get("reviews"))}
            for g in items
        ]
    # Flat list of reviews -> wrap under the user's programme.
    return [{"programme": _s(u.programme), "reviews": items}]


def _competencies(u):
    parsed = _maybe_json(u.competencies)
    items = _as_list(parsed)
    out = []
    for i, c in enumerate(items):
        d = _as_dict(c)
        out.append(
            {
                "id": _s(_pick(d, "id", default=f"comp-{i}")) or f"comp-{i}",
                "name": _s(_pick(d, "name")),
                "version": _s(_pick(d, "version", default="v1.0")) or "v1.0",
            }
        )
    return out


def _subscription(u):
    d = _as_dict(u.subscription_details)
    return {
        "startDate": _s(_pick(d, "startDate", "start_date")),
        "endDate": _s(_pick(d, "endDate", "end_date")),
        "status": _s(_pick(d, "status", default=u.status)),
    }


def _employer_label(u):
    """(display name, id) for the learner's employer.

    Prefers the live name from enrolment."Employers" when "Employer_id" is set:
    the learner's own "Employer" text is a snapshot typed at create time, so a
    renamed employer would otherwise keep showing the old name.

    Falls back to that text for learners created before employer profiles existed
    (their id is NULL), which is most historical rows. Never raises — a broken
    employer lookup must not fail the whole board.
    """
    employer_id = getattr(u, "employer_id", None)
    fallback = _s(getattr(u, "employer", ""))
    if employer_id is None:
        return fallback, None

    # Imported here for the same circularity reason as the board_wizard import
    # below.
    from django.db import DatabaseError

    from .models import Employer

    try:
        employer = Employer.objects.filter(pk=employer_id).only(
            "first_name", "surname"
        ).first()
    except DatabaseError:
        return fallback, employer_id
    if employer is None:
        # A dangling id — the API blocks writing one, but a row could predate
        # that check or have had its employer deleted.
        return fallback, employer_id
    return employer.full_name or fallback, employer_id


def to_board(u):
    # Imported here, not at module scope: enrolment_api.models imports from
    # learner_api.models, so a top-level import would be circular.
    from .board_wizard import fmt_date, merge_wizard_sections, record_address, saved_signature

    enrolled_at, enrolled_by = _split_enrolled(u.enrolled_time_and_user)
    saved_signature_value, saved_signature_date = saved_signature(u.id)
    employer_name, employer_id = _employer_label(u)
    board = {
        "user": {
            "id": str(u.id),
            # The learner's permanent identifier, the same value before and after
            # they become active. "id" above stays the enrolment integer because
            # existing callers and routes are built on it; prefer this for
            # anything new that needs to name a learner.
            "uuid": str(u.uuid) if getattr(u, "uuid", None) else None,
            "name": _s(u.username),
            "reference": _s(u.organization),
            # The case owner is chosen on the create form (picked from the
            # Caseowner/Admin staff in Staff_users) and is this learner's owner
            # and coach. Falls back to whoever enrolled them, then the line
            # manager, for rows created before that field existed.
            "owner": _s(u.case_owner) or enrolled_by or _s(u.line_manager),
            # Resolved from "Employer_id" where set, so a renamed employer shows
            # its current name rather than the stale string on the learner row.
            "employer": employer_name,
            "employerId": employer_id,
        },
        "contact": {
            "email": _s(u.email),
            "phone": _s(u.phone_number),
            "dob": fmt_date(u.date_of_birth),
            "groupMembership": _s(u.group),
            "signatureUrl": None,
            "hasMandate": False,
            # What the learner already gave on their record (first sign-in, or
            # the create form), so the enrolment wizard can fill its blanks
            # rather than ask again. See WizardContext.withRecordDefaults.
            "address": record_address(u),
            "savedSignature": saved_signature_value,
            "savedSignatureDate": saved_signature_date,
        },
        "activity": {
            "aptemUsage": "00:00",
            "daysTillNextReporting": 0,
            "lastLoggedIn": None,
            "logins": 0,
            "tasksAddedByUser": 0,
            "uncompletedTasks": 0,
            "adviceItemsAccessed": 0,
            "adviceLastAccessed": None,
            "actionPlans": "No plans created",
        },
        "programme": {
            "type": "Delivery",
            "name": _s(u.programme),
            "cohort": _s(u.cohort),
            "status": programme_status(u) or DEFAULT_PROGRAMME_STATUS,
            # Cohort dates, matched from curriculum.cohort_authoring_details.
            # apprenticeship_end_date is the learner's own override and only
            # applies when it's set, so the cohort end date is the fallback.
            # Formatted to match enrolledAt and the DOB row above — the panel
            # renders these as plain strings and mixing ISO in reads as a bug.
            "learnerStartDate": _s(getattr(u, "learner_start_date", None)),
            "learnerEndDate": _s(getattr(u, "learner_end_date", None)),
            "startDate": fmt_date(u.start_date),
            "endDate": fmt_date(u.apprenticeship_end_date) or fmt_date(u.end_date),
            "enrolledAt": enrolled_at,
            "enrolledBy": enrolled_by,
            "onboardingStatus": _s(u.onboarding_status) or "Not started",
            "onboardingCompletedAt": _s(u.onboarding_completed) or None,
        },
        "subProgrammes": _as_list(u.sub_programme),
        "aims": _as_list(u.aims_qualifications),
        "previousProgrammes": [],
        "functionalSkills": {
            "english": _fs_block(u.english_assessments, u.english_exemption),
            "maths": _fs_block(u.maths_assessments, u.maths_exemption),
            "ict": _fs_block(u.ict_assessments, u.ict_exemption),
        },
        "managedJobs": _as_list(_maybe_json(u.managed_jobs)),
        "tracker": _as_list(u.tracker),
        "milestones": _as_list(u.milestones),
        "notes": [],
        "courseProgress": [],
        "contacts": _as_list(u.contacts),
        "activities": _as_list(u.activity),
        "complianceDocuments": _as_list(u.compliance_documents),
        "reviewDocuments": _review_groups(u),
        "documents": _as_list(u.documents),
        "competencies": _competencies(u),
        "subscription": _subscription(u),
        "auditTrail": [],
        "trainingPlan": _as_list(u.learning_plan),
    }
    # For a learner who came in through the enrolment wizard the JSON columns
    # above are all NULL, but the same facts were captured during onboarding.
    # Fill the empty sections from the Extended_ILR / Wizard_* tables.
    return merge_wizard_sections(board, u.id)


# --------------------------------------------------------------------------- #
# inbound: create / update                                                     #
# --------------------------------------------------------------------------- #
# The Aptem "Add user" fields, shared by both learner tables (the create form is
# the same for apprenticeship and commercial learners). Text columns only; the
# four boolean access flags are handled separately by APTEM_BOOL_FIELDS.
APTEM_TEXT_FIELDS = {
    "title": "title",
    "preferredName": "preferred_name",
    "gender": "gender",
    "referrer": "referrer",
    "referrerAddress": "referrer_address",
    "referrerContact": "referrer_contact",
    "country": "country",
    "caseOwner": "case_owner",
    "learningProvider": "learning_provider",
    "mentor": "mentor",
    "referenceNumber": "reference_number",
    "extendedBreak": "extended_break",
    "attendanceType": "attendance_type",
    "employerAddress": "employer_address",
    "targetProgramme": "target_programme",
    "legalSex": "legal_sex",
    "age": "age",
    "address": "address",
    "postcode": "current_postcode",
    "addressLine1": "address_line_1",
    "addressLine2": "address_line_2",
    "townCity": "address_line_3",
    "county": "address_line_4",
    "niNumber": "national_insurance_number",
}

# Checkbox / radio fields stored as real booleans.
APTEM_BOOL_FIELDS = {
    "inviteToPlatform": "invite_to_platform",
    "allowCheckpoint": "allow_access_to_checkpoint",
    "allowConsole": "allow_access_to_console",
    "allowClassic": "allow_access_to_classic",
}


def _bool_or_none(value):
    """Coerce a JSON checkbox value to a real bool, or None to clear it."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


# payload key -> model attribute (flat text columns only)
WRITABLE_FIELDS = {
    "username": "username",
    "email": "email",
    "phone": "phone_number",
    "dob": "date_of_birth",
    "type": "type",
    "status": "status",
    "learnerStartDate": "learner_start_date",
    "learnerEndDate": "learner_end_date",
    "programmeStatus": "programme_status",
    "programme": "programme",
    "cohort": "cohort",
    "group": "group",
    "employer": "employer",
    "organization": "organization",
    "reference": "organization",
    "lineManager": "line_manager",
    "onboardingStatus": "onboarding_status",
    "onboardingCompleted": "onboarding_completed",
    # Which kind of learner the row is. Both kinds share one table, so this is
    # what the create form's learner-type switch writes.
    "learnerType": "learner_type",
    **APTEM_TEXT_FIELDS,
}


#: The only keys a **learner** may write about themselves, used when the
#: onboarding wizard saves on their own behalf (see
#: ``login.permissions.staff_only(allow_own_learner=...)``).
#:
#: An allowlist rather than a denylist, because WRITABLE_FIELDS above is where
#: the danger is: it maps ``programmeStatus``, ``programme``, ``cohort``,
#: ``employer`` and ``status``. A learner permitted to PATCH their own row
#: unrestricted could send ``programmeStatus: "Active"`` and promote themselves
#: straight past enrolment — skipping the wizard they are sitting in and the
#: three countersigned reviews that are the only legitimate route to Delivery.
#: Owning the record is not the same as being allowed to set every field on it.
#:
#: ``email`` is deliberately absent. It is the learner's sign-in identity —
#: ``login.services.sync_account`` propagates a change to their login account —
#: so a correction there is a staff action, not self-service.
LEARNER_SELF_WRITABLE_KEYS = frozenset({
    "username",
    "phone",
    "dob",
    # 'Submitted', set by the wizard's Finish. Distinct from programmeStatus:
    # it records that the learner handed their enrolment in, not that anyone
    # has accepted it.
    "onboardingStatus",
})


def restrict_to_self_writable(payload):
    """Drop keys a learner may not write about themselves.

    Returns ``(allowed_payload, rejected_keys)``. Stripping rather than
    rejecting the request keeps the wizard working when it sends a field it has
    always sent; the caller logs what was dropped.
    """
    allowed = {k: v for k, v in payload.items() if k in LEARNER_SELF_WRITABLE_KEYS}
    rejected = sorted(k for k in payload if k not in LEARNER_SELF_WRITABLE_KEYS)
    return allowed, rejected


# --------------------------------------------------------------------------- #
# structured training plan (shared by apprenticeship + delivery learners)     #
# --------------------------------------------------------------------------- #
# Shape: [{moduleId, moduleTitle, weeks: [{weekId, weekTitle,
#          components: [{componentId, componentTitle}]}]}, ...]
# Written wholesale by the training-plan wizard (frontend owns the shape);
# ids are curriculum authoring table primary keys, so downstream lookups
# (KSBs, expected_otjh) can match exactly instead of by title.
def _normalize_training_plan(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("trainingPlan must be a list.")
    return value


# Free courses assigned to a learner. A pure assignment record, kept separate
# from the training plan on purpose: it carries no hours, KSBs or progress, so
# only the free-course id and its display name are stored. `addedAt` is passed
# through when the client supplies it (the wizard stamps it on add).
def _normalize_free_courses(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("freeCourses must be a list.")
    out = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        free_course_id = _s(item.get("freeCourseId"))
        if not free_course_id or free_course_id in seen:
            continue
        seen.add(free_course_id)
        out.append({
            "freeCourseId": free_course_id,
            "courseName": _s(item.get("courseName")),
            "addedAt": _s(item.get("addedAt")),
        })
    return out


def flatten_training_plan(plan):
    """Structured plan -> (module titles, week entries, component entries),
    each entry carrying its curriculum id alongside its title for exact
    downstream lookups (KSBs, expected_otjh) instead of fragile title matching.
    """
    modules, weeks, components = [], [], []
    for m in _as_list(plan):
        if not isinstance(m, dict):
            continue
        module_title = _s(m.get("moduleTitle"))
        module_id = m.get("moduleId")
        if module_title:
            modules.append(module_title)
        for w in _as_list(m.get("weeks")):
            if not isinstance(w, dict):
                continue
            week_title = _s(w.get("weekTitle"))
            week_id = w.get("weekId")
            weeks.append({
                "module": module_title or None, "week": week_title,
                "moduleId": module_id, "weekId": week_id,
            })
            for c in _as_list(w.get("components")):
                if not isinstance(c, dict):
                    continue
                components.append({
                    "module": module_title or None, "week": week_title or None,
                    "component": _s(c.get("componentTitle")),
                    "moduleId": module_id, "weekId": week_id, "componentId": c.get("componentId"),
                })
    return modules, weeks, components


def _split_csv(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _legacy_plan_from_csv(source):
    """Best-effort reconstruction of the structured plan shape from the old
    comma-joined Modules/Weeks/Components text columns, for learners who
    haven't re-saved their plan since the structured format was introduced.
    No ids are available for this data, so expectedOtjh/KSB lookups downstream
    fall back to title matching for these entries only."""
    modules_csv = getattr(source, "modules", None)
    weeks_csv = getattr(source, "weeks", None)
    components_csv = getattr(source, "components", None)
    if not (modules_csv or weeks_csv or components_csv):
        return []

    module_titles = _split_csv(modules_csv)

    week_entries = []  # (module_title|None, week_title)
    for entry in _split_csv(weeks_csv):
        parts = [p.strip() for p in entry.split("·")]
        if len(parts) >= 2:
            week_entries.append((parts[0], " · ".join(parts[1:])))
        elif parts and parts[0]:
            week_entries.append((None, parts[0]))

    component_entries = []  # (module_title|None, week_title|None, component_title)
    for entry in _split_csv(components_csv):
        parts = [p.strip() for p in entry.split("·")]
        if len(parts) >= 3:
            component_entries.append((parts[0], parts[1], " · ".join(parts[2:])))
        elif len(parts) == 2:
            component_entries.append((None, parts[0], parts[1]))
        elif parts and parts[0]:
            component_entries.append((None, None, parts[0]))

    plan = []
    for module_title in module_titles:
        weeks = []
        for mod, week_title in week_entries:
            if mod != module_title:
                continue
            comps = [
                {"componentId": None, "componentTitle": comp_title}
                for cmod, cweek, comp_title in component_entries
                if cmod == module_title and cweek == week_title
            ]
            weeks.append({"weekId": None, "weekTitle": week_title, "components": comps})
        plan.append({"moduleId": None, "moduleTitle": module_title, "weeks": weeks})
    return plan


def training_plan_field(source):
    """Use the same source column for assignment reads and writes, including []."""
    return "training_plan" if isinstance(_maybe_json(getattr(source, "training_plan", None)), list) else "learning_plan"


def stored_training_plan(source):
    """None means unsaved; an empty array is an explicitly cleared assignment."""
    for field in ("training_plan", "learning_plan"):
        plan = _maybe_json(getattr(source, field, None))
        if isinstance(plan, list):
            return [entry for entry in plan if isinstance(entry, dict)]
    return None


def get_training_plan(source):
    """The structured plan for a CommercialUser or EnrolmentUser instance.
    Falls back to reconstructing one from the legacy CSV columns if the
    learner hasn't been re-saved since the structured format was introduced."""
    plan = stored_training_plan(source)
    if plan is not None:
        return plan
    return _legacy_plan_from_csv(source)


class ValidationError(Exception):
    """Raised with a user-facing message when a payload fails validation."""


def validate_choices(payload):
    checks = (
        ("status", STATUS_CHOICES),
        ("type", TYPE_CHOICES),
        ("programmeStatus", PROGRAMME_STATUS_CHOICES),
        ("learnerType", LEARNER_TYPE_CHOICES),
        ("attendanceType", ATTENDANCE_TYPE_CHOICES),
    )
    for key, allowed in checks:
        val = payload.get(key)
        if val not in (None, "") and val not in allowed:
            raise ValidationError(f"Invalid {key}: {val!r}. Allowed: {', '.join(allowed)}")


def _employer_id_field(payload):
    """{'employer_id': int|None} for a payload carrying "employerId", else {}.

    An integer column, so it bypasses the string-coercing loops below. Existence
    of the referenced employer is checked by the caller (it needs a DB read) —
    see _resolve_employer in views.py.
    """
    if "employerId" not in payload:
        return {}
    raw = payload["employerId"]
    if raw in (None, ""):
        return {"employer_id": None}
    try:
        return {"employer_id": int(str(raw).strip())}
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid employerId: {raw!r}. Expected a whole number.")


def validate_learner_dates(fields, existing=None):
    """Validate ISO text dates, including the stored half of a partial update."""
    keys = ("learner_start_date", "learner_end_date")
    if not any(key in fields for key in keys):
        return
    for key in keys:
        if key not in fields:
            continue
        value = fields[key]
        if value in (None, ""):
            fields[key] = None
            continue
        try:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise ValidationError("Start date and end date must be valid dates in YYYY-MM-DD format.")
    start, end = (fields.get(key, getattr(existing, key, None)) for key in keys)
    if start and end and start > end:
        raise ValidationError("End date must be on or after start date.")


def _blank_attendance_type_to_null(fields):
    """A cleared Attendance type is NULL, not ''.

    The column's CHECK constraint admits only NULL or one of
    ATTENDANCE_TYPE_CHOICES, so the empty string a cleared select sends would
    otherwise fail at the database instead of clearing the value.
    """
    if fields.get("attendance_type") == "":
        fields["attendance_type"] = None


def write_fields(payload, *, require_create=False):
    """Validate a payload and return {model_attr: value} for the flat columns."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    validate_choices(payload)
    if require_create:
        if not _s(payload.get("username")):
            raise ValidationError("username is required.")
        if not _s(payload.get("email")):
            raise ValidationError("email is required.")
    fields = {}
    if require_create and not _s(payload.get("learnerType")):
        # Never leave a new row untagged: an untyped learner would be invisible
        # to the commercial manager and only reachable via all_learners.
        fields["learner_type"] = "apprenticeship"
    for key, attr in WRITABLE_FIELDS.items():
        if key in payload:
            val = payload[key]
            fields[attr] = None if val is None else str(val).strip()
    for key, attr in APTEM_BOOL_FIELDS.items():
        if key in payload:
            fields[attr] = _bool_or_none(payload[key])
    if "trainingPlan" in payload:
        fields["learning_plan"] = _normalize_training_plan(payload["trainingPlan"])
    fields.update(_employer_id_field(payload))
    _blank_attendance_type_to_null(fields)
    if require_create and not _s(fields.get("programme_status")):
        # Every account starts at 'Fresh user'. Left unset it was stored NULL,
        # which some readers took for "status unknown" rather than new.
        fields["programme_status"] = DEFAULT_PROGRAMME_STATUS
    validate_learner_dates(fields)
    return fields


# --------------------------------------------------------------------------- #
# delivery (commercial rows of enrolment."Created_users")                      #
# --------------------------------------------------------------------------- #
# payload key -> model attribute
COMMERCIAL_WRITABLE_FIELDS = {
    "username": "username",
    "email": "email",
    "phone": "phone_number",
    "employer": "employer",
    "lineManager": "line_manager",
    "organization": "organization",
    "programmeStatus": "programme_status",
    "programme": "programme",
    "cohort": "cohort",
    "group": "group",
    "modules": "modules",
    "weeks": "weeks",
    "components": "components",
    # Same Aptem create form backs this table, so it takes the same fields.
    # `dob`/`type`/`status` are listed explicitly because the apprenticeship map
    # above reaches them through columns this table only gained recently.
    "dob": "date_of_birth",
    "type": "type",
    "status": "status",
    **APTEM_TEXT_FIELDS,
}


def to_commercial_row(u):
    return {
        "id": str(u.id),
        "uuid": str(u.uuid) if getattr(u, "uuid", None) else None,
        "username": _s(u.username),
        "email": _s(u.email),
        "phone": _s(u.phone_number),
        "employer": _s(u.employer),
        "lineManager": _s(u.line_manager),
        "organization": _s(u.organization),
        # The employer's record id, so the caller can fetch its full details.
        "employerId": u.employer_id,
        "programmeStatus": programme_status(u),
        "programme": _s(u.programme),
        "cohort": _s(u.cohort),
        "group": _s(u.group),
        "modules": _s(u.modules),
        "weeks": _s(u.weeks),
        "components": _s(u.components),
        # Resolved rather than read straight off Training_plan: an
        # apprenticeship learner's plan is stored in Learning_plan and a
        # commercial learner's in Training_plan (see stored_training_plan), so
        # reading one column reported "no plan" for every learner whose plan
        # was in the other -- while the Modules text column beside it listed
        # the modules, which is how the discrepancy showed up.
        "trainingPlan": _as_list(stored_training_plan(u)),
        # Aptem create-form fields — the directory reads type/status from these,
        # and the edit modal round-trips the rest.
        "type": _s(u.type) or "User",
        "status": _s(u.status),
        "dob": _s(u.date_of_birth),
        **{key: _s(getattr(u, attr)) for key, attr in APTEM_TEXT_FIELDS.items()},
        **{key: getattr(u, attr) for key, attr in APTEM_BOOL_FIELDS.items()},
    }


def write_commercial_fields(payload, *, require_create=False):
    """Validate a payload and return {model_attr: value} for a commercial learner."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    if require_create:
        if not _s(payload.get("username")):
            raise ValidationError("username is required.")
        if not _s(payload.get("email")):
            raise ValidationError("email is required.")
    # This table now holds Type/Status too, so it validates the same three lists
    # as the apprenticeship table rather than programmeStatus alone.
    validate_choices(payload)
    fields = {}
    for key, attr in COMMERCIAL_WRITABLE_FIELDS.items():
        if key in payload:
            val = payload[key]
            fields[attr] = None if val is None else str(val).strip()
    for key, attr in APTEM_BOOL_FIELDS.items():
        if key in payload:
            fields[attr] = _bool_or_none(payload[key])
    if "trainingPlan" in payload:
        fields["training_plan"] = _normalize_training_plan(payload["trainingPlan"])
    fields.update(_employer_id_field(payload))
    _blank_attendance_type_to_null(fields)
    return fields


# --------------------------------------------------------------------------- #
# staff / admin accounts (enrolment."Staff_users")                             #
# --------------------------------------------------------------------------- #
# payload key -> model attribute
STAFF_WRITABLE_FIELDS = {
    "username": "username",
    # The access grant. Validated against ACCESS_CHOICES by validate_choices and
    # again by a CHECK constraint on the column, because this value decides what
    # the account can reach.
    "access": "access",
    "email": "email",
    "phone": "phone_number",
    "dob": "date_of_birth",
    "type": "type",
    "status": "status",
    "position": "position",
    "title": "title",
    "preferredName": "preferred_name",
    "gender": "gender",
    "organization": "organization",
    "caseOwner": "case_owner",
    "learningProvider": "learning_provider",
    "referenceNumber": "reference_number",
}


def to_staff_row(u):
    """A staff account, shaped like a UserListRow so the directory can list
    learners and staff in one table."""
    status = _s(u.status)
    return {
        "id": str(u.id),
        "uuid": str(u.uuid) if getattr(u, "uuid", None) else None,
        "name": _s(u.username),
        # The directory's Type column shows the staff position (Admin,
        # Caseowner, ...) — that's the meaningful role for a non-learner.
        "type": _s(u.position) or _s(u.type) or "Admin",
        "email": _s(u.email),
        "group": _s(u.organization),
        "subscriptionStatus": status,
        "subscriptionVerified": status.lower() == "fulluser",
        # Staff have no training plan or programme.
        "learningPlan": False,
        "programmeStatus": "",
        "position": _s(u.position),
        "access": _s(u.access).lower(),
        "phone": _s(u.phone_number),
        "title": _s(u.title),
        "preferredName": _s(u.preferred_name),
        "gender": _s(u.gender),
        "dob": _s(u.date_of_birth),
        "organization": _s(u.organization),
        "caseOwner": _s(u.case_owner),
        "learningProvider": _s(u.learning_provider),
        "referenceNumber": _s(u.reference_number),
        **{key: getattr(u, attr) for key, attr in APTEM_BOOL_FIELDS.items()},
    }


def write_staff_fields(payload, *, require_create=False):
    """Validate a payload and return {model_attr: value} for Staff_users columns."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    if require_create:
        if not _s(payload.get("username")):
            raise ValidationError("username is required.")
        if not _s(payload.get("email")):
            raise ValidationError("email is required.")
        if not _s(payload.get("position")):
            raise ValidationError("position is required.")
    # Staff rows carry a position instead of a programme status.
    for key, allowed in (("status", STATUS_CHOICES), ("type", TYPE_CHOICES), ("position", POSITION_CHOICES)):
        val = payload.get(key)
        if val not in (None, "") and val not in allowed:
            raise ValidationError(f"Invalid {key}: {val!r}. Allowed: {', '.join(allowed)}")
    # Access is matched case-insensitively — it is a slug, not a label, and the
    # column's CHECK constraint compares lowercased too.
    access = payload.get("access")
    if access not in (None, "") and str(access).strip().lower() not in ACCESS_CHOICES:
        raise ValidationError(
            f"Invalid access: {access!r}. Allowed: {', '.join(ACCESS_CHOICES)}"
        )
    # `accesses` is the full set an account holds. Every member is validated the
    # same way, and the primary must be one of them — a landing page the account
    # cannot reach would strand it at sign-in.
    if "accesses" in payload:
        raw = payload.get("accesses")
        if raw in (None, ""):
            raw = []
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValidationError("accesses must be a list of access values.")
        cleaned = [str(value).strip().lower() for value in raw if str(value).strip()]
        for value in cleaned:
            if value not in ACCESS_CHOICES:
                raise ValidationError(
                    f"Invalid access: {value!r}. Allowed: {', '.join(ACCESS_CHOICES)}"
                )
        primary = str(access or "").strip().lower()
        if primary and cleaned and primary not in cleaned:
            raise ValidationError(
                "The primary access must be one of the accesses granted."
            )
    fields = {}
    for key, attr in STAFF_WRITABLE_FIELDS.items():
        if key in payload:
            val = payload[key]
            fields[attr] = None if val is None else str(val).strip()
    # Normalise after the generic pass so the stored value always matches the
    # constraint and comparisons elsewhere never need to re-lower it. An empty
    # string becomes NULL: the CHECK allows NULL or one of the four values, and
    # "" is neither — clearing an access must revoke it, not 500.
    if "access" in fields:
        fields["access"] = fields["access"].lower() if fields["access"] else None
    # The additional grants are derived, never written straight through: the
    # primary lives in "Access" and only the REST belong in "Access_extra", so
    # storing the raw set would duplicate the primary in both columns.
    # super-admin means everything, so it is held alone and clears the extras.
    if "accesses" in payload:
        granted = {
            str(value).strip().lower()
            for value in (payload.get("accesses") or [])
            if str(value).strip()
        }
        primary = fields.get("access") or (
            str(payload.get("access") or "").strip().lower() or None
        )
        if ACCESS_SUPER_ADMIN in granted:
            fields["access"] = ACCESS_SUPER_ADMIN
            fields["access_extra"] = None
        else:
            # With no primary named, the first granted access becomes the
            # landing page rather than leaving the account with grants it holds
            # but nowhere to land.
            if not primary and granted:
                primary = next(
                    (value for value in ACCESS_CHOICES if value in granted), None
                )
                fields["access"] = primary
            extras = sorted(granted - {primary})
            fields["access_extra"] = ",".join(extras) if extras else None
    for key, attr in APTEM_BOOL_FIELDS.items():
        if key in payload:
            fields[attr] = _bool_or_none(payload[key])
    return fields


# --------------------------------------------------------------------------- #
# outbound: learner detail (workspace/learner page)                           #
# --------------------------------------------------------------------------- #
def _component_marking_statuses(source, learner_profile):
    """Coach decision per component id, for this learner's submissions.

    Read straight from ``learning_reflection_submissions`` because that is where
    a coach's decision lands. Matched on both the enrolment id the learner page
    uses and the profile id older rows carry, so earlier submissions are not
    missed.
    """
    from django.db import DatabaseError, connections

    candidates = {str(getattr(source, "id", "") or "")}
    if learner_profile is not None:
        candidates.add(str(getattr(learner_profile, "id", "") or ""))
        candidates.add(str(getattr(learner_profile, "enrolment_id", "") or ""))
    candidates.discard("")
    if not candidates:
        return {}

    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                """
                select distinct on (activity_id)
                       activity_id, status, coach_feedback, reviewed_by, reviewed_at
                  from "Learner"."learning_reflection_submissions"
                 where learner_id::text = any(%s)
                 order by activity_id, case when activity_type = 'assignment' then
                     case status when 'accepted' then 0 when 'partial' then 1 when 'submitted_for_tutor_review' then 2 when 'rejected' then 3 else 4 end else 0 end,
                     submitted_at desc nulls last
                """,
                [sorted(candidates)],
            )
            return {
                str(row[0]): {
                    "status": str(row[1] or ""),
                    # The coach's own words. Shown to the learner behind a
                    # button rather than inline: it is a paragraph of feedback,
                    # not a label, and only matters once they choose to read it.
                    "feedback": str(row[2] or ""),
                    "reviewedBy": str(row[3] or ""),
                    "reviewedAt": row[4].isoformat() if row[4] else None,
                }
                for row in cur.fetchall()
                if row[0]
            }
    except DatabaseError:
        # Best-effort: without it an activity simply shows as it did before.
        return {}


def to_learner_detail(source, learner_profile):
    """Shape a CommercialUser/EnrolmentUser (+ its learner profile, if any)
    for the learner workspace page. A saved profile can exist before invitation
    and activation. The training plan itself comes straight from the
    source record's structured plan column, so it's visible even for learners
    who aren't currently active; KSBs, progress, and activity feed are read
    from the normalized Learner.* tables exposed through LearnerProfile."""
    training_plan = get_training_plan(source)
    modules, week, components = flatten_training_plan(training_plan)

    # Unified progress log holds both quiz attempts and video completions,
    # distinguished by "kind" (a record without a "kind" is treated as a quiz
    # attempt, for any pre-"kind" data).
    progress = _as_list(learner_profile.training_plan_progress) if learner_profile else []
    quiz_attempts = [r for r in progress if r.get("kind", "quiz") == "quiz"]
    video_progress = [r for r in progress if r.get("kind") == "video"]
    # Generic non-quiz component completions (podcast/reading/slides/reflection/…),
    # written by learner_api.components.submit_component_progress.
    component_progress = [
        r for r in progress
        if r.get("kind") == "component"
        or (r.get("kind") == "quiz_reading" and r.get("submittedAt"))
    ]
    progress_ksb_codes = sorted({
        _s(code).upper()
        for row in progress
        if isinstance(row, dict)
        for code in _as_list(row.get("ksbs"))
        if _s(code)
    })
    # Activity Feed is projected from the same normalized progress rows.
    activity_feed = learner_profile.activity_feed_entries(newest_first=True) if learner_profile else []
    snapshot_ksbs = _as_list(learner_profile.ksbs) if learner_profile else []
    curriculum_ksbs = []
    # Resolved from the programme's authored KSB profile, which needs only the
    # source row and its training plan. Gating this on an existing
    # LearnerProfile left every learner without one — a learner enrolled but
    # never activated, so no "Learner".learners row exists yet — with an empty
    # "ksbs" list, which is what the workspace hands the KSB picker. The
    # picker then had nothing to offer and reported "No programme KSBs match
    # this category", even though the programme's standard defines them.
    try:
        from .active_users import current_curriculum_ksb_items_for_learner

        curriculum_ksbs = current_curriculum_ksb_items_for_learner(
            learner_profile,
            source=source,
            training_plan=training_plan,
        )
    except Exception:
        curriculum_ksbs = []
    from .apprenticeship_agreement import _group_dates

    programme_start, cohort_end, _ = _group_dates(source)
    if hasattr(programme_start, "isoformat"):
        programme_start = programme_start.isoformat()
    programme_end = (
        getattr(source, "end_date", None)
        or getattr(source, "practical_period_end_date", None)
        or getattr(source, "apprenticeship_end_date", None)
        or cohort_end
    )
    if hasattr(programme_end, "isoformat"):
        programme_end = programme_end.isoformat()

    return {
        "id": str(source.id),
        # Only expose whether this learner has the pilot activity view. The
        # activity endpoint resolves the Aptem id again server-side, so the
        # browser cannot substitute another learner's Aptem id.
        "studentActivityAvailable": student_activity_available(getattr(source, "aptem_id", None)),
        "name": _s(source.username),
        "email": _s(source.email),
        "phone": _s(source.phone_number),
        "programme": _s(source.programme),
        # Defaulted, not passed through raw: a never-set status *is* "Fresh
        # user" (see DEFAULT_PROGRAMME_STATUS), and the console already displays
        # it that way. Returning "" instead left the SPA unable to tell "nobody
        # has started this enrolment" apart from "the status request failed",
        # which the learner workspace has to distinguish — one shows a waiting
        # page, the other must fall back to the full workspace rather than lock
        # the learner out. Deciding it here, where that ambiguity does not
        # exist, is the only place it can be decided correctly.
        "programmeStatus": programme_status(source) or DEFAULT_PROGRAMME_STATUS,
        # Same as learner_summary: whether the enrolment wizard is handed in.
        "onboardingStatus": _s(getattr(source, "onboarding_status", "")),
        "learnerType": _s(getattr(source, "learner_type", "")) or "apprenticeship",
        "programmeStartDate": _s(programme_start),
        # The learner's own recorded start, straight from
        # Created_users.Learner_start_date. programmeStartDate above resolves
        # through the cohort when the learner has no date of their own, so it
        # answers "when does this delivery run"; this answers "when does this
        # learner start", which is what the dashboard header states.
        "learnerStartDate": _s(getattr(source, "learner_start_date", None)),
        # The learner's own recorded end, mirroring learnerStartDate above.
        # programmeEndDate resolves through the delivery, so it answers "when
        # does this delivery finish"; this answers "when does this learner
        # finish", which is what the dashboard header states.
        "learnerEndDate": _s(getattr(source, "learner_end_date", None)),
        "programmeEndDate": _s(programme_end),
        "cohort": _s(source.cohort),
        "group": _s(source.group),
        "employer": _s(getattr(source, "employer", "")),
        "employerId": getattr(source, "employer_id", None),
        "organization": _s(getattr(source, "organization", "")),
        "lineManager": _s(getattr(source, "line_manager", "")),
        "isActive": programme_status(source).casefold() == "active",
        "modules": modules,
        "week": week,
        "components": components,
        "ksbs": curriculum_ksbs or snapshot_ksbs,
        "progressKsbCodes": progress_ksb_codes,
        "quizAttempts": quiz_attempts,
        "videoProgress": video_progress,
        "componentProgress": component_progress,
        # What a coach has decided about each activity this learner submitted,
        # keyed by component id: "submitted_for_tutor_review" while it waits,
        # "accepted" once validated, "referred"/"rejected" when sent back.
        #
        # An activity whose component requires tutor validation is not finished
        # until this says "accepted" -- completing it only records that the
        # learner did the work and handed it in. Without this the learner's plan
        # showed the activity green the moment they submitted, before any coach
        # had looked at it.
        "componentMarkingStatus": _component_marking_statuses(source, learner_profile),
        "activityFeed": activity_feed,
    }


# --------------------------------------------------------------------------- #
# organisations (enrolment."Organisations") and employers (enrolment."Employers")
# --------------------------------------------------------------------------- #
# payload key -> model attribute
ORGANISATION_WRITABLE_FIELDS = {
    "status": "status",
    "name": "name",
    "owner": "owner",
    "category": "category",
    "groupType": "group_type",
    "parentName": "parent_name",
    "edrsErnNumber": "edrs_ern_number",
    "apprenticeshipAgreementId": "apprenticeship_agreement_id",
    "postCode": "post_code",
    "address1": "address_1",
    "address2": "address_2",
    "cityTown": "city_town",
    "county": "county",
    "country": "country",
    "contactName": "contact_name",
    "contactEmail": "contact_email",
    "contactTelephone": "contact_telephone",
    "contactRole": "contact_role",
    "website": "website",
    "referenceNumber": "reference_number",
    "levyPayer": "levy_payer",
    "healthAndSafety": "health_and_safety",
    "logoUrl": "logo_url",
}

EMPLOYER_WRITABLE_FIELDS = {
    "firstName": "first_name",
    "surname": "surname",
    "gender": "gender",
    "email": "email",
    "mobile": "mobile",
    "postCode": "post_code",
    "address1": "address_1",
    "address2": "address_2",
    "townCity": "town_city",
    "county": "county",
    "country": "country",
}


def _working_hours(value):
    """Normalise the repeated {day, start, end} sessions to a clean list.

    The form's "Add another session" control can leave a half-filled row behind,
    so a session with no day and no times is dropped rather than stored as noise.
    """
    sessions = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        session = {
            "day": _s(item.get("day")),
            "start": _s(item.get("start")),
            "end": _s(item.get("end")),
        }
        if any(session.values()):
            sessions.append(session)
    return sessions


def to_organisation_row(o):
    """An organisation, shaped for the list table and the Employer Group picker."""
    return {
        "id": str(o.id),
        "status": _s(o.status),
        "name": _s(o.name),
        "owner": _s(o.owner),
        "category": _s(o.category),
        # The picker's own columns.
        "groupType": _s(o.group_type) or "Employer",
        "parentName": _s(o.parent_name),
        "edrsErnNumber": _s(o.edrs_ern_number),
        "apprenticeshipAgreementId": _s(o.apprenticeship_agreement_id),
        "postCode": _s(o.post_code),
        "address1": _s(o.address_1),
        "address2": _s(o.address_2),
        "cityTown": _s(o.city_town),
        "county": _s(o.county),
        "country": _s(o.country),
        "workingHours": _working_hours(o.working_hours),
        "contactName": _s(o.contact_name),
        "contactEmail": _s(o.contact_email),
        "contactTelephone": _s(o.contact_telephone),
        "contactRole": _s(o.contact_role),
        "website": _s(o.website),
        "referenceNumber": _s(o.reference_number),
        "levyPayer": _s(o.levy_payer),
        "approxNoOfEmployees": o.approx_no_of_employees,
        "healthAndSafety": _s(o.health_and_safety),
        "logoUrl": _s(o.logo_url),
        "sendHoursVerificationEmails": o.send_hours_verification_emails,
    }


def write_organisation_fields(payload, *, require_create=False):
    """Validate a payload and return {model_attr: value} for Organisations columns."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    if require_create and not _s(payload.get("name")):
        raise ValidationError("name is required.")
    # The organisation form no longer asks for a group type, but the Employer
    # Group picker shows it as a column — so a new row is stored as an Employer
    # rather than left blank for the read path to paper over.
    if require_create and not _s(payload.get("groupType")):
        payload = {**payload, "groupType": "Employer"}
    for key, allowed in (
        ("status", ORGANISATION_STATUS_CHOICES),
        ("groupType", ORGANISATION_GROUP_TYPE_CHOICES),
        ("levyPayer", LEVY_PAYER_CHOICES),
        ("healthAndSafety", HEALTH_SAFETY_CHOICES),
    ):
        val = payload.get(key)
        if val not in (None, "") and val not in allowed:
            raise ValidationError(f"Invalid {key}: {val!r}. Allowed: {', '.join(allowed)}")

    fields = {}
    for key, attr in ORGANISATION_WRITABLE_FIELDS.items():
        if key in payload:
            val = payload[key]
            fields[attr] = None if val is None else str(val).strip()
    if "workingHours" in payload:
        fields["working_hours"] = _working_hours(payload["workingHours"])
    # A boolean, so it bypasses the string-coercing loop above.
    if "sendHoursVerificationEmails" in payload:
        fields["send_hours_verification_emails"] = _bool_or_none(
            payload["sendHoursVerificationEmails"]
        )
    if "approxNoOfEmployees" in payload:
        raw = payload["approxNoOfEmployees"]
        if raw in (None, ""):
            fields["approx_no_of_employees"] = None
        else:
            try:
                fields["approx_no_of_employees"] = int(str(raw).strip())
            except (TypeError, ValueError):
                raise ValidationError("approxNoOfEmployees must be a whole number.")
    return fields


def to_employer_row(e):
    """An employer (a person at one or more organisations), shaped for the list."""
    return {
        "id": str(e.id),
        "firstName": _s(e.first_name),
        "surname": _s(e.surname),
        "name": e.full_name,
        "gender": _s(e.gender),
        "email": _s(e.email),
        "mobile": _s(e.mobile),
        "postCode": _s(e.post_code),
        "address1": _s(e.address_1),
        "address2": _s(e.address_2),
        "townCity": _s(e.town_city),
        "county": _s(e.county),
        "country": _s(e.country),
        # Ids are the link; names travel with them so a row renders without a join.
        "employerGroupIds": [str(i) for i in _as_list(e.employer_group_ids)],
        "employerGroupNames": [_s(n) for n in _as_list(e.employer_group_names)],
    }


def write_employer_fields(payload, *, require_create=False, resolve_groups=None):
    """Validate a payload and return {model_attr: value} for Employers columns.

    `resolve_groups` maps the submitted organisation ids to their current names,
    which is what gets denormalised into "Employer_group_names". The caller
    supplies it (it needs a database read), and it is also the membership check:
    an id that resolves to nothing is rejected rather than stored as a dangling
    reference.
    """
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    if require_create:
        if not _s(payload.get("firstName")):
            raise ValidationError("firstName is required.")
        if not _s(payload.get("surname")):
            raise ValidationError("surname is required.")

    fields = {}
    for key, attr in EMPLOYER_WRITABLE_FIELDS.items():
        if key in payload:
            val = payload[key]
            fields[attr] = None if val is None else str(val).strip()

    if "employerGroupIds" in payload:
        raw = payload["employerGroupIds"]
        if raw in (None, ""):
            ids = []
        elif not isinstance(raw, list):
            raise ValidationError("employerGroupIds must be a list of organisation ids.")
        else:
            ids = []
            for item in raw:
                try:
                    ids.append(int(str(item).strip()))
                except (TypeError, ValueError):
                    raise ValidationError(f"Invalid organisation id: {item!r}")
        # Preserve the submitted order but drop repeats.
        ids = list(dict.fromkeys(ids))
        names_by_id = resolve_groups(ids) if resolve_groups else {}
        missing = [i for i in ids if i not in names_by_id]
        if missing:
            raise ValidationError(
                "Unknown organisation id(s): " + ", ".join(str(i) for i in missing)
            )
        fields["employer_group_ids"] = ids
        fields["employer_group_names"] = [names_by_id[i] for i in ids]
    return fields
