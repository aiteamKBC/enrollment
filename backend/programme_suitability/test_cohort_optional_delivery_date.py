import json
from unittest.mock import patch
from django.test import SimpleTestCase
from curriculum_api import views


class OptionalCohortDeliveryDateTests(SimpleTestCase):
    def test_date_free_retry_keeps_existing_delivery_dates_and_does_not_create_duplicate(self):
        existing={'cohortId':'REAL-JUNE','cohortName':'June 2027','programmeId':'PROG-ME-L4',
            'programmeName':'Marketing Executive','startDate':'2027-06-01','endDate':'2028-12-01',
            'durationMonths':18,'epaMonths':3,'apprenticeshipEndDate':'2029-03-01','status':'planned'}
        with patch.object(views,'canonical_programme_id',return_value='PROG-ME-L4'), \
             patch.object(views,'unique_cohort_id',return_value='UNUSED-NEW-ID'), \
             patch.object(views,'archived_authoring_ids',return_value=set()), \
             patch.object(views,'cohort_authoring_detail_rows',return_value=[existing]), \
             patch.object(views,'update_cohort_fields') as update, \
             patch.object(views,'persist_cohort_authoring_detail') as create, \
             patch.object(views,'invalidate_curriculum_cache'):
            response=views.create_curriculum_cohort({'name':'June 2027','programme':'Marketing Executive','programmeId':'PROG-ME-L4'})
        self.assertEqual(response.status_code,200,response.content)
        payload=json.loads(response.content)
        self.assertFalse(payload['created'])
        self.assertEqual(payload['cohort']['id'],'REAL-JUNE')
        values=update.call_args.args[1]
        self.assertEqual(values['start_date'],'2027-06-01')
        self.assertEqual(values['end_date'],'2028-12-01')
        self.assertEqual(values['duration_months'],18)
        self.assertEqual(values['apprenticeship_end_date'],'2029-03-01')
        self.assertEqual(values['status'],'planned')
        create.assert_not_called()
