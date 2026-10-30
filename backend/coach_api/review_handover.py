"""APTEM responsibility transfer. No Graph operations or historical rewrites."""
from django.db import connections, transaction

from learner_api.models import EnrolmentUser, LearnerProfile, StaffUser
from .models import ImportedReviewInstance, MigratedReviewDocument
from .migrated_reviews import review_family
from .review_sources import ReviewIdentityConflict, booking_for_imported_review, source_for_learner


FAMILIES = {'MCM', 'PR', 'PR_SKILLS_RADAR', 'PSP'}
UNFINISHED = {'not-scheduled', 'scheduled', 'in-progress', 'awaiting-signature'}


def shared_assignment_database():
    """Use ONE transaction only when both aliases explicitly name the same target.

    Separate SQLite :memory: connections are never equivalent. Different hosts,
    roles or connection options require deployment verification, not guessing.
    No connection is opened and no configuration values are returned or logged.
    """
    primary, enrolment = (connections[key].settings_dict for key in ('default', 'enrolment'))
    keys = ('ENGINE', 'HOST', 'PORT', 'NAME', 'USER', 'OPTIONS')
    return bool(primary.get('ENGINE') == 'django.db.backends.postgresql'
                and primary.get('HOST') and primary.get('NAME')
                and all(primary.get(key) == enrolment.get(key) for key in keys))


def source_reviews(profile_id):
    from learner_api.review_history import _serialize_review
    with connections['default'].cursor() as cursor:
        cursor.execute('''SELECT id, learner_id, aptem_review_id, review_type, status, completed_date, review_data
                          FROM "Learner".reviews WHERE learner_id = %s ORDER BY id FOR SHARE''', [profile_id])
        result = []
        for row in cursor.fetchall():
            raw = dict(zip(('id', 'learner_id', 'aptem_review_id', 'review_type', 'status', 'completed_date', 'review_data'), row))
            result.append({**_serialize_review(raw, {}), 'id': raw['id'], 'learner_id': raw['learner_id']})
        return result


def completed(source, overlay=None):
    return bool(str(source.get('status') or '').strip().casefold() == 'completed'
                or source.get('completedDate') or (overlay and
                (overlay.status == 'completed' or overlay.completed_at)))


def transfer_locked(overlay, source, booking, new_owner):
    """Caller owns learner/calendar/overlay locks. Return a safe outcome code."""
    if completed(source, overlay):
        return 'historical'
    if booking is not None:
        # A local owner change is NOT a Teams organizer/invitation handover.
        # Includes conducted meetings, active sync attempts and reconciled rows.
        return 'booking_handover_required'
    if overlay is None:
        return 'uninitialized'  # Current assignment supplies responsibility; never initialize here.
    if (overlay.source_review_id != source['id'] or overlay.learner_id != source['learner_id']
            or overlay.event_key != f"imported-review:{source['aptemReviewId']}"):
        return 'source_association_conflict'
    if overlay.status not in UNFINISHED:
        return 'lifecycle_handover_required'
    if not isinstance(overlay.template_snapshot, dict) or not overlay.template_snapshot.get('sections'):
        return 'uninitialized_overlay'
    frozen_family = (overlay.template_snapshot.get('templateSource') or {}).get('family')
    if frozen_family and frozen_family != review_family(source['type']):
        return 'source_association_conflict'
    # Existing signatures identify their real signer independently of the owner.
    # Never replace a signer, role, submitted answer or frozen PDF on handover.
    roles = list(overlay.migrated_signatures.values_list('role', flat=True))
    if (len(set(roles)) != len(roles) or set(roles) - {'advisor', 'participant', 'employer'}
            or (roles and overlay.status != 'awaiting-signature')
            or MigratedReviewDocument.objects.filter(overlay=overlay).exists()):
        return 'signed_snapshot_handover_required'
    if overlay.owner_email.strip().casefold() == new_owner:
        return 'unchanged'
    overlay.owner_email = new_owner
    overlay.save(update_fields=['owner_email', 'updated_at'])
    return 'transferred'


