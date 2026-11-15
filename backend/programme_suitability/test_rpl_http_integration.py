import io
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from .cv_extraction import EXTRACTOR_VERSION
from .models import SuitabilityCvExtraction, SuitabilityRplReport
from .test_auth_fixture import SuitabilityAuthFixtureMixin


URL = '/apprenticeship/1601/suitability-rpl/'


def payload(**overrides):
    value = {'revision': 0, 'relevantPriorLearning': True, 'reductionJustified': False,
             'assessorEvidenceReview': 'Verified synthetic evidence.', 'noReductionReason': 'No reduction justified.',
             'suitabilityOutcome': 'suitable', 'finalSummary': 'Proceed.', 'suitabilityJustification': 'Suitable.',
             'suitabilityReviewed': False, 'rplJustification': '', 'rplReviewed': False, 'reductions': [], 'finalise': False}
    value.update(overrides); return value


class RplHttpIntegrationTests(SuitabilityAuthFixtureMixin, TestCase):
    databases = {'default', 'enrolment'}

    def ready_extraction(self, text='PRIVATE-SYNTHETIC-CV-SECRET', doc='00000000-0000-0000-0000-000000000001', blob='1601/cv-a.pdf', digest='a'*64):
        return SuitabilityCvExtraction.objects.create(learner_kind='apprenticeship', learner_id=1601, source_document_id=doc, source_container='synthetic-approved', source_blob_name=blob, source_blob_hash=digest, extractor_version=EXTRACTOR_VERSION, status='ready', extracted_text=text)

    def save_draft(self):
        response = self.admin_client.put(URL, data=json.dumps(payload()), content_type='application/json')
        self.assertEqual(response.status_code, 200); return response.json()['report']

    def test_admin_acknowledgement_real_http_and_non_admin_rejection(self):
        data = payload(cvExtractionAcknowledged=True, cvExtractionAcknowledgementReason='Synthetic unreadable CV')
        response = self.admin_client.put(URL, data=json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        report = SuitabilityRplReport.objects.get(pk=response.json()['report']['id'])
        self.assertEqual(report.cv_extraction_acknowledgement_reason, 'Synthetic unreadable CV')
        self.assertEqual(report.cv_extraction_acknowledged_by, self.admin.id)
        self.assertIsNotNone(report.cv_extraction_acknowledged_at)
        self.assertEqual(self.staff_client.put(URL, data=json.dumps(data), content_type='application/json').status_code, 403)
        from django.test import Client
        self.assertEqual(Client().put(URL, data=json.dumps(data), content_type='application/json').status_code, 401)

    @patch('openai.OpenAI')
    @patch('programme_suitability.rpl_report.extract_approved_cv')
    def test_rpl_generation_metadata_and_finalisation(self, extract, openai):
        self.ready_extraction(); extract.return_value = SuitabilityCvExtraction.objects.filter(source_document_id='cv-1').first()
        draft = self.save_draft()
        openai.return_value.chat.completions.create.return_value = type('R', (), {'choices': [type('C', (), {'message': type('M', (), {'content': json.dumps({'justification': 'Synthetic RPL justification.'})})()})()]})()
        with patch('django.conf.settings.SUITABILITY_RPL_AI_ENABLED', True), patch('django.conf.settings.OPENAI_API_KEY', 'synthetic'):
            generated = self.admin_client.post(URL + 'ai/rpl/', data='{}', content_type='application/json')
        self.assertEqual(generated.status_code, 200)
        report = SuitabilityRplReport.objects.get(pk=draft['id'])
        self.assertEqual(report.rpl_ai_metadata['cvEvidenceVersion']['sha256'], 'a'*64)

    def test_deletion_cleanup_and_status_api(self):
        self.ready_extraction()
        response = self.admin_client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['evidence']['evidence']['cv']['extractionStatus'], 'ready')
        SuitabilityCvExtraction.objects.all().delete()
        self.assertEqual(self.admin_client.get(URL).json()['evidence']['evidence']['cv']['extractionStatus'], 'pending')

    @patch('openai.OpenAI')
    @patch('programme_suitability.rpl_report._rpl_analysis_payload')
    def test_rpl_analysis_endpoint_returns_advisory_modules_without_changing_decision(self, analysis_payload, openai):
        saved = self.save_draft()
        analysis_payload.return_value = {
            'programme': {'id': 'PM-L5'}, 'learnerEvidence': {}, 'programmeUnderstanding': [],
            'skillsRadar': {}, 'qualifications': {}, 'plr': {'status': 'not_available'},
            'trainingPlan': {'available': True, 'plannedHours': 120, 'modules': [
                {'moduleId': 'module-a', 'moduleName': 'Authoritative module A', 'plannedHours': 70, 'content': '', 'authoritativeKsbMappings': []},
                {'moduleId': 'module-b', 'moduleName': 'Authoritative module B', 'plannedHours': 50, 'content': '', 'authoritativeKsbMappings': []},
            ]},
            'privateCvText': '', 'extractionWarning': '', 'evidenceReferences': [],
            'evidenceVersion': {'sourceSnapshotHash': SuitabilityRplReport.objects.get(pk=saved['id']).source_snapshot_hash, 'documentId': '', 'container': '', 'blobName': '', 'sha256': '', 'extractorVersion': ''},
        }
        recommendation = {
            'overallOutcome': 'insufficient-evidence',
            'reasoning': 'The supplied evidence requires assessor review before any whole-module decision.',
            'modules': [{
                'moduleId': 'module-a', 'moduleName': 'Authoritative module A', 'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'insufficient',
                'supportingEvidence': [], 'authoritativeKsbMappings': [],
                'missingEvidence': ['Independent outcome verification is missing.'],
                'explanation': 'Partial or self-assessed evidence cannot support removal of the complete module.',
            }, {
                'moduleId': 'module-b', 'moduleName': 'Authoritative module B', 'wholeModuleReductionRecommended': False, 'evidenceSufficiency': 'insufficient',
                'supportingEvidence': [], 'authoritativeKsbMappings': [],
                'missingEvidence': ['Independent outcome verification is missing.'],
                'explanation': 'No complete-module exemption is supported by the supplied evidence.',
            }],
            'evidenceGaps': ['PLR is not recorded.'], 'warnings': [],
        }
        openai.return_value.chat.completions.create.return_value = type('R', (), {
            'choices': [type('C', (), {'message': type('M', (), {'content': json.dumps(recommendation)})(), 'finish_reason': 'stop'})()],
            'usage': None,
        })()
        with patch('django.conf.settings.SUITABILITY_RPL_AI_ENABLED', True), patch('django.conf.settings.OPENAI_API_KEY', 'synthetic'):
            response = self.admin_client.post(URL + 'ai/rpl-analysis/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['recommendation']['overallOutcome'], 'insufficient-evidence')
        self.assertEqual(body['metadata']['sourceSnapshotHash'], SuitabilityRplReport.objects.get(pk=saved['id']).source_snapshot_hash)
        current = SuitabilityRplReport.objects.get(pk=saved['id'])
        self.assertFalse(current.relevant_prior_learning)
        self.assertFalse(current.reduction_justified)
        self.assertNotIn('PRIVATE-ANALYSIS-CV', json.dumps(body))

    def test_rpl_analysis_endpoint_remains_admin_only(self):
        self.assertEqual(self.staff_client.post(URL + 'ai/rpl-analysis/', data='{}', content_type='application/json').status_code, 403)
        from django.test import Client
        self.assertEqual(Client().post(URL + 'ai/rpl-analysis/', data='{}', content_type='application/json').status_code, 401)

    def test_private_secret_absent_from_snapshot_and_report_response(self):
        self.ready_extraction()
        body = json.dumps(self.admin_client.get(URL).json())
        self.assertNotIn('PRIVATE-SYNTHETIC-CV-SECRET', body)

    def test_missing_cv_status_is_distinct(self):
        from enrolment_api.models import WizardCvJob
        WizardCvJob.objects.filter(learner_id=1601).update(documents=[])
        body = self.admin_client.get(URL).json()
        self.assertEqual(body['evidence']['evidence']['cv']['status'], 'not_provided')

    @patch('openai.OpenAI')
    @patch('programme_suitability.rpl_report.extract_approved_cv')
    def test_complete_authenticated_rpl_workflow_succeeds(self, extract, openai):
        extraction = self.ready_extraction('CV-A-EXPERIENCE')
        extract.return_value = extraction
        openai.return_value.chat.completions.create.return_value = type('R', (), {'choices': [type('C', (), {'message': type('M', (), {'content': json.dumps({'justification': 'Verified RPL justification for CV A.'})})()})()]})()
        with patch('django.conf.settings.SUITABILITY_RPL_AI_ENABLED', True), patch('django.conf.settings.OPENAI_API_KEY', 'synthetic'):
            saved = self.admin_client.put(URL, data=json.dumps(payload()), content_type='application/json')
            self.assertEqual(saved.status_code, 200)
            generated = self.admin_client.post(URL + 'ai/rpl/', data='{}', content_type='application/json')
            self.assertEqual(generated.status_code, 200)
            report = SuitabilityRplReport.objects.get(pk=saved.json()['report']['id'])
            sent = openai.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
            self.assertIn('CV-A-EXPERIENCE', sent)
            self.assertEqual(report.rpl_ai_metadata['cvEvidenceVersion']['sha256'], 'a' * 64)
            approve = payload(revision=report.revision, rplJustification=report.rpl_justification, rplReviewed=True, suitabilityReviewed=True, finalise=True)
            finalised = self.admin_client.put(URL, data=json.dumps(approve), content_type='application/json')
            self.assertEqual(finalised.status_code, 200)
            self.assertEqual(finalised.json()['report']['status'], 'finalised')

    @patch('programme_suitability.rpl_report.extract_approved_cv')
    def test_cv_replacement_rejects_old_rpl_approval_and_finalisation(self, extract):
        first = self.ready_extraction('CV-A-EXPERIENCE')
        extract.return_value = first
        report_response = self.admin_client.put(URL, data=json.dumps(payload()), content_type='application/json')
        self.assertEqual(report_response.status_code, 200)
        report = SuitabilityRplReport.objects.get(pk=report_response.json()['report']['id'])
        report.cv_source_document_id = '00000000-0000-0000-0000-000000000001'; report.cv_source_container = 'synthetic-approved'; report.cv_source_blob_name = '1601/cv-a.pdf'; report.cv_source_blob_hash = 'a' * 64; report.cv_extractor_version = EXTRACTOR_VERSION
        report.rpl_justification = 'Old generated justification'; report.rpl_ai_metadata = {'cvEvidenceVersion': {'documentId': '00000000-0000-0000-0000-000000000001', 'sha256': 'a' * 64, 'extractorVersion': EXTRACTOR_VERSION}}; report.save()
        second = self.ready_extraction('CV-B-EXPERIENCE', digest='b' * 64)
        extract.return_value = second
        stale = payload(revision=report.revision, rplJustification=report.rpl_justification, rplReviewed=True, suitabilityReviewed=True, finalise=False)
        rejected_approval = self.admin_client.put(URL, data=json.dumps(stale), content_type='application/json')
        self.assertEqual(rejected_approval.status_code, 409 if rejected_approval.status_code == 409 else 400)
        stale['revision'] += 1; stale['finalise'] = True
        rejected_finalise = self.admin_client.put(URL, data=json.dumps(stale), content_type='application/json')
        self.assertIn(rejected_finalise.status_code, (400, 409))

    @override_settings(ROOT_URLCONF='programme_suitability.test_auth_urls')
    @patch('programme_suitability.cv_extraction.evidence_storage.download_blob_bytes')
    def test_cv_deletion_http_cleans_private_extraction_and_blocks_reuse(self, download):
        extraction = self.ready_extraction('DELETE-ME'); download.return_value = b'not-used'
        from enrolment_api.models import WizardCvJob
        self.assertIn('00000000-0000-0000-0000-000000000001', [item['id'] for item in WizardCvJob.objects.get(learner_id=1601).documents])
        self.assertIn('00000000-0000-0000-0000-000000000001', [item['id'] for item in WizardCvJob.objects.using('enrolment').get(learner_id=1601).documents])
        from enrolment_api.extended_ilr import KINDS
        self.assertTrue(KINDS['apprenticeship'].objects.filter(pk=1601).exists())
        response = self.admin_client.delete('/enrolment_api/wizard/apprenticeship/1601/cv-documents/00000000-0000-0000-0000-000000000001/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SuitabilityCvExtraction.objects.filter(pk=extraction.pk).exists())
        self.assertEqual(self.staff_client.delete('/enrolment_api/wizard/apprenticeship/1601/cv-documents/00000000-0000-0000-0000-000000000001/').status_code, 404)

    @patch('programme_suitability.rpl_report.extract_approved_cv')
    @patch('openai.OpenAI')
    def test_private_secret_reaches_mock_ai_but_not_snapshot_or_pdf(self, openai, extract):
        extraction = self.ready_extraction('UNIQUE-PRIVATE-CV-SECRET'); extract.return_value = extraction
        openai.return_value.chat.completions.create.return_value = type('R', (), {'choices': [type('C', (), {'message': type('M', (), {'content': json.dumps({'justification': 'Safe summary'})})()})()]})()
        with patch('django.conf.settings.SUITABILITY_RPL_AI_ENABLED', True), patch('django.conf.settings.OPENAI_API_KEY', 'synthetic'):
            saved = self.admin_client.put(URL, data=json.dumps(payload()), content_type='application/json')
            self.assertEqual(saved.status_code, 200)
            self.admin_client.post(URL + 'ai/rpl/', data='{}', content_type='application/json')
        sent = openai.return_value.chat.completions.create.call_args.kwargs['messages'][1]['content']
        self.assertIn('UNIQUE-PRIVATE-CV-SECRET', sent)
        response_text = json.dumps(self.admin_client.get(URL).json())
        self.assertNotIn('UNIQUE-PRIVATE-CV-SECRET', response_text)

    def test_extraction_status_api_states(self):
        statuses = [('pending', 'pending'), ('ready', 'ready'), ('failed', 'failed'), ('unsupported', 'unsupported')]
        for source, expected in statuses:
            SuitabilityCvExtraction.objects.create(learner_kind='apprenticeship', learner_id=1601, source_document_id=f'cv-{source}', source_container='synthetic-approved', source_blob_name=f'{source}.pdf', source_blob_hash=source.ljust(64, '0'), status=source, extracted_text='PRIVATE')
        self.assertEqual(self.admin_client.get(URL).status_code, 200)
        self.assertEqual(self.admin_client.get(URL).json()['evidence']['evidence']['cv']['extractionStatus'], 'pending')
