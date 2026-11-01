"""Session-authorised endpoints. Wizard configuration uses its existing publish API."""
import copy
import json
import logging

from django.db import DatabaseError, IntegrityError, connections, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect

from enrolment_api.auth import enrolment_login_required
from enrolment_api.wizard_layout import CONN, _actor, read_current
from learner_api.models import EnrolmentUser

from .configuration import ConfigurationError, KEY, defaults, learner_configuration, validate
from .models import Assessment
from .programme_assignment import ProgrammeAssignmentError, sync_confirmed_programme_to_learner
from .permissions import is_staff
from .scoring import calculate
from .completion import required_for, selection_status_for, status_for
from .selection import allowed_selections, recommended_programme, score_based_result, selection_source, validate_selection

logger = logging.getLogger(__name__)
REVIEW_STATES = {"PRELIMINARY_RECOMMENDATION", "PENDING_ADMISSIONS_REVIEW", "FURTHER_INFORMATION_REQUIRED", "REVIEW_COMPLETED"}


def body(request):
    if len(request.body) > 512_000:
        raise ConfigurationError("Suitability request is too large.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ConfigurationError("Provide a valid JSON object.")
    if not isinstance(data, dict):
        raise ConfigurationError("Provide a JSON object.")
    return data


def published():
    with connections[CONN].cursor() as cursor:
        current = read_current(cursor)
    if not current or not current[1].get(KEY) or not current[1][KEY].get("enabled"):
        return None, None
    return current[0], validate(current[1][KEY])


def payload(row, staff=False):
    result = score_based_result(row.configuration, row.pathway, row.result)
    if not staff:
        result.pop("breakdown", None)
    return {"available": True, "configuration": learner_configuration(row.configuration),
            "configurationVersion": row.configuration_version, "pathway": row.pathway,
            "responses": row.responses, "result": result, "selectedProgramme": row.selected_programme,
            "originalPreference": row.original_preference, "reviewStatus": row.review_status,
            "programmeSelectionSource": row.programme_selection_source,
            "programmeAssignment": row.review.get("programmeAssignment"),
            "alternativeSelectionReason": row.alternative_selection_reason,
            "selectionStatus": selection_status_for(row),
            "selectionConfirmedAt": row.selection_confirmed_at.isoformat() if row.selection_confirmed_at else None,
            "recommendedProgramme": result.get("recommendedProgramme"),
            "recommendationStatus": result.get("outcome"), "assessmentStatus": status_for(row),
            "admissionsReviewStatus": row.review_status,
            "allowedSelections": allowed_selections(row.configuration, row.pathway, row.result),
            "review": row.review, "revision": row.revision,
            "confirmedAt": row.confirmed_at.isoformat() if row.confirmed_at else None,
            "updatedAt": row.updated_at.isoformat(), **({"history": row.history} if staff else {})}


def completion_payload(row, learner, staff=False, configuration=None):
    data = payload(row, staff)
    data.update(required=required_for(configuration if configuration is not None else row.configuration, learner),
                completionStatus=status_for(row))
    return data


def audit(row, request, action):
    # Preserve earlier answers/results and staff overrides; no silent rescoring of old versions.
    row.history = [*row.history, {"action": action, "at": timezone.now().isoformat(), "actor": _actor(request),
                    "revision": row.revision, "responses": row.responses, "result": row.result,
                    "selectedProgramme": row.selected_programme, "reviewStatus": row.review_status,
                    "programmeSelectionSource": row.programme_selection_source,
                    "alternativeSelectionReason": row.alternative_selection_reason,
                    "selectionConfirmedAt": row.selection_confirmed_at.isoformat() if row.selection_confirmed_at else None,
                    "review": row.review}]


@enrolment_login_required
def configuration_defaults(request):
    if not is_staff(request):
        return JsonResponse({"error": "Admissions staff access required."}, status=403)
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    return JsonResponse({"configuration": defaults()})


@enrolment_login_required
@csrf_protect
def preview(request):
    if not is_staff(request):
        return JsonResponse({"error": "Admissions staff access required."}, status=403)
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        data = body(request)
        config = validate(data.get("configuration"))
        if data.get("validateOnly") is True:
            return JsonResponse({"valid": True})
        return JsonResponse(calculate(config, data.get("pathway"), data.get("responses", {})))
    except ConfigurationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def learner_record(kind, learner_id):
    if kind not in {"apprenticeship", "commercial"}:
        return None
    return EnrolmentUser.all_learners.only("id", "onboarding_status").filter(pk=learner_id).first()


@enrolment_login_required
@csrf_protect
def assessment(request, kind, learner_id):
    if getattr(request, "login_account", None) is None and not is_staff(request):
        return JsonResponse({"error": "Admissions staff or learner session required."}, status=403)
    if request.method not in {"GET", "PUT"}:
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        learner = learner_record(kind, learner_id)
        if learner is None:
            return JsonResponse({"error": "Not found."}, status=404)
        if request.method == "GET":
            row = Assessment.objects.filter(learner_id=learner_id).first()
            if row:
                return JsonResponse(completion_payload(row, learner, is_staff(request)))
            version, config = published()
            return JsonResponse({"available": config is not None, "configurationVersion": version,
                                 "configuration": learner_configuration(config) if config else None,
                                 "responses": {}, "revision": 0, "pathway": "", "result": None,
                                 "selectedProgramme": "", "reviewStatus": "PRELIMINARY_RECOMMENDATION",
                                 "selectionStatus": "NOT_STARTED", "selectionConfirmedAt": None,
                                 "alternativeSelectionReason": None,
                                 "required": required_for(config, learner), "completionStatus": "NOT_STARTED"})
        data = body(request)
        if type(data.get("revision")) is not int or data["revision"] < 0:
            raise ConfigurationError("Provide the assessment revision as a non-negative integer.")
        if "confirm" in data and not isinstance(data["confirm"], bool):
            raise ConfigurationError("Confirmation must be true or false.")
        action = data.get("action", "submit" if data.get("confirm") is True else "draft")
        if action not in ("draft", "submit", "select", "continue", "request_review"):
            raise ConfigurationError("Unknown assessment action.")
        confirm = action == "submit"
        with transaction.atomic():
            row = Assessment.objects.select_for_update().filter(learner_id=learner_id).first()
            same_answers = bool(row and data.get("pathway", row.pathway) == row.pathway
                                and data.get("responses", row.responses) == row.responses)
            same_selection = bool(row and data.get("selectedProgramme", row.selected_programme) in ("", row.selected_programme))
            if row and row.selection_confirmed_at and "alternativeSelectionReason" in data and data["alternativeSelectionReason"] != row.alternative_selection_reason:
                return JsonResponse({"error": "Your confirmed programme selection reason is read-only."}, status=403)
            if "alternativeSelectionReason" in data and action != "select":
                raise ConfigurationError("A programme selection reason can only be saved with an alternative selection.")
            if row and row.confirmed_at:
                if not same_answers or action == "draft":
                    return JsonResponse({"error": "Submitted assessment answers are read-only."}, status=403)
                if action == "submit" and not same_selection:
                    return JsonResponse({"error": "Use programme confirmation to choose an available programme."}, status=403)
                if row.selection_confirmed_at and not same_selection:
                    return JsonResponse({"error": "Your programme selection is confirmed and cannot be changed."}, status=403)
                if row.selection_confirmed_at and action in ("select", "continue"):
                    # A network retry of the same confirmation is safe; a new choice is not.
                    if data.get("selectedProgramme", row.selected_programme) != row.selected_programme:
                        return JsonResponse({"error": "Your programme selection is confirmed and cannot be changed."}, status=403)
                    if data["revision"] in (row.revision, row.revision - 1):
                        return JsonResponse(completion_payload(row, learner, is_staff(request)))
            if (row and confirm and status_for(row) == "SUBMITTED" and same_answers and same_selection
                    and data["revision"] in (row.revision, row.revision - 1)):
                # Retrying submission never changes a historical choice or silently rescores it.
                return JsonResponse(completion_payload(row, learner, is_staff(request)))
            if data["revision"] != (row.revision if row else 0):
                return JsonResponse({"error": "This assessment changed in another tab. Reload before editing."}, status=409)
            if action in ("select", "continue", "request_review"):
                if row is None or status_for(row) != "SUBMITTED":
                    raise ConfigurationError("Submit your assessment successfully before continuing or choosing an alternative.")
                if not same_answers:
                    raise ConfigurationError("Submitted assessment answers are read-only.")
                if action == "continue":
                    if data.get("selectedProgramme", row.selected_programme) != row.selected_programme:
                        raise ConfigurationError("Save your programme selection before continuing.")
                    effective = score_based_result(row.configuration, row.pathway, row.result)
                    recommended = recommended_programme(row.configuration, row.pathway, effective)
                    audit(row, request, "PROGRAMME_SELECTION_CONFIRMED")
                    row.result = effective
                    row.selected_programme = recommended["id"] if recommended else ""
                    row.alternative_selection_reason = None
                    row.programme_selection_source = selection_source(effective, row.selected_programme)
                    row.selection_confirmed_at = timezone.now()
                    if not row.original_preference:
                        row.original_preference = row.selected_programme
                    row.revision += 1
                    row.save()
                    sync_confirmed_programme_to_learner(row, actor=_actor(request))
                    return JsonResponse(completion_payload(row, learner, is_staff(request)))
                audit(row, request, "PROGRAMME_SELECTION" if action == "select" else "LEARNER_REVIEW_REQUEST")
                if action == "select":
                    effective = score_based_result(row.configuration, row.pathway, row.result)
                    selection = validate_selection(row.configuration, row.pathway, effective, data.get("selectedProgramme", ""))
                    if not selection:
                        raise ConfigurationError("Choose an available programme from your recommendation.")
                    recommended = recommended_programme(row.configuration, row.pathway, effective)
                    reason = None
                    if recommended and selection != recommended["id"]:
                        reason = data.get("alternativeSelectionReason")
                        # HTML textarea maxLength counts UTF-16 code units, including line breaks.
                        if not isinstance(reason, str) or not reason.strip() or len(reason.encode("utf-16-le", "surrogatepass")) // 2 > 1000:
                            raise ConfigurationError("Please explain your reason for choosing this programme (maximum 1000 characters).")
                    row.alternative_selection_reason = reason
                    row.selected_programme = selection
                    row.result = effective
                    row.programme_selection_source = selection_source(effective, selection)
                    row.selection_confirmed_at = timezone.now()
                else:
                    reason = data.get("reason")
                    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
                        raise ConfigurationError("Give a review request reason (maximum 2000 characters).")
                    row.review = {**row.review, "learnerRequest": {"reason": reason.strip(), "at": timezone.now().isoformat()}}
                    row.review_status = "PENDING_ADMISSIONS_REVIEW"
                row.revision += 1
                row.save()
                if action == "select":
                    sync_confirmed_programme_to_learner(row, actor=_actor(request))
            else:
                if row is None:
                    version, config = published()
                    if config is None:
                        return JsonResponse({"error": "No published suitability assessment is available."}, status=409)
                    if data.get("configurationVersion") != version:
                        return JsonResponse({"error": "Assessment configuration changed. Reload before starting."}, status=409)
                    row = Assessment(learner_id=learner_id, learner_kind=kind, configuration_version=version,
                                     configuration=config, pathway=data.get("pathway", ""), revision=0,
                                     programme_selection_source="NOT_SELECTED")
                else:
                    audit(row, request, "ASSESSMENT_SUBMITTED" if confirm else "ASSESSMENT_UPDATED")
                pathway = data.get("pathway", row.pathway)
                responses = data.get("responses", row.responses if pathway == row.pathway else {})
                changed = not same_answers
                # Selection-only saves use the persisted scoring result, never calculate again.
                result = score_based_result(row.configuration, pathway, row.result) if same_answers and row.result and (not confirm or row.confirmed_at) else calculate(row.configuration, pathway, responses, complete=confirm)
                selection = data.get("selectedProgramme", row.selected_programme if not changed else "")
                validate_selection(row.configuration, pathway, result, selection)
                if confirm and not selection:
                    recommended = recommended_programme(row.configuration, pathway, result)
                    selection = recommended["id"] if recommended else ""
                previous_selection = row.selected_programme
                row.pathway, row.responses, row.result = pathway, responses, result
                row.selected_programme = selection
                if changed or previous_selection != selection or row.programme_selection_source != "LEGACY":
                    row.programme_selection_source = selection_source(result, selection)
                if confirm:
                    if not row.original_preference and selection:
                        row.original_preference = selection
                    row.confirmed_at = timezone.now()
                    row.review_status = "PENDING_ADMISSIONS_REVIEW"
                elif changed:
                    row.confirmed_at = None
                    row.review_status = "PRELIMINARY_RECOMMENDATION"
                if row.confirmed_at is None and not selection:
                    row.programme_selection_source = "NOT_SELECTED"
                row.revision += 1
                if row._state.adding:
                    audit(row, request, "ASSESSMENT_CREATED")
                row.save()
        return JsonResponse(completion_payload(row, learner, is_staff(request)))
    except ProgrammeAssignmentError as exc:
        return JsonResponse({"error": str(exc), "code": "PROGRAMME_ASSIGNMENT_REVIEW_REQUIRED"}, status=409)
    except ConfigurationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except IntegrityError:
        return JsonResponse({"error": "This assessment was started in another tab. Reload before editing."}, status=409)
    except DatabaseError:
        logger.warning("Programme suitability storage unavailable (learner id %s)", learner_id)
        return JsonResponse({"error": "Suitability storage is unavailable. The existing CV form can still be saved."}, status=503)


@enrolment_login_required
@csrf_protect
def review(request, kind, learner_id):
    if not is_staff(request):
        return JsonResponse({"error": "Admissions staff access required."}, status=403)
    if request.method != "PUT":
        return JsonResponse({"error": "Method not allowed."}, status=405)
    try:
        data = body(request)
        status = data.get("status")
        reason = data.get("reason")
        if type(data.get("revision")) is not int or data["revision"] < 0:
            raise ConfigurationError("Provide the current assessment revision.")
        if not isinstance(status, str) or status not in REVIEW_STATES or not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ConfigurationError("Choose a review status and provide a reason (maximum 4000 characters).")
        with transaction.atomic():
            row = Assessment.objects.select_for_update().filter(learner_id=learner_id).first()
            if row is None:
                return JsonResponse({"error": "No suitability assessment found."}, status=404)
            if data.get("revision") != row.revision:
                return JsonResponse({"error": "Assessment changed. Reload before reviewing."}, status=409)
            override = data.get("recommendedProgramme") or None
            if override is not None and not isinstance(override, str):
                raise ConfigurationError("A manual recommendation must use a programme identifier.")
            allowed = {p["id"] for p in row.configuration["programmes"] if p["active"] and p["pathway"] == row.pathway}
            if override is not None and override not in allowed:
                raise ConfigurationError("Choose a programme from the assessed pathway.")
            audit(row, request, "ADMISSIONS_REVIEW")
            row.review = {**row.review, "recommendedProgramme": override, "reason": reason.strip(), "actor": _actor(request),
                          "basedOnRevision": row.revision,
                          "at": timezone.now().isoformat(), "notificationStatus": "AVAILABLE_IN_ASSESSMENT"}
            row.review_status = status
            row.revision += 1
            from .notifications import schedule_review_notification
            schedule_review_notification(row)
            row.save()
        return JsonResponse(payload(row, True))
    except ConfigurationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except DatabaseError:
        return JsonResponse({"error": "Could not save the admissions review."}, status=503)
