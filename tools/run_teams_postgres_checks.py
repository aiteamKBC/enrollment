"""Run unchanged Teams integration cases on the verified private local cluster."""
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse, unquote

import psycopg
ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / 'private/enrollment_regression.json').read_text())
with psycopg.connect(config['url']) as db:
    with db.cursor() as cursor:
        cursor.execute('SHOW data_directory')
        if Path(cursor.fetchone()[0]).resolve() != Path(config['verified_local_directory']).resolve():
            raise SystemExit('Unverified local PostgreSQL server.')
        cursor.execute('SELECT current_database()')
        if cursor.fetchone()[0] != 'enrollment_regression':
            raise SystemExit('Unverified regression database.')

sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'enrollment_service.settings_test'
os.environ['AZURE_MAIL_ENABLED'] = 'false'
os.environ['API_REQUIRE_AUTH'] = '1'
os.environ['CURRICULUM_WARM'] = '0'
os.environ['ENGLAND_HOLIDAY_AUTO_SYNC'] = '0'
from django.conf import settings
parsed = urlparse(config['url'])
database = {'ENGINE': 'django.db.backends.postgresql', 'NAME': parsed.path.lstrip('/'),
            'USER': unquote(parsed.username), 'PASSWORD': unquote(parsed.password), 'HOST': parsed.hostname, 'PORT': parsed.port}
settings.DATABASES = {alias: dict(database) for alias in ('default','enrolment')}
settings.ROOT_URLCONF = 'enrollment_service.regression_urls'
settings.CURRICULUM_ALLOW_RUNTIME_SCHEMA_BOOTSTRAP = True
# Every HTTP/Graph call must be mocked. libpq is restricted separately by the verified DSN.
def refused(*args, **kwargs):
    raise AssertionError('External network access is forbidden in integration tests.')
socket.create_connection = refused
socket.socket.connect = refused
socket.socket.connect_ex = refused

import django
django.setup()
from curriculum_api import views
views.ensure_live_session_tracking_tables()
from django.db import connection
from coach_api.models import CoachDashboardSnapshot, CoachAbsenceReport
from read_models.models import ReadModel
for model in (CoachDashboardSnapshot, CoachAbsenceReport, ReadModel):
    table = model._meta.db_table.replace('"."', '.')
    with connection.cursor() as cursor:
        cursor.execute('SELECT to_regclass(%s)', [connection.ops.quote_name(model._meta.db_table)])
        present = cursor.fetchone()[0]
    if present is None:
        with connection.schema_editor() as editor:
            editor.create_model(model)
with connection.cursor() as cursor:
    for artifact in ('session_results_archive.sql', 'teams_calendar_create_claims.sql'):
        cursor.execute((ROOT / 'database/test-support' / artifact).read_text(encoding='utf-8'))
from django.test.runner import DiscoverRunner
runner = DiscoverRunner(verbosity=1)
runner.setup_test_environment()
suite = runner.build_suite(sys.argv[1:] or [
    'coach_api.tests_calendar_race', 'curriculum_api.tests.CurriculumTeamsMeetingTests',
    'curriculum_api.tests.TeamsCalendarSyncVerdictTests',
    'coach_api.tests_security.CoachAuthorizationSecurityTests.test_coach_cannot_book_for_another_coachs_learner',
])
runner.run_checks(databases=[])
result = runner.run_suite(suite)
runner.teardown_test_environment()
raise SystemExit(0 if result.wasSuccessful() else 1)
