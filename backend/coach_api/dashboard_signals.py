"""Domain events that keep the Coach Dashboard read model warm."""

from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from learner_api.models import LearnerProfile, LearnerProgressEntry
from login.security import normalize_email

from .dashboard_cache import invalidate_coach_dashboard_cache
from .read_model import enqueue_coach_dashboard_refresh
from .models import CoachAbsenceReport, CoachCalendarEvent


def _enqueue_after_commit(coach_identity: str, *, using: str, reason: str) -> None:
    canonical = normalize_email(coach_identity)
    if not canonical:
        return

    # The outbox insert happens now on the same alias. When the domain operation
    # owns an outer `transaction.atomic`, a rollback removes both rows. Older
    # autocommit write paths also have the post-success request hook and bounded
    # reconciliation as eventual-refresh safety nets. Redis invalidation waits
    # for commit so an uncommitted value is never exposed.
    enqueue_coach_dashboard_refresh(canonical, reason=reason, using=using)

    def invalidate():
        invalidate_coach_dashboard_cache(canonical)

    transaction.on_commit(invalidate, using=using)


@receiver(
    [post_save, post_delete],
    sender=LearnerProgressEntry,
    dispatch_uid="coach_dashboard_learner_progress_changed",
)
def learner_progress_changed(sender, instance, using, **kwargs):
    learner = getattr(instance, "_state", None)
    learner = learner.fields_cache.get("learner") if learner is not None else None
    coach_email = getattr(learner, "coach_email", "") if learner is not None else ""
    if not coach_email and getattr(instance, "learner_id", None):
        coach_email = (
            LearnerProfile.objects.using(using)
            .filter(pk=instance.learner_id)
            .values_list("coach_email", flat=True)
            .first()
        )
    _enqueue_after_commit(coach_email, using=using, reason="learner-progress")


@receiver(
    [post_save, post_delete],
    sender=LearnerProfile,
    dispatch_uid="coach_dashboard_learner_profile_changed",
)
def learner_profile_changed(sender, instance, using, **kwargs):
    _enqueue_after_commit(instance.coach_email, using=using, reason="learner-profile")


@receiver(
    [post_save, post_delete],
    sender=CoachCalendarEvent,
    dispatch_uid="coach_dashboard_calendar_changed",
)
def coach_calendar_changed(sender, instance, using, **kwargs):
    _enqueue_after_commit(instance.owner_email, using=using, reason="coach-calendar")


@receiver(
    [post_save, post_delete],
    sender=CoachAbsenceReport,
    dispatch_uid="coach_dashboard_absence_changed",
)
def coach_absence_changed(sender, instance, using, **kwargs):
    _enqueue_after_commit(instance.owner_email, using=using, reason="coach-absence")
