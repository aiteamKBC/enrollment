"""No external handoff or database: preserve the current v1 payload contract."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from .handoff import snapshot


class UlnHandoffCompatibilityTests(SimpleTestCase):
    def test_new_canonical_column_is_excluded_but_legacy_uln_is_retained(self):
        for extra in ({}, {'ULN': None}, {'ULN': '0012345678'}):
            user = {'id': 7, 'uuid': 'synthetic-uuid', 'Learner_type': 'apprenticeship',
                    'Email': 'synthetic@example.invalid', 'learner_id': None, **extra}
            account = {'Subject_type': 'learner', 'Subject_id': 7, 'Role': 'learner',
                       'Email': 'synthetic@example.invalid'}
            plr = {'Learner_kind': 'apprenticeship', 'Learner_id': 7, 'ULN': extra.get('ULN')}
            cursor = MagicMock()
            def execute(query, params):
                if '"Created_users"' in query:
                    result = [user.copy()]
                elif '"Login_accounts"' in query:
                    result = [account]
                elif '"Wizard_Plr"' in query:
                    result = [plr]
                else:
                    result = []
                cursor.fetchall.return_value = [(row,) for row in result]
            cursor.execute.side_effect = execute
            connection = SimpleNamespace(cursor=lambda: cursor)
            cursor.__enter__.return_value = cursor
            with patch('enrollment_service.handoff.transaction.atomic'), \
                 patch('enrollment_service.handoff.connections', {'enrolment': connection}):
                payload = snapshot(7)
            self.assertEqual(payload['version'], 1)
            self.assertNotIn('ULN', payload['rows']['enrolment.Created_users'][0])
            self.assertEqual(payload['rows']['enrolment.Wizard_Plr'][0], plr)
