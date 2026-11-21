import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from . import lms_reviews_readonly as reviews, lms_readonly as reader


class ReviewReaderTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = {'reader_role': reader.READER_ROLE, 'read_only': 'on'}
        patch.object(reader, '_connect_safely', return_value=(self.conn, None)).start()
        self.addCleanup(patch.stopall)
        self.metadata = [dict(table_name=t, column_name=c, data_type=sorted(types)[0], readable=True, schema_usage=True)
            for t, columns in reviews.REQUIRED.items() for c, types in columns.items()]

    def read(self):
        return reviews.read_programme_reviews('confirmed-pid', 82, date(2027, 1, 1), date(2028, 1, 1))

    def test_select_only_scoped_parameters_and_read_only_cleanup(self):
        self.cursor.fetchall.side_effect = [self.metadata, [], []]
        self.assertEqual(self.read()['blockers'], [])
        self.assertTrue(self.conn.read_only)
        for call in self.cursor.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().upper().startswith('SELECT'))
        self.assertEqual(self.cursor.execute.call_args_list[2].args[1], ('confirmed-pid',))
        self.assertEqual(self.cursor.execute.call_args_list[3].args[1], ('confirmed-pid', 82, date(2027, 1, 1), date(2028, 1, 1)))
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called_once()
        self.conn.close.assert_called_once()

    def test_actual_missing_grants_are_explicit_and_denied_tables_are_not_selected(self):
        for row in self.metadata:
            row['readable'] = False
        self.cursor.fetchall.return_value = self.metadata
        result = self.read()
        self.assertEqual(len(self.cursor.execute.call_args_list), 2)
        self.assertTrue(all(b['code'] == 'select_denied' for b in result['blockers']))
        self.assertEqual({b['table'] for b in result['blockers']}, set(reviews.REQUIRED))
        self.assertTrue(all(b['columns'] for b in result['blockers']))

    def test_connection_guard_and_missing_tables_fail_closed(self):
        self.cursor.fetchone.return_value['reader_role'] = 'owner'
        self.assertEqual(self.read()['blockers'][0]['code'], 'read_only_not_confirmed')
        self.cursor.fetchone.return_value['reader_role'] = reader.READER_ROLE
        self.cursor.fetchall.return_value = []
        self.assertTrue(all(b['code'] == 'table_unavailable' for b in self.read()['blockers']))

    def test_overflow_never_silently_returns_partial_templates(self):
        self.cursor.fetchall.side_effect = [self.metadata, [{}] * 501, []]
        result = self.read()
        self.assertIsNone(result['review_templates'])
        self.assertEqual(result['blockers'][0]['code'], 'result_incomplete')

    def test_read_errors_and_invalid_identity_are_safe(self):
        self.cursor.execute.side_effect = RuntimeError('Synthetic connection failure')
        self.assertEqual(self.read()['blockers'][0]['code'], 'read_failed')
        self.conn.rollback.assert_called_once()
        with self.assertRaises(ValueError):
            reviews.read_programme_reviews('', 82, date(2027, 1, 1), date(2028, 1, 1))
