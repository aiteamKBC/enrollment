"""JSON API for enrolment users (no DRF — plain Django views).

Routes (mounted under /learner_api/ in config/urls.py):
    GET  /learner_api/enrolment-users/            -> {count, results:[UserListRow]}
    POST /learner_api/enrolment-users/            -> create; returns the new UserListRow
    GET  /learner_api/enrolment-users/options/    -> canonical dropdown option lists
    GET  /learner_api/enrolment-users/<id>/       -> EnrolmentBoard
    PATCH/PUT /learner_api/enrolment-users/<id>/  -> update flat fields; returns board
    GET  /learner_api/staff-users/                -> {count, results:[staff rows]}
    POST /learner_api/staff-users/                -> create an admin/staff account

Authentication: write methods require an authenticated staff or admin session —
see ``@staff_only(writes_only=True)`` on each view and
``login.permissions.staff_only`` for why the read paths are not gated yet.
Django's CSRF middleware is exempted because these are JSON endpoints; the
cross-site protection is the ``X-Requested-With`` header the login API also
requires, plus the SameSite=Lax session cookie.
"""
import json

import logging

from django.db import DatabaseError, transaction
from django.db.models.functions import Lower, Trim
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from login.permissions import staff_only
from login.permissions import learner_self_or_staff
from login.permissions import require_access
from login.models import LoginAccount

from .active_users import (
    PLACEMENT_SOURCE_FIELDS,
    cohort_delivery_window,
    mirror_learner_placement,
    mirror_placement_to_enrolment,
    replace_training_plan,
    stamp_cohort_window,
    sync_active_user,
)
from .identity import learner_profile_for_source
from .account_deletion import delete_learner_account
from .learner_dates import save_enrolment_fields
from .coach_assignment import case_owner_coach, current_coach
from .directory import learner_directory_queryset
from .learner_progression import ACTIVE_STATUS, advance_learner
from login.services import sync_account

#: Fields on a learner row that the login account keeps a copy of. A change to
#: any of them has to be pushed to that copy — see login.identity.ensure_account.
ACCOUNT_IDENTITY_FIELDS = ("email", "username", "preferred_name")
#: The same for a staff row, where the role follows position and access.
STAFF_IDENTITY_FIELDS = ("email", "username", "preferred_name", "position", "access")
from .constants import (
    ACCESS_SUPER_ADMIN,
    STATUS_CHOICES,
    TYPE_CHOICES,
    PROGRAMME_STATUS_CHOICES,
    DEFAULT_PROGRAMME_STATUS,
    POSITION_CHOICES,
    LEARNER_TYPE_CHOICES,
)
from .first_session import book_first_session, parse_slot, record_start_date_only, validate_slot
from .mappers import (
    ValidationError,
    _s,
    restrict_to_self_writable,
    to_board,
    to_commercial_row,
    to_edit_fields,
    to_list_row,
    to_staff_row,
    write_commercial_fields,
    write_fields,
    validate_learner_dates,
    write_staff_fields,
)
from .models import CommercialUser, Employer, EnrolmentUser, LearnerProfile, LearnerTrainingPlanModule, StaffUser
from .constants import ACCESS_TUTOR
from .progress_rules import progress_record_counts_as_achieved
from .teams_attendance import fetch_verified_teams_attendance_rows

logger = logging.getLogger(__name__)


def _learner_profiles_with_plan():
    """Load a learner list and its nested plan in four queries, not one per row."""
    return LearnerProfile.objects.prefetch_related(
        'plan_modules__weeks__components',
    )


def _tutor_number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _tutor_percent(numerator, denominator):
    denominator_value = _tutor_number(denominator)
    if denominator_value <= 0:
        return 0
    return max(0, min(100, round((_tutor_number(numerator) / denominator_value) * 100)))


def _tutor_display_date(value):
    if not value:
        return ''
    if hasattr(value, 'day') and hasattr(value, 'strftime'):
        return f"{value.day} {value.strftime('%b')}"
    return str(value)[:10]


