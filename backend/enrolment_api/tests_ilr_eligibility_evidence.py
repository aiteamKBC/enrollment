"""Extended ILR eligibility evidence: stored in Azure, path recorded on the ILR row.

SimpleTestCase with the ORM and the Azure SDK mocked: Django refuses any real
query from these tests and no blob is ever written, so they are safe to run
against any configured database.
"""
import os
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, override_settings

from enrolment_api import ilr_eligibility_evidence as view

BASE = "/enrolment_api/extended-ilr/apprenticeship/41/eligibility-evidence/"
AZURE = dict(
    AZURE_STORAGE_ACCOUNT="acct", AZURE_STORAGE_KEY="key",
    AZURE_QUARANTINE_CONTAINER="evidence-quarantine",
    AZURE_APPROVED_CONTAINER="evidence-approved",
    AZURE_REJECTED_CONTAINER="evidence-rejected",
)


def _row(items=None, completed=False):
    return SimpleNamespace(eligibility_evidence=items if items is not None else [], completed=completed, save=MagicMock())


def _stored(file_id="11111111-1111-1111-1111-111111111111"):
    return {
        "id": file_id, "filename": "passport.pdf", "contentType": "application/pdf", "sizeBytes": 4,
        "container": "evidence-approved", "blobName": f"apprenticeship/41/ilr-eligibility/{file_id}.pdf",
        "path": f"https://acct.blob.core.windows.net/evidence-approved/apprenticeship/41/ilr-eligibility/{file_id}.pdf",
        "uploadedAt": "2026-09-27T10:00:00+00:00",
    }


