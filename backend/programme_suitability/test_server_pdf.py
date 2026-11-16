import re
from types import SimpleNamespace

from django.test import SimpleTestCase
from pypdf import PdfReader

from .server_pdf import render_finalised_report


class ServerSuitabilityPdfTests(SimpleTestCase):
    def test_render_uses_finalised_snapshot_and_excludes_private_metadata(self):
        report = SimpleNamespace(
            report_reference='RPL-TEST-1',
            relevant_prior_learning=False,
            reduction_justified=False,
            suitability_reviewed_at='2026-01-01',
            suitability_justification='Approved suitability narrative.',
            rpl_reviewed_at='2026-01-01',
            rpl_justification='Approved RPL narrative.',
            source_snapshot={
                'learner': {'id': '1601', 'reference': 'REF-1', 'jobTitle': 'Marketing Executive', 'employer': 'Synthetic Employer'},
                'programme': {'id': 'ME_L4', 'name': 'Marketing Executive Level 4', 'level': '4'},
                'programmeUnderstanding': [{'question': 'Q1', 'answer': 'Saved response'}],
                'programmeSuitability': {'responses': [{'group': 'Role', 'selectedOptions': [{'label': 'Campaign planning'}]}]},
                'skillsRadar': {'summary': [{'label': 'Communication', 'score': 7, 'answered': 2, 'total': 2}]},
                'trainingPlan': {'plannedHours': 368.25},
                'evidence': {'cv': {'status': 'provided', 'extractedText': 'PRIVATE-CV-SECRET'}, 'qualifications': {'items': []}},
            },
            reductions=SimpleNamespace(all=lambda: []),
        )
        pdf = render_finalised_report(report)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(__import__('io').BytesIO(pdf)).pages)
        for heading in ('Section 01', 'Section 02', 'Section 03', 'Section 04', 'Section 05', 'Section 06'):
            self.assertIn(heading, text)
        self.assertIn('Approved suitability narrative.', text)
        self.assertIn('Approved RPL narrative.', text)
        self.assertNotIn('PRIVATE-CV-SECRET', text)
        self.assertNotRegex(text, re.compile(r'(?i)(extractedText|rplAiMetadata|suitabilityAiMetadata)'))
