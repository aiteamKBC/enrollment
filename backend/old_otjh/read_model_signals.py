"""Domain invalidation events for the global Record Monitor projection."""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from learner_api.models import EnrolmentUser

from .read_model import enqueue_record_monitor_refresh


@receiver(
    [post_save, post_delete],
    sender=EnrolmentUser,
    dispatch_uid='record_monitor_enrolment_changed',
)
def enrolment_changed(sender, instance, using, **kwargs):
    enqueue_record_monitor_refresh(reason='enrolment-link', using=using)
