"""Assertions for Inclusion staff and explicitly approved QA accounts."""
import re
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect, JsonResponse
from django.views.decorators.http import require_GET

from .permissions import _accesses_of
from .sessions import authenticate_request

INCLUSION_QA_EMAIL_EXCEPTIONS = frozenset({
    "emma.leavey@ofsted.gov.uk",
    "rowaneltash2@gmail.com",
})


@require_GET
def authorize(request):
    secret = settings.INCLUSION_SSO_SECRET
    callback = settings.INCLUSION_SSO_CALLBACK_URL
    if len(secret) < 32 or not callback:
        return JsonResponse({"error": "Inclusion sign-in is not configured."}, status=503)
    state = request.GET.get("state", "")
    if not re.fullmatch(r"[a-f0-9]{64}", state):
        return JsonResponse({"error": "Invalid login state."}, status=400)
    account = authenticate_request(request)
    if account is None or not account.is_active:
        return JsonResponse({"error": "Please sign in to the LMS again."}, status=401)
    accesses = _accesses_of(account)
    role = "qa" if "super-admin" in accesses else "coach" if "coach" in accesses else None
    # Use the authenticated account, never an email supplied in the query string.
    # This affects only the dashboard assertion; the LMS account stays a learner.
    if (account.email or "").strip().lower() in INCLUSION_QA_EMAIL_EXCEPTIONS:
        role = "qa"
    if not role:
        return JsonResponse({"error": "Inclusion is available to super admins and coaches only."}, status=403)
    assertion = signing.dumps({
        "aud": "inclusion-dashboard", "state": state, "account_id": account.pk,
        "email": account.email, "display_name": account.display_name or "", "role": role,
    }, key=secret, salt="kbc-inclusion-sso-v1")
    response = HttpResponseRedirect(callback + "#" + urlencode({"assertion": assertion}))
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
