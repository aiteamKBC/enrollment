"""The Training Plan — issue, sign and retrieve.

The tripartite document: it sets out how the apprentice, the employer and the
training provider will each support the apprenticeship, and carries the learning
plan that delivers it. All three parties sign it.

Content is assembled from what we already hold:
  * programme particulars  -> the learner's record and their group's dates
  * employment details     -> the learner's employer and the Extended ILR
  * the learning plan      -> the modules on enrolment."Created_users".Learning_plan,
    which is what the learning-plan editor writes
  * off-the-job hours      -> that plan's total

Those values are SNAPSHOT at issue: once three parties have signed, editing the
learning plan must not rewrite what they agreed to.

    GET  /learner_api/training-plan-document/<pk>/          particulars + document
    POST /learner_api/training-plan-document/<pk>/issue/    create/reissue
    POST /learner_api/training-plan-document/<pk>/sign/     one party signs
"""
import json
import logging

from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from curriculum_api.week_visibility import visible_child_sql, visible_week_sql
from enrolment_api.auth import employer_signing_party_error, enrolment_login_required

from .apprenticeship_agreement import (
    _employer_address,
    _group_dates,
    _weeks_between,
)
from .learning_plan import _saved_modules, learner_group_module_ids, learner_programme_modules
from .learner_progression import advance_learner
from .mappers import _s
from .models import EnrolmentUser, TrainingPlanDocument

logger = logging.getLogger(__name__)

SIGNING_PARTIES = ("apprentice", "employer", "provider")
MAX_SIGNATURE_CHARS = 400_000


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _iso(value):
    return value.isoformat() if value else ""


def _extended_ilr_answers(learner_id):
    """The wizard's Extended ILR answers, which hold the employment details."""
    try:
        from enrolment_api.models import ExtendedIlr

        row = ExtendedIlr.objects.filter(learner_id=learner_id).first()
    except DatabaseError:
        logger.exception("_extended_ilr_answers: lookup failed")
        return {}
    answers = getattr(row, "answers", None)
    return answers if isinstance(answers, dict) else {}


def _plan_modules(learner):
    """The learner's learning plan, falling back to their group's preset."""
    catalogue = {m["moduleId"]: m for m in learner_programme_modules(learner)}
    saved = _saved_modules(learner)
    ids = (
        [_s(m.get("moduleId")) for m in saved]
        if saved
        else learner_group_module_ids(learner)
    )
    return [catalogue[i] for i in ids if i in catalogue]


# How a component's `type` reads in the Method column of the printed plan.
COMPONENT_METHODS = {
    "live_session": "Live session",
    "recording_placeholder": "Recorded session",
    "video": "Pre-recorded video",
    "podcast": "Podcast",
    "reading": "Reading material",
    "quiz": "Quiz",
    "assignment": "Assignment (task)",
    "reflection": "Reflection",
    "workplace_task": "Workplace task",
    "slides": "Slides",
    "digital_learning": "Digital learning",
}


def _method_label(component_type):
    """A readable Method for the plan table, from the component's type."""
    key = _s(component_type)
    return COMPONENT_METHODS.get(key, key.replace("_", " ").capitalize())