def save_assignment(user, profile, fields, profile_fields):
    """Return None for Native; otherwise an explicit partial/full handover result.

    The caller saves contact-only changes if ``saved`` is False. No Review is
    touched in that case. Cross-database transactions are not simulated.
    """
    identity = source_for_learner(user, profile)
    if identity.kind == 'curriculum':
        return None
    result = {'transferred': [], 'unchanged': [], 'historical': [], 'uninitialized': [], 'blocked': []}
    def blocked(code):
        result['blocked'].append({'code': code})
        return False, result
    if identity.kind == 'conflict' or (profile and profile.enrolment_id != user.pk):
        raise ReviewIdentityConflict('The APTEM learner association needs verification before reassignment.')
    if identity.kind != 'aptem' or not profile:
        return blocked('source_identity_handover_required')
    new_owner = str(fields.get('coach_email') or '').strip().casefold()
    if not new_owner:
        return blocked('assigned_coach_required')
    if not shared_assignment_database():
        return blocked('shared_database_verification_required')
    from .views import lock_learner_calendar
    with transaction.atomic(using='default'):
        lock_learner_calendar(profile.pk)
        stored_user = EnrolmentUser.all_learners.using('default').select_for_update().get(pk=user.pk)
        stored_profile = LearnerProfile.objects.using('default').select_for_update().get(pk=profile.pk)
        if (stored_profile.enrolment_id != stored_user.pk
                or source_for_learner(stored_user, stored_profile).kind != 'aptem'
                or source_for_learner(stored_user, stored_profile).aptem_id != identity.aptem_id):
            raise ReviewIdentityConflict('The learner identity changed. Reload before reassigning the Coach.')
        staff = list(StaffUser.objects.using('default').filter(email__iexact=new_owner).only('username', 'email')[:2])
        if len(staff) != 1:
            return blocked('assigned_coach_identity_unresolved')
        rows = source_reviews(profile.pk)
        overlays = list(ImportedReviewInstance.objects.filter(learner_id=profile.pk).order_by('pk'))
        for source in rows:
            key = f"imported-review:{source['aptemReviewId']}"
            matches = [row for row in overlays if row.source_review_id == source['id']]
            if (not source['aptemReviewId'] or len(matches) > 1
                    or (source.get('aptemLearnerId') and source['aptemLearnerId'] != str(identity.aptem_id))
                    or sum(row['aptemReviewId'] == source['aptemReviewId'] for row in rows) != 1):
                result['blocked'].append({'reviewId': key, 'code': 'source_association_conflict'})
                continue
            overlay = matches[0] if matches else None
            if completed(source, overlay):
                result['historical'].append(key)
                continue
            if review_family(source['type']) not in FAMILIES:
                result['blocked'].append({'reviewId': key, 'code': 'unsupported_review_family'})
                continue
            try:
                booking = booking_for_imported_review(stored_profile, source, overlay, lock=True)
            except ReviewIdentityConflict:
                result['blocked'].append({'reviewId': key, 'code': 'booking_association_conflict'})
                continue
            if overlay:
                overlay = ImportedReviewInstance.objects.select_for_update().get(pk=overlay.pk)
            code = transfer_locked(overlay, source, booking, new_owner)
            if code in result and code != 'blocked':
                result[code].append(key)
            else:
                result['blocked'].append({'reviewId': key, 'code': code})
        for overlay in overlays:
            if overlay.source_review_id not in {row['id'] for row in rows}:
                result['blocked'].append({'reviewId': overlay.event_key, 'code': 'source_association_conflict'})
        for name, value in fields.items():
            setattr(stored_user, name, value)
        stored_user.save(using='default', update_fields=list(fields))
        for name, value in profile_fields.items():
            setattr(stored_profile, name, value)
        stored_profile.save(using='default', update_fields=[*profile_fields, 'updated_at'])
    return True, result
