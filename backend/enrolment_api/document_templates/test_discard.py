"""Draft discard is a scoped, audited state transition, never a hard delete."""
import copy
import io
import json
import os
from unittest.mock import patch, MagicMock

from django.core.management import call_command, CommandError
from django.test import TestCase, SimpleTestCase, override_settings
from . import revisions, revision_views as views, service
from .models import LearnerDocumentRevision as Revision, IssuedTemplate, TemplateVersion
from . import test_revisions as helpers


def stored_values(row):
    return {field.attname: copy.deepcopy(getattr(row, field.attname)) for field in row._meta.concrete_fields}


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'})
class DiscardDraftTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = helpers.RevisionTests.setUp
    request = helpers.RevisionTests.request
    result = helpers.RevisionTests.result
    payload = helpers.RevisionTests.payload
    publish = helpers.RevisionTests.publish
    save = helpers.RevisionTests.save
    original = helpers.RevisionTests.original
    draft = helpers.RevisionTests.draft
    save_revision = helpers.RevisionTests.save_revision
    finalize = helpers.RevisionTests.finalize
    learner_request = helpers.RevisionTests.learner_request

    def discard(self, detail, **overrides):
        revision = detail['revision']
        kwargs = {'pk': self.learner.pk, 'key': revision['documentKey'], 'identifier': revision['id']}
        data = {'sourceId': revision['sourceId'], 'editVersion': revision['editVersion']}
        kwargs.update(overrides.pop('route', {})); data.update(overrides.pop('data', {}))
        return self.request(views.discard_revision, 'DELETE', data, **kwargs, **overrides)

    def test_discard_keeps_original_signatures_template_and_audit_and_next_number(self):
        doc, version = self.original()
        before = revisions.serialize_source('apprenticeship-agreement', doc, self.learner)
        detail = self.draft(doc); identifier = detail['revision']['id']
        self.assertTrue(detail['revision']['canDiscard'])
        original = Revision.objects.get(issue_id=detail['revision']['issueId'], number=1)
        frozen_original = stored_values(original)
        self.result(self.discard(detail))
        row = Revision.objects.get(pk=identifier)
        self.assertEqual(row.state, 'discarded')
        self.assertIsNone(row.finalized_at)
        self.assertEqual(row.changes['discardedDraft']['by'], 'django:synthetic-admin')
        self.assertTrue(row.changes['discardedDraft']['at'])
        self.assertEqual(row.content, detail['preview']['template']['content'])
        original.refresh_from_db(); self.assertEqual(stored_values(original), frozen_original)
        doc.refresh_from_db(); self.assertEqual(revisions.serialize_source('apprenticeship-agreement', doc, self.learner), before)
        self.assertEqual(TemplateVersion.objects.get(pk=version['id']).number, 1)
        self.assertEqual(IssuedTemplate.objects.count(), 1)
        rows = self.result(self.request(views.document_history, pk=self.learner.pk, key='apprenticeship-agreement'))['results']
        self.assertEqual([r['number'] for r in rows], [1])
        new = self.draft(doc)
        self.assertEqual(new['revision']['number'], 3)
        self.assertNotEqual(new['revision']['id'], identifier)
        self.assertEqual(new['revision']['baseId'], str(original.pk))
        self.assertTrue(new['revision']['canDiscard'])
        self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': identifier}, pk=self.learner.pk, key='apprenticeship-agreement'), 400)  # A discarded source must not silently open unrelated R3.

    def test_repeated_discard_is_idempotent_and_preserves_first_actor_and_time(self):
        doc, _ = self.original(); detail = self.draft(doc)
        self.assertFalse(self.result(self.discard(detail))['alreadyDiscarded'])
        row = Revision.objects.get(pk=detail['revision']['id']); before = stored_values(row)
        self.assertTrue(self.result(self.discard(detail))['alreadyDiscarded'])
        row.refresh_from_db(); self.assertEqual(stored_values(row), before)

    def test_discarded_draft_cannot_be_read_edited_previewed_finalised_or_used_as_base(self):
        doc, _ = self.original(); detail = self.draft(doc); identifier = detail['revision']['id']
        self.result(self.discard(detail))
        for view, method in ((views.revision_detail,'GET'), (views.revision_detail,'PUT'), (views.preview_revision,'POST'), (views.finalize_revision,'POST')):
            with self.subTest(view=view.__name__, method=method):
                self.result(self.request(view, method, {}, pk=self.learner.pk, identifier=identifier), 404)
        self.result(self.request(views.start_revision, 'POST', {'sourceId': str(doc.pk), 'baseRevisionId': identifier}, pk=self.learner.pk, key='apprenticeship-agreement'), 400)

    def test_original_finalised_partly_signed_and_fully_signed_copies_cannot_be_discarded(self):
        doc, _ = self.original(); detail = self.draft(doc)
        original = Revision.objects.get(issue_id=detail['revision']['issueId'], number=1)
        self.result(self.discard(detail, route={'identifier':str(original.pk)}), 409)
        self.assertFalse(revisions.summary(original)['canDiscard'])
        for method in ('keep','sign-again'):
            current = self.draft(doc); final = self.finalize(self.save_revision(current, method))
            self.result(self.discard(final), 409)
            self.assertFalse(final['revision']['canDiscard'])
            if method == 'sign-again':
                row = Revision.objects.get(pk=final['revision']['id'])
                clone = type(doc).objects.get(pk=row.signing_source_id)
                clone.apprentice_signature = helpers.MARK; clone.apprentice_signed_name = 'New signer'; clone.save()
                self.result(self.discard(final), 409)
                clone.employer_signature = helpers.MARK; clone.fully_signed = True; clone.save()
                self.result(self.discard(final), 409)

    def test_draft_with_signing_request_or_unexpected_new_signature_is_not_eligible(self):
        doc, _ = self.original(); detail = self.draft(doc)
        row = Revision.objects.get(pk=detail['revision']['id'])
        for change in ('signing-source', 'new-mark', 'new-signer', 'finalized-time'):
            with self.subTest(change=change):
                row.refresh_from_db(); before = copy.deepcopy(row.__dict__)
                if change == 'signing-source': row.signing_source_id = str(doc.pk)
                if change == 'new-mark': row.data = copy.deepcopy(row.data); row.data.setdefault('marks', {})['apprentice'] = 'newly-captured'
                if change == 'new-signer': row.data = copy.deepcopy(row.data); row.data['signatures']['apprentice']['name'] = 'New independent signer'
                if change == 'finalized-time': row.finalized_at = row.created_at
                row.save()
                self.assertFalse(revisions.summary(row)['canDiscard'])
                self.result(self.discard(detail), 409)
                row.__dict__.update(before); row.save()

    def test_wrong_learner_document_and_source_never_discard_anything(self):
        doc, _ = self.original(); detail = self.draft(doc)
        for overrides in ({'route':{'pk':self.learner.pk+99}}, {'route':{'key':'ilr'}}, {'data':{'sourceId':'wrong-source'}}):
            with self.subTest(overrides=overrides): self.result(self.discard(detail, **overrides), 404)
        self.assertEqual(Revision.objects.get(pk=detail['revision']['id']).state, 'draft')

    def test_stale_confirmation_is_rejected_and_draft_remains_editable(self):
        doc, _ = self.original(); detail = self.draft(doc)
        saved = self.save_revision(detail)
        self.result(self.discard(detail), 409)
        self.assertEqual(Revision.objects.get(pk=detail['revision']['id']).state, 'draft')
        self.result(self.discard(saved))

    def test_only_admin_with_csrf_can_discard_and_other_documents_remain(self):
        doc, _ = self.original(); detail = self.draft(doc)
        self.result(self.learner_request(views.discard_revision, 'DELETE', {'sourceId':str(doc.pk), 'editVersion':1}, key='apprenticeship-agreement', identifier=detail['revision']['id']), 403)
        self.assertEqual(self.discard(detail, csrf=False).status_code, 403)
        from learner_api.models import EnrolmentUser, ApprenticeshipAgreement
        other = EnrolmentUser.all_learners.create(username='Other learner',email='other@example.test',learner_type='apprenticeship')
        other_doc = ApprenticeshipAgreement.objects.create(learner_kind='apprenticeship',learner_id=other.pk)
        other_draft = revisions.create_draft('apprenticeship-agreement',str(other_doc.pk),other,'other-admin')
        before = stored_values(other_draft)
        self.result(self.discard(detail))
        other_draft.refresh_from_db(); self.assertEqual(stored_values(other_draft), before)
        self.assertEqual(Revision.objects.filter(state='draft').count(),1)


class DiscardDeploymentTests(SimpleTestCase):
    def test_forward_plan_never_connects_and_does_not_replay_historical_sql(self):
        from enrolment_api.management.commands import apply_document_templates as command
        output = io.StringIO()
        with patch.object(command, 'connections') as connections:
            call_command('apply_document_revision_discard','--plan',stdout=output)
        connections.__getitem__.assert_not_called()
        self.assertIn('Learner_Document_Revisions', output.getvalue())
        self.assertNotIn('Document_Template_Versions', output.getvalue())

    def test_check_requires_discard_constraints_and_is_read_only(self):
        from enrolment_api.management.commands import apply_document_templates as command
        db = MagicMock(vendor='postgresql',settings_dict={'HOST':'synthetic.example.test'})
        cursor = db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('present',)
        cursor.fetchall.return_value = []
        with patch.object(command, 'connections',{'enrolment':db}), patch.dict(os.environ,{'DOCUMENT_TEMPLATE_DATABASE_URL':''}), self.assertRaises(CommandError):
            call_command('apply_document_revision_discard','--check',stdout=io.StringIO())
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list))
