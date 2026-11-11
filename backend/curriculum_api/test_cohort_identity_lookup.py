import json
import os
from unittest.mock import patch

from django.db import connection
from django.test import Client, RequestFactory, SimpleTestCase, TestCase

from learner_api.models import StaffUser
from login.models import LoginAccount
from login.sessions import COOKIE_NAME, issue_session

from .views import curriculum_cohort_collection


class CohortIdentityLookupTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    @patch('curriculum_api.views.get_cached_payload')
    @patch('curriculum_api.views.authoring_fetch_all')
    def test_managed_target_uses_stable_id_without_building_curriculum_tree(self, read_rows, full_payload):
        read_rows.return_value = [{
            'cohort_id': 'COHORT-20261110195722888955DB66B4D8CC1C',
            'cohort_name': 'June 2027', 'programme_id': 'PROG-ME-L4',
            'programme_name': 'Marketing Executive Level 4',
            'status': 'planned', 'start_date': '2027-06-01',
        }]
        request = self.request_factory.get('/curriculum_api/curriculum/cohorts/', {
            'view': 'identity', 'programme_id': 'PROG-ME-L4',
            'cohort_id': 'COHORT-20261110195722888955DB66B4D8CC1C',
            'name': 'June 2027',
        })
        response = curriculum_cohort_collection(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['results'], [{
            'id': 'COHORT-20261110195722888955DB66B4D8CC1C',
            'name': 'June 2027', 'programmeId': 'PROG-ME-L4',
            'programme': 'Marketing Executive Level 4',
            'startDate': '2027-06-01', 'status': 'planned',
        }])
        args, kwargs = read_rows.call_args
        self.assertIn('programme_id = %s and cohort_id = %s', args[1])
        self.assertEqual(args[2], ['PROG-ME-L4', 'COHORT-20261110195722888955DB66B4D8CC1C'])
        self.assertFalse(kwargs['ensure_tables'])
        full_payload.assert_not_called()

    @patch('curriculum_api.views.get_cached_payload')
    @patch('curriculum_api.views.authoring_fetch_all')
    def test_new_target_uses_programme_and_name_and_excludes_archived_records(self, read_rows, full_payload):
        read_rows.return_value = [
            {'cohort_id': 'LIVE', 'cohort_name': 'June 2027', 'programme_id': 'PROG-ME-L4', 'status': 'planned'},
            {'cohort_id': 'OLD', 'cohort_name': 'June 2027', 'programme_id': 'PROG-ME-L4', 'status': 'archived'},
        ]
        response = curriculum_cohort_collection(self.request_factory.get('/curriculum_api/curriculum/cohorts/', {
            'view': 'identity', 'programme_id': 'PROG-ME-L4', 'name': 'June 2027',
        }))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in json.loads(response.content)['results']], ['LIVE'])
        self.assertIn('lower(trim(cohort_name))', read_rows.call_args.args[1])
        full_payload.assert_not_called()

    def test_identity_lookup_requires_programme_and_target(self):
        response = curriculum_cohort_collection(self.request_factory.get('/curriculum_api/curriculum/cohorts/', {'view': 'identity'}))
        self.assertEqual(response.status_code, 400)


class AuthenticatedCohortIdentityLookupTests(TestCase):
    @patch.dict(os.environ, {'API_REQUIRE_AUTH': '1'})
    def test_real_admin_session_reads_only_the_existing_target(self):
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO curriculum.cohorts (cohort_id, cohort_name, programme_id, programme_name, status) VALUES (%s,%s,%s,%s,%s)',
                           ['COHORT-20261110195722888955DB66B4D8CC1C', 'June 2027', 'PROG-ME-L4', 'Marketing Executive Level 4', 'planned'])
        StaffUser.objects.create(id=114, username='Synthetic admin 114', access='enrolment', position='Admin')
        account = LoginAccount.objects.create(id=114, subject_type='staff', subject_id=114,
                                              role='admin', email='admin-114@example.test',
                                              display_name='Synthetic Admin', is_active=True)
        token, _, _ = issue_session(account)
        url = '/curriculum_api/curriculum/cohorts/?view=identity&programme_id=PROG-ME-L4&cohort_id=COHORT-20261110195722888955DB66B4D8CC1C&name=June%202027'
        self.assertEqual(Client().get(url).status_code, 401)
        client = Client()
        client.cookies[COOKIE_NAME] = token
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row['id'] for row in response.json()['results']], ['COHORT-20261110195722888955DB66B4D8CC1C'])