def _tutor_live_learner_row(profile, attendance_rate=0, allowed_module_ids=None):
    """Project Learner.learners into the compact My Learners card shape."""
    allowed_module_ids = None if allowed_module_ids is None else {
        str(module_id).strip()
        for module_id in allowed_module_ids
        if str(module_id).strip()
    }
    progress_entries = [
        entry for entry in (profile.training_plan_progress or [])
        if isinstance(entry, dict)
        and (
            allowed_module_ids is None
            or str(entry.get('moduleId') or '').strip() in allowed_module_ids
        )
    ]
    plan_modules = []
    planned_components = 0
    for module in profile.plan_modules.all():
        module_ref = str(module.module_ref or '').strip()
        if allowed_module_ids is not None and module_ref not in allowed_module_ids:
            continue
        module_name = (module.module_title or '').strip()
        if module_name:
            plan_modules.append({
                'id': module_ref or str(module.pk),
                'name': module_name,
            })
        for week in module.weeks.all():
            planned_components += len(list(week.components.all()))

    completed_keys = set()
    for index, entry in enumerate(progress_entries):
        if not progress_record_counts_as_achieved(entry):
            continue
        key = entry.get('quizId') or entry.get('componentId') or (
            entry.get('moduleTitle'), entry.get('weekTitle'), entry.get('componentTitle'), index
        )
        completed_keys.add(str(key))

    completed_components = len(completed_keys)
    component_progress = _tutor_percent(completed_components, planned_components)

    otjh_hours = _tutor_number(profile.completed_hours)
    otjh_target = (
        _tutor_number(profile.target_hours)
        or _tutor_number(profile.planned_hours)
        or _tutor_number(profile.minimum_hours)
    )
    otjh_progress = _tutor_percent(otjh_hours, otjh_target)

    ksb_items = list(profile.ksbs or [])
    ksb_targets = {
        str(item.get('code') or '').strip().upper()
        for item in ksb_items
        if isinstance(item, dict) and str(item.get('code') or '').strip()
    }
    completed_ksbs = {
        str(code).strip().upper()
        for entry in progress_entries
        if progress_record_counts_as_achieved(entry)
        for code in (entry.get('ksbs') or [])
        if str(code).strip()
    }
    validated_ksbs = len(ksb_targets & completed_ksbs)
    ksb_progress = _tutor_percent(validated_ksbs, len(ksb_targets))

    evidence_entries = [
        entry for entry in progress_entries
        if 'evidence' in ' '.join(
            str(entry.get(field) or '')
            for field in ('kind', 'title', 'action', 'detail')
        ).lower()
    ]
    latest_activity = max(
        (
            str(entry.get('submittedAt') or entry.get('startedAt') or '')
            for entry in progress_entries
            if entry.get('submittedAt') or entry.get('startedAt')
        ),
        default='',
    )
    last_active = _tutor_display_date(latest_activity) or _tutor_display_date(profile.updated_at)

    if (
        'atrisk' in (profile.otjh_status or '').replace(' ', '').lower()
        or (ksb_targets and ksb_progress < 25)
        or (otjh_target and otjh_progress < 25)
    ):
        risk_level = 'high'
    elif (
        (ksb_targets and ksb_progress < 60)
        or (otjh_target and otjh_progress < 60)
        or (planned_components and component_progress < 40)
    ):
        risk_level = 'medium'
    else:
        risk_level = 'low'

    return {
        'id': str(profile.id),
        'name': (profile.full_name or '').strip() or 'Unnamed learner',
        'email': (profile.email or '').strip(),
        'programme': (profile.programme or '').strip(),
        'cohort': (profile.cohort or '').strip(),
        'group': (profile.group_name or '').strip(),
        'modules': plan_modules,
        'progress': component_progress if planned_components else otjh_progress,
        'attendance': attendance_rate,
        'evidenceSubmitted': len(evidence_entries),
        'evidenceRequired': planned_components,
        'lastActive': last_active,
        'riskLevel': risk_level,
        'ksbStatus': f'{validated_ksbs} of {len(ksb_targets)} validated' if ksb_targets else 'Not available',
        'ksbCompleted': validated_ksbs,
        'ksbTarget': len(ksb_targets),
        'otjhHours': otjh_hours,
        'otjhTarget': otjh_target,
    }


@require_access(ACCESS_TUTOR)
@require_GET
def tutor_learners(request):
    """Return active learner profiles for the Tutor > My Learners screen."""
    try:
        requested_module_ids = {
            str(module_id).strip()
            for module_id in (
                request.GET.getlist('moduleIds')
                or request.GET.getlist('module_ids')
            )
            if str(module_id).strip()
        }
        if not requested_module_ids:
            return JsonResponse({'count': 0, 'learners': []})

        # The learner training-plan table is the source of module membership.
        # Resolve learner IDs from it first, then load the learner profiles for
        # the performance fields shown in each module card.
        plan_learner_ids = (
            LearnerTrainingPlanModule.objects.using('enrolment')
            .filter(
                module_ref__in=requested_module_ids,
                learner__lifecycle_status__iexact='active',
            )
            .values_list('learner_id', flat=True)
            .distinct()
        )
        profiles = (
            LearnerProfile.objects.using('enrolment')
            .filter(id__in=plan_learner_ids, lifecycle_status__iexact='active')
            .prefetch_related(
                'ksb_assignment__profile_version__definitions',
                'plan_modules__weeks__components',
                'progress_entries__ksb_links',
            )
            .distinct()
            .order_by('full_name', 'id')
        )
        profiles = list(profiles)
        attendance_by_learner = {}
        try:
            attendance_rows = fetch_verified_teams_attendance_rows(
                [profile.id for profile in profiles],
                module_refs=list(requested_module_ids),
            )
            grouped_attendance = {}
            for row in attendance_rows:
                learner_id = str(row.get('learner_id') or '')
                if not learner_id:
                    continue
                summary = grouped_attendance.setdefault(learner_id, {'sessions': 0, 'present': 0})
                summary['sessions'] += 1
                if str(row.get('attendance_status') or '').strip().lower() in {'present', 'late'}:
                    summary['present'] += 1
            attendance_by_learner = {
                learner_id: round((summary['present'] / summary['sessions']) * 100)
                for learner_id, summary in grouped_attendance.items()
                if summary['sessions']
            }
        except DatabaseError:
            logger.warning('tutor_learners_attendance_unavailable', exc_info=True)

        learners = [
            _tutor_live_learner_row(
                profile,
                attendance_by_learner.get(str(profile.id), 0),
                requested_module_ids,
            )
            for profile in profiles
        ]
    except DatabaseError as exc:
        logger.exception('tutor_learners_load_failed')
        return _error(f'Database error: {exc}', 503)

    return JsonResponse({'count': len(learners), 'learners': learners})


