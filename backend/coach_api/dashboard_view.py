"""HTTP boundary for the Coach Dashboard read model."""

import logging
import hashlib
from copy import deepcopy
from threading import Lock
from time import perf_counter

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from config.observability import metric_event

from .auth import authenticated_coach_email, coach_access_required
from . import dashboard_cache
from .dashboard_refresh import schedule_coach_dashboard_refresh, snapshot_needs_refresh
from .errors import coach_error
from .services.dashboard.service import CoachDashboardService
from .services.dashboard.sections import load_section, normalize_otjh_contract, paginate_learners, all_learners, summarize_meetings
from .validation import ObjectValidator, ValidationError, validation_error_response
from .services.dashboard.upcoming import next_work_week, upcoming_meetings
from .services.dashboard.timing import dashboard_stage


# Bound memory and serialize the one-time bootstrap for parallel section GETs.
# Existing snapshots (including older versions) never take this cold path.
_bootstrap_locks = [Lock() for _ in range(64)]


def _dashboard_perf(stage, started, *, learner_count=None):
    payload = {
        "event": "coach_perf",
        "endpoint": "dashboard",
        "stage": stage,
        "duration_ms": round((perf_counter() - started) * 1000, 2),
    }
    if learner_count is not None:
        payload["learner_count"] = learner_count
    metric_event("coach_perf", **payload)


@coach_access_required
@require_GET
def coach_dashboard(request):
    """Return the cached or persisted Dashboard read model for one effective coach."""
    endpoint_started = perf_counter()
    owner_email = authenticated_coach_email(request)
    cached_dashboard = dashboard_cache.get_cached_coach_dashboard(owner_email)
    if cached_dashboard is not None:
        try:
            cached_dashboard = CoachDashboardService(owner_email).normalize_start_dates(cached_dashboard, refresh_otjh=True)
            normalize_otjh_contract(cached_dashboard)
        except Exception:
            logging.getLogger(__name__).exception("coach_dashboard_start_date_read_failed")
            # Use the normal guarded loader/error response if source reads fail.
            cached_dashboard = None
    if cached_dashboard is not None:
        if snapshot_needs_refresh(cached_dashboard):
            schedule_coach_dashboard_refresh(owner_email, reason="stale-cache-hit")
        _dashboard_perf(
            "cache_hit", endpoint_started,
            learner_count=len(cached_dashboard.get("learners", [])),
        )
        response = JsonResponse(cached_dashboard)
        response["X-LMS-Cache"] = "HIT"
        return response

    try:
        response_payload = CoachDashboardService(
            owner_email, today=timezone.localdate(),
        ).build()
    except Exception:
        logging.getLogger(__name__).exception(
            "coach_dashboard_load_failed coach_account_id=%s", owner_email,
        )
        return coach_error(
            request,
            code="database_unavailable",
            message="Unable to load coach dashboard data.",
            status=503,
        )

    metric_event(
        "coach_dashboard_cache",
        status="MISS_COMPUTED",
        namespace=dashboard_cache.CACHE_NAMESPACE,
        duration_ms=round((perf_counter() - endpoint_started) * 1000, 2),
    )
    _dashboard_perf(
        "total", endpoint_started,
        learner_count=len(response_payload.get("learners", [])),
    )
    if snapshot_needs_refresh(response_payload):
        schedule_coach_dashboard_refresh(owner_email, reason="stale-snapshot-read")
    normalize_otjh_contract(response_payload)
    dashboard_cache.cache_coach_dashboard(owner_email, response_payload)
    response = JsonResponse(response_payload)
    response["X-LMS-Cache"] = "MISS"
    return response


