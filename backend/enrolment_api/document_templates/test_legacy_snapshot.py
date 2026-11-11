"""Legacy originals and finalized copies remain viewable without changing history."""
import copy
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import admin_preview, pdf_artifacts as pdf, revisions, revision_views as views
from .legacy_snapshot import SECTIONS, SNAPSHOT_NOTICE, can_render_original_snapshot
from .models import IssuedTemplate, LearnerDocumentRevision as Revision, RenderHistory
from . import test_templates as helpers, test_revisions as fixtures


@override_settings(DOCUMENT_REVISIONS_ENABLED=True, DOCUMENT_BRANDED_PDF_ENABLED=True)
class LegacySnapshotTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.TemplateWorkflowTests.setUp
    request = helpers.TemplateWorkflowTests.request
    payload = helpers.TemplateWorkflowTests.payload
    result = helpers.TemplateWorkflowTests.payload
    save = helpers.TemplateWorkflowTests.save
    publish = helpers.TemplateWorkflowTests.publish
    original = fixtures.RevisionTests.original
    draft = fixtures.RevisionTests.draft
    save_revision = fixtures.RevisionTests.save_revision
    finalize = fixtures.RevisionTests.finalize
    learner_request = fixtures.RevisionTests.learner_request

    def legacy_original(self):
        with override_settings(DOCUMENT_BRANDED_PDF_ENABLED=False):
            source, _ = self.original()
            self.draft(source)
        return source, Revision.objects.select_related('issue').get(number=1)

    def test_admin_and_learner_view_the_same_frozen_signed_original_without_writes(self):
        source, revision = self.legacy_original()
        frozen = copy.deepcopy(revision.data)
        # Neither a newer publication nor today's learner name may enter R1.
        newer = copy.deepcopy(revision.content); newer['title'] = 'Later publication only'
        with override_settings(DOCUMENT_BRANDED_PDF_ENABLED=False): self.publish(newer)
        self.learner.username = 'Changed current learner'; self.learner.save()
        before = (IssuedTemplate.objects.count(), Revision.objects.count(), RenderHistory.objects.count())
        with patch.object(pdf, 'render', side_effect=AssertionError('GET must not generate/store a replacement')), \
                patch.object(pdf, 'store', side_effect=AssertionError('GET must not write')):
            admin = self.result(self.request(views.revision_detail, pk=self.learner.pk, identifier=revision.pk))['preview']
            learner = self.result(self.learner_request(views.revision_detail, identifier=revision.pk))['preview']
        self.assertEqual(admin, learner)
        self.assertNotIn('pdfUnavailable', admin); self.assertNotIn('pdfUrl', admin)
        self.assertEqual(admin['notice'], SNAPSHOT_NOTICE)
        self.assertEqual(admin['data']['particulars'], frozen['particulars'])
        self.assertEqual(admin['data']['marks'], frozen['marks'])
        self.assertTrue(admin['data']['fullySigned'])
        self.assertNotIn('Later publication only', str(admin))
        self.assertNotIn('Changed current learner', str(admin))
        revision.refresh_from_db(); self.assertEqual(revision.data, frozen)
        self.assertEqual(before, (IssuedTemplate.objects.count(), Revision.objects.count(), RenderHistory.objects.count()))
        self.result(self.learner_request(views.revision_detail, learner_id=self.learner.pk + 1, identifier=revision.pk), 404)

    def test_admin_issued_original_preview_uses_the_same_legacy_compatibility(self):
        source, revision = self.legacy_original()
        request = self.factory.get('/', {'sourceId': str(source.pk)})
        from types import SimpleNamespace
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=False)
        with patch.object(pdf, 'render', side_effect=AssertionError('No server regeneration')):
            value = self.result(admin_preview.document_preview(request, pk=self.learner.pk, key='apprenticeship-agreement'))
        self.assertEqual(value['notice'], SNAPSHOT_NOTICE)
        self.assertIsNone(value['pdfUrl'])
        self.assertEqual(value['data']['marks'], revision.data['marks'])

    def test_missing_branded_original_and_finalized_revision_still_fail_closed(self):
        _, revision = self.legacy_original()
        revision.issue.template_snapshot = {**revision.issue.template_snapshot, 'presentationTheme': pdf.BRAND, 'originalPdfVersion': 2}
        self.assertIn('pdfUnavailable', revisions.preview_response(revision))
        revision.state = 'finalized'; revision.number = 3; revision.signature_method = 'keep'
        self.assertIn('pdfUnavailable', revisions.preview_response(revision))

    def test_available_training_plan_r3_uses_its_own_partial_signatures_for_both_roles(self):
        from django.utils import timezone
        from learner_api.models import TrainingPlanDocument
        from . import service
        from .registry import seed
        with override_settings(DOCUMENT_BRANDED_PDF_ENABLED=False):
            self.publish(seed('training-plan'))
            source = TrainingPlanDocument.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship',
                programme={'apprenticeName': 'Saved Training Plan Learner'},
                provider_signature=fixtures.MARK, provider_signed_name='Original Provider', provider_signed_at=timezone.now())
            service.bind_issue('training-plan', source, self.learner, revisions.serialize_source('training-plan', source, self.learner))
            r2 = self.finalize(self.save_revision(self.draft(source, 'training-plan'), 'keep'))
            r3 = self.finalize(self.save_revision(self.draft(source, 'training-plan'), 'sign-again'))
        revision = Revision.objects.select_related('issue').get(pk=r3['revision']['id'])
        self.assertEqual(revision.number, 3)
        signing = revisions.source_record('training-plan', self.learner.pk, revision.signing_source_id)
        signing.provider_signature = fixtures.MARK; signing.provider_signed_name = 'R3 Provider'
        signing.provider_signed_at = timezone.now(); signing.save()
        before = (copy.deepcopy(revision.data), revision.checksum, RenderHistory.objects.count())
        with patch.object(pdf, 'render', side_effect=AssertionError('No PDF regeneration on GET')):
            admin = self.result(self.request(views.revision_detail, pk=self.learner.pk, identifier=revision.pk))
            learner = self.result(self.learner_request(views.revision_detail, identifier=revision.pk))
        self.assertEqual(admin, learner)
        self.assertEqual(admin['revision']['status'], 'Available')
        value = admin['preview']; self.assertNotIn('pdfUnavailable', value)
        self.assertNotIn('pdfUrl', value); self.assertEqual(value['notice'], SNAPSHOT_NOTICE)
        self.assertEqual(value['data']['signatures']['provider']['name'], 'R3 Provider')
        self.assertFalse(value['data']['signatures']['apprentice']['signed'])
        self.assertFalse(value['data']['fullySigned'])
        self.assertEqual(value['template']['content'], revision.content)
        self.assertEqual(value['data']['marks']['provider'], fixtures.MARK)
        revision.refresh_from_db()
        self.assertEqual(before, (revision.data, revision.checksum, RenderHistory.objects.count()))
        kept = revisions.preview_response(Revision.objects.select_related('issue').get(pk=r2['revision']['id']))
        self.assertNotIn('pdfUnavailable', kept)
        self.assertFalse(kept['data']['fullySigned']); self.assertTrue(kept['data']['signatureProvenance'])
        # If the exact PDF exists, it always wins over compatibility rendering.
        row = RenderHistory.objects.create(issue=revision.issue,
            fingerprint=pdf.fingerprint(value['data'], revision.pk),
            payload={'kind': 'branded-issued-pdf-v3', 'revisionId': str(revision.pk), 'pdf': 'unchanged bytes'})
        self.assertEqual(revisions.preview_response(revision)['pdfUrl'],
                         pdf.revision_url(self.learner.pk, revision.pk, row.fingerprint))
        row.refresh_from_db(); self.assertEqual(row.payload['pdf'], 'unchanged bytes')
        row.delete()
        # Missing independent signing evidence must never fall back to R1 marks.
        revision.signing_source_id = '00000000-0000-0000-0000-000000000099'
        self.assertIn('pdfUnavailable', revisions.preview_response(revision))

    def test_incomplete_legacy_data_or_missing_signed_marks_is_not_reconstructed(self):
        _, revision = self.legacy_original()
        original = copy.deepcopy(revision.data)
        for missing in ('particulars', 'marks', 'signatures', 'planModules'):
            with self.subTest(missing=missing):
                revision.data = copy.deepcopy(original); revision.data.pop(missing)
                self.assertIn('pdfUnavailable', revisions.preview_response(revision))
        revision.data = copy.deepcopy(original); revision.data['marks']['apprentice'] = ''
        self.assertIn('pdfUnavailable', revisions.preview_response(revision))

    def test_only_four_statutory_snapshot_shapes_qualify_and_never_branded_or_review_data(self):
        _, revision = self.legacy_original()
        for key, sections in SECTIONS.items():
            with self.subTest(key=key):
                data = copy.deepcopy(revision.data)
                data.update({name: expected() for name, expected in sections.items()})
                data['signatures'] = {party: {'signed': True} for party in ('learner', 'apprentice', 'employer', 'provider')}
                data['marks'] = {party: fixtures.MARK for party in data['signatures']}
                self.assertTrue(can_render_original_snapshot(key, data))
                data['template']['originalPdfVersion'] = 2
                self.assertFalse(can_render_original_snapshot(key, data))
        self.assertFalse(can_render_original_snapshot('review-eligibility', revision.data))