def _parse_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"Invalid JSON body: {exc}")


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _annotate_account_state(rows, subject_type):
    """Mark each directory row with whether that person can already sign in.

    Creating somebody provisions an account but sends no email, so the
    directory needs to distinguish three states: no account at all (provisioning
    failed, or the record predates that change), an account awaiting its
    invitation, and somebody already onboarded. Only the first two are worth
    offering an invitation for.

    Best-effort: a lookup failure leaves the flags absent and the button simply
    does not appear, which is better than blocking the directory over it.
    """
    from django.db import DatabaseError, connections

    ids = [int(row["id"]) for row in rows if str(row.get("id") or "").isdigit()]
    if not ids:
        return
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                'select "Subject_id", "Password_set_at" is not null '
                'from login."Login_accounts" '
                'where "Subject_type" = %s and "Subject_id" = any(%s)',
                [subject_type, ids],
            )
            state = {int(row[0]): bool(row[1]) for row in cur.fetchall()}
    except DatabaseError:
        logger.warning("Could not read account state for the %s directory", subject_type,
                       exc_info=True)
        return

    for row in rows:
        if not str(row.get("id") or "").isdigit():
            continue
        onboarded = state.get(int(row["id"]))
        row["hasAccount"] = onboarded is not None
        row["hasSignedIn"] = bool(onboarded)


def _send_platform_invitation(request, subject_type, subject_id, subject=None,
                              send_email=False):
    """Provision a just-created person's platform account.

    ``send_email`` defaults to False: creating somebody gives them an account,
    but the invitation email is sent deliberately from the Accounts page in the
    Super Admin workspace, by somebody who has looked at the record. It used to
    go out automatically on save, which meant a typo in an address, or a record
    created to be finished later, put a live set-password link in an inbox
    before anyone had checked it.

    Imported lazily so learner_api keeps no import-time dependency on the
    login app — the two are wired together at the URL layer, and a circular
    import here would be easy to introduce and annoying to unpick.

    Never raises: see login.services.invite_subject. The returned dict rides
    along on the 201 so the console can say "created, but the invitation email
    could not be sent" instead of silently doing nothing.

    Authorisation matters here and is *not* assumed. These creation endpoints
    carry no auth decorator of their own, so the signed-in account is passed
    down and ``invite_subject`` refuses an anonymous or under-privileged caller.
    Without that, an unauthenticated POST naming ``position: "Admin"`` would
    mint an admin credential and email the set-password link to whatever address
    the request supplied.
    """
    from login.services import invite_subject
    from login.security import client_ip, user_agent

    inviter = getattr(request, "login_account", None)
    return invite_subject(
        subject_type,
        subject_id,
        subject=subject,
        inviter=inviter,
        invited_by=inviter.email if inviter else None,
        ip=client_ip(request),
        user_agent=user_agent(request),
        send_email=send_email,
    )


def _check_employer_id(fields):
    """Reject an "Employer_id" that names no employer record.

    The column is a plain integer rather than a real FK (these tables are
    unmanaged), so this is where referential integrity is actually enforced.
    Clearing it to None is always allowed.
    """
    employer_id = fields.get("employer_id")
    if employer_id is None:
        return
    if not Employer.objects.filter(pk=employer_id).exists():
        raise ValidationError(f"Unknown employerId: {employer_id}")


def _profile_is_apprenticeship(profile):
    """Whether this profile is an apprenticeship learner.

    ``learner_type`` is the stored answer, copied from the enrolment record, and
    is trusted whenever it is set.

    The name-based guess below is the old rule, kept only for profiles that have
    no enrolment record to read the type from (see apply_learner_type_column's
    untyped list). It is a guess: a commercial learner on a programme called
    "Apprenticeship Skills" would be misfiled by it, which is why the column
    exists.
    """
    stored = str(getattr(profile, "learner_type", "") or "").strip().casefold()
    if stored:
        return stored == "apprenticeship"

    programme = str(profile.programme or "").strip().casefold()
    lifecycle = str(profile.lifecycle_status or "").strip().casefold()
    return "apprentice" in programme or programme.startswith("apm") or lifecycle == "onboarding"


def _profile_commercial_row(profile):
    training_plan = profile.training_plan
    return {
        "id": str(profile.id),
        "username": profile.full_name or "",
        "email": profile.email or "",
        "phone": profile.phone_number or "",
        "employer": "",
        "lineManager": "",
        "organization": "",
        "programmeStatus": profile.programme_status or "",
        "programme": profile.programme or "",
        "cohort": profile.cohort or "",
        "group": profile.group_name or "",
        "modules": ", ".join(item.get("moduleTitle", "") for item in training_plan if item.get("moduleTitle")),
        "weeks": "",
        "components": "",
        "trainingPlan": training_plan,
    }


def _profile_enrolment_row(profile):
    lifecycle = str(profile.lifecycle_status or "").strip()
    subscription_status = "FullUser" if lifecycle.casefold() == "active" else lifecycle or "Invited"
    return {
        "id": str(profile.id),
        "name": profile.full_name or "",
        "type": "User",
        "email": profile.email or "",
        "group": profile.group_name or "",
        "subscriptionStatus": subscription_status,
        "subscriptionVerified": subscription_status.casefold() == "fulluser",
        "learningPlan": True,
        "programmeStatus": profile.programme_status or "",
        # Kept in step with mappers.to_list_row, which the directory also reads
        # from — a row missing the key would render blank instead of the name.
        "programme": profile.programme or "",
        "cohort": profile.cohort or "",
        "notesCount": 0,
        "hasTasks": True,
        "reference": "",
    }