def _module_breakdown(module_ids):
    """Weeks and components for the plan's modules, keyed by module id.

    The printed Training Plan lists each teaching activity, not just the module
    it belongs to — so the plan table is built from components, grouped by the
    week that delivers them. Off-the-job hours come from each component's
    expected_otjh, which is what actually sums to the module total.
    """
    if not module_ids:
        return {}

    from django.db import connection

    try:
        # Deliberately the `default` alias: curriculum.weeks/components are owned
        # by curriculum_api and migrated on `default`. See the note in
        # apprenticeship_agreement._group_dates.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT w.module_catalogue_id, w.week_number, w.title,
                       c.id, c.type, c.title, c.expected_otjh, c.display_order
                FROM curriculum.weeks w
                LEFT JOIN curriculum.components c ON c.week_id = w.id
                  AND """ + visible_child_sql("c") + """
                WHERE w.module_catalogue_id = ANY(%s)
                  AND """ + visible_week_sql("w") + """
                ORDER BY w.module_catalogue_id, w.week_number, w.display_order,
                         c.display_order, c.title
                """,
                [list(module_ids)],
            )
            rows = cursor.fetchall()
    except DatabaseError:
        logger.exception("_module_breakdown: lookup failed")
        return {}

    breakdown = {}
    for module_id, week_no, week_title, comp_id, comp_type, comp_title, otjh, _order in rows:
        weeks = breakdown.setdefault(_s(module_id), [])
        week = next((w for w in weeks if w["weekNumber"] == week_no), None)
        if week is None:
            week = {
                "weekNumber": week_no,
                "weekTitle": _s(week_title) or (f"Week {week_no}" if week_no else ""),
                "components": [],
            }
            weeks.append(week)
        # LEFT JOIN: a week with no components still appears, with none listed.
        if comp_id:
            week["components"].append({
                "componentId": _s(comp_id),
                "title": _s(comp_title),
                "method": _method_label(comp_type),
                "otjHours": float(otjh) if otjh is not None else 0.0,
            })
    return breakdown


def derive_content(learner):
    """Everything the Training Plan states, from the learner's own records."""
    start, end, dates_from = _group_dates(learner)
    modules = _plan_modules(learner)
    total_hours = round(sum(float(m.get("hours") or 0) for m in modules), 2)
    answers = _extended_ilr_answers(learner.pk)
    employer_block = answers.get("employer") if isinstance(answers.get("employer"), dict) else {}

    weeks = _weeks_between(start, end)

    programme = {
        "apprenticeName": _s(learner.username),
        "programme": _s(learner.programme),
        "cohort": _s(learner.cohort),
        "group": _s(learner.group),
        # We hold no separate standard name/level/reference, so the programme
        # name is what we can honestly state; an officer completes the rest.
        "standard": _s(learner.programme),
        "reference": _s(learner.reference_number),
        "level": "",
        "startDate": _iso(start),
        "endDate": _iso(end),
        "practicalStartDate": _iso(start),
        "practicalEndDate": _iso(end),
        "durationWeeks": float(weeks) if weeks is not None else None,
        "ilrPlannedHours": total_hours,
    }

    employment = {
        "employerName": _s(learner.employer) or _s(employer_block.get("organisationName")),
        "deliveryAddress": _employer_address(learner) or _s(employer_block.get("address")),
        "jobTitle": "",
        "workingHoursPerWeek": "",
        "lineManager": _s(learner.line_manager) or _s(employer_block.get("lineManagerName")),
        "lineManagerTitle": "",
        "startDateWithEmployer": "",
    }

    # Each module with the weeks and components that deliver it, so the printed
    # plan lists actual teaching activities rather than just module names.
    delivery_lead = _s(learner.learning_provider) or "Kent Business College"
    breakdown = _module_breakdown([_s(m.get("moduleId")) for m in modules])
    learning_plan = []
    for m in modules:
        module_id = _s(m.get("moduleId"))
        weeks = breakdown.get(module_id, [])
        # The module total is authoritative (it is what the learning-plan editor
        # agreed); the component sum is shown alongside where it differs.
        component_hours = round(
            sum(c["otjHours"] for w in weeks for c in w["components"]), 2
        )
        learning_plan.append({
            "moduleId": module_id,
            "activity": _s(m.get("moduleTitle")),
            "deliveryLead": delivery_lead,
            "plannedDate": _iso(start),
            "plannedEmHours": None,
            "plannedOtjHours": float(m.get("hours") or 0),
            "groupName": _s(m.get("groupName")),
            "weeks": weeks,
            "componentHours": component_hours,
            "componentCount": sum(len(w["components"]) for w in weeks),
        })

    otjh = {
        # The planned total is the learning plan's; the published minimum and
        # any RPL reduction have no source on our side yet.
        "plannedTotal": total_hours,
        "publishedMinimum": None,
        "rplVolume": None,
        "minimumReducedByRpl": None,
    }

    epa = {"epao": "", "gatewayReviewDate": "", "epaPeriodFrom": "", "epaPeriodTo": ""}

    contacts = {
        "apprentice": {
            "name": _s(learner.username),
            "email": _s(learner.email),
            "telephone": _s(learner.phone_number),
            "address": " ".join(
                p for p in (
                    _s(learner.address_line_1) or _s(learner.address),
                    _s(learner.address_line_2),
                    _s(learner.address_line_3),
                    _s(learner.current_postcode),
                ) if p
            ),
        },
        "employer": {
            "lineManager": employment["lineManager"],
            "position": "",
            "email": _s(employer_block.get("lineManagerEmail")),
            "telephone": _s(employer_block.get("lineManagerPhone")),
            "company": employment["employerName"],
            "address": employment["deliveryAddress"],
        },
    }

    return {
        "programme": programme,
        "employment": employment,
        "learningPlan": learning_plan,
        "otjh": otjh,
        "epa": epa,
        "contacts": contacts,
        "datesFrom": dates_from,
    }


# ---------------------------------------------------------------------------
# Serialising
# ---------------------------------------------------------------------------
def _party_json(signature, name, position, at):
    return {
        "signed": bool(_s(signature)),
        "name": _s(name),
        "position": _s(position),
        "signedAt": at.isoformat() if at else None,
    }


def _document_json(document):
    if document is None:
        return None
    return {
        "id": str(document.id),
        "status": _s(document.status),
        "fullySigned": bool(document.fully_signed),
        "createdAt": document.created_at.isoformat() if document.created_at else None,
        "updatedAt": document.updated_at.isoformat() if document.updated_at else None,
        # The frozen content — what the three parties signed.
        "programme": document.programme or {},
        "employment": document.employment or {},
        "learningPlan": document.learning_plan or [],
        "otjh": document.otjh or {},
        "epa": document.epa or {},
        "contacts": document.contacts or {},
        "signatures": {
            "apprentice": _party_json(
                document.apprentice_signature,
                document.apprentice_signed_name,
                document.apprentice_position,
                document.apprentice_signed_at,
            ),
            "employer": _party_json(
                document.employer_signature,
                document.employer_signed_name,
                document.employer_position,
                document.employer_signed_at,
            ),
            "provider": _party_json(
                document.provider_signature,
                document.provider_signed_name,
                document.provider_position,
                document.provider_signed_at,
            ),
        },
        "marks": {
            "apprentice": _s(document.apprentice_signature),
            "employer": _s(document.employer_signature),
            "provider": _s(document.provider_signature),
        },
    }


