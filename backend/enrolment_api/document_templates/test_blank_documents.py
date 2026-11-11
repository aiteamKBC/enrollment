"""Create is empty and unsigned; Edit uses the explicitly selected immutable base."""
import copy
import json
import os
import uuid
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from . import revisions, revision_views as views, service
from .models import LearnerDocumentRevision as Revision, TemplateVersion, RenderHistory
from .registry import seed, REVIEW_KEYS
from . import test_revisions as helpers
from .test_revisions import MARK


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'})
class BlankDocumentTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.RevisionTests.setUp
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

    def create(self, source, key='apprenticeship-agreement', request_id=None, status=201):
        return self.result(self.request(views.start_revision, 'POST', {'sourceId': str(source.pk), 'mode': 'blank',
            'requestId': request_id or str(uuid.uuid4())}, pk=self.learner.pk, key=key), status)

    def save_blank(self, detail, content=None, method='sign-again', status=200):
        return self.result(self.request(views.revision_detail, 'PUT', {'editVersion': detail['revision']['editVersion'],
            'content': content or detail['preview']['template']['content'], 'reason': 'New learner document', 'signatureMethod': method},
            pk=self.learner.pk, identifier=detail['revision']['id']), status)

    def authored(self, detail):
        content = copy.deepcopy(detail['preview']['template']['content'])
        content['blocks'] = [{'id': 'new-wording', 'type': 'paragraph', 'text': 'New terms for {{learner.fullName}}'},
            {'id': 'new-table', 'type': 'table', 'columns': ['Item', 'Value'], 'rows': [['New item', 'New value']]}]
        content['blocks'] += [{'id': 'sign-' + role, 'type': 'signature', 'role': role, 'label': role + ' signature'} for role in content['signatureRoles']]
        return content

    def test_create_empty_pinned_unsigned_and_hidden_then_save_resume_discard_and_next_number(self):
        doc, version = self.original(); before = revisions.serialize_source('apprenticeship-agreement', doc, self.learner)
        self.publish({**seed('apprenticeship-agreement'), 'title': 'New global wording'})
        request_id = str(uuid.uuid4()); detail = self.create(doc, request_id=request_id)
        self.assertEqual(detail['revision']['number'], 2)
        self.assertEqual(detail['revision']['creationMode'], 'blank')
        self.assertEqual(detail['preview']['template']['id'], version['id'])
        self.assertEqual(detail['preview']['template']['content']['blocks'], [{'id': 'blank-page', 'type': 'paragraph', 'text': ''}])
        self.assertFalse(detail['revision']['fullySigned']); self.assertTrue(detail['revision']['canDiscard'])
        self.assertEqual(detail['revision']['provenance'], {})
        self.assertNotIn(MARK, json.dumps(detail))
        self.assertEqual(detail['preview']['data']['marks'], {'apprentice': '', 'employer': ''})
        original = Revision.objects.get(issue_id=detail['revision']['issueId'], number=1)
        original_before = copy.deepcopy(original.content)
        empty = copy.deepcopy(detail['preview']['template']['content']); empty['blocks'] = []
        detail = self.save_blank(detail, empty)
        self.assertEqual(detail['preview']['template']['content']['blocks'], [{'id': 'blank-page', 'type': 'paragraph', 'text': ''}])
        saved = self.save_blank(detail, self.authored(detail))
        self.assertEqual(saved['revision']['creationMode'], 'blank')
        retry = self.create(doc, request_id=request_id)
        self.assertEqual(retry['revision']['id'], detail['revision']['id'])
        self.assertEqual(retry['preview']['template']['content'], saved['preview']['template']['content'])
        own = self.result(self.learner_request(views.document_history, key='apprenticeship-agreement'))
        self.assertEqual([r['number'] for r in own['results']], [1])
        self.result(self.learner_request(views.revision_detail, identifier=detail['revision']['id']), 404)
        self.result(self.learner_request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'mode': 'blank', 'requestId': str(uuid.uuid4())}, key='apprenticeship-agreement'), 403)
        self.assertEqual(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'mode': 'blank', 'requestId': str(uuid.uuid4())}, csrf=False, pk=self.learner.pk, key='apprenticeship-agreement').status_code, 403)
        self.create(doc, status=409)  # Another intent must never duplicate or replace the current draft.
        self.result(self.request(views.discard_revision, 'DELETE', {'sourceId': str(doc.pk), 'editVersion': saved['revision']['editVersion']},
            pk=self.learner.pk, key='apprenticeship-agreement', identifier=detail['revision']['id']))
        self.create(doc, request_id=request_id, status=409)  # Deleted request identity cannot be reused.
        following = self.create(doc); self.assertEqual(following['revision']['number'], 3)
        original.refresh_from_db(); self.assertEqual(original.content, original_before)
        doc.refresh_from_db(); self.assertEqual(revisions.serialize_source('apprenticeship-agreement', doc, self.learner), before)
        self.assertEqual(TemplateVersion.objects.count(), 2); self.assertEqual(RenderHistory.objects.count(), 0)

    def test_finalise_requires_real_body_and_required_signature_blocks_then_requests_fresh_signatures(self):
        from enrolment_api import pending_signatures
        doc, _ = self.original(); self.learner.onboarding_status = 'Submitted'; self.learner.save()
        detail = self.create(doc)
        self.save_blank(detail, method='keep', status=400)
        def finish_response(value):
            token = self.result(self.request(views.preview_revision, 'POST', {'editVersion': value['revision']['editVersion']}, pk=self.learner.pk, identifier=value['revision']['id']))['previewToken']
            return self.request(views.finalize_revision, 'POST', {'previewToken': token}, pk=self.learner.pk, identifier=value['revision']['id'])
        self.result(finish_response(detail), 400)
        signatures_only = self.authored(detail); signatures_only['blocks'] = signatures_only['blocks'][2:]
        saved = self.save_blank(detail, signatures_only); self.result(finish_response(saved), 400)
        saved = self.save_blank(saved, self.authored(detail))
        request = self.factory.get('/'); request.user = SimpleNamespace(is_authenticated=False)
        request.login_account = SimpleNamespace(role='learner', is_active=True, subject_type='learner', subject_id=self.learner.pk)
        self.assertEqual(self.result(pending_signatures.pending_signatures(request))['count'], 0)
        final = self.result(finish_response(saved)); self.assertEqual(final['revision']['status'], 'Available')
        self.assertFalse(final['revision']['fullySigned']); self.assertNotIn(MARK, json.dumps(final))
        self.assertEqual(self.result(pending_signatures.pending_signatures(request))['results'][0]['revisionId'], final['revision']['id'])
        signing = revisions.source_record('apprenticeship-agreement', self.learner.pk, Revision.objects.get(pk=final['revision']['id']).signing_source_id)
        self.assertEqual(signing.apprentice_name, ''); self.assertEqual(signing.doc_path, '')
        self.result(self.request(views.revision_detail, 'PUT', {}, pk=self.learner.pk, identifier=final['revision']['id']), 409)
        self.result(self.learner_request(views.revision_detail, identifier=final['revision']['id']))
        with patch('learner_api.learning_plan.promote_to_delivery_if_ready', return_value=None):
            self.result(self.learner_request(views.sign_revision, 'POST', {'party': 'apprentice', 'name': 'Fresh learner', 'signature': MARK}, identifier=final['revision']['id']))
            employer = self.factory.post('/', data=json.dumps({'party': 'employer', 'name': 'Fresh employer', 'signature': MARK}), content_type='application/json')
            employer.user = SimpleNamespace(is_authenticated=False); employer.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); employer._dont_enforce_csrf_checks = True
            with patch('enrolment_api.auth._employs', return_value=True): complete = self.result(views.sign_revision(employer, pk=self.learner.pk, identifier=final['revision']['id']))
        self.assertEqual(complete['revision']['status'], 'Signed')
        self.assertEqual(self.result(pending_signatures.pending_signatures(request))['count'], 0)
        self.assertEqual(complete['preview']['data']['template']['content'], saved['preview']['template']['content'])

    def test_exact_selected_old_version_not_latest_and_conflicting_draft_never_silently_resumed(self):
        doc, _ = self.original(); first = self.draft(doc)
        original = Revision.objects.get(issue_id=first['revision']['issueId'], number=1)
        newer = self.finalize(self.save_revision(first))
        selected = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': str(original.pk)}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        self.assertEqual(selected['revision']['baseId'], str(original.pk))
        self.assertEqual(selected['preview']['template']['content'], original.content)
        self.assertEqual(selected['preview']['data']['marks']['apprentice'], MARK)
        self.assertNotEqual(selected['preview']['template']['content'], newer['preview']['template']['content'])
        retry = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': str(original.pk)}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        self.assertEqual(retry['revision']['id'], selected['revision']['id'])
        self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': newer['revision']['id']}, pk=self.learner.pk, key='apprenticeship-agreement'), 409)
        self.create(doc, status=409)

    def test_all_eight_types_create_and_issue_without_copying_prior_content_or_signatures(self):
        from learner_api.models import EnrolmentReview
        from learner_api import review_form
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            with self.subTest(key=key), ExitStack() as stack:
                stack.enter_context(patch.object(review_form, '_skills_radar_context', return_value={}))
                stack.enter_context(patch.object(review_form, '_plr_context', return_value={}))
                stack.enter_context(patch('learner_api.learning_plan.promote_to_delivery_if_ready', return_value=None))
                self.publish(seed(key))
                if key.startswith('review-'):
                    doc = EnrolmentReview.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk, event_key='blank:' + key,
                        review_type=seed(key)['reviewType'], form_answers={'previous': {'answer': 'Never copy this answer'}}, form_completed=True,
                        learner_signature=MARK, learner_signed_name='Historical', learner_signed_at=timezone.now())
                    binding = service.bind_review_issue(doc, self.learner); data = revisions.serialize_source(key, doc, self.learner)
                    binding.review_snapshot = {k: copy.deepcopy(data[k]) for k in service.REVIEW_SNAPSHOT_KEYS if k in data}; binding.save()
                else:
                    model, _, _ = revisions.source_api(key)
                    doc = model.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk)
                    service.bind_issue(key, doc, self.learner, revisions.serialize_source(key, doc, self.learner))
                before = revisions.serialize_source(key, doc, self.learner)
                detail = self.create(doc, key)
                self.assertEqual(detail['preview']['template']['content']['blocks'][0]['text'], '')
                final = self.finalize(self.save_blank(detail, self.authored(detail)))
                self.assertEqual(final['revision']['status'], 'Available')
                self.assertEqual(final['preview']['data'].get('answers', {}), {})
                self.assertNotIn(MARK, json.dumps(final)); self.assertEqual(final['revision']['provenance'], {})
                for role in revisions.required_roles(key, final['preview']['data']):
                    signing_data = {'party': role, 'name': 'Fresh ' + role, 'signature': MARK}
                    if role in ('learner', 'apprentice'):
                        signed = self.result(self.learner_request(views.sign_revision, 'POST', signing_data, identifier=final['revision']['id']))
                    elif role == 'employer':
                        employer = self.factory.post('/', data=json.dumps(signing_data), content_type='application/json')
                        employer.user = SimpleNamespace(is_authenticated=False); employer.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); employer._dont_enforce_csrf_checks = True
                        with patch('enrolment_api.auth._employs', return_value=True): signed = self.result(views.sign_revision(employer, pk=self.learner.pk, identifier=final['revision']['id']))
                    else:
                        signed = self.result(self.request(views.sign_revision, 'POST', signing_data, pk=self.learner.pk, identifier=final['revision']['id']))
                self.assertEqual(signed['revision']['status'], 'Signed')
                self.assertEqual(signed['preview']['template']['content'], final['preview']['template']['content'])
                doc.refresh_from_db(); self.assertEqual(revisions.serialize_source(key, doc, self.learner), before)

    def test_mode_request_identity_source_and_learner_scope_are_validated(self):
        from learner_api.models import EnrolmentUser, ApprenticeshipAgreement
        doc, _ = self.original()
        for extra in ({'mode': 'unsupported'}, {'mode': 'blank'}, {'mode': 'blank', 'requestId': 'invalid'},
                      {'mode': 'blank', 'requestId': str(uuid.uuid4()), 'baseRevisionId': str(uuid.uuid4())}):
            self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), **extra}, pk=self.learner.pk, key='apprenticeship-agreement'), 400)
        other = EnrolmentUser.all_learners.create(username='Other', email='other@example.test', learner_type='apprenticeship')
        foreign = ApprenticeshipAgreement.objects.create(learner_kind='apprenticeship', learner_id=other.pk)
        self.create(foreign, status=400)
        self.assertEqual(Revision.objects.count(), 0)

    def test_new_dynamic_fields_use_current_learner_dates_and_edits_keep_their_own_snapshot(self):
        doc, _ = self.original()
        historical_name = self.learner.username
        self.learner.username = 'Current learner name'
        self.learner.learner_start_date = date(2031, 2, 1)
        self.learner.learner_end_date = date(2033, 2, 1)
        self.learner.save()
        detail = self.create(doc)
        fields = detail['preview']['template']['fields']
        self.assertEqual(fields['learner.fullName'], 'Current learner name')
        self.assertEqual(fields['enrolment.startDate'], '2031-02-01')
        self.assertEqual(fields['enrolment.endDate'], '2033-02-01')
        original = Revision.objects.get(issue_id=detail['revision']['issueId'], number=1)
        self.assertEqual(revisions.preview_response(original)['template']['fields']['learner.fullName'], historical_name)
        final = self.finalize(self.save_blank(detail, self.authored(detail)))
        self.learner.username = 'Changed after issue'; self.learner.save()
        edited = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': final['revision']['id']}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        self.assertEqual(edited['preview']['template']['fields'], fields)
        self.assertEqual(edited['preview']['template']['content'], final['preview']['template']['content'])