def _profile_enrolment_board(profile):
    return {
        "user": {"id": str(profile.id), "name": profile.full_name or "", "reference": "", "owner": ""},
        "contact": {
            "email": profile.email or "",
            "phone": profile.phone_number or "",
            "dob": "",
            "groupMembership": profile.group_name or "",
            "signatureUrl": None,
            "hasMandate": False,
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
            "name": profile.programme or "",
            "cohort": profile.cohort or "",
            "status": profile.programme_status or DEFAULT_PROGRAMME_STATUS,
            "startDate": profile.start_date.isoformat() if profile.start_date else "",
            "endDate": profile.end_date.isoformat() if profile.end_date else "",
            "enrolledAt": "",
            "enrolledBy": "",
            "onboardingStatus": "Not started",
            "onboardingCompletedAt": None,
        },
        "subProgrammes": [],
        "aims": [],
        "previousProgrammes": [],
        "functionalSkills": {
            "english": {"assessments": [], "exempt": False, "evidence": []},
            "maths": {"assessments": [], "exempt": False, "evidence": []},
            "ict": {"assessments": [], "exempt": False, "evidence": []},
        },
        "managedJobs": [],
        "tracker": [],
        "milestones": [],
        "notes": [],
        "courseProgress": [],
        "contacts": [],
        "activities": [],
        "complianceDocuments": [],
        "reviewDocuments": [],
        "documents": [],
        "competencies": [],
        "subscription": {"startDate": "", "endDate": "", "status": profile.lifecycle_status or ""},
        "auditTrail": [],
        "trainingPlan": profile.training_plan,
    }


def _update_profile_from_delivery_payload(profile, payload):
    field_map = {
        "username": "full_name",
        "email": "email",
        "phone": "phone_number",
        "programmeStatus": "programme_status",
        "programme": "programme",
        "cohort": "cohort",
        "group": "group_name",
    }
    update_fields = []
    for payload_key, model_field in field_map.items():
        if payload_key not in payload:
            continue
        value = str(payload.get(payload_key) or "").strip()
        setattr(profile, model_field, value)
        update_fields.append(model_field)
    # email_normalized is GENERATED ALWAYS in Postgres — the database derives it
    # from `email`, and naming it in a write is rejected outright. See the field's
    # note on LearnerProfile.
    if update_fields:
        profile.updated_at = timezone.now()
        update_fields.append("updated_at")
        profile.save(update_fields=update_fields)
        # This screen edits the profile, but the enrolment row is what enrolment
        # views, the compliance documents and progression read. A placement
        # changed here has to reach it, with the cohort's dates.
        if any(field in update_fields for field in ("programme", "cohort", "group_name")):
            learner = mirror_placement_to_enrolment(profile)
            # And the same check the enrolment screens run after a placement: a
            # learner given their dates here is otherwise fully eligible with
            # nothing left to re-run it for them. advance_learner enforces each
            # kind's own gates, so this cannot skip an apprentice's documents.
            if learner is not None:
                advance_learner(learner)
    if "trainingPlan" in payload:
        plan = payload.get("trainingPlan")
        if not isinstance(plan, list):
            raise ValidationError("trainingPlan must be a list.")
        replace_training_plan(profile, plan)


def _create_profile_from_delivery_payload(payload, *, apprenticeship):
    email = str(payload.get("email") or "").strip()
    name = str(payload.get("username") or payload.get("name") or "").strip()
    if not email:
        raise ValidationError("email is required.")
    if not name:
        raise ValidationError("username is required.")
    programme_status = str(payload.get("programmeStatus") or "").strip()
    requested_status = str(payload.get("status") or "").strip().casefold()
    lifecycle_status = (
        "active"
        if requested_status in {"active", "fulluser"} or programme_status.casefold() == "active"
        else ("onboarding" if apprenticeship else "inactive")
    )
    # enrolment_id is deliberately left null: this path creates a profile
    # straight from a payload, with no enrolment."Created_users" row behind it,
    # so there is no enrolment id to record. Inventing one would be worse than
    # the honest null — apply_learner_enrolment_id reports these as unlinked.
    #
    # email_normalized is GENERATED ALWAYS in Postgres and created_at/updated_at
    # are auto_now_add/auto_now — Django and the DB supply all three, so naming
    # them here would be rejected on insert.
    return LearnerProfile.objects.create(
        full_name=name,
        email=email,
        phone_number=str(payload.get("phone") or "").strip(),
        lifecycle_status=lifecycle_status,
        programme=str(payload.get("programme") or "").strip(),
        programme_status=programme_status,
        cohort=str(payload.get("cohort") or "").strip(),
        group_name=str(payload.get("group") or "").strip(),
    )


