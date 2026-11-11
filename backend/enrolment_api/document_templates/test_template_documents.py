"""Create uses latest published questions with no learner answers; Edit is historical."""
import copy
import uuid
from contextlib import ExitStack
from unittest.mock import patch
from django.test import TestCase, override_settings
from . import test_blank_documents as helpers, service, revisions, revision_views as views
from .models import LearnerDocumentRevision as Revision, TemplateVersion, TemplateDraft
from .registry import seed, REVIEW_KEYS, PROGRAMMES


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
class TemplateDocumentTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.BlankDocumentTests.setUp
    request = helpers.BlankDocumentTests.request
    payload = helpers.BlankDocumentTests.payload
    result = helpers.BlankDocumentTests.result
    publish = helpers.BlankDocumentTests.publish
    save = helpers.BlankDocumentTests.save
    original = helpers.BlankDocumentTests.original
    save_blank = helpers.BlankDocumentTests.save_blank
    finalize = helpers.BlankDocumentTests.finalize
    learner_request = helpers.BlankDocumentTests.learner_request

    def create(self, doc, key='apprenticeship-agreement', request_id=None, status=201):
        return self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'mode': 'template',
            'requestId': request_id or str(uuid.uuid4())}, pk=self.learner.pk, key=key), status)

    def latest_content(self, key='apprenticeship-agreement'):
        content = seed(key)
        content['title'] = 'Latest published contract'
        content['blocks'].append({'id': 'latest-question', 'type': 'field', 'label': 'Latest contract question?', 'value': 'Configured answer to clear'})
        content['blocks'].append({'id': 'latest-choices', 'type': 'questions', 'fields': [{
            'id': 'latest-radio', 'label': 'Latest choice question?', 'type': 'radio', 'helpText': 'Current help text',
            'required': False, 'condition': None, 'options': [{'id': 'Yes', 'label': 'Yes'}, {'id': 'No', 'label': 'No'}],
            'value': '', 'defaultOptionIds': ['Yes'], 'systemLinked': False, 'binding': None}]})
        return content

    def test_latest_published_questions_and_empty_answers_are_pinned_without_changing_original(self):
        doc, old = self.original(); latest = self.publish(self.latest_content())
        # Unpublished wording is not a usable contract template.
        draft = self.latest_content(); draft['title'] = 'Unpublished changes'; self.save(draft)
        request_id = str(uuid.uuid4()); detail = self.create(doc, request_id=request_id)
        self.assertEqual(detail['revision']['creationMode'], 'template')
        self.assertEqual(detail['revision']['templateVersionId'], latest['id'])
        self.assertEqual(detail['revision']['templateVersionNumber'], 2)
        content = detail['preview']['template']['content']
        self.assertEqual(content['title'], 'Latest published contract')
        question = next(b for b in content['blocks'] if b['id'] == 'latest-question')
        self.assertEqual(question['label'], 'Latest contract question?'); self.assertEqual(question['value'], '')
        self.assertEqual(question['fields'][0]['value'], '')
        choices = next(b for b in content['blocks'] if b['id'] == 'latest-choices')['fields'][0]
        self.assertEqual(choices['defaultOptionIds'], []); self.assertEqual(len(choices['options']), 2)
        self.assertTrue(all(value == '' for value in detail['preview']['template']['fields'].values()))
        self.assertEqual(detail['preview']['data']['answers'], {})
        self.assertEqual(detail['revision']['provenance'], {}); self.assertFalse(detail['revision']['fullySigned'])
        self.assertEqual(self.create(doc, request_id=request_id)['revision']['id'], detail['revision']['id'])
        saved = self.save_blank(detail)
        self.assertEqual(saved['preview']['template']['content'], content)
        TemplateVersion.objects.create(template_id='apprenticeship-agreement', document_key='apprenticeship-agreement', number=3,
            content={**self.latest_content(), 'title': 'Later V3'}, content_hash='synthetic-v3', published_by='Other Admin', approval_note='Fixture')
        final = self.finalize(saved)
        self.assertEqual(final['revision']['templateVersionId'], latest['id'])
        signing = Revision.objects.get(pk=final['revision']['id'])
        binding = service.IssuedTemplate.objects.get(document_key='apprenticeship-agreement', source_id=signing.signing_source_id)
        self.assertEqual(str(binding.version_id), latest['id'])
        original = Revision.objects.get(issue_id=detail['revision']['issueId'], number=1)
        self.assertEqual(revisions.preview_response(original)['template']['id'], old['id'])
        edited = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': str(original.pk)}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        self.assertEqual(edited['preview']['template']['id'], old['id'])
        self.assertNotIn('latest-question', [b['id'] for b in edited['preview']['template']['content']['blocks']])
        self.assertEqual(TemplateVersion.objects.count(), 3); self.assertEqual(TemplateDraft.objects.count(), 1)

    def test_matching_latest_programme_variant_is_used_and_can_be_saved_and_issued(self):
        doc, _ = self.original()
        self.learner.programme = PROGRAMMES['ME_L4']; self.learner.save()
        variant = self.latest_content(); variant['templateId'] += '@' + str(uuid.uuid4()); variant['programmes'] = ['ME_L4']
        published = self.publish(variant)
        detail = self.create(doc)
        self.assertEqual(detail['preview']['template']['id'], published['id'])
        self.assertEqual(detail['preview']['template']['content']['templateId'], variant['templateId'])
        final = self.finalize(self.save_blank(detail))
        self.assertEqual(final['revision']['templateVersionId'], published['id'])

    def test_untouched_legacy_empty_draft_is_initialized_in_place_with_audit_and_stale_save_protection(self):
        doc, _ = self.original(); legacy_request = str(uuid.uuid4())
        legacy = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'mode': 'blank', 'requestId': legacy_request}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        self.publish(self.latest_content())
        detail = self.create(doc)
        self.assertEqual(detail['revision']['id'], legacy['revision']['id'])
        self.assertEqual(detail['revision']['number'], legacy['revision']['number'])
        self.assertEqual(detail['revision']['editVersion'], 2)
        self.assertEqual(detail['revision']['changes']['initializedFromEmptyDraft']['requestId'], legacy_request)
        self.assertGreater(len(detail['preview']['template']['content']['blocks']), 1)
        self.result(self.request(views.revision_detail, 'PUT', {'editVersion': 1}, pk=self.learner.pk, identifier=detail['revision']['id']), 409)
        self.create(doc, status=409)

    def test_saved_legacy_empty_draft_is_not_overwritten(self):
        doc, _ = self.original()
        old = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'mode': 'blank', 'requestId': str(uuid.uuid4())}, pk=self.learner.pk, key='apprenticeship-agreement'), 201)
        saved = self.save_blank(old)
        self.create(doc, status=409)
        read = self.result(self.request(views.revision_detail, pk=self.learner.pk, identifier=saved['revision']['id']))
        self.assertEqual(read['preview']['template']['content'], saved['preview']['template']['content'])

    def test_all_eight_document_types_keep_questions_and_render_data_structure_without_saved_answers(self):
        from learner_api import review_form
        from learner_api.models import EnrolmentReview
        for key in ('apprenticeship-agreement', 'ilr', 'training-plan', 'written-agreement', *REVIEW_KEYS.values()):
            with self.subTest(key=key), ExitStack() as stack:
                stack.enter_context(patch.object(review_form, '_skills_radar_context', return_value={}))
                stack.enter_context(patch.object(review_form, '_plr_context', return_value={}))
                self.publish(seed(key))
                if key.startswith('review-'):
                    doc = EnrolmentReview.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship', event_key='template:' + key, review_type=seed(key)['reviewType'], form_completed=True)
                    binding = service.bind_review_issue(doc, self.learner); data = revisions.serialize_source(key, doc, self.learner)
                    binding.review_snapshot = {k: copy.deepcopy(data[k]) for k in service.REVIEW_SNAPSHOT_KEYS if k in data}; binding.save()
                else:
                    model, _, _ = revisions.source_api(key)
                    doc = model.objects.create(learner_id=self.learner.pk, learner_kind='apprenticeship')
                    service.bind_issue(key, doc, self.learner, revisions.serialize_source(key, doc, self.learner))
                detail = self.create(doc, key)
                self.assertEqual([b['id'] for b in detail['preview']['template']['content']['blocks']], [b['id'] for b in seed(key)['blocks']])
                self.assertEqual(detail['preview']['data']['learnerInformation']['name'], '')
                self.assertEqual(detail['preview']['data']['learnerName'], self.learner.username)
                self.assertEqual(detail['preview']['data']['answers'], {})
                self.assertTrue(all(value == '' for value in detail['preview']['template']['fields'].values()))
                self.finalize(self.save_blank(detail))
