"""Exercise existing wizard JSON persistence; modules need no new API or schema."""
import json
import os
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, SimpleTestCase

from enrolment_api import extended_ilr


class ModuleDraftPersistenceTests(SimpleTestCase):
    def test_existing_wizard_endpoint_round_trips_pcp_choices_and_keeps_other_answers(self):
        selection = {'programme': 'PCP_L6', 'pathway': 'chartered', 'modulesByPathway': {
            'operational': ['pmp'], 'chartered': ['pmo-stakeholders', 'portfolio']}}
        draft = {'cvJob': {'experienceText': 'Saved CV', 'hasFieldQualification': True},
                 'programmeModules': selection, 'personalDetails': {'firstName': 'Learner'}}
        row = SimpleNamespace(learner_kind='apprenticeship', learner_id=41, learner_name='Learner',
                              answers={'saved': 'ILR answers'}, wizard_draft={}, learner_signed=False,
                              learner_signed_date=None, provider_signed=False, provider_signed_date=None,
                              completed=False, updated_at=datetime.now(timezone.utc))
        def store(**kwargs):
            for name, value in kwargs['defaults'].items():
                setattr(row, name, deepcopy(value))
            return row, False
        learner_model = MagicMock()
        learner_model.objects.filter.return_value.first.return_value = SimpleNamespace(username='Learner')
        factory = RequestFactory()
        with patch.dict(os.environ, {'ENROLMENT_API_REQUIRE_AUTH': '0'}), \
                patch.dict(extended_ilr.KINDS, {'apprenticeship': learner_model}), \
                patch.object(extended_ilr.transaction, 'atomic', lambda **_: nullcontext()), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'update_or_create', side_effect=store), \
                patch.object(extended_ilr.ExtendedIlr.objects, 'filter') as find, \
                patch.object(extended_ilr, 'project_draft'), \
                patch.object(extended_ilr, 'project_custom_fields'), \
                patch('enrolment_api.skills_radar.service.read_legacy_radar', return_value=None), \
                patch.object(extended_ilr, 'read_ilr_details', return_value=None):
            find.return_value.first.return_value = row
            with patch('programme_suitability.modules_service.validate_choices', side_effect=lambda _kind, _id, value: value):
                saved = extended_ilr.extended_ilr(factory.put('/wizard/', json.dumps({'draft': draft}),
                                                            content_type='application/json'), 'apprenticeship', 41)
            self.assertEqual(saved.status_code, 200)
            reopened = extended_ilr.extended_ilr(factory.get('/wizard/'), 'apprenticeship', 41)
        data = json.loads(reopened.content)
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(data['draft'], draft)
        self.assertEqual(data['draft']['programmeModules'], selection)
        self.assertEqual(data['answers'], {'saved': 'ILR answers'})
