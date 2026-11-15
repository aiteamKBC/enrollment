from django.test import SimpleTestCase
from unittest.mock import patch

from . import service


class DynamicSuitabilityRplCatalogueTests(SimpleTestCase):
    def test_suitability_rpl_report_is_catalogued_as_shared_dynamic_review(self):
        with patch.object(service, 'ready', return_value=False):
            entry = next(row for row in service.index()['results'] if row['templateId'] == 'suitability-rpl-report')
        self.assertEqual(entry['title'], 'Suitability & RPL Report')
        self.assertEqual(entry['fullTitle'], 'Suitability & Recognition of Prior Learning Report')
        self.assertEqual(entry['category'], 'Review')
        self.assertEqual(entry['programmes'], [])
        self.assertTrue(entry['dynamic'])
        self.assertIsNone(entry['publishedVersion'])
        self.assertIsNone(entry['draft'])

    def test_dynamic_report_is_not_exposed_as_a_static_template_seed(self):
        from .registry import seeds

        self.assertNotIn('suitability-rpl-report', seeds()['templates'])
