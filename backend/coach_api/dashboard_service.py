"""Compatibility exports for the relocated Dashboard service."""

from .services.dashboard.context import CoachDashboardContext
from .services.dashboard.service import CoachDashboardService, dashboard_marking_projection
from .models import CoachDashboardSnapshot

__all__ = [
    "CoachDashboardContext",
    "CoachDashboardService",
    "CoachDashboardSnapshot",
    "dashboard_marking_projection",
]