@csrf_exempt
# A5/A4: the owner learner OR staff may READ their coach contact; only staff may
# SET it (the learner-write block below). Previously staff_only(writes_only=True)
# left the GET open to any authenticated caller (coach name/email of any learner).
@learner_self_or_staff(kwarg="pk")
def learner_coach(request, pk):
    """Read/update the coach on the enrolment record and its linked profile.

    The Owner picker and the edit form's Case owner represent one assignment.
    Persist both name and email on the source so reactivation preserves them.
    This picker is available only while the learner is Active.

        GET   /learner_api/learners/<id>/coach/   -> {coachName, coachEmail}
        PATCH /learner_api/learners/<id>/coach/   -> {coachName?, coachEmail?} -> same

    """
    try:
        source = EnrolmentUser.all_learners.filter(pk=pk).first()
        active = (
            learner_profile_for_source(source, pk, active_only=True)
            if source is not None
            else None
        )
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if active is None:
        return _error("No active learner record. Coach can be set once the learner is Active.", 404)

    if request.method == "GET":
        try:
            contact = current_coach(source, active)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"coachName": contact["coach_name"], "coachEmail": contact["coach_email"]})

    if request.method in ("PATCH", "PUT"):
        # A coach is assigned by staff from the learner's board, never by the
        # learner themselves — the gate admits the owner learner for the GET, so
        # writes are refused here.
        account = getattr(request, "login_account", None)
        if account is not None and account.role == "learner":
            return _error("Only staff can set a learner's coach.", 403)
        try:
            payload = _parse_body(request)
        except ValidationError as exc:
            return _error(str(exc), 400)

        # Empty string, never None: both columns on "Learner"."learners" are
        # NOT NULL, so coercing a cleared field to NULL made unassigning a coach
        # fail with an IntegrityError. "" is the table's own "no coach" value —
        # the model declares blank=True and every reader already treats a blank
        # as unassigned.
        update = {}
        if "coachName" in payload:
            update["coach_name"] = str(payload["coachName"] or "").strip()
        if "coachEmail" in payload:
            update["coach_email"] = str(payload["coachEmail"] or "").strip()
        if not update:
            return _error("Provide coachName and/or coachEmail.", 400)

        try:
            if "coach_email" not in update:
                update = case_owner_coach(update["coach_name"], source)
            elif "coach_name" not in update:
                if not update["coach_email"]:
                    update["coach_name"] = ""
                else:
                    staff = list(StaffUser.objects.filter(email__iexact=update["coach_email"]).only("username")[:2])
                    if len(staff) != 1:
                        return _error("Provide coachName with this coachEmail.", 400)
                    update["coach_name"] = staff[0].username or ""
            update["case_owner"] = update["coach_name"]
            for attr, value in update.items():
                setattr(source, attr, value)
            handover = save_enrolment_fields(source, update)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"coachName": update["coach_name"], "coachEmail": update["coach_email"],
                             **(handover or {})})

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: the learner directory (PII) — staff/admin only, reads too.
def enrolment_users(request):
    """The single learner collection — both kinds live in one table.

    GET  ?learnerType=apprenticeship|commercial narrows the listing; omitted
         returns every learner regardless of kind.
    POST accepts "learnerType" to say which kind to create (default
         apprenticeship). Both land in enrolment."Created_users".
    """
    if request.method == "GET":
        wanted = (request.GET.get("learnerType") or "").strip()
        if wanted and wanted not in LEARNER_TYPE_CHOICES:
            return _error(
                f"Invalid learnerType: {wanted!r}. Allowed: {', '.join(LEARNER_TYPE_CHOICES)}", 400
            )
        try:
            # Use the shared projection and its mapper annotation together.
            # A duplicate query with a different annotation name made the
            # mapper fetch the deferred plan documents again for every learner.
            learners = list(learner_directory_queryset(wanted))
            # Keep this collection read bounded: advancing every learner here
            # performs up to four compliance-document queries per row and can
            # exceed the database statement timeout on a directory-sized list.
            # Progression still runs on document/signature and learner updates,
            # and the scheduled ``advance_learner_statuses`` sweep handles
            # date-driven transitions between reads.
            rows = [to_list_row(u) for u in learners]
            # Whether each person already has a sign-in account, so the
            # directory can offer "Send invitation" only where it applies.
            # Batched deliberately: asking per row would be one query per
            # learner across the whole directory.
            _annotate_account_state(rows, "learner")
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"count": len(rows), "results": rows})

    if request.method == "POST":
        try:
            payload = _parse_body(request)
            fields = write_fields(payload, require_create=True)
            _check_employer_id(fields)
        except ValidationError as exc:
            return _error(str(exc), 400)

        # The first session is arranged while the learner is created, so both
        # the slot and the case owner who will host it are checked *before*
        # anything is written. Enrolling somebody and only then discovering the
        # date was a bank holiday would leave a learner with no session and no
        # obvious way to tell.
        session_date, session_time, slot_error = parse_slot(payload)
        if slot_error:
            return _error(slot_error, 400)
        if session_date is not None:
            slot_problem = validate_slot(session_date, session_time)
            if slot_problem:
                return _error(slot_problem, 400)
            if not _s(fields.get("case_owner")):
                return _error(
                    "A case owner is required to book the learner's first session.", 400
                )

        # The one and only write. It runs after the slot checks above so a bad
        # date is refused before a learner exists, which is what those checks
        # are for -- an earlier duplicate call here created the learner first,
        # then raised on its own email guard when this one ran, returning 500
        # for a learner that had in fact been created.
        try:
            row = _create_enrolment_user(
                request, fields, session_date=session_date, session_time=session_time,
            )
        except ValidationError as exc:
            return _error(str(exc), 400)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse(row, status=201)

    return _error("Method not allowed.", 405)


