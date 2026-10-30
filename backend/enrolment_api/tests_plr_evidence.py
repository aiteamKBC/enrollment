"""PLR entries: certificate/evidence stored in Azure with its path on the
Wizard_Plr_Records row, the new dates saved, and removed entries' files cleaned up.

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

from enrolment_api import plr_evidence as view
from enrolment_api import wizard_steps

BASE = "/enrolment_api/wizard/apprenticeship/41/plr-evidence/"
AZURE = dict(
    AZURE_STORAGE_ACCOUNT="acct", AZURE_STORAGE_KEY="key",
    AZURE_QUARANTINE_CONTAINER="evidence-quarantine",
    AZURE_APPROVED_CONTAINER="evidence-approved",
    AZURE_REJECTED_CONTAINER="evidence-rejected",
)


def _row(ref="rec-1", items=None):
    return SimpleNamespace(record_ref=ref, evidence=items if items is not None else [], save=MagicMock())


@override_settings(**AZURE)
class PlrEvidenceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.exists.return_value = True
        self.model = MagicMock()
        patches = [
            patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "0"}),
            patch.dict(view.KINDS, {"apprenticeship": learner_model}),
            patch.object(view, "WizardPlrRecord", self.model),
            patch.object(view.transaction, "atomic", lambda **_: nullcontext()),
            patch.object(view.uploads, "upload_to_quarantine"),
            patch.object(view.uploads, "move_blob"),
            patch.object(view.uploads, "delete_blob"),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.upload, self.move, self.delete = mocks[4:7]

    def _post(self, record_ref="rec-1"):
        request = self.factory.post(BASE, data={
            "record_ref": record_ref, "file": SimpleUploadedFile("cert.pdf", b"%PDF", content_type="application/pdf"),
        })
        return view.plr_evidence(request, kind="apprenticeship", learner_id=41)

    def test_upload_is_stored_in_azure_with_its_path_on_the_entry_row(self):
        row = _row()
        self.model.objects.select_for_update.return_value.get_or_create.return_value = (row, True)

        response = self._post()

        self.assertEqual(response.status_code, 201, response.content)
        self.model.objects.select_for_update.return_value.get_or_create.assert_called_once_with(
            learner_kind="apprenticeship", learner_id=41, record_ref="rec-1",
        )
        blob_name = self.upload.call_args.args[1]
        self.assertTrue(blob_name.startswith("apprenticeship/41/plr/rec-1/"))
        [item] = row.evidence
        self.assertEqual(item["path"], f"https://acct.blob.core.windows.net/evidence-approved/{blob_name}")
        row.save.assert_called_once_with(update_fields=["evidence", "updated_at"])
        self.assertIn(b'"recordRef": "rec-1"', response.content)
        self.assertNotIn(b"blob.core.windows.net", response.content)

    def test_a_record_ref_that_is_not_a_plain_id_is_refused(self):
        self.assertEqual(self._post("../other").status_code, 400)
        self.upload.assert_not_called()

    def test_delete_finds_the_file_on_its_entry_and_removes_blob_and_record(self):
        keep = {"id": str(uuid.uuid4()), "container": "evidence-approved", "blobName": "b/keep"}
        drop = {"id": str(uuid.uuid4()), "container": "evidence-approved", "blobName": "b/drop"}
        other, holder = _row("rec-0", [keep]), _row("rec-1", [drop])
        self.model.objects.filter.return_value.select_for_update.return_value = [other, holder]

        response = view.delete_plr_evidence(
            self.factory.delete(BASE), kind="apprenticeship", learner_id=41, file_id=uuid.UUID(drop["id"]),
        )

        self.assertEqual(response.status_code, 200)
        self.delete.assert_called_once_with("evidence-approved", "b/drop")
        self.assertEqual(holder.evidence, [])
        self.assertEqual(other.evidence, [keep])

    def test_another_learner_gets_not_found(self):
        request = self.factory.get(BASE)
        request.login_account = SimpleNamespace(role="learner", subject_id=99, is_active=True)
        with patch.dict(os.environ, {"ENROLMENT_API_REQUIRE_AUTH": "1"}):
            response = view.plr_evidence(request, kind="apprenticeship", learner_id=41)

        self.assertEqual(response.status_code, 404)
        self.model.objects.filter.assert_not_called()


class PlrProjectionTests(SimpleTestCase):
    def _project(self, removed_rows):
        removed = MagicMock()
        removed.__iter__.return_value = iter(removed_rows)
        with patch.object(wizard_steps.WizardPlr.objects, "update_or_create"), \
                patch.object(wizard_steps.WizardPlrRecord.objects, "update_or_create") as upsert, \
                patch.object(wizard_steps.WizardPlrRecord.objects, "filter") as rows, \
                patch("django.db.transaction.on_commit") as on_commit, \
                patch("enrolment_api.learner_uploads.remove_blob") as remove_blob:
            rows.return_value.exclude.return_value = removed
            wizard_steps.project_draft("apprenticeship", 41, {"plr": {"records": [{
                "id": "rec-1", "qualificationType": "GCSE", "subject": "Maths", "startDate": "2018-09-01",
                "endDate": "2020-06-30", "awardDate": "2020-08-20", "grade": "5", "recordType": "Manual",
            }]}})
            for call in on_commit.call_args_list:
                call.args[0]()
        return upsert, removed, on_commit, remove_blob

    def test_the_study_dates_are_saved(self):
        upsert, _, _, _ = self._project([])

        defaults = upsert.call_args.kwargs["defaults"]
        self.assertEqual(str(defaults["start_date"]), "2018-09-01")
        self.assertEqual(str(defaults["end_date"]), "2020-06-30")
        # Files are written only by the evidence endpoint, never by a draft save.
        self.assertNotIn("evidence", defaults)

    def test_removing_an_entry_deletes_its_files_after_commit(self):
        item = {"id": "f1", "container": "evidence-approved", "blobName": "b/gone"}
        _, removed, on_commit, remove_blob = self._project([SimpleNamespace(evidence=[item])])

        removed.delete.assert_called_once()
        on_commit.assert_called_once()
        remove_blob.assert_called_once_with(item)

    def test_removing_an_entry_without_files_schedules_nothing(self):
        _, removed, on_commit, _ = self._project([SimpleNamespace(evidence=[])])

        removed.delete.assert_called_once()
        on_commit.assert_not_called()
