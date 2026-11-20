"""Approved-reader boundary, SELECT-only SQL and explicit access blockers."""
import importlib
import unittest
from unittest.mock import MagicMock, patch
from . import lms_activity_readonly as activities
from . import lms_readonly as reader


def metadata():
    return [dict(table_name=table, column_name=column, data_type=sorted(types)[0], schema_usage=True, readable=True)
            for table, columns in activities.REQUIRED.items() for column, types in columns.items()]


class ActivityReaderTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = {'reader_role': reader.READER_ROLE, 'read_only': 'on'}
        self.connect = patch.object(reader, '_connect_safely', return_value=(self.connection, None)).start()
        self.addCleanup(patch.stopall)
        self.rows = metadata()
        self.module = dict(module_catalogue_id='confirmed-id', programme_id='programme', cohort_id='home', group_id='g1',
                           title='Verified title', ksb_profile_source_id='profile')

    def read(self):
        return activities.get_module_activities('confirmed-id', 'programme', 'home', 'g1')

    def test_import_does_not_connect(self):
        importlib.reload(activities)
        self.connect.assert_not_called()

    def test_exact_scope_select_only_read_only_guard_and_rollback(self):
        self.cursor.fetchall.side_effect = [self.rows, [self.module], [], [], []]
        result = self.read()
        self.assertEqual(result['modules'], [self.module])
        self.assertTrue(self.connection.read_only)
        for call in self.cursor.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().upper().startswith('SELECT'))
        query, parameters = self.cursor.execute.call_args_list[2].args
        self.assertEqual(parameters, ('confirmed-id', 'programme', 'home', 'g1'))
        self.assertIn('AND cohort_id=%s AND group_id=%s', query)
        self.assertNotIn('total_otjh', query)
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_missing_child_permissions_are_reported_without_attempting_select(self):
        for row in self.rows:
            row['readable'] = row['table_name'] == 'modules'
        self.cursor.fetchall.side_effect = [self.rows, [self.module]]
        result = self.read()
        self.assertIsNone(result['components'])
        self.assertEqual({b['table'] for b in result['blockers']},
            {'weeks', 'components', 'ksb_mappings', 'ksb_profiles', 'ksb_profile_versions', 'ksb_definitions'})
        self.assertTrue(all(b['code'] == 'select_denied' for b in result['blockers']))
        self.assertEqual(len(self.cursor.execute.call_args_list), 3)

    def test_bad_guard_and_missing_configuration_do_not_read_content(self):
        self.cursor.fetchone.return_value = {'reader_role': 'owner', 'read_only': 'on'}
        self.assertEqual(self.read()['blockers'][0]['code'], 'read_only_not_confirmed')
        self.cursor.fetchall.assert_not_called()
        self.connect.return_value = (None, 'not_configured')
        self.assertEqual(self.read()['blockers'][0]['code'], 'not_configured')

    def test_missing_schema_columns_and_types_are_not_silently_substituted(self):
        rows = [r for r in self.rows if not (r['table_name'] == 'components' and r['column_name'] == 'expected_otjh')]
        self.assertEqual(activities.schema_blockers('components', rows)[0]['code'], 'column_missing')
        rows[0]['data_type'] = 'unknown-type'
        self.assertTrue(any(b['code'] == 'column_type_mismatch' for b in activities.schema_blockers(rows[0]['table_name'], rows)))
        self.assertEqual(activities.schema_blockers('components', [])[0]['code'], 'table_unavailable')

    def test_missing_exact_module_never_reads_its_children(self):
        self.cursor.fetchall.side_effect = [self.rows, []]
        result = self.read()
        self.assertEqual(result['blockers'][0]['code'], 'module_placement_not_found')
        self.assertEqual(len(self.cursor.execute.call_args_list), 3)

    def test_driver_errors_are_sanitized_and_connections_always_closed(self):
        self.cursor.execute.side_effect = RuntimeError('secret connection string and learner data')
        result = self.read()
        self.assertEqual(result['blockers'][0]['code'], 'read_failed')
        self.assertNotIn('secret', str(result))
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()

    def test_overflow_is_reported_not_silently_truncated(self):
        self.cursor.fetchall.side_effect = [self.rows, [self.module], [{}] * 2001, [], []]
        result = self.read()
        self.assertIsNone(result['weeks'])
        self.assertTrue(any(b['code'] == 'result_incomplete' and b['table'] == 'weeks' for b in result['blockers']))

    def test_profile_definitions_are_scoped_to_covered_codes_and_one_version(self):
        self.cursor.fetchall.side_effect = [self.rows, [self.module], [], [],
            [{'ksb_code': 'K7'}], [{'id': 'profile', 'ksb_items': []}], [{'id': 123, 'source_profile_id': 'profile'}],
            [{'id': 44, 'code': 'K7', 'profile_version_id': 123}]]
        result = self.read()
        self.assertEqual(result['ksb_definitions'][0]['code'], 'K7')
        self.assertEqual(self.cursor.execute.call_args.args[1], (123, ['K7']))
        profile_call = self.cursor.execute.call_args_list[-3]
        self.assertEqual(profile_call.args[1], (['K7'], 'profile', 'profile'))

    def test_multiple_profile_versions_are_reported_not_guessed(self):
        self.cursor.fetchall.side_effect = [self.rows, [self.module], [], [], [{'ksb_code': 'K7'}], [], [{'id': 1}, {'id': 2}]]
        result = self.read()
        self.assertIsNone(result['ksb_definitions'])
        self.assertTrue(any(b['code'] == 'ksb_profile_version_unresolved' for b in result['blockers']))

    def test_identifiers_are_parameters_never_sql_fragments(self):
        malicious = "confirmed-id'; DROP TABLE curriculum.components; --"
        self.cursor.fetchall.side_effect = [self.rows, []]
        activities.get_module_activities(malicious, 'programme', 'home', 'g1')
        sql, parameters = self.cursor.execute.call_args.args
        self.assertNotIn(malicious, sql)
        self.assertEqual(parameters, (malicious, 'programme', 'home', 'g1'))

    def test_connection_cleanup_failure_remains_sanitized(self):
        self.cursor.fetchall.side_effect = [self.rows, []]
        self.connection.rollback.side_effect = RuntimeError('private connection detail')
        result = self.read()
        self.assertTrue(any(b['code'] == 'connection_cleanup_failed' for b in result['blockers']))
        self.assertNotIn('private connection', str(result))
        self.connection.close.assert_called_once()
