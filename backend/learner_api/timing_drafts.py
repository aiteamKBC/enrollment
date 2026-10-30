"""Durable timing checkpoints. Renew a lease, never replace an activity's evidence.

Tokens still require a valid signature and the authenticated learner/activity.
An expired legacy token can be recovered here, but its original evidence window
stays bounded to 24 hours. Time between leases cannot become verified time.
"""
import hashlib
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

DB = 'enrolment'
LEASE_MS = 24 * 60 * 60 * 1000


class ActivityTimingDraft(models.Model):
    session_id = models.CharField(primary_key=True, max_length=64)
    token_hash = models.CharField(max_length=64)
    started_at = models.DateTimeField()
    windows = models.JSONField(default=list)
    elapsed_seconds = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField()

    class Meta:
        app_label = 'learner_api'
        db_table = 'learner_activity_timing_drafts'
        managed = False


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def elapsed_ms(started_at, now):
    return max(0, int((now - started_at).total_seconds() * 1000))


def evidence_budget(windows, offset):
    return sum(max(0, min(end, offset) - start) for start, end in windows) // 1000


def read_evidence(token, claims, started_at, now):
    from .time_tracking import TrackingSessionError
    draft = ActivityTimingDraft.objects.using(DB).filter(session_id=claims['sessionId']).first()
    if draft is None or draft.token_hash != token_hash(token):
        raise TrackingSessionError('Your saved timing session needs to reconnect. Your draft is kept.',
                                   code='expired_tracking_session')
    offset = elapsed_ms(started_at, now)
    if now < started_at or offset > draft.windows[-1][1]:
        raise TrackingSessionError('Your timing session needs to reconnect. Your draft is kept.',
                                   code='expired_tracking_session')
    return {
        'serverSessionSeconds': evidence_budget(draft.windows, offset),
        'trackingWindows': draft.windows,
        'trackingExpiresAt': started_at + timedelta(milliseconds=draft.windows[-1][1]),
    }


def checkpoint(token, *, activity_kind, activity_id, learner_kind, learner_id, seconds):
    from .time_tracking import (TrackingSessionError, load_tracking_claims,
                               tracking_session_already_used, _normalise_claimed_seconds)
    claims, started_at = load_tracking_claims(token, activity_kind=activity_kind, activity_id=activity_id,
        learner_kind=learner_kind, learner_id=learner_id, allow_expired=True)
    amount = _normalise_claimed_seconds(seconds)
    if amount > 2**31 - 1:
        raise TrackingSessionError('The saved timing value is too large.')
    now = timezone.now()
    if now < started_at:
        raise TrackingSessionError('The timing session has an invalid start time.')
    offset = elapsed_ms(started_at, now)
    with transaction.atomic(using=DB):
        draft, _ = ActivityTimingDraft.objects.using(DB).get_or_create(
            session_id=claims['sessionId'], defaults={
                'token_hash': token_hash(token), 'started_at': started_at,
                'windows': [[0, LEASE_MS]], 'updated_at': now,
            })
        draft = ActivityTimingDraft.objects.using(DB).select_for_update().get(pk=draft.pk)
        if draft.token_hash != token_hash(token) or draft.started_at != started_at:
            raise TrackingSessionError('The timing session does not match the saved draft.', code='tracking_identity_mismatch')
        if tracking_session_already_used(claims['sessionId']):
            raise TrackingSessionError('This activity has already been saved. Reload to see your progress.',
                                       code='tracking_session_used')
        windows = [list(window) for window in draft.windows]
        if offset <= windows[-1][1]:
            windows[-1][1] = max(windows[-1][1], offset + LEASE_MS)
        else:
            windows.append([offset, offset + LEASE_MS])
        # A retry or an older tab must not reduce or add the same seconds twice.
        draft.elapsed_seconds = max(draft.elapsed_seconds, amount)
        draft.windows = windows
        draft.updated_at = now
        draft.save(using=DB, update_fields=['elapsed_seconds', 'windows', 'updated_at'])
    return {
        'trackingToken': token, 'sessionId': claims['sessionId'], 'startedAt': claims['startedAt'],
        'countingMode': claims['countingMode'], 'persistent': True,
        'savedSeconds': draft.elapsed_seconds, 'elapsedMilliseconds': offset,
    }
