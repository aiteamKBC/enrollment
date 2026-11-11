"""Isolated integration of the existing issuers/signatures with template versions.

Run with document_templates.test_settings: no dotenv, network or live database.
"""
import copy
import json
import inspect
import os
import uuid
import io
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.middleware.csrf import _get_new_csrf_string
from django.conf import settings
from django.core.management import call_command, CommandError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from . import service, views
from .models import IssuedTemplate, RenderHistory, TemplateDraft, TemplateVersion
from .preview import render_payload, sample
from .registry import PROGRAMMES, REVIEW_KEYS, TemplateError, digest, legacy_equivalent, seed, seeds, validate


class CatalogueTests(SimpleTestCase):
    def test_all_eight_complete_seeds_validate_and_keep_original_renderer(self):
        self.assertEqual(len(seeds()['templates']), 8)
        self.assertEqual(sum(t['category'] == 'Compliance' for t in seeds()['templates'].values()), 4)
        for key in seeds()['templates']:
            with self.subTest(key=key):
                content = validate(seed(key), publishing=True)
                self.assertTrue(legacy_equivalent(content))
                preview = render_payload(content)
                self.assertTrue(preview['sample'])
                self.assertIn('SAMPLE', json.dumps(preview))
                self.assertEqual(preview['data']['template']['content'], content)

    def test_backend_and_frontend_original_wording_snapshots_are_identical(self):
        root = Path(__file__).resolve().parents[3]
        frontend = root / 'frontend/src/features/compliance-documents/seed.json'
        self.assertEqual(seeds(), json.loads(frontend.read_text(encoding='utf-8')))
        self.assertGreater(len(seed('ilr')['blocks']), 80)
        self.assertGreater(len(seed('training-plan')['blocks']), 60)
        self.assertEqual(len(next(b['labels'] for b in seed('written-agreement')['blocks'] if b.get('source') == 'costs')), 18)

    def test_unknown_fields_expressions_and_unsafe_block_types_are_refused(self):
        for value in ('{{password}}', '{{learner.__class__}}', '{{eval(1)}}', '{{ learner.fullName }}'):
            content = seed('ilr'); content['blocks'][0]['text'] = value
            with self.subTest(value=value), self.assertRaises(TemplateError): validate(content)
        for kind in ('html', 'script', {'type': 'paragraph'}, None):
            content = seed('ilr'); content['blocks'][0]['type'] = kind
            with self.subTest(kind=kind), self.assertRaises(TemplateError): validate(content)

    def test_text_is_plain_and_supported_fields_are_accepted(self):
        content = seed('ilr'); content['blocks'][0]['text'] = '<script>alert(1)</script> {{learner.fullName}}'
        self.assertEqual(validate(content)['blocks'][0]['text'], content['blocks'][0]['text'])
        # These are literal PDF characters, never interpreted HTML or script.

    def test_native_data_and_signature_roles_cannot_be_rewired(self):
        content = seed('training-plan')
        native = next(b for b in content['blocks'] if b['type'] == 'native')
        native['source'] = 'Extended_ILR'
        with self.assertRaises(TemplateError): validate(content)
        content = seed('ilr'); content['blocks'] = [b for b in content['blocks'] if b['type'] != 'signature']
        validate(content)  # Incomplete drafts are allowed, publication isn't.
        with self.assertRaises(TemplateError): validate(content, publishing=True)
        content = seed('ilr'); next(b for b in content['blocks'] if b['type'] == 'signature')['role'] = 'superuser'
        with self.assertRaises(TemplateError): validate(content)

    def test_review_question_keys_and_existing_table_mapping_are_stable(self):
        content = seed('review-eligibility')
        native = next(b for b in content['blocks'] if b.get('labels'))
        name = next(iter(native['labels'])); native['labels'][name] = 'Reviewed wording'
        self.assertEqual(next(b for b in validate(content)['blocks'] if b.get('labels'))['labels'][name], 'Reviewed wording')
        native['labels']['invented-answer-column'] = 'Wrong'
        with self.assertRaises(TemplateError): validate(content)
        content = seed('training-plan'); next(b for b in content['blocks'] if b.get('columns'))['columns'].pop()
        with self.assertRaises(TemplateError): validate(content)

    def test_table_shape_bounds_duplicate_blocks_and_styles(self):
        for block in (
            {'type': 'table', 'columns': ['A'], 'rows': [['A', 'B']]},
            {'type': 'paragraph', 'text': 'x', 'style': {'fontSize': 100}},
            {'type': 'paragraph', 'text': 'x', 'style': {'onclick': 'alert(1)'}},
        ):
            content = seed('ilr'); content['blocks'].append({'id': 'new', **block})
            with self.assertRaises(TemplateError): validate(content)
        content = seed('ilr'); content['blocks'].append(copy.deepcopy(content['blocks'][0]))
        with self.assertRaises(TemplateError): validate(content)

    def test_additive_sql_has_no_existing_table_mutations(self):
        sql = Path(__file__).with_name('schema.sql').read_text()
        self.assertEqual(sql.count('CREATE TABLE IF NOT EXISTS'), 4)
        for forbidden in ('ALTER TABLE', 'DROP TABLE', 'Extended_ILR', 'Created_users'):
            self.assertNotIn(forbidden, sql)
        self.assertIn('BEFORE UPDATE OR DELETE', sql)


