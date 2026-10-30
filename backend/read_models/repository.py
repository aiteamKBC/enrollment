from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging
import time

from django.core.cache import cache
from django.db import DatabaseError
from django.utils import timezone

from .models import ReadModel


logger = logging.getLogger(__name__)
CACHE_NAMESPACE = "shared-read-model:v1"
_CACHE_FAILURE_COOLDOWN_SECONDS = 5.0
_cache_unavailable_until = 0.0


def _cache_available() -> bool:
    return time.monotonic() >= _cache_unavailable_until


def _mark_cache_unavailable() -> None:
    global _cache_unavailable_until
    _cache_unavailable_until = time.monotonic() + _CACHE_FAILURE_COOLDOWN_SECONDS


@dataclass(frozen=True)
class ReadModelValue:
    payload: dict
    schema_version: int
    refreshed_at: object
    expires_at: object

    @property
    def stale(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())


def cache_key(model_key: str, scope_type: str, scope_id: str, schema_version: int) -> str:
    raw = "\x1f".join((model_key, scope_type, scope_id, str(schema_version)))
    return f"{CACHE_NAMESPACE}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def get_read_model(
    model_key: str,
    scope_type: str,
    scope_id: str,
    *,
    schema_version: int,
    cache_ttl: int,
    using: str = "default",
) -> ReadModelValue | None:
    key = cache_key(model_key, scope_type, scope_id, schema_version)
    cached = None
    if _cache_available():
        try:
            cached = cache.get(key)
        except Exception as exc:
            _mark_cache_unavailable()
            logger.warning("read_model_cache_unavailable operation=get model=%s error=%s", model_key, exc)
    if isinstance(cached, dict) and cached.get("schema_version") == schema_version:
        return ReadModelValue(
            payload=dict(cached.get("payload") or {}),
            schema_version=schema_version,
            refreshed_at=cached.get("refreshed_at"),
            expires_at=cached.get("expires_at"),
        )

    try:
        row = (
            ReadModel.objects.using(using)
            .filter(
                model_key=model_key,
                scope_type=scope_type,
                scope_id=scope_id,
                schema_version=schema_version,
                status=ReadModel.STATUS_READY,
            )
            .only("payload", "schema_version", "refreshed_at", "expires_at")
            .first()
        )
    except DatabaseError as exc:
        # Deploying code before its additive migration must retain the legacy
        # path. The feature flag can be enabled only after this table exists.
        logger.warning("read_model_store_unavailable operation=get model=%s error=%s", model_key, exc)
        return None
    if row is None:
        return None

    envelope = {
        "payload": dict(row.payload or {}),
        "schema_version": int(row.schema_version),
        "refreshed_at": row.refreshed_at,
        "expires_at": row.expires_at,
    }
    if _cache_available():
        try:
            cache.set(key, envelope, timeout=max(int(cache_ttl), 1))
        except Exception as exc:
            _mark_cache_unavailable()
            logger.warning("read_model_cache_unavailable operation=set model=%s error=%s", model_key, exc)
    return ReadModelValue(**envelope)


def put_read_model(
    model_key: str,
    scope_type: str,
    scope_id: str,
    payload: dict,
    *,
    schema_version: int,
    ttl_seconds: int,
    source_revision: dict | None = None,
    using: str = "default",
) -> ReadModelValue | None:
    if not isinstance(payload, dict):
        raise ValueError("A shared read-model payload must be a JSON object.")
    expires_at = timezone.now() + timedelta(seconds=max(int(ttl_seconds), 1))
    try:
        row, _created = ReadModel.objects.using(using).update_or_create(
            model_key=model_key,
            scope_type=scope_type,
            scope_id=scope_id,
            defaults={
                "payload": payload,
                "schema_version": schema_version,
                "source_revision": source_revision or {},
                "status": ReadModel.STATUS_READY,
                "expires_at": expires_at,
            },
        )
    except DatabaseError as exc:
        logger.warning("read_model_store_unavailable operation=put model=%s error=%s", model_key, exc)
        return None

    value = ReadModelValue(
        payload=dict(payload),
        schema_version=int(schema_version),
        refreshed_at=row.refreshed_at,
        expires_at=row.expires_at,
    )
    if _cache_available():
        try:
            cache.set(
                cache_key(model_key, scope_type, scope_id, schema_version),
                {
                    "payload": value.payload,
                    "schema_version": value.schema_version,
                    "refreshed_at": value.refreshed_at,
                    "expires_at": value.expires_at,
                },
                timeout=max(int(ttl_seconds), 1),
            )
        except Exception as exc:
            _mark_cache_unavailable()
            logger.warning("read_model_cache_unavailable operation=set model=%s error=%s", model_key, exc)
    return value


def invalidate_read_model_cache(
    model_key: str,
    scope_type: str,
    scope_id: str,
    *,
    schema_version: int,
) -> None:
    if _cache_available():
        try:
            cache.delete(cache_key(model_key, scope_type, scope_id, schema_version))
        except Exception as exc:
            _mark_cache_unavailable()
            logger.warning("read_model_cache_unavailable operation=delete model=%s error=%s", model_key, exc)
