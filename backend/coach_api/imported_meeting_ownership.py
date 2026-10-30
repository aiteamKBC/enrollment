"""Default-off, source-qualified ownership of imported Review onlineMeetings.

Graph discovery is performed by the caller outside the transaction. This
module reserves only a provider-verified meeting, never a URL-derived one.
Legacy rows remain unverified until a separately approved inventory is complete.
"""
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.utils import timezone

from .actual_session_resolution import VerifiedMeetingTarget, strict_imported_meeting_target
from .migrated_reviews import initial_local_status, review_family
from .models import CoachCalendarEvent, ImportedMeetingOwnership, ImportedReviewInstance


SUPPORTED_FAMILIES = frozenset({'MCM', 'PR', 'PR_SKILLS_RADAR'})


@dataclass(frozen=True)
class MeetingOwnershipError(Exception):
    code: str


def enabled_for(event):
    return bool(getattr(settings, 'APTEM_MEETING_OWNERSHIP_ENABLED', False)
                and isinstance(event, dict)
                and event.get('reviewSource') == 'aptem'
                and review_family(event.get('importedReviewType')) in SUPPORTED_FAMILIES)


def storage_ready():
    """Reject active enforcement until migration 0033 is physically installed."""
    if connections['default'].vendor != 'postgresql':
        return False
    with connections['default'].cursor() as cursor:
        cursor.execute('''
          SELECT
            (SELECT count(*) = 3 FROM pg_class ix JOIN pg_index i ON i.indexrelid=ix.oid
              WHERE i.indrelid=to_regclass('"Coach"."coach_imported_meeting_ownership"')
                AND ix.relname IN ('aptem_owner_provider_uniq', 'aptem_owner_calendar_uniq',
                                   'aptem_owner_active_source_uniq')
                AND i.indisunique AND i.indisvalid),
            (SELECT count(*) = 2 FROM pg_constraint c
              WHERE c.conrelid=to_regclass('"Coach"."coach_imported_meeting_ownership"')
                AND c.conname IN ('aptem_owner_calendar_fk', 'aptem_owner_source_fk')
                AND c.contype='f' AND c.convalidated),
            (SELECT count(*) = 1 FROM pg_constraint c
              WHERE c.conrelid=to_regclass('"Coach"."coach_imported_meeting_ownership"')
                AND c.conname='aptem_owner_provider_valid'
                AND c.contype='c' AND c.convalidated),
            (SELECT count(*) = 2 FROM pg_trigger t
              WHERE ((t.tgrelid=to_regclass('"Coach"."coach_imported_meeting_ownership"')
                      AND t.tgname='aptem_imported_meeting_owner_guard')
                  OR (t.tgrelid=to_regclass('"Learner".coach_calendar_event')
                      AND t.tgname='aptem_owned_calendar_identity_guard'))
                AND t.tgenabled='O' AND NOT t.tgisinternal)
        ''')
        return cursor.fetchone() == (True, True, True, True)


def require_activation_ready():
    if not storage_ready():
        raise MeetingOwnershipError('MEETING_OWNERSHIP_STORAGE_UNAVAILABLE')
    # A unique registry cannot detect an unregistered historical booking with
    # the same remote identity. This gate needs a separately reviewed inventory.
    if not getattr(settings, 'APTEM_MEETING_OWNERSHIP_INVENTORY_APPROVED', False):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_INVENTORY_PENDING')
    if not local_coverage_ready():
        raise MeetingOwnershipError('MEETING_OWNERSHIP_LEGACY_UNREGISTERED')


def local_coverage_ready():
    """A necessary local check, never a substitute for provider reconciliation."""
    with connections['default'].cursor() as cursor:
        cursor.execute('''SELECT NOT EXISTS (
          SELECT 1 FROM "Learner".coach_calendar_event c
          WHERE c.sync_state='synced' AND c.graph_event_id <> ''
            AND c.event_type IN ('mcr', 'progress-review')
            AND (c.event_key LIKE 'imported-review:%%'
                 OR c.idempotency_key LIKE 'learner-book:%%')
            AND NOT EXISTS (
              SELECT 1 FROM "Coach".coach_imported_meeting_ownership o
              WHERE o.calendar_event_id=c.id AND o.state='active'
                AND o.graph_event_id=c.graph_event_id
            )
        )''')
        return cursor.fetchone() == (True,)


def resolve_verified_target(record):
    identity = {}
    _base, error = strict_imported_meeting_target(record, identity_out=identity)
    target = identity.get('target')
    if error or not isinstance(target, VerifiedMeetingTarget):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_PROVIDER_UNVERIFIED')
    return target