@coach_access_required
@require_GET
def coach_dashboard_section(request, section):
    """Small Dashboard reads sharing the existing auth and invalidation boundary."""
    started = perf_counter()
    with dashboard_stage("owner_resolution"):
        owner_email = authenticated_coach_email(request)
    options = {}
    if section == "meetings":
        validator = ObjectValidator(request.GET)
        range_start = validator.iso_date("from")
        range_end = validator.iso_date("to")
        if (range_start is None) != (range_end is None):
            validator.error("range", "Supply both from and to.")
        if range_start and range_end and (range_end - range_start).days != 4:
            validator.error("range", "The Dashboard window must span five days.")
        if range_start and range_start.weekday() != 0:
            validator.error("from", "The Dashboard window must start on Monday.")
        try:
            validator.check()
        except ValidationError as exc:
            return validation_error_response(exc)
        # Query dates are compatibility hints only. A stale browser request
        # cannot pin the Dashboard to a previous week.
        range_start, range_end = next_work_week(timezone.localdate())
    if section == "learners" and any(key in request.GET for key in ("page", "page_size", "pageSize", "search", "cohort", "status", "otjh_status", "sort", "direction")):
        validator = ObjectValidator(request.GET)
        options = {
            "page": validator.integer("page", default=1, minimum=1),
            "page_size": min(validator.integer("page_size" if "page_size" in request.GET else "pageSize", default=15, minimum=1), 100),
            "search": validator.text("search", max_length=200),
            "cohort": validator.text("cohort", default="all", max_length=255),
            "status": validator.text("status", default="all", max_length=100),
            "otjh_status": validator.text("otjh_status", default="all", choices={"all", "at-risk", "need-attention", "on-track", "unavailable"}),
            "sort": validator.text("sort", default="risk", choices={"risk", "name", "otjh", "components", "attendance", "start-date", "activity", "progress-review", "monthly-coaching"}),
            "direction": validator.text("direction", default="desc", choices={"asc", "desc"}),
        }
        try:
            validator.check()
        except ValidationError as exc:
            return validation_error_response(exc)
    try:
        payload = dashboard_cache.get_cached_dashboard_section(owner_email, section)
        cache_status = "HIT" if payload is not None else "MISS"
        if payload is not None and section != "meetings":
            # Keep the same read-time Profile date/hidden-status protection as
            # the legacy endpoint, even while the durable snapshot is stale.
            payload = CoachDashboardService(owner_email).normalize_start_dates(payload, refresh_otjh=True)
            with dashboard_stage("otjh_enrichment"):
                normalize_otjh_contract(payload)
        if payload is None:
            payload = load_section(owner_email, section)
            if payload is None:
                index = int.from_bytes(hashlib.sha256(owner_email.encode()).digest()[:2], "big") % len(_bootstrap_locks)
                with _bootstrap_locks[index]:
                    payload = load_section(owner_email, section)
                    if payload is None:
                        # Preserve first-use behavior for coaches with no durable
                        # snapshot. Followers reuse that one persisted build.
                        CoachDashboardService(owner_email).refresh()
                        payload = load_section(owner_email, section)
                if payload is None:
                    raise RuntimeError("Dashboard snapshot was not persisted.")
            dashboard_cache.cache_dashboard_section(owner_email, section, payload)
        if snapshot_needs_refresh(payload):
            schedule_coach_dashboard_refresh(owner_email, reason=f"dashboard-{section}-read")
        with dashboard_stage("serialization"):
            response_payload = ((paginate_learners(payload, **options) if options else all_learners(payload)) if section == "learners" else
                                summarize_meetings(deepcopy(payload)) if section == "summary" else
                                upcoming_meetings(payload, range_start, range_end) if section == "meetings" else payload)
            response = JsonResponse(response_payload)
        response["X-LMS-Cache"] = cache_status
        _dashboard_perf(section, started, learner_count=len(payload.get("learners", [])))
        return response
    except Exception:
        logging.getLogger(__name__).exception("coach_dashboard_section_load_failed section=%s", section)
        return coach_error(request, code="database_unavailable", message=f"Unable to load coach dashboard {section}.", status=503)
