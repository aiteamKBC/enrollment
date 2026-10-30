"""Generic component-completion API: record that a learner finished a non-quiz,
non-video learning component (podcast, reading, slide deck, reflection, activity,
workplace evidence, …) along with the post-completion reflection (KSBs, feedback,
self-reported time).

    POST /learner_api/components/<component_id>/complete/?learnerId=<id>&kind=<commercial|apprenticeship>
        -> appends a {"kind": "component", "componentType": <type>, ...} record to
           "Learner"."Active_users"."Training_plan_progress"

Mirrors learner_api.videos.submit_video_progress exactly (same unified progress
log, same reflection shape, no grading) — videos keep their own endpoint/`kind`
so existing analytics are undisturbed; everything else flows through here.
"""
import json
import logging

from django.db import DatabaseError, IntegrityError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .active_users import ComponentReferenceError, save_progress_record, sync_active_user
from .identity import learner_profile_for_source
from .models import CommercialUser, EnrolmentUser
from .time_tracking import (
    TrackingSessionError,
    tracking_session_already_used,
    verify_tracking_session,
)
from .working_rules import (
    DeclaredCompletionError,
    resolve_completion_instants,
    validation_response_payload,
)
from login.permissions import learner_self_or_admin

from .marking_queue_entry import queue_for_marking, requires_tutor_validation

logger = logging.getLogger(__name__)

SOURCE_MODELS = {
    "commercial": CommercialUser,
    "apprenticeship": EnrolmentUser,
}

