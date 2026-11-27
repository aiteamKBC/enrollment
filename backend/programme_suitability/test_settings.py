"""Isolated, memory-only feature integration tests; never loads .env."""
from enrollment_service.settings_test import *  # noqa: F403

DATABASE_ROUTERS = []
DATABASES['enrolment']['TEST'] = {'MIRROR': 'default'}
ROOT_URLCONF = 'programme_suitability.urls'
TEST_RUNNER = 'programme_suitability.test_runner.SuitabilityRunner'
# Existing tests exercise the retained legacy reader. Catalogue integration
# tests explicitly enable the new production default with synthetic records.
ENROLMENT_PROGRAMME_SOURCE = 'legacy'
