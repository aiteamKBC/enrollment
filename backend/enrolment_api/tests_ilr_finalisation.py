"""No database or external services: explicit ILR finalisation contract tests."""
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase

from . import extended_ilr
from .ilr_finalisation import legacy_review_required, signed_snapshot, validate_finalisation


PERSONAL = {
    'title': 'Ms', 'currentPostcode': 'CT1 1AA',
    'signature': 'data:image/png;base64,TEST',
    'signatureDeclaration': {
        'text': 'I certify that the information contained on this form is correct\n'
                'By signing, I confirm the ILR accuracy declaration and authorise this signature to be used on my enrolment paperwork.',
        'confirmedAt': '2026-11-04T12:00:00Z',
    },
}
DETAILS = {
    'legalSex': 'Female', 'ethnicity': 'Irish', 'longTermDisability': False,
    'niNumber': 'AB 12 34 56 C', 'niApplied': None, 'workingHoursPerWeek': '37.5',
    'highestQualification': 'Full level 3', 'employmentStatus': 'In paid employment',
    'employmentStartDate': '2024-01-15', 'fullTimeEducation': False,
}
SNAPSHOT = {'personalDetails': PERSONAL, 'ilrDetails': DETAILS,
            'finalisedAt': '2026-11-04T12:00:00Z', 'ilrCustom': {}}


class FinalisationValidationTests(SimpleTestCase):
    def setUp(self):
        self.learner = SimpleNamespace(title='Ms', current_postcode='CT1 1AA')

    def check(self, draft):
        with patch('enrolment_api.ilr_finalisation.read_current', return_value=None), \
                patch('enrolment_api.ilr_finalisation.connections') as connections:
            connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = MagicMock()
            return validate_finalisation(draft, self.learner)

    def test_complete_and_explicitly_confirmed(self):
        self.check({'personalDetails': PERSONAL, 'ilrDetails': DETAILS})

    def test_missing_required_or_signature_does_not_finalise(self):
        with self.assertRaisesMessage(ValueError, 'Employment status'):
            self.check({'personalDetails': PERSONAL, 'ilrDetails': {**DETAILS, 'employmentStatus': '', 'workingHoursPerWeek': ''}})
        with self.assertRaisesMessage(ValueError, 'Sign and confirm'):
            self.check({'personalDetails': {**PERSONAL, 'signatureDeclaration': None}, 'ilrDetails': DETAILS})

    def test_existing_signature_image_alone_is_not_finalisation(self):
        with self.assertRaisesMessage(ValueError, 'Sign and confirm'):
            self.check({'personalDetails': {**PERSONAL, 'signatureDeclaration': None}, 'ilrDetails': DETAILS})

    def test_corrupt_finalisation_fails_closed(self):
        row = SimpleNamespace(ilr_finalised_at='2026-11-04', ilr_signed_snapshot=None)
        with patch('enrolment_api.ilr_finalisation.finalisation', return_value=row):
            with self.assertRaises(DatabaseError):
                signed_snapshot(41)

    def test_historical_signature_without_explicit_next_event_is_not_locked(self):
        row = SimpleNamespace(ilr_finalised_at=None, ilr_signed_snapshot=None,
                              declaration_signature='data:image/png;base64,OLD')
        with patch('enrolment_api.ilr_finalisation.finalisation', return_value=row):
            self.assertIsNone(signed_snapshot(41))

    def test_submitted_historical_wizard_signature_requires_review_not_auto_finalisation(self):
        learner = SimpleNamespace(onboarding_status='Submitted')
        with patch('enrolment_api.ilr_finalisation.WizardPersonalDetails.objects.filter') as rows:
            rows.return_value.exclude.return_value.exists.return_value = True
            self.assertTrue(legacy_review_required(learner, 41))
        learner.onboarding_status = 'In progress'
        self.assertFalse(legacy_review_required(learner, 41))


