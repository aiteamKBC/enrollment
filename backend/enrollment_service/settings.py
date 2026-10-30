"""Standalone service configuration; no configuration is read from LMS_v2."""
import os
from pathlib import Path
from urllib.parse import urlparse
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Preserve process environment precedence and load only this service's .env.
for raw in (BASE_DIR / '.env').read_text(encoding='utf-8-sig').splitlines() if (BASE_DIR / '.env').exists() else []:
    line = raw.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

database_url = os.environ.get('DATABASE_URL', '')
if not database_url:
    raise ImproperlyConfigured('DATABASE_URL must point to the Enrolment Corner database.')
for alias in ('Database_url', 'DATABASEURL', 'ENROLMENT_DATABASE_URL'):
    if os.environ.get(alias) and os.environ[alias] != database_url:
        raise ImproperlyConfigured(f'{alias} must match DATABASE_URL in the standalone service.')
    os.environ[alias] = database_url

expected_host = os.environ.get('ENROLLMENT_DATABASE_HOST', '')
if expected_host and urlparse(database_url).hostname != expected_host:
    raise ImproperlyConfigured('DATABASE_URL does not match ENROLLMENT_DATABASE_HOST.')

os.environ.setdefault('CURRICULUM_WARM', '0')
os.environ.setdefault('ENGLAND_HOLIDAY_AUTO_SYNC', '0')
os.environ.setdefault('CHAT_DEMO_BOOTSTRAP_ENABLED', '0')

from config.legacy_settings import *  # noqa: F401,F403,E402

ROOT_URLCONF = 'enrollment_service.urls'
WSGI_APPLICATION = 'enrollment_service.wsgi.application'
ASGI_APPLICATION = 'enrollment_service.asgi.application'
SESSION_COOKIE_NAME = 'enrollment_django_session'
CSRF_COOKIE_NAME = 'enrollment_csrftoken'
ENROLLMENT_SESSION_COOKIE_NAME = 'enrollment_session'
LMS_URL = os.environ.get('LMS_URL', '').rstrip('/')
ENROLLMENT_ONLY = True
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Background LMS projections are not processes owned by this service.
READ_MODEL_OUTBOX_ENABLED = False
READ_MODEL_DUAL_WRITE_ENABLED = False
CURRICULUM_HOME_SHARED_READ_MODEL_ENABLED = False
COACH_DASHBOARD_SHARED_READ_MODEL_ENABLED = False
LEARNER_HOME_SHARED_READ_MODEL_ENABLED = False
