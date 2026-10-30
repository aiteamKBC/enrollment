"""Safe, stable public errors for Coach API endpoints."""

from __future__ import annotations

import uuid

from django.http import JsonResponse


def request_id_for(request) -> str:
    request_id = str(getattr(request, "request_id", "") or "").strip()
    if not request_id:
        request_id = str(uuid.uuid4())
        request.request_id = request_id
    return request_id


def coach_error(request, *, code: str, message: str, status: int) -> JsonResponse:
    """Return public copy without exception, SQL, Graph, or infrastructure text."""
    return JsonResponse(
        {
            "error": code,
            "message": message,
            "request_id": request_id_for(request),
        },
        status=status,
    )

