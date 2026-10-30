"""CV/Job Description step: documents stored in Azure with their path on the
Wizard_Cv_Job row, and the redesigned questions saved to their own columns.

SimpleTestCase with the ORM and the Azure SDK mocked: no query ever reaches a
database and no blob is written.
"""
import os
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, override_settings

from enrolment_api import cv_job_documents as view
from enrolment_api import wizard_steps

BASE = "/enrolment_api/wizard/apprenticeship/41/cv-documents/"
AZURE = dict(
    AZURE_STORAGE_ACCOUNT="acct", AZURE_STORAGE_KEY="key",
    AZURE_QUARANTINE_CONTAINER="evidence-quarantine",
    AZURE_APPROVED_CONTAINER="evidence-approved",
    AZURE_REJECTED_CONTAINER="evidence-rejected",
)


def _row(items=None):
    return SimpleNamespace(documents=items if items is not None else [], save=MagicMock())


@override_settings(**AZURE)
class CvDocumentTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.exists.return_value = True
        self.model = MagicMock()
        patches = [
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.dict(view.KINDS, {"apprenticeship": learner_model}),
            patch.object(view, "WizardCvJob", self.model),
            patch.object(view.transaction, "atomic", lambda **_: nullcontext()),
            patch.object(view.uploads, "upload_to_quarantine"),
            patch.object(view.uploads, "move_blob"),
            patch.object(view.uploads, "delete_blob"),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.upload, self.move, self.delete = mocks[4:7]

    def _post(self, doc_kind="cv", name="cv.pdf", content_type="application/pdf"):
        request = self.factory.post(BASE, data={
            "doc_kind": doc_kind, "file": SimpleUploadedFile(name, b"%PDF", content_type=content_type),
        })
        return view.cv_documents(request, kind="apprenticeship", learner_id=41)

    def test_upload_is_stored_in_azure_with_its_path_and_kind_on_the_cv_row(self):
        row = _row()
        self.model.objects.select_for_update.return_value.get_or_create.return_value = (row, False)

        response = self._post("gcse-maths", "maths.png", "image/png")

        self.assertEqual(response.status_code, 201, response.content)
        blob_name = self.upload.call_args.args[1]
        self.assertTrue(blob_name.startswith("apprenticeship/41/cv-job/gcse-maths/"))
        self.move.assert_called_once_with("evidence-quarantine", "evidence-approved", blob_name)
        [item] = row.documents
        self.assertEqual(item["docKind"], "gcse-maths")
        self.assertEqual(item["path"], f"https://acct.blob.core.windows.net/evidence-approved/{blob_name}")
        row.save.assert_called_once_with(update_fields=["documents", "updated_at"])
        self.assertIn(b'"docKind": "gcse-maths"', response.content)
        self.assertNotIn(b"blob.core.windows.net", response.content)

    def test_an_unknown_document_kind_is_refused_before_upload(self):
        self.assertEqual(self._post("passport").status_code, 400)
        self.upload.assert_not_called()

    def test_a_failed_record_write_removes_the_orphaned_blob(self):
        from django.db import DatabaseError
        self.model.objects.select_for_update.return_value.get_or_create.side_effect = DatabaseError("down")

        response = self._post()

        self.assertEqual(response.status_code, 502)
        self.delete.assert_called_once()

    def test_delete_removes_the_blob_and_its_record(self):
        keep, drop = ({"id": str(uuid.uuid4()), "docKind": k, "container": "evidence-approved", "blobName": f"b/{k}"}
                      for k in ("cv", "transcript"))
        row = _row([keep, drop])
        self.model.objects.select_for_update.return_value.filter.return_value.first.return_value = row

        response = view.delete_cv_document(
            self.factory.delete(BASE), kind="apprenticeship", learner_id=41, file_id=uuid.UUID(drop["id"]),
        )

        self.assertEqual(response.status_code, 200)
        self.delete.assert_called_once_with("evidence-approved", "b/transcript")
        self.assertEqual(row.documents, [keep])

    def test_another_learner_gets_not_found(self):
        request = self.factory.get(BASE)
        request.login_account = SimpleNamespace(role="learner", subject_id=99, is_active=True)
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}):
            response = view.cv_documents(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 404)
        self.model.objects.filter.assert_not_called()


class CvJobProjectionTests(SimpleTestCase):
    def test_the_new_answers_are_saved_to_their_columns(self):
        with patch.object(wizard_steps.WizardCvJob.objects, "update_or_create") as upsert:
            wizard_steps.project_draft("apprenticeship", 41, {"cvJob": {
                "experienceText": "N/A", "highestQualification": "BA (Hons)", "highestQualificationField": "Business",
                "hasFieldQualification": True, "highestFieldQualification": "CIM Level 4",
                "gcseEnglish": True, "gcseMaths": False,
                "functionalSkillsEnrol": "I would like to enrol in Maths functional skills course only",
            }})

        defaults = upsert.call_args.kwargs["defaults"]
        self.assertEqual(defaults["highest_qualification"], "BA (Hons)")
        self.assertEqual(defaults["highest_qualification_field"], "Business")
        self.assertIs(defaults["has_field_qualification"], True)
        self.assertEqual(defaults["highest_field_qualification"], "CIM Level 4")
        self.assertIs(defaults["gcse_english"], True)
        self.assertIs(defaults["gcse_maths"], False)
        # Files are written only by the documents endpoint, never by a draft save.
        self.assertNotIn("documents", defaults)
