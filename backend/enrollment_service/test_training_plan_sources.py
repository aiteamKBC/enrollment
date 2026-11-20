"""Document-only source enrichment cannot widen the LMS reader boundary."""
import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from . import lms_readonly as reader, lms_reviews_readonly as reviews, lms_activity_readonly as activities


def metadata(tables):
    return [dict(table_name=t, column_name=c, readable=True, schema_usage=True) for t, columns in tables.items() for c in columns]


class DocumentSourceTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = {'reader_role': reader.READER_ROLE, 'read_only': 'on'}
        patch.object(reader, '_connect_safely', return_value=(self.conn, None)).start()
        self.addCleanup(patch.stopall)

    def review_metadata(self):
        return metadata({'review_templates': ['id', 'programme_id', 'enabled', 'deleted_at', 'review_type_id',
            'signature_advisor', 'signature_employer', 'signature_participant', 'signature_referrer']})

    def test_verified_type_and_required_roles_are_read_for_exact_programme_and_ids(self):
        self.cursor.fetchall.side_effect = [self.review_metadata(), [dict(id='review-one', programme_id='pid',
            enabled=True, deleted_at=None, review_type_id='REVT-PROGRESS_REVIEW', signature_advisor=True,
            signature_employer=False, signature_participant=True, signature_referrer=False)]]
        result = reviews.read_document_review_definitions('pid', ['review-one'])
        self.assertFalse(result['blockers'])
        self.assertEqual(result['definitions']['review-one']['typeCode'], 'progress_review')
        self.assertEqual(result['definitions']['review-one']['signatures']['employer'], False)
        self.assertEqual(self.cursor.execute.call_args_list[-1].args[1], ('pid', ['review-one']))
        self.assertTrue(self.conn.read_only)
        self.assertTrue(all(c.args[0].strip().startswith('SELECT') for c in self.cursor.execute.call_args_list))
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called_once(); self.conn.close.assert_called_once()

    def test_signature_denial_does_not_hide_type_or_attempt_denied_columns(self):
        meta = self.review_metadata()
        for r in meta:
            if r['column_name'].startswith('signature_'):
                r['readable'] = False
        self.cursor.fetchall.side_effect = [meta, [dict(id='r', programme_id='pid', enabled=True,
            deleted_at=None, review_type_id='REVT-MCM')]]
        result = reviews.read_document_review_definitions('pid', ['r'])
        self.assertEqual(result['definitions']['r'], {'typeCode': 'mcm', 'signatures': None})
        self.assertEqual(result['blockers'][0]['code'], 'select_denied')
        self.assertNotIn('signature_', self.cursor.execute.call_args_list[-1].args[0])

    def test_custom_type_reads_stable_catalogue_code_without_name_matching(self):
        meta = self.review_metadata() + metadata({'review_types': ['id', 'code']})
        self.cursor.fetchall.side_effect = [meta, [dict(id='r', programme_id='pid', enabled=True, deleted_at=None,
            review_type_id='custom', **{'signature_' + k: False for k in ('advisor', 'employer', 'participant', 'referrer')})],
            [dict(id='custom', code='gateway_review')]]
        self.assertEqual(reviews.read_document_review_definitions('pid', ['r'])['definitions']['r']['typeCode'], 'gateway_review')

    def test_unsafe_role_and_missing_calendar_permissions_fail_closed(self):
        self.cursor.fetchone.return_value['reader_role'] = 'owner'
        result = reviews.read_document_review_definitions('pid', ['r'])
        self.assertEqual(result['blockers'][0]['code'], 'read_only_not_confirmed')
        self.cursor.fetchone.return_value['reader_role'] = reader.READER_ROLE
        self.cursor.fetchall.side_effect = None
        self.cursor.fetchall.return_value = []
        result = activities.get_document_week_dates('mid', 'pid', 'cid', 'gid', [{'sourceId': 'w'}])
        self.assertFalse(result['weeks'])
        self.assertEqual({r['table'] for r in result['blockers']}, {'modules', 'groups', 'cohorts', 'holidays', 'england_holidays'})

    def test_calendar_uses_existing_group_delivery_and_holiday_engine_without_queries_elsewhere(self):
        deleted = ['deleted_at', 'is_programme_deleted', 'deleted_by', 'deleted_via_parent']
        meta = metadata({
            'modules': ['module_catalogue_id', 'programme_id', 'cohort_id', 'group_id', 'start_date',
                'session_week_day', 'session_start_time', 'session_end_time', 'session_overrides', *deleted],
            'groups': ['group_id', 'cohort_id', 'programme_id', 'session_week_day', 'session_start_time', 'session_end_time', *deleted],
            'cohorts': ['cohort_id', 'programme_id', 'start_date', 'excluded_holiday_ids', *deleted],
            'holidays': ['id', 'label', 'start_date', 'end_date', 'notes', 'is_archived'],
            'england_holidays': ['id', 'title', 'holiday_date']})
        module = dict(module_catalogue_id='mid', programme_id='pid', cohort_id='cid', group_id='gid',
            start_date=date(2027, 3, 8), session_week_day='Monday', session_start_time='09:00',
            session_end_time='11:00', session_overrides={})
        group = dict(group_id='gid', programme_id='pid', cohort_id='cid', session_week_day='Tuesday',
            session_start_time='09:00', session_end_time='11:00')
        cohort = dict(cohort_id='cid', programme_id='pid', start_date=date(2027, 3, 8), excluded_holiday_ids=[])
        self.cursor.fetchall.side_effect = [meta, [module], [group], [cohort], [], []]
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            result = activities.get_document_week_dates('mid', 'pid', 'cid', 'gid', [{'sourceId': 'w'}])
        self.assertFalse(result['blockers'], result['blockers'])
        self.assertEqual(result['weeks']['w']['end'], '2027-03-14')
        self.assertEqual(len(queries), 0)
        self.conn.commit.assert_not_called()
