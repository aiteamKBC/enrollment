import re
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from pypdf import PdfReader

from .server_pdf import SECTION_TITLES, render_finalised_report, render_report


class ServerSuitabilityPdfTests(SimpleTestCase):
    def test_finalised_entry_point_delegates_to_the_canonical_renderer(self):
        report = SimpleNamespace()
        with patch('programme_suitability.server_pdf.render_report', return_value=b'%PDF-canonical') as renderer:
            self.assertEqual(render_finalised_report(report), b'%PDF-canonical')
        renderer.assert_called_once_with(report)

    def test_render_uses_finalised_snapshot_and_excludes_private_metadata(self):
        report = SimpleNamespace(
            status='finalised', report_version=2, revision=3, finalised_at='2026-01-01',
            report_reference='RPL-TEST-1',
            relevant_prior_learning=False,
            reduction_justified=False,
            assessor_evidence_review='Saved assessor evidence review.',
            no_reduction_reason='',
            suitability_outcome='suitable',
            final_summary='Proceed with the full programme.',
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
                'evidence': {'cv': {'status': 'provided', 'experience': 'Saved role experience.', 'extractedText': 'PRIVATE-CV-SECRET'}, 'qualifications': {'items': []}},
            },
            reductions=SimpleNamespace(all=lambda: []),
        )
        pdf = render_finalised_report(report)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(pdf)).pages)
        for heading in SECTION_TITLES:
            self.assertIn(heading, text)
        self.assertIn('Kent Business College', text)
        self.assertIn('FINALISED', text)
        self.assertIn('Approved suitability narrative.', text)
        self.assertIn('Approved RPL narrative.', text)
        self.assertIn('Saved assessor evidence review.', text)
        self.assertIn('Proceed with the full programme.', text)
        self.assertNotIn('PRIVATE-CV-SECRET', text)
        self.assertNotRegex(text, re.compile(r'(?i)(extractedText|rplAiMetadata|suitabilityAiMetadata)'))

    def test_radar_draws_saved_scores_to_scale_and_keeps_evidence_below(self):
        report = SimpleNamespace(status='finalised', source_snapshot={'skillsRadar': {
            'scaleMaximum': 5,
            'items': [{'description': name, 'score': score} for name, score in (
                ('Communication', 4), ('Teamwork', 4), ('Planning', 3),
                ('Leadership', 2), ('Problem solving', 3), ('Digital skills', 4),
            )],
        }})
        pdf = render_report(report)
        self.assertEqual(pdf, render_finalised_report(report))
        pages = PdfReader(BytesIO(pdf)).pages
        radar_page = next(page for page in pages if 'SKILLS RADAR & AVAILABLE EVIDENCE' in page.extract_text())
        text = radar_page.extract_text()
        for value in ('04', 'AUTOMATIC VALUES', 'COMPETENCY', 'SCORE', 'Communication', 'Digital skills', '4/5', '3/5', '2/5'):
            self.assertIn(value, text)
        self.assertLess(text.index('independently prove prior learning'), text.index(SECTION_TITLES[4]))
        # Five vector grid rings and the saved-score polygon have six sides each.
        operations = [op for _, op in radar_page.get_contents().operations]
        hexagon = [b'm', *([b'l'] * 5), b'h']
        self.assertGreaterEqual(sum(operations[i:i + len(hexagon)] == hexagon for i in range(len(operations))), 6)

    def test_missing_radar_scores_do_not_become_zero_values(self):
        report = SimpleNamespace(source_snapshot={'skillsRadar': {
            'scaleMaximum': 9,
            'items': [{'code': 'B1', 'score': 7}, {'code': 'K1', 'score': None}, {'code': 'S1', 'score': 0}],
        }})
        pages = PdfReader(BytesIO(render_report(report))).pages
        text = next(text for page in pages if 'SKILLS RADAR PREVIEW' in (text := page.extract_text()))
        self.assertIn('7/9', text)
        self.assertIn('N/A', text)
        self.assertNotIn('0/9', text)

    def test_long_report_paginates_without_losing_approved_sections(self):
        report = SimpleNamespace(
            status='draft', report_reference='DRAFT', report_version=1, revision=4,
            finalised_at=None, practical_start_date='', relevant_prior_learning=False,
            reduction_justified=False, assessor_evidence_review='Reviewed evidence.',
            no_reduction_reason='', suitability_outcome='suitable', final_summary='Proceed.',
            suitability_reviewed_at='2026-01-01', suitability_justification='Suitable.',
            rpl_reviewed_at='2026-01-01', rpl_justification=('Long approved RPL narrative. ' * 400) + 'Narrative conclusion.',
            source_snapshot={
                'learner': {'name': 'Long Report Learner'},
                'programme': {'name': 'Marketing Executive Level 4'},
                'programmeUnderstanding': [
                    {'question': f'Question {index}', 'answer': ('Long saved response ' * 700) if index == 0 else ('A saved response ' * 12)}
                    for index in range(8)
                ],
                'programmeSuitability': {'responses': [{'group': f'Group {index}', 'answers': [f'Answer {index}', 'Second answer']} for index in range(30)]},
                'skillsRadar': {
                    'summary': [{'label': 'Knowledge', 'score': 7, 'answered': 60, 'total': 60}],
                    'items': [{'code': f'K{index}', 'description': 'A saved skills radar item with descriptive content.', 'score': index % 9} for index in range(60)],
                },
                'evidence': {'cv': {'status': 'provided', 'documents': [{'filename': 'cv.pdf'}]}, 'qualifications': {'items': []}, 'plr': {'message': 'Not available'}},
                'trainingPlan': {'plannedHours': 368},
            },
            reductions=SimpleNamespace(all=lambda: []),
        )
        pdf = render_finalised_report(report)
        reader = PdfReader(BytesIO(pdf))
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        self.assertGreater(len(reader.pages), 4)
        for heading in SECTION_TITLES:
            self.assertIn(heading, text)
        self.assertIn('Group 29', text)
        self.assertIn('K59', text)
        self.assertIn('Narrative conclusion.', ' '.join(text.split()))
        self.assertEqual(text.count('SKILLS RADAR & AVAILABLE EVIDENCE'), 1)
        self.assertIn('All saved items are listed below the evidence.', text)
