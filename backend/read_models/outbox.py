from __future__ import annotations

import logging

from django.conf import settings
from django.db import DatabaseError, transaction

from .models import ReadModelEvent


logger = logging.getLogger(__name__)


def enqueue_read_model_event(
    *,
    event_type: str,
    model_key: str,
    scope_type: str,
    scope_id: str,
    payload: dict | None = None,
    using: str = "default",
) -> bool:
    """Append inside the caller's database transaction, failing open if absent."""
    if not getattr(settings, "READ_MODEL_OUTBOX_ENABLED", False):
        return False
    if not all(str(value or "").strip() for value in (event_type, model_key, scope_type, scope_id)):
        return False
    try:
        # A nested savepoint prevents a missing additive table from poisoning a
        # surrounding domain transaction during a staged deployment.
        with transaction.atomic(using=using):
            ReadModelEvent.objects.using(using).create(
                event_type=str(event_type)[:120],
                model_key=str(model_key)[:120],
                scope_type=str(scope_type)[:60],
                scope_id=str(scope_id)[:255],
                payload=payload or {},
            )
    except DatabaseError as exc:
        logger.warning("read_model_outbox_unavailable model=%s error=%s", model_key, exc)
        return False
    return True

