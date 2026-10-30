"""Unmanaged mapping of the Neon learner table enrolment."Created_users".

ONE table holds every learner. `Created_users` is written by the user-creation
form; its leading columns are exactly that form's fields, followed by the
operational columns the rest of the app reads. "Learner_type" tells the two kinds
apart ('apprenticeship' | 'commercial'), which is what the old
Enrolment_Users + Commercial_users table pair used to encode structurally.

`EnrolmentUser` keeps its historical class name (it is referenced across ~20
modules) but now maps `Created_users`; `CommercialUser` is a proxy over the same
table, scoped to the commercial rows.

The tables were created outside Django, so `managed = False` — Django never issues
DDL for them. Column names are irregular (leading spaces, mixed case, slashes), so
each field pins its exact `db_column`. The schema is targeted with the
`schema"."table` quoting trick, which Django emits as
`"enrolment"."Created_users"` — avoiding a search_path startup option that the
Neon connection pooler may reject.
"""
import json
import uuid
from functools import lru_cache

from django.db import DatabaseError, connections, models
from django.db.models.functions import Lower, Trim
from system_audit.writes import AuditedQuerySet


class SafeJSONField(models.JSONField):
    """JSONField tolerant of psycopg3 already-parsed values.

    psycopg 3 decodes `json`/`jsonb` columns into Python objects itself, but
    Django's JSONField.from_db_value then calls json.loads() on them and blows up
    on a list/dict. This accepts a value that's already parsed and only parses
    when handed a raw string.
    """

    def from_db_value(self, value, expression, connection):
        if value is None or isinstance(value, (list, dict)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value


LEARNER_ACTIVITY_EVENTS_RELATION = '"Learner"."learner_activity_events"'
LEARNER_KSBS_RELATION = '"Learner"."learner_ksbs"'


@lru_cache(maxsize=None)
def learner_activity_events_relation_exists(using: str) -> bool:
    """Compatibility check for the retired standalone activity-events table.

    Coach queries still use this to avoid prefetching the removed legacy
    relation. The learner feed itself now comes from learner_progress_entries.
    """
    try:
        with connections[using].cursor() as cursor:
            cursor.execute("select to_regclass(%s)", [LEARNER_ACTIVITY_EVENTS_RELATION])
            result = cursor.fetchone()
    except DatabaseError:
        return False
    return bool(result and result[0])


@lru_cache(maxsize=None)
def learner_ksbs_relation_exists(using: str) -> bool:
    """Is the legacy per-learner KSB snapshot compatible with its ORM model?

    ``LearnerKsb`` / ``LearnerProfile.assigned_ksbs`` map the pre-normalisation
    snapshot, kept as a read/rollback fallback. It is absent from the current
    database, where the authoritative source is the shared, immutable
    ``learner_ksb_assignments`` -> ``ksb_profile_versions`` graph.

    ``LearnerProfile.ksbs`` already tolerates its absence, but a queryset-level
    ``prefetch_related("assigned_ksbs")`` raises before any of that runs, so
    callers probe here first — same shape as the activity-events check above.
    """
    required_columns = {
        "id", "learner_id", "position", "code", "number", "ksb_type", "description",
    }
    try:
        with connections[using].cursor() as cursor:
            cursor.execute(
                """
                select array_agg(attribute.attname)
                from pg_attribute attribute
                where attribute.attrelid = to_regclass(%s)
                  and attribute.attnum > 0
                  and not attribute.attisdropped
                """,
                [LEARNER_KSBS_RELATION],
            )
            result = cursor.fetchone()
    except DatabaseError:
        return False
    available_columns = set(result[0] or []) if result else set()
    return required_columns.issubset(available_columns)


def _serialise_quiz_ref(value):
    """Keep learner API quiz IDs numeric when their database column is text."""
    if value in (None, ""):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _progress_entry_activity(entry):
    """Project one progress row into the learner activity-feed shape."""
    feed_kind = str(getattr(entry, "feed_kind", "") or "").strip()
    progress_kind = str(getattr(entry, "kind", "") or "").strip()
    kind = feed_kind or ("" if progress_kind == "activity_event" else progress_kind)
    component_type = str(getattr(entry, "component_type", "") or "").strip()

    action = str(getattr(entry, "feed_action", "") or "").strip()
    if not action:
        if kind == "quiz":
            action = "Completed quiz"
        elif kind == "video":
            action = "Watched video"
        elif kind == "live_session":
            action = "Completed live session"
        elif kind == "component":
            action = {
                "podcast": "Listened to podcast",
                "reading": "Completed reading",
                "video": "Watched video",
            }.get(component_type.casefold(), "Completed activity")

    title = str(getattr(entry, "feed_title", "") or getattr(entry, "component_title", "") or "").strip()
    if not title:
        title = {"quiz": "Quiz", "video": "Video", "live_session": "Live session"}.get(kind, "Activity")

    detail = str(getattr(entry, "feed_detail", "") or "").strip()
    if not detail and kind == "quiz":
        grade = getattr(entry, "grade", None)
        achieved = getattr(entry, "achieved_score", None)
        total = getattr(entry, "total_score", None)
        if grade is not None:
            percent = float(grade) * 100 if float(grade) <= 1 else float(grade)
            percent_text = str(int(percent)) if percent.is_integer() else str(round(percent, 1))
            detail = f"Scored {percent_text}%"
            if achieved is not None and total is not None:
                detail += f" · {float(achieved):g}/{float(total):g}"
    if not detail:
        detail = str(getattr(entry, "reported_time", "") or "").strip()

    occurred_at = getattr(entry, "feed_occurred_at", None) or getattr(entry, "submitted_at", None)
    item = {
        "kind": kind,
        "action": action,
        "title": title,
        "detail": detail,
        "componentId": getattr(entry, "component_ref", None),
        "componentType": component_type,
        "quizId": _serialise_quiz_ref(getattr(entry, "quiz_ref", None)),
        "module": str(getattr(entry, "module_title", "") or ""),
        "week": str(getattr(entry, "week_title", "") or ""),
        "passed": getattr(entry, "passed", None),
        "at": occurred_at.isoformat() if occurred_at else "",
    }
    for key, value in (
        ("programmeId", getattr(entry, "programme_ref", None)),
        ("programme", str(getattr(entry, "programme_title", "") or "")),
        ("cohortId", getattr(entry, "cohort_ref", None)),
        ("cohort", str(getattr(entry, "cohort_title", "") or "")),
        ("groupId", getattr(entry, "group_ref", None)),
        ("group", str(getattr(entry, "group_title", "") or "")),
    ):
        if value not in (None, ""):
            item[key] = value
    return item


class LearnerTypeQuerySet(AuditedQuerySet):
    """Queryset for the merged learner table, scoped by "Learner_type"."""

    def apprenticeship(self):
        return self.filter(learner_type="apprenticeship")

    def commercial(self):
        return self.filter(learner_type="commercial")


class CommercialManager(models.Manager):
    """Default manager for CommercialUser: only ever sees commercial rows.

    Both learner kinds live in one table since the merge, so the proxy's manager
    filters on "Learner_type" — otherwise CommercialUser.objects.all() would
    return apprenticeship learners too.
    """

    def get_queryset(self):
        return LearnerTypeQuerySet(self.model, using=self._db).filter(learner_type="commercial")


class ApprenticeshipManager(models.Manager):
    """Default manager for EnrolmentUser: apprenticeship rows only.

    Since the merge both kinds share one table, so an unfiltered manager would
    make every apprenticeship listing include commercial learners. Rows predating
    the merge are treated as apprenticeship (Learner_type is backfilled, but a
    NULL is included defensively so a row can never vanish from both managers).
    Use EnrolmentUser.all_learners for queries that intentionally span both.
    """

    def get_queryset(self):
        return LearnerTypeQuerySet(self.model, using=self._db).exclude(learner_type="commercial")


class EnrolmentUser(models.Model):
    # `objects` is scoped to apprenticeship learners; `all_learners` spans both
    # kinds (used by lookups that resolve a learner by id regardless of type).
    objects = ApprenticeshipManager()
    all_learners = models.Manager()

    id = models.AutoField(primary_key=True, db_column="id")
    # Aptem's learner identifier. It is the bridge to the read-only
    # Last_audit mirror; it is text here because that is how Created_users was
    # originally provisioned, while Last_audit stores the same value as bigint.
    aptem_id = models.TextField(db_column="aptem_id", null=True, blank=True)

    # The coach this learner is assigned to, on the enrolment side. The coach
    # workspace itself scopes on Learner.learners.coach_email -- these are the
    # enrolment record, kept in step by assign_audit_coaches, so the assignment
    # survives a mirror being rebuilt.
    coach_name = models.TextField(db_column="Coach_name", null=True, blank=True)
    coach_email = models.TextField(db_column="Coach_email", null=True, blank=True)

    # The user's permanent public identifier, added by apply_user_uuid. The
    # integer pk above stays the internal join key — ~25 columns across three
    # schemas hold it as bigint — so this is the id to expose in APIs and URLs,
    # not to join on. The database fills it (DEFAULT gen_random_uuid()), so it
    # is read back, never written by Django.
    # default= matters as much as the database default: editable=False still
    # puts the field in Django's INSERT, so without it the ORM sends an explicit
    # NULL, overriding DEFAULT gen_random_uuid() and tripping the NOT NULL
    # constraint on every create. An explicit value — a learner profile
    # inheriting the enrolment row's uuid — still wins over this.
    uuid = models.UUIDField(
        db_column="uuid", unique=True, editable=False, default=uuid.uuid4
    )

    # Which kind of learner this row is: 'apprenticeship' | 'commercial'.
    # Added by the merge_commercial_into_enrolment management command, which
    # folded enrolment."Commercial_users" into this table.
    learner_type = models.TextField(db_column="Learner_type", null=True, blank=True)

    # --- flat text columns ---
    username = models.TextField(db_column="Username", null=True, blank=True)
    email = models.TextField(db_column="Email", null=True, blank=True)
    # Existing column verified in Neon; this mapping does not create a column.
    aptem_id = models.TextField(db_column="aptem_id", null=True, blank=True)
    status = models.TextField(db_column=" Status", null=True, blank=True)  # NB: leading space
    type = models.TextField(db_column="Type", null=True, blank=True)
    programme_status = models.TextField(db_column="Programme_status", null=True, blank=True)
    programme = models.TextField(db_column="Programme", null=True, blank=True)
    cohort = models.TextField(db_column="Cohort", null=True, blank=True)
    group = models.TextField(db_column="Group", null=True, blank=True)
    # Cohort delivery window, copied from curriculum."cohort_authoring_details"
    # (matched by Programme + Cohort name — see cohort_dates in active_users.py).
    # Populated on create; refreshed on the Active mirror.
    start_date = models.DateField(db_column="Start_date", null=True, blank=True)
    end_date = models.DateField(db_column="End_date", null=True, blank=True)
    # Structured training plan (see mappers.TRAINING_PLAN docstring for shape).
    # This column pre-existed as unused free text; repurposed here since apprentice
    # learners previously had no way to persist a training plan at all.
    learning_plan = SafeJSONField(db_column="Learning_plan", null=True, blank=True)
    # Merged in from Commercial_users. Commercial learners store their plan here;
    # apprenticeship learners use learning_plan above. get_training_plan() reads
    # whichever is populated.
    training_plan = SafeJSONField(db_column="Training_plan", null=True, blank=True)
    # Free courses assigned to this learner from the Learning Plan modal. The
    # canonical store for BOTH learner kinds (commercial rows are Created_users
    # rows too), read/written by learner_api.learning_plan. A pure assignment
    # record ([{freeCourseId, courseName, addedAt}]) — deliberately separate from
    # learning_plan/training_plan so free courses never enter OTJH totals, the KSB
    # snapshot, progress, or hydrate_training_plan. NOT NULL '[]' in Postgres,
    # hence default=list.
    free_courses = SafeJSONField(db_column="Free_courses", default=list, blank=True)
    # Legacy comma-joined summary columns, also merged in from Commercial_users.
    # Superseded by training_plan; kept so old saved values stay visible.
    modules = models.TextField(db_column="Modules", null=True, blank=True)
    weeks = models.TextField(db_column="Weeks", null=True, blank=True)
    components = models.TextField(db_column="Components", null=True, blank=True)
    phone_number = models.TextField(db_column="Phone_number", null=True, blank=True)
    date_of_birth = models.TextField(db_column="Date_of_birth", null=True, blank=True)
    organization = models.TextField(db_column="Orgnization", null=True, blank=True)  # source spelling
    employer = models.TextField(db_column="Employer", null=True, blank=True)
    # The employer's record in enrolment."Employers". `employer` above stays as the
    # display name (it predates employer profiles and is what a plain listing
    # shows); this is the actual reference, so a learner can reach that employer's
    # address, contact details and organisation membership.
    #
    # A plain IntegerField, not a ForeignKey: these tables are unmanaged, and the
    # sibling Employers -> Organisations link is id-in-jsonb for the same reason.
    # The API rejects an unknown id on write. Added by the
    # apply_created_users_employer_id command.
    employer_id = models.IntegerField(db_column="Employer_id", null=True, blank=True)
    line_manager = models.TextField(db_column="Line_manager", null=True, blank=True)
    learner_start_date = models.TextField(db_column="Learner_start_date", null=True, blank=True)
    learner_end_date = models.TextField(db_column="Learner_end_date", null=True, blank=True)
    # NOTE: "First_session_booked" and "First_session_booked_at" exist on this
    # table (see sql/2026-09-17_first_session_booking_on_created_users.sql) but
    # are deliberately NOT mapped here, exactly like the "Learner_signature"
    # columns above them.
    #
    # Mapping a column makes Django SELECT it in *every* query against this
    # table -- including the one login runs for each request -- so a mapped
    # column that the database does not have yet takes the whole platform down,
    # not just the feature that uses it. These two are written and read by
    # learner_api.first_session through raw SQL that checks for them first, so a
    # database without them degrades to "not recorded" instead of "nobody can
    # sign in".
    start_date = models.TextField(db_column="Start_date", null=True, blank=True)
    end_date = models.TextField(db_column="End_date", null=True, blank=True)
    practical_period_end_date = models.TextField(db_column="Practical_period_end_date", null=True, blank=True)
    apprenticeship_end_date = models.TextField(db_column="Apprenticeship_End_date", null=True, blank=True)
    minimum_required_hours = models.TextField(db_column="Minimum_required_hours", null=True, blank=True)
    planned_hours = models.TextField(db_column="Planned_hours", null=True, blank=True)
    enrolled_time_and_user = models.TextField(db_column="Enrolled_time_and_user", null=True, blank=True)
    rpl_hours = models.TextField(db_column="RPL_Hours", null=True, blank=True)
    onboarding_status = models.TextField(db_column="Onboarding_status", null=True, blank=True)
    onboarding_completed = models.TextField(db_column="Onboarding_completed", null=True, blank=True)
    managed_jobs = models.TextField(db_column="Managed_jobs_and_placements/workshops", null=True, blank=True)
    competencies = models.TextField(db_column="Competencies", null=True, blank=True)

    # --- Aptem "Add user" fields (see apply_aptem_create_columns) ---
    # Captured by the create-user form, which mirrors Aptem's own Add-user screen.
    title = models.TextField(db_column="Title", null=True, blank=True)
    preferred_name = models.TextField(db_column="Preferred_name", null=True, blank=True)
    gender = models.TextField(db_column="Gender", null=True, blank=True)
    referrer = models.TextField(db_column="Referrer", null=True, blank=True)
    referrer_address = models.TextField(db_column="Referrer_address", null=True, blank=True)
    referrer_contact = models.TextField(db_column="Referrer_contact", null=True, blank=True)
    country = models.TextField(db_column="Country", null=True, blank=True)
    case_owner = models.TextField(db_column="Case_owner", null=True, blank=True)
    learning_provider = models.TextField(db_column="Learning_provider", null=True, blank=True)
    mentor = models.TextField(db_column="Mentor", null=True, blank=True)
    reference_number = models.TextField(db_column="Reference_number", null=True, blank=True)
    extended_break = models.TextField(db_column="Extended_break", null=True, blank=True)
    # "Recorded" / "Not recorded" (ATTENDANCE_TYPE_CHOICES); NULL when not given.
    # Added by the apply_created_users_attendance_type command.
    attendance_type = models.TextField(db_column="Attendance_type", null=True, blank=True)
    employer_address = models.TextField(db_column="Employer_address", null=True, blank=True)
    target_programme = models.TextField(db_column="Target_programme", null=True, blank=True)
    invite_to_platform = models.BooleanField(db_column="Invite_to_platform", null=True, blank=True)

    # The Aptem learner id this record was imported from, when it came from
    # the audit snapshot (see import_audit_learners). Text rather than an
    # integer because that is how the column is defined, and null for anyone
    # created directly on the platform.
    aptem_id = models.TextField(db_column="aptem_id", null=True, blank=True)
    allow_access_to_checkpoint = models.BooleanField(db_column="Allow_access_to_checkpoint", null=True, blank=True)
    allow_access_to_console = models.BooleanField(db_column="Allow_access_to_console", null=True, blank=True)
    allow_access_to_classic = models.BooleanField(db_column="Allow_access_to_classic", null=True, blank=True)

    # --- personal / address (pre-existing columns, now written by the form) ---
    legal_sex = models.TextField(db_column="Legal_Sex", null=True, blank=True)
    age = models.TextField(db_column="Age", null=True, blank=True)
    address = models.TextField(db_column="Address", null=True, blank=True)
    current_postcode = models.TextField(db_column="Current_postcode", null=True, blank=True)
    address_line_1 = models.TextField(db_column="Current_address_line_1", null=True, blank=True)
    address_line_2 = models.TextField(db_column="Current_address_line_2", null=True, blank=True)
    address_line_3 = models.TextField(db_column="Current_address_line_3", null=True, blank=True)
    address_line_4 = models.TextField(db_column="Current_address_line_4", null=True, blank=True)
    national_insurance_number = models.TextField(db_column="National_insurance_number", null=True, blank=True)

    # --- json columns ---
    sub_programme = models.JSONField(db_column="Sub-programme", null=True, blank=True)
    aims_qualifications = models.JSONField(db_column="Aims/Qualifications", null=True, blank=True)
    english_assessments = models.JSONField(db_column=" English_Assessments", null=True, blank=True)  # leading space
    maths_assessments = models.JSONField(db_column="Maths_Assessments", null=True, blank=True)
    ict_assessments = models.JSONField(db_column="ICT_Assessments", null=True, blank=True)
    english_exemption = models.JSONField(db_column="English_Exemption_from_Functional_Skills", null=True, blank=True)
    maths_exemption = models.JSONField(db_column="Maths_Exemption_from_Functional_Skills", null=True, blank=True)
    ict_exemption = models.JSONField(db_column="ICT_Exemption_from_Functional_Skills", null=True, blank=True)
    reviews = models.JSONField(db_column="Reviews", null=True, blank=True)
    tracker = models.JSONField(db_column="Tracker", null=True, blank=True)
    milestones = models.JSONField(db_column="Milestones", null=True, blank=True)
    contacts = models.JSONField(db_column="Contacts", null=True, blank=True)
    activity = models.JSONField(db_column="Activity", null=True, blank=True)
    compliance_documents = models.JSONField(db_column=" Compliance_documents", null=True, blank=True)  # leading space
    review_documents = models.JSONField(db_column=" Review_documents", null=True, blank=True)  # leading space
    documents = models.JSONField(db_column="Documents", null=True, blank=True)
    subscription_details = models.JSONField(db_column="Subscription_details", null=True, blank=True)

    class Meta:
        managed = False
        # Emitted by Django as "enrolment"."Created_users".
        # The single learner table: both apprenticeship and commercial learners
        # live here, told apart by "Learner_type". It replaced the old
        # Enrolment_Users + Commercial_users pair — see the
        # create_created_users_table management command.
        db_table = 'enrolment"."Created_users'

    def save(self, *args, **kwargs):
        # Default a new row to apprenticeship. The CommercialUser proxy overrides
        # this with 'commercial'; without a value the row would be invisible to
        # the commercial manager and only reachable via all_learners.
        if not self.learner_type:
            self.learner_type = "apprenticeship"
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "learner_type" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "learner_type"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username or 'Unnamed'} <{self.email or 'no-email'}>"


