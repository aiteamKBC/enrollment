"""Admin ULN writes use synthetic SQLite fixtures, never the hosted database."""
import json
import os
from types import SimpleNamespace
from unittest.mock import patch
from django.test import RequestFactory, TestCase
from learner_api.models import EnrolmentUser
from .extended_ilr import extended_ilr
from .models import ExtendedIlr, WizardPlr, WizardPlrRecord
from .wizard_steps import project_draft


class AdminUlnTests(TestCase):
    databases = {'default', 'enrolment'}

    def setUp(self):
        self.learner = EnrolmentUser.objects.create(username='Synthetic ULN Learner',
            learner_type='apprenticeship', programme_status='Onboarding')
        self.scope = {'learner_kind': 'apprenticeship', 'learner_id': self.learner.pk}
        self.plr = {'uln': '0123456789', 'records': [{'id': 'qualification', 'subject': 'Synthetic Award',
            'recordType': 'Manual', 'credits': 0}]}
        self.draft = {'plr': self.plr, 'personalDetails': {'firstName': 'Synthetic', 'lastName': 'Learner'}}
        project_draft('apprenticeship', self.learner.pk, self.draft)
        ExtendedIlr.objects.create(**self.scope, wizard_draft=self.draft)

    def request(self, body=None, role='admin'):
        factory = RequestFactory()
        req = factory.get('/ilr/') if body is None else factory.put('/ilr/', json.dumps(body), content_type='application/json')
        req.user = SimpleNamespace(is_authenticated=role == 'admin', is_staff=role == 'admin')
        if role != 'admin':
            req.login_account = SimpleNamespace(is_active=True, role=role, subject_id=self.learner.pk,
                subject_type='learner' if role == 'learner' else 'staff', id=1)
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '1'}), \
             patch('enrolment_api.extended_ilr.signed_snapshot', return_value=None), \
             patch('enrolment_api.extended_ilr.legacy_review_required', return_value=False), \
             patch('enrolment_api.extended_ilr.project_custom_fields'), \
             patch('enrolment_api.eligibility_prefill.add_defaults'), \
             patch('enrolment_api.auth._accesses_of', return_value={'enrolment'}), \
             patch('enrolment_api.plr_uln._accesses_of', return_value={'enrolment'}):
            return extended_ilr(req, kind='apprenticeship', learner_id=self.learner.pk)

    def test_valid_and_empty_ulns_persist_as_strings_on_selected_learner(self):
        other = EnrolmentUser.objects.create(username='Other Synthetic Learner', learner_type='apprenticeship')
        other_plr = WizardPlr.objects.create(learner_kind='apprenticeship', learner_id=other.pk, uln='9876543210')
        expected = self.plr['uln']
        for value in ['0012345678', '']:
            response = self.request({'draft': self.draft, 'ulnUpdate': {'value': value, 'expected': expected}})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(WizardPlr.objects.get(**self.scope).uln, value)
            loaded = json.loads(self.request().content)
            self.assertEqual(loaded['draft']['plr']['uln'], value)
            self.assertTrue(loaded['meta']['canEditUln'])
            expected = value
        other_plr.refresh_from_db()
        self.assertEqual(other_plr.uln, '9876543210')
        self.assertEqual(WizardPlrRecord.objects.get(**self.scope).subject, 'Synthetic Award')

    def test_invalid_values_are_rejected_without_writes(self):
        for value in [None, 1234567890, '123', '12345678901', '12345abcde', '１２３４５６７８９０']:
            response = self.request({'draft': self.draft, 'ulnUpdate': {'value': value, 'expected': self.plr['uln']}})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(WizardPlr.objects.get(**self.scope).uln, self.plr['uln'])

    def test_student_and_staff_cannot_use_admin_uln_update(self):
        for role in ['learner', 'staff']:
            response = self.request({'draft': self.draft, 'ulnUpdate': {'value': '', 'expected': self.plr['uln']}}, role)
            self.assertEqual(response.status_code, 403)
            self.assertFalse(json.loads(self.request(role=role).content)['meta']['canEditUln'])

    def test_stale_and_empty_unrelated_drafts_do_not_overwrite_uln(self):
        for role in ['learner', 'admin']:
            draft = {**self.draft, 'plr': {**self.plr, 'uln': ''}}
            response = self.request({'draft': draft}, role)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(WizardPlr.objects.get(**self.scope).uln, self.plr['uln'])
            self.assertEqual(json.loads(response.content)['draft']['plr']['uln'], self.plr['uln'])

    def test_stale_explicit_edit_is_rejected(self):
        response = self.request({'draft': self.draft, 'ulnUpdate': {'value': '', 'expected': '9999999999'}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(WizardPlr.objects.get(**self.scope).uln, self.plr['uln'])

    def test_reads_use_shared_uln_instead_of_stale_snapshot(self):
        WizardPlr.objects.filter(**self.scope).update(uln='1111111111')
        response = self.request()
        self.assertEqual(json.loads(response.content)['draft']['plr']['uln'], '1111111111')

    def test_platform_admin_still_requires_enrolment_grant(self):
        from .plr_uln import can_edit_uln
        request = SimpleNamespace(login_account=SimpleNamespace(is_active=True, role='admin', subject_type='staff'))
        with patch('enrolment_api.plr_uln._accesses_of', return_value={'coach'}):
            self.assertFalse(can_edit_uln(request))
        with patch('enrolment_api.plr_uln._accesses_of', return_value={'enrolment'}):
            self.assertTrue(can_edit_uln(request))

    def test_empty_uln_and_records_save_without_new_requirements(self):
        WizardPlr.objects.filter(**self.scope).update(uln='')
        draft = {**self.draft, 'plr': {'uln': '', 'records': []}}
        # A genuinely empty fixture, rather than asking a stale draft to delete a record.
        WizardPlrRecord.objects.filter(**self.scope).delete()
        ExtendedIlr.objects.filter(**self.scope).update(wizard_draft=draft)
        response = self.request({'draft': draft}, 'learner')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)['draft']['plr'], draft['plr'])
