"""Hermetic tests: mocked Psycopg and disposable in-memory query fixtures only."""
import importlib
import os
import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch

from . import lms_readonly as reader


# Deliberately synthetic: no environment files or real connection are used.
TEST_URL = 'postgresql://enrolment_modules_reader:synthetic-only@fixture.neon.tech/fixture?sslmode=require'


def schema_rows():
    return [{'table_name': table, 'column_name': column, 'data_type': sorted(types)[0]}
            for table, columns in reader._EXPECTED.items() for column, types in columns.items()]


class ReaderTests(unittest.TestCase):
    def test_cohort_apprenticeship_end_is_an_exact_read_only_lookup(self):
        from datetime import date
        self.cursor.fetchall.side_effect = [schema_rows() + [
            {'table_name': 'cohorts', 'column_name': 'apprenticeship_end_date', 'data_type': 'date'}],
            [{'programme_id': 'pid', 'cohort_id': 'cid', 'apprenticeship_end_date': date(2028, 6, 30)}]]
        self.assertEqual(reader.get_cohort_apprenticeship_end('pid', 'cid'), date(2028, 6, 30))
        self.assertTrue(self.connection.read_only)
        self.assertEqual(self.cursor.execute.call_args.args, (reader._QUERIES['cohort_apprenticeship_end'], ('pid', 'cid')))
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once()
        self.assertIn('deleted_at IS NULL', reader._QUERIES['cohort_apprenticeship_end'])

    def test_cohort_end_missing_permission_or_mismatched_identity_is_not_substituted(self):
        self.cursor.fetchall.side_effect = [schema_rows()]
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.get_cohort_apprenticeship_end('pid', 'cid')
        self.assertEqual(caught.exception.code, 'cohort_apprenticeship_end_access_unavailable')
        with patch.object(reader, '_read', return_value=[{'programme_id': 'other', 'cohort_id': 'cid'}]):
            with self.assertRaises(reader.LmsReadUnavailable):
                reader.get_cohort_apprenticeship_end('pid', 'cid')

    def test_replacement_queries_bind_exact_saved_placement_and_retain_deletion_evidence(self):
        with patch.object(reader, '_read', return_value=[]) as read:
            reader.get_saved_module_references(['old', 'old'])
            read.assert_called_once_with('saved_module_references', (['old'],))
            read.reset_mock()
            reader.get_replacement_offerings('programme', 'cohort', 'group')
            read.assert_called_once_with('replacement_offerings', ('programme', 'cohort', 'group', 200, 0))
        self.assertIn('m.cohort_id = %s AND m.group_id = %s', reader._QUERIES['replacement_offerings'])
        self.assertNotIn('deleted_at IS NULL', reader._QUERIES['saved_module_references'])
        self.assertIn('deleted_at IS NULL', reader._QUERIES['replacement_offerings'])

    def test_replacement_queries_reject_unbounded_or_missing_identifiers(self):
        with patch.object(reader, '_read') as read:
            for ids in ([], [''] , ['x'] * 201):
                with self.assertRaises(ValueError):
                    reader.get_saved_module_references(ids)
            for values in (('p', 'c', ''), ('p', None, 'g')):
                with self.assertRaises(ValueError):
                    reader.get_replacement_offerings(*values)
            read.assert_not_called()

    def setUp(self):
        self.environment = patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': TEST_URL, 'PGSERVICE': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.connect = patch.object(reader.psycopg, 'connect').start()
        self.addCleanup(patch.stopall)
        self.connection = self.connect.return_value
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = {'reader_role': reader.READER_ROLE, 'read_only': 'on'}
        self.cursor.fetchall.side_effect = [schema_rows(), [{'modules_sample_count': 3, 'cohorts_sample_count': 2}]]

    def test_import_does_not_connect(self):
        importlib.reload(reader)
        self.connect.assert_not_called()

    def test_missing_variable_does_not_connect_or_break_import(self):
        with patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': ''}):
            with self.assertRaises(reader.LmsReadUnavailable) as caught:
                reader.verify_connection()
        self.assertEqual(caught.exception.code, 'not_configured')
        self.connect.assert_not_called()

    def test_read_only_tls_bounded_smoke_and_cleanup(self):
        self.cursor.execute.side_effect = lambda *args: self.assertIs(self.connection.read_only, True)
        self.assertEqual(reader.verify_connection(), {
            'read_only': True, 'modules_sample_count': 3, 'cohorts_sample_count': 2,
        })
        options = self.connect.call_args.kwargs
        self.assertFalse(options['autocommit'])
        self.assertEqual(options['sslmode'], 'verify-full')
        self.assertEqual(options['sslrootcert'], reader.certifi.where())
        self.assertEqual(options['channel_binding'], 'require')
        self.assertEqual(options['options'], '')
        self.assertEqual(options['connect_timeout'], 5)
        self.assertIsNone(options['prepare_threshold'])
        self.assertTrue(self.connection.read_only)
        self.assertEqual([call.args[0] for call in self.cursor.execute.call_args_list],
                         [reader._GUARD, reader._SCHEMA, reader._QUERIES['smoke']])
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.connection.commit.assert_not_called()

    def test_offer_parameters_are_bound_and_never_interpolated(self):
        identifier = "verified-id'; DROP TABLE curriculum.modules; --"
        reader.get_module_offerings(identifier, cohort_id='cohort-id', limit=5, offset=10)
        sql, params = self.cursor.execute.call_args.args
        self.assertNotIn(identifier, sql)
        self.assertEqual(params, (identifier, 'cohort-id', 'cohort-id', 5, 10))

    def test_offerings_include_existing_readable_numeric_module_hours(self):
        hours_column = {'table_name': 'modules', 'column_name': 'total_otjh', 'data_type': 'numeric'}
        self.cursor.fetchall.side_effect = [schema_rows() + [hours_column], [{'module_catalogue_id': 'exact-offer', 'total_otjh': Decimal('105.75')}]]
        rows = reader.get_module_offerings('verified', cohort_id='home')
        self.assertEqual(rows[0]['total_otjh'], Decimal('105.75'))
        self.assertIn('m.total_otjh AS total_otjh', self.cursor.execute.call_args.args[0])
        self.assertTrue(self.connection.read_only)
        self.connection.commit.assert_not_called()

    def test_missing_or_unsupported_hour_column_keeps_schedule_reads_available(self):
        for optional in ([], [{'table_name': 'modules', 'column_name': 'total_otjh', 'data_type': 'text'}]):
            with self.subTest(optional=optional):
                self.cursor.fetchall.side_effect = [schema_rows() + optional, [{'module_catalogue_id': 'exact-offer', 'total_otjh': None}]]
                rows = reader.get_module_offerings('verified', cohort_id='home')
                self.assertIsNone(rows[0]['total_otjh'])
                self.assertIn('NULL AS total_otjh', self.cursor.execute.call_args.args[0])
                self.assertNotIn('m.total_otjh', self.cursor.execute.call_args.args[0])
                self.connection.commit.assert_not_called()

    def test_refuses_unknown_sql_without_opening_a_connection(self):
        with self.assertRaises(reader.LmsReadUnavailable):
            reader._read('DELETE FROM curriculum.modules')
        self.connect.assert_not_called()

    def test_assignment_read_is_parameterized_and_bounded(self):
        reader.get_assigned_cohort('programme', 'cohort', 'group')
        sql, params = self.cursor.execute.call_args.args
        self.assertEqual(params, ('group', 'group', 'programme', 'cohort'))
        self.assertIn('LIMIT 2001', sql)
        self.connection.commit.assert_not_called()

    def test_assignment_requires_stable_programme_and_cohort_ids(self):
        for args in [(None, 'c'), ('p', None), ('', 'c'), ('p', 'c', ' ')]:
            with self.assertRaises(ValueError):
                reader.get_assigned_cohort(*args)
        self.connect.assert_not_called()

    def test_programme_catalogue_query_is_bound_and_read_only(self):
        reader.get_programme_cohorts('verified-programme')
        sql, parameters = self.cursor.execute.call_args.args
        self.assertEqual(parameters, ('verified-programme',))
        self.assertIn('LIMIT 2001', sql)
        self.connection.commit.assert_not_called()

    def test_catalogue_rejects_invalid_ids_without_connecting(self):
        for value in [None, '', ' ', 'x' * 256]:
            with self.assertRaises(ValueError):
                reader.get_programme_cohorts(value)
        self.connect.assert_not_called()

    def test_group_read_requires_its_own_schema_and_never_commits(self):
        columns = schema_rows() + [{'table_name': 'groups', 'column_name': name, 'data_type': sorted(types)[0]}
                                  for name, types in reader._GROUP_EXPECTED.items()]
        self.cursor.fetchall.side_effect = [columns, [{'group_id': 'g1'}]]
        self.assertEqual(reader.get_cohort_groups('programme', 'cohort'), [{'group_id': 'g1'}])
        self.assertEqual(self.cursor.execute.call_args.args[1], ('programme', 'cohort'))
        self.connection.commit.assert_not_called()

    def test_missing_group_access_does_not_execute_the_group_query(self):
        self.cursor.fetchall.side_effect = [schema_rows()]
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.get_cohort_groups('programme', 'cohort')
        self.assertEqual(caught.exception.code, 'groups_access_unavailable')
        self.assertNotIn(reader._QUERIES['cohort_groups'], [call.args[0] for call in self.cursor.execute.call_args_list])

    def test_refuses_wrong_role_unsafe_options_and_non_neon_hosts(self):
        for value in [TEST_URL.replace('enrolment_modules_reader', 'owner'),
                      TEST_URL + '&options=unsafe', TEST_URL.replace('fixture.neon.tech', 'localhost'),
                      'not-a-url', TEST_URL + '&sslrootcert=untrusted-file']:
            with self.subTest(value='<synthetic invalid configuration>'):
                with patch.dict(os.environ, {'LMS_READONLY_DATABASE_URL': value}):
                    with self.assertRaises(reader.LmsReadUnavailable) as caught:
                        reader.verify_connection()
                self.assertEqual(caught.exception.code, 'invalid_configuration')
        self.connect.assert_not_called()

    def test_ambient_libpq_service_cannot_override_reader_configuration(self):
        with patch.dict(os.environ, {'PGSERVICE': 'synthetic-service'}):
            with self.assertRaises(reader.LmsReadUnavailable) as caught:
                reader.verify_connection()
        self.assertEqual(caught.exception.code, 'invalid_configuration')
        self.connect.assert_not_called()

    def test_tls_failures_are_classified_without_leaking_driver_details(self):
        self.connect.side_effect = RuntimeError('certificate verify failed: ' + TEST_URL)
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.verify_connection()
        self.assertEqual(caught.exception.code, 'tls_verification_failed')
        self.assertNotIn(TEST_URL, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)

    def test_cleanup_failure_also_has_a_sanitised_unavailable_result(self):
        self.connection.rollback.side_effect = RuntimeError(TEST_URL)
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.verify_connection()
        self.assertEqual(caught.exception.code, 'read_failed')
        self.connection.close.assert_called_once()
        self.assertIsNone(caught.exception.__context__)

    def test_rejects_missing_programme_and_unbounded_pages(self):
        for kwargs in [dict(programme_id=None), dict(programme_id=''),
                       dict(programme_id=123), dict(programme_id='id', limit=201),
                       dict(programme_id='id', limit=True), dict(programme_id='id', offset=-1)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                reader.get_module_offerings(**kwargs)
        self.connect.assert_not_called()

    def test_guard_stops_reads_when_role_or_read_only_state_is_wrong(self):
        for guard in [{'reader_role': 'owner', 'read_only': 'on'},
                      {'reader_role': reader.READER_ROLE, 'read_only': 'off'}]:
            self.cursor.execute.reset_mock()
            self.cursor.fetchone.return_value = guard
            with self.assertRaises(reader.LmsReadUnavailable) as caught:
                reader.verify_connection()
            self.assertEqual(caught.exception.code, 'read_only_not_confirmed')
            self.assertEqual(self.cursor.execute.call_count, 1)

    def test_schema_is_validated_before_schedule_columns_are_read(self):
        for rows in [[], schema_rows()[1:],
                     [{**row, 'data_type': 'unknown'} for row in schema_rows()]]:
            self.cursor.execute.reset_mock()
            self.cursor.fetchall.side_effect = [rows]
            with self.assertRaises(reader.LmsReadUnavailable) as caught:
                reader.verify_connection()
            self.assertEqual(caught.exception.code, 'schema_mismatch')
            self.assertEqual(self.cursor.execute.call_count, 2)

    def test_driver_exceptions_are_not_exposed_or_chained(self):
        self.connect.side_effect = RuntimeError(TEST_URL)
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.verify_connection()
        self.assertNotIn(TEST_URL, str(caught.exception))
        self.assertIsNone(caught.exception.__context__)
        self.connect.side_effect = None
        self.cursor.execute.side_effect = RuntimeError(TEST_URL)
        with self.assertRaises(reader.LmsReadUnavailable) as caught:
            reader.verify_connection()
        self.assertEqual(caught.exception.code, 'read_failed')
        self.assertIsNone(caught.exception.__context__)
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()


class OfferingFilterTests(unittest.TestCase):
    """Execute the actual allowlisted SELECT against synthetic SQLite tables.

    Only placeholder/cast syntax and two builtins are adapted. No Django setup,
    .env loading, PostgreSQL server, or remote data is involved.
    """
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("ATTACH DATABASE ':memory:' AS curriculum")
        self.db.create_function('btrim', 1, lambda value: value.strip() if value is not None else None)
        self.db.create_function('isfinite', 1, lambda value: bool(value) and value not in ('infinity', '-infinity'))
        for table, columns in reader._EXPECTED.items():
            fields = ', '.join(f'"{name}"' for name in columns)
            self.db.execute(f'CREATE TABLE curriculum.{table} ({fields})')
        group_fields = ', '.join(f'"{name}"' for name in reader._GROUP_EXPECTED)
        self.db.execute(f'CREATE TABLE curriculum.groups ({group_fields})')
        self.cohort = dict(cohort_id='cohort', cohort_name='Same name', programme_id='verified',
                           programme_name='Verified programme', status='active', source_type='fixture',
                           start_date='2026-01-01', end_date='2026-12-31')
        self.module = dict(module_catalogue_id='module-g1', programme_id='verified',
                           programme_name='Verified programme', title='Module', cohort_id='cohort',
                           cohort_name='Same name', group_id='g1', group_name='G1',
                           start_date='2026-02-01', end_date='2026-03-01', sessions_number=4)

    def insert(self, table, values):
        names = ', '.join(values)
        placeholders = ', '.join('?' for _ in values)
        self.db.execute(f'INSERT INTO curriculum.{table} ({names}) VALUES ({placeholders})', tuple(values.values()))

    def offerings(self, cohort_id=None):
        sql = reader._QUERIES['offerings'].replace('%s::text', '?').replace('%s', '?')
        return [dict(row) for row in self.db.execute(sql, ('verified', cohort_id, cohort_id, 100, 0))]

    def test_individual_g1_g2_identities_and_dates_are_retained(self):
        self.insert('cohorts', self.cohort)
        self.insert('modules', self.module)
        self.insert('modules', {**self.module, 'module_catalogue_id': 'module-g2', 'group_id': 'g2', 'group_name': 'G2'})
        records = self.offerings()
        self.assertEqual([r['module_catalogue_id'] for r in records], ['module-g1', 'module-g2'])
        self.assertEqual([r['group_id'] for r in records], ['g1', 'g2'])
        self.assertEqual(records[0]['start_date'], self.module['start_date'])
        self.assertEqual(self.offerings('different-cohort'), [])

    def test_module_deletion_invalid_identity_and_dates_are_excluded(self):
        self.insert('cohorts', self.cohort)
        for changes in [dict(deleted_by='module-delete'), dict(deleted_at='2026-01-01'),
                        dict(is_programme_deleted=True), dict(deleted_via_parent='programme'),
                        dict(programme_id=''), dict(programme_name='  '), dict(start_date=None),
                        dict(end_date=None), dict(start_date='infinity'),
                        dict(end_date='2025-01-01')]:
            with self.subTest(changes=changes):
                self.db.execute('DELETE FROM curriculum.modules')
                self.insert('modules', {**self.module, **changes})
                self.assertEqual(self.offerings(), [])

    def test_cohort_status_deletion_and_composite_join(self):
        self.insert('modules', self.module)
        for changes, included in [(dict(status='active'), True), (dict(status='planned'), True),
                                  (dict(status='archived'), False), (dict(status='other'), False),
                                  (dict(programme_id='unrelated'), False),
                                  (dict(cohort_id='different-id'), False),
                                  (dict(deleted_via_parent='programme'), False),
                                  (dict(end_date='2025-01-01'), False)]:
            with self.subTest(changes=changes):
                self.db.execute('DELETE FROM curriculum.cohorts')
                self.insert('cohorts', {**self.cohort, **changes})
                self.assertEqual(bool(self.offerings()), included)

    def test_planned_cohort_without_an_offering_returns_nothing(self):
        self.insert('cohorts', {**self.cohort, 'status': 'planned'})
        self.assertEqual(self.offerings(), [])

    def assignment(self, group_id='g1'):
        sql = reader._QUERIES['assigned_cohort'].replace('%s::text', '?').replace('%s', '?')
        return [dict(row) for row in self.db.execute(sql, (group_id, group_id, 'verified', 'cohort'))]

    def test_assignment_includes_undated_modules_but_only_saved_group(self):
        self.insert('cohorts', self.cohort)
        self.insert('modules', {**self.module, 'start_date': None, 'end_date': None})
        self.insert('modules', {**self.module, 'module_catalogue_id': 'other', 'group_id': 'g2'})
        self.insert('modules', {**self.module, 'module_catalogue_id': 'deleted', 'deleted_by': 'module-delete'})
        self.assertEqual([r['module_catalogue_id'] for r in self.assignment()], ['module-g1'])
        self.assertIsNone(self.assignment()[0]['start_date'])

    def test_assignment_keeps_empty_cohort_and_never_guesses_a_group(self):
        self.insert('cohorts', self.cohort)
        self.insert('modules', self.module)
        # No saved group means cohort offerings are visible with explicit group
        # IDs; the projection must not claim the first group is assigned.
        self.assertEqual(self.assignment(None)[0]['group_id'], 'g1')
        self.assertIsNone(self.assignment('unknown')[0]['module_catalogue_id'])
        self.db.execute('DELETE FROM curriculum.modules')
        self.assertEqual(self.assignment()[0]['cohort_name'], 'Same name')

    def test_assignment_does_not_join_other_programmes_or_deleted_cohorts(self):
        self.insert('cohorts', self.cohort)
        self.insert('modules', {**self.module, 'programme_id': 'other-programme'})
        self.assertIsNone(self.assignment()[0]['module_catalogue_id'])
        self.db.execute('UPDATE curriculum.cohorts SET deleted_at = ?', ('2026-01-01',))
        self.assertEqual(self.assignment(), [])

    def test_group_query_reads_empty_groups_and_excludes_wrong_parent_or_deleted(self):
        self.insert('cohorts', self.cohort)
        group = dict(group_id='g1', group_name='Group 1', programme_id='verified', cohort_id='cohort')
        self.insert('groups', group)
        self.insert('groups', {**group, 'group_id': 'wrong', 'cohort_id': 'other-cohort'})
        self.insert('groups', {**group, 'group_id': 'deleted', 'deleted_at': '2026-01-01'})
        sql = reader._QUERIES['cohort_groups'].replace('%s', '?')
        rows = [dict(row) for row in self.db.execute(sql, ('verified', 'cohort'))]
        self.assertEqual([r['group_id'] for r in rows], ['g1'])


if __name__ == '__main__':
    unittest.main()
