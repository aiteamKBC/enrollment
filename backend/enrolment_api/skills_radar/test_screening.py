import json
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.urls import resolve

from programme_suitability.models import SkillsRadarScreeningSetting

from .screening import KEY, read_setting, save_setting


class ScreeningSettingTests(TestCase):
    def test_default_change_history_and_stale_edit(self):
        self.assertEqual(read_setting(), {'thresholdPercent': 50, 'revision': 0, 'schemaReady': True})
        self.assertEqual(save_setting(65, 0, 'admin:1')['revision'], 1)
        self.assertIsNone(save_setting(75, 0, 'admin:2'))
        self.assertEqual(read_setting()['thresholdPercent'], 65)
        self.assertEqual(save_setting(75, 1, 'admin:2')['thresholdPercent'], 75)
        row = SkillsRadarScreeningSetting.objects.get(pk=KEY)
        self.assertEqual([(entry['from'], entry['to']) for entry in row.history], [(50, 65), (65, 75)])
        self.assertEqual(row.updated_by, 'admin:2')

    def test_validation_and_missing_schema_are_explicit(self):
        for value in (0, 101, 50.5, True, None):
            with self.assertRaises(ValueError):
                save_setting(value, 0, 'admin:1')
        with patch('enrolment_api.skills_radar.screening.SkillsRadarScreeningSetting.objects.filter',
                   side_effect=OperationalError('no such table: programme_skills_radar_screening_settings')):
            self.assertEqual(read_setting()['schemaReady'], False)

    def test_http_permissions_and_conflict(self):
        route = resolve('/enrolment_api/skills-radar/screening-setting/')
        self.assertEqual(route.url_name, 'skills-radar-screening-setting')
        factory = RequestFactory()
        staff = factory.get(route.route)
        staff.user = SimpleNamespace(is_authenticated=True, is_superuser=False)
        staff.login_account = None
        self.assertEqual(route.func(staff).status_code, 403)
        admin = factory.put(route.route, data=json.dumps({'thresholdPercent': 65, 'revision': 0}),
                            content_type='application/json')
        admin.user = SimpleNamespace(is_authenticated=True, is_superuser=True, pk=1)
        admin.login_account = None
        admin._dont_enforce_csrf_checks = True
        self.assertEqual(route.func(admin).status_code, 200)
        refreshed = factory.get(route.route)
        refreshed.user = admin.user
        refreshed.login_account = None
        self.assertEqual(json.loads(route.func(refreshed).content)['thresholdPercent'], 65)
        self.assertEqual(route.func(admin).status_code, 409)

    @patch('enrolment_api.auth._accesses_of', return_value=frozenset({'enrolment'}))
    def test_platform_admin_can_save_and_reload_setting(self, _accesses):
        route = resolve('/enrolment_api/skills-radar/screening-setting/')
        factory = RequestFactory()
        account = SimpleNamespace(role='admin', is_active=True, pk=7)
        request = factory.put(route.route, data=json.dumps({'thresholdPercent': 55, 'revision': 0}),
                              content_type='application/json')
        request.login_account = account
        request.user = SimpleNamespace(is_authenticated=False)
        request._dont_enforce_csrf_checks = True
        self.assertEqual(route.func(request).status_code, 200)
        refreshed = factory.get(route.route)
        refreshed.login_account = account
        refreshed.user = request.user
        self.assertEqual(json.loads(route.func(refreshed).content)['thresholdPercent'], 55)
