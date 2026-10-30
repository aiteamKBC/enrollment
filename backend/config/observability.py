"""Dependency-free request correlation and structured production logging."""

from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone


request_id_context = contextvars.ContextVar("request_id", default="")
logger = logging.getLogger("observability")

SAFE_LOG_FIELDS = (
    "stage",
    "duration_ms",
    "query_count",
    "query_duration_ms",
    "event",
    "method",
    "path",
    "status_code",
    "latency_ms",
    "account_id",
    "subject_type",
    "coach_id",
    "operation_id",
    "graph_status",
    "attempt_count",
    # Curriculum write tracking. Ids, tables, counts and branch names only --
    # deliberately no group/cohort/staff *names*, so turning the trace on never
    # starts spilling people's names into the log stream.
    "entity_type",
    "entity_id",
    "parent_id",
    "action",
    "outcome",
    "table",
    "row_count",
    "matched_id",
    "reason",
    # Session lifecycle (login.sessions). Ids, a boolean and a duration -- no
    # token, no token hash, no email, no IP, so turning these into counters
    # never puts a credential or a person's identity in the log stream.
    "session_id",
    "remember",
    "ttl_seconds",
)


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        if not getattr(record, "request_id", ""):
            record.request_id = request_id_context.get()
        return True


class JsonLogFormatter(logging.Formatter):
    """Emit only an explicit safe-field allowlist in addition to the message."""

    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "") or request_id_context.get()
        if request_id:
            payload["request_id"] = request_id
        for field in SAFE_LOG_FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def metric_event(name: str, **fields):
    """Emit a structured event suitable for log-derived counters/latencies."""
    safe_fields = {key: value for key, value in fields.items() if key in SAFE_LOG_FIELDS}
    logger.info(name, extra={"event": name, **safe_fields})


class RequestObservabilityMiddleware:
    """Generate correlation IDs and emit one safe completion event per request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Inbound IDs are intentionally ignored: no trusted proxy contract exists.
        request.request_id = str(uuid.uuid4())
        token = request_id_context.set(request.request_id)
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            account = getattr(request, "login_account", None)
            coach = getattr(request, "coach_staff", None)
            metric_event(
                "http_request",
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
                account_id=str(getattr(account, "id", "") or ""),
                subject_type=str(getattr(account, "subject_type", "") or ""),
                coach_id=str(getattr(coach, "uuid", "") or getattr(coach, "id", "") or ""),
            )
            return response
        finally:
            request_id_context.reset(token)

