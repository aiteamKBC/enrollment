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

    def test_tightly_spaced_rows_remain_separate_in_both_sections(self):
        for section, row in (
            ('Learning Events', ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic Award', '', '', '', '', '', '']),
            ('Achievements', ['Synthetic College', '', 'Award', '3', 'Synthetic Award', 'SYN-A', '', '', '']),
        ):
            for identical in (True, False):
                with self.subTest(section=section, identical=identical):
                    second = row.copy()
                    reference_index = 3 if section == 'Learning Events' else 5
                    if not identical:
                        second[reference_index] = 'SYN-B'
                    records = parse_pdf(synthetic_pdf([(section, [row, second])], row_padding=1.5))['records']
                    self.assertEqual(len(records), 2)
                    self.assertEqual([r['referenceNumber'] for r in records], [row[reference_index], second[reference_index]])
                    self.assertTrue(all(r['subject'] == 'Synthetic Award' for r in records))

    def test_wrapped_reference_cell_is_one_record_in_both_sections(self):
        for section, row in (
            ('Learning Events', ['Synthetic College', '', 'Award', 'SYN-\n001\nA', 'Synthetic Award', '', '', '', '', '', '']),
            ('Achievements', ['Synthetic College', '', 'Award', '3', 'Synthetic Award', 'SYN-\n001\nA', '', '', '']),
        ):
            with self.subTest(section=section):
                records = parse_pdf(synthetic_pdf([(section, [row])]))['records']
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['referenceNumber'], 'SYN- 001 A')
                self.assertEqual(records[0]['subject'], 'Synthetic Award')

    def test_larger_line_spacing_preserves_wrapped_cells_and_next_record(self):
        first = ['Synthetic College\nNorth Campus', '', 'Extended\nCertificate', 'SYN-A',
                 'Synthetic award\ncontinued title', '', '', '', '', '', '']
        second = ['', '', 'Award', 'SYN-B', 'Separate award', '', '', '', '', '', '']
        for counts in (True, False):
            with self.subTest(printed_counts=counts):
                records = parse_pdf(synthetic_pdf([('Learning Events', [first, second])],
                    line_spacing=14, printed_counts=counts))['records']
                self.assertEqual(len(records), 2)
                self.assertEqual(records[0]['provider'], 'Synthetic College North Campus')
                self.assertEqual(records[0]['qualificationType'], 'Extended Certificate')
                self.assertEqual(records[0]['subject'], 'Synthetic award continued title')
                self.assertEqual(records[1]['referenceNumber'], 'SYN-B')

    def test_wrapped_reference_can_continue_on_the_next_page(self):
        first = ['Synthetic College', '', 'Award', 'SYN-', 'Synthetic award', '', '', '', '', '', '']
        continuation = ['', '', '', '001', 'continued title', '', '', '', '', '', '']
        for banner in (True, False):
            with self.subTest(repeat_banners=banner):
                records = parse_pdf(synthetic_pdf([('Learning Events', [first]), ('Learning Events', [continuation])],
                    totals_override={'Learning Events': 1}, repeat_banners=banner))['records']
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['referenceNumber'], 'SYN- 001')
                self.assertEqual(records[0]['subject'], 'Synthetic award continued title')

    def test_ambiguous_boundaries_are_not_guessed_from_a_record_count(self):
        first = ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic award', '', '', '', '', '', '']
        second = ['', '', '', 'SYN-B', '', '', '', '', '', '', '']
        third = ['', '', '', 'SYN-C', '', '', '', '', '', '', '']
        for printed_counts, total in ((True, 2), (False, 3)):
            with self.subTest(printed_counts=printed_counts):
                with self.assertRaises(PlrError) as error:
                    parse_pdf(synthetic_pdf([('Learning Events', [first, second, third])],
                        row_padding=1.5, printed_counts=printed_counts, totals_override={'Learning Events': total}))
                self.assertEqual(error.exception.code, 'ambiguous_rows')

    def test_unanchored_distant_text_is_not_silently_appended_to_a_record(self):
        first = ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic award', '', '', '', '', '', '']
        unknown = ['', '', '', '', 'Unidentified separate text', '', '', '', '', '', '']
        with self.assertRaises(PlrError):
            parse_pdf(synthetic_pdf([('Learning Events', [first, unknown])],
                printed_counts=False, row_padding=25))
