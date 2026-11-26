"""PDF confirmation writes synthetic records only to the isolated SQLite DB."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connections
from django.test import RequestFactory, TestCase, override_settings
from learner_api.models import EnrolmentUser
from .models import WizardPlr, WizardPlrRecord, ExtendedIlr
from .plr_fixtures import synthetic_pdf, synthetic_boundary_cases, EVENT_COLUMNS
from .plr_pdf_import import plr_pdf_import
from .plr_record_changes import IMPORT_KEY, DELETED_KEY
from .plr_uln import reserve_uln
from . import tests_plr_pdf_preview as preview_tests, tests_plr_uln as uln_tests


class PdfImportTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = preview_tests.PdfPreviewTests.setUp
    enrolment = uln_tests.AdminUlnTests.request

    def upload(self, data=None, role='admin', csrf=True, extra=None, gate='1'):
        req = RequestFactory().post('/import/', {'file': SimpleUploadedFile('synthetic.pdf',
            synthetic_pdf() if data is None else data, content_type='application/pdf'), **(extra or {})})
        req.user = SimpleNamespace(is_authenticated=role == 'admin', is_staff=role == 'admin')
        if role in ('learner', 'staff'):
            req.login_account = SimpleNamespace(role=role, is_active=True, subject_type=role, subject_id=self.learner.pk)
        req._dont_enforce_csrf_checks = csrf
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': gate}), \
             patch('enrolment_api.auth._accesses_of', return_value={'enrolment'}):
            return plr_pdf_import(req, kind='apprenticeship', learner_id=self.learner.pk)

    def test_import_saves_uln_records_and_statuses_atomically_and_student_can_read_them(self):
        WizardPlr.objects.filter(**self.scope).update(uln='')
        progress = ExtendedIlr.objects.get(**self.scope).wizard_draft['personalDetails']
        result = self.upload(extra={'uln': '1111111111', 'records': '[{"subject":"FORGED"}]', 'identityVerified': 'true'})
        self.assertEqual(result.status_code, 200, result.content)
        data = json.loads(result.content)
        self.assertEqual((data['added'], data['skipped']), (3, 0))
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '1234509876')
        self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), 4)
        imported = [r for r in data['plr']['records'] if r['recordType'] == 'Imported']
        self.assertEqual((imported[0]['level'], imported[0]['credits'], imported[0]['awardDate']), ('', None, ''))
        self.assertEqual(imported[0]['grade'], 'L1')
        self.assertEqual((imported[1]['completionStatus'], imported[1]['outcomeStatus']), ('Withdrawn', 'No Achievement'))
        self.assertEqual((imported[2]['level'], imported[2]['grade']), ('3', '0'))
        self.assertFalse(WizardPlrRecord.objects.filter(subject='FORGED').exists())
        saved = ExtendedIlr.objects.get(**self.scope).wizard_draft
        self.assertEqual(saved['personalDetails'], progress)
        student = json.loads(self.enrolment(role='learner').content)
        self.assertEqual(student['draft']['plr'], data['plr'])
        self.assertNotIn(IMPORT_KEY, student['draft'])

    def test_repeat_import_preserves_edits_deletions_manual_records_and_source_metadata(self):
        imported = json.loads(self.upload().content)['plr']
        first = next(r for r in imported['records'] if r['recordType'] == 'Imported')
        draft = json.loads(self.enrolment().content)['draft']
        edited = self.enrolment({'draft': draft, 'plrRecordChange': {'action': 'edit', 'id': first['id'],
            'expected': first, 'record': {**first, 'grade': 'Reviewed'}}})
        self.assertEqual(edited.status_code, 200, edited.content)
        result = json.loads(self.upload().content)
        self.assertEqual((result['added'], result['skipped']), (0, 3))
        first = next(r for r in result['plr']['records'] if r['id'] == first['id'])
        self.assertEqual(first['grade'], 'Reviewed'); self.assertIsNone(first['credits'])
        draft = json.loads(self.enrolment().content)['draft']
        deleted = self.enrolment({'draft': draft, 'plrRecordChange': {'action': 'delete', 'id': first['id'], 'expected': first}})
        self.assertEqual(deleted.status_code, 200, deleted.content)
        result = json.loads(self.upload().content)
        self.assertEqual((result['added'], result['skipped']), (0, 3))
        stale = self.enrolment({'draft': {**draft, IMPORT_KEY: {}, DELETED_KEY: {}}}, 'learner')
        self.assertEqual(stale.status_code, 200, stale.content)
        self.assertFalse(WizardPlrRecord.objects.filter(record_ref=first['id']).exists())
        stored = ExtendedIlr.objects.get(**self.scope).wizard_draft
        self.assertIn(first['id'], stored[IMPORT_KEY]); self.assertIn(first['id'], stored[DELETED_KEY])
        self.assertEqual(WizardPlrRecord.objects.get(record_ref='existing').subject, 'Keep this qualification')

    def test_permissions_csrf_and_invalid_pdf_are_rejected(self):
        for gate in ('0', '1'):
            for role in ('learner', 'staff', 'anonymous'):
                self.assertIn(self.upload(role=role, gate=gate).status_code, (401, 403))
        self.assertEqual(self.upload(csrf=False).status_code, 403)
        self.assertEqual(self.upload(data=b'not a PDF').status_code, 400)
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_identity_and_uln_are_revalidated_at_confirmation(self):
        for data in (synthetic_pdf(uln='1111111111'), synthetic_pdf(name='Wrong Synthetic Learner'), synthetic_pdf(dob='01 Jan 2000')):
            self.assertEqual(self.upload(data).status_code, 409)
        WizardPlr.objects.filter(**self.scope).update(uln='')
        self.learner.username = 'Changed after preview'; self.learner.save(update_fields=['username'])
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '')
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_another_students_uln_is_rejected_for_import_and_manual_assignment(self):
        other = EnrolmentUser.objects.create(username='Other Synthetic Learner', learner_type='apprenticeship')
        WizardPlr.objects.filter(**self.scope).update(uln='')
        WizardPlr.objects.create(learner_kind='apprenticeship', learner_id=other.pk, uln='1234509876')
        response = self.upload()
        self.assertEqual(response.status_code, 409, response.content)
        draft = json.loads(self.enrolment().content)['draft']
        response = self.enrolment({'draft': draft, 'ulnUpdate': {'value': '1234509876', 'expected': ''}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '')

    def test_record_failure_and_metadata_failure_roll_back_all_rows_and_uln(self):
        WizardPlr.objects.filter(**self.scope).update(uln='')
        before = ExtendedIlr.objects.get(**self.scope).wizard_draft
        bulk_create = WizardPlrRecord.objects.bulk_create
        def fail_second(rows, **kwargs):
            bulk_create(rows[:1], **kwargs)
            raise DatabaseError('synthetic confidential values')
        for target, failure in [('records', fail_second), ('metadata', DatabaseError('synthetic confidential values'))]:
            context = patch.object(WizardPlrRecord.objects, 'bulk_create', side_effect=failure) if target == 'records' else patch.object(ExtendedIlr, 'save', side_effect=failure)
            with context, self.assertLogs('enrolment_api.wizard_steps', level='ERROR') as logs:
                response = self.upload()
            self.assertEqual(response.status_code, 503, response.content)
            self.assertNotIn('synthetic confidential values', ' '.join(logs.output))
            self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '')
            self.assertEqual(WizardPlrRecord.objects.count(), 1)
            self.assertEqual(ExtendedIlr.objects.get(**self.scope).wizard_draft, before)

    def test_unavailable_plr_or_unverified_identity_does_not_import(self):
        with patch('enrolment_api.plr_pdf_import.read_plr', return_value={'loadError': 'PLR unavailable'}):
            self.assertEqual(self.upload().status_code, 503)
        self.learner.date_of_birth = None; self.learner.save(update_fields=['date_of_birth'])
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_zero_records_saves_uln_without_erasing_manual_records(self):
        WizardPlr.objects.filter(**self.scope).update(uln='')
        response = self.upload(synthetic_pdf([('Learning Events', []), ('Achievements', [])]))
        self.assertEqual(response.status_code, 200, response.content)
        result = json.loads(response.content)
        self.assertEqual((result['added'], result['skipped']), (0, 0))
        self.assertEqual(result['plr']['uln'], '1234509876')
        self.assertEqual(WizardPlrRecord.objects.count(), 1)

    def test_explicit_credits_and_missing_fields_are_preserved(self):
        columns = [*EVENT_COLUMNS[:-1], ('Credits', 740), EVENT_COLUMNS[-1]]
        row = ['', '', 'Award', 'SYN-A', 'Synthetic award', '', '20 Jul 2022', 'L1', '', '', '24', '']
        response = self.upload(synthetic_pdf([('Learning Events', [row])], event_columns=columns))
        self.assertEqual(response.status_code, 200, response.content)
        record = WizardPlrRecord.objects.get(record_type='Imported')
        self.assertEqual(record.credits, 24); self.assertIsNone(record.place_of_study); self.assertIsNone(record.award_date)

    def test_ordinary_student_add_cannot_forge_imported_records(self):
        draft = json.loads(self.enrolment().content)['draft']
        draft['plr']['records'].append({'id': 'forged-import', 'subject': 'Student manual award', 'recordType': 'Imported', 'credits': 0})
        response = self.enrolment({'draft': draft}, 'learner')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(WizardPlrRecord.objects.get(record_ref='forged-import').record_type, 'Manual')

    def test_postgresql_uln_assignment_uses_a_transaction_scoped_global_lock(self):
        cursor = MagicMock()
        connection = SimpleNamespace(vendor='postgresql', in_atomic_block=True, cursor=lambda: cursor)
        with patch('enrolment_api.plr_uln.connections', {'enrolment': connection}):
            reserve_uln('1234509876', self.learner.pk)
        self.assertIn('pg_advisory_xact_lock', cursor.__enter__.return_value.execute.call_args.args[0])

    def test_identical_printed_rows_keep_their_multiplicity_without_reimport_duplicates(self):
        row = ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic Award', '', '', '', '', '', '']
        pdf = synthetic_pdf([('Learning Events', [row, row])])
        first = self.upload(pdf)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(json.loads(first.content)['added'], 2)
        again = json.loads(self.upload(pdf).content)
        self.assertEqual((again['added'], again['skipped']), (0, 2))


@override_settings(ENROLMENT_ULN_STORAGE='created_users')
class CanonicalPdfBoundaryTests(TestCase):
    """Canonical read/write paths on an in-memory database, never Neon."""
    databases = {'default', 'enrolment'}
    setUp = preview_tests.PdfPreviewTests.setUp
    upload = PdfImportTests.upload
    preview = preview_tests.PdfPreviewTests.call
    enrolment = uln_tests.AdminUlnTests.request

    @classmethod
    def setUpTestData(cls):
        connection = connections['enrolment']
        if connection.vendor != 'sqlite' or connection.settings_dict['NAME'] != ':memory:':
            raise RuntimeError('Synthetic canonical PDF tests require in-memory SQLite.')
        # The test runner creates the legacy model schema. Add the canonical
        # column only inside this isolated test class's rollback transaction.
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE enrolment."Created_users" ADD COLUMN "ULN" VARCHAR(10)')

    def canonical_uln(self):
        with connections['enrolment'].cursor() as cursor:
            cursor.execute('SELECT "ULN" FROM enrolment."Created_users" WHERE id = %s', [self.learner.pk])
            return cursor.fetchone()[0]

    def test_boundary_layouts_preview_import_and_reimport_with_student_visibility(self):
        saved = ExtendedIlr.objects.get(**self.scope).wizard_draft['personalDetails']
        total = 1  # Existing manual qualification must survive every import.
        for name, pdf, expected in synthetic_boundary_cases():
            with self.subTest(layout=name):
                previous_uln = self.canonical_uln()
                preview = self.preview(pdf)
                self.assertEqual(preview.status_code, 200, preview.content)
                self.assertEqual(self.canonical_uln(), previous_uln)
                self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), total)
                self.assertEqual([(r['referenceNumber'], r['subject']) for r in json.loads(preview.content)['records']], expected)
                response = self.upload(pdf)
                self.assertEqual(response.status_code, 200, response.content)
                imported = json.loads(response.content)
                self.assertEqual((imported['added'], imported['skipped']), (len(expected), 0))
                total += len(expected)
                self.assertEqual(self.canonical_uln(), '1234509876')
                self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '1234509876')
                self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), total)
                stored = ExtendedIlr.objects.get(**self.scope).wizard_draft
                self.assertEqual(stored['personalDetails'], saved)
                self.assertEqual(WizardPlrRecord.objects.get(record_ref='existing').subject, 'Keep this qualification')
                for role in ('admin', 'learner'):
                    loaded = self.enrolment(role=role)
                    self.assertEqual(loaded.status_code, 200, loaded.content)
                    self.assertEqual(json.loads(loaded.content)['draft']['plr'], imported['plr'])
                again = self.upload(pdf)
                self.assertEqual(again.status_code, 200, again.content)
                repeated = json.loads(again.content)
                self.assertEqual((repeated['added'], repeated['skipped']), (0, len(expected)))
                self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), total)

    def test_ambiguous_layout_does_not_save_uln_records_or_progress(self):
        first = ['Synthetic College', '', 'Award', 'SYN-A', 'Synthetic award', '', '', '', '', '', '']
        other = ['', '', '', 'SYN-B', '', '', '', '', '', '', '']
        pdf = synthetic_pdf([('Learning Events', [first, other, other])], row_padding=1.5,
                            totals_override={'Learning Events': 2})
        before = ExtendedIlr.objects.get(**self.scope).wizard_draft
        for response in (self.preview(pdf), self.upload(pdf)):
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(json.loads(response.content)['code'], 'ambiguous_rows')
        self.assertIsNone(self.canonical_uln())
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, '1234509876')
        self.assertEqual(WizardPlrRecord.objects.filter(**self.scope).count(), 1)
        self.assertEqual(ExtendedIlr.objects.get(**self.scope).wizard_draft, before)
