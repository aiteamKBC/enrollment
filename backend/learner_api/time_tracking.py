"""Server-issued timing sessions for learner activities.

The browser measures active time because it can observe playback/visibility.
A signed server session bounds verifiedSeconds by the session duration. This
verification evidence is separate from the learner-selected Timer/Input duration
saved by video and component completions; manual time can exceed this session.
"""
import json
import math
import uuid
from zoneinfo import ZoneInfo

from django.core import signing
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from login.permissions import learner_self_or_admin
from .working_hours_holidays import is_working_hours_holiday


TRACKING_SALT = "learner-api.activity-time.v1"
TRACKING_MAX_AGE_SECONDS = 24 * 60 * 60
ACTIVITY_KINDS = {"quiz", "video", "component"}
COUNTING_MODES = {"active_quiz", "active_playback", "visible_page"}
ALLOWED_MODES_BY_KIND = {
    "quiz": {"active_quiz"},
    "video": {"active_playback", "visible_page"},
    "component": {"visible_page", "active_playback"},
}


class TrackingSessionError(ValueError):
    """The timing token is absent, invalid, expired, or mismatched."""

    def __init__(self, message, *, code='invalid_tracking_session'):
        super().__init__(message)
        self.code = code


def component_access_is_open(at=None):
    """Learning components are available 24/7."""
    return True


def outside_uk_working_hours(at=None):
    """Outside UK weekday 07:00-19:00, including official/manual holidays.

    Europe/London applies GMT/BST automatically. Components remain available.

    No longer the completion gate: a Finish click is judged by
    ``working_rules.working_rule_failure``, which scopes holidays to the
    learner's own cohort and names the reason. Kept as the unscoped predicate
    for callers that only need the coarse question.
    """
    instant = at or timezone.now()
    if timezone.is_naive(instant):
        instant = timezone.make_aware(instant, ZoneInfo("Europe/London"))
    local = instant.astimezone(ZoneInfo("Europe/London"))
    return (local.weekday() >= 5 or local.hour < 7 or local.hour >= 19
            or is_working_hours_holiday(local.date()))


