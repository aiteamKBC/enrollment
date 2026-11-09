"""Real API + existing source signers, entirely in the isolated SQLite runner."""
import copy
import json
import os
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from . import service, revisions, revision_views as views
from .models import LearnerDocumentRevision as Revision, RenderHistory, TemplateVersion
from .registry import seed, validate, TemplateError
from . import test_templates as helpers

MARK = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKAAAAAyCAIAAABUA0cyAAAB2UlEQVR4nO2a247DIAxE41X//5eptKgRSgKYq53RnIcqahNiPIxx2koI4SC4/FkHQNZCgcGhwOBQYHAoMDgUGBwKDA4FBocCg0OBwaHA4FBgcCgwOJ/uK0UkHvD3KEAHn+pejglUiaZ3MQWOlo3qxleaGL/JatJYfhymyH8A5mH4Eji1b+Q8VmbKSUIlUddJSH4d/LrNWFw+AqxYZ9I0w7t99Z+m55yY5Fdu6moiXx1Mlb7w+p+Dc4hkF02aWauqKHnvFiJfFEMuksdLChkrjNAwpSaDPp52GcHEN5K56YainSo08S7ldTnZwQVremhkJL+kFhWVDVtSxXLKWza57X7yoz8GHdxaUUXh0SlFxUOf0ebgvmnfBZg11TOD+lRKSwXu2Ixdibq2yUrLXczUxNJXbpFym5yo1b1ErgxGObgJ2ml0RF+df0fZ11vw/maY1E4u6pXe5OBI6t2RRHQMcjlTGktujDy96hVm7RHY8BuAiY8uYaCPGx/ElsrSHhe47B7Dp9Iq2773MHPwFPt2t6PmyQ3WAXjfg1vxIy0SHw+7L6XFdzBdu1XgnfaltEtBaBRJAf7xHRwKDA4FBocCg0OBwaHA4FBgcCgwOBQYHAoMDgUGhwIf2HwBUvYjiNlMxu4AAAAASUVORK5CYII='


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'})
class RevisionTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.TemplateWorkflowTests.setUp
    request = helpers.TemplateWorkflowTests.request
    payload = helpers.TemplateWorkflowTests.payload
    result = helpers.TemplateWorkflowTests.payload
    save_template = helpers.TemplateWorkflowTests.save
    publish = helpers.TemplateWorkflowTests.publish
    save = helpers.TemplateWorkflowTests.save  # helper used by publish

    def original(self, key='apprenticeship-agreement'):
        from learner_api.models import ApprenticeshipAgreement
        version = self.publish(seed(key))
        doc = ApprenticeshipAgreement.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk,
            apprentice_name='Historical Learner', employer_name='Historical Employer', standard='Historical Standard',
            apprentice_signature=MARK, apprentice_signed_name='Historical Learner', apprentice_signed_at=timezone.now(),
            employer_signature=MARK, employer_signed_name='Historical Employer', employer_signed_at=timezone.now(),
            fully_signed=True, doc_path='original-unchanged.pdf', blob_name='original-blob')
        service.bind_issue(key, doc, self.learner, revisions.serialize_source(key, doc, self.learner))
        return doc, version

    def draft(self, doc, key='apprenticeship-agreement'):
        return self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk)}, pk=self.learner.pk, key=key), 201)

    def save_revision(self, detail, method='keep'):
        content = copy.deepcopy(detail['preview']['template']['content'])
        content['blocks'].append({'id': 'learner-amendment-' + str(detail['revision']['number']), 'type': 'paragraph', 'text': 'Revised wording only for this learner.'})
        return self.result(self.request(views.revision_detail, 'PUT', {'editVersion': detail['revision']['editVersion'],
            'content': content, 'reason': 'Correct learner-specific wording', 'signatureMethod': method}, pk=self.learner.pk, identifier=detail['revision']['id']))

    def finalize(self, detail):
        token = self.result(self.request(views.preview_revision, 'POST', {'editVersion': detail['revision']['editVersion']}, pk=self.learner.pk, identifier=detail['revision']['id']))['previewToken']
        return self.result(self.request(views.finalize_revision, 'POST', {'previewToken': token}, pk=self.learner.pk, identifier=detail['revision']['id']))

    def learner_request(self, view, method='GET', data=None, learner_id=None, **kwargs):
        request = self.factory.generic(method, '/', data=json.dumps(data or {}), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = SimpleNamespace(role='learner', is_active=True, subject_type='learner', subject_id=learner_id or self.learner.pk)
        request._dont_enforce_csrf_checks = True
        return view(request, pk=self.learner.pk, **kwargs)

    def test_keep_preserves_original_pinned_template_marks_and_no_new_signature_events(self):
        doc, version = self.original(); before = revisions.serialize_source('apprenticeship-agreement', doc, self.learner)
        content = seed('apprenticeship-agreement'); content['title'] = 'Global V2'; self.publish(content)
        detail = self.draft(doc)
        self.assertEqual(detail['preview']['data']['marks']['apprentice'], MARK)
        self.assertEqual(detail['preview']['template']['id'], version['id'])
        finalized = self.finalize(self.save_revision(detail))
        self.assertEqual(finalized['revision']['status'], 'Updated Copy')
        self.assertFalse(finalized['revision']['fullySigned'])
        self.assertFalse(finalized['preview']['data']['independentlySigned'])
        self.assertEqual(finalized['preview']['data']['signatureProvenance']['apprentice']['sourceRevision'], 1)
        self.assertEqual(finalized['preview']['data']['marks']['employer'], MARK)
        self.assertEqual(RenderHistory.objects.count(), 0)
        self.assertEqual(TemplateVersion.objects.count(), 2)
        doc.refresh_from_db(); self.assertEqual(revisions.serialize_source('apprenticeship-agreement', doc, self.learner), before)
        self.assertEqual(doc.doc_path, 'original-unchanged.pdf')
        rows = self.result(self.learner_request(views.document_history, key='apprenticeship-agreement'))['results']
        self.assertEqual([r['number'] for r in rows], [1, 2]); self.assertEqual([r['status'] for r in rows], ['Signed', 'Updated Copy'])
        self.assertTrue(all(r['templateVersionNumber'] == 1 for r in rows))
        read = self.result(self.learner_request(views.revision_detail, identifier=finalized['revision']['id']))
        self.assertEqual(read['preview']['data']['marks']['employer'], MARK)

    def test_access_drafts_hidden_other_learners_denied_and_finalized_content_locked(self):
        doc, _ = self.original(); detail = self.draft(doc); identifier = detail['revision']['id']
        self.result(self.learner_request(views.revision_detail, identifier=identifier), 404)
        self.result(self.learner_request(views.revision_detail, 'PUT', {}, identifier=identifier), 403)
        final = self.finalize(self.save_revision(detail))
        self.result(self.learner_request(views.revision_detail, identifier=identifier, learner_id=self.learner.pk + 100), 404)
        self.result(self.request(views.revision_detail, 'PUT', {}, pk=self.learner.pk, identifier=identifier), 409)
        self.result(self.request(views.sign_revision, 'POST', {'party': 'provider', 'name': 'No', 'signature': MARK}, pk=self.learner.pk, identifier=identifier), 400)
        new = self.draft(doc); self.assertEqual(new['revision']['number'], 3)
        self.assertEqual(new['revision']['baseId'], final['revision']['id'])
        self.assertEqual(new['preview']['data']['signatureProvenance']['apprentice']['sourceRevision'], 1)

    def test_reason_signature_choice_cas_preview_and_csrf_required(self):
        doc, _ = self.original(); detail = self.draft(doc); identifier = detail['revision']['id']
        self.result(self.request(views.revision_detail, 'PUT', {'editVersion': 1, 'content': detail['preview']['template']['content']}, pk=self.learner.pk, identifier=identifier), 400)
        saved = self.save_revision(detail)
        self.result(self.request(views.revision_detail, 'PUT', {'editVersion': 1}, pk=self.learner.pk, identifier=identifier), 409)
        self.result(self.request(views.finalize_revision, 'POST', {'previewToken': 'forged'}, pk=self.learner.pk, identifier=identifier), 400)
        self.assertEqual(self.request(views.finalize_revision, 'POST', {}, csrf=False, pk=self.learner.pk, identifier=identifier).status_code, 403)
        self.assertEqual(saved['revision']['editVersion'], 2)

    def test_other_learners_documents_are_unchanged_and_foreign_source_is_refused(self):
        from learner_api.models import EnrolmentUser, ApprenticeshipAgreement
        doc, _ = self.original()
        other = EnrolmentUser.all_learners.create(username='Other synthetic learner', email='other@example.test', learner_type='apprenticeship')
        foreign = ApprenticeshipAgreement.objects.create(learner_kind='apprenticeship', learner_id=other.pk,
            apprentice_name='Other learner', apprentice_signature=MARK, apprentice_signed_name='Other learner', apprentice_signed_at=timezone.now())
        service.bind_issue('apprenticeship-agreement', foreign, other, revisions.serialize_source('apprenticeship-agreement', foreign, other))
        before = revisions.serialize_source('apprenticeship-agreement', foreign, other)
        self.result(self.request(views.start_revision, 'POST', {'sourceId': str(foreign.pk)}, pk=self.learner.pk, key='apprenticeship-agreement'), 400)
        self.finalize(self.save_revision(self.draft(doc)))
        foreign.refresh_from_db(); self.assertEqual(revisions.serialize_source('apprenticeship-agreement', foreign, other), before)
        self.assertFalse(Revision.objects.filter(issue__learner_id=other.pk).exists())

    def test_missing_storage_has_safe_readiness_and_cannot_silently_save(self):
        with override_settings(DOCUMENT_REVISIONS_ENABLED=False):
            data = self.result(self.request(views.document_history, pk=self.learner.pk, key='ilr'))
            self.assertTrue(data['setupRequired']); self.assertEqual(data['results'], [])
            self.result(self.request(views.start_revision, 'POST', {'sourceId': 'x'}, pk=self.learner.pk, key='ilr'), 503)

    def test_employer_cannot_read_ilr_and_unrelated_staff_grants_are_denied(self):
        request = self.factory.get('/'); request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20)
        with patch('enrolment_api.auth._employs', return_value=True): self.assertEqual(views.document_history(request, pk=self.learner.pk, key='ilr').status_code, 404)
        request.login_account = SimpleNamespace(role='staff', is_active=True, subject_id=20)
        with patch('enrolment_api.auth._accesses_of', return_value=frozenset({'curriculum'})):
            self.assertEqual(views.document_history(request, pk=self.learner.pk, key='ilr').status_code, 404)

    def test_sign_again_uses_existing_signer_roles_and_locks_after_all_fresh_marks(self):
        from learner_api import apprenticeship_agreement as source
        doc, _ = self.original(); finalized = self.finalize(self.save_revision(self.draft(doc), 'sign-again'))
        identifier = finalized['revision']['id']; revision = Revision.objects.get(pk=identifier)
        self.assertEqual(finalized['revision']['status'], 'Available')
        self.assertFalse(finalized['preview']['data']['marks']['apprentice'])
        self.assertNotEqual(revision.signing_source_id, str(doc.pk))
        with patch.object(source, 'advance_learner', return_value=None):
            self.result(self.request(views.sign_revision, 'POST', {'party': 'apprentice', 'name': 'Staff must not impersonate', 'signature': MARK}, pk=self.learner.pk, identifier=identifier), 403)
            first = self.result(self.learner_request(views.sign_revision, 'POST', {'party': 'apprentice', 'name': 'Fresh Learner', 'signature': MARK}, identifier=identifier))
            self.assertFalse(first['revision']['fullySigned'])
            with patch('enrolment_api.auth._employs', return_value=True):
                request = self.factory.post('/', data=json.dumps({'party': 'employer', 'name': 'Fresh Employer', 'signature': MARK}), content_type='application/json')
                request.user = SimpleNamespace(is_authenticated=False); request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); request._dont_enforce_csrf_checks = True
                final = self.result(views.sign_revision(request, pk=self.learner.pk, identifier=identifier))
            self.assertEqual(final['revision']['status'], 'Signed')
            self.assertNotIn('signatureProvenance', final['preview']['data'])
            self.result(self.learner_request(views.sign_revision, 'POST', {'party': 'apprentice', 'name': 'Changed', 'signature': MARK}, identifier=identifier), 409)
            self.result(self.request(source.sign_agreement, 'POST', {'party': 'apprentice', 'name': 'Overwrite', 'signature': ''}, pk=self.learner.pk), 409)
        doc.refresh_from_db(); self.assertEqual(doc.apprentice_signed_name, 'Historical Learner'); self.assertTrue(doc.fully_signed)
        self.assertEqual(RenderHistory.objects.count(), 2)

    def test_rich_structure_validation_is_controlled_and_safe(self):
        content = seed('ilr'); content['blocks'].append({'id': 'rich-new', 'type': 'paragraph', 'text': 'Bold text', 'runs': [{'text': 'Bold ', 'bold': True, 'underline': True}, {'text': 'text', 'italic': True}]})
        content['pageLayout'] = {'size': 'a4', 'margins': dict(top=20, right=18, bottom=20, left=18), 'header': '{{learner.fullName}}', 'footer': 'Document', 'pageNumbers': True}
        self.assertEqual(validate(content, publishing=True)['blocks'][-1]['runs'][0]['bold'], True)
        content['blocks'][-1]['runs'][0]['onclick'] = 'alert(1)'
        with self.assertRaises(TemplateError): validate(content)

    def test_keep_and_fresh_signing_for_other_three_existing_compliance_models(self):
        from .preview import sample
        from learner_api import ilr_document as ilr, training_plan_document as tp, written_agreement as wa
        for key, module, issuer in [('ilr', ilr, ilr.issue_ilr), ('training-plan', tp, tp.issue_training_plan), ('written-agreement', wa, wa.issue_written_agreement)]:
            with self.subTest(key=key), ExitStack() as stack:
                data = sample(key, [])[1]; self.publish(seed(key))
                stack.enter_context(patch.object(module, 'advance_learner', return_value=None))
                if key == 'ilr':
                    stack.enter_context(patch.object(module, 'derive_learner_details', return_value=data['learnerDetails']))
                    stack.enter_context(patch.object(module, 'derive_answers', return_value=data['answers']))
                else: stack.enter_context(patch.object(module, 'derive_content', return_value=data))
                issued = self.result(self.request(issuer, 'POST', pk=self.learner.pk), 201)['document']
                doc = revisions.source_record(key, self.learner.pk, issued['id'])
                roles = seed(key)['signatureRoles']
                for role in roles:
                    setattr(doc, role + '_signature', MARK); setattr(doc, role + '_signed_name', 'Historical ' + role); setattr(doc, role + '_signed_at', timezone.now())
                doc.recalculate_signed(); doc.save(); original_data = revisions.serialize_source(key, doc, self.learner)
                kept = self.finalize(self.save_revision(self.draft(doc, key)))
                self.assertEqual(kept['revision']['status'], 'Updated Copy')
                fresh_draft = self.draft(doc, key); fresh = self.finalize(self.save_revision(fresh_draft, 'sign-again'))
                identifier = fresh['revision']['id']
                for role in roles:
                    if role in {'learner', 'apprentice'}:
                        response = self.learner_request(views.sign_revision, 'POST', {'party': role, 'name': 'Fresh learner', 'signature': MARK}, identifier=identifier)
                    elif role == 'employer':
                        request = self.factory.post('/', data=json.dumps({'party': role, 'name': 'Fresh employer', 'signature': MARK}), content_type='application/json'); request.user = SimpleNamespace(is_authenticated=False)
                        request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); request._dont_enforce_csrf_checks = True
                        with patch('enrolment_api.auth._employs', return_value=True): response = views.sign_revision(request, pk=self.learner.pk, identifier=identifier)
                    else: response = self.request(views.sign_revision, 'POST', {'party': role, 'name': 'Fresh provider', 'signature': MARK}, pk=self.learner.pk, identifier=identifier)
                    latest = self.result(response)
                self.assertEqual(latest['revision']['status'], 'Signed')
                doc.refresh_from_db(); self.assertEqual(revisions.serialize_source(key, doc, self.learner), original_data)

    def test_all_four_reviews_keep_frozen_answers_and_complete_fresh_signatures(self):
        from learner_api import review_form as source
        from learner_api.models import EnrolmentReview
        from .registry import REVIEW_KEYS
        for review_type, key in REVIEW_KEYS.items():
            with self.subTest(key=key), ExitStack() as stack:
                stack.enter_context(patch.object(source, '_skills_radar_context', return_value={}))
                stack.enter_context(patch.object(source, '_plr_context', return_value={}))
                stack.enter_context(patch('learner_api.learning_plan.promote_to_delivery_if_ready', return_value=None))
                self.publish(seed(key))
                doc = EnrolmentReview.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk, event_key='test:' + key,
                    review_type=review_type, form_answers={'original': {'answer': 'Retain this answer'}}, form_completed=True,
                    learner_signature=MARK, learner_signed_name='Historical learner', learner_signed_at=timezone.now(),
                    admin_signature=MARK, admin_signed_name='Historical admin', admin_signed_at=timezone.now(),
                    employer_signature=MARK, employer_signed_name='Historical employer', employer_signed_at=timezone.now())
                binding = service.bind_review_issue(doc, self.learner)
                data = source._serialize_form(doc, self.learner, None)
                binding.review_snapshot = {k: copy.deepcopy(data[k]) for k in service.REVIEW_SNAPSHOT_KEYS if k in data}; binding.save()
                original = source._serialize_form(doc, self.learner, None)
                keep = self.finalize(self.save_revision(self.draft(doc, key)))
                self.assertEqual(keep['preview']['data']['answers'], original['answers'])
                self.assertEqual(keep['revision']['status'], 'Updated Copy')
                fresh = self.finalize(self.save_revision(self.draft(doc, key), 'sign-again'))
                identifier = fresh['revision']['id']
                self.result(self.learner_request(views.sign_revision, 'POST', {'party': 'learner', 'name': 'Fresh learner', 'signature': MARK}, identifier=identifier))
                self.result(self.request(views.sign_revision, 'POST', {'party': 'admin', 'name': 'Fresh admin', 'signature': MARK}, pk=self.learner.pk, identifier=identifier))
                request = self.factory.post('/', data=json.dumps({'party': 'employer', 'name': 'Fresh employer', 'signature': MARK}), content_type='application/json')
                request.user = SimpleNamespace(is_authenticated=False); request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); request._dont_enforce_csrf_checks = True
                with patch('enrolment_api.auth._employs', return_value=True): complete = self.result(views.sign_revision(request, pk=self.learner.pk, identifier=identifier))
                self.assertEqual(complete['revision']['status'], 'Signed')
                doc.refresh_from_db(); self.assertEqual(source._serialize_form(doc, self.learner, None), original)
                self.assertTrue(revisions.content_locked(key, revisions.source_record(key, self.learner.pk, Revision.objects.get(pk=identifier).signing_source_id)))