def _create_enrolment_user(request, fields, *, require_account=False,
                           session_date=None, session_time=None):
    """Shared single/bulk creation, including placement and account setup.

    Bulk callers hold an enrolment transaction and require account failures
    to abort that transaction rather than leave a partially imported file.

    ``session_date``/``session_time`` book the learner's first session with their
    case owner. Both callers validate the slot before reaching here, so this only
    performs the booking; a learner imported from Aptem is skipped inside
    ``book_first_session``.
    """
    fields = dict(fields)

    # The login account is pinned to one Created_users primary key. Creating a
    # second source row for the same email can therefore leave the account on
    # one row while the permanent LearnerProfile remains linked to the other.
    # Compare a trimmed, case-insensitive value so legacy spacing/case cannot
    # bypass the guard. The database's owner-run unique index remains the final
    # protection against two truly concurrent requests.
    email = str(fields.get('email') or '').strip()
    if email:
        existing = (
            EnrolmentUser.all_learners
            .annotate(_normalised_email=Lower(Trim('email')))
            .filter(_normalised_email=email.casefold())
            .only('id')
            .first()
        )
        if existing is not None:
            raise ValidationError(
                'A learner with this email already exists. Update the existing learner instead.'
            )

    # Stamp who/when enrolled, server-side.
    stamp = timezone.now().strftime("%d/%m/%Y %H:%M:%S")
    fields.setdefault("enrolled_time_and_user", f"{stamp} by Enrolment Officer")

    # Every learner gets a platform account on enrolment — the form no
    # longer asks, and a caller cannot opt out by sending the flag as false.
    # Assignment rather than setdefault for exactly that reason: the column
    # records what the platform does, not a choice somebody made on a form.
    # The invitation *email* is a separate, deliberate step from the
    # Accounts page; this flag is about entitlement, not delivery.
    fields["invite_to_platform"] = True

    # Stamp the cohort's delivery window from the authored cohort table. The
    # cohort carries two end dates: end_date/practical_period_end_date close
    # the practical period, and apprenticeship_end_date adds the cohort's EPA
    # period on top. These columns are text on this table, so the dates are
    # written as ISO strings.
    start, practical_end, apprenticeship_end = cohort_delivery_window(
        fields.get("programme"), fields.get("cohort")
    )
    if start is not None:
        fields["start_date"] = start
    if practical_end is not None:
        fields["end_date"] = practical_end
        fields.setdefault("practical_period_end_date", practical_end.isoformat())
    if apprenticeship_end is not None:
        # setdefault, not assignment: this column doubles as a per-learner
        # override (see mappers.py), so an explicitly supplied one wins.
        fields.setdefault("apprenticeship_end_date", apprenticeship_end.isoformat())

    # all_learners: the default manager is scoped to apprenticeship, so
    # creating through it would fight the learnerType we just set.
    if "case_owner" in fields:
        fields.update(case_owner_coach(fields["case_owner"]))
    user = EnrolmentUser.all_learners.create(**fields)

    # Commercial learners are date-driven from the moment they are
    # created: before the start date they are Delivery, and on/after it
    # they become Active. Apprenticeship learners keep the normal document
    # and review progression.
    advance_learner(user)
    row = to_list_row(user)
    # Book the first session with the case owner resolved above. Reported on the
    # row like the invitation is, and for the same reason: it can partly fail
    # (the booking is durable, the Teams meeting may not have been created yet)
    # and the console has to be able to say so rather than imply success.
    if session_date is not None and session_time is not None:
        row["firstSession"] = book_first_session(
            user,
            scheduled_date=session_date,
            scheduled_time=session_time,
            owner_name=_s(fields.get("coach_name")) or _s(fields.get("case_owner")),
            owner_email=_s(fields.get("coach_email")),
        )
    elif session_date is not None:
        # A date with no time: an imported row that knows the day but not the
        # hour. The programme start is still that day, so it is recorded -- but
        # nothing is booked, and the row says so rather than implying a meeting.
        row["firstSession"] = record_start_date_only(user, session_date)
    # Provision the platform account without emailing. Enrolling somebody
    # gives them an account; an administrator sends the invitation from the
    # Accounts page when the record has been checked. Reported alongside the
    # created learner so the console can say the account is awaiting one.
    row["invitation"] = _send_platform_invitation(request, "learner", user.id, subject=user)
    if require_account and (
        row["invitation"].get("error")
        or not row["invitation"].get("awaitingInvitation")
    ):
        raise ValidationError("The learner's platform account could not be created. No students were imported.")
    return row