class DeploymentCommandTests(SimpleTestCase):
    def invoke(self, *args):
        with patch.dict(os.environ, {'DOCUMENT_TEMPLATE_DATABASE_URL': ''}):
            return call_command('apply_document_templates', *args, stdout=io.StringIO())

    def test_rejects_pooled_schema_writes_before_any_query(self):
        from enrolment_api.management.commands import apply_document_templates as command
        connection = MagicMock(vendor='postgresql', settings_dict={'HOST': 'ep-synthetic-pooler.example.test'})
        with patch.object(command, 'connections', {'enrolment': connection}), self.assertRaises(CommandError): self.invoke()
        connection.cursor.assert_not_called()

    def test_check_is_read_only(self):
        from enrolment_api.management.commands import apply_document_templates as command
        connection = MagicMock(vendor='postgresql', settings_dict={'HOST': 'ep-synthetic.example.test'})
        with patch.object(command, 'connections', {'enrolment': connection}): self.invoke('--check')
        statements = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
        self.assertEqual(len(statements), 4)
        self.assertTrue(all(call.args[0].startswith('SELECT to_regclass') for call in statements))

    def test_dry_run_uses_enrolment_alias_and_rolls_back(self):
        from enrolment_api.management.commands import apply_document_templates as command
        connection = MagicMock(vendor='postgresql', settings_dict={'HOST': 'ep-synthetic.example.test'})
        with patch.object(command, 'connections', {'enrolment': connection}), patch.object(command.transaction, 'atomic') as atomic, patch.object(command.transaction, 'set_rollback') as rollback:
            self.invoke('--dry-run')
        atomic.assert_called_once_with(using='enrolment'); rollback.assert_called_once_with(True, using='enrolment')


@patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'})
class TemplateWorkflowTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.factory = RequestFactory()
        from learner_api.models import EnrolmentUser
        self.learner = EnrolmentUser.all_learners.create(username='SYNTHETIC Test Learner', email='synthetic@example.test',
            learner_type='apprenticeship', programme=PROGRAMMES['ME_L4'], start_date='2030-01-01', end_date='2032-01-01')

    def request(self, view, method='GET', data=None, *, staff=True, csrf=True, **kwargs):
        request = self.factory.generic(method, '/synthetic-documents/', data=json.dumps(data or {}), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=staff, is_superuser=False, get_username=lambda: 'synthetic-admin')
        request._dont_enforce_csrf_checks = csrf
        return view(request, **kwargs)

    def payload(self, response, status=200):
        self.assertEqual(response.status_code, status, response.content.decode())
        return json.loads(response.content)

    def save(self, content, revision=0):
        identifier = content['templateId']
        try: base = service.latest(identifier)[0]['id']
        except TemplateError: base = 'new'
        return self.payload(self.request(views.template, 'PUT', {'content': content, 'revision': revision, 'baseVersion': base}, identifier=identifier))

    def publish(self, content):
        saved = self.save(content)
        preview = self.payload(self.request(views.preview, 'POST', {'revision': saved['revision']}, identifier=content['templateId']))
        result = self.payload(self.request(views.publish, 'POST', {'revision': saved['revision'], 'previewToken': preview['previewToken'],
            'reviewConfirmed': True, 'approvalNote': 'Synthetic integration review'}, identifier=content['templateId']), 201)
        return result['published']

    def test_all_templates_listed_with_category_and_status(self):
        result = self.payload(self.request(views.templates))
        self.assertEqual(len(result['results']), 8)
        self.assertFalse(result['setupRequired'])
        self.assertTrue(all(r['publishedVersion']['number'] == 0 for r in result['results']))

    def test_admin_only_and_csrf_enforced_for_new_writes(self):
        self.assertEqual(self.request(views.templates, staff=False).status_code, 403)
        self.assertEqual(self.request(views.template, 'PUT', {}, csrf=False, identifier='ilr').status_code, 403)
        self.assertEqual(TemplateDraft.objects.count(), 0)

    def test_unauthenticated_and_platform_learner_cannot_read_builder_or_real_data(self):
        request = self.factory.get('/')
        request.user = SimpleNamespace(is_authenticated=False)
        self.assertEqual(views.templates(request).status_code, 401)
        request.login_account = SimpleNamespace(role='learner', is_active=True, subject_id=self.learner.pk)
        self.assertEqual(views.templates(request).status_code, 404)
        self.assertEqual(views.preview(request, identifier='ilr').status_code, 404)

    def test_valid_csrf_token_allows_admin_draft_save(self):
        token = _get_new_csrf_string()
        data = {'content': seed('ilr'), 'baseVersion': service.latest('ilr')[0]['id'], 'revision': 0}
        request = self.factory.put('/synthetic/', data=json.dumps(data), content_type='application/json', HTTP_X_CSRFTOKEN=token)
        request.COOKIES[settings.CSRF_COOKIE_NAME] = token
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=False, get_username=lambda: 'synthetic-admin')
        self.payload(views.template(request, identifier='ilr'))
        self.assertEqual(TemplateDraft.objects.count(), 1)

    def test_non_enrolment_staff_and_employers_are_refused(self):
        for role, grants in [('staff', frozenset({'curriculum'})), ('employer', frozenset())]:
            request = self.factory.get('/')
            request.login_account = SimpleNamespace(role=role, is_active=True, subject_id=1, subject_type='staff')
            with patch('enrolment_api.auth._accesses_of', return_value=grants):
                self.assertEqual(views.templates(request).status_code, 404)

    def test_draft_does_not_publish_or_issue_and_rejects_stale_saves(self):
        content = seed('ilr'); content['title'] = 'Draft only'
        saved = self.save(content)
        self.assertEqual(service.latest('ilr')[0]['number'], 0)
        self.assertEqual(IssuedTemplate.objects.count(), 0)
        self.assertEqual(self.save(content, saved['revision'])['revision'], saved['revision'])
        self.assertEqual(self.request(views.template, 'PUT', {'content': content, 'revision': 0,
            'baseVersion': saved['baseVersion']}, identifier='ilr').status_code, 409)
        detail = self.payload(self.request(views.template, identifier='ilr'))
        self.assertEqual(detail['draft']['title'], 'Draft only')

    def test_saved_preview_uses_sample_and_never_creates_documents_or_signatures(self):
        saved = self.save(seed('ilr'))
        preview = self.payload(self.request(views.preview, 'POST', {'revision': saved['revision']}, identifier='ilr'))
        self.assertTrue(preview['sample']); self.assertTrue(preview['previewToken'])
        self.assertGreater(len(preview['missingFields']), 0)
        self.assertEqual(IssuedTemplate.objects.count(), 0)
        self.assertEqual(RenderHistory.objects.count(), 0)

    def test_explicit_real_data_preview_uses_existing_derive_functions_without_issuing(self):
        from learner_api import apprenticeship_agreement as source
        saved = self.save(seed('apprenticeship-agreement'))
        derived = {**sample('apprenticeship-agreement', [])[1]['particulars'], 'practicalStart': date(2030, 1, 1),
            'practicalEnd': date(2031, 10, 1), 'plannedOtjh': 321}
        with patch.object(source, 'derive_particulars', return_value=derived):
            preview = self.payload(self.request(views.preview, 'POST', {'revision': saved['revision'], 'learnerId': self.learner.pk}, identifier='apprenticeship-agreement'))
        self.assertFalse(preview['sample'])
        self.assertEqual(preview['template']['fields']['document.particulars.plannedOtjHours'], 321)
        self.assertEqual(preview['template']['fields']['document.particulars.practicalEndDate'], '2031-10-01')
        self.assertEqual(IssuedTemplate.objects.count(), 0)

    def test_publish_requires_matching_preview_and_organisational_review(self):
        saved = self.save(seed('ilr'))
        for payload in ({'revision': 1}, {'revision': 1, 'reviewConfirmed': True},
                        {'revision': 1, 'reviewConfirmed': True, 'previewToken': 'forged'}):
            self.assertEqual(self.request(views.publish, 'POST', payload, identifier='ilr').status_code, 400)
        self.assertEqual(TemplateVersion.objects.count(), 0)
        preview = self.payload(self.request(views.preview, 'POST', {'revision': 1}, identifier='ilr'))
        content = seed('ilr'); content['title'] = 'Newer draft'; self.save(content, saved['revision'])
        self.assertEqual(self.request(views.publish, 'POST', {'revision': 2, 'reviewConfirmed': True,
            'previewToken': preview['previewToken']}, identifier='ilr').status_code, 400)

    def test_required_signatures_cannot_be_published_missing(self):
        content = seed('ilr'); content['blocks'] = [b for b in content['blocks'] if b['type'] != 'signature']
        self.save(content)
        preview = self.payload(self.request(views.preview, 'POST', {'revision': 1}, identifier='ilr'))
        self.assertEqual(self.request(views.publish, 'POST', {'revision': 1, 'reviewConfirmed': True,
            'previewToken': preview['previewToken']}, identifier='ilr').status_code, 400)

    def test_history_is_append_only_through_api_and_old_versions_preview(self):
        content = seed('ilr'); first = self.publish(content)
        content['title'] = 'Approved version two'; second = self.publish(content)
        result = self.payload(self.request(views.history, identifier='ilr'))['results']
        self.assertEqual([r['number'] for r in result], [2, 1, 0])
        self.assertNotEqual(first['id'], second['id'])
        old = self.payload(self.request(views.preview, 'POST', {'version': first['id']}, identifier='ilr'))
        self.assertEqual(old['template']['content']['title'], seed('ilr')['title'])
        self.assertEqual(old['template']['id'], first['id'])
        self.assertIsNone(old['previewToken'])
        self.assertEqual(self.request(views.template, 'PATCH', {}, identifier='ilr').status_code, 405)

    def test_programme_variants_are_disjoint_and_other_programmes_use_shared(self):
        content = seed('ilr'); content['templateId'] += '@' + str(uuid.uuid4()); content['programmes'] = ['ME_L4']
        content['title'] = 'Marketing-only wording'; variant = self.publish(content)
        self.assertEqual(service.applicable('ilr', self.learner)['id'], variant['id'])
        self.learner.programme = PROGRAMMES['APM_L4']
        self.assertEqual(service.applicable('ilr', self.learner)['number'], 0)
        overlap = copy.deepcopy(content); overlap['templateId'] = 'ilr@' + str(uuid.uuid4())
        self.save(overlap)
        preview = self.payload(self.request(views.preview, 'POST', {'revision': 1}, identifier=overlap['templateId']))
        self.assertEqual(self.request(views.publish, 'POST', {'revision': 1, 'reviewConfirmed': True,
            'previewToken': preview['previewToken']}, identifier=overlap['templateId']).status_code, 400)
        self.assertEqual(service.latest('ilr')[0]['content'], validate(seed('ilr')))

    def test_discard_draft_keeps_publication_and_source_records(self):
        first = self.publish(seed('ilr')); saved = self.save(seed('ilr'))
        self.payload(self.request(views.template, 'DELETE', saved, identifier='ilr'))
        self.assertEqual(TemplateDraft.objects.count(), 0)
        self.assertEqual(service.latest('ilr')[0]['id'], first['id'])

    @override_settings(DOCUMENT_TEMPLATES_ENABLED=False)
    def test_schema_not_deployed_keeps_originals_and_prevents_new_writes(self):
        self.assertTrue(self.payload(self.request(views.templates))['setupRequired'])
        self.assertEqual(self.request(views.template, 'PUT', {}, identifier='ilr').status_code, 503)

    def test_all_four_existing_issuers_and_signers_pin_versions_without_rewriting_old_records(self):
        from learner_api import apprenticeship_agreement as aa, ilr_document as ilr, training_plan_document as tp, written_agreement as wa
        from learner_api.models import ApprenticeshipAgreement, IlrDocument, TrainingPlanDocument, WrittenAgreement
        cases = [('apprenticeship-agreement', aa, aa.issue_agreement, aa.sign_agreement, ApprenticeshipAgreement, 'agreement', 'apprentice'),
                 ('ilr', ilr, ilr.issue_ilr, ilr.sign_ilr, IlrDocument, 'document', 'learner'),
                 ('training-plan', tp, tp.issue_training_plan, tp.sign_training_plan, TrainingPlanDocument, 'document', 'apprentice'),
                 ('written-agreement', wa, wa.issue_written_agreement, wa.sign_written_agreement, WrittenAgreement, 'document', 'learner')]
        for key, module, issue, sign, model, result_key, party in cases:
            with self.subTest(key=key), ExitStack() as stack:
                data = sample(key, [])[1]
                if key == 'apprenticeship-agreement':
                    derived = {**data['particulars'], 'startDate': date(2030, 1, 1), 'endDate': date(2032, 1, 1),
                        'practicalStart': date(2030, 1, 1), 'practicalEnd': date(2031, 10, 1), 'plannedOtjh': 300,
                        'planModules': [], 'datesFrom': 'learner'}
                    stack.enter_context(patch.object(module, 'derive_particulars', return_value=derived))
                elif key == 'ilr':
                    stack.enter_context(patch.object(module, 'derive_learner_details', return_value=data['learnerDetails']))
                    stack.enter_context(patch.object(module, 'derive_answers', return_value=data['answers']))
                else: stack.enter_context(patch.object(module, 'derive_content', return_value=data))
                stack.enter_context(patch.object(module, 'advance_learner', return_value=None))
                content = seed(key); first = self.publish(content)
                issued = self.payload(self.request(issue, 'POST', pk=self.learner.pk), 201)[result_key]
                self.assertEqual(issued['template']['id'], first['id'])
                signed = self.payload(self.request(sign, 'POST', {'party': party, 'name': 'Synthetic Signer',
                    'signature': 'data:image/png;base64,c3ludGhldGlj'}, pk=self.learner.pk))[result_key]
                old_row = model.objects.get(pk=issued['id'])
                stored = {name: getattr(old_row, name) for name in ('doc_path', 'blob_name') if hasattr(old_row, name)}
                content['title'] = 'Newly approved ' + content['title']; second = self.publish(content)
                serializer = module._agreement_json if key == 'apprenticeship-agreement' else module._document_json
                old_after = serializer(model.objects.get(pk=issued['id']))
                self.assertEqual(old_after, signed)
                issued2 = self.payload(self.request(issue, 'POST', pk=self.learner.pk), 201)[result_key]
                self.assertEqual(issued2['template']['id'], second['id'])
                old_row.refresh_from_db()
                self.assertEqual({name: getattr(old_row, name) for name in stored}, stored)
                self.assertTrue(serializer(old_row)['signatures'][party]['signed'])
                history = self.payload(self.request(views.issued_history, issue_id=uuid.UUID(signed['template']['issueId'])))
                self.assertEqual(history['results'][0]['data'], signed)

    def test_pre_builder_issued_records_always_keep_legacy_template(self):
        from learner_api.models import IlrDocument
        from learner_api.ilr_document import _document_json
        old = IlrDocument.objects.create(learner_kind='apprenticeship', learner_id=self.learner.pk,
            learner_details={'givenNames': 'Original'}, answers={'saved': 'answer'}, learner_signature='data:image/png;original')
        content = seed('ilr'); content['title'] = 'Changed after old signing'; self.publish(content)
        payload = _document_json(old)
        self.assertTrue(payload['template']['legacy']); self.assertTrue(payload['template']['legacyEquivalent'])
        self.assertEqual(payload['template']['content']['title'], seed('ilr')['title'])
        old.refresh_from_db(); self.assertEqual(old.answers, {'saved': 'answer'})

    def test_structured_fields_save_reload_preview_publish_and_issue_preserve_partial_and_full_signatures(self):
        from learner_api import ilr_document as source
        from learner_api.models import IlrDocument
        from .question_fields import legacy_fields
        from .test_question_fields import question
        with patch.object(source, 'derive_learner_details', return_value={'givenNames': 'SYNTHETIC'}), patch.object(source, 'derive_answers', return_value={}), patch.object(source, 'advance_learner', return_value=None):
            original = seed('ilr'); self.publish(original)
            issued = self.payload(self.request(source.issue_ilr, 'POST', pk=self.learner.pk), 201)['document']
            partial = self.payload(self.request(source.sign_ilr, 'POST', {'party': 'learner', 'name': 'Synthetic Learner', 'signature': 'data:image/png;synthetic'}, pk=self.learner.pk))['document']
            content = seed('ilr'); index = next(i for i, b in enumerate(content['blocks']) if b['type'] == 'field')
            previous = content['blocks'][index:index + 2]
            fields = [f for b in previous for f in legacy_fields(b, 'ilr')]
            fields.reverse(); fields[0]['helpText'] = 'Help for {{learner.fullName}}'
            fields.append(question(value='First approved configured value'))
            content['blocks'][index:index + 2] = [{'id': previous[0]['id'], 'type': 'questions', 'fields': fields}]
            saved = self.save(content)
            reopened = self.payload(self.request(views.template, identifier='ilr'))
            self.assertEqual(reopened['draft']['blocks'][index]['fields'], fields)
            preview = self.payload(self.request(views.preview, 'POST', {'revision': saved['revision']}, identifier='ilr'))
            version = self.payload(self.request(views.publish, 'POST', {'revision': saved['revision'], 'previewToken': preview['previewToken'], 'reviewConfirmed': True}, identifier='ilr'), 201)['published']
            self.assertEqual(source._document_json(IlrDocument.objects.get(pk=issued['id'])), partial)
            fully = self.payload(self.request(source.sign_ilr, 'POST', {'party': 'provider', 'name': 'Synthetic Provider', 'signature': 'data:image/png;synthetic'}, pk=self.learner.pk))['document']
            self.assertTrue(fully['fullySigned'])
            fields[-1]['value'] = 'Later approved configured value'; latest = self.publish(content)
            self.assertEqual(source._document_json(IlrDocument.objects.get(pk=issued['id'])), fully)
            new = self.payload(self.request(source.issue_ilr, 'POST', pk=self.learner.pk), 201)['document']
            self.assertEqual(new['template']['id'], latest['id']); self.assertNotEqual(latest['id'], version['id'])
            self.assertEqual(new['template']['content']['blocks'][index]['fields'][0]['id'], fields[0]['id'])
            self.assertEqual(new['template']['content']['blocks'][index]['fields'][-1]['value'], 'Later approved configured value')

    def test_api_cannot_remove_a_protected_question_section(self):
        content = seed('review-eligibility')
        content['blocks'] = [b for b in content['blocks'] if b.get('source') != 'review:extendedIlr']
        self.assertEqual(self.request(views.template, 'PUT', {'content': content, 'revision': 0,
            'baseVersion': service.latest('review-eligibility')[0]['id']}, identifier='review-eligibility').status_code, 400)

    def test_api_cannot_duplicate_system_questions_by_copying_a_container(self):
        content = seed('review-eligibility')
        duplicate = copy.deepcopy(next(b for b in content['blocks'] if b.get('source') == 'review:extendedIlr'))
        duplicate['id'] = 'forged-duplicate-section'; content['blocks'].append(duplicate)
        self.assertEqual(self.request(views.template, 'PUT', {'content': content, 'revision': 0,
            'baseVersion': service.latest('review-eligibility')[0]['id']}, identifier='review-eligibility').status_code, 400)

    def test_calendar_review_creation_pins_once_and_rebooking_keeps_the_original_version(self):
        from learner_api.calendar import _record_enrolment_review
        from learner_api.models import EnrolmentReview
        for review_type, key in REVIEW_KEYS.items():
            with self.subTest(key=key):
                content = seed(key); first = self.publish(content)
                record = SimpleNamespace(event_type=review_type, event_key='synthetic-booking:' + review_type,
                    learner_name=self.learner.username, learner_email=self.learner.email, owner_name='Synthetic Coach', owner_email='',
                    scheduled_date=date(2030, 1, 1), scheduled_time=None, duration_minutes=60, notes='', meeting_provider='',
                    meeting_link='', graph_web_link='', graph_event_id='', last_graph_sync_error='')
                _record_enrolment_review(record, kind='apprenticeship', learner_kind_id=self.learner.pk, coach_id=None)
                review = EnrolmentReview.objects.get(event_key=record.event_key)
                self.assertEqual(service.binding_for(key, review).template_snapshot['id'], first['id'])
                content['title'] = 'Later approved booking wording'; self.publish(content)
                record.scheduled_date = date(2030, 2, 1)
                _record_enrolment_review(record, kind='apprenticeship', learner_kind_id=self.learner.pk, coach_id=None)
                self.assertEqual(EnrolmentReview.objects.filter(event_key=record.event_key).count(), 1)
                self.assertEqual(service.binding_for(key, review).template_snapshot['id'], first['id'])

    def test_all_four_review_issuers_use_existing_records_and_freeze_signed_answers(self):
        from learner_api import review_form as source
        from learner_api.models import EnrolmentReview
        for review_type, key in REVIEW_KEYS.items():
            with self.subTest(key=key), ExitStack() as stack:
                stack.enter_context(patch.object(source, '_case_owner_record', return_value=('', '', None)))
                stack.enter_context(patch.object(source, '_slots_payload', return_value={'reviews': []}))
                stack.enter_context(patch.object(source, '_skills_radar_context', return_value={}))
                stack.enter_context(patch.object(source, '_plr_context', return_value={}))
                stack.enter_context(patch('learner_api.learning_plan.promote_to_delivery_if_ready', return_value=None))
                stack.enter_context(patch.object(source, '_signatures', wraps=lambda review, **kw: {
                    role: {'signed': bool(getattr(review, role + '_signature')), 'signature': getattr(review, role + '_signature'),
                        'name': getattr(review, role + '_signed_name'), 'signedAt': source._iso(getattr(review, role + '_signed_at'))}
                    for role in ('learner', 'admin', 'employer')}))
                content = seed(key); first = self.publish(content)
                # Existing staff_only guard is tested in its own regression suite.
                response = self.request(inspect.unwrap(source.issue_onboarding_review), 'POST', {'type': review_type}, kind='apprenticeship', pk=self.learner.pk)
                self.payload(response, 201)
                review = EnrolmentReview.objects.get(learner_id=self.learner.pk, review_type=review_type)
                review.form_answers = {'savedQuestion': {'answer': 'Original answer'}}
                review.form_completed = True; review.save()
                source.record_signature(review, 'learner', 'data:image/png;original', 'Synthetic Learner')
                signed = source._serialize_form(review, self.learner, None)
                self.assertEqual(signed['template']['id'], first['id']); self.assertTrue(service.review_is_signed(review))
                content['title'] = 'New ' + content['title']; self.publish(content)
                review.form_answers = {'savedQuestion': {'answer': 'External later mutation'}}; review.save()
                after = source._serialize_form(review, self.learner, None)
                self.assertEqual(after['answers'], signed['answers']); self.assertEqual(after['template']['id'], first['id'])
                with patch.object(source, '_lookup', return_value=(self.learner, review, None, None)):
                    refused = self.request(inspect.unwrap(source.enrolment_review_form), 'PATCH', {'answers': {}},
                        kind='apprenticeship', pk=self.learner.pk, event_key=review.event_key)
                    self.assertEqual(refused.status_code, 409)
                archived = RenderHistory.objects.get(issue__source_id=str(review.pk), issue__document_key=key).payload
                self.assertEqual(archived['answers'], signed['answers'])
                source.record_signature(review, 'learner', '', '')
                self.assertFalse(service.review_is_signed(review))
                self.assertEqual(RenderHistory.objects.filter(issue__source_id=str(review.pk), issue__document_key=key).count(), 2)
                self.assertEqual(RenderHistory.objects.get(pk=RenderHistory.objects.filter(issue__document_key=key).order_by('recorded_at').first().pk).payload, archived)
