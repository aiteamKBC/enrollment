"""Learner-owned, retryable HTTP handoff. No source LMS credentials are needed."""
import inspect
import json
import os
import time
from urllib.parse import urlparse

import requests
from django.db import connections, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from login.permissions import require_role
from learner_api.calendar import learner_first_session
from .handoff_contract import DOCUMENTS, EVENT_TYPES, PLAN_TABLES, encode, signature, validate, MAX_BODY


def snapshot(learner_id, existing_staff=None):
    rows = {}
    with transaction.atomic(using='enrolment'), connections['enrolment'].cursor() as cur:
        def select(table, column, values):
            # Identifiers are fixed code constants, never request values.
            schema, name = table.split('.')
            cur.execute(f'SELECT row_to_json(t) FROM "{schema}"."{name}" t WHERE "{column}" = ANY(%s)', [list(values)])
            rows[table] = [item[0] for item in cur.fetchall()]
            return rows[table]
        users = select('enrolment.Created_users', 'id', [learner_id])
        if len(users) != 1:
            raise ValueError('Learner not found.')
        user = users[0]
        select('enrolment.Employers', 'id', [user.get('Employer_id')])
        profiles = select('Learner.learners', 'enrolment_id', [learner_id])
        profile_ids = [row['id'] for row in profiles]
        accounts = select('login.Login_accounts', 'Subject_id', [learner_id]) if existing_staff is None else []
        rows['login.Login_accounts'] = [row for row in accounts if row['Subject_type'] == 'learner']
        for table in DOCUMENTS:
            select('enrolment.' + table, 'Learner_id', [learner_id])
        modules = select('Learner.' + PLAN_TABLES[0], 'learner_id', profile_ids)
        weeks = select('Learner.' + PLAN_TABLES[1], 'plan_module_id', [row['id'] for row in modules])
        select('Learner.' + PLAN_TABLES[2], 'plan_week_id', [row['id'] for row in weeks])
        for table in ('learner_free_courses', 'learner_ksb_assignments'):
            select('Learner.' + table, 'learner_id', profile_ids)
        events = select('Learner.coach_calendar_event', 'learner_id', [learner_id, *profile_ids])
        rows['Learner.coach_calendar_event'] = [row for row in events if row['event_type'] in EVENT_TYPES and str(row.get('learner_email','')).strip().lower() == str(user.get('Email','')).strip().lower()]
    payload = {'version': 1, 'rows': rows}
    if existing_staff:
        payload['existingStaff'] = existing_staff
    validate(payload)
    return payload


@csrf_protect
@require_role('learner', 'staff', 'admin')
def handoff(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)
    endpoint = os.environ.get('LMS_HANDOFF_URL', '')
    secret = os.environ.get('LMS_HANDOFF_SECRET', '')
    if urlparse(endpoint).scheme != 'https' or len(secret) < 32:
        return JsonResponse({'error': 'LMS handoff is not configured. Contact your enrolment team.'}, status=503)
    account = request.login_account
    from learner_api.models import EnrolmentUser, StaffUser
    staff_identity = None
    if account.role == 'learner':
        learner = EnrolmentUser.all_learners.filter(pk=account.subject_id).first()
    else:
        if account.subject_type != 'staff':
            return JsonResponse({'error': 'A learner identity is required.'}, status=403)
        from login.learner_enrolment import existing_learner_record
        learner = existing_learner_record(account.email)
        staff = StaffUser.objects.filter(pk=account.subject_id, email__iexact=account.email).first()
        if staff is None:
            return JsonResponse({'error': 'Your linked learner identity could not be verified.'}, status=403)
        staff_identity = {'uuid': str(staff.uuid), 'email': account.email}
    if learner is None:
        return JsonResponse({'error': 'Learner not found.'}, status=404)
    # The first-session reader accepts GET; give it a read-only request copy.
    from copy import copy
    read_request = copy(request)
    read_request.method = 'GET'
    readiness = inspect.unwrap(learner_first_session)(read_request, learner.learner_type, learner.pk)
    if readiness.status_code != 200:
        return readiness
    if json.loads(readiness.content).get('access') != 'open':
        return JsonResponse({'error': 'Complete enrolment and your first-session access requirements first.'}, status=409)
    body = encode(snapshot(learner.pk, staff_identity))
    if len(body) > MAX_BODY:
        return JsonResponse({'error': 'This enrolment needs assistance from your enrolment team.'}, status=413)
    timestamp = str(int(time.time()))
    try:
        result = requests.post(endpoint, data=body, timeout=(5, 90), allow_redirects=False, headers={
            'Content-Type': 'application/json', 'X-Enrollment-Time': timestamp,
            'X-Enrollment-Signature': signature(secret, timestamp, body),
        })
    except requests.RequestException:
        return JsonResponse({'error': 'The LMS could not be reached. Please retry.'}, status=503)
    if result.status_code == 409:
        return JsonResponse({'error': 'An LMS identity conflict needs review by your enrolment team.'}, status=409)
    if result.status_code != 200:
        return JsonResponse({'error': 'The LMS has not confirmed your transfer. Please retry.'}, status=503)
    try:
        accepted = result.json().get('status') in ('accepted', 'already-accepted')
    except (ValueError, AttributeError):
        accepted = False
    if not accepted:
        return JsonResponse({'error': 'The LMS returned an invalid transfer confirmation. Please retry.'}, status=503)
    from django.conf import settings
    return JsonResponse({'status': 'accepted', 'url': settings.LMS_URL})
