from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class ReadModel(models.Model):
    """Latest successful projection for one reusable aggregate and scope."""

    STATUS_READY = "ready"
    STATUS_ERROR = "error"

    model_key = models.CharField(max_length=120)
    scope_type = models.CharField(max_length=60)
    scope_id = models.CharField(max_length=255)
    schema_version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField(default=dict)
    source_revision = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default=STATUS_READY)
    refreshed_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "platform_read_models"
        constraints = [
            models.UniqueConstraint(
                fields=["model_key", "scope_type", "scope_id"],
                name="platform_read_model_scope_uniq",
            ),
        ]


class ReadModelEvent(models.Model):
    """Transactional outbox event; history is pruned after successful handling."""

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_RETRY = "retry"
    STATUS_PROCESSED = "processed"
    STATUS_DEAD = "dead"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=120)
    model_key = models.CharField(max_length=120)
    scope_type = models.CharField(max_length=60)
    scope_id = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=120, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "platform_read_model_outbox"
        indexes = [
            models.Index(
                fields=["available_at", "created_at"],
                condition=models.Q(status__in=["pending", "retry"]),
                name="platform_outbox_ready_idx",
            ),
            models.Index(
                fields=["model_key", "scope_type", "scope_id", "status"],
                name="platform_outbox_scope_idx",
            ),
        ]


class PerformanceSample(models.Model):
    """One privacy-limited browser navigation sample for diagnostic accounts."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_id = models.CharField(max_length=64, blank=True)
    subject_type = models.CharField(max_length=24, blank=True)
    role = models.CharField(max_length=32, blank=True)
    workspace = models.CharField(max_length=48, blank=True)
    route_path = models.CharField(max_length=300)
    page_key = models.CharField(max_length=120, blank=True)
    page_label = models.CharField(max_length=160, blank=True)
    cold_navigation = models.BooleanField(default=False)
    page_ready_ms = models.FloatField()
    request_count = models.PositiveSmallIntegerField(default=0)
    duplicate_get_count = models.PositiveSmallIntegerField(default=0)
    failed_request_count = models.PositiveSmallIntegerField(default=0)
    total_api_ms = models.FloatField(default=0)
    max_api_ms = models.FloatField(default=0)
    total_db_ms = models.FloatField(null=True, blank=True)
    total_query_count = models.PositiveIntegerField(null=True, blank=True)
    request_details = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "platform_performance_samples"
        indexes = [
            models.Index(fields=["-created_at"], name="platform_perf_created_idx"),
            models.Index(
                fields=["workspace", "route_path", "-created_at"],
                name="platform_perf_route_idx",
            ),
        ]

