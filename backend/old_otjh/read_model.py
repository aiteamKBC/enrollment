"""Shared projection for the read-only previous-record monitoring dashboard."""

from __future__ import annotations

from datetime import datetime, timezone

from django.conf import settings

from read_models.outbox import enqueue_read_model_event
from read_models.registry import ReadModelSpec, register
from read_models.repository import get_read_model


MODEL_KEY = 'record.monitor'
SCOPE_TYPE = 'record-monitor'
SCOPE_ID = 'global'
SCHEMA_VERSION = 1
EVENT_REFRESH_REQUESTED = 'record.monitor.refresh_requested'


def record_monitor_scope_ids(limit: int):
    return [SCOPE_ID] if int(limit) > 0 else []


def build_record_monitor(scope_id: str) -> dict:
    if str(scope_id or '') != SCOPE_ID:
        raise ValueError('Unknown Record Monitor scope.')
    from .monitoring import load_records
    return {
        'records': load_records(),
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }


def register_record_monitor_read_model() -> None:
    register(ReadModelSpec(
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        schema_version=SCHEMA_VERSION,
        ttl_seconds=max(
            int(getattr(settings, 'RECORD_MONITOR_READ_MODEL_TTL_SECONDS', 30)),
            1,
        ),
        builder=build_record_monitor,
        scope_source=record_monitor_scope_ids,
    ))


def get_record_monitor():
    return get_read_model(
        MODEL_KEY,
        SCOPE_TYPE,
        SCOPE_ID,
        schema_version=SCHEMA_VERSION,
        cache_ttl=max(
            int(getattr(settings, 'RECORD_MONITOR_READ_MODEL_TTL_SECONDS', 30)),
            1,
        ),
    )


def enqueue_record_monitor_refresh(
    *, reason: str, using: str = 'default',
) -> bool:
    return enqueue_read_model_event(
        event_type=EVENT_REFRESH_REQUESTED,
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        scope_id=SCOPE_ID,
        payload={'reason': str(reason or 'change')[:120]},
        using=using,
    )