class CommercialUser(EnrolmentUser):
    """Commercial (delivery) learners — a view onto the merged learner table.

    Commercial_users was folded into Enrolment_Users by the
    merge_commercial_into_enrolment command, so this is now a proxy over the same
    table whose default manager only sees rows with Learner_type='commercial'.
    Existing call sites (CommercialUser.objects.get/filter/create) keep working
    and stay scoped to commercial learners.

    Because it's a proxy, every column on EnrolmentUser is available here too —
    including the apprenticeship-only compliance/ILR columns, which commercial
    rows simply leave null.
    """

    objects = CommercialManager()

    class Meta:
        proxy = True

    def save(self, *args, **kwargs):
        # A row created through this proxy is a commercial learner; stamp the
        # discriminator so the manager's filter can find it again.
        if not self.learner_type:
            self.learner_type = "commercial"
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "learner_type" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "learner_type"]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username or 'Unnamed'} <{self.email or 'no-email'}>"


class StaffUser(models.Model):
    """Unmanaged mapping of enrolment."Staff_users" — non-learner accounts.

    Backs the Create menu's "Create admin" path. Case owners, admins, enrolment
    officers and the curriculum/operations teams are staff, not learners, so they
    get their own table rather than sharing Enrolment_Users. `position` is
    constrained to constants.POSITION_CHOICES by the API.

    Created by the apply_staff_users_table management command; `id` is GENERATED
    ALWAYS AS IDENTITY, so Django never supplies it on insert.
    """

    id = models.AutoField(primary_key=True, db_column="id")

    # The user's permanent public identifier, added by apply_user_uuid. The
    # integer pk above stays the internal join key — ~25 columns across three
    # schemas hold it as bigint — so this is the id to expose in APIs and URLs,
    # not to join on. The database fills it (DEFAULT gen_random_uuid()), so it
    # is read back, never written by Django.
    # default= matters as much as the database default: editable=False still
    # puts the field in Django's INSERT, so without it the ORM sends an explicit
    # NULL, overriding DEFAULT gen_random_uuid() and tripping the NOT NULL
    # constraint on every create. An explicit value — a learner profile
    # inheriting the enrolment row's uuid — still wins over this.
    uuid = models.UUIDField(
        db_column="uuid", unique=True, editable=False, default=uuid.uuid4
    )

    username = models.TextField(db_column="Username", null=True, blank=True)
    email = models.TextField(db_column="Email", null=True, blank=True)
    phone_number = models.TextField(db_column="Phone_number", null=True, blank=True)
    type = models.TextField(db_column="Type", null=True, blank=True)
    status = models.TextField(db_column=" Status", null=True, blank=True)  # NB: leading space, matches the learner tables
    # One of constants.POSITION_CHOICES. A job title only — it no longer decides
    # what the account may do; see `access` below.
    position = models.TextField(db_column="Position", null=True, blank=True)

    # One of constants.ACCESS_CHOICES: what this account may actually reach.
    # Null on rows created before the column existed, which resolves to the
    # least-privileged role rather than a guess (login.identity.role_for_staff).
    # Added by the apply_staff_access_column management command.
    #
    # This is the PRIMARY grant — where the account lands at sign-in. An account
    # may hold others too; those live in `access_extra`.
    access = models.TextField(db_column="Access", null=True, blank=True)

    # Any ADDITIONAL grants beyond `access`, comma-separated. Read through
    # login.identity.accesses_for_staff, which unions the two — never compared
    # directly, or a multi-access account is refused the workspace it holds.
    # Added by the apply_staff_access_extra_column management command.
    access_extra = models.TextField(db_column="Access_extra", null=True, blank=True)

    title = models.TextField(db_column="Title", null=True, blank=True)
    preferred_name = models.TextField(db_column="Preferred_name", null=True, blank=True)
    gender = models.TextField(db_column="Gender", null=True, blank=True)
    date_of_birth = models.TextField(db_column="Date_of_birth", null=True, blank=True)
    organization = models.TextField(db_column="Orgnization", null=True, blank=True)  # source spelling, as elsewhere
    case_owner = models.TextField(db_column="Case_owner", null=True, blank=True)
    learning_provider = models.TextField(db_column="Learning_provider", null=True, blank=True)
    reference_number = models.TextField(db_column="Reference_number", null=True, blank=True)

    invite_to_platform = models.BooleanField(db_column="Invite_to_platform", null=True, blank=True)
    allow_access_to_checkpoint = models.BooleanField(db_column="Allow_access_to_checkpoint", null=True, blank=True)
    allow_access_to_console = models.BooleanField(db_column="Allow_access_to_console", null=True, blank=True)
    allow_access_to_classic = models.BooleanField(db_column="Allow_access_to_classic", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "enrolment"."Staff_users".
        db_table = 'enrolment"."Staff_users'

    def __str__(self):
        return f"{self.username or 'Unnamed'} <{self.email or 'no-email'}> [{self.position or 'no position'}]"


class LearnerProfile(models.Model):
    """Permanent learner identity shared by active and inactive workflows."""

    id = models.BigAutoField(primary_key=True)

    # The same value as this learner's Created_users.uuid — one identity across
    # the enrolment and active phases, which is the point of the column.
    # apply_user_uuid copies it across via enrolment_id; a profile with no
    # enrolment row of its own gets a fresh one.
    # default= matters as much as the database default: editable=False still
    # puts the field in Django's INSERT, so without it the ORM sends an explicit
    # NULL, overriding DEFAULT gen_random_uuid() and tripping the NOT NULL
    # constraint on every create. An explicit value — a learner profile
    # inheriting the enrolment row's uuid — still wins over this.
    uuid = models.UUIDField(
        db_column="uuid", unique=True, editable=False, default=uuid.uuid4
    )
    full_name = models.TextField()
    email = models.EmailField(max_length=320, unique=True)
    # An optional contact address.  Authentication and account ownership stay
    # bound to ``email`` (and the linked Created_users row); this field must
    # never be treated as a login identifier.
    secondary_email = models.EmailField(max_length=320, null=True, blank=True)
    # GENERATED ALWAYS in Postgres — the database derives it from `email`, and an
    # INSERT/UPDATE that names the column is rejected outright. `editable=False`
    # only hides a field from forms; it stays in the write. GeneratedField with
    # db_persist=True tells Django the DB owns the value, so it is read back but
    # never written. (Without this, promoting a learner to Active fails with
    # "cannot insert a non-DEFAULT value into column email_normalized".)
    email_normalized = models.GeneratedField(
        expression=Lower(Trim("email")),
        output_field=models.TextField(),
        db_persist=True,
    )
    phone_number = models.TextField(blank=True)

    # The learner's id in enrolment."Created_users" — the one identifier that
    # exists for them from the moment they are created, long before this profile
    # row does. Added by apply_learner_enrolment_id.
    #
    # The two tables have independent primary-key sequences (their ids are
    # disjoint), so before this column the only bridge between the schemas was an
    # email match — which breaks the moment an address is corrected. Null only
    # for profiles whose enrolment record has since been deleted.
    enrolment_id = models.BigIntegerField(null=True, blank=True, db_index=True)

    # Legacy profile fallback for the external Aptem identity.  Newer records
    # carry the same value on enrolment."Created_users"; readers must prefer
    # that stable enrolment bridge and only use this column when the enrolment
    # value is absent.
    aptem_id = models.BigIntegerField(null=True, blank=True)

    lifecycle_status = models.CharField(max_length=50, db_index=True)

    # 'apprenticeship' or 'commercial', copied from the enrolment record rather
    # than guessed. The commercial listing used to infer it from the programme
    # name ("apprentice" in it, or starting "apm"), which only held while no
    # programme was named something that collided. Null only for profiles with
    # no enrolment record to read it from.
    learner_type = models.CharField(max_length=32, blank=True, null=True, db_index=True)

    programme = models.TextField(blank=True)
    # curriculum.programmes.programme_id for `programme`. The name is what the
    # enrolment form captures; this is the stable key it resolves to, so a
    # renamed programme does not orphan the learner.
    programme_id = models.CharField(max_length=64, blank=True, null=True)
    programme_status = models.CharField(max_length=100, blank=True)
    cohort = models.TextField(blank=True)
    # curriculum.cohorts.cohort_id for `cohort`, and curriculum.groups.group_id
    # for `group_name` -- the same relationship programme_id has to `programme`,
    # for the two levels below it. The names stay: they are what the enrolment
    # screens capture and edit, and what every consumer that has not been moved
    # over still reads. These are the stable keys those names resolve to, so a
    # renamed cohort or group no longer drops its learners off the curriculum
    # rosters that used to find them by comparing text.
    #
    # NULL means "not resolved" -- an unmatched or ambiguous name -- and every
    # reader falls back to matching by name for those rows. Added out of band by
    # sql/2026-09-02_learner_placement_ids.sql, like programme_id before them,
    # so an environment that has not run it simply never sees them populated.
    cohort_id = models.CharField(max_length=64, blank=True, null=True)
    group_name = models.TextField(blank=True)
    group_id = models.CharField(max_length=64, blank=True, null=True)
    completed_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    target_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    minimum_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    maximum_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    planned_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    progress_hours = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    progress_variance = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    otjh_status = models.CharField(max_length=50, blank=True)
    coach_name = models.TextField(blank=True)
    coach_email = models.EmailField(max_length=320, blank=True)
    coach_rag = models.CharField(max_length=20, blank=True)

    # What a coach has actually decided about this learner's work. Recomputed
    # from Learner.learning_reflection_submissions after every marking decision
    # (learner_api.marking_tally) rather than incremented, so a changed decision
    # or a resubmission cannot leave them drifting from the submissions.
    accepted_assignments = models.IntegerField(default=0)
    rejected_assignments = models.IntegerField(default=0)
    accepted_reflections = models.IntegerField(default=0)
    rejected_reflections = models.IntegerField(default=0)

    # The learner's assigned plan -- the modules, weeks and components their
    # "My learning" page shows -- mirrored from
    # enrolment."Created_users"."Learning_plan", which stays the record staff
    # edit. Held here because every coach and reporting surface already reads
    # this table and had no way to see what a learner was actually assigned
    # without joining back to enrolment. Kept in step by
    # learning_plan.sync_learning_plan_mirror on every save of the plan; null
    # means no plan has been assigned, which for most learners is the honest
    # answer rather than a missing mirror.
    learning_plan = models.JSONField(null=True, blank=True)
    # Header dates mirror Created_users; the new start column uses capital S.
    learner_start_date = models.TextField(db_column="Learner_Start_date", null=True, blank=True)
    learner_end_date = models.TextField(db_column="Learner_end_date", null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    gateway_review_date = models.DateField(null=True, blank=True)
    alert_notify_for_epa = models.DateField(null=True, blank=True)
    enter_epa = models.DateField(null=True, blank=True)
    # NOT NULL with a now() default in Postgres. Declared bare, Django sent an
    # explicit NULL on insert and the constraint rejected the row, so promoting a
    # learner to Active could not create their mirror. auto_now_add/auto_now make
    # Django supply the timestamps itself.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learners'

    def __str__(self):
        return f"{self.full_name or 'Unnamed'} <{self.email}>"

    @property
    def username(self):
        return self.full_name

    @property
    def group(self):
        return self.group_name

    @property
    def status(self):
        return self.lifecycle_status

    @property
    def ksbs(self):
        try:
            assignment = self.ksb_assignment
        except LearnerKsbAssignment.DoesNotExist:
            assignment = None
        if assignment is not None:
            return [
                {
                    "code": item.code,
                    "number": item.number,
                    "type": item.ksb_type,
                    "description": item.description,
                }
                for item in assignment.profile_version.definitions.all()
            ]

        # Compatibility fallback while existing environments are migrated.
        try:
            return [
                {
                    "code": item.code,
                    "number": item.number,
                    "type": item.ksb_type,
                    "description": item.description,
                }
                for item in self.assigned_ksbs.all()
            ]
        except DatabaseError:
            return []

    @property
    def training_plan(self):
        modules = []
        for module in self.plan_modules.all():
            weeks = []
            for week in module.weeks.all():
                components = [
                    {
                        "componentId": component.component_ref,
                        "componentTitle": component.component_title,
                    }
                    for component in week.components.all()
                ]
                weeks.append(
                    {
                        "weekId": week.week_ref,
                        "weekTitle": week.week_title,
                        "components": components,
                    }
                )
            modules.append(
                {
                    "moduleId": module.module_ref,
                    "moduleTitle": module.module_title,
                    "weeks": weeks,
                }
            )
        return modules

    @property
    def training_plan_progress(self):
        records = []
        # Prefetched, not lazily walked: the body below touches ksb_links twice,
        # quiz_answers once, and two relations under each answer, so plain
        # `.all()` issues a query per entry per relation. Large learner histories
        # can otherwise spend minutes on thousands of round trips in the
        # property every progress and OTJH screen reads.
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        entries = prefetched.get("progress_entries")
        if entries is None:
            entries = self.progress_entries.filter(deleted_at__isnull=True).prefetch_related(
                "ksb_links",
                "quiz_answers__chosen_answers",
                "quiz_answers__correct_answers",
            )
        else:
            # Reuse rows already loaded by learner detail. Calling the related
            # manager's prefetch_related clones its queryset and fetches the
            # whole history again on each projection of the same profile.
            from django.db.models import prefetch_related_objects

            prefetch_related_objects(
                entries, "ksb_links", "quiz_answers__chosen_answers",
                "quiz_answers__correct_answers",
            )
        for entry in entries:
            if entry.kind == "activity_event" or entry.deleted_at is not None:
                continue
            record = {
                "kind": entry.kind,
                "moduleId": entry.module_ref,
                "moduleTitle": entry.module_title,
                "programmeId": entry.programme_ref,
                "programme": entry.programme_title,
                "cohortId": entry.cohort_ref,
                "cohort": entry.cohort_title,
                "groupId": entry.group_ref,
                "group": entry.group_title,
                "weekId": entry.week_ref,
                "weekTitle": entry.week_title,
                "componentId": entry.component_ref,
                "componentTitle": entry.component_title,
                "componentType": entry.component_type,
                "expectedOtjh": float(entry.expected_otjh) if entry.expected_otjh is not None else None,
                "points": entry.points,
                "quizId": _serialise_quiz_ref(entry.quiz_ref),
                "attempt": entry.attempt,
                "grade": float(entry.grade) if entry.grade is not None else None,
                "achievedScore": float(entry.achieved_score) if entry.achieved_score is not None else None,
                "totalScore": float(entry.total_score) if entry.total_score is not None else None,
                "passed": entry.passed,
                "feedback": entry.feedback,
                "reportedTime": entry.reported_time,
                "reflectionSkipped": entry.reflection_skipped,
                "startedAt": entry.started_at.isoformat() if entry.started_at else "",
                "submittedAt": entry.submitted_at.isoformat() if entry.submitted_at else "",
                "timeTaken": entry.time_taken,
                "timeTrackingSource": entry.time_tracking_source,
                "timeTrackingCalculation": entry.time_tracking_calculation,
                "timeTrackingSessionId": entry.time_tracking_session_ref,
                "claimedSeconds": entry.claimed_seconds,
                "serverSessionSeconds": entry.server_session_seconds,
                "verifiedSeconds": entry.verified_seconds,
                "outsideWorkingHours": entry.outside_working_hours,
                "insideWorkingHoursConfirmed": entry.inside_working_hours_confirmed,
                "insideWorkingHoursConfirmedAt": (
                    entry.inside_working_hours_confirmed_at.isoformat()
                    if entry.inside_working_hours_confirmed_at else ""
                ),
                "outsideWorkingHoursConfirmed": entry.outside_working_hours_confirmed,
                "outsideWorkingHoursConfirmedAt": (
                    entry.outside_working_hours_confirmed_at.isoformat()
                    if entry.outside_working_hours_confirmed_at else ""
                ),
                "declaredCompletedAt": (
                    entry.declared_completed_at.isoformat()
                    if entry.declared_completed_at else ""
                ),
                "submissionValidationReason": entry.submission_validation_reason,
                "ksbs": [
                    row.ksb_code
                    for row in entry.ksb_links.all()
                ],
                "ksbMappings": [
                    {
                        "code": row.ksb_code,
                        "description": row.ksb_description,
                        "sourceType": row.source_type,
                        "sourceId": row.source_id,
                        "classification": row.classification,
                        "weight": float(row.weight) if row.weight is not None else 0,
                        "weightClass": row.weight_class,
                        "weight_class": row.weight_class,
                    }
                    for row in entry.ksb_links.all()
                ],
            }
            if entry.kind == "quiz":
                record["questions"] = [
                    {
                        "questionId": answer.question_ref,
                        **(answer.result_snapshot or {}),
                        **({"imageUrl": answer.question_image_url} if answer.question_image_url is not None else {}),
                        # Built from the prefetched rows rather than .exists(),
                        # which would issue its own query per answer and undo
                        # the prefetch above.
                        "chosenAnswerId": (
                            [choice.answer_ref for choice in answer.chosen_answers.all()]
                            or answer.chosen_answer_ref
                        ),
                        "correct": answer.is_correct,
                        "earned": float(answer.earned) if answer.earned is not None else None,
                        "correctAnswerId": [
                            key.answer_ref
                            for key in answer.correct_answers.all()
                        ],
                    }
                    for answer in entry.quiz_answers.all()
                ]
            elif entry.kind == "quiz_reading":
                # This reading belongs to a quiz rather than an authored
                # component. Give Actual Time consumers a stable row identity.
                record["componentId"] = f"quiz-reading:{entry.quiz_ref}:{entry.attempt or entry.pk}"
            records.append({key: value for key, value in record.items() if value not in (None, "")})
        return records

    @property
    def activity_feed(self):
        return self.activity_feed_entries()

    def activity_feed_entries(self, *, newest_first=False):
        prefetched = getattr(self, "_prefetched_objects_cache", None)
        prefetched_progress = (
            prefetched.get("progress_entries")
            if isinstance(prefetched, dict)
            else None
        )
        if prefetched_progress is not None:
            entries = [
                _progress_entry_activity(entry)
                for entry in prefetched_progress
                if str(getattr(entry, "feed_kind", "") or "").strip()
            ]
        else:
            entries = [
                _progress_entry_activity(entry)
                for entry in self.progress_entries.exclude(feed_kind="")
            ]
        entries.sort(key=lambda item: item.get("at") or "", reverse=newest_first)
        return entries

    @property
    def latest_activity_feed(self):
        """Newest-first feed projected from learner_progress_entries."""
        return self.activity_feed_entries(newest_first=True)


class LearnerKsb(models.Model):
    learner = models.ForeignKey(LearnerProfile, on_delete=models.CASCADE, related_name="assigned_ksbs")
    position = models.PositiveIntegerField()
    code = models.CharField(max_length=100)
    number = models.CharField(max_length=100, blank=True)
    ksb_type = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_ksbs'
        ordering = ("position", "id")


class KsbProfileVersion(models.Model):
    source_profile_id = models.CharField(max_length=255)
    version_hash = models.CharField(max_length=64)
    programme = models.TextField(blank=True)
    definition_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'curriculum"."ksb_profile_versions'
        unique_together = (("source_profile_id", "version_hash"),)


class KsbDefinition(models.Model):
    profile_version = models.ForeignKey(
        KsbProfileVersion,
        on_delete=models.CASCADE,
        related_name="definitions",
    )
    position = models.PositiveIntegerField()
    code = models.CharField(max_length=100)
    number = models.CharField(max_length=100, blank=True)
    ksb_type = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'curriculum"."ksb_definitions'
        ordering = ("position", "id")
        unique_together = (("profile_version", "code"),)


class LearnerKsbAssignment(models.Model):
    learner = models.OneToOneField(
        LearnerProfile,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="ksb_assignment",
    )
    profile_version = models.ForeignKey(
        KsbProfileVersion,
        on_delete=models.PROTECT,
        related_name="learner_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_ksb_assignments'


class LearnerTrainingPlanModule(models.Model):
    learner = models.ForeignKey(LearnerProfile, on_delete=models.CASCADE, related_name="plan_modules")
    position = models.PositiveIntegerField()
    module_ref = models.TextField(null=True, blank=True)
    module_title = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_training_plan_modules'
        ordering = ("position", "id")


class LearnerTrainingPlanWeek(models.Model):
    plan_module = models.ForeignKey(
        LearnerTrainingPlanModule,
        on_delete=models.CASCADE,
        related_name="weeks",
    )
    position = models.PositiveIntegerField()
    week_ref = models.TextField(null=True, blank=True)
    week_title = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_training_plan_weeks'
        ordering = ("position", "id")


class LearnerTrainingPlanComponent(models.Model):
    plan_week = models.ForeignKey(
        LearnerTrainingPlanWeek,
        on_delete=models.CASCADE,
        related_name="components",
    )
    position = models.PositiveIntegerField()
    component_ref = models.TextField(null=True, blank=True)
    component_title = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_training_plan_components'
        ordering = ("position", "id")


class LearnerFreeCourse(models.Model):
    """Free courses assigned to a learner — the Learner-schema read mirror.

    The enrolment-side source of truth is EnrolmentUser.free_courses
    (Created_users."Free_courses" jsonb). This flat table mirrors it exactly the
    way learner_training_plan_modules mirrors the module plan: FK to
    LearnerProfile, kept in step by learning_plan.sync_learning_plan_mirror and
    rebuilt at activation. Deliberately carries NO hours, KSBs or component tree
    — a free course is a pure assignment. free_course_ref is the
    curriculum.free_courses id (a plain reference, like module_ref), not a
    cross-schema FK.
    """
    learner = models.ForeignKey(LearnerProfile, on_delete=models.CASCADE, related_name="free_courses")
    position = models.PositiveIntegerField()
    free_course_ref = models.TextField(null=True, blank=True)
    free_course_title = models.TextField(blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_free_courses'
        ordering = ("position", "id")


class LearnerFreeCourseProgress(models.Model):
    """A learner's completion of a single free-course activity.

    Deliberately its OWN store, separate from Training_plan_progress and the KSB
    snapshot: free-course completion is shown on the free-courses page and must
    NEVER enter OTJH totals, the KSB snapshot or the programme's overall
    progress. Keyed by the enrolment learner id (Created_users.id — the same id
    the learner-facing endpoints resolve from the session for both learner
    kinds) and the curriculum component id (FREECOMP-…), so it needs no
    LearnerProfile row (commercial learners may not have one).
    """
    learner_id = models.BigIntegerField()
    component_ref = models.TextField()
    free_course_ref = models.TextField(null=True, blank=True)
    completed_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'Learner"."learner_free_course_progress'
        unique_together = (("learner_id", "component_ref"),)


class LearnerProgressEntry(models.Model):
    learner = models.ForeignKey(LearnerProfile, on_delete=models.CASCADE, related_name="progress_entries")
    entry_order = models.PositiveIntegerField()
    kind = models.CharField(max_length=30)
    programme_ref = models.TextField(null=True, blank=True)
    programme_title = models.TextField(blank=True, default='')
    cohort_ref = models.TextField(null=True, blank=True)
    cohort_title = models.TextField(blank=True, default='')
    group_ref = models.TextField(null=True, blank=True)
    group_title = models.TextField(blank=True, default='')
    module_ref = models.TextField(null=True, blank=True)
    module_title = models.TextField(blank=True)
    week_ref = models.TextField(null=True, blank=True)
    week_title = models.TextField(blank=True)
    component_ref = models.TextField(null=True, blank=True)
    component_title = models.TextField(blank=True)
    component_type = models.CharField(max_length=100, blank=True)
    expected_otjh = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    points = models.IntegerField(null=True, blank=True)
    quiz_ref = models.TextField(null=True, blank=True)
    attempt = models.PositiveIntegerField(null=True, blank=True)
    grade = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    achieved_score = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total_score = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    reported_time = models.TextField(blank=True)
    reflection_skipped = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    time_taken = models.TextField(blank=True)
    time_tracking_source = models.TextField(blank=True, default='')
    time_tracking_calculation = models.TextField(blank=True, default='')
    time_tracking_session_ref = models.TextField(blank=True, default='')
    claimed_seconds = models.PositiveIntegerField(null=True, blank=True)
    server_session_seconds = models.PositiveIntegerField(null=True, blank=True)
    verified_seconds = models.PositiveIntegerField(null=True, blank=True)
    outside_working_hours = models.BooleanField(default=False)
    outside_working_hours_confirmed = models.BooleanField(default=False)
    outside_working_hours_confirmed_at = models.DateTimeField(null=True, blank=True)
    inside_working_hours_confirmed = models.BooleanField(default=False)
    inside_working_hours_confirmed_at = models.DateTimeField(null=True, blank=True)
    # The working instant the learner declared after their real Finish click
    # failed the working rules, and why that click failed. ``submitted_at``
    # keeps the real click and is never rewritten.
    declared_completed_at = models.DateTimeField(null=True, blank=True)
    submission_validation_reason = models.CharField(max_length=32, blank=True, default='')
    feed_kind = models.CharField(max_length=30, blank=True)
    feed_action = models.TextField(blank=True)
    feed_title = models.TextField(blank=True)
    feed_detail = models.TextField(blank=True)
    feed_occurred_at = models.DateTimeField(null=True, blank=True)
    # How this row's activity is tied to Curriculum. Set on every write and
    # backfilled for history by learner_api.0007; see that migration for the
    # full vocabulary. `legacy_component_ref` keeps the pre-repair identifier
    # so a repaired row never loses what it originally pointed at.
    component_link_status = models.TextField(blank=True, default='')
    legacy_component_ref = models.TextField(null=True, blank=True)
    component_link_source = models.TextField(blank=True, default='')
    # Soft-deleted progress remains as historical evidence but is excluded from
    # active learner progress, hours and KSB projections.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_progress_entries'
        ordering = ("entry_order", "id")


class LearnerProgressKsb(models.Model):
    pk = models.CompositePrimaryKey("progress_id", "position")
    progress = models.ForeignKey(LearnerProgressEntry, on_delete=models.CASCADE, related_name="ksb_links")
    position = models.PositiveIntegerField()
    ksb_code = models.CharField(max_length=100)
    ksb_description = models.TextField(blank=True, default='')
    source_type = models.CharField(max_length=32, blank=True, default='')
    source_id = models.TextField(blank=True, default='')
    classification = models.CharField(max_length=32, blank=True, default='')
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    weight_class = models.CharField(max_length=32, blank=True, default='')

    class Meta:
        managed = False
        db_table = 'Learner"."learner_progress_ksbs'
        unique_together = (("progress", "position"),)
        ordering = ("position",)


class LearnerQuizAnswer(models.Model):
    progress = models.ForeignKey(LearnerProgressEntry, on_delete=models.CASCADE, related_name="quiz_answers")
    position = models.PositiveIntegerField()
    question_ref = models.BigIntegerField()
    # NULL means a legacy attempt without a snapshot; '' means no image was shown.
    question_image_url = models.TextField(null=True, blank=True)
    # New attempts retain the reviewed text/key even if the quiz changes later.
    result_snapshot = models.JSONField(null=True, blank=True)
    chosen_answer_ref = models.BigIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    earned = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."learner_quiz_answers'
        ordering = ("position", "id")


class LearnerQuizCorrectAnswer(models.Model):
    pk = models.CompositePrimaryKey("quiz_answer_id", "position")
    quiz_answer = models.ForeignKey(
        LearnerQuizAnswer,
        on_delete=models.CASCADE,
        related_name="correct_answers",
    )
    position = models.PositiveIntegerField()
    answer_ref = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'Learner"."learner_quiz_correct_answers'
        unique_together = (("quiz_answer", "position"),)
        ordering = ("position",)


class LearnerQuizChosenAnswer(models.Model):
    pk = models.CompositePrimaryKey("quiz_answer_id", "position")
    quiz_answer = models.ForeignKey(
        LearnerQuizAnswer,
        on_delete=models.CASCADE,
        related_name="chosen_answers",
    )
    position = models.PositiveIntegerField()
    answer_ref = models.BigIntegerField()

    class Meta:
        managed = False
        db_table = 'Learner"."learner_quiz_chosen_answers'
        unique_together = (("quiz_answer", "position"),)
        ordering = ("position",)


class ActiveUser(models.Model):
    """Unmanaged mapping of "Learner"."Active_users".

    Populated automatically whenever an apprenticeship (EnrolmentUser) or
    commercial (CommercialUser) learner's programme status is set to "Active".
    The `id` column is GENERATED ALWAYS AS IDENTITY, so Django never supplies it
    on insert. Lives in the case-sensitive `Learner` schema of the same Neon DB.
    """

    id = models.AutoField(primary_key=True, db_column="id")

    username = models.TextField(db_column="Username ", null=True, blank=True)  # NB: trailing space
    email = models.TextField(db_column="Email", null=True, blank=True)
    phone_number = models.TextField(db_column="Phone_number", null=True, blank=True)
    programme = models.TextField(db_column="Programme", null=True, blank=True)
    programme_status = models.TextField(db_column="Programme_status", null=True, blank=True)
    cohort = models.TextField(db_column="Cohort", null=True, blank=True)
    group = models.TextField(db_column="Group", null=True, blank=True)
    # Cohort delivery window, copied from curriculum."cohort_authoring_details"
    # (matched by Programme + Cohort name — see cohort_dates in active_users.py).
    # Refreshed on every Active mirror sync.
    start_date = models.DateField(db_column="Start_date", null=True, blank=True)
    end_date = models.DateField(db_column="End_date", null=True, blank=True)
    # Maintained by a database trigger whenever End_date changes.
    alert_notify_for_epa = models.DateField(db_column="Alert_notify_for_EPA", null=True, blank=True)
    enter_epa = models.DateField(db_column="Enter_EPA", null=True, blank=True)
    gateway_review_date = models.DateField(db_column="Gateway_review_date", null=True, blank=True)
    minimum_hours = models.TextField(db_column="Minimum_hours", null=True, blank=True)
    maximum_hours = models.TextField(db_column="Maximum_hours", null=True, blank=True)
    # Coach contact, set per-learner on the delivery "Enrolled learners" page and
    # stored on the mirror only. Preserved across re-syncs because sync_active_user
    # uses UPDATE with a fixed field list that excludes these (see active_users.py);
    # a status toggle to non-Active deletes the row, so coach data is re-entered on
    # reactivation. Real DB columns are lowercase (unlike the rest of this table).
    coach_name = models.TextField(db_column="coach_name", null=True, blank=True)
    coach_email = models.TextField(db_column="coach_email", null=True, blank=True)
    coach_rag = models.TextField(db_column="coach_rag", null=True, blank=True)
    # Total on-the-job hours the learner has completed, summed from the reflection
    # time on their training_plan_progress records. Stored as text (this table
    # keeps all hour columns as text). Recomputed on every progress submit.
    completed_hours = models.TextField(db_column="Completed_hours", null=True, blank=True)
    # Total OTJ hours the learner's training plan plans for (sum of every
    # component's authored expected_otjh). Written on learner-detail load.
    planned_hours = models.TextField(db_column="planned_hours", null=True, blank=True)
    # Cumulative planned hours up to & including the current week (the "target"
    # the learner should have reached by now). Grows week by week.
    target_hours = models.TextField(db_column="Target_hours", null=True, blank=True)
    # completed - target (hours ahead[+]/behind[-] the current-week target).
    progress_hours = models.TextField(db_column="Progress_Hours", null=True, blank=True)
    # (completed - target) / target, as a decimal (e.g. -0.88). '' when target=0.
    progress_variance = models.TextField(db_column="Progress_variance", null=True, blank=True)
    # RAG status derived from progress_variance: "On track" (> -5%),
    # "Need attention" (-15% < v <= -5%), "At risk" (v <= -15%).
    otjh_status = models.TextField(db_column="OTJHoursStatus", null=True, blank=True)

    # --- json columns (SafeJSONField: psycopg3 pre-parses json) ---
    # Structured plan: [{moduleId, moduleTitle, weeks: [{weekId, weekTitle,
    # components: [{componentId, componentTitle}]}]}] — same shape as
    # CommercialUser.training_plan / EnrolmentUser.learning_plan.
    training_plan = SafeJSONField(db_column="Training_plan", null=True, blank=True)
    ksbs = SafeJSONField(db_column="KSBs", null=True, blank=True)
    # Quiz attempts: [{week, attempt, grade, Score, module, passed, quizId, quizName,
    # ksbs, feedback, reportedTime, questions, startedAt, submittedAt, timeTaken}, ...]
    # — legacy read-only store; new attempts go to training_plan_progress.
    weekly_quizzes = SafeJSONField(db_column="Weekly_Quizzes", null=True, blank=True)
    # Legacy JSON mirror retained only for compatibility. Current dashboards
    # project their feed from Learner.learner_progress_entries.
    activity_feed = SafeJSONField(db_column="Activity_Feed", null=True, blank=True)
    # Legacy mirror of the learner progress log. Current dashboards read from
    # Learner.learner_progress_entries via LearnerProfile.training_plan_progress.
    training_plan_progress = SafeJSONField(db_column="Training_plan_progress", null=True, blank=True)

    class Meta:
        managed = False
        # Emitted by Django as "Learner"."Active_users".
        db_table = 'Learner"."Active_users'

    def __str__(self):
        return f"{self.username or 'Unnamed'} <{self.email or 'no-email'}>"


class UnactiveUser(models.Model):
    """Unmanaged mapping of "Learner"."Unactive_users" — the archive for learners
    whose programme status is NOT Active.

    Same shape as ActiveUser so a learner's full row (coach, progress, hours,
    KSBs, plan) is preserved when they leave Active, and restored when they
    return. NB the column names differ slightly from Active_users: Username has
    NO trailing space here, and there is an extra `status` column (the non-Active
    status the learner was moved out under). Move logic lives in active_users.py.
    """

    id = models.AutoField(primary_key=True, db_column="id")

    username = models.TextField(db_column="Username", null=True, blank=True)  # no trailing space (unlike Active_users)
    email = models.TextField(db_column="Email", null=True, blank=True)
    phone_number = models.TextField(db_column="Phone_number", null=True, blank=True)
    programme = models.TextField(db_column="Programme", null=True, blank=True)
    programme_status = models.TextField(db_column="Programme_status", null=True, blank=True)
    cohort = models.TextField(db_column="Cohort", null=True, blank=True)
    group = models.TextField(db_column="Group", null=True, blank=True)
    # Cohort delivery window, mirrored from the Active_users row on archive
    # (copied automatically by _archive_active_user's shared-field loop).
    start_date = models.DateField(db_column="Start_date", null=True, blank=True)
    end_date = models.DateField(db_column="End_date", null=True, blank=True)
    # Maintained by the same database trigger as the active mirror.
    alert_notify_for_epa = models.DateField(db_column="Alert_notify_for_EPA", null=True, blank=True)
    enter_epa = models.DateField(db_column="Enter_EPA", null=True, blank=True)
    gateway_review_date = models.DateField(db_column="Gateway_review_date", null=True, blank=True)
    coach_name = models.TextField(db_column="coach_name", null=True, blank=True)
    coach_email = models.TextField(db_column="coach_email", null=True, blank=True)
    coach_rag = models.TextField(db_column="coach_rag", null=True, blank=True)
    completed_hours = models.TextField(db_column="Completed_hours", null=True, blank=True)
    # The non-Active status the learner currently holds (e.g. "Withdrawn", "On break").
    status = models.TextField(db_column="status", null=True, blank=True)

    training_plan = SafeJSONField(db_column="Training_plan", null=True, blank=True)
    ksbs = SafeJSONField(db_column="KSBs", null=True, blank=True)
    # Legacy archive mirror retained for compatibility; current UI reads from
    # Learner.learner_progress_entries when a learner profile is available.
    training_plan_progress = SafeJSONField(db_column="Training_plan_progress", null=True, blank=True)
    # Legacy mirror retained for archive compatibility; current UI projects the
    # feed from Learner.learner_progress_entries.
    activity_feed = SafeJSONField(db_column="Activity_Feed", null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'Learner"."Unactive_users'

    def __str__(self):
        return f"{self.username or 'Unnamed'} <{self.email or 'no-email'}> [{self.status or '?'}]"


class LearnerAbsence(models.Model):
    """Stored attendance/absence summary used by the coach attendance table."""

    learner_email = models.EmailField(primary_key=True, db_column="learner_email")
    learner_id = models.IntegerField(db_column="learner_id")
    learner_name = models.TextField(db_column="learner_name")
    sessions = models.PositiveIntegerField(db_column="sessions", default=0)
    present = models.PositiveIntegerField(db_column="present", default=0)
    absent = models.PositiveIntegerField(db_column="absent", default=0)
    late = models.PositiveIntegerField(db_column="late", default=0)
    catchup = models.PositiveIntegerField(db_column="catchup", default=0)
    risk = models.CharField(db_column="risk", max_length=16, blank=True)
    last_session_date = models.DateField(db_column="last_session_date", null=True, blank=True)
    consecutive_missed = models.PositiveIntegerField(db_column="consecutive_missed", default=0)
    updated_at = models.DateTimeField(db_column="updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'Learner"."Absence'

    def __str__(self):
        return f"{self.learner_name}: {self.present}/{self.sessions} present"


class EnrolmentReview(models.Model):
    """One row per booked enrolment review — enrolment."Enrolment_Reviews".

    The three onboarding reviews (eligibility / workspace / training plan) live
    operationally in "Coach".coach_calendar_event, which is the calendar both the
    learner and the case owner read. This table is the enrolment-side record of
    them: who booked what, with which officer, and what happened to it.

    Kept deliberately denormalised (names stored alongside ids) because it is a
    compliance record — it must still read correctly after a learner is renamed,
    archived to Unactive_users, or a case owner leaves and their staff row goes.

    Written by learner_api.calendar on book/cancel; created by
    `python manage.py apply_enrolment_reviews_table`.
    """

    STATUS_BOOKED = "booked"
    # Issued by the enrolment team from the learner's board: a review with no
    # calendar booking behind it (see review_form.issue_onboarding_review).
    STATUS_ISSUED = "issued"
    STATUS_CANCELLED = "cancelled"
    STATUS_COMPLETED = "completed"

    STATUS_CHOICES = [
        (STATUS_BOOKED, "Booked"),
        (STATUS_ISSUED, "Issued"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_COMPLETED, "Completed"),
    ]

    id = models.BigAutoField(primary_key=True)
    # Mirrors the coach_calendar_event row this review is booked as, so the two
    # can always be reconciled. Unique: one enrolment row per calendar event.
    event_key = models.TextField(db_column="Event_key", unique=True)
    review_type = models.TextField(db_column="Review_type")
    review_label = models.TextField(db_column="Review_label", blank=True)

    # Learner side. Learner_kind + Learner_id is the console's identity pair
    # (one table cannot key both apprenticeship and commercial learners).
    learner_kind = models.TextField(db_column="Learner_kind")
    learner_id = models.BigIntegerField(db_column="Learner_id")
    learner_name = models.TextField(db_column="Learner_name", blank=True)
    learner_email = models.TextField(db_column="Learner_email", blank=True)

    # Case owner / enrolment officer the review is booked with. Named "Coach_*"
    # to match the vocabulary the rest of the calendar code uses for the event
    # owner, though for these reviews it is the case owner rather than a coach.
    coach_id = models.BigIntegerField(db_column="Coach_id", null=True, blank=True)
    coach_name = models.TextField(db_column="Coach_name", blank=True)
    coach_email = models.TextField(db_column="Coach_email", blank=True)

    # Review details.
    scheduled_date = models.DateField(db_column="Scheduled_date", null=True, blank=True)
    scheduled_time = models.TimeField(db_column="Scheduled_time", null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(db_column="Duration_minutes", default=60)
    status = models.TextField(db_column="Status", choices=STATUS_CHOICES, default=STATUS_BOOKED)
    notes = models.TextField(db_column="Notes", blank=True)

    # Microsoft Teams / Graph outcome. Invite_sent is false when the Graph sync
    # failed, i.e. the slot is held but nobody was actually notified.
    meeting_provider = models.TextField(db_column="Meeting_provider", blank=True)
    meeting_link = models.TextField(db_column="Meeting_link", blank=True)
    graph_event_id = models.TextField(db_column="Graph_event_id", blank=True)
    invite_sent = models.BooleanField(db_column="Invite_sent", default=False)
    sync_error = models.TextField(db_column="Sync_error", blank=True)

    # The review form itself (ILR, Extended ILR, Functional Skills, FS & job role
    # discussion, comments, programme status). One jsonb document rather than ~40
    # columns, for the same reason as Extended_ILR.Answers: it is a compliance
    # questionnaire that gets reworded, and a document absorbs added or renamed
    # questions without DDL. Per-section completion lives in Section_status.
    form_answers = SafeJSONField(db_column="Form_answers", null=True, blank=True)
    section_status = SafeJSONField(db_column="Section_status", null=True, blank=True)
    # Set when the officer clicks Finish, which closes the review.
    form_completed = models.BooleanField(db_column="Form_completed", default=False)
    form_completed_at = models.DateTimeField(db_column="Form_completed_at", null=True, blank=True)
    reviewed_by = models.TextField(db_column="Reviewed_by", blank=True)
    started_at = models.DateTimeField(db_column="Started_at", null=True, blank=True)

    # Sign-off, available once the form is completed. Both sides sign the same
    # review: the learner from their reviews page, staff from the learner's board.
    # The signature itself is a PNG data URL (see SignaturePad), stored as text.
    learner_signature = models.TextField(db_column="Learner_signature", blank=True)
    learner_signed_name = models.TextField(db_column="Learner_signed_name", blank=True)
    learner_signed_at = models.DateTimeField(db_column="Learner_signed_at", null=True, blank=True)
    admin_signature = models.TextField(db_column="Admin_signature", blank=True)
    admin_signed_name = models.TextField(db_column="Admin_signed_name", blank=True)
    admin_signed_at = models.DateTimeField(db_column="Admin_signed_at", null=True, blank=True)
    # Third signing party: the learner's employer. Only asked for on reviews where
    # `employer_signature_required` is set (Health & Safety is employer-facing;
    # the RPL review is not).
    employer_signature = models.TextField(db_column="Employer_signature", blank=True, default="")
    employer_signed_name = models.TextField(db_column="Employer_signed_name", blank=True, default="")
    employer_signed_at = models.DateTimeField(db_column="Employer_signed_at", null=True, blank=True)
    employer_signature_required = models.BooleanField(
        db_column="Employer_signature_required", null=True, blank=True
    )

    booked_at = models.DateTimeField(db_column="Booked_at", null=True, blank=True)
    cancelled_at = models.DateTimeField(db_column="Cancelled_at", null=True, blank=True)
    # NOT NULL in the table with a DEFAULT now(). Django always sends a value for
    # a concrete field, which would override that default with NULL, so these are
    # auto-populated here instead of relying on the database default.
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Enrolment_Reviews'
        ordering = ("-scheduled_date", "review_type")

    def __str__(self):
        return f"{self.review_label or self.review_type} for {self.learner_name or self.learner_id}"


class _ReviewDetail(models.Model):
    """Shared identity for the per-review-type detail tables.

    Each row belongs to one enrolment."Enrolment_Reviews" row, linked by
    Review_id (a real FK, unlike the learner tables' loose id pairing) and also
    carrying Event_key so a row is identifiable without a join.

    These tables sit alongside Enrolment_Reviews.Form_answers rather than
    replacing it: the jsonb document stays the working store the form reads and
    writes section by section, while these give each review type real columns to
    report on. review_tables.sync_review_detail projects one into the other on
    every save.
    """

    id = models.BigAutoField(primary_key=True)
    review = models.ForeignKey(
        EnrolmentReview,
        on_delete=models.CASCADE,
        db_column="Review_id",
        related_name="%(class)s_rows",
    )
    event_key = models.TextField(db_column="Event_key")
    learner_id = models.BigIntegerField(db_column="Learner_id")
    learner_name = models.TextField(db_column="Learner_name", blank=True)
    completed = models.BooleanField(db_column="Completed", default=False)
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        abstract = True


class EligibilityReviewDetail(_ReviewDetail):
    """enrolment."Review_Eligibility" — the Eligibility Review & FS Discussion."""

    # ILR
    over16 = models.TextField(db_column="Over_16", blank=True)
    within_contract_time = models.TextField(db_column="Within_contract_time", blank=True)
    paye_scheme = models.TextField(db_column="PAYE_scheme", blank=True)

    # Extended ILR
    eligible_residency = models.TextField(db_column="Eligible_residency", blank=True)
    identity_documents_seen = models.TextField(db_column="Identity_documents_seen", blank=True)
    eligibility_evidence = models.TextField(db_column="Eligibility_evidence", blank=True)
    right_to_work_england = models.TextField(db_column="Right_to_work_England", blank=True)
    fifty_percent_england = models.TextField(db_column="Fifty_percent_England", blank=True)
    minimum_wage = models.TextField(db_column="Minimum_wage", blank=True)

    # Functional Skills. The assessment lists and per-subject results stay jsonb:
    # they are variable-length collections, not one value per learner.
    initial_assessments = SafeJSONField(db_column="Initial_assessments", null=True, blank=True)
    diagnostic_assessments = SafeJSONField(db_column="Diagnostic_assessments", null=True, blank=True)
    exemption_english = models.TextField(db_column="Exemption_English", blank=True)
    exemption_maths = models.TextField(db_column="Exemption_Maths", blank=True)
    exemption_ict = models.TextField(db_column="Exemption_ICT", blank=True)
    fs_results = SafeJSONField(db_column="FS_results", null=True, blank=True)

    # Functional Skills & Job Role Discussion
    holds_level2 = models.TextField(db_column="Holds_level_2", blank=True)
    level_matches_role = models.TextField(db_column="Level_matches_role", blank=True)
    productive_purpose = models.TextField(db_column="Productive_purpose", blank=True)
    ksb_exposure = models.TextField(db_column="KSB_exposure", blank=True)
    release_for_otj = models.TextField(db_column="Release_for_OTJ", blank=True)
    embed_otj = models.TextField(db_column="Embed_OTJ", blank=True)
    warning_areas = models.TextField(db_column="Warning_areas", blank=True)

    comments = models.TextField(db_column="Comments", blank=True)
    programme_status = models.TextField(db_column="Programme_status", blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Review_Eligibility'

    def __str__(self):
        return f"Eligibility review {self.event_key}"


class RplReviewDetail(_ReviewDetail):
    """enrolment."Review_RPL" — RPL And Experience."""

    # Prior Learning is a list of items, so it stays a document.
    prior_learning_items = SafeJSONField(db_column="Prior_learning_items", null=True, blank=True)

    # Recognition of Prior Learning and Experience
    apprenticeship_appropriate = models.TextField(db_column="Apprenticeship_appropriate", blank=True)
    plan_aligns_standard = models.TextField(db_column="Plan_aligns_standard", blank=True)
    prior_education = models.TextField(db_column="Prior_education", blank=True)
    prior_work_experience = models.TextField(db_column="Prior_work_experience", blank=True)
    plan_needs_adjusting = models.TextField(db_column="Plan_needs_adjusting", blank=True)

    # Personal Learner Record
    uln = models.TextField(db_column="ULN", blank=True)
    reported_attainment = models.TextField(db_column="Reported_attainment", blank=True)
    attainment_english = models.TextField(db_column="Attainment_English", blank=True)
    attainment_maths = models.TextField(db_column="Attainment_Maths", blank=True)
    attainment_ict = models.TextField(db_column="Attainment_ICT", blank=True)

    skills_radar_notes = models.TextField(db_column="Skills_radar_notes", blank=True)
    comments = models.TextField(db_column="Comments", blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Review_RPL'

    def __str__(self):
        return f"RPL review {self.event_key}"


class HealthSafetyReviewDetail(_ReviewDetail):
    """enrolment."Review_Health_Safety" — Workplace Health & Safety Declaration."""

    basic_arrangements = models.TextField(db_column="Basic_arrangements", blank=True)
    day_one_induction = models.TextField(db_column="Day_one_induction", blank=True)
    fire_safety = models.TextField(db_column="Fire_safety", blank=True)
    first_aid = models.TextField(db_column="First_aid", blank=True)
    supervision = models.TextField(db_column="Supervision", blank=True)
    ppe = models.TextField(db_column="PPE", blank=True)
    accident_recording = models.TextField(db_column="Accident_recording", blank=True)
    inform_changes = models.TextField(db_column="Inform_changes", blank=True)
    hs_policy = models.TextField(db_column="HS_policy", blank=True)
    liability_insurance = models.TextField(db_column="Liability_insurance", blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Review_Health_Safety'

    def __str__(self):
        return f"Health & safety review {self.event_key}"


class UlnPrivacyReviewDetail(_ReviewDetail):
    """enrolment."Review_ULN_Privacy" — ULN Privacy Notice & Learner Acknowledgement."""

    # "Yes" once the learner has read the ULN privacy notice / confirmed the
    # acknowledgement; saving each panel records it.
    privacy_notice_read = models.TextField(db_column="Privacy_notice_read", blank=True)
    learner_acknowledged = models.TextField(db_column="Learner_acknowledged", blank=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Review_ULN_Privacy'

    def __str__(self):
        return f"ULN privacy review {self.event_key}"


class Organisation(models.Model):
    """Unmanaged mapping of enrolment."Organisations" — the employing companies.

    Backs the Create menu's "Create organisation profile" path, and is the source
    of the Employer Group picker on the employer form: an employer is a person,
    an organisation is the company they belong to.

    Created by the apply_employer_tables management command; `id` is GENERATED
    ALWAYS AS IDENTITY, so Django never supplies it on insert.
    """

    id = models.AutoField(primary_key=True, db_column="id")

    status = models.TextField(db_column="Status", null=True, blank=True)
    name = models.TextField(db_column="Name", null=True, blank=True)
    owner = models.TextField(db_column="Owner", null=True, blank=True)
    category = models.TextField(db_column="Category", null=True, blank=True)
    # Shown in the Employer Group picker's Group type / Parent name columns.
    group_type = models.TextField(db_column="Group_type", null=True, blank=True)
    parent_name = models.TextField(db_column="Parent_name", null=True, blank=True)

    edrs_ern_number = models.TextField(db_column="EDRS_ERN_number", null=True, blank=True)
    apprenticeship_agreement_id = models.TextField(
        db_column="Apprenticeship_agreement_id", null=True, blank=True
    )

    post_code = models.TextField(db_column="Post_code", null=True, blank=True)
    address_1 = models.TextField(db_column="Address_1", null=True, blank=True)
    address_2 = models.TextField(db_column="Address_2", null=True, blank=True)
    city_town = models.TextField(db_column="City_Town", null=True, blank=True)
    county = models.TextField(db_column="County", null=True, blank=True)
    country = models.TextField(db_column="Country", null=True, blank=True)

    # The form's "Add another session" repeats a {day, start, end} triple, so this
    # is a list rather than a set of columns.
    working_hours = SafeJSONField(db_column="Working_hours", default=list, blank=True)

    contact_name = models.TextField(db_column="Contact_name", null=True, blank=True)
    contact_email = models.TextField(db_column="Contact_email", null=True, blank=True)
    contact_telephone = models.TextField(db_column="Contact_telephone", null=True, blank=True)
    contact_role = models.TextField(db_column="Contact_role", null=True, blank=True)

    website = models.TextField(db_column="Website", null=True, blank=True)
    reference_number = models.TextField(db_column="Reference_number", null=True, blank=True)
    levy_payer = models.TextField(db_column="Levy_payer", null=True, blank=True)
    approx_no_of_employees = models.IntegerField(
        db_column="Approx_no_of_employees", null=True, blank=True
    )
    health_and_safety = models.TextField(db_column="Health_and_safety", null=True, blank=True)
    logo_url = models.TextField(db_column="Logo_url", null=True, blank=True)
    # Nullable rather than defaulting to False: "not yet decided" is a distinct
    # state from "deliberately off" on a form where the field can be left alone.
    send_hours_verification_emails = models.BooleanField(
        db_column="Send_hours_verification_emails", null=True, blank=True
    )

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "enrolment"."Organisations".
        db_table = 'enrolment"."Organisations'

    def __str__(self):
        return f"{self.name or 'Unnamed organisation'} [{self.status or 'no status'}]"


class Employer(models.Model):
    """Unmanaged mapping of enrolment."Employers" — a person at an organisation.

    Backs the Create menu's "Create employer profile" path. The Employer Group
    selection is stored as a jsonb array of Organisation ids, with the names
    denormalised alongside so a list row renders without a join. Not a real FK:
    the control is multi-select and these tables are unmanaged, so a constraint
    would need DDL coordination for no gain.

    Created by the apply_employer_tables management command; `id` is GENERATED
    ALWAYS AS IDENTITY, so Django never supplies it on insert.
    """

    id = models.AutoField(primary_key=True, db_column="id")

    # The user's permanent public identifier, added by apply_user_uuid. The
    # integer pk above stays the internal join key — ~25 columns across three
    # schemas hold it as bigint — so this is the id to expose in APIs and URLs,
    # not to join on. The database fills it (DEFAULT gen_random_uuid()), so it
    # is read back, never written by Django.
    # default= matters as much as the database default: editable=False still
    # puts the field in Django's INSERT, so without it the ORM sends an explicit
    # NULL, overriding DEFAULT gen_random_uuid() and tripping the NOT NULL
    # constraint on every create. An explicit value — a learner profile
    # inheriting the enrolment row's uuid — still wins over this.
    uuid = models.UUIDField(
        db_column="uuid", unique=True, editable=False, default=uuid.uuid4
    )

    first_name = models.TextField(db_column="First_name", null=True, blank=True)
    surname = models.TextField(db_column="Surname", null=True, blank=True)
    gender = models.TextField(db_column="Gender", null=True, blank=True)
    email = models.TextField(db_column="Email", null=True, blank=True)
    mobile = models.TextField(db_column="Mobile", null=True, blank=True)

    post_code = models.TextField(db_column="Post_code", null=True, blank=True)
    address_1 = models.TextField(db_column="Address_1", null=True, blank=True)
    address_2 = models.TextField(db_column="Address_2", null=True, blank=True)
    town_city = models.TextField(db_column="Town_City", null=True, blank=True)
    county = models.TextField(db_column="County", null=True, blank=True)
    country = models.TextField(db_column="Country", null=True, blank=True)

    employer_group_ids = SafeJSONField(db_column="Employer_group_ids", default=list, blank=True)
    employer_group_names = SafeJSONField(db_column="Employer_group_names", default=list, blank=True)

    # The employer's saved signature (PNG data URL, as SignaturePad produces).
    # Offered as the default when they sign a document, the same way a learner
    # reuses the signature captured during enrolment.
    signature = models.TextField(db_column="Signature", blank=True, default="")
    signature_name = models.TextField(db_column="Signature_name", blank=True, default="")
    signature_date = models.DateTimeField(db_column="Signature_date", null=True, blank=True)

    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        # Emitted by Django as "enrolment"."Employers".
        db_table = 'enrolment"."Employers'

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.surname or ''}".strip()

    def __str__(self):
        return f"{self.full_name or 'Unnamed employer'} <{self.email or 'no-email'}>"


class ApprenticeshipAgreement(models.Model):
    """The statutory Apprenticeship Agreement between an apprentice and employer.

    Required by ASCLA 2009 and the Apprenticeships (Miscellaneous Provisions)
    Regulations 2017. It is a contract of service, signed by the apprentice and
    the employer only — the training provider does not sign it (note 6 on the
    DfE form), which is why there is no admin signature here.

    The particulars are SNAPSHOT onto the row when the agreement is issued
    rather than looked up live. Once a party has signed, what they signed must
    not change because someone later edited the learning plan or moved the
    group's dates.

    One 'active' agreement per learner (enforced by a partial unique index);
    reissuing marks the previous one 'superseded' rather than deleting it.

    Table created by the apply_apprenticeship_agreements_table command.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUPERSEDED = "superseded"

    id = models.UUIDField(primary_key=True, db_column="id", default=uuid.uuid4, editable=False)

    learner_kind = models.CharField(db_column="Learner_kind", max_length=32)
    learner_id = models.BigIntegerField(db_column="Learner_id")

    # ---- Particulars, frozen at issue ----
    apprentice_name = models.TextField(db_column="Apprentice_name", blank=True, default="")
    employer_name = models.TextField(db_column="Employer_name", blank=True, default="")
    employer_address = models.TextField(db_column="Employer_address", blank=True, default="")
    standard = models.TextField(db_column="Standard", blank=True, default="")
    start_date = models.DateField(db_column="Start_date", null=True, blank=True)
    end_date = models.DateField(db_column="End_date", null=True, blank=True)
    practical_start = models.DateField(db_column="Practical_start", null=True, blank=True)
    practical_end = models.DateField(db_column="Practical_end", null=True, blank=True)
    duration_weeks = models.DecimalField(
        db_column="Duration_weeks", max_digits=8, decimal_places=1, null=True, blank=True
    )
    planned_otjh = models.DecimalField(
        db_column="Planned_otjh", max_digits=10, decimal_places=2, null=True, blank=True
    )
    # The modules the hours total came from, so the figure stays justifiable.
    plan_modules = SafeJSONField(db_column="Plan_modules", null=True, blank=True)

    # ---- Signatories ----
    apprentice_signature = models.TextField(db_column="Apprentice_signature", blank=True, default="")
    apprentice_signed_name = models.TextField(db_column="Apprentice_signed_name", blank=True, default="")
    apprentice_signed_at = models.DateTimeField(db_column="Apprentice_signed_at", null=True, blank=True)
    employer_signature = models.TextField(db_column="Employer_signature", blank=True, default="")
    employer_signed_name = models.TextField(db_column="Employer_signed_name", blank=True, default="")
    employer_signed_at = models.DateTimeField(db_column="Employer_signed_at", null=True, blank=True)

    fully_signed = models.BooleanField(db_column="Fully_signed", default=False)

    # ---- The rendered PDF ----
    container = models.CharField(db_column="Container", max_length=128, blank=True, default="")
    blob_name = models.CharField(db_column="Blob_name", max_length=512, blank=True, default="")
    doc_path = models.TextField(db_column="Doc_path", blank=True, default="")
    size_bytes = models.BigIntegerField(db_column="Size_bytes", null=True, blank=True)

    status = models.CharField(db_column="Status", max_length=32, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Apprenticeship_Agreements'
        ordering = ("-created_at",)

    @property
    def apprentice_signed(self):
        return bool((self.apprentice_signature or "").strip())

    @property
    def employer_signed(self):
        return bool((self.employer_signature or "").strip())

    def recalculate_signed(self):
        """Both parties must sign. Kept as a method so the rule has one home."""
        self.fully_signed = self.apprentice_signed and self.employer_signed
        return self.fully_signed

    def __str__(self):
        return f"Apprenticeship Agreement for {self.apprentice_name or self.learner_id}"


class IlrDocument(models.Model):
    """The learner's Individual Learner Record, as an issued and signed document.

    The ILR captures identity, eligibility and funding details. Two parties sign
    it: the learner (the learning declaration, confirming their information is
    accurate and they agree to their PLR being shared) and the provider (the
    Provider/Sub-contractor declaration, confirming identity and eligibility
    evidence was seen). The employer has no role and never sees it.

    Like ApprenticeshipAgreement, the content is SNAPSHOT onto the row at issue
    rather than read live from enrolment."Extended_ILR" — a learner editing
    their wizard answers afterwards must not change what was signed.

    Table created by the apply_ilr_documents_table command.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUPERSEDED = "superseded"

    id = models.UUIDField(primary_key=True, db_column="id", default=uuid.uuid4, editable=False)

    learner_kind = models.CharField(db_column="Learner_kind", max_length=32)
    learner_id = models.BigIntegerField(db_column="Learner_id")

    # Page 1 "Learner details" — name, DOB, address, contact, and the ILR
    # identifiers (ULN, NI number, ethnicity...) where we hold them.
    learner_details = SafeJSONField(db_column="Learner_details", null=True, blank=True)
    # The Extended ILR questionnaire, as stored by the wizard.
    answers = SafeJSONField(db_column="Answers", null=True, blank=True)

    learner_signature = models.TextField(db_column="Learner_signature", blank=True, default="")
    learner_signed_name = models.TextField(db_column="Learner_signed_name", blank=True, default="")
    learner_signed_at = models.DateTimeField(db_column="Learner_signed_at", null=True, blank=True)
    provider_signature = models.TextField(db_column="Provider_signature", blank=True, default="")
    provider_signed_name = models.TextField(db_column="Provider_signed_name", blank=True, default="")
    provider_signed_at = models.DateTimeField(db_column="Provider_signed_at", null=True, blank=True)

    fully_signed = models.BooleanField(db_column="Fully_signed", default=False)

    status = models.CharField(db_column="Status", max_length=32, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."ILR_Documents'
        ordering = ("-created_at",)

    @property
    def learner_signed(self):
        return bool((self.learner_signature or "").strip())

    @property
    def provider_signed(self):
        return bool((self.provider_signature or "").strip())

    def recalculate_signed(self):
        """Learner and provider both sign; the employer has no part in an ILR."""
        self.fully_signed = self.learner_signed and self.provider_signed
        return self.fully_signed

    def __str__(self):
        return f"ILR for {self.learner_kind}:{self.learner_id}"


class TrainingPlanDocument(models.Model):
    """The tripartite Training Plan.

    Sets out how the apprentice, the employer and the training provider will
    each support the apprenticeship, and carries the learning plan that delivers
    it. All THREE parties sign — unlike the Apprenticeship Agreement (apprentice
    + employer) or the ILR (learner + provider).

    Each party signs with a name AND a position, which the form prints.

    Content is SNAPSHOT at issue, like the other two documents: editing the
    learning plan afterwards must not rewrite what three parties signed.

    Table created by the apply_training_plans_table command.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUPERSEDED = "superseded"

    id = models.UUIDField(primary_key=True, db_column="id", default=uuid.uuid4, editable=False)

    learner_kind = models.CharField(db_column="Learner_kind", max_length=32)
    learner_id = models.BigIntegerField(db_column="Learner_id")

    # ---- Frozen content ----
    programme = SafeJSONField(db_column="Programme", null=True, blank=True)
    employment = SafeJSONField(db_column="Employment", null=True, blank=True)
    learning_plan = SafeJSONField(db_column="Learning_plan", null=True, blank=True)
    otjh = SafeJSONField(db_column="Otjh", null=True, blank=True)
    epa = SafeJSONField(db_column="Epa", null=True, blank=True)
    contacts = SafeJSONField(db_column="Contacts", null=True, blank=True)

    # ---- Signatories ----
    apprentice_signature = models.TextField(db_column="Apprentice_signature", blank=True, default="")
    apprentice_signed_name = models.TextField(db_column="Apprentice_signed_name", blank=True, default="")
    apprentice_position = models.TextField(db_column="Apprentice_position", blank=True, default="")
    apprentice_signed_at = models.DateTimeField(db_column="Apprentice_signed_at", null=True, blank=True)
    employer_signature = models.TextField(db_column="Employer_signature", blank=True, default="")
    employer_signed_name = models.TextField(db_column="Employer_signed_name", blank=True, default="")
    employer_position = models.TextField(db_column="Employer_position", blank=True, default="")
    employer_signed_at = models.DateTimeField(db_column="Employer_signed_at", null=True, blank=True)
    provider_signature = models.TextField(db_column="Provider_signature", blank=True, default="")
    provider_signed_name = models.TextField(db_column="Provider_signed_name", blank=True, default="")
    provider_position = models.TextField(db_column="Provider_position", blank=True, default="")
    provider_signed_at = models.DateTimeField(db_column="Provider_signed_at", null=True, blank=True)

    fully_signed = models.BooleanField(db_column="Fully_signed", default=False)

    status = models.CharField(db_column="Status", max_length=32, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Training_Plan_Documents'
        ordering = ("-created_at",)

    @property
    def apprentice_signed(self):
        return bool((self.apprentice_signature or "").strip())

    @property
    def employer_signed(self):
        return bool((self.employer_signature or "").strip())

    @property
    def provider_signed(self):
        return bool((self.provider_signature or "").strip())

    def recalculate_signed(self):
        """All three parties must sign a Training Plan."""
        self.fully_signed = (
            self.apprentice_signed and self.employer_signed and self.provider_signed
        )
        return self.fully_signed

    def __str__(self):
        return f"Training Plan for {self.learner_kind}:{self.learner_id}"


class WrittenAgreement(models.Model):
    """The Written Agreement between the employer and the main provider.

    Records the delivery the provider will give, the End Point Assessment
    arrangements, the cost breakdown against the funding band, and the process
    for queries and complaints.

    The printed template is signed by the employer, the EPAO and the provider.
    Here it is signed by the learner, the employer and the provider — the same
    three parties as the Training Plan, so a learner signs all their paperwork
    in one place. Each signs with a name and a job role.

    Content is SNAPSHOT at issue, like the other three documents.

    Table created by the apply_written_agreements_table command.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUPERSEDED = "superseded"

    id = models.UUIDField(primary_key=True, db_column="id", default=uuid.uuid4, editable=False)

    learner_kind = models.CharField(db_column="Learner_kind", max_length=32)
    learner_id = models.BigIntegerField(db_column="Learner_id")

    # ---- Frozen content ----
    particulars = SafeJSONField(db_column="Particulars", null=True, blank=True)
    delivery = SafeJSONField(db_column="Delivery", null=True, blank=True)
    epa = SafeJSONField(db_column="Epa", null=True, blank=True)
    costs = SafeJSONField(db_column="Costs", null=True, blank=True)
    contacts = SafeJSONField(db_column="Contacts", null=True, blank=True)

    # ---- Signatories ----
    learner_signature = models.TextField(db_column="Learner_signature", blank=True, default="")
    learner_signed_name = models.TextField(db_column="Learner_signed_name", blank=True, default="")
    learner_position = models.TextField(db_column="Learner_position", blank=True, default="")
    learner_signed_at = models.DateTimeField(db_column="Learner_signed_at", null=True, blank=True)
    employer_signature = models.TextField(db_column="Employer_signature", blank=True, default="")
    employer_signed_name = models.TextField(db_column="Employer_signed_name", blank=True, default="")
    employer_position = models.TextField(db_column="Employer_position", blank=True, default="")
    employer_signed_at = models.DateTimeField(db_column="Employer_signed_at", null=True, blank=True)
    provider_signature = models.TextField(db_column="Provider_signature", blank=True, default="")
    provider_signed_name = models.TextField(db_column="Provider_signed_name", blank=True, default="")
    provider_position = models.TextField(db_column="Provider_position", blank=True, default="")
    provider_signed_at = models.DateTimeField(db_column="Provider_signed_at", null=True, blank=True)

    fully_signed = models.BooleanField(db_column="Fully_signed", default=False)

    status = models.CharField(db_column="Status", max_length=32, default=STATUS_ACTIVE)
    created_at = models.DateTimeField(db_column="Created_at", auto_now_add=True)
    updated_at = models.DateTimeField(db_column="Updated_at", auto_now=True)

    class Meta:
        managed = False
        db_table = 'enrolment"."Written_Agreements'
        ordering = ("-created_at",)

    @property
    def learner_signed(self):
        return bool((self.learner_signature or "").strip())

    @property
    def employer_signed(self):
        return bool((self.employer_signature or "").strip())

    @property
    def provider_signed(self):
        return bool((self.provider_signature or "").strip())

    def recalculate_signed(self):
        """All three parties must sign."""
        self.fully_signed = (
            self.learner_signed and self.employer_signed and self.provider_signed
        )
        return self.fully_signed

    def __str__(self):
        return f"Written Agreement for {self.learner_kind}:{self.learner_id}"


# Kept in its own module so checkpoint storage has no progress-table side effects.
from .timing_drafts import ActivityTimingDraft  # noqa: E402,F401