def _active_document(learner_kind, learner_id, lock=False):
    """The learner's active training plan. `lock=True` takes a row lock for the
    caller's transaction — only valid inside
    `transaction.atomic(using="enrolment")`."""
    qs = TrainingPlanDocument.objects.filter(
        learner_kind=learner_kind,
        learner_id=learner_id,
        status=TrainingPlanDocument.STATUS_ACTIVE,
    )
    if lock:
        qs = qs.select_for_update()
    return qs.first()


def _learner_kind(learner):
    return _s(getattr(learner, "learner_type", "")) or "apprenticeship"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@enrolment_login_required
@csrf_exempt
def training_plan_document(request, pk):
    """The learner's Training Plan, plus the content a new one would carry."""
    if request.method != "GET":
        return _error("Method not allowed.", 405)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        derived = derive_content(learner)
        document = _active_document(_learner_kind(learner), learner.pk)
    except DatabaseError as exc:
        logger.exception("training_plan_document: build failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "learner": {
            "id": str(learner.id),
            "name": _s(learner.username),
            "programmeStatus": _s(learner.programme_status),
        },
        # What a freshly issued plan would state, from live data.
        "programme": derived["programme"],
        "employment": derived["employment"],
        "learningPlan": derived["learningPlan"],
        "otjh": derived["otjh"],
        "epa": derived["epa"],
        "contacts": derived["contacts"],
        "meta": {
            "datesFrom": derived["datesFrom"],
            "moduleCount": len(derived["learningPlan"]),
        },
        "document": _document_json(document),
    })


@enrolment_login_required
@csrf_exempt
def issue_training_plan(request, pk):
    """Issue the plan, snapshotting the current content onto a new row."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        kind = _learner_kind(learner)
        derived = derive_content(learner)

        # `using="enrolment"` is required — a bare atomic() opens on `default`
        # and leaves this supersede+create unprotected. See BE-1.
        with transaction.atomic(using="enrolment"):
            existing = _active_document(kind, learner.pk, lock=True)
            if existing is not None:
                existing.status = TrainingPlanDocument.STATUS_SUPERSEDED
                existing.save(update_fields=["status", "updated_at"])

            document = TrainingPlanDocument.objects.create(
                learner_kind=kind,
                learner_id=learner.pk,
                programme=derived["programme"],
                employment=derived["employment"],
                learning_plan=derived["learningPlan"],
                otjh=derived["otjh"],
                epa=derived["epa"],
                contacts=derived["contacts"],
            )
    except DatabaseError as exc:
        logger.exception("issue_training_plan: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({"document": _document_json(document)}, status=201)


@enrolment_login_required
@csrf_exempt
def sign_training_plan(request, pk):
    """Record one party's signature. Empty signature withdraws that sign-off."""
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return _error("Request body must be valid JSON.", 400)
    if not isinstance(payload, dict):
        return _error("Request body must be a JSON object.", 400)

    party = _s(payload.get("party")).lower()
    if party not in SIGNING_PARTIES:
        allowed = "', '".join(SIGNING_PARTIES)
        return _error(f"party must be one of '{allowed}'.", 400)
    party_error = employer_signing_party_error(request, party)
    if party_error is not None:
        return party_error

    signature = _s(payload.get("signature"))
    name = _s(payload.get("name"))
    position = _s(payload.get("position"))
    if signature:
        if len(signature) > MAX_SIGNATURE_CHARS:
            return _error("That signature image is too large.", 400)
        if not signature.startswith("data:image/"):
            return _error("signature must be a PNG data URL.", 400)
        if not name:
            return _error("name is required when signing.", 400)

    try:
        learner = EnrolmentUser.all_learners.filter(pk=pk).first()
        if learner is None:
            return _error("Learner not found.", 404)

        # Lock the row for the read-modify-write so concurrent signers can't
        # overwrite each other's signature. See BE-4.
        with transaction.atomic(using="enrolment"):
            document = _active_document(_learner_kind(learner), learner.pk, lock=True)
            if document is None:
                return _error("No training plan has been issued for this learner yet.", 404)

            now = timezone.now() if signature else None
            setattr(document, f"{party}_signature", signature)
            setattr(document, f"{party}_signed_name", name if signature else "")
            setattr(document, f"{party}_position", position if signature else "")
            setattr(document, f"{party}_signed_at", now)

            document.recalculate_signed()
            document.save()
        promoted = advance_learner(learner)
    except DatabaseError as exc:
        logger.exception("sign_training_plan: failed")
        return _error(f"Database error: {exc}", 502)

    return JsonResponse({
        "document": _document_json(document),
        "programmeStatus": _s(learner.programme_status),
        "programmeStatusChangedTo": promoted,
    })
