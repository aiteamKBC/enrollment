"""UK postcode lookup for the public eligibility form.

    GET /enrolment_api/public/postcode-lookup/?postcode=CT1%201AA
        -> 200 {"found": true, "postcode": "CT1 1AA", "country": "England",
                "region": "South East", "district": "Canterbury", "inEngland": true}
        -> 200 {"found": false}                      no such postcode
        -> 400 not shaped like a UK postcode
        -> 429 too many checks from one address
        -> 503 the lookup service did not answer

The applicant's browser asks this endpoint as they fill in /apprenticeship_application,
and submit_application asks lookup() again for the postcodes it saves, so the
location stored with an application never comes from the client.

Postcodes are looked up in postcodes.io: open ONS Postcode Directory data, no
API key, nothing about the applicant sent but the postcode. Answers are cached
for a week; postcodes change far more slowly than that.

Like the application itself this needs no sign-in (see login/api_gate.py), so
the view checks the shape of the postcode before anything leaves the server
and limits how often one address may ask.
"""
import logging
import re
import time
from urllib.parse import quote

import requests
from django.core.cache import cache
from django.http import JsonResponse

from login.security import client_ip

logger = logging.getLogger(__name__)

SERVICE_URL = "https://api.postcodes.io/postcodes/"
TIMEOUT_SECONDS = 4
CACHE_SECONDS = 7 * 24 * 60 * 60

#: Checks one client address may make per minute. An applicant makes a handful;
#: the limit only stops the endpoint being used as a free lookup service.
MAX_CHECKS_PER_MINUTE = 60

#: A UK postcode with the space removed: outward code (area, district) and
#: inward code (sector, unit), e.g. CT11AA, SW1A1AA, M11AE.
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}$")

INVALID_POSTCODE = "Enter a valid UK postcode, for example CT1 1AA."
UNKNOWN_POSTCODE = "We could not find this postcode. Check it and try again."


class PostcodeLookupUnavailable(Exception):
    """The lookup service could not answer, so the postcode is neither found nor unknown."""


def compact(postcode):
    return re.sub(r"\s+", "", str(postcode or "")).upper()


def is_valid(postcode):
    return bool(POSTCODE_RE.match(compact(postcode)))


def formatted(postcode):
    """The postcode as it is written, e.g. 'ct11aa' -> 'CT1 1AA'."""
    code = compact(postcode)
    return f"{code[:-3]} {code[-3:]}"


def _text(value):
    return value[:100] if isinstance(value, str) else ""


def lookup(postcode):
    """Where a UK postcode is, or None when there is no such postcode.

    Returns {"postcode", "country", "region", "district"}: the postcode as
    written, the UK country, the English region ("" outside England) and the
    local authority district. Raises PostcodeLookupUnavailable when the service
    cannot answer.
    """
    code = compact(postcode)
    if not POSTCODE_RE.match(code):
        return None
    key = f"postcode-lookup:v1:{code}"
    cached = cache.get(key)
    if cached is not None:
        # {} records a postcode the service did not know.
        return cached or None
    try:
        response = requests.get(SERVICE_URL + quote(code), timeout=TIMEOUT_SECONDS, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise PostcodeLookupUnavailable(str(exc)) from None
    if response.status_code == 404:
        cache.set(key, {}, CACHE_SECONDS)
        return None
    if response.status_code != 200:
        raise PostcodeLookupUnavailable(f"postcodes.io answered {response.status_code}")
    try:
        result = response.json().get("result")
    except (ValueError, AttributeError):
        raise PostcodeLookupUnavailable("postcodes.io sent an unreadable answer") from None
    if not isinstance(result, dict):
        raise PostcodeLookupUnavailable("postcodes.io sent no result")
    found = {
        "postcode": _text(result.get("postcode")) or formatted(code),
        "country": _text(result.get("country")),
        "region": _text(result.get("region")),
        "district": _text(result.get("admin_district")),
    }
    cache.set(key, found, CACHE_SECONDS)
    return found


def _too_many(request):
    key = f"postcode-lookup:rate:{client_ip(request) or 'unknown'}:{int(time.time() // 60)}"
    cache.add(key, 0, 120)
    try:
        return cache.incr(key) > MAX_CHECKS_PER_MINUTE
    except ValueError:
        # The counter expired between add and incr: this is the first check of a new minute.
        return False


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def postcode_lookup(request):
    if request.method != "GET":
        return _error("Method not allowed.", 405)
    # A custom header forces a CORS preflight, so other sites cannot use this
    # endpoint from their visitors' browsers.
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return _error("Missing X-Requested-With header.", 403)
    postcode = request.GET.get("postcode", "")
    if len(postcode) > 16 or not is_valid(postcode):
        return _error(INVALID_POSTCODE, 400)
    if _too_many(request):
        return _error("Too many postcode checks. Please wait a minute and try again.", 429)
    try:
        found = lookup(postcode)
    except PostcodeLookupUnavailable as exc:
        logger.warning("Postcode lookup unavailable: %s", exc)
        return _error("We could not check this postcode just now.", 503)
    if found is None:
        return JsonResponse({"found": False})
    return JsonResponse({"found": True, **found, "inEngland": found["country"] == "England"})
