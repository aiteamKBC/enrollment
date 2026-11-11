"""Notification API integration: real unmanaged document rows, no live services."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.db import DatabaseError
from django.test import TestCase, override_settings
from enrolment_api import pending_signatures as api
from learner_api.models import EnrolmentReview, EnrolmentUser
from . import test_revisions as helpers, revisions, service
from .models import LearnerDocumentRevision, TemplateDraft
from .registry import seed


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
class PendingSignatureTests(TestCase):
    databases = {'default', 'enrolment'}
    def setUp(self):
        helpers.RevisionTests.setUp(self)
        self.learner.onboarding_status = 'Submitted'; self.learner.save()
    request = helpers.RevisionTests.request
    payload = helpers.RevisionTests.payload
    result = helpers.RevisionTests.result
    publish = helpers.RevisionTests.publish
    save = helpers.RevisionTests.save
    original = helpers.RevisionTests.original
    draft = helpers.RevisionTests.draft
    save_revision = helpers.RevisionTests.save_revision
    finalize = helpers.RevisionTests.finalize
    learner_request = helpers.RevisionTests.learner_request

    def own_request(self, account=None, query='', method='GET'):
        request = self.factory.generic(method, '/enrolment_api/pending-signatures/' + query)
        request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = account or SimpleNamespace(role='learner', is_active=True, subject_type='learner', subject_id=self.learner.pk)
        return api.pending_signatures(request)

    def pending(self): return self.result(self.own_request())

    def document(self, key='apprenticeship-agreement', **extra):
        model, _ = api.COMPLIANCE[key]
        return model.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship', **extra)

    def test_existing_unsigned_documents_need_no_notification_rows_or_reissue(self):
        doc = self.document(); first = self.pending()
        self.assertEqual(first['count'], 1)
        self.assertEqual(first, self.pending())
        self.assertEqual(first['results'][0]['documentId'], str(doc.pk))
        self.assertIn('signature-apprenticeship-agreement-' + str(doc.pk), first['results'][0]['href'])
        self.assertEqual(LearnerDocumentRevision.objects.count(), 0)
        self.assertNotIn(self.learner.username, json.dumps(first))
        self.assertNotIn('data:image/', json.dumps(first))

    def test_personal_signature_not_overall_fully_signed_drives_all_four_models(self):
        for key, (model, role) in api.COMPLIANCE.items():
            with self.subTest(key=key):
                doc = self.document(key)
                # Employer signed / learner unsigned still requires this learner.
                doc.employer_signature = helpers.MARK if hasattr(doc, 'employer_signature') else ''
                if hasattr(doc, 'employer_signed_at'): doc.employer_signed_at = None
                doc.save()
                self.assertEqual(self.pending()['count'], 1)
                setattr(doc, role + '_signature', helpers.MARK); doc.save()
                self.assertFalse(doc.fully_signed)
                self.assertEqual(self.pending()['count'], 0)
                setattr(doc, role + '_signature', '  '); doc.save()
                self.assertEqual(self.pending()['count'], 1)
                doc.delete()

    def test_unissued_template_drafts_preview_and_publication_do_not_notify(self):
        saved = self.save(seed('ilr'))
        self.assertEqual(TemplateDraft.objects.count(), 1)
        self.assertEqual(self.pending()['count'], 0)
        self.assertGreater(saved['revision'], 0)
        self.publish(seed('apprenticeship-agreement'))
        self.assertEqual(self.pending()['count'], 0)

    def test_latest_active_issuance_is_unique_and_superseded_voided_records_disappear(self):
        old = self.document(status='superseded'); current = self.document()
        data = self.pending(); self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['documentId'], str(current.pk))
        self.assertNotEqual(data['results'][0]['documentId'], str(old.pk))
        current.status = 'voided'; current.save()
        self.assertEqual(self.pending()['count'], 0)

    def test_completed_reviews_only_latest_current_instance_and_no_other_party_counts(self):
        row = EnrolmentReview.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk, event_key='onboarding:test/review 1', review_type='workspace', status='issued')
        self.assertEqual(self.pending()['count'], 0)
        row.form_completed = True; row.save()
        item = self.pending()['results'][0]
        self.assertEqual(item['category'], 'Review'); self.assertTrue(item['href'].startswith('/learner/onboarding/reviews?'))
        row.learner_signature = helpers.MARK; row.save()
        self.assertEqual(self.pending()['count'], 0)
        row.learner_signature = ''; row.save()
        newest = EnrolmentReview.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk, event_key='onboarding:new', review_type='workspace', status='issued')
        self.assertEqual(self.pending()['count'], 0)  # Never resurrect the older pending form.
        newest.form_completed = True; newest.save(); self.assertEqual(self.pending()['count'], 1)
        newest.status = 'voided'; newest.save(); self.assertEqual(self.pending()['count'], 0)
        row.status = 'cancelled'; row.save(); newest.status = 'cancelled'; newest.save(); self.assertEqual(self.pending()['count'], 0)

    def test_sign_again_revision_links_exact_copy_and_resolves_after_learner_signs(self):
        from learner_api import apprenticeship_agreement as source
        from . import revision_views
        original, _ = self.original()
        self.assertEqual(self.pending()['count'], 0)
        draft = self.draft(original); self.save_revision(draft)
        self.assertEqual(self.pending()['count'], 0)
        draft = self.result(self.request(revision_views.revision_detail, pk=self.learner.pk, identifier=draft['revision']['id']))
        kept = self.finalize(draft); self.assertEqual(kept['revision']['status'], 'Updated Copy')
        self.assertEqual(self.pending()['count'], 0)
        fresh = self.finalize(self.save_revision(self.draft(original), 'sign-again'))
        requests = self.pending(); self.assertEqual(requests['count'], 1)
        item = requests['results'][0]; self.assertEqual(item['revisionId'], fresh['revision']['id'])
        self.assertEqual(item['templateVersion'], 1)
        self.assertIn('signature-revision-' + fresh['revision']['id'], item['href'])
        request = self.factory.post('/', data=json.dumps({'party': 'apprentice', 'name': 'Synthetic Learner', 'signature': helpers.MARK}), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=False); request.login_account = SimpleNamespace(role='learner', is_active=True, subject_type='learner', subject_id=self.learner.pk); request._dont_enforce_csrf_checks = True
        with patch.object(source, 'advance_learner', return_value=None): self.result(revision_views.sign_revision(request, pk=self.learner.pk, identifier=fresh['revision']['id']))
        self.assertFalse(LearnerDocumentRevision.objects.get(pk=fresh['revision']['id']).data['fullySigned'])
        self.assertEqual(self.pending()['count'], 0)  # Employer still unsigned.
        original.refresh_from_db(); self.assertTrue(original.fully_signed)

    def test_latest_finalized_revision_supersedes_old_request_but_unissued_draft_does_not(self):
        original, _ = self.original()
        first = self.finalize(self.save_revision(self.draft(original), 'sign-again'))
        draft = self.save_revision(self.draft(original), 'sign-again')
        self.assertEqual(self.pending()['results'][0]['revisionId'], first['revision']['id'])
        newest = self.finalize(draft)
        self.assertEqual(self.pending()['count'], 1)
        self.assertEqual(self.pending()['results'][0]['revisionId'], newest['revision']['id'])
        original.status = 'superseded'; original.save()
        self.document()  # Normal re-issuance: the old revision chain is historical.
        self.assertEqual(self.pending()['count'], 1); self.assertIsNone(self.pending()['results'][0]['revisionId'])

    def test_notifications_work_when_optional_template_revision_storage_not_installed(self):
        self.document()
        with override_settings(DOCUMENT_TEMPLATES_ENABLED=False, DOCUMENT_REVISIONS_ENABLED=False):
            self.assertEqual(self.pending()['count'], 1)

    def test_authentication_self_only_no_id_override_and_private_payload(self):
        own = self.document()
        other = EnrolmentUser.all_learners.create(username='Other', email='other@example.test', learner_type='apprenticeship')
        model, _ = api.COMPLIANCE['training-plan']; model.objects.create(learner_kind='apprenticeship', learner_id=other.pk)
        result = self.result(self.own_request(query='?learner_id=' + str(other.pk)))
        self.assertEqual(result['count'], 1); self.assertEqual(result['results'][0]['documentId'], str(own.pk))
        for role in ('admin', 'staff', 'employer'):
            account = SimpleNamespace(role=role, is_active=True, subject_type='staff', subject_id=other.pk)
            self.assertEqual(self.own_request(account).status_code, 403)
        self.assertEqual(self.own_request(SimpleNamespace(role='learner', is_active=False)).status_code, 401)
        dual = self.factory.get('/'); dual.user = SimpleNamespace(is_authenticated=True)
        dual.login_account = SimpleNamespace(role='learner', is_active=False, subject_type='learner', subject_id=self.learner.pk)
        self.assertEqual(api.pending_signatures(dual).status_code, 403)
        self.assertEqual(self.own_request(method='POST').status_code, 405)
        response = self.own_request(); self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['Vary'], 'Cookie')

    def test_lookup_failure_is_retryable_error_not_false_zero_count(self):
        with patch.object(api, 'current_sources', side_effect=DatabaseError('synthetic outage')), patch.object(api.logger, 'exception') as logged:
            result = self.result(self.own_request(), 503)
        logged.assert_called_once()
        self.assertNotIn('count', result)

    def test_read_never_renders_documents_or_retrieves_signature_images(self):
        self.document()
        with patch.object(revisions, 'payload', side_effect=AssertionError('Notification reads must not render documents')):
            self.assertEqual(self.pending()['count'], 1)

    def test_commercial_portal_has_no_apprenticeship_signature_requests(self):
        self.document(); self.learner.learner_type = 'commercial'; self.learner.save()
        self.assertEqual(self.pending()['count'], 0)

    def test_review_notifications_wait_for_the_existing_portal_submission_gate(self):
        EnrolmentReview.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship', event_key='not-yet-accessible', review_type='workspace', status='completed', form_completed=True)
        self.learner.onboarding_status = 'In Progress'; self.learner.save()
        self.assertEqual(self.pending()['count'], 0)
        self.learner.onboarding_status = 'Submitted'; self.learner.save()
        self.assertEqual(self.pending()['count'], 1)

    def test_all_four_review_revision_requests_use_fresh_learner_marks(self):
        from learner_api import review_form as source
        from . import revision_views
        for review_type, key in api.REVIEW_KEYS.items():
            with self.subTest(key=key), patch.object(source, '_skills_radar_context', return_value={}), patch.object(source, '_plr_context', return_value={}), patch('learner_api.learning_plan.promote_to_delivery_if_ready', return_value=None):
                self.publish(seed(key))
                original = EnrolmentReview.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship', event_key='test:' + key,
                    review_type=review_type, status='completed', form_completed=True, learner_signature=helpers.MARK,
                    admin_signature=helpers.MARK, employer_signature=helpers.MARK)
                binding = service.bind_review_issue(original, self.learner)
                data = source._serialize_form(original, self.learner, None)
                binding.review_snapshot = {k: data[k] for k in service.REVIEW_SNAPSHOT_KEYS if k in data}; binding.save()
                revision = self.finalize(self.save_revision(self.draft(original, key), 'sign-again'))
                pending = self.pending(); self.assertEqual(pending['count'], 1)
                self.assertEqual(pending['results'][0]['revisionId'], revision['revision']['id'])
                self.assertTrue(pending['results'][0]['href'].startswith('/learner/onboarding/reviews?signature=signature-revision-'))
                self.result(self.learner_request(revision_views.sign_revision, 'POST', {'party': 'learner', 'name': 'Learner', 'signature': helpers.MARK}, identifier=revision['revision']['id']))
                self.assertEqual(self.pending()['count'], 0)
