from django.conf import settings
from django.test import SimpleTestCase


class IsolatedSuitabilitySettingsTests(SimpleTestCase):
    def test_all_database_aliases_are_local_sqlite_and_router_is_disabled(self):
        for alias in ('default', 'enrolment'):
            config = settings.DATABASES[alias]
            self.assertEqual(config['ENGINE'], 'django.db.backends.sqlite3')
            self.assertEqual(config['NAME'], ':memory:')
            self.assertFalse(config.get('HOST'))
        self.assertEqual(settings.DATABASE_ROUTERS, [])
        self.assertFalse(settings.PROGRAMME_STRUCTURE_SHADOW_ENABLED)
