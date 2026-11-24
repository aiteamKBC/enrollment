"""Regression coverage for the public enrolment API URL surface.

The Programme Structure merge must not remove the existing learner-signature or
document-template routes.  These tests deliberately use anonymous requests for
the legacy endpoints so the authentication boundary remains visible.
"""

import os
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import resolve


class EnrolmentApiRouteTests(SimpleTestCase):
    def test_legacy_and_programme_structure_routes_resolve(self):
        routes = {
            "/enrolment_api/pending-signatures/": "learner-pending-signatures",
            "/enrolment_api/document-revisions/learners/42/training-plan/history/": "document_history",
            "/enrolment_api/document-templates/": "templates",
            "/enrolment_api/programme-structure/validate/": "programme-structure-validate",
            "/enrolment_api/programme-structure/preview/": "programme-structure-preview",
            "/enrolment_api/programme-structure/draft/": "programme-structure-draft",
            "/enrolment_api/programme-structure/publish/": "programme-structure-publish",
            "/enrolment_api/programme-structure/catalog/readiness/": "programme-catalog-readiness",
            "/enrolment_api/programme-structure/catalog/programmes/": "programme-catalog-programmes",
        }

        for path, expected in routes.items():
            match = resolve(path)
            self.assertEqual(
                match.url_name or match.func.__name__,
                expected,
                path,
            )

    def test_legacy_signature_and_document_routes_keep_authentication(self):
        paths = (
            "/enrolment_api/pending-signatures/",
            "/enrolment_api/document-revisions/learners/42/training-plan/history/",
            "/enrolment_api/document-templates/",
        )
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1", "API_REQUIRE_AUTH": "1"}, clear=False):
            for path in paths:
                response = self.client.get(path)
                self.assertIn(response.status_code, (401, 403), path)

    def test_catalogue_collection_is_mounted_behind_authentication(self):
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}):
            # OPTIONS bypasses the outer API middleware, so this proves the
            # Django URL resolver reached the catalogue view's own auth gate.
            response = self.client.options("/enrolment_api/programme-structure/catalog/programmes/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Authentication required."})
