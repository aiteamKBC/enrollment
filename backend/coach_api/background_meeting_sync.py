"""Read-only source qualification for the existing Coach snapshot command."""
from django.db import connections

from . import reconciliation_links
from .models import ImportedReviewInstance
from .migrated_reviews import review_family, source_is_completed
from .migrated_session_scope import booking_window, refresh_basis
from .review_sources import ReviewIdentityConflict, booking_for_imported_review, meeting_profile


def imported_context(record):
    """None means Native/generic; uncertainty raises before Graph or a write.

    Calendar keys discover candidates only. The existing Coach source reader
    and booking resolver establish the source/profile/owner association.
    """
    from . import views
    from learner_api.models import LearnerProfile

    link = reconciliation_links.for_calendar(record)
    if record.review_instance_id and not link:
        if record.event_key.startswith('imported-review:'):
            raise ReviewIdentityConflict('Imported key has an unreconciled Native association.')
        return None  # Existing explicitly linked Native occurrence.
    key = link.overlay.event_key if link else record.event_key
    profile = None
    if not key.startswith('imported-review:'):
        if record.event_type not in {'mcr', 'progress-review', 'review'}:
            return None
        profile = meeting_profile(record)
        if profile is None:
            raise ReviewIdentityConflict('Meeting source cannot be verified.')
        aptem, conflicts = views.resolve_effective_aptem_ids([profile], strict=True)
        if conflicts:
            raise ReviewIdentityConflict('Meeting source identities conflict.')
        if profile.pk not in aptem:
            return None
        parts = (record.idempotency_key or '').split(':')
        if (len(parts) != 6 or parts[0] != 'learner-book'
                or parts[1] not in {'mcm', 'progress-review'} or not parts[5].isdigit()):
            raise ReviewIdentityConflict('Imported meeting has no qualified source review key.')
        # Resolve the internal legacy source ID, never an equal-number profile.
        with connections[views.get_learner_db_alias()].cursor() as cursor:
            cursor.execute('SELECT aptem_review_id FROM "Learner".reviews WHERE id = %s AND learner_id = %s',
                           [int(parts[5]), profile.pk])
            rows = cursor.fetchall()
        if len(rows) != 1 or not rows[0][0]:
            raise ReviewIdentityConflict('Imported source review cannot be resolved.')
        key = f'imported-review:{rows[0][0]}'

    owner = record.owner_email.strip().casefold()
    # pdf_only selects the existing minimal source/identity read. It does not
    # generate a PDF, initialize a form, or perform progress/AI calculations.
    definition = views._imported_review_definition(owner, key, pdf_only=True)
    if not definition:
        raise ReviewIdentityConflict('Imported review is unavailable to the owning Coach.')
    source, instance = definition['historicalReview'], definition['instance']
    family = review_family(source.get('type'))
    if family not in {'MCM', 'PR', 'PR_SKILLS_RADAR', 'PSP'}:
        raise ReviewIdentityConflict('Unsupported imported meeting family.')
    profile = profile or LearnerProfile.objects.filter(pk=instance['learnerId']).first()
    if not profile or profile.pk != instance['learnerId']:
        raise ReviewIdentityConflict('Imported meeting profile is inconsistent.')
    overlays = list(ImportedReviewInstance.objects.filter(source_review_id=int(source['id']))[:2])
    if len(overlays) > 1:
        raise ReviewIdentityConflict('Imported meeting has ambiguous continuations.')
    overlay = overlays[0] if overlays else None
    if overlay and (overlay.owner_email.casefold() != owner or overlay.learner_id != profile.pk
                    or overlay.event_key != instance['id']):
        raise ReviewIdentityConflict('Imported continuation ownership is inconsistent.')
    booking = booking_for_imported_review(profile, source, overlay)
    expected_type = 'mcr' if family == 'MCM' else 'progress-review'
    if (not booking or refresh_basis(booking) != refresh_basis(record)
            or record.event_type != expected_type or record.sync_state != 'synced'
            or record.status in {'not-scheduled', 'cancelled'}
            or not record.graph_event_id or not (record.meeting_link or record.graph_web_link)
            or not booking_window(record)[0]):
        raise ReviewIdentityConflict('Imported booking is inconsistent or not synchronized.')
    return {'reviewKey': instance['id'], 'family': family, 'profileId': profile.pk,
            'sourceReviewId': str(source['id']), 'overlayId': overlay.pk if overlay else None,
            'status': overlay.status if overlay else source.get('status'),
            'completed': source_is_completed(source) or bool(overlay and overlay.completed_at),
            'revision': (overlay.meeting_intelligence or {}).get('reviewRevision', 0) if overlay else 0}
