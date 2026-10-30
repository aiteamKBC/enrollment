"""The postcode check behind the public eligibility form.

requests.get is replaced in every test and the cache is process-local: no
request leaves the machine.
"""
import json
from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from enrolment_api import postcode_lookup as pl

URL = "/enrolment_api/public/postcode-lookup/"
LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "postcode-tests"}}

CANTERBURY = {
    "status": 200,
    "result": {"postcode": "CT1 1AA", "country": "England", "region": "South East", "admin_district": "Canterbury",
               "longitude": 1.08, "latitude": 51.28},
}


def answer(status, body=None):
    response = Mock(status_code=status)
    response.json.return_value = body
    return response


@override_settings(CACHES=LOCAL_CACHE)
class LookupTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_finds_a_postcode_and_keeps_only_where_it_is(self):
        with patch.object(pl.requests, "get", return_value=answer(200, CANTERBURY)) as get:
            found = pl.lookup(" ct1  1aa ")
        self.assertEqual(found, {"postcode": "CT1 1AA", "country": "England", "region": "South East", "district": "Canterbury"})
        # Only the postcode leaves the server, compacted and quoted into a fixed URL.
        self.assertEqual(get.call_args.args[0], "https://api.postcodes.io/postcodes/CT11AA")
        self.assertEqual(get.call_args.kwargs["timeout"], pl.TIMEOUT_SECONDS)

    def test_answers_are_cached_including_unknown_postcodes(self):
        with patch.object(pl.requests, "get", side_effect=[answer(200, CANTERBURY), answer(404, {"status": 404})]) as get:
            pl.lookup("CT1 1AA")
            self.assertEqual(pl.lookup("CT11AA")["district"], "Canterbury")
            self.assertIsNone(pl.lookup("ZZ9 9ZZ"))
            self.assertIsNone(pl.lookup("ZZ99ZZ"))
        self.assertEqual(get.call_count, 2)

    def test_malformed_postcodes_are_never_sent(self):
        with patch.object(pl.requests, "get") as get:
            for text in ("", "12345", "CT1", "../../etc", "CT1 1AA?x=1"):
                self.assertIsNone(pl.lookup(text))
        get.assert_not_called()

    def test_a_service_failure_is_neither_found_nor_unknown(self):
        for failure in (requests.Timeout("slow"), answer(500), answer(200, ["not", "an", "object"]), answer(200, {"result": None})):
            cache.clear()
            kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
            with patch.object(pl.requests, "get", **kwargs), self.assertRaises(pl.PostcodeLookupUnavailable):
                pl.lookup("CT1 1AA")
        # A failure is not cached: the next check asks again.
        with patch.object(pl.requests, "get", return_value=answer(200, CANTERBURY)):
            self.assertEqual(pl.lookup("CT1 1AA")["country"], "England")

    def test_formats_postcodes_as_written(self):
        self.assertEqual(pl.formatted("sw1a1aa"), "SW1A 1AA")
        self.assertTrue(pl.is_valid("m1 1ae"))
        self.assertFalse(pl.is_valid("M1"))


@override_settings(CACHES=LOCAL_CACHE)
class ViewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def get(self, postcode, **headers):
        headers.setdefault("HTTP_X_REQUESTED_WITH", "XMLHttpRequest")
        return pl.postcode_lookup(self.factory.get(URL, {"postcode": postcode}, **headers))

    def test_reports_the_location_and_whether_it_is_in_england(self):
        with patch.object(pl, "lookup", return_value={"postcode": "EH1 1YZ", "country": "Scotland", "region": "", "district": "City of Edinburgh"}):
            response = self.get("eh11yz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {
            "found": True, "postcode": "EH1 1YZ", "country": "Scotland", "region": "", "district": "City of Edinburgh",
            "inEngland": False,
        })

    def test_an_unknown_postcode_is_not_found(self):
        with patch.object(pl, "lookup", return_value=None):
            self.assertEqual(json.loads(self.get("ZZ9 9ZZ").content), {"found": False})

    def test_refuses_bad_requests_before_asking_the_service(self):
        with patch.object(pl, "lookup") as lookup:
            self.assertEqual(self.get("not a postcode").status_code, 400)
            self.assertEqual(self.get("C" * 40).status_code, 400)
            no_header = pl.postcode_lookup(self.factory.get(URL, {"postcode": "CT1 1AA"}))
            self.assertEqual(no_header.status_code, 403)
            post = pl.postcode_lookup(self.factory.post(URL, {"postcode": "CT1 1AA"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"))
            self.assertEqual(post.status_code, 405)
        lookup.assert_not_called()

    def test_a_service_failure_is_a_503(self):
        with patch.object(pl, "lookup", side_effect=pl.PostcodeLookupUnavailable("down")), \
                self.assertLogs("enrolment_api.postcode_lookup", level="WARNING"):
            response = self.get("CT1 1AA")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("down", response.content.decode())

    def test_one_address_is_limited_per_minute(self):
        with patch.object(pl, "lookup", return_value=None), patch.object(pl, "MAX_CHECKS_PER_MINUTE", 3):
            statuses = [self.get("CT1 1AA").status_code for _ in range(4)]
        self.assertEqual(statuses, [200, 200, 200, 429])
