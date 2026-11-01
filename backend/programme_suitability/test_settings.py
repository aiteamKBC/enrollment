"""Isolated, memory-only feature integration tests; never loads .env."""
from enrollment_service.settings_test import *  # noqa: F403

DATABASE_ROUTERS = []
ROOT_URLCONF = 'programme_suitability.urls'
TEST_RUNNER = 'programme_suitability.test_runner.SuitabilityRunner'
