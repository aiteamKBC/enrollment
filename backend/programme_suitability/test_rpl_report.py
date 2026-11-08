"""Phase 2 report tests. The configured runner uses in-memory SQLite only."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .models import SuitabilityRplReport
from .rpl_report import (
    ReportInputError,
    _context,
    _cv_evidence,
    _ai_input,
    _programme_metadata,
    _save_report,
    _skills_radar,
    build_source_snapshot,
    generate_ai_justification,
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

        version_two = self.save(self.context_a, base_payload(newVersion=True))
        self.assertEqual(version_two.report_version, 2)
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

    @override_settings(SUITABILITY_RPL_AI_ENABLED=False, OPENAI_API_KEY='')
    def test_ai_is_disabled_by_default_without_network_call(self):
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        with self.assertRaisesMessage(ReportInputError, 'disabled'):
            generate_ai_justification(report, 'suitability')

    @override_settings(SUITABILITY_RPL_AI_ENABLED=True, OPENAI_API_KEY='synthetic-key', OPENAI_MODEL='synthetic-model')
    @patch('openai.OpenAI')
    def test_ai_output_is_a_draft_only_and_uses_synthetic_input(self, openai_client):
        message = SimpleNamespace(content=json.dumps({'justification': 'Synthetic editable draft.'}))
        openai_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
        )
        report = SuitabilityRplReport(source_snapshot=source_snapshot())
        text, metadata = generate_ai_justification(report, 'suitability')
        self.assertEqual(text, 'Synthetic editable draft.')
        self.assertEqual(metadata['provider'], 'openai')
        sent = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
        self.assertIn('Do not invent facts', sent)
        evidence_payload = openai_client.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertNotIn('Synthetic Learner', evidence_payload)
        self.assertIn('Project Officer', evidence_payload)

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

    def test_ai_payloads_preserve_suitability_groups_and_programme_specification(self):
        snapshot = source_snapshot()
        snapshot['programmeSuitability'] = {
            'pathway': 'marketing', 'selectedProgramme': 'MM_L6',
            'selectedProgrammeSpecification': {'id': 'MM_L6', 'title': 'Marketing Manager Level 6', 'summary': 'Synthetic summary'},
            'responses': [{'group': 'Marketing Strategy', 'question': 'Which responsibilities apply?',
                           'answers': ['I lead strategy'], 'selectedOptions': [{'id': 'a', 'label': 'I lead strategy'}]}],
        }
        snapshot['programmeUnderstanding'] = [{'question': 'Q1', 'answer': 'Understanding'}, {'question': 'Q2', 'answer': 'Progression'}]
        report = SimpleNamespace(source_snapshot=snapshot)
        payload = _ai_input(report, 'suitability')
        self.assertEqual(payload['programmeUnderstanding'][1]['answer'], 'Progression')
        self.assertEqual(payload['programmeSuitability']['responses'][0]['selectedOptions'][0]['label'], 'I lead strategy')
        self.assertEqual(payload['programmeSuitability']['selectedProgrammeSpecification']['id'], 'MM_L6')


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
