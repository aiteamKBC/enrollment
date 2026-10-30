"""Additive mobile communication tables; existing chat history stays untouched."""
import uuid
from django.db import models
from django.utils import timezone
from .policy import default_preferences

class Thread(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=240, unique=True)
    kind = models.CharField(max_length=12)
    cohort_id = models.CharField(max_length=64, blank=True)
    learner_a = models.BigIntegerField(null=True)
    learner_b = models.BigIntegerField(null=True)
    coach_id = models.BigIntegerField(null=True)
    updated_at = models.DateTimeField(default=timezone.now)

class Message(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.PROTECT, related_name="messages")
    account_id = models.BigIntegerField()
    sender_name = models.CharField(max_length=200)
    body = models.TextField(max_length=4000)
    client_id = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["account_id", "client_id"], name="mobile_comms_message_retry")]
        indexes = [models.Index(fields=["thread", "id"], name="mobile_comms_thread_message")]

class ReadCursor(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE)
    account_id = models.BigIntegerField()
    message_id = models.BigIntegerField(default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["thread", "account_id"], name="mobile_comms_read_cursor")]

class Preference(models.Model):
    account_id = models.BigIntegerField(primary_key=True)
    categories = models.JSONField(default=default_preferences)

class Device(models.Model):
    token = models.CharField(max_length=512, unique=True)
    account_id = models.BigIntegerField(db_index=True)
    session_id = models.CharField(max_length=100)
    platform = models.CharField(max_length=10, default="android")
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(default=timezone.now)

class Notification(models.Model):
    account_id = models.BigIntegerField(db_index=True)
    key = models.CharField(max_length=350)
    category = models.CharField(max_length=24)
    body = models.CharField(max_length=500)
    target = models.JSONField(default=dict)
    source = models.JSONField(default=dict)
    due_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["account_id", "key"], name="mobile_comms_notification_once")]
        indexes = [models.Index(fields=["account_id", "due_at"], name="mobile_comms_inbox_due")]

class Delivery(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE)
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    provider_id = models.CharField(max_length=200, blank=True)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "device"], name="mobile_comms_delivery_once")]