def _verify_source_and_overlay(record, event, profile, source):
    """Called under learner -> source -> calendar locks, before the overlay."""
    from .review_sources import ReviewIdentityConflict, booking_for_imported_review

    try:
        source_id = int(event.get('reviewId') or 0)
    except (TypeError, ValueError):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT') from None
    if source_id <= 0 or record.learner_id != int(event.get('learnerId') or 0):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    family = review_family(source.get('review_type'))
    if (family not in SUPPORTED_FAMILIES or family != review_family(event.get('importedReviewType'))
            or source.get('id') != source_id
            or source.get('learner_id') != record.learner_id
            or source.get('completed_date')
            or not initial_local_status(source.get('status'))
            or str(source.get('aptem_review_id')) != str(event.get('aptemReviewId'))
            or record.event_key != f"imported-review:{source.get('aptem_review_id')}"
            or record.owner_email.casefold() != str(event.get('ownerEmail') or '').casefold()
            or record.review_instance_id or record.review_template_id):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    overlays = list(ImportedReviewInstance.objects.select_for_update().filter(
        source_review_id=source_id)[:2])
    if len(overlays) > 1:
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    overlay = overlays[0] if overlays else None
    if overlay and (overlay.learner_id != record.learner_id
                    or overlay.event_key != record.event_key
                    or overlay.owner_email.casefold() != record.owner_email.casefold()
                    or overlay.completed_at
                    or overlay.status not in {ImportedReviewInstance.STATUS_NOT_SCHEDULED,
                                              ImportedReviewInstance.STATUS_SCHEDULED}):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    try:
        linked = booking_for_imported_review(profile, {
            'id': source['id'], 'aptemReviewId': source['aptem_review_id'],
            'type': source['review_type'], 'status': source['status'],
        }, overlay)
    except ReviewIdentityConflict:
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT') from None
    if not linked or linked.pk != record.pk:
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    return source_id, overlay


def finalize_owned_booking(record, warning, event, target):
    """Atomically reserve provider identity and mark this booking synced."""
    try:
        return _finalize_owned_booking(record, warning, event, target)
    except IntegrityError:
        # A unique/FK/trigger failure may contain provider IDs in its raw DB
        # detail. The caller logs only this stable, non-identifying code.
        raise MeetingOwnershipError('MEETING_OWNERSHIP_CONFLICT') from None
    except DatabaseError:
        raise MeetingOwnershipError('MEETING_OWNERSHIP_STORAGE_FAILED') from None


def _finalize_owned_booking(record, warning, event, target):
    from .views import (CALENDAR_GRAPH_SYNC_FIELDS, lock_learner_calendar,
                        public_graph_sync_warning)
    from .review_reconciliation import read_profile, read_source
    if not isinstance(target, VerifiedMeetingTarget):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_PROVIDER_UNVERIFIED')
    with transaction.atomic():
        lock_learner_calendar(record.learner_id)
        try:
            source_id = int(event.get('reviewId') or 0)
        except (TypeError, ValueError):
            raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT') from None
        if source_id <= 0:
            raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
        # Assignment handover also takes the learner calendar lock, then its
        # own Enrolment -> profile order. Do not invert those row locks here.
        profile, _identity = read_profile(record.learner_id, lock=False)
        source = read_source(source_id, lock=True)
        current = CoachCalendarEvent.objects.select_for_update().get(pk=record.pk)
        if (current.sync_state != CoachCalendarEvent.SYNC_SYNCING
                or current.sync_attempt_count != record.sync_attempt_count
                or current.last_sync_attempt_at != record.last_sync_attempt_at
                or current.event_key != record.event_key
                or current.owner_email != record.owner_email
                or current.learner_id != record.learner_id
                or current.scheduled_date != record.scheduled_date
                or current.scheduled_time != record.scheduled_time
                or current.duration_minutes != record.duration_minutes
                or (current.graph_event_id and current.graph_event_id != target.graph_event_id)
                or record.graph_event_id != target.graph_event_id
                or target.calendar_id != record.pk):
            raise MeetingOwnershipError('MEETING_OWNERSHIP_BOOKING_CHANGED')
        source_id, overlay = _verify_source_and_overlay(current, event, profile, source)
        if (CoachCalendarEvent.objects.filter(graph_event_id=target.graph_event_id,
                                              graph_organizer_email__iexact=record.graph_organizer_email)
                .exclude(pk=record.pk).exists()):
            raise MeetingOwnershipError('MEETING_OWNERSHIP_LOCAL_CONFLICT')
        # Publish calendar fields first *inside this transaction*. The registry
        # trigger checks the physical Learner row; any uniqueness failure rolls
        # the calendar update back with the registry insertion.
        with connections['default'].cursor() as cursor:
            cursor.execute("SET LOCAL coach.aptem_owner_verified_sync = 'on'")
        current.graph_event_id = record.graph_event_id
        current.graph_organizer_email = record.graph_organizer_email
        current.meeting_provider = record.meeting_provider
        current.meeting_link = record.meeting_link
        current.graph_web_link = record.graph_web_link
        current.sync_state = CoachCalendarEvent.SYNC_SYNCED
        current.last_graph_sync_error = public_graph_sync_warning(warning)
        current.save(update_fields=CALENDAR_GRAPH_SYNC_FIELDS)
        ownership = (ImportedMeetingOwnership.objects.select_for_update()
                     .filter(calendar_event_id=record.pk).first())
        identity = (str(target.tenant_id), str(target.organizer_id), target.online_meeting_id)
        if ownership:
            if (ownership.state != ImportedMeetingOwnership.STATE_ACTIVE
                    or ownership.source_review_id != source_id
                    or ownership.learner_id != record.learner_id
                    or ownership.review_event_key != record.event_key
                    or ownership.owner_email.casefold() != record.owner_email.casefold()
                    or (str(ownership.tenant_id), str(ownership.organizer_id),
                        ownership.online_meeting_id) != identity
                    or ownership.graph_event_id != target.graph_event_id
                    or (ownership.overlay_id and ownership.overlay_id != getattr(overlay, 'pk', None))):
                raise MeetingOwnershipError('MEETING_OWNERSHIP_CONFLICT')
            ownership.overlay = overlay
            ownership.booking_fingerprint = target.booking_fingerprint
            ownership.save(update_fields=['overlay', 'booking_fingerprint'])
        else:
            ImportedMeetingOwnership.objects.create(
                source_review_id=source_id, learner_id=record.learner_id,
                review_event_key=record.event_key, calendar_event_id=record.pk,
                overlay=overlay, owner_email=record.owner_email,
                tenant_id=target.tenant_id, organizer_id=target.organizer_id,
                online_meeting_id=target.online_meeting_id,
                graph_event_id=target.graph_event_id,
                booking_fingerprint=target.booking_fingerprint,
                verified_at=timezone.now())
        record.sync_state = current.sync_state
        record.last_graph_sync_error = current.last_graph_sync_error


