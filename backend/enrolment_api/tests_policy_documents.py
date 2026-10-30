"""KBC policy PDFs: opened through a signed-in redirect to private storage.

SimpleTestCase with storage mocked: no database or Azure call is made.
"""
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase

from enrolment_api import auth, policy_documents

FRONTEND_LIST = Path(settings.BASE_DIR).parent / "frontend" / "src" / "mocks" / "enrolment-console.ts"


class OpenPolicyDocumentTests(SimpleTestCase):
    def open(self, doc_id):
        request = RequestFactory().get(f"/enrolment_api/policy-documents/{doc_id}/")
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}), \
                patch.object(policy_documents, "azure_configured", return_value=True), \
                patch.object(policy_documents, "get_read_sas", return_value="https://example.test/signed") as sas:
            return policy_documents.open_policy_document(request, doc_id=doc_id), sas

    def test_redirects_to_a_short_lived_link_that_opens_in_the_browser(self):
        response, sas = self.open("kbc-policy-library")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.test/signed")
        container, blob = sas.call_args.args
        self.assertEqual(container, settings.AZURE_ENROLMENT_DOCS_CONTAINER)
        self.assertEqual(blob, "policies/kbc/A1_-_Library_Policy.pdf")
        self.assertEqual(sas.call_args.kwargs["content_disposition"], 'inline; filename="A1_-_Library_Policy.pdf"')

    def test_an_unknown_document_is_not_found(self):
        response, sas = self.open("ibis-0")

        self.assertEqual(response.status_code, 404)
        sas.assert_not_called()

    def test_needs_a_signed_in_caller(self):
        request = RequestFactory().get("/enrolment_api/policy-documents/kbc-policy-library/")
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}), \
                patch.object(auth, "is_authenticated", return_value=False):
            response = policy_documents.open_policy_document(request, doc_id="kbc-policy-library")
        self.assertEqual(response.status_code, 401)

    def test_any_signed_in_learner_may_read_them(self):
        request = SimpleNamespace(login_account=SimpleNamespace(role="learner", subject_id=41))
        self.assertTrue(auth._may_access(request, "open_policy_document", {"doc_id": "kbc-policy-library"}))


class CatalogueTests(SimpleTestCase):
    def test_the_wizard_lists_exactly_these_documents(self):
        # Acknowledgements are stored by id, so the two lists must not drift.
        source = FRONTEND_LIST.read_text(encoding="utf-8")
        frontend = re.findall(r"\['(kbc-policy-[a-z0-9-]+)', '([^']+)'\]", source)
        self.assertEqual(dict(frontend), policy_documents.POLICY_DOCUMENTS)