# Human action label + a fallback display noun per component type, used for the
# activity feed. Any unknown type falls back to a generic "Completed activity".
TYPE_ACTIONS = {
    "podcast": ("Listened to podcast", "Podcast"),
    "reading": ("Completed reading", "Reading"),
    "powerpoint": ("Reviewed slides", "PowerPoint"),
    "reflection": ("Submitted reflection", "Reflection"),
    "activity": ("Completed activity", "Activity"),
    "workplace-evidence": ("Logged workplace evidence", "Workplace evidence"),
    "live_session": ("Completed live session", "Live session"),
    "recording placeholder": ("Watched recording", "Recording"),
}


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _format_clock(seconds):
    """Seconds -> "MM:SS"."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return None
    m, s = divmod(max(total, 0), 60)
    return f"{m:02d}:{s:02d}"


def _component_meta(component_id):
    """Live (type, title) for a component (curriculum.components).
    Returns (None, None) if not found / on error."""
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "SELECT type, title FROM curriculum.components WHERE id = %s",
                [component_id],
            )
            row = cur.fetchone()
    except DatabaseError:
        return None, None
    if not row:
        return None, None
    ctype, title = row
    return (ctype or None), (title or None)


# Assignment evidence is checked by the monthly form (including library/links).
# Keep the
# helper for compatibility with callers, but no component is gated by a file.
EVIDENCE_COMPONENT_TYPES = set()


def normalise_component_type(value):
    """Component types are stored with underscores ('live_session') but reach us
    hyphenated from some clients — normalise before comparing."""
    return str(value or "").strip().lower().replace("-", "_")


def component_requires_evidence(component_type):
    return normalise_component_type(component_type) in EVIDENCE_COMPONENT_TYPES


def _assignment_form_ready(component_id, kind, learner_id, assignment_topic=""):
    """An assignment can complete only after all monthly submission checks pass.

    The wizard saves these as a draft before it calls the progress endpoint;
    checking the database here makes the rule authoritative rather than a UI-
    only validation that a direct request could bypass.
    """
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                'SELECT full_submission FROM "Learner"."learning_reflection_submissions" '
                'WHERE learner_kind = %s AND learner_id = %s '
                "AND activity_type = 'assignment' AND activity_id = %s AND assignment_topic_id = %s LIMIT 1",
                [kind, str(learner_id), component_id, assignment_topic],
            )
            row = cur.fetchone()
    except DatabaseError as exc:
        logger.warning("Could not verify assignment form for %s: %s", component_id, exc)
        return False
    if not row:
        return False
    payload = row[0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    if not isinstance(payload, dict):
        return False
    # Imported history is not a new completion. Missing client version/origin
    # must never be a path around the eight-step submission requirements.
    if payload.get('submissionOrigin') == 'imported_legacy':
        return False
    from .monthly_assignment import assignment_checks
    try:
        return all(check['passed'] for check in assignment_checks({
            **payload, 'learnerKind': kind, 'learnerId': str(learner_id), 'activityId': component_id,
        }))
    except (DatabaseError, ValueError, TypeError):
        logger.warning('Could not verify monthly submission for %s', component_id)
        return False


def _component_ksb_mappings(component_id):
    try:
        with connections["enrolment"].cursor() as cur:
            cur.execute(
                "SELECT ksb_mappings FROM curriculum.components WHERE id = %s LIMIT 1",
                [component_id],
            )
            row = cur.fetchone()
            canonical = row[0] if row else []
            if isinstance(canonical, str):
                try:
                    canonical = json.loads(canonical) if canonical else []
                except (TypeError, ValueError):
                    canonical = []
            mappings = []
            if isinstance(canonical, list):
                for item in canonical:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("code") or item.get("ksbCode") or item.get("ksb_code") or "").strip()
                    if code:
                        mappings.append({
                            "code": code,
                            "weight": item.get("weight") or 0,
                        })
            if mappings:
                return mappings

            cur.execute(
                "SELECT ksb_code, weight FROM curriculum.ksb_mappings "
                "WHERE component_id = %s AND ksb_code IS NOT NULL AND ksb_code <> '' "
                "AND deleted_at IS NULL AND COALESCE(is_programme_deleted, false) = false "
                "ORDER BY ksb_code",
                [component_id],
            )
            return [{"code": row[0], "weight": row[1] or 0} for row in cur.fetchall()]
    except DatabaseError as exc:
        logger.warning("Could not resolve KSB mappings for component %s: %s", component_id, exc)
        return []


def component_ksb_codes(component_id):
    """The KSB codes authored against a component, in a stable order.

    These are applied automatically on completion — the learner is not asked to
    pick KSBs, because the mapping (and its weighting) is already authored on
    the component itself. Resolved server-side so the credited KSBs always match
    the curriculum rather than whatever a client happens to post.
    """
    seen = set()
    codes = []
    for mapping in _component_ksb_mappings(component_id):
        code = str(mapping.get("code") or "").strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def _completion_criteria(component_id, kind, learner_id, component_type=None):
    """Evaluate the completion gate for a component.

    KSB weights measure curriculum contribution and never block completion.
    Assignment evidence is optional; its three written sections are validated
    separately by ``_assignment_form_ready`` before this helper runs.

    Returns (ok, detail), with the outstanding evidence requirement when gated.
    """
    ksb_weight, ksb_count, evidence_count = 0.0, 0, 0
    needs_evidence = component_requires_evidence(component_type)
    if not needs_evidence:
        return True, None
    try:
        with connections["enrolment"].cursor() as cur:
            mappings = _component_ksb_mappings(component_id)
            ksb_weight = sum(float(item.get("weight") or 0) for item in mappings)
            ksb_count = len(mappings)

            if needs_evidence:
                # Only approved uploads count — a quarantined or rejected file
                # is not usable evidence.
                cur.execute(
                    'SELECT COUNT(*) FROM "Learner"."evidence_files" '
                    "WHERE section_ref = %s AND learner_kind = %s AND learner_id = %s "
                    "AND status = 'approved'",
                    [component_id, kind, str(learner_id)],
                )
                row = cur.fetchone()
                evidence_count = int(row[0] or 0) if row else 0
    except DatabaseError as exc:
        # Fail open: a lookup failure must not strand a learner who has done
        # the work. The criteria are re-checked on every submit.
        logger.warning("Could not evaluate completion criteria for %s: %s", component_id, exc)
        return True, None

    # KSB weight is progress metadata, not a learner completion gate.
    weight_ok = True
    evidence_ok = (evidence_count > 0) if needs_evidence else True
    detail = {
        "ksbWeightTotal": ksb_weight,
        "ksbWeightTarget": 100.0,
        "ksbWeightMet": weight_ok,
        "ksbMappingCount": ksb_count,
        "evidenceRequired": needs_evidence,
        "evidenceCount": evidence_count,
        "evidenceMet": evidence_ok,
    }
    return (weight_ok and evidence_ok), detail


@csrf_exempt
@learner_self_or_admin(query_param="learnerId")
def submit_component_progress(request, component_id):
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    kind = (request.GET.get("kind") or "").strip()
    learner_id = (request.GET.get("learnerId") or "").strip()
    model = SOURCE_MODELS.get(kind)
    if model is None or not learner_id:
        return _error("kind and learnerId query params are required.", 400)

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError) as exc:
        return _error(f"Invalid JSON body: {exc}", 400)

    # Working rules are decided here, before any lookup, criteria check or
    # write: a refusal must leave no completion, no percentage change and no
    # history entry behind.
    submitted_at_dt = timezone.now()
    try:
        declared_at, validation_reason = resolve_completion_instants(
            payload, submitted_at_dt, component_id=component_id,
        )
    except DeclaredCompletionError as exc:
        return JsonResponse(validation_response_payload(exc), status=409)
    except DatabaseError:
        return _error("Could not verify the working-hours holiday calendar. Please try again.", 503)

    week_title = payload.get("week")
    module_title = payload.get("module")
    time_taken_seconds = payload.get("timeTakenSeconds")
    # KSBs are auto-mapped from the component's authored mappings rather than
    # chosen by the learner. Any "ksbs" in the payload is ignored: the component
    # already declares which KSBs it develops, and at what weight.
    ksbs = component_ksb_codes(component_id)
    if not ksbs and isinstance(payload.get("ksbs"), list):
        # Fall back to the client's list only for components with no authored
        # mappings, so legacy/unmapped content keeps working as before.
        ksbs = payload["ksbs"]
    feedback = payload.get("feedback") or ""
    reported_time = payload.get("reportedTime") or ""
    reflection_skipped = payload.get("skipReflection") is True
    time_entry_source = "input" if payload.get("timeEntrySource") == "input" else "timer"
    client_title = payload.get("componentTitle") or None
    client_type = (payload.get("componentType") or "").strip() or None

    from .assignment_topics import topic_id
    try:
        assignment_topic = topic_id(payload.get("assignmentTopicId"))
    except ValueError as exc:
        return _error(str(exc), 400)
    live_type, live_title = _component_meta(component_id)
    if assignment_topic and normalise_component_type(live_type) != "assignment":
        return _error("Topics are only available for assignment components.", 400)
    component_type = client_type or live_type or "component"
    component_title = client_title or live_title or TYPE_ACTIONS.get(component_type, (None, "Activity"))[1]

    try:
        source = model.objects.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except (DatabaseError, ValueError) as exc:
        return _error(f"Database error: {exc}", 502)

    # Only the monthly assignment goes through the eight-step submission. Once
    # an author flags one, the plan's other assignments complete like any other
    # activity (see assignment_settings).
    monthly_submission = False
    if normalise_component_type(live_type or client_type) == "assignment":
        from .assignment_settings import monthly_assignment_required
        monthly_submission = bool(assignment_topic) or monthly_assignment_required(component_id, source)
    if monthly_submission and not _assignment_form_ready(
        component_id, kind, learner_id, assignment_topic,
    ):
        return _error("Complete the monthly submission quality checks, presentation and coaching booking, then save before submitting.", 409)

    # Generic completion criteria. Assignment form readiness is enforced above
    # using the master component type, so it cannot be bypassed by a client that
    # labels an assignment as another activity type.
    criteria_ok, criteria = _completion_criteria(
        component_id, kind, learner_id, live_type or client_type,
    )
    if not criteria_ok:
        missing = []
        if not criteria["evidenceMet"]:
            missing.append("at least one evidence file must be uploaded")
        return JsonResponse(
            {
                "error": "This component cannot be completed yet: " + ", and ".join(missing) + ".",
                "criteria": criteria,
            },
            status=409,
        )

    try:
        active = learner_profile_for_source(source, learner_id, active_only=True)
        if active is None:
            active = sync_active_user(source)
    except DatabaseError as exc:
        return _error(f"Database error: {exc}", 502)
    if active is None:
        return _error(
            "This learner does not have an active learner profile, so progress cannot be saved.",
            409,
        )

    history = (active.training_plan_progress if active and isinstance(active.training_plan_progress, list) else [])
    # 1-based count of prior completions of THIS component.
    attempt_number = sum(
        1 for r in history if r.get("kind") == "component" and r.get("componentId") == component_id
    ) + 1

    try:
        tracking = verify_tracking_session(
            payload.get("trackingToken"),
            activity_kind="component",
            activity_id=component_id,
            learner_kind=kind,
            learner_id=learner_id,
            claimed_seconds=time_taken_seconds,
            submitted_at=submitted_at_dt,
        )
    except TrackingSessionError as exc:
        return _error(str(exc), 400)
    if tracking_session_already_used(tracking["sessionId"]):
        return _error("This activity timing session has already been submitted.", 409)
    started_at = tracking["startedAt"].isoformat()
    submitted_at = submitted_at_dt.isoformat()
    # The learner chooses Timer or Input before finishing. Keep that selection
    # for display and actual-hours totals; verifiedSeconds remains separate
    # signed-session evidence. Explicit units prevent hours/minutes ambiguity.
    time_taken = _format_clock(tracking["claimedSeconds"])
    selected_time = f'{tracking["claimedSeconds"] / 60} minutes'

    record = {
        "kind": "component",
        "componentType": component_type,
        "componentId": component_id,
        **({"assignmentTopicId": assignment_topic} if assignment_topic else {}),
        "attempt": attempt_number,
        "ksbs": ksbs,                          # KSB codes the learner selected
        "feedback": feedback,                  # reflection note
        "reportedTime": selected_time,         # selected Timer/Input duration
        "reflectionSkipped": reflection_skipped,
        "startedAt": started_at,
        "submittedAt": submitted_at,
        "timeTaken": time_taken,
        "timeTrackingSource": f'{tracking["source"]}:{time_entry_source}',
        "timeTrackingCalculation": tracking["calculation"],
        "timeTrackingSessionId": tracking["sessionId"],
        "claimedSeconds": tracking["claimedSeconds"],
        "serverSessionSeconds": tracking["serverSessionSeconds"],
        "verifiedSeconds": tracking["verifiedSeconds"],
        "outsideWorkingHours": bool(validation_reason),
        "outsideWorkingHoursConfirmed": False,
        "insideWorkingHoursConfirmed": False,
        "insideWorkingHoursConfirmedAt": None,
        "outsideWorkingHoursConfirmedAt": None,
        "declaredCompletedAt": declared_at.isoformat() if declared_at else None,
        "submissionValidationReason": validation_reason,
    }

    action, _noun = TYPE_ACTIONS.get(component_type, ("Completed activity", "Activity"))
    activity = {
        "kind": "component",
        "componentType": component_type,
        "action": action,
        "title": component_title or "Activity",
        "detail": selected_time,
        "componentId": component_id,
        "week": week_title,
        "module": module_title,
        "at": submitted_at,
    }
    try:
        if monthly_submission:
            from .monthly_assignment import complete_saved_assignment
            complete_saved_assignment(kind, learner_id, component_id, record,
                lambda: save_progress_record(active, record, {**activity, "detail": record["reportedTime"]}),
                assignment_topic=assignment_topic)
        else:
            save_progress_record(active, record, activity)
    except ComponentReferenceError as exc:
        return _error(str(exc), 400)
    except ValueError as exc:
        return _error(str(exc), 409)
    except IntegrityError as exc:
        if "learner_progress_tracking_session_uq" in str(exc):
            return _error("This activity timing session has already been submitted.", 409)
        return _error(f"Database error saving progress: {exc}", 502)
    except DatabaseError as exc:
        return _error(f"Database error saving progress: {exc}", 502)

    # An activity the author marked for tutor validation is handed in here, not
    # finished here: the marking queue reads
    # Learner.learning_reflection_submissions, so without a row there the coach
    # never sees the work. Components with a reflection flow write their own on
    # submit; an assignment authored with reflection_required=false has none, so
    # it is written from the completion with the evidence the learner uploaded.
    awaiting_validation = bool(assignment_topic and requires_tutor_validation(component_id))
    if not assignment_topic and requires_tutor_validation(component_id):
        queue_for_marking(
            component_id=component_id,
            kind=kind,
            learner_id=learner_id,
            context={
                "learnerName": str(getattr(source, "username", "") or "").strip(),
                "programmeName": str(getattr(source, "programme", "") or "").strip(),
                # A non-monthly assignment is handed in as an ordinary activity,
                # the type its reflection is saved under, so it does not take
                # the monthly submission's row.
                "activityType": "activity" if (
                    normalise_component_type(component_type) == "assignment" and not monthly_submission
                ) else component_type,
                "activityTitle": component_title or "Activity",
                "moduleTitle": module_title or "",
                "weekTitle": week_title or "",
                "plannedOtjh": payload.get("plannedOtjh", reported_time),
                # The marking schema stores decimal hours. ``timeTaken`` above
                # remains the learner-facing MM:SS clock value.
                "actualTimeHours": record["claimedSeconds"] / 3600,
                "progressEntryId": None,
            },
        )
        awaiting_validation = True

    return JsonResponse({
        "record": record,
        "componentTitle": component_title or "Activity",
        "componentType": component_type,
        "week": week_title,
        "module": module_title,
        # True when the activity now sits with a coach: the learner has finished
        # their part, and the component shows as complete only once it is
        # accepted.
        "awaitingCoachValidation": awaiting_validation,
    })
