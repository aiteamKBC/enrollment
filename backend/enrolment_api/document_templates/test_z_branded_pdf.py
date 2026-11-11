"""Real Node/jsPDF bytes through Django, with isolated learner A/B fixtures."""
import base64
import copy
import hashlib
from unittest.mock import patch
from django.test import override_settings
from . import test_issued_preview as fixtures
from . import pdf_artifacts as pdf, pdf_views, original_pdf, admin_preview, revisions
from .models import IssuedTemplate, RenderHistory, LearnerDocumentRevision, TemplateVersion
from .registry import REVIEW_KEYS, seed
from learner_api.models import EnrolmentReview


class BrandedPdfTests(fixtures.AdminPreviewTests):
    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_header_release_preserves_cached_v3_and_keeps_old_signature_continuations_on_v3(self):
        with patch.object(pdf, 'BRAND', 'kbc-dfe-purple-v3'):
            old = self.first('ilr')
        issue = IssuedTemplate.objects.get(pk=old['template']['issueId'])
        artifact = pdf.artifact(issue, old); before = copy.deepcopy(artifact.payload)
        new = self.first('ilr', self.people[1])
        self.assertEqual(new['template']['presentationTheme'], 'kbc-dfe-purple-v4')
        with patch.object(pdf, 'render', side_effect=AssertionError('Historical GET must not restyle PDFs')):
            result = self.api(pdf_views.issued_pdf, pk=self.learner.pk, issue_id=issue.pk)
            self.assertEqual(result.content, base64.b64decode(before['pdf']))
        preview = self.result(self.preview('ilr', issue.source_id))
        signed = self.result(self.sign('ilr', issue.source_id, preview['previewToken']))['document']
        self.assertEqual(signed['template']['presentationTheme'], 'kbc-dfe-purple-v3')
        self.assertIsNotNone(pdf.artifact(issue, signed))
        artifact.refresh_from_db(); self.assertEqual(artifact.payload, before)

    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_all_eight_real_issues_store_server_pdf_originals_and_previews_match_downloads_without_writes(self):
        EnrolmentReview.objects.filter(learner_id=self.learner.pk)._raw_delete(using='enrolment')
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            with self.subTest(key=key):
                before = (IssuedTemplate.objects.count(), RenderHistory.objects.count())
                preview = self.result(self.preview(key))
                self.assertTrue(base64.b64decode(preview['pdfBase64']).startswith(b'%PDF-'))
                self.assertEqual((IssuedTemplate.objects.count(), RenderHistory.objects.count()), before)
                document = self.first(key); issue = IssuedTemplate.objects.get(pk=document['template']['issueId'])
                self.assertEqual(document['template']['presentationTheme'], pdf.BRAND)
                self.assertEqual(document['template']['originalPdfVersion'], 2)
                self.assertEqual(document['template']['content']['blocks'], seed(key)['blocks'])
                self.assertFalse(any(document['signatures'][role]['signed'] for role in revisions.required_roles(key, document)))
                row = pdf.artifact(issue, document); self.assertIsNotNone(row)
                expected = base64.b64decode(row.payload['pdf'])
                count = RenderHistory.objects.count()
                # GET must not call the renderer, allocate revisions or issue requests.
                with patch.object(pdf, 'render', side_effect=AssertionError('GET must not regenerate')):
                    result = self.api(pdf_views.issued_pdf, pk=self.learner.pk, issue_id=issue.pk)
                    self.assertEqual(result.content, expected)
                    shown = self.result(self.preview(key, issue.source_id)); self.assertIn('/pdf/?render=', shown['pdfUrl'])
                    self.assertEqual(self.first(key)['template']['issueId'], str(issue.pk))
                self.assertEqual(RenderHistory.objects.count(), count)
                self.assertEqual(self.api(original_pdf.original_pdf, pk=self.learner.pk, issue_id=issue.pk).content, expected)
        self.assertEqual(IssuedTemplate.objects.count(), 8)
        self.assertEqual(LearnerDocumentRevision.objects.count(), 0)
        self.assertEqual(TemplateVersion.objects.count(), 0)

    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_signing_appends_new_pdf_and_never_replaces_the_unsigned_original(self):
        document = self.first('ilr'); issue = IssuedTemplate.objects.get(pk=document['template']['issueId'])
        original = pdf.artifact(issue, document); frozen = copy.deepcopy(original.payload)
        preview = self.result(self.preview('ilr', issue.source_id))
        with patch.object(pdf, 'render', side_effect=pdf.DatabaseError('Synthetic signed PDF failure')):
            self.result(self.sign('ilr', issue.source_id, preview['previewToken']), 502)
        source = revisions.source_record('ilr', self.learner.pk)
        self.assertFalse(source.provider_signature)
        signed = self.result(self.sign('ilr', issue.source_id, preview['previewToken']))['document']
        self.assertTrue(signed['signatures']['provider']['signed'])
        current = pdf.artifact(issue, signed); self.assertIsNotNone(current)
        self.assertNotEqual(current.fingerprint, original.fingerprint)
        original.refresh_from_db(); self.assertEqual(original.payload, frozen)
        request = self.factory.get('/', {'render': original.fingerprint})
        from types import SimpleNamespace
        request.user = SimpleNamespace(is_authenticated=True,is_staff=True,is_superuser=False)
        self.assertEqual(pdf_views.issued_pdf(request,pk=self.learner.pk,issue_id=issue.pk).content,base64.b64decode(frozen['pdf']))
        with patch.object(pdf, 'render', side_effect=AssertionError('Historical PDF must not regenerate')):
            self.assertEqual(self.api(pdf_views.issued_pdf, pk=self.learner.pk, issue_id=issue.pk).content, base64.b64decode(current.payload['pdf']))
        self.result(self.api(original_pdf.original_pdf, 'POST', {'pdf': frozen['pdf'], 'snapshot': document}, pk=self.learner.pk, issue_id=issue.pk), 409)
        self.assertEqual(self.api(pdf_views.issued_pdf, pk=self.people[1].pk, issue_id=issue.pk).status_code, 404)

    def test_historical_pdf_checksum_survives_new_branded_issuance_and_missing_artifact_fails_closed(self):
        document = self.first('training-plan'); issue_id = document['template']['issueId']
        old_pdf = pdf.render({'template': document['template'], 'data': document})
        self.result(self.api(original_pdf.original_pdf, 'POST', {'pdf': base64.b64encode(old_pdf).decode(), 'snapshot': document},
                             pk=self.learner.pk, issue_id=issue_id), 201)
        checksum = hashlib.sha256(old_pdf).hexdigest()
        with override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True):
            new = self.first('training-plan', self.people[1]); self.assertEqual(new['template']['presentationTheme'], pdf.BRAND)
            with patch.object(pdf, 'render', side_effect=AssertionError('Old PDF must not regenerate')):
                old = self.api(original_pdf.original_pdf, pk=self.learner.pk, issue_id=issue_id).content
                self.assertEqual(hashlib.sha256(old).hexdigest(), checksum)
                self.result(self.preview('training-plan', document['id']))
            legacy_source = revisions.source_record('training-plan', self.learner.pk)
            self.create(legacy_source)
            original_revision = LearnerDocumentRevision.objects.get(issue_id=issue_id, number=1)
            self.assertTrue(revisions.preview_response(original_revision)['pdfUrl'].endswith('/original-pdf/'))
            missing = self.first('written-agreement', self.people[1])
            RenderHistory.objects.filter(issue_id=missing['template']['issueId'], payload__kind='branded-issued-pdf-v3').delete()
            self.assertEqual(self.api(pdf_views.issued_pdf, pk=self.people[1].pk, issue_id=missing['template']['issueId']).status_code, 409)

    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_create_edit_saved_revision_pdf_stays_exact_and_finalised_history_does_not_regenerate(self):
        document = self.first('training-plan'); source = revisions.source_record('training-plan', self.learner.pk)
        detail = self.create(source); self.assertEqual(detail['preview']['template']['presentationTheme'], pdf.BRAND)
        self.assertFalse(detail['preview']['data']['signatures']['provider']['signed'])
        saved = self.save_revision(detail)
        final = self.finalize(saved)
        revision = LearnerDocumentRevision.objects.get(pk=final['revision']['id'])
        self.assertIn('/pdf/?render=', final['preview']['pdfUrl'])
        row = pdf.artifact(revision.issue, revisions.payload(revision), revision.pk)
        expected = base64.b64decode(row.payload['pdf'])
        with patch.object(pdf, 'render', side_effect=AssertionError('History GET must not regenerate')):
            result = self.api(pdf_views.revision_pdf, pk=self.learner.pk, identifier=revision.pk)
            self.assertEqual(result.content, expected)
            with override_settings(DOCUMENT_BRANDED_PDF_ENABLED=False):
                self.assertIn('/pdf/?render=', revisions.preview_response(revision)['pdfUrl'])
        self.assertEqual(source.pk, revisions.source_record('training-plan', self.learner.pk).pk)

    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_runtime_failure_rolls_back_first_issue_without_losing_prior_learner_data(self):
        from learner_api import ilr_document
        old_name = self.learner.username
        with patch.object(pdf, 'render', side_effect=pdf.DatabaseError('Synthetic runtime unavailable')):
            self.assertEqual(self.api(ilr_document.issue_ilr, 'POST', pk=self.learner.pk).status_code, 502)
        self.assertFalse(IssuedTemplate.objects.filter(learner_id=self.learner.pk).exists())
        self.learner.refresh_from_db(); self.assertEqual(self.learner.username, old_name)
