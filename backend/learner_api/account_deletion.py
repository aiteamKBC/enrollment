"""Delete a learner's linked identities on the shared enrolment database."""
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import Collector

from login.models import Invitation, LoginAccount, LoginAudit, LoginSession, PasswordReset

from .mappers import ValidationError
from .models import EnrolmentUser, LearnerKsb, LearnerProfile, learner_ksbs_relation_exists


class _ProfileCollector(Collector):
    """Cascade a profile delete, skipping the retired learner_ksbs snapshot.

    ``LearnerKsb`` still maps ``"Learner"."learner_ksbs"`` as a rollback
    fallback, but the table is absent from the current database. A plain
    ``profile.delete()`` cascades into it and fails with "relation does not
    exist", rolling back the whole account deletion. Where the table is
    present its rows are still removed as before.
    """

    def related_objects(self, related_model, related_fields, objs):
        if related_model is LearnerKsb and not learner_ksbs_relation_exists(self.using):
            return related_model._base_manager.using(self.using).none()
        return super().related_objects(related_model, related_fields, objs)


def _delete_profile(profile):
    collector = _ProfileCollector(using="enrolment", origin=profile)
    collector.collect([profile])
    collector.delete()


def _profiles_for_deletion(user):
    # Profile ids and enrolment ids use independent sequences. Never match pk.
    match = Q(enrolment_id=user.pk)
    if user.uuid:
        match |= Q(enrolment_id__isnull=True, uuid=user.uuid)
    profiles = list(LearnerProfile.objects.using("enrolment").select_for_update().filter(match))
    email = str(user.email or "").strip()
    if email:
        legacy = list(LearnerProfile.objects.using("enrolment").select_for_update().filter(
            enrolment_id__isnull=True, email__iexact=email,
        ).exclude(pk__in=[profile.pk for profile in profiles]))
        if legacy and EnrolmentUser.all_learners.using("enrolment").filter(
            email__iexact=email,
        ).exclude(pk=user.pk).exists():
            raise ValidationError("This email belongs to multiple enrolment records. Link the legacy learner profile to the correct enrolment record before deleting this account.")
        profiles.extend(legacy)
    return profiles


def delete_learner_account(user_id):
    """Remove linked identities atomically; a failure rolls back every deletion.

    Profile deletion also cascades through its ORM-owned learning plan, KSBs,
    progress entries and quiz answers. Shared curriculum records are preserved.
    """
    with transaction.atomic(using="enrolment"):
        user = EnrolmentUser.all_learners.using("enrolment").select_for_update().get(pk=user_id)
        profiles = _profiles_for_deletion(user)
        accounts = list(LoginAccount.objects.using("enrolment").select_for_update().filter(
            subject_type="learner", subject_id=user.pk,
        ))
        for account in accounts:
            for model in (LoginSession, Invitation, PasswordReset, LoginAudit):
                model.objects.using("enrolment").filter(account_id=account.pk).delete()
            account.delete(using="enrolment")
        for profile in profiles:
            _delete_profile(profile)
        user.delete(using="enrolment")
