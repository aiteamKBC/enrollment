"""Smoke the actual service settings on a verified local database, with HTTP blocked."""
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse
import psycopg

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT/'private/enrollment_regression.json').read_text())
with psycopg.connect(config['url']) as db:
    with db.cursor() as cursor:
        cursor.execute('SHOW data_directory')
        if Path(cursor.fetchone()[0]).resolve() != Path(config['verified_local_directory']).resolve():
            raise SystemExit('Wrong local test server.')
sys.path.insert(0,str(ROOT/'backend'))
for key in ('DATABASE_URL','DATABASEURL','Database_url','ENROLMENT_DATABASE_URL'):
    os.environ[key]=config['url']
os.environ.update({'DJANGO_SETTINGS_MODULE':'enrollment_service.settings',
    'ENROLLMENT_DATABASE_HOST':urlparse(config['url']).hostname,
    'AZURE_MAIL_ENABLED':'false', 'DJANGO_ALLOWED_HOSTS':'testserver,localhost,127.0.0.1',
    'CURRICULUM_WARM':'0','ENGLAND_HOLIDAY_AUTO_SYNC':'0','DJANGO_DEBUG':'true',
    'DB_POOL_ENABLED':'false'})
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
def local_only(sock, address):
    if address[0] not in ('127.0.0.1', '::1'):
        raise AssertionError('External network blocked in runtime smoke checks.')
    return original_connect(sock,address)
def local_only_ex(sock,address):
    if address[0] not in ('127.0.0.1','::1'):
        raise AssertionError('External network blocked in runtime smoke checks.')
    return original_connect_ex(sock,address)
socket.socket.connect=local_only
socket.socket.connect_ex=local_only_ex
import django
django.setup()
from django.core.wsgi import get_wsgi_application
get_wsgi_application()
from django.core.management import call_command
call_command('check')
from django.test import Client
client=Client()
for route, expected in [('/health/',200),('/login_api/me/',401),('/learner_api/enrolment-users/',401),('/enrolment_api/wizard-layout/',401)]:
    response=client.get(route)
    if response.status_code != expected:
        raise AssertionError(f'{route}: expected {expected}, received {response.status_code}')
    print(route,response.status_code)
from learner_api.models import EnrolmentUser, StaffUser
from django.db import connections, transaction
from login.models import LoginAccount, LoginSession
from login.sessions import COOKIE_NAME, issue_session
staff=StaffUser.objects.create(username='Runtime synthetic officer',email='runtime-officer@example.invalid',access='super-admin',status='FullUser',type='Admin')
account=LoginAccount.objects.create(subject_type='staff',subject_id=staff.pk,email=staff.email,role='admin',is_active=True)
try:
    token,_session,_ttl=issue_session(account)
    client.cookies[COOKIE_NAME]=token
    for route in ('/learner_api/enrolment-users/','/learner_api/staff-users/','/learner_api/organisations/','/enrolment_api/wizard-layout/','/enrolment_api/eligibility-form-layout/','/learner_api/curriculum/programmes/'):
        response=client.get(route)
        if response.status_code!=200:
            (ROOT/'private/runtime-response.log').write_bytes(response.content)
            raise AssertionError(f'{route}: expected 200, received {response.status_code}; private response saved.')
        print(route,response.status_code)
    for kind in ('apprenticeship','commercial'):
        learner=EnrolmentUser.all_learners.create(username='Synthetic runtime learner',email=f'runtime-{kind}@example.invalid',learner_type=kind,programme_status='Delivery')
        try:
            for route in (
                f'/learner_api/calendar/{kind}/{learner.pk}/first-session/',
                f'/learner_api/calendar/{kind}/{learner.pk}/',
                f'/learner_api/learning-plan/{learner.pk}/',
                f'/enrolment_api/wizard-bootstrap/{kind}/{learner.pk}/',
                f'/enrolment_api/documents/{kind}/{learner.pk}/',
            ):
                response=client.get(route)
                if response.status_code!=200:
                    (ROOT/'private/runtime-response.log').write_bytes(response.content)
                    raise AssertionError(f'{route}: expected 200, received {response.status_code}; private response saved.')
                print(route,response.status_code)
        finally:
            # Actual request middleware releases pooled connections, so fixtures
            # are committed and explicitly cleaned instead of spanning requests
            # with an artificial transaction. Only this synthetic ID is removed.
            from enrollment_service.handoff_contract import DOCUMENTS
            with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cur:
                for table in DOCUMENTS:
                    cur.execute(f'DELETE FROM enrolment."{table}" WHERE "Learner_id"=%s',[learner.pk])
                cur.execute('DELETE FROM enrolment."Created_users" WHERE id=%s',[learner.pk])
finally:
    LoginSession.objects.filter(account_id=account.pk).delete()
    LoginAccount.objects.filter(pk=account.pk).delete()
    StaffUser.objects.filter(pk=staff.pk).delete()
print('Runtime middleware, enrolment reads for both learner types and anonymous boundaries verified locally; external network blocked.')