def enforce_component_access_window(at=None):
    # Kept as a compatibility seam for the signed tracking flow. Access is
    # currently unrestricted, so starting and submitting are always allowed.
    return None


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _normalise_claimed_seconds(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise TrackingSessionError("timeTakenSeconds must be a non-negative number.") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise TrackingSessionError("timeTakenSeconds must be a non-negative number.")
    return int(seconds)


def issue_tracking_session(*, activity_kind, activity_id, learner_kind, learner_id, counting_mode, issued_at=None, persistent=False):
    if activity_kind not in ACTIVITY_KINDS:
        raise TrackingSessionError("Unknown activity kind.")
    if not str(activity_id).strip():
        raise TrackingSessionError("activityId is required.")
    if counting_mode not in ALLOWED_MODES_BY_KIND[activity_kind]:
        raise TrackingSessionError("The time-counting mode is not valid for this activity kind.")

    started_at = issued_at or timezone.now()
    enforce_component_access_window(started_at)
    claims = {
        "sessionId": str(uuid.uuid4()),
        "activityKind": activity_kind,
        "activityId": str(activity_id),
        "learnerKind": learner_kind,
        "learnerId": str(learner_id),
        "countingMode": counting_mode,
        "startedAt": started_at.isoformat(),
    }
    if persistent:
        claims['persistent'] = True
    session = {
        "trackingToken": signing.dumps(claims, salt=TRACKING_SALT, compress=True),
        "startedAt": claims["startedAt"],
        "sessionId": claims["sessionId"],
        "countingMode": counting_mode,
    }
    if persistent:
        from .timing_drafts import checkpoint
        session.update(checkpoint(session['trackingToken'], activity_kind=activity_kind, activity_id=activity_id,
            learner_kind=learner_kind, learner_id=learner_id, seconds=0))
    return session


def tracking_session_already_used(session_id):
    """The partial unique index is the race-safe guard; this gives a clear 409."""
    from .models import LearnerProgressEntry

    return LearnerProgressEntry.objects.using("enrolment").filter(
        time_tracking_session_ref=str(session_id or "")
    ).exists()


def load_tracking_claims(token, *, activity_kind, activity_id, learner_kind, learner_id, allow_expired=False):
    if not isinstance(token, str) or not token:
        raise TrackingSessionError("A server-issued trackingToken is required. Reopen the activity and try again.")
    try:
        claims = signing.loads(token, salt=TRACKING_SALT)
    except signing.SignatureExpired:
        raise TrackingSessionError("The activity timing session has expired. Your answers and timing draft are kept.", code='expired_tracking_session') from None
    except signing.BadSignature:
        raise TrackingSessionError("The activity timing session is invalid. Your answers and timing draft are kept.", code='invalid_tracking_signature') from None

    if not isinstance(claims, dict) or not isinstance(claims.get('sessionId'), str) or not claims['sessionId']:
        raise TrackingSessionError('The activity timing session is invalid.')
    expected = {
        "activityKind": activity_kind,
        "activityId": str(activity_id),
        "learnerKind": learner_kind,
        "learnerId": str(learner_id),
    }
    if any(str(claims.get(key, "")) != str(value) for key, value in expected.items()):
        raise TrackingSessionError("The activity timing session does not match this learner or activity.", code='tracking_identity_mismatch')

    counting_mode = str(claims.get("countingMode") or "")
    if counting_mode not in COUNTING_MODES:
        raise TrackingSessionError("The activity timing session has an invalid counting mode.")
    started_at = parse_datetime(str(claims.get("startedAt") or ""))
    if started_at is None or timezone.is_naive(started_at):
        raise TrackingSessionError("The activity timing session has an invalid start time.")
    if not allow_expired and not claims.get('persistent'):
        try:
            signing.loads(token, salt=TRACKING_SALT, max_age=TRACKING_MAX_AGE_SECONDS)
        except signing.SignatureExpired:
            # Only an authenticated checkpoint can adopt an expired legacy
            # session. Ordinary completion never creates/renews a lease.
            from .timing_drafts import read_evidence
            read_evidence(token, claims, started_at, timezone.now())
    return claims, started_at


def verify_tracking_session(token, *, activity_kind, activity_id, learner_kind, learner_id,
                            claimed_seconds, submitted_at=None):
    claims, started_at = load_tracking_claims(token, activity_kind=activity_kind, activity_id=activity_id,
        learner_kind=learner_kind, learner_id=learner_id)
    counting_mode = claims['countingMode']

    submitted_at = submitted_at or timezone.now()
    enforce_component_access_window(submitted_at)
    server_session_seconds = max(0, int((submitted_at - started_at).total_seconds()))
    evidence = {}
    # Legacy tokens remain supported. Recovery registers their original signed
    # identity; neither the token nor reward event IDs are replaced.
    if claims.get('persistent'):
        from .timing_drafts import read_evidence
        evidence = read_evidence(token, claims, started_at, timezone.now())
    else:
        try:
            signing.loads(token, salt=TRACKING_SALT, max_age=TRACKING_MAX_AGE_SECONDS)
        except signing.SignatureExpired:
            from .timing_drafts import read_evidence
            evidence = read_evidence(token, claims, started_at, timezone.now())
    if evidence:
        from .timing_drafts import evidence_budget, elapsed_ms
        server_session_seconds = evidence_budget(evidence['trackingWindows'], elapsed_ms(started_at, submitted_at))
    claimed = _normalise_claimed_seconds(claimed_seconds)
    verified = min(claimed, server_session_seconds)
    return {
        "sessionId": str(claims.get("sessionId") or ""),
        "startedAt": started_at,
        "submittedAt": submitted_at,
        "claimedSeconds": claimed,
        "verifiedSeconds": verified,
        "countingMode": counting_mode,
        "source": f"signed_session_capped_{counting_mode}",
        "calculation": "min(client_active_seconds, signed_server_session_seconds)",
        **evidence,
        # A declared completion instant can be earlier than the current lease.
        "serverSessionSeconds": server_session_seconds,
    }


@csrf_exempt
@learner_self_or_admin(query_param="learnerId")
def start_time_tracking(request):
    if request.method != "POST":
        return _error("Method not allowed.", 405)

    learner_kind = (request.GET.get("kind") or "").strip()
    learner_id = (request.GET.get("learnerId") or "").strip()
    if learner_kind not in {"commercial", "apprenticeship"} or not learner_id:
        return _error("kind and learnerId query params are required.", 400)
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else {}
    except (ValueError, UnicodeDecodeError) as exc:
        return _error(f"Invalid JSON body: {exc}", 400)

    try:
        session = issue_tracking_session(
            activity_kind=str(payload.get("activityKind") or "").strip(),
            activity_id=str(payload.get("activityId") or "").strip(),
            learner_kind=learner_kind,
            learner_id=learner_id,
            counting_mode=str(payload.get("countingMode") or "").strip(),
            persistent=payload.get('persistent') is True,
        )
    except TrackingSessionError as exc:
        return _error(str(exc), 400)
    from django.conf import settings
    if getattr(settings, 'KBC_SHARED_REWARDS_READY', False) is True:
        from .reward_tracking import capability
        from .mobile import MobileError
        try:
            session['rewardTracking'] = capability(request, str(payload.get('activityKind') or ''), str(payload.get('activityId') or ''))
        except MobileError:
            session['rewardTracking'] = False
    return JsonResponse(session)


@csrf_exempt
@learner_self_or_admin(query_param="learnerId")
def checkpoint_time_tracking(request):
    if request.method != 'POST':
        return _error('Method not allowed.', 405)
    kind = (request.GET.get('kind') or '').strip()
    learner_id = (request.GET.get('learnerId') or '').strip()
    if kind not in {'commercial', 'apprenticeship'} or not learner_id:
        return _error('kind and learnerId query params are required.', 400)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError('Expected an object.')
        from .timing_drafts import checkpoint
        result = checkpoint(payload.get('trackingToken'), activity_kind=payload.get('activityKind'),
            activity_id=payload.get('activityId'), learner_kind=kind, learner_id=learner_id,
            seconds=payload.get('elapsedSeconds', 0))
    except TrackingSessionError as exc:
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=409 if exc.code == 'tracking_session_used' else 400)
    except (ValueError, UnicodeDecodeError):
        return _error('Invalid timing request.', 400)
    response = JsonResponse(result)
    response['Cache-Control'] = 'private, no-store'
    return response
