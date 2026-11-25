"""Legacy-schema PLR persistence, using synthetic data and SQLite only.

Run with --settings=enrolment_api.plr_test_settings. No hosted DB is loaded.
"""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connections, DatabaseError
from django.test import RequestFactory, TestCase
from learner_api.models import EnrolmentUser
from .extended_ilr import extended_ilr
from .models import ExtendedIlr, WizardPlr, WizardPlrRecord, WizardPolicyAck
from .wizard_steps import project_draft, read_projection, read_plr, PLR_LOAD_ERROR
from .wizard_bootstrap import wizard_bootstrap


class LegacyPlrRegressionTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.learner = EnrolmentUser.objects.create(
            username='Synthetic Regression Learner', learner_type='apprenticeship',
            programme_status='Onboarding', onboarding_status='Not started',
        )
        self.scope = {'learner_kind': 'apprenticeship', 'learner_id': self.learner.pk}
        self.draft = {
            'personalDetails': {'firstName': 'Synthetic', 'lastName': 'Regression', 'signature': 'existing-mark'},
            'plr': {'uln': '0123456789', 'records': [{
                'id': 'existing-qualification', 'subject': 'Fictional qualification',
                'qualificationType': 'GCSE', 'placeOfStudy': 'Synthetic College',
                'grade': '5', 'credits': 0, 'level': '2', 'recordType': 'Manual',
                'startDate': '2020-09-01', 'endDate': '2022-06-30', 'awardDate': '2022-08-20',
            }]},
        }

    def test_models_and_test_schema_have_no_import_only_columns(self):
        for model, absent in (
            (WizardPlr, {'Revision'}),
            (WizardPlrRecord, {'Source_fingerprint', 'Source_metadata', 'Deleted'}),
        ):
            self.assertTrue(absent.isdisjoint({f.column for f in model._meta.fields}))
        with connections['enrolment'].cursor() as cursor:
            cursor.execute("PRAGMA enrolment.table_info('Wizard_Plr_Records')")
            columns = {row[1] for row in cursor.fetchall()}
        self.assertTrue({'Source_fingerprint', 'Source_metadata', 'Deleted'}.isdisjoint(columns))

    def test_existing_plr_round_trip_preserves_qualifications_progress_and_evidence(self):
        project_draft('apprenticeship', self.learner.pk, self.draft)
        record = WizardPlrRecord.objects.get(**self.scope)
        record.evidence = [{'id': 'existing-certificate'}]
        record.save(update_fields=['evidence'])
        self.draft['plr']['records'][0]['grade'] = '6'
        project_draft('apprenticeship', self.learner.pk, self.draft)
        saved = read_projection('apprenticeship', self.learner.pk)
        self.assertEqual(saved['plr'], {**self.draft['plr'], 'records': [{**self.draft['plr']['records'][0], 'grade': '5'}]})
        self.assertEqual(saved['personalDetails']['signature'], 'existing-mark')
        record.refresh_from_db()
        self.assertEqual(record.evidence, [{'id': 'existing-certificate'}])
        self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), 1)

    def test_student_and_admin_api_save_and_reload_existing_data(self):
        WizardPlr.objects.create(**self.scope, uln=self.draft['plr']['uln'])
        for role in ('learner', 'admin'):
            request = RequestFactory().put('/enrolment_api/extended-ilr/apprenticeship/1/',
                json.dumps({'draft': self.draft}), content_type='application/json')
            request.user = SimpleNamespace(is_authenticated=role == 'admin')
            if role == 'learner':
                request.login_account = SimpleNamespace(
                    is_active=True, role='learner', subject_id=self.learner.pk, id=1,
                )
            with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}), \
                 patch('enrolment_api.extended_ilr.signed_snapshot', return_value=None), \
                 patch('enrolment_api.extended_ilr.legacy_review_required', return_value=False), \
                 patch('enrolment_api.extended_ilr.project_custom_fields'), \
                 patch('enrolment_api.eligibility_prefill.add_defaults'):
                response = extended_ilr(request, kind='apprenticeship', learner_id=self.learner.pk)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(json.loads(response.content)['draft']['plr'], self.draft['plr'])
                request.method = 'GET'
                response = extended_ilr(request, kind='apprenticeship', learner_id=self.learner.pk)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(json.loads(response.content)['draft']['plr'], self.draft['plr'])

    def test_failed_plr_read_is_explicit_and_other_projection_sections_survive(self):
        project_draft('apprenticeship', self.learner.pk, self.draft)
        with patch.object(WizardPlrRecord.objects, 'filter', side_effect=DatabaseError('private learner SQL value')), \
             self.assertLogs('enrolment_api.wizard_steps', level='ERROR') as logs:
            saved = read_projection('apprenticeship', self.learner.pk)
        self.assertEqual(saved['plr'], {'loadError': PLR_LOAD_ERROR})
        self.assertNotIn('private learner SQL value', ' '.join(logs.output))
        self.assertEqual(saved['personalDetails']['signature'], 'existing-mark')
        self.assertEqual(read_plr('apprenticeship', self.learner.pk), self.draft['plr'])

    def test_no_records_is_a_successful_empty_collection(self):
        self.assertEqual(read_plr('apprenticeship', self.learner.pk), {'uln': '', 'records': []})

    def test_real_database_statement_error_does_not_break_later_section_queries(self):
        project_draft('apprenticeship', self.learner.pk, self.draft)
        def fail_query(**kwargs):
            with connections['enrolment'].cursor() as cursor:
                cursor.execute('SELECT missing_synthetic_column FROM enrolment."Wizard_Plr_Records"')
        with patch.object(WizardPlrRecord.objects, 'filter', side_effect=fail_query):
            saved = read_projection('apprenticeship', self.learner.pk)
        self.assertEqual(saved['plr'], {'loadError': PLR_LOAD_ERROR})
        self.assertEqual(saved['personalDetails']['firstName'], 'Synthetic')
        self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), 1)

    def test_student_and_admin_bootstrap_survive_plr_failure(self):
        project_draft('apprenticeship', self.learner.pk, self.draft)
        for role in ('learner', 'admin'):
            request = RequestFactory().get('/bootstrap/')
            request.user = SimpleNamespace(is_authenticated=role == 'admin')
            if role == 'learner':
                request.login_account = SimpleNamespace(is_active=True, role=role, subject_id=self.learner.pk)
            with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}), \
                 patch.object(WizardPlrRecord.objects, 'filter', side_effect=DatabaseError('PLR unavailable')), \
                 patch('learner_api.learning_plan.promote_learner_if_ready', return_value=False), \
                 patch('enrolment_api.wizard_bootstrap.advance_learner'), \
                 patch('enrolment_api.wizard_bootstrap.to_board', return_value={'contact': {}, 'programme': {}}), \
                 patch('enrolment_api.extended_ilr.signed_snapshot', return_value=None), \
                 patch('enrolment_api.extended_ilr.legacy_review_required', return_value=False), \
                 patch('enrolment_api.eligibility_prefill.add_defaults'):
                response = wizard_bootstrap(request, kind='apprenticeship', learner_id=self.learner.pk)
            self.assertEqual(response.status_code, 200, response.content)
            draft = json.loads(response.content)['ilr']['draft']
            self.assertEqual(draft['plr'], {'loadError': PLR_LOAD_ERROR})
            self.assertEqual(draft['personalDetails']['signature'], 'existing-mark')

    def test_failed_plr_writes_preserve_collection_and_allow_other_progress_to_save(self):
        project_draft('apprenticeship', self.learner.pk, self.draft)
        row = ExtendedIlr.objects.create(**self.scope, wizard_draft=self.draft)
        changed = {**self.draft, 'plr': {'uln': '', 'records': []}, 'policies': {'acknowledged': {'synthetic-policy': True}}}
        request = RequestFactory().put('/ilr/', json.dumps({'draft': changed}), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True)
        for failure in ('read', 'write', 'client-unavailable'):
            if failure == 'client-unavailable':
                changed['plr'] = {'loadError': PLR_LOAD_ERROR}
                request = RequestFactory().put('/ilr/', json.dumps({'draft': changed}), content_type='application/json')
                request.user = SimpleNamespace(is_authenticated=True)
            target = 'filter' if failure == 'read' else 'update_or_create'
            manager = WizardPlrRecord.objects if failure == 'read' else WizardPlr.objects
            with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}), \
                 patch.object(manager, target, side_effect=DatabaseError('private write data')), \
                 patch('enrolment_api.extended_ilr.project_custom_fields'):
                response = extended_ilr(request, kind='apprenticeship', learner_id=self.learner.pk)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(json.loads(response.content)['draft']['plr'], {'loadError': PLR_LOAD_ERROR})
            row.refresh_from_db()
            self.assertEqual(row.wizard_draft['plr'], self.draft['plr'])
            self.assertEqual(WizardPlrRecord.objects.get(**self.scope).subject, 'Fictional qualification')
            self.assertTrue(WizardPolicyAck.objects.get(**self.scope, policy_id='synthetic-policy').acknowledged)

    def test_plr_outage_does_not_allow_submitted_completion(self):
        request = RequestFactory().put('/ilr/', json.dumps({'draft': self.draft, 'submitted': True}), content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True)
        self.learner.onboarding_status = 'Submitted'
        self.learner.save(update_fields=['onboarding_status'])
        with patch.object(WizardPlrRecord.objects, 'filter', side_effect=DatabaseError('unavailable')):
            response = extended_ilr(request, kind='apprenticeship', learner_id=self.learner.pk)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(ExtendedIlr.objects.filter(**self.scope).exists())