@csrf_exempt
# Intentionally ungated (reviewed 2026-09-05): pure static enum lists (status,
# type, programmeStatus, position, learnerType choices), no DB read and no PII,
# so any authenticated caller under the ANY prefix may read them. Contrast
# employers/options/, which IS staff-gated because it discloses staff usernames.
def enrolment_user_options(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    return JsonResponse(
        {
            "status": STATUS_CHOICES,
            "type": TYPE_CHOICES,
            "programmeStatus": PROGRAMME_STATUS_CHOICES,
            "position": POSITION_CHOICES,
            "learnerType": LEARNER_TYPE_CHOICES,
        }
    )


@csrf_exempt
# allow_own_learner: the onboarding wizard submits on the learner's own behalf.
# The payload is narrowed to LEARNER_SELF_WRITABLE_KEYS below — owning the row
# is not permission to set programmeStatus on it.
@staff_only(allow_own_learner="pk")  # A4: staff, or the learner reading their own.
def enrolment_user_detail(request, pk):
    try:
        # all_learners, not objects: `objects` is scoped to apprenticeship rows, so
        # looking a learner up by id must span both kinds or a commercial learner
        # 404s here — ids are unique across the single table.
        user = EnrolmentUser.all_learners.filter(pk=pk).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if user is None:
        return _error("User not found.", 404)
    if request.method == "DELETE":
        # The shared decorator allows a learner to PATCH their own wizard
        # record. That exception must never extend to account deletion.
        if getattr(request, "learner_self_write", False):
            return _error("Only staff can delete a user account.", 403)

        try:
            delete_learner_account(user.pk)
        except EnrolmentUser.DoesNotExist:
            return _error("User not found.", 404)
        except ValidationError as exc:
            return _error(str(exc), 400)
        except DatabaseError as exc:
            return _error(f"Could not delete user account: {exc}", 502)
        return JsonResponse({"deleted": True, "id": pk})
    if request.method == "GET":
        # Safety net: a learner whose onboarding reviews are all signed belongs in
        # Delivery. The sign endpoint normally moves them the moment the last
        # signature lands, but reviews signed before that hook existed were never
        # re-evaluated — so opening the learner re-checks and heals them.
        from .learning_plan import promote_learner_if_ready

        learner_kind = str(getattr(user, "learner_type", "") or "").strip() or "apprenticeship"
        if promote_learner_if_ready(learner_kind, user.pk):
            user.refresh_from_db()
        advance_learner(user)
        return JsonResponse(to_board(user))
    if request.method in ("PATCH", "PUT"):
        handover = None
        try:
            payload = _parse_body(request)
            # A learner submitting their own wizard may only touch their own
            # details — not programmeStatus, programme, cohort or employer,
            # which WRITABLE_FIELDS otherwise accepts. Stripped rather than
            # rejected so a wizard step that has always sent a field keeps
            # working; what was dropped is logged, not silently forgotten.
            if getattr(request, "learner_self_write", False):
                payload, rejected = restrict_to_self_writable(payload)
                if rejected:
                    logger.warning(
                        "Learner %s attempted to write fields they do not own: %s",
                        pk, ", ".join(rejected),
                    )
            fields = write_fields(payload)
            validate_learner_dates(fields, user)
            _check_employer_id(fields)
        except ValidationError as exc:
            return _error(str(exc), 400)
        try:
            for attr, value in fields.items():
                setattr(user, attr, value)
            if fields:
                handover = save_enrolment_fields(user, fields)
                # The address and name on this row ARE the sign-in identity, and
                # the login account keeps its own copy — which is what an
                # invitation is sent to. Correcting an email here without this
                # left the invitation going to the old address for ever, since
                # the account only refreshes itself when its owner signs in and
                # an invited learner has not yet.
                if any(field in fields for field in ACCOUNT_IDENTITY_FIELDS):
                    sync_account("learner", user.pk, subject=user)
                if any(field in fields for field in PLACEMENT_SOURCE_FIELDS):
                    # A learner placed after they were created has none of the
                    # cohort's dates, and progression cannot start them without
                    # one. Stamped before advance_learner runs, so a learner who
                    # is already past their start date activates on this save.
                    stamp_cohort_window(user)
                    # Where a learner sits is read from their permanent profile
                    # by the workspace, the coach tools and the reports, so the
                    # change has to reach that row too.
                    mirror_learner_placement(user)
                advance_learner(user)
                if 'programme_status' in fields:
                    # An explicit staff status edit has already changed the
                    # source before advance_learner runs. Its transition-only
                    # hook cannot notice Active -> Active, leaving the profile
                    # in Delivery and hiding coach/review/calendar records.
                    sync_active_user(user)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({**to_board(user), **(handover or {})})
    return _error("Method not allowed.", 405)


@csrf_exempt
# Staff only on GET too, unlike its neighbours: the response carries a national
# insurance number, a date of birth and a home address. The board GET next door
# is ungated because what it returns is programme progress, which is not the
# same thing.
@staff_only()
def enrolment_user_fields(request, pk):
    """The editable columns of one learner, to prefill the edit form.

        GET /learner_api/enrolment-users/<id>/fields/  -> {field: value}

    Saving goes back to ``enrolment_user_detail`` (PATCH), which validates the
    choice lists and re-stamps the delivery window when a learner's placement
    moves. This endpoint only reads.
    """
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    try:
        # all_learners for the same reason as enrolment_user_detail: ids are
        # unique across the single table but `objects` hides commercial rows.
        user = EnrolmentUser.all_learners.filter(pk=pk).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if user is None:
        return _error("User not found.", 404)
    return JsonResponse(to_edit_fields(user))


# Same reason as enrolment_user_detail: the learner presses Finish themselves.
@staff_only(writes_only=True, allow_own_learner="pk")
def enrolment_user_finish(request, pk):
    """Check whether an enrolled learner is ready for automatic activation.

        POST /learner_api/enrolment-users/<id>/finish/  -> EnrolmentBoard

    Enrolment happens entirely in enrolment."Created_users": every learner the
    console creates lives there and nowhere else. This endpoint is the single
    gate out of it. Only when enrolment is finished does the learner get a
    "Learner"."learners" row — created by sync_active_user with the SAME id, so
    the training plan, KSBs, progress, activity and chat rows that key off
    learners.id all line up — and only then does their journey (learner page,
    coach caseload, calendar) become reachable.

    Deliberately explicit rather than automatic on create: an in-progress
    enrolment must not appear as a live learner.
    """
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        user = EnrolmentUser.all_learners.filter(pk=pk).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if user is None:
        return _error("User not found.", 404)

    if not str(user.email or "").strip():
        return _error("This learner needs an email address before enrolment can be finished.", 400)

    try:
        # Programme activation is evidence/date driven. This legacy endpoint is
        # kept for the UI, but cannot bypass unsigned documents or a future
        # start date.
        advance_learner(user)
        if str(user.programme_status or "").strip() != ACTIVE_STATUS:
            if str(getattr(user, "learner_type", "") or "").strip().casefold() == "commercial":
                return _error(
                    "This commercial learner becomes Active once a learning plan is assigned and the programme start date has arrived.",
                    409,
                )
            return _error(
                "This learner becomes Active automatically once every compliance document is signed and the programme start date has arrived.",
                409,
            )
        learner = sync_active_user(user)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if learner is None:
        return _error("Could not create the learner record. Please try again.", 502)

    return JsonResponse(to_board(user))



@csrf_exempt
@staff_only()  # A4: the commercial-learner directory (PII) — staff/admin only.
def commercial_users(request):
    if request.method == "GET":
        try:
            rows = [
                _profile_commercial_row(profile)
                for profile in _learner_profiles_with_plan().order_by("id")
                if not _profile_is_apprenticeship(profile)
            ]
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"count": len(rows), "results": rows})

    if request.method == "POST":
        try:
            payload = _parse_body(request)
            profile = _create_profile_from_delivery_payload(payload, apprenticeship=False)
        except ValidationError as exc:
            return _error(str(exc), 400)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse(_profile_commercial_row(profile), status=201)

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: staff directory leaks access grants — staff/admin only, reads too.
def staff_users(request):
    """Staff/admin accounts — enrolment."Staff_users".

    GET accepts a repeatable ?position= filter, used by the create-user form's
    Case owner dropdown to list only the staff who can own a case.
    """
    if request.method == "GET":
        positions = [p for p in request.GET.getlist("position") if p.strip()]
        try:
            qs = StaffUser.objects.all()
            if positions:
                qs = qs.filter(position__in=positions)
            rows = [to_staff_row(u) for u in qs.order_by("username", "id")]
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse({"count": len(rows), "results": rows})

    if request.method == "POST":
        try:
            payload = _parse_body(request)
            fields = write_staff_fields(payload, require_create=True)
        except ValidationError as exc:
            return _error(str(exc), 400)

        # As on the learner form: creating a colleague invites them, and the
        # column records that rather than a choice — see enrolment_users.
        fields["invite_to_platform"] = True

        try:
            user = StaffUser.objects.create(**fields)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)

        row = to_staff_row(user)
        # Account only; the invitation email is sent from the Accounts page.
        # The authorisation in login.services still applies, so a staff member
        # creating an Admin gets a refused account reported on the row — the
        # record saves, the credential does not.
        row["invitation"] = _send_platform_invitation(request, "staff", user.id, subject=user)
        return JsonResponse(row, status=201)

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: staff record incl. access grant — staff/admin only, reads too.
def staff_user_detail(request, pk):
    try:
        user = StaffUser.objects.get(pk=pk)
    except StaffUser.DoesNotExist:
        return _error("Staff user not found.", 404)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)

    if request.method == "GET":
        return JsonResponse(to_staff_row(user))

    if request.method in ("PATCH", "PUT"):
        try:
            payload = _parse_body(request)
            fields = write_staff_fields(payload)
        except ValidationError as exc:
            return _error(str(exc), 400)

        # Granting access is an admin-only act, even though editing a staff
        # record is not. `staff_only` lets any staff account write any staff
        # row, so without this a curriculum- or coach-access user could PATCH
        # their own record to access='super-admin' and take the whole platform —
        # the escalation this mechanism exists to prevent.
        if "access" in fields:
            actor = getattr(request, "login_account", None)
            if actor is None or actor.role != "admin":
                return _error(
                    "Only an administrator can change an account's access.", 403
                )
            if actor.subject_type == "staff" and actor.subject_id == user.pk:
                # Self-*demotion* is refused: it would strip the caller's own
                # admin role mid-request and, with one administrator, could leave
                # nobody able to grant access to anyone ever again.
                #
                # Self-promotion to super-admin is allowed, and has to be: the
                # bootstrap administrator holds their role via Position with no
                # Access recorded, so refusing every self-change would leave them
                # permanently unable to record the access they already exercise.
                # It grants nothing new either way — they are already an admin.
                if fields["access"] != ACCESS_SUPER_ADMIN:
                    return _error(
                        "You cannot reduce your own access. Ask another administrator.",
                        400,
                    )

        if fields:
            for attr, value in fields.items():
                setattr(user, attr, value)
            try:
                # auto_now on Updated_at only fires when it's in update_fields.
                user.save(update_fields=[*fields.keys(), "updated_at"])
            except DatabaseError as exc:
                return _error(f"Database error: {exc}", 502)
            # Same as the learner path, plus role: a staff account's role is
            # derived from position and access, so an edit to either has to
            # reach the account that enforces it.
            if any(field in fields for field in STAFF_IDENTITY_FIELDS):
                sync_account("staff", user.pk, subject=user)
        return JsonResponse(to_staff_row(user))

    return _error("Method not allowed.", 405)


@csrf_exempt
@staff_only()  # A4: commercial-learner record (PII) — staff/admin only, reads too.
# NB: deliberately NOT allow_own_learner. This view keys on Learner"."learners.id,
# while a learner's subject_id is a Created_users.id (independent sequence), so the
# "own record" a learner could match here is never actually their own — the
# parameter grants no functional access and only opens an unrestricted learner
# PATCH path (A23). Kept as plain staff-only, matching pre-M2 behaviour.
def commercial_user_detail(request, pk):
    try:
        profile = _learner_profiles_with_plan().filter(pk=pk).first()
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if profile is None:
        return _error("User not found.", 404)
    if request.method == "GET":
        return JsonResponse(_profile_commercial_row(profile))
    if request.method in ("PATCH", "PUT"):
        try:
            payload = _parse_body(request)
            _update_profile_from_delivery_payload(profile, payload)
        except ValidationError as exc:
            return _error(str(exc), 400)
        except DatabaseError as exc:
            return _error(f"Database error: {exc}", 502)
        return JsonResponse(_profile_commercial_row(profile))
    return _error("Method not allowed.", 405)
