"""Isolated Skills Radar integration runner. Never imports the live .env."""
from programme_suitability.test_settings import *  # noqa: F403

TEST_RUNNER = 'enrolment_api.skills_radar.test_runner.RadarRunner'
