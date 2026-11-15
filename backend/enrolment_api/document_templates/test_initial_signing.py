"""Outstanding first-issue signatures survive creation of immutable R1 history."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import revisions, service, test_revisions as fixtures, test_templates as helpers
from .models import LearnerDocumentRevision as Revision
from .registry import seed


@override_settings(DOCUMENT_REVISIONS_ENABLED=True, DOCUMENT_BRANDED_PDF_ENABLED=False)
class InitialSigningTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.TemplateWorkflowTests.setUp
    request = helpers.TemplateWorkflowTests.request
    payload = helpers.TemplateWorkflowTests.payload
    result = helpers.TemplateWorkflowTests.payload
    save = helpers.TemplateWorkflowTests.save
    publish = helpers.TemplateWorkflowTests.publish
    draft = fixtures.RevisionTests.draft
    save_revision = fixtures.RevisionTests.save_revision
    finalize = fixtures.RevisionTests.finalize
    learner_request = fixtures.RevisionTests.learner_request

    def unsigned(self, key):
        self.publish(seed(key))
        model, _, _ = revisions.source_api(key)
        document = model.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk)
        service.bind_issue(key, document, self.learner, revisions.serialize_source(key, document, self.learner))
        self.draft(document, key)
        return document, Revision.objects.select_related('issue').get(issue__source_id=str(document.pk), number=1)

    def sign(self, key, party, signature=fixtures.MARK):
        _, _, signer = revisions.source_api(key)
        module = __import__(signer.__module__, fromlist=['advance_learner'])
        data = {'party': party, 'name': 'Synthetic ' + party, 'signature': signature}
        with patch.object(module, 'advance_learner', return_value=None):
            if party in ('apprentice', 'learner'):
                return self.learner_request(signer, 'POST', data)
            if party == 'employer':
                request = self.factory.post('/', json.dumps(data), content_type='application/json')
                request.user = SimpleNamespace(is_authenticated=False)
                request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20)
                request._dont_enforce_csrf_checks = True
                with patch('enrolment_api.auth._employs', return_value=True): return signer(request, pk=self.learner.pk)
            return self.request(signer, 'POST', data, pk=self.learner.pk)

    def test_all_four_first_issues_accept_outstanding_signatures_without_changing_r1(self):
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement'):
            with self.subTest(key=key):
                document, original = self.unsigned(key)
                frozen = copy.deepcopy(original.data); checksum = original.checksum
                self.assertTrue(revisions.content_locked(key, document))
                for party in seed(key)['signatureRoles']:
                    self.result(self.sign(key, party))
                    document.refresh_from_db()
                    self.assertEqual(getattr(document, party + '_signature'), fixtures.MARK)
                    # History must not allow an existing mark to be removed or overwritten.
                    self.result(self.sign(key, party), 409)
                    self.result(self.sign(key, party, ''), 409)
                self.assertTrue(document.fully_signed)
                original.refresh_from_db()
                self.assertEqual(original.data, frozen); self.assertEqual(original.checksum, checksum)
                self.assertTrue(revisions.content_locked(key, document))

    def test_finalized_successor_and_superseded_original_remain_locked(self):
        key = 'training-plan'
        document, _ = self.unsigned(key)
        detail = self.draft(document, key)
        self.finalize(self.save_revision(detail, 'sign-again'))
        self.result(self.sign(key, 'apprentice'), 409)
        document.status = document.STATUS_SUPERSEDED
        self.assertTrue(revisions.signing_locked(key, document, 'apprentice', fixtures.MARK))

    def test_discarding_a_draft_does_not_leave_the_first_issue_unsignable(self):
        key = 'written-agreement'
        document, original = self.unsigned(key)
        draft = Revision.objects.get(issue=original.issue, state='draft')
        revisions.discard(draft, 'Synthetic Admin', draft.edit_version)
        self.result(self.sign(key, 'provider'))
        self.result(self.sign(key, 'learner'))
        document.refresh_from_db()
        self.assertTrue(document.provider_signature); self.assertTrue(document.learner_signature)
        self.assertFalse(document.fully_signed)

    @override_settings(DOCUMENT_BRANDED_PDF_ENABLED=True)
    def test_reviewed_provider_signature_appends_pdf_and_preserves_unsigned_original(self):
        from . import admin_preview, pdf_artifacts as pdf
        from learner_api import written_agreement
        key = 'written-agreement'
        document, original = self.unsigned(key)
        unsigned = pdf.artifact(original.issue, original.data)
        self.assertIsNotNone(unsigned)
        frozen_pdf = copy.deepcopy(unsigned.payload)
        frozen_revision = copy.deepcopy(original.data)
        request = self.factory.get('/', {'sourceId': str(document.pk)})
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=False)
        preview = self.result(admin_preview.document_preview(request, pk=self.learner.pk, key=key))
        with patch.object(written_agreement, 'advance_learner', return_value=None):
            signed = self.result(self.request(admin_preview.sign_reviewed_document, 'POST', {
                'sourceId': str(document.pk), 'reviewToken': preview['previewToken'], 'party': 'provider',
                'name': 'Synthetic Provider', 'signature': fixtures.MARK,
            }, pk=self.learner.pk, key=key))['document']
        self.assertTrue(signed['signatures']['provider']['signed'])
        current = pdf.artifact(original.issue, signed)
        self.assertIsNotNone(current); self.assertNotEqual(current.fingerprint, unsigned.fingerprint)
        unsigned.refresh_from_db(); original.refresh_from_db()
        self.assertEqual(unsigned.payload, frozen_pdf); self.assertEqual(original.data, frozen_revision)

    def test_latest_signed_revision_satisfies_requirements_without_signing_old_versions(self):
        from learner_api.learner_progression import learner_signed_compliance_documents, compliance_document_state
        from enrolment_api.pending_signatures import pending_for
        from . import revision_views as views
        originals = []
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement'):
            document, original = self.unsigned(key)
            frozen = copy.deepcopy(original.data)
            older = self.finalize(self.save_revision(self.draft(document, key), 'sign-again'))
            latest = self.finalize(self.save_revision(self.draft(document, key), 'sign-again'))
            party = 'apprentice' if key in ('apprenticeship-agreement', 'training-plan') else 'learner'
            _, _, signer = revisions.source_api(key)
            module = __import__(signer.__module__, fromlist=['advance_learner'])
            body = {'party': party, 'signature': fixtures.MARK, 'name': 'Latest version learner'}
            with patch.object(module, 'advance_learner', return_value=None):
                self.result(self.learner_request(views.sign_revision, 'POST', body, identifier=older['revision']['id']), 409)
                self.result(self.learner_request(views.sign_revision, 'POST', body, identifier=latest['revision']['id']))
            document.refresh_from_db(); original.refresh_from_db()
            self.assertFalse(getattr(document, party + '_signature'))
            self.assertEqual(original.data, frozen)
            originals.append(document)
        self.assertTrue(learner_signed_compliance_documents('apprenticeship', self.learner.pk))
        from learner_api.calendar import _may_book_first_session_early
        self.learner.programme_status = 'Delivery'; self.learner.save()
        self.assertTrue(_may_book_first_session_early('apprenticeship', self.learner))
        self.assertFalse(any(compliance_document_state('apprenticeship', self.learner.pk).values()))  # Other parties still pending.
        self.assertEqual(pending_for(self.learner.pk, include_reviews=False)['results'], [])
        # Draft R4 does not replace signed R3. Issuing it creates exactly one
        # new obligation, without resurrecting R1/R2/R3 signature requests.
        key = 'training-plan'; document = originals[2]
        draft = self.save_revision(self.draft(document, key), 'sign-again')
        self.assertTrue(learner_signed_compliance_documents('apprenticeship', self.learner.pk))
        issued = self.finalize(draft)
        self.assertFalse(learner_signed_compliance_documents('apprenticeship', self.learner.pk))
        self.assertFalse(_may_book_first_session_early('apprenticeship', self.learner))
        pending = pending_for(self.learner.pk, include_reviews=False)['results']
        self.assertEqual(len(pending), 1); self.assertEqual(pending[0]['revisionId'], issued['revision']['id'])
