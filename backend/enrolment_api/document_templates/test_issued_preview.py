"""Admin previews through real issuers/signers, isolated SQLite fixtures only."""
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from . import admin_preview, revisions, service
from . import test_initial_issue as fixtures
from .models import IssuedTemplate, RenderHistory, LearnerDocumentRevision, TemplateVersion
from .registry import seed, REVIEW_KEYS
from learner_api.models import EnrolmentReview


class AdminPreviewTests(fixtures.InitialIssueTests):
    @classmethod
    def setUpClass(cls):
        from coach_api.models import CoachCalendarEvent
        with connection.cursor() as cursor:
            if 'Coach' not in {row[1] for row in cursor.execute('PRAGMA database_list').fetchall()}:
                cursor.execute("ATTACH DATABASE ':memory:' AS Coach")
            exists = cursor.execute("SELECT name FROM Coach.sqlite_master WHERE type='table' AND name='coach_calendar_event'").fetchone()
        if not exists:
            with connection.schema_editor() as editor:
                editor.create_model(CoachCalendarEvent)
                editor.deferred_sql.clear()
        super().setUpClass()

    def preview(self, key, source=None, *, learner=None, staff=True):
        pk = (learner or self.learner).pk
        query = {'sourceId': str(source)} if source is not None else {'phase': 'pre-issue'}
        request = self.factory.get('/isolated-preview/', query)
        request.user = SimpleNamespace(is_authenticated=True, is_staff=staff, is_superuser=False, get_username=lambda: 'isolated-admin')
        return admin_preview.document_preview(request, pk=pk, key=key)

    def sign(self, key, source, token, *, data=None):
        values = {'sourceId': str(source), 'reviewToken': token, 'party': 'admin' if key.startswith('review-') else 'provider',
                  'name': 'Isolated Admin', 'signature': fixtures.learner_fixtures.fixtures.MARK, **(data or {})}
        request = self.factory.post('/isolated-reviewed-sign/', json.dumps(values), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=False, get_username=lambda: 'isolated-admin')
        request._dont_enforce_csrf_checks = True
        # Real review signer requires the established login-account role too.
        if key.startswith('review-'):
            request.login_account = SimpleNamespace(role='staff', is_active=True, subject_id=1, display_name='Isolated Admin')
        with patch('enrolment_api.auth._accesses_of', return_value=frozenset({'enrolment'})):
            return admin_preview.sign_reviewed_document(request, pk=self.learner.pk, key=key)

    def test_all_eight_pre_issue_previews_are_read_only_real_data_and_current_published_not_draft(self):
        EnrolmentReview.objects.filter(learner_id=self.learner.pk)._raw_delete(using='enrolment')
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            with self.subTest(key=key):
                content = {**seed(key), 'designTheme': 'purple-reference-v2', 'title': 'Published ' + key}
                published = self.publish(content)
                self.save({**content, 'title': 'UNPUBLISHED DO NOT USE'})
                counts = (IssuedTemplate.objects.count(), RenderHistory.objects.count(), LearnerDocumentRevision.objects.count())
                value = self.result(self.preview(key))
                self.assertEqual(value['template']['id'], published['id'])
                self.assertIn('PRE-ISSUE PREVIEW', value['notice'])
                self.assertFalse(value['sample']); self.assertIsNone(value['previewToken'])
                self.assertIn(self.learner.username, json.dumps(value))
                self.assertNotIn('AUDIT_B_', json.dumps(value)); self.assertNotIn('UNPUBLISHED DO NOT USE', json.dumps(value))
                self.assertTrue(all(not state.get('signed') for state in value['data']['signatures'].values() if isinstance(state, dict)))
                self.assertEqual(counts, (IssuedTemplate.objects.count(), RenderHistory.objects.count(), LearnerDocumentRevision.objects.count()))
        self.assertEqual(EnrolmentReview.objects.filter(learner_id=self.learner.pk).count(), 0)

    def test_all_eight_issued_previews_preserve_snapshots_signatures_and_status_without_starting_reviews(self):
        EnrolmentReview.objects.filter(learner_id=self.learner.pk)._raw_delete(using='enrolment')
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            document = self.first(key)
            source = revisions.source_record(key, self.learner.pk)
            before = {f.attname: copy.deepcopy(getattr(source, f.attname)) for f in source._meta.concrete_fields}
            counts = (RenderHistory.objects.count(), LearnerDocumentRevision.objects.count())
            value = self.result(self.preview(key, source.pk))
            self.assertEqual(value['data'], document)
            self.assertTrue(value['previewToken']); self.assertIn('Issued original R1', value['notice'])
            source.refresh_from_db(); self.assertEqual({f.attname: getattr(source, f.attname) for f in source._meta.concrete_fields}, before)
            self.assertEqual(counts, (RenderHistory.objects.count(), LearnerDocumentRevision.objects.count()))
        self.assertEqual(EnrolmentReview.objects.filter(learner_id=self.learner.pk, started_at__isnull=False).count(), 0)

    def test_stored_pdf_used_only_for_exact_original_and_not_after_signature_changes(self):
        doc = self.first('ilr'); source = revisions.source_record('ilr', self.learner.pk)
        self.result(self.api(fixtures.original_pdf.original_pdf, 'POST', {'snapshot': doc, 'pdf': base64.b64encode(fixtures.PDF).decode()},
                             pk=self.learner.pk, issue_id=doc['template']['issueId']), 201)
        value = self.result(self.preview('ilr', source.pk)); self.assertTrue(value['pdfUrl'].endswith('/original-pdf/'))
        self.result(self.sign('ilr', source.pk, value['previewToken']))
        signed = self.result(self.preview('ilr', source.pk))
        self.assertIsNone(signed['pdfUrl']); self.assertTrue(signed['data']['signatures']['provider']['signed'])
        self.assertEqual(RenderHistory.objects.filter(payload__kind='original-issued-pdf-v1').get().payload['pdf'], base64.b64encode(fixtures.PDF).decode())

    def test_real_provider_signers_use_reviewed_snapshot_and_reject_stale_missing_or_foreign_tokens(self):
        for key in ('ilr', 'training-plan', 'written-agreement'):
            document = self.first(key); source = revisions.source_record(key, self.learner.pk)
            preview = self.result(self.preview(key, source.pk))
            self.result(self.sign(key, source.pk, ''), 409)
            self.result(self.sign(key, source.pk, 'tampered'), 409)
            result = self.result(self.sign(key, source.pk, preview['previewToken']))
            self.assertTrue(result['document']['signatures']['provider']['signed'])
            self.result(self.sign(key, source.pk, preview['previewToken']), 409)
            # Current profile edits never alter issued compliance particulars.
            saved = self.result(self.preview(key, source.pk))['data']
            self.learner.username = 'Changed profile after issue'; self.learner.save()
            self.assertEqual(self.result(self.preview(key, source.pk))['data'], saved)

    def test_real_review_signer_is_guarded_without_opening_the_form(self):
        key = 'review-eligibility'
        source = EnrolmentReview.objects.get(learner_id=self.learner.pk, review_type=seed(key)['reviewType'])
        service.bind_review_issue(source, self.learner)
        for role in ('learner', 'admin', 'employer'):
            setattr(source, role + '_signature', '')
        source.form_completed = True; source.save()
        preview = self.result(self.preview(key, source.event_key))
        self.result(self.sign(key, source.event_key, preview['previewToken']))
        source.refresh_from_db(); self.assertTrue(source.admin_signature)
        self.assertTrue(self.result(self.preview(key, source.event_key))['data']['signatures']['admin']['signed'])

    def test_replaced_issued_source_foreign_learner_and_draft_cannot_be_substituted(self):
        key = 'training-plan'; self.first(key); source = revisions.source_record(key, self.learner.pk)
        preview = self.result(self.preview(key, source.pk))
        other = self.first(key, self.people[1]); other_source = revisions.source_record(key, self.people[1].pk)
        self.result(self.preview(key, other_source.pk), 409)
        self.result(self.sign(key, other_source.pk, preview['previewToken']), 409)
        # New global publications and unissued learner drafts cannot change row preview.
        detail = self.create(source)
        same = self.result(self.preview(key, source.pk))
        self.assertNotIn('documentRevision', same['data'])
        self.assertEqual(preview['data'], same['data'])
        self.assertEqual(LearnerDocumentRevision.objects.get(pk=detail['revision']['id']).state, 'draft')
        source.status = 'superseded'; source.save()
        self.result(self.preview(key, source.pk), 409)
        self.result(self.sign(key, source.pk, preview['previewToken']), 409)

    def test_non_admin_access_and_pre_issue_race_are_refused(self):
        self.result(self.preview('ilr', staff=False), 403)
        self.first('ilr'); self.result(self.preview('ilr'), 409)
        req = self.factory.get('/', {'phase': 'pre-issue'})
        req.login_account = SimpleNamespace(role='learner', is_active=True, subject_id=self.people[1].pk)
        self.assertEqual(admin_preview.document_preview(req, pk=self.learner.pk, key='ilr').status_code, 404)
        self.assertEqual(admin_preview.document_preview(req, pk=self.people[1].pk, key='ilr').status_code, 403)

    def test_legacy_signed_review_without_frozen_content_fails_closed(self):
        source = EnrolmentReview.objects.filter(learner_id=self.learner.pk).first()
        source.admin_signature = 'data:image/png;base64,old'; source.save()
        key = REVIEW_KEYS[source.review_type]
        self.result(self.preview(key, source.pk), 409)
