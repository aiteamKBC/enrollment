"""Short-lived, identity-scoped cache for the Coach Dashboard response."""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

from .auth import normalize_email
from django.utils import timezone


logger = logging.getLogger(__name__)
# v4 carries the API-owned OTJH target-to-date/RAG fields.  A namespace bump
# prevents an older browser/API payload from being served without them.
CACHE_NAMESPACE = "coach-dashboard-summary:v5"
SECTION_NAMES = ("summary", "learners", "meetings", "risk")


def dashboard_section_cache_key(coach_identity: str, section: str) -> str:
    if section not in SECTION_NAMES:
        raise ValueError("Unknown Dashboard section.")
    version = "v3" if section == "summary" else "v2"
    return f"{coach_dashboard_cache_key(coach_identity)}:sections:{version}:{section}"


def get_cached_dashboard_section(coach_identity: str, section: str):
    try:
        return cache.get(dashboard_section_cache_key(coach_identity, section))
    except Exception:
        logger.warning("coach_dashboard_section_cache_read_failed", exc_info=True)
        return None


def cache_dashboard_section(coach_identity: str, section: str, payload: dict) -> None:
    try:
        cache.set(dashboard_section_cache_key(coach_identity, section), payload, timeout=coach_dashboard_cache_ttl())
    except Exception:
        logger.warning("coach_dashboard_section_cache_write_failed", exc_info=True)


def _invalidate_sections(coach_identity: str) -> None:
    cache.delete_many([dashboard_section_cache_key(coach_identity, section) for section in SECTION_NAMES])


def coach_dashboard_cache_key(coach_identity: str) -> str:
    """Return a non-sensitive key for the already-authorised effective coach."""
    canonical = normalize_email(coach_identity)
    if not canonical:
        raise ValueError("A canonical coach identity is required for dashboard caching.")
    identity_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    # A new business day must never read yesterday's derived Dashboard data.
    return f"{CACHE_NAMESPACE}:{identity_hash}:asof:{timezone.localdate().isoformat()}"


def coach_dashboard_cache_ttl() -> int:
    return max(int(getattr(settings, "COACH_DASHBOARD_CACHE_TTL", 30)), 1)


def get_cached_coach_dashboard(coach_identity: str):
    key = coach_dashboard_cache_key(coach_identity)
    try:
        payload = cache.get(key)
    except Exception:  # noqa: BLE001 - cache availability must not gate reads
        logger.warning(
            "coach_dashboard_cache status=READ_FAILED namespace=%s",
            CACHE_NAMESPACE,
            exc_info=True,
        )
        return None
    logger.info(
        "coach_dashboard_cache status=%s namespace=%s",
        "HIT" if payload is not None else "MISS",
        CACHE_NAMESPACE,
    )
    return payload


def cache_coach_dashboard(coach_identity: str, payload: dict) -> None:
    try:
        _invalidate_sections(coach_identity)
        cache.set(
            coach_dashboard_cache_key(coach_identity),
            payload,
            timeout=coach_dashboard_cache_ttl(),
        )
    except Exception:  # noqa: BLE001 - a computed response remains valid
        logger.warning(
            "coach_dashboard_cache status=WRITE_FAILED namespace=%s",
            CACHE_NAMESPACE,
            exc_info=True,
        )


def invalidate_coach_dashboard_cache(coach_identity: str) -> None:
    """Invalidate the final response cache for one effective coach only."""
    try:
        _invalidate_sections(coach_identity)
        cache.delete(coach_dashboard_cache_key(coach_identity))
    except Exception:  # noqa: BLE001 - never turn a successful write into 500
        logger.warning(
            "coach_dashboard_cache status=INVALIDATE_FAILED namespace=%s",
            CACHE_NAMESPACE,
            exc_info=True,
        )
        return
    logger.info("coach_dashboard_cache status=INVALIDATED namespace=%s", CACHE_NAMESPACE)
