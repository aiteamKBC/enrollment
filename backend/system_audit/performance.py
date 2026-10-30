"""Privacy-limited navigation diagnostics for explicitly allowlisted accounts."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
import json
import math

from django.conf import settings
from django.db import DatabaseError
from django.http import JsonResponse
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from learner_api.constants import ACCESS_SUPER_ADMIN
from login.permissions import require_access
from read_models.models import PerformanceSample

from . import pages


MAX_REQUESTS = 100
MAX_SAMPLE_MS = 120_000
API_PREFIXES = (
    "/curriculum_api/", "/coach_api/", "/learner_api/", "/enrolment_api/",
    "/quiz_api/", "/engagement_api/", "/audit_api/", "/manual_audit_api/",
    "/hours_test_api/", "/progress_reviews_api/", "/login_api/", "/api/",
)
WORKSPACE_PRIORITY = {
    name: index
    for index, name in enumerate((
        "coach", "learner", "curriculum", "admin", "enrolment",
        "tutor", "employer", "record-monitor",
    ))
}


def diagnostic_account_allowed(account) -> bool:
    if account is None or not getattr(settings, "PERFORMANCE_DIAGNOSTICS", False):
        return False
    configured = getattr(settings, "PERFORMANCE_DIAGNOSTIC_ACCOUNT_IDS", frozenset())
    return str(getattr(account, "pk", "")) in configured


def _number(value, *, minimum=0.0, maximum=MAX_SAMPLE_MS):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        return None
    return round(parsed, 2)


def _integer(value, *, minimum=0, maximum=1_000_000):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _clean_request(raw):
    if not isinstance(raw, dict):
        return None
    method = str(raw.get("method") or "GET").upper()
    if method not in {"GET", "HEAD"}:
        return None
    path = str(raw.get("path") or "").split("?", 1)[0].split("#", 1)[0]
    if not path.startswith(API_PREFIXES) or len(path) > 300:
        return None
    try:
        match = resolve(path)
    except Resolver404:
        return None
    # Store Django's route pattern, never concrete learner/account/entity ids.
    path = "/" + str(match.route).lstrip("/")
    duration_ms = _number(raw.get("durationMs"))
    status = _integer(raw.get("status"), maximum=599)
    if duration_ms is None or status is None:
        return None
    detail = {
        "method": method,
        "path": path,
        "status": status,
        "durationMs": duration_ms,
    }
    request_id = str(raw.get("requestId") or "")[:64]
    if request_id:
        detail["requestId"] = request_id
    query_count = _integer(raw.get("queryCount"))
    db_ms = _number(raw.get("dbMs"))
    server_ms = _number(raw.get("serverMs"))
    if query_count is not None:
        detail["queryCount"] = query_count
    if db_ms is not None:
        detail["dbMs"] = db_ms
    if server_ms is not None:
        detail["serverMs"] = server_ms
    cache_status = str(raw.get("cacheStatus") or "").upper()
    if cache_status in {"HIT", "MISS"}:
        detail["cacheStatus"] = cache_status
    return detail


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil((percentile / 100) * len(ordered)) - 1)
    return round(float(ordered[index]), 2)


def purge_expired_performance_samples() -> int:
    retention_days = max(int(getattr(settings, "PERFORMANCE_SAMPLE_RETENTION_DAYS", 14)), 1)
    cutoff = timezone.now() - timedelta(days=retention_days)
    try:
        deleted, _ = PerformanceSample.objects.filter(created_at__lt=cutoff).delete()
    except DatabaseError:
        return 0
    return deleted


@csrf_exempt
@require_POST
@never_cache
def performance_record(request):
    account = getattr(request, "login_account", None)
    if not diagnostic_account_allowed(account):
        return JsonResponse({"error": "Performance diagnostics are not enabled for this account."}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "Body must be JSON."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Body must be a JSON object."}, status=400)

    page = pages.resolve(payload.get("path"))
    # Only the server-owned route catalogue is eligible. Unknown paths can
    # contain arbitrary identifiers, and diagnostic storage never needs them.
    if not page["known"]:
        return JsonResponse({"recorded": False})
    page_ready_ms = _number(payload.get("pageReadyMs"))
    if page_ready_ms is None:
        return JsonResponse({"error": "pageReadyMs is invalid."}, status=400)

    requests = []
    for raw in (payload.get("requests") or [])[:MAX_REQUESTS]:
        cleaned = _clean_request(raw)
        if cleaned:
            requests.append(cleaned)
    signatures = Counter(f"{item['method']} {item['path']}" for item in requests)
    duplicate_count = sum(count - 1 for count in signatures.values() if count > 1)
    failed_count = sum(1 for item in requests if item["status"] == 0 or item["status"] >= 400)
    durations = [item["durationMs"] for item in requests]
    db_values = [item["dbMs"] for item in requests if "dbMs" in item]
    query_values = [item["queryCount"] for item in requests if "queryCount" in item]

    try:
        PerformanceSample.objects.create(
            run_id=str(payload.get("runId") or "")[:64],
            subject_type=str(getattr(account, "subject_type", "") or "")[:24],
            role=str(getattr(account, "role", "") or "")[:32],
            workspace=page["workspace"][:48],
            route_path=page["routePattern"][:300],
            page_key=page["pageKey"][:120],
            page_label=page["pageLabel"][:160],
            cold_navigation=bool(payload.get("cold")),
            page_ready_ms=page_ready_ms,
            request_count=len(requests),
            duplicate_get_count=duplicate_count,
            failed_request_count=failed_count,
            total_api_ms=round(sum(durations), 2),
            max_api_ms=round(max(durations, default=0), 2),
            total_db_ms=round(sum(db_values), 2) if db_values else None,
            total_query_count=sum(query_values) if query_values else None,
            request_details=requests,
        )
    except DatabaseError:
        return JsonResponse({"recorded": False, "available": False, "reason": "table-missing"})
    purge_expired_performance_samples()
    return JsonResponse({"recorded": True, "available": True})


@require_access(ACCESS_SUPER_ADMIN)
@require_GET
@never_cache
def performance_report(request):
    days = _integer(request.GET.get("days") or 14, minimum=1, maximum=14) or 14
    queryset = PerformanceSample.objects.filter(created_at__gte=timezone.now() - timedelta(days=days))
    workspace = str(request.GET.get("workspace") or "")[:48]
    if workspace:
        queryset = queryset.filter(workspace=workspace)
    rows = list(queryset.order_by("-created_at")[:50_000])
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.workspace, row.route_path, row.page_label)].append(row)

    report = []
    ordered_groups = sorted(
        grouped.items(),
        key=lambda item: (
            WORKSPACE_PRIORITY.get(item[0][0], len(WORKSPACE_PRIORITY)),
            item[0][1],
        ),
    )
    for (workspace_name, route_path, page_label), samples in ordered_groups:
        ready = [row.page_ready_ms for row in samples if not row.cold_navigation]
        api_hits = [
            detail["durationMs"]
            for row in samples
            for detail in row.request_details
            if detail.get("status", 500) < 400
        ]
        cacheable_api = [
            detail
            for row in samples
            for detail in row.request_details
            if detail.get("cacheStatus") in {"HIT", "MISS"}
        ]
        cache_hit_api = [
            detail["durationMs"]
            for detail in cacheable_api
            if detail.get("cacheStatus") == "HIT" and detail.get("status", 500) < 400
        ]
        p95_ready = _percentile(ready, 95)
        p95_api = _percentile(api_hits, 95)
        p95_cache_hit_api = _percentile(cache_hit_api, 95)
        api_budget_passed = (
            p95_cache_hit_api is not None and p95_cache_hit_api <= 500
            if cacheable_api
            else p95_api is None or p95_api <= 500
        )
        max_requests = max((row.request_count for row in samples), default=0)
        failed = sum(row.failed_request_count for row in samples)
        report.append({
            "workspace": workspace_name,
            "path": route_path,
            "pageLabel": page_label,
            "samples": len(samples),
            "warmSamples": len(ready),
            "pageReadyP50Ms": _percentile(ready, 50),
            "pageReadyP95Ms": p95_ready,
            "apiP95Ms": p95_api,
            "cacheHitApiP95Ms": p95_cache_hit_api,
            "cacheHitRequests": len(cache_hit_api),
            "maxInitialGets": max_requests,
            "duplicateGets": sum(row.duplicate_get_count for row in samples),
            "failedRequests": failed,
            "queryCountP95": _percentile(
                [row.total_query_count for row in samples if row.total_query_count is not None], 95,
            ),
            "budgetPassed": bool(
                ready and len(ready) >= 5
                and p95_ready is not None and p95_ready <= 2500
                and api_budget_passed
                and max_requests <= 8
                and failed == 0
            ),
        })
    return JsonResponse({
        "windowDays": days,
        "retentionDays": max(int(getattr(settings, "PERFORMANCE_SAMPLE_RETENTION_DAYS", 14)), 1),
        "performanceBudget": {"pageReadyP95Ms": 2500, "apiP95Ms": 500, "initialGets": 8},
        "pages": report,
    })
