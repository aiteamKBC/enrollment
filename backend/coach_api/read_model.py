"""Registration and event boundary for the shared Coach Dashboard projection."""

from __future__ import annotations

from django.conf import settings

from login.security import normalize_email
from read_models.outbox import enqueue_read_model_event
from read_models.registry import ReadModelSpec, register


MODEL_KEY = "coach.dashboard"
SCOPE_TYPE = "coach"
EVENT_REFRESH_REQUESTED = "coach.dashboard.refresh_requested"


def coach_dashboard_scope_ids(limit: int):
    """Bounded reconciliation source during the legacy-to-shared migration."""
    from coach_api import dashboard_service as compatibility

    return compatibility.CoachDashboardSnapshot.objects.order_by("refreshed_at").values_list(
        "owner_email", flat=True,
    )[:max(int(limit), 1)]


def build_coach_dashboard(owner_email: str) -> dict:
    """Build from sources and keep the legacy row current during migration."""
    from coach_api import dashboard_service as compatibility
    from coach_api.services.dashboard.service import CoachDashboardService

    service = CoachDashboardService(owner_email)
    payload = service.build_live()
    compatibility.CoachDashboardSnapshot.objects.update_or_create(
        owner_email=service.context.owner_email,
        defaults={"payload": payload, "schema_version": service.SCHEMA_VERSION},
    )
    return payload


def register_coach_dashboard_read_model() -> None:
    from coach_api.services.dashboard.service import CoachDashboardService

    register(ReadModelSpec(
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        schema_version=CoachDashboardService.SCHEMA_VERSION,
        ttl_seconds=max(int(getattr(settings, "COACH_DASHBOARD_SNAPSHOT_MAX_AGE", 30)), 1),
        builder=build_coach_dashboard,
        scope_source=coach_dashboard_scope_ids,
    ))


def enqueue_coach_dashboard_refresh(owner_email: str, *, reason: str, using: str = "default") -> bool:
    canonical = normalize_email(owner_email)
    if not canonical:
        return False
    return enqueue_read_model_event(
        event_type=EVENT_REFRESH_REQUESTED,
        model_key=MODEL_KEY,
        scope_type=SCOPE_TYPE,
        scope_id=canonical,
        payload={"reason": str(reason or "change")[:120]},
        using=using,
    )

