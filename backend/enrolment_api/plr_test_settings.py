"""Isolated SQLite tests: never imports production settings or .env."""
from programme_suitability.test_settings import *  # noqa: F403
ROOT_URLCONF = 'enrolment_api.plr_test_urls'
TEST_RUNNER = 'enrolment_api.plr_test_runner.PlrRunner'
