"""Issue a short-lived assertion for the Safeguarding application's login attempt."""
import re
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET

from .sessions import authenticate_request


@require_GET
def authorize(request):
    secret = getattr(settings, "SAFEGUARDING_SSO_SECRET", "")
    callback = getattr(settings, "SAFEGUARDING_SSO_CALLBACK_URL", "")
    if len(secret) < 32 or not callback:
        return JsonResponse({"error": "Safeguarding sign-in is not configured."}, status=503)
    state = request.GET.get("state", "")
    if not re.fullmatch(r"[a-f0-9]{64}", state):
        return JsonResponse({"error": "Invalid login state."}, status=400)
    account = authenticate_request(request)
    if account is None or not account.is_active:
        return JsonResponse({"error": "Please sign in to the LMS again."}, status=401)
    assertion = signing.dumps({
        "aud": "safeguarding", "state": state,
        "account_id": account.pk, "email": account.email,
        "display_name": account.display_name or "",
    }, key=secret, salt="kbc-safeguarding-sso-v1")
    # The destination is deployment configuration, never supplied by the browser.
    response = HttpResponseRedirect(callback + "#" + urlencode({"assertion": assertion}))
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
