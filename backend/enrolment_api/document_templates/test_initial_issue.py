"""Real first Issue APIs, shared purple selection, immutable original artifacts."""
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import patch
from . import test_learner_documents as learner_fixtures
from . import service, original_pdf, views as template_views, revisions
from .registry import seed, REVIEW_KEYS
from .models import IssuedTemplate, RenderHistory, TemplateVersion, LearnerDocumentRevision
from learner_api import apprenticeship_agreement as aa, ilr_document as ilr, training_plan_document as tp, written_agreement as wa, review_form as review
from learner_api.models import EnrolmentReview

PDF = b'%PDF-1.4\nsynthetic original artifact for storage tests\n%%EOF'


class InitialIssueTests(learner_fixtures.LearnerDocumentTests):
    def api(self, view, method='GET', data=None, **kwargs):
        request = self.factory.generic(method, '/isolated-first-issue/', data=json.dumps(data or {}), content_type='application/json', HTTP_X_DOCUMENT_ISSUE_MODE='initial')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=False, get_username=lambda: 'synthetic-admin')
        if view.__name__ in ('issue_onboarding_review', 'onboarding_reviews', 'enrolment_review_form'):
            request.login_account = SimpleNamespace(role='staff', is_active=True, display_name='Synthetic Admin', email='admin@example.test')
        request._dont_enforce_csrf_checks = True
        return view(request, **kwargs)

    def first(self, key, learner=None):
        learner = learner or self.learner
        if key.startswith('review-'):
            result = self.result(self.api(review.issue_onboarding_review, 'POST', {'type': seed(key)['reviewType']}, kind='apprenticeship', pk=learner.pk), 201)
            return result['issuedDocumentSnapshot']
        view, field = {'apprenticeship-agreement': (aa.issue_agreement, 'agreement'), 'ilr': (ilr.issue_ilr, 'document'),
                       'training-plan': (tp.issue_training_plan, 'document'), 'written-agreement': (wa.issue_written_agreement, 'document')}[key]
        return self.result(self.api(view, 'POST', pk=learner.pk), 201)[field]

    def test_all_eight_first_issues_are_purple_retry_safe_originals_with_unchanged_create_resolution(self):
        # Only synthetic seed rows; bypass unrelated optional review-detail tables.
        EnrolmentReview.objects.filter(learner_id=self.learner.pk)._raw_delete(using='enrolment')
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            with self.subTest(key=key):
                before = copy.deepcopy(service.applicable(key, self.learner))
                document = self.first(key)
                self.assertEqual(document['template']['content']['designTheme'], 'purple-reference-v2')
                self.assertEqual(document['template']['number'], 0)
                self.assertEqual(service.applicable(key, self.learner), before)  # Create's resolver unchanged.
                self.assertEqual(document['template']['content']['blocks'], seed(key)['blocks'])
                self.assertEqual(self.first(key)['template']['issueId'], document['template']['issueId'])
                self.assertNotIn('AUDIT_B_', json.dumps(document))
                self.assertFalse(document.get('fullySigned', False))
                issue = IssuedTemplate.objects.get(pk=document['template']['issueId'])
                self.assertEqual(issue.source_id, str(revisions.source_record(key, self.learner.pk).pk))
                if key.startswith('review-'):
                    self.assertIsNone(revisions.source_record(key, self.learner.pk).started_at)
        self.assertEqual(IssuedTemplate.objects.count(), 8)
        self.assertEqual(TemplateVersion.objects.count(), 0)
        self.assertEqual(LearnerDocumentRevision.objects.count(), 0)  # No R2 to change the design.

    def test_explicit_published_classic_requires_actual_publication_and_is_never_mutated(self):
        key='written-agreement'; old=self.publish(seed(key))
        content={**seed(key),'designTheme':'purple-reference-v2'}; draft=self.save(content)
        response=self.api(wa.issue_written_agreement,'POST',pk=self.learner.pk)
        blocked=self.result(response,409);self.assertTrue(blocked['publicationRequired'])
        self.assertEqual(IssuedTemplate.objects.count(),0)
        self.assertNotIn('designTheme',TemplateVersion.objects.get(pk=old['id']).content)
        preview=self.result(self.request(template_views.preview,'POST',{'revision':draft['revision']},identifier=key))
        published=self.result(self.request(template_views.publish,'POST',{'revision':draft['revision'],'previewToken':preview['previewToken'],
            'reviewConfirmed':True,'approvalNote':'Isolated fixture approval'},identifier=key),201)['published']
        document=self.first(key)
        self.assertEqual(document['template']['id'],published['id'])
        self.assertEqual(document['template']['content']['designTheme'],'purple-reference-v2')

    def test_published_programme_variant_is_selected_without_changing_content(self):
        import uuid
        key='training-plan';content={**seed(key),'designTheme':'purple-reference-v2','programmes':['ME_L4'],'templateId':key+'@'+str(uuid.uuid4())}
        selected=self.publish(content);document=self.first(key)
        self.assertEqual(document['template']['id'],selected['id'])
        self.assertEqual(document['template']['content'],TemplateVersion.objects.get(pk=selected['id']).content)

    def test_original_pdf_bytes_are_immutable_and_scoped_without_overwriting_signed_files(self):
        document=self.first('training-plan');issue=document['template']['issueId']
        encoded=base64.b64encode(PDF).decode()
        body={'snapshot':document,'pdf':encoded}
        stored=self.result(self.api(original_pdf.original_pdf,'POST',body,pk=self.learner.pk,issue_id=issue),201)
        row=RenderHistory.objects.get(issue_id=issue,payload__kind='original-issued-pdf-v1');before=copy.deepcopy(row.payload)
        self.result(self.api(original_pdf.original_pdf,'POST',{'pdf':base64.b64encode(PDF+b'changed').decode()},pk=self.learner.pk,issue_id=issue))
        row.refresh_from_db();self.assertEqual(row.payload,before)
        result=self.api(original_pdf.original_pdf,pk=self.learner.pk,issue_id=issue)
        self.assertEqual(result.content,PDF);self.assertEqual(result['Content-Type'],'application/pdf')
        self.assertTrue(stored['sha256']);self.assertEqual(RenderHistory.objects.filter(payload__kind='original-issued-pdf-v1').count(),1)
        # A fresh independent original cannot alter this old artifact.
        other=self.first('written-agreement');other_issue=other['template']['issueId']
        self.result(self.api(original_pdf.original_pdf,'POST',{'pdf':encoded,'snapshot':other},pk=self.learner.pk,issue_id=other_issue),201)
        self.assertEqual(self.api(original_pdf.original_pdf,pk=self.learner.pk,issue_id=issue).content,PDF)
        self.result(self.api(original_pdf.original_pdf,pk=self.people[1].pk,issue_id=issue),404)
        self.result(self.api(original_pdf.original_pdf,'POST',{'pdf':encoded,'snapshot':{}},pk=self.learner.pk,issue_id=other_issue)) # Existing artifact wins on retry.

    def test_original_pdf_rejects_stale_snapshots_signatures_bad_bytes_and_learner_writes(self):
        document=self.first('ilr');issue=document['template']['issueId'];body={'pdf':base64.b64encode(PDF).decode(),'snapshot':document}
        self.result(self.api(original_pdf.original_pdf,'POST',{**body,'snapshot':{}},pk=self.learner.pk,issue_id=issue),409)
        self.result(self.api(original_pdf.original_pdf,'POST',{**body,'pdf':'bad'},pk=self.learner.pk,issue_id=issue),400)
        self.result(self.learner_request(original_pdf.original_pdf,'POST',body,issue_id=issue),403)
        source=revisions.source_record('ilr',self.learner.pk);source.learner_signature='data:image/png;base64,old';source.save()
        signed=revisions.serialize_source('ilr',source,self.learner)
        self.result(self.api(original_pdf.original_pdf,'POST',{**body,'snapshot':signed},pk=self.learner.pk,issue_id=issue),409)
        self.assertEqual(RenderHistory.objects.filter(payload__kind='original-issued-pdf-v1').count(),0)
        # A delayed upload of the frozen unsigned ORIGINAL remains safe after signing.
        self.result(self.api(original_pdf.original_pdf,'POST',body,pk=self.learner.pk,issue_id=issue),201)
        stored=RenderHistory.objects.get(issue_id=issue,payload__kind='original-issued-pdf-v1')
        self.assertFalse(stored.payload['snapshot']['signatures']['learner']['signed'])

    def test_retry_and_new_issuance_leave_old_signed_source_and_pdf_checksum_unchanged(self):
        import hashlib
        from django.utils import timezone
        document=self.first('apprenticeship-agreement');issue=document['template']['issueId']
        body={'pdf':base64.b64encode(PDF).decode(),'snapshot':document}
        self.result(self.api(original_pdf.original_pdf,'POST',body,pk=self.learner.pk,issue_id=issue),201)
        old=revisions.source_record('apprenticeship-agreement',self.learner.pk)
        old.apprentice_signature=learner_fixtures.fixtures.MARK;old.apprentice_signed_name='Historical learner';old.apprentice_signed_at=timezone.now()
        old.employer_signature=learner_fixtures.fixtures.MARK;old.employer_signed_name='Historical employer';old.employer_signed_at=timezone.now()
        old.fully_signed=True;old.doc_path='unchanged-historical-signed.pdf';old.save()
        before=revisions.serialize_source('apprenticeship-agreement',old,self.learner)
        retry=self.first('apprenticeship-agreement');self.assertEqual(retry['id'],str(old.pk));self.assertTrue(retry['fullySigned'])
        self.first('apprenticeship-agreement',learner=self.people[1])
        old.refresh_from_db();self.assertEqual(revisions.serialize_source('apprenticeship-agreement',old,self.learner),before)
        self.assertEqual(hashlib.sha256(self.api(original_pdf.original_pdf,pk=self.learner.pk,issue_id=issue).content).hexdigest(),hashlib.sha256(PDF).hexdigest())
