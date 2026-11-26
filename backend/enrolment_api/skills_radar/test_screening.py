import json
from types import SimpleNamespace
from unittest.mock import patch

from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.urls import resolve

from programme_suitability.models import SkillsRadarScreeningSetting

from .screening import KEY, read_setting, save_setting


def payload(low=5, high=9, count=2, revision=0):
    return {'minimumQualifyingRating': low, 'maximumQualifyingRating': high,
            'minimumHighRatedHardKsbs': count, 'revision': revision}


class ScreeningSettingTests(TestCase):
    def test_default_change_history_and_stale_edit(self):
        self.assertEqual(read_setting(), {**payload(), 'schemaReady': True})
        self.assertEqual(save_setting(payload(low=6), 'admin:1')['revision'], 1)
        self.assertIsNone(save_setting(payload(count=4), 'admin:2'))
        self.assertEqual(save_setting(payload(low=6, high=8, revision=1), 'admin:2')['revision'], 2)
        self.assertEqual(save_setting(payload(low=6, high=8, count=3, revision=2), 'admin:3')['revision'], 3)
        self.assertEqual(read_setting(), {**payload(low=6, high=8, count=3, revision=3), 'schemaReady': True})
        row = SkillsRadarScreeningSetting.objects.get(pk=KEY)
        self.assertEqual([entry['to'] for entry in row.history], [
            {'minimumQualifyingRating': 6, 'maximumQualifyingRating': 9, 'minimumHighRatedHardKsbs': 2},
            {'minimumQualifyingRating': 6, 'maximumQualifyingRating': 8, 'minimumHighRatedHardKsbs': 2},
            {'minimumQualifyingRating': 6, 'maximumQualifyingRating': 8, 'minimumHighRatedHardKsbs': 3},
        ])
        self.assertTrue(all(entry['unit'] == 'screening_rule' for entry in row.history))
        self.assertEqual(row.threshold_percent, 50)
        self.assertEqual(row.updated_by, 'admin:3')

    def test_existing_percentage_history_and_count_are_preserved(self):
        old = {'from': 50, 'to': 63, 'actor': 'old-admin'}
        SkillsRadarScreeningSetting.objects.create(key=KEY, threshold_percent=63,
            minimum_high_rated_hard_ksbs=4, revision=7, history=[old])
        self.assertEqual(read_setting()['minimumHighRatedHardKsbs'], 4)
        save_setting(payload(low=6, high=8, count=4, revision=7), 'new-admin')
        row = SkillsRadarScreeningSetting.objects.get(pk=KEY)
        self.assertEqual(row.threshold_percent, 63)
        self.assertEqual(row.history[0], old)
        self.assertEqual(row.minimum_high_rated_hard_ksbs, 4)

    def test_validation_and_missing_schema_are_explicit(self):
        invalid = [payload(low=0), payload(low=10), payload(low=2.5), payload(low=True),
                   payload(high=0), payload(high=10), payload(high=3.5), payload(low=8, high=6),
                   payload(count=0), payload(count=501), payload(count=2.5), payload(count=True),
                   payload(revision=-1), {**payload(), 'minimumQualifyingRating': None}]
        for value in invalid:
            with self.assertRaises(ValueError):
                save_setting(value, 'admin:1')
        self.assertFalse(SkillsRadarScreeningSetting.objects.filter(pk=KEY).exists())
        with patch('enrolment_api.skills_radar.screening.SkillsRadarScreeningSetting.objects.filter',
                   side_effect=OperationalError('no such table: programme_skills_radar_screening_settings')):
            self.assertEqual(read_setting()['schemaReady'], False)
        with patch('enrolment_api.skills_radar.screening.SkillsRadarScreeningSetting.objects.filter',
                   side_effect=OperationalError('no such column: programme_skills_radar_screening_settings.minimum_high_rated_hard_ksbs')):
            self.assertEqual(read_setting()['minimumHighRatedHardKsbs'], 2)
            self.assertFalse(read_setting()['schemaReady'])
        with patch('enrolment_api.skills_radar.screening.SkillsRadarScreeningSetting.objects.filter') as query:
            query.return_value.first.side_effect = OperationalError('no such column: minimum_qualifying_rating')
            query.return_value.values.return_value.first.return_value = {'minimum_high_rated_hard_ksbs': 4, 'revision': 7}
            self.assertEqual(read_setting(), {**payload(count=4, revision=7), 'schemaReady': False})

    def test_http_permissions_and_conflict(self):
        route = resolve('/enrolment_api/skills-radar/screening-setting/')
        self.assertEqual(route.url_name, 'skills-radar-screening-setting')
        factory = RequestFactory()
        staff = factory.get(route.route)
        staff.user = SimpleNamespace(is_authenticated=True, is_superuser=False)
        staff.login_account = None
        self.assertEqual(route.func(staff).status_code, 403)
        admin = factory.put(route.route, data=json.dumps(payload(low=6, high=8, count=3)),
                            content_type='application/json')
        admin.user = SimpleNamespace(is_authenticated=True, is_superuser=True, pk=1)
        admin.login_account = None
        admin._dont_enforce_csrf_checks = True
        self.assertEqual(route.func(admin).status_code, 200)
        refreshed = factory.get(route.route)
        refreshed.user = admin.user
        refreshed.login_account = None
        self.assertEqual(json.loads(route.func(refreshed).content),
                         {**payload(low=6, high=8, count=3, revision=1), 'schemaReady': True})
        self.assertEqual(route.func(admin).status_code, 409)
        invalid = factory.put(route.route, data=json.dumps(payload(low=9, high=8, count=3, revision=1)),
                              content_type='application/json')
        invalid.user = admin.user
        invalid.login_account = None
        invalid._dont_enforce_csrf_checks = True
        self.assertEqual(route.func(invalid).status_code, 400)
        self.assertEqual(read_setting()['revision'], 1)

    @patch('enrolment_api.auth._accesses_of', return_value=frozenset({'enrolment'}))
    def test_platform_admin_can_save_and_reload_setting(self, _accesses):
        route = resolve('/enrolment_api/skills-radar/screening-setting/')
        factory = RequestFactory()
        account = SimpleNamespace(role='admin', is_active=True, pk=7)
        request = factory.put(route.route, data=json.dumps(payload(low=6, high=8, count=4)),
                              content_type='application/json')
        request.login_account = account
        request.user = SimpleNamespace(is_authenticated=False)
        request._dont_enforce_csrf_checks = True
        self.assertEqual(route.func(request).status_code, 200)
        refreshed = factory.get(route.route)
        refreshed.login_account = account
        refreshed.user = request.user
        self.assertEqual(json.loads(route.func(refreshed).content),
                         {**payload(low=6, high=8, count=4, revision=1), 'schemaReady': True})
