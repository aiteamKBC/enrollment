"""Safe diagnostics for Aptem summary generation; never retain provider text."""
import logging
import re

from .errors import request_id_for


logger = logging.getLogger(__name__)
MESSAGES = {
    "AI_SUMMARY_CONFIGURATION": "AI summary generation is not configured correctly. Contact support.",
    "AI_SUMMARY_ACCESS": "The AI service denied access. Contact support to check the service configuration.",
    "AI_SUMMARY_QUOTA": "The AI service has reached its usage or spending limit. Contact support.",
    "AI_SUMMARY_RATE_LIMIT": "The AI service is busy. Wait a moment and try again.",
    "AI_SUMMARY_TIMEOUT": "AI summary generation timed out. Try again.",
    "AI_SUMMARY_CONNECTION": "The AI service could not be reached. Try again shortly.",
    "AI_SUMMARY_SERVICE": "The AI service is temporarily unavailable. Try again shortly.",
    "AI_SUMMARY_INVALID_OUTPUT": "The AI service returned an incomplete or invalid summary. Try again.",
    "AI_SUMMARY_REQUEST": "The AI service could not accept the summary request. Contact support.",
    "AI_SUMMARY_FAILED": "The AI summary could not be generated. Try again or enter the answer manually.",
}
QUOTA_CODES = {
    "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded", "organization_usage_limit_exceeded", "usage_limit_exceeded",
}


def summary_generation_error(request, exc):
    """Return fixed public copy and log only bounded codes/correlation IDs."""
    code = "AI_SUMMARY_FAILED"
    status = getattr(exc, "status_code", None)
    status = status if type(status) is int and 400 <= status <= 599 else None
    provider_code = getattr(exc, "code", None)
    error_type = type(exc).__name__
    if error_type == "APITimeoutError":
        code = "AI_SUMMARY_TIMEOUT"
    elif error_type == "APIConnectionError":
        code = "AI_SUMMARY_CONNECTION"
    elif status in {401, 403}:
        code = "AI_SUMMARY_ACCESS"
    elif status == 429:
        code = "AI_SUMMARY_QUOTA" if isinstance(provider_code, str) and provider_code in QUOTA_CODES else "AI_SUMMARY_RATE_LIMIT"
    elif status and status >= 500:
        code = "AI_SUMMARY_SERVICE"
    elif status in {400, 404, 422}:
        code = "AI_SUMMARY_REQUEST"
    elif isinstance(exc, RuntimeError):
        # Match only known internal validation messages; never disclose arbitrary text.
        message = str(exc)
        if message in {
            "OPENAI_API_KEY is not configured.",
            "The OpenAI client library is not installed on the server.",
        }:
            code = "AI_SUMMARY_CONFIGURATION"
        elif message in {
            "The AI service returned an invalid meeting summary.",
            "The AI service returned a meeting summary without an overview.",
        } or message.startswith("The AI service returned an empty meeting summary ("):
            code = "AI_SUMMARY_INVALID_OUTPUT"
    request_id = request_id_for(request)
    provider_request_id = getattr(exc, "request_id", None)
    if not isinstance(provider_request_id, str) or not re.fullmatch(r"req_[A-Za-z0-9_-]{1,100}", provider_request_id):
        provider_request_id = ""
    logger.warning(
        "Aptem summary generation failed code=%s status=%s request_id=%s provider_request_id=%s",
        code, status, request_id, provider_request_id,
    )
    return {"code": code, "message": MESSAGES[code], "requestId": request_id}
