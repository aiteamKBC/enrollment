"""Synthetic, isolated tests for authorized record edits and stale-save safety."""
from copy import deepcopy
import json
from unittest.mock import patch
from django.test import TestCase
from learner_api.models import EnrolmentUser
from .models import ExtendedIlr, WizardPlr, WizardPlrRecord
from .plr_record_changes import DELETED_KEY
from .tests_plr_uln import AdminUlnTests


class PlrRecordChangeTests(TestCase):
    databases = {'default', 'enrolment'}
    setUp = AdminUlnTests.setUp
    request = AdminUlnTests.request

    def change(self, action, *, expected=None, record=None, ref='qualification', role='admin'):
        body = {'draft': deepcopy(self.draft), 'plrRecordChange': {
            'action': action, 'id': ref, 'expected': expected or self.plr['records'][0],
        }}
        if record is not None:
            body['plrRecordChange']['record'] = record
        return self.request(body, role)

    def test_edit_is_selected_only_and_visible_to_student_after_stale_save(self):
        other = WizardPlrRecord.objects.create(**self.scope, record_ref='other', subject='Keep this qualification', record_type='Manual')
        original = WizardPlrRecord.objects.get(**self.scope, record_ref='qualification')
        original.evidence = [{'id': 'keep-certificate'}]; original.save(update_fields=['evidence'])
        response = self.change('edit', record={**self.plr['records'][0], 'grade': 'Pass'})
        self.assertEqual(response.status_code, 200, response.content)
        original.refresh_from_db(); other.refresh_from_db()
        self.assertEqual(original.grade, 'Pass')
        self.assertEqual(original.evidence, [{'id': 'keep-certificate'}])
        self.assertEqual(other.subject, 'Keep this qualification')
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, self.plr['uln'])
        stale = self.request({'draft': self.draft}, 'learner')
        self.assertEqual(stale.status_code, 200, stale.content)
        records = json.loads(self.request(role='learner').content)['draft']['plr']['records']
        self.assertEqual(next(r for r in records if r['id'] == original.record_ref)['grade'], 'Pass')
        self.assertTrue(any(r['id'] == 'other' for r in records))

    def test_delete_keeps_evidence_and_cannot_be_undone_by_stale_or_forged_drafts(self):
        row = WizardPlrRecord.objects.get(**self.scope)
        row.evidence = [{'id': 'retained-file', 'blobName': 'synthetic/cert.pdf'}]; row.save(update_fields=['evidence'])
        other = WizardPlrRecord.objects.create(**self.scope, record_ref='other', subject='Retained award', record_type='Manual')
        with patch('enrolment_api.learner_uploads.remove_blob') as remove_blob:
            response = self.change('delete')
            self.assertEqual(response.status_code, 200, response.content)
            remove_blob.assert_not_called()
        self.assertFalse(WizardPlrRecord.objects.filter(**self.scope, record_ref='qualification').exists())
        self.assertTrue(WizardPlrRecord.objects.filter(pk=other.pk).exists())
        stored = ExtendedIlr.objects.get(**self.scope).wizard_draft
        self.assertEqual(stored[DELETED_KEY]['qualification']['evidence'], row.evidence)
        self.assertNotIn(DELETED_KEY, json.loads(response.content)['draft'])
        for role in ['learner', 'admin']:
            response = self.request({'draft': {**self.draft, DELETED_KEY: {}}}, role)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertFalse(WizardPlrRecord.objects.filter(**self.scope, record_ref='qualification').exists())
            self.assertEqual([r['id'] for r in json.loads(response.content)['draft']['plr']['records']], ['other'])
        self.assertEqual(self.change('edit', record=self.plr['records'][0]).status_code, 404)

    def test_student_and_staff_edit_delete_requests_are_rejected(self):
        for role in ['learner', 'staff']:
            for action in ['edit', 'delete']:
                self.assertEqual(self.change(action, role=role, record=self.plr['records'][0]).status_code, 403)
        self.assertTrue(WizardPlrRecord.objects.filter(**self.scope).exists())

    def test_stale_edit_or_delete_cannot_erase_a_newer_change(self):
        self.assertEqual(self.change('edit', record={**self.plr['records'][0], 'grade': 'Pass'}).status_code, 200)
        for action in ['edit', 'delete']:
            self.assertEqual(self.change(action, record=self.plr['records'][0]).status_code, 409)
        self.assertEqual(WizardPlrRecord.objects.get(**self.scope).grade, 'Pass')

    def test_other_learner_record_cannot_be_changed(self):
        other = EnrolmentUser.objects.create(username='Other Synthetic Learner', learner_type='apprenticeship')
        row = WizardPlrRecord.objects.create(learner_kind='apprenticeship', learner_id=other.pk,
            record_ref='other-student-record', subject='Private qualification', record_type='Manual')
        response = self.change('delete', ref=row.record_ref, expected={**self.plr['records'][0], 'id': row.record_ref})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WizardPlrRecord.objects.filter(pk=row.pk).exists())

    def test_student_add_and_evidence_placeholder_still_work(self):
        row = WizardPlrRecord.objects.create(**self.scope, record_ref='new', evidence=[{'id': 'new-certificate'}])
        draft = {**self.draft, 'plr': {**self.plr, 'records': [*self.plr['records'], {
            'id': 'new', 'subject': 'Added by Student', 'recordType': 'Manual', 'credits': 0,
        }]}}
        response = self.request({'draft': draft}, 'learner')
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual(row.subject, 'Added by Student')
        self.assertEqual(row.evidence, [{'id': 'new-certificate'}])

    def test_legacy_json_only_records_can_be_edited_without_losing_other_entries(self):
        WizardPlrRecord.objects.filter(**self.scope).delete()
        response = self.change('edit', record={**self.plr['records'][0], 'grade': 'Pass'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(WizardPlrRecord.objects.get(**self.scope).grade, 'Pass')
