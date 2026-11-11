"""A status edit is inert until the official publication transaction applies it."""
from django.db import connections, transaction
from django.test import TestCase

from enrolment_api.wizard_layout import CONN
from .publication import PublicationBlocked, sync_published_cohort_statuses


class PublishedCohortStatusTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.old = {'programmeStructure': {'programmes': [{
            'id': 'ME_L4', 'curriculumProgrammeId': 'PROG-ME-L4',
            'cohorts': [{'id': 'REAL-FEB', 'name': 'Feb 2027', 'status': 'planned'}],
        }]}}
        self.proposed = {'programmes': [{
            'id': 'ME_L4', 'curriculumProgrammeId': 'PROG-ME-L4',
            'cohorts': [{'id': 'REAL-FEB', 'name': 'Feb 2027', 'status': 'completed'}],
        }]}
        with connections[CONN].cursor() as cursor:
            cursor.execute('INSERT INTO curriculum.cohorts '
                           '(cohort_id, cohort_name, programme_id, status) VALUES (%s, %s, %s, %s)',
                           ['REAL-FEB', 'Feb 2027', 'PROG-ME-L4', 'planned'])

    def status(self):
        with connections[CONN].cursor() as cursor:
            cursor.execute('SELECT status FROM curriculum.cohorts WHERE cohort_id = %s', ['REAL-FEB'])
            return cursor.fetchone()[0]

    def test_draft_change_is_inert_until_publication_sync(self):
        self.assertEqual(self.status(), 'planned')
        with transaction.atomic(using=CONN):
            with connections[CONN].cursor() as cursor:
                sync_published_cohort_statuses(cursor, self.old, self.proposed)
        self.assertEqual(self.status(), 'completed')

    def test_failed_publication_rolls_back_status(self):
        try:
            with transaction.atomic(using=CONN):
                with connections[CONN].cursor() as cursor:
                    sync_published_cohort_statuses(cursor, self.old, self.proposed)
                raise PublicationBlocked('Publication failed')
        except PublicationBlocked:
            pass
        self.assertEqual(self.status(), 'planned')

    def test_wrong_programme_identity_is_rejected(self):
        self.proposed['programmes'][0]['curriculumProgrammeId'] = 'OTHER'
        with connections[CONN].cursor() as cursor:
            with self.assertRaisesRegex(PublicationBlocked, 'matching active Curriculum'):
                sync_published_cohort_statuses(cursor, self.old, self.proposed)
        self.assertEqual(self.status(), 'planned')

    def test_name_change_is_inert_until_publication_and_retry_is_idempotent(self):
        self.proposed['programmes'][0]['cohorts'][0]['name'] = 'Renamed Feb'
        self.assertEqual(self.status(), 'planned')
        with connections[CONN].cursor() as cursor:
            with transaction.atomic(using=CONN):
                sync_published_cohort_statuses(cursor, self.old, self.proposed)
            cursor.execute('SELECT cohort_name FROM curriculum.cohorts WHERE cohort_id = %s', ['REAL-FEB'])
            self.assertEqual(cursor.fetchone()[0], 'Renamed Feb')
            with transaction.atomic(using=CONN):
                sync_published_cohort_statuses(cursor, self.old, self.proposed)
            cursor.execute('SELECT cohort_name FROM curriculum.cohorts WHERE cohort_id = %s', ['REAL-FEB'])
            self.assertEqual(cursor.fetchone()[0], 'Renamed Feb')
        self.assertEqual(self.status(), 'completed')

    def test_failed_rename_rolls_back_parent_and_status(self):
        self.proposed['programmes'][0]['cohorts'][0]['name'] = 'Renamed Feb'
        try:
            with transaction.atomic(using=CONN):
                with connections[CONN].cursor() as cursor:
                    sync_published_cohort_statuses(cursor, self.old, self.proposed)
                raise PublicationBlocked('Later publication step failed')
        except PublicationBlocked:
            pass
        with connections[CONN].cursor() as cursor:
            cursor.execute('SELECT cohort_name, status FROM curriculum.cohorts WHERE cohort_id = %s', ['REAL-FEB'])
            self.assertEqual(cursor.fetchone(), ('Feb 2027', 'planned'))

    def test_conflicting_rename_is_rejected_without_changes(self):
        self.proposed['programmes'][0]['cohorts'][0]['name'] = 'Already Used'
        with connections[CONN].cursor() as cursor:
            cursor.execute('INSERT INTO curriculum.cohorts (cohort_id, cohort_name, programme_id, programme_name, status) VALUES (%s, %s, %s, %s, %s)',
                           ['OTHER', 'Already Used', 'PROG-ME-L4', 'Marketing Executive', 'planned'])
            with transaction.atomic(using=CONN):
                with self.assertRaisesRegex(PublicationBlocked, 'Another cohort'):
                    sync_published_cohort_statuses(cursor, self.old, self.proposed)
            cursor.execute('SELECT cohort_name, status FROM curriculum.cohorts WHERE cohort_id = %s', ['REAL-FEB'])
            self.assertEqual(cursor.fetchone(), ('Feb 2027', 'planned'))

    def test_existing_curriculum_status_divergence_is_rejected(self):
        self.proposed['programmes'][0]['cohorts'][0]['status'] = 'planned'
        with connections[CONN].cursor() as cursor:
            cursor.execute('UPDATE curriculum.cohorts SET status = %s WHERE cohort_id = %s',
                           ['completed', 'REAL-FEB'])
            with self.assertRaisesRegex(PublicationBlocked, 'differs from Curriculum'):
                sync_published_cohort_statuses(cursor, self.old, self.proposed)
