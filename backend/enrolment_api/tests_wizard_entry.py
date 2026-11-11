"""Apprenticeship entry and profile Title; database and email are fully mocked."""
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase
from enrolment_api import extended_ilr as ilr, wizard_bootstrap as bootstrap


class TitleTests(SimpleTestCase):
    def save(self, personal=None, kind='apprenticeship'):
        learner = MagicMock(username='Test Learner', title='Dr')
        model = MagicMock()
        model.objects.filter.return_value.first.return_value = learner
        model.objects.select_for_update.return_value.get.return_value = learner
        draft = {'personalDetails': personal} if personal is not None else {'plr': {'uln': '123'}}
        request = RequestFactory().put('/enrolment_api/extended-ilr/apprenticeship/41/',
                                      data=json.dumps({'draft': draft}), content_type='application/json')
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
             patch.dict(ilr.KINDS, {kind: model}), \
             patch.object(ilr.transaction, 'atomic', lambda **_: nullcontext()), \
             patch.object(ilr.ExtendedIlr.objects, 'filter', return_value=SimpleNamespace(first=lambda: None)), \
             patch.object(ilr.ExtendedIlr.objects, 'update_or_create', return_value=(MagicMock(), True)) as upsert, \
             patch.object(ilr, 'project_draft'), patch.object(ilr, 'project_custom_fields'), \
             patch.object(ilr, '_payload', return_value={'draft': {}}):
            response = ilr.extended_ilr(request, kind=kind, learner_id=41)
        return response, learner, upsert

    def test_title_saves_to_the_existing_column_and_preserves_the_draft_answer(self):
        response, learner, upsert = self.save({'title': ' Ms ', 'signature': 'original', 'signatureDate': '2020-01-02'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(learner.title, 'Ms')
        self.assertEqual(json.loads(response.content)['draft']['personalDetails']['title'], 'Ms')
        learner.save.assert_called_once_with(update_fields=['title'])
        stored = upsert.call_args.kwargs['defaults']['wizard_draft']['personalDetails']
        self.assertEqual(stored, {'title': ' Ms ', 'signature': 'original', 'signatureDate': '2020-01-02'})

    def test_unrelated_or_old_drafts_do_not_overwrite_profile_title(self):
        for personal in (None, {'firstName': 'Test'}):
            response, learner, _ = self.save(personal)
            self.assertEqual(response.status_code, 200)
            learner.save.assert_not_called()

    def test_partial_drafts_and_explicit_clear_remain_saveable(self):
        response, learner, _ = self.save({'title': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(learner.title, '')

    def test_invalid_title_does_not_write_anything(self):
        for value in (None, 123, {}, 'x' * 31):
            response, learner, upsert = self.save({'title': value})
            self.assertEqual(response.status_code, 400)
            upsert.assert_not_called()
            learner.save.assert_not_called()

    def test_existing_titles_and_commercial_persistence_are_preserved(self):
        response, learner, _ = self.save({'title': 'Reverend'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(learner.title, 'Reverend')
        response, learner, upsert = self.save({'title': 'Ms'}, kind='commercial')
        self.assertEqual(response.status_code, 200)
        learner.save.assert_not_called()
        self.assertEqual(upsert.call_args.kwargs['defaults']['wizard_draft']['personalDetails']['title'], 'Ms')


class EntryTests(SimpleTestCase):
    def open(self, *, kind='apprenticeship', status='Fresh user', saved=None, onboarding='Not started', role='learner'):
        learner = MagicMock(pk=41, learner_type=kind, programme_status=status, onboarding_status=onboarding, username='Test Learner')
        board = {'contact': {}, 'programme': {'name': ''}}
        request = RequestFactory().get('/enrolment_api/wizard-bootstrap/apprenticeship/41/')
        request.login_account = SimpleNamespace(role=role, subject_id=41)
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
             patch.object(bootstrap.EnrolmentUser.all_learners, 'filter') as query, \
             patch('learner_api.learning_plan.promote_learner_if_ready', return_value=False), \
             patch.object(bootstrap, 'advance_learner'), \
             patch.object(bootstrap, 'to_board', return_value=board), \
             patch.object(bootstrap, 'read_extended_ilr', return_value={'draft': saved or {}}):
            query.return_value.first.return_value = learner
            response = bootstrap.wizard_bootstrap(request, kind=kind, learner_id=41)
        self.assertEqual(response.status_code, 200, response.content)
        return learner, json.loads(response.content)

    def test_opening_apprenticeship_does_not_change_programme_status(self):
        for status in ('Fresh user', ''):
            learner, data = self.open(status=status)
            self.assertEqual(learner.programme_status, status)
            learner.save.assert_not_called()
            self.assertTrue(data['board']['contact']['titleRequired'])
            self.assertEqual(data['ilr']['draft'], {})

    def test_historical_signed_or_submitted_records_are_not_blocked_by_missing_title(self):
        for saved, onboarding in (({'personalDetails': {'signature': 'old', 'signatureDate': '2020-01-02'}}, ''),
                                  ({'ilrDetails': {'signature': 'old ILR mark'}}, ''), ({}, 'Submitted'), ({}, 'Completed')):
            learner, data = self.open(status='Onboarding', saved=saved, onboarding=onboarding)
            learner.save.assert_not_called()
            self.assertFalse(data['board']['contact']['titleRequired'])
            self.assertEqual(data['ilr']['draft'], saved)

    def test_commercial_and_later_stage_status_are_not_changed(self):
        for kind, status in (('commercial', 'Fresh user'), ('commercial', 'Onboarding'), ('apprenticeship', 'Active')):
            learner, _ = self.open(kind=kind, status=status)
            learner.save.assert_not_called()

    def test_staff_preview_does_not_start_somebody_elses_onboarding(self):
        learner, _ = self.open(role='staff')
        learner.save.assert_not_called()
