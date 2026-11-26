import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile
from learner_api.models import EnrolmentUser
from .models import WizardPlr, WizardPlrRecord
from .plr_pdf import parse_pdf, PlrError
from .plr_fixtures import synthetic_pdf
from . import plr_service as service
from .plr_views import personal_learning_records


class PlrPersistenceTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.learner = EnrolmentUser.objects.create(username='Morgan Synthetic Learner', date_of_birth='1994-03-12', learner_type='apprenticeship', programme_status='Onboarding')
        self.other = EnrolmentUser.objects.create(username='Another Fictional Learner', learner_type='apprenticeship', programme_status='Onboarding')
        self.report = parse_pdf(synthetic_pdf())
        self.kind = 'apprenticeship'

    def test_persistent_mapping_and_duplicates_after_edit(self):
        manual = WizardPlrRecord.objects.create(**service.scope(self.kind, self.learner.pk), record_ref='manual', record_type='Manual', subject='Manual attainment', evidence=[{'id': 'retained'}])
        result = service.import_report(self.kind, self.learner, self.report)
        self.assertEqual((result['added'], result['skipped']), (3, 0))
        self.assertEqual(result['uln'], self.report['uln'])
        first = next(r for r in result['records'] if r['recordType'] == 'Imported')
        self.assertIsNone(first['credits']); self.assertEqual(first['level'], ''); self.assertEqual(first['awardDate'], '')
        changed = {**first, 'subject': 'Reviewed title', 'grade': '', 'credits': 0}
        service.change_record(self.kind, self.learner.pk, first['id'], changed, result['revision'])
        again = service.import_report(self.kind, self.learner, self.report)
        self.assertEqual((again['added'], again['skipped']), (0, 3))
        saved = WizardPlrRecord.objects.get(record_ref=first['id'])
        self.assertEqual(saved.subject, 'Reviewed title'); self.assertEqual(saved.credits, 0)
        self.assertEqual(saved.source_metadata['grade'], 'L1')
        manual.refresh_from_db(); self.assertEqual(manual.evidence, [{'id': 'retained'}])

    def test_saved_and_unsaved_uln_mismatches_never_write(self):
        for existing, entered in [('2345609876', ''), ('', '2345609876')]:
            row, _ = WizardPlr.objects.get_or_create(**service.scope(self.kind, self.learner.pk))
            row.uln = existing; row.save()
            with self.assertRaisesRegex(PlrError, 'does not match'):
                service.import_report(self.kind, self.learner, self.report, entered)
            row.refresh_from_db(); self.assertEqual(row.uln, existing)
            self.assertEqual(WizardPlrRecord.objects.count(), 0)

    def test_uln_assigned_to_other_student_is_rejected(self):
        WizardPlr.objects.create(**service.scope(self.kind, self.other.pk), uln=self.report['uln'])
        with self.assertRaisesRegex(PlrError, 'another student'):
            service.import_report(self.kind, self.learner, self.report)
        self.assertFalse(WizardPlr.objects.filter(learner_id=self.learner.pk).exists())

    def test_identity_mismatch_cannot_be_reviewed_away(self):
        for report in ({**self.report, 'name': 'Wrong Learner'}, {**self.report, 'dob': '2000-01-01'}):
            with self.assertRaisesRegex(PlrError, 'does not match'):
                service.import_report(self.kind, self.learner, report, reviewed=True)
        self.assertEqual(WizardPlrRecord.objects.count(), 0)

    def test_missing_identity_requires_admin_review(self):
        report = {**self.report, 'dob': None}
        with self.assertRaises(PlrError) as exc: service.import_report(self.kind, self.learner, report)
        self.assertEqual(exc.exception.code, 'identity_review_required')
        self.assertEqual(service.import_report(self.kind, self.learner, report, reviewed=True)['added'], 3)

    def test_transaction_rolls_back_uln_and_all_records(self):
        report = {**self.report, 'records': [self.report['records'][0], {**self.report['records'][1], 'credits': 'bad'}]}
        with self.assertRaises(PlrError): service.import_report(self.kind, self.learner, report)
        self.assertEqual(WizardPlrRecord.objects.count(), 0)
        self.assertFalse(WizardPlr.objects.filter(learner_id=self.learner.pk).exists())

    def test_delete_retains_evidence_uln_and_source_identity(self):
        result = service.import_report(self.kind, self.learner, self.report)
        record = WizardPlrRecord.objects.first(); record.evidence = [{'id': 'keep'}]; record.save()
        deleted = service.change_record(self.kind, self.learner.pk, record.record_ref, None, result['revision'], delete=True)
        self.assertEqual(len(deleted['records']), 2); self.assertEqual(deleted['uln'], self.report['uln'])
        record.refresh_from_db(); self.assertEqual(record.evidence, [{'id': 'keep'}]); self.assertTrue(record.deleted)
        self.assertEqual(service.import_report(self.kind, self.learner, self.report)['added'], 0)

    def test_old_drafts_cannot_erase_edit_or_forge_imports(self):
        result = service.import_report(self.kind, self.learner, self.report)
        from .wizard_steps import project_draft
        with patch('enrolment_api.plr_service.transaction.atomic'):
            project_draft(self.kind, self.learner.pk, {'plr': {'uln': '', 'records': []}})
        self.assertEqual(service.state(self.kind, self.learner.pk)['records'], result['records'])
        forged = {**result['records'][0], 'subject': 'Forged title'}
        project_draft(self.kind, self.learner.pk, {'plr': {'uln': '1111111111', 'records': [forged]}})
        self.assertEqual(service.state(self.kind, self.learner.pk)['uln'], result['uln'])
        self.assertEqual(service.state(self.kind, self.learner.pk)['records'], result['records'])
        with self.assertRaises(PlrError): project_draft(self.kind, self.learner.pk, {'plr': {'records': [{**forged, 'id': 'forged'}]}})

    def test_student_manual_add_still_works_and_missing_values_stay_blank(self):
        service.append_draft_records(self.kind, self.learner.pk, {'records': [{'id': 'manual', 'recordType': 'Manual', 'subject': 'Manual title'}]})
        saved = service.state(self.kind, self.learner.pk)['records'][0]
        self.assertIsNone(saved['credits']); self.assertEqual(saved['awardDate'], '')

    def test_stale_edit_and_record_ownership(self):
        result = service.import_report(self.kind, self.learner, self.report)
        record = result['records'][0]
        for learner_id, revision in [(self.learner.pk, 0), (self.other.pk, 0)]:
            with self.assertRaises(PlrError): service.change_record(self.kind, learner_id, record['id'], record, revision)

    def test_uln_string_validation(self):
        for value in (1234567890, '123', '12345abcde', '１２３４５６７８９０'):
            with self.assertRaises(PlrError): service.valid_uln(value)
        self.assertEqual(service.valid_uln('0123456789'), '0123456789')

    def request(self, method, admin=True, learner_id=None, data=None):
        req = getattr(RequestFactory(), method)('/plr/', data=data or {}, content_type='application/json')
        req._dont_enforce_csrf_checks = True
        req.user = SimpleNamespace(is_authenticated=admin, is_staff=admin, is_superuser=admin)
        if not admin:
            req.login_account = SimpleNamespace(is_active=True, role='learner', subject_id=self.learner.pk)
        return personal_learning_records(req, kind=self.kind, learner_id=learner_id or self.learner.pk)

    def test_student_read_and_server_permissions(self):
        service.import_report(self.kind, self.learner, self.report)
        response = self.request('get', admin=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)['records']), 3)
        self.assertFalse(json.loads(response.content)['canManage'])
        for method in ('post', 'put', 'patch', 'delete'):
            self.assertEqual(self.request(method, admin=False).status_code, 403)
        self.assertEqual(self.request('get', admin=False, learner_id=self.other.pk).status_code, 404)

    def test_csrf_required(self):
        req = RequestFactory().put('/plr/', '{}', content_type='application/json')
        req.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=True)
        self.assertEqual(personal_learning_records(req, kind=self.kind, learner_id=self.learner.pk).status_code, 403)

    def test_admin_upload_real_synthetic_pdf_and_reload(self):
        file = SimpleUploadedFile('synthetic.pdf', synthetic_pdf(), content_type='application/pdf')
        req = RequestFactory().post('/plr/', {'file': file, 'enteredUln': ''})
        req._dont_enforce_csrf_checks = True
        req.user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=True)
        response = personal_learning_records(req, kind=self.kind, learner_id=self.learner.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['added'], 3)
        self.assertEqual(json.loads(self.request('get').content)['uln'], self.report['uln'])
