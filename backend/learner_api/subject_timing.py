"""Measured time for local attempts on owned legacy activities.

Evidence lives in the existing attempt snapshot; imported/audited history is
never rewritten. Tokens are scoped to one learner, placement and attempt.
"""
from uuid import uuid4
from urllib.parse import urlparse
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def tracking_identity(group_id, activity_id, catalogue_kind, attempt_id):
    return f'legacy:{group_id}:{catalogue_kind}:{activity_id}:{attempt_id}'


def supports_playback_timing(item):
    """Match the media players that expose audible playback events.

    Vimeo/Spotify and arbitrary embeds use visible-page time, explicitly shown
    as such in the UI; they must not pretend to report playback events.
    """
    if item.get('can_embed') is False:
        return False
    url = urlparse(str(item.get('url') or ''))
    host = (url.hostname or '').lower()
    if item.get('kind') == 'audio':
        return host != 'open.spotify.com'
    if item.get('kind') != 'video':
        return False
    return (host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}
            or url.path.lower().endswith(('.mp4', '.webm', '.ogg', '.mov', '.m4v'))
            or url.path.endswith('/source-file/'))


def create_session(kind, learner_id, group_id, activity_id, definition):
    from .time_tracking import issue_tracking_session
    attempt_id = str(uuid4())
    activity_kind = 'quiz' if definition.get('quiz') else 'component'
    # Mixed reading/media and embedded documents use visible-page time, just
    # like native readings. Standalone audio/video uses actual playback.
    media = definition.get('media') or []
    playback_only = (not definition.get('has_reading') and not definition.get('reading_html')
                     and media and all(supports_playback_timing(item) for item in media))
    mode = 'active_quiz' if activity_kind == 'quiz' else 'active_playback' if playback_only else 'visible_page'
    session = issue_tracking_session(activity_kind=activity_kind,
        activity_id=tracking_identity(group_id, activity_id, definition.get('catalogue_kind', 'material'), attempt_id),
        learner_kind=kind, learner_id=learner_id, counting_mode=mode)
    return attempt_id, {**session, 'learnerKind': kind, 'activityKind': activity_kind}


def completion_time(definition, payload, *, kind, learner_id, group_id, activity_id, attempt_id):
    from .time_tracking import TrackingSessionError, TRACKING_SALT, _normalise_claimed_seconds
    from .working_rules import resolve_completion_instants
    session = definition.get('studyTimingSession')
    if not session:
        if payload.get('trackingToken') or payload.get('timeTakenSeconds') is not None:
            raise TrackingSessionError('Start a timed activity before recording study time.')
        return None  # Existing untimed attempts retain their original contract.
    if kind != session['learnerKind'] or payload.get('trackingToken') != session['trackingToken']:
        raise TrackingSessionError('The timing session does not match this attempt.')
    submitted_at = timezone.now()
    # This exact signed token is already stored in the locked, owned attempt.
    # The unfinished attempt is its durable scope, so restoring a draft need
    # not create an unrelated native timing-draft row after the token's TTL.
    try:
        claims = signing.loads(payload['trackingToken'], salt=TRACKING_SALT)
    except signing.BadSignature:
        raise TrackingSessionError('The activity timing session is invalid.') from None
    expected = {'activityKind': session['activityKind'], 'learnerKind': kind, 'learnerId': str(learner_id),
        'activityId': tracking_identity(group_id, activity_id, definition.get('catalogue_kind', 'material'), attempt_id),
        'sessionId': session['sessionId'], 'countingMode': session['countingMode']}
    if not isinstance(claims, dict) or any(str(claims.get(key, '')) != str(value) for key, value in expected.items()):
        raise TrackingSessionError('The timing session does not match this learner or activity.')
    started_at = parse_datetime(str(claims.get('startedAt') or ''))
    if started_at is None or timezone.is_naive(started_at):
        raise TrackingSessionError('The activity timing session has an invalid start time.')
    claimed = _normalise_claimed_seconds(payload.get('timeTakenSeconds'))
    budget = max(0, int((submitted_at - started_at).total_seconds()))
    tracking = {'sessionId': claims['sessionId'], 'startedAt': started_at,
        'claimedSeconds': claimed, 'verifiedSeconds': min(claimed, budget),
        'serverSessionSeconds': budget, 'countingMode': claims['countingMode'],
        'source': f"signed_attempt_capped_{claims['countingMode']}",
        'calculation': 'min(client_active_seconds, signed_server_session_seconds)'}
    # Legacy placements have no native component/cohort identity. Use the same
    # working-time rule and correction flow as other unplaced learning.
    declared, reason = resolve_completion_instants(payload, submitted_at)
    return {**tracking, 'version': 1, 'startedAt': tracking['startedAt'].isoformat(),
        'submittedAt': submitted_at.isoformat(), 'declaredCompletedAt': declared.isoformat() if declared else None,
        'submissionValidationReason': reason}


def measured_seconds(value):
    """Only the new server-verified evidence may award local attempt hours."""
    if not isinstance(value, dict) or value.get('version') != 1:
        return None
    seconds = value.get('verifiedSeconds')
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        return None
    return seconds
