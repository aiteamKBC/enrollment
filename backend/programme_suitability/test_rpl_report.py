"""Phase 2 report tests. The configured runner uses in-memory SQLite only."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .models import SuitabilityCvExtraction, SuitabilityRplReport
from .rpl_report import (
    ReportInputError,
    _context,
    _cv_evidence,
    _ai_input,
    _programme_metadata,
    _private_cv_text,
    _rpl_analysis_payload,
    _validate_rpl_analysis,
    _save_report,
    _skills_radar,
    build_source_snapshot,
    generate_ai_justification,
    generate_rpl_evidence_analysis,
    suitability_rpl_analysis,
    suitability_rpl_report,
)


def source_snapshot(programme_id='PM-L5'):
    return {
        'learner': {'id': '41', 'name': 'Synthetic Learner', 'jobTitle': 'Project Officer'},
        'programme': {'id': programme_id, 'name': 'Project Manager Level 5'},
        'programmeUnderstanding': [],
        'programmeSuitability': {'status': 'available'},
        'skillsRadar': {'status': 'available', 'items': []},
        'evidence': {'plr': {'status': 'not_available', 'message': 'Not available - integration not implemented'}},
        'trainingPlan': {
            'available': True,
            'source': 'authoritative_training_plan',
            'plannedHours': 120,
            'modules': [
                {'moduleId': 'module-a', 'title': 'Authoritative module A', 'plannedHours': 70, 'ksbCodes': ['K1', 'S2']},
                {'moduleId': 'module-b', 'title': 'Authoritative module B', 'plannedHours': 50, 'ksbCodes': ['B1']},
            ],
        },
        'capturedAt': '2026-11-05T12:00:00+00:00',
    }


def base_payload(**updates):
    payload = {
        'revision': 0,
        'relevantPriorLearning': False,
        'reductionJustified': False,
        'assessorEvidenceReview': 'The assessor reviewed the available synthetic evidence.',
        'noReductionReason': 'The evidence does not demonstrate an overlap sufficient to reduce training.',
        'suitabilityOutcome': 'suitable',
        'suitabilityJustification': 'The selected programme is suitable on the reviewed evidence.',
        'suitabilityReviewed': True,
        'rplJustification': 'The full programme remains appropriate.',
        'rplReviewed': True,
        'finalSummary': 'Proceed with the full programme.',
        'reductions': [],
        'finalise': False,
    }
    payload.update(updates)
    return payload


class SuitabilityRplPersistenceTests(TestCase):
    databases = {'default'}

    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.put('/report/', data='{}', content_type='application/json')
        self.request.login_account = SimpleNamespace(
            id=9, pk=9, role='admin', display_name='Synthetic Administrator',
            email='admin@example.invalid', is_active=True,
        )
        self.learner = SimpleNamespace(pk=41)
        self.context_a = {
            'key': 'a' * 64, 'programmeId': 'PM-L5', 'programmeName': 'Project Manager Level 5',
            'enrolmentReference': 'SYN-41-A', 'practicalStartDate': '2026-11-01',
        }
        self.context_b = {
            'key': 'b' * 64, 'programmeId': 'OD-L5', 'programmeName': 'Operations Manager Level 5',
            'enrolmentReference': 'SYN-41-B', 'practicalStartDate': '2026-12-01',
        }

    def save(self, context, payload, snapshot=None):
        with patch('programme_suitability.rpl_report.build_source_snapshot', return_value=(snapshot or source_snapshot(context['programmeId']), {'layout': 7})):
            return _save_report(self.request, self.learner, 'apprenticeship', context, None, payload)

    def test_separate_programme_contexts_create_separate_reports(self):
        first = self.save(self.context_a, base_payload())
        second = self.save(self.context_b, base_payload())
        self.assertNotEqual(first.context_key, second.context_key)
        self.assertEqual(SuitabilityRplReport.objects.filter(learner_id=41).count(), 2)

    def test_snapshot_is_frozen_after_first_save(self):
        first = self.save(self.context_a, base_payload())
        changed = source_snapshot()
        changed['learner']['jobTitle'] = 'Changed later'
        updated = self.save(self.context_a, base_payload(revision=first.revision), changed)
        self.assertEqual(updated.source_snapshot['learner']['jobTitle'], 'Project Officer')
        self.assertEqual(updated.source_snapshot_hash, first.source_snapshot_hash)

    def test_all_three_rpl_states_and_authoritative_module_validation(self):
        no_prior = self.save(self.context_a, base_payload())
        self.assertFalse(no_prior.relevant_prior_learning)

        relevant_no_reduction = self.save(
            self.context_a,
            base_payload(revision=no_prior.revision, relevantPriorLearning=True),
        )
        self.assertTrue(relevant_no_reduction.relevant_prior_learning)
        self.assertFalse(relevant_no_reduction.reduction_justified)

        reduction_payload = base_payload(
            revision=relevant_no_reduction.revision,
            relevantPriorLearning=True,
            reductionJustified=True,
            noReductionReason='',
            reductions=[{
                'moduleId': 'module-a', 'reducedHours': 12, 'ksbCodes': ['K1'],
                'evidence': 'Certificate and professional discussion verified.',
                'assessorComments': 'Partial overlap only.',
            }],
        )
        reduced = self.save(self.context_a, reduction_payload)
        self.assertEqual(reduced.reductions.get().module_title, 'Authoritative module A')
        self.assertEqual(float(reduced.reductions.get().planned_hours), 70)

        invalid = dict(reduction_payload, revision=reduced.revision)
        invalid['reductions'] = [dict(reduction_payload['reductions'][0], moduleId='invented-module')]
        with self.assertRaisesMessage(ReportInputError, 'available Training Plan module'):
            self.save(self.context_a, invalid)

    def test_finalised_report_is_immutable_and_requires_explicit_new_version(self):
        draft = self.save(self.context_a, base_payload())
        finalised = self.save(self.context_a, base_payload(revision=draft.revision, finalise=True))
        self.assertEqual(finalised.status, 'finalised')
        with self.assertRaisesMessage(ReportInputError, 'finalised and immutable'):
            self.save(self.context_a, base_payload(revision=finalised.revision))

        version_two = self.save(self.context_a, base_payload(newVersion=True, amendmentReason='Reassess the RPL decision after new evidence.'))
        self.assertEqual(version_two.report_version, 2)
        self.assertEqual(version_two.parent_report_id, finalised.pk)
        self.assertEqual(version_two.amendment_reason, 'Reassess the RPL decision after new evidence.')
        self.assertIsNone(version_two.suitability_reviewed_at)
        self.assertIsNone(version_two.rpl_reviewed_at)
        self.assertEqual(finalised.source_snapshot_hash, source_snapshot_hash(finalised))

    def test_editing_a_justification_clears_previous_approval_until_reapproved(self):
        draft = self.save(self.context_a, base_payload())
        self.assertIsNotNone(draft.suitability_reviewed_at)
        changed = self.save(self.context_a, base_payload(
            revision=draft.revision,
            suitabilityJustification='Synthetic replacement after rejecting the draft.',
            suitabilityReviewed=False,
        ))
        self.assertIsNone(changed.suitability_reviewed_at)
        approved = self.save(self.context_a, base_payload(
            revision=changed.revision,
            suitabilityJustification='Synthetic replacement after rejecting the draft.',
            suitabilityReviewed=True,
        ))
        self.assertIsNotNone(approved.suitability_reviewed_at)


def source_snapshot_hash(report):
    """Return the stored value to make the immutability assertion explicit."""
    report.refresh_from_db()
    return report.source_snapshot_hash


class SuitabilityRplPermissionAndAiTests(TestCase):
    databases = {'default'}

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = SimpleNamespace(id=9, pk=9, role='admin', display_name='Admin', email='admin@example.invalid')
        self.learner_account = SimpleNamespace(id=10, pk=10, role='learner')

    @patch('login.permissions.authenticate_request')
    def test_endpoint_is_admin_only(self, authenticate):
        authenticate.return_value = self.learner_account
        response = suitability_rpl_report(self.factory.get('/report/'), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 403)

    def test_rpl_analysis_accepts_valid_reason_over_40_words_without_http_400(self):
        long_reason = ' '.join(['Verified evidence requires assessor review.'] * 11)
        report = SimpleNamespace(source_snapshot_hash='snapshot-a')
        recommendation = {
            'overallOutcome': 'insufficient-evidence', 'reasoning': long_reason,
            'modules': [], 'evidenceGaps': [], 'warnings': [],
        }
        with patch('login.permissions.authenticate_request', return_value=self.admin), \
             patch('programme_suitability.rpl_report._learner', return_value=SimpleNamespace(pk=41)), \
             patch('programme_suitability.rpl_report._context', return_value={'key': 'context-a'}), \
             patch('programme_suitability.rpl_report.SuitabilityRplReport.objects') as manager, \
             patch('programme_suitability.rpl_report.generate_rpl_evidence_analysis', return_value=(recommendation, {'sourceSnapshotHash': 'snapshot-a'})):
            manager.prefetch_related.return_value.filter.return_value.first.return_value = report
            request = self.factory.post('/analysis/', data='{}', content_type='application/json')
            request._dont_enforce_csrf_checks = True
            response = suitability_rpl_analysis(request, 'apprenticeship', 41)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['recommendation']['reasoning'], long_reason)

    @override_settings(SUITABILITY_RPL_AI_ENABLED=False, OPENAI_API_KEY='')
    def test_ai_is_disabled_by_default_without_network_call(self):
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        with self.assertRaisesMessage(ReportInputError, 'disabled'):
            generate_ai_justification(report, 'suitability')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_ai_output_is_a_draft_only_and_uses_synthetic_input(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': (
            'The saved role evidence aligns with the selected programme requirements. '
            'The recorded answers provide a suitable basis for assessor review.\n\n'
            'The draft remains editable and is grounded only in the supplied evidence.'
        )}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='stop')],
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        text, metadata = generate_ai_justification(report, 'suitability')
        self.assertIn('saved role evidence', text)
        self.assertEqual(metadata['provider'], 'openai')
        sent = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Do not invent facts', sent)
        self.assertIn('150-200 words in two coherent paragraphs', sent)
        self.assertIn('selected Programme Suitability answers', sent)
        self.assertIn('Do not repeat the full programme specification', sent)
        self.assertIn('significant new occupational knowledge, skills and behaviours', sent)
        self.assertIn('authoritative standard outcomes', sent)
        self.assertIn('Do not claim employer discussion, agreement or sign-off', sent)
        self.assertIn('Discuss programme suitability only', sent)
        self.assertNotIn('RPL', text)
        self.assertNotIn('exemption', text)
        evidence_payload = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertNotIn('Synthetic Learner', evidence_payload)
        self.assertIn('Project Officer', evidence_payload)

    @patch('programme_suitability.rpl_report.extract_approved_cv')
    def test_private_cv_text_restores_normalised_snapshot_document_kind(self, extract_mock):
        extraction = SimpleNamespace(source_blob_hash='a' * 64, source_container='evidence-approved', source_blob_name='1601/cv.pdf', extractor_version='cv-text-v1', extracted_text='PRIVATE')
        extract_mock.return_value = extraction
        SuitabilityCvExtraction.objects.create(
            learner_kind='apprenticeship', learner_id=1601,
            source_document_id='cv-1', source_container='evidence-approved',
            source_blob_name='1601/cv.pdf', source_blob_hash='a' * 64,
            extractor_version='cv-text-v1', status='ready', extracted_text='PRIVATE',
        )
        report = SuitabilityRplReport(
            learner_kind='apprenticeship', learner_id=1601,
            cv_source_document_id='', cv_source_blob_hash='',
        )
        text = _private_cv_text(report, {'sourceDocuments': [{
            'id': 'cv-1', 'kind': 'cv', 'container': 'evidence-approved',
            'blobName': '1601/cv.pdf', 'filename': 'cv.pdf',
        }]})
        self.assertEqual(text, 'PRIVATE')
        self.assertEqual(extract_mock.call_args.args[2]['docKind'], 'cv')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_suitability_ai_rejects_unfinished_report_narrative(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': (
            'The saved evidence supports the selected programme.\n\n'
            'The learner responsibilities are described in the supplied answers, for example,'
        )}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='stop')],
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        with self.assertRaisesMessage(ReportInputError, 'unfinished'):
            generate_ai_justification(report, 'suitability')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_suitability_ai_accepts_complete_single_paragraph_after_normalisation(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': (
            'The saved role and selected answers provide evidence of alignment with the ME_L4 programme.\r\n'
            'The narrative is complete and suitable for assessor review.'
        )}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='stop')],
            usage=SimpleNamespace(model_dump=lambda: {'prompt_tokens': 10, 'completion_tokens': 12, 'total_tokens': 22}),
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        text, metadata = generate_ai_justification(report, 'suitability')
        self.assertEqual(text, 'The saved role and selected answers provide evidence of alignment with the ME_L4 programme.\nThe narrative is complete and suitable for assessor review.')
        self.assertEqual(metadata['usage']['total_tokens'], 22)

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_suitability_ai_validation_error_contains_safe_diagnostics(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': 'The draft ends with an unfinished comma,'}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='stop')],
            usage=SimpleNamespace(model_dump=lambda: {'prompt_tokens': 10, 'completion_tokens': 12, 'total_tokens': 22}),
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        with self.assertRaisesMessage(ReportInputError, 'paragraphs=1') as captured:
            generate_ai_justification(report, 'suitability')
        self.assertIn('length=', str(captured.exception))
        self.assertIn('finish_reason=stop', str(captured.exception))
        self.assertIn('total_tokens=22', str(captured.exception))

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_suitability_ai_rejects_internal_fields_and_non_stop_completion(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': (
            'The programmeSpecification supports the selected pathway.\n\n'
            'The supplied evidence is sufficient for review.'
        )}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='length')],
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        with self.assertRaisesMessage(ReportInputError, 'did not finish'):
            generate_ai_justification(report, 'suitability')

        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason='stop')],
        )
        with self.assertRaisesMessage(ReportInputError, 'technical or internal'):
            generate_ai_justification(report, 'suitability')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_rpl_ai_output_uses_full_evidence_for_all_three_decisions(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': 'Synthetic RPL draft for assessor review.'}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
        )
        snapshot = source_snapshot()
        snapshot['learner']['responsibilities'] = ['Coordinates project schedules.']
        snapshot['evidence']['cv'] = {'status': 'provided', 'experience': 'Maintains project schedules.'}
        snapshot['evidence']['qualifications'] = {
            'status': 'provided', 'items': [{'label': 'Highest qualification', 'value': 'Level 3 Diploma', 'source': 'ILR learner details'}],
        }
        snapshot['skillsRadar'] = {
            'scaleMaximum': 9, 'catalogueVersion': 'historical-v1', 'cataloguePinned': True,
            'summary': [{'label': 'Knowledge', 'answered': 1, 'total': 1, 'score': 7.0}],
            'items': [{'code': 'K1', 'kind': 'Knowledge', 'level': '7', 'description': 'Project controls knowledge', 'moduleKey': 'module-a', 'score': 7}],
        }
        snapshot['trainingPlan'] = {
            'available': True, 'source': 'authoritative_training_plan', 'plannedHours': 120,
            'modules': [{'moduleId': 'module-a', 'title': 'Authoritative module A', 'plannedHours': 70, 'ksbCodes': ['K1', 'S2']}],
        }
        snapshot['programme']['specification'] = 'Synthetic programme specification.'
        reductions = [SimpleNamespace(module_title='Authoritative module A', planned_hours=70, reduced_hours=12, ksb_codes=['K1'], evidence='Verified certificate', assessor_comments='Partial overlap')]
        for relevant, justified in ((False, False), (True, False), (True, True)):
            report = SimpleNamespace(
                source_snapshot=snapshot,
                relevant_prior_learning=relevant,
                reduction_justified=justified,
                assessor_evidence_review='Synthetic evidence review.',
                no_reduction_reason='' if justified else 'Retain the full programme.',
                reductions=SimpleNamespace(all=lambda: reductions if justified else []),
            )
            text, metadata = generate_ai_justification(report, 'rpl')
            self.assertEqual(text, 'Synthetic RPL draft for assessor review.')
            self.assertEqual(metadata['provider'], 'openai')
            sent = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
            self.assertIn('historical-v1', sent)
            self.assertIn('Authoritative module A', sent)
            self.assertIn(str(relevant).lower(), sent.lower())
            self.assertIn(str(justified).lower(), sent.lower())

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_incomplete_evidence_is_sent_as_blank_or_unavailable_without_invention(self, openai_client):
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'justification': 'Synthetic cautious draft.'})))],
        )
        report = SimpleNamespace(source_snapshot=source_snapshot(), relevant_prior_learning=False, reduction_justified=False,
                                 assessor_evidence_review='', no_reduction_reason='Retain the full programme.',
                                 reductions=SimpleNamespace(all=lambda: []))
        text, _ = generate_ai_justification(report, 'rpl')
        self.assertEqual(text, 'Synthetic cautious draft.')
        sent = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertIn('Not available - integration not implemented', sent)
        self.assertIn('Do not invent facts', openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content'])

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_no_prior_learning_prompt_requires_full_plan_and_supported_evidence(self, openai_client):
        narrative = (
            'The saved CV describes general marketing work and the verified qualification evidence records a Level 3 Diploma, '
            'but neither source demonstrates equivalent prior learning against a specific module. The Skills Radar results provide '
            'self-assessment context only and do not establish an exemption from the saved modules.\n\n'
            'The assessor-recorded decision therefore retains the full standard Training Plan of 120 planned hours across '
            'Authoritative module A and Authoritative module B. No reduction is approved because the available evidence does not '
            'show a verified module-level overlap; the original plan remains unchanged while any further evidence can be reviewed.'
        )
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'justification': narrative})), finish_reason='stop')],
        )
        snapshot = source_snapshot()
        snapshot['evidence'] = {
            'cv': {'status': 'provided', 'experience': 'General marketing work.'},
            'qualifications': {'status': 'provided', 'items': [{'label': 'Highest qualification', 'value': 'Level 3 Diploma'}]},
            'plr': {'status': 'not_available', 'message': 'Not available - integration not implemented'},
        }
        report = SimpleNamespace(
            source_snapshot=snapshot, relevant_prior_learning=False, reduction_justified=False,
            assessor_evidence_review='', no_reduction_reason='Retain the full programme.',
            reductions=SimpleNamespace(all=lambda: []), cv_extraction_acknowledged=True,
        )
        text, _metadata = generate_ai_justification(report, 'rpl')
        self.assertIn('120 planned hours', text)
        sent_system = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
        sent_user = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertIn('reported experience or self-assessment', sent_system)
        self.assertIn('No Relevant Prior Learning identified only when', sent_system)
        self.assertIn('reported experience or self-assessment from demonstrated occupational competence', sent_system)
        self.assertIn('employer discussion', sent_system)
        self.assertIn('No Relevant Prior Learning', sent_user)
        self.assertIn('Authoritative module A', sent_user)

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_rpl_validation_rejects_unrecorded_employer_claim(self, openai_client):
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                'justification': 'The employer confirmed that the learner already meets the module outcomes, so no reduction is required.'
            })), finish_reason='stop')],
        )
        report = SimpleNamespace(
            source_snapshot=source_snapshot(), relevant_prior_learning=False, reduction_justified=False,
            assessor_evidence_review='', no_reduction_reason='Retain the full programme.',
            reductions=SimpleNamespace(all=lambda: []), cv_extraction_acknowledged=True,
        )
        with self.assertRaisesMessage(ReportInputError, 'employer evidence'):
            generate_ai_justification(report, 'rpl')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_no_prior_learning_rpl_validation_rejects_contradictions_and_internal_terms(self, openai_client):
        report = SimpleNamespace(
            source_snapshot=source_snapshot(), relevant_prior_learning=False, reduction_justified=False,
            assessor_evidence_review='', no_reduction_reason='Retain the full programme.',
            reductions=SimpleNamespace(all=lambda: []), cv_extraction_acknowledged=True,
        )
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'justification': 'A module reduction is approved for the learner.'})), finish_reason='stop')],
        )
        with self.assertRaisesMessage(ReportInputError, 'contradicts'):
            generate_ai_justification(report, 'rpl')
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'justification': 'The reductions array confirms K999 is mapped to the module.'})), finish_reason='stop')],
        )
        with self.assertRaisesMessage(ReportInputError, 'technical or internal'):
            generate_ai_justification(report, 'rpl')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_no_prior_learning_rpl_validation_rejects_unmapped_ksb_codes(self, openai_client):
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'justification': 'The supplied evidence does not establish K999 against the saved module.'})), finish_reason='stop')],
        )
        report = SimpleNamespace(
            source_snapshot=source_snapshot(), relevant_prior_learning=False, reduction_justified=False,
            assessor_evidence_review='', no_reduction_reason='Retain the full programme.',
            reductions=SimpleNamespace(all=lambda: []), cv_extraction_acknowledged=True,
        )
        with self.assertRaisesMessage(ReportInputError, 'KSB codes'):
            generate_ai_justification(report, 'rpl')

    def test_ai_payloads_preserve_suitability_groups_and_programme_specification(self):
        snapshot = source_snapshot()
        snapshot['programmeSuitability'] = {
            'pathway': 'marketing', 'selectedProgramme': 'MM_L6',
            'selectedProgrammeSpecification': {
                'id': 'MM_L6', 'title': 'Marketing Manager Level 6', 'summary': 'Synthetic summary',
                'outcomes': ['Synthetic authoritative outcome'],
                'ksbMappings': [{'code': 'K1', 'moduleId': 'module-a'}],
            },
            'recommendedProgramme': 'ME_L4', 'outcome': 'suitable',
            'responses': [{'group': 'Marketing Strategy', 'question': 'Which responsibilities apply?',
                           'answers': ['I lead strategy'], 'selectedOptions': [{'id': 'a', 'label': 'I lead strategy'}]}],
        }
        snapshot['programmeUnderstanding'] = [{'question': 'Q1', 'answer': 'Understanding'}, {'question': 'Q2', 'answer': 'Progression'}]
        report = SimpleNamespace(source_snapshot=snapshot)
        payload = _ai_input(report, 'suitability')
        self.assertEqual(payload['programmeUnderstanding'][1]['answer'], 'Progression')
        self.assertEqual(payload['programmeSuitability']['responses'][0]['selectedOptions'][0]['label'], 'I lead strategy')
        self.assertEqual(payload['programmeSuitability']['selectedProgrammeSpecification']['id'], 'MM_L6')
        self.assertEqual(
            payload['programmeSuitability']['selectedProgrammeSpecification']['outcomes'],
            ['Synthetic authoritative outcome'],
        )
        self.assertEqual(
            payload['programmeSuitability']['selectedProgrammeSpecification']['ksbMappings'],
            [{'code': 'K1', 'moduleId': 'module-a'}],
        )
        self.assertNotIn('recommendedProgramme', payload['programmeSuitability'])
        self.assertNotIn('outcome', payload['programmeSuitability'])


class RplEvidenceAnalysisTests(SimpleTestCase):
    def analysis_payload(self, *, mappings=True):
        return {
            'programme': {'id': 'ME_L4', 'name': 'Marketing Executive Level 4'},
            'learnerEvidence': {'jobTitle': 'Marketing Executive', 'responsibilities': ['Plans campaigns.']},
            'programmeUnderstanding': [],
            'skillsRadar': {'catalogueVersion': 'catalogue-v1', 'cataloguePinned': True, 'items': [
                {'code': 'K1', 'kind': 'Knowledge', 'description': 'Planning', 'moduleKey': 'module-a', 'score': 7},
            ]},
            'qualifications': {'status': 'provided', 'items': [{'label': 'Level 3 Diploma', 'value': 'Marketing'}]},
            'plr': {'status': 'not_available'},
            'trainingPlan': {'available': True, 'plannedHours': 70, 'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Campaign planning', 'plannedHours': 70,
                'content': 'Plan and evaluate campaigns.',
                'authoritativeKsbMappings': [{'code': 'K1', 'description': 'Planning', 'source': 'occupational-standard'}] if mappings else [],
            }]},
            'privateCvText': '',
            'extractionWarning': '',
            'evidenceVersion': {'sourceSnapshotHash': 'snapshot-a', 'documentId': 'cv-a', 'sha256': 'a' * 64, 'extractorVersion': 'cv-text-v1'},
            'evidenceReferences': [
                {'id': 'evidence.skills-radar.1', 'source': 'skills_radar', 'label': 'Saved Skills Radar item 1'},
                {'id': 'evidence.qualification.1', 'source': 'qualification_record', 'label': 'Saved qualification record 1'},
                {'id': 'evidence.cv.approved', 'source': 'approved_cv_extraction', 'label': 'Approved CV extraction'},
                {'id': 'evidence.training-plan.module.module-a', 'source': 'training_plan', 'label': 'Saved Training Plan module module-a'},
            ],
        }

    def test_no_relevant_prior_learning_recommends_no_whole_module_removal(self):
        payload = self.analysis_payload()
        result = _validate_rpl_analysis({
            'overallOutcome': 'no-relevant-prior-learning',
            'reasoning': 'The available evidence does not demonstrate complete coverage of the module.',
            'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Campaign planning', 'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'partial',
                'supportingEvidence': [{'evidenceId': 'evidence.skills-radar.1', 'detail': 'Self-assessment only.'}],
                'missingEvidence': ['Independent competence evidence is required.'],
                'explanation': 'The module remains required because complete outcomes are not evidenced.',
            }],
            'evidenceGaps': ['PLR check not recorded.'], 'warnings': [],
        }, payload)
        self.assertFalse(result['modules'][0]['wholeModuleReductionRecommended'])

    def test_supporting_evidence_must_use_an_allowed_canonical_id(self):
        payload = self.analysis_payload()
        base = {
            'moduleId': 'module-a', 'moduleName': 'Campaign planning',
            'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'needs-review',
            'missingEvidence': ['Independent verification is required.'],
            'explanation': 'Further review is required before any complete-module decision.',
        }
        for reference in (
            {'evidenceId': 'Skills Radar', 'detail': 'A descriptive label is not an evidence ID.'},
            {'evidenceId': 'evidence.skills-radar.NOT_REAL', 'detail': 'This ID is not in the evidence catalogue.'},
            {'source': 'Skills Radar', 'reference': 'K1', 'detail': 'The legacy shape is not accepted.'},
        ):
            with self.assertRaisesMessage(ReportInputError, 'unsupported evidence reference ID'):
                _validate_rpl_analysis({
                    'overallOutcome': 'insufficient-evidence',
                    'reasoning': 'The evidence requires assessor review.',
                    'modules': [{**base, 'supportingEvidence': [reference]}],
                    'evidenceGaps': [], 'warnings': [],
                }, payload)

    def test_empty_supporting_evidence_is_valid_when_review_is_required(self):
        payload = self.analysis_payload()
        result = _validate_rpl_analysis({
            'overallOutcome': 'insufficient-evidence',
            'reasoning': 'The available evidence is insufficient for a complete-module recommendation.',
            'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Campaign planning',
                'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'needs-review',
                'supportingEvidence': [],
                'missingEvidence': ['Independent verification is required.'],
                'explanation': 'No complete-module recommendation can be made yet.',
            }],
            'evidenceGaps': ['Evidence verification is required.'], 'warnings': [],
        }, payload)
        self.assertEqual(result['modules'][0]['supportingEvidence'], [])

    def test_valid_overlong_overall_reason_is_accepted(self):
        payload = self.analysis_payload()
        long_reason = ' '.join(['Evidence requires assessor review.'] * 11)
        result = _validate_rpl_analysis({
            'overallOutcome': 'insufficient-evidence',
            'reasoning': long_reason,
            'modules': [{
                'moduleId': 'module-a', 'wholeModuleReductionRecommended': False,
                'evidenceSufficiency': 'needs-review', 'supportingEvidence': [],
                'missingEvidence': ['Independent verification is required.'],
                'explanation': 'Further review is required before any complete-module decision.',
            }],
            'evidenceGaps': [], 'warnings': [],
        }, payload)
        self.assertEqual(result['reasoning'], long_reason)

    def test_module_recommendation_requires_canonical_boolean_field(self):
        payload = self.analysis_payload()
        base = {
            'moduleId': 'module-a', 'moduleName': 'Campaign planning',
            'supportingEvidence': [],
            'missingEvidence': ['Independent competence evidence is required.'],
            'explanation': 'Further review is required before any complete-module decision.',
            'evidenceSufficiency': 'needs-review',
        }
        with self.assertRaisesMessage(ReportInputError, 'Module module-a needs a boolean whole-module reduction recommendation; received NoneType.'):
            _validate_rpl_analysis({
                'overallOutcome': 'insufficient-evidence', 'reasoning': 'Review required.',
                'modules': [{**base, 'wholeModuleReductionRecommended': None}],
                'evidenceGaps': [], 'warnings': [],
            }, payload)
        with self.assertRaisesMessage(ReportInputError, 'Module module-a needs a boolean whole-module reduction recommendation; received str.'):
            _validate_rpl_analysis({
                'overallOutcome': 'insufficient-evidence', 'reasoning': 'Review required.',
                'modules': [{**base, 'wholeModuleReductionRecommended': 'false'}],
                'evidenceGaps': [], 'warnings': [],
            }, payload)
        with self.assertRaisesMessage(ReportInputError, 'Module module-a needs a boolean whole-module reduction recommendation; received NoneType.'):
            _validate_rpl_analysis({
                'overallOutcome': 'insufficient-evidence', 'reasoning': 'Review required.',
                'modules': [{**base, 'wholeModuleCandidate': False}],
                'evidenceGaps': [], 'warnings': [],
            }, payload)

    def test_all_authoritative_modules_are_returned_with_sufficiency_status(self):
        payload = self.analysis_payload(mappings=False)
        payload['trainingPlan']['modules'] = [
            {'moduleId': 'martech', 'moduleName': 'Martech', 'plannedHours': 105.75, 'authoritativeKsbMappings': []},
            {'moduleId': 'social-media', 'moduleName': 'Social Media', 'plannedHours': 103, 'authoritativeKsbMappings': []},
            {'moduleId': 'marketing-impact', 'moduleName': 'Marketing Impact and Planning', 'plannedHours': 159.5,
             'authoritativeKsbMappings': [{'code': 'K3', 'description': 'Authoritative planning outcome', 'source': 'occupational-standard'}]},
        ]
        modules = [{
            'moduleId': item['moduleId'], 'moduleName': item['moduleName'],
            'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'insufficient',
            'supportingEvidence': [],
            'missingEvidence': ['Authoritative outcome verification is required.'],
            'explanation': 'The evidence is insufficient to recommend removal of the complete module.',
        } for item in payload['trainingPlan']['modules']]
        result = _validate_rpl_analysis({
            'overallOutcome': 'insufficient-evidence',
            'reasoning': 'The available evidence requires assessor review.',
            'modules': modules, 'evidenceGaps': ['KSB mappings are incomplete.'], 'warnings': [],
        }, payload)
        self.assertEqual({item['moduleId'] for item in result['modules']}, {'martech', 'social-media', 'marketing-impact'})
        self.assertTrue(all(item['evidenceSufficiency'] == 'insufficient' for item in result['modules']))
        self.assertEqual(
            next(item['authoritativeKsbMappings'] for item in result['modules'] if item['moduleId'] == 'marketing-impact'),
            [{'code': 'K3', 'description': 'Authoritative planning outcome', 'source': 'occupational-standard'}],
        )
        self.assertTrue(all(item['authoritativeKsbMappings'] == [] for item in result['modules'] if item['moduleId'] != 'marketing-impact'))

    @patch('openai.OpenAI')
    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    def test_verified_whole_module_candidate_is_structured_and_provenance_bound(self, openai_client):
        payload = self.analysis_payload()
        result = {
            'overallOutcome': 'potential-whole-module-reduction',
            'reasoning': 'Independent evidence covers the complete saved module outcomes.',
            'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Model invented name', 'plannedHours': 999,
                'wholeModuleReductionRecommended': True, 'evidenceSufficiency': 'sufficient',
                'supportingEvidence': [{'evidenceId': 'evidence.qualification.1', 'detail': 'Verified module coverage.'}],
                'missingEvidence': [], 'explanation': 'The verified evidence covers the complete module outcomes.',
            }], 'evidenceGaps': [], 'warnings': [],
        }
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(result)), finish_reason='stop')],
            usage=None,
        )
        report = SimpleNamespace(source_snapshot={
            'programme': payload['programme'], 'learner': payload['learnerEvidence'], 'programmeUnderstanding': [],
            'skillsRadar': payload['skillsRadar'], 'evidence': {
                'cv': {'status': 'not_provided'}, 'qualifications': payload['qualifications'], 'plr': payload['plr'],
            }, 'trainingPlan': payload['trainingPlan'],
        }, source_snapshot_hash='snapshot-a', cv_source_document_id='cv-a', cv_source_container='approved',
            cv_source_blob_name='cv-a.pdf', cv_source_blob_hash='a' * 64, cv_extractor_version='cv-text-v1',
            cv_extraction_acknowledged=False)
        recommendation, metadata = generate_rpl_evidence_analysis(report)
        self.assertTrue(recommendation['modules'][0]['wholeModuleReductionRecommended'])
        self.assertEqual(recommendation['modules'][0]['moduleName'], 'Campaign planning')
        self.assertEqual(recommendation['modules'][0]['plannedHours'], 70)
        self.assertEqual(recommendation['modules'][0]['authoritativeKsbMappings'], payload['trainingPlan']['modules'][0]['authoritativeKsbMappings'])
        self.assertNotIn('ksbMappings', recommendation['modules'][0])
        self.assertEqual(metadata['sourceSnapshotHash'], 'snapshot-a')
        self.assertEqual(metadata['cvEvidenceVersion']['sha256'], 'a' * 64)
        prompt = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('complete module outcomes', prompt)
        self.assertIn('wholeModuleReductionRecommended', prompt)
        self.assertIn('evidenceSufficiency', prompt)
        sent_payload = json.loads(openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content'].split('Evidence for analysis:\n', 1)[1])
        sent_module = sent_payload['trainingPlan']['modules'][0]
        self.assertEqual(sent_module['authoritativeKsbCodes'], ['K1'])
        self.assertNotIn('ksbMappings', sent_module)
        evidence_ids = {item['id'] for item in sent_payload['evidenceReferences']}
        self.assertIn('evidence.skills-radar.1', evidence_ids)
        self.assertIn('evidence.qualification.1', evidence_ids)
        self.assertIn('evidence.plr.status', evidence_ids)
        self.assertIn('evidence.training-plan.module.module-a', evidence_ids)
        self.assertNotIn('K1', evidence_ids)

    def test_partial_prior_learning_cannot_be_presented_as_whole_module_candidate(self):
        payload = self.analysis_payload()
        with self.assertRaisesMessage(ReportInputError, 'complete mapped evidence'):
            _validate_rpl_analysis({
                'overallOutcome': 'potential-whole-module-reduction', 'reasoning': 'Some topics overlap.',
                'modules': [{
                    'moduleId': 'module-a', 'moduleName': 'Campaign planning', 'wholeModuleReductionRecommended': True, 'evidenceSufficiency': 'partial',
                    'supportingEvidence': [{'evidenceId': 'evidence.cv.approved', 'detail': 'Some campaign work.'}],
                    'missingEvidence': ['Complete outcome verification is missing.'], 'explanation': 'Partial overlap only.',
                }], 'evidenceGaps': [], 'warnings': [],
            }, payload)

    def test_missing_ksb_mappings_force_review_instead_of_module_removal(self):
        payload = self.analysis_payload(mappings=False)
        with self.assertRaisesMessage(ReportInputError, 'complete mapped evidence'):
            _validate_rpl_analysis({
                'overallOutcome': 'potential-whole-module-reduction', 'reasoning': 'A module may be removable.',
                'modules': [{
                    'moduleId': 'module-a', 'moduleName': 'Campaign planning', 'wholeModuleReductionRecommended': True, 'evidenceSufficiency': 'sufficient',
                    'supportingEvidence': [{'evidenceId': 'evidence.qualification.1', 'detail': 'Verified.'}],
                    'missingEvidence': [], 'explanation': 'No mappings were supplied.',
                }], 'evidenceGaps': [], 'warnings': [],
            }, payload)

    def test_model_ksb_mappings_are_ignored_and_authority_is_attached_server_side(self):
        payload = self.analysis_payload(mappings=False)
        result = _validate_rpl_analysis({
            'overallOutcome': 'insufficient-evidence',
            'reasoning': 'The evidence needs assessor review.',
            'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Campaign planning',
                'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'needs-review',
                'supportingEvidence': [{'evidenceId': 'evidence.skills-radar.1', 'detail': 'Self-assessed only.'}],
                # A legacy/malicious model field must never influence the result.
                'ksbMappings': [{'code': 'K999', 'description': 'Invented', 'source': 'model'}],
                'missingEvidence': ['Authoritative module mapping is unavailable.'],
                'explanation': 'The Skills Radar evidence is not enough to recommend removal.',
            }],
            'evidenceGaps': ['Module KSB mappings are unavailable.'], 'warnings': [],
        }, payload)
        self.assertEqual(result['modules'][0]['authoritativeKsbMappings'], [])
        self.assertNotIn('ksbMappings', result['modules'][0])

    def test_raw_private_cv_text_is_rejected_from_recommendation(self):
        payload = self.analysis_payload()
        payload['privateCvText'] = 'Distinctive synthetic CV secret and experience statement.'
        with self.assertRaisesMessage(ReportInputError, 'raw private CV text'):
            _validate_rpl_analysis({
                'overallOutcome': 'insufficient-evidence', 'reasoning': 'Distinctive synthetic CV secret and experience statement.',
                'modules': [{
                    'moduleId': 'module-a', 'moduleName': 'Campaign planning', 'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'insufficient',
                    'supportingEvidence': [],
                    'missingEvidence': ['Independent verification is missing.'], 'explanation': 'Review required.',
                }], 'evidenceGaps': [], 'warnings': [],
            }, payload)


class SuitabilityRplEvidenceMappingTests(SimpleTestCase):
    @patch('programme_suitability.rpl_report.programme_for', return_value='ME_L4')
    def test_reference_number_and_aptem_fallback_do_not_expose_other_ids(self, _programme_for_mock):
        learner = SimpleNamespace(
            pk=71, programme='Marketing Executive Level 4', reference_number='', aptem_id='APT-71',
            learner_start_date='', start_date='2026-09-01',
        )
        first = _context(learner, 'commercial', None)
        learner.aptem_id = 'APT-CHANGED'
        second = _context(learner, 'commercial', None)
        self.assertEqual(first['enrolmentReference'], 'APT-71')
        self.assertEqual(first['programmeId'], 'ME_L4')
        self.assertEqual(first['programmeName'], 'Marketing Executive Level 4')
        self.assertEqual(first['key'], second['key'])

        learner.reference_number = 'REF-71'
        preferred = _context(learner, 'commercial', None)
        self.assertEqual(preferred['enrolmentReference'], 'REF-71')

    @patch('programme_suitability.rpl_report.connections')
    @patch('programme_suitability.rpl_report.programme_for', return_value='PCP_L6')
    def test_canonical_programme_keeps_display_name_and_uses_curriculum_level(self, programme_for_mock, connections_mock):
        cursor = MagicMock()
        cursor.fetchall.return_value = [('PROG-PCP-L6 display name', '6')]
        connections_mock.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
        learner = SimpleNamespace(pk=72, programme='Project Controls Professional Level 6')
        layout = {'programmeSuitability': {'programmes': [{'id': 'PCP_L6', 'catalogueId': 'PROG-PCP-L6'}]}}
        result = _programme_metadata(learner, 'commercial', layout, {
            'programmeId': 'Project Controls Professional Level 6',
            'programmeName': 'Project Controls Professional Level 6',
        })
        self.assertEqual(result, {
            'id': 'PCP_L6', 'name': 'Project Controls Professional Level 6', 'level': '6',
        })
        programme_for_mock.assert_called_once_with(learner, 'commercial', layout)
        self.assertEqual(cursor.execute.call_args.args[1], ['PROG-PCP-L6'])

    @patch('programme_suitability.rpl_report.applicable')
    @patch('programme_suitability.rpl_report.read_legacy_radar')
    def test_skills_radar_merges_projected_rows_and_respects_pinned_catalogue(self, legacy_mock, applicable_mock):
        legacy_mock.return_value = {'assessments': {'projected': {'level': 'rarely'}}}
        applicable_mock.return_value = ({
            'version': 'historical-v1',
            'records': [
                {'id': 'projected', 'code': 'K1', 'kind': 'Knowledge', 'description': 'Projected evidence'},
                {'id': 'draft', 'code': 'S1', 'kind': 'Skill', 'description': 'Draft evidence'},
            ],
        }, True)
        draft = {'skillsRadar': {
            'catalogueVersions': {'PCP_L6': 'historical-v1'},
            'assessments': {'draft': {'level': 'champion'}},
        }}
        result = _skills_radar({}, draft, 'PCP_L6', 'commercial', 73)
        self.assertEqual({item['id']: item['score'] for item in result['items']}, {'projected': 2, 'draft': 9})
        self.assertEqual(result['scaleMaximum'], 9)
        self.assertEqual(result['catalogueVersion'], 'historical-v1')
        self.assertTrue(result['cataloguePinned'])
        self.assertEqual(result['summary'], [
            {'label': 'Knowledge', 'answered': 1, 'total': 1, 'score': 2.0},
            {'label': 'Skills', 'answered': 1, 'total': 1, 'score': 9.0},
            {'label': 'Behaviours', 'answered': 0, 'total': 0, 'score': None},
        ])
        legacy_mock.assert_called_once_with('commercial', 73)
        radar = applicable_mock.call_args.args[1]
        self.assertEqual(radar['catalogueVersions'], {'PCP_L6': 'historical-v1'})
        self.assertEqual(set(radar['assessments']), {'projected', 'draft'})

    @patch('programme_suitability.rpl_report.WizardCvJob.objects.filter')
    def test_ilr_highest_qualification_is_fallback_with_provenance(self, filter_mock):
        filter_mock.return_value.first.return_value = None
        details = SimpleNamespace(highest_qualification='Level 3 Diploma')
        result = _cv_evidence('apprenticeship', 74, {}, details)
        self.assertEqual(result['qualifications']['items'], [{
            'label': 'Highest qualification', 'value': 'Level 3 Diploma', 'source': 'ILR learner details',
        }])
        self.assertEqual(result['qualifications']['status'], 'provided')

    @patch('programme_suitability.rpl_report._training_plan')
    @patch('programme_suitability.rpl_report._cv_evidence')
    @patch('programme_suitability.rpl_report._skills_radar')
    @patch('programme_suitability.rpl_report._programme_metadata')
    @patch('programme_suitability.rpl_report._layout')
    @patch('programme_suitability.rpl_report.WizardIlrLearnerDetails.objects.filter')
    @patch('programme_suitability.rpl_report.ExtendedIlr.objects.filter')
    def test_complete_commercial_snapshot_maps_both_understanding_answers_and_separates_suitability(
        self, extended_filter, details_filter, layout_mock, programme_mock, skills_mock, cv_mock, training_mock,
    ):
        extended_filter.return_value.first.return_value = SimpleNamespace(
            answers={
                'understanding': {'programmeUnderstanding': 'First saved answer', 'careerProgression': 'Second saved answer'},
                'additionalInformation': {'jobRoleRelevance': 'Coordinates live project controls.'},
                'employer': {'organisationName': 'Synthetic Employer'},
            },
            wizard_draft={},
        )
        details_filter.return_value.first.return_value = SimpleNamespace(job_title='Project Controls Officer')
        layout_mock.return_value = (12, {'texts': {}})
        programme_mock.return_value = {'id': 'PCP_L6', 'name': 'Project Controls Professional Level 6', 'level': '6'}
        skills_mock.return_value = {'status': 'available', 'catalogueVersion': 'v1', 'items': []}
        cv_mock.return_value = {
            'cv': {'status': 'provided', 'experience': 'Maintains project schedules.', 'documents': []},
            'qualifications': {'status': 'not_provided', 'items': [], 'documents': []},
            'plr': {'status': 'not_available', 'message': 'Not available - integration not implemented'},
        }
        training_mock.return_value = {'available': False, 'modules': []}
        assessment = SimpleNamespace(
            selected_programme='PCP_L6', configuration={'questions': [{
                'id': 'fit', 'title': 'Programme fit', 'description': 'Which responsibilities apply?',
                'pathway': 'project-controls', 'order': 0,
                'answers': [
                    {'id': 'yes', 'text': 'Suitability-only answer', 'order': 0},
                    {'id': 'other', 'text': 'Unselected answer', 'order': 1},
                ],
            }]}, responses={'fit': ['yes']}, result={}, review={}, configuration_version=3, revision=4,
            modules_configuration_version=None, modules_revision=None,
        )
        learner = SimpleNamespace(
            pk=75, username='Synthetic Complete Learner', employer='', learning_plan=[], training_plan=[],
        )
        snapshot, versions = build_source_snapshot(learner, 'commercial', assessment, {
            'programmeId': 'PCP_L6', 'programmeName': 'Project Controls Professional Level 6',
            'enrolmentReference': 'SYN-75', 'practicalStartDate': '2026-11-01',
        })
        self.assertEqual([item['answer'] for item in snapshot['programmeUnderstanding']], ['First saved answer', 'Second saved answer'])
        self.assertEqual(snapshot['programmeSuitability']['pathway'], 'project-controls')
        self.assertEqual(snapshot['programmeSuitability']['responses'], [{
            'questionId': 'fit', 'group': 'Programme fit', 'question': 'Which responsibilities apply?',
            'answers': ['Suitability-only answer'],
            'selectedOptions': [{'id': 'yes', 'label': 'Suitability-only answer'}],
            'saved': True,
        }])
        self.assertEqual(snapshot['learner']['jobTitle'], 'Project Controls Officer')
        self.assertEqual(snapshot['learner']['responsibilities'], ['Coordinates live project controls.', 'Maintains project schedules.'])
        self.assertNotIn('Suitability-only answer', snapshot['learner']['responsibilities'])
        self.assertEqual(snapshot['programme'], {
            'id': 'PCP_L6', 'name': 'Project Controls Professional Level 6', 'level': '6', 'startDate': '2026-11-01',
        })
        self.assertEqual(versions['skillsRadarEffectiveCatalogueVersion'], 'v1')

    @patch('programme_suitability.rpl_report._training_plan', return_value={'available': False, 'modules': []})
    @patch('programme_suitability.rpl_report._cv_evidence', return_value={
        'cv': {'status': 'not_provided', 'experience': '', 'documents': []},
        'qualifications': {'status': 'not_provided', 'items': [], 'documents': []},
        'plr': {'status': 'not_available', 'message': 'Not available - integration not implemented'},
    })
    @patch('programme_suitability.rpl_report._skills_radar', return_value={'status': 'missing', 'catalogueVersion': '', 'items': []})
    @patch('programme_suitability.rpl_report._programme_metadata', return_value={'id': '', 'name': '', 'level': ''})
    @patch('programme_suitability.rpl_report._layout', return_value=(None, {}))
    @patch('programme_suitability.rpl_report.WizardIlrLearnerDetails.objects.filter')
    @patch('programme_suitability.rpl_report.ExtendedIlr.objects.filter')
    def test_incomplete_snapshot_keeps_evidence_blank_without_inventing_values(
        self, extended_filter, details_filter, *_mocks,
    ):
        extended_filter.return_value.first.return_value = None
        details_filter.return_value.first.return_value = None
        learner = SimpleNamespace(pk=76, username='Synthetic Incomplete Learner', employer='', learning_plan=[], training_plan=[])
        snapshot, _ = build_source_snapshot(learner, 'apprenticeship', None, {
            'programmeId': '', 'programmeName': '', 'enrolmentReference': '', 'practicalStartDate': '',
        })
        self.assertEqual([item['answer'] for item in snapshot['programmeUnderstanding']], ['', ''])
        self.assertEqual(snapshot['learner']['jobTitle'], '')
        self.assertEqual(snapshot['learner']['responsibilities'], [])
        self.assertEqual(snapshot['evidence']['plr']['status'], 'not_available')
