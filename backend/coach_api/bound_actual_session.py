"""Read a protected imported call claim against its canonical meeting owner.

This is a local validation only. A caller performing Graph work must also
compare the independently resolved provider target before using the context.
"""
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError

from .migrated_session_scope import session_context_from_claim
from .models import ImportedMeetingOwnership, ImportedReviewInstance


class BoundSessionConflict(ValueError):
    """A saved claim or its ownership no longer matches this booking."""


@dataclass(frozen=True)
class BoundSession:
    context: object
    claim: dict


def bound_session(record, *, overlay=None, target=None, lock=False):
    """Return an exact claimed call, or None when no claim was ever made.

    The caller has already authorized the source-qualified Review. In a write
    transaction it locks the calendar before calling this function; registry
    and overlay locks follow that order.
    """
    if overlay is not None:
        state = overlay.meeting_intelligence or {}
        if not isinstance(state, dict) or state.get('actualSession') is None:
            return None
        if not getattr(settings, 'APTEM_ACTUAL_SESSION_ASSOCIATION_POLICY_APPROVED', False):
            raise BoundSessionConflict('The claimed meeting association is unavailable.')
    owners = ImportedMeetingOwnership.objects
    if lock:
        owners = owners.select_for_update()
    try:
        owner = owners.filter(calendar_event_id=record.pk).first()
    except DatabaseError:
        raise BoundSessionConflict('The claimed meeting registry is unavailable.') from None
    if owner is None:
        if overlay and isinstance(overlay.meeting_intelligence, dict) and overlay.meeting_intelligence.get('actualSession'):
            raise BoundSessionConflict('The claimed meeting owner is unavailable.')
        return None
    if owner.overlay_id is None:
        if overlay and isinstance(overlay.meeting_intelligence, dict) and overlay.meeting_intelligence.get('actualSession'):
            raise BoundSessionConflict('The claimed Review is not registered.')
        return None
    if overlay is None:
        overlays = ImportedReviewInstance.objects
        if lock:
            overlays = overlays.select_for_update()
        try:
            overlay = overlays.filter(pk=owner.overlay_id).first()
        except DatabaseError:
            raise BoundSessionConflict('The claimed Review is unavailable.') from None
    if overlay is None:
        raise BoundSessionConflict('The claimed Review is unavailable.')
    state = overlay.meeting_intelligence or {}
    claim = state.get('actualSession') if isinstance(state, dict) else None
    if claim is None:
        return None
    session = session_context_from_claim(record, claim)
    if (session is None
            or not getattr(settings, 'APTEM_ACTUAL_SESSION_ASSOCIATION_POLICY_APPROVED', False)
            or owner.state != ImportedMeetingOwnership.STATE_ACTIVE
            or owner.provenance != 'graph-calendar-verified'
            or owner.overlay_id != overlay.pk
            or owner.source_review_id != overlay.source_review_id
            or owner.learner_id != overlay.learner_id
            or owner.review_event_key != overlay.event_key
            or owner.review_event_key != record.event_key
            or owner.owner_email.casefold() != record.owner_email.casefold()
            or owner.owner_email.casefold() != overlay.owner_email.casefold()
            or owner.graph_event_id != record.graph_event_id
            or owner.booking_fingerprint != claim.get('bookingFingerprint')
            or claim.get('sourceReviewId') != owner.source_review_id
            or claim.get('overlayEventKey') != overlay.event_key
            or claim.get('tenantId') != str(owner.tenant_id)
            or claim.get('organizerId') != str(owner.organizer_id)
            or claim.get('onlineMeetingId') != owner.online_meeting_id
            or claim.get('associationMethod') != 'single-run-corroborated'):
        raise BoundSessionConflict('The claimed meeting association changed.')
    if target is not None and (
            target.calendar_id != record.pk
            or target.graph_event_id != owner.graph_event_id
            or target.booking_fingerprint != owner.booking_fingerprint
            or target.tenant_id != str(owner.tenant_id)
            or target.organizer_id != str(owner.organizer_id)
            or target.online_meeting_id != owner.online_meeting_id):
        raise BoundSessionConflict('The provider meeting identity changed.')
    return BoundSession(session, claim)