def require_synced_owner(record, event):
    """A legacy synced booking is not proof of provider ownership."""
    try:
        source_id = int(event.get('reviewId') or 0)
    except (TypeError, ValueError):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT') from None
    owner = ImportedMeetingOwnership.objects.filter(calendar_event_id=record.pk).first()
    if (not owner or owner.state != ImportedMeetingOwnership.STATE_ACTIVE
            or owner.source_review_id != source_id
            or owner.learner_id != record.learner_id
            or owner.review_event_key != record.event_key
            or owner.owner_email.casefold() != record.owner_email.casefold()
            or owner.graph_event_id != record.graph_event_id):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_UNVERIFIED')


def bind_owned_overlay(record, overlay):
    """Link an initialized form to its existing verified meeting under locks."""
    owner = (ImportedMeetingOwnership.objects.select_for_update()
             .filter(calendar_event_id=record.pk).first())
    if (not owner or owner.state != ImportedMeetingOwnership.STATE_ACTIVE
            or owner.source_review_id != overlay.source_review_id
            or owner.learner_id != overlay.learner_id
            or owner.review_event_key != overlay.event_key
            or owner.graph_event_id != record.graph_event_id
            or owner.owner_email.casefold() != overlay.owner_email.casefold()
            or owner.overlay_id not in (None, overlay.pk)):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_UNVERIFIED')
    if owner.overlay_id is None:
        owner.overlay = overlay
        owner.save(update_fields=['overlay'])


def required_for_record(record):
    """Identify supported imported families without treating PSP as PR."""
    if not getattr(settings, 'APTEM_MEETING_OWNERSHIP_ENABLED', False):
        return False
    prefix = 'imported-review:'
    if not record.event_key.startswith(prefix):
        return False
    aptem_id = record.event_key[len(prefix):]
    with connections['default'].cursor() as cursor:
        cursor.execute('''SELECT review_type FROM "Learner".reviews
                          WHERE learner_id=%s AND aptem_review_id=%s LIMIT 2''',
                       [record.learner_id, aptem_id])
        rows = cursor.fetchall()
    if len(rows) != 1:
        raise MeetingOwnershipError('MEETING_OWNERSHIP_SOURCE_CONFLICT')
    return review_family(rows[0][0]) in SUPPORTED_FAMILIES


def require_cancel_owner(record):
    owner = (ImportedMeetingOwnership.objects.select_for_update()
             .filter(calendar_event_id=record.pk).first())
    if (not owner or owner.state != ImportedMeetingOwnership.STATE_ACTIVE
            or owner.learner_id != record.learner_id
            or owner.review_event_key != record.event_key
            or owner.owner_email.casefold() != record.owner_email.casefold()
            or owner.graph_event_id != record.graph_event_id):
        raise MeetingOwnershipError('MEETING_OWNERSHIP_UNVERIFIED')
    return owner


def retire_after_cancel(record):
    owner = require_cancel_owner(record)
    owner.state = ImportedMeetingOwnership.STATE_CANCELLED
    owner.save(update_fields=['state'])


