import base64
import json
import logging
from urllib.parse import urlsplit

from django.http import JsonResponse, StreamingHttpResponse
from django.test.client import RequestFactory
from django.urls import Resolver404, resolve
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from login.api_gate import refusal_for


ALLOWED_API_PREFIXES = (
    "/curriculum_api/",
    "/coach_api/",
    "/quiz_api/",
    "/learner_api/",
    "/audit_api/",
    "/hours_test_api/",
    "/engagement_api/",
    "/enrolment_api/",
    "/api/chat/",
    "/api/calendar/",
)
MAX_BATCH_REQUESTS = 40
BATCH_PATHS = {"/api/batch/", "/coach_api/_batch/"}
logger = logging.getLogger(__name__)


def _safe_api_path(raw_url):
    parsed = urlsplit(str(raw_url or ""))
    if parsed.scheme or parsed.netloc or not parsed.path.startswith(ALLOWED_API_PREFIXES):
        return None
    if parsed.path in BATCH_PATHS or ".." in parsed.path.split("/"):
        return None
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _subrequest(parent_request, url, headers):
    forwarded_headers = {}
    for name in ("accept", "content-type", "x-requested-with"):
        value = (headers or {}).get(name)
        if value:
            forwarded_headers[f"HTTP_{name.upper().replace('-', '_')}"] = value

    request = RequestFactory().get(url, **forwarded_headers)
    request.COOKIES = parent_request.COOKIES.copy()
    if hasattr(parent_request, "user"):
        request.user = parent_request.user
    if hasattr(parent_request, "session"):
        request.session = parent_request.session
    request._dont_enforce_csrf_checks = True
    return request


def _execute_get(parent_request, item):
    request_id = str(item.get("id") or "")
    safe_url = _safe_api_path(item.get("url"))
    if not request_id or not safe_url:
        return {"id": request_id, "status": 400, "body": "", "headers": {}}

    parsed = urlsplit(safe_url)

    # Apply the API gate to the sub-request. It has to be done here rather than
    # left to middleware: dispatch below calls the view function directly, so
    # nothing in MIDDLEWARE — the gate included — ever sees these. Without this,
    # posting a batch would read any gated prefix the caller's own role is
    # refused at the front door, which is a bypass of the whole role layer.
    #
    # Checked against the parent's already-resolved account, so it costs no
    # extra query, and a batch of 40 items costs 40 dictionary lookups.
    refusal = refusal_for(
        parsed.path,
        getattr(parent_request, "login_account", None),
        django_user_is_authenticated=bool(
            getattr(getattr(parent_request, "user", None), "is_authenticated", False)
        ),
    )
    if refusal is not None:
        return {
            "id": request_id,
            "status": refusal.status_code,
            "body": base64.b64encode(refusal.content).decode("ascii"),
            "headers": {"Content-Type": "application/json"},
        }

    try:
        match = resolve(parsed.path)
        response = match.func(
            _subrequest(parent_request, safe_url, item.get("headers")),
            *match.args,
            **match.kwargs,
        )
        if isinstance(response, StreamingHttpResponse):
            return {"id": request_id, "status": 409, "body": "", "headers": {}}
        selected_headers = {
            name: response[name]
            for name in ("Content-Type", "Cache-Control", "ETag", "Last-Modified")
            if response.has_header(name)
        }
        return {
            "id": request_id,
            "status": response.status_code,
            "body": base64.b64encode(response.content).decode("ascii"),
            "headers": selected_headers,
        }
    except Resolver404:
        return {"id": request_id, "status": 404, "body": "", "headers": {}}
    except Exception:  # Keep one broken section from failing the full page batch.
        logger.exception("Batched API subrequest failed path=%s", parsed.path)
        body = json.dumps({"error": "internal_error", "message": "Batched request failed."}).encode()
        return {
            "id": request_id,
            "status": 500,
            "body": base64.b64encode(body).decode("ascii"),
            "headers": {"Content-Type": "application/json"},
        }


@csrf_exempt
@require_POST
def api_get_batch(request):
    try:
        payload = json.loads(request.body or b"{}")
        requests = payload.get("requests")
    except (TypeError, ValueError, UnicodeDecodeError):
        requests = None

    if not isinstance(requests, list):
        return JsonResponse({"detail": "requests must be a list."}, status=400)
    if len(requests) > MAX_BATCH_REQUESTS:
        return JsonResponse({"detail": f"A batch can contain at most {MAX_BATCH_REQUESTS} requests."}, status=400)

    valid_requests = [item for item in requests if isinstance(item, dict)]
    if not valid_requests:
        return JsonResponse({"responses": []})

    # Keep this endpoint safe for already-open clients during the batching
    # rollback. Shared-hosting WSGI workers have strict thread/process limits;
    # running child requests serially avoids exhausting LiteSpeed/Passenger.
    responses = [_execute_get(request, item) for item in valid_requests]
    return JsonResponse({"responses": responses})
