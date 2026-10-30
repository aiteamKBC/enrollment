"""Video-progress API: record that a learner watched a video component, along
with the post-watch reflection (KSBs, feedback, self-reported time).

    POST /learner_api/videos/<component_id>/complete/?learnerId=<id>&kind=<commercial|apprenticeship>
        -> appends a "kind": "video" record to
           "Learner"."Active_users"."Training_plan_progress"

The record mirrors the quiz-attempt shape (learner_api.quizzes) so the unified
progress log stays consistent; there is no grading — a video is either watched
and reflected on, or not.
"""
import json

from django.db import DatabaseError, IntegrityError, connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .active_users import ComponentReferenceError, save_progress_record, sync_active_user
from .components import component_ksb_codes
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

SOURCE_MODELS = {
    "commercial": CommercialUser,
    "apprenticeship": EnrolmentUser,
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


def _video_title(component_id):
    """Live display title for a video component (curriculum.components).
    Returns (title, type) or (None, None) if not found / on error."""
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
    return (title or "Video"), ctype


@csrf_exempt
# Owners and admins can record video progress for the selected learner.
@learner_self_or_admin(query_param="learnerId")
def submit_video_progress(request, component_id):
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

    # Decided before any lookup or write, so a refusal leaves no completion,
    # no percentage change and no history entry behind.
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
    # picked by the learner (see components.component_ksb_codes). The client's
    # list is only a fallback for components with no authored mappings.
    ksbs = component_ksb_codes(component_id)
    if not ksbs and isinstance(payload.get("ksbs"), list):
        ksbs = payload["ksbs"]
    feedback = payload.get("feedback") or ""
    reflection_skipped = payload.get("skipReflection") is True
    time_entry_source = "input" if payload.get("timeEntrySource") == "input" else "timer"
    # Client may pass the title it rendered; fall back to a live master lookup.
    video_title = payload.get("videoTitle") or None

    if video_title is None:
        video_title, _ctype = _video_title(component_id)

    try:
        source = model.objects.get(pk=learner_id)
    except model.DoesNotExist:
        return _error("Learner not found.", 404)
    except (DatabaseError, ValueError) as exc:
        return _error(f"Database error: {exc}", 502)

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

    history = active.training_plan_progress if isinstance(active.training_plan_progress, list) else []
    # 1-based count of prior completions of THIS video component.
    attempt_number = sum(
        1 for r in history if r.get("kind") == "video" and r.get("componentId") == component_id
    ) + 1

    try:
        tracking = verify_tracking_session(
            payload.get("trackingToken"),
            activity_kind="video",
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

    # Slim, id-referenced record. The videoTitle/week/module NAMES are dropped —
    # the plan tree resolves them from componentId.
    record = {
        "kind": "video",
        "componentId": component_id,
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

    if active is not None:
        activity = {
            "kind": "video",
            "action": "Watched video",
            "title": video_title or "Video",
            "detail": selected_time,
            "componentId": component_id,
            "week": week_title,
            "module": module_title,
            "at": submitted_at,
        }
        try:
            save_progress_record(active, record, activity)
        except ComponentReferenceError as exc:
            return _error(str(exc), 400)
        except IntegrityError as exc:
            if "learner_progress_tracking_session_uq" in str(exc):
                return _error("This activity timing session has already been submitted.", 409)
            return _error(f"Database error saving progress: {exc}", 502)
        except DatabaseError as exc:
            return _error(f"Database error saving progress: {exc}", 502)
        # Engagement points award themselves: save_progress_record registers a
        # post-commit hook (engagement_api.hooks.award_for_progress) that fires
        # once this save actually commits — see active_users.save_progress_record.

    # Echo the display title/week/module in the RESPONSE (not stored) so the
    # results screen can render immediately without resolving componentId.
    return JsonResponse({
        "record": record,
        "videoTitle": video_title or "Video",
        "week": week_title,
        "module": module_title,
    })