@override_settings(**AZURE)
class EligibilityEvidenceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username="Test Learner")
        self.model = MagicMock()
        patches = [
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.dict(view.KINDS, {"apprenticeship": learner_model}),
            patch.object(view, "ExtendedIlr", self.model),
            patch.object(view.transaction, "atomic", lambda **_: nullcontext()),
            patch.object(view.uploads, "upload_to_quarantine"),
            patch.object(view.uploads, "move_blob"),
            patch.object(view.uploads, "delete_blob"),
            patch.object(view, "get_download_sas", return_value="https://sas.example/file"),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.upload, self.move, self.delete, self.sas = self.mocks[4:8]
        # Nothing signed yet by default.
        self.model.objects.filter.return_value.exists.return_value = False

    def _post(self, name="passport.pdf", content_type="application/pdf"):
        request = self.factory.post(BASE, data={"file": SimpleUploadedFile(name, b"%PDF", content_type=content_type)})
        return view.eligibility_evidence(request, kind="apprenticeship", learner_id=41)

    def test_upload_goes_through_quarantine_and_records_the_approved_path_on_the_ilr_row(self):
        row = _row()
        self.model.objects.select_for_update.return_value.get_or_create.return_value = (row, False)

        response = self._post()

        self.assertEqual(response.status_code, 201, response.content)
        blob_name = self.upload.call_args.args[1]
        self.assertTrue(blob_name.startswith("apprenticeship/41/ilr-eligibility/"))
        self.move.assert_called_once_with("evidence-quarantine", "evidence-approved", blob_name)
        [item] = row.eligibility_evidence
        self.assertEqual(item["container"], "evidence-approved")
        self.assertEqual(item["blobName"], blob_name)
        self.assertEqual(item["path"], f"https://acct.blob.core.windows.net/evidence-approved/{blob_name}")
        self.assertEqual(item["filename"], "passport.pdf")
        # Only the evidence column is written: the answers and signature flags are untouched.
        row.save.assert_called_once_with(update_fields=["eligibility_evidence", "updated_at"])
        # The storage pointer is not handed to the browser.
        self.assertNotIn(b"blobName", response.content)
        self.assertNotIn(b"blob.core.windows.net", response.content)

    def test_a_second_upload_is_appended_rather_than_replacing_the_first(self):
        row = _row([_stored()])
        self.model.objects.select_for_update.return_value.get_or_create.return_value = (row, False)

        self.assertEqual(self._post("visa.png", "image/png").status_code, 201)

        self.assertEqual([i["filename"] for i in row.eligibility_evidence], ["passport.pdf", "visa.png"])

    def test_a_signed_ilr_refuses_uploads_before_anything_reaches_azure(self):
        self.model.objects.filter.return_value.exists.return_value = True

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.upload.assert_not_called()

    def test_a_signature_landing_mid_upload_discards_the_blob(self):
        row = _row(completed=True)
        self.model.objects.select_for_update.return_value.get_or_create.return_value = (row, False)

        response = self._post()

        self.assertEqual(response.status_code, 409)
        row.save.assert_not_called()
        self.delete.assert_called_once()
        self.assertEqual(self.delete.call_args.args[0], "evidence-approved")

    def test_a_file_failing_the_scan_is_moved_to_rejected_and_not_recorded(self):
        with patch.object(view.uploads, "_scan", return_value="infected"):
            response = self._post()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.move.call_args.args[:2], ("evidence-quarantine", "evidence-rejected"))
        self.model.objects.select_for_update.assert_not_called()

    def test_unsupported_types_are_refused(self):
        response = self._post("clip.mp4", "video/mp4")

        self.assertEqual(response.status_code, 400)
        self.upload.assert_not_called()

    def test_listing_returns_files_and_lock_state_without_storage_pointers(self):
        self.model.objects.filter.return_value.first.return_value = _row([_stored()], completed=True)

        response = view.eligibility_evidence(self.factory.get(BASE), kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"locked": true', response.content)
        self.assertIn(b"passport.pdf", response.content)
        self.assertNotIn(b"blob.core.windows.net", response.content)

    def test_download_mints_a_sas_url_for_the_stored_blob(self):
        stored = _stored()
        self.model.objects.filter.return_value.first.return_value = _row([stored])

        response = view.download_eligibility_evidence(
            self.factory.get(BASE), kind="apprenticeship", learner_id=41, file_id=uuid.UUID(stored["id"]),
        )

        self.assertEqual(response.status_code, 200)
        self.sas.assert_called_once_with("evidence-approved", stored["blobName"])

    def test_delete_removes_the_blob_and_its_record_while_unsigned(self):
        stored, other = _stored(), _stored("22222222-2222-2222-2222-222222222222")
        row = _row([stored, other])
        self.model.objects.select_for_update.return_value.filter.return_value.first.return_value = row

        response = view.delete_eligibility_evidence(
            self.factory.delete(BASE), kind="apprenticeship", learner_id=41, file_id=uuid.UUID(stored["id"]),
        )

        self.assertEqual(response.status_code, 200)
        self.delete.assert_called_once_with("evidence-approved", stored["blobName"])
        self.assertEqual(row.eligibility_evidence, [other])

    def test_delete_is_refused_once_the_ilr_is_signed(self):
        stored = _stored()
        row = _row([stored], completed=True)
        self.model.objects.select_for_update.return_value.filter.return_value.first.return_value = row

        response = view.delete_eligibility_evidence(
            self.factory.delete(BASE), kind="apprenticeship", learner_id=41, file_id=uuid.UUID(stored["id"]),
        )

        self.assertEqual(response.status_code, 409)
        self.delete.assert_not_called()
        row.save.assert_not_called()


@override_settings(**AZURE)
class EligibilityEvidenceAccessTests(SimpleTestCase):
    """The enrolment gate: a learner reaches only their own evidence."""

    def _as_learner(self, subject_id):
        request = RequestFactory().get(BASE)
        request.login_account = SimpleNamespace(role="learner", subject_id=subject_id, is_active=True)
        return request

    def test_another_learner_gets_not_found(self):
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}), \
                patch.object(view, "ExtendedIlr") as model:
            response = view.eligibility_evidence(self._as_learner(99), kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 404)
        model.objects.filter.assert_not_called()

    def test_unauthenticated_callers_are_refused(self):
        request = RequestFactory().get(BASE)
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}):
            response = view.eligibility_evidence(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 401)
