"""No dotenv loading, no PostgreSQL connection, no external credentials."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = 'synthetic-enrollment-test-key-only'
DEBUG = True
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
INSTALLED_APPS = [
    'django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
    'django.contrib.messages', 'django.contrib.staticfiles',
    'login', 'learner_api', 'enrolment_api', 'coach_api', 'curriculum_api',
    'audit_api', 'manual_audit_api', 'engagement_api', 'quiz_api', 'chat',
    'read_models', 'old_otjh', 'system_audit', 'progress_reviews_api', 'knowledge_base', 'mobile_comms',
]
DATABASES = {name: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'} for name in ('default', 'enrolment')}
DATABASE_ROUTERS = ['learner_api.routers.EnrolmentRouter']
MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'login.middleware.LoginSessionMiddleware',
    'login.api_gate.ApiSessionGateMiddleware',
]
ROOT_URLCONF = 'enrollment_service.urls'
USE_TZ = True
TIME_ZONE = 'Europe/London'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True}]
STATIC_URL = '/static/'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
AZURE_STORAGE_ACCOUNT = ''
AZURE_STORAGE_KEY = ''
AZURE_ENROLMENT_DOCS_CONTAINER = 'synthetic-enrolment-docs'
AZURE_QUARANTINE_CONTAINER = 'synthetic-quarantine'
AZURE_APPROVED_CONTAINER = 'synthetic-approved'
AZURE_REJECTED_CONTAINER = 'synthetic-rejected'
AZURE_SAS_TTL_MINUTES = 15
AZURE_LEARNER_PHOTOS_CONTAINER = 'synthetic-photos'
KNOWLEDGE_BASE_STORAGE = 'local'
KNOWLEDGE_BASE_AZURE_CONTAINER = ''
CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
READ_MODEL_OUTBOX_ENABLED = False
READ_MODEL_DUAL_WRITE_ENABLED = False
CURRICULUM_ALLOW_RUNTIME_SCHEMA_BOOTSTRAP = False
ENROLLMENT_SESSION_COOKIE_NAME = 'enrollment_session'
LMS_URL = 'https://lms.example.test'
