"""Domain events for the Learner Home projection."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from coach_api.models import CoachAbsenceReport

from .models import (
    CommercialUser,
    EnrolmentUser,
    LearnerProfile,
    LearnerProgressEntry,
    LearnerTrainingPlanModule,
)
from .read_model import enqueue_learner_home_refresh


def _kind(value) -> str:
    return 'commercial' if str(value or '').strip().casefold() == 'commercial' else 'apprenticeship'


def _enqueue_enrolment(learner_id, *, learner_type=None, using: str, reason: str) -> None:
    if not learner_id:
        return
    if learner_type is None:
        learner_type = (
            EnrolmentUser.all_learners.using(using)
            .filter(pk=learner_id)
            .values_list('learner_type', flat=True)
            .first()
        )
    enqueue_learner_home_refresh(_kind(learner_type), learner_id, reason=reason, using=using)


def _profile_for(instance, using):
    state = getattr(instance, '_state', None)
    profile = state.fields_cache.get('learner') if state is not None else None
    if profile is not None:
        return profile
    learner_id = getattr(instance, 'learner_id', None)
    if not learner_id:
        return None
    return (
        LearnerProfile.objects.using(using)
        .filter(pk=learner_id)
        .only('enrolment_id', 'learner_type')
        .first()
    )


@receiver([post_save, post_delete], sender=EnrolmentUser, dispatch_uid='learner_home_enrolment_changed')
@receiver([post_save, post_delete], sender=CommercialUser, dispatch_uid='learner_home_commercial_changed')
def enrolment_changed(sender, instance, using, **kwargs):
    _enqueue_enrolment(
        instance.pk, learner_type=getattr(instance, 'learner_type', None),
        using=using, reason='enrolment',
    )


@receiver([post_save, post_delete], sender=LearnerProfile, dispatch_uid='learner_home_profile_changed')
def profile_changed(sender, instance, using, **kwargs):
    _enqueue_enrolment(
        instance.enrolment_id, learner_type=instance.learner_type,
        using=using, reason='learner-profile',
    )


@receiver([post_save, post_delete], sender=LearnerProgressEntry, dispatch_uid='learner_home_progress_changed')
@receiver([post_save, post_delete], sender=LearnerTrainingPlanModule, dispatch_uid='learner_home_plan_changed')
def learner_detail_changed(sender, instance, using, **kwargs):
    profile = _profile_for(instance, using)
    if profile is not None:
        _enqueue_enrolment(
            profile.enrolment_id, learner_type=profile.learner_type,
            using=using, reason='progress-or-plan',
        )


@receiver([post_save, post_delete], sender=CoachAbsenceReport, dispatch_uid='learner_home_absence_changed')
def learner_absence_changed(sender, instance, using, **kwargs):
    _enqueue_enrolment(instance.learner_id, using=using, reason='attendance-recovery')
