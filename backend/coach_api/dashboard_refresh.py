"""Compatibility surface for durable Coach Dashboard refresh requests."""

from __future__ import annotations

from datetime import datetime
from django.conf import settings
from django.utils import timezone

from login.security import normalize_email

from .read_model import enqueue_coach_dashboard_refresh


def snapshot_needs_refresh(payload: dict, *, now: datetime | None = None) -> bool:
    """Whether a served snapshot has crossed the stale-while-revalidate age."""
    read_model = payload.get("readModel") or {}
    version = read_model.get("version")
    if version is not None:
        try:
            from .services.dashboard.service import CoachDashboardService

            if int(version) != int(CoachDashboardService.SCHEMA_VERSION):
                return True
        except (TypeError, ValueError, ImportError):
            return True
    refreshed_at = read_model.get("refreshedAt")
    if not refreshed_at:
        # Only the persistent read model owns this timestamp. Other compatible
        # payloads (including tests and a just-computed legacy response) should
        # not accidentally start a refresh worker.
        return False
    try:
        refreshed = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        if timezone.is_naive(refreshed):
            refreshed = timezone.make_aware(refreshed)
    except (TypeError, ValueError):
        return True
    current = now or timezone.now()
    max_age = max(int(getattr(settings, "COACH_DASHBOARD_SNAPSHOT_MAX_AGE", 30)), 1)
    return (current - refreshed).total_seconds() >= max_age


def schedule_coach_dashboard_refresh(coach_identity: str, *, reason: str) -> bool:
    """Append a durable event; the supervised worker performs the rebuild."""
    if (
        not getattr(settings, "COACH_DASHBOARD_BACKGROUND_REFRESH_ENABLED", True)
        or getattr(settings, "COACH_TEST_MODE", False)
    ):
        return False
    canonical = normalize_email(coach_identity)
    if not canonical:
        return False
    return enqueue_coach_dashboard_refresh(canonical, reason=reason)


def _refresh_worker(coach_identity: str, reason: str) -> None:
    """Synchronous compatibility hook used by tests and manual repair tooling."""
    from . import dashboard_cache
    from .read_model import build_coach_dashboard
    from .services.dashboard.service import CoachDashboardService
    from read_models.repository import put_read_model

    canonical = normalize_email(coach_identity)
    payload = build_coach_dashboard(canonical)
    value = put_read_model(
        "coach.dashboard",
        "coach",
        canonical,
        payload,
        schema_version=CoachDashboardService.SCHEMA_VERSION,
        ttl_seconds=max(int(getattr(settings, "COACH_DASHBOARD_SNAPSHOT_MAX_AGE", 30)), 1),
    )
    response = dict(payload)
    if value is not None:
        response["readModel"] = {
            "version": value.schema_version,
            "refreshedAt": value.refreshed_at.isoformat(),
        }
    dashboard_cache.cache_coach_dashboard(canonical, response)
