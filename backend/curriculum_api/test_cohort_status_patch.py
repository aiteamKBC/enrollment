import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from .views import curriculum_cohort_detail


class CohortStatusPatchTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.cohort = {
            'cohort_id': 'COHORT-ME-FEB-2027',
            'cohort_name': 'Feb 2027',
            'programme_id': 'PROG-ME-L4',
            'status': 'planned',
            'start_date': '2027-02-01',
            'end_date': None,
        }

    @patch('curriculum_api.views.invalidate_curriculum_cache')
    @patch('curriculum_api.views.cohort_record_revision', return_value='current-revision')
    @patch('curriculum_api.views.update_cohort_fields')
    @patch('curriculum_api.views.resolve_cohort_row')
    def test_explicit_status_is_persisted_without_changing_identity(self, resolve, update, _revision, _invalidate):
        resolve.return_value = self.cohort
        request = self.factory.patch('/curriculum_api/curriculum/cohorts/COHORT-ME-FEB-2027/',
                                     data=json.dumps({'status': 'active'}), content_type='application/json')
        response = curriculum_cohort_detail(request, self.cohort['cohort_id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(update.call_args.args[0], self.cohort['cohort_id'])
        self.assertEqual(update.call_args.args[1]['status'], 'active')
        self.assertNotIn('programme_id', update.call_args.args[1])

    @patch('curriculum_api.views.update_cohort_fields')
    @patch('curriculum_api.views.resolve_cohort_row')
    def test_invalid_explicit_status_is_rejected_without_a_write(self, resolve, update):
        resolve.return_value = self.cohort
        request = self.factory.patch('/curriculum_api/curriculum/cohorts/COHORT-ME-FEB-2027/',
                                     data=json.dumps({'status': 'archived'}), content_type='application/json')
        response = curriculum_cohort_detail(request, self.cohort['cohort_id'])
        self.assertEqual(response.status_code, 400)
        update.assert_not_called()

    @patch('curriculum_api.views.update_cohort_fields')
    @patch('curriculum_api.views.resolve_cohort_row')
    def test_archived_cohort_cannot_be_restored_by_status_patch(self, resolve, update):
        resolve.return_value = {**self.cohort, 'status': 'archived'}
        request = self.factory.patch('/curriculum_api/curriculum/cohorts/COHORT-ME-FEB-2027/',
                                     data=json.dumps({'status': 'active'}), content_type='application/json')
        response = curriculum_cohort_detail(request, self.cohort['cohort_id'])
        self.assertEqual(response.status_code, 409)
        update.assert_not_called()
