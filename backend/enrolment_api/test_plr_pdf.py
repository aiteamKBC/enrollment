import unittest
from .plr_fixtures import synthetic_pdf, EVENT_COLUMNS
from .plr_pdf import PlrError, parse_pdf, parse_bounded


class SyntheticPdfTests(unittest.TestCase):
    def test_both_sections_multiline_empty_dates_and_original_grades(self):
        result = parse_pdf(synthetic_pdf())
        self.assertEqual(result['uln'], '1234509876')
        self.assertEqual(len(result['records']), 3)
        first, withdrawn, achieved = result['records']
        self.assertEqual(first['provider'], 'Fictional Academy North Campus')
        self.assertEqual(first['subject'], 'Award in Fictional Payroll Practice (RQF)')
        self.assertEqual(first['grade'], 'L1')
        self.assertNotIn('level', first)
        self.assertIsNone(first['awardDate'])
        self.assertEqual(withdrawn['completionStatus'], 'Withdrawn')
        self.assertEqual(withdrawn['grade'], 'Not Applicable')
        self.assertEqual(achieved['level'], '3')
        self.assertEqual(achieved['grade'], '0')
        self.assertEqual(achieved['awardDate'], '2022-07-20')

    def test_zero_sections(self):
        result = parse_pdf(synthetic_pdf([('Learning Events', []), ('Achievements', [])]))
        self.assertEqual(result['records'], [])

    def test_same_section_across_pages(self):
        row = ['Example School', '', 'Award', 'SYN-A', 'Example award', '', '', 'L1', '', '', '']
        second = [*row]; second[3] = 'SYN-B'
        self.assertEqual(len(parse_pdf(synthetic_pdf([('Learning Events', [row]), ('Learning Events', [second])]))['records']), 2)

    def test_missing_uln_does_not_pick_ten_digit_qualification_reference(self):
        with self.assertRaisesRegex(PlrError, 'valid ULN'):
            parse_pdf(synthetic_pdf(uln=''))

    def test_partial_report_rejected(self):
        with self.assertRaisesRegex(PlrError, 'part of'):
            parse_pdf(synthetic_pdf(displayed=1))

    def test_bad_file_rejected(self):
        for data in (b'not a pdf', b'%PDF-corrupted'):
            with self.assertRaises(PlrError): parse_pdf(data)

    def test_bounded_worker(self):
        self.assertEqual(parse_bounded(synthetic_pdf())['uln'], '1234509876')

    def test_multiline_provider_title_and_type_stay_in_one_row(self):
        row = ['Fictional Academy\nNorth Campus', '', 'Extended\nCertificate', 'SYN-A',
               'A long fictional\nqualification title', '', '', '', '', '', '']
        result = parse_pdf(synthetic_pdf([('Learning Events', [row])]))
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['qualificationType'], 'Extended Certificate')
        self.assertEqual(result['records'][0]['subject'], 'A long fictional qualification title')

    def test_continuation_row_across_pages_with_and_without_section_banner(self):
        row = ['Fictional College', '', 'Award', 'SYN-A', 'A long fictional', '', '', '', '', '', '']
        continuation = ['North Campus', '', '', '', 'qualification continued', '', '', 'Pass', '', '', '']
        for banner in [True, False]:
            result = parse_pdf(synthetic_pdf([('Learning Events', [row]), ('Learning Events', [continuation])],
                totals_override={'Learning Events': 1}, repeat_banners=banner))
            self.assertEqual(len(result['records']), 1)
            self.assertEqual(result['records'][0]['subject'], 'A long fictional qualification continued')
            self.assertEqual(result['records'][0]['provider'], 'Fictional College North Campus')

    def test_empty_provider_missing_dates_and_explicit_credits_are_not_inferred(self):
        columns = [*EVENT_COLUMNS[:-1], ('Credits', 740), EVENT_COLUMNS[-1]]
        row = ['', '', 'Award', 'SYN-A', 'Fictional qualification', '', '20 Jul 2022', 'L1', '', '', '24', '']
        result = parse_pdf(synthetic_pdf([('Learning Events', [row])], event_columns=columns))['records'][0]
        self.assertNotIn('provider', result)
        self.assertEqual(result['credits'], '24')
        self.assertIsNone(result['awardDate'])
        self.assertNotIn('level', result)

    def test_horizontal_watermark_and_missing_printed_count(self):
        result = parse_pdf(synthetic_pdf(printed_counts=False, horizontal_watermark=True))
        self.assertEqual(len(result['records']), 3)
        self.assertIsNone(result['reportCounts']['Achievements'])

    def test_disagreeing_record_count_is_rejected(self):
        with self.assertRaisesRegex(PlrError, 'count does not match'):
            parse_pdf(synthetic_pdf(totals_override={'Learning Events': 9}))

    def test_encrypted_pdf_is_rejected(self):
        import pymupdf
        with pymupdf.open(stream=synthetic_pdf(), filetype='pdf') as document:
            data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='synthetic-owner', user_pw='synthetic-password')
        with self.assertRaisesRegex(PlrError, 'unencrypted'):
            parse_pdf(data)

    def test_continuation_pages_can_repeat_only_the_report_title(self):
        self.assertEqual(len(parse_pdf(synthetic_pdf(repeat_identity=False))['records']), 3)

    def test_parser_timeout_is_a_clear_error(self):
        import subprocess
        from unittest.mock import patch
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('synthetic-worker', 20)):
            with self.assertRaisesRegex(PlrError, 'safety limits'):
                parse_bounded(synthetic_pdf())

    def test_sparse_rows_preserve_blank_fields_instead_of_merging_qualifications(self):
        first = ['Fictional College', '', 'Award', 'SYN-A', 'Fictional Award', '', '', '', '', '', '']
        second = ['', '', '', 'SYN-B', '', '', '', '', '', '', '']
        result = parse_pdf(synthetic_pdf([('Learning Events', [first, second])]))
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(result['records'][1]['referenceNumber'], 'SYN-B')
        self.assertNotIn('subject', result['records'][1])