class FinalisationEndpointTests(SimpleTestCase):
    def request(self, draft, finalise=False):
        request = RequestFactory().put('/enrolment_api/extended-ilr/apprenticeship/41/',
                                       data=json.dumps({'draft': draft, **({'finalizeIlr': True} if finalise else {})}),
                                       content_type='application/json')
        request.login_account = SimpleNamespace(role='learner', subject_id=41, id=7, is_active=True)
        return request

    def test_forged_post_sign_change_is_rejected_before_any_write(self):
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username='Synthetic Learner')
        learner_model.objects.select_for_update.return_value.get.return_value = SimpleNamespace(username='Synthetic Learner')
        changed = {**PERSONAL, 'title': 'Dr'}
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=SNAPSHOT), \
                patch.object(extended_ilr, 'published_ilr_custom_keys', return_value=set()), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create') as save:
            response = extended_ilr.extended_ilr(self.request({'personalDetails': changed, 'ilrDetails': DETAILS}), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    def test_signature_replacement_and_new_ilr_custom_answer_are_rejected(self):
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username='Synthetic Learner')
        learner_model.objects.select_for_update.return_value.get.return_value = SimpleNamespace(username='Synthetic Learner')
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=SNAPSHOT), \
                patch.object(extended_ilr, 'published_ilr_custom_keys', return_value={'cf_new'}), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create') as save:
            replaced = extended_ilr.extended_ilr(self.request({
                'personalDetails': {**PERSONAL, 'signature': 'data:image/png;base64,REPLACED'},
                'ilrDetails': DETAILS,
            }), 'apprenticeship', 41)
            new_answer = extended_ilr.extended_ilr(self.request({
                'personalDetails': PERSONAL, 'ilrDetails': DETAILS,
                'custom': {'cf_new': 'forged'},
            }), 'apprenticeship', 41)
        self.assertEqual(replaced.status_code, 409)
        self.assertEqual(new_answer.status_code, 409)
        save.assert_not_called()

    def test_ambiguous_submitted_historical_record_cannot_be_re_signed(self):
        learner_model = MagicMock()
        learner = SimpleNamespace(username='Synthetic Learner', onboarding_status='Submitted')
        learner_model.objects.filter.return_value.first.return_value = learner
        learner_model.objects.select_for_update.return_value.get.return_value = learner
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=None), \
                patch.object(extended_ilr, 'legacy_review_required', return_value=True), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create') as save:
            response = extended_ilr.extended_ilr(self.request({
                'personalDetails': PERSONAL, 'ilrDetails': DETAILS,
            }, True), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 409)
        save.assert_not_called()

    def test_duplicate_sign_and_next_is_idempotent(self):
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username='Synthetic Learner')
        learner_model.objects.select_for_update.return_value.get.return_value = SimpleNamespace(username='Synthetic Learner')
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=SNAPSHOT), \
                patch.object(extended_ilr, 'published_ilr_custom_keys', return_value=set()), \
                patch.object(extended_ilr, 'read_extended_ilr', return_value={'meta': {'ilrFinalisedAt': SNAPSHOT['finalisedAt']}}), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create') as save:
            response = extended_ilr.extended_ilr(self.request({
                'personalDetails': PERSONAL, 'ilrDetails': DETAILS,
            }, True), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 200)
        save.assert_not_called()

    def test_signed_ilr_does_not_block_later_extended_ilr_answers(self):
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username='Synthetic Learner')
        learner_model.objects.select_for_update.return_value.get.return_value = SimpleNamespace(username='Synthetic Learner')
        row = MagicMock()
        previous = SimpleNamespace(wizard_draft={'personalDetails': {'staff': 'retained'}})
        request = self.request({'personalDetails': PERSONAL, 'ilrDetails': DETAILS, 'policies': {'acknowledged': {}}})
        request._body = json.dumps({'answers': {'contact': {'byEmail': True}}, 'draft': {
            'personalDetails': PERSONAL, 'ilrDetails': DETAILS, 'policies': {'acknowledged': {}},
        }}).encode()
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=SNAPSHOT), \
                patch.object(extended_ilr, 'published_ilr_custom_keys', return_value=set()), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'filter') as saved_row, \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create', return_value=(row, False)) as save, \
                patch.object(extended_ilr, 'project_draft') as project, \
                patch.object(extended_ilr, 'project_custom_fields'), \
                patch.object(extended_ilr, '_payload', return_value={'meta': {}, 'draft': {}}):
            saved_row.return_value.first.return_value = previous
            response = extended_ilr.extended_ilr(request, 'apprenticeship', 41)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.kwargs['defaults']['wizard_draft']['personalDetails'], {'staff': 'retained'})
        self.assertEqual(project.call_args.args[2], {'policies': {'acknowledged': {}}})

    def test_failed_projection_does_not_mark_ilr_finalised(self):
        learner_model = MagicMock()
        learner = MagicMock(username='Synthetic Learner', title='Ms', current_postcode='CT1 1AA')
        learner_model.objects.filter.return_value.first.return_value = learner
        learner_model.objects.select_for_update.return_value.get.return_value = learner
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=None), \
                patch.object(extended_ilr, 'validate_finalisation'), \
                patch.object(extended_ilr, 'project_draft', side_effect=DatabaseError('synthetic failure')), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'filter', return_value=SimpleNamespace(first=lambda: None)), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create', return_value=(MagicMock(), True)), \
                patch.object(extended_ilr.WizardIlrLearnerDetails.objects, 'filter') as details:
            response = extended_ilr.extended_ilr(self.request({
                'personalDetails': PERSONAL, 'ilrDetails': DETAILS,
            }, True), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'synthetic failure', response.content)
        details.return_value.update.assert_not_called()

    def test_sign_and_next_stores_explicit_actor_timestamp_and_snapshot(self):
        learner_model = MagicMock()
        learner = MagicMock(username='Synthetic Learner', title='Ms', current_postcode='CT1 1AA')
        learner_model.objects.filter.return_value.first.return_value = learner
        learner_model.objects.select_for_update.return_value.get.return_value = learner
        row = MagicMock()
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr, 'signed_snapshot', return_value=None), \
                patch.object(extended_ilr, 'validate_finalisation'), \
                patch.object(extended_ilr, 'project_draft'), \
                patch.object(extended_ilr, 'project_custom_fields'), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'filter', return_value=SimpleNamespace(first=lambda: None)), \
                patch.object(extended_ilr, 'published_ilr_custom_keys', return_value=set()), \
                patch.object(extended_ilr, 'learner_record', return_value={'title': 'Ms'}), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create', return_value=(row, True)), \
                patch.object(extended_ilr.WizardIlrLearnerDetails.objects, 'filter') as details, \
                patch.object(extended_ilr, '_payload', return_value={'meta': {}, 'draft': {}}):
            details.return_value.update.return_value = 1
            response = extended_ilr.extended_ilr(self.request({'personalDetails': PERSONAL, 'ilrDetails': DETAILS}, True), 'apprenticeship', 41)
        self.assertEqual(response.status_code, 200)
        saved = details.return_value.update.call_args.kwargs
        self.assertEqual(saved['ilr_finalised_by'], 7)
        self.assertEqual(saved['ilr_signed_snapshot']['personalDetails']['signature'], PERSONAL['signature'])
        self.assertEqual(json.loads(response.content)['meta']['ilrFinalisedAt'], saved['ilr_signed_snapshot']['finalisedAt'])
