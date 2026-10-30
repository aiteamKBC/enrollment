"""Cache identity for Coach Caseload projections."""

import hashlib
import logging

from django.core.cache import cache

from coach_api.auth import normalize_email


# v14 carries the API-owned OTJH target-to-date/RAG fields.
CASELOAD_CACHE_VERSION = 14
logger = logging.getLogger(__name__)


def coach_caseload_cache_key(owner_email: str, *, summary_only: bool, query_string: str) -> str:
    query_scope = hashlib.sha256(query_string.encode()).hexdigest()[:16]
    return (
        f"coach-caseload:v{CASELOAD_CACHE_VERSION}:"
        f"{normalize_email(owner_email)}:{int(summary_only)}:{query_scope}"
    )


def coach_caseload_lock_key(cache_key: str) -> str:
    return f"{cache_key}:building"


def get_cached_caseload(cache_key: str):
    """Return a cached projection, treating an unavailable cache as a miss."""
    try:
        return cache.get(cache_key)
    except Exception:  # noqa: BLE001 - Redis is an optional accelerator
        logger.warning("coach_caseload_cache_read_failed", exc_info=True)
        return None


def cache_caseload(cache_key: str, payload: dict, timeout: int) -> None:
    try:
        cache.set(cache_key, payload, timeout)
    except Exception:  # noqa: BLE001 - the response was already computed
        logger.warning("coach_caseload_cache_write_failed", exc_info=True)


def acquire_caseload_lock(lock_key: str, timeout: int = 120) -> bool:
    """Fail open so Redis downtime cannot block the underlying read path."""
    try:
        return cache.add(lock_key, "1", timeout)
    except Exception:  # noqa: BLE001
        logger.warning("coach_caseload_cache_lock_failed", exc_info=True)
        return True


def release_caseload_lock(lock_key: str) -> None:
    try:
        cache.delete(lock_key)
    except Exception:  # noqa: BLE001
        logger.warning("coach_caseload_cache_unlock_failed", exc_info=True)
