"""Short-lived generation ownership; never hold a DB lock during provider I/O."""
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from .migrated_session_scope import instant
from .models import ImportedReviewInstance


class SummaryInProgress(ValueError):
    pass


def claim(overlay):
    """Caller holds the overlay lock. Internal lease does not advance answerVersion."""
    state = dict(overlay.meeting_intelligence or {})
    attempt = state.get('_summaryAttempt') or {}
    expiry = instant(attempt.get('expiresAt'))
    if expiry and expiry > timezone.now():
        raise SummaryInProgress('A summary is already being generated for this review. Wait for it to finish, then refresh.')
    token = str(uuid4())
    state['_summaryAttempt'] = {'id': token, 'owner': overlay.owner_email,
                                'expiresAt': (timezone.now() + timedelta(minutes=15)).isoformat()}
    overlay.meeting_intelligence = state
    overlay.save(update_fields=['meeting_intelligence'])
    return token


def owns(overlay, token):
    return ((overlay.meeting_intelligence or {}).get('_summaryAttempt') or {}).get('id') == token


def release(pk, owner, token):
    # Overlay-only cleanup acquires no calendar/learner locks and can never
    # delete a newer attempt. The server-created token scopes cleanup even if
    # ownership changed during provider I/O; no answer/identity field is written.
    with transaction.atomic():
        overlay = ImportedReviewInstance.objects.select_for_update().filter(pk=pk).first()
        if overlay and owns(overlay, token) and overlay.meeting_intelligence['_summaryAttempt'].get('owner') == owner:
            state = dict(overlay.meeting_intelligence or {})
            state.pop('_summaryAttempt', None)
            overlay.meeting_intelligence = state
            overlay.save(update_fields=['meeting_intelligence'])
