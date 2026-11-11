"""Real current learner snapshots through Create, finalise and existing signers."""
import copy
import json
import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, override_settings
from . import test_binding_audit as fixtures, test_blank_documents as edits, revisions, revision_views as views, service
from .models import LearnerDocumentRevision as Revision
from .registry import seed, seeds


@override_settings(DOCUMENT_REVISIONS_ENABLED=True)
class LearnerDocumentTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = fixtures.LearnerBindingAuditTests.setUp
    request = fixtures.LearnerBindingAuditTests.request
    payload = fixtures.LearnerBindingAuditTests.payload
    result = fixtures.LearnerBindingAuditTests.payload
    save = fixtures.LearnerBindingAuditTests.save
    publish = fixtures.LearnerBindingAuditTests.publish
    issued = fixtures.LearnerBindingAuditTests.issued
    save_revision = edits.BlankDocumentTests.save_blank
    finalize = edits.BlankDocumentTests.finalize
    learner_request = edits.BlankDocumentTests.learner_request

    @classmethod
    def setUpClass(cls):
        # Reuse the audit's real learner/curriculum fixtures; never the .env DB.
        if connection.vendor != 'sqlite' or connection.settings_dict['NAME'] != ':memory:':
            raise RuntimeError('Learner document tests require isolated SQLite.')
        models = (fixtures.Employer, fixtures.WizardPersonalDetails, fixtures.WizardIlrLearnerDetails,
                  fixtures.WizardPlr, fixtures.WizardPlrRecord, fixtures.WizardKsbAssessment,
                  fixtures.WizardSkillsRadar, fixtures.RplReviewDetail, fixtures.ModuleAuthoringModule,
                  fixtures.ModuleAuthoringWeek, fixtures.ModuleAuthoringComponent)
        with connection.schema_editor() as editor:
            for model in models:
                schema, table = model._meta.db_table.split('"."')
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT name FROM {schema}.sqlite_master WHERE type=\'table\' AND name=%s', [table])
                    exists = cursor.fetchone()
                if exists: continue
                constrained = [field for field in model._meta.fields if getattr(field, 'db_constraint', False)]
                for field in constrained: field.db_constraint = False
                try: editor.create_model(model)
                finally:
                    for field in constrained: field.db_constraint = True
                editor.deferred_sql.clear()
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA curriculum.table_info(modules)'); columns = {row[1] for row in cursor.fetchall()}
            for name in ('start_date', 'end_date', 'learner_roster_mode'):
                if name not in columns: cursor.execute(f'ALTER TABLE curriculum.modules ADD COLUMN {name} TEXT')
        super().setUpClass()

    def create(self, source, key='training-plan', learner=None, request_id=None, status=201):
        learner = learner or self.learner
        return self.result(self.request(views.start_revision, 'POST', {'sourceId': str(source.pk), 'mode': 'learner',
            'requestId': request_id or str(uuid.uuid4())}, pk=learner.pk, key=key), status)

    def test_all_eight_current_sources_are_scoped_populated_unsigned_and_frozen(self):
        from .preview import learner_data
        for key in seeds()['templates']:
            with self.subTest(key=key):
                source, original = self.issued(key, self.learner)
                self.learner.username = 'AUDIT_A_CURRENT_NAME'; self.learner.learner_start_date = date(2034, 2, 1); self.learner.learner_end_date = date(2036, 2, 1); self.learner.save()
                expected = learner_data(key, self.learner)
                request_id = str(uuid.uuid4()); detail = self.create(source, key, request_id=request_id)
                self.assertEqual(detail['revision']['creationMode'], 'learner')
                self.assertEqual(detail['revision']['number'], 2)
                self.assertEqual(detail['preview']['template']['fields']['learner.fullName'], 'AUDIT_A_CURRENT_NAME')
                self.assertEqual(detail['preview']['template']['fields']['enrolment.startDate'], '2034-02-01')
                self.assertNotIn('AUDIT_B', json.dumps(detail))
                self.assertEqual(detail['revision']['provenance'], {})
                self.assertFalse(detail['revision']['fullySigned']); self.assertTrue(detail['revision']['canDiscard'])
                for role in seed(key)['signatureRoles']:
                    self.assertFalse(detail['preview']['data']['signatures'][role]['signed'])
                    self.assertEqual(detail['preview']['data']['signatures'][role]['name'], '')
                self.assertNotIn(fixtures.MARK, json.dumps(detail))
                if key == 'training-plan':
                    self.assertEqual(detail['preview']['data']['learningPlan'], expected['learningPlan'])
                    self.assertTrue(detail['preview']['data']['learningPlan'])
                if key.startswith('review-'):
                    self.assertEqual(detail['preview']['data']['answers'], expected['answers'])
                    self.assertFalse(detail['preview']['data']['completed'])
                self.assertEqual(self.create(source, key, request_id=request_id)['revision']['id'], detail['revision']['id'])
                saved = self.save_revision(detail)
                self.learner.username = 'Changed AFTER draft'; self.learner.learner_start_date = date(2040, 1, 1); self.learner.save()
                final = self.finalize(saved)
                self.assertEqual(final['preview']['template']['fields'], detail['preview']['template']['fields'])
                signing = revisions.source_record(key, self.learner.pk, Revision.objects.get(pk=final['revision']['id']).signing_source_id)
                if key == 'training-plan': self.assertEqual(signing.learning_plan, expected['learningPlan'])
                if key == 'apprenticeship-agreement': self.assertEqual(signing.apprentice_name, 'AUDIT_A_CURRENT_NAME')
                if key == 'ilr': self.assertEqual(signing.learner_details, expected['learnerDetails'])
                if key == 'written-agreement': self.assertEqual(signing.delivery, expected['delivery'])
                self.assertEqual(final['revision']['status'], 'Available')
                source.refresh_from_db(); self.assertEqual(revisions.serialize_source(key, source, self.learner), original)
                self.result(self.learner_request(views.revision_detail, identifier=final['revision']['id']))

    def test_latest_template_fresh_signing_and_notifications_are_for_the_new_revision_only(self):
        from enrolment_api import pending_signatures
        source, original = self.issued('training-plan', self.learner)
        latest = seed('training-plan'); latest['title'] = 'Latest current-data Training Plan'
        published = self.publish(latest)
        self.learner.onboarding_status = 'Submitted'; self.learner.save()
        detail = self.create(source)
        self.assertEqual(detail['revision']['templateVersionId'], published['id'])
        def pending():
            request = self.factory.get('/'); request.user = SimpleNamespace(is_authenticated=False)
            request.login_account = SimpleNamespace(role='learner', is_active=True, subject_type='learner', subject_id=self.learner.pk)
            return self.result(pending_signatures.pending_signatures(request))
        self.assertFalse(any(row['documentKey'] == 'training-plan' for row in pending()['results']))
        final = self.finalize(self.save_revision(detail))
        notice = next(row for row in pending()['results'] if row['documentKey'] == 'training-plan')
        self.assertEqual(notice['revisionId'], final['revision']['id']); self.assertEqual(notice['templateVersion'], 2)
        for role in ('apprentice', 'employer', 'provider'):
            data = {'party': role, 'name': 'Fresh ' + role, 'signature': fixtures.MARK}
            if role == 'apprentice': signed = self.result(self.learner_request(views.sign_revision, 'POST', data, identifier=final['revision']['id']))
            elif role == 'employer':
                request = self.factory.post('/', data=json.dumps(data), content_type='application/json'); request.user = SimpleNamespace(is_authenticated=False)
                request.login_account = SimpleNamespace(role='employer', is_active=True, subject_id=20); request._dont_enforce_csrf_checks = True
                with patch('enrolment_api.auth._employs', return_value=True): signed = self.result(views.sign_revision(request, pk=self.learner.pk, identifier=final['revision']['id']))
            else: signed = self.result(self.request(views.sign_revision, 'POST', data, pk=self.learner.pk, identifier=final['revision']['id']))
        self.assertEqual(signed['revision']['status'], 'Signed')
        self.assertEqual(signed['preview']['data']['learningPlan'], detail['preview']['data']['learningPlan'])
        source.refresh_from_db(); self.assertEqual(revisions.serialize_source('training-plan', source, self.learner), original)
        self.assertFalse(any(row['documentKey'] == 'training-plan' for row in pending()['results']))

    def test_missing_student_dates_stay_blank_and_do_not_use_cohort_dates(self):
        source, _ = self.issued('apprenticeship-agreement', self.learner)
        self.learner.learner_start_date = None; self.learner.learner_end_date = None; self.learner.save()
        detail = self.create(source, 'apprenticeship-agreement')
        self.assertIsNone(detail['preview']['template']['fields']['enrolment.startDate'])
        self.assertIsNone(detail['preview']['template']['fields']['enrolment.endDate'])
        self.assertEqual([w['code'] for w in detail['preview']['bindingWarnings']], ['missing_date', 'missing_date'])

    def test_pristine_unanswered_draft_is_upgraded_but_saved_work_is_preserved(self):
        source, _ = self.issued('training-plan', self.learner)
        old = self.result(self.request(views.start_revision, 'POST', {'sourceId': str(source.pk), 'mode': 'template', 'requestId': str(uuid.uuid4())}, pk=self.learner.pk, key='training-plan'), 201)
        detail = self.create(source)
        self.assertEqual(detail['revision']['id'], old['revision']['id'])
        self.assertEqual(detail['revision']['editVersion'], 2)
        self.assertTrue(detail['preview']['data']['programme']['apprenticeName'])
        saved = self.save_revision(detail)
        self.create(source, status=409)
        current = self.result(self.request(views.revision_detail, pk=self.learner.pk, identifier=saved['revision']['id']))
        self.assertEqual(current['preview'], saved['preview'])
